import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import nm_storage


class ProfileStorageTests(unittest.TestCase):
    def test_save_failure_restores_networkmanager_namespace(self):
        calls = []
        def run(args):
            calls.append(args)
            return SimpleNamespace(stdout='42\n' if args[0] == 'systemctl' else 'ro,noatime\n')
        with patch.object(nm_storage.os, 'statvfs', return_value=SimpleNamespace(f_flag=0)), \
             patch.object(nm_storage, 'run', side_effect=run):
            with self.assertRaisesRegex(RuntimeError, 'save failed'):
                with nm_storage.profile_writes():
                    raise RuntimeError('save failed')
        mounts = [args for args in calls if 'mount' in args]
        self.assertEqual(mounts[0][-2:], ['remount,bind,rw', '/'])
        self.assertEqual(mounts[1][-2:], ['remount,bind,ro', '/'])
        self.assertFalse(any('restart' in args for args in calls))

    def test_already_writable_namespace_needs_no_remount(self):
        calls = []
        def run(args):
            calls.append(args)
            return SimpleNamespace(stdout='42\n' if args[0] == 'systemctl' else 'rw,noatime\n')
        with patch.object(nm_storage.os, 'statvfs', return_value=SimpleNamespace(f_flag=0)), \
             patch.object(nm_storage, 'run', side_effect=run):
            with nm_storage.profile_writes():
                pass
        self.assertFalse(any('mount' in args for args in calls))

    def test_readonly_host_is_rejected_before_touching_networkmanager(self):
        with patch.object(nm_storage.os, 'statvfs', return_value=SimpleNamespace(f_flag=nm_storage.os.ST_RDONLY)), \
             patch.object(nm_storage, 'run') as run:
            with self.assertRaisesRegex(RuntimeError, 'temporarily writable'):
                with nm_storage.profile_writes():
                    pass
        run.assert_not_called()
