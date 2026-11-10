#!/usr/bin/env python3
"""Copy TeslaUSB snapshot files to a durable queue and upload them over VPN.

This program never writes to TeslaUSB's camera image, snapshots, or archive state.
It is intentionally compatible with Python 3.7+ on older TeslaUSB images.
"""

import argparse
import configparser
import fcntl
import hashlib
import ipaddress
import json
import os
import re
import shlex
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path

import car_policy


SAFE_NAME = re.compile(r"^[A-Za-z0-9_.+-]+$")
CLIP_DIRS = ("SavedClips", "SentryClips")
MAX_FILE_SIZE = 2 * 1024 * 1024 * 1024  # avoid accidentally staging a disk image
PNG_START = b"\x89PNG\r\n\x1a\n"
PNG_END = b"\x00\x00\x00\x00IEND\xaeB\x60\x82"


def safe_name(name):
    return name not in ("", ".", "..") and bool(SAFE_NAME.fullmatch(name))


def priority(name):
    lower = name.lower()
    if lower == "thumb.png":
        return 0
    if lower in ("event.json", "events.json"):
        return 1
    if lower.endswith("-front.mp4"):
        return 2
    if lower.endswith(".mp4"):
        return 3
    return 4


def load_config(path):
    config = configparser.ConfigParser(interpolation=None)
    if not config.read(path):
        raise ValueError("configuration not found: " + path)
    section = config["car_link"]
    archive_root = Path(section.get("archive_root", "/mutable/TeslaCam"))
    snapshot_root = Path(section.get("snapshot_root", "/backingfiles/snapshots"))
    snapshot_mount_root = Path(section.get("snapshot_mount_root", "/tmp/snapshots"))
    spool_root = Path(section.get("spool_root", "/backingfiles/car-link-spool"))
    require_mountpoint = section.get("require_mountpoint", "/backingfiles").strip()
    ssh_host = section.get("ssh_host", "").strip()
    ssh_user = section.get("ssh_user", "").strip()
    ssh_path = section.get("ssh_path", "").strip()
    ssh_key = section.get("ssh_key", "").strip()
    vpn_interface = section.get("vpn_interface", "wg0").strip()
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", ssh_user):
        raise ValueError("ssh_user must be a simple account name")
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", ssh_host):
        raise ValueError("ssh_host must be the home server's IPv4 WireGuard address")
    try:
        ipaddress.IPv4Address(ssh_host)
    except ValueError:
        raise ValueError("ssh_host must be the home server's IPv4 WireGuard address")
    if (not re.fullmatch(r"[A-Za-z0-9_./-]+", ssh_path) or
            ".." in Path(ssh_path).parts):
        raise ValueError("ssh_path must be a simple path within the restricted rsync root")
    if not ssh_key.startswith("/"):
        raise ValueError("ssh_key must be an absolute path")
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", vpn_interface):
        raise ValueError("invalid vpn_interface")
    values = {
        "archive_root": archive_root,
        "archive_index": Path(section.get("archive_index", "/mutable/sentry_files_archived")),
        "snapshot_root": snapshot_root,
        "snapshot_mount_root": snapshot_mount_root,
        "spool_root": spool_root,
        "require_mountpoint": require_mountpoint,
        "upload_marker": Path(section.get("upload_marker", "/run/teslausb-car-link/upload-active")),
        "ssh_host": ssh_host,
        "ssh_user": ssh_user,
        "ssh_path": ssh_path.rstrip("/"),
        "ssh_key": ssh_key,
        "vpn_interface": vpn_interface,
        "min_file_age": section.getint("min_file_age_seconds", fallback=30),
        "stable_mp4_snapshots": section.getint("stable_mp4_snapshots", fallback=2),
        "bandwidth_kib": section.getint("bandwidth_kib_per_second", fallback=750),
        "active_bandwidth_kib": section.getint(
            "active_bandwidth_kib_per_second", fallback=250),
        "max_files": section.getint("max_files_per_send", fallback=100),
        "retention_days": section.getint("sent_retention_days", fallback=0),
        "max_spool_bytes": section.getint("max_spool_gib", fallback=5) * 1024**3,
        "metadata_reserve_bytes": section.getint("metadata_reserve_mib", fallback=256) * 1024**2,
        "min_free_bytes": section.getint("min_free_gib", fallback=45) * 1024**3,
    }
    for key in ("bandwidth_kib", "active_bandwidth_kib", "max_files",
                "max_spool_bytes"):
        if values[key] <= 0:
            raise ValueError(key + " must be positive")
    if values["retention_days"] < 0:
        raise ValueError("sent_retention_days must be nonnegative")
    if values["min_free_bytes"] < 0:
        raise ValueError("min_free_gib must be nonnegative")
    if not 0 <= values["metadata_reserve_bytes"] < values["max_spool_bytes"]:
        raise ValueError("metadata_reserve_mib must fit within max_spool_gib")
    if values["min_file_age"] < 0:
        raise ValueError("min_file_age_seconds must be nonnegative")
    if values["stable_mp4_snapshots"] < 1:
        raise ValueError("stable_mp4_snapshots must be positive")
    return values


def database(config):
    root = config["spool_root"]
    mountpoint = config.get("require_mountpoint", "")
    if mountpoint and not os.path.ismount(mountpoint):
        raise RuntimeError("queue SSD is not mounted at " + mountpoint)
    root.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(str(root / "queue.sqlite3"), timeout=30)
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("PRAGMA synchronous=FULL")
    db.execute("PRAGMA busy_timeout=30000")
    db.execute("""CREATE TABLE IF NOT EXISTS files (
        path TEXT PRIMARY KEY,
        source_size INTEGER NOT NULL,
        source_mtime_ns INTEGER NOT NULL,
        sha256 TEXT NOT NULL,
        size INTEGER NOT NULL,
        priority INTEGER NOT NULL,
        status TEXT NOT NULL CHECK(status IN ('pending','sent')),
        updated_at INTEGER NOT NULL,
        sent_at INTEGER
    )""")
    db.execute("CREATE INDEX IF NOT EXISTS files_pending ON files(status, priority, updated_at)")
    db.execute("""CREATE TABLE IF NOT EXISTS observations (
        path TEXT PRIMARY KEY,
        snapshot_number INTEGER NOT NULL,
        source_size INTEGER NOT NULL,
        source_mtime_ns INTEGER NOT NULL,
        stable_count INTEGER NOT NULL
    )""")
    db.commit()
    return db


@contextmanager
def lock(root, name):
    root.mkdir(parents=True, exist_ok=True)
    with open(str(root / ("." + name + ".lock")), "a+b") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        yield


def under_root(path, root):
    try:
        return os.path.commonpath((str(path), str(root.resolve()))) == str(root.resolve())
    except ValueError:
        return False


def copy_atomic(source, destination):
    destination.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=".staging-", dir=str(destination.parent))
    digest = hashlib.sha256()
    copied = 0
    try:
        with os.fdopen(fd, "wb") as out_file, open(str(source), "rb") as in_file:
            before = os.fstat(in_file.fileno())
            while True:
                chunk = in_file.read(1024 * 1024)
                if not chunk:
                    break
                out_file.write(chunk)
                digest.update(chunk)
                copied += len(chunk)
            after = os.fstat(in_file.fileno())
            out_file.flush()
            os.fsync(out_file.fileno())
        if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
            raise RuntimeError("source changed while copying")
        if copied != after.st_size:
            raise RuntimeError("short read while copying")
        os.chmod(temp_name, 0o600)
        os.utime(temp_name, ns=(after.st_atime_ns, after.st_mtime_ns))
        os.replace(temp_name, str(destination))
        return digest.hexdigest(), copied
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)


def snapshot_number(path):
    for part in Path(path).parts:
        match = re.fullmatch(r"snap-(\d+)", part)
        if match:
            return int(match.group(1))
    return None


def mp4_stable_in_snapshots(db, relative, source, stat, required):
    if required == 1:
        return True
    number = snapshot_number(source)
    if number is None:
        return False
    row = db.execute("""SELECT snapshot_number, source_size,
        source_mtime_ns, stable_count FROM observations WHERE path=?""",
        (str(relative),)).fetchone()
    if row and number == row[0]:
        return row[3] >= required
    if (row and number == row[0] + 1 and
            (stat.st_size, stat.st_mtime_ns) == (row[1], row[2])):
        count = min(required, row[3] + 1)
    else:
        count = 1
    db.execute("""INSERT OR REPLACE INTO observations
        (path, snapshot_number, source_size, source_mtime_ns, stable_count)
        VALUES (?, ?, ?, ?, ?)""",
        (str(relative), number, stat.st_size, stat.st_mtime_ns, count))
    db.commit()
    return count >= required


def valid_sidecar(source, name):
    lower = name.lower()
    if lower in ("event.json", "events.json"):
        try:
            with open(str(source), "rb") as handle:
                data = handle.read(1024 * 1024 + 1)
            return len(data) <= 1024 * 1024 and isinstance(json.loads(data), dict)
        except (OSError, ValueError, UnicodeError):
            return False
    if lower == "thumb.png":
        try:
            with open(str(source), "rb") as handle:
                first = handle.read(8)
                handle.seek(-12, os.SEEK_END)
                last = handle.read(12)
            return first == PNG_START and last == PNG_END
        except OSError:
            return False
    return True


def archived_paths(config):
    """Read TeslaUSB's home-delivery ledger without changing its state."""
    path = config.get("archive_index")
    if path is None:
        return set()
    try:
        lines = Path(path).read_text(errors="replace").splitlines()
    except FileNotFoundError:
        return set()
    paths = set()
    for line in lines:
        line = line[2:] if line.startswith("./") else line
        parts = Path(line).parts
        if (len(parts) == 3 and parts[0] in CLIP_DIRS and
                all(safe_name(part) for part in parts)):
            paths.add(str(Path(*parts)))
    return paths


def scan(config, db, policy=None):
    scan_mode = "normal"
    min_file_age = config["min_file_age"]
    trusted_time = policy is None or car_policy.ntp_synced(policy)
    if policy is not None:
        if car_policy.home_wifi_reachable(policy):
            print("home Wi-Fi archive is reachable; LTE staging paused")
            return 0, 0
        scan_mode = car_policy.cap_mode(policy, car_policy.update_meter(policy))
        if scan_mode == "hard":
            print("LTE hard data cap reached; staging paused")
            return 0, 0
        if not trusted_time:
            # A reflink snapshot is immutable, and pre-NTP wall time cannot
            # reliably measure age against the car's FAT timestamps.
            min_file_age = 0
    root = config["archive_root"]
    if not root.is_dir():
        raise RuntimeError("TeslaUSB archive view is missing: " + str(root))
    staged = 0
    skipped = 0
    now = time.time()
    home_archived = archived_paths(config)
    spool_bytes = db.execute("SELECT COALESCE(SUM(size), 0) FROM files").fetchone()[0]
    for clip_type in CLIP_DIRS:
        clip_root = root / clip_type
        if not clip_root.is_dir():
            continue
        for event in sorted(clip_root.iterdir(), reverse=True):
            if not safe_name(event.name) or not event.is_dir() or event.is_symlink():
                continue
            for candidate in sorted(event.iterdir(),
                                    key=lambda item: (priority(item.name), item.name)):
                if not safe_name(candidate.name) or not candidate.is_symlink():
                    continue
                if scan_mode == "soft" and priority(candidate.name) > 1:
                    continue
                relative = Path(clip_type) / event.name / candidate.name
                if str(relative) in home_archived:
                    continue
                try:
                    source = candidate.resolve(strict=True)
                    if not (under_root(source, config["snapshot_root"]) or
                            under_root(source, config["snapshot_mount_root"])):
                        skipped += 1
                        continue
                    stat = source.stat()
                    if not source.is_file() or stat.st_size > MAX_FILE_SIZE:
                        skipped += 1
                        continue
                    if not valid_sidecar(source, candidate.name):
                        skipped += 1
                        continue
                    if candidate.name.lower().endswith(".mp4") and not mp4_stable_in_snapshots(
                            db, relative, source, stat, config["stable_mp4_snapshots"]):
                        continue
                    # FAT timestamps can appear in the future if car/Pi clocks or
                    # time zones differ. Do not postpone those files forever.
                    if (stat.st_mtime <= now + 60 and
                            now - stat.st_mtime < min_file_age):
                        continue
                    row = db.execute(
                        "SELECT source_size, source_mtime_ns, status FROM files WHERE path=?",
                        (str(relative),),
                    ).fetchone()
                    destination = config["spool_root"] / relative
                    if row and row[0:2] == (stat.st_size, stat.st_mtime_ns):
                        if row[2] == "sent" or destination.is_file():
                            continue
                    existing_bytes = destination.stat().st_size if destination.is_file() else 0
                    extra_bytes = max(0, stat.st_size - existing_bytes)
                    video_limit = (config["max_spool_bytes"] -
                                   config.get("metadata_reserve_bytes", 0))
                    if (spool_bytes + extra_bytes > config["max_spool_bytes"] or
                            (priority(candidate.name) > 1 and
                             spool_bytes + extra_bytes > video_limit) or
                            shutil.disk_usage(str(config["spool_root"])).free <
                            config["min_free_bytes"] + stat.st_size):
                        print("queue space reserve reached; retry later: " + str(relative),
                              file=sys.stderr)
                        skipped += 1
                        continue
                    digest, size = copy_atomic(source, destination)
                    db.execute("""INSERT OR REPLACE INTO files
                        (path, source_size, source_mtime_ns, sha256, size, priority,
                         status, updated_at, sent_at)
                        VALUES (?, ?, ?, ?, ?, ?, 'pending', ?, NULL)""",
                        (str(relative), stat.st_size, stat.st_mtime_ns, digest,
                         size, priority(candidate.name), int(now) if trusted_time else 0),
                    )
                    db.commit()
                    spool_bytes += extra_bytes
                    staged += 1
                except (FileNotFoundError, OSError, RuntimeError) as exc:
                    print("retry later: {}: {}".format(relative, exc), file=sys.stderr)
                    skipped += 1
    return staged, skipped


def vpn_route_ok(config):
    result = subprocess.run(
        ["ip", "-o", "route", "get", config["ssh_host"]],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True,
        timeout=5,
    )
    return result.returncode == 0 and re.search(
        r"\bdev\s+" + re.escape(config["vpn_interface"]) + r"\b", result.stdout
    ) is not None


def send(config, db, policy=None):
    if policy is not None:
        if car_policy.home_wifi_reachable(policy):
            print("home Wi-Fi archive is reachable; LTE queue paused")
            return 0
        flags = car_policy.current_throttle_flags()
        if flags is None or flags & 0x1:
            print("power state unknown or undervoltage active; LTE queue paused")
            return 0
        meter = car_policy.update_meter(policy)
        if car_policy.cap_mode(policy, meter) == "hard":
            print("LTE hard data cap reached; uploads paused")
            return 0
    if not vpn_route_ok(config):
        print("VPN route is unavailable; leaving files queued")
        return 0
    if not Path(config["ssh_key"]).is_file():
        raise RuntimeError("SSH key is missing: " + config["ssh_key"])
    ssh = ["ssh", "-i", config["ssh_key"], "-o", "BatchMode=yes",
           "-o", "StrictHostKeyChecking=yes", "-o", "ConnectTimeout=10"]
    video = subprocess.run(["systemctl", "is-active", "--quiet", "car-video.service"],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    bandwidth = (config["active_bandwidth_kib"] if
                 video.returncode == 0 else config["bandwidth_kib"])
    sent = 0
    home_archived = archived_paths(config)
    rows = db.execute("""SELECT path, sha256, priority FROM files WHERE status='pending'
        ORDER BY priority, path DESC LIMIT ?""", (config["max_files"],)).fetchall()
    for relative_string, digest, file_priority in rows:
        if relative_string in home_archived:
            # CIFS may deliver a pending file while the LTE worker is paused.
            # Retain its delivery record and let the normal local prune remove
            # only the sidecar's queued copy.
            trusted_time = policy is None or car_policy.ntp_synced(policy)
            db.execute("UPDATE files SET status='sent', sent_at=? WHERE path=? AND sha256=?",
                       (int(time.time()) if trusted_time else 0, relative_string, digest))
            db.commit()
            continue
        if policy is not None:
            # A send batch can span a network change. Yield to the normal
            # archive before starting another file after home Wi-Fi returns.
            if car_policy.home_wifi_reachable(policy):
                print("home Wi-Fi archive returned; LTE queue paused")
                break
            meter = car_policy.update_meter(policy)
            mode = car_policy.cap_mode(policy, meter)
            if mode == "hard":
                break
            if mode == "soft" and file_priority > 1:
                continue
            flags = car_policy.current_throttle_flags()
            if flags is None or flags & 0x1:
                break
        relative = Path(relative_string)
        if len(relative.parts) != 3 or any(not safe_name(part) for part in relative.parts):
            print("invalid queued path: " + relative_string, file=sys.stderr)
            continue
        source = config["spool_root"] / relative
        if not source.is_file():
            print("missing queued file: " + relative_string, file=sys.stderr)
            continue
        source_arg = "./" + relative_string
        account = "{}@{}".format(config["ssh_user"], config["ssh_host"])
        remote = "{}:{}/".format(account, config["ssh_path"])
        command = ["rsync", "-rtR", "--omit-dir-times", "--ignore-existing",
                   "--partial-dir=.partial", "--timeout=60",
                   "--bwlimit={}".format(bandwidth),
                   "-e", " ".join(shlex.quote(part) for part in ssh), source_arg, remote]
        marker = config["upload_marker"]
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.touch()
        try:
            result = subprocess.run(command, stdout=subprocess.PIPE,
                                    stderr=subprocess.PIPE, universal_newlines=True,
                                    timeout=3600, cwd=str(config["spool_root"]))
        finally:
            try:
                marker.unlink()
            except FileNotFoundError:
                pass
        if result.returncode != 0:
            print("upload failed for {}: {}".format(
                relative_string, result.stderr.strip()[-500:]), file=sys.stderr)
            break
        trusted_time = policy is None or car_policy.ntp_synced(policy)
        db.execute("UPDATE files SET status='sent', sent_at=? WHERE path=? AND sha256=?",
                   (int(time.time()) if trusted_time else 0, relative_string, digest))
        db.commit()
        sent += 1
    return sent


def prune(config, db):
    if config["retention_days"] == 0:
        rows = db.execute("SELECT path FROM files WHERE status='sent' AND size>0").fetchall()
    else:
        if not car_policy.ntp_synced({}):
            return 0
        cutoff = int(time.time()) - config["retention_days"] * 86400
        rows = db.execute("SELECT path FROM files WHERE status='sent' AND sent_at < ?",
                          (cutoff,)).fetchall()
    removed = 0
    for (relative_string,) in rows:
        path = config["spool_root"] / relative_string
        try:
            path.unlink()
            removed += 1
        except FileNotFoundError:
            pass
        db.execute("UPDATE files SET size=0 WHERE path=? AND status='sent'",
                   (relative_string,))
    db.commit()
    return removed


def status(db):
    for state, count, total in db.execute(
            "SELECT status, COUNT(*), COALESCE(SUM(size),0) FROM files GROUP BY status"):
        print("{}: {} files, {} bytes".format(state, count, total))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("scan", "send", "prune", "status"))
    parser.add_argument("--config", default="/etc/teslausb-car-link.ini")
    args = parser.parse_args()
    config = load_config(args.config)
    db = database(config)
    try:
        if args.command == "scan":
            with lock(config["spool_root"], "scan"):
                policy = car_policy.load_policy(args.config)
                staged, skipped = scan(config, db, policy)
                print("staged {} files; skipped {} for retry/review".format(staged, skipped))
        elif args.command == "send":
            with lock(config["spool_root"], "send"):
                policy = car_policy.load_policy(args.config)
                print("uploaded {} files".format(send(config, db, policy)))
        elif args.command == "prune":
            with lock(config["spool_root"], "scan"):
                print("removed {} sent local copies".format(prune(config, db)))
        else:
            status(db)
    finally:
        db.close()


if __name__ == "__main__":
    main()
