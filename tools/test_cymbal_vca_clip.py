"""Known answers and must-fail controls for tools/cymbal_vca_clip.py (#369 step 8).

The module makes two claims and they need different kinds of test:

  * a NEGATIVE one -- no position, drive or asymmetry of §10's asymmetric
    clipping lands inside both of the 808's measured ranges at once. A negative
    is only worth anything if the apparatus could have said yes, so the tests
    here plant a mixture that DOES land inside both boxes and assert that
    `verdict` accepts it, beside the real sweep that it refuses;
  * a CORRECTION to step 7 -- that its 9.4 dB energy gap is mostly the analysis
    filter and the source spectrum. That one is checked by reproducing step 7's
    own committed numbers from this module's independently written chain, so the
    correction cannot be an artefact of having built a different chain.

Plus the usual: every named property must be moved by some injected defect, the
two blind defects must be blind, and the numbers quoted in the docstring must
come from the artifacts rather than from the prose.

And one about how the matrix is READ: a bound whose estimator refuses is
neither a pass nor a fail. It must not print as FAIL, count as MOVED, or read
as NOT BLIND. A refusal that leaves a control without an answer makes the run
REFUSED (2), unless something MEASURED failed, which is FAIL (1) and wins
(#1769).
"""
import math
import pathlib
import sys

import numpy as np
import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import cymbal_vca_clip as v      # noqa: E402
import cymbal_m_origin as mo     # noqa: E402
import cymbal_low_tail as lt     # noqa: E402
import cymbal_tone_realisation as tr  # noqa: E402
import exit_contract                 # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[1]
LOW_TAIL = ROOT / "docs/scorecard/cymbal-369/low-tail/low-tail.json"
M_ORIGIN = ROOT / "docs/scorecard/cymbal-369/low-tail/m-origin.json"


# ---------------------------------------------------------------------------
# the chain: the same filters, not a parallel model of them
# ---------------------------------------------------------------------------
def test_the_time_domain_cascade_is_the_magnitude_model_exactly():
    """`chain_db` reads the response off the very filters `render` runs, and
    `cymbal_tone_realisation` computes it from the same bank registers. Anything
    but equality is a transcription bug, so the bound is 1e-6 dB rms and not a
    tolerance chosen to pass."""
    assert v._chain_exact(tr.tone_poles()) < 1e-6


def test_the_analytic_band_energies_reproduce_m_origins_committed_figures():
    """Step 7's per-band M re Ln, recomputed from this module's chain. If these
    disagree the correction below is about a different circuit and means
    nothing."""
    e = mo.band_energies()
    for band in ("low", "decay", "short"):
        got = v.analytic_band_db(band)
        assert got["M"] == pytest.approx(e[band]["cand3_M_re_Ln"], abs=0.02)
        assert got["Mn"] == pytest.approx(e[band]["cand3_Mn_re_Ln"], abs=0.15)


def test_dropping_the_high_pass_changes_the_chain():
    """The paired negative for the two tests above: they must not pass on any
    cascade at all."""
    assert v._chain_exact(tr.tone_poles(), defect="NO_HIGHPASS") > 1.0


# ---------------------------------------------------------------------------
# the source
# ---------------------------------------------------------------------------
def test_the_staircase_is_six_squares_and_seven_levels():
    assert v.source_levels() == v.N_OSC + 1


def test_one_oscillator_is_two_levels_and_the_comb_control_catches_it():
    assert v.source_levels(defect="SINGLE_OSC") == 2
    assert v._source_comb(defect="SINGLE_OSC") < v.N_OSC + 1.0


def test_the_generators_own_aliasing_is_bounded_not_assumed():
    """The source-tilt correction rests on the staircase's real 1/n harmonics in
    M. A naively sampled square folds its own out-of-band harmonics back into
    exactly that region, and that would look identical to the finding."""
    assert v._source_alias() < 0.30


def test_the_documented_fundamentals_are_the_ones_in_the_reference():
    """§1.5's table, transcribed once. A frequency set that drifted from the
    reference would change the difference-tone comb the whole question is
    about."""
    assert v.OSC_HZ == (205.3, 369.6, 304.4, 522.7, 800.0, 540.0)
    assert v.DUTY == pytest.approx(0.4798)


# ---------------------------------------------------------------------------
# the nonlinearity's known answers
# ---------------------------------------------------------------------------
def test_the_difference_tone_is_the_analytic_second_order_coefficient():
    got, want = v._im_amp()
    assert got / want == pytest.approx(1.0, abs=0.02)


def test_a_symmetric_clipper_makes_no_difference_tone():
    """The paired negative. Without it `im-known-answer` would pass on any
    nonlinearity whatever, because it only ever checks one number."""
    assert v._asym_load_bearing() < -40.0


def test_the_symmetric_clippers_predicted_coefficient_is_exactly_zero():
    assert v.clip_a2(0.0, defect="SYMMETRIC_NL") == 0.0
    assert v.clip_a2(0.0) != 0.0


def test_the_clippers_departure_from_the_identity_scales_with_drive():
    """The clipper is normalised so its LINEAR term is the identity, which is
    what makes "drive -> -inf is the linear chain" a limit rather than an
    approximation, and why the -40 dB row of every sweep reproduces the `none`
    row to three decimals.

    The identity is NOT exact at finite drive -- the quadratic term is still
    there, which is the whole point of the block -- so what is asserted is that
    the residual falls by a decade per 20 dB, i.e. that it IS the second-order
    term and not some offset that would survive to zero drive."""
    rng = np.random.default_rng(0)
    x = 1e-3 * rng.standard_normal(4096)
    res = [float(np.max(np.abs(v.clip(x, d, v.ASYM, rms=1.0) - x))) / float(np.max(np.abs(x)))
           for d in (-40.0, -20.0, 0.0)]
    assert res[0] < 1e-4
    for a, b in zip(res, res[1:]):
        assert b / a == pytest.approx(10.0, rel=0.05)


def test_a_quadratic_halves_the_time_constant():
    """The algebra the DECAY half of the module's prediction rests on, measured.
    The prediction was still wrong, but not here -- see the module docstring."""
    assert v._product_law() == pytest.approx(1.0, abs=0.08)


def test_the_clipper_refuses_a_silent_input():
    with pytest.raises(v.Refused):
        v.clip(np.zeros(128), 0.0, v.ASYM)


def test_thd_rises_with_drive_and_the_refusal_threshold_is_reachable():
    """A refusal that no swept value can trigger is not a refusal."""
    t = [v.thd_db(d) for d in v.DRIVE_DB]
    assert all(b > a for a, b in zip(t, t[1:]))
    assert max(t) > v.MAX_THD_DB or min(t) < v.MAX_THD_DB


# ---------------------------------------------------------------------------
# the correction to step 7, which is the larger half of the result
# ---------------------------------------------------------------------------
def test_the_analysis_filter_is_accurate_on_the_low_band_and_not_on_the_others():
    """The finding, as a number in both directions. The low band's reference
    band IS its peak, so it reads true; the two bands peaking at 7.1 kHz have
    their M read several dB high by the analysis filter's own skirt."""
    leak = v.leakage_error()
    assert abs(leak["low"]["leak_M_db"]) < 0.6
    assert leak["decay"]["leak_M_db"] > 2.0
    assert leak["short"]["leak_M_db"] > 5.0


def test_the_rendered_bound_is_above_the_analytic_one():
    """Step 7's bound is analytic; the 808 figures it is compared against are
    filtered. Computing the bound the same way the measurement was made raises
    it, and by how much is the correction."""
    rb = v.rendered_bound(kind="white", step=12.0)
    assert rb["M"]["rendered_bound_db"] > rb["M"]["analytic_bound_db"] + 2.0


def test_the_documented_source_tilts_the_low_band_towards_the_machine():
    """§1.5's staircase is not flat. Through the SAME chain and the SAME
    analysis filter it puts more energy in M relative to Ln than white noise
    does, which is the second half of the correction."""
    st = v.source_tilt()
    assert st["low"]["tilt_M_db"] > 2.0


def test_no_linear_balance_lands_inside_both_808_boxes_at_once():
    """The joint constraint, which is what the next increment needs and what
    neither this step's energy bound nor step 7's decay finding says on its own.
    rho and M re Ln are not independent: the balances that raise rho are the ones
    that starve M. Run at a coarse grid so this is a test and not the experiment.
    """
    for kind in ("white", "staircase"):
        rb = v.rendered_bound(kind=kind, step=6.0)
        assert rb["n_balances_in_808_box"] == 0, \
            f"{kind}: a linear balance now matches -- {rb['balances_in_808_box']}"


def test_the_linear_balance_sweep_does_clear_the_skirt_baseline():
    """Step 7's §5 says a linear mix's rho_M(-10) "stays between 0.89 and 0.97 --
    it never even clears the skirt baseline". Rendered through the time-domain
    chain it reaches 1.24-1.35, above the 808's own top of 1.139. What survives
    is the joint statement above, not that one."""
    rb = v.rendered_bound(kind="staircase", step=6.0)
    assert rb["max_rho_M_-10"] > v.T_RHO_BASELINE_MAX


def test_the_correction_does_not_close_the_gap_entirely():
    """The honest other side: the corrections shrink step 7's 9.4 dB, they do
    not erase it. If this ever fails the claim in the docstring has to change,
    not the test."""
    rb = v.rendered_bound(kind="staircase", step=12.0)
    assert rb["M"]["rendered_bound_db"] < v.M_RE_LN_808_RANGE[0]


# ---------------------------------------------------------------------------
# the verdict: it must be able to say yes
# ---------------------------------------------------------------------------
def _row(pos, drive, m, rho, mn=-12.0, rho_mn=1.0):
    return {"position": pos, "drive_db": drive, "M_re_Ln": m, "Mn_re_Ln": mn,
            "rho_M_-10": rho, "rho_Mn_-10": rho_mn, "refused": {}, "thd_db": -20.0}


def test_the_verdict_accepts_a_row_inside_both_boxes():
    """The control without which the module's negative answer is worthless: a
    planted row that IS a match must be reported as one."""
    lo, hi = v.RHO_M_808_RANGE
    e_lo, e_hi = v.M_RE_LN_808_RANGE
    rows = [_row("sum", 0.0, (e_lo + e_hi) / 2, (lo + hi) / 2)]
    out = v.verdict(rows)
    assert out["positions_matching"] == ["sum"]
    assert "refused" not in out


def test_the_verdict_rejects_two_rows_that_each_satisfy_one_half():
    """Wrong-then-right 4, as a permanent control: the first version answered
    "reaches both targets at ['sum']" from two rows twelve dB apart."""
    lo, hi = v.RHO_M_808_RANGE
    e_lo, e_hi = v.M_RE_LN_808_RANGE
    rows = [_row("sum", -20.0, -9.0, (lo + hi) / 2),          # rho in box, energy not
            _row("sum", 0.0, (e_lo + e_hi) / 2, 3.0)]         # energy in box, rho not
    out = v.verdict(rows)
    assert out["positions_matching"] == []
    assert "refused" in out


def test_the_verdict_rejects_an_overshoot_rather_than_crediting_it():
    """Being three times the 808's ratio is not "at least as good as"; both
    bounds of the box are enforced."""
    e_lo, e_hi = v.M_RE_LN_808_RANGE
    out = v.verdict([_row("sum", 6.0, (e_lo + e_hi) / 2, 3.02)])
    assert out["positions_matching"] == []


def test_the_verdict_reports_how_far_the_closest_row_missed_by():
    out = v.verdict([_row("post_vca", -20.0, -6.92, 1.1108)])
    assert out["closest_row"]["position"] == "post_vca"
    assert "closest is post_vca" in out["refused"]


# ---------------------------------------------------------------------------
# the targets are read from the artifacts, not from the prose
# ---------------------------------------------------------------------------
@pytest.mark.skipif(not (LOW_TAIL.is_file() and M_ORIGIN.is_file()),
                    reason="step 7's artifacts are not present")
def test_targets_agree_with_the_committed_artifacts():
    t = v.targets()
    assert set(t["verified_against"]) == {"low-tail.json", "m-origin.json"}
    assert t["per_window_808_rho_M"][v.T_RHO_WINDOW]["min"] == pytest.approx(
        v.RHO_M_808_RANGE[0], abs=1e-4)


@pytest.mark.skipif(not LOW_TAIL.is_file(), reason="step 7's artifact is not present")
def test_targets_refuse_a_quote_that_has_drifted(monkeypatch):
    """A target copied into a docstring goes stale silently; one checked against
    its source cannot. Asserted by breaking it."""
    monkeypatch.setattr(v, "T_RHO_808_MIN", 1.200)
    with pytest.raises(v.Refused):
        v.targets()


@pytest.mark.skipif(not M_ORIGIN.is_file(), reason="step 7's artifact is not present")
def test_targets_refuse_a_window_mismatch(monkeypatch):
    """rho is a Schroeder crossing time in a window; comparing across windows is
    the mistake `targets()` itself made on its first run."""
    monkeypatch.setattr(v, "T_RHO_WINDOW", "3.5s")
    with pytest.raises(v.Refused):
        v.targets()


# ---------------------------------------------------------------------------
# the defect matrix
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("defect", v.DEFECTS)
def test_every_injected_defect_turns_at_least_one_property_red(defect):
    got = v.properties(defect=defect)
    assert any(not got[p]["pass"] for p in v.PROPERTIES), \
        f"{defect} is invisible to the suite"


@pytest.mark.parametrize("defect", v.BLIND_BY_CONSTRUCTION)
def test_the_blind_defects_are_verified_blind(defect):
    got = v.properties(defect=defect)
    bad = [p for p in v.PROPERTIES if not got[p]["pass"]]
    assert not bad, f"{defect} was meant to be blind and moved {bad}"


def test_the_clean_matrix_passes_and_main_refuses_when_it_does_not(monkeypatch):
    """`main` must not report a measurement over a failed control matrix, and
    that is asserted by making the matrix fail. A MEASURED failure is exit 1:
    the comparison ran and disagreed (`tools/exit_contract.py`)."""
    monkeypatch.setattr(v, "check", lambda **k: (False, ["forced"], exit_contract.FAIL))
    assert v.main([]) == exit_contract.FAIL


# ---------------------------------------------------------------------------
# a refusal is neither a pass nor a fail (#1769, the ruling in #1699)
# ---------------------------------------------------------------------------
# `properties()` records a bound whose estimator refused as
# {"value": None, "pass": False, "refused": ...}. `pass` is a bool, so every
# reader that asks "not pass?" read a refusal as a MEASURED failure: printed
# FAIL, credited as MOVED under a defect, read as NOT BLIND, and exited 1
# under a REFUSED label. The two REAL controls below force ONE `_BOUNDS`
# entry, so `properties()`'s row shape itself is under test; the scripted ones
# pin how `check()` reads each row, which the real defects cannot isolate
# because they move other bounds as well.
FORCED = "forced refusal (control)"


def _on_clean(real, behaviour):
    """The real bound under every defect; `behaviour` on the clean run. A
    forced bound that also misbehaves under the defects would contaminate the
    defect and blind rows this control is not about."""
    def fn(*, defect=None):
        if defect is None:
            return behaviour()
        return real(defect=defect)
    return fn


def _refuse():
    raise v.Refused(FORCED)


def _fail():
    return 0.0


def _controls_block(out):
    return out.split("the ANALYSIS filter")[0]


def _defect_row(lines, name):
    """A defect's one row in the matrix. Not `_row`, which is the verdict
    tests' sweep row above, and not the indented reason lines a refusal adds
    beneath a defect's row."""
    hits = [l for l in lines if l.startswith(f"  {name} ")]
    assert len(hits) == 1, f"{name} has {len(hits)} rows: {hits}"
    return hits[0]


def _clean_row(lines, name):
    hits = [l for l in lines if l.lstrip().startswith(f"{name} ")]
    assert len(hits) == 1, f"{name} has {len(hits)} clean rows: {hits}"
    return hits[0]


def _cell(row, prop):
    """The matrix cell of `prop` in a defect row: an 18-wide field after the
    20-character `  {defect:18s}` prefix, one space between cells."""
    i = v.PROPERTIES.index(prop)
    return row[20 + 19 * i: 20 + 19 * i + 18].strip()


def test_a_refusing_clean_bound_exits_refused_not_fail(monkeypatch, capsys):
    """Wrong-then-right, as a control: on main this printed `FAIL` and exited 1
    under a REFUSED label -- a refusal rendered as a measured failure, with
    the label and the status disagreeing. The message is pinned beside the
    status because argparse's usage error ALSO exits 2."""
    fn, ok = v._BOUNDS["product-law"]
    monkeypatch.setitem(v._BOUNDS, "product-law", (_on_clean(fn, _refuse), ok))
    assert v.main([]) == exit_contract.REFUSED
    assert exit_contract.REFUSED in exit_contract.NO_EVIDENCE
    out = _controls_block(capsys.readouterr().out)
    assert "REFUSED: this instrument's own controls have no verdict" in out, out[-300:]
    lines = out.splitlines()
    clean, matrix = out.split("\n  defect ")
    row = _clean_row(clean.splitlines(), "product-law")
    assert FORCED in row and "REFUSED" in row and "FAIL" not in row, row
    assert "FAIL" not in clean
    for d in v.BLIND_BY_CONSTRUCTION:
        assert "verified blind" in _defect_row(lines, d)
    assert "NOT BLIND" not in matrix and "TURNED NOTHING RED" not in matrix, matrix


def test_a_measured_failure_outranks_a_refusal(monkeypatch, capsys):
    """A measured FAIL is a RESULT (1) and wins: a refusal never hides it,
    and it never turns the refusal into a FAIL."""
    fn, ok = v._BOUNDS["product-law"]
    monkeypatch.setitem(v._BOUNDS, "product-law", (_on_clean(fn, _refuse), ok))
    fn, ok = v._BOUNDS["tau-reads-back"]
    monkeypatch.setitem(v._BOUNDS, "tau-reads-back", (_on_clean(fn, _fail), ok))
    assert v.main([]) == exit_contract.FAIL
    out = capsys.readouterr().out
    assert "FAIL: this instrument's own controls do not pass" in out, out[-300:]
    clean = out.split("\n  defect ")[0].splitlines()
    refused = _clean_row(clean, "product-law")
    measured = _clean_row(clean, "tau-reads-back")
    assert "REFUSED" in refused and "FAIL" not in refused, refused
    assert "FAIL" in measured and "REFUSED" not in measured, measured


def test_the_clean_real_run_still_exits_zero_with_every_defect_moved(tmp_path, capsys):
    """The unchanged path, on the real tree: the process exits 0, every defect
    moves something MEASURABLE, both blind defects are verified blind by
    measurement.

    And the live instance of #1769, which this run found: NO_NONLINEARITY and
    SYMMETRIC_NL zero the clipper's a2, so `im-known-answer` -- a ratio over
    the predicted a2 -- is 0/0 under them and REFUSES. On main both cells
    printed MOVED, crediting a refusal as a detection. Each defect is still
    seen through its other, measured cells, so its row's question is answered
    and the exit is unchanged; the cell is REFUSED, and REFUSED appears
    nowhere else in the matrix."""
    out_json = tmp_path / "o.json"
    assert v.main(["--out", str(out_json)]) == exit_contract.PASS
    block = _controls_block(capsys.readouterr().out)
    clean, matrix = block.split("\n  defect ")
    assert "REFUSED" not in clean and "FAIL" not in clean, clean
    assert "NOT BLIND" not in matrix and "TURNED NOTHING RED" not in matrix, matrix
    assert "NO VERDICT" not in matrix, matrix
    lines = block.splitlines()
    for d in v.DEFECTS:
        assert "MOVED" in _defect_row(lines, d), d
    for d in v.BLIND_BY_CONSTRUCTION:
        assert "verified blind" in _defect_row(lines, d), d
    a2_zero = ("NO_NONLINEARITY", "SYMMETRIC_NL")
    for d in v.DEFECTS:
        assert _cell(_defect_row(lines, d), "im-known-answer") == \
            ("REFUSED" if d in a2_zero else ""), d
    reasons = [l for l in lines if "REFUSED, not a detection" in l]
    assert len(reasons) == len(a2_zero), reasons
    assert all("second-order coefficient is zero" in r for r in reasons), reasons


def _scripted(clean, under):
    """A `properties()` whose rows are scripted: `clean` maps a property to
    its row on the clean measurement, `under[defect]` likewise under a defect;
    every property not named returns a measured pass. Only the two row shapes
    the real `properties()` emits are used."""
    def properties(*, defect=None):
        rows = clean if defect is None else under.get(defect, {})
        return {p: dict(rows.get(p, {"value": 1.0, "pass": True})) for p in v.PROPERTIES}
    return properties


REFUSED_ROW = {"value": None, "pass": False, "refused": FORCED}
FAILED_ROW = {"value": 0.0, "pass": False}


def test_a_defect_that_only_makes_a_bound_refuse_is_not_credited_as_a_detection(monkeypatch):
    """Wrong-then-right, as a control: on main `moved = {k: not pass}` read
    this defect as MOVED, so a defect that silences an estimator passed the
    "turned nothing red" control. A refusal is no detection and no verdict."""
    under = {d: {"im-known-answer": FAILED_ROW} for d in v.DEFECTS if d != "NO_NONLINEARITY"}
    under["NO_NONLINEARITY"] = {"product-law": REFUSED_ROW}
    monkeypatch.setattr(v, "properties", _scripted({}, under))
    res = v.check()
    row = _defect_row(res[1], "NO_NONLINEARITY")
    assert "MOVED" not in row, row
    assert "REFUSED" in row and FORCED in row, row
    assert res[0] is False
    assert res[2] == exit_contract.REFUSED


def test_a_defect_that_moves_something_measurable_is_seen_even_if_another_cell_refuses(monkeypatch):
    """The live shape on the real tree: NO_NONLINEARITY and SYMMETRIC_NL zero
    the clipper's a2, so `im-known-answer` refuses under them. The defect is
    seen through other cells, so its row answers its question and the exit is
    unchanged -- but the refused cell is REFUSED, never MOVED. A strict "any
    refusal exits 2" would make the real instrument refuse on every run, and
    crediting the cell as MOVED is the defect; this pins the line between."""
    under = {d: {"im-known-answer": FAILED_ROW} for d in v.DEFECTS}
    under["NO_NONLINEARITY"] = {"im-known-answer": FAILED_ROW, "product-law": REFUSED_ROW}
    monkeypatch.setattr(v, "properties", _scripted({}, under))
    res = v.check()
    row = _defect_row(res[1], "NO_NONLINEARITY")
    assert _cell(row, "im-known-answer") == "MOVED", row
    assert _cell(row, "product-law") == "REFUSED", row
    assert any("REFUSED, not a detection" in l and FORCED in l for l in res[1]), res[1]
    assert res[0] is True
    assert res[2] == exit_contract.PASS


def test_a_blind_defect_that_makes_a_bound_refuse_has_no_verdict_not_not_blind(monkeypatch):
    """Wrong-then-right, as a control: on main `bad = [k for k if not pass]`
    read this as NOT BLIND. The defect's blindness was not measured, so it is
    neither verified nor refuted."""
    under = {d: {"im-known-answer": FAILED_ROW} for d in v.DEFECTS}
    under["POST_GAIN_2X"] = {"product-law": REFUSED_ROW}
    monkeypatch.setattr(v, "properties", _scripted({}, under))
    res = v.check()
    row = _defect_row(res[1], "POST_GAIN_2X")
    assert "NOT BLIND" not in row and "(blind by construction, verified blind)" not in row, row
    assert "NO VERDICT" in row and "REFUSED" in row and FORCED in row, row
    assert "verified blind" in _defect_row(res[1], "SEED_PHASE")
    assert res[0] is False
    assert res[2] == exit_contract.REFUSED


@pytest.mark.parametrize("measured_failure", ["invisible-defect", "not-blind", "clean-fail"])
def test_a_measured_control_failure_is_still_exit_1_and_outranks_a_refusal(monkeypatch, measured_failure):
    """The paths that were already exit 1 stay exit 1, and each wins over a
    refusal elsewhere in the matrix."""
    under = {d: {"im-known-answer": FAILED_ROW} for d in v.DEFECTS}
    clean = {}
    if measured_failure == "invisible-defect":
        under["NO_NONLINEARITY"] = {}                  # moves nothing, refuses nothing
    elif measured_failure == "not-blind":
        under["POST_GAIN_2X"] = {"im-known-answer": FAILED_ROW}
        clean = {"product-law": REFUSED_ROW}
    else:
        clean = {"product-law": REFUSED_ROW, "tau-reads-back": FAILED_ROW}
    monkeypatch.setattr(v, "properties", _scripted(clean, under))
    res = v.check()
    assert len(res) == 3, "check() must return (ok, lines, status): the exit status is data"
    assert res[0] is False
    assert res[2] == exit_contract.FAIL


def test_an_implementation_error_in_a_bound_is_not_relabelled_refused(monkeypatch):
    """The input that would defeat the third state (verification-rules 8): a
    bound that CRASHES has not refused, and must still escape as itself. Only
    `Refused` is a refusal; a blanket `except Exception` would hide a bug as
    "no evidence"."""
    name = next(iter(v._BOUNDS))
    def crash(**k):
        raise ValueError("an implementation error, not a precondition")
    monkeypatch.setitem(v._BOUNDS, name, (crash, lambda x: True))
    with pytest.raises(ValueError, match="an implementation error"):
        v.check()
    with pytest.raises(ValueError, match="an implementation error"):
        v.main([])


def test_rho_is_refused_on_a_band_below_the_instruments_qualified_range():
    """Imported from `cymbal_m_origin` rather than restated, and exercised: a
    band 30 dB below Ln is division by almost nothing."""
    m = {"bands": {"M": {"energy_j": 1e-4}, "Mn": {"energy_j": 1e-6},
                   v.REF: {"energy_j": 1.0}},
         "rho": {"M": {"-10": 9.9, "-5": 9.9}, "Mn": {"-10": 9.9, "-5": 9.9}}}
    monkey = lt.measure
    try:
        lt.measure = lambda *a, **k: m
        out = v.read(np.zeros(8), v.SR)
    finally:
        lt.measure = monkey
    assert out["rho_M_-10"] is None and "M" in out["refused"]
    assert out["rho_Mn_-10"] is None and "Mn" in out["refused"]


# ---------------------------------------------------------------------------
# the headline, against the artifact rather than the prose
# ---------------------------------------------------------------------------
def test_the_real_sweep_is_refused_at_every_asymmetry():
    """The result itself, at a coarse drive grid so it is a test and not a
    re-run of the experiment. If any asymmetry ever matches, the module's
    answer changes and this test is where that is noticed."""
    for asym in (0.15, 0.8):
        rows = v.sweep(asym=asym, drives=(-20.0, 0.0, 12.0))
        out = v.verdict(rows)
        assert out["positions_matching"] == [], \
            f"asymmetry {asym} now matches: {out['per_position']}"


def test_the_linear_chain_from_the_documented_source_beats_our_shipped_kit():
    """The other half of the correction, as a number: rendered from §1.5's
    staircase the LINEAR chain reads rho_M near 0.99 and rho_Mn near 1.10,
    where our shipped kit reads 0.871 and 0.878."""
    r = v.read(*v.render(position="none", drive=0.0))
    assert r["rho_M_-10"] > 0.95
    assert r["rho_Mn_-10"] > 1.00
