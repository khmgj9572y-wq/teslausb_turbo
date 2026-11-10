#!/usr/bin/env python3
"""Bind current uplink station profiles without activating or switching radios."""

import argparse
import os
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, '/usr/local/lib/teslausb-car-link')
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import radio_policy
import wifi_admin
import nm_storage


def nm(*args):
    return wifi_admin.nmcli(*args).stdout.strip()


def candidates(settings, interface, mac):
    found, skipped = [], 0
    for line in nm('-t', '-f', 'UUID,TYPE,DEVICE', 'connection', 'show').splitlines():
        fields = wifi_admin.split_terse(line)
        if len(fields) != 3 or fields[1] not in ('wifi', '802-11-wireless'):
            continue
        identifier, _, active = fields
        if nm('-g', '802-11-wireless.mode', 'connection', 'show', 'uuid', identifier) == 'ap':
            continue
        bound_mac = nm('-g', '802-11-wireless.mac-address', 'connection', 'show', 'uuid', identifier)
        bound_interface = nm('-g', 'connection.interface-name', 'connection', 'show', 'uuid', identifier)
        if not radio_policy.profile_uses_uplink(bound_mac, bound_interface, active, (mac,), (interface,)):
            skipped += 1
            continue
        found.append(identifier)
    return found, skipped


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    settings = radio_policy.settings()
    interface = radio_policy.require_uplink()
    mac = settings.get('uplink_mac') or radio_policy.permanent_mac(interface)
    profiles, skipped = candidates(settings, interface, mac)
    if not args.apply:
        print('Eligible uplink station profiles: {}; other-radio profiles preserved: {}'.format(
            len(profiles), skipped))
        return
    if os.geteuid() != 0 or os.statvfs('/').f_flag & os.ST_RDONLY:
        raise RuntimeError('Apply requires root with root temporarily writable')
    backup = Path(tempfile.mkdtemp(prefix='car-station-backup.', dir='/root'))
    backup.chmod(0o700)
    for source in (Path('/etc/netplan'), Path('/etc/NetworkManager/system-connections'), wifi_admin.STORE):
        if source.is_dir():
            shutil.copytree(source, backup / source.name, symlinks=True)
    managed = wifi_admin.metadata()
    with nm_storage.profile_writes():
        for identifier in profiles:
            nm('connection', 'modify', 'uuid', identifier,
               'connection.interface-name', '', '802-11-wireless.mac-address', mac,
               '802-11-wireless.cloned-mac-address', 'permanent',
               'ipv4.route-metric', '600', 'ipv6.route-metric', '600')
            if identifier in managed:
                # Picker profiles are restored from /mutable at boot, not /etc.
                import configparser
                import io
                path = wifi_admin.profile_file(identifier)
                p = configparser.ConfigParser(interpolation=None)
                p.read(path)
                p['connection'].pop('interface-name', None)
                p['wifi']['mac-address'] = mac
                p['wifi']['cloned-mac-address'] = 'permanent'
                for family in ('ipv4', 'ipv6'):
                    if family in p:
                        p[family]['route-metric'] = '600'
                out = io.StringIO(); p.write(out)
                wifi_admin.atomic_write(path, out.getvalue().encode())
                wifi_admin.stage(identifier)
    print('Bound {} uplink profiles; preserved {} other-radio profiles; no connection was activated'.format(
        len(profiles), skipped))


if __name__ == '__main__':
    main()
