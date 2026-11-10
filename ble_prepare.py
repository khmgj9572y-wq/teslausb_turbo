#!/usr/bin/env python3
"""Prepare a private per-boot cache for Tesla's BLE command client."""

import json
import os
from pathlib import Path


def main():
    directory = Path('/run/teslausb-ble')
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    directory.chmod(0o700)
    path = directory / 'session-cache.json'
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        path.chmod(0o600)
        return
    with os.fdopen(descriptor, 'w') as handle:
        json.dump({'MaxEntries': 0, 'vehicles': {}}, handle)
        handle.write('\n')


if __name__ == '__main__':
    main()
