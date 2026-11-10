# Hardware acceptance and remaining setup

Offline checks do not establish physical recording, radio performance, or
screen behavior. Keep actual addresses, hostnames, SSIDs, VINs, and keys on
the Pi; record installation-specific evidence privately, outside this repo.
Updates prepared offline must be installed and checked on the Pi before
being described as deployed.

## Start with the existing installation

1. Check that root is read-only, both SSD filesystems are mounted, the car
   sees the dashcam drive, and a familiar USB music track plays.
2. Save a short clip. Confirm a new snapshot contains a playable copy.
   Check power flags while the SSD and LTE modem are both active.
3. Install only the reviewed sidecar files that changed. Preserve backups
   on the Pi, install imported modules before their consumers, then restore
   read-only root. Do not rerun the TeslaUSB installer or disk-image setup
   on an existing SSD.

## BLE keep-awake and USB reconnect

- After an agreed cold boot, confirm Bluetooth is powered, the existing Pi
  key is still paired, and the mode-0600 session cache lives under `/run`.
- Run a short archive cycle. With `SENTRY_CASE=3`, TeslaUSB should start its
  periodic charge-port-close task for the archive and stop it afterward.
  Confirm the background task is gone after completion. Sentry Mode should
  remain unchanged.
- Confirm the car's dashcam drive returns after the normal archive replug.
  Record UDC state once while awake and once after the car actually sleeps.
  A desktop being idle does not establish that the car is sleeping.
- Enable the gadget guard only after those observations. Exercise an archive
  cycle and inspect its private log. It must ignore intentional disconnects,
  require two bad observations, and honor the three-per-hour limit.

## Event delivery and video controls

- Away from home Wi-Fi, save a new event and check that its thumbnail lands
  before event JSON, followed by front and other camera clips. Confirm the
  home notification and that the published footage plays.
- Measure the delay through two distinct snapshots for MP4s. Set the
  snapshot interval only after checking uninterrupted car recording and SSD
  space use; local unit tests cannot establish the delivery deadline.
- Test an LTE-first event followed by a home archive. The home publisher
  should report matching duplicates, with no conflicts or changed files.
- Return to home Wi-Fi during a queued batch. The current transfer may
  finish; the worker must pause before starting the next unarchived file.
- Use the protected Moonlight Start/Stop controls. Check picture framing,
  moving video over LTE, the session countdown, and automatic stop at the
  configured limit. Check the home `off` command separately. Leave remote
  `on` disabled until its home-side prerequisites are verified.

## USB uplink and AirPlay, after the new adapter arrives

Follow the staged cutover in `INSTALL_BUNDLE.md`. First test a temporary
station profile on the USB adapter while keeping the onboard uplink.
Then rebind station profiles by permanent MAC and verify the route and
tunnel before starting the onboard hotspot.

Check all of the following with a phone:

- The hotspot uses writable lease storage and stays up after reboot.
- The picker creates station profiles on the USB uplink, including a hidden
  network. A wrong password leaves the LTE control path available.
- Captive-portal sign-in works through the protected Pi browser. Close it
  afterward and confirm its temporary routing is removed. Verify temporary
  network expiry and that permanent protected profiles cannot be deleted.
- AirPlay is discoverable through the hotspot, and is not advertised on the
  station, LTE, or tunnel interfaces. An actual client can mirror to HDMI.
- Connecting AirPlay pauses Moonlight; disconnecting resumes only a prior,
  unexpired, still-allowed session. Manual Stop prevents resumption. A
  simultaneous Moonlight Start must not take the screen from AirPlay.
- Check HDMI audio before enabling receiver audio. Recheck power with
  mirroring, LTE traffic, and SSD writes together.

## Historical footage backfill window

Keep this separate from the live event queue. Begin only in the agreed
overnight power and home-Wi-Fi window.

- Re-inventory the missing destination files. Use immutable snapshot sources
  and preserve car-relative paths and mtimes. Do not clear or edit TeslaUSB's
  delivery ledger to force a bulk copy.
- Require a Wi-Fi underlay throughout the job; pause on a network change.
  Use resumable partial files and the existing staging publisher's no-clobber
  rules. Never delete or overwrite final NAS footage.
- Keep the car awake only while the backfill runs, and stop that task when
  the job ends or pauses. Leave enough SSD reserve for new recordings.
- Confirm files reached the final NAS tree, not merely the staging area.
  Compare the final inventory and sizes, inspect any conflicts, and play
  representative clips before declaring the backfill complete.

## Watchdog and rollback

Check `/dev/watchdog0` and its actual driver before installing the manager
template. Agree on a reboot window before activating it. Test forced-hang
recovery only with spare media and explicit approval; preserve the original
media and its rollback path. Prepare a credential-free recovery backup after
the hardware checks. Exclude BLE, tunnel, intake, and SMB credentials; keep
their originals only on the Pi and document re-enrollment for recovery.
