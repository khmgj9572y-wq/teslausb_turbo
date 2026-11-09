#!/bin/bash -eu
# Idempotent route cleanup if the portal service stops or times out.
state=/run/car-wifi-portal/route
[ -f "$state" ] || exit 0
mapfile -t route < "$state"
if [ "${#route[@]}" -eq 4 ]
then
  ip route del default via "${route[3]}" dev wlan0 metric 550 2>/dev/null || true
  ip route del "${route[0]}/32" via "${route[1]}" dev "${route[2]}" 2>/dev/null || true
fi
if ip -4 route show default | grep -q ' metric 550$'
then
  echo 'Temporary portal route remains; keeping cleanup state' >&2
  exit 1
fi
rm -f "$state"
