# Instructions for Claude

Project: `hostsfile-dyndns-updater` — nginx + a small Python backend that accept
FritzBox DynDNS updates and maintain `/etc/hosts`. Ships as a .deb and an
Ansible role. See `README.md` for the user-facing description.

## Layout

- `src/hostsfile_dyndns_updater/` — Python package, **standard library only**
  (must run on Ubuntu 22.04's Python 3.10 without pip). `hosts.py` edits the hosts
  file, `server.py` is the HTTP handler (Unix socket), `config.py` loads
  `config.ini`, `passwords.py` hashes/verifies, `cli.py` is the entry point.
- `etc/` — defaults shipped in the package: `config.ini`, `nginx-site.conf`,
  systemd unit.
- `packaging/` — `build-deb.sh` (plain `dpkg-deb`, no debhelper) and maintainer scripts.
- `ansible/roles/hostsfile_dyndns_updater/` — role; its templates mirror `etc/`.
- `tests/` — `unittest` tests, including a real Unix-socket server.

## Commands

- `make test` — must pass before every commit.
- `make lint` — shellcheck + Ansible syntax check.
- `tests/install-test.sh <deb>` — installs the package as root on a clean Ubuntu and
  runs nginx + backend end to end (CI runs it in 22.04/24.04/26.04 containers;
  don't run it on a machine you care about).
- `make deb` — build the package into `dist/` (git-ignored).

## Rules

- **Security first.** Never take the hostname from the request; never log query
  strings, credentials or password hashes; keep constant-time comparisons; reject
  rather than sanitize bad input. Any new request parameter needs strict
  validation and a test for the rejection path.
- Keep `etc/nginx-site.conf` and the role's `templates/nginx-site.conf.j2` in sync
  (same for `etc/config.ini` and `templates/config.ini.j2`); new options must
  appear in the config loader, default config, Ansible defaults/template, README
  and tests.
- Keep the systemd hardening in `etc/hostsfile-dyndns-updater.service`; loosen it
  only with a documented reason. The service must still work with `ProtectSystem=strict`.
- Conffiles must be listed in `packaging/debian/conffiles`; maintainer scripts must
  stay idempotent and pass `shellcheck`.
- Releases are tags `vX.Y.Z` matching `__version__`; `.github/workflows/build-deb.yml`
  builds, install-tests and publishes them. The asset name
  `hostsfile-dyndns-updater_all.deb` is used by the README install command — keep it.
- Bump `__version__` in `src/hostsfile_dyndns_updater/__init__.py` for releases;
  the .deb version is derived from it.
- Test data: use public addresses (e.g. `8.8.8.8`, `2606:4700::1`) — documentation
  ranges such as `203.0.113.0/24` count as private in Python and are rejected.
- README and code comments are in English. Do not add dependencies without a strong
  reason. Do not open pull requests unless asked.
