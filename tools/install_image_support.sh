#!/bin/bash
# Reapply after TeslaUSB setup/updates. Does not activate or reassign radios.
set -euo pipefail
[ "$EUID" -eq 0 ] || { echo 'Run as root' >&2; exit 1; }
cleanup_legacy=false
case "${1:-}" in '') ;; --remove-legacy-fake-clock) cleanup_legacy=true;; *) echo 'Unknown option' >&2; exit 1;; esac
src=$(cd "$(dirname "$0")/.." && pwd)
lib=/usr/local/lib/teslausb-car-link
mountpoint -q /mutable
getent passwd systemd-timesync >/dev/null
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
backup=$(mktemp -d /root/car-image-support-backup.XXXXXX)
chmod 700 "$backup"
install -d -m 755 "$lib" "$lib/tools" "$lib/examples"
put() {
  if [ -f "$2" ]; then cp -p "$2" "$backup/$(basename "$2")"; fi
  install -m "$3" "$1" "$2.new"; mv -f "$2.new" "$2"
}
for name in mutable_state.py ble_prepare.py ble_adapter_check.py radio_policy.py nm_storage.py; do
  put "$src/$name" "$lib/$name" 755
done
put "$src/tools/rebind_uplink.py" "$lib/rebind_uplink.py" 755
# Keep the re-wrapper and its dependencies available locally after staging is removed.
for name in install_hci0_lock.sh tesla-control-hci0-lock.sh; do
  put "$src/tools/$name" "$lib/tools/$name" 755
done
put "$src/examples/hci0-lock.tmpfiles.conf" "$lib/examples/hci0-lock.tmpfiles.conf" 644
python3 -B "$lib/mutable_state.py" prepare
install -d -m 700 /var/lib/bluetooth
if [ ! -d /var/lib/systemd/timesync ]; then
  install -d -m 755 -o systemd-timesync -g systemd-timesync /var/lib/systemd/timesync
fi
for unit in var-lib-bluetooth.mount var-lib-systemd-timesync.mount \
  clock-save.service clock-save.timer clock-save-shutdown.service car-ble-unblock.service; do
  put "$src/systemd/$unit" "/etc/systemd/system/$unit" 644
done
if $cleanup_legacy && ! command -v fake-hwclock >/dev/null 2>&1 && [ -f /mutable/etc/fake-hwclock.data ]; then
  cp -p /mutable/etc/fake-hwclock.data "$backup/legacy-fake-hwclock.data"
  rm /mutable/etc/fake-hwclock.data
  echo 'Obsolete saved-clock file removed after a local backup'
fi
systemctl daemon-reload
systemd-analyze verify /etc/systemd/system/var-lib-bluetooth.mount \
  /etc/systemd/system/var-lib-systemd-timesync.mount /etc/systemd/system/clock-save.service \
  /etc/systemd/system/clock-save-shutdown.service /etc/systemd/system/car-ble-unblock.service
systemctl enable var-lib-bluetooth.mount var-lib-systemd-timesync.mount \
  clock-save.timer clock-save-shutdown.service car-ble-unblock.service
if [ -x /root/bin/tesla-control ]; then
  bash "$lib/tools/install_hci0_lock.sh"
else
  echo 'Install the Tesla command binary, then run the local tools/install_hci0_lock.sh'
fi
echo 'Image support enabled for boot; Bluetooth, networking, and radio roles were not restarted or changed'
