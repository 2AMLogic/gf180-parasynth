#!/usr/bin/env python3
"""Mutation controls for the #557 freeze's guards (PR #601 reviews).

    python3 tools/bd_pitch_predeclaration_mutants.py      # needs pytest + numpy

Each mutant removes ONE guard from a scratch copy of the tree (never this
checkout) and runs the focused controls named for it.  Every mutant must turn
them RED at a RECORDED assertion; the exit status is 0 only when all 12 do.
That decision is not made here: each focused run goes through
`tools/probes/holdout_injected_controls.py::verdict` (#1463), as the sound
and FPGA-record probes' runs do, and `EXPECTED` below records the test and
the assertion that caught each mutant on the real suite.

Until #1463 the verdict was `red = r.returncode == 1`, sound only because the
baseline gate had proved the same selection GREEN.  A focused run that exits 1
for any OTHER reason -- an unrelated test in the same `-k` selection, another
assertion in the right test, a setup error, a run that left no report -- was
counted RED, and the failing test's name and assertion were never compared
with what the mutant is supposed to break (docs/verification-rules.md 5,
third condition).  A fixture suite that only compared the sources with their
pristine copies, and never imported the validator, was credited `all 12
mutants red`, exit 0.

Exit status, the shared probes' aggregate convention: 1 when any mutant
SURVIVED (the suite stayed green with the guard gone -- a hole in the
controls, a mix included), else 2 when any REFUSED (nothing was measured for
it: the reason is on its line), else 0.

Three inputs that defeat it (rule 8), REFUSED (exit 2) before any mutant
verdict line is printed:

  1. A mutant whose `old` text no longer appears exactly once in the source
     would silently mutate nothing and the suite would stay green -- reported
     as SURVIVED, which is right, but for the wrong reason.  So a target found
     0 or 2+ times REFUSES.
  2. `pytest` exiting non-zero for a reason that is not the removed guard.
     `python -m pytest` exits 1 when the module is not installed, so on a host
     without it every mutant was counted RED with ZERO tests executed (PR #757
     review, reproduced on a worker whose system python3 has no pytest;
     Makefile's PY falls back to python3 when .venv is absent, so `make
     controls` reached it).  A baseline that is already failing on the
     UNMUTATED tree, and a `-k` that selects no test at all (exit 5), are the
     same hole.  So every selection is run on the unmutated scratch copy first
     and must exit 0.
  3. A red that is not the recorded assertion.  `verdict` credits a mutant
     only when an EXPECTED test that PASSED on the clean baseline failed at
     its recorded statement under the mutant; anything else is REFUSED with
     both the expected and the observed names printed.

Each `-k` selection is first resolved into the explicit node IDs it collects
on the unmutated tree, and those -- not `-k` -- are the collection targets for
the baseline and the mutant.  A `-k` run deselects the rest of the file, and
the shared clean-run check refuses any deselection; explicit node IDs collect
exactly the focused set (parameter IDs included) and deselect nothing, while
TEST stays the identity the recorded names are labelled against.
Ported from doctor/601-alt-359303ec and re-targeted at this branch's code.

Refusal 1 fired for real (#755): commit b6590cb7 moved the local `_finite`
helper out of tools/bd_pitch_predeclaration.py into the one shared guard
(tools/finite_number.py, #718), so `bool-accepted-as-number` matched nothing
and NONE of the 12 ran for about four weeks -- because nothing invoked this
script.  The mutant now removes the bool rejection from the shared guard where
it actually lives, that file joins the restore set, and `make controls` runs
this script so the next such drift is red instead of unseen.  The fast half of
that cover, and of refusal 3's records, is in
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

sys.path.insert(0, str(ROOT / "tools" / "probes"))
import holdout_injected_controls as hic  # noqa: E402

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

# The test and the assertion that caught each mutant on the REAL suite
# (2026-11-27, ce0713ee, Python 3.12.3, pytest 9.2.0): the suite-relative
# pytest ID with its parameter ID, the failed statement as written in the
# test, and the exception where it is not AssertionError (a `pytest.raises`
# that did not raise reports `Failed`).  Overlap is enough: a superset still
# fires and its extras are printed as information, so a new test does not make
# this noisy, while a renamed-away or altered record REFUSES rather than
# passing silently.  Derived, never written from intention: with the guard
# `non-dict-entry-unguarded` removes, the validator raised AttributeError from
# inside itself and the recorded statement was empty, so the test now asserts
# the refusal itself and the mutant fails there.
_CLI_REFUSES = hic.Site('assert code == 2 and "REFUSED" in out, out')
_CAUGHT_NOT_DERIVED = hic.Site('assert _caught(bp.check(bad), "is not its derivation")')
_CAUGHT_NOT_UNQUALIFIED = hic.Site(
    'assert _caught(bp.check(bad), "exactly the untouched Fischer conditions at unqualified knobs")')
ENTRY = "test_non_dict_reading_entry_refuses"
EXPECTED = {
    "bool-accepted-as-number": {
        "test_cli_refuses_invalid_baseline_numbers[True-100]": _CLI_REFUSES,
        "test_cli_refuses_invalid_baseline_numbers[10-True]": _CLI_REFUSES,
    },
    "negative-spread": {
        "test_cli_refuses_invalid_baseline_numbers[-5-100]": _CLI_REFUSES,
    },
    "negative-threshold": {
        "test_negative_evaluated_threshold_is_refused":
            hic.Site('with pytest.raises(bp.Refused, match="non-negative"):', "Failed"),
    },
    "non-finite-deficit": {
        "test_cli_refuses_invalid_baseline_numbers[10-inf]": _CLI_REFUSES,
        "test_cli_refuses_invalid_baseline_numbers[10-100_0]": _CLI_REFUSES,
        "test_cli_refuses_invalid_baseline_numbers[10-True]": _CLI_REFUSES,
    },
    "strict-at-equality": {
        "test_satisfiable_at_equality":
            hic.Site('assert at["satisfiable"] is True, "pass is improvement >= minimum: equality '
                     'satisfies it"'),
    },
    "development-take-decides": {
        "test_cli_with_readings_answers_on_the_untouched_median":
            hic.Site("assert code == 0 and '\"satisfiable\": true' in out, out"),
        "test_satisfiable_follows_the_untouched_median_not_the_development_take":
            hic.Site('assert ok["satisfiable"] is True and ok["min_improvement_cents"] == '
                     'pytest.approx(37.8)'),
    },
    "min-count-derivation-unchecked": {
        "test_control_min_measured_lowered_below_its_derivation_is_caught": _CAUGHT_NOT_DERIVED,
    },
    "tone-holdout-removed": {
        "test_lost_tone_holdout_refuses":
            hic.Site('with pytest.raises(bp.Refused, match="holds out no tone"):', "Failed"),
    },
    "coarse-5hz-pitch-grid": {
        "test_sweep_grid_is_not_coarse":
            hic.Site("assert f[0] <= 40.0 and f[-1] >= 65.0 and max(b - a for a, b in zip(f, "
                     "f[1:])) <= 0.5"),
    },
    "unqualified-knob-admitted": {
        "test_control_answering_knob0_reading_is_excluded":
            hic.Site('assert sorted(got["measured"]) == sorted(set(rd) - set(D00)), got["measured"]'),
    },
    "predicted-not-tied-to-unqualified": {
        f"test_control_predicted_refusals_not_the_unqualified_set_is_caught[pred{i}-uq{i}]":
            _CAUGHT_NOT_UNQUALIFIED for i in range(4)
    },
    "non-dict-entry-unguarded": {
        test: hic.Site("assert not isinstance(e.value, AttributeError)")
        for test in (f"{ENTRY}[entry0]", f"{ENTRY}[100]", f"{ENTRY}[None]", f"{ENTRY}[100.0]")
    },
}


def _targets(dst: pathlib.Path, k: str) -> tuple[int, list[str]]:
    """The explicit node IDs one `-k` selection collects, and pytest's status."""
    r = subprocess.run([sys.executable, "-m", "pytest", "--collect-only", "-q",
                        "-p", "no:cacheprovider", TEST, "-k", k],
                       cwd=dst, capture_output=True, text=True)
    return r.returncode, [ln.strip() for ln in r.stdout.splitlines()
                          if ln.strip().startswith(TEST + "::")]


def report(results: dict[str, hic.Result]) -> int:
    """One line per mutant with the evidence beside it, then the summary; the
    return value is the script's exit status (see the module docstring)."""
    survivors = refused = 0
    for name, res in results.items():
        if res.outcome == hic.REFUSED:
            refused += 1
            print(f"{name:36s} REFUSED -- {res.reason}")
        elif res.outcome == hic.NO_FIRE:
            survivors += 1
            print(f"{name:36s} SURVIVED -- {res.reason}")
        else:
            print(f"{name:36s} RED -- {len(res.failed)} test(s) caught it at the recorded assertion")
            for test in res.failed:
                print(f"{'':36s}   {test}")
                print(f"{'':36s}     {EXPECTED[name][test].statement}")
            if res.other:
                print(f"{'':36s}   (other failures, information only: {', '.join(res.other)})")
    # A survivor -- the controls do not cover the guard -- outranks a refusal:
    # 1 when any mutant survived (a mix included), else 2 when any refused,
    # else 0.  Exit 2 is the runner's NO-VERDICT channel (tools/exit_contract.py).
    if survivors:
        print(f"FAIL: {survivors} mutant(s) SURVIVED their controls"
              + (f", {refused} refused" if refused else ""))
        return 1
    if refused:
        print(f"REFUSED: {refused} mutant(s) -- the reasons are above; nothing was "
              f"measured for them, and no red is claimed.")
        return 2
    print(f"all {len(results)} mutants red")
    return 0


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="bpp-mutants-") as tmp:
        dst = pathlib.Path(tmp) / "tree"
        # the whole tree once: check() resolves probe paths anywhere in the repo
        shutil.copytree(ROOT, dst, ignore=shutil.ignore_patterns(
            ".git", ".loom", "build", ".venv", "__pycache__", ".pytest_cache", "node_modules"))
        originals = {rel: (dst / rel).read_text() for rel in FILES}
        # Precondition 1 (free): every mutant still aims at code.
        for name, (rel, old, new, _) in MUTANTS.items():
            n = originals[rel].count(old)
            if n != 1:
                print(f"REFUSED: mutant {name}: target text found {n} times in {rel}")
                return 2
        targets = {}
        for k in SELECTIONS:
            rc, ids = _targets(dst, k)
            if rc != 0 or not ids:
                print(f"REFUSED: `pytest {TEST} -k {k}` collects no test on the UNMUTATED tree "
                      f"(pytest exit {rc}) -- no mutant's verdict can mean anything")
                return 2
            targets[k] = ids
        # Precondition 2: every selection is GREEN on the UNMUTATED tree, by
        # the SAME explicit targets its mutants are then run with.
        baselines = {}
        for k in SELECTIONS:
            base = hic.run_baseline(dst, TEST, targets[k])
            if base.report is None:
                print(f"REFUSED: baseline `pytest {TEST} -k {k}` -- {base.reason}")
                print(base.tail.rstrip())
                return 2
            recorded = {TEST + "::" + test for test in base.report.outcomes}
            if recorded != set(targets[k]):
                print(f"REFUSED: baseline `-k {k}` recorded {len(recorded)} test(s) for "
                      f"{len(targets[k])} target(s) -- the report is not of the focused run")
                return 2
            baselines[k] = base.report
            print(f"baseline -k {k:38s} {len(recorded)} test(s) green on the UNMUTATED tree")
        results = {}
        for name, (rel, old, new, k) in MUTANTS.items():
            for r, src in originals.items():              # restore every file
                (dst / r).write_text(src)
            (dst / rel).write_text(originals[rel].replace(old, new))
            rc, run, _stdout = hic.run_suite(dst, TEST, targets[k])
            results[name] = hic.verdict(rc, run, EXPECTED.get(name, {}),
                                         suite=TEST, baseline=baselines[k])
        return report(results)


if __name__ == "__main__":
    sys.exit(main())
