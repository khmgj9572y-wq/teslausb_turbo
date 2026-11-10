#!/usr/bin/env python3
"""Coordinate AirPlay takeover and optional Moonlight resumption."""

import argparse
import json
import re
from pathlib import Path

import car_policy

STATE = Path('/run/teslausb-car-link/airplay-owner.json')
LOCK = Path('/run/teslausb-car-link/display.lock')


def boot_id():
    return Path('/proc/sys/kernel/random/boot_id').read_text().strip()


def busy():
    state = car_policy.read_json(STATE)
    return state.get('owner') == 'airplay' and state.get('boot') == boot_id()


def cancel_resume():
    with car_policy.file_lock(LOCK):
        state = car_policy.read_json(STATE)
        if state.get('owner') == 'airplay':
            state['resume'] = False
            car_policy.write_json(STATE, state)


def stop_video(policy):
    """Serialize a manual stop with takeover so it cannot resume afterward."""
    import car_control
    with car_policy.file_lock(LOCK):
        state = car_policy.read_json(STATE)
        if state.get('owner') == 'airplay':
            state['resume'] = False
            car_policy.write_json(STATE, state)
        return car_control.systemctl('stop', policy['video_service'])


def start_video(policy):
    """Keep the policy check and start together with screen takeover."""
    import car_control
    with car_policy.file_lock(LOCK):
        if busy():
            return False, 'AirPlay is using the screen'
        allowed, reason = car_control.video_allowed(policy)
        if not allowed:
            return False, reason
        if not car_control.video_active(policy):
            if not car_control.systemctl('start', policy['video_service']):
                return False, 'start failed'
        return True, 'on'


def claim(policy, token):
    import car_control
    with car_policy.file_lock(LOCK):
        previous = car_policy.read_json(STATE)
        if previous.get('owner') == 'airplay' and previous.get('boot') == boot_id():
            if previous.get('token') == token:
                return 'already owned'
            raise RuntimeError('Another AirPlay receiver owns the screen')
        state = {'owner': 'airplay', 'boot': boot_id(), 'token': token,
                 'resume': car_control.video_active(policy),
                 'prior_start': car_policy.read_json(car_control.VIDEO_START)}
        car_policy.write_json(STATE, state)
        if not car_control.systemctl('stop', policy['video_service']):
            STATE.unlink(missing_ok=True)
            raise RuntimeError('Could not pause Moonlight')
    return 'AirPlay owns screen'


def release(policy, token=None):
    import car_control
    with car_policy.file_lock(LOCK):
        state = car_policy.read_json(STATE)
        if not state or (token is not None and state.get('token') != token):
            return 'no matching owner'
        STATE.unlink(missing_ok=True)
        started = state.get('prior_start', {})
        age = car_policy.uptime() - float(started.get('uptime', -1e9))
        may_resume = (state.get('resume') and state.get('boot') == boot_id() and
                      started.get('boot') == boot_id() and
                      age >= 0 and (policy['max_session_seconds'] == 0 or
                                    age < policy['max_session_seconds']))
        if not may_resume:
            return 'screen released'
        allowed, reason = car_control.video_allowed(policy)
        if not allowed:
            return 'screen released; Moonlight blocked: ' + reason
        if not car_control.systemctl('start', policy['video_service']):
            raise RuntimeError('Could not resume Moonlight')
        # Preserve the original session deadline across an AirPlay interruption.
        car_policy.write_json(car_control.VIDEO_START, started)
    return 'Moonlight resumed'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('claim', 'release', 'release-any'))
    parser.add_argument('token', nargs='?')
    args = parser.parse_args()
    if args.action != 'release-any' and (not args.token or not re.fullmatch('[0-9a-f]{32}', args.token)):
        raise ValueError('Invalid display session')
    policy = car_policy.load_policy('/etc/teslausb-car-link.ini')
    result = claim(policy, args.token) if args.action == 'claim' else release(policy, args.token)
    print(json.dumps({'ok': True, 'result': result}))


if __name__ == '__main__':
    main()
