# hostsfile-dyndns-updater

Receive DynDNS updates from an AVM FRITZ!Box and mirror the announced IP address
into `/etc/hosts` of a Linux host. Useful when you want a hostname such as
`home.example.org` to resolve to your current home IP on a server, without
running a DNS server.

```
FritzBox --HTTPS GET /update--> nginx --api.sock--> API service ----apply.sock----> root helper --> /etc/hosts
                                (TLS,               (user hostsfile-dyndns,         (started per change,
                                 rate limit)         no privileges)                  writes only /etc/hosts)
```

* **nginx** terminates TLS (1.2/1.3), rate-limits, allows only `GET /update`, and
  never logs the query string.
* The **API service** (Python 3, standard library only) authenticates the
  request and validates the input. It runs as the unprivileged system user
  `hostsfile-dyndns` without any capability and cannot write `/etc/hosts`.
* For an actual change it hands a one-line request to the **root helper**, which
  systemd starts only for that change. The helper checks the request again
  against the configuration, updates `/etc/hosts` and answers; the FritzBox gets
  `good` only after the file was really written. Both parts log the change with a
  common request id.
* Shipped as a **.deb** package for Ubuntu (systemd units, nginx site, default
  config, fail2ban jail, logrotate) and configurable with an **Ansible** role.

## Add it to an existing Ansible playbook

> **For AI assistants:** if you were asked to add this tool to an Ansible
> playbook, follow this section step by step. Everything you need is here; do not
> invent variables. Ask the user for anything marked **ask**.

The role ships as the Ansible collection `clu_pei_dae.hostsfile_dyndns_updater`
(subdirectory `ansible/` of this repository), installable straight from GitHub.
Role name: `clu_pei_dae.hostsfile_dyndns_updater.hostsfile_dyndns_updater`.

**What the role does on the target host** (Ubuntu 22.04/24.04/26.04): installs
the .deb (downloaded from the GitHub release and verified against its
`SHA256SUMS`), installs `nginx` as a dependency, writes
`/etc/hostsfile-dyndns-updater/config.ini` and the nginx site
`/etc/nginx/sites-available/hostsfile-dyndns-updater`, and starts the sockets and
the service (the package creates the system user `hostsfile-dyndns`). The tool
then edits `/etc/hosts` on that host. It does **not** obtain TLS
certificates and does not touch other nginx sites.

### 1. Inspect the existing project

Find out, and reuse instead of replacing: the inventory group of the target host,
whether the play already uses `become`, how secrets are stored (Ansible Vault,
`group_vars`, `vars_files`), whether a `requirements.yml` and a `roles/` or
`collections/` directory exist, and whether nginx is already managed on the
target (then the port/`server_name` must not clash with existing sites).

**Ask the user** (unless already evident): the public DNS name the FritzBox will
call (`server_name`, e.g. `dyndns.example.org`), the hostname to maintain in
`/etc/hosts` (e.g. `home.example.org`), and where the TLS certificate and key are
on the target (a certificate the FritzBox trusts, e.g. Let's Encrypt; the
default snake-oil certificate is rejected by the FritzBox).

### 2. Install the collection

Add to the project's `requirements.yml` (create it if missing, keep existing
entries; pin a release tag, see the
[releases page](https://github.com/clu-pei-dae/hostsfile-dyndns-updater/releases)):

```yaml
collections:
  - name: git+https://github.com/clu-pei-dae/hostsfile-dyndns-updater.git#/ansible
    type: git
    version: v1.0.0
```

Then run `ansible-galaxy collection install -r requirements.yml`. If the project
has no `requirements.yml` convention, install once with
`ansible-galaxy collection install "git+https://github.com/clu-pei-dae/hostsfile-dyndns-updater.git#/ansible,v1.0.0"`.

### 3. Create the credentials

The password is for the FritzBox; the playbook only ever gets its hash. Generate a
random password (at least 16 characters) and its hash without installing anything
on the controller:

```sh
password=$(openssl rand -base64 24)
hash=$(printf '%s\n' "$password" | python3 -c "import hashlib,base64,os,sys;p=sys.stdin.readline().rstrip('\n').encode();s=os.urandom(16);print('pbkdf2_sha256\$600000\$%s\$%s'%(base64.b64encode(s).decode(),base64.b64encode(hashlib.pbkdf2_hmac('sha256',p,s,600000)).decode()))")
```

(On a host with the package installed:
`printf '%s\n' "$password" | hostsfile-dyndns-updater hash-password --stdin`.)

Store `$hash` the way the project stores secrets, preferably Ansible Vault
(`ansible-vault encrypt_string "$hash" --name vault_dyndns_password_hash`).
**Tell the user the plain password once** so they can enter it in the FritzBox;
never write it to a file or commit it.

### 4. Add the role to the play

Add it to the play of the target hosts (adapt group, vars location and names):

```yaml
- hosts: dyndns_server          # group that should run the endpoint
  become: true
  roles:
    - role: clu_pei_dae.hostsfile_dyndns_updater.hostsfile_dyndns_updater
      vars:
        hostsfile_dyndns_updater_github_release: v1.0.0   # same tag as in requirements.yml
        hostsfile_dyndns_updater_server_name: dyndns.example.org
        hostsfile_dyndns_updater_tls_certificate: /etc/letsencrypt/live/dyndns.example.org/fullchain.pem
        hostsfile_dyndns_updater_tls_certificate_key: /etc/letsencrypt/live/dyndns.example.org/privkey.pem
        hostsfile_dyndns_updater_hosts:
          - name: home
            hostname: home.example.org
            username: fritzbox
            password_hash: "{{ vault_dyndns_password_hash }}"
```

If the play already runs roles or tasks that create the certificate, place this
role after them. Optional variables (source restriction `..._nginx_allow`,
rate limit, `..._log_level`, fail2ban settings `..._fail2ban_*` (the jail is set up
automatically when fail2ban is installed on the target), `..._listen_port`, `..._listen_ipv6` (set `false` on hosts without IPv6), `..._require_source_match`, other install methods)
are documented in the [role defaults](ansible/roles/hostsfile_dyndns_updater/defaults/main.yml);
leave them out unless the user asks.

### 5. Verify

* `ansible-playbook --syntax-check <playbook>`, then a run with `--check --diff`
  if the project uses it (the package download/install steps report changes on a
  first run).
* After a real run, on the target:
  `systemctl is-active hostsfile-dyndns-updater.socket hostsfile-dyndns-updater-apply.socket hostsfile-dyndns-updater nginx`.
* From anywhere, with the real password:
  `curl -u fritzbox:"$password" "https://dyndns.example.org/update?ipv4=<a public IPv4>&domain=home.example.org"`
  must answer `good <ip>` (first time) or `nochg <ip>`; a wrong password gives
  `badauth`. Remember this writes to `/etc/hosts` on the target; use the FritzBox's
  real address or revert afterwards.

### 6. Hand over to the user

Tell the user how to configure the FRITZ!Box (see [FRITZ!Box setup](#fritzbox-setup)),
which values to enter (update URL, domain, username, the plain password) and that
re-running the playbook upgrades the tool when the pinned release is changed.

## Security model

| Property | Mechanism |
| --- | --- |
| Confidentiality / integrity in transit | HTTPS only (TLS 1.2+, HSTS). The FritzBox validates the server certificate, so use a real certificate (e.g. Let's Encrypt). |
| Authenticity of the sender | Per-host username and password, sent via HTTP Basic auth. Passwords are stored as PBKDF2-HMAC-SHA256 hashes (600k iterations) and compared in constant time; unknown users cost the same PBKDF2 work as wrong passwords, so usernames cannot be probed. |
| Integrity of `/etc/hosts` | Hostnames are fixed in the configuration, never taken from the request (an optional `domain` parameter must match). Addresses are parsed with `ipaddress`; loopback, link-local, multicast, reserved and (by default) private addresses are rejected. The root helper repeats all checks against the configuration before writing. Writes happen under a lock and never leave the file empty (see [`/etc/hosts` semantics](#etchosts-semantics)). |
| Abuse | fail2ban jail (if fail2ban is installed) bans sources with 5 rejected requests in 10 minutes for 1 hour; nginx `limit_req` (6 requests/min per source, burst 5), optional `allow`/`deny` by source network, optional `require_source_match` (announced address must equal the connecting address; requests without a known source are rejected), method and size limits. |
| Blast radius | Privilege separation. The network-facing API service runs as user `hostsfile-dyndns` with no capabilities and no network access of its own (`PrivateNetwork=yes`); it can write only the request log. The root helper is started per change, reads one JSON line of at most 4 KiB, has no capabilities either and may write only the file `/etc/hosts` and the request log (`ProtectSystem=strict`, `ReadWritePaths=/etc/hosts`). Only the API's group may connect to the helper's socket. Both units: no new privileges, syscall filter, only `AF_UNIX`. |

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

The package depends on `nginx`, `ssl-cert` and `adduser`. It creates the system
user and group `hostsfile-dyndns` (kept on purge, as usual for Debian packages)
and installs:

| File | Purpose |
| --- | --- |
| `/etc/hostsfile-dyndns-updater/config.ini` | Configuration (conffile, `root:hostsfile-dyndns` 0640: the API reads it through its group) |
| `/etc/nginx/sites-available/hostsfile-dyndns-updater` | nginx site (conffile), enabled by symlink on first install |
| `/usr/lib/systemd/system/hostsfile-dyndns-updater.socket` | Socket nginx connects to (`/run/hostsfile-dyndns-updater/api.sock`, group `www-data`) |
| `/usr/lib/systemd/system/hostsfile-dyndns-updater.service` | API service, user `hostsfile-dyndns` |
| `/usr/lib/systemd/system/hostsfile-dyndns-updater-apply.socket` | Socket of the root helper (`/run/hostsfile-dyndns-updater/apply.sock`, group `hostsfile-dyndns`) |
| `/usr/lib/systemd/system/hostsfile-dyndns-updater-apply@.service` | Root helper, one instance per change |
| `/usr/bin/hostsfile-dyndns-updater` | CLI |
| `/var/log/hostsfile-dyndns-updater/requests.log` | Request log (created by `postinst`, `root:hostsfile-dyndns` 0660) |
| `/etc/fail2ban/filter.d/hostsfile-dyndns-updater.conf`, `/etc/fail2ban/jail.d/hostsfile-dyndns-updater.conf` | fail2ban filter and jail (conffiles; used only if fail2ban is installed) |
| `/etc/logrotate.d/hostsfile-dyndns-updater` | logrotate configuration (conffile; used only if logrotate is installed) |

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
All `[server]` options are documented in the shipped `config.ini`. Keep the file
readable for the service (`root:hostsfile-dyndns`, mode 0640); `sudoedit` does.

To maintain a file other than `/etc/hosts` (`hosts_file`), allow the helper to
write it: `sudo systemctl edit hostsfile-dyndns-updater-apply@.service` and add
`[Service]` / `ReadWritePaths=/path/to/file` (the Ansible role does this itself).

### Configure with Ansible

See [Add it to an existing Ansible playbook](#add-it-to-an-existing-ansible-playbook).
For working on this repository, `ansible/playbooks/site.yml` is a complete example
and `ansible/inventory.example.ini` an inventory:

```sh
cd ansible
ansible-playbook -i inventory.example.ini playbooks/site.yml
```

By default the role downloads the package from the GitHub release on the target
host and verifies it against the release's `SHA256SUMS`. Re-running the playbook upgrades the package when a newer release (`github` with
`latest`), a different .deb (`file`) or a newer apt version (`apt`) is available;
otherwise nothing changes and the service is not restarted. Key variables:
`hostsfile_dyndns_updater_install_method` (`github`, `file` or `apt`),
`..._github_release` (`latest` or a tag like `v1.0.0`; pin it for reproducible
deployments), `..._deb_src` (for `file`: a .deb on the controller),
`..._server_name`, `..._tls_certificate(_key)`, `..._nginx_allow`, `..._hosts`
(list of `name`, `hostname`, `username`, `password_hash`). Keep the hashes in
Ansible Vault.

## Updating an existing installation

Configuration survives updates: `config.ini` and the nginx site are conffiles.
After the package is replaced, its `postinst` restarts the sockets and the API
service (if the configuration is valid) and reloads nginx. `/etc/hosts` is not
touched.

**Upgrading from 0.x to 1.0** switches from one root service to the split
described above. The package does it on its own: it creates the user
`hostsfile-dyndns`, gives `config.ini` and the request log to its group, stops
the old service and starts the sockets. Nothing in `config.ini` has to change (the
new `apply_socket` option has the right default). If you manage
`/etc/hostsfile-dyndns-updater/config.ini` with other tools, keep it
`root:hostsfile-dyndns` 0640, otherwise the API cannot read it.

**Manual installation** (latest release):

```sh
curl -fsSLo /tmp/hostsfile-dyndns-updater.deb \
  https://github.com/clu-pei-dae/hostsfile-dyndns-updater/releases/latest/download/hostsfile-dyndns-updater_all.deb
sudo apt-get install -o Dpkg::Options::=--force-confdef -o Dpkg::Options::=--force-confold \
  /tmp/hostsfile-dyndns-updater.deb
```

For a specific version replace `latest/download` with `download/vX.Y.Z`. To verify
the download, fetch `SHA256SUMS` from the same release and run
`sha256sum -c --ignore-missing SHA256SUMS` in the download directory.

The `--force-conf*` options keep your edited `config.ini` and nginx site without
an interactive prompt. If a release changes these defaults, the new versions are
stored next to them as `*.dpkg-dist`; compare and merge them by hand if you want
the changes, then delete them:

```sh
sudo diff /etc/hostsfile-dyndns-updater/config.ini{,.dpkg-dist}
sudo diff /etc/nginx/sites-available/hostsfile-dyndns-updater{,.dpkg-dist}
```

Check the result:

```sh
dpkg-query -W hostsfile-dyndns-updater      # installed version
sudo hostsfile-dyndns-updater check-config
systemctl is-active hostsfile-dyndns-updater.socket hostsfile-dyndns-updater-apply.socket \
  hostsfile-dyndns-updater nginx
```

**Ansible:** the role rewrites both files on every run, so no `.dpkg-dist` merging
is needed. Raise the tag in `requirements.yml` and in
`hostsfile_dyndns_updater_github_release`, then run:

```sh
ansible-galaxy collection install -r requirements.yml --force   # role of the new version
ansible-playbook <playbook>
```

With `hostsfile_dyndns_updater_github_release: latest`, re-running the playbook
alone is enough. With `install_method: apt`, it upgrades to the newest version in
the repository.

**Self-built package:** `sudo apt-get install <same options as above> ./dist/hostsfile-dyndns-updater_<version>_all.deb`.

## Request log, fail2ban and logrotate

Both parts write to `/var/log/hostsfile-dyndns-updater/requests.log`: the API
service one line per request, the root helper one line per change of the hosts
file. A change therefore always shows up twice, linked by the same `request` id,
first from the helper (the file was written) and then from the API (the FritzBox
was answered):

```
2026-09-30T10:15:02+0200 APPLY result=good host=home.example.org addr=8.8.8.8,2606:4700::1 request=5f0c9e1a7d3b2c64
2026-09-30T10:15:02+0200 OK status=200 result=good client=8.8.8.8 user=fritzbox host=home.example.org addr=8.8.8.8,2606:4700::1 request=5f0c9e1a7d3b2c64
2026-09-30T11:15:03+0200 OK status=200 result=nochg client=8.8.8.8 user=fritzbox host=home.example.org addr=8.8.8.8
2026-09-30T10:16:40+0200 FAIL status=401 result=badauth client=198.51.100.4 user=-
2026-09-30T10:17:12+0200 FAIL status=400 result=badip client=8.8.8.8 user=fritzbox host=home.example.org
2026-09-30T12:00:01+0200 APPLY result=error host=home.example.org addr=8.8.4.4 reason=writefailed request=0b7d51e9c2a4f318
2026-09-30T12:00:01+0200 ERROR status=500 result=911 client=8.8.8.8 user=fritzbox host=home.example.org addr=8.8.4.4 request=0b7d51e9c2a4f318 detail=apply-writefailed
```

* `APPLY` (root helper): `result=good` the file was changed, `result=nochg` it
  already had these addresses, `result=error` with a `reason`: `writefailed`
  (details in `journalctl -u 'hostsfile-dyndns-updater-apply@*'`), or `badhost`,
  `badip`, `badrequest` for a request the helper refused.
* `OK` (API): accepted request. `result=good` means the address changed,
  `result=nochg` means it was already set (the helper is not involved then, so
  there is no `request` id). `addr` lists the announced addresses.
* `FAIL` (API): rejected request. `result` is the reason (`badauth` wrong or
  missing credentials, `badip` invalid or forbidden address, `nohost` wrong
  `domain`, `notfound` wrong path, `badmethod`, `toolong`).
* `ERROR` (API): the FritzBox got `911`. `detail` says why: `apply-unavailable`
  (helper not reachable), `apply-<reason>` (helper refused or failed), `internal`
  (see `journalctl -u hostsfile-dyndns-updater`).
* `client` is the address nginx saw. `user` is shown only after successful
  authentication; attempted usernames and passwords are never logged.

What is logged is set by `log_level` in `config.ini` (Ansible:
`hostsfile_dyndns_updater_log_level`):

| `log_level` | `OK` lines | `APPLY` / `FAIL` / `ERROR` lines |
| --- | --- | --- |
| `changes` (default) | only `result=good` (address changed) | always |
| `all` | every accepted request, including `nochg` | always |

Requests nginx rejects itself (rate limit, other paths, methods other than GET)
never reach the backend; they are in nginx' access log
`/var/log/nginx/hostsfile-dyndns-updater.access.log`.

**fail2ban.** If fail2ban is installed (before or after this package), the jail
`hostsfile-dyndns-updater` is active: a client with **5 `FAIL` lines within 10
minutes is banned from HTTP/HTTPS for 1 hour**. That covers wrong passwords as well
as other invalid requests. A FritzBox with a wrong password or a bad update URL is
banned too, so add it to `ignoreip` if its address is static. Change the defaults in
a `.local` file instead of editing the jail:

```ini
# /etc/fail2ban/jail.d/hostsfile-dyndns-updater.local
[hostsfile-dyndns-updater]
ignoreip = 127.0.0.1/8 ::1 84.1.2.0/24
bantime  = 1d
```

Then `sudo fail2ban-client reload`. Useful commands:
`sudo fail2ban-client status hostsfile-dyndns-updater` (banned addresses),
`sudo fail2ban-client set hostsfile-dyndns-updater unbanip <ip>`, and
`sudo fail2ban-regex /var/log/hostsfile-dyndns-updater/requests.log hostsfile-dyndns-updater`
(test the filter). With Ansible, the role writes the jail when fail2ban is installed
on the target; see the `hostsfile_dyndns_updater_fail2ban_*` variables in the
[role defaults](ansible/roles/hostsfile_dyndns_updater/defaults/main.yml).

**logrotate.** If logrotate is installed (a recommended dependency), the log is
rotated weekly and 8 compressed weeks are kept. Both parts reopen the log after
rotation by themselves; no restart is needed.

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
* Unchanged addresses do not rewrite the file (and do not start the root
  helper). `::` (no IPv6) is ignored.
* The helper may write the file but not `/etc`, so it updates the file in place:
  it overwrites the content and then cuts off the rest, so readers never see an
  empty file. Where the directory is writable (e.g. running without systemd), the
  file is replaced atomically instead.

## Releasing

Bump `__version__` and `version` in `ansible/galaxy.yml`, commit, then `git tag vX.Y.Z && git push origin vX.Y.Z`. The
`Build and release .deb` workflow builds the package, runs the install test on
Ubuntu 22.04, 24.04 and 26.04, and publishes a GitHub release. If a release for the tag already exists (e.g. created in the web UI), the packages are attached to it instead. Pushes to `main`
and pull requests build and test without releasing.

## Development

```sh
make test      # unit tests (stdlib unittest, no dependencies)
make lint      # shellcheck for packaging scripts, ansible syntax check if available
make deb       # build dist/*.deb
```

Run the backend locally in one process: `PYTHONPATH=src python3 -m hostsfile_dyndns_updater serve -c my.ini`
with `socket_group =` and `apply_socket =` empty and `hosts_file` pointing to a
scratch file. The helper alone: `echo '{"request": "0123456789abcdef", "host":
"home.example.org", "addresses": ["8.8.8.8"]}' | python3 -m hostsfile_dyndns_updater apply -c my.ini`.
`tests/install-test.sh` shows how both parts run as under systemd (via
`systemd-socket-activate` and `setpriv`).

## License

Apache-2.0, see [LICENSE](LICENSE).
