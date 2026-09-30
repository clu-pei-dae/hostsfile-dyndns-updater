#!/bin/sh
# Installs the built .deb on a clean Ubuntu system (run as root, e.g. in a
# container) and exercises the packaged nginx site + backend end to end.
# Usage: install-test.sh path/to/package.deb
set -eu
deb=$1
export DEBIAN_FRONTEND=noninteractive

apt-get update -q
# fail2ban and logrotate first, so the package finds them like on a real host.
apt-get install -y -q curl fail2ban logrotate
apt-get install -y -q "$deb"

test -x /usr/bin/hostsfile-dyndns-updater
test -L /etc/nginx/sites-enabled/hostsfile-dyndns-updater
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
hosts=$(mktemp)
echo "127.0.0.1 localhost" > "$hosts"
cat > /etc/hostsfile-dyndns-updater/config.ini <<CFG
[server]
hosts_file = $hosts
[host home]
hostname = home.example.org
username = fritzbox
password_hash = $hash
CFG
hostsfile-dyndns-updater check-config

mkdir -p /run/hostsfile-dyndns-updater
hostsfile-dyndns-updater serve &
backend=$!
nginx
sleep 2

call() {
    curl -sk --noproxy '*' --resolve dyndns.example.org:443:127.0.0.1 -w ' %{http_code}' "$@"
}
url='https://dyndns.example.org/update?ipv4=8.8.8.8&domain=home.example.org'
[ "$(call -u "fritzbox:$password" "$url")" = "good 8.8.8.8
 200" ]
grep -q "^8.8.8.8	home.example.org" "$hosts"
sleep 11  # stay below the rate limit
[ "$(call -u fritzbox:wrong "$url" | tail -c 3)" = "401" ]

# Request log (default log_level = changes)
log=/var/log/hostsfile-dyndns-updater/requests.log
cat "$log"
[ "$(wc -l < "$log")" -eq 2 ]
grep -q " OK status=200 result=good client=127.0.0.1 user=fritzbox host=home.example.org addr=8.8.8.8$" "$log"
grep -q " FAIL status=401 result=badauth client=127.0.0.1 user=-$" "$log"

# fail2ban: the jail's configuration is valid and its filter matches only the failure.
# The config test covers all jails; Ubuntu 22.04's default sshd jail reads
# /var/log/auth.log, which syslog creates on real hosts but not in containers.
touch /var/log/auth.log
fail2ban-client -t
fail2ban-regex "$log" hostsfile-dyndns-updater | tee /tmp/f2b.out
grep -q "Lines: 2 lines, 0 ignored, 1 matched, 1 missed" /tmp/f2b.out

# logrotate: rotate, then the backend must write into the new file
logrotate -f -s /tmp/logrotate.state /etc/logrotate.d/hostsfile-dyndns-updater
[ -e "$log.1" ] && [ ! -s "$log" ]
sleep 11  # stay below the rate limit
[ "$(call -u fritzbox:wrong "$url" | tail -c 3)" = "401" ]
grep -q " FAIL status=401 " "$log"

kill "$backend"
nginx -s stop
apt-get purge -y -q hostsfile-dyndns-updater
test ! -e /etc/nginx/sites-enabled/hostsfile-dyndns-updater
test ! -e /etc/hostsfile-dyndns-updater
test ! -e /var/log/hostsfile-dyndns-updater
test ! -e /etc/fail2ban/jail.d/hostsfile-dyndns-updater.conf
test ! -e /etc/logrotate.d/hostsfile-dyndns-updater
echo "install test OK"
