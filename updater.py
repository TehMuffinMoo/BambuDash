import io
import hashlib
import http.client
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
import urllib.error
import urllib.request
import zipfile
import zlib
from pathlib import Path, PurePosixPath


REPOSITORY = "https://github.com/TehMuffinMoo/BambuDash.git"
RELEASE_API = "https://api.github.com/repos/TehMuffinMoo/BambuDash/releases/latest"
ARCHIVE_URL = "https://codeload.github.com/TehMuffinMoo/BambuDash/zip/refs/tags/"
MAX_DOWNLOAD = 32 * 1024 * 1024
MAX_CONTENTS = 100 * 1024 * 1024


class UpdateError(Exception):
    pass


def version_tuple(value):
    if not isinstance(value, str) or not re.fullmatch(r"(0|[1-9]\d{0,8})\.(0|[1-9]\d{0,8})\.(0|[1-9]\d{0,8})", value):
        raise UpdateError("Release versions must use the form v1.2.3.")
    return tuple(int(part) for part in value.split("."))


def download(url, limit):
    request = urllib.request.Request(url, headers={
        "User-Agent": "BambuDash-updater",
        "Accept": "application/vnd.github+json" if url == RELEASE_API else "application/zip",
    })
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            data = response.read(limit + 1)
    except urllib.error.HTTPError as error:
        if url == RELEASE_API and error.code == 404:
            return None
        raise UpdateError(f"GitHub returned HTTP {error.code}.") from error
    except (urllib.error.URLError, TimeoutError, OSError, http.client.HTTPException) as error:
        raise UpdateError("Could not reach GitHub. Check your internet connection.") from error
    if len(data) > limit:
        raise UpdateError("The GitHub response is too large.")
    return data


def latest_release():
    body = download(RELEASE_API, 1024 * 1024)
    if body is None:
        return None
    try:
        release = json.loads(body)
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        raise UpdateError("GitHub returned an invalid release response.") from error
    if not isinstance(release, dict) or release.get("draft") is not False or release.get("prerelease") is not False:
        raise UpdateError("GitHub did not return a stable release.")
    tag = release.get("tag_name")
    if not isinstance(tag, str) or not tag.startswith("v"):
        raise UpdateError("The latest release does not have a version tag.")
    version_tuple(tag[1:])
    return tag[1:]


def release_files(version):
    version_tuple(version)
    body = download(ARCHIVE_URL + "v" + version, MAX_DOWNLOAD)
    if body is None:
        raise UpdateError("The release archive is unavailable.")
    files = {}
    names = set()
    total = 0
    root = None
    try:
        with zipfile.ZipFile(io.BytesIO(body)) as archive:
            entries = archive.infolist()
            if len(entries) > 2000:
                raise UpdateError("The release archive has too many files.")
            for entry in entries:
                name = entry.filename
                if "\\" in name or "\x00" in name:
                    raise UpdateError("The release archive contains an unsafe path.")
                path = PurePosixPath(name)
                parts = path.parts
                if path.is_absolute() or not parts or any(part in (".", "..") or ":" in part for part in parts):
                    raise UpdateError("The release archive contains an unsafe path.")
                if root is None:
                    root = parts[0]
                if parts[0] != root:
                    raise UpdateError("The release archive has multiple root directories.")
                if len(parts) == 1:
                    continue
                relative = PurePosixPath(*parts[1:])
                if any(part.lower() in (".git", ".venv", "__pycache__") for part in relative.parts):
                    raise UpdateError("The release archive contains a protected path.")
                if any(part.rstrip(" .") != part or any(ord(char) < 32 for char in part) or
                       part.split(".")[0].upper() in ("CON", "PRN", "AUX", "NUL", "COM1", "COM2", "COM3", "COM4", "COM5", "COM6", "COM7", "COM8", "COM9", "LPT1", "LPT2", "LPT3", "LPT4", "LPT5", "LPT6", "LPT7", "LPT8", "LPT9")
                       for part in relative.parts):
                    raise UpdateError("The release archive contains an unsafe path.")
                if stat.S_ISLNK(entry.external_attr >> 16):
                    raise UpdateError("The release archive contains a symbolic link.")
                if entry.is_dir():
                    continue
                total += entry.file_size
                folded = str(relative).casefold()
                if total > MAX_CONTENTS or folded in names:
                    raise UpdateError("The release archive is too large or contains duplicate files.")
                names.add(folded)
                files[str(relative)] = (archive.read(entry), (entry.external_attr >> 16) & 0o777)
    except (zipfile.BadZipFile, RuntimeError, OSError, EOFError, ValueError, zlib.error) as error:
        raise UpdateError("The release archive could not be read.") from error
    expected = (version + "\n").encode()
    if files.get("VERSION", (None,))[0] != expected or "server.py" not in files or "requirements.txt" not in files:
        raise UpdateError("The release archive is missing required files or has the wrong version.")
    return files


def checked_path(root, relative):
    path = root
    for part in PurePosixPath(relative).parts:
        path = path / part
        if path.is_symlink():
            raise UpdateError(f"Cannot update a symbolic link: {relative}")
    return path


def matches_release(path, content):
    local = path.read_bytes()
    if local == content:
        return True
    text_file = path.name in ("VERSION", ".gitignore") or path.suffix.lower() in (
        ".py", ".js", ".css", ".html", ".md", ".txt", ".sh", ".json"
    )
    if text_file and b"\r\n" not in content:
        try:
            local.decode("utf-8")
            content.decode("utf-8")
        except UnicodeDecodeError:
            return False
        return local.replace(b"\r\n", b"\n") == content
    return False


def check_copied_install(root, old_files, new_files):
    for name, (content, _) in old_files.items():
        path = checked_path(root, name)
        if not path.is_file() or not matches_release(path, content):
            raise UpdateError(f"Local file differs from the installed release: {name}")
    for name in new_files.keys() - old_files.keys():
        if checked_path(root, name).exists():
            raise UpdateError(f"A local file would be overwritten: {name}")


def write_file(path, content, mode):
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=".bambudash-update-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
        if mode:
            os.chmod(temporary, mode)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def update_copied_install(root, old_files, new_files):
    check_copied_install(root, old_files, new_files)
    changed = []
    try:
        for name in sorted(old_files.keys() | new_files.keys()):
            old = old_files.get(name)
            new = new_files.get(name)
            if old == new:
                continue
            path = checked_path(root, name)
            if old is None and path.exists():
                raise UpdateError(f"A local file would be overwritten: {name}")
            if old is not None and (not path.is_file() or not matches_release(path, old[0])):
                raise UpdateError(f"Local file differs from the installed release: {name}")
            original = (path.read_bytes(), stat.S_IMODE(path.stat().st_mode)) if old is not None else None
            changed.append((name, original))
            if new is None:
                path.unlink()
            else:
                write_file(path, *new)
    except (OSError, UpdateError) as error:
        failures = []
        for name, original in reversed(changed):
            try:
                path = checked_path(root, name)
                if original is not None:
                    write_file(path, *original)
                elif path.exists():
                    path.unlink()
            except (OSError, UpdateError):
                failures.append(name)
        if failures:
            raise UpdateError("Update failed and rollback could not restore: " + ", ".join(failures)) from error
        raise UpdateError(f"Update failed; original files were restored: {error}") from error


def git(root, *args, check=True):
    if not shutil.which("git"):
        raise UpdateError("Git is required to update this Git checkout.")
    env = os.environ.copy()
    env["GIT_TERMINAL_PROMPT"] = "0"
    try:
        result = subprocess.run(
            ["git", "-C", str(root), *args], capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=120, env=env,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise UpdateError("Could not run Git to update this checkout.") from error
    if check and result.returncode:
        raise UpdateError(f"Git update failed: {result.stderr.strip() or result.stdout.strip()}")
    return result


def check_git_workspace(root, files):
    if Path(git(root, "rev-parse", "--show-toplevel").stdout.strip()).resolve() != root.resolve():
        raise UpdateError("The Git checkout root does not match the BambuDash installation.")
    if git(root, "status", "--porcelain", "--untracked-files=normal").stdout.strip():
        raise UpdateError("This Git checkout has local changes. Commit, move, or discard them before updating.")
    if git(root, "symbolic-ref", "-q", "HEAD", check=False).returncode:
        raise UpdateError("Updates require a checked-out Git branch, not a detached HEAD.")
    tracked = set(git(root, "ls-files", "-z").stdout.split("\0"))
    for name in files:
        path = checked_path(root, name)
        if name not in tracked and path.exists():
            raise UpdateError(f"A local file would be overwritten: {name}")


def prepare_git_install(root, version, files):
    check_git_workspace(root, files)
    git(root, "fetch", "--no-tags", REPOSITORY, "refs/tags/v" + version)
    if git(root, "merge-base", "--is-ancestor", "HEAD", "FETCH_HEAD", check=False).returncode:
        raise UpdateError("The release is not a fast-forward of this checkout. Update it manually.")
    entries = git(root, "ls-tree", "-r", "-z", "--full-tree", "FETCH_HEAD").stdout.split("\0")
    objects = {}
    for entry in filter(None, entries):
        metadata, name = entry.split("\t", 1)
        mode, kind, object_id = metadata.split()
        if kind != "blob" or mode not in ("100644", "100755"):
            raise UpdateError("The release contains an unsupported Git object.")
        objects[name] = object_id
    expected = {
        name: hashlib.sha1(b"blob " + str(len(content)).encode() + b"\0" + content).hexdigest()
        for name, (content, _) in files.items()
    }
    if objects != expected:
        raise UpdateError("The release changed during the update. Check for updates again.")


def install_dependencies(root, requirements):
    if matches_release(root / "requirements.txt", requirements):
        return False
    with tempfile.TemporaryDirectory(prefix="bambudash-requirements-") as directory:
        path = Path(directory) / "requirements.txt"
        path.write_bytes(requirements)
        try:
            subprocess.run(
                [sys.executable, "-m", "pip", "install", "-r", str(path)],
                check=True, timeout=600,
            )
        except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as error:
            raise UpdateError("Dependency installation failed. Application files were not updated, but dependencies may have changed.") from error
    return True


class UpdateManager:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.lock = threading.Lock()
        self.latest = None
        self.state = "idle"
        self.message = ""
        self.target = None

    def current_version(self):
        try:
            value = (self.root / "VERSION").read_text(encoding="utf-8").strip()
        except OSError as error:
            raise UpdateError("The installed version could not be read.") from error
        version_tuple(value)
        return value

    def status(self):
        with self.lock:
            current = self.current_version()
            return {
                "current": current,
                "latest": self.latest,
                "available": bool(self.latest and version_tuple(self.latest) > version_tuple(current)),
                "state": self.state,
                "message": self.message,
                "target": self.target,
            }

    def check(self):
        latest = latest_release()
        with self.lock:
            self.latest = latest
        return self.status()

    def start(self, target, restart):
        with self.lock:
            if self.state in ("installing", "restarting"):
                raise UpdateError("An update is already in progress.")
            if not self.latest or target != self.latest or version_tuple(target) <= version_tuple(self.current_version()):
                raise UpdateError("Check for updates again before installing.")
            self.state = "installing"
            self.message = "Installing update..."
            self.target = target
        try:
            threading.Thread(target=self._install, args=(target, restart), daemon=True).start()
        except RuntimeError as error:
            with self.lock:
                self.state = "error"
                self.message = "Could not start the update."
            raise UpdateError(self.message) from error

    def _install(self, target, restart):
        lock_path = Path(tempfile.gettempdir()) / (
            "bambudash-update-" + hashlib.sha256(os.fsencode(str(self.root))).hexdigest() + ".lock"
        )
        lock_acquired = False
        dependencies_changed = False
        try:
            try:
                descriptor = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            except FileExistsError as error:
                raise UpdateError(f"Another update is running, or a stale lock exists: {lock_path}") from error
            lock_acquired = True
            os.close(descriptor)
            current = self.current_version()
            new_files = release_files(target)
            if (self.root / ".git").exists():
                prepare_git_install(self.root, target, new_files)
                dependencies_changed = install_dependencies(self.root, new_files["requirements.txt"][0])
                check_git_workspace(self.root, new_files)
                git(self.root, "merge", "--ff-only", "FETCH_HEAD")
            else:
                old_files = release_files(current)
                check_copied_install(self.root, old_files, new_files)
                dependencies_changed = install_dependencies(self.root, new_files["requirements.txt"][0])
                update_copied_install(self.root, old_files, new_files)
            try:
                lock_path.unlink()
                lock_acquired = False
            except OSError as error:
                print(f"Could not remove update lock {lock_path}: {error}", file=sys.stderr, flush=True)
            with self.lock:
                self.state = "restarting"
                self.message = "Update installed. Restarting BambuDash..."
            restart()
        except (UpdateError, OSError, RuntimeError) as error:
            message = str(error)
            if dependencies_changed:
                message += " Dependencies may have changed; check the Python environment."
            with self.lock:
                self.state = "error"
                self.message = message
            print(f"BambuDash update failed: {message}", file=sys.stderr, flush=True)
        finally:
            if lock_acquired:
                try:
                    lock_path.unlink()
                except OSError as error:
                    print(f"Could not remove update lock {lock_path}: {error}", file=sys.stderr, flush=True)
