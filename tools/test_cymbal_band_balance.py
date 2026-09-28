"""Known answers and controls for tools/cymbal_band_balance.py (#369/#396).

Four kinds of test, and the third and fourth are the ones that matter:

  * closed-form known answers -- a 2-pole band-pass is 0 dB at its own f0, a
    single RC pole is 3.01 dB down at its corner, a 2-pole high-pass reaches its
    stated pass band far above its corner;
  * cross-checks against numbers this tool did not produce -- Figure 9's own
    digitised curve (not its 2-pole fit), `werner_fig9.tilt_table`, and
    `model/cymbal_candidate.TONE_K1[*]["peak_db"]`;
  * the REFUSAL, asserted in both directions -- it must fire on this tree and it
    must LIFT on a complete precondition set, or it is an opinion compiled into
    a function rather than an assertion (CLAUDE.md: run a gate against the
    current state before committing it);
  * controls that must fail, as the properties x defects matrix, plus one
    control asserted BLIND by construction -- a gain common to all three bands
    is a level, not a balance, and a suite that reported it would be reading the
    wrong quantity.

And one binding test: the committed ablation record's own numbers must be the
ones this tool computes, so the scorecard's verdict cannot drift from the
instrument that produced it.
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
import cymbal_band_balance as bb               # noqa: E402
import cymbal_tone_realisation as ct           # noqa: E402
import tone_stage_schematic as ts              # noqa: E402
import werner_fig9 as w9                       # noqa: E402

ABLATION = bb.SCORECARD / "balance" / "balance-ablation.json"


@pytest.fixture(scope="module")
def figs():
    return w9.from_artifact()[0], ct.level_corner_hz()


# ---- closed-form known answers --------------------------------------------


def test_the_bandpass_is_unity_at_its_own_centre():
    """`bandpass_db` carries Figure 4's recorded PEAK, so it must be normalised
    at the peak. One dB of error here is one dB of balance error in every band."""
    for f0 in (3450.0, 7100.0, 274.4):
        assert float(bb.bandpass_db(np.array([f0]), f0, 6.0)[0]) == pytest.approx(0.0, abs=1e-9)


def test_the_bandpass_falls_6_db_per_octave_far_from_its_centre():
    f0 = 3450.0
    d = bb.bandpass_db(np.array([f0 * 16, f0 * 32]), f0, 6.0)
    assert float(d[1] - d[0]) == pytest.approx(-6.02, abs=0.1)


def test_the_2_pole_highpass_reaches_its_stated_pass_band():
    """`highpass2_db` carries Figure 4's recorded PASS BAND, so it must be
    normalised where f >> f0, not at the corner."""
    f0, q = 2500.0, 0.97
    assert float(bb.highpass2_db(np.array([f0 * 1000]), f0, q)[0]) == pytest.approx(0.0, abs=1e-3)
    # ...and at the corner a Q 0.97 section is NOT 3 dB down; that is the point
    # of keeping the two normalisations separate.
    assert abs(float(bb.highpass2_db(np.array([f0]), f0, q)[0])) > 0.1


def test_a_single_rc_pole_is_3_01_db_down_at_its_corner():
    f = bb.cc.LEVEL_CORNER_HZ
    d = bb.highpass1_db(np.array([f, f * 1000]), f)
    assert float(d[0] - d[1]) == pytest.approx(-3.0103, abs=0.01)


def test_the_chain_at_its_own_peak_is_the_recorded_constants():
    """The gate's `chain-normalisation` property as a standalone assertion: at
    the band-pass peak the chain contributes exactly BP peak + HP pass + the
    high-pass's own shape there, and nothing else."""
    for band in bb.BANDS:
        f = np.array([bb.BP_HZ[band]])
        got = float(bb.band_filter_db(band, f)[0])
        hp_pass = {"low": cc.HH1_PASS_DB, "decay": cc.HH2_PASS_DB,
                   "short": cc.HH3_PASS_DB}[band]
        if band == "low":
            shape = float(bb.highpass2_db(f, cc.HH1_HZ, cc.HH1_Q)[0])
        elif band == "decay":
            shape = float(bb.highpass2_db(f, cc.HH2_HZ, cc.HH2_Q)[0])
        else:
            shape = float(bb.highpass2_db(f, cc.HH3_HZ, cc.HH3_Q)[0]
                          + bb.highpass1_db(f, cc.HH3_P1_HZ)[0])
        want = cc.BP_PEAK_DB[bb.BP_KEY[band]] + hp_pass + shape
        assert got == pytest.approx(want, abs=bb.CHAIN_TOL_DB), band


# ---- cross-checks against numbers this tool did not produce ---------------


def test_the_tone_term_matches_figure_9s_own_digitised_curve(figs):
    """Not the 2-pole fit -- the DIGITISED POINTS. Ht3 is plotted across the
    whole band, so its value at the short band's 10079 Hz calibration third is
    read off the figure and owes nothing to this tool's model of it.

    Both routes are checked against it, which is the point: the short band is
    the ONE place where the figure and the solved network can be compared at
    the frequency the balance actually uses, and they must agree there."""
    fig9, _ = figs
    hz, db, _ = bb.tone_fit("short", fig9)
    f = bb.CENTRE_HZ["short"]
    assert hz.min() <= f <= hz.max()
    truth = float(np.interp(math.log10(f), np.log10(hz), db))
    ours = float(bb.tone_db("short", np.array([f]), fig9)[0])
    assert ours == pytest.approx(truth, abs=0.2), (ours, truth)
    nodal = bb.schematic_tone_term("short")["db"]
    assert nodal == pytest.approx(truth, abs=0.2), (nodal, truth)


def test_the_tone_terms_shape_reproduces_werner_fig9s_tilt_table(figs):
    """`tone_db` keeps the absolute gain where `tilt_table` normalises it away;
    normalised at 1 kHz the two must be the same curve."""
    fig9, _ = figs
    tilt = w9.tilt_table(fig9)
    for band, name in bb.BAND_OF.items():
        ours = bb.tone_db(band, ct.THIRDS, fig9)
        ours = ours - float(np.interp(1000.0, ct.THIRDS, ours))
        theirs = np.array([v for _, v, _ in tilt[name]])
        assert np.max(np.abs(ours - theirs)) < ct.TILT_TOL_DB, band


def test_the_models_recorded_peak_db_literals_are_the_artifacts(figs):
    """`model/cymbal_candidate.TONE_K1[*]["peak_db"]` are the values #396 asked
    to apply. Whatever is decided about applying them, they must be the
    artifact's -- a literal that drifts from its evidence is failure-modes.md's
    "claims outliving their evidence"."""
    fig9, _ = figs
    for band, name in bb.BAND_OF.items():
        _, _, fit = bb.tone_fit(band, fig9)
        assert cc.TONE_K1[band]["peak_db"] == pytest.approx(fit["gain_db"], abs=0.01), name


def test_the_models_recorded_bounds_and_gap_are_the_tools(figs):
    """`TONE_BOUND_AT_CENTRE_DB`, `FIGURE_TONE_BOUND_AT_CENTRE_DB` and
    `BALANCE_GAP_DB` are the numbers the model's docstring argues from. A
    literal that drifts from the tool that produced it is failure-modes.md's
    "claims outliving their evidence"."""
    fig9, corner = figs
    for band in bb.BANDS:
        got = bb.tone_term(band, fig9)["width_db"]
        assert got == pytest.approx(cc.TONE_BOUND_AT_CENTRE_DB[band], abs=1e-3), band
        fig = bb.figure_tone_term(band, fig9)["width_db"]
        assert fig == pytest.approx(cc.FIGURE_TONE_BOUND_AT_CENTRE_DB[band], abs=0.01), band
    g = bb.gap_db(fig9, corner)
    for band in bb.BANDS:
        assert g[band]["gap_db"] == pytest.approx(cc.BALANCE_GAP_DB[band], abs=0.01), band


# ---- the tone term is #390's nodal solution, pinned to it (#420) ----------


def test_the_tone_term_is_tone_stage_schematics_own_nodal_solution(figs):
    """THE binding test for #420. Every `tone` value the balance uses must be
    `tone_stage_schematic.db_at` at the fitted wiper fraction, at the frequency
    that band's level is set at -- not a transcription of it, and not a number
    that can drift the way #410's four prose figures did.

    Exact equality to 1e-9 dB, not a tolerance: one side is read from the
    committed record and the other is recomputed from the module that wrote it,
    so any difference at all means the record has gone stale.
    """
    fig9, _ = figs
    for band, name in ct.BAND_OF.items():
        f = bb.CENTRE_HZ[band]
        want = float(ts.db_at(np.array([f]), ts.ALPHA_K1, name)[0])
        got = bb.tone_term(band, fig9)
        assert got["db"] == pytest.approx(want, abs=1e-9), (band, name)
        assert got["hz"] == pytest.approx(f, abs=1e-9), band
        assert got["family"] == name
        assert got["route"] == bb.ROUTE_SCHEMATIC


def test_the_committed_schematic_artifact_is_the_modules_own_solution(figs):
    """`sn-p13-vr4.json` is the `schematic-vr4` precondition, so it must be the
    nodal solution rather than a snapshot of one. Re-emitted from the module
    and compared field by field: an artifact that could drift from its emitter
    would be a precondition satisfied by a stale file."""
    fig9, _ = figs
    assert bb.SCHEMATIC_ARTIFACT.exists()
    committed = json.loads(bb.SCHEMATIC_ARTIFACT.read_text())
    fresh = ts.balance_record(fig9)
    assert committed["alpha_k1"] == pytest.approx(ts.ALPHA_K1, abs=1e-12)
    assert committed["source"]["sha256"] == ts.SN_PDF_SHA256
    for band, name in ct.BAND_OF.items():
        assert committed["bands"][band]["family"] == name
        for key, entry in fresh["bands"][band]["at_hz"].items():
            got = committed["bands"][band]["at_hz"][key]
            for field in ("db", "bound_db", "lo_db", "hi_db", "residual_db"):
                assert got[field] == pytest.approx(entry[field], abs=1e-9), (band, key, field)


def test_the_tone_bound_collapsed_to_the_solutions_own_residual(figs):
    """#420's second deliverable. The `tone ±` column was 7.4 / 13.9 / 0.04 dB
    of Figure 9 extrapolation spread; resolving the network replaces it with
    that solution's largest disagreement with Figure 9's digitised curves plus
    3 sigma on its one fitted parameter. It must COLLAPSE -- and it must not
    collapse to zero, which would mean the bound had been dropped rather than
    re-derived."""
    fig9, _ = figs
    stats = ts.residual_stats(fig9)
    _, _, sigma, _ = ts.fit_alpha_k1_with_sigma(fig9)
    for band, name in ct.BAND_OF.items():
        t = bb.tone_term(band, fig9)
        want = ts.resolved_bound_db(bb.CENTRE_HZ[band], name,
                                    stats=stats, sigma_alpha=sigma)
        assert t["width_db"] == pytest.approx(2.0 * want["bound_db"], abs=1e-9), band
        assert 0.0 < t["width_db"] < 0.1, band
        assert t["width_db"] < bb.figure_tone_term(band, fig9)["width_db"] \
            or band == "short", band
    # ...and the whole point of the collapse: the propagated balance is now
    # inside the tolerance that the figure route is outside of.
    rel = bb.relative_db(fig9, ct.level_corner_hz())
    assert all(v["width_db"] < bb.BALANCE_BOUND_DB for v in rel.values()), rel


def test_the_short_bands_bound_is_the_one_place_both_routes_can_be_compared(figs):
    """Stated rather than smoothed over. 10079 Hz is inside Ht3's plotted
    window, so the short band is the only band where Figure 9's bound and the
    nodal solution meet at the frequency the balance uses -- and the nodal
    value lands just OUTSIDE Figure 9's 0.041 dB-wide bound there.

    That is a bound marginally too tight, not a disagreement between methods:
    the excursion is 0.02 dB, smaller than the solution's own residual against
    the same digitised curve, and three orders of magnitude below the
    applicability bound. Asserted with both a floor and a ceiling so it cannot
    grow silently in either direction."""
    fig9, _ = figs
    fig = bb.figure_tone_term("short", fig9)
    nodal = bb.schematic_tone_term("short")
    assert fig["measured"] is True and nodal["measured"] is True
    assert fig["width_db"] < 0.05
    outside = max(fig["lo_db"] - nodal["db"], nodal["db"] - fig["hi_db"])
    assert 0.0 < outside < 0.05, (outside, fig, nodal)
    assert outside < nodal["residual_db"] + nodal["alpha_db"]


def test_the_schematic_tone_term_refuses_a_frequency_it_never_solved(figs):
    """The record answers at the frequencies it was emitted for and REFUSES
    elsewhere rather than interpolating. A tool that answers where it cannot is
    worse than one that is absent, because its output looks like data."""
    with pytest.raises(bb.Refused) as e:
        bb.schematic_tone_term("low", at_hz=1234.0)
    assert "1234.0" in str(e.value)


def test_the_schematic_record_refuses_when_absent_or_malformed(tmp_path):
    with pytest.raises(bb.Refused) as e:
        bb.schematic_record(tmp_path / "nope.json")
    assert "--emit" in str(e.value)
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"artifact": "something-else"}))
    with pytest.raises(bb.Refused):
        bb.schematic_record(bad)


# ---- the bound, evaluated where the levels are actually set ---------------


def test_the_low_bands_bound_is_tighter_at_its_own_centre_than_at_7100(figs):
    """The headline correction. #396 and reference §18 quote 18 dB for Ht1; that
    is the bound at 7.1 kHz, and the low band is levelled at 3175 Hz. It is a
    statement about FIGURE 9's window, so it stays on the figure route after
    #420 moved the balance itself onto the solved network."""
    fig9, _ = figs
    here = bb.figure_tone_term("low", fig9)["width_db"]
    there = bb.figure_tone_term("low", fig9, at_hz=7100.0)["width_db"]
    assert here < there
    assert there > 15.0        # the figure #396 quotes is reproduced...
    assert here < 10.0         # ...and is not the number that applies


def test_the_short_bands_figure_tone_term_is_measured_not_extrapolated(figs):
    fig9, _ = figs
    t = bb.figure_tone_term("short", fig9)
    assert t["measured"] is True
    assert t["width_db"] < 1.0
    for band in ("low", "decay"):
        assert bb.figure_tone_term(band, fig9)["measured"] is False


def test_the_figure_route_is_wide_and_the_schematic_route_is_not(figs):
    """The gate's `tone-bound` property as a standalone assertion, in both
    halves. If the first ever became false, #396's exclusion of the figure
    route would be unearned; if the second ever became false, #390's solution
    would not have resolved the term #420 says it resolved."""
    fig9, corner = figs
    fig = bb.relative_db(fig9, corner, route=bb.ROUTE_FIGURE)
    nodal = bb.relative_db(fig9, corner, route=bb.ROUTE_SCHEMATIC)
    assert any(v["width_db"] > bb.BALANCE_BOUND_DB for v in fig.values()), fig
    assert all(v["width_db"] <= bb.BALANCE_BOUND_DB for v in nodal.values()), nodal


# ---- the refusal, in both directions -------------------------------------


def test_the_vca_drive_precondition_refuses_on_this_tree_and_schematic_vr4_no_longer_does(figs):
    """The refusal, asserted in the direction #420 changed. `schematic-vr4` was
    absent when #396's step was written and #390/#417 supplied it, so the
    refusal must now name `vca-drive` and NOT `schematic-vr4` -- a refusal that
    went on naming a resolved input would be reporting a state of the
    repository that stopped being true."""
    fig9, corner = figs
    assert bb.SCHEMATIC_ARTIFACT.exists()
    assert not bb.VCA_ARTIFACT.exists()
    by_name = {p["name"]: p for p in bb.preconditions()}
    assert by_name["schematic-vr4"]["present"] is True
    assert by_name["vca-drive"]["present"] is False
    with pytest.raises(bb.Refused) as e:
        bb.balance_gains(fig9, corner)
    assert "vca-drive" in str(e.value)
    assert "schematic-vr4" not in str(e.value)


def test_the_refusal_is_not_vacuous(figs):
    """Hand it a complete precondition set and it ANSWERS. An unsatisfiable
    gate trains everyone to ignore gates, including the ones that work."""
    fig9, corner = figs
    got = bb.balance_gains(fig9, corner,
                           present={p["name"]: True for p in bb.preconditions()})
    assert set(got) == set(bb.BANDS)
    assert got["low"] == pytest.approx(0.0, abs=1e-9)
    assert got["short"] > got["decay"] > 0.0


def test_each_missing_precondition_refuses_on_its_own(figs):
    fig9, corner = figs
    names = [p["name"] for p in bb.preconditions()]
    for missing in names:
        present = {n: n != missing for n in names}
        with pytest.raises(bb.Refused) as e:
            bb.balance_gains(fig9, corner, present=present)
        assert missing in str(e.value), missing


def test_the_excluded_figure_route_refuses_on_its_measured_bound(figs):
    """#396 excludes further figure-fitting. That exclusion is a measurement
    here, not a decree: the propagated bound does not fit the 3.0 dB tolerance."""
    fig9, corner = figs
    with pytest.raises(bb.Refused) as e:
        bb.figure_route_gains(fig9, corner)
    assert str(bb.BALANCE_BOUND_DB) in str(e.value)


def test_an_absent_or_malformed_level_record_refuses(tmp_path):
    with pytest.raises(bb.Refused):
        bb.shipped_rule_levels(tmp_path / "nope.json")
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"levels": {"per_band": {"low": {}}}}))
    with pytest.raises(bb.Refused):
        bb.shipped_rule_levels(bad)


# ---- the gap, and the rule it is measured against ------------------------


def test_the_shipped_rule_relative_levels_bind_to_the_registers_it_wrote():
    """candidate3.json records each band's energies AND the amp / envelope
    registers the rule wrote to reach them. Two records of one level, so each
    must reproduce the other -- otherwise the gap is arithmetic about a level
    rule nothing implements."""
    lv = bb.shipped_rule_levels()
    for band, v in lv.items():
        want = math.sqrt(v["shipped_abs"] / v["unit_abs"]) * bb.UNIT_AMP
        got = v["amp"] * v["env_gain"]
        assert 20 * math.log10(got / want) == pytest.approx(0.0, abs=bb.RULE_BIND_TOL), band


def test_the_gap_is_larger_than_the_figure_bound_it_was_blamed_on(figs):
    """The finding. If the 9-18 dB figure uncertainty were what blocks the
    balance, the gap would be of that size. It is 39.8 dB in the short band,
    against the widest bound the FIGURE route ever offered (21.3 dB) -- the
    comparison is made against that one, not against the resolved bound, because
    "39.8 exceeds 0.07" asserts nothing."""
    fig9, corner = figs
    g = bb.gap_db(fig9, corner)
    figg = bb.gap_db(fig9, corner, route=bb.ROUTE_FIGURE)
    assert g["low"]["gap_db"] == pytest.approx(0.0, abs=1e-9)
    assert max(abs(v["gap_db"]) for v in g.values()) \
        > max(v["bound_db"] for v in figg.values())
    assert g["short"]["gap_db"] > 30.0


def test_resolving_the_tone_term_made_the_gaps_bigger_not_smaller(figs):
    """#420's item 3, as an assertion rather than a sentence. If VR4's network
    had been the missing explanation for the 38 dB residual, resolving it would
    have shrunk the gaps. It grew them: +0.28 dB (decay) and +1.56 dB (short).
    The refusal's rationale therefore survives its own re-derivation, and the
    direction is the one that would have falsified it."""
    fig9, corner = figs
    g = bb.gap_db(fig9, corner)
    figg = bb.gap_db(fig9, corner, route=bb.ROUTE_FIGURE)
    moved = {b: g[b]["gap_db"] - figg[b]["gap_db"] for b in bb.BANDS}
    assert moved["low"] == pytest.approx(0.0, abs=1e-9)
    assert moved["decay"] == pytest.approx(0.28, abs=0.02), moved
    assert moved["short"] == pytest.approx(1.56, abs=0.02), moved


# ---- the ablation's level solver ------------------------------------------


def _fake_cal():
    lv = bb.shipped_rule_levels()
    return {"per_band": {b: dict(v) for b, v in lv.items()}, "amps": {}}


def test_the_ablation_imposes_exactly_the_gaps_and_nothing_else(figs):
    """`rebalance` may move the absolute level (the anchor is not the claim) but
    every band-to-band RATIO must move by exactly its gap."""
    fig9, corner = figs
    cal = _fake_cal()
    out = bb.rebalance(cal, fig9, corner)
    gaps = bb.gap_db(fig9, corner)
    base = {b: math.sqrt(cal["per_band"][b]["shipped_abs"] / cal["per_band"][b]["unit_abs"])
            for b in bb.BANDS}
    new = {b: out["per_band"][b]["amp"] * out["per_band"][b]["env_gain"] / bb.UNIT_AMP
           for b in bb.BANDS}
    for b in bb.BANDS:
        moved = 20 * math.log10((new[b] / new["low"]) / (base[b] / base["low"]))
        assert moved == pytest.approx(gaps[b]["gap_db"], abs=0.05), b


def test_the_ablation_keeps_every_band_inside_its_registers(figs):
    fig9, corner = figs
    out = bb.rebalance(_fake_cal(), fig9, corner)
    for b, v in out["per_band"].items():
        assert v["amp"] <= bb.AMP_MAX + 1e-9, b
        assert bb._env_base_peak(b) * v["env_gain"] <= bb.ENV_PEAK_MAX + 1e-9, b
    # the anchor is the LARGEST common scale that fits, so at least one band
    # must be hard against a ceiling -- otherwise the solver left level unused.
    tight = [b for b, v in out["per_band"].items()
             if bb._env_base_peak(b) * v["env_gain"] > bb.ENV_PEAK_MAX - 1e-6
             or v["amp"] > bb.AMP_MAX - 1e-9]
    assert tight, out["per_band"]


def test_the_ablation_refuses_a_target_it_cannot_reach(figs):
    """A level the registers cannot hold must refuse, not clip: a silently
    clamped band is a different balance."""
    fig9, corner = figs
    cal = _fake_cal()
    cal["per_band"]["short"]["unit_abs"] = 1e-30       # demands ~300 dB of gain
    saved = bb.ENV_PEAK_MAX
    try:
        bb.ENV_PEAK_MAX = 1e-12                       # every band now over the top
        with pytest.raises(bb.Refused):
            bb.rebalance(cal, fig9, corner)
    finally:
        bb.ENV_PEAK_MAX = saved


# ---- the committed ablation record binds to this tool --------------------


@pytest.mark.skipif(not ABLATION.exists(), reason="ablation record not present")
def test_the_committed_ablation_used_the_gaps_this_tool_computes(figs):
    fig9, corner = figs
    blob = json.loads(ABLATION.read_text())
    gaps = bb.gap_db(fig9, corner)
    for b, v in blob["levels"]["ablation"]["gap_db"].items():
        assert v == pytest.approx(gaps[b]["gap_db"], abs=0.01), b
    assert blob["variant"] == "balance"
    assert blob["sources_dirty"] is False


# `docs/scorecard/cymbal-369/balance/README.md` §4, as (tilt, worst, n>6) per
# window. A table in a document is not a fact: these are re-derived from the
# record on every run, so a scorecard number that drifts from its render breaks
# the suite instead of quietly outliving its evidence.
ABLATION_SUMMARY = {"0-50ms": (8.4, 17.1, 8),
                    "50-300ms": (19.6, 17.0, 9),
                    "300-1000ms": (21.6, 14.8, 10)}


@pytest.mark.skipif(not ABLATION.exists(), reason="ablation record not present")
def test_the_scorecard_summary_table_is_the_records_own_numbers():
    blob = json.loads(ABLATION.read_text())
    for window, (tilt, worst, n) in ABLATION_SUMMARY.items():
        r = blob["thirds"][window]["candidate_minus_808"]
        keys = list(r)
        assert r[keys[-1]] - r[keys[0]] == pytest.approx(tilt, abs=0.05), window
        assert max(abs(v) for v in r.values()) == pytest.approx(worst, abs=0.05), window
        assert sum(1 for v in r.values() if abs(v) > 6.0) == n, window
    assert blob["bands"]["candidate"]["H_minus_L_db"] == pytest.approx(24.38, abs=0.01)
    assert blob["bands"]["candidate"]["H"]["edt10_ms"] == pytest.approx(69.9, abs=0.1)
    assert blob["levels"]["ablation"]["common_scale_db"] == pytest.approx(-33.82, abs=0.01)


@pytest.mark.skipif(not ABLATION.exists(), reason="ablation record not present")
def test_the_ablation_refutes_the_equal_vca_drive_assumption():
    """The render's verdict, bound to the record rather than to the prose: with
    every resolved factor applied and the three VCA drives held equal, the band
    split lands FURTHER from the 808 than the shipped-kit rule it replaced."""
    blob = json.loads(ABLATION.read_text())
    ref = blob["bands"]["fischer_CY5025"]["H_minus_L_db"]
    ours = blob["bands"]["candidate"]["H_minus_L_db"]
    shipped = blob["bands"]["shipped"]["H_minus_L_db"]
    assert abs(ours - ref) > abs(shipped - ref) + 10.0, (ours, shipped, ref)
    assert all(blob["preservation"].values())


# ---- controls that must fail ---------------------------------------------


EXPECTED = {
    #  defect                 the properties it MUST move
    "SWAP_PEAKS":            {"chain-normalisation"},
    "NO_TONE_TERM":          {"tone-bound", "centre-bound-tighter", "refusal-live"},
    "TONE_AT_7100_FOR_ALL":  {"centre-bound-tighter"},
    "SHIPPED_RULE_IS_FLAT":  {"rule-bind"},
    "PRECOND_ALWAYS_OK":     {"refusal-live"},
}


@pytest.mark.parametrize("defect", sorted(EXPECTED))
def test_each_control_moves_exactly_the_properties_it_should(defect, figs):
    fig9, corner = figs
    got = bb.properties(fig9, corner, defect=defect)
    moved = {p for p, (ok, _) in got.items() if not ok}
    assert moved == EXPECTED[defect], (defect, moved, EXPECTED[defect])


def test_a_gain_common_to_all_three_bands_is_blind_everywhere(figs):
    """A balance is a ratio. A tool that moved on a common gain would be
    reporting a level in the place a ratio belongs."""
    fig9, corner = figs
    got = bb.properties(fig9, corner, defect="UNIFORM_6DB_ALL_BANDS")
    assert all(ok for ok, _ in got.values()), got


def test_the_clean_gate_passes_and_every_control_is_caught(figs):
    fig9, corner = figs
    ok, lines = bb.check(fig9, corner)
    assert ok, "\n".join(lines)
    assert any("5/5 controls" in ln for ln in lines), "\n".join(lines)
    assert any("1/1 blind-by-construction" in ln for ln in lines), "\n".join(lines)
