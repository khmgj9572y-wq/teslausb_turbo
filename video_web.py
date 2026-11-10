#!/usr/bin/env python3
"""Manual Moonlight controls used by TeslaUSB's authenticated CGI."""

import json
import sys

import car_control
import car_policy
import display_owner


def status(policy):
    active = car_control.video_active(policy)
    remaining = None
    started = car_policy.read_json(car_control.VIDEO_START)
    if policy["max_session_seconds"] > 0 and active and started.get("boot") == car_control.boot_id():
        remaining = max(0, int(policy["max_session_seconds"] -
                               (car_policy.uptime() - started.get("uptime", 0))))
    return {"ok": True, "active": active, "airplay_active": display_owner.busy(), "remaining_seconds": remaining,
            "max_session_minutes": policy["max_session_seconds"] // 60 or None}


def act(policy, action, request):
    if action == "video-status":
        return status(policy)
    if action == "video-stop":
        if not display_owner.stop_video(policy):
            raise RuntimeError("Moonlight could not be stopped")
        return status(policy)
    if action != "video-start":
        raise ValueError("Unknown video action")
    if request.get("parked") is not True:
        raise ValueError("Confirm that the car is parked before starting")
    allowed, reason = display_owner.start_video(policy)
    if not allowed:
        raise RuntimeError("Moonlight cannot start: " + reason)
    return status(policy)


def main():
    if len(sys.argv) != 2:
        raise ValueError("Choose a video action")
    action = sys.argv[1]
    request = {}
    if action != "video-status":
        raw = sys.stdin.buffer.read(4097)
        if len(raw) > 4096:
            raise ValueError("Request is too large")
        request = json.loads(raw)
        if not isinstance(request, dict):
            raise ValueError("Request must be an object")
    policy = car_policy.load_policy("/etc/teslausb-car-link.ini")
    print(json.dumps(act(policy, action, request)))


if __name__ == "__main__":
    try:
        main()
    except (ValueError, RuntimeError, OSError) as error:
        print(json.dumps({"ok": False, "error": str(error)}))
        sys.exit(1)
