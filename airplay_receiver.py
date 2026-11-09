#!/usr/bin/env python3
"""Run packaged UxPlay without persisting client debug data."""

import os
import signal
import subprocess
import time
import uuid
from pathlib import Path

import radio_policy

START = 'raop_rtp_mirror starting mirroring'
STOP = ('raop_rtp_mirror->running is no longer true',
        '***ERROR lost connection with client')


def event(line):
    if START in line:
        return 'claim'
    if any(value in line for value in STOP):
        return 'release'
    return None


def main():
    settings = radio_policy.settings()
    interface = radio_policy.interface_for('hotspot', settings)
    if not interface:
        raise RuntimeError('Hotspot radio is unavailable')
    active = subprocess.run(['nmcli', '-g', 'GENERAL.CONNECTION', 'device', 'show', interface],
                            capture_output=True, text=True, timeout=5)
    if active.returncode or active.stdout.strip() != 'car-hotspot':
        raise RuntimeError('AirPlay requires the active car hotspot')
    config = settings.get('airplay', {})
    args = ['/usr/bin/uxplay', '-n', config.get('name', 'Car Screen'), '-nh',
            '-m', settings['hotspot_mac'],
            '-p', '7100', '-fs', '-nofreeze', '-vsync', 'no', '-fps', '30', '-s', '1280x720',
            '-vd', 'v4l2h264dec', '-vc', 'v4l2convert', '-bt709',
            '-vs', 'waylandsink fullscreen=true']
    if not config.get('audio_enabled', False):
        args += ['-a']
    env = dict(os.environ, CAR_AIRPLAY_INTERFACE=interface,
               LD_PRELOAD='/usr/local/lib/teslausb-car-link/dnssd_scope.so')
    token = uuid.uuid4().hex
    helper = str(Path(__file__).with_name('display_owner.py'))
    process = subprocess.Popen(['/usr/bin/stdbuf', '-oL', '-eL', *args], env=env,
                               stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                               text=True, errors='replace', bufsize=1)
    def terminate(signum, frame):
        process.terminate()
    signal.signal(signal.SIGTERM, terminate)
    signal.signal(signal.SIGINT, terminate)
    owned = False
    try:
        for line in process.stdout:
            action = event(line)
            if action == 'claim' and not owned:
                subprocess.run(['sudo', '-n', helper, 'claim', token], check=True, timeout=20)
                owned = True
                print('AirPlay mirroring connected', flush=True)
            elif action == 'release' and owned:
                time.sleep(0.3)
                subprocess.run(['sudo', '-n', helper, 'release', token], check=True, timeout=20)
                owned = False
                print('AirPlay mirroring disconnected', flush=True)
            # Other UxPlay output may contain device IDs, PINs, or media metadata.
            # It is intentionally not copied into the persistent journal.
        code = process.wait()
        if code:
            raise RuntimeError('UxPlay exited with status ' + str(code))
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
        subprocess.run(['sudo', '-n', helper, 'release', token], check=False, timeout=20)


if __name__ == '__main__':
    main()
