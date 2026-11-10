import sys
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import car_control
import car_policy
import display_owner
import airplay_receiver
import video_toggle


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

    def test_disabled_hard_limit_allows_prior_session_to_resume(self):
        self.policy['max_session_seconds'] = 0
        with patch.object(car_control, 'video_active', return_value=True), \
             patch.object(car_control, 'systemctl', return_value=True) as ctl, \
             patch.object(car_control, 'video_allowed', return_value=(True, 'allowed')):
            display_owner.claim(self.policy, 'a' * 32)
            with patch.object(car_policy, 'uptime', return_value=7200):
                self.assertEqual(display_owner.release(self.policy, 'a' * 32), 'Moonlight resumed')
            self.assertTrue(any(c.args[0] == 'start' for c in ctl.call_args_list))

    def test_takeover_waits_for_an_in_progress_moonlight_start(self):
        checking = threading.Event()
        continue_start = threading.Event()
        stop_called = threading.Event()
        def command(action, service):
            if action == 'stop':
                stop_called.set()
            return True
        def check_policy(policy):
            checking.set()
            if not continue_start.wait(2):
                raise RuntimeError('Test start was not released')
            return True, 'allowed'
        with patch.object(car_control, 'video_active', return_value=False), \
             patch.object(car_control, 'video_allowed', side_effect=check_policy), \
             patch.object(car_control, 'systemctl', side_effect=command) as ctl, \
             ThreadPoolExecutor(max_workers=2) as pool:
            start = pool.submit(display_owner.start_video, self.policy)
            self.assertTrue(checking.wait(2))
            claim_started = threading.Event()
            def take_screen():
                claim_started.set()
                return display_owner.claim(self.policy, 'a' * 32)
            takeover = pool.submit(take_screen)
            try:
                self.assertTrue(claim_started.wait(2))
                self.assertFalse(stop_called.wait(0.1))
                self.assertFalse(takeover.done())
                ctl.assert_not_called()
            finally:
                continue_start.set()
            self.assertEqual(start.result(timeout=2), (True, 'on'))
            takeover.result(timeout=2)
            self.assertEqual([c.args[0] for c in ctl.call_args_list], ['start', 'stop'])
            self.assertTrue(display_owner.busy())

    def test_moonlight_start_is_refused_while_airplay_owns_screen(self):
        with patch.object(car_control, 'video_active', return_value=False), \
             patch.object(car_control, 'systemctl', return_value=True) as ctl, \
             patch.object(car_control, 'video_allowed') as check:
            display_owner.claim(self.policy, 'a' * 32)
            ctl.reset_mock()
            allowed, reason = display_owner.start_video(self.policy)
            self.assertFalse(allowed)
            self.assertIn('AirPlay', reason)
            ctl.assert_not_called()
            check.assert_not_called()

    def test_gpio_press_during_airplay_cancels_moonlight_resume(self):
        with patch.object(car_control, 'video_active', return_value=True), \
             patch.object(car_control, 'systemctl', return_value=True) as ctl:
            display_owner.claim(self.policy, 'a' * 32)
            self.assertEqual(video_toggle.toggle(self.policy), 'off')
            display_owner.release(self.policy, 'a' * 32)
            self.assertFalse(any(c.args[0] == 'start' for c in ctl.call_args_list))

    def test_connection_events_do_not_depend_on_client_identifiers(self):
        self.assertEqual(airplay_receiver.event('raop_rtp_mirror starting mirroring'), 'claim')
        self.assertEqual(airplay_receiver.event('raop_rtp_mirror->running is no longer true'), 'release')
        self.assertIsNone(airplay_receiver.event('connection request from a client'))
