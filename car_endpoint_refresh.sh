#!/bin/sh
# car-endpoint-refresh - keep the car tunnel's endpoint IP in step with the home DDNS name.
# The car-link code needs an IPv4 literal (route checks, portal /32 pin), so the IP stays in the
# files; this resolves the name and rewrites the IP everywhere when it changes, then sets the live peer.
# Config: /etc/teslausb-car-endpoint.env (see examples/car-endpoint.env)
# Usage: car-endpoint-refresh [--dry-run]
set -eu
DRY=0; [ "${1:-}" = "--dry-run" ] && DRY=1
. ${ENDPOINT_ENV:-/etc/teslausb-car-endpoint.env}
WG_IF=${WG_IF:-tun_wg7}
WG_CONF=${WG_CONF:-/etc/wireguard/$WG_IF.conf}
INI=${INI:-/etc/teslausb-car-link.ini}
ENVF=${ENVF:-/etc/teslausb-car-video.env}
log() { logger -t car-endpoint-refresh -- "$*"; echo "$*"; }

case "${CAR_ENDPOINT_NAME:-}" in
  ''|REPLACE_*) log "CAR_ENDPOINT_NAME not configured, nothing changed"; exit 0 ;;
esac
new=$(timeout 20 getent ahostsv4 "$CAR_ENDPOINT_NAME" | awk 'NR==1{print $1}')
case "$new" in
  ''|*[!0-9.]*) log "resolve failed for $CAR_ENDPOINT_NAME, nothing changed"; exit 0 ;;
  10.*|127.*|169.254.*|192.168.*|0.*|172.1[6-9].*|172.2[0-9].*|172.3[01].*|100.6[4-9].*|100.[7-9][0-9].*|100.1[01][0-9].*|100.12[0-7].*)
    log "ignoring non-public answer $new for $CAR_ENDPOINT_NAME (captive portal?), nothing changed"; exit 0 ;;
esac
cur_ep=$(sed -n 's/^Endpoint *= *//p' "$WG_CONF" | head -1)
port=${cur_ep##*:}; cur=${cur_ep%:*}
case "$port" in ''|*[!0-9]*) log "no Endpoint port in $WG_CONF, nothing changed"; exit 1 ;; esac
peer=$(wg show "$WG_IF" peers 2>/dev/null | head -1 || true)
live=$(wg show "$WG_IF" endpoints 2>/dev/null | awk 'NR==1{print $2}' || true)

if [ "$new" = "$cur" ] && [ "$live" = "$new:$port" -o -z "$peer" ]; then
  [ $DRY = 1 ] && echo "up to date: $new:$port (live ${live:-none})"
  exit 0
fi
if [ $DRY = 1 ]; then
  echo "WOULD CHANGE $cur -> $new (live ${live:-none}):"
  echo "  $WG_CONF Endpoint, $INI wg_endpoint_ip, $ENVF WG_ENDPOINT_IP, wg set $WG_IF peer endpoint $new:$port"
  exit 0
fi
if [ "$new" != "$cur" ]; then
  # TeslaUSB keeps / read-only: open it only for these edits, then close it again.
  ro=0; findmnt -no OPTIONS / 2>/dev/null | grep -q '^ro' && ro=1
  [ $ro = 1 ] && [ "${CER_NO_REMOUNT:-0}" != 1 ] && mount -o remount,rw /
  sed -i "s/^Endpoint *= *.*/Endpoint = $new:$port/" "$WG_CONF"
  [ -f "$INI" ] && sed -i "s/^\(wg_endpoint_ip *= *\).*/\1$new/" "$INI"
  [ -f "$ENVF" ] && sed -i "s/^WG_ENDPOINT_IP=.*/WG_ENDPOINT_IP=$new/" "$ENVF"
  sync
  if [ $ro = 1 ] && [ "${CER_NO_REMOUNT:-0}" != 1 ]; then
    mount -o remount,ro / || log "WARNING: / stays read-write until reboot (remount ro failed)"
  fi
fi
[ -n "$peer" ] && wg set "$WG_IF" peer "$peer" endpoint "$new:$port"
log "endpoint $cur -> $new:$port (was live ${live:-none})"
