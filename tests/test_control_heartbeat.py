import importlib.util
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import car_control
import car_policy
import heartbeat


class ControlTests(unittest.TestCase):
    def test_missing_ssd_blocks_video(self):
        config = {"soft_cap_bytes": 40, "hard_cap_bytes": 60}
        self.assertEqual(car_control.video_allowed(
            config, {"storage_unavailable": True})[1], "queue SSD unavailable")

    def test_remote_video_on_is_disabled_until_park_gate_is_ready(self):
        with mock.patch.object(car_control, "systemctl") as systemctl:
            result = car_control.apply_video_command(
                {"remote_video_on_enabled": False}, "on")
            self.assertEqual(result, "blocked: remote video on disabled")
            systemctl.assert_not_called()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.config = {
            "soft_cap_bytes": 40, "hard_cap_bytes": 60,
            "sunshine_wg_ip": "192.0.2.20", "intake_wg_ip": "192.0.2.10",
            "vpn_interface": "wg0", "video_service": "car-video.service",
            "max_session_seconds": 1800, "max_handshake_age_seconds": 180,
            "power_log": root / "power.log",
        }

    def test_video_is_blocked_at_soft_cap(self):
        with mock.patch.object(car_policy, "current_throttle_flags", return_value=0), \
             mock.patch.object(car_policy, "active_link", return_value="usb1"), \
             mock.patch.object(car_control, "wg_route", return_value=True):
            allowed, reason = car_control.video_allowed(self.config, {"bytes": 40})
        self.assertFalse(allowed)
        self.assertEqual(reason, "LTE data cap")

    def test_video_is_blocked_on_undervoltage(self):
        with mock.patch.object(car_policy, "current_throttle_flags", return_value=1):
            allowed, reason = car_control.video_allowed(self.config, {"bytes": 0})
        self.assertFalse(allowed)
        self.assertIn("undervoltage", reason)

    def test_session_limit_stops_video(self):
        with mock.patch.object(car_policy, "update_meter", return_value={"bytes": 0}), \
             mock.patch.object(car_policy, "current_throttle_flags", return_value=0), \
             mock.patch.object(car_policy, "active_link", return_value="wlan0"), \
             mock.patch.object(car_policy, "uptime", return_value=2000), \
             mock.patch.object(car_control, "wg_route", return_value=True), \
             mock.patch.object(car_control, "video_active", return_value=True), \
             mock.patch.object(car_control, "boot_id", return_value="boot-a"), \
             mock.patch.object(car_control, "systemctl", return_value=True) as systemctl, \
             mock.patch.object(car_control, "VIDEO_START", Path(self.temp.name) / "start.json"), \
             mock.patch.object(car_control, "HEARTBEAT_STATE", Path(self.temp.name) / "hb.json"), \
             mock.patch.object(car_control, "RUNTIME", Path(self.temp.name)):
            car_policy.write_json(car_control.VIDEO_START, {"boot": "boot-a", "uptime": 100})
            result = car_control.tick(self.config)
        self.assertEqual(result, "video stopped: session limit")
        systemctl.assert_any_call("stop", "car-video.service")

    def test_stale_wireguard_handshake_stops_video(self):
        with mock.patch.object(car_policy, "update_meter", return_value={"bytes": 0}), \
             mock.patch.object(car_policy, "current_throttle_flags", return_value=0), \
             mock.patch.object(car_policy, "active_link", return_value="usb1"), \
             mock.patch.object(car_policy, "uptime", return_value=400), \
             mock.patch.object(car_control, "wg_route", return_value=True), \
             mock.patch.object(car_control, "wg_handshake_recent", return_value=False), \
             mock.patch.object(car_control, "video_active", return_value=True), \
             mock.patch.object(car_control, "boot_id", return_value="boot-a"), \
             mock.patch.object(car_control, "systemctl", return_value=True) as systemctl, \
             mock.patch.object(car_control, "VIDEO_START", Path(self.temp.name) / "start.json"), \
             mock.patch.object(car_control, "HEARTBEAT_STATE", Path(self.temp.name) / "hb.json"), \
             mock.patch.object(car_control, "RUNTIME", Path(self.temp.name)):
            car_policy.write_json(car_control.VIDEO_START, {"boot": "boot-a", "uptime": 100})
            result = car_control.tick(self.config)
        self.assertEqual(result, "video stopped: WireGuard handshake stale")
        systemctl.assert_any_call("stop", "car-video.service")

    def test_disabled_hard_limit_does_not_stop_an_active_long_session(self):
        self.config['max_session_seconds'] = 0
        with mock.patch.object(car_policy, 'update_meter', return_value={'bytes': 0}), \
             mock.patch.object(car_policy, 'current_throttle_flags', return_value=0), \
             mock.patch.object(car_policy, 'active_link', return_value='wifi-uplink'), \
             mock.patch.object(car_policy, 'uptime', return_value=7200), \
             mock.patch.object(car_control, 'wg_route', return_value=True), \
             mock.patch.object(car_control, 'wg_handshake_recent', return_value=True), \
             mock.patch.object(car_control, 'video_active', return_value=True), \
             mock.patch.object(car_control, 'boot_id', return_value='boot-a'), \
             mock.patch.object(car_control, 'systemctl', return_value=True) as ctl, \
             mock.patch.object(car_control, 'VIDEO_START', Path(self.temp.name) / 'start.json'), \
             mock.patch.object(car_control, 'HEARTBEAT_STATE', Path(self.temp.name) / 'hb.json'), \
             mock.patch.object(car_control, 'RUNTIME', Path(self.temp.name)):
            car_policy.write_json(car_control.VIDEO_START, {'boot': 'boot-a', 'uptime': 100})
            car_policy.write_json(car_control.HEARTBEAT_STATE, {'last_success_uptime': 7150})
            self.assertEqual(car_control.tick(self.config), 'video allowed')
        ctl.assert_not_called()


class HeartbeatTests(unittest.TestCase):
    def test_redirect_is_refused(self):
        with self.assertRaisesRegex(RuntimeError, "redirect refused"):
            heartbeat.NoRedirect().redirect_request(
                None, None, 302, "Found", {}, "https://example.invalid/")

    def test_invalid_video_command_cannot_act(self):
        with tempfile.TemporaryDirectory() as temp:
            state_path = Path(temp) / "hb.json"
            response = mock.MagicMock()
            response.status = 200
            response.read.return_value = b'{"video":"delete"}'
            response.__enter__.return_value = response
            policy = {"heartbeat_url": "http://192.0.2.10/hb",
                      "intake_wg_ip": "192.0.2.10", "vpn_interface": "wg0"}
            with mock.patch.object(car_control, "wg_route", return_value=True), \
                 mock.patch.object(heartbeat, "payload", return_value={"uptime_seconds": 1}), \
                 mock.patch.object(heartbeat, "open_heartbeat", return_value=response), \
                 mock.patch.object(car_control, "apply_video_command") as apply, \
                 mock.patch.object(car_control, "HEARTBEAT_STATE", state_path):
                with self.assertRaisesRegex(RuntimeError, "invalid video command"):
                    heartbeat.send_heartbeat(policy, {}, Path(temp), Path(temp))
            apply.assert_not_called()
            self.assertFalse(state_path.exists())

    def test_repeated_on_does_not_restart_expired_session(self):
        with tempfile.TemporaryDirectory() as temp:
            state_path = Path(temp) / "hb.json"
            car_policy.write_json(state_path, {"last_video_command": "on"})
            response = mock.MagicMock()
            response.status = 200
            response.read.return_value = b'{"video":"on"}'
            response.__enter__.return_value = response
            policy = {"heartbeat_url": "http://192.0.2.10/hb",
                      "intake_wg_ip": "192.0.2.10", "vpn_interface": "wg0"}
            with mock.patch.object(car_control, "wg_route", return_value=True), \
                 mock.patch.object(heartbeat, "payload", return_value={"uptime_seconds": 1}), \
                 mock.patch.object(heartbeat, "open_heartbeat", return_value=response), \
                 mock.patch.object(car_policy, "uptime", return_value=1000), \
                 mock.patch.object(car_control, "apply_video_command") as apply, \
                 mock.patch.object(car_control, "HEARTBEAT_STATE", state_path):
                result = heartbeat.send_heartbeat(policy, {}, Path(temp), Path(temp))
            self.assertEqual(result, "on already handled")
            apply.assert_not_called()
            self.assertEqual(car_policy.read_json(state_path)["last_success_uptime"], 1000)

    def test_failed_on_can_be_retried_on_next_heartbeat(self):
        with tempfile.TemporaryDirectory() as temp:
            state_path = Path(temp) / "hb.json"
            response = mock.MagicMock()
            response.status = 200
            response.read.return_value = b'{"video":"on"}'
            response.__enter__.return_value = response
            policy = {"heartbeat_url": "http://192.0.2.10/hb",
                      "intake_wg_ip": "192.0.2.10", "vpn_interface": "wg0"}
            with mock.patch.object(car_control, "wg_route", return_value=True), \
                 mock.patch.object(heartbeat, "payload", return_value={"uptime_seconds": 1}), \
                 mock.patch.object(heartbeat, "open_heartbeat", return_value=response), \
                 mock.patch.object(car_policy, "uptime", return_value=1000), \
                 mock.patch.object(car_control, "apply_video_command", return_value="start failed") as apply, \
                 mock.patch.object(car_control, "HEARTBEAT_STATE", state_path):
                self.assertEqual(heartbeat.send_heartbeat(policy, {}, Path(temp), Path(temp)),
                                 "start failed")
                self.assertEqual(heartbeat.send_heartbeat(policy, {}, Path(temp), Path(temp)),
                                 "start failed")
            self.assertEqual(apply.call_count, 2)
            self.assertNotIn("last_video_command", car_policy.read_json(state_path))


if __name__ == "__main__":
    unittest.main()
