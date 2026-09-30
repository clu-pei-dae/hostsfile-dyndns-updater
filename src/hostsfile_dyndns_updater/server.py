"""Local HTTP backend behind nginx (Unix socket). Implements GET /update.

Runs unprivileged. It authenticates and validates requests and decides from
the (world-readable) hosts file whether anything changes; actual changes are
written by the root helper (see apply.py) and reported back synchronously, so
the FritzBox only gets "good" once the hosts file really was updated.
"""

import base64
import binascii
import functools
import grp
import ipaddress
import json
import logging
import os
import socket
import socketserver
import threading
from http.server import BaseHTTPRequestHandler
from urllib.parse import parse_qs, urlsplit

from . import apply, passwords, requestlog
from .config import Config, HostEntry, address_allowed
from .hosts import pending_addresses

log = logging.getLogger("hostsfile-dyndns-updater")

MAX_REQUEST_LINE = 2048


@functools.lru_cache(maxsize=8)
def _dummy_hash(iterations: int) -> str:
    """Hash verified against for unknown usernames, so they cost as much as known ones."""
    return passwords.hash_password("dummy", iterations)


def _max_iterations(config: Config) -> int:
    return max(passwords.parse_hash(e.password_hash)[0] for e in config.hosts.values())


class Request:
    """What the handler learned about a request, for the request log."""

    def __init__(self):
        self.user: str | None = None  # set only after successful authentication
        self.host: str | None = None
        self.addresses: list[str] = []
        self.changed = False
        self.request_id: str | None = None  # set when the hosts file is to be changed
        self.detail: str | None = None  # why an ERROR happened (server-generated token)


class Rejected(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


class ApplyFailed(Exception):
    """The hosts file could not be updated; answered with 911."""


def _first(query: dict[str, list[str]], *keys: str) -> str | None:
    for key in keys:
        if query.get(key):
            return query[key][0].strip()
    return None


def _credentials(headers, query, allow_query: bool) -> tuple[str, str] | None:
    auth = headers.get("Authorization", "")
    if auth[:6].lower() == "basic ":
        try:
            user, sep, pw = base64.b64decode(auth[6:].strip(), validate=True).decode().partition(":")
        except (binascii.Error, UnicodeDecodeError):
            return None
        return (user, pw) if sep else None
    if allow_query:
        user, pw = _first(query, "username"), _first(query, "password", "pass")
        if user is not None and pw is not None:
            return user, pw
    return None


def authenticate(config: Config, creds: tuple[str, str] | None) -> HostEntry:
    if creds is None:
        raise Rejected(401, "badauth")
    entry = config.hosts.get(creds[0])
    encoded = entry.password_hash if entry else _dummy_hash(_max_iterations(config))
    ok = passwords.verify_password(creds[1], encoded)
    if not (entry and ok):
        raise Rejected(401, "badauth")
    return entry


def parse_addresses(config: Config, query, peer: str | None) -> list[str]:
    result: list[str] = []
    for version, keys in ((4, ("ipv4", "ip", "ipaddr", "myip")), (6, ("ipv6", "ip6", "ip6addr"))):
        raw = _first(query, *keys)
        if not raw:
            continue
        try:
            addr = ipaddress.ip_address(raw)
        except ValueError:
            raise Rejected(400, "badip") from None
        if addr.version != version:
            raise Rejected(400, "badip")
        if addr.is_unspecified:
            continue  # FritzBox sends :: when it has no IPv6 address
        if not address_allowed(addr, config.allow_private_addresses):
            raise Rejected(400, "badip")
        if config.require_source_match:
            try:
                peer_addr = ipaddress.ip_address(peer or "")
            except ValueError:
                raise Rejected(403, "badip") from None  # no trustworthy peer address
            if peer_addr.version == version and peer_addr != addr:
                raise Rejected(403, "badip")
        result.append(str(addr))
    if not result:
        raise Rejected(400, "badip")
    return result


_lock = threading.Lock()


def handle_update(config: Config, headers, target: str, peer: str | None,
                  req: Request | None = None) -> str:
    req = req or Request()
    parts = urlsplit(target)
    if parts.path != "/update":
        raise Rejected(404, "notfound")
    query = parse_qs(parts.query, keep_blank_values=True)
    entry = authenticate(config, _credentials(headers, query, config.allow_query_credentials))
    req.user, req.host = entry.username, entry.hostname
    domain = _first(query, "domain", "hostname")
    if domain and domain.lower() != entry.hostname:
        raise Rejected(403, "nohost")
    addresses = parse_addresses(config, query, peer)
    req.addresses = addresses
    with _lock:
        with open(config.hosts_file, encoding="utf-8") as fh:
            current = fh.read()
        if pending_addresses(current, entry.hostname, addresses):
            req.request_id = apply.new_request_id()
            req.changed = _apply(config, req) == "good"
    return f"{'good' if req.changed else 'nochg'} {' '.join(addresses)}"


def _apply(config: Config, req: Request) -> str:
    """Have the hosts file updated; return "good" or "nochg" or raise ApplyFailed."""
    if config.apply_socket:
        try:
            reply = apply.request(config.apply_socket, req.request_id, req.host, req.addresses)
        except apply.Unavailable as exc:
            log.error("root helper at %s: %s", config.apply_socket, exc)
            req.detail = "apply-unavailable"
            raise ApplyFailed() from None
    else:  # everything in this process (running as root without the helper)
        message = {"request": req.request_id, "host": req.host, "addresses": req.addresses}
        reply = apply.handle(config, json.dumps(message).encode())
    if reply["result"] == "error":
        req.detail = f"apply-{reply['reason']}"
        raise ApplyFailed()
    return reply["result"]


def make_handler(config: Config):
    class Handler(BaseHTTPRequestHandler):
        server_version = "hostsfile-dyndns-updater"
        sys_version = ""

        def _respond(self, status: int, body: str) -> None:
            data = (body + "\n").encode()
            self.send_response(status)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):  # noqa: N802
            peer = self.headers.get("X-Real-IP")
            req = Request()
            try:
                if len(self.path) > MAX_REQUEST_LINE:
                    raise Rejected(414, "toolong")
                body = handle_update(config, self.headers, self.path, peer, req)
            except Rejected as exc:
                status, body = exc.status, exc.message
            except ApplyFailed:
                status, body = 500, "911"
            except Exception:  # never leak internals to the client
                log.exception("internal error")
                status, body = 500, "911"
                req.detail = "internal"
            else:
                status = 200
            result = body.split(" ", 1)[0]
            requestlog.request(status, result, peer, req.user, req.host, req.addresses,
                               req.changed, req.request_id, req.detail)
            self._respond(status, body)

        def _method_not_allowed(self):
            requestlog.request(405, "badmethod", self.headers.get("X-Real-IP"))
            self.send_response(405)
            self.send_header("Allow", "GET")
            self.send_header("Content-Length", "0")
            self.end_headers()

        do_HEAD = do_POST = do_PUT = do_DELETE = do_PATCH = _method_not_allowed  # noqa: N815

        def log_message(self, fmt, *args):  # request lines may hold secrets
            pass

    return Handler


def systemd_listen_fd() -> int | None:
    """The listening socket passed by systemd socket activation, if any."""
    if os.environ.get("LISTEN_PID") != str(os.getpid()) or os.environ.get("LISTEN_FDS") != "1":
        return None
    for name in ("LISTEN_PID", "LISTEN_FDS", "LISTEN_FDNAMES"):
        os.environ.pop(name, None)
    return 3  # SD_LISTEN_FDS_START


class Server(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    daemon_threads = True

    def __init__(self, config: Config, listen_fd: int | None = None):
        self.config = config
        _dummy_hash(_max_iterations(config))
        requestlog.setup(config)
        if listen_fd is not None:
            # Socket created by systemd (owner, group and mode set in the .socket unit).
            super().__init__(config.socket, make_handler(config), bind_and_activate=False)
            self.socket.close()
            self.socket = socket.socket(fileno=listen_fd)
            if self.socket.family != socket.AF_UNIX or self.socket.type != socket.SOCK_STREAM:
                raise SystemExit("socket from systemd is not a Unix stream socket")
            self.server_address = self.socket.getsockname()
            return
        path = config.socket
        if os.path.exists(path):
            os.unlink(path)
        old = os.umask(0o117)  # socket mode 0660
        try:
            super().__init__(path, make_handler(config))
        finally:
            os.umask(old)
        if config.socket_group:
            try:
                os.chown(path, -1, grp.getgrnam(config.socket_group).gr_gid)
            except KeyError:
                self.server_close()
                raise SystemExit(f"socket_group {config.socket_group!r} does not exist") from None
