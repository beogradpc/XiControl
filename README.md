# XiControl — control Xiaomi devices from your Windows PC

A desktop app that controls Xiaomi / Mi Home devices directly over your home network
(no phone needed), with Xiaomi's cloud as a fallback for devices that can't be reached locally.

## Start

1. Install **Python 3.10+** from python.org (tick *Add python.exe to PATH*).
2. Unzip this folder and double-click **`run.bat`**. The first start installs what it needs (1–2 min).
3. Click **Sign in with Mi account**, scan the QR code with the Mi Home app on your phone,
   pick your devices — done.

Want it without Python? Run **`build_exe.bat`** once; the result is `dist\XiControl\XiControl.exe`
(plus `dist\XiControl.zip`, about 49 MB, to copy to another PC). The script leaves out the parts of Qt the app
never uses and runs `XiControl.exe --selftest` before zipping. `build_exe.bat onefile` makes a single,
slower-starting exe (not trimmed).

**Windows Firewall / network:** set your network to **Private** (Settings → Network → your connection). The first
time you scan, Windows may ask whether to allow Python/XiControl — allow it, otherwise devices can't answer.

**Python broken?** If `run.bat` says Python is missing although it is installed, repair it in
Settings → Apps → Python → Modify → Repair. A damaged `.venv` is rebuilt automatically, and the packages are
updated whenever `requirements.txt` changes.

## Using it

- **Home screen** – every device is a tile showing its status (e.g. *On · Auto*, *Charging · 87% battery*).
  Tap the round button on a tile to switch it on/off; filter by room with the chips at the top, type in the
  **search box** to find a device by name, model or room, and tick **Online only** to hide everything that is
  switched off. **⟳** (or F5) reads every device right away and picks up changes on your Mi account — new
  devices are offered for import, names, rooms and addresses are brought up to date. **☾ / ☀** switches between
  light and dark mode; **⚙** opens settings (appearance incl. "same as Windows", account, region, log).
- **Homes** – with more than one home on your Mi account (e.g. *home* and *work*), the name at the top left is a
  picker: click it to show one home or *All homes* (XiControl remembers the choice). **Right-click** it to edit
  the home on your Mi account: rename the home, add a room, rename or delete a room (its devices stay in the
  home, without a room). The changes show up in the Xiaomi Home app too.
- **Tile size (▦)** – three sizes. *Small*: icon and name, many devices on one screen. *Medium*: status and the
  main readings in one line (temperature, humidity, PM2.5, light, power, door, motion, battery… - whatever the
  device measures). *Large*: up to six readings with their names. Hover over a tile for all readings.
- **Local network or Mi cloud** – the small sign in front of every device's name shows how it is reached right
  now: a **green house** = over your local network, without Xiaomi's servers; a **blue cloud** = through the Mi
  cloud. Hover over it for the reason. The line under the home's name counts both.
- **Notifications (🔔)** – the bell shows how many notifications are new. Its window lists what the Xiaomi Home
  app shows under Notifications (the newest message of every device and the automation log, read from your Mi
  account), together with XiControl's own alerts; *Earlier* shows a device's older messages, *Open* its page.
  Closing the window marks them as read here (the phone app keeps its own count).
- **Right-click a tile** for a menu: open, switch on/off, the device's own commands (e.g. *Start Sweep*,
  *Pause*, *Return to dock*), *Add to an action…*, *Rename…*, *Move to room…*, *Show in tray menu*,
  *Firmware…*, *Remove from XiControl…* and *Delete from Mi account…*. Renaming and moving can also be sent to
  the Xiaomi Home app (tick the box in the dialog; a room has to exist there already).
- **Actions (⚡)** – several commands with one click, e.g. *Leaving home*: all lights off, purifier off, a pause,
  the vacuum starts. Make them in the ⚡ window (or *right-click → Add to an action…*); a step that fails (device
  offline) doesn't stop the others. Pin an action (☆) and it gets its own button above the tiles. The same window
  lists the **manual scenes** of the Xiaomi Home app with a Run button and its **automations**: what starts each
  one and what it does (*door sensor: not closed ▸ run "siren"*), with the same **on/off switch** as in the app.
  *Show: Only <device>* (or *right-click → Automations with this device…*) narrows everything to one device.
  Making and editing automations is done in the Xiaomi Home app.
- **Sharing** – *right-click → Sharing…* shows which other Mi accounts a device is shared with and what they may
  do (control or view only). **Share with a Mi account…** asks for the other person's Mi ID, shows whose account
  it is and sends the invitation (they accept it in their Xiaomi Home app); **Stop sharing** takes the device
  back from the selected account.
- **Firmware** – XiControl checks the firmware of all devices once in a while (one request for all) and marks a
  tile with **⬆** when an update is waiting. *Right-click → Firmware…* (or **+ → Firmware updates…** for all
  devices) shows the installed and the newest version with its release notes, and **Update now** starts the
  update the way the Xiaomi Home app does: the device downloads it itself, installs it and restarts; the window
  shows the progress. Bluetooth devices are updated by the Xiaomi Home app on a phone.
- **Adding and removing devices** – the **+** menu syncs the devices of your Mi account, scans the network,
  adds a device by IP + token, shows **Manage devices…** (one list of everything with its firmware, with *Select
  offline*, to remove many devices at once — from XiControl only, or ticked also from the Mi account) and **Add a
  new camera with a QR code…** (see *Device notes*). *Right-click → Delete from Mi account…* (also in the ⋯ menu
  of a device page) is what *Delete device* does in the Xiaomi Home app; it can't be undone and needs an extra
  tick. Devices shared with you by another account can only be removed from XiControl.
- **Device page** – click a tile. The big round button switches the device; below it are the modes,
  the main sliders (brightness, temperature, time…), action buttons (Start, Pause, Dock…) and live readings.
  Below those, the rest is sorted into plain-language groups — **Settings** (child lock, sounds, display…,
  each with a one-line explanation), **Timers**, **Maintenance** (filter/brush life bars with a Reset button
  that asks before resetting), **Actions** (the device's other commands; the buttons wrap to more rows) and
  **Technical details** (hidden until you click Show — also the commands that only the phone app uses, which
  want internal ids or typed-in JSON). **⋯** (top right) has rename/connection, room, firmware, device info, raw
  commands, *Show in tray menu* and remove. Esc goes back.
- **Tray (notification area)** – closing the window keeps XiControl running in the tray, where it keeps an eye
  on your devices. Click the icon to open the window; right-click for quick on/off switches of the devices
  you marked with *⋯ → Show in tray menu*, and **Quit XiControl** to really exit. Notifications tell you when a
  device goes offline (after two missed checks, and only if it was online before) or comes back, when a
  filter, brush or mop is at 10 % or less (once, until it has been replaced), and when the Mi account needs a
  new sign-in — click a notification to open that device or the sign-in. In **⚙ Settings → Running in the
  background** you can switch off closing to the tray and the notifications, and turn on **Start with
  Windows**, which starts XiControl hidden in the tray when you sign in to Windows (a per-user entry in
  `HKCU\Software\Microsoft\Windows\CurrentVersion\Run`; `main.py --tray` starts it that way by hand).

## How it works

| | |
|---|---|
| **Local control** | Wi-Fi devices are controlled over your LAN with the miIO protocol (UDP 54321, AES-encrypted with each device's token). Fast, works without internet. |
| **Device tokens** | Every device has a secret token. Signing in to your Mi account downloads them automatically (QR code login recommended; password login with captcha and an e-mail or SMS code also supported). |
| **Staying signed in** | The sign-in keeps Xiaomi's long-lived key (passToken), so an expired session is renewed silently. If no devices are found in the chosen region, the other regions are searched automatically. |
| **Auto-generated controls** | For each model the app downloads Xiaomi's public MIoT description (miot-spec.org, cached) and builds switches, sliders, mode pickers, colour pickers and action buttons from it — so new devices work without updates. |
| **Cloud fallback** | Bluetooth/Zigbee devices behind a gateway, many cameras and speakers, or anything that doesn't answer locally, are controlled through Xiaomi's servers while you're signed in. |
| **Older devices** | Roborock vacuums, older Yeelights and plugs that use legacy commands get a dedicated quick-control panel. |
| **Console** | Every device has a console (⋯ → Send commands) for sending any raw command (`get_properties`, `action`, `get_status`, …). |

**Choosing the connection** – Automatic (LAN first, cloud fallback), Local network only or Mi cloud only:

- **one device:** right-click its tile → *Connection* (or *Rename and connection…*);
- **several devices:** *+ → Manage devices…*, tick them, *Connection of the ticked*;
- **all devices at once:** *Settings → Connection*. *As set for each device* is the normal state; the three
  *All devices: …* choices override every device without changing what was set for it, so switching back
  restores it. With *All devices: local network only* nothing goes through Xiaomi's servers - devices that only
  work through the Mi cloud (Bluetooth and most Zigbee devices, most cameras) can't be reached meanwhile.

**Zigbee plugs through their hub:** a Mi Smart Plug (Zigbee) (`lumi.plug.mmeu01`) behind a Mi Control Hub
DGNWG05LM (`lumi.gateway.mieu01`) is read and switched through the hub over the LAN, with the hub's token - no
internet needed. A plug the hub has lost contact with falls back to the Mi cloud, which then reports it offline.
The hub's battery devices (motion sensors, buttons, door sensors) only send events, and the hub passes those to
the Mi cloud alone, so they stay cloud devices; so do Bluetooth devices.

The LAN is only tried for devices whose address is in one of the networks this PC is connected to — a device
at another location (or behind another router) goes straight through the Mi cloud instead of timing out
first. "LAN only" overrides that, e.g. for a network reached through a VPN. In Automatic mode a device whose
LAN address didn't answer uses the cloud first, and the LAN is tried again after 5 minutes, then 10, 20…
up to an hour.

Everything that is reached through the Mi cloud is read **together**: one request asks your account which
devices are online, a second one reads all their values — a home with 100 devices costs a handful of
requests per refresh instead of one per device. Whether a device is *online* is what the Mi account says
(the same as the Xiaomi Home app shows): the cloud also answers for a device that is switched off, with the
values it remembers, so "it answered" is not proof. Devices that don't answer are polled less and less often
(up to every 5 minutes).

At start-up the app scans the network (with the real netmasks, skipping VPN adapters) and fixes device IP
addresses that changed. A scan while signed in also names the devices on your account that aren't added yet,
so they can be imported with their token in one click.

New devices are normally set up (paired) in the Xiaomi Home app on your phone; XiControl then imports them.
Cameras that are set up with a QR code can also be added from the PC (see below).

## Device notes

- **Robot vacuums, lights, plugs, power strips, air purifiers, fans, humidifiers, air fryers:** full control.
- **Plugs with metering:** their page has a **Consumption** card with bars per day, week, month and year, from
  the statistics the Mi cloud keeps (cuco and Aqara/lumi plugs; plugs without a meter, such as chuangmi hmi206,
  have none). A "day" of the cloud starts at midnight in China, i.e. in the afternoon in Europe.
  **Vacuum map:** the newer Xiaomi robot vacuums that keep their map in the Mi cloud (e.g. the Robot Vacuum
  S40) show it on their page — rooms in colours with their names, walls, no-go zones and virtual walls, the
  path of the current cleaning, the dock and the robot with its heading. It is read again every 20 seconds
  while the page is open (⟳ reads it at once). XiControl reads the map the way the Xiaomi Home app's plugin
  for these vacuums does; other vacuums (Roborock, Dreame…) don't show a map yet.
- **Cameras:** settings (power, night vision, motion detection, etc.) work, and the camera page has one
  **▶ Watch live video** button. XiControl picks the best way the camera offers and falls back to the next
  one by itself (the window also lets you choose):
  - **RTSP** – if a camera has its own RTSP stream (some models, custom firmware), enter it with **Edit**.
  - **Mi cloud (WebRTC)** – newer cameras and doorbells (roughly 2023 on; the ones that also work with Amazon
    Alexa). The Mi cloud only passes the connection details; the video then flows directly between the
    camera and this PC, so it is close to real time (not 10–20 s behind like HLS). Works for cameras at
    another location too.
  - **Local network (go2rtc)** – straight from a camera in the same network as this PC.
  - **Mi cloud (HLS)** – for cameras that support Google Home; 10–20 s behind.

  **Battery cameras and doorbells** sleep to save power: XiControl wakes them first with the command the
  Xiaomi Home app uses for that model, so the first picture takes 10–15 s. Closing the window (or quitting
  the app) tells the camera right away that nobody is watching.

  WebRTC and the local way use the free open-source [go2rtc](https://github.com/AlexxIT/go2rtc) helper.
  XiControl downloads it on first use (after asking, and only if the SHA-256 checksum published on GitHub
  matches), runs it on `127.0.0.1` only, and passes it your sign-in key on the command line (never written to
  disk).

  **Recordings** – the same page lists the motion / doorbell clips of the last 3 days that are kept in the Mi
  cloud; **▶ Play** shows one. (The app downloads and decrypts the clip itself and plays it from memory —
  nothing is written to disk.) Cameras without cloud storage only list their events; their videos stay on the
  camera and can be watched in the Xiaomi Home app.

  The video window has sound on/off (off by default), snapshots (saved to *Pictures\XiControl*), full screen
  (double-click or F11) and reconnects by itself when the stream drops. Cameras are imported as
  "Mi cloud only", because most of them don't answer on the LAN.
- **Adding a new camera with a QR code** *(experimental)* – **+ → Add a new camera with a QR code…**: enter
  the Wi-Fi name and password, reset the camera and hold the QR code shown on the screen in front of its
  lens. XiControl asks Xiaomi for a pairing key, waits until the camera reports in and imports it. The Wi-Fi
  password is only put into the QR code on the screen — it is not saved or sent anywhere. This follows what
  the Xiaomi Home app does, but could not be tried with a real camera yet; if a camera doesn't accept the
  code, add it with the phone app and use **+ → Sync devices from Mi account**. Devices paired over Bluetooth or
  their own Wi-Fi hotspot (most non-camera devices) still need the phone app.
- **Speakers:** Xiaomi *Wi-Fi smart speakers* that appear in Mi Home can be controlled (volume, playback,
  etc., usually via cloud). Plain **Bluetooth speakers** aren't on the network at all, so no network app can
  control them — pair them with the PC over Bluetooth instead.
- **Region:** choose the same region as in the Mi Home app (Profile → Settings → Region). Europe (`de`) for Serbia.
  Changing it in Settings while signed in switches the server right away.

## Privacy & files

Settings and the device list are stored in `%APPDATA%\XiControl\config.json`. Device tokens, camera stream
addresses and the Mi account session are **encrypted with Windows DPAPI**: only your Windows account on this
PC can read them (older plain-text files are encrypted automatically on the first start). Your password is
never saved. The previous good copy is kept as `config.json.bak`; a damaged file is set aside and the backup
is used instead. *Settings → Sign out* removes the session.

`%APPDATA%\XiControl\xicontrol.log` records what went wrong (keys are masked) — *Settings → Open log*.
Only one XiControl runs at a time; starting it again brings the open window to the front.

## Project layout

```
main.py               start the app (log file, start-up errors, single instance)
xc/miio.py            local miIO protocol + network discovery
xc/cloud.py           Mi account login (QR / password / 2FA), session renewal, device list, cloud control,
                      rename / delete on the account, camera events
xc/spec.py            MIoT spec download, cache and parsing
xc/device.py          one device: chooses LAN or cloud (by network, with back-off; one choice for all devices)
xc/gateway.py         Zigbee devices behind a Mi Control Hub: read and switched through the hub over the LAN
xc/camera.py          camera video: wake-up, WebRTC bridge for go2rtc, HLS, recordings (local endpoint)
xc/pairing.py         QR code for adding a new camera; gui/pair_dialog.py shows it
xc/energy.py          consumption statistics of plugs; gui/energy.py draws the chart
gui/notifications.py  the Notifications window; gui/sharing.py sharing a device, who it is shared with
xc/vacuum_map.py      vacuum maps: where the file is, decrypting and reading it; gui/vacuum_map.py draws it
xc/actions.py         actions made in XiControl (steps run one after the other); gui/actions_window.py
gui/firmware.py       firmware versions and updates; gui/device_menu.py the right-click menu of a tile
gui/hub.py            live state of all devices; reads the cloud devices together
xc/config.py          settings file; xc/secure.py encrypts the secrets in it (DPAPI)
xc/log.py             log file with secrets masked
xc/alerts.py          when a change deserves a notification; xc/autostart.py the start-with-Windows entry
gui/tray.py           tray icon, quick switches and notifications; gui/icon.py draws the app icon
xc/catalog.py         categories and legacy-device profiles
gui/                  PySide6 user interface
tests/                python -m pytest tests   (fake_device.py / fake_home.py simulate devices;
                      gui_smoke.py walks through the UI offscreen without touching your settings)
```

Not affiliated with Xiaomi. Cloud login follows the same approach as the open-source
Xiaomi-cloud-tokens-extractor, Home Assistant's xiaomi_miot integration and go2rtc.
