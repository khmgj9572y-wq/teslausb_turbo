#!/bin/bash
# tesla-control wrapper: one Bluetooth user at a time on hci0.
# tesla-control (go-ble) opens the HCI *user channel*, which takes hci0 away from BlueZ for the whole command; a BlueZ
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
exec /root/bin/tesla-control.real "$@"

