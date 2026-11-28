"""Known answers and controls for tools/cymbal_tone_realisation.py (#369/#396).

Three kinds of test, and the middle kind is the one that matters:

  * closed-form known answers -- a single RC pole is 3.01 dB down at its own
    corner, a two-pole cascade's coefficients are p1+p2 and -p1 p2, a
    coefficient that will not fit its register raises rather than clipping;
  * cross-checks against numbers this tool did not produce -- the LEVEL
    stage's 2-20 kHz tilt as `tools/werner_fig4.py` measured it off Figure 10,
    and the tone stage's as step 4 recorded it in
    `model/cymbal_candidate.TONE_TILT_2K_20K_DB`;
  * controls that must fail, as the properties x defects matrix, including
    CAND2_LEVEL_ONLY -- the realisation that actually shipped as candidate 2
    and measured a negative -- so the bound stays discriminating rather than
    decorative;
  * controls that REFUSE: one that cannot be evaluated is NO VERDICT, booked in
    neither denominator and carried out of `check()` as None -- never the False
    of a control that was evaluated and moved nothing (#1853);
  * a CLEAN run that REFUSES is the same NO VERDICT, and a refusal from the
    report's tables or the record's bands reaches the caller as `REFUSED:` and
    exit 2 -- never a traceback wearing the FAIL status (#1992).
"""
from __future__ import annotations

import json
import math
import pathlib
import sys

import numpy as np
import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "model"), str(ROOT / "tools")]
import cymbal_candidate as cc                  # noqa: E402
import cymbal_tone_realisation as ct           # noqa: E402
import modal_fixed as mf                       # noqa: E402
import werner_fig4 as wf                       # noqa: E402
import werner_fig9 as w9                       # noqa: E402

SR = 48000


@pytest.fixture(scope="module")
def figs():
    fig9 = w9.from_artifact()[0]
    return fig9, ct.tone_poles(fig9), ct.level_corner_hz()


# ---- closed-form known answers --------------------------------------------


def test_a_single_analog_pole_is_3_01_db_down_at_its_own_corner():
    f = 1511.2
    s = 2j * np.pi * np.array([1.0, f])
    w = 2 * math.pi * f
    db = 20 * np.log10(np.abs(w / (s + w)))
    assert db[0] == pytest.approx(0.0, abs=0.01)
    assert db[1] - db[0] == pytest.approx(-3.0103, abs=0.01)


def test_the_matched_z_pole_is_3_db_down_at_the_corner_it_was_given():
    """The bank's mapping is z = exp(-2 pi f / SR); at f << SR it must put the
    half-power point at the frequency it was given.

    The reference has to be DC, not "the lowest frequency in the sweep". This
    assertion first read 1542.5 Hz against 1511.2 -- 2.07 % -- because it
    normalised at 200 Hz, which is itself 0.075 dB down. Near a half-power
    point the curve is steep enough to turn 0.075 dB into 31 Hz, which is the
    same normalisation trap as #101's window leakage in miniature.

    Against DC the answer is 1516.2 Hz: matched-z puts the half-power point
    0.33 % high at f / SR = 1/32, and that residual is the discretisation
    error, not a bug. It is two orders of magnitude below the +-50 % the
    reference gives for the 808's own unit-to-unit component spread.
    """
    f = 1511.2
    a1, a2 = cc.real_pole_regs([f])
    assert a2 == 0
    hz = np.linspace(1.0, 6000.0, 60000)
    db = ct.section_db(a1 / (1 << 24), 0.0, mf.RAW, hz)
    db -= float(ct.section_db(a1 / (1 << 24), 0.0, mf.RAW, np.array([0.0]))[0])
    corner = float(hz[int(np.argmin(np.abs(db + 3.0103)))])
    assert corner == pytest.approx(f, rel=0.005), corner


def test_two_real_poles_pack_as_their_sum_and_minus_their_product():
    f1, f2 = cc.HH3_P1_HZ, 1511.2
    p1, p2 = (math.exp(-2 * math.pi * f / SR) for f in (f1, f2))
    a1, a2 = cc.real_pole_regs([f1, f2])
    assert a1 == round((p1 + p2) * (1 << 24))
    assert a2 == round(-p1 * p2 * (1 << 24))


def test_one_pole_matches_what_revision_2_wrote_for_hh3s_third_pole():
    """Revision 2 wrote a1 = round(r * 2^24), a2 = 0 by hand; the helper must
    reproduce it exactly, or revision 3 would move a value it did not intend to."""
    r = math.exp(-2 * math.pi * cc.HH3_P1_HZ / SR)
    assert cc.real_pole_regs([cc.HH3_P1_HZ]) == (int(round(r * (1 << 24))), 0)


def test_a_coefficient_that_will_not_fit_its_register_raises():
    with pytest.raises(ValueError):
        cc.real_pole_regs([0.0, 0.0])          # two poles at DC: a1 = 2.0, one LSB over
    with pytest.raises(ValueError):
        cc.real_pole_regs([100.0, 200.0, 300.0])


def test_the_bank_has_no_numerator_code_for_a_single_zero():
    """Why the low and decay bands drop BOTH stages instead of keeping one
    zero: the decode has (1-z^-1)^0, ^2 and ^3 (and 1-z^-2), never ^1."""
    hz = np.array([1000.0, 10000.0])
    for code in (mf.RAW, mf.BP, mf.HP, cc.HP3):
        n = ct.numerator(code, hz)
        assert not np.allclose(np.abs(n), np.abs(1 - ct._z(hz)))


# ---- cross-checks against numbers this tool did not produce ---------------


def test_the_level_model_reproduces_figure_10s_own_measured_tilt(figs):
    """`werner_fig4.py` measured the LEVEL buffer's 2-20 kHz tilt off the
    digitised envelope; our single-pole model of it must agree."""
    blob = json.loads(wf.ARTIFACT.read_text())
    theirs = float(blob["level_stage"]["tilt_2k_to_20k_db"])
    corner = float(blob["level_stage"]["one_pole_corner_hz"])
    s = 2j * np.pi * np.array([2000.0, 20000.0])
    w = 2 * math.pi * corner
    ours = np.diff(20 * np.log10(np.abs(s / (s + w))))[0]
    assert ours == pytest.approx(theirs, abs=0.3), (ours, theirs)


def test_the_net_tilt_equals_step_4s_two_separately_measured_halves(figs):
    """Step 4 recorded LEVEL +16.6 dB and Ht3 -17.7 dB over 2-20 kHz, net
    -1.1 dB, from two different figures. Recompute the net and land there."""
    _, poles, corner = figs
    blob = json.loads(wf.ARTIFACT.read_text())
    want = float(blob["level_stage"]["tilt_2k_to_20k_db"]) + cc.TONE_TILT_2K_20K_DB
    got = ct.net_tilt_db("short", poles, corner)
    assert got == pytest.approx(want, abs=0.3), (got, want)
    assert got == pytest.approx(-1.1, abs=0.2)


def test_the_tone_model_reproduces_werner_fig9s_published_tilt_table(figs):
    fig9, poles, corner = figs
    tilt = w9.tilt_table(fig9)
    for band, name in ct.BAND_OF.items():
        ours = ct.analog_target_db(band, ct.THIRDS, poles, corner, level=False)
        ours -= float(np.interp(1000.0, ct.THIRDS, ours))
        theirs = np.array([v for _, v, _ in tilt[name]])
        assert np.max(np.abs(ours - theirs)) < ct.TILT_TOL_DB, band


def test_the_recorded_shape_errors_in_the_model_match_the_tool(figs):
    """`model/cymbal_candidate.TONE_REALISATION` quotes the error of each
    band's realisation in its own comment; a comment that drifts from the tool
    is the failure mode docs/failure-modes.md calls status carried in prose."""
    _, poles, corner = figs
    for band in ct.BANDS:
        got = ct.shape_error(band, poles, corner)["shape_max_db"]
        assert got == pytest.approx(cc.TONE_REALISATION[band]["shape_err_db"], abs=0.01), band


def test_the_level_corner_in_the_model_is_the_artifacts(figs):
    _, _, corner = figs
    assert cc.LEVEL_CORNER_HZ == pytest.approx(corner, abs=1.0)


# ---- the realisation, and the bank that has to hold it --------------------


def test_the_integer_bank_reproduces_the_named_poles(figs):
    _, poles, _ = figs
    a1, a2, code, hz = ct.section_regs("short", poles)
    probe = ct.THIRDS[(ct.THIRDS >= 1000.0) & (ct.THIRDS <= 16000.0)]
    got = ct.bank_impulse_db(a1, a2, code, probe)
    den = np.ones_like(ct._z(probe))
    for f in hz:
        den = den * (1 - math.exp(-2 * math.pi * f / SR) * ct._z(probe))
    want = ct._db(ct.numerator(code, probe) / den)
    assert np.max(np.abs((got - got[0]) - (want - want[0]))) < ct.BANK_TOL_DB


def test_the_shipped_image_is_the_thing_measured(figs):
    _, poles, _ = figs
    ok, detail = ct._image_bind(poles)
    assert ok, detail


def test_the_candidate_writes_no_hp3_so_the_shared_decode_need_not_change():
    img = dict(cc.candidate_kit())
    import drums_fx as dx
    codes = {img[dx.A_MODE + m * dx.MODE_STRIDE + 3]
             for m in (cc.M_CYH1, dx.M_CYHI, cc.M_CYH3, cc.M_CYH3B)}
    assert cc.HP3 not in codes, codes


def test_every_band_is_inside_the_bound_and_revision_2_was_not(figs):
    """The bound has to be satisfiable AND discriminating: revision 2 realised
    the same two stages as the LEVEL zero alone, and fails it in every band."""
    _, poles, corner = figs
    for band in ct.BANDS:
        ours = ct.shape_error(band, poles, corner)["shape_max_db"]
        rev2 = ct.shape_error(band, poles, corner, defect="CAND2_LEVEL_ONLY")["shape_max_db"]
        assert ours <= ct.SHAPE_BOUND_DB, (band, ours)
        assert rev2 > ct.SHAPE_BOUND_DB, (band, rev2)


def test_the_active_range_excludes_where_the_band_has_no_energy(figs):
    """The decay band is 65 dB down at 1 kHz, so its 5.5 dB deviation there
    cannot reach the residual and must not be counted."""
    _, poles, corner = figs
    e = ct.shape_error("decay", poles, corner)
    assert e["active_hz"][0] >= 5000.0
    assert abs(e["deviation_db"][0]) > ct.SHAPE_BOUND_DB      # 1 kHz, outside the range
    assert e["shape_max_db"] <= ct.SHAPE_BOUND_DB


# ---- controls that must fail ---------------------------------------------


EXPECTED = {
    #  defect              the properties it MUST move
    "SIGN_A1":            {"shape-bound", "bank-exact", "image-bind"},
    "SWAP_BANDS":         {"tone-tilt", "image-bind"},
    "NO_LEVEL_STAGE":     {"net-cancel", "shape-bound"},
    "CAND2_LEVEL_ONLY":   {"shape-bound", "image-bind"},
    "HP_POLE_NOT_LP":     {"image-bind"},
}


@pytest.mark.parametrize("defect", sorted(EXPECTED))
def test_each_control_moves_exactly_the_properties_it_should(defect, figs):
    fig9, poles, corner = figs
    got = ct.properties(poles, corner, fig9, defect=defect)
    moved = {p for p, (ok, _) in got.items() if not ok}
    assert moved == EXPECTED[defect], (defect, moved, EXPECTED[defect])


def test_a_uniform_gain_change_is_blind_everywhere_by_construction(figs):
    """The level rule renormalises each band, so a suite that saw a uniform
    6 dB here would be reading a level where it claims to read a shape."""
    fig9, poles, corner = figs
    got = ct.properties(poles, corner, fig9, defect="UNIFORM_6DB")
    assert all(ok for ok, _ in got.values()), got


def test_the_clean_gate_passes_and_every_control_is_caught(figs):
    """Unchanged by #1853: nothing refuses, so the summary carries no NO VERDICT
    count and the verdict is True, not a truthy stand-in for it."""
    fig9, poles, corner = figs
    ok, lines = ct.check(poles, corner, fig9)
    assert ok is True, "\n".join(lines)
    assert any("5/5 controls" in ln for ln in lines), "\n".join(lines)
    assert ("  5/5 controls turned at least one property red; "
            "1/1 blind-by-construction controls stayed blind" in lines), "\n".join(lines)
    assert "NO VERDICT" not in "\n".join(lines) and "REFUSED" not in "\n".join(lines)


def test_an_absent_artifact_refuses_rather_than_answering(tmp_path):
    with pytest.raises(ct.Refused):
        ct.level_corner_hz(tmp_path / "nope.json")
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"level_stage": {}}))
    with pytest.raises(ct.Refused):
        ct.level_corner_hz(bad)


# ---- a control that REFUSES is NO VERDICT, not a control that moved nothing ----
#
# #1853. `check()` printed the row REFUSED and then appended False to `caught`,
# so a control that did not run read as one that was evaluated and moved
# nothing: 4/5 controls, False, exit 1. The refusals are planted INSIDE the
# real `properties()` -- at `shape_error`, which every control reaches with its
# own `defect=` -- so the class raised and the handler exercised are the real
# ones. What is returned is the list of controls that actually fired: a plant
# that never ran must not pass as one that did (rule 5, condition 2).


def _plant(monkeypatch, *, raises=None, inert=()):
    """`raises` maps a control to the exception its evaluation raises; `inert`
    names synthetic controls to add to the matrix that are evaluated and move
    nothing."""
    raises = raises or {}
    real_props, real_err = ct.properties, ct.shape_error
    fired = []

    def shape_error(band, poles, corner_hz, *, defect=None):
        if defect in raises:
            fired.append(defect)
            raise raises[defect]
        return real_err(band, poles, corner_hz, defect=defect)

    def properties(poles, corner_hz, fig9, *, defect=None):
        if defect in inert:
            fired.append(defect)
            return real_props(poles, corner_hz, fig9)
        return real_props(poles, corner_hz, fig9, defect=defect)

    monkeypatch.setattr(ct, "shape_error", shape_error)
    monkeypatch.setattr(ct, "properties", properties)
    monkeypatch.setattr(ct, "DEFECTS", ct.DEFECTS + tuple(inert))
    return fired


def _row(defect):
    """The `check()` row for one control, as the matrix prints its name."""
    return f"  {defect:20s} "


def test_a_control_that_refuses_is_no_verdict_not_an_uncaught_control(figs, monkeypatch):
    """Red first on main: 4/5 controls, False. After: the row keeps its reason,
    the count and its denominator exclude it, and every other control is still
    evaluated and still caught."""
    fig9, poles, corner = figs
    fired = _plant(monkeypatch, raises={"SWAP_BANDS": ct.Refused("planted: unrealisable here")})
    verdict, lines = ct.check(poles, corner, fig9)
    assert fired == ["SWAP_BANDS"]
    assert _row("SWAP_BANDS") + "REFUSED: planted: unrealisable here" in lines
    for d in ct.DEFECTS:
        if d != "SWAP_BANDS":
            assert any(ln.startswith(_row(d)) and "MOVED" in ln for ln in lines), d
    assert any(ln.startswith(_row("UNIFORM_6DB")) and "BLIND" in ln for ln in lines)
    assert ("  4/4 controls turned at least one property red; "
            "1/1 blind-by-construction controls stayed blind; 1 NO VERDICT" in lines)
    assert not any("4/5 controls" in ln for ln in lines), "\n".join(lines)
    assert verdict is None


def test_every_control_refusing_is_still_no_verdict(figs, monkeypatch):
    """Rule 8's input for the repair: with nothing evaluated, both `all()`s are
    vacuously true. That is not a pass -- 0/0 is not evidence."""
    fig9, poles, corner = figs
    every = ct.DEFECTS + ct.BLIND_BY_CONSTRUCTION
    fired = _plant(monkeypatch, raises={d: ct.Refused(f"planted: {d}") for d in every})
    verdict, lines = ct.check(poles, corner, fig9)
    assert fired == list(every)
    assert ("  0/0 controls turned at least one property red; "
            f"0/0 blind-by-construction controls stayed blind; {len(every)} NO VERDICT" in lines)
    assert verdict is None


def test_a_refused_blind_control_is_neither_stayed_blind_nor_caught(figs, monkeypatch):
    """On main this refusal went into `caught`: the line read 5/5 controls over
    an all(caught) that was False. It must leave both lists."""
    fig9, poles, corner = figs
    fired = _plant(monkeypatch,
                   raises={"UNIFORM_6DB": ct.Refused("planted: no level to renormalise")})
    verdict, lines = ct.check(poles, corner, fig9)
    assert fired == ["UNIFORM_6DB"]
    assert _row("UNIFORM_6DB") + "REFUSED: planted: no level to renormalise" in lines
    assert ("  5/5 controls turned at least one property red; "
            "0/0 blind-by-construction controls stayed blind; 1 NO VERDICT" in lines)
    assert not any("1/1 blind-by-construction" in ln for ln in lines), "\n".join(lines)
    assert verdict is None


def test_a_measured_failure_outranks_a_refusal(figs, monkeypatch):
    """The injected-bug control for the repair: NO VERDICT must not become the
    way a broken detector reads. One control is evaluated and moves nothing,
    another refuses; the verdict is the measured FAIL."""
    fig9, poles, corner = figs
    fired = _plant(monkeypatch, raises={"SWAP_BANDS": ct.Refused("planted: unrealisable here")},
                   inert=("INERT",))
    verdict, lines = ct.check(poles, corner, fig9)
    assert fired == ["SWAP_BANDS", "INERT"]
    blind = " ".join(f"{'BLIND':>11s}" for _ in ct.PROPERTIES)
    assert _row("INERT") + blind in lines
    assert ("  4/5 controls turned at least one property red; "
            "1/1 blind-by-construction controls stayed blind; 1 NO VERDICT" in lines)
    assert verdict is False


def test_a_pole_the_bank_cannot_hold_is_this_modules_refusal(figs, monkeypatch):
    """The one ValueError the old handler was written for: `cc.real_pole_regs`
    raises rather than clip. An unrealisable control is a refusal, and must
    arrive at `check()` as THIS module's Refused."""
    fig9, poles, corner = figs
    real = cc.real_pole_regs

    def unfit(hz):
        # only the HP_POLE_NOT_LP control asks for the short band's high-pass pole
        if any(f == poles["short"][0] for f in hz):
            raise ValueError("coefficient 2.100000 outside the Q2.24 register")
        return real(hz)

    monkeypatch.setattr(ct.cc, "real_pole_regs", unfit)
    with pytest.raises(ct.Refused, match="outside the Q2.24 register"):
        ct.realisation("short", poles, defect="HP_POLE_NOT_LP")
    verdict, lines = ct.check(poles, corner, fig9)
    reason = _row("HP_POLE_NOT_LP") + "REFUSED: coefficient 2.100000 outside the Q2.24 register"
    assert any(ln.startswith(reason) for ln in lines), "\n".join(lines)
    assert sum("REFUSED" in ln for ln in lines) == 1, "\n".join(lines)
    assert ("  4/4 controls turned at least one property red; "
            "1/1 blind-by-construction controls stayed blind; 1 NO VERDICT" in lines)
    assert verdict is None


def test_an_execution_error_inside_a_control_is_not_relabelled_refused(figs, monkeypatch):
    """Rule 8: the input that would defeat the repair is an implementation
    error raised for one control. With `ValueError` out of the handler it must
    propagate as itself -- never a REFUSED row, never a NO VERDICT."""
    fig9, poles, corner = figs
    fired = _plant(monkeypatch,
                   raises={"SIGN_A1": ValueError("an implementation error, not a precondition")})
    with pytest.raises(ValueError, match="an implementation error"):
        ct.check(poles, corner, fig9)
    assert fired == ["SIGN_A1"]


@pytest.mark.parametrize("verdict, label, status",
                         [(True, "PASS", 0), (False, "FAIL", 1), (None, "NO VERDICT", 2)])
def test_main_and_record_carry_each_verdict_as_itself(figs, tmp_path, capsys, monkeypatch,
                                                      verdict, label, status):
    """`check()`'s boolean readers: None must not fall through a truthiness
    test into FAIL. `pass` stays `true` for PASS only; `verdict` names all three."""
    monkeypatch.setattr(ct, "check", lambda *a: (verdict, ["  (planted)"]))
    out = tmp_path / "rec.json"
    assert ct.main(["--check", "--json", str(out)]) == status
    assert "gate: " + label in capsys.readouterr().out
    gate = json.loads(out.read_text())["gate"]
    assert gate["verdict"] == label and gate["pass"] is (verdict is True), gate


def test_main_exits_2_when_a_control_refuses_and_nothing_else_fails(monkeypatch, capsys):
    """End to end through the real `check()`: red first on main (exit 1)."""
    fired = _plant(monkeypatch, raises={"SWAP_BANDS": ct.Refused("planted: unrealisable here")})
    assert ct.main(["--check"]) == 2
    assert fired == ["SWAP_BANDS"]
    out = capsys.readouterr().out
    assert "REFUSED: planted: unrealisable here" in out and "gate: NO VERDICT" in out, out


def test_main_still_exits_1_when_a_control_moves_nothing_beside_a_refusal(monkeypatch, capsys):
    _plant(monkeypatch, raises={"SWAP_BANDS": ct.Refused("planted: unrealisable here")},
           inert=("INERT",))
    assert ct.main(["--check"]) == 1
    assert "gate: FAIL" in capsys.readouterr().out


# ---- a refusal from the CLEAN run is NO VERDICT, not an uncaught exception ----
#
# #1992. `check()` evaluated the clean run outside any handler and `main()` called
# `check()` outside its own, so a Refused from the clean run escaped both as a
# traceback: the shell saw exit 1, the status reserved for a control that was
# evaluated and moved nothing. #1853's repair handled the control loop and
# deliberately left this path alone. The refusal is planted INSIDE the real
# `properties()`, for the `defect is None` call only, so the class raised is the
# real one and every control would still be evaluable. What is returned is the
# list of controls that reached the real function: on a refused clean run that
# must be empty, because no control may then be reported as evaluated (rule 5,
# conditions 1 and 2).

CLEAN = "clean"
CLEAN_REFUSAL = "REFUSED: planted: the clean run cannot be evaluated"
UNFIT = "REFUSED: coefficient 2.100000 outside the Q2.24 register"


def _plant_clean(monkeypatch, exc):
    """Make the clean run's `properties()` -- the `defect is None` call only --
    raise `exc`; return the list of controls that reached the real function."""
    real_props = ct.properties
    reached = []

    def properties(poles, corner_hz, fig9, *, defect=None):
        if defect is None:
            raise exc
        reached.append(defect)
        return real_props(poles, corner_hz, fig9, defect=defect)

    monkeypatch.setattr(ct, "properties", properties)
    return reached


def _plant_clean_fail(monkeypatch):
    """A clean run that IS evaluated and does not pass: `tone-tilt` goes red."""

    real_props = ct.properties

    def properties(poles, corner_hz, fig9, *, defect=None):
        got = real_props(poles, corner_hz, fig9, defect=defect)
        if defect is None:
            got = dict(got)
            got["tone-tilt"] = (False, "planted: the clean run does not pass")
        return got

    monkeypatch.setattr(ct, "properties", properties)


def _plant_unfit_short_pole(monkeypatch):
    """The real raise site. `cc.real_pole_regs` declines a coefficient that will
    not fit its register; made to do so for the short band's tone pole, so every
    path that realises the CLEAN tone stage -- the gate's clean run, `report()`'s
    tables, `record()`'s bands -- refuses through this module's `Refused`."""
    real = cc.real_pole_regs
    short = cc.TONE_REALISATION["short"]["extra_pole_hz"]

    def unfit(hz):
        if list(hz) == [short]:
            raise ValueError("coefficient 2.100000 outside the Q2.24 register")
        return real(hz)

    monkeypatch.setattr(ct.cc, "real_pole_regs", unfit)


def test_a_refused_clean_run_is_no_verdict_not_an_uncaught_exception(figs, monkeypatch):
    """Red first on the #1876 head: the planted Refused escapes `check()`. After:
    the row names the reason, no property and no control is reported as
    evaluated, and the verdict is None -- not the False of a control that moved
    nothing, and not a pass."""
    fig9, poles, corner = figs
    reached = _plant_clean(monkeypatch, ct.Refused("planted: the clean run cannot be evaluated"))
    verdict, lines = ct.check(poles, corner, fig9)
    assert reached == []
    assert _row(CLEAN) + CLEAN_REFUSAL in lines, "\n".join(lines)
    assert any(ln.startswith("the clean run REFUSED; no control was evaluated") for ln in lines), (
        "\n".join(lines))
    assert verdict is None
    text = "\n".join(lines)
    assert "PASS" not in text and "FAIL" not in text, text


def test_main_exits_2_when_the_clean_run_refuses(figs, monkeypatch, capsys):
    """End to end through the real `check()`: red first on the #1876 head, where
    the Refused leaves `main()` as a traceback and the process as exit 1."""
    reached = _plant_clean(monkeypatch, ct.Refused("planted: the clean run cannot be evaluated"))
    assert ct.main(["--check"]) == 2
    assert reached == []
    out = capsys.readouterr().out
    assert CLEAN_REFUSAL in out, out
    assert "gate: NO VERDICT" in out, out
    assert "gate: PASS" not in out and "gate: FAIL" not in out, out


@pytest.mark.parametrize("argv", [["--json"], ["--check", "--json"]])
def test_main_json_records_a_refused_clean_run_as_no_verdict(figs, tmp_path, capsys, monkeypatch,
                                                            argv):
    """`record()` reaches the gate through `check()`: the evidence record carries
    the refusal as its verdict -- `pass` false, `verdict` NO VERDICT, the row with
    its reason -- and the exit status is 2, not the 1 a traceback would give."""
    _plant_clean(monkeypatch, ct.Refused("planted: the clean run cannot be evaluated"))
    out = tmp_path / "rec.json"
    assert ct.main(argv + [str(out)]) == 2
    gate = json.loads(out.read_text())["gate"]
    assert gate["verdict"] == "NO VERDICT" and gate["pass"] is False, gate
    assert _row(CLEAN) + CLEAN_REFUSAL in gate["lines"]
    assert f"wrote {out}" in capsys.readouterr().out


def test_a_clean_run_that_fails_without_refusing_is_still_fail(figs, monkeypatch, capsys):
    """Rule 8's input for the guard: a clean run that IS evaluated and does not
    pass is a measured FAIL (exit 1). The refusal handler must not turn every
    non-passing clean run into NO VERDICT."""
    fig9, poles, corner = figs
    _plant_clean_fail(monkeypatch)
    verdict, lines = ct.check(poles, corner, fig9)
    assert verdict is False
    assert any(ln.startswith("the clean run does not pass; the controls below mean nothing")
               for ln in lines), "\n".join(lines)
    assert ct.main(["--check"]) == 1
    out = capsys.readouterr().out
    assert "gate: FAIL" in out and "NO VERDICT" not in out, out


def test_an_execution_error_in_the_clean_run_is_not_relabelled_refused(figs, monkeypatch):
    """Rule 8, as for a control (#1853): the handler is for refusals. An
    implementation error raised by the clean run must propagate as itself --
    never a REFUSED row, never a NO VERDICT."""
    fig9, poles, corner = figs
    _plant_clean(monkeypatch, ValueError("an implementation error, not a precondition"))
    with pytest.raises(ValueError, match="an implementation error"):
        ct.check(poles, corner, fig9)


def test_a_sibling_modules_refusal_in_the_clean_run_is_still_no_verdict(figs, monkeypatch, capsys):
    """Rule 8's other input, #1160's shape: each module's `Refused` subclasses
    `refusal.Refused` and none subclasses another's, so a handler written against
    THIS module's class catches none of a sibling's. `properties()` calls into
    `werner_fig9`; a refusal raised there must read as refused, not escape as a
    traceback because the guard named the wrong class."""
    assert not issubclass(w9.Refused, ct.Refused)
    _plant_clean(monkeypatch, w9.Refused("planted: Figure 9 cannot be read"))
    assert ct.main(["--check"]) == 2
    out = capsys.readouterr().out
    assert "REFUSED: planted: Figure 9 cannot be read" in out and "gate: NO VERDICT" in out, out


@pytest.mark.parametrize("argv", [[], ["--report"], ["--report", "--check"], ["--json"]])
def test_a_refusal_outside_the_gate_reaches_the_caller_as_refused(figs, tmp_path, capsys,
                                                                  monkeypatch, argv):
    """`report()`'s tables and `record()`'s bands call `realisation()` outside
    `check()`, and no arguments means `--report`. Red first on the #1876 head
    (traceback, exit 1); after, the same `REFUSED:` line and exit 2 that `main()`
    already gives an artifact it cannot read. No record is written."""
    _plant_unfit_short_pole(monkeypatch)
    rec = tmp_path / "rec.json"
    assert ct.main(argv + ([str(rec)] if "--json" in argv else [])) == 2
    out = capsys.readouterr().out
    assert UNFIT in out, out
    assert "gate: PASS" not in out and "gate: FAIL" not in out, out
    assert not rec.exists()


def test_a_measured_fail_outranks_a_refusal_beside_it(figs, tmp_path, capsys, monkeypatch):
    """Rule 8's input for the exit status: a FAIL that was measured must not be
    masked by a refusal beside it. The clean run fails; then the record's bands
    refuse (exit 1, no record written), and the report refuses beside the gate
    (the gate still prints, still 1). Both raises are planted at the `record()`
    and `report()` boundaries, because no input refuses either without also
    refusing the clean run -- that raise site belongs to
    `test_a_refusal_outside_the_gate_reaches_the_caller_as_refused`."""
    _plant_clean_fail(monkeypatch)

    def record(poles, corner, fig9):
        raise ct.Refused("planted: the bands cannot be built")

    monkeypatch.setattr(ct, "record", record)
    rec = tmp_path / "rec.json"
    assert ct.main(["--check", "--json", str(rec)]) == 1
    assert not rec.exists()
    out = capsys.readouterr().out
    assert "gate: FAIL" in out, out
    assert "REFUSED: planted: the bands cannot be built" in out, out

    def report(poles, corner):
        raise ct.Refused("planted: the tables cannot be built")

    monkeypatch.setattr(ct, "report", report)
    assert ct.main(["--report", "--check"]) == 1
    out = capsys.readouterr().out
    assert "REFUSED: planted: the tables cannot be built" in out, out
    assert "gate: FAIL" in out, out
