# TeslaUSB car link

Pi-side additions for [TeslaUSB](https://github.com/marcone/teslausb):
event uploads over a dedicated VPN and Moonlight video on a car HDMI input.
Voice runs separately through a phone SIP channel. This repository contains
**templates only**; keep real endpoints, keys, hostnames, credentials, and
vehicle details out of Git.

## Current state

This repository contains source and installation templates. Local tests cover
the queue, policy, Wi-Fi controls, and display handoff; hardware acceptance
must be completed on each installation. The base is Raspberry Pi OS
**64-bit Trixie** on a new SD card; Bookworm 64-bit is a fallback. Preserve the working SD card as
rollback. TeslaUSB's external-drive setup can erase an SSD and needs
`DATA_DRIVE` set to the whole spare disk (at least 64 GB). Follow
[IMAGE_PLAN.md](IMAGE_PLAN.md) and use spare media before connecting an
existing archive drive to a fresh setup run. When the new image passes those
checks, follow [INSTALL_BUNDLE.md](INSTALL_BUNDLE.md) one feature at a time.
Use [NEXT_SESSION.md](NEXT_SESSION.md) for the remaining physical checks.

Run `python3 -B tools/check_bundle.py --tests` before publishing an update.
It checks Python and shell syntax, common private artifacts, literal network
addresses, and possible vehicle identifiers, then runs the offline tests.
An optional `--private-terms-file /path/outside/checkout` checks local hostnames,
SSIDs, or other private terms without printing the matched values. Keep that
file outside the repository. These checks supplement a diff review.

## Components

| File | Purpose |
| --- | --- |
| `event_queue.py` | Stage SavedClips and SentryClips from TeslaUSB snapshots into a bounded SSD queue, then upload over WireGuard. |
| `car_policy.py` | Count LTE bytes by the `rndis_host` interface, enforce monthly caps after verified NTP, and identify home Wi-Fi. |
| `gadget_guard.py` | Replug a gadget missed by the car after two failed 30-second checks, at most three times per hour. |
| `car_control.py` | Stop video on cap, undervoltage, link loss, or session timeout; record power flags while active. |
| `heartbeat.py` | POST status to the intake host every five minutes and apply validated video on/off/keep replies. |
| `player.sh` | Start Sunshine's `Desktop` stream with Moonlight Qt; direct RTSP/mpv is a fallback. |
| `video_web.py`, `web/moonlight/` | Authenticated manual Start/Stop controls, session status, and remaining time in both web views. |
| `radio_policy.py`, `radio_services.py` | Permanent-MAC radio roles and a local car hotspot with DHCP leases under `/run`. |
| `airplay_receiver.py`, `display_owner.py`, `dnssd_scope.c` | Hotspot-only UxPlay discovery and Moonlight screen handoff. |
| `video_toggle.py` | Optional GPIO button to start or stop the video service without touch. |
| `preflight.sh` | Read-only OS, USB, HDMI, disk, and power probe. |
| `wifi_admin.py`, `web/`, `portal_session.sh` | Password-protected Wi-Fi tab for both TeslaUSB views, temporary profiles, and a phone-controlled captive-portal browser. |
| `tools/ik41_at_safe.c` | Narrow AT reader for USB mode and PDP APN, with explicit MBIM/RNDIS switching commands. |
| `examples/80-ik41us-rndis.network` | RNDIS-only DHCP profile with LTE route metric 700. |
| `systemd/` | Event, guard, policy, heartbeat, BLE unblock, and video units. |
| `lte_info.py`, `tools/ik41_info.c`, `web/4g/` | Protected 4G status, signal history, APN/SIM/network details, USB information, and on-demand identifiers. |
| `ssh_keepawake.py` | Keep the car awake while SSH is in use; idle sessions stop counting. See [KEEPAWAKE.md](KEEPAWAKE.md). |
| `tools/install_hci0_lock.sh`, `ble_adapter_check.py` | Serialize car-key Bluetooth access and check UART controller assignment at boot. |

The uploader intentionally reads TeslaUSB's snapshot links and does not
modify its camera image or archive state. TeslaUSB's normal home Wi-Fi archive
remains the backstop. It snapshots about every 3,480 seconds by default;
shorter intervals require physical recording validation.

## LTE upload behavior

`event_queue.py scan` stages `SavedClips` and `SentryClips` only. For each event,
the validated `thumb.png` goes first, then a complete `event.json` or
`events.json`, then the front camera and other MP4s. JSON must parse as an
object; a thumbnail must have the PNG signature and final IEND chunk. MP4s
must have the same size and mtime in two consecutive distinct TeslaUSB
snapshots. At the default snapshot cadence of about 3,480 seconds, this can
delay an MP4 by nearly an hour. Measure and safely shorten that cadence on
the spare image before promising video delivery within a few minutes. The
sample queue stops at 5 GiB or 45 GiB free space, whichever
comes first; 256 MiB within the queue is reserved for event JSON and
thumbnails. Sent local copies are pruned on a 10-minute cycle. Tune only
after checking actual SSD reserve and LTE use.

The Pi counts received **and** transmitted bytes on whichever interface is
bound to `rndis_host`, even if its name changes across boots. The sample soft
cap is 40 decimal GB: stage/send metadata and thumbnails only, and stop
Moonlight. The sample hard cap is 60 decimal GB: heartbeat only over LTE.
Caps reset on the first UTC month **only after NTP is verified**. The queue
pauses while the home SMB archive is reachable through the configured Wi-Fi uplink.
It rechecks between files so a batch yields when home Wi-Fi returns.
The sidecar also reads TeslaUSB's existing home-archive ledger and skips files
already recorded there. This prevents an existing SSD's historical clips
from being uploaded again over LTE. A queued file later completed by the
home archive is treated as delivered; only its sidecar copy is pruned.

The intake SSH key is expected to run `rrsync -wo -no-del` and accept rsync only into
a **staging root**, separate from the final `footage/` archive. The Pi sends
relative `SavedClips/<event>/<file>` and `SentryClips/<event>/<file>` paths;
it does not run a remote shell command. It preserves file mtimes, uses
`--ignore-existing` for repeated uploads to staging, and keeps interrupted
files in `.partial` until they finish. The home intake creates the restricted
staging root before the Pi connects. See
`examples/teslausb-car-link.ini` for **placeholder** connection fields.
`rrsync -no-overwrite` is not part of this design: upstream rrsync refuses
`--partial-dir` when that option is enabled.

The home intake claims a completed staging file by rename, then publishes it
into `footage/` with an atomic no-clobber hard link. On `EEXIST`, matching
size and mtime is a duplicate; a difference is a conflict that the home
side alerts on. It never overwrites the SMB archive. Home
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

The **4G** tab is available in both TeslaUSB views. Its collector reads the
IK41's separate vendor AT interface without detaching the RNDIS data driver
or changing modem settings. Live reports refresh about every 30 seconds;
device/APN information is cached for five minutes. A USB-port change or
re-enumeration clears the old device report, and a newly connected stick is
given at least 20 seconds to initialize. Collection and its shared AT lock
have bounded waits; a missing stick or busy interface is reported explicitly.

The page includes operator and registration state, SIM readiness, packet
attachment and active contexts, RSSI and LTE RSRP/RSRQ ranges, signal history,
reported band codes, cellular and Pi addresses, DNS, routes, traffic rates,
error/drop counters, monthly Pi usage and caps, firmware, USB details, and
raw read-only reports. Fields not supplied by the firmware remain unavailable;
band codes are not guessed into frequencies. Signal ranges follow
[3GPP TS 27.007](https://www.etsi.org/deliver/etsi_ts/127000_127099/127007/15.05.00_60/ts_127007v150500p.pdf).

IMEI, IMSI, ICCID, the SIM's phone-number report, and USB serial are requested
only through **Reveal identifiers**. That action requires an authenticated,
same-origin POST; normal polling reads a private cache and does not query
identifiers. There are no reset, USB-mode, APN, firmware, or network-scan
controls. Runtime data stays under `/run`, outside the web root, and no live
values belong in Git. The tab inherits the existing web access policy;
its installer does not rewrite nginx/Caddy authentication or allowlists.

The planned layout assigns USB MT7921AU adapter #1 to client Wi-Fi and
adapter #3 to the AirPlay hotspot. Adapter #2 is reserved for separate
equipment outside this bundle. Onboard Wi-Fi stays available for vehicle
integration or other use. Until cutover, `split_enabled` remains false and
the existing onboard uplink stays in place. Radio roles use permanent
MAC addresses in local `/etc/teslausb-car-radios.json`; neither role relies on
`wlan0`/`wlan1` numbering. The picker binds new station profiles to the uplink
MAC. The cutover helper preserves the home/shop profile priorities and sets
uplink route metric 600, ahead of LTE at 700. It requires an attached, connected,
internet-tested MT7921AU before modifying profiles.
`future_uplink_mac` must explicitly select the USB uplink. With several
identical drivers, the helper never picks the first adapter automatically.
Set `future_hotspot_mac` only for the dedicated USB hotspot. Without that
selector, an uplink cutover leaves hotspot/AirPlay disabled and does not
assign the onboard radio. The hotspot uses 5 GHz by default. The driver has
reported concurrent AP/managed operation on one channel and AP capability
on 5/6 GHz. Verify combinations and permitted channels on the installed
firmware and regulatory configuration before using those options.
Station profiles bound to another radio are preserved during cutover.
`tools/bind_station_profiles.py --apply` binds unbound/current-uplink station
profiles to the current uplink MAC without activating a connection or
changing radio roles; run it with root temporarily writable. The picker
always binds new profiles to the configured uplink.
Both maintenance tools temporarily make NetworkManager's own root mount
writable when its private namespace retained the read-only flag. They
restore that flag afterward, including on failure, without restarting the
manager or changing its protected child mounts.

The hotspot uses a manual address and a separate DHCP server with leases in
`/run/car-hotspot`, avoiding NetworkManager shared-mode writes to read-only
root. It supplies a local AirPlay link without advertising a default gateway
or sharing the Pi's LTE connection. Internet sharing is optional future work.

Packaged UxPlay 1.71.1 runs as the desktop user with V4L2 H.264 decoding and a
fullscreen Wayland sink. Its DNS-SD registrations are scoped to the hotspot
interface by a small library; other Avahi publishers retain their policies.
Receiver ports are also restricted to the hotspot. AirPlay startup is refused
until the two-radio split and hotspot are active. On mirroring connection,
Moonlight yields the display; on disconnect it resumes only if previously
running and still within its original session and policy limits. A manual
Moonlight Stop cancels resumption. AirPlay has no driving-state lockout.
Manual, heartbeat, and GPIO Moonlight starts share the screen-owner lock
with AirPlay takeover. Pressing the GPIO toggle while AirPlay owns the
screen cancels a paused Moonlight session's automatic return.
Audio starts disabled until the HDMI speaker path is tested.
On Trixie, swap is configured as RAM-only zram so its backing file cannot
hold the SD root writable after package installation.

## USB layout, Bluetooth storage, and saved time

Keep the SSD alone on a blue USB 3 port. Put Wi-Fi radios, LTE, and USB
Bluetooth on USB 2. In this layout, USB 3 radio re-enumeration can drop the
SSD connection; the cutover helper rejects a radio linked at USB 3 speed.
Use a powered USB 2 hub before adding multiple AX3000 adapters. The Pi 4's
host ports share a 1.2 A budget; USB over-current can cut every port and take
the dashcam SSD offline. Before a powered hub is available, limit the
Pi-powered radio set to one AX3000 plus LTE and USB Bluetooth, with the
high-draw older USB Wi-Fi adapter unplugged. Check power under load.

| Device | Connection | Bundle role |
| --- | --- | --- |
| SSD | USB 3, alone | Dashcam/music backing storage |
| USB Wi-Fi #1 | Powered USB 2 hub | Uplink, selected by private MAC |
| USB Wi-Fi #2 | Powered USB 2 hub | Reserved for separate equipment |
| USB Wi-Fi #3 | Powered USB 2 hub | AirPlay hotspot, selected by private MAC |
| LTE and USB Bluetooth | USB 2 hub | Mobile data and separate Bluetooth use |
| Onboard Wi-Fi / UART Bluetooth | Built in | Available Wi-Fi / car-key Bluetooth |

Run `tools/install_image_support.sh` after TeslaUSB setup or an update. It
reapplies the command wrapper and installs writable local state support
without restarting Bluetooth or changing network roles. The wrapper selects
the UART controller through sysfs and passes its actual `hciN` to the binary;
caller-supplied adapter flags are preserved. The logical shared lock remains
`/run/lock/hci0.lock` even when controller numbers change. If no UART is found,
the wrapper logs and uses the binary's default, matching the existing fallback.

BlueZ storage is seeded once from the root copy into root-only
`/mutable/bluetooth`, then bind-mounted over `/var/lib/bluetooth`. Existing
pairings and GATT caches on the SSD are never overwritten by reseeding.
The saved clock uses `/mutable/timesync`, owned by `systemd-timesync`, bound
over `/var/lib/systemd/timesync`. A one-minute timer and an ordered shutdown
hook save its timestamp only while the kernel reports NTP synchronization.
The shutdown hook uses a read-only kernel query, so it does not need D-Bus.
See [adjtimex(2)](https://man7.org/linux/man-pages/man2/adjtimex.2.html).
The root copy can still provide an old early-boot lower bound before
timesyncd advances to the SSD timestamp. This is not an RTC; NTP checks still
gate dated state and monthly accounting. The image-support installer can
optionally back up and remove an obsolete legacy saved-clock file when its
old package is absent.

The Wi-Fi picker is served by TeslaUSB's existing authenticated web server.
It lists current Wi-Fi and LTE status, nearby networks, and saved profiles.
New profiles and expiration state live on `/mutable`; root-only keyfile copies
under `/run/NetworkManager/system-connections` are loaded after reboot.
Existing home and shop profiles are shown but cannot be deleted from the tab.
A new network starts with a lower route priority than LTE. After it passes an
internet check, it becomes the preferred route. A captive portal leaves LTE
preferred and offers a temporary browser on the Pi, controlled from the
phone through the authenticated web UI. That browser uses a virtual display,
so the car screen does not need touch input. Its session ends after 15 minutes
and restores the routes. Wi-Fi passwords are never included in command-line
arguments or web responses; root-only profile files hold them on the Pi.

The sample WireGuard peer routes only the home intake and Sunshine hosts and
uses `PersistentKeepalive=25`. Generate the Pi key pair on the Pi at install;
only its public key goes to the home server. Keep the Wi-Fi route preferred
over the RNDIS LTE route. The RNDIS profile matches `usb*` **and** driver
`rndis_host`, so TeslaUSB's temporary `g_ether` gadget is not managed by it.
The narrow IK41 AT utility can read the current USB mode and context 1 APN;
its mode-switch commands should be followed by USB-ID and LTE route checks.

Moonlight is primary. The sample starts at 1280×720, 30 fps, and 4 Mb/s;
adjust after measuring the HDMI adapter's modes, host encoder, LTE path,
and Pi decoder. The home Sunshine host must show the desired camera or
dashboard stream on an active display/capture session. Moonlight leaves
audio on the host; the phone SIP channel handles AI voice. The video service
is off until started by command or the optional GPIO button. Use the car
video input only while parked.

The player checks the tunnel's underlay when a stream starts. The sample uses
4 Mb/s on Wi-Fi and 1.5 Mb/s with 1,024-byte packets on LTE. A stream already
running when the underlay changes keeps its current settings until it
reconnects. The display unit targets the active Wayland desktop; adjust its
user ID and display environment for the installed OS.

Open the **Moonlight** tab in the classic web view, or the **Moonlight** link
in the alternative view. Confirm that the car is parked, then select
**Start Moonlight**. **Stop Moonlight** is always available. These manual
controls keep the existing session, data, power, and network limits; starting
an already running session does not reset its time limit. They do not enable
heartbeat-driven remote video start.

`max_session_min` defaults to zero, which disables the fixed session cutoff.
The home idle watcher owns inactivity timeout, allowing an active voice or
screen session to continue. A positive value enables an optional hard limit.
The control timer checks every 30 seconds and stops video when an enabled
limit, soft LTE cap, current undervoltage, missing
uplink/VPN route, stale WireGuard handshake, or stale heartbeat is detected.
The handshake check has a two-minute startup grace and a three-minute age
limit by default. The heartbeat timer sends
uptime, active link, LTE bytes, SSD free, queue depth, snapshot time (only
after NTP), throttle flags, UDC state, and guard replug count. Its JSON reply
can request `video: on`, `off`, or `keep`. Repeated `on` replies do not restart
an expired session; a new command transition or the physical button does.
The heartbeat runs separately with a ten-second network timeout, so it
cannot block the upload worker.
The Pi has no verified gear-state input. `remote_video_on_enabled` defaults to
false, so heartbeat replies can turn video off but cannot start it. Enable
remote `video:on` only after the home service has a verified Park signal.

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
