import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import ssh_keepawake


class SessionActivityTests(unittest.TestCase):
    def test_idle_session_stops_counting_after_grace_period(self):
        props = {'TimestampMonotonic': '100000000'}
        with patch.object(ssh_keepawake, 'cpu_seconds', return_value=0), \
             patch.object(ssh_keepawake, 'tty_age', return_value=2000):
            self.assertEqual(ssh_keepawake.session_activity(props, [], 150), [])
            self.assertEqual(ssh_keepawake.session_activity(props, [], 200), ['new'])
            self.assertEqual(ssh_keepawake.session_activity(props, [], 1001), [])

    def test_cpu_job_keeps_old_session_active_without_terminal(self):
        with patch.object(ssh_keepawake, 'cpu_seconds', return_value=4), \
             patch.object(ssh_keepawake, 'tty_age', return_value=None):
            reasons = ssh_keepawake.session_activity(
                {'TimestampMonotonic': '1'}, [(1900, 1)], 2000)
            self.assertEqual(reasons, ['cpu'])

    def test_old_cpu_work_and_future_terminal_timestamp_do_not_hold_awake(self):
        history = [(100, 0), (1800, 4)]
        with patch.object(ssh_keepawake, 'cpu_seconds', return_value=4), \
             patch.object(ssh_keepawake, 'tty_age', return_value=-100):
            reasons = ssh_keepawake.session_activity({'TimestampMonotonic': '1'}, history, 2000)
            self.assertEqual(reasons, [])
            self.assertEqual(history[0], (1800, 4))
