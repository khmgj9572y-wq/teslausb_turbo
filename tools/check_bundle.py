#!/usr/bin/env python3
"""Check the shareable source bundle locally, without contacting the Pi."""

import argparse
import ast
import ipaddress
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
IGNORED = {'.git', '__pycache__'}
PRIVATE_SUFFIXES = {'.key', '.pem', '.token', '.log', '.sqlite3', '.img', '.xqcn', '.qcow2'}
DOCUMENTATION_NETS = tuple(ipaddress.ip_network(value) for value in (
    '192.0.2.0/24', '198.51.100.0/24', '203.0.113.0/24'))
IPV4 = re.compile(r'(?<![\w.])(?:[0-9]{1,3}\.){3}[0-9]{1,3}(?![\w.])')
VIN = re.compile(r'(?<![A-Za-z0-9])[A-HJ-NPR-Z0-9]{8}[0-9X][A-HJ-NPR-Z0-9]{8}(?![A-Za-z0-9])')
PRIVATE_KEY = re.compile(r'-----BEGIN [A-Z ]*PRIVATE KEY-----')
PUBLIC_KEY = re.compile(r'(?<![A-Za-z0-9-])(?:ssh-ed25519|ssh-rsa|ecdsa-sha2-\S+)\s+[A-Za-z0-9+/]{30,}={0,3}')
WIREGUARD_KEY = re.compile(r'(?i)\b(?:PrivateKey|PublicKey|PresharedKey)\s*=\s*[\'"]?[A-Za-z0-9+/]{43}=')
MAC_ADDRESS = re.compile(r'(?i)(?<![0-9a-f:])(?:[0-9a-f]{2}:){5}[0-9a-f]{2}(?![0-9a-f:])')


def privacy_issues(text, terms=()):
    """Report locations and categories only; never repeat a matched secret."""
    issues = []
    for number, line in enumerate(text.splitlines(), 1):
        if PRIVATE_KEY.search(line):
            issues.append((number, 'private key block'))
        if PUBLIC_KEY.search(line):
            issues.append((number, 'actual SSH public key'))
        if WIREGUARD_KEY.search(line):
            issues.append((number, 'actual tunnel key'))
        for match in MAC_ADDRESS.finditer(line):
            value = match.group().lower()
            if value not in ('00:00:00:00:00:00', 'ff:ff:ff:ff:ff:ff') and not int(value[:2], 16) & 2:
                issues.append((number, 'non-placeholder controller address'))
        if VIN.search(line):
            issues.append((number, 'possible vehicle identifier'))
        if any(term.casefold() in line.casefold() for term in terms):
            issues.append((number, 'private local term'))
        for match in IPV4.finditer(line):
            try:
                address = ipaddress.ip_address(match.group())
            except ValueError:
                continue
            # Loopback, example networks, the generic hotspot default, and a
            # subnet mask are source constants. Other addresses need review.
            if (address.is_loopback or address.is_unspecified or
                    str(address) in ('10.42.0.1', '255.255.255.0') or
                    any(address in network for network in DOCUMENTATION_NETS)):
                continue
            issues.append((number, 'non-placeholder network address'))
    return issues


def files(root):
    for path in sorted(root.rglob('*')):
        if any(part in IGNORED for part in path.relative_to(root).parts):
            continue
        if path.is_file():
            yield path


def check(root, terms=()):
    errors = []
    count = 0
    for path in files(root):
        relative = path.relative_to(root)
        count += 1
        if path.is_symlink() or path.suffix in PRIVATE_SUFFIXES:
            errors.append(f'{relative}: private artifact or symlink in source bundle')
            continue
        try:
            source = path.read_text(encoding='utf-8')
        except (UnicodeError, OSError):
            errors.append(f'{relative}: unreadable or non-text artifact')
            continue
        errors.extend(f'{relative}:{line}: {reason}' for line, reason in privacy_issues(source, terms))
        first = source.splitlines()[0] if source else ''
        if path.suffix == '.py' or (path.suffix == '.cgi' and 'python' in first):
            try:
                ast.parse(source, filename=str(relative))
            except SyntaxError as error:
                errors.append(f'{relative}:{error.lineno}: invalid Python syntax')
        elif path.suffix == '.sh':
            result = subprocess.run(['bash', '-n', str(path)], capture_output=True, timeout=10)
            if result.returncode:
                errors.append(f'{relative}: invalid shell syntax')
    return count, errors


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--private-terms-file', type=Path,
                        help='Optional external file: one hostname, SSID, or other private term per line')
    parser.add_argument('--tests', action='store_true', help='Also run the offline unit tests')
    args = parser.parse_args()
    terms = []
    if args.private_terms_file:
        path = args.private_terms_file.resolve()
        if path == ROOT or ROOT in path.parents:
            parser.error('Private terms must be kept outside the source bundle')
        terms = [line.strip() for line in path.read_text().splitlines() if line.strip()]
    count, errors = check(ROOT, terms)
    if errors:
        for error in errors:
            print(error, file=sys.stderr)
        return 1
    print(f'Checked {count} source files: syntax and privacy checks passed', flush=True)
    print('Privacy checks are a guardrail; review the diff before publishing.', flush=True)
    if args.tests:
        return subprocess.run([sys.executable, '-B', '-m', 'unittest', 'discover',
                               '-s', 'tests', '-q'], cwd=ROOT).returncode
    return 0


if __name__ == '__main__':
    sys.exit(main())
