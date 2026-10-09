#!/usr/bin/env python3
"""Mutation controls for the #557 freeze's guards (PR #601 reviews).

    python3 tools/bd_pitch_predeclaration_mutants.py      # needs pytest + numpy

Each mutant removes ONE guard from a scratch copy of the tree (never this
checkout) and runs the focused controls named for it.  Every mutant must turn
them RED (pytest exit 1); any other outcome is reported and the script exits 1.
This backs the mutation-control claim in docs/bd-pitch-baseline-request.md with
code: it covers the guards the start-red stub cannot reach one at a time (the
stub replaces the whole validator, and does not touch
tools/bd_glide_phase_sweep.py at all).

Input that defeats it (rule 8): a mutant whose `old` text no longer appears
exactly once in the source would silently mutate nothing and the suite would
stay green -- reported as NOT RED, which is right, but for the wrong reason.
So a target found 0 or 2+ times REFUSES (exit 2) before anything runs.
Ported from doctor/601-alt-359303ec and re-targeted at this branch's code.
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
    # --- the nine named in the doc (third review round) ---
    "bool-accepted-as-number": (
        BPP, "return not isinstance(x, bool) and isinstance(x, (int, float)) and math.isfinite(x)",
        "return isinstance(x, (int, float)) and math.isfinite(x)",
        "invalid_baseline_numbers"),
    "negative-spread": (BPP, "if not _finite(v) or v < 0:", "if not _finite(v):",
                        "invalid_baseline_numbers"),
    "negative-threshold": (BPP, "if not _finite(need) or need < 0:", "if not _finite(need):",
                           "negative_evaluated_threshold"),
    "non-finite-deficit": (BPP, "if not _finite(deficit):", "if deficit is None:",
                           "invalid_baseline_numbers"),
    "strict-at-equality": (BPP, '"satisfiable": need <= best}', '"satisfiable": need < best}',
                           "equality"),
    "development-take-decides": (
        BPP, '"satisfiable": need <= best}',
        '"satisfiable": need <= one_take_diagnostic(rec, baseline)'
        '["development_take_deficit_cents"]}',
        "untouched_median"),
    "min-count-derivation-unchecked": (
        BPP, "if isinstance(k, int) and not isinstance(k, bool) and k != n - len(set(pred) & ids):",
        "if False:", "derivation"),
    "tone-holdout-removed": (BPP, 'for axis in ("tone", "decay"):', 'for axis in ("decay",):',
                             "tone_holdout"),
    "coarse-5hz-pitch-grid": (
        SWP, "F0S = tuple(float(f) for f in np.arange(40.0, 65.0 + 1e-9, 0.5))",
        "F0S = tuple(float(f) for f in np.arange(40.0, 65.0 + 1e-9, 5.0))",
        "grid_is_not_coarse"),
    # --- the fourth review round (DECAY knob 0 unqualified, #602) ---
    "unqualified-knob-admitted": (
        BPP, 'if (conds[cid].get("model") or {}).get("decay_knob") in uq:', "if False:",
        "knob0"),
    "predicted-not-tied-to-unqualified": (
        BPP, "if set(pred) != at_uq or len(pred) != len(set(pred)):", "if False:",
        "predicted_refusals_not_the_unqualified"),
    "non-dict-entry-unguarded": (BPP, "    if bad_rows:\n", "    if False:\n",
                                 "non_dict_reading_entry"),
}


def main() -> int:
    bad = 0
    with tempfile.TemporaryDirectory(prefix="bpp-mutants-") as tmp:
        dst = pathlib.Path(tmp) / "tree"
        # the whole tree once: check() resolves probe paths anywhere in the repo
        shutil.copytree(ROOT, dst, ignore=shutil.ignore_patterns(
            ".git", ".loom", "build", "__pycache__", ".pytest_cache", "node_modules"))
        originals = {rel: (dst / rel).read_text() for rel in (BPP, SWP)}
        for name, (rel, old, new, _) in MUTANTS.items():
            n = originals[rel].count(old)
            if n != 1:
                print(f"REFUSED: mutant {name}: target text found {n} times in {rel}")
                return 2
        for name, (rel, old, new, k) in MUTANTS.items():
            for r, src in originals.items():              # restore every file
                (dst / r).write_text(src)
            (dst / rel).write_text(originals[rel].replace(old, new))
            r = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
                                TEST, "-k", k], cwd=dst, capture_output=True, text=True)
            tail = (r.stdout.strip().splitlines() or ["<no output>"])[-1]
            red = r.returncode == 1
            bad += not red
            print(f"{name:36s} {'RED' if red else f'NOT RED (pytest exit {r.returncode})'}  {tail}")
    print(f"all {len(MUTANTS)} mutants red" if not bad else f"FAIL: {bad} mutant(s) not red")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
