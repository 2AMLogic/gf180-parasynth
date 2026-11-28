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
 5. `main()` names the estimator's OWN refusals (#1912): an
    `audio_measure.InsufficientEvidence` raised inside a defect section
    exits 2 as REFUSED, while a plain `AssertionError` -- its base class,
    and a real bug -- still leaves `main()` uncaught.
"""
from __future__ import annotations


import ast
import math
import pathlib
import sys

import numpy as np
import pytest
from scipy.io import wavfile

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "model"))
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "tools" / "probes"))

import audio_measure as am                                          # noqa: E402
import estimator_defects as ed                                      # noqa: E402
import refusal                                                      # noqa: E402
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
    assert out.count("finding the lines gets the closed-form answer") == 1, \
        "the phrase may appear only in the refusal naming what was not " \
        "checked -- never as a verdict line against a nonexistent reading"
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


# ---------------------------------------------------------------------------
# 6. section 6's nine unguarded `.value` sites (#845)
# ---------------------------------------------------------------------------
# defect 6 reads two estimators through seams (`shipped=`, `repaired=`),
# four blocks: the shipped-vs-repaired TABLE (printed figures), the
# constant-16 % READBACK (a division of two `.value` reads), the
# grid-RECONCILIATION rows (printed figures), and the olds/news appends
# feeding the two swing VERDICTS. Refusal counts below: table 7 rows,
# readback 3 rows, reconciliation 2 grid labels, spread 3 pole counts --
# a rigged estimator refuses all of them, 15 in total.
def _refuse_shipped(freqs, g, cut_hz):
    return am._fail("injected: the shipped corner refuses",
                    plateau_db=-0.904)


def _refuse_repaired(cut_hz):
    def f(freqs, g):
        return am._fail("injected: the repaired corner refuses",
                        plateau_db=-0.904)
    return f


def test_defect6_table_refusal_none_encoded_is_refused_not_typeerror(
        _reset_verdict_lists, capsys):
    """SITES: defect 6's shipped-vs-repaired table rows (a99a408e :490-491,
    moved by #792/#839; locate by expression) -- printed FIGURES, so this
    control has less power than the verdict controls below. INJECTED
    DEFECT: the shipped estimator refuses, None-encoded. Before the repair
    the table's first row raised TypeError at its first arithmetic
    `old.value/fc` (the detail carries `plateau_db`, so the crash lands on
    `.value` and not on the detail lookup) -- NO VERDICT for anything
    behind it. The seam feeds all four blocks, so fifteen readings refuse;
    seven of them are table rows, asserted on the table's own refusal
    message."""
    ed.defect_6(shipped=_refuse_shipped)
    out = capsys.readouterr().out
    assert len(ed.refusals) == 15, "every block the rigged seam feeds"
    assert out.count("no row was printed for this cutoff") == 7, \
        "one refusal per cutoff row in the table"
    assert ed.fails == []
    assert out.count("REFUSED") == 15
    assert "injected: the shipped corner refuses" in out
    assert "SHIPPED (pre-#150)" in out, "the header printed, no row under it did"
    assert "nan" not in out


def test_defect6_table_refusal_nan_carrying_is_refused_not_a_plausible_row(
        _reset_verdict_lists, capsys):
    """The opposite encoding at the same table sites: an ok-carrying nan
    sails past an `.ok`-only guard and printed a plausible nan row for every
    cutoff. The isfinite leg must catch it. Printed-figure coverage again;
    the verdict controls carry the power. The seam feeds all four blocks:
    fifteen readings refuse."""
    def nan_shipped(freqs, g, cut_hz):
        return am.Estimate(float("nan"), True, "", {"plateau_db": -0.904}, None)
    ed.defect_6(shipped=nan_shipped)
    out = capsys.readouterr().out
    assert len(ed.refusals) == 15
    assert out.count("no row was printed for this cutoff") == 7
    assert ed.fails == []
    assert "nan" not in out


def test_defect6_readback_refusal_none_encoded_is_refused_not_typeerror(
        _reset_verdict_lists, capsys):
    """SITES: the constant-16 % readback division (a99a408e :501-502, the
    :245 class -- BOTH operands are `.value` reads and the arithmetic is a
    division), plus the grid-reconciliation rows it shares the rig with
    (a99a408e :513-515). INJECTED DEFECT: the shipped estimator refuses on
    the GOT curve ONLY -- a refusal in exactly one operand of the division;
    the reference side passes through the real estimator. Before the repair
    `got.value / ref.value` raised TypeError. Counts: readback 3 rows +
    reconciliation 2 grid labels. The BOUNDARY leg: the swing verdicts read
    neither rig's curve, so they must still run and hold -- a guard that
    refused the whole section here would have no power."""
    def refusing_got(freqs, g, cut_hz):
        got = -40.0 * np.log10(1.0 + (freqs / (cut_hz * 0.84)) ** 2)
        if np.allclose(g, got):
            return am._fail("injected: the readback's got side refuses",
                            plateau_db=-0.904)
        return ed.corner_shipped(freqs, g, cut_hz)
    ed.defect_6(shipped=refusing_got)
    out = capsys.readouterr().out
    assert len(ed.refusals) == 5
    assert ed.fails == []
    assert out.count("REFUSED") == 5
    assert "nan" not in out
    assert out.count("on a constant ratio") == 3, \
        "the unrigged swing verdicts still ran"


def test_defect6_readback_zero_denominator_is_refused_not_inf(
        _reset_verdict_lists, capsys):
    """SITES: the same readback division, the refusing-but-FINITE encoding
    the ok/None/isfinite legs do not see: a corner of 0.0 Hz (a negative
    one is the same predicate leg and reads as a plausible negative
    percentage instead of crashing). Injected on the REFERENCE curve at
    fc = 250 only -- one operand, one row. Before the repair block A's row
    printed a plausible `0.0000 -100.00%` and the readback raised
    ZeroDivisionError on `got/0.0`. A nonpositive corner is not a
    measurement. Counts: table 1 row (fc 250) + readback 1 row + both
    reconciliation labels + the 4-pole spread (whose ideal curve IS the
    reference curve) = 5; the 2- and 6-pole verdicts are unrigged and must
    still run."""
    def zero_reference_at_250(freqs, g, cut_hz):
        ref = -40.0 * np.log10(1.0 + (freqs / cut_hz) ** 2)
        if cut_hz == 250.0 and np.allclose(g, ref):
            return am.Estimate(0.0, True, "", {"plateau_db": -0.904}, None)
        return ed.corner_shipped(freqs, g, cut_hz)
    ed.defect_6(shipped=zero_reference_at_250)
    out = capsys.readouterr().out
    assert len(ed.refusals) == 5
    assert ed.fails == []
    assert out.count("REFUSED") == 5
    assert "nan" not in out
    assert out.count("on a constant ratio") == 2, \
        "the 2- and 6-pole verdicts still ran"


def test_defect6_spread_verdict_nan_carrying_is_refused_not_failed(
        _reset_verdict_lists, capsys):
    """THE CONTROL (#845 AC2): SITES the olds/news appends (a99a408e
    :529-530) feeding the two swing verdicts (a99a408e :531/:534). A
    nan-carrying operand made `max/min - 1 > 0.08` silently False -- a
    FAILED verdict where REFUSED is the truth, the exact pathology #805 was
    filed for, now with verdicts downstream. This is the control with the
    most power in this set. All fifteen blocks refuse (7 table + 3 readback
    + 2 reconciliation + 3 spreads), the verdict phrase may not appear at
    all, and the outcome is asserted on the printed token AND the exit
    code -- never exit status alone."""
    def nan_shipped(freqs, g, cut_hz):
        return am.Estimate(float("nan"), True, "", {"plateau_db": -0.904}, None)
    ed.defect_6(shipped=nan_shipped)
    out = capsys.readouterr().out
    assert len(ed.refusals) == 15
    assert ed.fails == [], "the predicate must not turn a nan into FAILED"
    assert "FAIL" not in out
    assert out.count("NEITHER swing verdict ran") == 3, \
        "one refusal per poles block, naming that the verdict did not run"
    assert out.count("on a constant ratio") == 0, \
        "the phrase may appear only in a verdict line, and no verdict ran"
    assert "nan" not in out
    assert ed.verdict_exit_code() == 2


def test_defect6_spread_verdict_none_encoded_is_refused_not_typeerror(
        _reset_verdict_lists, capsys):
    """The same verdict sites, None-encoded (the shipped encoding): before
    the repair the append's `.value / fc` raised TypeError at the first
    cutoff -- NO VERDICT for any block behind it. REFUSED is required."""
    ed.defect_6(shipped=_refuse_shipped)
    out = capsys.readouterr().out
    assert len(ed.refusals) == 15
    assert ed.fails == []
    assert "FAIL" not in out
    assert out.count("on a constant ratio") == 0
    assert ed.verdict_exit_code() == 2


def test_defect6_repaired_side_refusal_is_refused_too(_reset_verdict_lists,
                                                      capsys):
    """SITES: the repaired reads (`rc.filt_corner(fc)(F, g).value`,
    a99a408e :491/:502/:530) -- the seam the other controls leave real.
    The repaired estimator refusing must refuse its thirteen readings --
    thirteen, not fifteen: the grid-reconciliation block reads only the
    shipped exhibit and has no repaired operand -- and the verdicts behind
    the :530 read must be REFUSED, never FAILED."""
    ed.defect_6(repaired=_refuse_repaired)
    out = capsys.readouterr().out
    assert len(ed.refusals) == 13, \
        "table 7 + readback 3 + spread 3; reconciliation reads shipped only"
    assert ed.fails == []
    assert "FAIL" not in out
    assert out.count("REFUSED") == 13
    assert out.count("NEITHER swing verdict ran") == 3
    assert ed.verdict_exit_code() == 2


def test_defect6_runs_green_on_the_real_estimators(_reset_verdict_lists,
                                                   capsys):
    """The boundary control: no injection, the real estimators. Nothing
    refuses, nothing fails, every verdict runs and holds -- the guard did
    not widen into refusing everything, and the exhibit still exhibits (the
    shipped estimator's spread still clears its 8 % verdict). Needs no
    corpus, so it runs wherever the tests run."""
    ed.defect_6()
    out = capsys.readouterr().out
    assert ed.refusals == []
    assert ed.fails == []
    assert ed.verdict_exit_code() == 0
    assert out.count("REFUSED") == 0
    assert "TWO READINGS, RECONCILED" in out
    assert out.count("on a constant ratio") == 3


# ---------------------------------------------------------------------------
# 7. the `main()` boundary names the estimator's own refusals (#1912)
# ---------------------------------------------------------------------------
# The #1782 census read this module's `__main__` off the AST: every handler
# it defines names a `Refused` class and none names `InsufficientEvidence`, so
# an estimator that RAISES -- rather than returning a refused `Estimate` --
# would pass them all. Whether any input gets one to raise is not something
# the AST can say. Traced by running, red first, on main @ ff1532c5
# (2026-11-28), one rig per census site:
#
#   :105  unreachable  its argument is the synthetic strike of :121-124
#   :176  stopped      run_case.load_reference's `except am.NonFiniteAudio`
#   :183  shadowed     the same NaN render raises at :181 first -- see below
#   :417  stopped      the same gate as :176, on the cowbell
#   :420  stopped      run_case.prepare's `except am.NonFiniteAudio`
#   :715  ESCAPED      audio_measure.InsufficientEvidence, exit 1
#
# and the same shape three more times where no census row looks: the shipped
# copy `prepare_shipped`'s `am.is_silent` at :79 (what shadows :183 -- it
# tests the identical predicate one line earlier), and defect 5's two direct
# `wavfile.read` loops at :666 and :683. All four escapes are red runs behind
# `main()`'s widened boundary; :683 is also pinned at process level in
# tools/test_estimator_defects_refusal.py.
LOADED_VOICES = ("LC", "HC", "MC", "CB")      # what defect_1 and defect_4 load


def _strike():
    """A decaying 220 Hz strike as int16 PCM with a pre-onset lead: enough
    for `rc.load_reference` (not silent, 16-bit) and `rc.prepare` (an onset
    after index 0) to both accept it."""
    rng = np.random.default_rng(0)
    t = np.arange(int(0.3 * SR)) / SR
    x = np.exp(-t / 0.05) * np.sin(2 * math.pi * 220 * t) + 0.01 * rng.normal(size=len(t))
    x[:240] = 0.0
    return np.round(x / np.abs(x).max() * 20000).astype("<i2")


def _synthetic_corpus(root, nan=()):
    """A file per loaded voice under the relative path `run_case.REF_MAIN`
    names; the voices in `nan` are float32 and all NaN -- the one encoding of
    a corpus file that reaches `audio_measure.require_finite`, since an int16
    file cannot hold one."""
    for v in LOADED_VOICES:
        p = root / rc.REF_MAIN[v][0]
        p.parent.mkdir(parents=True, exist_ok=True)
        content = (np.full(int(0.3 * SR), np.nan, dtype=np.float32) if v in nan
                   else _strike())
        wavfile.write(str(p), SR, content)
    return root


def _finite_render(sound, *a, **k):
    """A STAND-IN for `rc.render_drum_solo`, which costs about 10 s a voice.
    Speed only: no assertion in this section reads a number computed from
    it. With the real render the same rigs raise the same exceptions."""
    t = np.arange(int(0.3 * SR)) / SR
    return np.exp(-t / 0.05) * np.sin(2 * math.pi * 220 * t), SR


@pytest.fixture()
def _main_on(tmp_path, monkeypatch, _reset_verdict_lists):
    """`ed.main()` over a synthetic corpus, argv set, the render standing in
    for speed. Whatever a test rigs on top is undone with the fixture's."""
    monkeypatch.setattr(ed, "REFS", _synthetic_corpus(tmp_path / "refs"))
    monkeypatch.setattr(rc, "render_drum_solo", _finite_render)
    monkeypatch.setattr(sys, "argv", ["estimator_defects.py"])
    return ed.main


def test_main_names_the_corner_estimators_evidence_refusal_refused_2(
        _main_on, monkeypatch, capsys):
    """SITE :715, the census row this issue is titled for. RED FIRST on main @
    ff1532c5: `defect_6` -> `corner_shipped` -> `am.corner_from_curve` ->
    `am.plateau_db` raised `InsufficientEvidence: plateau_db: no measured
    points in the reference band` straight out of `main()` -- an exit-1
    traceback, which `verdict_exit_code` renders exactly like a failed
    invariant. Unreachable on the section's own constants (`_ref_band` pins
    the band's lower edge at `freqs[0]`), so the empty band is injected: off
    every grid the section reads. Nothing between `corner_shipped` and
    `main()` names the class, and the section's `_bad_corner` guard runs only
    once `shipped()` has RETURNED. Premise: the class is no
    `refusal.Refused`, so the #1183 boundary could not have caught it."""
    assert not issubclass(am.InsufficientEvidence, refusal.Refused)
    monkeypatch.setattr(rc, "_ref_band", lambda freqs, cut_hz: (1e6, 2e6))
    assert _main_on() == 2
    out = capsys.readouterr().out
    assert "REFUSED" in out
    assert "no measured points in the reference band" in out
    assert "check(s) FAILED" not in out, "a refused run prints no fail summary"
    assert ed.fails == []


def test_main_names_a_non_finite_render_refused_2(_main_on, monkeypatch, capsys):
    """The `NonFiniteAudio` leg, and census site :183's record. That site
    (`rc.prepare` -> `require_finite`) is SHADOWED, not gated: the reference
    side is finite by the time it gets there (`load_reference` asserted it at
    :176), and the same NaN render raises one line earlier, at :181, through
    the shipped copy `prepare_shipped`'s `am.is_silent` (:79) -- on the
    identical predicate, so `rc.prepare` never sees a non-finite record.
    That earlier raise is itself uncaught: RED FIRST on main @ ff1532c5 with
    exit 1, an `audio_measure.NonFiniteAudio` traceback."""
    monkeypatch.setattr(rc, "render_drum_solo",
                        lambda sound, *a, **k: (np.full(480, np.nan), SR))
    assert _main_on() == 2
    out = capsys.readouterr().out
    assert "REFUSED" in out
    assert "non-finite samples" in out
    assert "check(s) FAILED" not in out
    assert ed.fails == []


def test_plain_assertion_error_through_main_is_not_a_refusal(
        _main_on, monkeypatch, capsys):
    """RULE 8's input, the one that defeats the WIDER guard.
    `am.InsufficientEvidence` IS an `AssertionError`, so a handler written
    against the base class -- or `except Exception` -- would relabel a real
    bug as a refused precondition. A plain `AssertionError` from a section
    must leave `main()` uncaught, which `sys.exit(main())` renders as exit 1:
    no REFUSED line, nothing recorded."""
    assert issubclass(am.InsufficientEvidence, AssertionError), \
        "the premise of this control: the handler's class is an AssertionError"

    def section(voices):
        raise AssertionError("injected: a plain AssertionError, not an evidence refusal")
    monkeypatch.setattr(ed, "defect_1", section)
    with pytest.raises(AssertionError) as info:
        _main_on()
    assert not isinstance(info.value, am.InsufficientEvidence)
    out = capsys.readouterr().out
    assert "REFUSED" not in out
    assert "check(s)" not in out, "no summary: the run did not end, it crashed"
    assert ed.refusals == []
    assert ed.fails == []


@pytest.mark.parametrize("site, call", [
    (":176-and-:417-load_reference",
     lambda root: rc.load_reference("LC", _synthetic_corpus(root, nan=("LC",)))),
    (":420-prepare",
     lambda root: rc.prepare(np.full(480, np.nan), SR)),
])
def test_run_case_gates_turn_a_non_finite_record_into_a_refused(
        tmp_path, site, call):
    """The census sites that STOPPED, each at its own gate: the estimator
    does raise at `audio_measure.require_finite`, and the `except
    am.NonFiniteAudio` two lines below it turns the raise into
    `run_case.Refused` -- a `refusal.Refused`, which the #1183 boundary
    already maps to 2. Red-first run on main @ ff1532c5: `REFUSED ... non-
    finite samples`, return 2, for each site's rig. They exited 2 before this
    issue's change and need none of it; this pins the gate that does it, so
    the boundary never silently becomes the only thing standing there."""
    with pytest.raises(rc.Refused) as info:
        call(tmp_path / "refs")
    assert isinstance(info.value, refusal.Refused)
    assert not isinstance(info.value, am.InsufficientEvidence)
    assert isinstance(info.value.__cause__, am.NonFiniteAudio), site


def test_prepend_silence_never_hands_prepare_a_non_finite_record(
        _reset_verdict_lists, monkeypatch):
    """SITE :105, UNREACHABLE, and why: its argument is the synthetic strike
    of :121-124 -- zeros, `np.exp`, `np.sin` -- so no input this module
    accepts makes `rc.prepare`'s `require_finite` refuse there. Pinned with a
    spy on `require_finite` under the real helper: every call finite, and
    `rc.prepare`'s own `side` among them, so the gate was reached."""
    seen = []
    real = am.require_finite

    def spy(x, what="the signal"):
        seen.append((what, bool(np.isfinite(np.asarray(x, dtype=np.float64)).all())))
        return real(x, what)
    monkeypatch.setattr(am, "require_finite", spy)
    ed._prepend_silence_invariance()
    assert seen, "the spy saw nothing, so it is not on the path and proves nothing"
    assert all(finite for _what, finite in seen)
    assert "the recording" in [what for what, _finite in seen], "`rc.prepare` was reached"


def test_a_refusal_after_a_failed_check_keeps_the_fail_line_and_exits_2(
        _main_on, monkeypatch, capsys):
    """The issue's test-plan edge: a refusal BESIDE a failed check. `check()`
    prints its FAIL line where the check ran, so an earlier failure stays on
    stdout ahead of the REFUSED line and the exit is 2, never 1 --
    `verdict_exit_code`'s order (REFUSED outranks FAILED), held at the
    boundary too. The summary does not print: a boundary refusal ends the
    run, exactly as #1183's does."""
    def section():
        ed.check(False, "injected: a failed invariant ahead of the refusal")
    monkeypatch.setattr(ed, "defect_3", section)
    monkeypatch.setattr(rc, "_ref_band", lambda freqs, cut_hz: (1e6, 2e6))
    assert _main_on() == 2
    out = capsys.readouterr().out
    assert out.index("FAIL  injected: a failed invariant") < out.index("REFUSED")
    assert ed.fails == ["injected: a failed invariant ahead of the refusal"]
    assert "check(s) FAILED" not in out
