"""INI configuration loader.

    [server]
    socket = /run/hostsfile-dyndns-updater/api.sock
    socket_group = www-data
    hosts_file = /etc/hosts
    allow_private_addresses = no
    require_source_match = no
    allow_query_credentials = no

    [host home]
    hostname = home.example.org
    username = fritzbox
    password_hash = pbkdf2_sha256$...
"""

import configparser
import ipaddress
from dataclasses import dataclass, field

from . import passwords
from .hosts import valid_hostname

DEFAULT_CONFIG = "/etc/hostsfile-dyndns-updater/config.ini"


class ConfigError(Exception):
    pass


@dataclass(frozen=True)
class HostEntry:
    name: str
    hostname: str
    username: str
    password_hash: str


@dataclass(frozen=True)
class Config:
    socket: str = "/run/hostsfile-dyndns-updater/api.sock"
    socket_group: str = "www-data"
    hosts_file: str = "/etc/hosts"
    allow_private_addresses: bool = False
    require_source_match: bool = False
    allow_query_credentials: bool = False
    hosts: dict[str, HostEntry] = field(default_factory=dict)  # keyed by username


def load(path: str = DEFAULT_CONFIG) -> Config:
    parser = configparser.ConfigParser(interpolation=None)
    try:
        with open(path, encoding="utf-8") as fh:
            parser.read_file(fh)
    except OSError as exc:
        raise ConfigError(f"cannot read {path}: {exc}") from exc
    except configparser.Error as exc:
        raise ConfigError(f"cannot parse {path}: {exc}") from exc

    server = parser["server"] if parser.has_section("server") else {}
    defaults = Config()

    def boolean(key: str, default: bool) -> bool:
        try:
            return parser.getboolean("server", key, fallback=default)
        except ValueError as exc:
            raise ConfigError(f"[server] {key}: {exc}") from exc

    hosts: dict[str, HostEntry] = {}
    for section in parser.sections():
        if not section.startswith("host "):
            continue
        name = section[5:].strip()
        sec = parser[section]
        try:
            entry = HostEntry(
                name=name,
                hostname=sec["hostname"].strip().lower(),
                username=sec["username"].strip(),
                password_hash=sec["password_hash"].strip(),
            )
        except KeyError as exc:
            raise ConfigError(f"[{section}] missing option {exc}") from exc
        if not valid_hostname(entry.hostname):
            raise ConfigError(f"[{section}] invalid hostname {entry.hostname!r}")
        if not entry.username or ":" in entry.username:
            raise ConfigError(f"[{section}] username must be non-empty and contain no ':'")
        try:
            passwords.parse_hash(entry.password_hash)
        except ValueError as exc:
            raise ConfigError(f"[{section}] {exc}") from exc
        if entry.username in hosts:
            raise ConfigError(f"[{section}] duplicate username {entry.username!r}")
        hosts[entry.username] = entry
    if not hosts:
        raise ConfigError("no [host <name>] section configured")

    return Config(
        socket=server.get("socket", defaults.socket),
        socket_group=server.get("socket_group", defaults.socket_group),
        hosts_file=server.get("hosts_file", defaults.hosts_file),
        allow_private_addresses=boolean("allow_private_addresses", False),
        require_source_match=boolean("require_source_match", False),
        allow_query_credentials=boolean("allow_query_credentials", False),
        hosts=hosts,
    )


def address_allowed(address: ipaddress.IPv4Address | ipaddress.IPv6Address,
                    allow_private: bool) -> bool:
    if (address.is_unspecified or address.is_loopback or address.is_multicast
            or address.is_link_local or address.is_reserved):
        return False
    return allow_private or not address.is_private
