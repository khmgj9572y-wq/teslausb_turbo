#!/bin/bash
# install_hci0_lock.sh: put the hci0 lock wrapper in front of TeslaUSB's tesla-control.
#   sudo tools/install_hci0_lock.sh            install (moves the real binary to /root/bin/tesla-control.real)
#   sudo tools/install_hci0_lock.sh --remove   restore the plain binary
# Re-run after any TeslaUSB setup/upgrade that reinstalls /root/bin/tesla-control (ssh-keepawake logs a warning then).
set -euo pipefail
HERE=$(cd "$(dirname "$0")/.." && pwd)
B=/root/bin/tesla-control
[ "$(id -u)" = 0 ] || { echo "run as root"; exit 1; }
[ "${1:-}" = '' ] || [ "${1:-}" = '--remove' ] || { echo 'Unknown option' >&2; exit 1; }
test -f "$HERE/tools/tesla-control-hci0-lock.sh"
test -f "$HERE/examples/hci0-lock.tmpfiles.conf"
getent group bluetooth >/dev/null
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
if [ "${1:-}" = "--remove" ]; then
  [ -x $B.real ] || { echo "nothing to remove"; exit 0; }
  mv -f $B.real $B; rm -f /etc/tmpfiles.d/hci0-lock.conf; echo "wrapper removed"; exit 0
fi
if head -c 4 "$B" | grep -q $'\x7fELF'; then
  # An upstream reinstall can replace the wrapper while leaving the older
  # .real binary behind. Preserve that older binary and wrap the new one.
  if [ -e "$B.real" ]; then
    backup=$(mktemp -d /root/tesla-control-lock-backup.XXXXXX)
    chmod 700 "$backup"
    cp -p "$B.real" "$backup/tesla-control.real"
  fi
  install -m 0755 -o root -g root "$B" "$B.real.new"
  mv -f "$B.real.new" "$B.real"
elif ! grep -q "tesla-control wrapper" $B; then
  echo "ABORT: $B is neither the binary nor this wrapper"; exit 1
fi
install -m 0644 -o root -g root "$HERE/examples/hci0-lock.tmpfiles.conf" /etc/tmpfiles.d/hci0-lock.conf
systemd-tmpfiles --create /etc/tmpfiles.d/hci0-lock.conf
install -m 0755 -o root -g root "$HERE/tools/tesla-control-hci0-lock.sh" $B.new && mv -f $B.new $B
echo "wrapper installed: $B -> $B.real (lock $(stat -c '%U:%G %a' /run/lock/hci0.lock))"

