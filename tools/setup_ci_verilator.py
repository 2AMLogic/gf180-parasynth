#!/usr/bin/env python3
"""Build the exact Verilator revision used to qualify the full audio phrases.

`--assert-path` (issue #1154) is the refusal half: no build, no clone. It runs
`verilator --version` as resolved from PATH and exits non-zero naming expected
and actual unless the first line names the qualified build. rtl-full runs it as
a step with `id: verilator_identity` and gates the phrase step on that step's
outcome: a failed step does not set `cancelled()`, so a bare `!cancelled()`
phrase step would run the phrases under the apt verilator and emit an ungrounded
M5A/M5B report -- the exact bypass this gate exists to close.
"""
from pathlib import Path
import argparse
import os
import subprocess

REVISION = "ea338be98e1e838d3518809ce8899f85a009963c"  # upstream v5.052
EXPECTED_VERSION_PREFIX = "Verilator 5.052 "
ROOT = Path(__file__).resolve().parents[1]


def assert_simulator_identity(version_output: str) -> str:
    """Return the first line of `verilator --version` output, refusing with a
    RuntimeError that names expected and actual unless it starts with the
    qualified build prefix. Shared by the build path and `--assert-path`."""
    lines = version_output.strip().splitlines()
    first = lines[0] if lines else ""
    if not first.startswith(EXPECTED_VERSION_PREFIX):
        raise RuntimeError(
            "unexpected simulator: expected a version line starting with "
            f"{EXPECTED_VERSION_PREFIX!r}, actual {first or '(no output)'!r}"
        )
    return first


def assert_path_identity() -> str:
    """Refuse unless `verilator` as resolved from PATH is the qualified build."""
    try:
        version = subprocess.check_output(
            ["verilator", "--version"], text=True, stderr=subprocess.STDOUT
        )
    except FileNotFoundError as exc:
        raise RuntimeError(
            "unexpected simulator: no verilator resolved from PATH, expected a "
            f"version line starting with {EXPECTED_VERSION_PREFIX!r}"
        ) from exc
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(
            "unexpected simulator: verilator --version exited "
            f"{exc.returncode}: {(exc.output or '').strip()!r}"
        ) from exc
    line = assert_simulator_identity(version)
    print(line)
    return line


def build_pinned_verilator() -> None:
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
    print(assert_simulator_identity(version), revision)
    if os.environ.get("GITHUB_PATH"):
        with open(os.environ["GITHUB_PATH"], "a") as output:
            output.write(str(source / "bin") + "\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--assert-path",
        action="store_true",
        help="no build, no clone: refuse unless verilator on PATH is the qualified build",
    )
    args = parser.parse_args()
    if args.assert_path:
        assert_path_identity()
        return
    build_pinned_verilator()


if __name__ == "__main__":
    main()
