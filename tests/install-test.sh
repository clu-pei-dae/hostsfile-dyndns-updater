#!/bin/sh
# Installs the built .deb on a clean Ubuntu system (run as root, e.g. in a
# container) and exercises the packaged nginx site + backend end to end.
# Usage: install-test.sh path/to/package.deb
set -eu
deb=$1
export DEBIAN_FRONTEND=noninteractive

apt-get update -q
# fail2ban and logrotate first, so the package finds them like on a real host.
# systemd only for systemd-socket-activate (the test runs without systemd as PID 1).
apt-get install -y -q curl fail2ban logrotate systemd
apt-get install -y -q "$deb"

user=hostsfile-dyndns
as_user() { setpriv --reuid="$user" --regid="$user" --init-groups "$@"; }
owner() { stat -c '%U:%G %a' "$1"; }

test -x /usr/bin/hostsfile-dyndns-updater
test -L /etc/nginx/sites-enabled/hostsfile-dyndns-updater
getent passwd "$user"
[ "$(owner /etc/hostsfile-dyndns-updater)" = "root:$user 750" ]
[ "$(owner /etc/hostsfile-dyndns-updater/config.ini)" = "root:$user 640" ]
[ "$(owner /var/log/hostsfile-dyndns-updater)" = "root:root 755" ]
log=/var/log/hostsfile-dyndns-updater/requests.log
[ "$(owner "$log")" = "root:$user 660" ]
systemd-analyze verify /usr/lib/systemd/system/hostsfile-dyndns-updater.service \
    /usr/lib/systemd/system/hostsfile-dyndns-updater.socket \
    /usr/lib/systemd/system/hostsfile-dyndns-updater-apply.socket \
    /usr/lib/systemd/system/hostsfile-dyndns-updater-apply@.service
# Some sandboxes have no IPv6 at all; nginx cannot bind [::] there.
if [ ! -e /proc/net/if_inet6 ]; then
    sed -i --follow-symlinks '/listen \[::\]/d' /etc/nginx/sites-available/* /etc/nginx/sites-enabled/* 2>/dev/null || true
fi
nginx -t
# Unconfigured: the backend must refuse to start rather than run without hosts.
if hostsfile-dyndns-updater check-config >/dev/null 2>&1; then
    echo "default config unexpectedly valid" >&2
    exit 1
fi

password=correct-horse-battery-staple
hash=$(python3 -c "from hostsfile_dyndns_updater import passwords as p; print(p.hash_password('$password'))")
# Default hosts_file = /etc/hosts. In a container it is a bind mount, so this also
# covers the in-place update the helper does under its systemd sandbox.
cp /etc/hosts /tmp/hosts.orig
cat >> /etc/hostsfile-dyndns-updater/config.ini <<CFG
[host home]
hostname = home.example.org
username = fritzbox
password_hash = $hash
CFG
hostsfile-dyndns-updater check-config
as_user hostsfile-dyndns-updater check-config  # the API reads it through its group

# What the two socket units do, without systemd as PID 1:
run=/run/hostsfile-dyndns-updater
mkdir -p "$run"
#  - hostsfile-dyndns-updater-apply.socket (Accept=yes): one root `apply` per
#    connection, the connection on stdin/stdout
systemd-socket-activate --accept --inetd -l "$run/apply.sock" hostsfile-dyndns-updater apply &
helper=$!
#  - hostsfile-dyndns-updater.socket: the API as the unprivileged user, with the
#    listening socket passed in
systemd-socket-activate -l "$run/api.sock" \
    setpriv --reuid="$user" --regid="$user" --init-groups hostsfile-dyndns-updater serve &
activator=$!
sleep 1
chgrp "$user" "$run/apply.sock" && chmod 0660 "$run/apply.sock"
chgrp www-data "$run/api.sock" && chmod 0660 "$run/api.sock"
nginx
sleep 1

call() {
    curl -sk --noproxy '*' --resolve dyndns.example.org:443:127.0.0.1 -w ' %{http_code}' "$@"
}
url='https://dyndns.example.org/update?ipv4=8.8.8.8&domain=home.example.org'
[ "$(call -u "fritzbox:$password" "$url")" = "good 8.8.8.8
 200" ]
grep -q "^8.8.8.8	home.example.org" /etc/hosts
if ls /etc/.hosts.* 2>/dev/null; then  # no temporary files left behind
    echo "temporary hosts file left in /etc" >&2
    exit 1
fi

# The API runs unprivileged and could not have written /etc/hosts itself.
api=$(pgrep -u "$user" -f "hostsfile_dyndns_updater serve")
[ "$(ps -o user= -p "$api")" = "$user" ]
grep -q "^CapEff:[[:space:]]*0000000000000000$" "/proc/$api/status"
if as_user sh -c 'echo "# test" >> /etc/hosts' 2>/dev/null; then
    echo "the service user can write /etc/hosts" >&2
    exit 1
fi

# Unchanged address: answered by the API alone, no helper run, nothing logged.
[ "$(call -u "fritzbox:$password" "$url")" = "nochg 8.8.8.8
 200" ]
[ "$(call -u fritzbox:wrong "$url" | tail -c 3)" = "401" ]

# Request log (default log_level = changes): both parts logged the change with
# the same request id; the failure is logged by the API.
cat "$log"
[ "$(wc -l < "$log")" -eq 3 ]
rid=$(sed -n 's/.* APPLY result=good host=home.example.org addr=8.8.8.8 request=\([0-9a-f]\{16\}\)$/\1/p' "$log")
[ -n "$rid" ]
grep -q " OK status=200 result=good client=127.0.0.1 user=fritzbox host=home.example.org addr=8.8.8.8 request=$rid$" "$log"
grep -q " FAIL status=401 result=badauth client=127.0.0.1 user=-$" "$log"

# fail2ban: the jail's configuration is valid and its filter matches only the failure.
# The config test covers all jails; Ubuntu 22.04's default sshd jail reads
# /var/log/auth.log, which syslog creates on real hosts but not in containers.
touch /var/log/auth.log
fail2ban-client -t
fail2ban-regex "$log" hostsfile-dyndns-updater | tee /tmp/f2b.out
grep -q "Lines: 3 lines, 0 ignored, 1 matched, 2 missed" /tmp/f2b.out

# logrotate: rotate, then both parts must write into the new file (the API
# through its group).
logrotate -f -s /tmp/logrotate.state /etc/logrotate.d/hostsfile-dyndns-updater
[ -e "$log.1" ] && [ ! -s "$log" ]
[ "$(owner "$log")" = "root:$user 660" ]
sleep 11  # stay below the rate limit
[ "$(call -u fritzbox:wrong "$url" | tail -c 3)" = "401" ]
[ "$(call -u "fritzbox:$password" "https://dyndns.example.org/update?ipv4=8.8.4.4")" = "good 8.8.4.4
 200" ]
cat "$log"
grep -q " FAIL status=401 " "$log"
grep -q " APPLY result=good host=home.example.org addr=8.8.4.4 " "$log"
grep -q " OK status=200 result=good .* addr=8.8.4.4 " "$log"

kill "$api" "$helper" "$activator" 2>/dev/null || true
nginx -s stop
cp /tmp/hosts.orig /etc/hosts
apt-get purge -y -q hostsfile-dyndns-updater
test ! -e /etc/nginx/sites-enabled/hostsfile-dyndns-updater
test ! -e /etc/hostsfile-dyndns-updater
test ! -e /var/log/hostsfile-dyndns-updater
test ! -e /etc/fail2ban/jail.d/hostsfile-dyndns-updater.conf
test ! -e /etc/logrotate.d/hostsfile-dyndns-updater
echo "install test OK"
