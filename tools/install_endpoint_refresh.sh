#!/bin/bash
# install_endpoint_refresh.sh: keep the tunnel endpoint IP in step with the home DDNS name.
#   sudo tools/install_endpoint_refresh.sh        install + enable the 5-minute timer
#   sudo tools/install_endpoint_refresh.sh --dry  install, then only print what a run would change
# Needs /etc/teslausb-car-endpoint.env (copy examples/car-endpoint.env and set CAR_ENDPOINT_NAME).
set -euo pipefail
HERE=$(cd "$(dirname "$0")/.." && pwd)
[ "$(id -u)" = 0 ] || { echo "run as root"; exit 1; }
[ "${1:-}" = '' ] || [ "${1:-}" = '--dry' ] || { echo 'Unknown option' >&2; exit 1; }
[ -f /etc/teslausb-car-endpoint.env ] || { echo "ABORT: /etc/teslausb-car-endpoint.env missing (see examples/car-endpoint.env)"; exit 1; }
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
install -m 0755 -o root -g root "$HERE/car_endpoint_refresh.sh" /usr/local/sbin/car-endpoint-refresh
install -m 0644 -o root -g root "$HERE/systemd/car-endpoint-refresh.service" /etc/systemd/system/car-endpoint-refresh.service
install -m 0644 -o root -g root "$HERE/systemd/car-endpoint-refresh.timer" /etc/systemd/system/car-endpoint-refresh.timer
systemctl daemon-reload
if [ "${1:-}" = "--dry" ]; then
  /usr/local/sbin/car-endpoint-refresh --dry-run
else
  systemctl enable --now car-endpoint-refresh.timer
  echo "enabled: systemctl list-timers car-endpoint-refresh.timer ; log: journalctl -t car-endpoint-refresh"
fi
