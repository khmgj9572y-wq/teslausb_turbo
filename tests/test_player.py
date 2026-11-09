import os
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class PlayerTests(unittest.TestCase):
    def test_moonlight_backend_builds_headless_stream_command(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            config = root / "video.env"
            config.write_text(
                "VIDEO_BACKEND=moonlight\nMOONLIGHT_HOST=192.0.2.10\n"
                "MOONLIGHT_APP=Desktop\nMOONLIGHT_RESOLUTION=1280x720\n"
                "MOONLIGHT_FPS=30\nMOONLIGHT_BITRATE_KBPS=4000\n")
            fake = root / "moonlight-qt"
            fake.write_text("#!/bin/sh\nprintf '%s\\n' \"$@\" > \"$CAPTURE_ARGS\"\n")
            fake.chmod(0o755)
            captured = root / "args.txt"
            env = dict(os.environ, VIDEO_CONFIG=str(config), CAPTURE_ARGS=str(captured),
                       PATH=str(root) + os.pathsep + os.environ.get("PATH", ""))
            try:
                subprocess.run([str(ROOT / "player.sh")], env=env, timeout=1,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            except subprocess.TimeoutExpired:
                pass  # player is intentionally a reconnecting loop
            self.assertTrue(captured.is_file())
            args = captured.read_text().splitlines()
            self.assertEqual(args[0:3], ["stream", "192.0.2.10", "Desktop"])
            self.assertIn("--video-decoder", args)
            self.assertIn("hardware", args)


if __name__ == "__main__":
    unittest.main()
