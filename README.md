# BambuDash

BambuDash is a local, read-only dashboard for Bambu Lab printers. It uses Bambu Cloud to discover printers and receive status updates; LAN-only mode is not required.

## Features

- Desktop dashboard and a touchscreen interface designed for 800 × 480 displays.
- Current print, progress, estimated completion time, layer, and print thumbnail.
- Nozzle, bed, and chamber temperatures, plus E1 air quality and purifier fan mode.
- AMS units, filament slots, and active filament.
- Light and dark themes, and an optional demo mode.

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
