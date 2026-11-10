# Keep the car awake while SSH is in use

`tools/install_ssh_keepawake.sh [--dry]` installs `ssh-keepawake.service`
(`ssh_keepawake.py` -> `/usr/local/sbin/ssh-keepawake`).

Every 30 s it looks at SSH sessions (logind `Service=sshd`, `Remote=yes`,
active, older than 60 s). A session is **in use** if, in the last 15 minutes,
its terminal had input or output, its processes used at least 2 s of CPU
(a running job without a terminal), or it logged in. While any session is in
use it sends TeslaUSB's keep-awake nudge (`tesla-control ... charge-port-close`
over BLE) every 4 minutes, and skips a nudge while TeslaUSB's own keep-awake
task runs. Idle sessions are never closed: they stop counting and the car can
sleep as usual.

- The nudge closes an open charge-port door when no cable is plugged in (the
  same command TeslaUSB's keep-awake uses).
- Requirements: TeslaUSB `tesla-control`, a paired key in `/root/.ble`, and
  `export TESLA_BLE_VIN=...` in `/root/teslausb_setup_variables.conf` (never
  commit the VIN).
- `sudo ssh-keepawake --status` prints the last decision;
  `journalctl -u ssh-keepawake` shows session/nudge changes.

## Sharing the Bluetooth adapter (hci0)

`tesla-control` (go-ble) opens the HCI **user channel**: for each command it
takes hci0 away from BlueZ (`/org/bluez/hci0` disappears for about a second,
longer on retries). A BlueZ user that is mid-exchange at that moment (bleak,
`bluetoothctl`) loses its adapter and connection.

`tools/install_hci0_lock.sh` puts a small wrapper in front of
`/root/bin/tesla-control` (the binary moves to `tesla-control.real`). Every
caller (TeslaUSB keep-awake, the web UI's pairing/status buttons,
ssh-keepawake) then waits for `/run/lock/hci0.lock` (root:bluetooth 0660,
created by tmpfiles) for up to 120 s (`TESLA_CONTROL_LOCK_WAIT`), and holds it
until the command exits. BlueZ scripts take the same lock around their BLE
exchange:

```python
import fcntl
lock = open('/run/lock/hci0.lock', 'a')
fcntl.flock(lock, fcntl.LOCK_EX)   # blocks while tesla-control runs
...                                 # BLE work through BlueZ
lock.close()
```

Keep that hold short (seconds): keep-awake nudges wait behind it. A TeslaUSB
setup run that reinstalls `tesla-control` removes the wrapper; ssh-keepawake
logs a warning at start, and re-running the installer restores it
(`--remove` undoes it). For full isolation, give the BlueZ user its own USB
Bluetooth adapter (hci1) and leave hci0 to `tesla-control`.

After an upstream reinstall, the lock installer preserves the older `.real`
binary in a root-only local backup directory and wraps the newly installed
binary. It fails if root cannot be returned to read-only.

Install `ble_adapter_check.py` alongside `ble_prepare.py` in the sidecar
library directory when updating `car-ble-unblock.service`. The boot check
logs controller numbers and UART/USB transport only, without MAC addresses.
If UART is no longer hci0, it warns and prevents the power-on step from
targeting a different controller. The power-on step selects the hci0 BlueZ
object explicitly and takes the same shared lock as the command wrapper.

