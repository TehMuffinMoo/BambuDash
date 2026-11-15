import io
import json
import shutil
import stat
import subprocess
import tempfile
import threading
import unittest
import urllib.request
import zipfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import server
import updater


def archive(entries):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as package:
        for name, content in entries.items():
            package.writestr("BambuDash-release/" + name, content)
    return buffer.getvalue()


class ReleaseTests(unittest.TestCase):
    def test_latest_release_requires_a_stable_version_tag(self):
        release = {"draft": False, "prerelease": False, "tag_name": "v1.2.3"}
        with patch.object(updater, "download", return_value=json.dumps(release).encode()):
            self.assertEqual(updater.latest_release(), "1.2.3")
        for change in ({"prerelease": True}, {"tag_name": "main"}, {"tag_name": "v1.02.3"}):
            with self.subTest(change=change), patch.object(updater, "download", return_value=json.dumps({**release, **change}).encode()):
                with self.assertRaises(updater.UpdateError):
                    updater.latest_release()
        with patch.object(updater, "download", return_value=None):
            self.assertIsNone(updater.latest_release())

    def test_release_archive_rejects_unsafe_and_mismatched_contents(self):
        base = {"VERSION": b"0.2.0\n", "server.py": b"server", "requirements.txt": b"requirements"}
        with patch.object(updater, "download", return_value=archive(base)):
            self.assertEqual(updater.release_files("0.2.0")["server.py"][0], b"server")
        for extra in ({"../outside": b"bad"}, {"public/App.js": b"one", "public/app.js": b"two"}):
            with self.subTest(extra=extra), patch.object(updater, "download", return_value=archive({**base, **extra})):
                with self.assertRaises(updater.UpdateError):
                    updater.release_files("0.2.0")
        with patch.object(updater, "download", return_value=archive(base)):
            with self.assertRaisesRegex(updater.UpdateError, "wrong version"):
                updater.release_files("0.3.0")
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as package:
            for name, content in base.items():
                package.writestr("BambuDash-release/" + name, content)
            link = zipfile.ZipInfo("BambuDash-release/link")
            link.external_attr = (stat.S_IFLNK | 0o777) << 16
            package.writestr(link, "../outside")
        with patch.object(updater, "download", return_value=buffer.getvalue()):
            with self.assertRaisesRegex(updater.UpdateError, "symbolic link"):
                updater.release_files("0.2.0")


class CopiedInstallTests(unittest.TestCase):
    def test_updates_release_files_and_preserves_unmanaged_files(self):
        old = {"VERSION": (b"0.1.0\n", 0o644), "app.py": (b"old\n", 0o644), "removed.txt": (b"old", 0o644)}
        new = {"VERSION": (b"0.2.0\n", 0o644), "app.py": (b"new\n", 0o644), "added.txt": (b"new", 0o644)}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name, (content, _) in old.items():
                (root / name).write_bytes(content.replace(b"\n", b"\r\n"))
            (root / "personal.txt").write_text("keep")
            updater.update_copied_install(root, old, new)
            self.assertEqual((root / "VERSION").read_bytes(), b"0.2.0\n")
            self.assertEqual((root / "app.py").read_bytes(), b"new\n")
            self.assertFalse((root / "removed.txt").exists())
            self.assertEqual((root / "personal.txt").read_text(), "keep")

    def test_rejects_modified_and_conflicting_files_without_overwriting(self):
        old = {"VERSION": (b"0.1.0\n", 0o644), "app.py": (b"old", 0o644)}
        new = {"VERSION": (b"0.2.0\n", 0o644), "app.py": (b"new", 0o644), "added.txt": (b"new", 0o644)}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "VERSION").write_bytes(b"0.1.0\n")
            (root / "app.py").write_bytes(b"custom")
            with self.assertRaisesRegex(updater.UpdateError, "differs"):
                updater.update_copied_install(root, old, new)
            self.assertEqual((root / "app.py").read_bytes(), b"custom")
            (root / "app.py").write_bytes(b"old")
            (root / "added.txt").write_bytes(b"personal")
            with self.assertRaisesRegex(updater.UpdateError, "overwritten"):
                updater.update_copied_install(root, old, new)
            self.assertEqual((root / "added.txt").read_bytes(), b"personal")
            self.assertEqual((root / "VERSION").read_bytes(), b"0.1.0\n")

    def test_failed_write_restores_exact_original_contents(self):
        old = {"VERSION": (b"0.1.0\n", 0o644), "app.py": (b"old\n", 0o644)}
        new = {"VERSION": (b"0.2.0\n", 0o644), "app.py": (b"new\n", 0o644)}
        real_write = updater.write_file
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "VERSION").write_bytes(b"0.1.0\r\n")
            (root / "app.py").write_bytes(b"old\r\n")

            def failing_write(path, content, mode):
                if path.name == "app.py" and content == b"new\n":
                    raise OSError("simulated failure")
                real_write(path, content, mode)

            with patch.object(updater, "write_file", side_effect=failing_write):
                with self.assertRaisesRegex(updater.UpdateError, "original files were restored"):
                    updater.update_copied_install(root, old, new)
            self.assertEqual((root / "VERSION").read_bytes(), b"0.1.0\r\n")
            self.assertEqual((root / "app.py").read_bytes(), b"old\r\n")

    def test_manager_installs_selected_release_and_requests_restart(self):
        old = {"VERSION": (b"0.1.0\n", 0o644), "requirements.txt": (b"same\n", 0o644)}
        new = {"VERSION": (b"0.2.0\n", 0o644), "requirements.txt": (b"same\n", 0o644)}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name, (content, _) in old.items():
                (root / name).write_bytes(content)
            manager = updater.UpdateManager(root)
            manager.latest = "0.2.0"
            restarted = threading.Event()
            with patch.object(updater, "release_files", side_effect=lambda version: old if version == "0.1.0" else new):
                manager.start("0.2.0", restarted.set)
                self.assertTrue(restarted.wait(5))
            self.assertEqual((root / "VERSION").read_bytes(), b"0.2.0\n")
            self.assertEqual(manager.status()["state"], "restarting")
            with self.assertRaises(updater.UpdateError):
                manager.start("0.2.0", restarted.set)


@unittest.skipUnless(shutil.which("git"), "Git is required")
class GitInstallTests(unittest.TestCase):
    def run_git(self, root, *args):
        return subprocess.run(
            ["git", "-C", str(root), *args], check=True, capture_output=True, text=True
        ).stdout

    def test_fast_forward_release_and_reject_dirty_checkout(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source"
            source.mkdir()
            self.run_git(source, "init", "-q")
            self.run_git(source, "config", "user.name", "Test")
            self.run_git(source, "config", "user.email", "test@example.invalid")
            self.run_git(source, "config", "core.autocrlf", "false")
            for name, content in {"VERSION": b"0.1.0\n", "server.py": b"old", "requirements.txt": b"same\n"}.items():
                (source / name).write_bytes(content)
            self.run_git(source, "add", ".")
            self.run_git(source, "commit", "-qm", "initial")
            checkout = root / "checkout"
            subprocess.run(["git", "-c", "core.autocrlf=false", "clone", "-q", str(source), str(checkout)], check=True)
            self.run_git(checkout, "config", "core.autocrlf", "false")
            (source / "VERSION").write_bytes(b"0.2.0\n")
            (source / "server.py").write_bytes(b"new")
            self.run_git(source, "add", ".")
            self.run_git(source, "commit", "-qm", "release")
            self.run_git(source, "tag", "v0.2.0")
            files = {name: ((source / name).read_bytes(), 0o644) for name in ("VERSION", "server.py", "requirements.txt")}
            with patch.object(updater, "REPOSITORY", str(source)):
                (checkout / "server.py").write_bytes(b"custom")
                with self.assertRaisesRegex(updater.UpdateError, "local changes"):
                    updater.prepare_git_install(checkout, "0.2.0", files)
                (checkout / "server.py").write_bytes(b"old")
                with self.assertRaisesRegex(updater.UpdateError, "release changed"):
                    updater.prepare_git_install(checkout, "0.2.0", {**files, "server.py": (b"different", 0o644)})
                updater.prepare_git_install(checkout, "0.2.0", files)
                updater.git(checkout, "merge", "--ff-only", "FETCH_HEAD")
            self.assertEqual((checkout / "VERSION").read_bytes(), b"0.2.0\n")
            self.assertEqual(self.run_git(checkout, "status", "--porcelain"), "")


class UpdateEndpointTests(unittest.TestCase):
    def handler(self, host, origin):
        handler = object.__new__(server.Handler)
        handler.path = "/api/update/install"
        request = json.dumps({"version": "0.2.0"}).encode()
        handler.headers = {"Content-Length": str(len(request)), "Host": host, "Origin": origin}
        handler.rfile = io.BytesIO(request)
        handler.wfile = io.BytesIO()
        handler.send_response = MagicMock()
        handler.send_header = MagicMock()
        handler.end_headers = MagicMock()
        return handler

    def test_install_requires_localhost_and_passes_selected_version(self):
        handler = self.handler("127.0.0.1:8000", "http://127.0.0.1:8000")
        with patch.object(server.updates, "start") as start:
            handler.do_POST()
        handler.send_response.assert_called_once_with(202)
        self.assertEqual(start.call_args.args[0], "0.2.0")
        handler = self.handler("attacker.example:8000", "http://attacker.example:8000")
        with patch.object(server.updates, "start") as start:
            handler.do_POST()
        handler.send_response.assert_called_once_with(403)
        start.assert_not_called()

    def test_confirmed_install_updates_copy_and_requests_server_restart(self):
        old = {"VERSION": (b"0.1.0\n", 0o644), "requirements.txt": (b"same\n", 0o644)}
        new = {"VERSION": (b"0.2.0\n", 0o644), "requirements.txt": (b"same\n", 0o644)}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name, (content, _) in old.items():
                (root / name).write_bytes(content)
            manager = updater.UpdateManager(root)
            manager.latest = "0.2.0"
            httpd = server.ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
            httpd.restart_requested = False
            thread = threading.Thread(target=httpd.serve_forever, daemon=True)
            with patch.object(server, "updates", manager), \
                    patch.object(updater, "release_files", side_effect=lambda version: old if version == "0.1.0" else new):
                thread.start()
                try:
                    port = httpd.server_address[1]
                    request = urllib.request.Request(
                        f"http://127.0.0.1:{port}/api/update/install",
                        data=json.dumps({"version": "0.2.0"}).encode(),
                        headers={"Content-Type": "application/json"},
                    )
                    with urllib.request.urlopen(request, timeout=5) as response:
                        self.assertEqual(response.status, 202)
                    thread.join(5)
                    self.assertFalse(thread.is_alive())
                    self.assertTrue(httpd.restart_requested)
                    self.assertEqual((root / "VERSION").read_bytes(), b"0.2.0\n")
                finally:
                    if thread.is_alive():
                        httpd.shutdown()
                        thread.join(5)
                    httpd.server_close()


if __name__ == "__main__":
    unittest.main()
