import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import radio_policy
import car_policy
import wifi_admin
import radio_services


class RadioTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.uplink = '02:00:00:00:00:01'
        self.hotspot = '02:00:00:00:00:02'
        self.add_radio('wlan0', self.hotspot, 'brcmfmac')
        self.add_radio('wlan7', self.uplink, 'mt7921u')

    def add_radio(self, name, mac, driver):
        path = self.root / name
        (path / 'device').mkdir(parents=True)
        target = self.root / 'drivers' / driver
        target.mkdir(parents=True, exist_ok=True)
        (path / 'device' / 'driver').symlink_to(target)
        (path / 'address').write_text(mac)

    def test_uplink_follows_mac_when_interface_numbers_change(self):
        with patch.object(radio_policy, 'permanent_mac', return_value=self.hotspot):
            result = radio_policy.interface_for('uplink', {'uplink_mac': self.uplink}, self.root)
        self.assertEqual(result, 'wlan7')

    def test_missing_uplink_does_not_select_hotspot(self):
        with patch.object(radio_policy, 'permanent_mac', return_value=self.hotspot):
            result = radio_policy.interface_for('uplink', {'uplink_mac': '02:00:00:00:00:99'}, self.root)
        self.assertIsNone(result)

    def test_random_scan_mac_uses_permanent_address(self):
        (self.root / 'wlan7' / 'address').write_text('02:00:00:00:00:44')
        with patch.object(radio_policy, 'permanent_mac', side_effect=lambda name, root: self.uplink if name == 'wlan7' else self.hotspot):
            self.assertEqual(radio_policy.interface_for('uplink', {'uplink_mac': self.uplink}, self.root), 'wlan7')

    def test_home_archive_route_on_hotspot_is_not_a_wifi_uplink(self):
        config = {'home_archive_host': '192.0.2.2', 'home_archive_port': 445}
        with patch.object(radio_policy, 'uplink_interface', return_value='wlan7'), \
             patch.object(car_policy.socket, 'gethostbyname', return_value='192.0.2.2'), \
             patch.object(car_policy.subprocess, 'run', return_value=Mock(returncode=0, stdout='192.0.2.2 dev wlan0')), \
             patch.object(car_policy.socket, 'create_connection') as connect:
            self.assertFalse(car_policy.home_wifi_reachable(config))
            connect.assert_not_called()

    def test_new_profile_is_bound_to_uplink_mac(self):
        data = wifi_admin.create_keyfile('d90be53b-40ca-4f81-98fb-73e89a623bb6', 'Guest', '', False, uplink_mac=self.uplink).decode()
        self.assertIn('mac-address=' + self.uplink, data)
        self.assertNotIn('interface-name=', data)
        self.assertNotIn(self.hotspot, data)

    def test_staging_cannot_switch_the_existing_uplink_to_ap(self):
        with patch.object(radio_policy, 'settings', return_value={'split_enabled': False}), \
             patch.object(radio_services, 'run') as command:
            with self.assertRaisesRegex(RuntimeError, 'not been activated'):
                radio_services.hotspot_up()
            command.assert_not_called()

    def test_one_radio_cannot_be_assigned_both_roles(self):
        config = {'split_enabled': True, 'uplink_mac': self.hotspot, 'hotspot_mac': self.hotspot}
        with patch.object(radio_policy, 'settings', return_value=config):
            with self.assertRaisesRegex(RuntimeError, 'different radios'):
                radio_services.hotspot_settings()

    def test_receiver_refuses_station_interface_before_hotspot_activation(self):
        with patch.object(radio_services, 'hotspot_settings', return_value=({}, 'wifi-ap')), \
             patch.object(radio_services, 'run', return_value=Mock(stdout='home-client\n')) as command:
            with self.assertRaisesRegex(RuntimeError, 'active hotspot'):
                radio_services.airplay_prepare()
            self.assertEqual(command.call_count, 1)

    def test_station_profile_dedicated_to_another_radio_is_preserved(self):
        eligible = radio_policy.profile_uses_uplink
        self.assertFalse(eligible(self.hotspot, '', '', (self.uplink,), ('wifi-uplink',)))
        self.assertFalse(eligible('', 'wifi-other', '', (self.uplink,), ('wifi-uplink',)))
        self.assertFalse(eligible('', '', 'wifi-other', (self.uplink,), ('wifi-uplink',)))
        self.assertTrue(eligible('', '', 'wifi-uplink', (self.uplink,), ('wifi-uplink',)))
        self.assertTrue(eligible('', '', '', (self.uplink,), ('wifi-uplink',)))

    def test_terse_mac_escaping_does_not_exclude_the_existing_uplink(self):
        escaped = self.uplink.replace(':', '\\:').upper()
        self.assertTrue(radio_policy.profile_uses_uplink(
            escaped, '', 'wifi-uplink', (self.uplink,), ('wifi-uplink',)))
