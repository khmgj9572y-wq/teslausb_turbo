import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import car_policy


class SessionConfigTests(unittest.TestCase):
    def test_zero_or_missing_limit_is_allowed_but_negative_limit_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'config.ini'
            path.write_text('[policy]\n')
            self.assertEqual(car_policy.load_policy(str(path))['max_session_seconds'], 0)
            path.write_text('[policy]\nmax_session_min = 0\n')
            self.assertEqual(car_policy.load_policy(str(path))['max_session_seconds'], 0)
            path.write_text('[policy]\nmax_session_min = -1\n')
            with self.assertRaisesRegex(ValueError, 'nonnegative'):
                car_policy.load_policy(str(path))
