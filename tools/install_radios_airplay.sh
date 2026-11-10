#!/bin/bash -eu
# Stage the split-radio receiver without changing the active Wi-Fi connection.
[ "$EUID" -eq 0 ] || { echo 'Run as root' >&2; exit 1; }
src=$(cd "$(dirname "$0")/.." && pwd)
test -e /teslausb/TESLAUSB_SETUP_FINISHED
mountpoint -q /mutable
test -f /etc/teslausb-car-link.ini
. /etc/teslausb-car-wifi.env
id "$PORTAL_USER" >/dev/null
stage_uid=$(id -u "$PORTAL_USER")
stage_gid=$(id -g "$PORTAL_USER")
test "$stage_uid" -ne 0
stage_root_ro=false
case ",$(findmnt -no OPTIONS /)," in *,ro,*) stage_root_ro=true ;; esac
stage_swap_mask=false
stage_nft_mask=false
case "$(systemctl is-enabled rpi-resize-swap-file.service 2>/dev/null || true)" in
  masked*) ;;
  *) systemctl mask --runtime --now rpi-resize-swap-file.service >/dev/null; stage_swap_mask=true ;;
esac
restore_root() {
  stage_exit=$?
  trap - EXIT
  # PackageKit may keep its on-disk transaction DB open after apt finishes.
  systemctl stop packagekit.service >/dev/null 2>&1 || true
  sync
  if $stage_root_ro; then
    if ! mount -o remount,ro /; then
      echo 'Root remains writable; a controlled reboot is required' >&2
      stage_exit=1
    fi
  fi
  if $stage_swap_mask; then systemctl unmask --runtime rpi-resize-swap-file.service >/dev/null; fi
  if $stage_nft_mask; then systemctl unmask --runtime nftables.service >/dev/null; fi
  exit "$stage_exit"
}
trap restore_root EXIT
mount -o remount,rw /
if [ -f /etc/rpi/swap.conf ]; then
  install -d -m 755 /etc/rpi/swap.conf.d
  printf '[Main]\nMechanism=zram\n' > /etc/rpi/swap.conf.d/90-teslausb-readonly.conf
  systemctl daemon-reload
fi
install -d -m 700 /root/car-link-radio-backup
stage_backup=$(mktemp -d /root/car-link-radio-backup/stage.XXXXXX)
cp -a /usr/local/lib/teslausb-car-link "$stage_backup/installed-modules"
tar -cpf "$stage_backup/network-settings.tar" /etc/netplan /etc/NetworkManager/system-connections /etc/avahi/avahi-daemon.conf 2>/dev/null
chmod 600 "$stage_backup/network-settings.tar"
stage_packages=(uxplay=1.71.1-1 \
  gstreamer1.0-tools gstreamer1.0-plugins-good gstreamer1.0-plugins-bad \
  gstreamer1.0-libav gstreamer1.0-gl libavahi-compat-libdnssd-dev \
  avahi-utils dnsmasq-base)
if [ ! -x /usr/sbin/nft ]; then
  case "$(systemctl is-enabled nftables.service 2>/dev/null || true)" in
    masked*) ;;
    *) systemctl mask --runtime nftables.service >/dev/null; stage_nft_mask=true ;;
  esac
  stage_packages+=(nftables)
fi
DEBIAN_FRONTEND=noninteractive apt-get update
DEBIAN_FRONTEND=noninteractive apt-get install -y "${stage_packages[@]}"
lib=/usr/local/lib/teslausb-car-link
# Install imported modules before updating the services that import them.
install -m 755 "$src/radio_policy.py" "$src/display_owner.py" "$src/nm_storage.py" "$lib/"
if [ ! -f /etc/teslausb-car-radios.json ]; then
  PYTHONPATH="$lib" python3 - "$stage_gid" <<'PY'
import json,os,sys
from pathlib import Path
import radio_policy
board=radio_policy.interface_for('board',{'board_driver':'brcmfmac'})
if not board: raise SystemExit('Onboard Wi-Fi radio is unavailable')
mac=radio_policy.permanent_mac(board)
config={'split_enabled':False,'uplink_mac':mac,'uplink_driver':'brcmfmac',
        'hotspot_mac':'','hotspot_driver':'mt7921u','future_uplink_driver':'mt7921u',
        'future_uplink_mac':'','future_hotspot_mac':'','board_driver':'brcmfmac',
        'hotspot':{'ssid':'Car Screen','address':'10.42.0.1/24','channel':36},
        'airplay':{'name':'Car Screen','audio_enabled':False}}
path=Path('/etc/teslausb-car-radios.json')
path.write_text(json.dumps(config,indent=2)+'\n')
os.chown(path,0,int(sys.argv[1])); path.chmod(0o640)
PY
fi
for file in car_policy.py heartbeat.py wifi_admin.py car_control.py video_web.py \
  radio_services.py airplay_receiver.py portal_session.sh portal_cleanup.sh
do install -m 755 "$src/$file" "$lib/$file"; done
install -m 755 "$src/tools/rebind_uplink.py" "$lib/rebind_uplink.py"
install -m 644 "$src/dnssd_scope.c" "$lib/dnssd_scope.c"
cc -shared -fPIC -O2 -Wall -Wextra -Werror \
  -I/usr/include/avahi-compat-libdns_sd "$lib/dnssd_scope.c" \
  -ldl -o "$lib/dnssd_scope.so"
chmod 755 "$lib/dnssd_scope.so"
stage_airplay_dir=/mutable/car-link/airplay
install -d -m 700 -o "$stage_uid" -g "$stage_gid" \
  "$stage_airplay_dir" "$stage_airplay_dir/config" "$stage_airplay_dir/cache"
install -m 644 "$src/systemd/car-hotspot.service" \
  "$src/systemd/car-hotspot-dhcp.service" /etc/systemd/system/
sed -e "s/REPLACE_WITH_OS_USER/$PORTAL_USER/g" -e "s/REPLACE_WITH_OS_UID/$stage_uid/g" \
  "$src/systemd/car-airplay.service" > /etc/systemd/system/car-airplay.service
chmod 644 /etc/systemd/system/car-airplay.service
install -m 644 "$src/web/moonlight/index.html" /var/www/html/moonlight/index.html
systemctl daemon-reload
for plugin in v4l2h264dec v4l2convert waylandsink
do
  runuser -u "$PORTAL_USER" -- env HOME="$stage_airplay_dir" \
    XDG_CACHE_HOME="$stage_airplay_dir/cache" gst-inspect-1.0 "$plugin" >/dev/null
done
echo 'Radio and AirPlay code staged; active Wi-Fi settings were not rebound.'
echo 'Activate the tested MT7921AU before enabling the hotspot and AirPlay units.'
