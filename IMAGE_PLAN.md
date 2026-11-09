# New SD image plan

Target: stock Raspberry Pi OS Lite **64-bit Trixie**, TeslaUSB generic
installer with `norootshrink`, and Moonlight Qt. The working SD card stays
untouched as rollback. Use a **spare SSD of at least 64 GB** during first
setup: TeslaUSB's
external-drive setup can repartition/erase a selected USB drive. Do not
connect the existing car archive SSD to a fresh setup run until a tested
non-destructive reuse procedure exists.

## Source pin

TeslaUSB `main-dev` was inspected at commit
`fb4ed15b46dc41a2ec7c8a7a2fe0783ce1d2ffd9`. At that commit,
`setup/generic/install.sh` has Git blob SHA-1
`61390f33af915575b94a61a3b57c18ae5306669d`. It contains the
`sntp`/`ntpdig` fallback needed on Trixie. The generic installer itself
contains hard-coded `main-dev` fetch URLs; fetching only that first script
at a commit does **not** pin the subsequent downloads.

`tools/prepare_pinned_installer.py` fetches the installer at the commit,
verifies its Git blob hash, replaces its `main-dev` fetch references with
that commit, and writes an inspectable script. It does not execute setup.
After inspection, set `export BRANCH=` to the same commit in TeslaUSB's
setup variables before running its follow-up `/etc/rc.local` setup. Confirm
every fetched file resolves to this commit during spare-media testing.

## Staged validation

1. Image a **new** SD card with Raspberry Pi OS Lite 64-bit Trixie. Record
   the image version and checksum. Keep the old SD card physically separate.
2. Connect a spare SSD. Prepare and inspect the pinned generic installer;
   invoke it with `norootshrink` only after confirming the selected disk is
   the spare. In TeslaUSB's setup variables set `DATA_DRIVE` to the **whole
   spare disk**, not a partition, before running `/etc/rc.local`. At the pinned
   commit, `DATA_DRIVE` selects `/mutable` and `/backingfiles`; leaving it
   unset selects the SD card. `norootshrink` does not select the data disk.
   Confirm the actual TeslaUSB source commit after installation.
3. Prove the USB gadget, manual/honk saves, Sentry recording, home Wi-Fi SMB
   archive, music sync, and post-archive re-enumeration on spare media.
4. Validate the gadget guard and then RNDIS LTE, Wi-Fi/LTE routes, DNS,
   WireGuard, restricted intake rsync, Moonlight, and heartbeat one at a time.
5. Back up and inspect the original SSD. Develop a separate, read-only
   rehearsal for reusing its XFS `/backingfiles` and `/mutable` layout.
   Do not run TeslaUSB setup against it. Only attach it to the new SD image
   after the rehearsal demonstrates no partitioning/formatting.

## BLE keep-awake

The new image gets a **fresh** car BLE key generated locally. Never copy the
old private key or store the new private key in this repository, the NAS, or
a backup. Keep `/root/.ble` mode 700 and `key_private.pem` mode 600. Pair the
new public key physically at the car, verify `wake`, Sentry Mode on/off, and
charge-port-close (the keep-awake commands in use), then
remove the old Pi key from the car's Locks/key list. A `driver` role may be
less privileged than `owner`, but its compatibility with the exact commands
must be physically tested; do not assume it works. The included
`car-ble-unblock.service` addresses the reported Trixie rfkill case and must
be verified after a cold boot.

## Other gates

- Moonlight's official feed lists Raspberry Pi/Trixie ARM64 package 6.2.0-1.
  Inspect its repository setup script before running it; do not pipe it
  directly to a shell.
- Confirm `/dev/watchdog0` and the `bcm2835_wdt` driver before installing
  `examples/10-car-watchdog.conf.template` into systemd's manager config.
- Do not date logs or reset the LTE monthly counter until NTP is verified.
- The car-side SIP audio path is outside this image.

## Source checks

- [TeslaUSB generic installer](https://github.com/marcone/teslausb/blob/fb4ed15b46dc41a2ec7c8a7a2fe0783ce1d2ffd9/setup/generic/install.sh)
- [TeslaUSB external SSD warning](https://github.com/marcone/teslausb/blob/main-dev/doc/Pi4ExternalDriveSetup.md)
- [TeslaUSB 32-bit timestamp report](https://github.com/marcone/teslausb/issues/1007)
- [TeslaUSB BLE rfkill report](https://github.com/marcone/teslausb/issues/1019)
- [TeslaUSB Sentry recording report](https://github.com/marcone/teslausb/issues/1058)
- [Moonlight Trixie ARM64 package](https://cloudsmith.io/~moonlight-game-streaming/repos/moonlight-qt/packages/)
