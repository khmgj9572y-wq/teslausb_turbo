#!/usr/bin/env python3
"""Cached read-only modem diagnostics for the authenticated 4G tab."""

import csv
import ipaddress
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import car_policy

CACHE = Path('/run/teslausb-car-link/lte')
READER = '/usr/local/sbin/ik41-info'
REGISTRATION = {0: 'Not registered', 1: 'Home network', 2: 'Searching',
                3: 'Registration denied', 4: 'Unknown', 5: 'Roaming',
                6: 'Home (SMS only)', 7: 'Roaming (SMS only)', 8: 'Emergency only'}
RADIO = {0: 'GSM', 1: 'GSM Compact', 2: 'UMTS', 3: 'EDGE', 4: 'HSDPA',
         5: 'HSUPA', 6: 'HSPA', 7: 'LTE', 8: 'EC-GSM', 9: 'NB-IoT'}


def command(args, timeout=8):
    return subprocess.run(args, capture_output=True, text=True, timeout=timeout, check=True)


def json_command(args):
    try:
        return json.loads(command(args).stdout)
    except (OSError, ValueError, subprocess.SubprocessError):
        return []


def read(path):
    try:
        return Path(path).read_text().strip()
    except OSError:
        return None


def at(group):
    try:
        result = subprocess.run([READER, group], capture_output=True, text=True, timeout=38)
        return json.loads(result.stdout)
    except (OSError, ValueError, subprocess.SubprocessError):
        return {'ok': False, 'error': 'AT reader unavailable or timed out', 'responses': {}}


def lines(responses, key):
    item = responses.get(key, {})
    if not item.get('ok'):
        return []
    return [s.strip() for s in item.get('text', '').splitlines()
            if s.strip() and s.strip() not in ('OK', 'ERROR') and not s.strip().startswith('AT')]


def row(responses, key, prefix):
    matches = [s[len(prefix):].strip() for s in lines(responses, key) if s.startswith(prefix)]
    if not matches:
        return []
    try:
        return next(csv.reader([matches[-1]], skipinitialspace=True))
    except (csv.Error, StopIteration):
        return []


def number(value):
    try:
        return int(value)
    except (ValueError, TypeError):
        return None


def quantized(code, offset, step, maximum, unit):
    value = number(code)
    if value is None or not 0 <= value <= maximum:
        return {'value': None, 'display': None}
    if value == 0:
        return {'value': offset + step, 'display': '< {:g} {}'.format(offset + step, unit)}
    low = offset + step * value
    text = ('≥ {:g} {}' if value == maximum else '{:g} to < {:g} {}')
    return {'value': low, 'display': text.format(low, unit) if value == maximum else text.format(low, low + step, unit)}


def parse(responses):
    result = {'signal': {}, 'contexts': [], 'active_contexts': [], 'registration': {},
              'bearers': [], 'pdp_addresses': []}
    for key in ['manufacturer', 'model', 'firmware', 'capabilities', 'last_error']:
        values = lines(responses, key)
        result[key] = '\n'.join(values) or None
    csq = row(responses, 'rssi', '+CSQ:')
    if len(csq) >= 2:
        index = number(csq[0]); ber = number(csq[1])
        result['signal'].update({'rssi_code': index if index is not None and 0 <= index <= 31 else None,
                                 'rssi_dbm': -113 + 2 * index if index is not None and 0 <= index <= 31 else None,
                                 'rssi_bound': '≤' if index == 0 else '≥' if index == 31 else '',
                                 'ber_code': None if ber == 99 else ber})
    cesq = row(responses, 'extended_signal', '+CESQ:')
    if len(cesq) >= 6:
        result['signal']['rsrq'] = quantized(cesq[4], -20, 0.5, 34, 'dB')
        result['signal']['rsrp'] = quantized(cesq[5], -141, 1, 97, 'dBm')
    operator = row(responses, 'operator', '+COPS:')
    if len(operator) >= 3:
        result['operator'] = operator[2]
        result['operator_format'] = number(operator[1])
        result['radio'] = RADIO.get(number(operator[3]), operator[3]) if len(operator) > 3 else None
    for key, prefix in [('registration', '+CEREG:'), ('packet_registration', '+CGREG:'),
                        ('circuit_registration', '+CREG:')]:
        fields = row(responses, key, prefix)
        if fields:
            status = number(fields[1] if len(fields) > 1 else fields[0])
            entry = {'code': status, 'state': REGISTRATION.get(status, 'Unknown')}
            if len(fields) >= 4:
                entry.update({'area_code': fields[2] or None, 'cell_id': fields[3] or None})
            result['registration'][key] = entry
    for key, prefix, output in [('sim', '+CPIN:', 'sim_state'), ('attachment', '+CGATT:', 'attached'),
                                 ('functionality', '+CFUN:', 'functionality'), ('usb_mode', '+USBMODE:', 'usb_mode')]:
        fields = row(responses, key, prefix)
        if fields:
            result[output] = fields[0] if key == 'sim' else number(fields[0])
    for line in lines(responses, 'apn'):
        if not line.startswith('+CGDCONT:'):
            continue
        fields = next(csv.reader([line.partition(':')[2]], skipinitialspace=True))
        if len(fields) >= 3:
            result['contexts'].append({'id': number(fields[0]), 'type': fields[1], 'apn': fields[2],
                                       'address': fields[3] if len(fields) > 3 else None})
    for line in lines(responses, 'contexts_active'):
        if line.startswith('+CGACT:'):
            fields = next(csv.reader([line.partition(':')[2]], skipinitialspace=True))
            if len(fields) >= 2:
                result['active_contexts'].append({'id': number(fields[0]), 'active': number(fields[1]) == 1})
    for line in lines(responses, 'context_details'):
        if line.startswith('+CGCONTRDP:'):
            fields = next(csv.reader([line.partition(':')[2]], skipinitialspace=True))
            if len(fields) >= 4:
                names = ['id', 'bearer_id', 'apn', 'local_address', 'gateway', 'dns_primary', 'dns_secondary']
                bearer = dict(zip(names, fields))
                bearer['id'] = number(bearer['id'])
                result['bearers'].append(bearer)
    for line in lines(responses, 'pdp_addresses'):
        if line.startswith('+CGPADDR:'):
            fields = next(csv.reader([line.partition(':')[2]], skipinitialspace=True))
            if fields:
                result['pdp_addresses'].append({'id': number(fields[0]), 'addresses': fields[1:]})
    result['band_codes'] = ', '.join(row(responses, 'band', '^GETBAND:')) or None
    result['vendor_signal_report'] = ', '.join(row(responses, 'radio_signal', '^HCSQ:')) or None
    result['vendor_lte_report'] = ', '.join(row(responses, 'lte_signal', '^LTERSRP:')) or None
    system = row(responses, 'system', '^SYSINFOEX:')
    if len(system) >= 9:
        result['system_radio'] = system[6]
        result['system_submode'] = system[8]
        result['system_roaming'] = number(system[2]) == 1
    return result


def usb_device(interface=None, usb_root=Path('/sys/bus/usb/devices')):
    if interface:
        parent = (interface / 'device').resolve().parent
        if read(parent / 'idVendor') == '1bbb':
            return parent
    for device in usb_root.iterdir():
        if read(device / 'idVendor') == '1bbb' and read(device / 'idProduct') in ('01aa', '00b6'):
            return device
    return None


def hardware(device):
    if device is None:
        return {'present': False}
    names = {'idVendor': 'vid', 'idProduct': 'pid', 'manufacturer': 'usb_manufacturer',
             'product': 'product', 'bcdDevice': 'device_revision', 'speed': 'speed_mbps',
             'bMaxPower': 'max_power', 'version': 'usb_version', 'bNumInterfaces': 'interfaces',
             'power/control': 'power_control', 'power/runtime_status': 'runtime_power'}
    return dict({'present': True, 'path': device.name}, **{name: read(device / source) for source, name in names.items()})


def network(interface, policy, previous):
    if interface is None:
        return {'present': False}
    now = car_policy.uptime(); name = interface.name
    addresses = json_command(['ip', '-j', 'addr', 'show', 'dev', name])
    entry = addresses[0] if addresses else {}
    rx = number(read(interface / 'statistics/rx_bytes')); tx = number(read(interface / 'statistics/tx_bytes'))
    index = number(read(interface / 'ifindex'))
    rates = {'rx_bps': None, 'tx_bps': None}
    elapsed = now - previous.get('sample_uptime', now)
    if previous.get('ifindex') == index and elapsed > 0:
        for key, value in [('rx', rx), ('tx', tx)]:
            old = previous.get(key + '_bytes')
            if value is not None and old is not None and value >= old:
                rates[key + '_bps'] = (value - old) / elapsed
    defaults = json_command(['ip', '-j', '-4', 'route', 'show', 'default'])
    own = [r for r in defaults if r.get('dev') == name]
    preferred = min(defaults, key=lambda r: r.get('metric', 0)).get('dev') if defaults else None
    try:
        dns = command(['nmcli', '-g', 'IP4.DNS', 'device', 'show', name]).stdout.splitlines()
    except (OSError, subprocess.SubprocessError):
        dns = []
    return {'present': True, 'interface': name, 'driver': (interface / 'device/driver').resolve().name,
            'operstate': entry.get('operstate'), 'up': 'UP' in entry.get('flags', []),
            'carrier': read(interface / 'carrier') == '1', 'mtu': entry.get('mtu'),
            'addresses': [a['local'] + '/' + str(a['prefixlen']) for a in entry.get('addr_info', [])],
            'gateway': own[0].get('gateway') if own else None, 'route_metric': own[0].get('metric', 0) if own else None,
            'dns': dns, 'preferred': preferred == name, 'tunnel_uses_lte': car_policy.active_link(policy) == name,
            'rx_bytes': rx, 'tx_bytes': tx, 'ifindex': index, 'sample_uptime': now,
            'errors': {key: number(read(interface / 'statistics' / key)) for key in
                       ('rx_errors', 'tx_errors', 'rx_dropped', 'tx_dropped')}, **rates}


def command_list(response):
    item = response.get('responses', {}).get('commands', {})
    # Some IK41 revisions return the list without a final OK. Preserve the
    # useful report and explicitly mark that terminator as absent.
    names = [s.strip() for s in item.get('text', '').splitlines()
             if re.fullmatch(r'[+^$&][A-Za-z0-9_]+', s.strip())]
    return {'commands': names, 'final_ok': bool(item.get('ok'))}


def collect(policy):
    CACHE.mkdir(parents=True, exist_ok=True, mode=0o700); CACHE.chmod(0o700)
    old = car_policy.read_json(CACHE / 'status.json')
    now = car_policy.uptime(); interface = car_policy.find_lte_interface(policy); device = usb_device(interface)
    token = (str(device) + ':' + str(read(device / 'devnum'))) if device else None
    if token != old.get('_device_token'):
        old = {}
        for name in ('identity.json', 'supported.json'):
            (CACHE / name).unlink(missing_ok=True)
    first_seen = old.get('_first_seen', now)
    connected_ms = number(read(device / 'power/connected_duration')) if device else None
    ready = device is not None and ((connected_ms is not None and connected_ms >= 20000) or now - first_seen >= 20)
    responses = old.get('diagnostics', {}) if device else {}
    warning = None; sampled = old.get('_at_uptime')
    if ready:
        live = at('live')
        if live.get('ok'):
            responses.update(live.get('responses', {})); sampled = now
        else:
            warning = live.get('error', 'AT interface unavailable')
        if now - old.get('_device_uptime', -100000) >= 300:
            static = at('device')
            if static.get('ok'):
                responses.update(static.get('responses', {})); old['_device_uptime'] = now
        if not (CACHE / 'supported.json').exists():
            car_policy.write_json(CACHE / 'supported.json', command_list(at('supported')))
    info = parse(responses)
    net = network(interface, policy, old.get('network', {}))
    meter = car_policy.update_meter(policy)
    history = old.get('history', [])
    if sampled == now:
        history.append({'uptime': now, 'rssi_dbm': info['signal'].get('rssi_dbm'),
                        'rsrp_dbm': info['signal'].get('rsrp', {}).get('value')})
    history = [point for point in history if now - point['uptime'] <= 3600][-150:]
    result = {'ok': True, 'sample_uptime': now, 'usb': hardware(device), 'network': net,
              'modem': info, 'diagnostics': responses, 'history': history, 'at_warning': warning,
              'starting': device is not None and not ready,
              'usage': {'bytes': meter.get('bytes') if not meter.get('storage_unavailable') else None,
                        'month': meter.get('month') if car_policy.ntp_synced(policy) else None,
                        'soft_cap_bytes': policy['soft_cap_bytes'], 'hard_cap_bytes': policy['hard_cap_bytes'],
                        'cap_mode': car_policy.cap_mode(policy, meter) if not meter.get('storage_unavailable') else 'unavailable',
                        'storage_available': not meter.get('storage_unavailable', False)},
              'supported_command_count': len(car_policy.read_json(CACHE / 'supported.json').get('commands', [])),
              '_device_token': token, '_first_seen': first_seen, '_at_uptime': sampled,
              '_device_uptime': old.get('_device_uptime', -100000)}
    car_policy.write_json(CACHE / 'status.json', result)
    return result


def status():
    data = car_policy.read_json(CACHE / 'status.json')
    if not data:
        return {'ok': True, 'starting': True, 'age_seconds': None}
    now = car_policy.uptime()
    data['age_seconds'] = max(0, round(now - data['sample_uptime']))
    data['at_age_seconds'] = max(0, round(now - data['_at_uptime'])) if data.get('_at_uptime') is not None else None
    data['device_age_seconds'] = max(0, round(now - data['_device_uptime'])) if data.get('_device_uptime', -1) >= 0 else None
    return {k: v for k, v in data.items() if not k.startswith('_')}


def identities():
    responses = at('identity').get('responses', {})
    result = {}
    for key, size in [('imei', (14, 17)), ('imsi', (14, 16)), ('iccid', (18, 22))]:
        text = '\n'.join(lines(responses, key))
        found = re.findall(r'(?<!\d)\d{' + str(size[0]) + ',' + str(size[1]) + r'}(?!\d)', text)
        result[key] = found[-1] if found else None
    if not result['iccid']:
        fields = row(responses, 'iccid_file', '+CRSM:')
        if len(fields) >= 3 and fields[:2] == ['144', '0'] and re.fullmatch('[0-9A-Fa-f]{20}', fields[2]):
            raw = fields[2]; decoded = ''.join(raw[i + 1] + raw[i] for i in range(0, len(raw), 2)).rstrip('Ff')
            result['iccid'] = decoded if re.fullmatch(r'\d{18,22}', decoded) else None
    phones = [s for s in lines(responses, 'phone') if s.startswith('+CNUM:')]
    result['phone'] = '\n'.join(phones) or None
    device = usb_device()
    result['usb_serial'] = read(device / 'serial') if device else None
    result.update({'ok': True, 'read_uptime': car_policy.uptime()})
    return result


def main():
    if sys.argv[1:] == ['status']:
        print(json.dumps(status()))
    elif sys.argv[1:] == ['identity']:
        print(json.dumps(identities()))
    elif sys.argv[1:] == ['supported']:
        print(json.dumps({'ok': True, **car_policy.read_json(CACHE / 'supported.json')}))
    elif sys.argv[1:] == ['collect']:
        with car_policy.file_lock('/run/lock/car-lte-collect.lock'):
            collect(car_policy.load_policy('/etc/teslausb-car-link.ini'))
        print('4G telemetry updated')
    else:
        raise SystemExit('Choose status, identity, supported, or collect')


if __name__ == '__main__':
    try:
        main()
    except Exception:
        print(json.dumps({'ok': False, 'error': '4G information is temporarily unavailable'}))
        sys.exit(1)
