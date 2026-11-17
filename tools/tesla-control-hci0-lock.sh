#!/bin/bash
# tesla-control wrapper: one Bluetooth user at a time, and ONLY the onboard adapter.
# tesla-control (go-ble) opens the HCI *user channel*, which takes the adapter away from BlueZ for the whole command; a BlueZ
# user (bleak, bluetoothctl) running at that moment loses its adapter. Every BLE user takes /run/lock/hci0.lock first:
# this wrapper for all tesla-control callers (TeslaUSB keep-awake, web UI pairing/status, ssh-keepawake, car-alarm-watch),
# and BlueZ scripts around their BLE exchange (python: fcntl.flock(open('/run/lock/hci0.lock','a'), fcntl.LOCK_EX)).
# The lock is held until the real binary exits (fd 9 is inherited through exec).
# Without -bt-adapter the binary opened a USB dongle, despite its help text
# ("defaults to hci0"). The wrapper resolves the onboard UART adapter via sysfs (device path has /serial, no /usb).
# HARD PIN (rule: onboard Bluetooth = car key only, everything else on USB adapters). No onboard adapter -> refuse (exit 69 + log), never run unpinned. A caller's own -bt-adapter is accepted
# only if it names the onboard adapter; anything else is refused.
SYSFS=${TESLA_CONTROL_SYSFS:-/sys/class/bluetooth}
LOCK=/run/lock/hci0.lock
WAIT=${TESLA_CONTROL_LOCK_WAIT:-120}
refuse() {
  echo "tesla-control wrapper: $1" >&2
  logger -t tesla-control-lock "refused: $1" 2>/dev/null || true
  exit 69
}
ADAPTER=
for h in "$SYSFS"/hci*; do
  [ -e "$h" ] || continue
  n=$(basename "$h")
  [[ "$n" =~ ^hci[0-9]+$ ]] || continue
  p=$(readlink -f "$h")
  case "$p" in */usb*) continue;; */serial*) ADAPTER=$n; break;; esac
done
[ -n "$ADAPTER" ] || refuse "no onboard UART Bluetooth adapter found; not running (pinned to onboard)"
prev=
for a in "$@"; do
  case "$a" in
    -bt-adapter=*|--bt-adapter=*) [ "${a#*=}" = "$ADAPTER" ] || refuse "caller asked for adapter '${a#*=}', only onboard $ADAPTER is allowed";;
  esac
  case "$prev" in
    -bt-adapter|--bt-adapter) [ "$a" = "$ADAPTER" ] || refuse "caller asked for adapter '$a', only onboard $ADAPTER is allowed";;
  esac
  prev=$a
done
case "$prev" in -bt-adapter|--bt-adapter) refuse "-bt-adapter without a value";; esac
exec 9>>"$LOCK" || { echo "tesla-control wrapper: cannot open $LOCK" >&2; exit 75; }
if ! flock -w "$WAIT" 9; then
  echo "tesla-control wrapper: hci0 busy, gave up after ${WAIT}s waiting for $LOCK" >&2
  logger -t tesla-control-lock "gave up after ${WAIT}s waiting for $LOCK" 2>/dev/null
  exit 75
fi
for a in "$@"; do
  case "$a" in -bt-adapter|--bt-adapter|-bt-adapter=*|--bt-adapter=*) exec /root/bin/tesla-control.real "$@";; esac
done
exec /root/bin/tesla-control.real -bt-adapter "$ADAPTER" "$@"
