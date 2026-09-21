#!/usr/bin/env python3
"""Restore the frozen Surge WAVs without a plugin or a fresh render.

The committed profile is the authority. Validate the entire archive against
its file set, sizes and SHA-256 hashes before writing any cache file.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]


class Refused(RuntimeError):
    pass


def restore(archive: Path, profile: Path) -> int:
    directory = profile.parent.resolve()
    try:
        entries = json.loads(profile.read_text())["clips"]
        if not isinstance(entries, dict) or not entries:
            raise Refused("profile has no frozen clips")
        targets = {}
        for entry in entries.values():
            name = entry["file"]
            path = PurePosixPath(name)
            dest = (directory / name).resolve()
            if (path.is_absolute() or ".." in path.parts or path.parts[0] != "cache"
                    or not dest.is_relative_to(directory / "cache")):
                raise Refused(f"invalid cache path: {name}")
            if name in targets:
                raise Refused(f"duplicate cache path: {name}")
            targets[name] = (dest, entry)
        verified = {}
        with zipfile.ZipFile(archive) as bundle:
            names = bundle.namelist()
            if len(names) != len(set(names)) or set(names) != set(targets):
                raise Refused("archive file set differs from frozen profile")
            for name, (dest, entry) in targets.items():
                if bundle.getinfo(name).file_size != entry["bytes"]:
                    raise Refused(f"archive size mismatch: {name}")
                data = bundle.read(name)
                if hashlib.sha256(data).hexdigest() != entry["sha256"]:
                    raise Refused(f"archive SHA-256 mismatch: {name}")
                verified[dest] = data
        for dest, data in verified.items():
            dest.parent.mkdir(parents=True, exist_ok=True)
            if dest.exists() and dest.read_bytes() == data:
                continue
            temporary = dest.with_suffix(".wav.part")
            temporary.write_bytes(data)
            temporary.replace(dest)
        for dest, data in verified.items():
            if dest.read_bytes() != data:
                raise Refused(f"restored cache differs from validated archive: {dest}")
        return len(verified)
    except (OSError, ValueError, KeyError, zipfile.BadZipFile) as exc:
        raise Refused(f"cannot restore frozen archive {archive}: {exc}") from exc


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, default=ROOT / "refprofile/frozen-cache.zip")
    parser.add_argument("--profile", type=Path, default=ROOT / "refprofile/profile.json")
    args = parser.parse_args(argv)
    try:
        count = restore(args.archive, args.profile)
    except Refused as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 2
    print(f"OK: {count} frozen clips restored; every size and SHA-256 matches {args.profile}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
