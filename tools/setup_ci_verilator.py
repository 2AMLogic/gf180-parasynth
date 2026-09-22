#!/usr/bin/env python3
"""Build the exact Verilator revision used to qualify the full audio phrases."""
from pathlib import Path
import os
import subprocess

REVISION = "ea338be98e1e838d3518809ce8899f85a009963c"  # upstream v5.052
ROOT = Path(__file__).resolve().parents[1]


def main():
    source = ROOT / ".tools/verilator-5.052"
    if not source.exists():
        subprocess.run(["git", "clone", "--depth", "1", "--branch", "v5.052",
                        "https://github.com/verilator/verilator.git", str(source)], check=True)
    revision = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=source, text=True).strip()
    if revision != REVISION:
        raise RuntimeError(f"Verilator source revision differs: {revision}")
    executable = source / "bin/verilator_bin"
    if not executable.is_file():
        for command in (["autoconf"], ["./configure"], ["make", "-C", "src", "-j2", "opt"]):
            subprocess.run(command, cwd=source, check=True)
    version = subprocess.check_output([str(source / "bin/verilator"), "--version"], text=True).strip()
    if not version.startswith("Verilator 5.052 "):
        raise RuntimeError(f"unexpected simulator: {version}")
    print(version, revision)
    if os.environ.get("GITHUB_PATH"):
        with open(os.environ["GITHUB_PATH"], "a") as output:
            output.write(str(source / "bin") + "\n")


if __name__ == "__main__":
    main()
