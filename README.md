# BambuDash

BambuDash is a local, read-only dashboard for Bambu Lab printers. It uses Bambu Cloud to discover printers and receive status updates; LAN-only mode is not required.

## Features

- Desktop dashboard and a touchscreen interface designed for 800 × 480 displays.
- Current print, progress, estimated completion time, layer, and print thumbnail.
- Nozzle, bed, and chamber temperatures, plus E1 air quality and purifier fan mode.
- Printer alerts, print stage and speed, fans, Wi-Fi, nozzle, light, camera, and reported USB storage status.
- AMS units, filament slots, and active filament.
- Light and dark themes, and an optional demo mode with three selectable printers.
- Version information and in-app updates from stable GitHub releases.

Available readings depend on the printer and its firmware. The dashboard checks for updates every five seconds. Local P2S print thumbnails require network access to the printer and its media; cloud print thumbnails come from Bambu Cloud.

## Installation

### Standard installation

Create a Python virtual environment, install dependencies, and start the server:

```sh
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python server.py
```

On Windows, use:

```powershell
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\python.exe server.py
```

Open http://127.0.0.1:8000 for the desktop dashboard or http://127.0.0.1:8000/touch.html for the touchscreen view. Select **Connect printer**, choose your region, and sign in with an email verification code or an existing Bambu Cloud access token.

### Updates

Open **Settings** to see the installed version, check for a stable GitHub release, and install an available update. BambuDash also checks automatically while a dashboard is open. Installing an update restarts the server; a cloud session must be reconnected unless it was remembered.

Git checkouts must have a clean working tree and the release must be a fast-forward of the current branch. Copied installations are compared against their installed release before updating; modified files and conflicting local files are not overwritten. Installations from before versioned releases need to be updated manually once before they can use in-app updates.

If an update changes dependencies, they are installed before the application files are updated. A failed update may therefore leave the Python environment changed; check the error and repair the environment if needed.

For each release, update `VERSION` and [CHANGELOG.md](CHANGELOG.md), commit the changes, and publish a GitHub release with a matching tag such as `v0.1.0`. In-app updates use published stable releases, not drafts, prereleases, or unreleased commits.

### Raspberry Pi touchscreen

1. Install Raspberry Pi OS with Desktop, enable desktop auto-login, and configure the display and touch hardware.
2. Install the prerequisites: `sudo apt install python3 python3-venv python3-pip chromium curl`.
3. Copy the project to its permanent location on the Pi, excluding `.venv` and `__pycache__`.
4. Run `python3 install_pi.py` as the desktop user, not as root. The installer creates a virtual environment, user service, and kiosk autostart entry. Port 8000 must be free.
5. Log out and back in or reboot, then open **Settings** on the touchscreen to connect your printer.

To exit the kiosk, press `Alt+F4` on an attached keyboard. To stop the service, run `systemctl --user stop bambudash.service`. To disable automatic startup, run `systemctl --user disable --now bambudash.service` and remove `~/.config/autostart/bambudash.desktop`.

When upgrading an existing Layer Touch installation, run the installer again as the same desktop user. It removes the old service and autostart entry and migrates an existing remembered session; an existing BambuDash session takes precedence.

## Important information

- The server listens only on localhost and is intended for one local user, not public deployment.
- Credentials stay in memory unless **Remember this session on this device** is selected. This stores the cloud token unencrypted in `~/.config/bambudash/session.json`, accessible to the OS user. Disconnecting removes the saved token. If `XDG_CONFIG_HOME` is set, it replaces `~/.config`.
- Bambu Cloud interfaces are unofficial and may change or be blocked. Some account security configurations may require an existing access token.
