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
             patch.object(wifi_admin, "synced", return_value=False), \
             patch.object(wifi_admin, "remove") as remove:
            self.assertEqual(wifi_admin.expire()["clock_ready"], False)
            remove.assert_not_called()


if __name__ == "__main__":
    unittest.main()
