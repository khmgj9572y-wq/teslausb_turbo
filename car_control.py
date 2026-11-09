#!/usr/bin/env python3
"""Enforce the LTE cap, video session limit, and power/link stops."""

import argparse
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

import car_policy


RUNTIME = Path("/run/teslausb-car-link")
VIDEO_START = RUNTIME / "video-start.json"
HEARTBEAT_STATE = RUNTIME / "heartbeat-state.json"
UPLOAD_MARKER = RUNTIME / "upload-active"


def boot_id():
    return Path("/proc/sys/kernel/random/boot_id").read_text().strip()


def systemctl(action, service):
    return subprocess.run(["systemctl", action, service],
                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                          timeout=10).returncode == 0


def wg_route(address, interface="wg0"):
    if not address or address.startswith("REPLACE_"):
        return False
    try:
        result = subprocess.run(["ip", "route", "get", address],
                                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                universal_newlines=True, timeout=3)
        return result.returncode == 0 and re.search(
            r"\bdev\s+" + re.escape(interface) + r"\b", result.stdout) is not None
    except (OSError, subprocess.TimeoutExpired):
        return False


def wg_handshake_recent(interface, max_age):
    try:
        result = subprocess.run(["wg", "show", interface, "latest-handshakes"],
                                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                universal_newlines=True, timeout=3)
        if result.returncode != 0:
            return False
        now = time.time()
        for line in result.stdout.splitlines():
            fields = line.split()
            if len(fields) >= 2:
                stamp = int(fields[1])
                if stamp > 0 and 0 <= now - stamp <= max_age:
                    return True
    except (OSError, ValueError, subprocess.TimeoutExpired):
        pass
    return False


def video_allowed(config, state=None):
    state = car_policy.update_meter(config) if state is None else state
    if car_policy.cap_mode(config, state) != "normal":
        return False, "LTE data cap"
    flags = car_policy.current_throttle_flags()
    if flags is None or flags & 0x1:
        return False, "undervoltage or unknown power state"
    if car_policy.active_link(config) is None:
        return False, "no uplink route"
    if not wg_route(config["sunshine_wg_ip"], config["vpn_interface"]):
        return False, "Sunshine VPN route unavailable"
    return True, "allowed"


def video_active(config):
    return systemctl("is-active", config["video_service"])


def mark_video_start():
    car_policy.write_json(VIDEO_START, {"boot": boot_id(),
                                        "uptime": car_policy.uptime()})


def append_power_log(config, flags, up):
    path = config["power_log"]
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(str(path), "a") as handle:
        handle.write("uptime={:.0f}s throttled={}\n".format(
            up, "unknown" if flags is None else "0x{:x}".format(flags)))


def tick(config):
    meter = car_policy.update_meter(config)
    active = video_active(config)
    uploading = UPLOAD_MARKER.exists()
    flags = car_policy.current_throttle_flags()
    up = car_policy.uptime()
    if active or uploading:
        marker = RUNTIME / "last-power-log.json"
        previous = car_policy.read_json(marker)
        if up - float(previous.get("uptime", -100000)) >= 60:
            append_power_log(config, flags, up)
            car_policy.write_json(marker, {"uptime": up})
    if uploading and (flags is None or flags & 0x1):
        systemctl("stop", "car-event-send.service")
    if not active:
        return "video already off"
    allowed, reason = video_allowed(config, meter)
    if not allowed:
        systemctl("stop", config["video_service"])
        return "video stopped: " + reason
    started = car_policy.read_json(VIDEO_START)
    boot = boot_id()
    if started.get("boot") != boot:
        mark_video_start()
        started = car_policy.read_json(VIDEO_START)
    if up - float(started.get("uptime", up)) >= config["max_session_seconds"]:
        systemctl("stop", config["video_service"])
        return "video stopped: session limit"
    if (up - float(started.get("uptime", up)) > 120 and
            not wg_handshake_recent(config["vpn_interface"],
                                    config["max_handshake_age_seconds"])):
        systemctl("stop", config["video_service"])
        return "video stopped: WireGuard handshake stale"
    heartbeat = car_policy.read_json(HEARTBEAT_STATE)
    last_ok = heartbeat.get("last_success_uptime")
    if last_ok is not None and up - float(last_ok) > 600:
        systemctl("stop", config["video_service"])
        return "video stopped: heartbeat link lost"
    if last_ok is None and up - float(started.get("uptime", up)) > 600:
        systemctl("stop", config["video_service"])
        return "video stopped: no heartbeat"
    return "video allowed"


def apply_video_command(config, command):
    if command == "off":
        systemctl("stop", config["video_service"])
        return "off"
    if command == "on":
        allowed, reason = video_allowed(config)
        if not allowed:
            return "blocked: " + reason
        if not video_active(config):
            if not systemctl("start", config["video_service"]):
                return "start failed"
        return "on"
    return "keep"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("tick", "can-video", "mark-video-start"))
    parser.add_argument("--config", default="/etc/teslausb-car-link.ini")
    args = parser.parse_args()
    config = car_policy.load_policy(args.config)
    if args.command == "tick":
        print(tick(config))
    elif args.command == "can-video":
        allowed, reason = video_allowed(config)
        print(reason)
        if not allowed:
            sys.exit(1)
    else:
        mark_video_start()


if __name__ == "__main__":
    main()
