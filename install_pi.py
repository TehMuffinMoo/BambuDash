import os
import shutil
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent


def quoted_path(path):
    value = str(path)

    if any(character in value for character in ('\n', '\r', '"', '\\', '%', '$', '`')):
        raise ValueError(
            "The project path contains characters unsupported by the launcher. "
            "Move it to a simpler path."
        )

    return f'"{value}"'


def install():
    if sys.platform != "linux":
        raise RuntimeError(
            "Run this installer on Raspberry Pi OS, not on the development computer."
        )

    if not (shutil.which("chromium") or shutil.which("chromium-browser")):
        raise RuntimeError(
            "Chromium is required. Install it using your Raspberry Pi OS package manager."
        )

    if not shutil.which("systemctl"):
        raise RuntimeError("A systemd user session is required.")

    # Paths used by systemd must NOT have shell-style quotes.
    root = str(ROOT)
    python = str(ROOT / ".venv" / "bin" / "python")
    server = str(ROOT / "server.py")

    # Paths used by the desktop launcher need shell-safe quoting.
    script = quoted_path(ROOT / "kiosk.sh")

    config = Path(
        os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")
    )

    service = config / "systemd" / "user" / "bambudash.service"
    desktop = config / "autostart" / "bambudash.desktop"
    legacy_service = service.with_name("layer-touch.service")
    legacy_desktop = desktop.with_name("layer-touch.desktop")

    print("Creating Python virtual environment...")

    subprocess.run(
        [sys.executable, "-m", "venv", str(ROOT / ".venv")],
        check=True
    )

    print("Installing Python dependencies...")

    subprocess.run(
        [
            str(ROOT / ".venv" / "bin" / "pip"),
            "install",
            "-r",
            str(ROOT / "requirements.txt")
        ],
        check=True
    )

    if not Path(python).is_file():
        raise RuntimeError("Virtual environment Python executable was not created.")

    if not Path(server).is_file():
        raise RuntimeError("server.py could not be found.")

    if not (ROOT / "kiosk.sh").is_file():
        raise RuntimeError("kiosk.sh could not be found.")

    service.parent.mkdir(parents=True, exist_ok=True)
    desktop.parent.mkdir(parents=True, exist_ok=True)

    print("Creating systemd service...")

    service.write_text(f"""[Unit]
Description=BambuDash cloud dashboard
Wants=network-online.target
After=network-online.target

[Service]
Type=simple
WorkingDirectory={root}
ExecStart={python} {server}
Environment=PYTHONUNBUFFERED=1
Restart=on-failure
RestartSec=5

[Install]
WantedBy=default.target
""")

    print("Reloading systemd...")

    subprocess.run(
        ["systemctl", "--user", "daemon-reload"],
        check=True
    )

    print("Validating systemd service...")

    subprocess.run(
        [
            "systemd-analyze",
            "--user",
            "verify",
            str(service)
        ],
        check=True
    )

    if legacy_service.exists() or legacy_service.is_symlink():
        print("Disabling the previous Layer Touch service...")
        subprocess.run(
            ["systemctl", "--user", "disable", "--now", "layer-touch.service"],
            check=True
        )
        legacy_service.unlink()
        subprocess.run(["systemctl", "--user", "daemon-reload"], check=True)

    legacy_desktop.unlink(missing_ok=True)

    print("Creating desktop autostart entry...")

    desktop.write_text(f"""[Desktop Entry]
Type=Application
Name=BambuDash
Exec=/bin/sh {script}
Terminal=false
X-GNOME-Autostart-enabled=true
""")

    print("Enabling and starting BambuDash...")

    subprocess.run(
        [
            "systemctl",
            "--user",
            "enable",
            "--now",
            "bambudash.service"
        ],
        check=True
    )

    print()
    print("BambuDash installed successfully!")
    print("The dashboard service is enabled and running.")
    print("Log out and back in, or reboot, to launch the kiosk.")


if __name__ == "__main__":
    try:
        install()

    except (RuntimeError, ValueError, subprocess.CalledProcessError) as error:
        print(f"\nInstallation failed: {error}", file=sys.stderr)
        sys.exit(1)
