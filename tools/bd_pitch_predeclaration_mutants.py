#!/usr/bin/env python3
"""Mutation controls for the #557 freeze's guards (PR #601 reviews).

    python3 tools/bd_pitch_predeclaration_mutants.py      # needs pytest + numpy

Each mutant removes ONE guard from a scratch copy of the tree (never this
checkout) and runs the focused controls named for it.  Every mutant must turn
them RED (pytest exit 1); any other outcome is reported and the script exits 1.
That reading of exit 1 is only sound because the baseline gate below has
already proved the same selection GREEN on the unmutated tree.
This backs the mutation-control claim in docs/bd-pitch-baseline-request.md with
code: it covers the guards the start-red stub cannot reach one at a time (the
stub replaces the whole validator, and does not touch
tools/bd_glide_phase_sweep.py at all).

Two inputs that defeat it (rule 8), both REFUSED (exit 2) before any verdict
line is printed:

  1. A mutant whose `old` text no longer appears exactly once in the source
     would silently mutate nothing and the suite would stay green -- reported
     as NOT RED, which is right, but for the wrong reason.  So a target found
     0 or 2+ times REFUSES.
  2. `pytest` exiting 1 for a reason that is not the removed guard.  `python -m
     pytest` exits 1 when the module is not installed, so on a host without it
     every mutant was counted RED with ZERO tests executed -- `all 12 mutants
     red`, exit 0, nothing measured (PR #757 review, reproduced on a worker
     whose system python3 has no pytest; Makefile's PY falls back to python3
     when .venv is absent, so `make controls` reached it).  A baseline that is
     already failing on the UNMUTATED tree, and a `-k` that selects no test at
     all (exit 5), are the same hole.  So every selection is run on the
     unmutated scratch copy first and must exit 0.
Ported from doctor/601-alt-359303ec and re-targeted at this branch's code.

That REFUSE fired for real (#755): commit b6590cb7 moved the local `_finite`
helper out of tools/bd_pitch_predeclaration.py into the one shared guard
(tools/finite_number.py, #718), so `bool-accepted-as-number` matched nothing
and NONE of the 12 ran for about four weeks -- because nothing invoked this
script.  The mutant now removes the bool rejection from the shared guard where
it actually lives, that file joins the restore set, and `make controls` runs
this script so the next such drift is red instead of unseen.  The fast half of
that cover is `test_mutant_targets_still_match_exactly_once` in
tools/test_bd_pitch_predeclaration_mutants.py, which `make verify` runs in
milliseconds; this script is the half that proves the controls go red.
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
FIN = "tools/finite_number.py"      # the one shared finite-number guard (#718)

# name: (file, old, new, pytest -k expression)
MUTANTS = {
    # --- the nine named in the doc (third review round) ---
    "bool-accepted-as-number": (
        FIN, "    if isinstance(x, (bool, np.bool_)):\n        return False\n",
        "    if False:\n        return False\n",
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

# Every file any mutant touches, DERIVED from MUTANTS rather than listed again:
# main() restores all of these before each mutant, so retargeting a mutant at a
# new file (#755 moved one to FIN) cannot leave that file mutated for the
# mutants that follow it.  A hand-maintained second list could.
FILES = tuple(sorted({rel for rel, *_ in MUTANTS.values()}))

# Each selection once, for the baseline gate: three mutants share
# "invalid_baseline_numbers", and re-running it per mutant would pay for the
# same evidence three times.
SELECTIONS = tuple(sorted({k for *_, k in MUTANTS.values()}))


def _focused(dst: pathlib.Path, k: str) -> subprocess.CompletedProcess:
    """One focused control run inside the scratch tree -- the SAME command for
    the baseline and for the mutant, so the baseline cannot pass by running
    something easier than the thing whose exit code becomes a verdict."""
    return subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
                           TEST, "-k", k], cwd=dst, capture_output=True, text=True)


def _tail(r: subprocess.CompletedProcess) -> str:
    """Last line of output, falling back to stderr: a missing pytest writes
    `No module named pytest` to stderr and NOTHING to stdout, which is how the
    original defect rendered as a bare `<no output>` beside every RED."""
    return ((r.stdout.strip() or r.stderr.strip()).splitlines() or ["<no output>"])[-1]


def main() -> int:
    bad = 0
    with tempfile.TemporaryDirectory(prefix="bpp-mutants-") as tmp:
        dst = pathlib.Path(tmp) / "tree"
        # the whole tree once: check() resolves probe paths anywhere in the repo
        shutil.copytree(ROOT, dst, ignore=shutil.ignore_patterns(
            ".git", ".loom", "build", "__pycache__", ".pytest_cache", "node_modules"))
        originals = {rel: (dst / rel).read_text() for rel in FILES}
        # Precondition 1 (free): every mutant still aims at code.
        for name, (rel, old, new, _) in MUTANTS.items():
            n = originals[rel].count(old)
            if n != 1:
                print(f"REFUSED: mutant {name}: target text found {n} times in {rel}")
                return 2
        # Precondition 2 (~4 s): every selection is GREEN on the UNMUTATED tree,
        # so a later exit 1 can only mean the removed guard.  Without this, a
        # host with no pytest reports all 12 RED having run nothing.
        for k in SELECTIONS:
            r = _focused(dst, k)
            if r.returncode != 0:
                print(f"REFUSED: baseline `pytest {TEST} -k {k}` exits {r.returncode} on the "
                      f"UNMUTATED tree -- no mutant's exit code can mean anything: {_tail(r)}")
                return 2
        for name, (rel, old, new, k) in MUTANTS.items():
            for r, src in originals.items():              # restore every file
                (dst / r).write_text(src)
            (dst / rel).write_text(originals[rel].replace(old, new))
            r = _focused(dst, k)
            tail = _tail(r)
            red = r.returncode == 1          # sound: the baseline gate proved 0
            bad += not red
            print(f"{name:36s} {'RED' if red else f'NOT RED (pytest exit {r.returncode})'}  {tail}")
    print(f"all {len(MUTANTS)} mutants red" if not bad else f"FAIL: {bad} mutant(s) not red")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
