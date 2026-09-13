#!/usr/bin/env python3
"""Prepare an ISO baseline or build a cumulative GitHub update (never an ISO)."""
import argparse
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import sys
import tarfile

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
INCLUDES = ROOT / "config/includes.chroot"
spec = importlib.util.spec_from_file_location("doshy_update", INCLUDES / "usr/local/lib/doshy/update_core.py")
core = importlib.util.module_from_spec(spec)
spec.loader.exec_module(core)
PATHS = "/usr/share/doshy/update-paths.json"


def collect():
    payload = {}
    for path in sorted(INCLUDES.rglob("*")):
        name = "/" + path.relative_to(INCLUDES).as_posix()
        if path.is_symlink():
            raise ValueError("Symlinks are not supported in the update source: " + name)
        if path.is_file() and core.managed(name) and name != PATHS and "__pycache__" not in path.parts:
            data = path.read_bytes()
            # Match build.sh's CRLF normalization without changing image bytes.
            if path.suffix != ".png":
                data = data.replace(b"\r\n", b"\n")
            payload[name] = data
    names = sorted([*payload, PATHS])
    payload[PATHS] = (json.dumps(names, indent=2) + "\n").encode()
    return payload


def build(output, expected=None, prepare=False):
    current = (INCLUDES / core.VERSION.lstrip("/")).read_text().strip()
    core.version(current)
    if expected and expected != current:
        raise ValueError(f"Tag version {expected} does not match usr/share/doshy/version ({current}).")
    payload = collect()
    if prepare:
        (INCLUDES / PATHS.lstrip("/")).write_bytes(payload[PATHS])
        print("Prepared managed-file list for the next ISO build.")
        return
    packages = []
    for line in (ROOT / "updates/packages.txt").read_text().splitlines():
        name = line.partition("#")[0].strip()
        if name:
            packages.append(name)
    manifest = {"format": 1, "version": current, "base": "bookworm", "architecture": "amd64",
                "packages": sorted(set(packages)),
                "files": {name: {"sha256": core.digest(data), "mode": core.file_mode(name)} for name, data in payload.items()}}
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    asset = output / core.ASSET
    with tarfile.open(asset, "w:gz", format=tarfile.USTAR_FORMAT) as archive:
        items = {"manifest.json": (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode(),
                 **{"root" + name: data for name, data in payload.items()}}
        for name, data in sorted(items.items()):
            item = tarfile.TarInfo(name)
            item.size, item.mode, item.mtime = len(data), 0o644, 0
            archive.addfile(item, io.BytesIO(data))
    core.unpack(asset.read_bytes(), current)  # Validate exactly as installed OS will.
    (output / (core.ASSET + ".sha256")).write_text(hashlib.sha256(asset.read_bytes()).hexdigest() + "  " + core.ASSET + "\n")
    print(f"Built doshy OS {current}: {asset} ({asset.stat().st_size:,} bytes)")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version")
    parser.add_argument("--output", default=str(ROOT / "out/updates"))
    parser.add_argument("--prepare", action="store_true")
    args = parser.parse_args()
    try:
        build(args.output, args.version, args.prepare)
    except (ValueError, core.UpdateError) as error:
        sys.exit(str(error))
