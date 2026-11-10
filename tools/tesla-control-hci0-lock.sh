#!/bin/bash
# tesla-control wrapper: serialize and explicitly select the onboard UART adapter.
# tesla-control (go-ble) opens the HCI *user channel*, which takes an adapter away from BlueZ; a BlueZ
# user (bleak, bluetoothctl) running at that moment loses its adapter. Every BLE user takes /run/lock/hci0.lock first:
# this wrapper for all tesla-control callers (TeslaUSB keep-awake, web UI pairing/status, ssh-keepawake), and BlueZ
# scripts around their BLE exchange (python: fcntl.flock(open('/run/lock/hci0.lock','a'), fcntl.LOCK_EX)).
# The lock is held until the real binary exits (fd 9 is inherited through exec).
LOCK=/run/lock/hci0.lock
WAIT=${TESLA_CONTROL_LOCK_WAIT:-120}
exec 9>>"$LOCK" || { echo "tesla-control wrapper: cannot open $LOCK" >&2; exit 75; }
if ! flock -w "$WAIT" 9; then
  echo "tesla-control wrapper: hci0 busy, gave up after ${WAIT}s waiting for $LOCK" >&2
  logger -t tesla-control-lock "gave up after ${WAIT}s waiting for $LOCK" 2>/dev/null
  exit 75
fi
for argument in "$@"; do
  case "$argument" in
    -bt-adapter|--bt-adapter|-bt-adapter=*|--bt-adapter=*) exec /root/bin/tesla-control.real "$@";;
  esac
done
adapter=
for entry in /sys/class/bluetooth/hci*; do
  [ -e "$entry" ] || continue
  name=$(basename "$entry")
  [[ "$name" =~ ^hci[0-9]+$ ]] || continue
  device=$(readlink -f "$entry")
  case "$device" in
    */usb*) continue;;
    */serial*) adapter=$name; break;;
  esac
done
if [ -z "$adapter" ]; then
  logger -t tesla-control-lock 'no onboard UART Bluetooth adapter found; running without -bt-adapter' 2>/dev/null || true
  exec /root/bin/tesla-control.real "$@"
fi
exec /root/bin/tesla-control.real -bt-adapter "$adapter" "$@"

