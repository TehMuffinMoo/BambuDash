import base64
import hashlib
import json
import os
import threading
import time
import urllib.error
import urllib.request
import urllib.parse
import uuid
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import paho.mqtt.client as mqtt
import printer_media


ROOT = Path(__file__).parent
API = "https://api.bambulab.com"
CONFIG_HOME = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
SESSION_FILE = CONFIG_HOME / "bambudash" / "session.json"
LEGACY_SESSION_FILE = CONFIG_HOME / "layer-touch" / "session.json"
SESSION_FILE_LOCK = threading.RLock()
HEADERS = {
    "User-Agent": "BambuStudio/1.10.0",
    "Content-Type": "application/json",
    "Accept": "application/json",
    "X-BBL-Client-Name": "studio",
    "X-BBL-Client-Type": "slicer",
    "X-BBL-Language": "en-US",
}


def cloud_request(path, data=None, token=None, region="global", allow_empty=False):
    headers = HEADERS.copy()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    body = json.dumps(data).encode() if data is not None else None
    host = "https://api.bambulab.cn" if region == "china" else API
    request = urllib.request.Request(host + path, data=body, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            body = response.read()
            if allow_empty and not body.strip():
                return None
            try:
                return json.loads(body)
            except (json.JSONDecodeError, UnicodeDecodeError) as error:
                raise ValueError(
                    "Bambu Cloud returned an unexpected response. Try again later or use an access token."
                ) from error
    except urllib.error.HTTPError as error:
        raise ValueError(f"Bambu Cloud returned HTTP {error.code}. Check your credentials or try again later.") from error
    except (urllib.error.URLError, TimeoutError) as error:
        raise ValueError("Could not reach Bambu Cloud. Check your internet connection.") from error


def merge_dict(target, update, path=()):
    for key, value in update.items():
        current_path = path + (key,)
        if isinstance(value, dict) and isinstance(target.get(key), dict):
            merge_dict(target[key], value, current_path)
        elif current_path in (("print", "ams", "ams"), ("print", "ams", "ams", "tray"),
                              ("print", "device", "extruder", "info")) and isinstance(value, list):
            items = target.setdefault(key, [])
            if not isinstance(items, list):
                items = target[key] = []
            for entry in value:
                if not isinstance(entry, dict) or "id" not in entry:
                    continue
                existing = next((item for item in items if isinstance(item, dict) and str(item.get("id")) == str(entry["id"])), None)
                if existing is None:
                    existing = {}
                    items.append(existing)
                merge_dict(existing, entry, current_path)
        else:
            target[key] = value


def save_session(token, region):
    with SESSION_FILE_LOCK:
        SESSION_FILE.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(SESSION_FILE.parent, 0o700)
        temporary = SESSION_FILE.with_suffix(".tmp")
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            with os.fdopen(descriptor, "w") as stream:
                os.fchmod(stream.fileno(), 0o600)
                json.dump({"token": token, "region": region}, stream)
            os.replace(temporary, SESSION_FILE)
            LEGACY_SESSION_FILE.unlink(missing_ok=True)
        except Exception:
            temporary.unlink(missing_ok=True)
            raise


def forget_session():
    with SESSION_FILE_LOCK:
        SESSION_FILE.unlink(missing_ok=True)
        LEGACY_SESSION_FILE.unlink(missing_ok=True)


def matching_preview_task(tasks, serial, task, job):
    printer_tasks = [entry for entry in tasks if isinstance(entry, dict) and str(entry.get("deviceId")) == serial]
    if job.get("print_type") == "local":
        return None
    return next((entry for entry in printer_tasks if str(entry.get("id")) == task), None)


class CloudSession:
    def __init__(self):
        self.lock = threading.RLock()
        self.client = None
        self.devices = []
        self.reports = {}
        self.connection = "disconnected"
        self.restore_cancel = threading.Event()
        self.token = None
        self.region = "global"
        self.preview_jobs = {}
        self.preview_images = {}
        self.access_codes = {}

    def connect(self, token, region="global", cancel=None):
        devices = cloud_request("/v1/iot-service/api/user/bind", token=token, region=region).get("devices", [])
        try:
            payload = token.split(".")[1]
            identity = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
            username = identity.get("username")
        except (IndexError, ValueError, UnicodeDecodeError):
            username = None
        if not username:
            profile = cloud_request("/v1/design-user-service/my/preference", token=token, region=region)
            if profile.get("uid") is not None:
                username = f"u_{profile['uid']}"
        if not username:
            raise ValueError("Could not determine your Bambu Cloud account ID.")
        if cancel is not None and cancel.is_set():
            return
        self.disconnect()
        client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f"bambu-dashboard-{uuid.uuid4()}")
        client.username_pw_set(username, token)
        client.tls_set()
        client.reconnect_delay_set(min_delay=1, max_delay=30)
        client.on_connect = self.on_connect
        client.on_disconnect = self.on_disconnect
        client.on_message = self.on_message
        with self.lock:
            self.devices = [{"id": device.get("dev_id"), "name": device.get("name"),
                             "model": device.get("dev_product_name") or device.get("dev_model_name"),
                             "online": device.get("online"), "print_status": device.get("print_status")}
                            for device in devices if device.get("dev_id")]
            self.reports = {}
            self.connection = "connecting"
            self.client = client
            self.token = token
            self.region = region
            self.preview_jobs = {}
            self.preview_images = {}
            self.access_codes = {device["dev_id"]: device.get("dev_access_code", "")
                                 for device in devices if device.get("dev_id")}
        host = "cn.mqtt.bambulab.com" if region == "china" else "us.mqtt.bambulab.com"
        try:
            client.connect_async(host, 8883, 60)
            client.loop_start()
        except Exception:
            self.disconnect()
            raise ValueError("Could not start the Bambu Cloud status connection.")

    def on_connect(self, client, userdata, flags, reason_code, properties):
        with self.lock:
            if client is not self.client:
                return
            self.connection = "connected" if not reason_code.is_failure else "error"
            devices = list(self.devices)
        if not reason_code.is_failure:
            for device in devices:
                serial = device["id"]
                client.subscribe(f"device/{serial}/report")
                client.publish(f"device/{serial}/request", json.dumps({"pushing": {"sequence_id": "0", "command": "pushall"}}))

    def on_disconnect(self, client, userdata, disconnect_flags, reason_code, properties):
        with self.lock:
            if client is self.client:
                self.connection = "connecting" if reason_code.is_failure else "disconnected"

    def on_message(self, client, userdata, message):
        try:
            report = json.loads(message.payload)
            serial = message.topic.split("/")[1]
            if not isinstance(report, dict):
                return
            with self.lock:
                if client is self.client:
                    merge_dict(self.reports.setdefault(serial, {}), report)
                    self.reports[serial]["_updated_at"] = time.time()
                    self.schedule_preview(client, serial)
        except (ValueError, IndexError, UnicodeDecodeError):
            return

    def disconnect(self):
        with self.lock:
            client, self.client = self.client, None
            self.devices = []
            self.reports = {}
            self.connection = "disconnected"
            self.token = None
            self.preview_jobs = {}
            self.preview_images = {}
            self.access_codes = {}
        if client:
            client.disconnect()
            client.loop_stop()

    def schedule_preview(self, client, serial):
        report = self.reports[serial]
        job = report.get("print", {})
        task = next((str(value) for value in (job.get("subtask_id"), job.get("task_id"))
                 if str(value).isdigit() and int(value) > 0), "")
        local = job.get("print_type") == "local"
        path = printer_media.active_plate_path(job) if local else None
        hosts = printer_media.printer_hosts(job) if local else []
        access_code = self.access_codes.get(serial, "")
        identity = (task, path, job.get("subtask_name")) if local else task
        available = bool(path and hosts and access_code) if local else bool(task)
        if job.get("gcode_state") not in ("RUNNING", "PREPARE", "PAUSE") or not available:
            report.pop("_preview_url", None)
            self.preview_jobs.pop(serial, None)
            self.preview_images.pop(serial, None)
            if local and job.get("gcode_state") in ("RUNNING", "PREPARE", "PAUSE"):
                report["_preview_status"] = "plate_missing" if not path else "printer_address_missing" if not hosts else "access_code_missing"
            else:
                report.pop("_preview_status", None)
            return
        previous = self.preview_jobs.get(serial)
        if previous and previous[0] == identity and (previous[1] in ("loading", "ready") or time.time() - previous[2] < 60):
            return
        if not previous or previous[0] != identity:
            report.pop("_preview_url", None)
            self.preview_images.pop(serial, None)
        self.preview_jobs[serial] = (identity, "loading", time.time())
        report["_preview_status"] = "loading"
        if local:
            threading.Thread(target=self.load_local_preview, args=(client, serial, identity, hosts, access_code, path), daemon=True).start()
        else:
            threading.Thread(target=self.load_preview, args=(client, serial, task, self.token, self.region), daemon=True).start()

    def load_local_preview(self, client, serial, identity, hosts, access_code, path):
        image = None
        failure = "printer_media_unavailable"
        for host in hosts:
            media = printer_media.PrinterMedia(host, serial, access_code)
            try:
                image = media.download(path)
                break
            except (OSError, ValueError, UnicodeError) as error:
                if isinstance(error, printer_media.PrinterMediaError) and error.code == 2:
                    media.stage = "ftps"
                    try:
                        image = printer_media.download_plate_ftps(host, serial, access_code, path, identity[2] or "")
                        break
                    except (OSError, ValueError, UnicodeError) as ftp_error:
                        error = ftp_error
                detail = f"result_{error.code}" if isinstance(error, printer_media.PrinterMediaError) else type(error).__name__
                failure = f"media_error_{media.stage}_{detail}"
                continue
        with self.lock:
            current = self.preview_jobs.get(serial)
            if client is self.client and current and current[0] == identity:
                self.preview_jobs[serial] = (identity, "ready" if image else "done", time.time())
                if image:
                    self.preview_images[serial] = image
                    version = hashlib.sha256(image).hexdigest()[:16]
                    self.reports[serial]["_preview_url"] = f"/api/preview/{urllib.parse.quote(serial, safe='')}?v={version}"
                    self.reports[serial]["_preview_status"] = "ready"
                else:
                    self.reports[serial]["_preview_status"] = failure

    def load_preview(self, client, serial, task, token, region):
        cover = None
        try:
            tasks = cloud_request("/v1/user-service/my/tasks", token=token, region=region).get("hits", [])
            with self.lock:
                job = dict(self.reports.get(serial, {}).get("print", {}))
            match = matching_preview_task(tasks, serial, task, job)
            candidate = match.get("cover") if match else None
            if isinstance(candidate, str):
                url = urllib.parse.urlsplit(candidate)
                if url.scheme == "https" and url.hostname and not url.username and not url.password:
                    cover = candidate
        except (ValueError, TypeError, AttributeError):
            pass
        with self.lock:
            current = self.preview_jobs.get(serial)
            if client is self.client and current and current[0] == task:
                self.preview_jobs[serial] = (task, "done", time.time())
                if cover:
                    self.reports[serial]["_preview_url"] = cover
                    self.reports[serial]["_preview_status"] = "ready"
                else:
                    self.reports[serial].pop("_preview_url", None)
                    self.reports[serial]["_preview_status"] = "cloud_preview_unavailable"

    def snapshot(self):
        with self.lock:
            return {"connection": self.connection, "devices": self.devices, "reports": self.reports}

    def restore(self):
        try:
            with SESSION_FILE_LOCK:
                if self.restore_cancel.is_set():
                    return
                if not SESSION_FILE.exists() and LEGACY_SESSION_FILE.exists():
                    with LEGACY_SESSION_FILE.open() as stream:
                        saved = json.load(stream)
                    save_session(saved["token"], saved.get("region", "global"))
                if not SESSION_FILE.exists():
                    return
                with SESSION_FILE.open() as stream:
                    saved = json.load(stream)
            while not self.restore_cancel.is_set():
                try:
                    self.connect(saved["token"], saved.get("region", "global"), cancel=self.restore_cancel)
                    return
                except ValueError as error:
                    if "Could not reach Bambu Cloud" not in str(error):
                        raise
                    if self.restore_cancel.wait(30):
                        return
        except (OSError, ValueError, KeyError, TypeError):
            print("Could not restore the saved Bambu Cloud session; reconnect from Settings.", flush=True)


session = CloudSession()


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(ROOT / "public"), **kwargs)

    def json_response(self, value, status=200):
        body = json.dumps(value).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = urllib.parse.urlsplit(self.path).path
        if path.startswith("/api/preview/"):
            serial = urllib.parse.unquote(path[len("/api/preview/"):])
            with session.lock:
                image = session.preview_images.get(serial)
            if not image:
                return self.json_response({"error": "Preview unavailable"}, 404)
            self.send_response(200)
            self.send_header("Content-Type", "image/png")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(image)))
            self.end_headers()
            self.wfile.write(image)
        elif path == "/api/status":
            self.json_response(session.snapshot())
        elif path.startswith("/api/"):
            self.json_response({"error": "Not found"}, 404)
        else:
            super().do_GET()

    def do_POST(self):
        origin = self.headers.get("Origin")
        if origin and origin not in (f"http://{self.headers.get('Host')}",):
            return self.json_response({"error": "Invalid request origin."}, 403)
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length > 16384:
                raise ValueError("Request is too large.")
            data = json.loads(self.rfile.read(length) or b"{}")
            if self.path == "/api/send-code":
                email = data.get("email", "").strip()
                if not email or "@" not in email:
                    raise ValueError("Enter your Bambu account email address.")
                cloud_request("/v1/user-service/user/sendemail/code", {"email": email, "type": "codeLogin"}, region=data.get("region", "global"), allow_empty=True)
                self.json_response({"ok": True})
            elif self.path == "/api/connect":
                session.restore_cancel.set()
                token = data.get("token", "").strip()
                if not token:
                    account = data.get("email", "").strip()
                    code = data.get("code", "").strip()
                    if not account or not code:
                        raise ValueError("Enter your email and verification code.")
                    result = cloud_request("/v1/user-service/user/login", {"account": account, "code": code}, region=data.get("region", "global"))
                    token = result.get("accessToken")
                if not token:
                    raise ValueError("Bambu Cloud did not return an access token. Check your verification code.")
                region = data.get("region", "global")
                session.connect(token, region)
                if data.get("remember") is True:
                    save_session(token, region)
                else:
                    forget_session()
                self.json_response({"ok": True})
            elif self.path == "/api/disconnect":
                session.restore_cancel.set()
                session.disconnect()
                forget_session()
                self.json_response({"ok": True})
            else:
                self.json_response({"error": "Not found"}, 404)
        except OSError:
            self.json_response({"error": "Could not update the saved cloud session."}, 500)
        except (ValueError, TypeError, json.JSONDecodeError) as error:
            self.json_response({"error": str(error)}, 400)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8000"))
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    threading.Thread(target=session.restore, daemon=True).start()
    print(f"BambuDash running at http://127.0.0.1:{port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        session.disconnect()
        server.server_close()