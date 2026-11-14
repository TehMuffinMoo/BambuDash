import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import install_pi


class InstallerTests(unittest.TestCase):
    def test_upgrade_disables_old_service_and_is_repeatable(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "project"
            config = Path(directory) / "config"
            (root / ".venv" / "bin").mkdir(parents=True)
            (root / ".venv" / "bin" / "python").touch()
            (root / "server.py").touch()
            (root / "kiosk.sh").touch()
            legacy_service = config / "systemd" / "user" / "layer-touch.service"
            legacy_desktop = config / "autostart" / "layer-touch.desktop"
            legacy_service.parent.mkdir(parents=True)
            legacy_desktop.parent.mkdir(parents=True)
            legacy_service.touch()
            legacy_desktop.touch()
            with patch.object(install_pi, "ROOT", root), patch.object(install_pi.sys, "platform", "linux"), \
                    patch.dict(os.environ, {"XDG_CONFIG_HOME": str(config)}), \
                    patch.object(install_pi.shutil, "which", return_value="/usr/bin/tool"), \
                    patch.object(install_pi, "quoted_path", return_value='"/project/kiosk.sh"'), \
                    patch.object(install_pi.subprocess, "run") as run:
                install_pi.install()
                calls = [call.args[0] for call in run.call_args_list]
                disable = ["systemctl", "--user", "disable", "--now", "layer-touch.service"]
                enable = ["systemctl", "--user", "enable", "--now", "bambudash.service"]
                self.assertLess(calls.index(disable), calls.index(enable))
                self.assertFalse(legacy_service.exists())
                self.assertFalse(legacy_desktop.exists())
                service = config / "systemd" / "user" / "bambudash.service"
                desktop = config / "autostart" / "bambudash.desktop"
                self.assertIn("Description=BambuDash", service.read_text())
                self.assertIn("Name=BambuDash", desktop.read_text())
                run.reset_mock()
                install_pi.install()
                self.assertNotIn(disable, [call.args[0] for call in run.call_args_list])

    def test_failed_validation_keeps_old_installation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "project"
            config = Path(directory) / "config"
            (root / ".venv" / "bin").mkdir(parents=True)
            (root / ".venv" / "bin" / "python").touch()
            (root / "server.py").touch()
            (root / "kiosk.sh").touch()
            legacy_service = config / "systemd" / "user" / "layer-touch.service"
            legacy_desktop = config / "autostart" / "layer-touch.desktop"
            legacy_service.parent.mkdir(parents=True)
            legacy_desktop.parent.mkdir(parents=True)
            legacy_service.touch()
            legacy_desktop.touch()

            def run(command, **kwargs):
                if command[0] == "systemd-analyze":
                    raise subprocess.CalledProcessError(1, command)

            with patch.object(install_pi, "ROOT", root), patch.object(install_pi.sys, "platform", "linux"), \
                    patch.dict(os.environ, {"XDG_CONFIG_HOME": str(config)}), \
                    patch.object(install_pi.shutil, "which", return_value="/usr/bin/tool"), \
                    patch.object(install_pi, "quoted_path", return_value='"/project/kiosk.sh"'), \
                    patch.object(install_pi.subprocess, "run", side_effect=run):
                with self.assertRaises(subprocess.CalledProcessError):
                    install_pi.install()
            self.assertTrue(legacy_service.exists())
            self.assertTrue(legacy_desktop.exists())
            self.assertFalse((config / "autostart" / "bambudash.desktop").exists())


if __name__ == "__main__":
    unittest.main()
