#!/bin/bash
# install_ssh_keepawake.sh: keep the car awake while an SSH session on the Pi is in use (see KEEPAWAKE.md).
#   sudo tools/install_ssh_keepawake.sh          install + enable
#   sudo tools/install_ssh_keepawake.sh --dry    install, then run once in dry mode (logs decisions, never nudges)
# Needs TeslaUSB's BLE keep-awake prerequisites: /root/bin/tesla-control, a paired /root/.ble key, and
# export TESLA_BLE_VIN=... in /root/teslausb_setup_variables.conf.
set -euo pipefail
HERE=$(cd "$(dirname "$0")/.." && pwd)
[ "$(id -u)" = 0 ] || { echo "run as root"; exit 1; }
[ "${1:-}" = '' ] || [ "${1:-}" = '--dry' ] || { echo 'Unknown option' >&2; exit 1; }
[ -x /root/bin/tesla-control ] && [ -e /root/.ble/key_private.pem ] || { echo "ABORT: TeslaUSB BLE key/tool missing"; exit 1; }
grep -q '^export TESLA_BLE_VIN=' /root/teslausb_setup_variables.conf || { echo "ABORT: TESLA_BLE_VIN not set"; exit 1; }
mount / -o remount,rw
restore_root() {
  result=$?
  trap - EXIT
  sync
  if ! mount / -o remount,ro; then
    echo 'ERROR: root remains writable' >&2
    result=1
  fi
  exit "$result"
}
trap restore_root EXIT
install -m 0755 -o root -g root "$HERE/ssh_keepawake.py" /usr/local/sbin/ssh-keepawake
install -m 0644 -o root -g root "$HERE/systemd/ssh-keepawake.service" /etc/systemd/system/ssh-keepawake.service
systemctl daemon-reload
if [ "${1:-}" = "--dry" ]; then
  systemd-run --quiet --unit=ssh-keepawake-dry --collect /usr/local/sbin/ssh-keepawake --dry
  echo "dry run started: journalctl -fu ssh-keepawake-dry ; stop: systemctl stop ssh-keepawake-dry"
else
  systemctl enable --now ssh-keepawake.service
  echo "enabled: journalctl -fu ssh-keepawake ; status: sudo ssh-keepawake --status"
fi

