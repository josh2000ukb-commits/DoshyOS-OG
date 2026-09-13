#!/usr/bin/env python3
"""Verify an old-to-new bundle migration and rollback inside a temporary root."""
import argparse
import importlib.util
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.dont_write_bytecode = True
spec = importlib.util.spec_from_file_location("core", ROOT / "config/includes.chroot/usr/local/lib/doshy/update_core.py")
core = importlib.util.module_from_spec(spec)
spec.loader.exec_module(core)


def verify(old_asset, old_version, new_asset, new_version):
    old_manifest, old_files = core.unpack(Path(old_asset).read_bytes(), old_version)
    new_manifest, new_files = core.unpack(Path(new_asset).read_bytes(), new_version)
    with tempfile.TemporaryDirectory(prefix="doshy-upgrade-") as directory:
        updater = core.Updater(Path(directory))
        for name, data in old_files.items():
            core.atomic(updater.path(name), data, old_manifest["files"][name]["mode"])
        core.save_json(updater.path(core.BASELINE), old_manifest)
        personal = updater.path("/home/example/Documents/keep.txt")
        core.atomic(personal, b"keep my files")
        conflicts = updater.install_files(new_manifest, new_files)
        assert not conflicts, conflicts
        assert updater.state()["version"] == new_version
        for name, data in new_files.items():
            assert updater.path(name).read_bytes() == data, name
        assert personal.read_bytes() == b"keep my files"
        updater.rollback()
        assert updater.state()["version"] == old_version
        for name, data in old_files.items():
            assert updater.path(name).read_bytes() == data, name
        assert personal.read_bytes() == b"keep my files"
        print(f"Verified {old_version} → {new_version}, all {len(new_files)} managed files, personal-file preservation and rollback.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("old_asset")
    parser.add_argument("old_version")
    parser.add_argument("new_asset")
    parser.add_argument("new_version")
    args = parser.parse_args()
    verify(args.old_asset, args.old_version, args.new_asset, args.new_version)
