#!/bin/sh
# Build dist/hostsfile-dyndns-updater_<version>_all.deb (needs only dpkg-deb).
set -eu
root=$(cd "$(dirname "$0")/.." && pwd)
version=$(sed -n 's/^__version__ = "\(.*\)"/\1/p' "$root/src/hostsfile_dyndns_updater/__init__.py")
stage=$(mktemp -d)
trap 'rm -rf "$stage"' EXIT
chmod 0755 "$stage"

install -d "$stage/DEBIAN" \
    "$stage/usr/lib/python3/dist-packages" \
    "$stage/usr/bin" \
    "$stage/usr/lib/systemd/system" \
    "$stage/usr/share/doc/hostsfile-dyndns-updater" \
    "$stage/etc/nginx/sites-available" \
    "$stage/etc/fail2ban/filter.d" \
    "$stage/etc/fail2ban/jail.d" \
    "$stage/etc/logrotate.d"
install -d -m 0700 "$stage/etc/hostsfile-dyndns-updater"

cp -r "$root/src/hostsfile_dyndns_updater" "$stage/usr/lib/python3/dist-packages/"
find "$stage" -name __pycache__ -prune -exec rm -rf {} +
printf '#!/bin/sh\nexec /usr/bin/python3 -m hostsfile_dyndns_updater "$@"\n' > "$stage/usr/bin/hostsfile-dyndns-updater"
chmod 755 "$stage/usr/bin/hostsfile-dyndns-updater"
install -m 0644 "$root/etc/hostsfile-dyndns-updater.service" "$stage/usr/lib/systemd/system/"
install -m 0600 "$root/etc/config.ini" "$stage/etc/hostsfile-dyndns-updater/config.ini"
install -m 0644 "$root/etc/nginx-site.conf" "$stage/etc/nginx/sites-available/hostsfile-dyndns-updater"
install -m 0644 "$root/etc/fail2ban-filter.conf" "$stage/etc/fail2ban/filter.d/hostsfile-dyndns-updater.conf"
install -m 0644 "$root/etc/fail2ban-jail.conf" "$stage/etc/fail2ban/jail.d/hostsfile-dyndns-updater.conf"
install -m 0644 "$root/etc/logrotate.conf" "$stage/etc/logrotate.d/hostsfile-dyndns-updater"
install -m 0644 "$root/README.md" "$stage/usr/share/doc/hostsfile-dyndns-updater/README.md"
install -m 0644 "$root/LICENSE" "$stage/usr/share/doc/hostsfile-dyndns-updater/copyright"

sed "s/@VERSION@/$version/" "$root/packaging/debian/control.in" > "$stage/DEBIAN/control"
for f in conffiles postinst prerm postrm; do
    install -m "$(stat -c %a "$root/packaging/debian/$f")" "$root/packaging/debian/$f" "$stage/DEBIAN/$f"
done

mkdir -p "$root/dist"
dpkg-deb --root-owner-group --build "$stage" "$root/dist/hostsfile-dyndns-updater_${version}_all.deb"
