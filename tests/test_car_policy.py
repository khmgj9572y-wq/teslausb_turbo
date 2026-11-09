import importlib.util
import tempfile
import unittest
from pathlib import Path


MODULE = Path(__file__).resolve().parents[1] / "car_policy.py"
SPEC = importlib.util.spec_from_file_location("car_policy", str(MODULE))
policy = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(policy)


class MeterTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.net = root / "net"
        self.iface = self.net / "usb1"
        stats = self.iface / "statistics"
        stats.mkdir(parents=True)
        (self.iface / "device").mkdir()
        driver = root / "drivers" / "rndis_host"
        driver.mkdir(parents=True)
        (self.iface / "device" / "driver").symlink_to(driver)
        (self.iface / "ifindex").write_text("6")
        (stats / "rx_bytes").write_text("100")
        (stats / "tx_bytes").write_text("200")
        self.config = {"net_root": self.net, "lte_driver": "rndis_host",
                       "meter_file": root / "meter.json", "ntp_marker": "",
                       "soft_cap_bytes": 500, "hard_cap_bytes": 1000}

    def counters(self, rx, tx):
        (self.iface / "statistics" / "rx_bytes").write_text(str(rx))
        (self.iface / "statistics" / "tx_bytes").write_text(str(tx))

    def test_counts_both_directions_without_double_counting(self):
        first = policy.update_meter(self.config, boot="a", month="2026-11", sync=True)
        self.assertEqual(first["bytes"], 300)
        self.assertEqual(first["month"], "2026-11")
        self.counters(200, 350)
        second = policy.update_meter(self.config, boot="a", month="2026-11", sync=True)
        self.assertEqual(second["bytes"], 550)
        self.assertEqual(policy.cap_mode(self.config, second), "soft")
        again = policy.update_meter(self.config, boot="a", month="2026-11", sync=True)
        self.assertEqual(again["bytes"], 550)

    def test_month_rollover_requires_trusted_time(self):
        policy.update_meter(self.config, boot="a", month="2026-11", sync=True)
        self.counters(300, 500)
        unsynced = policy.update_meter(self.config, boot="a", month="2026-12", sync=False)
        self.assertEqual(unsynced["bytes"], 800)
        self.assertEqual(unsynced["month"], "2026-11")
        trusted = policy.update_meter(self.config, boot="a", month="2026-12", sync=True)
        self.assertEqual(trusted["bytes"], 0)
        self.assertEqual(trusted["month"], "2026-12")

    def test_counter_reset_and_interface_gap(self):
        policy.update_meter(self.config, boot="a", month="2026-11", sync=True)
        (self.iface / "device" / "driver").unlink()
        missing = policy.update_meter(self.config, boot="a", month="2026-11", sync=True)
        self.assertEqual(missing["bytes"], 300)
        self.assertFalse(missing["link_present"])
        driver = self.net.parent / "drivers" / "rndis_host"
        (self.iface / "device" / "driver").symlink_to(driver)
        self.counters(20, 30)
        resumed = policy.update_meter(self.config, boot="a", month="2026-11", sync=True)
        self.assertEqual(resumed["bytes"], 350)


if __name__ == "__main__":
    unittest.main()
