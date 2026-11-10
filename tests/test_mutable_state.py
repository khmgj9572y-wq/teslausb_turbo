import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import mutable_state


class LocalStateTests(unittest.TestCase):
    def test_kernel_query_is_read_only_and_rejects_unsynchronized_state(self):
        for state, expected in [(0, True), (4, True), (5, False), (-1, False)]:
            modes = []
            def query(buffer):
                modes.append(bytes(buffer)[:4])
                return state
            with patch.object(mutable_state.ctypes, 'CDLL', return_value=SimpleNamespace(adjtimex=query)):
                self.assertEqual(mutable_state.ntp_synchronized(), expected)
            self.assertEqual(modes, [b'\0' * 4])

    def test_pairing_state_is_seeded_once_and_existing_state_is_not_overwritten(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); source = root / 'root-copy'; source.mkdir()
            (source / 'pairing-state').write_text('old')
            dest = root / 'ssd-copy'
            self.assertTrue(mutable_state.seed(source, dest, os.getuid(), os.getgid(), 0o700))
            (dest / 'pairing-state').write_text('current')
            self.assertFalse(mutable_state.seed(source, dest, os.getuid(), os.getgid(), 0o700))
            self.assertEqual((dest / 'pairing-state').read_text(), 'current')
            self.assertEqual(dest.stat().st_mode & 0o777, 0o700)

    def test_clock_seed_preserves_timestamp_and_does_not_replace_a_newer_copy(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); source = root / 'old'; source.mkdir()
            (source / 'clock').touch(); os.utime(source / 'clock', (100, 100))
            dest = root / 'ssd'
            mutable_state.seed(source, dest, os.getuid(), os.getgid(), 0o755, clock_only=True)
            self.assertEqual((dest / 'clock').stat().st_mtime, 100)
            os.utime(dest / 'clock', (200, 200))
            mutable_state.seed(source, dest, os.getuid(), os.getgid(), 0o755, clock_only=True)
            self.assertEqual((dest / 'clock').stat().st_mtime, 200)

    def test_unsynchronized_or_unmounted_clock_is_never_saved(self):
        with patch.object(mutable_state.os.path, 'ismount', return_value=False), \
             patch.object(mutable_state, 'ntp_synchronized') as run:
            self.assertFalse(mutable_state.save_clock())
            run.assert_not_called()
        with patch.object(mutable_state.os.path, 'ismount', return_value=True), \
             patch.object(mutable_state, 'ntp_synchronized', return_value=False), \
             patch.object(mutable_state.os, 'open') as opened:
            self.assertFalse(mutable_state.save_clock())
            opened.assert_not_called()

    def test_synchronized_save_updates_timestamp_without_changing_contents(self):
        with tempfile.TemporaryDirectory() as temp:
            clock = Path(temp) / 'clock'; clock.write_text(''); os.utime(clock, (100, 100))
            account = SimpleNamespace(pw_uid=os.getuid(), pw_gid=os.getgid())
            with patch.object(mutable_state.os.path, 'ismount', return_value=True), \
                 patch.object(mutable_state, 'ntp_synchronized', return_value=True), \
                 patch.object(mutable_state.pwd, 'getpwnam', return_value=account):
                self.assertTrue(mutable_state.save_clock(Path(temp)))
            self.assertGreater(clock.stat().st_mtime, 100)
            self.assertEqual(clock.read_text(), '')
