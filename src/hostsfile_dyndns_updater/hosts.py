"""Edit a hosts(5) file: set the address of a hostname per IP family."""

import fcntl
import ipaddress
import os
import re
import tempfile

MARKER = "# managed by hostsfile-dyndns-updater"
HOSTNAME_RE = re.compile(
    r"^(?=.{1,253}$)([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)(\.[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)*$"
)


def valid_hostname(name: str) -> bool:
    # A numeric top-level label would make the name look like an IP address.
    return bool(HOSTNAME_RE.match(name)) and not name.rsplit(".", 1)[-1].isdigit()


def _family(address: str) -> int | None:
    try:
        return ipaddress.ip_address(address.split("%", 1)[0]).version
    except ValueError:
        return None


def update_content(content: str, hostname: str, address: str) -> str:
    """Return `content` with `hostname` mapped to `address`.

    Only lines of the same IP family are touched. A line that maps the
    hostname alone is updated in place; if other names share the line, the
    hostname is removed from it so their mapping stays untouched. When no
    line was updated, a managed line is appended.
    """
    wanted = ipaddress.ip_address(address)
    host = hostname.lower()
    out: list[str] = []
    done = False
    for line in content.splitlines():
        body, sep, comment = line.partition("#")
        fields = body.split()
        if (
            len(fields) >= 2
            and _family(fields[0]) == wanted.version
            and host in (f.lower() for f in fields[1:])
        ):
            others = [f for f in fields[1:] if f.lower() != host]
            if others:
                out.append(f"{' '.join(fields[:1] + others)}{'  ' + sep + comment if sep else ''}")
                continue
            if done:
                continue  # duplicate mapping for the same hostname
            tail = f"  {sep}{comment}" if sep else f"  {MARKER}"
            out.append(f"{address}\t{hostname}{tail}")
            done = True
            continue
        out.append(line)
    if not done:
        out.append(f"{address}\t{hostname}  {MARKER}")
    return "\n".join(out) + "\n"


def update_hosts_file(path: str, hostname: str, address: str) -> bool:
    """Update `path` atomically. Return True if the file changed."""
    path = os.path.realpath(path)  # keep a symlinked hosts file a symlink
    # Lock the directory: the file itself is replaced, so it cannot hold the lock.
    lock_fd = os.open(os.path.dirname(path) or ".", os.O_RDONLY)
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX)
        with open(path, encoding="utf-8") as fh:
            current = fh.read()
        new = update_content(current, hostname, address)
        if new == current:
            return False
        _write(path, new)
        return True
    finally:
        os.close(lock_fd)  # closing releases the flock


def _write(path: str, content: str) -> None:
    st = os.stat(path)
    directory = os.path.dirname(path) or "."
    fd, tmp = tempfile.mkstemp(prefix=".hosts.", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(content)
            fh.flush()
            os.fsync(fh.fileno())
        os.chmod(tmp, st.st_mode & 0o7777)
        try:
            os.chown(tmp, st.st_uid, st.st_gid)
        except PermissionError:
            pass  # unprivileged run (tests); root always may
        try:
            os.replace(tmp, path)
        except OSError:
            # e.g. bind-mounted /etc/hosts in containers: rewrite in place
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(content)
            os.unlink(tmp)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise
