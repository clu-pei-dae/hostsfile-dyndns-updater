# hostsfile-dyndns-updater

Receive DynDNS updates from an AVM FRITZ!Box and mirror the announced IP address
into `/etc/hosts` of a Linux host. Useful when you want a hostname such as
`home.example.org` to resolve to your current home IP on a server, without
running a DNS server.

```
FritzBox --HTTPS GET /update?...--> nginx (TLS, rate limit) --unix socket--> backend --> /etc/hosts
```

* **nginx** terminates TLS (1.2/1.3), rate-limits, allows only `GET /update`, and
  never logs the query string.
* The **backend** (Python 3, standard library only) authenticates the request,
  validates the input and edits `/etc/hosts` atomically. It listens on a Unix
  socket only, so it is unreachable from the network.
* Shipped as a **.deb** package for Ubuntu (systemd unit, nginx site, default
  config) and configurable with an **Ansible** role.

## Security model

| Property | Mechanism |
| --- | --- |
| Confidentiality / integrity in transit | HTTPS only (TLS 1.2+, HSTS). The FritzBox validates the server certificate, so use a real certificate (e.g. Let's Encrypt). |
| Authenticity of the sender | Per-host username and password, sent via HTTP Basic auth. Passwords are stored as PBKDF2-HMAC-SHA256 hashes (600k iterations) and compared in constant time; unknown users cost the same time as wrong passwords. |
| Integrity of `/etc/hosts` | Hostnames are fixed in the configuration, never taken from the request (an optional `domain` parameter must match). Addresses are parsed with `ipaddress`; loopback, link-local, multicast, reserved and (by default) private addresses are rejected. The file is replaced atomically under a lock, mode and owner are preserved. |
| Abuse | nginx `limit_req` (6 requests/min per source, burst 5), optional `allow`/`deny` by source network, optional `require_source_match` (announced address must equal the connecting address), method and size limits. |
| Blast radius | The backend runs under a hardened systemd unit (no new privileges, `ProtectSystem=strict` with only `/etc` writable, syscall filter, only `AF_UNIX`, only `CAP_CHOWN`). |

## Installation (Ubuntu 22.04 / 24.04 / 26.04)

One command, using the latest GitHub release:

```sh
curl -fsSLo /tmp/hostsfile-dyndns-updater.deb \
  https://github.com/clu-pei-dae/hostsfile-dyndns-updater/releases/latest/download/hostsfile-dyndns-updater_all.deb \
  && sudo apt install /tmp/hostsfile-dyndns-updater.deb
```

Releases ship a `SHA256SUMS` file. Every release is installed and exercised
(nginx + backend, purge) on all three Ubuntu versions by CI before it is published.

Build it yourself instead:

```sh
make deb                                  # or: packaging/build-deb.sh
sudo apt install ./dist/hostsfile-dyndns-updater_*_all.deb
```

The package depends on `nginx` and `ssl-cert`. It installs:

| File | Purpose |
| --- | --- |
| `/etc/hostsfile-dyndns-updater/config.ini` | Backend configuration (conffile, mode 0600) |
| `/etc/nginx/sites-available/hostsfile-dyndns-updater` | nginx site (conffile), enabled by symlink on first install |
| `/usr/lib/systemd/system/hostsfile-dyndns-updater.service` | Backend service |
| `/usr/bin/hostsfile-dyndns-updater` | CLI |

The default nginx site listens on 443 with the snake-oil certificate so that
nginx starts out of the box. **Replace it with a trusted certificate and set
`server_name`.** The backend is not started until at least one host is
configured.

### Configure manually

```sh
hostsfile-dyndns-updater hash-password     # prompts, prints a hash (min. 16 characters)
sudoedit /etc/hostsfile-dyndns-updater/config.ini
```

```ini
[host home]
hostname = home.example.org
username = fritzbox
password_hash = pbkdf2_sha256$600000$...$...
```

Then `sudo hostsfile-dyndns-updater check-config && sudo systemctl restart hostsfile-dyndns-updater`.
All `[server]` options are documented in the shipped `config.ini`.

### Configure with Ansible

The role lives in `ansible/roles/hostsfile_dyndns_updater`. See
`ansible/playbooks/site.yml` for an example and `defaults/main.yml` for all
variables.

```sh
cd ansible
ansible-playbook -i inventory.example.ini playbooks/site.yml
```

By default the role downloads the package from the GitHub release on the target
host and verifies it against the release's `SHA256SUMS`. Key variables:
`hostsfile_dyndns_updater_install_method` (`github`, `file` or `apt`),
`..._github_release` (`latest` or a tag like `v0.1.0`; pin it for reproducible
deployments), `..._deb_src` (for `file`: a .deb on the controller),
`..._server_name`, `..._tls_certificate(_key)`, `..._nginx_allow`, `..._hosts`
(list of `name`, `hostname`, `username`, `password_hash`). Keep the hashes in
Ansible Vault.

## FRITZ!Box setup

*Internet → Freigaben → DynDNS*, provider **Benutzerdefiniert**:

| Field | Value |
| --- | --- |
| Update-URL | `https://dyndns.example.org/update?ipv4=<ipaddr>&ipv6=<ip6addr>&domain=<domain>` |
| Domainname | `home.example.org` (must equal `hostname` in the config) |
| Benutzername / Kennwort | the configured username and the plain password |

The credentials are sent as HTTP Basic authentication. If your firmware does not
send them that way, add `&username=<username>&password=<pass>` to the URL and set
`allow_query_credentials = yes` (less safe: credentials appear in the URL).

Responses follow DynDNS conventions: `good <ip>` (changed), `nochg <ip>`
(unchanged), `badauth` (401), `badip` (400), `nohost` (403).

## `/etc/hosts` semantics

* Per IP family (IPv4/IPv6) the hostname is mapped to the announced address.
* An existing line that maps only that hostname is updated in place (comments
  are kept). If other names share the line, the hostname is removed from it and a
  new line is appended. Otherwise a line marked
  `# managed by hostsfile-dyndns-updater` is appended.
* Unchanged addresses do not rewrite the file. `::` (no IPv6) is ignored.

## Releasing

Bump `__version__`, commit, then `git tag vX.Y.Z && git push origin vX.Y.Z`. The
`Build and release .deb` workflow builds the package, runs the install test on
Ubuntu 22.04, 24.04 and 26.04, and publishes a GitHub release. Pushes to `main`
and pull requests build and test without releasing.

## Development

```sh
make test      # unit tests (stdlib unittest, no dependencies)
make lint      # shellcheck for packaging scripts, ansible syntax check if available
make deb       # build dist/*.deb
```

Run the backend locally: `PYTHONPATH=src python3 -m hostsfile_dyndns_updater serve -c my.ini`
(with `socket_group =` empty and `hosts_file` pointing to a scratch file).

## License

Apache-2.0, see [LICENSE](LICENSE).
