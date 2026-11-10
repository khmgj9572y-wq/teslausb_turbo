#!/usr/bin/env python3
"""Bind station profiles to a tested MT7921AU; activation is a separate step.

Run from this checkout, or alongside the installed modules on the Pi. --apply
requires a writable root and an already tested connection on the new adapter.
It never accepts or prints Wi-Fi passwords in command-line arguments.
"""

import argparse
import configparser
import tempfile
import json
import os
import secrets
import shutil
import subprocess
import sys
import re
import pwd
from pathlib import Path

sys.path.insert(0, '/usr/local/lib/teslausb-car-link')
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import radio_policy
import wifi_admin
import nm_storage


def nm(*args):
    return wifi_admin.nmcli(*args).stdout.strip()


def candidate():
    settings = radio_policy.settings()
    adapters = radio_policy.interfaces_for_driver('mt7921u')
    selector = settings.get('future_uplink_mac', '')
    if not selector:
        raise RuntimeError('Set future_uplink_mac in the local radio config before cutover')
    interface = radio_policy.interface_for('candidate', {'candidate_mac': selector})
    if interface not in adapters:
        raise RuntimeError('The configured USB uplink is unavailable')
    return interface, selector.lower()


def require_usb2(interface):
    speed = radio_policy.usb_speed(interface)
    if speed is None or speed > 480:
        raise RuntimeError('Radio must be on USB 2; keep the SSD alone on USB 3')


def hotspot_target(settings, uplink_mac):
    target = settings.get('future_hotspot_mac', '')
    if not target and settings.get('split_enabled') and settings.get('hotspot_driver') == 'mt7921u':
        target = settings.get('hotspot_mac', '')
    if not target:
        return None
    target = target.lower()
    if target == uplink_mac.lower():
        raise RuntimeError('Uplink and hotspot require separate USB radios')
    interface = radio_policy.interface_for('hotspot', {'hotspot_mac': target})
    if interface not in radio_policy.interfaces_for_driver('mt7921u'):
        raise RuntimeError('The configured USB hotspot radio is unavailable')
    require_usb2(interface)
    return target


def apply(interface, mac):
    if os.geteuid() != 0 or os.statvfs('/').f_flag & os.ST_RDONLY:
        raise RuntimeError('Cutover requires root with the root filesystem writable')
    require_usb2(interface)
    with nm_storage.profile_writes():
        return apply_writable(interface, mac)


def apply_writable(interface, mac):
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
    hotspot_mac = hotspot_target(settings, mac)
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
    backup_root = Path('/root/car-link-radio-backup')
    backup_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    backup = Path(tempfile.mkdtemp(prefix='uplink.', dir=backup_root))
    for source in (Path('/etc/netplan'), Path('/etc/NetworkManager/system-connections'), wifi_admin.STORE):
        if source.is_dir():
            shutil.copytree(source, backup / source.name, symlinks=True)
    shutil.copy2(radio_policy.SETTINGS, backup / 'radios.json')
    rebound = 0
    old_interface = radio_policy.interface_for('uplink', settings)
    lines = nm('-t', '-f', 'NAME,UUID,TYPE,DEVICE', 'connection', 'show').splitlines()
    for line in lines:
        fields = wifi_admin.split_terse(line)
        if len(fields) != 4 or fields[2] not in ('wifi', '802-11-wireless'):
            continue
        name, identifier, _, active_interface = fields
        if name in ('car-hotspot', 'car-uplink-probe') or identifier in managed:
            continue
        if nm('-g', '802-11-wireless.mode', 'connection', 'show', 'uuid', identifier) == 'ap':
            continue
        if not radio_policy.profile_uses_uplink(
                nm('-g', '802-11-wireless.mac-address', 'connection', 'show', 'uuid', identifier),
                nm('-g', 'connection.interface-name', 'connection', 'show', 'uuid', identifier),
                active_interface, (settings.get('uplink_mac'), mac), (old_interface, interface)):
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
    settings.update(uplink_mac=mac, uplink_driver='mt7921u', split_enabled=bool(hotspot_mac),
                    hotspot_mac=hotspot_mac or '', hotspot_driver='mt7921u')
    wifi_admin.atomic_write(radio_policy.SETTINGS, (json.dumps(settings, indent=2) + '\n').encode())
    # This file has radio identities only; the desktop receiver must read it.
    os.chown(radio_policy.SETTINGS, 0, account.pw_gid)
    radio_policy.SETTINGS.chmod(0o640)
    directory = Path('/mutable/car-link/radios')
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    key = directory / 'hotspot.pass'
    if hotspot_mac and not key.exists():
        wifi_admin.atomic_write(key, (secrets.token_urlsafe(18) + '\n').encode())
    wifi_admin.atomic_write(directory / 'cutover.json', json.dumps({
        'stage': 'profiles-bound-activation-pending', 'interface': interface,
        'uplink_mac': mac, 'profiles_rebound': rebound, 'backup': str(backup)}).encode())
    print('Profiles bound to tested MT7921AU:', rebound)
    print('Activate the saved uplink profile on that adapter.')
    print('USB hotspot is configured; test it before enabling services.' if hotspot_mac else
          'Hotspot stays disabled until future_hotspot_mac selects its dedicated USB adapter.')
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
