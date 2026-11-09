#!/usr/bin/env python3
"""Start a local-only hotspot and constrain AirPlay to that radio."""

import ipaddress
import json
import os
import pwd
import re
import subprocess
import sys
import uuid
from pathlib import Path

import radio_policy
import wifi_admin

RUNTIME = Path('/run/car-hotspot')
STORE = Path('/mutable/car-link/radios')


def run(args, check=True, text=None):
    return subprocess.run(args, input=text, text=True, stdout=subprocess.PIPE,
                          stderr=subprocess.DEVNULL, check=check, timeout=40)


def hotspot_settings():
    settings = radio_policy.settings()
    if not settings.get('split_enabled'):
        raise RuntimeError('Two-radio cutover has not been activated')
    if settings.get('uplink_mac') == settings.get('hotspot_mac'):
        raise RuntimeError('Uplink and hotspot must be different radios')
    interface = radio_policy.interface_for('hotspot', settings)
    if not interface or not re.fullmatch(r'[A-Za-z0-9_.-]{1,15}', interface):
        raise RuntimeError('Hotspot radio is unavailable')
    return settings, interface


def hotspot_up():
    settings, interface = hotspot_settings()
    if not os.path.ismount('/mutable'):
        raise RuntimeError('Writable SSD is unavailable')
    hotspot = settings.get('hotspot', {})
    ssid = wifi_admin.validate_ssid(hotspot.get('ssid', 'Car Screen'))
    password_path = STORE / 'hotspot.pass'
    if not password_path.is_file():
        raise RuntimeError('Hotspot password has not been provisioned')
    password = wifi_admin.validate_password(password_path.read_text().strip())
    if not password:
        raise RuntimeError('Hotspot requires a password')
    subnet = ipaddress.IPv4Interface(hotspot.get('address', '10.42.0.1/24'))
    if subnet.network.prefixlen != 24 or subnet.ip != subnet.network.network_address + 1:
        raise ValueError('Hotspot needs a /24 with gateway at .1')
    channel = int(hotspot.get('channel', 36))
    if channel not in (36, 40, 44, 48):
        raise ValueError('Choose a non-DFS 5 GHz channel')
    identifier = str(uuid.uuid5(uuid.NAMESPACE_DNS, 'teslausb-hotspot-' + settings['hotspot_mac']))
    keyfile = '\n'.join([
        '[connection]', 'id=car-hotspot', 'uuid=' + identifier, 'type=wifi',
        'autoconnect=true', 'autoconnect-priority=100', '', '[wifi]',
        'mode=ap', 'band=a', 'channel=' + str(channel),
        'mac-address=' + settings['hotspot_mac'], 'cloned-mac-address=permanent',
        'ssid=' + wifi_admin.keyfile_value(ssid), '', '[wifi-security]',
        'key-mgmt=wpa-psk', 'proto=rsn', 'pairwise=ccmp', 'group=ccmp',
        'psk=' + wifi_admin.keyfile_value(password), '', '[ipv4]',
        'method=manual', 'address1=' + str(subnet), 'never-default=true',
        '', '[ipv6]', 'method=disabled', ''])
    stored = STORE / 'car-hotspot.nmconnection'
    live = wifi_admin.RUNTIME / stored.name
    wifi_admin.atomic_write(stored, keyfile.encode())
    wifi_admin.atomic_write(live, keyfile.encode())
    run(['nmcli', 'connection', 'load', str(live)])
    run(['nmcli', '-w', '25', 'connection', 'up', 'uuid', identifier, 'ifname', interface])
    RUNTIME.mkdir(parents=True, exist_ok=True)
    nobody = pwd.getpwnam('nobody')
    os.chown(RUNTIME, nobody.pw_uid, nobody.pw_gid)
    os.chmod(RUNTIME, 0o750)
    network = subnet.network.network_address
    dhcp = '\n'.join([
        'port=0', 'bind-interfaces', 'interface=' + interface,
        'user=nobody', 'dhcp-authoritative',
        'dhcp-range=' + str(network + 20) + ',' + str(network + 200) + ',255.255.255.0,12h',
        # No router or DNS options: internet remains on the phone's cellular link.
        'dhcp-option=3', 'dhcp-option=6',
        'dhcp-leasefile=' + str(RUNTIME / 'leases'), ''])
    (RUNTIME / 'dnsmasq.conf').write_text(dhcp)
    (RUNTIME / 'dnsmasq.conf').chmod(0o644)
    return 'Local hotspot ready'


def airplay_prepare():
    settings, interface = hotspot_settings()
    result = run(['nmcli', '-g', 'GENERAL.CONNECTION', 'device', 'show', interface])
    if result.stdout.strip() != 'car-hotspot':
        raise RuntimeError('AirPlay requires the active hotspot')
    avahi = Path('/etc/avahi/avahi-daemon.conf').read_text()
    if re.search(r'(?m)^\s*enable-reflector\s*=\s*yes\s*$', avahi):
        raise RuntimeError('Avahi reflection would leak hotspot advertisements')
    for decoder in ('v4l2h264dec', 'v4l2convert', 'waylandsink'):
        run(['gst-inspect-1.0', decoder])
    if not Path('/usr/local/lib/teslausb-car-link/dnssd_scope.so').is_file():
        raise RuntimeError('Scoped DNS-SD library is missing')
    run(['/usr/sbin/nft', 'delete', 'table', 'inet', 'car_airplay'], check=False)
    quoted = json.dumps(interface)
    rules = ('table inet car_airplay { chain input { type filter hook input priority -10; policy accept; '
             'tcp dport 7100-7102 iifname != ' + quoted + ' drop; '
             'udp dport 7100-7102 iifname != ' + quoted + ' drop; } }\n')
    run(['/usr/sbin/nft', '-f', '-'], text=rules)
    return 'AirPlay restricted to hotspot'


def main():
    if len(sys.argv) != 2:
        raise ValueError('Choose a radio action')
    action = sys.argv[1]
    if action == 'hotspot-up':
        print(hotspot_up())
    elif action == 'hotspot-down':
        run(['nmcli', 'connection', 'down', 'id', 'car-hotspot'], check=False)
    elif action == 'airplay-prepare':
        print(airplay_prepare())
    elif action == 'airplay-cleanup':
        run(['/usr/sbin/nft', 'delete', 'table', 'inet', 'car_airplay'], check=False)
    else:
        raise ValueError('Unknown radio action')


if __name__ == '__main__':
    main()
