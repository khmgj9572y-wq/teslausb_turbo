#!/bin/bash
# Add read-only 4G telemetry without modifying nginx/Caddy or modem settings.
set -euo pipefail
[ "$EUID" -eq 0 ] || { echo 'Run as root' >&2; exit 1; }
src=$(cd "$(dirname "$0")/.." && pwd)
lib=/usr/local/lib/teslausb-car-link
web=/var/www/html
test -f /etc/teslausb-car-link.ini
test -f "$lib/car_policy.py"
test -f "$web/index.html"
root_was_ro=false
case ",$(findmnt -no OPTIONS /)," in *,ro,*) root_was_ro=true;; esac
restore() {
  result=$?
  trap - EXIT
  sync
  if $root_was_ro && ! mount -o remount,ro /; then
    echo 'ERROR: root remains writable' >&2; result=1
  fi
  exit "$result"
}
mount -o remount,rw /
trap restore EXIT
backup=$(mktemp -d /root/car-link-lte-backup.XXXXXX)
chmod 700 "$backup"
for file in "$lib/lte_info.py" /usr/local/sbin/ik41-info \
  /etc/systemd/system/car-lte-info.service /etc/systemd/system/car-lte-info.timer \
  /etc/sudoers.d/car-lte-info "$web/cgi-bin/car-lte.cgi"; do
  if [ -f "$file" ]; then cp -p "$file" "$backup/$(basename "$file")"; fi
done
if [ -d "$web/4g" ]; then cp -a "$web/4g" "$backup/web-4g"; fi
install -d -m 755 "$lib" "$web/4g"
put() { install -m "$3" "$1" "$2.new"; mv -f "$2.new" "$2"; }
cc -O2 -Wall -Wextra -Werror "$src/tools/ik41_info.c" \
  $(pkg-config --cflags --libs libusb-1.0) -o "$backup/ik41-info-built"
put "$backup/ik41-info-built" /usr/local/sbin/ik41-info 755
put "$src/tools/ik41_info.c" "$lib/ik41_info.c" 644
put "$src/lte_info.py" "$lib/lte_info.py" 755
put "$src/web/lte_api.cgi" "$web/cgi-bin/car-lte.cgi" 755
put "$src/web/4g/index.html" "$web/4g/index.html" 644
put "$src/web/4g/new-ui-link.js" "$web/4g/new-ui-link.js" 644
for unit in car-lte-info.service car-lte-info.timer; do
  put "$src/systemd/$unit" "/etc/systemd/system/$unit" 644
done
printf '%s\n' 'www-data ALL=(root) NOPASSWD: /usr/local/lib/teslausb-car-link/lte_info.py status, /usr/local/lib/teslausb-car-link/lte_info.py identity, /usr/local/lib/teslausb-car-link/lte_info.py supported' > "$backup/sudoers"
chmod 440 "$backup/sudoers"
visudo -cf "$backup/sudoers" >/dev/null
put "$backup/sudoers" /etc/sudoers.d/car-lte-info 440
python3 "$src/tools/install_lte_ui.py"
systemctl daemon-reload
systemd-analyze verify /etc/systemd/system/car-lte-info.service
systemctl enable --now car-lte-info.timer
systemctl start car-lte-info.service
echo '4G tab installed; existing web access policy and modem mode are unchanged'
