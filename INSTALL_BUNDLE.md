# Install the car-link bundle on a tested new image

This procedure starts **after** [IMAGE_PLAN.md](IMAGE_PLAN.md): a new 64-bit
Trixie SD card and spare SSD have passed TeslaUSB recording, Sentry, home
archive, and post-archive USB reconnect tests. Keep the original SD and SSD
untouched until their reuse path is proven. All connection values in this
repository are placeholders; fill the real values locally under `/etc`.

## Stage files without enabling services

The following assumes this repository was copied to `/tmp/teslausb-car-link`
on the new Pi. The TeslaUSB root is normally read-only, so remount it for
installation, then reboot after the first validation to restore read-only.

```sh
sudo /root/bin/remountfs_rw
sudo install -d -m 755 /usr/local/lib/teslausb-car-link
sudo install -m 755 /tmp/teslausb-car-link/ble_prepare.py /usr/local/lib/teslausb-car-link/
sudo install -m 755 /tmp/teslausb-car-link/radio_policy.py /tmp/teslausb-car-link/display_owner.py /usr/local/lib/teslausb-car-link/
sudo install -m 755 /tmp/teslausb-car-link/event_queue.py /tmp/teslausb-car-link/car_policy.py /tmp/teslausb-car-link/car_control.py /tmp/teslausb-car-link/heartbeat.py /tmp/teslausb-car-link/gadget_guard.py /tmp/teslausb-car-link/player.sh /tmp/teslausb-car-link/video_toggle.py /tmp/teslausb-car-link/wifi_admin.py /tmp/teslausb-car-link/video_web.py /tmp/teslausb-car-link/portal_session.sh /tmp/teslausb-car-link/portal_cleanup.sh /usr/local/lib/teslausb-car-link/
sudo install -m 644 /tmp/teslausb-car-link/systemd/* /etc/systemd/system/
sudo install -m 600 /tmp/teslausb-car-link/examples/teslausb-car-link.ini /etc/teslausb-car-link.ini
sudo install -m 600 /tmp/teslausb-car-link/examples/car-video.env /etc/teslausb-car-video.env
sudo systemctl daemon-reload
```

Copy
`web/moonlight/index.html` to `/var/www/html/moonlight/index.html`. The CGI
bridge and `install_wifi_ui.py` add the authenticated Moonlight controls to
both TeslaUSB views. Keep these files together when updating the bundle.

The video service runs as a non-root account and sources its settings file.
Set `/etc/teslausb-car-video.env` to `root:<that account's group>` mode 0640
before starting it; the file may later contain a private fallback stream URL.

For the Wi-Fi tab, install Debian's `xvfb`, `x11vnc`, `websockify`, and
`novnc` packages while root is writable. Copy `web/wifi/` to
`/var/www/html/wifi/` and `web/wifi_api.cgi` to
`/var/www/html/cgi-bin/car-wifi.cgi`. Set `PORTAL_USER` in root-owned,
mode-0600 `/etc/teslausb-car-wifi.env` to the Pi's non-root account. Run
`tools/install_wifi_ui.py` after confirming TeslaUSB's nginx Basic auth is
enabled; it adds the Wi-Fi entry to the classic and alternative views and a
protected local noVNC reverse proxy. Check `nginx -t` before reloading.
Enable `car-wifi-load.service` and `car-wifi-expire.timer` after testing the
page. Temporary profiles are copied from `/mutable` into NetworkManager's
supported `/run` keyfile directory at boot; the original profiles are not
modified. Test a portal with LTE connected before relying on phone sign-in.

Edit `/etc/teslausb-car-link.ini` and `/etc/teslausb-car-video.env`: replace
every `REPLACE_...` value. Set the `User=` placeholder in
`/etc/systemd/system/car-video.service` to the actual non-root account used
for Moonlight pairing. Create `/mutable/car-link/moonlight` and make that
account its owner. Pair Moonlight with `HOME`, `XDG_CONFIG_HOME`,
`XDG_CACHE_HOME`, and `XDG_DATA_HOME` set to the same `/mutable` paths in the
unit; its pairing state must not be written to the read-only root. Run
`systemctl daemon-reload` after changing the unit. No real IP, hostname,
key, VIN, or password belongs in this repository.
On a Wayland desktop, also replace `REPLACE_WITH_OS_UID` in
`car-video.service` with that account's numeric UID so Moonlight can use the
active display socket. The service targets the running desktop rather than a
separate console.

Set both `vpn_interface` fields to the name of the dedicated WireGuard
interface. Put the heartbeat service's actual port in `heartbeat_url`.

Generate a dedicated WireGuard key pair on the Pi and a separate SSH key for
its restricted intake account. Check that the WireGuard private key is nonempty
and valid before deriving its public key. A failed `wg genkey > file` command
can leave an empty file behind; replace only that empty file with a new key,
and preserve a nonempty invalid file for inspection. Keep private keys mode
0600 on the Pi and send only the public keys home. The home intake key's
forced command should be `rrsync -wo -no-del` rooted at a staging directory.
Use `ssh_path = .` in the local config for that restricted root. Confirm the server's SSH host key out of
band; `event_queue.py` requires strict host-key checking. The home host must
accept the sender's `-rtR --ignore-existing --partial-dir=.partial` transfer;
do one harmless file first. Home then hard-links each completed file into
the final archive only if absent, and owns staging cleanup.

## Enable one feature at a time

For the optional MT7921AU/onboard hotspot split, run
`sudo bash tools/install_radios_airplay.sh` from this checkout. It backs up
the installed modules and network settings on the Pi, installs packaged
UxPlay and GStreamer dependencies, builds the scoped DNS-SD library, and
stages the hotspot/receiver units. It leaves the current uplink in place and
does not enable the split or rewrite nginx/Caddy access rules.

When the MT7921AU arrives, first connect a temporary cloned station profile
named `car-uplink-probe` on it, preserving the onboard uplink during the test.
Use metric 650 for that probe so it stays behind the old uplink at 600.
After confirming internet access through the new adapter, run
`rebind_uplink.py --apply` with root writable. The helper saves private
profile backups on the SD, binds saved station profiles and picker profiles
to the new permanent MAC, and provisions a root-only hotspot password at
`/mutable/car-link/radios/hotspot.pass`. Activate the saved uplink profile on
the MT7921AU, verify its route and tunnel, then enable/start
`car-hotspot.service`, `car-hotspot-dhcp.service`, and `car-airplay.service`.
Remove the temporary probe profile after success. The new adapter may get a
different DHCP address; update a home DHCP reservation if one is required.
Restore read-only root after cutover. If connectivity fails, restore the
private profile backups and the onboard uplink before enabling the hotspot.

Verify discovery only on the hotspot, actual phone mirroring, screen handoff,
and HDMI audio before relying on it. Recheck power with SSD writes, LTE
transmission, hotspot traffic, and decoding together. The old high-draw USB
Wi-Fi adapter should be unplugged; the active radios may need a powered hub.

1. With the vehicle parked, run `gadget_guard.py once` only after confirming
   TeslaUSB's gadget is meant to be connected. Observe one home archive cycle
   and inspect `/mutable/gadget-guard.log`. Observe UDC state with the car
   asleep before enabling the service continuously.
2. Install the RNDIS network profile for the modem and verify Wi-Fi has the
   better route metric, DHCP lease state is under `/run`, and DNS resolves
   over both links. Establish the dedicated WireGuard peer; check that only
   the intake and Sunshine hosts route through it.
3. Test `car_control.py tick` and verify LTE byte accounting and power flags.
   Enable `car-control.timer` before automatic uploads so an in-progress
   transfer can be stopped on undervoltage. Test `event_queue.py scan`,
   `status`, and one `send` into the restricted intake. Verify the home CIFS
   archive later skips an LTE-first file whose size and mtime match. Then
   enable the scan, send, and prune timers.
4. Install Moonlight Qt from its inspected official package-repository
   script. Pair as the non-root OS user on a console, test 720p/30 fps and
   the HDMI mode, then start `car-video.service` manually. Only then enable
   the optional GPIO video button and the heartbeat
   timer. Test the 30-minute auto-off, home `off` command, soft data cap, and
   link-drop stop. Before enabling home `on`, the home service must verify
   Park; the Pi has no verified gear-state signal. Keep
   `remote_video_on_enabled = false` in the Pi's config until that gate is
   verified. The heartbeat timer can run with remote `on` disabled.
5. Enable `car-ble-unblock.service` only if cold-boot rfkill reproduces.
   Generate and pair a fresh BLE key on the new image; verify keep-awake and
   remove the old key in the car. Do not copy either private key to the NAS.
   Install `ble_prepare.py` with the unit. Keep the client's session cache
   on `/run/teslausb-ble/session-cache.json` (mode 0600), with
   `/root/.tesla-cache.json` symlinked there. The unit prepares the private
   runtime directory at boot; the root filesystem can remain read-only.
   Set the same path in `TESLA_CACHE_FILE` for commands that inherit the
   TeslaUSB configuration. Keep the actual VIN in the root-only TeslaUSB
   setup file so its native pairing page can read it.
6. Check `/dev/watchdog0` and the driver, then install the watchdog manager
   template and verify a safe reboot test on spare media.

The services and timers are intentionally disabled in this repository. Keep
the actual endpoints and keys in `/etc` on the Pi, while counters/logs live
under `/run`, `/mutable`, or `/backingfiles`.
