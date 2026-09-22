#!/usr/bin/env python3
"""Bounded probe: does automatic envelope-function lifetime fix old Verilator?

The ordinary verifier is the clean comparator. This changes only function
lifetime in a temporary source and records a separate outcome; it does not
modify the shipping RTL or weaken its bit-exact comparison.
"""
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def main():
    directory = ROOT / "build/verilator-envelope-probe"
    directory.mkdir(parents=True, exist_ok=True)
    source = (ROOT / "rtl-sketch/voice_dp.v").read_text()
    anchor = "function [25:0] env_update("
    if source.count(anchor) != 1:
        print("REFUSED: envelope lifetime probe anchor changed")
        return 2
    variant = directory / "voice_dp.v"
    variant.write_text(source.replace(anchor, "function automatic [25:0] env_update("))
    command = [sys.executable, str(ROOT / "rtl-sketch/verify_voice.py"),
               "--set", "quick", "--only", "waves3", "--filter2x", "--pulse2x",
               "--simulator", "verilator", "--rtl", str(variant),
               "--outdir", str(directory)]
    return subprocess.run(command, cwd=ROOT).returncode


if __name__ == "__main__":
    raise SystemExit(main())
