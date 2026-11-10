#!/usr/bin/env python3
"""Identify UART Bluetooth without publishing controller addresses."""

from pathlib import Path
import sys
import re
import subprocess


def controllers(root=Path('/sys/class/bluetooth')):
    found = []
    for path in sorted(Path(root).glob('hci[0-9]*')):
        if not re.fullmatch(r'hci[0-9]+', path.name):
            continue
        try:
            device = (path / 'device').resolve(strict=True)
            drivers, buses = set(), set()
            for parent in (device, *device.parents):
                for name, values in (('driver', drivers), ('subsystem', buses)):
                    link = parent / name
                    if link.is_symlink():
                        values.add(link.resolve(strict=True).name)
            transport = ('usb' if 'usb' in buses else
                         'uart' if 'serial' in buses or any(
                             name.startswith('hci_uart') for name in drivers) else 'unknown')
            found.append({'adapter': path.name, 'transport': transport})
        except OSError:
            continue
    return found


def messages(found):
    result = ['Bluetooth adapters: ' + (', '.join(
        item['adapter'] + '=' + item['transport'] for item in found) or 'none')]
    uart = [item['adapter'] for item in found if item['transport'] == 'uart']
    if uart == ['hci0']:
        result.append('Car-key UART adapter is hci0; shared lock is /run/lock/hci0.lock')
    else:
        result.append('WARNING: car-key UART adapter is ' + (', '.join(uart) or 'unavailable') +
                      '; the car-key wrapper must select it explicitly. Shared lock remains /run/lock/hci0.lock.')
    return result


def power_on(found):
    uart = [item['adapter'] for item in found if item['transport'] == 'uart']
    if len(uart) != 1:
        print('UART adapter is unavailable or ambiguous; no controller was powered', flush=True)
        return 1
    result = subprocess.run(['flock', '-w', '120', '/run/lock/hci0.lock',
                             'busctl', '--system', '--timeout=10s', 'set-property', 'org.bluez',
                             '/org/bluez/' + uart[0], 'org.bluez.Adapter1', 'Powered', 'b', 'true'],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=140)
    return result.returncode


def main():
    found = controllers()
    for message in messages(found):
        print(message, flush=True)
    if '--require-hci0' in sys.argv[1:]:
        return 0 if [item['adapter'] for item in found if item['transport'] == 'uart'] == ['hci0'] else 1
    if '--power-on' in sys.argv[1:]:
        return power_on(found)
    return 0


if __name__ == '__main__':
    sys.exit(main())
