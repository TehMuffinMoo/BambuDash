import unittest
import tempfile
import ftplib
import hashlib
import io
import json
import struct
import threading
import urllib.error
import zipfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import printer_media
import server
from server import merge_dict


class CloudRequestTests(unittest.TestCase):
    def test_send_code_accepts_empty_successful_response(self):
        response = io.BytesIO(b"")
        handler = object.__new__(server.Handler)
        handler.path = "/api/send-code"
        request = json.dumps({"email": "test@example.com", "region": "global"}).encode()
        handler.headers = {"Content-Length": str(len(request))}
        handler.rfile = io.BytesIO(request)
        handler.wfile = io.BytesIO()
        handler.send_response = MagicMock()
        handler.send_header = MagicMock()
        handler.end_headers = MagicMock()
        with patch.object(server.urllib.request, "urlopen", return_value=response) as urlopen:
            handler.do_POST()
        handler.send_response.assert_called_once_with(200)
        self.assertEqual(json.loads(handler.wfile.getvalue()), {"ok": True})
        cloud_request = urlopen.call_args.args[0]
        self.assertEqual(cloud_request.full_url, "https://api.bambulab.com/v1/user-service/user/sendemail/code")
        self.assertEqual(json.loads(cloud_request.data), {"email": "test@example.com", "type": "codeLogin"})

    def test_empty_response_is_rejected_when_json_is_required(self):
        with patch.object(server.urllib.request, "urlopen", return_value=io.BytesIO(b"")):
            with self.assertRaisesRegex(ValueError, "unexpected response"):
                server.cloud_request("/v1/user-service/user/login", {"account": "test@example.com"})

    def test_non_json_code_response_is_not_reported_as_success(self):
        with patch.object(server.urllib.request, "urlopen", return_value=io.BytesIO(b"<html>blocked</html>")):
            with self.assertRaisesRegex(ValueError, "unexpected response"):
                server.cloud_request("/v1/user-service/user/sendemail/code", {}, allow_empty=True)

    def test_http_failure_is_not_reported_as_success(self):
        error = urllib.error.HTTPError("https://api.bambulab.com", 429, "Too Many Requests", {}, None)
        with patch.object(server.urllib.request, "urlopen", side_effect=error):
            with self.assertRaisesRegex(ValueError, "HTTP 429"):
                server.cloud_request("/v1/user-service/user/sendemail/code", {}, allow_empty=True)

    def test_json_response_is_still_decoded(self):
        with patch.object(server.urllib.request, "urlopen", return_value=io.BytesIO(b'{"accessToken":"example"}')):
            result = server.cloud_request("/v1/user-service/user/login", {})
        self.assertEqual(result, {"accessToken": "example"})


class TelemetryMergeTests(unittest.TestCase):
    def test_partial_ams_and_extruder_updates_preserve_other_entries(self):
        report = {"print": {"ams": {"ams": [
            {"id": "0", "tray": [{"id": "0", "tray_type": "PLA"}, {"id": "1", "tray_type": "PETG"}]},
            {"id": "1", "tray": [{"id": "0", "tray_type": "ABS"}]},
        ]}, "device": {"extruder": {"info": [{"id": 0, "snow": 259, "temp": 42}]}}}}
        update = {"print": {"ams": {"ams": [{"id": "0", "tray": [{"id": "1", "remain": 50}]}]},
                            "device": {"extruder": {"info": [{"id": 0, "temp": 43}]}}}}
        merge_dict(report, update)
        units = report["print"]["ams"]["ams"]
        self.assertEqual(len(units), 2)
        self.assertEqual(units[0]["tray"][0]["tray_type"], "PLA")
        self.assertEqual(units[0]["tray"][1], {"id": "1", "tray_type": "PETG", "remain": 50})
        self.assertEqual(report["print"]["device"]["extruder"]["info"][0]["snow"], 259)

    def test_unrelated_arrays_are_replaced(self):
        report = {"print": {"hms": [1, 2]}}
        merge_dict(report, {"print": {"hms": []}})
        self.assertEqual(report["print"]["hms"], [])

    def test_partial_status_updates_preserve_other_components(self):
        report = {"print": {
            "device": {
                "nozzle": {"info": [{"id": 0, "diameter": "0.4"}, {"id": 1, "diameter": "0.6"}]},
                "airduct": {"parts": [{"id": 160, "state": 40}, {"id": 161, "state": 20}]},
                "storage": [{"name": "usb"}],
            },
            "lights_report": [{"node": "chamber_light", "mode": "on"}, {"node": "work_light", "mode": "off"}],
        }}
        update = {"print": {
            "device": {
                "nozzle": {"info": [{"id": 1, "type": "steel"}]},
                "airduct": {"parts": [{"id": 160, "state": 60}]},
                "storage": [],
            },
            "lights_report": [{"node": "work_light", "mode": "on"}],
        }}
        merge_dict(report, update)
        device = report["print"]["device"]
        self.assertEqual(device["nozzle"]["info"], [{"id": 0, "diameter": "0.4"}, {"id": 1, "diameter": "0.6", "type": "steel"}])
        self.assertEqual(device["airduct"]["parts"], [{"id": 160, "state": 60}, {"id": 161, "state": 20}])
        self.assertEqual(device["storage"], [])
        self.assertEqual(report["print"]["lights_report"], [{"node": "chamber_light", "mode": "on"}, {"node": "work_light", "mode": "on"}])


class SavedSessionTests(unittest.TestCase):
    def test_save_restore_and_forget(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bambudash" / "session.json"
            legacy = Path(directory) / "layer-touch" / "session.json"
            with patch.object(server, "SESSION_FILE", path), patch.object(server, "LEGACY_SESSION_FILE", legacy):
                server.save_session("example-token", "china")
                self.assertEqual(path.stat().st_mode & 0o777, 0o600)
                self.assertEqual(path.parent.stat().st_mode & 0o777, 0o700)
                session = server.CloudSession()
                with patch.object(session, "connect") as connect:
                    session.restore()
                    connect.assert_called_once_with("example-token", "china", cancel=session.restore_cancel)
                server.forget_session()
                self.assertFalse(path.exists())

    def test_restores_and_migrates_legacy_session(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bambudash" / "session.json"
            legacy = Path(directory) / "layer-touch" / "session.json"
            legacy.parent.mkdir()
            legacy.write_text(json.dumps({"token": "old-token", "region": "china"}))
            with patch.object(server, "SESSION_FILE", path), patch.object(server, "LEGACY_SESSION_FILE", legacy):
                session = server.CloudSession()
                with patch.object(session, "connect") as connect:
                    session.restore()
                connect.assert_called_once_with("old-token", "china", cancel=session.restore_cancel)
                self.assertEqual(json.loads(path.read_text()), {"token": "old-token", "region": "china"})
                self.assertFalse(legacy.exists())
                self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_new_session_takes_precedence_and_forget_removes_both(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bambudash" / "session.json"
            legacy = Path(directory) / "layer-touch" / "session.json"
            path.parent.mkdir()
            legacy.parent.mkdir()
            path.write_text(json.dumps({"token": "new-token", "region": "global"}))
            legacy.write_text(json.dumps({"token": "old-token"}))
            with patch.object(server, "SESSION_FILE", path), patch.object(server, "LEGACY_SESSION_FILE", legacy):
                session = server.CloudSession()
                with patch.object(session, "connect") as connect:
                    session.restore()
                connect.assert_called_once_with("new-token", "global", cancel=session.restore_cancel)
                server.forget_session()
                self.assertFalse(path.exists())
                self.assertFalse(legacy.exists())

    def test_save_removes_legacy_session(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bambudash" / "session.json"
            legacy = Path(directory) / "layer-touch" / "session.json"
            legacy.parent.mkdir()
            legacy.write_text(json.dumps({"token": "old-token"}))
            with patch.object(server, "SESSION_FILE", path), patch.object(server, "LEGACY_SESSION_FILE", legacy):
                server.save_session("new-token", "global")
                self.assertEqual(json.loads(path.read_text())["token"], "new-token")
                self.assertFalse(legacy.exists())

    def test_disconnect_during_migration_does_not_restore_saved_token(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bambudash" / "session.json"
            legacy = Path(directory) / "layer-touch" / "session.json"
            legacy.parent.mkdir()
            legacy.write_text(json.dumps({"token": "old-token"}))
            started = threading.Event()
            proceed = threading.Event()
            session = server.CloudSession()

            def migrate(token, region):
                started.set()
                if not proceed.wait(5):
                    raise TimeoutError("Migration was not released")
                path.parent.mkdir()
                path.write_text(json.dumps({"token": token, "region": region}))
                legacy.unlink()

            with patch.object(server, "SESSION_FILE", path), patch.object(server, "LEGACY_SESSION_FILE", legacy), \
                    patch.object(server, "save_session", side_effect=migrate), patch.object(session, "connect") as connect:
                restoring = threading.Thread(target=session.restore)
                restoring.start()
                try:
                    self.assertTrue(started.wait(5))
                    session.restore_cancel.set()
                    forgetting = threading.Thread(target=server.forget_session)
                    forgetting.start()
                finally:
                    proceed.set()
                    restoring.join(5)
                forgetting.join(5)
                self.assertFalse(restoring.is_alive())
                self.assertFalse(forgetting.is_alive())
                connect.assert_not_called()
                self.assertFalse(path.exists())
                self.assertFalse(legacy.exists())


class PreviewTests(unittest.TestCase):
    def test_extracts_only_active_plate_from_sliced_package(self):
        image = b"\x89PNG\r\n\x1a\nactive plate"
        archive = io.BytesIO()
        with zipfile.ZipFile(archive, "w") as package:
            package.writestr("Metadata/plate_1.png", b"\x89PNG\r\n\x1a\nwrong plate")
            package.writestr("Metadata/plate_8.png", image)
        def retrieve(command, callback):
            if command == "RETR /cache/job.gcode.3mf":
                callback(archive.getvalue())
            else:
                raise ftplib.error_perm("550 unavailable")
        with patch.object(printer_media, "PrinterFTPS") as ftp, patch.object(printer_media, "printer_tls_context"):
            ftp.return_value.retrbinary.side_effect = retrieve
            result = printer_media.download_plate_ftps("10.0.0.1", "printer", "12345678", "/data/Metadata/plate_8.png", "job")
        self.assertEqual(result, image)

    def test_missing_media_thumbnail_falls_back_to_ftps(self):
        session = server.CloudSession()
        client = object()
        session.client = client
        identity = ("42", "/data/Metadata/plate_8.png", "job")
        session.reports = {"printer": {"print": {"print_type": "local"}}}
        session.preview_jobs = {"printer": (identity, "loading", 0)}
        image = b"\x89PNG\r\n\x1a\nexample"
        with patch.object(printer_media, "PrinterMedia") as media, patch.object(printer_media, "download_plate_ftps", return_value=image) as ftp:
            media.return_value.download.side_effect = printer_media.PrinterMediaError(2)
            session.load_local_preview(client, "printer", identity, ["10.0.0.1"], "12345678", identity[1])
        ftp.assert_called_once_with("10.0.0.1", "printer", "12345678", identity[1], "job")
        self.assertEqual(session.preview_images["printer"], image)

    def test_ftps_retrieves_exact_active_plate(self):
        image = b"\x89PNG\r\n\x1a\nexample"
        with patch.object(printer_media, "PrinterFTPS") as ftp, patch.object(printer_media, "printer_tls_context"):
            ftp.return_value.retrbinary.side_effect = lambda command, callback: callback(image)
            result = printer_media.download_plate_ftps("10.0.0.1", "printer", "12345678", "/data/Metadata/plate_8.png")
        self.assertEqual(result, image)
        ftp.return_value.retrbinary.assert_called_once()
        self.assertEqual(ftp.return_value.retrbinary.call_args.args[0], "RETR /data/Metadata/plate_8.png")

    def test_preview_endpoint_serves_cached_png(self):
        handler = object.__new__(server.Handler)
        handler.path = "/api/preview/printer?v=123"
        handler.wfile = io.BytesIO()
        handler.send_response = MagicMock()
        handler.send_header = MagicMock()
        handler.end_headers = MagicMock()
        session = server.CloudSession()
        session.preview_images["printer"] = b"\x89PNG\r\n\x1a\nexample"
        with patch.object(server, "session", session):
            handler.do_GET()
        handler.send_response.assert_called_once_with(200)
        handler.send_header.assert_any_call("Content-Type", "image/png")
        self.assertEqual(handler.wfile.getvalue(), session.preview_images["printer"])

    def test_printer_media_download_checks_chunks(self):
        image = b"\x89PNG\r\n\x1a\nexample"
        def frame(body):
            return struct.pack("<IIII", len(body), 0x0102013F, 1, 0) + body
        def response(data, chunk=b""):
            return frame(json.dumps(data).encode() + b"\n\n" + chunk)
        responses = frame(b"{}") + response({"result": 0})
        responses += response({"sequence": 1, "cmdtype": 4, "result": 1}, image[:8])
        responses += response({"sequence": 1, "cmdtype": 4, "result": 0, "reply": {"total": len(image), "file_md5": hashlib.md5(image).hexdigest()}}, image[8:])
        stream = io.BytesIO(responses)
        connection = MagicMock()
        connection.recv.side_effect = stream.read
        context = MagicMock()
        context.verify_flags = 0
        context.wrap_socket.return_value = connection
        with patch.object(printer_media.ssl, "create_default_context", return_value=context), patch.object(printer_media.socket, "create_connection"):
            result = printer_media.PrinterMedia("10.0.0.1", "serial", "12345678").download("/data/Metadata/plate_8.png")
        self.assertEqual(result, image)
        self.assertIn(b"/data/Metadata/plate_8.png", connection.sendall.call_args_list[-1].args[0])
        context.wrap_socket.assert_called_once_with(unittest.mock.ANY, server_hostname="serial")

    def test_local_plate_download_is_cached_for_current_job(self):
        session = server.CloudSession()
        client = object()
        session.client = client
        identity = ("42", "/data/Metadata/plate_8.png", "job")
        session.reports = {"printer": {"print": {"print_type": "local"}}}
        session.preview_jobs = {"printer": (identity, "loading", 0)}
        image = b"\x89PNG\r\n\x1a\nexample"
        with patch.object(printer_media, "PrinterMedia") as media:
            media.return_value.download.return_value = image
            session.load_local_preview(client, "printer", identity, ["10.0.0.1"], "12345678", identity[1])
        self.assertEqual(session.preview_images["printer"], image)
        self.assertTrue(session.reports["printer"]["_preview_url"].startswith("/api/preview/printer?v="))
        session.preview_jobs["printer"] = (("new-job",), "loading", 0)
        with patch.object(printer_media, "PrinterMedia") as media:
            media.return_value.download.return_value = b"older-image"
            session.load_local_preview(client, "printer", identity, ["10.0.0.1"], "12345678", identity[1])
        self.assertEqual(session.preview_images["printer"], image)

    def test_local_plate_path_rejects_mismatch_and_traversal(self):
        self.assertEqual(printer_media.active_plate_path({"gcode_file": "/data/Metadata/plate_8.gcode", "plate_idx": 8}), "/data/Metadata/plate_8.png")
        self.assertIsNone(printer_media.active_plate_path({"gcode_file": "/data/Metadata/plate_8.gcode", "plate_idx": 7}))
        self.assertIsNone(printer_media.active_plate_path({"gcode_file": "/data/Metadata/../plate_8.gcode"}))

    def test_local_task_never_uses_cloud_history_cover(self):
        job = {"print_type": "local", "subtask_name": "Christmas Lamp Post", "design_id": "2662891", "plate_idx": 8}
        tasks = [{"id": 3124, "deviceId": "printer", "title": "Christmas Lamp Post", "designId": 2662891, "plateIndex": 8}]
        self.assertIsNone(server.matching_preview_task(tasks, "printer", "3124", job))

    def test_match_requires_current_task_and_printer(self):
        session = server.CloudSession()
        client = object()
        session.client = client
        session.reports = {"printer": {"print": {"gcode_state": "RUNNING", "task_id": "42"}}}
        session.preview_jobs = {"printer": ("42", "loading", 0)}
        hits = [{"id": 42, "deviceId": "other", "cover": "https://example.com/other.png"},
                {"id": 42, "deviceId": "printer", "cover": "https://example.com/current.png"}]
        with patch.object(server, "cloud_request", return_value={"hits": hits}):
            session.load_preview(client, "printer", "42", "token", "global")
        self.assertEqual(session.reports["printer"]["_preview_url"], "https://example.com/current.png")
        session.preview_jobs["printer"] = ("43", "loading", 0)
        with patch.object(server, "cloud_request", return_value={"hits": hits}):
            session.load_preview(client, "printer", "43", "token", "global")
        self.assertNotIn("_preview_url", session.reports["printer"])

    def test_rejects_non_https_cover(self):
        session = server.CloudSession()
        client = object()
        session.client = client
        session.reports = {"printer": {}}
        session.preview_jobs = {"printer": ("42", "loading", 0)}
        hits = [{"id": 42, "deviceId": "printer", "cover": "http://example.com/image.png"}]
        with patch.object(server, "cloud_request", return_value={"hits": hits}):
            session.load_preview(client, "printer", "42", "token", "global")
        self.assertNotIn("_preview_url", session.reports["printer"])


if __name__ == "__main__":
    unittest.main()