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
