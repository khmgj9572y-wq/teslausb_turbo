#!/usr/bin/env python3
"""Read-only 4G bridge, protected by the existing TeslaUSB access policy."""
import json
import os
import subprocess
import sys
from urllib.parse import parse_qs, urlparse

HELPER = '/usr/local/lib/teslausb-car-link/lte_info.py'


def respond(status, data):
    body = json.dumps(data, separators=(',', ':')).encode()
    sys.stdout.write('Status: ' + status + '\r\nContent-Type: application/json; charset=utf-8\r\n'
                     'Cache-Control: no-store\r\nX-Content-Type-Options: nosniff\r\n'
                     'Referrer-Policy: same-origin\r\nContent-Length: ' + str(len(body)) + '\r\n\r\n')
    sys.stdout.flush(); sys.stdout.buffer.write(body)


def main():
    action = parse_qs(os.environ.get('QUERY_STRING', '')).get('action', [''])[0]
    method = os.environ.get('REQUEST_METHOD', '')
    if action == 'status' and method == 'GET':
        pass
    elif action in ('identity', 'supported') and method == 'POST':
        origin = os.environ.get('HTTP_ORIGIN')
        parsed = urlparse(origin) if origin else None
        if (os.environ.get('HTTP_X_CAR_LTE_ACTION') != '1' or
                not os.environ.get('CONTENT_TYPE', '').startswith('application/json') or
                os.environ.get('HTTP_SEC_FETCH_SITE', 'same-origin') not in ('same-origin', 'none') or
                (parsed and (parsed.scheme not in ('http', 'https') or parsed.netloc != os.environ.get('HTTP_HOST') or
                             parsed.path or parsed.query or parsed.fragment))):
            respond('403 Forbidden', {'ok': False, 'error': 'Request not allowed'}); return
        try:
            length = int(os.environ.get('CONTENT_LENGTH', '-1'))
            if not 0 <= length <= 64 or json.loads(sys.stdin.buffer.read(length)) != {}:
                raise ValueError()
        except (ValueError, TypeError):
            respond('400 Bad Request', {'ok': False, 'error': 'No parameters are accepted'}); return
    else:
        respond('405 Method Not Allowed', {'ok': False, 'error': 'Unknown action'}); return
    try:
        result = subprocess.run(['sudo', '-n', HELPER, action], capture_output=True, timeout=45)
        data = json.loads(result.stdout)
        if not isinstance(data, dict) or 'ok' not in data:
            raise ValueError()
        respond('200 OK' if result.returncode == 0 else '503 Service Unavailable', data)
    except (OSError, ValueError, subprocess.TimeoutExpired):
        respond('503 Service Unavailable', {'ok': False, 'error': '4G information is temporarily unavailable'})


if __name__ == '__main__':
    main()
