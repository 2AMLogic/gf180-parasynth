#!/usr/bin/env python3
"""Install a content-pinned Linux OSS CAD Suite for the 85F build."""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
import platform
import subprocess
import tarfile
import urllib.request

VERSION = "2026-09-21"
ARCHIVE = "oss-cad-suite-linux-x64-20260921.tgz"
SHA256 = "fc11a9c05c1de96b2821a5468ed02ba35253b278a78106cbe68167a5c419970c"
URL = f"https://github.com/YosysHQ/oss-cad-suite-build/releases/download/{VERSION}/{ARCHIVE}"


def extract_verified(archive, destination, expected_sha256=SHA256):
    with Path(archive).open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    if digest != expected_sha256:
        raise RuntimeError("OSS CAD archive checksum mismatch")
    with tarfile.open(archive) as bundle:
        bundle.extractall(destination, filter="data")
    return digest


def main():
    if platform.system() != "Linux" or platform.machine() not in ("x86_64", "AMD64"):
        raise RuntimeError("pinned toolchain requires Linux x86-64")
    root = Path(__file__).resolve().parents[1]
    destination = root / "build/oss-cad-pinned"
    destination.mkdir(parents=True, exist_ok=True)
    archive = destination / ARCHIVE
    urllib.request.urlretrieve(URL, archive)
    digest = extract_verified(archive, destination)
    binary = destination / "oss-cad-suite/bin"
    for name in ("yosys", "nextpnr-ecp5", "ecppack"):
        if not (binary / name).is_file():
            raise RuntimeError(f"pinned toolchain lacks {name}")
    versions = {}
    for name, option in (("yosys", "-V"), ("nextpnr-ecp5", "--version")):
        result = subprocess.run([str(binary / name), option], check=True, capture_output=True, text=True)
        versions[name] = (result.stdout + result.stderr).strip()
    (destination / "toolchain.json").write_text(json.dumps({
        "release": VERSION, "url": URL, "sha256": digest, "versions": versions}, indent=2) + "\n")
    with Path(os.environ["GITHUB_PATH"]).open("a") as paths:
        paths.write(str(binary) + "\n")
    print(json.dumps(versions), flush=True)


if __name__ == "__main__":
    main()
