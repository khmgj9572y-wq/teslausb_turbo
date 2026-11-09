# TeslaUSB car link

Pi-side additions for [TeslaUSB](https://github.com/marcone/teslausb):
event uploads over a dedicated VPN and Moonlight video on a car HDMI input.
Voice runs separately through a phone SIP channel. This repository contains
**templates only**; keep real endpoints, keys, hostnames, credentials, and
vehicle details out of Git.

## Current state

This is a pre-install bundle. The code has local tests; physical recording,
sleeping-car UDC behavior, power under load, and the new image still need
validation. The planned base is Raspberry Pi OS Lite **64-bit Trixie** on a
new SD card; Bookworm 64-bit is a fallback. Preserve the working SD card as
rollback. TeslaUSB's external-drive setup can erase an SSD and needs
`DATA_DRIVE` set to the whole spare disk (at least 64 GB). Follow
[IMAGE_PLAN.md](IMAGE_PLAN.md) and use spare media before connecting an
existing archive drive to a fresh setup run. When the new image passes those
checks, follow [INSTALL_BUNDLE.md](INSTALL_BUNDLE.md) one feature at a time.

## Components

| File | Purpose |
| --- | --- |
| `event_queue.py` | Stage SavedClips and SentryClips from TeslaUSB snapshots into a bounded SSD queue, then upload over WireGuard. |
| `car_policy.py` | Count LTE bytes by the `rndis_host` interface, enforce monthly caps after verified NTP, and identify home Wi-Fi. |
| `gadget_guard.py` | Replug a gadget missed by the car after two failed 30-second checks, at most three times per hour. |
| `car_control.py` | Stop video on cap, undervoltage, link loss, or session timeout; record power flags while active. |
| `heartbeat.py` | POST status to the intake host every five minutes and apply validated video on/off/keep replies. |
| `player.sh` | Start Sunshine's `Desktop` stream with Moonlight Qt; direct RTSP/mpv is a fallback. |
| `video_toggle.py` | Optional GPIO button to start or stop the video service without touch. |
| `preflight.sh` | Read-only OS, USB, HDMI, disk, and power probe. |
| `examples/80-ik41us-rndis.network` | RNDIS-only DHCP profile with LTE route metric 700. |
| `systemd/` | Event, guard, policy, heartbeat, BLE unblock, and video units. |

The uploader intentionally reads TeslaUSB's snapshot links and does not
modify its camera image or archive state. TeslaUSB's normal home Wi-Fi archive
remains the backstop. It snapshots about every 3,480 seconds by default;
shorter intervals require physical recording validation.

## LTE upload behavior

`event_queue.py scan` stages `SavedClips` and `SentryClips` only. The newest
event's `event.json`/`events.json` and `thumb.png` go before front camera and
other MP4s. The sample queue stops at 5 GiB or 45 GiB free space, whichever
comes first; 256 MiB within the queue is reserved for event JSON and
thumbnails. Sent local copies are pruned on a 10-minute cycle. Tune only
after checking actual SSD reserve and LTE use.

The Pi counts received **and** transmitted bytes on whichever interface is
bound to `rndis_host`, even if its name changes across boots. The sample soft
cap is 40 decimal GB: stage/send metadata and thumbnails only, and stop
Moonlight. The sample hard cap is 60 decimal GB: heartbeat only over LTE.
Caps reset on the first UTC month **only after NTP is verified**. The queue
pauses while the home SMB archive is reachable via `wlan0`.

The intake SSH key is expected to run `rrsync -wo` and accept rsync only into
a **staging root**, separate from the final `footage/` archive. The Pi sends
relative `SavedClips/<event>/<file>` and `SentryClips/<event>/<file>` paths;
it does not run a remote shell command. It preserves file mtimes, uses
`--ignore-existing` for repeated uploads to staging, and keeps interrupted
files in `.partial` until they finish. The home intake creates the restricted
staging root before the Pi connects. See
`examples/teslausb-car-link.ini` for **placeholder** connection fields.
`rrsync -no-overwrite` is not part of this design: upstream rrsync refuses
`--partial-dir` when that option is enabled.

The home intake publishes each completed staging file into `footage/` with
an atomic hard link that succeeds only when the final name is absent. On
`EEXIST`, it skips or compares and never overwrites the SMB archive. Home
owns staging cleanup. The staging and final paths must support same-filesystem
hard links; this needs an end-to-end intake test. The Pi counts a file as
delivered when its rsync exits zero and prunes only its local queued copy.

TeslaUSB's CIFS archive also uses rsync with `-a` and therefore normally
skips an existing file with the same size and mtime. It does not use
`--ignore-existing`; if the attributes differ, it may copy the file again.
Verify one LTE-first event followed by a home Wi-Fi archive before relying
on no duplicate transfer.

## Gadget enumeration guard

TeslaUSB's `archiveloop` holds its flock for its entire process lifetime; it
does not expose a short lock around each intentional disconnect. The guard
checks the gadget's UDC file and acts only while it is nonempty. It requires
two bad states 30 seconds apart, checks for a running transition script,
then **rechecks UDC immediately before acting**. It runs TeslaUSB's existing
disable/enable scripts without editing them, waits three seconds between
them, and logs the resulting UDC state after ten seconds to
`/mutable/gadget-guard.log`. Attempt counters live under `/mutable` and are
limited to three per hour. A sleeping car may report `not attached` or
`suspended`; observe this physically before enabling the service by default.

## Network and video

The sample WireGuard peer routes only the home intake and Sunshine hosts and
uses `PersistentKeepalive=25`. Generate the Pi key pair on the Pi at install;
only its public key goes to the home server. Keep the Wi-Fi route preferred
over the RNDIS LTE route. The RNDIS profile matches `usb*` **and** driver
`rndis_host`, so TeslaUSB's temporary `g_ether` gadget is not managed by it.

Moonlight is primary. The sample starts at 1280×720, 30 fps, and 4 Mb/s;
adjust after measuring the HDMI adapter's modes, host encoder, LTE path,
and Pi decoder. The home Sunshine host must show the desired camera or
dashboard stream on an active display/capture session. Moonlight leaves
audio on the host; the phone SIP channel handles AI voice. The video service
is off until started by command or the optional GPIO button. Use the car
video input only while parked.

`max_session_min` defaults to 30. The control timer checks every 30 seconds
and stops video when the limit, soft LTE cap, current undervoltage, missing
uplink/VPN route, stale WireGuard handshake, or stale heartbeat is detected.
The handshake check has a two-minute startup grace and a three-minute age
limit by default. The heartbeat timer sends
uptime, active link, LTE bytes, SSD free, queue depth, snapshot time (only
after NTP), throttle flags, UDC state, and guard replug count. Its JSON reply
can request `video: on`, `off`, or `keep`. Repeated `on` replies do not restart
an expired session; a new command transition or the physical button does.
The heartbeat runs separately with a ten-second network timeout, so it
cannot block the upload worker.
The Pi has no verified gear-state input; the home service must send `video:on`
only when the car is in Park. This remains a commissioning gate.

## Installation gates

1. Follow the pinned-source, spare-drive sequence in [IMAGE_PLAN.md](IMAGE_PLAN.md).
   Back up the working SD image and verify the external SSD can be reused
   without a setup operation that formats it.
2. Fix any undervoltage. The Pi 4 needs a stable 5 V, 3 A supply and its USB
   host ports share a 1.2 A budget. Check `vcgencmd get_throttled` under LTE
   transmit and video load.
3. Validate RNDIS DHCP and route metrics on the installed OS. A profile can
   be tested under `/run/systemd/network` before writing it to `/etc`.
4. Verify WireGuard, the restricted rsync key, home archive interaction, and
   Moonlight on spare media before enabling automatic services.
5. Generate a fresh BLE key on the new image, test a lesser role physically,
   and verify keep-awake after a cold boot. The key is a car credential and
   must stay off the repo and NAS. Install the BLE unblock unit only if the
   Trixie rfkill behavior appears.
6. Confirm `/dev/watchdog0` before enabling the included systemd hardware
   watchdog template. Do not assume loading a module alone activates it.

No service is enabled by this repository. On TeslaUSB, `/` is normally
read-only; use `/root/bin/remountfs_rw` only for deliberate installation
changes and reboot to restore read-only operation. Put state, queues, and
logs under `/run`, `/mutable`, or `/backingfiles`.

## Sources

- [TeslaUSB snapshot implementation](https://github.com/marcone/teslausb/blob/main-dev/run/make_snapshot.sh)
- [TeslaUSB archive loop](https://github.com/marcone/teslausb/blob/main-dev/run/archiveloop)
- [TeslaUSB CIFS archive](https://github.com/marcone/teslausb/blob/main-dev/run/cifs_archive/archive-clips.sh)
- [TeslaUSB external-drive setup](https://github.com/marcone/teslausb/blob/main-dev/doc/Pi4ExternalDriveSetup.md)
- [Moonlight Qt on Raspberry Pi](https://github.com/moonlight-stream/moonlight-docs/wiki/Installing-Moonlight-Qt-on-Raspberry-Pi-4)
- [Sunshine getting started](https://docs.lizardbyte.dev/projects/sunshine/latest/md_docs_2getting__started.html)
- [Rsync option reference](https://rsync.samba.org/ftp/rsync/rsync.1)
- [Rrsync option reference](https://rsync.samba.org/ftp/rsync/rrsync.1)
- [Systemd hardware watchdog](https://www.freedesktop.org/software/systemd/man/latest/systemd-system.conf.html)
