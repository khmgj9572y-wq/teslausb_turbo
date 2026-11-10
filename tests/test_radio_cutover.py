import importlib.util
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
spec = importlib.util.spec_from_file_location('rebind_uplink', ROOT / 'tools/rebind_uplink.py')
cutover = importlib.util.module_from_spec(spec); spec.loader.exec_module(cutover)


class CutoverTests(unittest.TestCase):
    def test_uplink_never_chooses_an_unconfigured_usb_radio(self):
        with patch.object(cutover.radio_policy, 'settings', return_value={}), \
             patch.object(cutover.radio_policy, 'interfaces_for_driver', return_value=['wifi-a', 'wifi-b']):
            with self.assertRaisesRegex(RuntimeError, 'future_uplink_mac'):
                cutover.candidate()

    def test_explicit_uplink_is_selected_from_multiple_matching_drivers(self):
        mac = '02:00:00:00:00:01'
        with patch.object(cutover.radio_policy, 'settings', return_value={'future_uplink_mac': mac}), \
             patch.object(cutover.radio_policy, 'interfaces_for_driver', return_value=['wifi-a', 'wifi-b']), \
             patch.object(cutover.radio_policy, 'interface_for', return_value='wifi-b'):
            self.assertEqual(cutover.candidate(), ('wifi-b', mac))

    def test_usb3_radio_is_rejected_before_any_profile_changes(self):
        with patch.object(cutover.radio_policy, 'usb_speed', return_value=5000):
            with self.assertRaisesRegex(RuntimeError, 'USB 2'):
                cutover.require_usb2('wifi-a')

    def test_absent_hotspot_selector_does_not_assign_the_onboard_radio(self):
        self.assertIsNone(cutover.hotspot_target({'hotspot_mac': '02:00:00:00:00:02',
                                                'hotspot_driver': 'brcmfmac', 'split_enabled': False},
                                               '02:00:00:00:00:01'))

    def test_dedicated_hotspot_must_be_present_separate_and_on_usb2(self):
        settings = {'future_hotspot_mac': '02:00:00:00:00:03'}
        with patch.object(cutover.radio_policy, 'interface_for', return_value='wifi-c'), \
             patch.object(cutover.radio_policy, 'interfaces_for_driver', return_value=['wifi-a', 'wifi-c']), \
             patch.object(cutover.radio_policy, 'usb_speed', return_value=480):
            self.assertEqual(cutover.hotspot_target(settings, '02:00:00:00:00:01'),
                             settings['future_hotspot_mac'])
