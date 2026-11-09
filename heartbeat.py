#!/usr/bin/env python3
"""Send a small car status heartbeat through the dedicated WireGuard peer."""

import argparse
import datetime
import ipaddress
import json
import shutil
import sqlite3
import urllib.request
from pathlib import Path
from urllib.parse import urlparse

import car_control
import car_policy
import gadget_guard


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, message, headers, new_url):
        raise RuntimeError("heartbeat redirect refused")


def open_heartbeat(request, timeout):
    return urllib.request.build_opener(NoRedirect()).open(request, timeout=timeout)


def queue_depth(spool_root):
    db_path = Path(spool_root) / "queue.sqlite3"
    if not db_path.is_file():
        return {"files": 0, "bytes": 0}
    try:
        db = sqlite3.connect("file:{}?mode=ro".format(db_path), uri=True, timeout=2)
        try:
            count, size = db.execute(
                "SELECT COUNT(*), COALESCE(SUM(size),0) FROM files WHERE status='pending'").fetchone()
            return {"files": count, "bytes": size}
        finally:
            db.close()
    except (OSError, sqlite3.Error):
        return {"files": None, "bytes": None}


def last_snapshot_time(root, time_ready):
    if not time_ready:
        return None
    try:
        paths = list(Path(root).glob("snap-*/snap.bin.toc"))
        if not paths:
            return None
        latest = max(path.stat().st_mtime for path in paths)
        return datetime.datetime.fromtimestamp(latest, datetime.timezone.utc).isoformat()
    except OSError:
        return None


def payload(policy, guard_config, spool_root, snapshot_root):
    meter = car_policy.update_meter(policy)
    iface = car_policy.find_lte_interface(policy)
    link = car_policy.active_link(policy)
    if link is None:
        link_type = "offline"
    elif iface is not None and link == iface.name:
        link_type = "lte"
    elif link == "wlan0":
        link_type = "wifi"
    else:
        link_type = link
    _, udc_state = gadget_guard.current_udc_state(guard_config)
    guard_state = gadget_guard.load_state(guard_config["state_file"])
    attempts = gadget_guard.recent_attempts(
        guard_state.get("attempts", []), gadget_guard.boot_id(), gadget_guard.uptime())
    flags = car_policy.current_throttle_flags()
    ready = car_policy.ntp_synced(policy)
    try:
        ssd_free = shutil.disk_usage(str(policy["ssd_mountpoint"])).free
    except OSError:
        ssd_free = None
    result = {
        "uptime_seconds": round(car_policy.uptime()),
        "active_link": link_type,
        "lte_bytes_this_month": int(meter.get("bytes", 0)),
        "lte_month_utc": meter.get("month") if ready else None,
        "ssd_free_bytes": ssd_free,
        "storage_available": not meter.get("storage_unavailable", False),
        "queue_depth": queue_depth(spool_root),
        "last_snapshot_time": last_snapshot_time(snapshot_root, ready),
        "throttled_flags": None if flags is None else "0x{:x}".format(flags),
        "udc_state": udc_state,
        "gadget_guard_replugs_last_hour": len(attempts),
    }
    if ready:
        result["time_utc"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
    return result


def validate_url(url):
    parsed = urlparse(url)
    if parsed.scheme != "http" or parsed.path != "/hb" or not parsed.hostname:
        raise ValueError("heartbeat_url must be http://<intake WG IP>/hb")
    try:
        host = ipaddress.ip_address(parsed.hostname)
    except ValueError:
        raise ValueError("heartbeat_url must use an IP address")
    if not host.is_private:
        raise ValueError("heartbeat_url must use a private WireGuard IP")
    return str(host)


def send_heartbeat(policy, guard_config, spool_root, snapshot_root):
    host = validate_url(policy["heartbeat_url"])
    if host != policy["intake_wg_ip"]:
        raise ValueError("heartbeat URL host differs from intake_wg_ip")
    if not car_control.wg_route(host, policy["vpn_interface"]):
        raise RuntimeError("heartbeat host has no WireGuard route")
    body = json.dumps(payload(policy, guard_config, spool_root, snapshot_root),
                      separators=(",", ":")).encode()
    request = urllib.request.Request(policy["heartbeat_url"], data=body,
                                     method="POST", headers={"Content-Type": "application/json"})
    with open_heartbeat(request, timeout=10) as reply:
        if reply.status != 200:
            raise RuntimeError("heartbeat HTTP status " + str(reply.status))
        response_bytes = reply.read(8193)
    if len(response_bytes) > 8192:
        raise RuntimeError("heartbeat response too large")
    response = json.loads(response_bytes)
    if not isinstance(response, dict):
        raise RuntimeError("heartbeat response must be an object")
    command = response.get("video", "keep")
    if command not in ("on", "off", "keep"):
        raise RuntimeError("invalid video command")
    state = car_policy.read_json(car_control.HEARTBEAT_STATE)
    prior = state.get("last_video_command")
    state["last_success_uptime"] = car_policy.uptime()
    if command == "on" and prior == "on":
        car_policy.write_json(car_control.HEARTBEAT_STATE, state)
        return "on already handled"
    result = car_control.apply_video_command(policy, command)
    if command != "on" or result == "on":
        state["last_video_command"] = command
    car_policy.write_json(car_control.HEARTBEAT_STATE, state)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default="/etc/teslausb-car-link.ini")
    args = parser.parse_args()
    policy = car_policy.load_policy(args.config)
    guard_config = gadget_guard.config_from(args.config)
    import event_queue
    event_config = event_queue.load_config(args.config)
    print(send_heartbeat(policy, guard_config, event_config["spool_root"],
                         event_config["snapshot_root"]))


if __name__ == "__main__":
    main()
