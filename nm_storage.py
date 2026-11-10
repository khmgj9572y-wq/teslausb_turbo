#!/usr/bin/env python3
"""Temporarily permit persistent NM profile saves during root maintenance."""

import os
import subprocess
from contextlib import contextmanager


def run(args):
    return subprocess.run(args, capture_output=True, text=True, check=True, timeout=10)


@contextmanager
def profile_writes():
    if os.statvfs('/').f_flag & os.ST_RDONLY:
        raise RuntimeError('Root must be temporarily writable before saving station profiles')
    pid = run(['systemctl', 'show', 'NetworkManager.service', '-p', 'MainPID', '--value']).stdout.strip()
    if not pid.isdigit() or int(pid) <= 1:
        raise RuntimeError('NetworkManager is not running')
    enter = ['nsenter', '-t', pid, '-m', '--']
    options = run(enter + ['findmnt', '-n', '-o', 'OPTIONS', '/']).stdout.strip().split(',')
    readonly = 'ro' in options
    if readonly:
        # A bind remount changes only this namespace's mount flag. It does
        # not remount the shared superblock or restart NetworkManager.
        run(enter + ['mount', '-o', 'remount,bind,rw', '/'])
    try:
        yield
    finally:
        if readonly:
            run(enter + ['mount', '-o', 'remount,bind,ro', '/'])
