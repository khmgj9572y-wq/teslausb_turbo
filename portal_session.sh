#!/bin/bash -eu
# Temporary browser for a captive portal. The phone controls this Xvfb display
# through noVNC; the Pi's browser traffic uses the new Wi-Fi connection.

user=${PORTAL_USER:?Set PORTAL_USER in /etc/teslausb-car-wifi.env}
state=/run/car-wifi-portal
home=/mutable/car-link/portal-browser
mkdir -p "$state" "$home"
chmod 700 "$state" "$home"
chown "$user" "$home"

endpoint=$(python3 - <<'PY'
import configparser, ipaddress
parser = configparser.ConfigParser(interpolation=None)
if not parser.read('/etc/teslausb-car-link.ini'):
    raise SystemExit('car-link config missing')
print(ipaddress.IPv4Address(parser['policy']['wg_endpoint_ip']))
PY
)

lte_if=
for path in /sys/class/net/*
do
  if [ -e "$path/device/driver" ] &&
     [ "$(basename "$(readlink -f "$path/device/driver")")" = rndis_host ]
  then
    lte_if=${path##*/}
    break
  fi
done
[ -n "$lte_if" ] || { echo '4G interface unavailable' >&2; exit 1; }

lte_gw=$(ip -4 route show default dev "$lte_if" | awk '$1=="default" && $2=="via" {print $3; exit}')
wifi_gw=$(ip -4 route show default dev wlan0 | awk '$1=="default" && $2=="via" {print $3; exit}')
[ -n "$lte_gw" ] && [ -n "$wifi_gw" ] || { echo 'Gateway unavailable' >&2; exit 1; }
[ -z "$(ip -4 route show exact "$endpoint/32")" ] || { echo 'Endpoint route already customized' >&2; exit 1; }
[ -z "$(ip -4 route show default | grep ' metric 550$' || true)" ] || {
  echo 'Temporary Wi-Fi route already exists' >&2; exit 1;
}

printf '%s\n%s\n%s\n%s\n' "$endpoint" "$lte_gw" "$lte_if" "$wifi_gw" > "$state/route"
chmod 600 "$state/route"
ip route add "$endpoint/32" via "$lte_gw" dev "$lte_if"
ip route add default via "$wifi_gw" dev wlan0 metric 550

runuser -u "$user" -- env HOME="$home" XDG_CONFIG_HOME="$home/config" \
  XDG_CACHE_HOME="$home/cache" /usr/bin/Xvfb :98 -screen 0 1280x720x24 \
  -nolisten tcp -ac &
xvfb_pid=$!
sleep 1
kill -0 "$xvfb_pid"

runuser -u "$user" -- env HOME="$home" XDG_CONFIG_HOME="$home/config" \
  DISPLAY=:98 /usr/bin/openbox &
runuser -u "$user" -- env HOME="$home" DISPLAY=:98 \
  /usr/bin/x11vnc -display :98 -localhost -rfbport 5901 -nopw -forever -shared -quiet &
runuser -u "$user" -- /usr/bin/websockify --web=/usr/share/novnc \
  127.0.0.1:6080 127.0.0.1:5901 &

runuser -u "$user" -- env HOME="$home" XDG_CONFIG_HOME="$home/config" \
  XDG_CACHE_HOME="$home/cache" DISPLAY=:98 /usr/bin/chromium \
  --user-data-dir="$home/chromium" --no-first-run --no-default-browser-check \
  --disable-gpu --disable-session-crashed-bubble --window-size=1280,720 \
  http://connectivitycheck.gstatic.com/generate_204 &
browser_pid=$!
wait "$browser_pid"
