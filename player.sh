#!/bin/sh
# Moonlight Qt is primary. RTSP/mpv remains available for comparison/fallback.
set -eu
. "${VIDEO_CONFIG:-/etc/teslausb-car-video.env}"

backend=${VIDEO_BACKEND:-moonlight}
case "$backend" in
    moonlight)
        : "${MOONLIGHT_HOST:?set MOONLIGHT_HOST}"
        : "${MOONLIGHT_APP:?set MOONLIGHT_APP}"
        command -v moonlight-qt >/dev/null 2>&1 || {
            echo 'moonlight-qt is not installed' >&2
            exit 127
        }
        ;;
    rtsp)
        : "${STREAM_URL:?set STREAM_URL}"
        command -v mpv >/dev/null 2>&1 || {
            echo 'mpv is not installed' >&2
            exit 127
        }
        ;;
    *)
        echo "unknown VIDEO_BACKEND: $backend" >&2
        exit 2
        ;;
esac

while :; do
    if [ "$backend" = moonlight ]; then
        bitrate=${MOONLIGHT_BITRATE_KBPS:-4000}
        lte_interface=
        for driver in /sys/class/net/*/device/driver; do
            [ -L "$driver" ] || continue
            if [ "$(basename "$(readlink -f "$driver")")" = rndis_host ]; then
                lte_interface=${driver#/sys/class/net/}
                lte_interface=${lte_interface%%/*}
                break
            fi
        done
        if [ -n "$lte_interface" ] && [ -n "${WG_ENDPOINT_IP:-}" ]; then
            route=$(ip -4 route get "$WG_ENDPOINT_IP" 2>/dev/null || true)
            case " $route " in
                *" dev $lte_interface "*) bitrate=${MOONLIGHT_LTE_BITRATE_KBPS:-1500} ;;
            esac
        fi
        set -- stream "$MOONLIGHT_HOST" "$MOONLIGHT_APP" \
            --resolution "${MOONLIGHT_RESOLUTION:-1280x720}" \
            --fps "${MOONLIGHT_FPS:-30}" \
            --bitrate "$bitrate" --packet-size "${MOONLIGHT_PACKET_SIZE:-1024}" \
            --video-codec H.264 --video-decoder hardware \
            --display-mode fullscreen --no-vsync --audio-on-host
        moonlight-qt "$@" || true
    else
        mpv --no-config --no-audio --profile=low-latency \
            --vo=gpu --gpu-context=drm --hwdec=auto-safe \
            --fullscreen --really-quiet -- "$STREAM_URL" || true
    fi
    sleep 2
done
