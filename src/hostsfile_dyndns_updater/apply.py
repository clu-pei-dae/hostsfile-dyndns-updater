"""Root helper: apply one update to the hosts file.

The API service runs unprivileged and cannot write the hosts file. For every
change it connects to /run/hostsfile-dyndns-updater/apply.sock; systemd starts
`hostsfile-dyndns-updater apply` as root for that connection (Accept=yes) with
the connection on stdin/stdout. One JSON line each way:

    request: {"request": "5f0c9e1a7d3b2c64", "host": "home.example.org", "addresses": ["8.8.8.8"]}
    reply:   {"result": "good", "changed": ["8.8.8.8"]}
             {"result": "nochg", "changed": []}
             {"result": "error", "reason": "badhost"}

The helper trusts nothing in the request: the hostname must be one configured
in config.ini, every address is validated again with the same rules as the
API, and only config.hosts_file is written. Each run writes an APPLY line to
the request log, so the change and its outcome can be traced.
"""

import ipaddress
import json
import logging
import re
import secrets
import socket

from . import requestlog
from .config import Config, address_allowed, hostnames
from .hosts import update_hosts_file

log = logging.getLogger("hostsfile-dyndns-updater")

MAX_MESSAGE = 4096
REQUEST_ID_RE = re.compile(r"^[0-9a-f]{16}$")
REASONS = ("badrequest", "badhost", "badip", "writefailed")


class ApplyError(Exception):
    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


def new_request_id() -> str:
    return secrets.token_hex(8)


def _request_id(message) -> str | None:
    rid = message.get("request") if isinstance(message, dict) else None
    return rid if isinstance(rid, str) and REQUEST_ID_RE.match(rid) else None


def validate(config: Config, message) -> tuple[str, str, list[str]]:
    """Return (request_id, hostname, addresses) or raise ApplyError."""
    if not isinstance(message, dict) or set(message) != {"request", "host", "addresses"}:
        raise ApplyError("badrequest")
    request_id = _request_id(message)
    if request_id is None:
        raise ApplyError("badrequest")
    host = message["host"]
    if not isinstance(host, str) or host not in hostnames(config):
        raise ApplyError("badhost")
    raw = message["addresses"]
    if not isinstance(raw, list) or not 1 <= len(raw) <= 2 or not all(isinstance(a, str) for a in raw):
        raise ApplyError("badip")
    addresses = []
    for value in raw:
        try:
            addr = ipaddress.ip_address(value)
        except ValueError:
            raise ApplyError("badip") from None
        if not address_allowed(addr, config.allow_private_addresses):
            raise ApplyError("badip")
        addresses.append(addr)
    if len({a.version for a in addresses}) != len(addresses):
        raise ApplyError("badip")  # at most one address per family
    return request_id, host, [str(a) for a in addresses]


def handle(config: Config, raw: bytes) -> dict:
    """Validate and apply one request; log an APPLY line; return the reply."""
    request_id = host = None
    addresses: list[str] = []
    try:
        if len(raw) > MAX_MESSAGE:
            raise ApplyError("badrequest")
        try:
            message = json.loads(raw)
        except ValueError:  # includes UnicodeDecodeError
            raise ApplyError("badrequest") from None
        request_id = _request_id(message)
        request_id, host, addresses = validate(config, message)
        changed = [a for a in addresses if update_hosts_file(config.hosts_file, host, a)]
    except ApplyError as exc:
        requestlog.applied("error", request_id, reason=exc.reason)
        return {"result": "error", "reason": exc.reason}
    except OSError as exc:
        log.error("writing %s failed: %s", config.hosts_file, exc)
        requestlog.applied("error", request_id, host, addresses, reason="writefailed")
        return {"result": "error", "reason": "writefailed"}
    result = "good" if changed else "nochg"
    requestlog.applied(result, request_id, host, addresses)
    return {"result": result, "changed": changed}


def serve_stream(config: Config, rfile, wfile) -> None:
    """Handle one request from rfile (the socket systemd passed as stdin)."""
    reply = handle(config, rfile.readline(MAX_MESSAGE + 1))
    wfile.write(json.dumps(reply).encode() + b"\n")
    wfile.flush()


class Unavailable(Exception):
    """The helper could not be reached or gave no valid answer."""


def request(socket_path: str, request_id: str, host: str, addresses: list[str],
            timeout: float = 8.0) -> dict:
    """Client side, used by the API service. Returns a validated reply."""
    message = json.dumps({"request": request_id, "host": host, "addresses": addresses})
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.settimeout(timeout)
            sock.connect(socket_path)
            sock.sendall(message.encode() + b"\n")
            with sock.makefile("rb") as fh:
                raw = fh.readline(MAX_MESSAGE + 1)
    except OSError as exc:
        raise Unavailable(str(exc)) from None
    try:
        reply = json.loads(raw)
    except ValueError:
        raise Unavailable("invalid reply") from None
    if not isinstance(reply, dict):
        raise Unavailable("invalid reply")
    if reply.get("result") in ("good", "nochg") and isinstance(reply.get("changed"), list):
        return reply
    if reply.get("result") == "error" and reply.get("reason") in REASONS:
        return reply
    raise Unavailable("invalid reply")
