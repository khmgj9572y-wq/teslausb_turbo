import sys
import unittest
import tempfile
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import video_web


class VideoWebTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        for attribute, name in [('STATE', 'owner.json'), ('LOCK', 'display.lock')]:
            patcher = patch.object(video_web.display_owner, attribute, Path(self.temp.name) / name)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.policy = {"video_service": "car-video.service",
                       "max_session_seconds": 1800}

    def test_start_requires_parked_confirmation(self):
        with patch.object(video_web.car_control, "systemctl") as command:
            with self.assertRaisesRegex(ValueError, "parked"):
                video_web.act(self.policy, "video-start", {"parked": False})
            command.assert_not_called()

    def test_start_respects_existing_video_policy(self):
        with patch.object(video_web.car_control, "video_allowed",
                          return_value=(False, "LTE data cap")), \
             patch.object(video_web.car_control, "systemctl") as command:
            with self.assertRaisesRegex(RuntimeError, "LTE data cap"):
                video_web.act(self.policy, "video-start", {"parked": True})
            command.assert_not_called()

    def test_manual_start_uses_service_without_enabling_remote_on(self):
        with patch.object(video_web.car_control, "video_allowed", return_value=(True, "allowed")), \
             patch.object(video_web.car_control, "video_active", return_value=False), \
             patch.object(video_web.car_control, "systemctl", return_value=True) as command, \
             patch.object(video_web, "status", return_value={"ok": True, "active": True}):
            self.assertTrue(video_web.act(self.policy, "video-start", {"parked": True})["active"])
            command.assert_called_once_with("start", "car-video.service")

    def test_already_running_session_does_not_restart(self):
        with patch.object(video_web.car_control, "video_allowed", return_value=(True, "allowed")), \
             patch.object(video_web.car_control, "video_active", return_value=True), \
             patch.object(video_web.car_control, "systemctl") as command, \
             patch.object(video_web, "status", return_value={"ok": True, "active": True}):
            video_web.act(self.policy, "video-start", {"parked": True})
            command.assert_not_called()

    def test_stop_needs_no_parked_confirmation(self):
        with patch.object(video_web.car_control, "systemctl", return_value=True) as command, \
             patch.object(video_web, "status", return_value={"ok": True, "active": False}):
            self.assertFalse(video_web.act(self.policy, "video-stop", {})["active"])
            command.assert_called_once_with("stop", "car-video.service")

    def test_status_reports_remaining_session_time(self):
        with patch.object(video_web.car_control, "video_active", return_value=True), \
             patch.object(video_web.car_policy, "read_json", return_value={"boot": "a", "uptime": 100}), \
             patch.object(video_web.car_control, "boot_id", return_value="a"), \
             patch.object(video_web.car_policy, "uptime", return_value=400):
            self.assertEqual(video_web.status(self.policy)["remaining_seconds"], 1500)

    def test_unlimited_session_reports_no_countdown(self):
        self.policy['max_session_seconds'] = 0
        with patch.object(video_web.car_control, 'video_active', return_value=True):
            state = video_web.status(self.policy)
        self.assertIsNone(state['remaining_seconds'])
        self.assertIsNone(state['max_session_minutes'])
