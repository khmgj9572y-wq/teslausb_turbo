import fcntl
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(shutil.which('flock'), 'Linux flock is required; run these on the Pi')
class LockTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        for name in ('tools', 'examples', 'bin', 'mockbin', 'etc/tmpfiles.d', 'run/lock', 'sys/class/bluetooth'):
            (self.root / name).mkdir(parents=True)
        self.binary = self.root / 'bin/tesla-control'
        self.lock = self.root / 'run/lock/hci0.lock'
        for name in ('install_hci0_lock.sh', 'tesla-control-hci0-lock.sh'):
            source = (ROOT / 'tools' / name).read_text()
            for old, new in [('/root/bin/tesla-control', str(self.binary)),
                             ('/root/tesla-control-lock-backup', str(self.root / 'backup')),
                             ('/etc/tmpfiles.d', str(self.root / 'etc/tmpfiles.d')),
                             ('/run/lock/hci0.lock', str(self.lock)),
                             ('/sys/class/bluetooth', str(self.root / 'sys/class/bluetooth'))]:
                source = source.replace(old, new)
            self.assertNotIn('/root/bin/', source)
            self.assertNotIn('/root/tesla-control-lock-backup', source)
            self.assertNotRegex(source, r'(?<![A-Za-z0-9_/.-])/etc/tmpfiles\.d')
            path = self.root / 'tools' / name
            path.write_text(source); path.chmod(0o755)
        shutil.copyfile(ROOT / 'examples/hci0-lock.tmpfiles.conf',
                        self.root / 'examples/hci0-lock.tmpfiles.conf')
        mocks = {
            'id': '#!/bin/sh\necho 0\n',
            'getent': '#!/bin/sh\nexit 0\n',
            'sync': '#!/bin/sh\nexit 0\n',
            'logger': '#!/bin/sh\nprintf "%s\\n" "$*" >> "$LOGGER_LOG"\n',
            'mount': '#!/bin/sh\ncase "$*" in *remount,ro*) [ "${FAIL_RO:-0}" != 1 ];; *) exit 0;; esac\n',
            'systemd-tmpfiles': '#!/bin/sh\ntouch "$LOCK_PATH"\nchmod 660 "$LOCK_PATH"\n',
            'install': '#!/usr/bin/env python3\nimport os,shutil,sys\na=sys.argv[1:]; mode=0o755\n'
                       'while a[0].startswith("-"):\n flag,value=a[:2];a=a[2:]\n'
                       ' if flag=="-m": mode=int(value,8)\n'
                       'shutil.copyfile(a[0],a[1]);os.chmod(a[1],mode)\n',
        }
        for name, source in mocks.items():
            p = self.root / 'mockbin' / name
            p.write_text(source); p.chmod(0o755)
        self.env = dict(os.environ, PATH=str(self.root / 'mockbin') + os.pathsep + os.environ['PATH'],
                        LOCK_PATH=str(self.lock), TESLA_CONTROL_LOCK_WAIT='0.1',
                        LOGGER_LOG=str(self.root / 'logger.log'))

    def install(self, **env):
        return subprocess.run(['bash', str(self.root / 'tools/install_hci0_lock.sh')],
                              env=dict(self.env, **env), capture_output=True, text=True, timeout=5)

    def test_reinstall_wraps_current_binary_and_preserves_previous_real(self):
        self.binary.write_bytes(b'\x7fELFnew-tool')
        real = Path(str(self.binary) + '.real')
        real.write_bytes(b'\x7fELFold-tool')
        result = self.install()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(real.read_bytes(), b'\x7fELFnew-tool')
        backups = list(self.root.glob('backup.*/tesla-control.real'))
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_bytes(), b'\x7fELFold-tool')
        self.assertIn('tesla-control wrapper', self.binary.read_text())
        self.assertEqual(self.install().returncode, 0)

    def test_readonly_restore_failure_fails_install(self):
        self.binary.write_bytes(b'\x7fELFtool')
        result = self.install(FAIL_RO='1')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('root remains writable', result.stderr)

    def test_busy_lock_does_not_run_binary_and_unlock_preserves_arguments(self):
        real = Path(str(self.binary) + '.real')
        real.write_text('#!/bin/sh\nprintf "%s\\n" "$@" > "$ARGS_LOG"\n')
        real.chmod(0o755)
        wrapper = self.root / 'tools/tesla-control-hci0-lock.sh'
        args = ['-ble', 'value with spaces']
        record = self.root / 'args'
        env = dict(self.env, ARGS_LOG=str(record))
        with self.lock.open('a') as handle:
            fcntl.flock(handle, fcntl.LOCK_EX)
            blocked = subprocess.run([str(wrapper), *args], env=env, capture_output=True, timeout=3)
            self.assertEqual(blocked.returncode, 75)
            self.assertFalse(record.exists())
        free = subprocess.run([str(wrapper), *args], env=env, capture_output=True, timeout=3)
        self.assertEqual(free.returncode, 0)
        self.assertEqual(record.read_text().splitlines(), args)

    def test_uart_is_selected_instead_of_usb_even_when_numbering_changes(self):
        for name, bus in [('hci0', 'usb1'), ('hci4', 'serial0')]:
            device = self.root / 'devices' / bus / 'bluetooth' / name
            device.mkdir(parents=True)
            (self.root / 'sys/class/bluetooth' / name).symlink_to(device)
        real = Path(str(self.binary) + '.real')
        real.write_text('#!/bin/sh\nprintf "%s\\n" "$@" > "$ARGS_LOG"\n'); real.chmod(0o755)
        record = self.root / 'args'
        env = dict(self.env, ARGS_LOG=str(record))
        wrapper = self.root / 'tools/tesla-control-hci0-lock.sh'
        subprocess.run([str(wrapper), '-ble', 'ping'], env=env, check=True, timeout=3)
        self.assertEqual(record.read_text().splitlines(), ['-bt-adapter', 'hci4', '-ble', 'ping'])
        for flags in [['-bt-adapter', 'hci7'], ['--bt-adapter=hci7']]:
            subprocess.run([str(wrapper), *flags, 'ping'], env=env, check=True, timeout=3)
            self.assertEqual(record.read_text().splitlines(), flags + ['ping'])

    def test_no_uart_logs_and_preserves_the_bare_fallback(self):
        real = Path(str(self.binary) + '.real')
        real.write_text('#!/bin/sh\nprintf "%s\\n" "$@" > "$ARGS_LOG"\n'); real.chmod(0o755)
        record = self.root / 'args'
        subprocess.run([str(self.root / 'tools/tesla-control-hci0-lock.sh'), 'ping'],
                       env=dict(self.env, ARGS_LOG=str(record)), check=True, timeout=3)
        self.assertEqual(record.read_text().splitlines(), ['ping'])
        self.assertIn('no onboard UART', (self.root / 'logger.log').read_text())
