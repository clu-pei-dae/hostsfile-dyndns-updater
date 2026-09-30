"""Request log, written by the API service and by the root helper.

Format (fields in this order, values never contain spaces):

    2026-09-30T10:15:02+0200 APPLY result=good host=home.example.org addr=8.8.8.8 request=5f0c9e1a7d3b2c64
    2026-09-30T10:15:02+0200 OK status=200 result=good client=8.8.8.8 user=fritzbox host=home.example.org addr=8.8.8.8 request=5f0c9e1a7d3b2c64
    2026-09-30T10:16:40+0200 FAIL status=401 result=badauth client=198.51.100.4 user=-

APPLY lines come from the process that wrote the hosts file (the root helper),
OK/FAIL/ERROR lines from the API service; `request` links both for a change.
OK lines for unchanged addresses are logged only with log_level = all; all
other lines are always logged. The fail2ban filter matches FAIL lines only.

Only values the server produced or validated are written: `user` is a
configured username or "-", `client` a parsed IP address or "-". Anything a
client could choose freely would let it forge lines for fail2ban.
"""

import ipaddress
import logging
import logging.handlers
import os

from .config import Config

logger = logging.getLogger("hostsfile-dyndns-updater.requests")

# Python levels behind the configurable log_level.
UNCHANGED = logging.DEBUG  # "all"
CHANGED = logging.INFO  # "changes"
FAILED = logging.WARNING  # always


def setup(config: Config, create: bool = True) -> None:
    """Log to config.log_file. With create=False (root helper) a missing file is
    not created, since it would get the wrong owner; the journal is used instead."""
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        handler.close()
    logger.setLevel(UNCHANGED if config.log_level == "all" else CHANGED)
    if not config.log_file or not (create or os.path.isfile(config.log_file)):
        logger.propagate = True  # to the service log (journal)
        return
    # WatchedFileHandler reopens the file after logrotate moved it.
    old = os.umask(0o077)
    try:
        handler = logging.handlers.WatchedFileHandler(config.log_file, encoding="utf-8")
    except OSError as exc:
        raise SystemExit(f"cannot open log_file {config.log_file}: {exc}") from None
    finally:
        os.umask(old)
    handler.setFormatter(logging.Formatter("%(asctime)s %(message)s", "%Y-%m-%dT%H:%M:%S%z"))
    logger.addHandler(handler)
    logger.propagate = False


def client_address(peer: str | None) -> str:
    try:
        return str(ipaddress.ip_address(peer or ""))
    except ValueError:
        return "-"


def request(status: int, result: str, peer: str | None, user: str | None = None,
            host: str | None = None, addresses: list[str] | None = None,
            changed: bool = False, request_id: str | None = None,
            detail: str | None = None) -> None:
    fields = [f"status={status}", f"result={result}", f"client={client_address(peer)}",
              f"user={user or '-'}"]
    if host:
        fields.append(f"host={host}")
    if addresses:
        fields.append(f"addr={','.join(addresses)}")
    if request_id:
        fields.append(f"request={request_id}")
    if detail:
        fields.append(f"detail={detail}")
    if status >= 500:
        level, kind = logging.ERROR, "ERROR"
    elif status >= 400:
        level, kind = FAILED, "FAIL"
    else:
        # A request the root helper handled is always logged, to pair its APPLY line.
        level, kind = (CHANGED if changed or request_id else UNCHANGED), "OK"
    logger.log(level, "%s %s", kind, " ".join(fields))


def applied(result: str, request_id: str | None, host: str | None = None,
            addresses: list[str] | None = None, reason: str | None = None) -> None:
    """Line of the process that writes the hosts file (always logged)."""
    fields = [f"result={result}"]
    if host:
        fields.append(f"host={host}")
    if addresses:
        fields.append(f"addr={','.join(addresses)}")
    if reason:
        fields.append(f"reason={reason}")
    fields.append(f"request={request_id or '-'}")
    logger.log(logging.ERROR if result == "error" else CHANGED, "APPLY %s", " ".join(fields))
