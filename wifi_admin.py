#!/usr/bin/env python3
"""Manage TeslaUSB Wi-Fi picker profiles without writing to read-only root.

The web CGI sends JSON on stdin. Secret passphrases never appear in argv,
responses, or this program's logs. Only profiles owned by this module can be
changed or removed. The existing home, shop, and LTE profiles stay untouched.
"""

import json
import os
import re
import socket
import subprocess
import sys
import tempfile
import time
import uuid as uuidlib
from pathlib import Path

import radio_policy


STORE = Path("/mutable/car-link/wifi")
RUNTIME = Path("/run/NetworkManager/system-connections")
META = STORE / "profiles.json"
PROBE_URL = "https://connectivitycheck.gstatic.com/generate_204"
DURATIONS = {"1h": 3600, "4h": 4 * 3600, "1d": 86400,
             "3d": 3 * 86400, "1w": 7 * 86400, "30d": 30 * 86400,
             "forever": None}


def command(args, timeout=20, check=True):
    try:
        result = subprocess.run(args, stdout=subprocess.PIPE,
                                stderr=subprocess.DEVNULL, text=True,
                                timeout=timeout, env=dict(os.environ, LC_ALL="C"))
    except (OSError, subprocess.TimeoutExpired):
        raise RuntimeError("Network command did not complete")
    if check and result.returncode:
        raise RuntimeError("Network command failed")
    return result


def nmcli(*args, timeout=20, check=True):
    return command(["nmcli", *args], timeout=timeout, check=check)


def split_terse(line):
    fields, value, escaped = [], [], False
    for char in line:
        if escaped:
            value.append(char)
            escaped = False
        elif char == "\\":
            escaped = True
        elif char == ":":
            fields.append("".join(value))
            value = []
        else:
            value.append(char)
    if escaped:
        value.append("\\")
    fields.append("".join(value))
    return fields


def synced():
    result = command(["timedatectl", "show", "-p", "NTPSynchronized",
                      "--value"], timeout=4, check=False)
    return result.returncode == 0 and result.stdout.strip().lower() == "yes"


def ensure_store():
    if not os.path.ismount("/mutable"):
        raise RuntimeError("Writable SSD is unavailable")
    STORE.mkdir(parents=True, exist_ok=True, mode=0o700)
    RUNTIME.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(STORE, 0o700)


def atomic_write(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".carwifi-", dir=str(path.parent))
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def metadata():
    try:
        data = json.loads(META.read_text())
        if isinstance(data, dict) and isinstance(data.get("profiles"), dict):
            return data["profiles"]
    except (OSError, ValueError):
        pass
    return {}


def save_metadata(profiles):
    atomic_write(META, (json.dumps({"profiles": profiles}, sort_keys=True) + "\n").encode())


def keyfile_value(value):
    return value.replace("\\", "\\\\").replace(";", "\\;")


def validate_ssid(value):
    if not isinstance(value, str) or not 1 <= len(value.encode("utf-8")) <= 32:
        raise ValueError("SSID must be 1 to 32 bytes")
    if any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise ValueError("SSID contains a control character")
    return value


def validate_password(value):
    if not isinstance(value, str):
        raise ValueError("Password must be text")
    if value and not 8 <= len(value.encode("utf-8")) <= 63:
        raise ValueError("Wi-Fi password must be 8 to 63 bytes")
    if any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise ValueError("Password contains a control character")
    return value


def profile_file(identifier):
    if not re.fullmatch(r"[0-9a-f-]{36}", identifier):
        raise ValueError("Invalid network identifier")
    return STORE / ("carwifi-" + identifier + ".nmconnection")


def runtime_file(identifier):
    return RUNTIME / profile_file(identifier).name


def create_keyfile(identifier, ssid, password, hidden, autoconnect=False,
                   metric=800, uplink_mac=None):
    if not uplink_mac or not radio_policy.MAC.fullmatch(uplink_mac.lower()):
        raise ValueError('Uplink radio MAC is required')
    profile_id = "carwifi-" + identifier[:8]
    lines = ["[connection]", "id=" + profile_id, "uuid=" + identifier,
             "type=wifi",
             "autoconnect=" + str(autoconnect).lower(),
             "autoconnect-priority=-50", "", "[wifi]",
             "mode=infrastructure", "mac-address=" + uplink_mac.lower(),
             "cloned-mac-address=permanent", "ssid=" + keyfile_value(ssid),
             "hidden=" + str(bool(hidden)).lower()]
    if password:
        lines += ["", "[wifi-security]", "key-mgmt=wpa-psk",
                  "psk=" + keyfile_value(password)]
    lines += ["", "[ipv4]", "method=auto", "route-metric=" + str(metric),
              "", "[ipv6]", "method=auto", "route-metric=" + str(metric), ""]
    return ("\n".join(lines) + "\n").encode("utf-8")


def stage(identifier):
    source = profile_file(identifier)
    target = runtime_file(identifier)
    atomic_write(target, source.read_bytes())
    nmcli("connection", "load", str(target))


def is_connected(identifier):
    result = nmcli("-g", "GENERAL.CON-UUID", "device", "show", radio_policy.require_uplink(),
                   check=False)
    return result.returncode == 0 and result.stdout.strip() == identifier


def internet_on_wifi():
    result = command(["curl", "--interface", "if!" + radio_policy.require_uplink(), "--silent",
                      "--output", "/dev/null", "--write-out", "%{http_code}",
                      "--max-time", "8", PROBE_URL], timeout=12, check=False)
    return result.returncode == 0 and result.stdout == "204"


def promote(identifier):
    source = profile_file(identifier)
    text = source.read_text()
    if "autoconnect=false" in text and text.count("route-metric=800") == 2:
        text = text.replace("autoconnect=false", "autoconnect=true")
        text = text.replace("route-metric=800", "route-metric=600")
        atomic_write(source, text.encode("utf-8"))
    elif "autoconnect=true" not in text or text.count("route-metric=600") != 2:
        raise RuntimeError("Stored network is not in the expected state")
    stage(identifier)
    nmcli("device", "reapply", radio_policy.require_uplink())


def connect(identifier, profiles):
    if identifier not in profiles:
        raise ValueError("This network is protected")
    profile = profiles[identifier]
    result = nmcli("-w", "25", "connection", "up", "uuid", identifier,
                   "ifname", radio_policy.require_uplink(), timeout=30, check=False)
    if result.returncode or not is_connected(identifier):
        profile["state"] = "failed"
    elif internet_on_wifi():
        promote(identifier)
        profile["state"] = "ready"
    else:
        profile["state"] = "portal"
    profile["checked_at"] = int(time.time()) if synced() else None
    save_metadata(profiles)
    return {"ok": True, "state": profile["state"]}


def active_portal(profiles):
    result = nmcli("-g", "GENERAL.CON-UUID", "device", "show", radio_policy.require_uplink(),
                   check=False)
    identifier = result.stdout.strip()
    if identifier not in profiles or profiles[identifier].get("state") != "portal":
        raise RuntimeError("No captive-portal network is active")
    return identifier


def portal_start():
    profiles = metadata()
    active_portal(profiles)
    if not lte_status()["up"]:
        raise RuntimeError("4G must be connected for remote portal sign-in")
    command(["systemctl", "start", "car-wifi-portal.service"], timeout=20)
    for _ in range(20):
        try:
            with socket.create_connection(("127.0.0.1", 6080), timeout=0.2):
                break
        except OSError:
            time.sleep(0.2)
    else:
        raise RuntimeError("Sign-in browser did not start")
    return {"ok": True,
            "url": "/wifi/portal/vnc.html?autoconnect=true&path=wifi/portal/websockify"}


def portal_check():
    profiles = metadata()
    identifier = active_portal(profiles)
    if not internet_on_wifi():
        return {"ok": True, "state": "portal"}
    promote(identifier)
    profiles[identifier]["state"] = "ready"
    profiles[identifier]["checked_at"] = int(time.time()) if synced() else None
    save_metadata(profiles)
    command(["systemctl", "stop", "car-wifi-portal.service"], timeout=20,
            check=False)
    return {"ok": True, "state": "ready"}


def portal_stop():
    command(["systemctl", "stop", "car-wifi-portal.service"], timeout=20,
            check=False)
    return {"ok": True}


def add(data):
    ensure_store()
    ssid = validate_ssid(data.get("ssid"))
    password = validate_password(data.get("password", ""))
    duration = data.get("duration")
    if duration not in DURATIONS:
        raise ValueError("Choose a duration")
    if duration != "forever" and not synced():
        raise RuntimeError("Clock is not synchronized; try again shortly")
    hidden = data.get("hidden", False)
    if not isinstance(hidden, bool):
        raise ValueError("Invalid hidden-network choice")
    profiles = metadata()
    identifier = str(uuidlib.uuid4())
    expires = None if DURATIONS[duration] is None else int(time.time()) + DURATIONS[duration]
    atomic_write(profile_file(identifier), create_keyfile(
        identifier, ssid, password, hidden,
        uplink_mac=radio_policy.permanent_mac(radio_policy.require_uplink())))
    profiles[identifier] = {"ssid": ssid, "expires": expires,
                            "state": "pending", "checked_at": None}
    save_metadata(profiles)
    stage(identifier)
    return connect(identifier, profiles)


def remove(identifier, profiles):
    if identifier not in profiles:
        raise ValueError("This network is protected")
    nmcli("connection", "delete", "uuid", identifier, check=False)
    profile_file(identifier).unlink(missing_ok=True)
    runtime_file(identifier).unlink(missing_ok=True)
    profiles.pop(identifier)
    save_metadata(profiles)


def expire():
    ensure_store()
    if not synced():
        return {"ok": True, "expired": 0, "clock_ready": False}
    profiles = metadata()
    now = int(time.time())
    expired = [identifier for identifier, profile in profiles.items()
               if profile.get("expires") is not None and profile["expires"] <= now]
    for identifier in expired:
        remove(identifier, profiles)
    return {"ok": True, "expired": len(expired), "clock_ready": True}


def load():
    ensure_store()
    expire()
    loaded = 0
    for identifier in metadata():
        if profile_file(identifier).is_file():
            stage(identifier)
            loaded += 1
    return {"ok": True, "loaded": loaded}


def scan():
    result = nmcli("-t", "-f", "IN-USE,SSID,SIGNAL,SECURITY", "device",
                   "wifi", "list", "ifname", radio_policy.require_uplink(), "--rescan", "yes",
                   timeout=20)
    found = {}
    for line in result.stdout.splitlines():
        fields = split_terse(line)
        if len(fields) != 4 or not fields[1] or fields[1] == "--":
            continue
        ssid = fields[1]
        try:
            signal = int(fields[2])
        except ValueError:
            signal = 0
        if ssid not in found or signal > found[ssid]["signal"]:
            found[ssid] = {"ssid": ssid, "signal": signal,
                           "security": fields[3], "active": fields[0] == "*"}
    return {"ok": True, "networks": sorted(found.values(),
             key=lambda item: (-item["signal"], item["ssid"]))}


def lte_status():
    for candidate in sorted(Path("/sys/class/net").iterdir()):
        try:
            if (candidate / "device" / "driver").resolve(strict=True).name != "rndis_host":
                continue
        except OSError:
            continue
        result = command(["ip", "-4", "-o", "addr", "show", "dev", candidate.name],
                         timeout=4, check=False)
        return {"up": result.returncode == 0 and " inet " in result.stdout,
                "interface": candidate.name}
    return {"up": False, "interface": None}


def status():
    profiles = metadata()
    result = nmcli("-t", "-f", "NAME,UUID,TYPE", "connection", "show")
    saved = []
    now = int(time.time()) if synced() else None
    for line in result.stdout.splitlines():
        fields = split_terse(line)
        if len(fields) != 3 or fields[2] not in ("wifi", "802-11-wireless"):
            continue
        name, identifier, _ = fields
        if name == 'car-hotspot':
            continue
        managed = profiles.get(identifier)
        expires = managed.get("expires") if managed else None
        saved.append({"name": managed["ssid"] if managed else name,
                      "uuid": identifier, "protected": managed is None,
                      "permanent": expires is None,
                      "remaining_seconds": max(0, expires - now) if
                      expires is not None and now is not None else None,
                      "state": managed.get("state") if managed else "existing"})
    interface = radio_policy.require_uplink()
    active = nmcli("-g", "GENERAL.CONNECTION", "device", "show", interface,
                   check=False).stdout.strip()
    address = command(["ip", "-4", "-o", "addr", "show", "dev", interface],
                      timeout=4, check=False).stdout
    match = re.search(r"\binet\s+(\S+)", address)
    wifi = scan_active()
    return {"ok": True, "ssid": wifi["ssid"] or active, "signal": wifi["signal"],
            "ip": match.group(1) if match else None, "lte": lte_status(),
            "saved": sorted(saved, key=lambda item: (item["protected"], item["name"].lower()))}


def scan_active():
    result = nmcli("-t", "-f", "IN-USE,SSID,SIGNAL", "device", "wifi",
                   "list", "ifname", radio_policy.require_uplink(), "--rescan", "no", check=False)
    for line in result.stdout.splitlines():
        fields = split_terse(line)
        if len(fields) == 3 and fields[0] == "*":
            try:
                signal = int(fields[2])
            except ValueError:
                signal = None
            return {"ssid": fields[1], "signal": signal}
    return {"ssid": None, "signal": None}


def read_request():
    data = sys.stdin.buffer.read(4097)
    if len(data) > 4096:
        raise ValueError("Request is too large")
    value = json.loads(data)
    if not isinstance(value, dict):
        raise ValueError("Request must be an object")
    return value


def main():
    if os.geteuid() != 0:
        raise RuntimeError("Root access is required")
    if len(sys.argv) != 2:
        raise ValueError("Choose one action")
    action = sys.argv[1]
    if action == "status":
        response = status()
    elif action == "scan":
        response = scan()
    elif action == "add":
        response = add(read_request())
    elif action in ("delete", "permanent", "retry"):
        data = read_request()
        identifier = data.get("uuid", "")
        if not isinstance(identifier, str) or not re.fullmatch(r"[0-9a-f-]{36}", identifier):
            raise ValueError("Invalid network identifier")
        ensure_store()
        profiles = metadata()
        if identifier not in profiles:
            raise ValueError("This network is protected")
        if action == "delete":
            remove(identifier, profiles)
            response = {"ok": True}
        elif action == "permanent":
            profiles[identifier]["expires"] = None
            save_metadata(profiles)
            response = {"ok": True}
        else:
            response = connect(identifier, profiles)
    elif action == "load":
        response = load()
    elif action == "expire":
        response = expire()
    elif action == "portal-start":
        response = portal_start()
    elif action == "portal-check":
        response = portal_check()
    elif action == "portal-stop":
        response = portal_stop()
    else:
        raise ValueError("Unknown action")
    print(json.dumps(response, separators=(",", ":")))


if __name__ == "__main__":
    try:
        main()
    except (ValueError, RuntimeError, OSError, json.JSONDecodeError) as exc:
        # These messages never contain a passphrase or nmcli stderr.
        print(json.dumps({"ok": False, "error": str(exc)}))
        sys.exit(1)
