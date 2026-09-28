"""Known answers and controls for tools/cymbal_tone_render.py and revision 4 (#369 step 10).

Nothing here renders: a render is minutes and these are the assertions that must
hold before one is worth starting. The render's own evidence record is
`docs/scorecard/cymbal-369/tone-render/tone-render.json`.

The verdict function gets its own controls because step 8's wrong-then-right 4
was "a verdict function that reported a match from two rows twelve dB apart".
"""
from __future__ import annotations

import math
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "model"), str(ROOT / "tools")]
import drums_fx as dx                    # noqa: E402
import cymbal_bands as cb                # noqa: E402
import cymbal_candidate as cc            # noqa: E402
import cymbal_tone_nodal as tn           # noqa: E402
import cymbal_tone_render as tre         # noqa: E402


# ---------------------------------------------------------------------------
# revision 4 is additive: revision 3 must stay reproducible
# ---------------------------------------------------------------------------
def test_revision_3_is_unchanged_register_for_register():
    """Steps 5-9's artifacts are re-derived from `candidate_kit()` with no TONE
    argument. If revision 4 moved a single one of its registers, every one of
    those records would silently stop reproducing."""
    amps = {cc.M_CYH1: 0.25, dx.M_CYHI: 0.25, cc.M_CYH3B: 0.25}
    r3 = dict(cc.candidate_kit(amps))
    assert dx.A_MODE + cc.M_CYH1B * dx.MODE_STRIDE not in r3
    assert dx.A_PATH + cc.P_CYH1B not in r3
    # the short band's tone pole is still Figure 9's in revision 3
    a1, a2 = cc.real_pole_regs([cc.HH3_P1_HZ, cc.TONE_REALISATION["short"]["extra_pole_hz"]])
    base = dx.A_MODE + cc.M_CYH3B * dx.MODE_STRIDE
    assert r3[base] == a1 & cc.COEF_MASK and r3[base + 1] == a2 & cc.COEF_MASK
    # and the low band's level is still on M_CYH1
    assert r3[dx.A_MODE + cc.M_CYH1 * dx.MODE_STRIDE + 2] == dx.amp_reg(0.25)


def test_revision_4_differs_from_revision_3_in_exactly_seven_registers():
    """One new path, three new mode registers, the short band's two coefficients
    and the low band's amp moving off M_CYH1. Nothing else."""
    r3 = dict(cc.candidate_kit({cc.M_CYH1: 0.25, dx.M_CYHI: 0.25, cc.M_CYH3B: 0.25}))
    r4 = dict(cc.candidate_kit({cc.M_CYH1B: 0.25, dx.M_CYHI: 0.25, cc.M_CYH3B: 0.25},
                               tone="50"))
    diff = sorted(a for a in set(r3) | set(r4) if r3.get(a) != r4.get(a))
    expect = sorted([dx.A_PATH + cc.P_CYH1B,
                     dx.A_MODE + cc.M_CYH1 * dx.MODE_STRIDE + 2,
                     dx.A_MODE + cc.M_CYH3B * dx.MODE_STRIDE,
                     dx.A_MODE + cc.M_CYH3B * dx.MODE_STRIDE + 1,
                     dx.A_MODE + cc.M_CYH1B * dx.MODE_STRIDE,
                     dx.A_MODE + cc.M_CYH1B * dx.MODE_STRIDE + 1,
                     dx.A_MODE + cc.M_CYH1B * dx.MODE_STRIDE + 2])
    assert diff == expect, [f"0x{a:02X}" for a in diff]


def test_revision_4_never_writes_the_reset_address():
    """Mode 19's `num` register IS 0xFF = A_RESET. Writing it would reset the
    block mid-strike, and `DrumsFx.write` decodes A_RESET before the mode range,
    so this is not theoretical."""
    for code in tn.CODES:
        img = dict(cc.candidate_kit({}, tone=code))
        assert dx.A_RESET not in img, code


def test_an_unknown_tone_code_raises_before_anything_is_written():
    with pytest.raises(ValueError):
        cc.candidate_kit({}, tone="33")
    with pytest.raises(ValueError):
        cc.tone_gains("33")


def test_the_tone_code_selects_no_coefficients():
    """One fixed register set covers the whole knob: the images at the five TONE
    positions must be identical when the levels are held fixed, because TONE is
    realised entirely as those levels."""
    imgs = {c: dict(cc.candidate_kit({cc.M_CYH1B: 0.25, dx.M_CYHI: 0.25,
                                      cc.M_CYH3B: 0.25}, tone=c)) for c in tn.CODES}
    ref = imgs["50"]
    for c, img in imgs.items():
        assert img == ref, c


# ---------------------------------------------------------------------------
# the model and the tool must not be able to drift apart
# ---------------------------------------------------------------------------
def test_the_models_tone_gain_table_is_the_tools():
    tool = tn.tone_gain_db()
    for code in tn.CODES:
        for band in tn.BANDS:
            assert cc.TONE_GAIN_DB[code][band] == pytest.approx(tool[code][band], abs=1e-3), \
                (code, band)


def test_the_models_realisation_is_the_validated_one():
    chosen = tn.chosen()
    for band, v in chosen.items():
        want = cc.TONE_R4[band]["pole_hz"]
        got = v["poles"][0] if v["poles"] else None
        assert (want is None) == (got is None), band
        if want is not None:
            assert want == pytest.approx(got, abs=1e-6), band


def test_the_budget_the_model_declares_is_the_budget_the_tool_counts():
    bud = tn.budget()
    assert (cc.N_MODES_R4, cc.N_PATH_R4) == (bud["modes"], bud["paths"])
    assert cc.M_CYH1B == bud["last_mode"]


def test_the_precondition_gate_passes_on_the_current_tree():
    """`assert_realisation_is_the_validated_one` is what stops a render of a
    realisation nobody validated. Run it against the tree, not just describe it."""
    got = tre.assert_realisation_is_the_validated_one()
    assert got["check_ok"]
    assert got["budget"]["addressable"]


def test_the_precondition_gate_refuses_a_model_that_drifted(monkeypatch):
    monkeypatch.setitem(cc.TONE_R4["low"], "pole_hz", 1234.0)
    with pytest.raises(tre.Refused):
        tre.assert_realisation_is_the_validated_one()


def test_the_precondition_gate_refuses_a_gain_table_that_drifted(monkeypatch):
    monkeypatch.setitem(cc.TONE_GAIN_DB["75"], "short", 99.0)
    with pytest.raises(tre.Refused):
        tre.assert_realisation_is_the_validated_one()


# ---------------------------------------------------------------------------
# the bracket, stated before the render
# ---------------------------------------------------------------------------
def test_the_bracket_brackets_the_808():
    """The pre-render bracket is derived from the circuit alone; if it did not
    contain the machine's own curve, the circuit's per-band levels would already
    be refuted and no render would be needed."""
    br = tre.bracket_db()
    ref = tre.fischer_curves()
    for dec, r in ref.items():
        for c in tn.CODES:
            v = r["h_minus_l_anchored_db"][c]
            assert br[c][0] - 1e-9 <= v <= br[c][1] + 1e-9, (dec, c, v, br[c])


def test_the_bracket_is_zero_width_at_the_anchor():
    assert tre.bracket_db()[tre.ANCHOR] == [0.0, 0.0]


def test_the_bracket_is_not_vacuous_in_the_middle():
    """A bracket that admitted everything would make property 2 decoration. At
    TONE 25 and 75 it is 4.3 and 3.0 dB wide, narrower than the machine's own
    7.3 dB span."""
    br = tre.bracket_db()
    for c in ("25", "75"):
        assert br[c][1] - br[c][0] < 5.0, (c, br[c])


# ---------------------------------------------------------------------------
# the verdict function's own controls: it must be able to report a miss
# ---------------------------------------------------------------------------
def _synthetic(anchored, h_ratio, ln_ratio=None):
    ln_ratio = ln_ratio or {c: 1.0 for c in tn.CODES}
    return {"h_minus_l_anchored_db": dict(anchored),
            "h_edt10_ratio": dict(h_ratio),
            "ln_edt10_ratio": dict(ln_ratio)}


def test_a_perfect_match_passes_every_property():
    ref = tre.fischer_curves()
    col = ref["50"]
    ours = _synthetic(col["h_minus_l_anchored_db"], col["h_edt10_ratio"])
    v = tre.verdict(ours, {"50": col})
    for name, p in v["properties"].items():
        assert p["ok"], (name, p)


def test_a_planted_four_dB_miss_is_reported_as_a_miss():
    """Step 8's wrong-then-right 4: a verdict that reports a match from two rows
    twelve dB apart. This is the control that makes that impossible here."""
    ref = tre.fischer_curves()
    col = ref["50"]
    bad = {c: col["h_minus_l_anchored_db"][c] + (0.0 if c == tre.ANCHOR else 4.0)
           for c in tn.CODES}
    v = tre.verdict(_synthetic(bad, col["h_edt10_ratio"]), {"50": col})
    assert not v["properties"]["h-minus-l-tracks-808"]["ok"]
    assert v["properties"]["h-minus-l-tracks-808"]["worst_db"] == pytest.approx(4.0, abs=1e-6)


def test_a_flat_curve_is_reported_as_not_monotone_and_not_tracking():
    ref = tre.fischer_curves()
    col = ref["50"]
    flat = {c: 0.0 for c in tn.CODES}
    v = tre.verdict(_synthetic(flat, {c: 1.0 for c in tn.CODES}), {"50": col})
    assert not v["properties"]["h-minus-l-monotone"]["ok"]
    assert not v["properties"]["h-minus-l-tracks-808"]["ok"]
    assert not v["properties"]["h-edt-falls-with-tone"]["ok"]


def test_a_reversed_curve_is_reported_as_not_monotone():
    ref = tre.fischer_curves()
    col = ref["50"]
    rev = {c: -col["h_minus_l_anchored_db"][c] for c in tn.CODES}
    v = tre.verdict(_synthetic(rev, col["h_edt10_ratio"]), {"50": col})
    assert not v["properties"]["h-minus-l-monotone"]["ok"]


def test_a_tone_dependent_low_band_decay_is_caught():
    ref = tre.fischer_curves()
    col = ref["50"]
    ln = {c: (1.0 if c == tre.ANCHOR else 2.5) for c in tn.CODES}
    v = tre.verdict(_synthetic(col["h_minus_l_anchored_db"], col["h_edt10_ratio"], ln),
                    {"50": col})
    assert not v["properties"]["ln-edt-tone-invariant"]["ok"]


def test_the_verdict_reports_every_one_of_the_25_settings():
    """Acceptance 3 says report every setting, and says which are development."""
    ref = tre.fischer_curves()
    col = ref["50"]
    v = tre.verdict(_synthetic(col["h_minus_l_anchored_db"], col["h_edt10_ratio"]), ref)
    seen = {s for c in v["columns"].values() for s in c["settings"].values()}
    assert len(seen) == 25
    assert set(cb.DEVELOPMENT) | set(cb.CONFIRMATION) == seen
    n_dev = sum(1 for c in v["columns"].values() for s in c["split"].values()
                if s == "development")
    assert n_dev == len(cb.DEVELOPMENT) == 9


def test_the_bounds_are_step_9s_bounds_unchanged():
    import cymbal_tone_knob as ctk
    assert tre.BOUND_DB == ctk.BOUND_DB == 3.0
    assert tre.TIME_TOL == ctk.TIME_TOL == 0.50


# ---------------------------------------------------------------------------
# superposition, and the requirement it gates
# ---------------------------------------------------------------------------
def test_superposition_reproduces_a_planted_linear_mix_exactly():
    """The known answer for `predict_anchored`: build shares whose linear mix IS
    the prediction, and the check must read zero error."""
    shares = {"low": {"L": 1.0, "H": 0.01},
              "decay": {"L": 1e-3, "H": 1.0},
              "short": {"L": 1e-5, "H": 0.3}}
    pred = tre.predict_anchored(shares)
    chk = tre.superposition_check(shares, pred)
    assert chk["ok"] and chk["worst_db"] == 0.0


def test_the_requirement_recovers_a_planted_offset():
    """Plant a short-band offset, generate the curve it produces, and the solver
    must read the offset back to the grid step."""
    shares = {"low": {"L": 1.0, "H": 0.01},
              "decay": {"L": 1e-3, "H": 1.0},
              "short": {"L": 1e-5, "H": 0.3}}
    planted = 12.0
    target = tre.predict_anchored(shares, {"short": planted})
    got = tre.required_short_band_offset(shares, target)
    assert got["offset_db"] == pytest.approx(planted, abs=0.25)
    assert got["worst_residual_db"] < 1e-6


def test_raising_the_short_band_is_what_moves_the_curve():
    """`predict_anchored` must actually respond to the offset, or the requirement
    above would be unfalsifiable."""
    shares = {"low": {"L": 1.0, "H": 0.01},
              "decay": {"L": 1e-3, "H": 1.0},
              "short": {"L": 1e-5, "H": 0.3}}
    a = tre.predict_anchored(shares)["10"]
    b = tre.predict_anchored(shares, {"short": 15.0})["10"]
    assert b > a + 1.0, (a, b)


def test_a_broken_superposition_gates_the_requirement():
    """The recorded run refuses the requirement, because the swing VCAs clip and
    a linear mix of the separately-rendered bands misses the render's own curve
    by 1.26 dB against a 0.5 dB bound. That gating must be real."""
    shares = {"low": {"L": 1.0, "H": 0.01},
              "decay": {"L": 1e-3, "H": 1.0},
              "short": {"L": 1e-5, "H": 0.3}}
    pred = tre.predict_anchored(shares)
    measured = {c: (0.0 if c == tre.ANCHOR else pred[c] + 2.0) for c in tn.CODES}
    assert not tre.superposition_check(shares, measured)["ok"]


def test_cy_owned_addresses_excludes_every_shared_register():
    """The hats share the 7.1 kHz band-pass (M_HATBP) and, in the shipped kit,
    the closed hat's own high-pass carries the cymbal's short band. Neither may
    be claimed as the cymbal's."""
    owned = tre.cy_owned_addresses()
    for m in (dx.M_HATBP, dx.M_CHHP, dx.M_OHHP):
        for f in range(4):
            assert dx.A_MODE + m * dx.MODE_STRIDE + f not in owned, m
    for e in (dx.E_CH, dx.E_OH):
        for f in range(4):
            assert dx.A_ENV + e * dx.ENV_STRIDE + f not in owned, e


# ---------------------------------------------------------------------------
# the committed evidence record
# ---------------------------------------------------------------------------
RECORD = ROOT / "docs" / "scorecard" / "cymbal-369" / "tone-render" / "tone-render.json"


@pytest.mark.skipif(not RECORD.exists(), reason="the render record is not committed")
def test_the_committed_record_says_what_the_writeup_says():
    import json
    d = json.loads(RECORD.read_text())
    assert d["preservation_complete"] is True
    assert d["preservation"]["ok"] is True
    assert d["precondition"]["budget"]["modes"] == 20
    # the headline: monotone and inside the bracket, but not tracking
    p = d["verdict"]["properties"]
    assert p["h-minus-l-monotone"]["ok"] and p["h-minus-l-in-bracket"]["ok"]
    assert not p["h-minus-l-tracks-808"]["ok"]
    assert not p["h-edt-tracks-808"]["ok"]
    # ...and the mechanism: the short band holds almost none of H
    sh = d["band_shares"]
    assert 10 * math.log10(sh["short"]["H"] / sh["decay"]["H"]) < -8.0
    assert sh["low"]["H"] > sh["short"]["H"]
    # ...and the one-number requirement was REFUSED, not reported
    assert d["superposition"]["ok"] is False
    assert d["required_short_band_offset"] is None
