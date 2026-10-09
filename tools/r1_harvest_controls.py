#!/usr/bin/env python3
"""Injected-defect controls for tools/r1_harvest.py (#609). `make controls`.

Each mutant is a copy of the harvester with one verdict-bearing line broken,
declared against the ONE test in tools/test_r1_harvest.py that must go red. The
clean copy must be green first (start-red needs a green baseline to mean
anything). CAUGHT / BLIND / NO VERDICT; exit 1 unless every mutant is caught.
A mutation whose anchor text is absent is a NO VERDICT (the harvester moved),
never a silent pass.
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "tools" / "r1_harvest.py"
TESTS = "tools/test_r1_harvest.py"
ROOT_LINE = "ROOT = Path(__file__).resolve().parents[1]"

# name -> (anchor, replacement, test that must fail)
MUTANTS = {
    "INVALID_BRANCH_REMOVED": (
        '"receipt_valid": chk.returncode == 0,', '"receipt_valid": True,',
        "test_a_receipt_the_real_checker_rejects_is_listed_invalid_and_exits_nonzero"),
    "INVALID_DROPPED": (
        "        t, mode = rec[", "        if chk.returncode != 0:\n            continue\n"
        "        t, mode = rec[",
        "test_a_receipt_the_real_checker_rejects_is_listed_invalid_and_exits_nonzero"),
    "EXIT_ALWAYS_ZERO": (
        "return 1 if bad else 0", "return 0",
        "test_one_invalid_receipt_among_valid_ones_still_fails_the_run"),
    "MALFORMED_SKIPPED": (
        "        rec = json.loads(rp.read_text())\n",
        "        try:\n            rec = json.loads(rp.read_text())\n"
        "        except ValueError:\n            continue\n",
        "test_a_malformed_receipt_is_refused_not_skipped"),
    "NULL_COUNTED_VALID": (
        '"receipt_valid": None,', '"receipt_valid": True,',
        "test_the_domain_test_row_has_receipt_valid_null_and_is_not_counted_valid"),
}


def pytest_run(module: Path, k: str | None = None) -> subprocess.CompletedProcess:
    cmd = [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", TESTS]
    if k:
        cmd += ["-k", k]
    return subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True,
                          env={**os.environ, "R1_HARVEST_MODULE": str(module)})


def main() -> int:
    src = SRC.read_text()
    assert ROOT_LINE in src, "r1_harvest ROOT line moved; update this control"
    bad = 0
    with tempfile.TemporaryDirectory() as td:
        def write(name, text):
            p = Path(td) / f"{name}.py"
            p.write_text(text.replace(ROOT_LINE, f"ROOT = Path({str(ROOT)!r})"))
            return p
        clean = pytest_run(write("clean", src))
        print(f"{'CLEAN':<26} {'green' if clean.returncode == 0 else 'RED -- no baseline'}")
        if clean.returncode != 0:
            print(clean.stdout[-2000:])
            return 1
        for name, (anchor, repl, test) in MUTANTS.items():
            if src.count(anchor) != 1:
                print(f"{name:<26} NO VERDICT (anchor found {src.count(anchor)}x)")
                bad += 1
                continue
            r = pytest_run(write(name, src.replace(anchor, repl)))
            failed = [ln for ln in r.stdout.splitlines() if ln.startswith("FAILED")]
            caught = r.returncode != 0 and any(test in ln for ln in failed)
            print(f"{name:<26} {'CAUGHT by ' + test if caught else 'BLIND'}")
            bad += not caught
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
