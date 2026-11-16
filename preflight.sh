#!/bin/sh
# Read-only probe. Does not print TeslaUSB configuration, keys, or tokens.
set -u

section() { printf '\n== %s ==\n' "$1"; }

section 'Operating system'
sed -n '1,6p' /etc/os-release 2>/dev/null || true
uname -r

section 'TeslaUSB service and storage'
systemctl is-active teslausb.service 2>/dev/null || true
findmnt -T /backingfiles 2>/dev/null || true
df -h /backingfiles 2>/dev/null || true
xfs_info /backingfiles 2>/dev/null | grep -o 'reflink=[01]' || true

section 'USB driver tree and network interfaces'
lsusb 2>/dev/null || true
lsusb -t 2>/dev/null || true
ip -br addr 2>/dev/null || true
ip route 2>/dev/null || true

section 'HDMI adapter status and advertised modes'
for file in /sys/class/drm/*HDMI*/status /sys/class/drm/*HDMI*/modes; do
    if [ -f "$file" ]; then
        printf '%s\n' "$file"
        cat "$file"
    fi
done

section 'Pi undervoltage / throttling flags'
vcgencmd get_throttled 2>/dev/null || true

section 'Web file browser mounts (configuration only)'
if [ -e /backingfiles/music_disk.bin ] || [ -e /backingfiles/lightshow_disk.bin ] ||
   [ -e /backingfiles/boombox_disk.bin ]; then
    missing=
    [ -d /var/www/html/fs ] || missing="$missing /var/www/html/fs"
    [ -f /etc/auto.master.d/www.autofs ] || missing="$missing /etc/auto.master.d/www.autofs"
    [ -x /root/bin/auto.www ] || missing="$missing /root/bin/auto.www"
    if [ -n "$missing" ]; then
        echo "FAIL: file browser mount configuration is missing:$missing"
    elif ! grep -Eq '^[[:space:]]*/var/www/html/fs[[:space:]]+/root/bin/auto[.]www([[:space:]]|$)' /etc/auto.master.d/www.autofs; then
        echo 'FAIL: browser autofs master map does not point to the expected executable'
    elif ! systemctl is-active --quiet autofs.service; then
        echo 'FAIL: browser mount configuration exists, but autofs is inactive'
    else
        echo 'CONFIGURED: reload autofs after repair and verify an actual protected browser listing'
    fi
    echo 'See IMAGE_PLAN.md; this probe does not mount images or prove a working browser listing.'
else
    echo 'No music/lightshow/boombox image; file browser mounts not expected'
fi
