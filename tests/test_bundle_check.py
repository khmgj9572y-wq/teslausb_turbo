import importlib.util
import tempfile
import unittest
from pathlib import Path

SPEC = importlib.util.spec_from_file_location(
    'check_bundle', Path(__file__).resolve().parents[1] / 'tools' / 'check_bundle.py')
check_bundle = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(check_bundle)


class BundleCheckTests(unittest.TestCase):
    def test_source_examples_pass_but_an_actual_endpoint_requires_review(self):
        self.assertEqual(check_bundle.privacy_issues(
            '127.0.0.1 192.0.2.10 198.51.100.20 203.0.113.30 10.42.0.1/24'), [])
        # This synthetic private endpoint is assembled so the source itself
        # remains suitable for publication.
        issues = check_bundle.privacy_issues('endpoint=' + '.'.join(['10', '9', '8', '7']))
        self.assertEqual(issues, [(1, 'non-placeholder network address')])

    def test_secret_and_private_terms_are_not_repeated_in_findings(self):
        header = '-----BEGIN ' + 'PRIVATE KEY-----'
        secret = 'private-example-host'
        issues = check_bundle.privacy_issues(header + '\n' + secret, [secret])
        self.assertEqual(issues, [(1, 'private key block'), (2, 'private local term')])
        self.assertNotIn(secret, str(issues))
        self.assertNotIn(header, str(issues))

    def test_local_checks_do_not_execute_python_or_follow_symlinks(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            marker = root / 'executed'
            (root / 'example.py').write_text(f'raise RuntimeError({str(marker)!r})\n')
            (root / 'linked.py').symlink_to(root / 'example.py')
            count, errors = check_bundle.check(root)
            self.assertEqual(count, 2)
            self.assertEqual(len(errors), 1)
            self.assertIn('symlink', errors[0])
            self.assertFalse(marker.exists())

    def test_forced_command_ssh_keys_and_tunnel_keys_are_flagged(self):
        ssh = 'restrict,command="restricted-intake" ' + 'ssh-' + 'ed25519 ' + 'A' * 48
        tunnel = 'Public' + 'Key = ' + 'B' * 43 + '='
        issues = check_bundle.privacy_issues(ssh + '\n' + tunnel)
        self.assertEqual(issues, [(1, 'actual SSH public key'), (2, 'actual tunnel key')])

    def test_hardware_addresses_require_review_but_synthetic_fixture_addresses_pass(self):
        hardware = ':'.join(['00', '11', '22', '33', '44', '55'])
        self.assertEqual(check_bundle.privacy_issues(hardware), [(1, 'non-placeholder controller address')])
        self.assertEqual(check_bundle.privacy_issues('02:00:00:00:00:01'), [])
