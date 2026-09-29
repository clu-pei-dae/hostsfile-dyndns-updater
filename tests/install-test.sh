#!/bin/sh
# Installs the built .deb on a clean Ubuntu system (run as root, e.g. in a
# container) and exercises the packaged nginx site + backend end to end.
# Usage: install-test.sh path/to/package.deb
set -eu
deb=$1
export DEBIAN_FRONTEND=noninteractive

apt-get update -q
apt-get install -y -q curl "$deb"

test -x /usr/bin/hostsfile-dyndns-updater
test -L /etc/nginx/sites-enabled/hostsfile-dyndns-updater
# Some sandboxes have no IPv6 at all; nginx cannot bind [::] there.
if [ ! -e /proc/net/if_inet6 ]; then
    sed -i '/listen \[::\]/d' /etc/nginx/sites-available/* /etc/nginx/sites-enabled/* 2>/dev/null || true
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

kill "$backend"
nginx -s stop
apt-get purge -y -q hostsfile-dyndns-updater
test ! -e /etc/nginx/sites-enabled/hostsfile-dyndns-updater
test ! -e /etc/hostsfile-dyndns-updater
echo "install test OK"
