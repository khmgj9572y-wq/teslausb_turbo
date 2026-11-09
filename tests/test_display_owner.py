import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import car_control
import car_policy
import display_owner
import airplay_receiver


class DisplayOwnerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        for module, attr, value in [
            (display_owner, 'STATE', root / 'owner.json'),
            (display_owner, 'LOCK', root / 'lock'),
            (car_control, 'VIDEO_START', root / 'video-start.json')]:
            p = patch.object(module, attr, value)
            p.start(); self.addCleanup(p.stop)
        self.boot = patch.object(display_owner, 'boot_id', return_value='b')
        self.boot.start(); self.addCleanup(self.boot.stop)
        self.clock = patch.object(car_policy, 'uptime', return_value=200)
        self.clock.start(); self.addCleanup(self.clock.stop)
        self.policy = {'video_service': 'car-video.service', 'max_session_seconds': 1800}
        self.started = {'boot': 'b', 'uptime': 100}
        car_policy.write_json(car_control.VIDEO_START, self.started)

    def test_takeover_and_resume_preserve_original_session_deadline(self):
        with patch.object(car_control, 'video_active', return_value=True), \
             patch.object(car_control, 'systemctl', return_value=True) as ctl, \
             patch.object(car_control, 'video_allowed', return_value=(True, 'allowed')):
            display_owner.claim(self.policy, 'a' * 32)
            self.assertTrue(display_owner.busy())
            self.assertEqual(display_owner.release(self.policy, 'a' * 32), 'Moonlight resumed')
            self.assertFalse(display_owner.busy())
            self.assertEqual(car_policy.read_json(car_control.VIDEO_START), self.started)
            self.assertEqual(ctl.call_args_list[0].args, ('stop', 'car-video.service'))
            self.assertEqual(ctl.call_args_list[1].args, ('start', 'car-video.service'))

    def test_manual_stop_during_airplay_cancels_resume(self):
        with patch.object(car_control, 'video_active', return_value=True), \
             patch.object(car_control, 'systemctl', return_value=True) as ctl:
            display_owner.claim(self.policy, 'a' * 32)
            display_owner.stop_video(self.policy)
            display_owner.release(self.policy, 'a' * 32)
            self.assertFalse(any(c.args[0] == 'start' for c in ctl.call_args_list))

    def test_expired_session_is_not_restarted_after_airplay(self):
        with patch.object(car_control, 'video_active', return_value=True), \
             patch.object(car_control, 'systemctl', return_value=True) as ctl:
            display_owner.claim(self.policy, 'a' * 32)
            with patch.object(car_policy, 'uptime', return_value=2001):
                display_owner.release(self.policy, 'a' * 32)
            self.assertFalse(any(c.args[0] == 'start' for c in ctl.call_args_list))

    def test_old_receiver_cannot_release_another_owner(self):
        with patch.object(car_control, 'video_active', return_value=False), \
             patch.object(car_control, 'systemctl', return_value=True):
            display_owner.claim(self.policy, 'a' * 32)
            display_owner.release(self.policy, 'b' * 32)
            self.assertTrue(display_owner.busy())

    def test_connection_events_do_not_depend_on_client_identifiers(self):
        self.assertEqual(airplay_receiver.event('raop_rtp_mirror starting mirroring'), 'claim')
        self.assertEqual(airplay_receiver.event('raop_rtp_mirror->running is no longer true'), 'release')
        self.assertIsNone(airplay_receiver.event('connection request from a client'))
