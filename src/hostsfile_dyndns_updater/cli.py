"""Command line entry point."""

import argparse
import getpass
import logging
import sys

from . import __version__, config, passwords
from .server import Server


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="hostsfile-dyndns-updater", description=__doc__)
    parser.add_argument("--version", action="version", version=__version__)
    sub = parser.add_subparsers(dest="command", required=True)
    serve = sub.add_parser("serve", help="run the API backend (used by the systemd unit)")
    serve.add_argument("-c", "--config", default=config.DEFAULT_CONFIG)
    check = sub.add_parser("check-config", help="validate the configuration file")
    check.add_argument("-c", "--config", default=config.DEFAULT_CONFIG)
    sub.add_parser("hash-password", help="print a password hash for config.ini")
    args = parser.parse_args(argv)

    if args.command == "hash-password":
        pw = getpass.getpass("Password: ")
        if pw != getpass.getpass("Repeat: "):
            print("passwords differ", file=sys.stderr)
            return 1
        if len(pw) < 16:
            print("password must have at least 16 characters", file=sys.stderr)
            return 1
        print(passwords.hash_password(pw))
        return 0

    try:
        cfg = config.load(args.config)
    except config.ConfigError as exc:
        print(f"configuration error: {exc}", file=sys.stderr)
        return 2
    if args.command == "check-config":
        print(f"OK: {len(cfg.hosts)} host(s) configured")
        return 0

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    server = Server(cfg)
    logging.info("listening on %s", cfg.socket)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
