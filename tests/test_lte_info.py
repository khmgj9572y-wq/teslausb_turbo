import importlib.machinery
import importlib.util
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import lte_info
import car_policy


def reply(text, ok=True):
    return {'ok': ok, 'text': text}


class ModemParsingTests(unittest.TestCase):
    def test_signal_ranges_follow_cesq_and_unknowns_stay_unknown(self):
        data = lte_info.parse({'rssi': reply('+CSQ: 24,99'),
                               'extended_signal': reply('+CESQ:43,99,255,255,30,69')})
        self.assertEqual(data['signal']['rssi_dbm'], -65)
        self.assertEqual(data['signal']['rsrp']['value'], -72)
        self.assertEqual(data['signal']['rsrq']['display'], '-5 to < -4.5 dB')
        self.assertIsNone(data['signal']['ber_code'])
        unknown = lte_info.parse({'rssi': reply('+CSQ: 99,99'),
                                  'extended_signal': reply('+CESQ:99,99,255,255,255,255')})
        self.assertIsNone(unknown['signal']['rssi_dbm'])
        self.assertIsNone(unknown['signal']['rsrp']['value'])
        self.assertEqual(lte_info.quantized(0, -141, 1, 97, 'dBm')['display'], '< -140 dBm')
        self.assertEqual(lte_info.quantized(97, -141, 1, 97, 'dBm')['display'], '≥ -44 dBm')

    def test_registration_and_quoted_operator_do_not_require_format_changes(self):
        data = lte_info.parse({'operator': reply('+COPS: 0,0,"Example, Carrier",7'),
                               'registration': reply('+CEREG: 2,5,"ABCD","123ABC",7'),
                               'apn': reply('+CGDCONT: 1,"IP","internet.example","192.0.2.10",0,0'),
                               'contexts_active': reply('+CGACT: 1,1\n+CGACT: 2,0')})
        self.assertEqual(data['operator'], 'Example, Carrier')
        self.assertEqual(data['radio'], 'LTE')
        self.assertEqual(data['registration']['registration']['state'], 'Roaming')
        self.assertEqual(data['contexts'][0]['apn'], 'internet.example')
        self.assertEqual(data['active_contexts'], [{'id': 1, 'active': True}, {'id': 2, 'active': False}])

    def test_device_removal_clears_cached_radio_information(self):
        with tempfile.TemporaryDirectory() as temp, \
             patch.object(lte_info, 'CACHE', Path(temp)), \
             patch.object(lte_info, 'usb_device', return_value=None), \
             patch.object(car_policy, 'find_lte_interface', return_value=None), \
             patch.object(car_policy, 'uptime', return_value=1000), \
             patch.object(car_policy, 'ntp_synced', return_value=False), \
             patch.object(car_policy, 'update_meter', return_value={'storage_unavailable': True}):
            car_policy.write_json(Path(temp) / 'status.json', {'_device_token': 'old',
                'diagnostics': {'operator': reply('+COPS: 0,0,"Old Carrier",7')}})
            result = lte_info.collect({'soft_cap_bytes': 40, 'hard_cap_bytes': 60})
            self.assertFalse(result['usb']['present'])
            self.assertNotIn('operator', result['modem'])
            self.assertEqual(result['diagnostics'], {})
            self.assertIsNone(result['usage']['bytes'])
            self.assertEqual(result['usage']['cap_mode'], 'unavailable')

    def test_status_reads_cache_without_contacting_modem_or_exposing_private_fields(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(lte_info, 'CACHE', Path(temp)), \
             patch.object(car_policy, 'uptime', return_value=100), patch.object(lte_info, 'at') as at:
            car_policy.write_json(Path(temp) / 'status.json', {'ok': True, 'sample_uptime': 90,
                '_at_uptime': 80, '_device_token': 'private-internal', 'modem': {'sim_state': 'READY'}})
            data = lte_info.status()
            self.assertEqual(data['age_seconds'], 10)
            self.assertEqual(data['at_age_seconds'], 20)
            self.assertFalse(any(key.startswith('_') for key in data))
            at.assert_not_called()

    def test_identifier_read_uses_fixed_sim_file_and_decodes_bcd(self):
        response = {'responses': {'imei': reply('1' * 15), 'imsi': reply('2' * 15),
                                 'iccid': reply('ERROR', False),
                                 'iccid_file': reply('+CRSM: 144,0,"' + '98' * 9 + 'F1"')}}
        with patch.object(lte_info, 'at', return_value=response) as at, \
             patch.object(lte_info, 'usb_device', return_value=None):
            data = lte_info.identities()
        self.assertEqual(data['imei'], '1' * 15)
        self.assertEqual(data['iccid'], '89' * 9 + '1')
        at.assert_called_once_with('identity')

    def test_command_report_without_final_ok_is_labelled_incomplete(self):
        data = lte_info.command_list({'responses': {'commands': reply('+CSQ\n^SYSINFOEX\n', False)}})
        self.assertEqual(data, {'commands': ['+CSQ', '^SYSINFOEX'], 'final_ok': False})

    def test_vendor_signal_codes_are_preserved_without_guessing_a_scale(self):
        data = lte_info.parse({'radio_signal': reply('^HCSQ:"LTE",64,94,138,12'),
                               'lte_signal': reply('^LTERSRP:-94,-12')})
        self.assertEqual(data['vendor_signal_report'], 'LTE, 64, 94, 138, 12')
        self.assertEqual(data['vendor_lte_report'], '-94, -12')
        self.assertNotIn('sinr_db', data['signal'])


def load(name, path):
    loader = importlib.machinery.SourceFileLoader(name, str(path))
    module = importlib.util.module_from_spec(importlib.util.spec_from_loader(name, loader))
    loader.exec_module(module)
    return module


class ModemWebTests(unittest.TestCase):
    def setUp(self):
        self.cgi = load('lte_cgi', ROOT / 'web/lte_api.cgi')
        self.installer = load('install_lte_ui', ROOT / 'tools/install_lte_ui.py')

    def test_identifier_requests_cannot_use_get_or_cross_origin(self):
        for method, origin, expected in [('GET', '', '405'), ('POST', 'https://other.invalid', '403')]:
            env = {'REQUEST_METHOD': method, 'QUERY_STRING': 'action=identity',
                   'HTTP_ORIGIN': origin, 'HTTP_HOST': 'localhost', 'HTTP_X_CAR_LTE_ACTION': '1',
                   'CONTENT_TYPE': 'application/json', 'CONTENT_LENGTH': '2'}
            with patch.dict(self.cgi.os.environ, env, clear=True), \
                 patch.object(self.cgi, 'respond') as respond, patch.object(self.cgi.subprocess, 'run') as run:
                self.cgi.main()
                self.assertTrue(respond.call_args.args[0].startswith(expected))
                run.assert_not_called()

    def test_no_arbitrary_at_command_is_accepted_from_the_web(self):
        body = json.dumps({'command': 'not-allowed'}).encode()
        env = {'REQUEST_METHOD': 'POST', 'QUERY_STRING': 'action=identity', 'HTTP_HOST': 'localhost',
               'HTTP_X_CAR_LTE_ACTION': '1', 'CONTENT_TYPE': 'application/json', 'CONTENT_LENGTH': str(len(body))}
        with patch.dict(self.cgi.os.environ, env, clear=True), \
             patch.object(self.cgi.sys, 'stdin', SimpleNamespace(buffer=io.BytesIO(body))), \
             patch.object(self.cgi, 'respond') as respond, patch.object(self.cgi.subprocess, 'run') as run:
            self.cgi.main()
            self.assertTrue(respond.call_args.args[0].startswith('400'))
            run.assert_not_called()

    def test_tabs_are_idempotent_and_existing_layout_is_preserved(self):
        source = '\n'.join(['#tab9:checked ~ .tab.content9 {', '#tab9:checked ~ .tabheaderholder .tablabel9 {',
            '<input class="tabradiobtn" type="radio" name="tabs" id="tab9">',
            '<label for="tab9" class="tablabel tablabel9">Moonlight</label>',
            '  <div id="settingsoverlay" class="modaloverlay">'])
        patched = self.installer.classic(source)
        self.assertIn('car-lte-frame', patched)
        self.assertIn('Moonlight</label>', patched)
        self.assertEqual(self.installer.classic(patched), patched)
        alternative = self.installer.alternative('<html><body>Existing app</body></html>')
        self.assertEqual(self.installer.alternative(alternative), alternative)
        self.assertIn('Existing app', alternative)
