#!/usr/bin/env python3
"""Download and verify a TeslaUSB generic installer, then pin its fetches.

This tool only writes the prepared script. It never executes TeslaUSB setup.
Inspect the output before running it, and use a spare SSD for first setup.
"""

import argparse
import hashlib
import os
import tempfile
import urllib.request
from pathlib import Path


COMMIT = "fb4ed15b46dc41a2ec7c8a7a2fe0783ce1d2ffd9"
INSTALLER_BLOB_SHA1 = "61390f33af915575b94a61a3b57c18ae5306669d"
URL = ("https://raw.githubusercontent.com/marcone/teslausb/" + COMMIT +
       "/setup/generic/install.sh")


def git_blob_sha1(data):
    header = b"blob " + str(len(data)).encode("ascii") + b"\0"
    return hashlib.sha1(header + data).hexdigest()


def prepare(data):
    if git_blob_sha1(data) != INSTALLER_BLOB_SHA1:
        raise ValueError("upstream installer does not match the recorded Git blob")
    text = data.decode("utf-8")
    if text.count("main-dev") < 4:
        raise ValueError("installer's upstream fetch references changed")
    return text.replace("main-dev", COMMIT).encode("utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", help="destination for the inspectable script")
    args = parser.parse_args()
    destination = Path(args.output)
    if destination.exists():
        raise ValueError("destination already exists: " + str(destination))
    with urllib.request.urlopen(URL, timeout=30) as reply:
        data = reply.read(2 * 1024 * 1024 + 1)
    if len(data) > 2 * 1024 * 1024:
        raise ValueError("installer unexpectedly large")
    prepared = prepare(data)
    destination.parent.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(prefix=".teslausb-install-", dir=str(destination.parent))
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(prepared)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temp, 0o700)
        os.replace(temp, str(destination))
    finally:
        if os.path.exists(temp):
            os.unlink(temp)
    print("prepared {} from commit {}".format(destination, COMMIT))
    print("sha256 {}".format(hashlib.sha256(prepared).hexdigest()))
    print("review the file; run it only on a new SD card with a spare SSD")


if __name__ == "__main__":
    main()
