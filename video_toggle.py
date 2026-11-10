#!/usr/bin/env python3
"""Toggle the headless video player with a momentary GPIO button to ground."""

import argparse
import time

import car_control
import car_policy
import display_owner


def toggle(policy):
    # A press during AirPlay also cancels a previously paused Moonlight session.
    if display_owner.busy() or car_control.video_active(policy):
        if not display_owner.stop_video(policy):
            return 'stop failed'
        return 'off'
    allowed, reason = display_owner.start_video(policy)
    return 'on' if allowed else reason


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gpio-pin", type=int, default=27,
                        help="BCM GPIO number; default 27 (header pin 13)")
    args = parser.parse_args()
    policy = car_policy.load_policy('/etc/teslausb-car-link.ini')
    from gpiozero import Button
    button = Button(args.gpio_pin, pull_up=True, bounce_time=0.05)
    print("video toggle waiting on GPIO {}".format(args.gpio_pin), flush=True)
    while True:
        button.wait_for_press()
        print('video toggle: ' + toggle(policy), flush=True)
        button.wait_for_release()
        time.sleep(0.25)


if __name__ == "__main__":
    main()
