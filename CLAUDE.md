# Instructions for Claude

Project: `hostsfile-dyndns-updater` — nginx + a small Python backend that accept
FritzBox DynDNS updates and maintain `/etc/hosts`. Ships as a .deb and an
Ansible role. See `README.md` for the user-facing description.

## Layout

- `src/hostsfile_dyndns_updater/` — Python package, **standard library only**
  (must run on Ubuntu 22.04's Python 3.10 without pip). `hosts.py` edits the hosts
  file, `server.py` is the unprivileged HTTP API (Unix socket), `apply.py` the root
  helper that writes the hosts file plus its client, `config.py` loads
  `config.ini`, `passwords.py` hashes/verifies, `requestlog.py` writes the request
  log, `cli.py` is the entry point (`serve`, `apply`, `check-config`, `hash-password`).
- `etc/` — defaults shipped in the package: `config.ini`, `nginx-site.conf`,
  systemd units (API `.socket` + `.service` as user `hostsfile-dyndns`, helper
  `-apply.socket` with `Accept=yes` + `-apply@.service` as root), fail2ban filter
  and jail, logrotate configuration.
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
- **Privilege separation.** The API service must stay unprivileged: it never gets
  write access to the hosts file, capabilities, or network access; everything
  that writes the hosts file goes through the root helper. The helper stays
  minimal and trusts nothing it receives: it re-validates every field against
  `config.ini` (hostname must be configured, addresses via `address_allowed`,
  request id format) and logs only validated values. Do not add features to the
  helper that the API could do unprivileged.
- Every change of the hosts file is logged by both parts with the same
  `request=` id (`APPLY` from the helper, `OK`/`ERROR` from the API); keep it that
  way when touching either side, and keep `FAIL` lines unchanged for fail2ban.
- Files both parts touch: `config.ini` and the request log are
  `root:hostsfile-dyndns` (0640 / 0660) in a root-owned directory, so the API
  cannot swap them for symlinks that root would then write through. Keep
  `postinst`, the logrotate `create` line, the Ansible role and `install-test.sh`
  consistent with that.
- The request log format is an interface: the fail2ban filter
  (`etc/fail2ban-filter.conf`) parses it. Only write values the server produced or
  validated (never raw usernames, headers or query values), or clients can forge
  lines that get others banned. Changing the format needs matching filter changes
  and the `fail2ban-regex` check in `tests/install-test.sh`.
- Keep `etc/fail2ban-jail.conf` and the role's `templates/fail2ban-jail.conf.j2` in sync.
- Keep `etc/nginx-site.conf` and the role's `templates/nginx-site.conf.j2` in sync
  (same for `etc/config.ini` and `templates/config.ini.j2`); new options must
  appear in the config loader, default config, Ansible defaults/template, README
  and tests.
- Keep the systemd hardening of all four units; loosen it only with a documented
  reason. Both services must work with `ProtectSystem=strict`; the helper may
  write only `/etc/hosts` (the file, not `/etc`) and the log directory, which is
  why `hosts.py` falls back to an in-place update.
- `tests/install-test.sh` emulates the socket units with `systemd-socket-activate`
  and runs the API via `setpriv` as `hostsfile-dyndns`; when unit behaviour
  changes, change the emulation too.
- Conffiles must be listed in `packaging/debian/conffiles`; maintainer scripts must
  stay idempotent and pass `shellcheck`.
- Releases are tags `vX.Y.Z` matching `__version__`; `.github/workflows/build-deb.yml`
  builds, install-tests and publishes them. The asset name
  `hostsfile-dyndns-updater_all.deb` is used by the README install command — keep it.
- Bump `__version__` in `src/hostsfile_dyndns_updater/__init__.py` for releases;
  the .deb version is derived from it. `version` in `ansible/galaxy.yml` (the role is
  published as the collection `clu_pei_dae.hostsfile_dyndns_updater`, installed from
  git via `#/ansible`) must match; the release workflow checks both.
- Changing `etc/config.ini` or `etc/nginx-site.conf` creates `*.dpkg-dist` files on
  upgrades (see README "Updating an existing installation"); mention it in the release notes.
- The README section "Add it to an existing Ansible playbook" is the entry point for
  AI assistants integrating the tool elsewhere. Keep it accurate when role variables,
  the CLI (`hash-password --stdin`) or install methods change.
- Test data: use public addresses (e.g. `8.8.8.8`, `2606:4700::1`) — documentation
  ranges such as `203.0.113.0/24` count as private in Python and are rejected.
- README and code comments are in English. Do not add dependencies without a strong
  reason. Do not open pull requests unless asked.
