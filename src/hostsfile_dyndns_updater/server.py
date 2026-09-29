"""Local HTTP backend behind nginx (Unix socket). Implements GET /update."""

import base64
import binascii
import grp
import ipaddress
import logging
import os
import socketserver
import threading
from http.server import BaseHTTPRequestHandler
from urllib.parse import parse_qs, urlsplit

from . import passwords
from .config import Config, HostEntry, address_allowed
from .hosts import update_hosts_file

log = logging.getLogger("hostsfile-dyndns-updater")

MAX_REQUEST_LINE = 2048
# Verified against when the username is unknown, to keep timing uniform.
_DUMMY_HASH = passwords.hash_password("dummy", passwords.MIN_ITERATIONS)


class Rejected(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


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
    ok = passwords.verify_password(creds[1], entry.password_hash if entry else _DUMMY_HASH)
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
        if config.require_source_match and peer:
            try:
                peer_addr = ipaddress.ip_address(peer)
            except ValueError:
                raise Rejected(400, "badip") from None
            if peer_addr.version == version and peer_addr != addr:
                raise Rejected(403, "badip")
        result.append(str(addr))
    if not result:
        raise Rejected(400, "badip")
    return result


_lock = threading.Lock()


def handle_update(config: Config, headers, target: str, peer: str | None) -> str:
    parts = urlsplit(target)
    if parts.path != "/update":
        raise Rejected(404, "notfound")
    query = parse_qs(parts.query, keep_blank_values=True)
    entry = authenticate(config, _credentials(headers, query, config.allow_query_credentials))
    domain = _first(query, "domain", "hostname")
    if domain and domain.lower() != entry.hostname:
        raise Rejected(403, "nohost")
    addresses = parse_addresses(config, query, peer)
    changed = False
    with _lock:
        for address in addresses:
            changed |= update_hosts_file(config.hosts_file, entry.hostname, address)
    log.info("%s %s -> %s", "updated" if changed else "unchanged", entry.hostname, ",".join(addresses))
    return f"{'good' if changed else 'nochg'} {' '.join(addresses)}"


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
            try:
                if len(self.path) > MAX_REQUEST_LINE:
                    raise Rejected(414, "toolong")
                self._respond(200, handle_update(config, self.headers, self.path, peer))
            except Rejected as exc:
                if exc.status in (401, 403):
                    log.warning("rejected request from %s: %s", peer or "?", exc.message)
                self._respond(exc.status, exc.message)
            except Exception:  # never leak internals to the client
                log.exception("internal error")
                self._respond(500, "911")

        def _method_not_allowed(self):
            self.send_response(405)
            self.send_header("Allow", "GET")
            self.send_header("Content-Length", "0")
            self.end_headers()

        do_POST = do_PUT = do_DELETE = do_PATCH = _method_not_allowed  # noqa: N815

        def log_message(self, fmt, *args):  # request lines may hold secrets
            pass

    return Handler


class Server(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    daemon_threads = True

    def __init__(self, config: Config):
        self.config = config
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
