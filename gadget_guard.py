#!/usr/bin/env python3
"""Recover a TeslaUSB gadget that the car failed to enumerate.

TeslaUSB's archiveloop holds its own flock for its entire lifetime, so it
cannot serve as a short per-disconnect lock. This guard rechecks the gadget's
UDC immediately before acting and never acts while UDC is empty.
"""

import argparse
import configparser
import json
import os
import subprocess
import tempfile
import time
from pathlib import Path


def config_from(path):
    parser = configparser.ConfigParser(interpolation=None)
    if not parser.read(path):
        raise ValueError("missing config: " + path)
    section = parser["gadget_guard"]
    values = {
        "udc_file": Path(section.get("udc_file", "/sys/kernel/config/usb_gadget/teslausb/UDC")),
        "udc_root": Path(section.get("udc_root", "/sys/class/udc")),
        "state_file": Path(section.get("state_file", "/mutable/gadget-guard-state.json")),
        "log_file": Path(section.get("log_file", "/mutable/gadget-guard.log")),
        "disable_cmd": section.get("disable_cmd", "/root/bin/disable_gadget.sh"),
        "enable_cmd": section.get("enable_cmd", "/root/bin/enable_gadget.sh"),
        "interval": section.getint("interval_seconds", fallback=30),
        "max_attempts": section.getint("max_replugs_per_hour", fallback=3),
        "settle_seconds": section.getint("settle_seconds", fallback=3),
        "verify_seconds": section.getint("verify_seconds", fallback=10),
    }
    if values["interval"] <= 0 or values["max_attempts"] <= 0:
        raise ValueError("guard interval and max attempts must be positive")
    if values["settle_seconds"] < 0 or values["verify_seconds"] < 0:
        raise ValueError("guard waits must be nonnegative")
    return values


def boot_id():
    return Path("/proc/sys/kernel/random/boot_id").read_text().strip()


def uptime():
    return float(Path("/proc/uptime").read_text().split()[0])


def read_text(path):
    try:
        return Path(path).read_text().strip()
    except (OSError, UnicodeError):
        return ""


def current_udc_state(config):
    name = read_text(config["udc_file"])
    if not name or "/" in name or name in (".", ".."):
        return "", "intentional-disconnect"
    state = read_text(config["udc_root"] / name / "state")
    return name, state or "unknown"


def load_state(path):
    try:
        obj = json.loads(Path(path).read_text())
        return obj if isinstance(obj, dict) else {}
    except (OSError, ValueError):
        return {}


def save_state(path, state):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(prefix=".gadget-guard-", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w") as handle:
            json.dump(state, handle, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, str(path))
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def append_log(config, message, up=None):
    path = config["log_file"]
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(str(path), "a") as handle:
        handle.write("uptime={:.0f}s {}\n".format(uptime() if up is None else up, message))


def transition_running():
    own_pid = os.getpid()
    for proc in Path("/proc").iterdir():
        if not proc.name.isdigit() or int(proc.name) == own_pid:
            continue
        try:
            command = (proc / "cmdline").read_bytes().replace(b"\0", b" ")
        except OSError:
            continue
        if b"/root/bin/disable_gadget.sh" in command or b"/root/bin/enable_gadget.sh" in command:
            return True
    return False


def recent_attempts(attempts, boot, now):
    kept = []
    for item in attempts:
        if not isinstance(item, dict):
            continue
        if item.get("boot") == boot:
            try:
                elapsed = now - float(item.get("uptime", -100000))
            except (TypeError, ValueError):
                continue
            if 0 <= elapsed < 3600:
                kept.append(item)
        elif now < 3600:
            # No RTC: conservatively retain previous-boot attempts during the
            # first hour of this boot rather than resetting the limit.
            kept.append(item)
    return kept


def check_once(config, state=None, sleep=time.sleep, run=subprocess.run,
               is_transition=transition_running, up=None, boot=None):
    state = {} if state is None else dict(state)
    up = uptime() if up is None else up
    boot = boot_id() if boot is None else boot
    if state.get("boot") != boot:
        state["consecutive"] = 0
        state["boot"] = boot
    state["attempts"] = recent_attempts(state.get("attempts", []), boot, up)
    udc, observed = current_udc_state(config)
    if not udc or observed == "configured":
        state["consecutive"] = 0
        state["last_state"] = observed
        return state, "healthy-or-intentional"
    if observed == "unknown":
        state["consecutive"] = 0
        return state, "udc-state-unavailable"
    state["consecutive"] = int(state.get("consecutive", 0)) + 1
    state["last_state"] = observed
    if state["consecutive"] < 2:
        return state, "waiting-for-second-check"
    if len(state["attempts"]) >= config["max_attempts"]:
        last_log = float(state.get("last_rate_log_uptime", -100000))
        if up - last_log >= 600:
            append_log(config, "rate limit reached; UDC state=" + observed, up)
            state["last_rate_log_uptime"] = up
        return state, "rate-limited"
    if is_transition():
        return state, "teslausb-transition-in-progress"
    # TeslaUSB leaves UDC empty during intentional disconnects. Recheck
    # immediately before running the repair, because there is no short lock.
    udc_again, state_again = current_udc_state(config)
    if udc_again != udc or not udc_again or state_again in ("configured", "unknown"):
        state["consecutive"] = 0
        return state, "changed-before-action"
    state["attempts"].append({"boot": boot, "uptime": up})
    state["consecutive"] = 0
    # Persist the attempt before the first disruptive command so a crash or
    # power loss cannot bypass the hourly cap.
    save_state(config["state_file"], state)
    append_log(config, "replug attempt {} state={}".format(len(state["attempts"]), state_again), up)
    try:
        disable = run([config["disable_cmd"]], timeout=90, stdout=subprocess.PIPE,
                      stderr=subprocess.STDOUT, universal_newlines=True)
        disable_rc = disable.returncode
    except (OSError, subprocess.TimeoutExpired):
        disable_rc = -1
    sleep(config["settle_seconds"])
    try:
        enable = run([config["enable_cmd"]], timeout=90, stdout=subprocess.PIPE,
                     stderr=subprocess.STDOUT, universal_newlines=True)
        enable_rc = enable.returncode
    except (OSError, subprocess.TimeoutExpired):
        enable_rc = -1
    sleep(config["verify_seconds"])
    _, after = current_udc_state(config)
    append_log(config, "replug result state={} disable_rc={} enable_rc={}".format(
        after, disable_rc, enable_rc), up + config["settle_seconds"] + config["verify_seconds"])
    state["last_state"] = after
    return state, "replugged"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("once", "run", "status"))
    parser.add_argument("--config", default="/etc/teslausb-car-link.ini")
    args = parser.parse_args()
    config = config_from(args.config)
    if args.command == "status":
        print(json.dumps(load_state(config["state_file"]), sort_keys=True))
        return
    while True:
        try:
            state = load_state(config["state_file"])
            next_state, outcome = check_once(config, state)
            save_state(config["state_file"], next_state)
            if args.command == "once":
                print(outcome)
                return
        except Exception as exc:
            append_log(config, "guard error: " + repr(exc))
            if args.command == "once":
                raise
        time.sleep(config["interval"])


if __name__ == "__main__":
    main()
