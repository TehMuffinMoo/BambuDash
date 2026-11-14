import hashlib
import ftplib
import ipaddress
import json
import re
import socket
import ssl
import struct
import tempfile
import zipfile
from pathlib import Path


MAX_IMAGE_SIZE = 8 * 1024 * 1024
MAX_PACKAGE_SIZE = 128 * 1024 * 1024
MAX_FRAME_SIZE = MAX_IMAGE_SIZE + 65536
CERTS = Path(__file__).parent / "certs"


class PrinterMediaError(OSError):
    def __init__(self, code):
        super().__init__(f"Printer media result {code}")
        self.code = code


def printer_tls_context():
    context = ssl.create_default_context(cafile=str(CERTS / "bambu.cert"))
    context.load_verify_locations(cafile=str(CERTS / "bambu_p2s_250626.cert"))
    context.verify_flags &= ~getattr(ssl, "VERIFY_X509_STRICT", 0)
    context.maximum_version = ssl.TLSVersion.TLSv1_2
    return context


class PrinterFTPS(ftplib.FTP_TLS):
    def __init__(self, serial, context):
        super().__init__(context=context, timeout=10)
        self.serial = serial

    def connect(self, host, port=990, timeout=10, source_address=None):
        self.host = host
        self.port = port
        self.timeout = timeout
        raw = socket.create_connection((host, port), timeout, source_address=source_address)
        try:
            self.sock = self.context.wrap_socket(raw, server_hostname=self.serial)
        except Exception:
            raw.close()
            raise
        self.af = self.sock.family
        self.file = self.sock.makefile("r", encoding=self.encoding)
        self.welcome = self.getresp()
        return self.welcome

    def ntransfercmd(self, command, rest=None):
        connection, size = ftplib.FTP.ntransfercmd(self, command, rest)
        if self._prot_p:
            try:
                connection = self.context.wrap_socket(connection, server_hostname=self.serial, session=self.sock.session)
            except Exception:
                connection.close()
                raise
        return connection, size


def download_plate_ftps(host, serial, access_code, path, job_name=""):
    if not re.fullmatch(r"/data/Metadata/plate_[1-9][0-9]*\.png", path):
        raise ValueError("Invalid active plate path")
    client = PrinterFTPS(serial, printer_tls_context())
    try:
        client.connect(host)
        client.login("bblp", access_code)
        client.prot_p()
        for candidate in (path, path.removeprefix("/data")):
            image = bytearray()
            def receive(chunk):
                image.extend(chunk)
                if len(image) > MAX_IMAGE_SIZE:
                    raise ValueError("Printer thumbnail is too large")
            try:
                client.retrbinary(f"RETR {candidate}", receive)
            except ftplib.error_perm:
                continue
            if image.startswith(b"\x89PNG\r\n\x1a\n"):
                return bytes(image)
        if job_name and not any(character in job_name for character in ("/", "\\", "\r", "\n", "\0")):
            plate = re.search(r"plate_([1-9][0-9]*)\.png$", path).group(1)
            names = [job_name] if job_name.endswith(".3mf") else [f"{job_name}.gcode.3mf", f"{job_name}.3mf"]
            for directory in ("/cache", ""):
                for name in names:
                    with tempfile.SpooledTemporaryFile(max_size=4 * 1024 * 1024) as package:
                        size = 0
                        def receive_package(chunk):
                            nonlocal size
                            size += len(chunk)
                            if size > MAX_PACKAGE_SIZE:
                                raise ValueError("Printer package is too large")
                            package.write(chunk)
                        try:
                            client.retrbinary(f"RETR {directory}/{name}", receive_package)
                        except ftplib.error_perm:
                            continue
                        try:
                            package.seek(0)
                            with zipfile.ZipFile(package) as archive:
                                entry = next((item for item in archive.infolist() if item.filename.lower() == f"metadata/plate_{plate}.png"), None)
                                if entry and entry.file_size <= MAX_IMAGE_SIZE:
                                    image = archive.read(entry)
                                    if image.startswith(b"\x89PNG\r\n\x1a\n"):
                                        return image
                        except (zipfile.BadZipFile, OSError):
                            continue
        raise FileNotFoundError("Active plate thumbnail is unavailable")
    except ftplib.Error as error:
        raise OSError("Printer file service failed") from error
    finally:
        client.close()


def active_plate_path(job):
    filename = job.get("gcode_file") or job.get("file") or ""
    match = re.fullmatch(r"/data/Metadata/plate_([1-9][0-9]*)\.gcode", filename)
    if not match:
        return None
    plate = job.get("plate_idx") or job.get("plate_id")
    if plate is not None and str(plate) not in ("", "0", match.group(1)):
        return None
    return f"/data/Metadata/plate_{match.group(1)}.png"


def printer_hosts(job):
    hosts = []
    for interface in (job.get("net") or {}).get("info", []):
        try:
            address = ipaddress.IPv4Address(struct.pack("<I", int(interface.get("ip", 0))))
            if address.is_private and not (address.is_loopback or address.is_unspecified or address.is_link_local):
                hosts.append(str(address))
        except (ValueError, TypeError, struct.error, AttributeError):
            continue
    return hosts


class PrinterMedia:
    def __init__(self, host, serial, access_code):
        self.host = host
        self.serial = serial
        self.access_code = access_code
        self.connection = None
        self.frame_sequence = 1
        self.stage = "connect"

    def send(self, magic, payload):
        header = struct.pack("<IIII", len(payload), magic, self.frame_sequence, 0)
        self.frame_sequence += 1
        self.connection.sendall(header + payload)

    def receive_exactly(self, count):
        chunks = bytearray()
        while len(chunks) < count:
            data = self.connection.recv(count - len(chunks))
            if not data:
                raise OSError("Printer closed the media connection")
            chunks.extend(data)
        return bytes(chunks)

    def receive(self):
        length, _, _, _ = struct.unpack("<IIII", self.receive_exactly(16))
        if length > MAX_FRAME_SIZE:
            raise ValueError("Printer media response is too large")
        return self.receive_exactly(length)

    def receive_json(self):
        payload = self.receive()
        decoded, end = json.JSONDecoder().raw_decode(payload.decode("latin-1"))
        if not isinstance(decoded, dict):
            raise ValueError("Invalid printer media response")
        remainder = payload[end:]
        if remainder.startswith(b"\r\n\r\n"):
            remainder = remainder[4:]
        elif remainder.startswith(b"\n\n"):
            remainder = remainder[2:]
        return decoded, remainder

    def download(self, path):
        context = printer_tls_context()
        raw = socket.create_connection((self.host, 6000), timeout=5)
        try:
            self.stage = "tls"
            self.connection = context.wrap_socket(raw, server_hostname=self.serial)
            self.connection.settimeout(15)
            credentials = b"bblp".ljust(8, b"\0") + self.access_code.encode("ascii").ljust(8, b"\0")
            if len(credentials) != 16:
                raise ValueError("Invalid printer access code")
            self.stage = "login"
            self.send(0x0101013F, credentials)
            self.receive()
            setup = {"sequence": 0, "mtype": 12291, "req": {"t_av": 1, "mtype": 12289, "peer_t": 3, "pid": "00000001", "ver": "02.03.00.00"}}
            self.stage = "setup"
            self.send(0x0102013F, json.dumps(setup, separators=(",", ":")).encode())
            response, _ = self.receive_json()
            if int(response.get("result", -1)) != 0:
                raise OSError("Printer media setup failed")
            request = {"mtype": 12289, "cmdtype": 4, "sequence": 1, "req": {"path": path, "offset": 0}}
            self.stage = "download"
            self.send(0x0102013F, json.dumps(request, separators=(",", ":")).encode())
            image = bytearray()
            for _ in range(1024):
                response, chunk = self.receive_json()
                if int(response.get("sequence", -1)) != 1 or int(response.get("cmdtype", 4)) != 4:
                    continue
                details = response.get("reply") or {}
                if not details.get("mem_dl_param_size"):
                    image.extend(chunk)
                if len(image) > MAX_IMAGE_SIZE:
                    raise ValueError("Printer thumbnail is too large")
                result = int(response.get("result", -1))
                if result == 1:
                    continue
                if result != 0:
                    raise PrinterMediaError(result)
                total = int(details.get("total") or len(image))
                checksum = str(details.get("file_md5") or "").lower()
                if total != len(image) or (checksum and hashlib.md5(image).hexdigest() != checksum):
                    raise ValueError("Printer thumbnail failed integrity validation")
                if not image.startswith(b"\x89PNG\r\n\x1a\n"):
                    raise ValueError("Printer did not return a PNG thumbnail")
                return bytes(image)
            raise OSError("Printer thumbnail transfer did not complete")
        finally:
            if self.connection:
                self.connection.close()
            else:
                raw.close()