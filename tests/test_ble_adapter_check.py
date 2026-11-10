import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import ble_adapter_check


class AdapterTests(unittest.TestCase):
    def test_uart_is_identified_independently_of_controller_number(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for name, bus in [('hci1', 'serial'), ('hci0', 'usb')]:
                device = root / 'devices' / name
                device.mkdir(parents=True)
                target = root / 'buses' / bus
                target.mkdir(parents=True)
                (device / 'subsystem').symlink_to(target)
                hci = root / 'bluetooth' / name
                hci.mkdir(parents=True)
                (hci / 'device').symlink_to(device)
            found = ble_adapter_check.controllers(root / 'bluetooth')
            self.assertEqual(found, [{'adapter': 'hci0', 'transport': 'usb'},
                                     {'adapter': 'hci1', 'transport': 'uart'}])
            self.assertIn('WARNING', '\n'.join(ble_adapter_check.messages(found)))

    def test_expected_assignment_has_no_warning_or_controller_address(self):
        text = '\n'.join(ble_adapter_check.messages([
            {'adapter': 'hci0', 'transport': 'uart'}, {'adapter': 'hci1', 'transport': 'usb'}]))
        self.assertNotIn('WARNING', text)
        self.assertIn('hci0=uart', text)
        self.assertNotRegex(text, r'(?:[0-9a-f]{2}:){5}[0-9a-f]{2}')

    def test_missing_uart_is_a_warning(self):
        self.assertIn('WARNING', '\n'.join(ble_adapter_check.messages([])))
