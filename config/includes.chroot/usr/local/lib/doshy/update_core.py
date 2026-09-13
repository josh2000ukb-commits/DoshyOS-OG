#!/usr/bin/python3
"""Cumulative, file-based doshy updates. No formatting, partitioning or home writes."""
import argparse
import contextlib
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.error
import urllib.request
import uuid

ASSET = "doshy-update.tar.gz"
LIMIT = 128 * 1024 * 1024
BASELINE = "/usr/share/doshy/update-baseline.json"
VERSION = "/usr/share/doshy/version"
CONFIG = "/etc/doshy/update.json"
STATE = "/var/lib/doshy-update"
HELPER = "/usr/local/bin/doshy-update-helper"


class UpdateError(Exception):
    pass


def version(value):
    if not isinstance(value, str) or not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", value):
        raise UpdateError("Release versions must use MAJOR.MINOR.PATCH, for example 1.1.0.")
    return tuple(map(int, value.split(".")))


def repository(value):
    value = value.strip().removeprefix("https://github.com/").rstrip("/")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9-]*/[A-Za-z0-9_.-]+", value):
        raise UpdateError("Enter a public GitHub repository as owner/repository.")
    return value


def managed(path):
    p = PurePosixPath(path)
    if not path.startswith("/") or str(p) != path or ".." in p.parts:
        return False
    if path == BASELINE:
        return False
    return (path.startswith(("/etc/xdg/", "/etc/gtk-2.0/", "/etc/gtk-3.0/",
                             "/etc/lightdm/", "/etc/skel/", "/usr/local/lib/doshy/",
                             "/usr/share/doshy/"))
            or (p.parent == PurePosixPath("/usr/local/bin") and p.name.startswith("doshy-"))
            or (p.parent == PurePosixPath("/usr/share/backgrounds") and p.name.startswith("doshy"))
            or (p.parent == PurePosixPath("/usr/share/applications") and p.name.startswith("doshy-"))
            or (p.parent == PurePosixPath("/etc/systemd/system") and p.name.startswith("doshy-") and p.suffix == ".service")
            or path == "/usr/share/polkit-1/actions/org.doshy.policy")


def digest(data):
    return hashlib.sha256(data).hexdigest()


def file_mode(path):
    return 0o755 if (path.startswith("/usr/local/bin/") or path.endswith((".sh", "/login-setup", "/autostart"))) else 0o644


def sync_directory(path):
    if os.name == "posix":
        fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


def atomic(path, data, mode=0o644):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".doshy-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(tmp, mode)
        os.replace(tmp, path)
        sync_directory(path.parent)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def save_json(path, data):
    atomic(path, (json.dumps(data, indent=2, sort_keys=True) + "\n").encode())


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def download(url, limit=LIMIT):
    if not url.startswith("https://"):
        raise UpdateError("Updates require HTTPS.")
    req = urllib.request.Request(url, headers={"User-Agent": "doshy-updater/1", "Accept": "application/vnd.github+json"})
    try:
        with urllib.request.urlopen(req, timeout=45) as response:
            if not response.url.startswith("https://"):
                raise UpdateError("Refusing an insecure download redirect.")
            data = response.read(limit + 1)
    except urllib.error.HTTPError as error:
        if error.code == 404:
            raise UpdateError("Repository or published release not found. Use a public repository with a finished release.") from error
        raise UpdateError(f"GitHub returned HTTP {error.code}. Try again later.") from error
    except (urllib.error.URLError, TimeoutError) as error:
        raise UpdateError(f"Cannot reach GitHub: {error}") from error
    if len(data) > limit:
        raise UpdateError("The update exceeds the supported size limit.")
    return data


def release(repo):
    info = json.loads(download(f"https://api.github.com/repos/{repository(repo)}/releases/latest", 2 * 1024 * 1024))
    tag = info.get("tag_name", "")
    if not tag.startswith("v") or info.get("draft") or info.get("prerelease"):
        raise UpdateError("The latest release must be a stable vMAJOR.MINOR.PATCH release.")
    version(tag[1:])
    assets = {a["name"]: a for a in info.get("assets", [])}
    if not all(name in assets for name in (ASSET, ASSET + ".sha256")):
        raise UpdateError("The latest release has no complete OS update bundle yet. Try again after publishing finishes.")
    prefix = f"https://github.com/{repo}/releases/download/{tag}/"
    for name in (ASSET, ASSET + ".sha256"):
        if assets[name].get("browser_download_url") != prefix + name:
            raise UpdateError("Release asset does not belong to the configured repository and tag.")
    return {"version": tag[1:], "tag": tag, "assets": assets, "repository": repo}


def unpack(data, expected_version):
    """Read regular files only; never extract archive paths onto the filesystem."""
    files = {}
    total = 0
    try:
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as archive:
            for item in archive:
                if not item.isfile() or item.name in files or item.size > LIMIT:
                    raise UpdateError("Update contains a link, duplicate, non-file or oversized entry.")
                total += item.size
                if total > LIMIT or len(files) >= 4096:
                    raise UpdateError("Expanded update is too large.")
                files[item.name] = archive.extractfile(item).read()
    except (tarfile.TarError, EOFError) as error:
        raise UpdateError("Invalid update archive.") from error
    try:
        manifest = json.loads(files.pop("manifest.json"))
    except (KeyError, ValueError) as error:
        raise UpdateError("Missing or invalid update manifest.") from error
    if (not isinstance(manifest, dict) or manifest.get("format") != 1 or manifest.get("version") != expected_version
            or manifest.get("base") != "bookworm" or manifest.get("architecture") != "amd64"):
        raise UpdateError("This update does not match the release or the supported OS base.")
    version(manifest["version"])
    entries = manifest.get("files")
    if not isinstance(entries, dict) or not entries:
        raise UpdateError("Update has no managed files.")
    payload = {}
    for path, meta in entries.items():
        if not managed(path) or not isinstance(meta, dict) or meta.get("mode") not in (0o644, 0o755):
            raise UpdateError(f"Disallowed update path or mode: {path}")
        content = files.pop("root" + path, None)
        if content is None or digest(content) != meta.get("sha256"):
            raise UpdateError(f"Missing or damaged file: {path}")
        payload[path] = content
    if files:
        raise UpdateError("Update contains unlisted files.")
    if VERSION in payload and payload[VERSION].decode().strip() != expected_version:
        raise UpdateError("The bundled version file does not match this release.")
    packages = manifest.get("packages", [])
    if not isinstance(packages, list) or len(packages) > 512:
        raise UpdateError("Invalid package list.")
    for package in packages:
        if (not isinstance(package, str) or not re.fullmatch(r"[a-z0-9][a-z0-9+.-]+", package)
                or package.startswith(("linux-image", "linux-headers", "grub", "shim", "syslinux", "live-boot", "live-config"))):
            raise UpdateError(f"Unsupported update package: {package}")
    return manifest, payload


class Updater:
    def __init__(self, root=Path("/")):
        self.root = Path(root)
        self.storage = self.path(STATE)

    def path(self, name):
        return self.root / name.lstrip("/")

    def safe_target(self, name):
        target = self.path(name)
        for parent in (target, *target.parents):
            if parent == self.root:
                break
            if parent.is_symlink():
                raise UpdateError(f"Refusing to overwrite a symbolic link: {name}")
        if target.exists() and not target.is_file():
            raise UpdateError(f"Update destination is not a regular file: {name}")
        return target

    def state(self):
        path = self.storage / "state.json"
        if not path.exists():
            path = self.path(BASELINE)
        if not path.exists():
            raise UpdateError("This installation needs the updater baseline from the combined OS update first.")
        return read_json(path)

    def check(self):
        config = read_json(self.path(CONFIG))
        if not config.get("repository"):
            raise UpdateError("No update source is configured. Choose Set GitHub repository in System updates.")
        latest = release(repository(config["repository"]))
        latest["installed"] = self.state()["version"]
        latest["available"] = version(latest["version"]) > version(latest["installed"])
        return latest

    @contextlib.contextmanager
    def lock(self):
        import fcntl
        self.storage.mkdir(parents=True, exist_ok=True, mode=0o755)
        with (self.storage / "lock").open("w") as handle:
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as error:
                raise UpdateError("Another OS update is already running.") from error
            yield

    def check_system(self):
        os_release = self.path("/etc/os-release").read_text()
        if 'VERSION_CODENAME=bookworm' not in os_release and 'VERSION_CODENAME="bookworm"' not in os_release:
            raise UpdateError("This updater supports the Debian 12 version of doshyos-OG only.")
        if subprocess.check_output(["dpkg", "--print-architecture"], text=True).strip() != "amd64":
            raise UpdateError("This release requires amd64.")
        if self.path("/run/live/medium").exists():
            # Confirm the writable root overlay actually sits on a persistent filesystem.
            mounts = []
            for line in self.path("/proc/self/mountinfo").read_text().splitlines():
                before, after = line.split(" - ", 1)
                fields, tail = before.split(), after.split()
                mounts.append((fields[4].replace("\\040", " "), tail[0], tail[2]))
            root_mount = next((m for m in mounts if m[0] == "/"), None)
            upper = None
            if root_mount and root_mount[1] == "overlay":
                upper = next((s[9:] for s in root_mount[2].split(",") if s.startswith("upperdir=")), None)
            backing = sorted((m for m in mounts if upper and (upper == m[0] or upper.startswith(m[0].rstrip("/") + "/"))), key=lambda m: len(m[0]))
            if not backing or backing[-1][1] not in ("ext2", "ext3", "ext4", "btrfs", "xfs", "f2fs", "ntfs3"):
                raise UpdateError("This live session has no confirmed full persistence. Boot with a persistence partition containing '/ union' before updating.")

    def plan(self, manifest, payload):
        previous = self.state().get("files", {})
        operations, conflicts = [], []
        for name in sorted(set(previous) | set(manifest["files"])):
            if not managed(name):
                raise UpdateError(f"Invalid managed baseline path: {name}")
            target = self.safe_target(name)
            current = target.read_bytes() if target.exists() else None
            incoming = payload.get(name)
            old_hash = previous.get(name, {}).get("sha256")
            current_hash = digest(current) if current is not None else None
            if current == incoming and (incoming is None or stat.S_IMODE(target.stat().st_mode) == manifest["files"][name]["mode"]):
                continue
            # Preserve deletions and edits made locally, including files added outside us.
            if (name in previous and current_hash != old_hash) or (name not in previous and current is not None):
                conflicts.append(name)
                continue
            operations.append((name, incoming, manifest["files"].get(name, {}).get("mode", 0o644)))
        return operations, conflicts

    def restore(self, journal):
        backup = self.storage / "backups" / journal["id"]
        for entry in reversed(journal["operations"]):
            target = self.safe_target(entry["path"])
            if entry["existed"]:
                atomic(target, (backup / entry["backup"]).read_bytes(), entry["mode"])
                if hasattr(os, "chown"):
                    os.chown(target, entry["uid"], entry["gid"])
            else:
                target.unlink(missing_ok=True)
                if target.parent.exists():
                    sync_directory(target.parent)
        if journal["before_state"] is None:
            (self.storage / "state.json").unlink(missing_ok=True)
        else:
            save_json(self.storage / "state.json", journal["before_state"])
        (self.storage / "pending.json").unlink(missing_ok=True)
        sync_directory(self.storage)

    def recover(self):
        pending = self.storage / "pending.json"
        if pending.exists():
            print("Recovering an interrupted file update...", flush=True)
            self.restore(read_json(pending))

    def install_files(self, manifest, payload):
        self.recover()
        operations, conflicts = self.plan(manifest, payload)
        needed = sum(len(data) for data in payload.values())
        needed += sum(self.safe_target(name).stat().st_size for name, _, _ in operations if self.safe_target(name).exists())
        needed = needed * 2 + 32 * 1024 * 1024
        self.storage.mkdir(parents=True, exist_ok=True)
        if shutil.disk_usage(self.storage).free < needed:
            raise UpdateError("Not enough free space for the update and its backup.")
        backup_id = str(uuid.uuid4())
        backup = self.storage / "backups" / backup_id
        backup.mkdir(parents=True, mode=0o700)
        old_state = self.storage / "state.json"
        journal = {"id": backup_id, "version": manifest["version"], "operations": [],
                   "before_state": read_json(old_state) if old_state.exists() else None}
        # All backups are complete before publishing the recovery journal or writing files.
        for i, (name, data, mode) in enumerate(operations):
            target = self.safe_target(name)
            entry = {"path": name, "existed": target.exists(), "backup": str(i),
                     "after": digest(data) if data is not None else None}
            if target.exists():
                st = target.stat()
                entry.update(mode=stat.S_IMODE(st.st_mode), uid=st.st_uid, gid=st.st_gid)
                atomic(backup / str(i), target.read_bytes(), 0o600)
            journal["operations"].append(entry)
        save_json(self.storage / "pending.json", journal)
        try:
            for name, data, mode in operations:
                target = self.safe_target(name)
                if data is None:
                    target.unlink(missing_ok=True)
                    sync_directory(target.parent)
                else:
                    atomic(target, data, mode)
            for name in conflicts:
                if name in payload:
                    atomic(self.storage / "conflicts" / manifest["version"] / name.lstrip("/"), payload[name])
            save_json(self.storage / "state.json", {"version": manifest["version"], "files": manifest["files"], "conflicts": conflicts})
            save_json(self.storage / "last.json", journal)
            (self.storage / "pending.json").unlink()
            sync_directory(self.storage)
        except BaseException:
            self.restore(journal)
            raise
        return conflicts

    def apply(self, expected):
        with self.lock():
            self.recover()
            self.check_system()
            latest = self.check()
            if latest["version"] != expected or not latest["available"]:
                raise UpdateError("The available release has changed or is already installed. Check for updates again.")
            print(f"Downloading doshy OS {expected}...", flush=True)
            assets = latest["assets"]
            checksum = download(assets[ASSET + ".sha256"]["browser_download_url"], 1024).decode().split()
            if len(checksum) != 2 or not re.fullmatch("[a-f0-9]{64}", checksum[0]) or checksum[1] != ASSET:
                raise UpdateError("Invalid release checksum file.")
            data = download(assets[ASSET]["browser_download_url"])
            if digest(data) != checksum[0]:
                raise UpdateError("Downloaded update failed its SHA-256 check.")
            manifest, payload = unpack(data, expected)
            self.plan(manifest, payload)  # Reject unsafe destinations before package operations.
            if manifest.get("packages"):
                print("Installing required Debian packages...", flush=True)
                env = dict(os.environ, DEBIAN_FRONTEND="noninteractive")
                subprocess.run(["apt-get", "update"], check=True, env=env)
                subprocess.run(["apt-get", "install", "--yes", "--no-remove", "--no-install-recommends",
                                "-o", "Dpkg::Options::=--force-confold", *manifest["packages"]], check=True, env=env)
            conflicts = self.install_files(manifest, payload)
            for command in (["systemctl", "daemon-reload"], ["update-desktop-database", "/usr/share/applications"]):
                subprocess.run(command, check=False)
            print(f"Installed doshy OS {expected}. Restart when convenient.", flush=True)
            if conflicts:
                print("Local changes were preserved. New defaults are in " + STATE + "/conflicts/" + expected)
                print("\n".join(conflicts))

    def rollback(self):
        with self.lock():
            self.recover()
            path = self.storage / "last.json"
            if not path.exists():
                raise UpdateError("There is no previous update to restore.")
            journal = read_json(path)
            for entry in journal["operations"]:
                target = self.safe_target(entry["path"])
                current = digest(target.read_bytes()) if target.exists() else None
                if current != entry["after"]:
                    raise UpdateError("A file changed after the update; rollback stopped to preserve it: " + entry["path"])
            save_json(self.storage / "pending.json", journal)
            self.restore(journal)
            path.unlink()
            print("Previous doshy files restored. Added Debian packages remain installed. Restart when convenient.")


def seed(root=Path("/")):
    root = Path(root)
    installed = {}
    # Only shipped doshy paths, recorded by the build hook in a generated path list.
    names = (root / "usr/share/doshy/update-paths.json").read_text()
    for name in json.loads(names):
        p = root / name.lstrip("/")
        if managed(name) and p.is_file() and not p.is_symlink():
            installed[name] = {"sha256": digest(p.read_bytes()), "mode": file_mode(name)}
    current = (root / VERSION.lstrip("/")).read_text().strip()
    version(current)
    save_json(root / BASELINE.lstrip("/"), {"version": current, "files": installed})


def dialog(kind, text, *args):
    result = subprocess.run(["zenity", "--" + kind, "--title=System updates", "--width=520", "--no-markup", "--text=" + text, *args], text=True, capture_output=True)
    return result.returncode, result.stdout.strip()


def privileged(*args):
    prefix = ["sudo", "-n"] if subprocess.run(["sudo", "-n", "true"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0 else ["pkexec"]
    with tempfile.TemporaryFile(mode="w+t") as log:
        job = subprocess.Popen([*prefix, HELPER, *args], stdout=log, stderr=subprocess.STDOUT)
        progress = subprocess.Popen(["zenity", "--progress", "--title=System updates", "--text=Working. Keep the drive connected.", "--pulsate", "--auto-close", "--no-cancel"], stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, text=True)
        while job.poll() is None:
            time.sleep(0.3)
        try:
            progress.communicate("100\n", timeout=5)
        except (BrokenPipeError, subprocess.TimeoutExpired):
            progress.terminate()
        log.seek(0)
        message = log.read()[-16000:]
        # Package-manager output needs a scrollable window rather than a giant label.
        title = "System update complete" if job.returncode == 0 else "System update did not complete"
        subprocess.run(["zenity", "--text-info", "--title=" + title, "--width=720", "--height=480"],
                       input=message or "The operation was cancelled.", text=True)


def gui(notify=False):
    updater = Updater()
    if notify:
        marker = Path(os.environ.get("XDG_CACHE_HOME", str(Path.home() / ".cache"))) / "doshy/update-check"
        if marker.exists() and time.time() - marker.stat().st_mtime < 86400:
            return
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.touch()
        try:
            latest = updater.check()
        except (UpdateError, OSError, ValueError):
            return
        if not latest["available"]:
            return
    else:
        code, action = dialog("list", "Updates preserve your files and personal settings.", "--column=Action", "--hide-header",
                              "Check for updates", "Set GitHub repository", "Restore previous update")
        if code:
            return
        if action == "Set GitHub repository":
            code, repo = dialog("entry", "Enter the public GitHub repository you trust to publish doshy OS updates (owner/repository).")
            if not code:
                privileged("configure", repository(repo))
            return
        if action == "Restore previous update":
            code, _ = dialog("question", "Restore the previous doshy OS files? Your personal files remain unchanged. Added Debian packages will remain installed.")
            if not code:
                privileged("rollback")
            return
        latest = updater.check()
        if not latest["available"]:
            dialog("info", "You are up to date. Installed version: " + latest["installed"])
            return
    code, _ = dialog("question", f"doshy OS {latest['version']} is available from {latest['repository']}.\nInstalled: {latest['installed']}\n\nDownload and install now? Keep the drive connected until it finishes.", "--ok-label=Install update", "--cancel-label=Later")
    if not code:
        privileged("apply", latest["version"])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["gui", "check", "configure", "apply", "rollback", "seed", "recover"])
    parser.add_argument("value", nargs="?")
    parser.add_argument("--notify", action="store_true")
    args = parser.parse_args()
    try:
        updater = Updater()
        if args.action not in ("gui", "check") and os.geteuid() != 0:
            raise UpdateError("Administrator authentication is required.")
        if args.action == "gui":
            gui(args.notify)
        elif args.action == "check":
            print(json.dumps(updater.check()))
        elif args.action == "configure":
            with updater.lock():
                save_json(updater.path(CONFIG), {"repository": repository(args.value or "")})
            print("Update source saved. Choose Check for updates to continue.")
        elif args.action == "apply":
            version(args.value)
            updater.apply(args.value)
        elif args.action == "rollback":
            updater.rollback()
        elif args.action == "seed":
            seed()
        elif args.action == "recover":
            with updater.lock():
                updater.recover()
    except (UpdateError, OSError, ValueError, subprocess.SubprocessError) as error:
        if args.action == "gui" and not args.notify:
            dialog("error", str(error))
        else:
            print("Update failed: " + str(error), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
