#!/usr/bin/env python3
"""Bind station profiles to a tested MT7921AU; activation is a separate step.

Run from this checkout, or alongside the installed modules on the Pi. --apply
requires a writable root and an already tested connection on the new adapter.
It never accepts or prints Wi-Fi passwords in command-line arguments.
"""

import argparse
import configparser
import datetime
import json
import os
import secrets
import shutil
import subprocess
import sys
import re
import pwd
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, '/usr/local/lib/teslausb-car-link')
import radio_policy
import wifi_admin


def nm(*args):
    return wifi_admin.nmcli(*args).stdout.strip()


def candidate():
    interface = radio_policy.interface_for('candidate', {'candidate_driver': 'mt7921u'})
    if not interface:
        raise RuntimeError('MT7921AU is not attached; current radio settings were kept')
    return interface, radio_policy.permanent_mac(interface)


def apply(interface, mac):
    if os.geteuid() != 0 or os.statvfs('/').f_flag & os.ST_RDONLY:
        raise RuntimeError('Cutover requires root with the root filesystem writable')
    if nm('-g', 'GENERAL.STATE', 'device', 'show', interface).split()[0] != '100':
        raise RuntimeError('Connect and test the new adapter before rebinding profiles')
    probe = wifi_admin.command(['curl', '--interface', 'if!' + interface, '--silent',
             '--output', '/dev/null', '--write-out', '%{http_code}', '--max-time', '8',
             wifi_admin.PROBE_URL], timeout=12, check=False)
    if probe.returncode or probe.stdout != '204':
        raise RuntimeError('The new adapter has not passed its internet test')
    settings = radio_policy.settings()
    if mac == settings.get('hotspot_mac'):
        raise RuntimeError('The onboard hotspot radio cannot be the uplink')
    user = Path('/etc/teslausb-car-wifi.env').read_text()
    match = re.search(r'^PORTAL_USER=([A-Za-z0-9_-]+)$', user, re.M)
    if not match:
        raise RuntimeError('Non-root receiver account is not configured')
    account = pwd.getpwnam(match.group(1))
    managed = wifi_admin.metadata()
    parsed_profiles = {}
    for identifier in managed:
        parser = configparser.ConfigParser(interpolation=None)
        parser.read(wifi_admin.profile_file(identifier))
        if 'connection' not in parser or 'wifi' not in parser:
            raise RuntimeError('Managed Wi-Fi profile needs repair before cutover')
        parsed_profiles[identifier] = parser
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    backup = Path('/root/car-link-radio-backup') / stamp
    backup.mkdir(parents=True, mode=0o700)
    for source in (Path('/etc/netplan'), Path('/etc/NetworkManager/system-connections'), wifi_admin.STORE):
        if source.is_dir():
            shutil.copytree(source, backup / source.name, symlinks=True)
    shutil.copy2(radio_policy.SETTINGS, backup / 'radios.json')
    rebound = 0
    lines = nm('-t', '-f', 'NAME,UUID,TYPE', 'connection', 'show').splitlines()
    for line in lines:
        fields = wifi_admin.split_terse(line)
        if len(fields) != 3 or fields[2] not in ('wifi', '802-11-wireless'):
            continue
        name, identifier, _ = fields
        if name in ('car-hotspot', 'car-uplink-probe') or identifier in managed:
            continue
        if nm('-g', '802-11-wireless.mode', 'connection', 'show', 'uuid', identifier) == 'ap':
            continue
        nm('connection', 'modify', 'uuid', identifier,
           'connection.interface-name', '', '802-11-wireless.mac-address', mac,
           '802-11-wireless.cloned-mac-address', 'permanent',
           'ipv4.route-metric', '600', 'ipv6.route-metric', '600')
        rebound += 1
    for identifier in managed:
        path = wifi_admin.profile_file(identifier)
        parser = parsed_profiles[identifier]
        parser['connection'].pop('interface-name', None)
        parser['wifi']['mac-address'] = mac
        parser['wifi']['cloned-mac-address'] = 'permanent'
        import io
        output = io.StringIO()
        parser.write(output)
        wifi_admin.atomic_write(path, output.getvalue().encode())
        wifi_admin.stage(identifier)
        rebound += 1
    settings.update(uplink_mac=mac, uplink_driver='mt7921u', split_enabled=True)
    wifi_admin.atomic_write(radio_policy.SETTINGS, (json.dumps(settings, indent=2) + '\n').encode())
    # This file has radio identities only; the desktop receiver must read it.
    os.chown(radio_policy.SETTINGS, 0, account.pw_gid)
    radio_policy.SETTINGS.chmod(0o640)
    directory = Path('/mutable/car-link/radios')
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    key = directory / 'hotspot.pass'
    if not key.exists():
        wifi_admin.atomic_write(key, (secrets.token_urlsafe(18) + '\n').encode())
    wifi_admin.atomic_write(directory / 'cutover.json', json.dumps({
        'stage': 'profiles-bound-activation-pending', 'interface': interface,
        'uplink_mac': mac, 'profiles_rebound': rebound, 'backup': str(backup)}).encode())
    print('Profiles bound to tested MT7921AU:', rebound)
    print('Activate the saved uplink profile on that adapter before starting the hotspot.')
    print('Restore read-only root after the controlled cutover.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    interface, mac = candidate()
    if args.apply:
        apply(interface, mac)
    else:
        print('MT7921AU detected on', interface, '; no changes made')


if __name__ == '__main__':
    try:
        main()
    except (ValueError, RuntimeError, OSError) as error:
        raise SystemExit(str(error))
