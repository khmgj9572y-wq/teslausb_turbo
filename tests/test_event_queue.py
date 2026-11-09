import importlib.util
import os
import sqlite3
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock


MODULE = Path(__file__).resolve().parents[1] / "event_queue.py"
sys.path.insert(0, str(MODULE.parent))
SPEC = importlib.util.spec_from_file_location("event_queue", str(MODULE))
event_queue = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(event_queue)


class QueueTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.archive = root / "mutable" / "TeslaCam"
        self.snapshots = root / "snapshots"
        self.spool = root / "spool"
        self.snapshots.mkdir()
        self.config = {
            "archive_root": self.archive,
            "snapshot_root": self.snapshots,
            "snapshot_mount_root": root / "mounted",
            "spool_root": self.spool,
            "require_mountpoint": "",
            "upload_marker": root / "upload-active",
            "min_file_age": 0,
            "stable_mp4_snapshots": 1,
            "retention_days": 7,
            "max_spool_bytes": 1024 * 1024,
            "min_free_bytes": 0,
            "max_files": 100,
            "bandwidth_kib": 750,
            "active_bandwidth_kib": 250,
            "ssh_host": "192.0.2.10",
            "ssh_user": "teslaintake",
            "ssh_path": ".",
            "ssh_key": str(root / "key"),
            "vpn_interface": "wg0",
        }
        (root / "key").write_text("test key")
        self.db = event_queue.database(self.config)
        self.addCleanup(self.db.close)

    def add_link(self, clip_type, event, filename, contents, outside=False):
        source_root = Path(self.temp.name) / ("outside" if outside else "snapshots")
        source = source_root / event / filename
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_bytes(contents)
        os.utime(str(source), (time.time() - 120, time.time() - 120))
        destination = self.archive / clip_type / event / filename
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.symlink_to(source)
        return source, destination

    def test_stages_snapshot_without_changing_teslausb_view(self):
        source, link = self.add_link("SavedClips", "2026-11-08_12-00-00",
                                     "event.json", b'{"reason":"user_interaction_honk"}')
        staged, skipped = event_queue.scan(self.config, self.db)
        self.assertEqual((staged, skipped), (1, 0))
        self.assertTrue(link.is_symlink())
        self.assertEqual(source.read_bytes(),
                         (self.spool / "SavedClips" / link.parent.name / link.name).read_bytes())
        staged_again, _ = event_queue.scan(self.config, self.db)
        self.assertEqual(staged_again, 0)

    def test_rejects_symlink_outside_snapshot_mounts(self):
        _, link = self.add_link("SentryClips", "2026-11-08_12-00-00",
                                "event.json", b"private", outside=True)
        staged, skipped = event_queue.scan(self.config, self.db)
        self.assertEqual((staged, skipped), (0, 1))
        self.assertFalse((self.spool / "SentryClips" / link.parent.name / link.name).exists())

    def test_changed_snapshot_file_is_queued_again(self):
        source, _ = self.add_link("SavedClips", "2026-11-08_12-00-00",
                                  "2026-11-08_12-00-00-front.mp4", b"clip1")
        event_queue.scan(self.config, self.db)
        self.db.execute("UPDATE files SET status='sent', sent_at=?", (int(time.time()),))
        self.db.commit()
        source.write_bytes(b"clip2 with more bytes")
        os.utime(str(source), (time.time() - 60, time.time() - 60))
        staged, _ = event_queue.scan(self.config, self.db)
        self.assertEqual(staged, 1)
        self.assertEqual(self.db.execute("SELECT status FROM files").fetchone()[0], "pending")

    def test_send_marks_only_successful_files_and_orders_metadata_first(self):
        self.add_link("SavedClips", "2026-11-08_12-00-00",
                      "2026-11-08_12-00-00-front.mp4", b"video")
        self.add_link("SavedClips", "2026-11-08_12-00-00", "event.json", b"{}")
        self.add_link("SavedClips", "2026-11-08_12-00-00", "thumb.png",
                      event_queue.PNG_START + b"image-data" + event_queue.PNG_END)
        event_queue.scan(self.config, self.db)
        success = mock.Mock(returncode=0, stdout="", stderr="")
        with mock.patch.object(event_queue, "vpn_route_ok", return_value=True), \
             mock.patch.object(event_queue.subprocess, "run", return_value=success) as run:
            sent = event_queue.send(self.config, self.db)
        self.assertEqual(sent, 3)
        transfers = [call.args[0] for call in run.call_args_list if call.args[0][0] == "rsync"]
        self.assertEqual(transfers[0][-2], "./SavedClips/2026-11-08_12-00-00/thumb.png")
        self.assertEqual(transfers[1][-2], "./SavedClips/2026-11-08_12-00-00/event.json")
        self.assertIn("--ignore-existing", transfers[0])
        self.assertIn("--partial-dir=.partial", transfers[0])
        self.assertEqual(transfers[0][-1], "teslaintake@192.0.2.10:./")
        self.assertTrue(all(call.args[0][0] != "ssh" for call in run.call_args_list))
        self.assertEqual(self.db.execute(
            "SELECT COUNT(*) FROM files WHERE status='sent'").fetchone()[0], 3)

    def test_prune_keeps_database_state_for_deduplication(self):
        self.add_link("SentryClips", "2026-11-08_12-00-00", "event.json", b"{}")
        event_queue.scan(self.config, self.db)
        self.config["retention_days"] = 0
        self.db.execute("UPDATE files SET status='sent', sent_at=?", (int(time.time()) - 1,))
        self.db.commit()
        self.assertEqual(event_queue.prune(self.config, self.db), 1)
        self.assertEqual(self.db.execute("SELECT size FROM files").fetchone()[0], 0)
        self.assertEqual(event_queue.scan(self.config, self.db)[0], 0)

    def test_spool_cap_preserves_source_for_retry(self):
        source, link = self.add_link("SavedClips", "2026-11-08_12-00-00",
                                     "event.json", b"{}")
        self.config["max_spool_bytes"] = 1
        self.assertEqual(event_queue.scan(self.config, self.db), (0, 1))
        self.assertTrue(link.is_symlink())
        self.assertEqual(source.read_bytes(), b"{}")

    def test_failed_transfer_remains_pending(self):
        self.add_link("SavedClips", "2026-11-08_12-00-00", "event.json", b"{}")
        event_queue.scan(self.config, self.db)
        results = [mock.Mock(returncode=0, stdout="", stderr=""),
                   mock.Mock(returncode=12, stdout="", stderr="connection lost")]
        with mock.patch.object(event_queue, "vpn_route_ok", return_value=True), \
             mock.patch.object(event_queue.subprocess, "run", side_effect=results):
            self.assertEqual(event_queue.send(self.config, self.db), 0)
        self.assertEqual(self.db.execute("SELECT status FROM files").fetchone()[0], "pending")

    def test_home_wifi_pauses_lte_upload(self):
        self.add_link("SentryClips", "2026-11-08_12-00-00", "event.json", b"{}")
        event_queue.scan(self.config, self.db)
        with mock.patch.object(event_queue.car_policy, "home_wifi_reachable", return_value=True), \
             mock.patch.object(event_queue.subprocess, "run") as run:
            self.assertEqual(event_queue.send(self.config, self.db, policy={}), 0)
        run.assert_not_called()

    def test_soft_cap_sends_metadata_but_not_video(self):
        event = "2026-11-08_12-00-00"
        self.add_link("SentryClips", event, "event.json", b"{}")
        self.add_link("SentryClips", event, "2026-11-08_12-00-00-front.mp4", b"video")
        event_queue.scan(self.config, self.db)
        success = mock.Mock(returncode=0, stdout="", stderr="")
        with mock.patch.object(event_queue.car_policy, "home_wifi_reachable", return_value=False), \
             mock.patch.object(event_queue.car_policy, "current_throttle_flags", return_value=0), \
             mock.patch.object(event_queue.car_policy, "update_meter", return_value={"bytes": 40}), \
             mock.patch.object(event_queue.car_policy, "cap_mode", return_value="soft"), \
             mock.patch.object(event_queue, "vpn_route_ok", return_value=True), \
             mock.patch.object(event_queue.subprocess, "run", return_value=success) as run:
            self.assertEqual(event_queue.send(self.config, self.db, policy={}), 1)
        transfers = [call.args[0] for call in run.call_args_list if call.args[0][0] == "rsync"]
        self.assertEqual(len(transfers), 1)
        self.assertTrue(transfers[0][-2].endswith("event.json"))
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM files WHERE status='pending'").fetchone()[0], 1)

    def test_soft_cap_stages_only_event_metadata(self):
        event = "2026-11-08_12-00-00"
        self.add_link("SentryClips", event, "event.json", b"{}")
        self.add_link("SentryClips", event, "2026-11-08_12-00-00-front.mp4", b"video")
        with mock.patch.object(event_queue.car_policy, "home_wifi_reachable", return_value=False), \
             mock.patch.object(event_queue.car_policy, "update_meter", return_value={"bytes": 40}), \
             mock.patch.object(event_queue.car_policy, "cap_mode", return_value="soft"):
            staged, _ = event_queue.scan(self.config, self.db, policy={})
        self.assertEqual(staged, 1)
        self.assertEqual(self.db.execute("SELECT path FROM files").fetchone()[0],
                         "SentryClips/{}/event.json".format(event))

    def test_video_backlog_reserves_space_for_new_event_metadata(self):
        event = "2026-11-08_12-00-00"
        self.add_link("SentryClips", event, "event.json", b"{}")
        self.add_link("SentryClips", event, "2026-11-08_12-00-00-front.mp4", b"123456")
        self.config["max_spool_bytes"] = 10
        self.config["metadata_reserve_bytes"] = 4
        staged, skipped = event_queue.scan(self.config, self.db)
        self.assertEqual((staged, skipped), (1, 1))
        self.assertEqual(self.db.execute("SELECT path FROM files").fetchone()[0],
                         "SentryClips/{}/event.json".format(event))

    def test_invalid_event_json_waits_for_later_snapshot(self):
        self.add_link("SentryClips", "2026-11-08_12-00-00", "event.json", b'{"reason":')
        staged, skipped = event_queue.scan(self.config, self.db)
        self.assertEqual((staged, skipped), (0, 1))

    def test_thumbnail_needs_png_signature_and_iend(self):
        event = "2026-11-08_12-00-00"
        source, _ = self.add_link("SentryClips", event, "thumb.png", b"\x89PNG\r\n\x1a\ntruncated")
        self.assertEqual(event_queue.scan(self.config, self.db), (0, 1))
        source.write_bytes(event_queue.PNG_START + b"content" + event_queue.PNG_END)
        os.utime(str(source), (time.time() - 60, time.time() - 60))
        self.assertEqual(event_queue.scan(self.config, self.db)[0], 1)

    def test_mp4_needs_two_consecutive_snapshots_with_same_size_and_mtime(self):
        self.config["stable_mp4_snapshots"] = 2
        event = "2026-11-08_12-00-00"
        name = "2026-11-08_11-59-00-front.mp4"
        first = self.snapshots / "snap-000001" / "TeslaCam" / "SavedClips" / event / name
        first.parent.mkdir(parents=True)
        first.write_bytes(b"finished clip")
        os.utime(str(first), (1000000000, 1000000000))
        link = self.archive / "SavedClips" / event / name
        link.parent.mkdir(parents=True)
        link.symlink_to(first)
        self.assertEqual(event_queue.scan(self.config, self.db)[0], 0)
        self.assertEqual(event_queue.scan(self.config, self.db)[0], 0)
        second = self.snapshots / "snap-000002" / "TeslaCam" / "SavedClips" / event / name
        second.parent.mkdir(parents=True)
        second.write_bytes(b"finished clip")
        os.utime(str(second), (1000000000, 1000000000))
        link.unlink()
        link.symlink_to(second)
        self.assertEqual(event_queue.scan(self.config, self.db)[0], 1)


if __name__ == "__main__":
    unittest.main()
