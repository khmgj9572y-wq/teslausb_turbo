import importlib.util
import tempfile
import unittest
from pathlib import Path
from unittest import mock


MODULE = Path(__file__).resolve().parents[1] / "gadget_guard.py"
SPEC = importlib.util.spec_from_file_location("gadget_guard", str(MODULE))
guard = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(guard)
IMAGE_IN_USE = guard.image_in_use


class GuardTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        udc_root = root / "udc"
        (udc_root / "test-udc").mkdir(parents=True)
        self.udc_file = root / "gadget-UDC"
        self.udc_file.write_text("test-udc\n")
        self.udc_state = udc_root / "test-udc" / "state"
        self.udc_state.write_text("not attached\n")
        self.config = {
            "udc_file": self.udc_file,
            "udc_root": udc_root,
            "state_file": root / "state.json",
            "log_file": root / "guard.log",
            "disable_cmd": "/fake/disable_gadget.sh",
            "enable_cmd": "/fake/enable_gadget.sh",
            "interval": 30,
            "max_attempts": 3,
            "settle_seconds": 3,
            "verify_seconds": 10,
        }
        patcher = mock.patch.object(guard, 'image_in_use', return_value=False)
        patcher.start(); self.addCleanup(patcher.stop)

    def test_two_checks_then_replug_and_log_result(self):
        calls = []

        def fake_run(command, **_):
            calls.append(command[0])
            if command[0].endswith('disable_gadget.sh'):
                self.udc_file.write_text('\n')
            if command[0].endswith("enable_gadget.sh"):
                self.udc_file.write_text('test-udc\n')
                self.udc_state.write_text("configured\n")
            return mock.Mock(returncode=0)

        state, outcome = guard.check_once(self.config, up=100, boot="boot-a",
                                          is_transition=lambda: False, run=fake_run)
        self.assertEqual(outcome, "waiting-for-second-check")
        self.assertFalse(calls)
        state, outcome = guard.check_once(self.config, state, up=130, boot="boot-a",
                                          is_transition=lambda: False, run=fake_run,
                                          sleep=lambda _: None)
        self.assertEqual(outcome, "replugged")
        self.assertEqual(calls, ["/fake/disable_gadget.sh", "/fake/enable_gadget.sh"])
        self.assertEqual(guard.load_state(self.config["state_file"])["attempts"][0]["uptime"], 130)
        self.assertIn("state=configured", self.config["log_file"].read_text())

    def test_empty_udc_is_intentional_disconnect(self):
        self.udc_file.write_text("\n")
        state, outcome = guard.check_once(self.config, {"boot": "boot-a", "consecutive": 1},
                                          up=130, boot="boot-a", is_transition=lambda: False)
        self.assertEqual(outcome, "healthy-or-intentional")
        self.assertEqual(state["consecutive"], 0)
        self.assertFalse(self.config["log_file"].exists())

    def test_rate_limit_blocks_fourth_replug(self):
        prior = {"boot": "boot-a", "consecutive": 1,
                 "attempts": [{"boot": "boot-a", "uptime": t} for t in (10, 20, 30)]}
        state, outcome = guard.check_once(self.config, prior, up=100, boot="boot-a",
                                          is_transition=lambda: False)
        self.assertEqual(outcome, "rate-limited")
        self.assertEqual(len(state["attempts"]), 3)

    def test_previous_boot_attempts_remain_during_first_hour(self):
        prior = [{"boot": "boot-old", "uptime": 99}]
        self.assertEqual(len(guard.recent_attempts(prior, "boot-new", 100)), 1)
        self.assertEqual(len(guard.recent_attempts(prior, "boot-new", 3600)), 0)

    def test_transition_process_prevents_replug(self):
        state = {"boot": "boot-a", "consecutive": 1, "attempts": []}
        next_state, outcome = guard.check_once(self.config, state, up=100, boot="boot-a",
                                               is_transition=lambda: True)
        self.assertEqual(outcome, "teslausb-transition-in-progress")
        self.assertFalse(next_state["attempts"])

    def test_udc_disappearing_before_action_prevents_replug(self):
        state = {"boot": "boot-a", "consecutive": 1, "attempts": []}

        def change_udc():
            self.udc_file.write_text("\n")
            return False

        next_state, outcome = guard.check_once(self.config, state, up=100, boot="boot-a",
                                               is_transition=change_udc)
        self.assertEqual(outcome, "changed-before-action")
        self.assertFalse(next_state["attempts"])

    def test_failed_disable_never_enables_or_waits(self):
        run = mock.Mock(return_value=mock.Mock(returncode=1)); sleep = mock.Mock()
        state, result = guard.check_once(self.config, {'boot': 'a', 'consecutive': 1},
            up=100, boot='a', is_transition=lambda: False, run=run, sleep=sleep)
        self.assertEqual(result, 'disable-failed')
        self.assertEqual(run.call_count, 1)
        self.assertEqual(run.call_args.args[0], [self.config['disable_cmd']])
        sleep.assert_not_called()

    def test_loop_attachment_blocks_enable_even_before_filesystem_mount(self):
        root = Path(self.temp.name) / 'block'
        loop = root / 'loop7/loop'; loop.mkdir(parents=True)
        image = Path(self.temp.name) / 'camera.bin'; image.touch()
        (loop / 'backing_file').write_text(str(image))
        self.assertTrue(IMAGE_IN_USE(image, root))
        self.assertFalse(IMAGE_IN_USE(image.with_name('different.bin'), root))

    def test_archive_starting_during_settle_blocks_enable(self):
        for kind in ('transition', 'loop', 'rebound'):
            self.udc_file.write_text('test-udc\n')
            changed = {'busy': False}
            def run(command, **kwargs):
                self.udc_file.write_text('\n')
                return mock.Mock(returncode=0)
            def settle(_):
                changed['busy'] = True
                if kind == 'rebound': self.udc_file.write_text('test-udc\n')
            runner = mock.Mock(side_effect=run)
            _, result = guard.check_once(self.config, {'boot': 'a', 'consecutive': 1},
                up=100, boot='a', run=runner, sleep=settle,
                is_transition=lambda: kind == 'transition' and changed['busy'],
                is_image_busy=lambda: kind == 'loop' and changed['busy'])
            self.assertEqual(result, 'enable-skipped', kind)
            self.assertEqual(runner.call_count, 1, kind)

    def test_suspended_after_configured_does_not_trigger_replug(self):
        self.udc_state.write_text('configured\n')
        state, _ = guard.check_once(self.config, up=100, boot='a')
        self.udc_state.write_text('suspended\n')
        run = mock.Mock()
        for up in (130, 160, 190):
            state, result = guard.check_once(self.config, state, up=up, boot='a', run=run)
            self.assertEqual(result, 'configured-host-suspended')
        run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
