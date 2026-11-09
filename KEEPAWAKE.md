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
- `tesla-control` takes the Bluetooth adapter exclusively (HCI user channel)
  for each command, so BlueZ-based BLE users lose the adapter for that moment.
