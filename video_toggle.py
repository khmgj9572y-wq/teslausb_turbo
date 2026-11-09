#!/usr/bin/env python3
"""Toggle the headless video player with a momentary GPIO button to ground."""

import argparse
import subprocess
import time


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gpio-pin", type=int, default=27,
                        help="BCM GPIO number; default 27 (header pin 13)")
    args = parser.parse_args()
    from gpiozero import Button
    button = Button(args.gpio_pin, pull_up=True, bounce_time=0.05)
    print("video toggle waiting on GPIO {}".format(args.gpio_pin), flush=True)
    while True:
        button.wait_for_press()
        active = subprocess.run(
            ["systemctl", "is-active", "--quiet", "car-video.service"])
        action = "stop" if active.returncode == 0 else "start"
        subprocess.run(["systemctl", action, "car-video.service"], check=False)
        button.wait_for_release()
        time.sleep(0.25)


if __name__ == "__main__":
    main()
