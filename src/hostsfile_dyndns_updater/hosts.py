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


def _same_address(field: str, wanted) -> bool:
    try:
        return ipaddress.ip_address(field) == wanted
    except ValueError:
        return False  # e.g. with a zone index; rewrite it


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
            done = True
            if _same_address(fields[0], wanted):
                out.append(line)  # already right: leave the line as the admin wrote it
                continue
            tail = f"  {sep}{comment}" if sep else f"  {MARKER}"
            out.append(f"{address}\t{hostname}{tail}")
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
    """Replace the file atomically if the directory is writable, else in place.

    The systemd unit of the root helper can write the hosts file only, not its
    directory (and in containers /etc/hosts is a bind mount), so the in-place
    path is the normal one in production.
    """
    try:
        _write_atomic(path, content)
    except OSError:
        _write_in_place(path, content)


def _write_atomic(path: str, content: str) -> None:
    st = os.stat(path)
    fd, tmp = tempfile.mkstemp(prefix=".hosts.", dir=os.path.dirname(path) or ".")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(content)
            fh.flush()
            os.fsync(fh.fileno())
        os.chmod(tmp, st.st_mode & 0o7777)
        try:
            os.chown(tmp, st.st_uid, st.st_gid)
        except PermissionError:
            pass  # unprivileged run (tests)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def _write_in_place(path: str, content: str) -> None:
    # Overwrite, then cut off the rest: readers never see an empty file.
    data = content.encode("utf-8")
    fd = os.open(path, os.O_WRONLY | os.O_NOFOLLOW)
    try:
        written = 0
        while written < len(data):
            written += os.pwrite(fd, data[written:], written)
        os.ftruncate(fd, len(data))
        os.fsync(fd)
    finally:
        os.close(fd)


def pending_addresses(content: str, hostname: str, addresses: list[str]) -> list[str]:
    """Addresses of `addresses` that are not yet mapped to `hostname` in `content`."""
    return [a for a in addresses if update_content(content, hostname, a) != content]
