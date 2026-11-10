#!/usr/bin/env python3
"""Seed local SSD state once, and save only NTP-trusted clock timestamps."""
import os
import ctypes
import pwd
import shutil
import sys
import tempfile
from pathlib import Path


def seed(source, destination, uid, gid, mode, clock_only=False):
    source, destination = Path(source), Path(destination)
    if destination.is_symlink():
        raise RuntimeError('State destination must not be a symlink')
    if destination.exists() and not destination.is_dir():
        raise RuntimeError('State destination must be a directory')
    created = not destination.exists()
    if created:
        temporary = Path(tempfile.mkdtemp(prefix='.car-state-', dir=destination.parent))
        try:
            if clock_only:
                if (source / 'clock').is_file():
                    shutil.copy2(source / 'clock', temporary / 'clock')
            elif source.is_dir():
                shutil.copytree(source, temporary, dirs_exist_ok=True, symlinks=True)
            temporary.chmod(mode)
            os.chown(temporary, uid, gid)
            temporary.rename(destination)
        finally:
            if temporary.exists():
                shutil.rmtree(temporary)
    destination.chmod(mode)
    os.chown(destination, uid, gid)
    clock = destination / 'clock'
    if clock_only and clock.is_file() and not clock.is_symlink():
        os.chown(clock, uid, gid)
    return created


def prepare():
    if os.geteuid() != 0 or not os.path.ismount('/mutable'):
        raise RuntimeError('Local state preparation requires root and mounted /mutable')
    account = pwd.getpwnam('systemd-timesync')
    seed('/var/lib/bluetooth', '/mutable/bluetooth', 0, 0, 0o700)
    seed('/var/lib/systemd/timesync', '/mutable/timesync', account.pw_uid, account.pw_gid, 0o755, clock_only=True)
    print('Local Bluetooth and timesync state prepared; existing SSD contents preserved')


def ntp_synchronized():
    # modes=0 is a read-only timex query. The returned state is the same
    # kernel synchronization state used for NTPSynchronized, without D-Bus.
    try:
        function = ctypes.CDLL(None, use_errno=True).adjtimex
        function.argtypes = [ctypes.c_void_p]
        function.restype = ctypes.c_int
        buffer = ctypes.create_string_buffer(512)
        result = function(buffer)
        return 0 <= result < 5  # TIME_ERROR (5) includes STA_UNSYNC.
    except (AttributeError, OSError):
        return False


def save_clock(path=Path('/var/lib/systemd/timesync')):
    path = Path(path)
    if not os.path.ismount(path):
        return False
    if not ntp_synchronized():
        return False
    account = pwd.getpwnam('systemd-timesync')
    descriptor = os.open(path / 'clock', os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW, 0o644)
    try:
        os.fchown(descriptor, account.pw_uid, account.pw_gid)
        os.utime(descriptor, None)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    return True


if __name__ == '__main__':
    if sys.argv[1:] == ['prepare']:
        prepare()
    elif sys.argv[1:] == ['save-clock']:
        print('Trusted clock timestamp saved' if save_clock() else 'Clock not saved: writable bind or NTP sync unavailable')
    else:
        raise SystemExit('Choose prepare or save-clock')
