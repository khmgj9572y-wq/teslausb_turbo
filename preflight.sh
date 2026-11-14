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

section 'Web file browser mounts'
if [ -e /backingfiles/music_disk.bin ] || [ -e /backingfiles/lightshow_disk.bin ] ||
   [ -e /backingfiles/boombox_disk.bin ]; then
    missing=
    [ -d /var/www/html/fs ] || missing="$missing /var/www/html/fs"
    [ -f /etc/auto.master.d/www.autofs ] || missing="$missing /etc/auto.master.d/www.autofs"
    [ -x /root/bin/auto.www ] || missing="$missing /root/bin/auto.www"
    if [ -n "$missing" ]; then
        echo "FAIL: drive images exist but the file browser mounts are missing:$missing"
        echo "      rerun TeslaUSB's web setup step after the images are in place (IMAGE_PLAN.md)"
    else
        echo "OK: file browser mounts configured"
    fi
else
    echo "no music/lightshow/boombox image; file browser mounts not expected"
fi
