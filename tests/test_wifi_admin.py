import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("wifi_admin", ROOT / "wifi_admin.py")
wifi_admin = importlib.util.module_from_spec(spec)
spec.loader.exec_module(wifi_admin)


class WifiAdminTests(unittest.TestCase):
    def setUp(self):
        self.uplink = patch.object(wifi_admin.radio_policy, 'require_uplink', return_value='wifi-uplink')
        self.uplink.start()
        self.addCleanup(self.uplink.stop)

    def test_nmcli_escaped_colon_is_not_a_column_break(self):
        self.assertEqual(wifi_admin.split_terse(r"*:Guest\: Wi-Fi:83:WPA2"),
                         ["*", "Guest: Wi-Fi", "83", "WPA2"])

    def test_invalid_names_and_passwords_cannot_inject_keyfile_lines(self):
        for name in ("", "x" * 33, "guest\n[connection]"):
            with self.assertRaises(ValueError):
                wifi_admin.validate_ssid(name)
        for password in ("short", "x" * 64, "password\nkey-mgmt=none"):
            with self.assertRaises(ValueError):
                wifi_admin.validate_password(password)

    def test_captive_portal_keeps_profile_unpromoted(self):
        with tempfile.TemporaryDirectory() as root:
            store = Path(root)
            identifier = "d90be53b-40ca-4f81-98fb-73e89a623bb6"
            profiles = {identifier: {"ssid": "Guest", "expires": None,
                                     "state": "pending"}}
            with patch.object(wifi_admin, "STORE", store), \
                 patch.object(wifi_admin, "META", store / "profiles.json"), \
                 patch.object(wifi_admin, "nmcli") as nmcli, \
                 patch.object(wifi_admin, "is_connected", return_value=True), \
                 patch.object(wifi_admin, "internet_on_wifi", return_value=False), \
                 patch.object(wifi_admin, "promote") as promote, \
                 patch.object(wifi_admin, "synced", return_value=True):
                nmcli.return_value.returncode = 0
                result = wifi_admin.connect(identifier, profiles)
                self.assertEqual(result["state"], "portal")
                promote.assert_not_called()
                self.assertEqual(json.loads((store / "profiles.json").read_text())
                                 ["profiles"][identifier]["state"], "portal")

    def test_protected_connection_cannot_be_removed(self):
        with patch.object(wifi_admin, "nmcli") as nmcli:
            with self.assertRaises(ValueError):
                wifi_admin.remove("d90be53b-40ca-4f81-98fb-73e89a623bb6", {})
            nmcli.assert_not_called()

    def test_expiry_waits_for_trusted_clock(self):
        with patch.object(wifi_admin, "ensure_store"), \
             patch.object(wifi_admin, 'recheck_connection'), \
             patch.object(wifi_admin, "synced", return_value=False), \
             patch.object(wifi_admin, "remove") as remove:
            self.assertEqual(wifi_admin.expire()["clock_ready"], False)
            remove.assert_not_called()

    def test_promoted_wifi_is_demoted_when_it_loses_internet(self):
        identifier = 'd90be53b-40ca-4f81-98fb-73e89a623bb6'
        profiles = {identifier: {'state': 'ready'}}
        with patch.object(wifi_admin, 'nmcli') as nm, \
             patch.object(wifi_admin, 'internet_on_wifi', return_value=False), \
             patch.object(wifi_admin, 'set_priority') as priority, \
             patch.object(wifi_admin, 'save_metadata') as save:
            nm.return_value.stdout = identifier
            self.assertEqual(wifi_admin.recheck_connection(profiles), 'demoted')
            priority.assert_called_once_with(identifier, 800)
            self.assertEqual(profiles[identifier]['state'], 'portal')
            save.assert_called_once_with(profiles)

    def test_priority_edits_do_not_change_an_ssid_containing_setting_text(self):
        with tempfile.TemporaryDirectory() as root:
            identifier = 'd90be53b-40ca-4f81-98fb-73e89a623bb6'
            store = Path(root)
            with patch.object(wifi_admin, 'STORE', store), \
                 patch.object(wifi_admin, 'stage'), patch.object(wifi_admin, 'nmcli'):
                path = wifi_admin.profile_file(identifier)
                data = wifi_admin.create_keyfile(identifier, ' autoconnect=false ', 'password12',
                                                False, uplink_mac='02:00:00:00:00:01')
                path.write_bytes(data)
                wifi_admin.promote(identifier)
                text = path.read_text()
                self.assertIn('ssid = \\sautoconnect=false\\s', text)
                self.assertEqual(text.count('route-metric = 600'), 2)
                wifi_admin.set_priority(identifier, 800)
                self.assertIn('ssid = \\sautoconnect=false\\s', path.read_text())

    def test_protected_wifi_is_not_demoted_by_the_picker(self):
        with patch.object(wifi_admin, 'nmcli') as nm, patch.object(wifi_admin, 'internet_on_wifi') as probe:
            nm.return_value.stdout = 'protected-uuid'
            self.assertIsNone(wifi_admin.recheck_connection({}))
            probe.assert_not_called()


if __name__ == "__main__":
    unittest.main()
