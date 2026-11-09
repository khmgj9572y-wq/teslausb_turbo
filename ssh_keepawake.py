#!/usr/bin/python3 -I
"""ssh-keepawake: keep the Tesla awake while someone is actually USING an SSH session on the car Pi.

Never cuts a session off: an idle session simply stops counting, and the car may sleep as usual.
A session (logind, Service=sshd, Remote=yes, State active/online, older than MIN_AGE) is IN USE if, within IDLE seconds, it had
terminal input or output (pts atime/mtime), used >= CPU_BUSY seconds of CPU in its scope (a running job), or just logged in.
While any session is in use: BLE "charge-port-close" nudge (TeslaUSB's own keep-awake command) every NUDGE seconds.
Skips a nudge while TeslaUSB's own keep-awake task (/tmp/keep_awake_task_pid) is running. Never closes or touches sessions.
  ssh-keepawake            run (systemd service)
  ssh-keepawake --dry      log decisions, never nudge
  ssh-keepawake --status   print the last state
"""
import json, os, re, subprocess, sys, time
from pathlib import Path

POLL, IDLE, MIN_AGE, NUDGE, CPU_BUSY = 30, 15 * 60, 60, 240, 2.0
STATE = Path('/run/ssh-keepawake/state.json')
VARS = Path('/root/teslausb_setup_variables.conf')
TESLA = '/root/bin/tesla-control'; KEY = '/root/.ble/key_private.pem'
VIN_RE = re.compile(r'[A-HJ-NPR-Za-hj-npr-z0-9]{17}')


def log(msg):
    print(VIN_RE.sub('<VIN>', msg), flush=True)


def vin():
    for line in VARS.read_text().splitlines():
        m = re.fullmatch(r"\s*export\s+TESLA_BLE_VIN=['\"]?([A-Za-z0-9]{17})['\"]?\s*", line)
        if m: return m.group(1).upper()
    return None


def sessions():
    out = subprocess.run(['loginctl', 'list-sessions', '--no-legend'], capture_output=True, text=True, timeout=10).stdout
    found = []
    for line in out.splitlines():
        sid = line.split()[0] if line.split() else ''
        if not sid.isdigit(): continue
        props = {}
        r = subprocess.run(['loginctl', 'show-session', sid, '-p', 'Service', '-p', 'Remote', '-p', 'State', '-p', 'TTY',
                            '-p', 'Scope', '-p', 'Name', '-p', 'TimestampMonotonic', '-p', 'User'],
                           capture_output=True, text=True, timeout=10)
        for kv in r.stdout.splitlines():
            k, _, v = kv.partition('='); props[k] = v
        if props.get('Service') == 'sshd' and props.get('Remote') == 'yes' and props.get('State') in ('active', 'online'):
            props['Id'] = sid; found.append(props)
    return found


def mono():
    return time.clock_gettime(time.CLOCK_MONOTONIC)


def cpu_seconds(props):
    p = Path('/sys/fs/cgroup/user.slice/user-%s.slice/%s/cpu.stat' % (props.get('User', ''), props.get('Scope', '')))
    try:
        for line in p.read_text().splitlines():
            if line.startswith('usage_usec '): return int(line.split()[1]) / 1e6
    except OSError:
        pass
    return None


def tty_age(props):
    tty = props.get('TTY', '')
    if not tty.startswith('pts/'): return None
    try:
        st = os.stat('/dev/' + tty); return time.time() - max(st.st_atime, st.st_mtime)
    except OSError:
        return None


def teslausb_keepawake_running():
    try:
        pid = int(Path('/tmp/keep_awake_task_pid').read_text().strip()); os.kill(pid, 0); return True
    except (OSError, ValueError):
        return False


def wrapper_missing():
    """True if /root/bin/tesla-control is the bare binary (hci0 lock wrapper lost, e.g. after a TeslaUSB reinstall)."""
    try:
        with open(TESLA, 'rb') as f: return f.read(4) == b'\x7fELF'
    except OSError:
        return False


def nudge(v):
    r = subprocess.run([TESLA, '-ble', '-key-file', KEY, '-vin', v, 'charge-port-close'], capture_output=True, text=True, timeout=300)  # includes up to 120 s hci0 lock wait
    text = (r.stdout + r.stderr).strip()
    if r.returncode == 0 or 'already closed' in text or 'cable connected' in text:
        return True, ('ok' if r.returncode == 0 else ('already closed' if 'already closed' in text else 'cable connected'))
    return False, text[-200:]


def main():
    dry = '--dry' in sys.argv[1:]
    if '--status' in sys.argv[1:]:
        print(STATE.read_text() if STATE.exists() else '{}'); return
    STATE.parent.mkdir(mode=0o755, exist_ok=True)
    cpu_hist = {}   # session id -> list of (mono, cpu_s)
    last_nudge = None; was_awake = False; v = vin()
    log('start: idle %ds, nudge every %ds, min age %ds%s, VIN %s' % (IDLE, NUDGE, MIN_AGE, ' (DRY)' if dry else '', 'set' if v else 'MISSING'))
    if wrapper_missing():
        log('WARNING: %s is the bare binary: hci0 lock wrapper missing (re-run tools/install_hci0_lock.sh)' % TESLA)
    while True:
        now = mono(); in_use = []; idle = []
        live = sessions(); ids = {s['Id'] for s in live}
        for sid in list(cpu_hist):
            if sid not in ids: del cpu_hist[sid]
        for s in live:
            age = now - int(s.get('TimestampMonotonic', '0') or 0) / 1e6
            c = cpu_seconds(s); h = cpu_hist.setdefault(s['Id'], [])
            if c is not None: h.append((now, c))
            while h and now - h[0][0] > IDLE: h.pop(0)
            busy = len(h) >= 2 and h[-1][1] - h[0][1] >= CPU_BUSY
            t = tty_age(s); typed = t is not None and t < IDLE
            fresh = age < IDLE
            label = '%s#%s' % (s.get('Name', '?'), s['Id'])
            if age < MIN_AGE:
                continue
            (in_use if (typed or busy or fresh) else idle).append(
                label + '(' + ','.join(x for x, y in (('tty', typed), ('cpu', busy), ('new', fresh)) if y) + ')' if (typed or busy or fresh) else label)
        awake = bool(in_use)
        if awake != was_awake:
            log(('KEEP AWAKE: in use ' + ' '.join(in_use)) if awake else ('release: no session in use (idle: %s)' % (' '.join(idle) or 'none')))
            was_awake = awake
        result = None
        if awake and (last_nudge is None or now - last_nudge >= NUDGE):
            if not v: v = vin()
            if teslausb_keepawake_running():
                result = 'skipped (TeslaUSB keep-awake running)'; last_nudge = now
            elif dry:
                result = 'DRY: would nudge'; last_nudge = now
            elif v:
                ok, why = nudge(v); result = ('nudged: ' if ok else 'NUDGE FAILED: ') + why
                last_nudge = now if ok else now - NUDGE + 60   # retry in a minute after a failure
            else:
                result = 'no VIN in settings'
            log(result)
        STATE.write_text(json.dumps({'time': int(time.time()), 'keep_awake': awake, 'in_use': in_use, 'idle': idle,
                                     'last_nudge_s_ago': int(now - last_nudge) if last_nudge is not None else None, 'last_result': result,
                                     'dry': dry}) + '\n')
        time.sleep(POLL)


if __name__ == '__main__':
    main()
