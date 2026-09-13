"""Updater tests operate only inside temporary directories, without apt or root."""
import copy
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.dont_write_bytecode = True
spec = importlib.util.spec_from_file_location("doshy_update", ROOT / "config/includes.chroot/usr/local/lib/doshy/update_core.py")
core = importlib.util.module_from_spec(spec)
spec.loader.exec_module(core)
FILE = "/usr/local/bin/doshy-example"
SETTING = "/etc/xdg/doshy.conf"


def manifest(files, number="1.1.0", packages=None):
    return {"format": 1, "version": number, "base": "bookworm", "architecture": "amd64",
            "packages": packages or [], "files": {p: {"sha256": core.digest(b), "mode": 0o644} for p, b in files.items()}}


def bundle(meta, files, extra=None):
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode="w:gz") as archive:
        for name, data in {"manifest.json": json.dumps(meta).encode(), **{"root" + p: b for p, b in files.items()}}.items():
            item = tarfile.TarInfo(name)
            item.size = len(data)
            archive.addfile(item, io.BytesIO(data))
        if extra:
            archive.addfile(extra)
    return stream.getvalue()


class UpdateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.updater = core.Updater(self.root)
        self.initial = {FILE: b"old program", SETTING: b"original setting"}
        for p, b in self.initial.items():
            core.atomic(self.updater.path(p), b)
        core.save_json(self.updater.path(core.BASELINE), manifest(self.initial, "1.0.0"))
        core.atomic(self.updater.path("/home/person/Documents/keep.txt"), b"personal file")

    def tearDown(self):
        self.temp.cleanup()

    def test_update_preserves_home_and_records_version(self):
        files = {FILE: b"new", SETTING: self.initial[SETTING]}
        conflicts = self.updater.install_files(manifest(files), files)
        self.assertEqual(conflicts, [])
        self.assertEqual(self.updater.path(FILE).read_bytes(), b"new")
        self.assertEqual(self.updater.state()["version"], "1.1.0")
        self.assertEqual(self.updater.path("/home/person/Documents/keep.txt").read_bytes(), b"personal file")

    def test_cumulative_update_can_skip_versions(self):
        files = {FILE: b"latest", "/usr/share/doshy/new.txt": b"new feature"}
        self.updater.install_files(manifest(files, "3.0.0"), files)
        self.assertEqual(self.updater.state()["version"], "3.0.0")
        self.assertFalse(self.updater.path(SETTING).exists())

    def test_local_settings_and_deletions_are_preserved(self):
        core.atomic(self.updater.path(SETTING), b"my setting")
        self.updater.path(FILE).unlink()
        files = {FILE: b"new", SETTING: b"new default"}
        conflicts = self.updater.install_files(manifest(files), files)
        self.assertEqual(set(conflicts), {FILE, SETTING})
        self.assertEqual(self.updater.path(SETTING).read_bytes(), b"my setting")
        self.assertFalse(self.updater.path(FILE).exists())
        self.assertEqual((self.updater.storage / "conflicts/1.1.0/etc/xdg/doshy.conf").read_bytes(), b"new default")

    def test_unmanaged_collision_preserved(self):
        name = "/usr/local/bin/doshy-custom"
        core.atomic(self.updater.path(name), b"mine")
        files = {**self.initial, name: b"theirs"}
        self.assertEqual(self.updater.install_files(manifest(files), files), [name])
        self.assertEqual(self.updater.path(name).read_bytes(), b"mine")

    def test_modified_obsolete_file_preserved(self):
        core.atomic(self.updater.path(SETTING), b"mine")
        files = {FILE: b"new"}
        self.assertIn(SETTING, self.updater.install_files(manifest(files), files))
        self.assertEqual(self.updater.path(SETTING).read_bytes(), b"mine")

    def test_failure_restores_all_files_and_version(self):
        real_atomic = core.atomic
        fired = False

        def fail_once(path, data, mode=0o644):
            nonlocal fired
            if path == self.updater.path(FILE) and data == b"new" and not fired:
                fired = True
                raise OSError("simulated full drive")
            return real_atomic(path, data, mode)

        files = {FILE: b"new", SETTING: b"changed"}
        with patch.object(core, "atomic", side_effect=fail_once):
            with self.assertRaises(OSError):
                self.updater.install_files(manifest(files), files)
        for name, data in self.initial.items():
            self.assertEqual(self.updater.path(name).read_bytes(), data)
        self.assertEqual(self.updater.state()["version"], "1.0.0")
        self.assertFalse((self.updater.storage / "pending.json").exists())

    def test_recovery_uses_durable_journal(self):
        files = {FILE: b"new", SETTING: b"changed"}
        self.updater.install_files(manifest(files), files)
        journal = core.read_json(self.updater.storage / "last.json")
        core.save_json(self.updater.storage / "pending.json", journal)
        fresh = core.Updater(self.root)
        fresh.recover()
        self.assertEqual(fresh.state()["version"], "1.0.0")
        self.assertEqual(fresh.path(FILE).read_bytes(), self.initial[FILE])

    def test_rollback_restores_previous_release(self):
        files = {FILE: b"new", SETTING: b"changed"}
        self.updater.install_files(manifest(files), files)
        with patch.object(self.updater, "lock", return_value=core.contextlib.nullcontext()):
            self.updater.rollback()
        self.assertEqual(self.updater.state()["version"], "1.0.0")
        self.assertEqual(self.updater.path(FILE).read_bytes(), self.initial[FILE])

    def test_rollback_preserves_post_update_edits(self):
        files = {FILE: b"new", SETTING: b"changed"}
        self.updater.install_files(manifest(files), files)
        core.atomic(self.updater.path(FILE), b"edited after update")
        with patch.object(self.updater, "lock", return_value=core.contextlib.nullcontext()):
            with self.assertRaises(core.UpdateError):
                self.updater.rollback()
        self.assertEqual(self.updater.path(FILE).read_bytes(), b"edited after update")

    def test_valid_bundle_roundtrip(self):
        meta = manifest(self.initial)
        unpacked, files = core.unpack(bundle(meta, self.initial), "1.1.0")
        self.assertEqual(unpacked, meta)
        self.assertEqual(files, self.initial)

    def test_rejects_corrupted_file(self):
        meta = manifest(self.initial)
        files = {**self.initial, FILE: b"tampered"}
        with self.assertRaises(core.UpdateError):
            core.unpack(bundle(meta, files), "1.1.0")

    def test_rejects_wrong_base_arch_version(self):
        for field, value in (("base", "trixie"), ("architecture", "arm64"), ("version", "8.0.0")):
            meta = manifest(self.initial)
            meta[field] = value
            with self.subTest(field=field), self.assertRaises(core.UpdateError):
                core.unpack(bundle(meta, self.initial), "1.1.0")

    def test_rejects_traversal_and_personal_paths(self):
        for name in ("/home/person/x", "/etc/shadow", "/boot/vmlinuz", "/usr/local/bin/../secret", "relative", core.CONFIG):
            files = {name: b"bad"}
            with self.subTest(name=name), self.assertRaises(core.UpdateError):
                core.unpack(bundle(manifest(files), files), "1.1.0")

    def test_rejects_links_and_duplicates(self):
        for kind in (tarfile.SYMTYPE, tarfile.LNKTYPE, tarfile.DIRTYPE, tarfile.REGTYPE):
            item = tarfile.TarInfo("root" + FILE)
            item.type = kind
            item.linkname = "/etc/shadow"
            with self.subTest(kind=kind), self.assertRaises(core.UpdateError):
                core.unpack(bundle(manifest(self.initial), self.initial, item), "1.1.0")

    def test_rejects_unlisted_files_and_privileged_modes(self):
        files = {**self.initial, "/usr/share/doshy/unlisted": b"extra"}
        with self.assertRaises(core.UpdateError):
            core.unpack(bundle(manifest(self.initial), files), "1.1.0")
        meta = manifest(self.initial)
        meta["files"][FILE]["mode"] = 0o4755
        with self.assertRaises(core.UpdateError):
            core.unpack(bundle(meta, self.initial), "1.1.0")

    def test_rejects_package_options_and_boot_changes(self):
        for package in ("--allow-unauthenticated", "linux-image-amd64", "grub-pc", "pkg;rm", "../file.deb"):
            with self.subTest(package=package), self.assertRaises(core.UpdateError):
                core.unpack(bundle(manifest(self.initial, packages=[package]), self.initial), "1.1.0")

    def test_refuses_symlink_destinations(self):
        target = self.updater.path(FILE)
        target.unlink()
        try:
            target.symlink_to(self.updater.path("/home/person/Documents/keep.txt"))
        except OSError:
            self.skipTest("Symlinks unavailable on this Windows account")
        with self.assertRaises(core.UpdateError):
            self.updater.plan(manifest({FILE: b"new"}), {FILE: b"new"})

    def test_numeric_versions(self):
        self.assertGreater(core.version("1.10.0"), core.version("1.9.9"))
        for value in ("main", "v1.0", "1.0.0-beta", "1.0.0;whoami"):
            with self.assertRaises(core.UpdateError):
                core.version(value)

    def test_repository_configuration_validation(self):
        self.assertEqual(core.repository("https://github.com/person/OS/"), "person/OS")
        for value in ("", "http://evil/x", "person/repo/../x", "person/repo?x"):
            with self.assertRaises(core.UpdateError):
                core.repository(value)

    def test_release_requires_complete_same_repository_assets(self):
        def info():
            return {"tag_name": "v1.1.0", "assets": [{"name": n, "browser_download_url": "https://github.com/person/OS/releases/download/v1.1.0/" + n} for n in (core.ASSET, core.ASSET + ".sha256")]}
        with patch.object(core, "download", return_value=json.dumps(info()).encode()):
            self.assertEqual(core.release("person/OS")["version"], "1.1.0")
        for broken in (dict(info(), assets=[]), dict(info(), prerelease=True), dict(info(), tag_name="main")):
            with patch.object(core, "download", return_value=json.dumps(broken).encode()), self.assertRaises(core.UpdateError):
                core.release("person/OS")
        bad = info()
        bad["assets"][0]["browser_download_url"] = "https://evil.invalid/payload"
        with patch.object(core, "download", return_value=json.dumps(bad).encode()), self.assertRaises(core.UpdateError):
            core.release("person/OS")

    def test_live_update_requires_real_persistent_upper(self):
        core.atomic(self.updater.path("/etc/os-release"), b"VERSION_CODENAME=bookworm\n")
        self.updater.path("/run/live/medium").mkdir(parents=True)
        mounts = "1 0 0:1 / / rw - overlay overlay rw,upperdir=/run/live/overlay/rw\n2 1 0:2 / /run/live/overlay rw - tmpfs tmpfs rw\n"
        with patch.object(core.subprocess, "check_output", return_value="amd64\n"):
            core.atomic(self.updater.path("/proc/self/mountinfo"), mounts.encode())
            with self.assertRaises(core.UpdateError):
                self.updater.check_system()
            mounts = mounts.replace("tmpfs tmpfs", "ext4 /dev/sdb2")
            core.atomic(self.updater.path("/proc/self/mountinfo"), mounts.encode())
            self.updater.check_system()

    def test_full_install_does_not_require_live_persistence(self):
        core.atomic(self.updater.path("/etc/os-release"), b"VERSION_CODENAME=bookworm\n")
        with patch.object(core.subprocess, "check_output", return_value="amd64\n"):
            self.updater.check_system()

    def test_actual_release_builder_roundtrip(self):
        result = subprocess.run([sys.executable, str(ROOT / "scripts/build-update.py"), "--output", str(self.root / "release")], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        asset = self.root / "release" / core.ASSET
        current = (ROOT / "config/includes.chroot/usr/share/doshy/version").read_text().strip()
        meta, payload = core.unpack(asset.read_bytes(), current)
        self.assertIn("/usr/local/bin/doshy-update", payload)
        self.assertNotIn(core.CONFIG, payload)
        self.assertEqual(core.digest(asset.read_bytes()), asset.with_name(core.ASSET + ".sha256").read_text().split()[0])

    def latest(self, number="1.1.0"):
        return {"version": number, "available": True, "assets": {
            core.ASSET: {"browser_download_url": "https://github.com/person/OS/bundle"},
            core.ASSET + ".sha256": {"browser_download_url": "https://github.com/person/OS/checksum"}}}

    def test_apply_download_verify_packages_install_end_to_end(self):
        files = {**self.initial, FILE: b"from GitHub"}
        data = bundle(manifest(files, packages=["python3"]), files)
        checksum = f"{core.digest(data)}  {core.ASSET}\n".encode()
        with patch.object(self.updater, "lock", return_value=core.contextlib.nullcontext()), \
                patch.object(self.updater, "check_system"), \
                patch.object(self.updater, "check", return_value=self.latest()), \
                patch.object(core, "download", side_effect=[checksum, data]), \
                patch.object(core.subprocess, "run") as run:
            self.updater.apply("1.1.0")
        self.assertEqual(self.updater.path(FILE).read_bytes(), b"from GitHub")
        self.assertEqual(self.updater.state()["version"], "1.1.0")
        install = run.call_args_list[1].args[0]
        self.assertIn("--no-remove", install)
        self.assertIn("Dpkg::Options::=--force-confold", install)

    def test_checksum_failure_never_runs_apt_or_writes_files(self):
        data = bundle(manifest(self.initial), self.initial)
        checksum = f"{'0' * 64}  {core.ASSET}\n".encode()
        with patch.object(self.updater, "lock", return_value=core.contextlib.nullcontext()), \
                patch.object(self.updater, "check_system"), \
                patch.object(self.updater, "check", return_value=self.latest()), \
                patch.object(core, "download", side_effect=[checksum, data]), \
                patch.object(core.subprocess, "run") as run:
            with self.assertRaises(core.UpdateError):
                self.updater.apply("1.1.0")
        run.assert_not_called()
        self.assertEqual(self.updater.state()["version"], "1.0.0")

    def test_package_failure_leaves_os_files_and_version_unchanged(self):
        files = {**self.initial, FILE: b"new"}
        data = bundle(manifest(files, packages=["python3"]), files)
        checksum = f"{core.digest(data)}  {core.ASSET}\n".encode()
        with patch.object(self.updater, "lock", return_value=core.contextlib.nullcontext()), \
                patch.object(self.updater, "check_system"), \
                patch.object(self.updater, "check", return_value=self.latest()), \
                patch.object(core, "download", side_effect=[checksum, data]), \
                patch.object(core.subprocess, "run", side_effect=subprocess.CalledProcessError(100, "apt-get")):
            with self.assertRaises(subprocess.CalledProcessError):
                self.updater.apply("1.1.0")
        self.assertEqual(self.updater.state()["version"], "1.0.0")
        self.assertEqual(self.updater.path(FILE).read_bytes(), self.initial[FILE])

    def test_release_changed_since_confirmation_stops_download(self):
        with patch.object(self.updater, "lock", return_value=core.contextlib.nullcontext()), \
                patch.object(self.updater, "check_system"), \
                patch.object(self.updater, "check", return_value=self.latest("1.2.0")), \
                patch.object(core, "download") as fetch:
            with self.assertRaises(core.UpdateError):
                self.updater.apply("1.1.0")
        fetch.assert_not_called()

    def test_seed_records_actual_post_hook_files(self):
        core.atomic(self.updater.path(core.VERSION), b"1.0.0\n")
        core.save_json(self.updater.path("/usr/share/doshy/update-paths.json"), [FILE, SETTING])
        core.atomic(self.updater.path(FILE), b"build hook result")
        core.seed(self.root)
        self.assertEqual(self.updater.state()["files"][FILE]["sha256"], core.digest(b"build hook result"))

    @unittest.skipUnless(os.name == "posix", "Requires Linux flock and Unix permissions")
    def test_lock_rejects_concurrent_update(self):
        second = core.Updater(self.root)
        with self.updater.lock():
            with self.assertRaises(core.UpdateError):
                with second.lock():
                    self.fail("Second updater acquired lock")

    @unittest.skipUnless(os.name == "posix", "Requires Unix executable permissions")
    def test_mode_change_with_identical_contents_is_applied(self):
        meta = manifest(self.initial)
        meta["files"][FILE]["mode"] = 0o755
        self.updater.install_files(meta, self.initial)
        self.assertEqual(self.updater.path(FILE).stat().st_mode & 0o777, 0o755)
        with self.updater.lock():
            self.updater.restore(core.read_json(self.updater.storage / "last.json"))
        self.assertEqual(self.updater.path(FILE).stat().st_mode & 0o777, 0o644)

    def test_insufficient_backup_space_stops_before_writes(self):
        files = {**self.initial, FILE: b"new"}
        usage = core.shutil._ntuple_diskusage(total=100, used=99, free=1)
        with patch.object(core.shutil, "disk_usage", return_value=usage):
            with self.assertRaises(core.UpdateError):
                self.updater.install_files(manifest(files), files)
        self.assertEqual(self.updater.path(FILE).read_bytes(), self.initial[FILE])


if __name__ == "__main__":
    unittest.main()
