#!/usr/bin/env python3
"""Identify UART Bluetooth without publishing controller addresses."""

from pathlib import Path
import sys


def controllers(root=Path('/sys/class/bluetooth')):
    found = []
    for path in sorted(Path(root).glob('hci[0-9]*')):
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
                      '; tesla-control expects hci0. Check adapter assignment before BLE commands.')
    return result


def main():
    found = controllers()
    for message in messages(found):
        print(message, flush=True)
    if '--require-hci0' in sys.argv[1:]:
        return 0 if [item['adapter'] for item in found if item['transport'] == 'uart'] == ['hci0'] else 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
