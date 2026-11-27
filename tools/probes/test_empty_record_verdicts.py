#!/usr/bin/env python3
"""#1466's tier-1 census, as regression tests on each probe's verdict.

    python3 -m pytest tools/probes/test_empty_record_verdicts.py -q

Every tool here reads its verdict off a failure collection -- `if fails:` /
`if bad:` -- and before this file nobody had established, by running it,
whether an empty collection could read as a pass. #1458 found two that did
(`--estimator typo` printed `All checks passed.` and exited 0). This file is
the same question asked of the gated probes in `tools/`, each answered by
driving the probe's own entry point with the input that empties its record.

Two dispositions, and a test per disposition:

FIXED -- the empty record was reached and exited 0 on the pre-fix tree at
ce0713ee. Measured there, in a clean worktree:

  permitted_differences.py --trials 0                       exit 0, `FAIL 0  REFUSED 0  PASS 18`
  permitted_differences.py --trials 0 --false-alarm-rate 1  exit 0, `0.0 s`
  permitted_differences.py --trials 0 --inject BLANKET_INVARIANCE
                                                           exit 0, `observed red (none)`
  estimator_fixtures.py      FAMILIES = {}                  exit 0, `Axis coverage OK.`
  coupling_controls.py       MUTANTS = {}                   exit 0, `PASS: 0 defects, each caught by its declared property`
  holdout_injected_controls.py INJECTS = {}                 exit 0, `every control fired`

  `--trials 0` is a real command line. The other three declare what to run
  in a module-level dict rather than reading it from a flag, so their "empty
  directory" is that dict emptied; the tests say so by patching exactly that
  dict and nothing else.

UNREACHABLE -- every input tried was refused, and these rows pin each refusal
so "unreachable" stays a checked claim rather than a census entry:

  check_surge_waveform_comment.py  an empty sweep, a sweep with no matching
                                   line, a comment block with no rows, a
                                   missing file: all `REFUSED`, exit 2
  check_workflows.py               an empty workflow directory: exit 2; a
                                   directory holding one trivial workflow:
                                   exit 1 (the order gates have nothing to
                                   guard)

WHAT A COUNT CANNOT SEE is unchanged from #1458: a check recorded PASS by a
comparison that gates nothing satisfies every count here. The inputs that
would satisfy a row while violating its intent (docs/verification-rules.md
rule 8), each a row of its own:

  * a guard on `n_ok == 0` ("nothing passed") -- the FAIL-only and REFUSED-
    only runs must keep exit 1 and exit 2 respectively, not be re-labelled
    an empty run;
  * a `--trials 0` refusal that also catches a legitimate zero-check mode:
    `--policies` and `--list` print a table and exit 0 on purpose;
  * `--calibrate --trials 0` -- calibrate reads `--cal-trials`, so `--trials
    0` is unused there and the run is a real 96-draw calibration: exit 0;
  * one family that returns no records -- not an empty catalogue, and it
    must FAIL its coverage rows (exit 1), not be called an empty run.

Not run here, said rather than implied: `coupling_controls.py`'s clean
`run_props()` is 57 s on the census host and the fix refuses before calling
it, so no row in this file waits on it; the full probe is `make controls`'
question.
"""
from __future__ import annotations

import pathlib
import subprocess
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import permitted_differences as pd                                 # noqa: E402
import estimator_fixtures as ef                                    # noqa: E402
import coupling_controls as cc                                     # noqa: E402
import holdout_injected_controls as hic                            # noqa: E402
import check_surge_waveform_comment as ck                          # noqa: E402
import check_workflows as cw                                       # noqa: E402

PROBES = pathlib.Path(__file__).resolve().parent
PASS, FAIL, NO_VERDICT = 0, 1, 2
REAL_WF = cw.WF


def _cli(path: pathlib.Path, *args: str, timeout: int = 300) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(path), *args], capture_output=True,
                          text=True, timeout=timeout, cwd=str(PROBES.parent))


def _tail(out: str) -> str:
    lines = [ln for ln in out.splitlines() if ln.strip()]
    return lines[-1].strip() if lines else ""


def _shows(out: str, what: str) -> str:
    return f"{what}:\n{out[-1500:]}"


# ---------------------------------------------------------------------------
# tools/probes/permitted_differences.py -- FIXED, reached by a real command line
# ---------------------------------------------------------------------------
PD = PROBES / "permitted_differences.py"


def test_zero_trials_is_no_verdict_not_eighteen_passes():
    """`--trials 0`: no row runs a trial, so no row has a worst one. Pre-fix
    every row read PASS and the run exited 0 with `FAIL 0  REFUSED 0  PASS 18`."""
    p = _cli(PD, "--trials", "0")
    assert p.returncode == NO_VERDICT, _shows(p.stdout + p.stderr, f"exit {p.returncode}")
    assert "NO VERDICT" in p.stdout + p.stderr, _shows(p.stdout, "the refusal must be named")
    assert "PASS 18" not in p.stdout, _shows(p.stdout, "zero trials still rendered as passes")


def test_zero_trials_in_the_false_alarm_mode_is_no_verdict():
    """`--trials 0 --false-alarm-rate 1` printed `0/18 = 0.0000 %` and an
    upper bound over eighteen comparisons that never happened, and exited 0."""
    p = _cli(PD, "--trials", "0", "--false-alarm-rate", "1")
    assert p.returncode == NO_VERDICT, _shows(p.stdout + p.stderr, f"exit {p.returncode}")
    assert "NO VERDICT" in p.stdout + p.stderr, _shows(p.stdout, "the refusal must be named")
    assert "false alarms:" not in p.stdout, _shows(p.stdout, "a rate was printed over nothing")


def test_zero_trials_under_an_injection_is_no_verdict():
    """`--trials 0 --inject BLANKET_INVARIANCE` (no --expect-fail) exited 0
    because nothing went red -- the control "passed" by running nothing."""
    p = _cli(PD, "--trials", "0", "--inject", "BLANKET_INVARIANCE")
    assert p.returncode == NO_VERDICT, _shows(p.stdout + p.stderr, f"exit {p.returncode}")


def test_negative_trials_is_no_verdict():
    """`--trials -1` already exited 2 pre-fix, through `range(-1)` being empty
    and `0 > -0.25` refusing each row -- the right exit for a reason nobody
    chose. It is pinned so the refusal stays on the request, not on that
    comparison."""
    p = _cli(PD, "--trials", "-1")
    assert p.returncode == NO_VERDICT, _shows(p.stdout + p.stderr, f"exit {p.returncode}")


def test_zero_calibration_draws_is_no_verdict():
    """`--calibrate --cal-trials 0` printed `NO DATA` per row, called it
    `every calibration draw refused` and exited 1 -- a refusal rendered as a
    failure."""
    p = _cli(PD, "--calibrate", "--cal-trials", "0")
    assert p.returncode == NO_VERDICT, _shows(p.stdout + p.stderr, f"exit {p.returncode}")
    assert "NO VERDICT" in p.stdout + p.stderr, _shows(p.stdout, "the refusal must be named")


@pytest.mark.parametrize("flag,val", [
    ("--false-alarm-rate", "0"),
    ("--false-alarm-rate", "-1"),
    ("--safety-sweep", "0"),
    ("--safety-sweep", "-1"),
])
def test_non_positive_sweep_or_false_alarm_reps_is_no_verdict(flag, val):
    """`--false-alarm-rate -1` had `range(-1)` empty, printed a 100 % upper
    bound over -18 comparisons in 0.0 s, and exited 0; `0` was silently
    treated as flag omitted. Both must refuse with exit 2."""
    p = _cli(PD, flag, val)
    assert p.returncode == NO_VERDICT, _shows(p.stdout + p.stderr, f"{flag} {val}: exit {p.returncode}")
    assert "NO VERDICT" in p.stdout + p.stderr, _shows(p.stdout, "the refusal must be named")


def test_empty_cases_table_is_no_verdict(monkeypatch, capsys):
    """`CASES = []`: pre-fix printed `FAIL 0  REFUSED 0  PASS 0` and exited 0."""
    monkeypatch.setattr(pd, "CASES", [])
    code = pd.main(["--trials", "1"])
    out = capsys.readouterr().out
    assert code == NO_VERDICT, _shows(out, f"exit {code}")
    assert "NO VERDICT" in out, _shows(out, "the refusal must be named")


def test_the_default_run_still_passes_and_counts():
    """A clean nonempty run: exit 0, and the summary counts what ran."""
    p = _cli(PD)
    assert p.returncode == PASS, _shows(p.stdout + p.stderr, f"exit {p.returncode}")
    assert _tail(p.stdout) == "FAIL 0  REFUSED 0  PASS 18", _shows(p.stdout, "the summary")
    assert "NO VERDICT" not in p.stdout, _shows(p.stdout, "a real run reported no verdict")


def test_policies_and_calibrate_are_legitimate_zero_check_modes():
    """`--policies` prints a table and runs no comparison on purpose: exit 0.
    `--calibrate` reads `--cal-trials` (96), so `--trials 0` is unused there
    and the run is a real calibration: exit 0. Neither may be caught by the
    `--trials` refusal."""
    p = _cli(PD, "--policies", "--trials", "0")
    assert p.returncode == PASS, _shows(p.stdout + p.stderr, f"--policies exit {p.returncode}")
    p = _cli(PD, "--calibrate", "--trials", "0")
    assert p.returncode == PASS, _shows(p.stdout + p.stderr, f"--calibrate exit {p.returncode}")


def test_a_row_that_ran_no_trial_is_refused_not_passed():
    """The verdict itself, below every mode: `run_case` over zero trials
    returned PASS pre-fix (`exceed` was 0 and `0 > 0` is False). A row's
    verdict is the worst of its trials; with none there is no worst."""
    r = pd.run_case(pd.CASES[0], 0, pd.VALIDATE_BASE, None)
    assert r.verdict == pd.REFUSED, f"zero trials read {r.verdict}"
    assert r.trials == 0 and r.exceedances == 0 and r.residuals == []
    assert r.reasons and "nothing was measured" in r.reasons[0], r.reasons


def test_a_row_with_trials_keeps_its_verdict():
    """Rule 8 for the row guard: a measured row is not an empty one. The
    control must not re-label a run that did execute."""
    r = pd.run_case(pd.CASES[0], 2, pd.VALIDATE_BASE, None)
    assert r.trials == 2 and len(r.residuals) + r.refusals == 2
    assert r.verdict in (pd.PASS, pd.FAIL, pd.REFUSED)
    assert not (r.verdict == pd.REFUSED and r.refusals == 0), "refused with every draw answered"


# ---------------------------------------------------------------------------
# tools/probes/estimator_fixtures.py -- FIXED, the catalogue emptied
# ---------------------------------------------------------------------------
def test_an_empty_family_table_is_no_verdict():
    """`FAMILIES = {}`: no fixture, so no (family, axis) row, so nothing to
    cover. Pre-fix the catalogue printed `TOTAL 0 records` and the run ended
    `Axis coverage OK.` with exit 0."""
    saved = ef.FAMILIES
    try:
        ef.FAMILIES = {}
        code = ef.main()
    finally:
        ef.FAMILIES = saved
    assert code == NO_VERDICT, f"an empty catalogue exited {code}"


def test_the_default_catalogue_still_passes_and_counts(capsys):
    code = ef.main()
    out = capsys.readouterr().out
    assert code == PASS, _shows(out, f"exit {code}")
    assert "Axis coverage OK" in out, _shows(out, "the clean run's verdict line")
    assert "NO VERDICT" not in out, _shows(out, "a real catalogue reported no verdict")


def test_a_family_with_no_records_fails_coverage_it_is_not_an_empty_run(capsys):
    """Rule 8: one family returning [] is a catalogue with a hole, not an
    empty one. Its axes must FAIL (exit 1); the empty-catalogue refusal must
    not absorb it."""
    saved = ef.FAMILIES
    try:
        ef.FAMILIES = {"damped_sine": lambda sr: []}
        code = ef.main()
    finally:
        ef.FAMILIES = saved
    out = capsys.readouterr().out
    assert code == FAIL, _shows(out, f"exit {code}")
    assert "FAIL" in out, _shows(out, "the family's uncovered axes must be reported")


# ---------------------------------------------------------------------------
# tools/probes/coupling_controls.py -- FIXED, the mutant table emptied
# ---------------------------------------------------------------------------
def test_no_declared_mutant_is_no_verdict(monkeypatch, capsys):
    """`MUTANTS = {}`: pre-fix the clean run went green, the matrix printed
    every property BLIND-TO-ALL, and the run ended `PASS: 0 defects, each
    caught by its declared property` with exit 0. The refusal must come
    before the clean run: `run_props` is stood in to raise, so reaching it
    fails this row."""
    monkeypatch.setattr(cc, "MUTANTS", {})
    monkeypatch.setattr(cc, "run_props", lambda: (_ for _ in ()).throw(
        AssertionError("the clean run was started with no control declared")))
    code = cc.main([])
    out = capsys.readouterr().out
    assert code == NO_VERDICT, _shows(out, f"exit {code}")
    assert "NO VERDICT" in out, _shows(out, "the refusal must be named")
    assert "PASS" not in out, _shows(out, "zero mutants still rendered as a pass")
    assert "BLIND-TO-ALL" not in out, _shows(out, "a matrix was printed over no defect")


def test_a_declared_mutant_whose_property_is_absent_still_refuses(monkeypatch, capsys):
    """The existing guard, pinned: a property named by MUTANTS that the suite
    does not carry is NO VERDICT -- and is not the empty-run refusal."""
    monkeypatch.setattr(cc, "PROPS", [])
    code = cc.main([])
    out = capsys.readouterr().out
    assert code == NO_VERDICT, _shows(out, f"exit {code}")
    assert "absent from the suite" in out, _shows(out, "which refusal fired")


def test_list_is_a_legitimate_zero_check_mode(capsys):
    code = cc.main(["--list"])
    out = capsys.readouterr().out
    assert code == PASS and len(out.strip().splitlines()) == len(cc.MUTANTS), out


# ---------------------------------------------------------------------------
# tools/probes/holdout_injected_controls.py -- FIXED, the injection table emptied
# ---------------------------------------------------------------------------
def test_no_declared_injection_is_no_verdict(monkeypatch, capsys):
    """`INJECTS = {}`: pre-fix the real baseline went green and the run ended
    `every control fired` with exit 0. `baseline` is stood in to raise: with
    no control to precondition, the tree copy must not even start."""
    monkeypatch.setattr(hic, "INJECTS", {})
    monkeypatch.setattr(hic, "baseline", lambda: (_ for _ in ()).throw(
        AssertionError("the baseline was started with no control declared")))
    code = hic.main()
    out = capsys.readouterr().out
    assert code == NO_VERDICT, _shows(out, f"exit {code}")
    assert "NO VERDICT" in out, _shows(out, "the refusal must be named")
    assert "every control fired" not in out, _shows(out, "zero controls reported as fired")


def test_the_fired_verdict_counts_its_controls(monkeypatch, capsys):
    """The verdict line names how many controls fired, so a reader can tell
    three from zero without counting rows above it. One FIRED control, the
    baseline stood in as green: exit 0 and `every control fired (1)`."""
    green = hic.SuiteReport(failures={}, outcomes={"test_a": "passed"}, executed=1, completed=1)
    monkeypatch.setattr(hic, "baseline", lambda: hic.Baseline(green, "stood in: green"))
    monkeypatch.setattr(hic, "run", lambda name, base: hic.Result(
        hic.FIRED, ["test_a"], "stood in: fired"))
    monkeypatch.setattr(hic, "INJECTS", {"ONLY": hic.INJECTS["GATE_OFF"]})
    code = hic.main()
    out = capsys.readouterr().out
    assert code == PASS, _shows(out, f"exit {code}")
    assert "every control fired (1)" in out, _shows(out, "the verdict line must count")


def test_a_control_that_did_not_fire_keeps_exit_1(monkeypatch, capsys):
    """Rule 8 for the count: a measured NO_FIRE is a result, exit 1, and must
    not be re-labelled an empty run."""
    green = hic.SuiteReport(failures={}, outcomes={"test_a": "passed"}, executed=1, completed=1)
    monkeypatch.setattr(hic, "baseline", lambda: hic.Baseline(green, "stood in: green"))
    monkeypatch.setattr(hic, "run", lambda name, base: hic.Result(
        hic.NO_FIRE, [], "stood in: stayed green"))
    monkeypatch.setattr(hic, "INJECTS", {"ONLY": hic.INJECTS["GATE_OFF"]})
    code = hic.main()
    out = capsys.readouterr().out
    assert code == FAIL, _shows(out, f"exit {code}")
    assert "every control fired" not in out, _shows(out, "a control that stayed green was counted")


# ---------------------------------------------------------------------------
# tools/check_surge_waveform_comment.py -- UNREACHABLE, refusals pinned
# ---------------------------------------------------------------------------
SURGE = PROBES.parent / "check_surge_waveform_comment.py"


@pytest.mark.parametrize("which", ["empty_sweep", "no_matching_line", "no_comment_rows",
                                   "missing_sweep", "empty_rigs"])
def test_an_empty_input_to_the_surge_comment_check_is_refused(tmp_path, which):
    empty = tmp_path / "empty.txt"
    empty.write_text("")
    nomatch = tmp_path / "nomatch.txt"
    nomatch.write_text("nothing here is a `shape ... width ...:` line\n")
    rigs = tmp_path / "rigs.py"
    rigs.write_text("# ---- oscillator 1, for the waveform study\n# the table is gone\n"
                    "V_CLASSIC, V_SINE = 1, 2\n")
    arg = {"empty_sweep": ["--sweep", str(empty)],
           "no_matching_line": ["--sweep", str(nomatch)],
           "no_comment_rows": ["--rigs", str(rigs)],
           "missing_sweep": ["--sweep", str(tmp_path / "absent.txt")],
           "empty_rigs": ["--rigs", str(empty)]}[which]
    p = _cli(SURGE, *arg)
    out = p.stdout + p.stderr
    assert p.returncode == NO_VERDICT, _shows(out, f"{which}: exit {p.returncode}")
    assert out.lstrip().startswith("REFUSED"), _shows(out, f"{which}: must say REFUSED")
    assert not out.lstrip().startswith(("OK", "STALE")), _shows(
        out, f"{which}: reported a verdict on an empty input")


def test_the_sweep_comment_check_clean_run_counts_its_cells():
    p = _cli(SURGE)
    assert p.returncode == PASS, _shows(p.stdout + p.stderr, f"exit {p.returncode}")
    assert " cells in reference_rigs.py all reproduce" in p.stdout, _shows(p.stdout, "the count")


# ---------------------------------------------------------------------------
# tools/check_workflows.py -- UNREACHABLE, refusals pinned
# ---------------------------------------------------------------------------
def test_an_empty_workflow_directory_is_refused(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cw, "WF", tmp_path / "none")
    code = cw.main()
    out = capsys.readouterr()
    assert code == NO_VERDICT, f"exit {code}: {out.err}"
    assert "no workflow files found" in out.err, out.err


def test_one_trivial_workflow_fails_because_the_order_gates_have_nothing_to_guard(
        tmp_path, monkeypatch, capsys):
    """Not empty, but empty of what the gates select: the #940 order gate
    must FIND the roadmap step somewhere in the tree, and reports rather
    than rewarding its absence with a green run."""
    d = tmp_path / "wf"
    d.mkdir()
    (d / "trivial.yml").write_text(
        "on: push\njobs:\n  a:\n    runs-on: ubuntu-latest\n    steps:\n"
        "      - uses: actions/checkout@v4\n")
    monkeypatch.setattr(cw, "WF", d)
    code = cw.main()
    out = capsys.readouterr()
    assert code == FAIL, f"exit {code}: {out.err}"
    assert "has nothing to guard" in out.err, out.err


def test_the_real_tree_still_passes_the_workflow_check(monkeypatch, capsys):
    monkeypatch.setattr(cw, "WF", REAL_WF)
    code = cw.main()
    out = capsys.readouterr()
    assert code == PASS, f"exit {code}: {out.err}"
    assert "workflow(s) parse and declare a usable trigger" in out.out, out.out
