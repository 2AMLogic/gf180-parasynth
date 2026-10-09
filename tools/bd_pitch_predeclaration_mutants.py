#!/usr/bin/env python3
"""Mutation controls for the #557 freeze's guards (PR #601 reviews).

    python3 tools/bd_pitch_predeclaration_mutants.py

Each mutant removes ONE guard from a scratch copy of the tree (never this
checkout) and runs the focused controls named for it.  Every mutant must turn
them RED (pytest exit 1); any other outcome is reported and the script exits 1.
This is how the controls added in the third review were shown to start red with
the fix removed.  It covers the guards the start-red stub cannot reach: the
stub replaces bd_pitch_predeclaration but not tools/bd_glide_phase_sweep.py.

Input that defeats it (rule 8): a mutant whose `old` text no longer appears in
the source.  That is caught: the script REFUSES (exit 2) rather than reporting
a mutant that never ran as red.
"""
from __future__ import annotations

import pathlib
import shutil
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
TEST = "tools/test_bd_pitch_predeclaration.py"
BPP = "tools/bd_pitch_predeclaration.py"
SWP = "tools/bd_glide_phase_sweep.py"

# name: (file, old, new, pytest -k expression)
MUTANTS = {
    "spread-negative-allowed": (
        BPP, 'raise Refused(f"{what} = {v!r} is negative; it is a magnitude")', "pass",
        "negative-spread"),
    "bool-allowed": (
        BPP, "if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):\n"
             "        raise Refused(f\"{what} = {v!r} is not a finite number\")",
        "if not isinstance(v, (int, float)) or not math.isfinite(v):\n"
        "        raise Refused(f\"{what} = {v!r} is not a finite number\")",
        "bool-spread"),
    "deficit-unvalidated": (
        BPP, 'deficit = _number(pair.get("glide_deficit_cents"),',
        'deficit = float(pair.get("glide_deficit_cents")) or (', "deficit"),
    "threshold-unvalidated": (BPP, "or need <= 0:", "or need <= -1e9:", "threshold"),
    "strict-excess": (BPP, "satisfiable=ship >= need)", "satisfiable=ship > need)", "equality"),
    "one-take-decides": (BPP, "satisfiable=ship >= need)", "satisfiable=deficit >= need)",
                         "acceptance_statistic"),
    "no-readings-claims": (BPP, "        out.update(satisfiable=None,",
                           "        out.update(satisfiable=deficit >= need,",
                           "without_readings or one_take"),
    "sweep-below-check-removed": (
        SWP, '    if c < w["phase_only"] - CLAIM_TOL_CENTS:\n        out.append(f"claimed worst',
        '    if False:\n        out.append(f"claimed worst', "claim_below"),
    "sweep-grid-check-removed": (SWP, "    if got_f0 != list(F0_GRID):", "    if False:",
                                 "coarse_grid"),
    "sweep-coarse-f0-grid": (SWP, "F0_LO, F0_HI, F0_STEP = 40.0, 65.0, 0.5",
                             "F0_LO, F0_HI, F0_STEP = 45.0, 55.0, 5.0",
                             "onset_phase or grid_is_fine"),
    "sweep-edge-below-removed": (
        SWP, '    if c < w["phase_only"] - CLAIM_TOL_CENTS:\n        out.append(f"range-wide',
        '    if False:\n        out.append(f"range-wide', "coarse_worst"),
}


def main() -> int:
    bad = 0
    with tempfile.TemporaryDirectory(prefix="bpp-mutants-") as tmp:
        for name, (rel, old, new, k) in MUTANTS.items():
            dst = pathlib.Path(tmp) / name
            # the whole tree: check() resolves probe paths anywhere in the repo
            shutil.copytree(ROOT, dst, ignore=shutil.ignore_patterns(
                ".git", ".loom", "build", "__pycache__", ".pytest_cache", "node_modules"))
            f = dst / rel
            src = f.read_text()
            if src.count(old) != 1:
                print(f"REFUSED: mutant {name}: target text found {src.count(old)} times in {rel}")
                return 2
            f.write_text(src.replace(old, new))
            r = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
                                TEST, "-k", k], cwd=dst, capture_output=True, text=True)
            tail = (r.stdout.strip().splitlines() or ["<no output>"])[-1]
            red = r.returncode == 1
            bad += not red
            print(f"{name:28s} {'RED' if red else f'NOT RED (pytest exit {r.returncode})'}  {tail}")
    print("all mutants red" if not bad else f"FAIL: {bad} mutant(s) not red")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
