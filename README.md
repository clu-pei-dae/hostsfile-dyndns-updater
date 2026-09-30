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
`/etc/nginx/sites-available/hostsfile-dyndns-updater`, and starts the service.
The tool then edits `/etc/hosts` on that host. It does **not** obtain TLS
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
    version: v0.1.1
```

Then run `ansible-galaxy collection install -r requirements.yml`. If the project
has no `requirements.yml` convention, install once with
`ansible-galaxy collection install "git+https://github.com/clu-pei-dae/hostsfile-dyndns-updater.git#/ansible,v0.1.1"`.

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
        hostsfile_dyndns_updater_github_release: v0.1.1   # same tag as in requirements.yml
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
rate limit, `..._listen_port`, `..._listen_ipv6` (set `false` on hosts without IPv6), `..._require_source_match`, other install methods)
are documented in the [role defaults](ansible/roles/hostsfile_dyndns_updater/defaults/main.yml);
leave them out unless the user asks.

### 5. Verify

* `ansible-playbook --syntax-check <playbook>`, then a run with `--check --diff`
  if the project uses it (the package download/install steps report changes on a
  first run).
* After a real run, on the target: `systemctl is-active hostsfile-dyndns-updater nginx`.
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
| Integrity of `/etc/hosts` | Hostnames are fixed in the configuration, never taken from the request (an optional `domain` parameter must match). Addresses are parsed with `ipaddress`; loopback, link-local, multicast, reserved and (by default) private addresses are rejected. The file is replaced atomically under a lock, mode and owner are preserved. |
| Abuse | nginx `limit_req` (6 requests/min per source, burst 5), optional `allow`/`deny` by source network, optional `require_source_match` (announced address must equal the connecting address; requests without a known source are rejected), method and size limits. |
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
`..._github_release` (`latest` or a tag like `v0.1.1`; pin it for reproducible
deployments), `..._deb_src` (for `file`: a .deb on the controller),
`..._server_name`, `..._tls_certificate(_key)`, `..._nginx_allow`, `..._hosts`
(list of `name`, `hostname`, `username`, `password_hash`). Keep the hashes in
Ansible Vault.

## Updating an existing installation

Configuration survives updates: `config.ini` and the nginx site are conffiles.
After the package is replaced, its `postinst` restarts the backend (if the
configuration is valid) and reloads nginx. `/etc/hosts` is not touched.

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
systemctl is-active hostsfile-dyndns-updater nginx
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

Run the backend locally: `PYTHONPATH=src python3 -m hostsfile_dyndns_updater serve -c my.ini`
(with `socket_group =` empty and `hosts_file` pointing to a scratch file).

## License

Apache-2.0, see [LICENSE](LICENSE).
