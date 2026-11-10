#!/usr/bin/env python3
"""Shared read-only link checks and persistent LTE byte accounting."""

import configparser
import datetime
import fcntl
import json
import os
import re
import socket
import subprocess
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path

import radio_policy


def load_policy(path):
    parser = configparser.ConfigParser(interpolation=None)
    if not parser.read(path):
        raise ValueError("missing config: " + path)
    section = parser["policy"]
    values = {
        "meter_file": Path(section.get("meter_file", "/backingfiles/car-link-meter.json")),
        "ssd_mountpoint": Path(section.get("ssd_mountpoint", "/backingfiles")),
        "net_root": Path(section.get("net_root", "/sys/class/net")),
        "lte_driver": section.get("lte_driver", "rndis_host"),
        "soft_cap_bytes": section.getint("soft_cap_gb", fallback=40) * 1000**3,
        "hard_cap_bytes": section.getint("hard_cap_gb", fallback=60) * 1000**3,
        "home_archive_host": section.get("home_archive_host", "").strip(),
        "home_archive_port": section.getint("home_archive_port", fallback=445),
        "power_log": Path(section.get("power_log", "/mutable/car-link-power.log")),
        "video_service": section.get("video_service", "car-video.service"),
        "max_session_seconds": section.getint("max_session_min", fallback=0) * 60,
        "max_handshake_age_seconds": section.getint("max_handshake_age_seconds", fallback=180),
        "intake_wg_ip": section.get("intake_wg_ip", "").strip(),
        "sunshine_wg_ip": section.get("sunshine_wg_ip", "").strip(),
        "wg_endpoint_ip": section.get("wg_endpoint_ip", "").strip(),
        "vpn_interface": section.get("vpn_interface", "wg0").strip(),
        "heartbeat_url": section.get("heartbeat_url", "").strip(),
        "remote_video_on_enabled": section.getboolean(
            "remote_video_on_enabled", fallback=False),
        "ntp_marker": section.get("ntp_marker", "").strip(),
    }
    if values["soft_cap_bytes"] <= 0 or values["hard_cap_bytes"] <= values["soft_cap_bytes"]:
        raise ValueError("hard_cap_gb must exceed a positive soft_cap_gb")
    if values["max_session_seconds"] < 0:
        raise ValueError("max_session_min must be nonnegative; zero disables the hard limit")
    if values["max_handshake_age_seconds"] <= 0:
        raise ValueError("max_handshake_age_seconds must be positive")
    return values


def read_json(path):
    try:
        value = json.loads(Path(path).read_text())
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(prefix=".car-link-", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w") as handle:
            json.dump(value, handle, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temp, 0o600)
        os.replace(temp, str(path))
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


@contextmanager
def file_lock(path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(str(path), "a+b") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        yield


def find_lte_interface(config):
    root = config["net_root"]
    if not root.is_dir():
        return None
    for candidate in sorted(root.iterdir()):
        driver_link = candidate / "device" / "driver"
        try:
            if driver_link.resolve(strict=True).name == config["lte_driver"]:
                return candidate
        except OSError:
            continue
    return None


def ntp_synced(config):
    marker = config.get("ntp_marker", "")
    if marker and Path(marker).exists():
        return True
    try:
        result = subprocess.run(
            ["timedatectl", "show", "-p", "NTPSynchronized", "--value"],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            universal_newlines=True, timeout=3)
        return result.returncode == 0 and result.stdout.strip().lower() == "yes"
    except (OSError, subprocess.TimeoutExpired):
        return False


def utc_month():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m")


def update_meter(config, boot=None, month=None, sync=None):
    path = config["meter_file"]
    if not os.path.ismount(config["ssd_mountpoint"]):
        return {"storage_unavailable": True, "link_present": False, "bytes": 0}
    boot = boot or Path("/proc/sys/kernel/random/boot_id").read_text().strip()
    sync = ntp_synced(config) if sync is None else sync
    month = (month or utc_month()) if sync else None
    try:
        with file_lock(str(path) + ".lock"):
            state = read_json(path)
            iface = find_lte_interface(config)
            if iface is None:
                state["link_present"] = False
                write_json(path, state)
                return state
            try:
                rx = int((iface / "statistics" / "rx_bytes").read_text())
                tx = int((iface / "statistics" / "tx_bytes").read_text())
                ifindex = int((iface / "ifindex").read_text())
            except (OSError, ValueError):
                return state
            old_month = state.get("month")
            if month and old_month and month > old_month:
                state["bytes"] = 0
            if month and (not old_month or month > old_month):
                state["month"] = month
            same_counter = (state.get("boot") == boot and
                            state.get("ifindex") == ifindex and
                            state.get("interface") == iface.name)
            if same_counter and rx >= int(state.get("rx", 0)) and tx >= int(state.get("tx", 0)):
                delta = rx - int(state.get("rx", 0)) + tx - int(state.get("tx", 0))
            else:
                delta = rx + tx
            state["bytes"] = int(state.get("bytes", 0)) + delta
            state.update({"boot": boot, "interface": iface.name,
                          "link_present": True, "ifindex": ifindex, "rx": rx, "tx": tx,
                          "sample_uptime": uptime()})
            write_json(path, state)
            return state
    except OSError:
        return {"storage_unavailable": True, "link_present": False, "bytes": 0}


def cap_mode(config, state):
    used = int(state.get("bytes", 0))
    if used >= config["hard_cap_bytes"]:
        return "hard"
    if used >= config["soft_cap_bytes"]:
        return "soft"
    return "normal"


def current_throttle_flags():
    try:
        result = subprocess.run(["vcgencmd", "get_throttled"],
                                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                universal_newlines=True, timeout=3)
        if result.returncode == 0:
            match = re.search(r"0x([0-9a-fA-F]+)", result.stdout)
            if match:
                return int(match.group(1), 16)
    except (OSError, subprocess.TimeoutExpired):
        pass
    return None


def home_wifi_reachable(config):
    host = config.get("home_archive_host", "")
    if not host or host.startswith("REPLACE_"):
        return False
    try:
        address = socket.gethostbyname(host)
        route = subprocess.run(["ip", "route", "get", address],
                               stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                               universal_newlines=True, timeout=3)
        uplink = radio_policy.uplink_interface(config.get('net_root'))
        if not uplink or route.returncode != 0 or not re.search(
                r"\bdev\s+" + re.escape(uplink) + r"\b", route.stdout):
            return False
        with socket.create_connection((address, config["home_archive_port"]), timeout=2):
            return True
    except (OSError, subprocess.TimeoutExpired):
        return False


def active_link(config):
    endpoint = config.get("wg_endpoint_ip", "")
    if not endpoint or endpoint.startswith("REPLACE_"):
        return None
    try:
        result = subprocess.run(["ip", "route", "get", endpoint],
                                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                universal_newlines=True, timeout=3)
        match = re.search(r"\bdev\s+(\S+)", result.stdout)
        return match.group(1) if result.returncode == 0 and match else None
    except (OSError, subprocess.TimeoutExpired):
        return None


def uptime():
    try:
        return float(Path("/proc/uptime").read_text().split()[0])
    except OSError:
        return time.monotonic()
