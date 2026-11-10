#!/usr/bin/env python3
"""Resolve radio roles using permanent MAC addresses, never wlan numbering."""

import json
import re
import subprocess
import sys
from pathlib import Path

SETTINGS = Path('/etc/teslausb-car-radios.json')
NET_ROOT = Path('/sys/class/net')
MAC = re.compile(r'^[0-9a-f]{2}(?::[0-9a-f]{2}){5}$')


def settings(path=None):
    path = Path(path) if path else SETTINGS
    try:
        data = json.loads(path.read_text())
    except FileNotFoundError:
        # Compatibility until explicit roles are installed on an older image.
        return {'uplink_driver': 'brcmfmac'}
    if not isinstance(data, dict):
        raise ValueError('Radio settings must be an object')
    return data


def permanent_mac(interface, net_root=None):
    root = Path(net_root) if net_root else NET_ROOT
    current = (root / interface / 'address').read_text().strip().lower()
    try:
        result = subprocess.run(['/usr/sbin/ethtool', '-P', interface],
                                capture_output=True, text=True, timeout=2)
        match = re.search(r'Permanent address:\s*([0-9a-f:]{17})', result.stdout, re.I)
        if (result.returncode == 0 and match and MAC.fullmatch(match.group(1).lower())
                and match.group(1).lower() not in ('00:00:00:00:00:00', 'ff:ff:ff:ff:ff:ff')):
            return match.group(1).lower()
    except (OSError, subprocess.TimeoutExpired):
        pass
    return current


def interface_for(role, config=None, net_root=None):
    data = settings() if config is None else config
    root = Path(net_root) if net_root else NET_ROOT
    target = data.get(role + '_mac', '').lower()
    driver = data.get(role + '_driver', '')
    if target and not MAC.fullmatch(target):
        raise ValueError('Invalid ' + role + ' radio MAC')
    if not root.is_dir():
        return None
    for candidate in sorted(root.iterdir()):
        try:
            actual_driver = (candidate / 'device' / 'driver').resolve(strict=True).name
            if not (candidate / 'phy80211').exists() and actual_driver not in (
                    'brcmfmac', 'mt7921u', 'rtw88_8814au'):
                continue
            if target:
                current = (candidate / 'address').read_text().strip().lower()
                if current == target or permanent_mac(candidate.name, root) == target:
                    return candidate.name
            elif driver and actual_driver == driver:
                return candidate.name
        except OSError:
            continue
    return None


def uplink_interface(net_root=None):
    return interface_for('uplink', net_root=net_root)


def require_uplink():
    interface = uplink_interface()
    if not interface:
        raise RuntimeError('Wi-Fi uplink adapter is unavailable')
    return interface


def profile_uses_uplink(bound_mac, bound_interface, active_interface, macs, interfaces):
    """Preserve station profiles dedicated to any other attached radio."""
    macs = {value.lower() for value in macs if value}
    interfaces = {value for value in interfaces if value}
    # nmcli's terse single-property output escapes the colon separators.
    bound_mac = (bound_mac or '').replace('\\:', ':').lower()
    bound_interface = bound_interface if bound_interface != '--' else ''
    active_interface = active_interface if active_interface != '--' else ''
    return (not bound_mac or bound_mac in macs) and (
        not bound_interface or bound_interface in interfaces) and (
        not active_interface or active_interface in interfaces)


if __name__ == '__main__':
    if len(sys.argv) != 2 or sys.argv[1] not in ('uplink', 'hotspot'):
        raise SystemExit('Choose uplink or hotspot')
    interface = interface_for(sys.argv[1])
    if not interface:
        raise SystemExit('Radio is unavailable')
    print(interface)
