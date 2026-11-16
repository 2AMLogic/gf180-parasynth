#!/usr/bin/env python3
"""Controls for `tools/probes/estimator_defects.py`'s own guards (#805).

    python3 -m pytest tools/probes/test_estimator_defects.py -q

Three things are pinned, each per `docs/verification-rules.md` (a control
that has never seen the defect it exists to catch is not a control):

1. the invariance BASELINE in defect 5's trailing-silence check is asserted
   at the point of use. Both refusal encodings are exercised, because they
   fail the site in opposite directions: `Estimate(None, False, ...)` used
   to raise TypeError at the first `base.value` read (loud, NO VERDICT),
   a nan-carrying refusal made `abs(nan - base) < 1e-12` silently False --
   a wrong FAILED verdict with a plausible-looking cause. A third control
   pins the boundary: only the perturbed side refusing must still produce
   FAILED rows, exactly as before, so the guard cannot quietly widen into
   refusing everything.
2. REFUSED is a distinct outcome of the check machinery -- a distinct exit
   code (2, the file's existing refused-precondition code), never 0 and
   never 1, and `fails` cannot absorb it.
3. the shipped refusal ENCODING in `model/` is pinned behaviourally:
   `audio_measure._fail` returns `value is None and not ok`, real shipped
   refusal paths return None-encoded Estimates, and an AST scan asserts no
   `Estimate(...)` construction anywhere in `model/` carries a nan literal.
   The scan is over Estimate-returning refusal paths ONLY -- never a bare
   `nan` grep over `model/`, which the ~40 bare-float nan returns there
   (none of them refusals) and `estimator_defects.py`'s own probe-local
   nan convention would fail on false premises.
4. defect 1's prepend-silence invariance BASELINE is asserted before the
   FIRST `.value` read, table print included. Its guard originally sat
   below a print loop that formatted the same readings it protects, so a
   None-encoded refusal -- the shipped encoding, pinned by (3) -- raised
   TypeError at the print and the guard was unreachable (`_prepend_
   silence_invariance` exposes `shipped=`/`repaired=` seams so these
   controls can rig either estimator; the None-encoded controls are
   red-first against that defeat, the nan-encoded one is a regression
   pin, and the real-estimator run is the boundary that stops the guard
   widening into refusing everything).
"""
from __future__ import annotations

import ast
import math
import pathlib
import sys

import numpy as np
import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "model"))
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "tools" / "probes"))

import audio_measure as am                                          # noqa: E402
import estimator_defects as ed                                      # noqa: E402
import run_case as rc                                               # noqa: E402

SR = 48000


@pytest.fixture()
def _reset_verdict_lists():
    """`ed.fails` / `ed.refusals` are module state the probe accumulates
    into; each control starts from empty and restores whatever it found."""
    saved = (list(ed.fails), list(ed.refusals))
    ed.fails.clear()
    ed.refusals.clear()
    yield
    ed.fails[:] = saved[0]
    ed.refusals[:] = saved[1]


# ---------------------------------------------------------------------------
# 1. the trailing-silence invariance BASELINE refuses (#805 AC2 / AC5)
# ---------------------------------------------------------------------------
def test_baseline_refusal_none_encoded_is_refused_not_failed(
        _reset_verdict_lists, capsys):
    """INJECTED DEFECT: the BASELINE refuses -- not the perturbed reading --
    in the shipped `Estimate(None, False, ...)` encoding. Before the repair
    the unguarded `g.value - base.value` raised TypeError here (NO VERDICT
    at all); the repaired guard must record REFUSED, add nothing to
    `fails`, and run no comparison against a baseline that does not exist."""
    def refusing_baseline(x, sr):
        return am._fail("injected: the baseline refuses", n=len(x))
    ed._trailing_silence_invariance(measure=refusing_baseline)
    out = capsys.readouterr().out
    assert len(ed.refusals) == 1
    assert ed.fails == []
    assert "REFUSED" in out
    assert "injected: the baseline refuses" in out


def test_baseline_refusal_nan_encoded_is_refused_not_failed(
        _reset_verdict_lists, capsys):
    """INJECTED DEFECT, the OPPOSITE encoding: a nan-carrying refusal.
    Before the repair `abs(nan - base.value) < 1e-12` was silently False,
    so every pad row reported a FAILED invariance -- a wrong verdict with a
    plausible-looking cause -- instead of one REFUSED precondition. The
    guard must refuse on this encoding too."""
    def nan_baseline(x, sr):
        return am.Estimate(float("nan"), False, "injected: nan refusal", {}, None)
    ed._trailing_silence_invariance(measure=nan_baseline)
    out = capsys.readouterr().out
    assert len(ed.refusals) == 1
    assert ed.fails == []
    assert "REFUSED" in out


def test_perturbed_side_refusal_still_yields_failed_rows(_reset_verdict_lists):
    """INJECTED DEFECT on the OTHER side: the baseline is fine, the
    perturbed reading refuses. This must stay a FAILED invariance row per
    pad, exactly as before the repair -- a guard that refused the whole
    block here would have no power and no precedent in the file."""
    calls = {"n": 0}

    def baseline_ok_then_perturbed_refuses(x, sr):
        calls["n"] += 1
        if calls["n"] == 1:
            return am.Estimate(0.0921, True, "", {}, None)
        return am._fail("injected: the perturbed side refuses")
    ed._trailing_silence_invariance(measure=baseline_ok_then_perturbed_refuses)
    assert ed.refusals == []
    assert len(ed.fails) == 3, "one FAILED invariance row per pad"


def test_trailing_silence_invariance_holds_on_a_clean_decay(
        _reset_verdict_lists):
    """The repaired path on its real inputs: a clean 0.5 s, 40 ms decay
    under 0.2 / 1.7 / 5.0 s of trailing silence is EXACTLY invariant, so
    nothing is refused and nothing fails."""
    ed._trailing_silence_invariance()
    assert ed.refusals == []
    assert ed.fails == []


# ---------------------------------------------------------------------------
# 1b. defect 1's prepend-silence invariance BASELINE refuses (#805 AC4/AC8)
# ---------------------------------------------------------------------------
def test_prepend_silence_baseline_refusal_none_encoded_is_refused_not_typeerror(
        _reset_verdict_lists, capsys):
    """INJECTED DEFECT: the SHIPPED-side estimator refuses, None-encoded --
    the encoding defect 1's site actually meets on the shipped path. The
    guard used to sit BELOW the table print, which formatted the same
    readings it protects, so this input died at the print with TypeError
    (NO VERDICT) and the refusal branch was unreachable. The repaired
    helper guards before the first `.value` read: REFUSED recorded,
    nothing in `fails`, no TypeError."""
    def refusing_shipped(p, sr):
        return am._fail("injected: the shipped side refuses", n=len(p))
    ed._prepend_silence_invariance(shipped=refusing_shipped)
    out = capsys.readouterr().out
    assert len(ed.refusals) == 1
    assert ed.fails == []
    assert "REFUSED" in out
    assert "injected: the shipped side refuses" in out


def test_prepend_silence_repaired_side_refusal_none_encoded_is_refused(
        _reset_verdict_lists, capsys):
    """The same defeat on the OTHER column: the print loop formatted
    `n.value` on the same line, so a None-encoded refusal from the
    repaired estimator raised the identical TypeError with the guard
    still unreached. Same precondition, same verdict required."""
    def refusing_repaired(p, sr):
        return am._fail("injected: the repaired side refuses", n=len(p))
    ed._prepend_silence_invariance(repaired=refusing_repaired)
    out = capsys.readouterr().out
    assert len(ed.refusals) == 1
    assert ed.fails == []
    assert "REFUSED" in out
    assert "injected: the repaired side refuses" in out


def test_prepend_silence_refusal_nan_encoded_is_refused_not_failed(
        _reset_verdict_lists, capsys):
    """Regression pin (this encoding already refused on the pre-repair
    tree, once the guard was reached): a nan-carrying refusal must print
    no plausible `nan` table row and record REFUSED, never a FAILED
    swing measured against a reading that does not exist."""
    def nan_repaired(p, sr):
        return am.Estimate(float("nan"), False, "injected: nan refusal", {}, None)
    ed._prepend_silence_invariance(repaired=nan_repaired)
    out = capsys.readouterr().out
    assert len(ed.refusals) == 1
    assert ed.fails == []
    assert "REFUSED" in out
    assert "shipped swings" not in out, "no swing was measured, none may print"


def test_prepend_silence_invariance_holds_on_a_clean_synthetic_strike(
        _reset_verdict_lists, capsys):
    """No injection, the real estimators: every reading on the synthetic
    strike is fine, the table prints, and the two invariance checks run
    and hold -- the boundary control that stops the guard quietly
    widening into refusing everything (the AC5 shape, on defect 1's
    site). Needs no corpus: the strike is synthetic."""
    ed._prepend_silence_invariance()
    out = capsys.readouterr().out
    assert ed.refusals == []
    assert ed.fails == []
    assert "shipped swings" in out
    assert "the repaired path is invariant to prepended silence" in out


# ---------------------------------------------------------------------------
# 2. REFUSED is a distinct exit outcome (#805 AC3)
# ---------------------------------------------------------------------------
def test_exit_codes_distinct_refused_from_failed_from_clear(_reset_verdict_lists):
    assert ed.verdict_exit_code() == 0
    ed.fails.append("a failed invariant")
    assert ed.verdict_exit_code() == 1
    ed.refusals.append("a refused precondition")
    assert ed.verdict_exit_code() == 2, "REFUSED outranks FAILED -- never 1"
    ed.fails.clear()
    assert ed.verdict_exit_code() == 2, "a refusal alone -- never 0"


def test_check_refused_is_recorded_neither_as_ok_nor_as_fail(
        _reset_verdict_lists, capsys):
    ed.check_refused("precondition unmet: nothing was compared")
    out = capsys.readouterr().out
    assert ed.refusals == ["precondition unmet: nothing was compared"]
    assert ed.fails == [], "`fails` must not absorb a refusal"
    assert "REFUSED" in out


# ---------------------------------------------------------------------------
# 3. the shipped refusal ENCODING is pinned (#805 AC7)
# ---------------------------------------------------------------------------
def test_fail_the_shared_refusal_constructor_is_none_encoded():
    f = am._fail("probe refusal")
    assert f.value is None
    assert f.ok is False


@pytest.mark.parametrize("label,signal", [
    ("silent", np.zeros(int(0.5 * SR))),
    ("truncated-50ms", None),        # built below: a decay cut short of its T20
])
def test_shipped_refusal_paths_return_none_encoded_estimates(label, signal):
    """Real refusal paths of a shipped estimator, asserted behaviourally:
    `not ok`, `value is None`, and -- the whole point of the pin -- value
    is not a nan that would slip through a `< tol` comparison."""
    if signal is None:
        t = np.arange(int(2.0 * SR)) / SR
        full = np.exp(-t / 0.200) * np.sin(2 * math.pi * 220 * t)
        signal = full[:int(0.05 * SR)]
    r = am.schroeder_t20(signal, SR)
    assert not r.ok, f"{label}: expected a refusal"
    assert r.value is None, f"{label}: refusals are None-encoded, got {r.value!r}"


def _expr_is_nan(node) -> bool:
    """True when an AST expression is a nan literal in any spelling this
    repository uses: `float("nan")`, `math.isnan`-constant floats, `nan`
    imported or module-attributed, and the negated forms."""
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
        return _expr_is_nan(node.operand)
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) \
            and node.func.id == "float" and len(node.args) == 1 \
            and isinstance(node.args[0], ast.Constant) \
            and node.args[0].value == "nan":
        return True
    if isinstance(node, ast.Constant) and isinstance(node.value, float) \
            and math.isnan(node.value):
        return True
    if isinstance(node, ast.Name) and node.id == "nan":
        return True
    if isinstance(node, ast.Attribute) and node.attr == "nan":
        return True
    return False


def _is_estimate_call(func) -> bool:
    return (isinstance(func, ast.Name) and func.id == "Estimate") or \
        (isinstance(func, ast.Attribute) and func.attr == "Estimate")


def test_no_estimate_construction_in_model_carries_a_nan():
    """#805 AC7's second half, asserted over Estimate-returning refusal
    paths only: an `Estimate(float("nan"), ...)` construction anywhere in
    `model/` would be a refusal encoding this issue's None-guards cannot
    see -- the silent-FAILED-instead-of-REFUSED shape. Deliberately NOT a
    bare `nan` grep over `model/`: that directory holds bare-float nan
    returns that are not refusals, and a grep would also answer a question
    nobody asked."""
    offenders = []
    for path in sorted((ROOT / "model").glob("*.py")):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and _is_estimate_call(node.func):
                args = list(node.args) + [kw.value for kw in node.keywords]
                if any(_expr_is_nan(a) for a in args):
                    offenders.append(f"{path.name}:L{node.lineno}")
    assert offenders == [], \
        f"shipped refusals must be None-encoded; nan-carrying Estimate(s): {offenders}"


# ---------------------------------------------------------------------------
# 4. the five remaining unguarded `.value` sites, ruled per site (#843)
# ---------------------------------------------------------------------------
def test_defect2_row_refusal_none_encoded_is_refused_not_typeerror(
        _reset_verdict_lists, capsys):
    """SITE a99a408e :183, defect 2's table row (`fb.value`, `ab.value`,
    `ab.value-ah`). INJECTED DEFECT: the row's estimator refuses,
    None-encoded. Before the repair the print raised TypeError on the
    refusal's None value -- and on origin/main @ ade2e9ff this was not
    latent: the validated-domain headroom gate makes the alias-free `fb`
    refuse by design, so the probe died at its first row with NO VERDICT
    for every section after it. One refusal per f0 row, no row printed,
    the floor-vs-answer verdict not run against a reading that does not
    exist."""
    def refusing(x, f0, sr, **kw):
        return am._fail("injected: the floor row refuses", f0=f0)
    ed.defect_2(inharmonic=refusing)
    out = capsys.readouterr().out
    assert len(ed.refusals) == 7, "one refusal per f0 row"
    assert ed.fails == []
    assert out.count("REFUSED") == 7
    assert "answer moved" in out, "the header printed, no row under it did"
    assert "nan" not in out, "no plausible nan row may print"
    assert "the measured floor equals" not in out, "that verdict did not run"


def test_defect2_row_refusal_nan_carrying_is_refused_not_a_plausible_row(
        _reset_verdict_lists, capsys):
    """The opposite encoding at the same site: an ok-carrying nan sails past
    an `.ok`-only guard and used to print a plausible `nan` in the table and
    make `ab.value-ah` nan. The isfinite leg must catch it."""
    def nan_row(x, f0, sr, **kw):
        return am.Estimate(float("nan"), True, "", {}, None)
    ed.defect_2(inharmonic=nan_row)
    out = capsys.readouterr().out
    assert len(ed.refusals) == 7
    assert ed.fails == []
    assert "nan" not in out


def test_defect2_runs_green_on_the_real_estimators(_reset_verdict_lists, capsys):
    """The boundary control (the AC5 shape): no injection, the real
    estimator. defect 2's `fb` column asks for the floor ON PURPOSE
    (`min_headroom_db=None` -- the alias-free control whose whole point is
    that the reading IS the floor), so nothing refuses, every row prints,
    and the floor-vs-answer verdict runs and holds. This control is the one
    that would have caught the live TypeError: it needs no corpus, so it
    runs wherever the tests run."""
    ed.defect_2()
    out = capsys.readouterr().out
    assert ed.refusals == []
    assert ed.fails == []
    assert "answer moved" in out
    assert "the Hann floor swings" in out


def test_defect4_row_refusal_is_refused_not_typeerror(
        _reset_verdict_lists, capsys, monkeypatch):
    """SITES a99a408e :265 / :268, defect 4's per-signal rows (`new.value`,
    `new.value-old`, `d_new.value`, `d_new.value-d_old`, and the same
    statement's `lo.value` / `hi.value` prints the enumeration did not name
    but the same guard protects -- a guard below that print would be
    defect 1's defeat note again). INJECTED DEFECT: `find_line` refuses,
    None-encoded -- the shipped encoding -- and `tone_ratio_db` and
    `difference_tone_db` inherit the refusal through it. Covers defect 4's
    rows: REFUSED once per row, no row printed."""
    def refusing_find_line(x, sr, hz, *, search=0.1):
        return am._fail("injected: no line here", nominal_hz=hz)
    monkeypatch.setattr(rc, "find_line", refusing_find_line)
    t = np.arange(int(0.1 * SR)) / SR
    two = 1.0 * np.sin(2 * math.pi * 823.70 * t) + 0.5 * np.sin(2 * math.pi * 558.35 * t)
    ed._defect4_row("ours", two, SR)
    out = capsys.readouterr().out
    assert len(ed.refusals) == 1
    assert ed.fails == []
    assert "REFUSED" in out
    assert "partial balance" not in out, "no row was printed"
    assert "nan" not in out


def test_defect4_row_nan_carrying_reading_is_refused_not_printed(
        _reset_verdict_lists, capsys, monkeypatch):
    """The opposite encoding at the same site: a nan-carrying ok=True
    `tone_ratio_db` among healthy neighbours printed a plausible `+nan` in
    the report line and made `new.value-old` nan. Covers defect 4's rows on
    the isfinite leg."""
    def nan_ratio(x, sr, hz_hi, hz_lo, **kw):
        return am.Estimate(float("nan"), True, "", {}, None)
    monkeypatch.setattr(rc, "tone_ratio_db", nan_ratio)
    t = np.arange(int(0.1 * SR)) / SR
    two = 1.0 * np.sin(2 * math.pi * 823.70 * t) + 0.5 * np.sin(2 * math.pi * 558.35 * t)
    ed._defect4_row("ours", two, SR)
    out = capsys.readouterr().out
    assert len(ed.refusals) == 1
    assert ed.fails == []
    assert "nan" not in out


def test_closed_form_check_none_encoded_refusal_is_refused_not_typeerror(
        _reset_verdict_lists, capsys):
    """SITE a99a408e :277 -- the one with a verdict downstream: `.value` was
    read INSIDE the `check(...)` predicate itself. INJECTED DEFECT:
    None-encoded refusal. Before the repair `abs(None - ...) < 0.2` raised
    TypeError -- NO VERDICT. REFUSED is required, and the neighbouring
    difference-tone verdict, which is independent, must still run."""
    def refusing(*a, **k):
        return am._fail("injected: the closed-form reading refuses")
    ed._defect4_synthetic_checks(tone_ratio=refusing)
    out = capsys.readouterr().out
    assert len(ed.refusals) == 1
    assert ed.fails == []
    assert "REFUSED" in out
    assert "finding the lines gets the closed-form answer" not in out, \
        "the check did not run against a reading that does not exist"
    assert "a difference tone that is not there is refused" in out, \
        "the independent verdict still ran"


def test_closed_form_check_nan_carrying_reading_is_refused_not_failed(
        _reset_verdict_lists, capsys):
    """The encoding this site is filed for: a nan-carrying reading made
    `abs(nan - ...) < 0.2` silently False -- a FAILED verdict where REFUSED
    is the truth, the exact pathology #805 was filed for, now with a
    verdict downstream. Covers defect 4's closed-form check."""
    def nan_ratio(*a, **k):
        return am.Estimate(float("nan"), True, "", {}, None)
    ed._defect4_synthetic_checks(tone_ratio=nan_ratio)
    out = capsys.readouterr().out
    assert len(ed.refusals) == 1
    assert ed.fails == [], "the predicate must not turn a nan into FAILED"
    assert "FAIL" not in out


def test_tone_ratio_shipped_exhibit_still_wrong_on_two_partials():
    """SITE a99a408e :245, LEFT DELIBERATELY UNGUARDED -- the
    exhibit-integrity AC. `tone_ratio_shipped` is a faithful pre-#108
    reimplementation, and it must still reproduce the wrong answer that
    makes it an exhibit: on two partials at the machine's own frequencies
    the rectangular projection at the nominal points reads their leakage,
    more than a dB off the closed form, and returns a plain float."""
    t = np.arange(int(0.1 * SR)) / SR
    two = 1.0 * np.sin(2 * math.pi * 823.70 * t) + 0.5 * np.sin(2 * math.pi * 558.35 * t)
    old = ed.tone_ratio_shipped(two, SR, 800.0, 540.0)
    assert isinstance(old, float)
    assert abs(old - 20 * math.log10(2.0)) > 1.0, \
        "the exhibit no longer exhibits the defect it exists to keep reproducible"


def test_tone_ratio_shipped_exhibit_still_raises_on_a_refusing_reading(
        monkeypatch):
    """The other facet of the same AC: the exhibit is a faithful copy, so a
    refusing `tone_amplitude` still raises TypeError exactly as the shipped
    probe did -- NO VERDICT was the shipped behaviour, and the exhibit
    keeps it. If a later edit 'fixes' the exhibit this goes red: the crash
    is pinned behaviour, not an accident."""
    def refusing(x, hz, sr, **kw):
        return am._fail("injected: the shipped projection refuses")
    monkeypatch.setattr(am, "tone_amplitude", refusing)
    with pytest.raises(TypeError):
        ed.tone_ratio_shipped(np.ones(int(0.1 * SR)), SR, 800.0, 540.0)
