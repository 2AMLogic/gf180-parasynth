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
README = ROOT / "docs" / "scorecard" / "cymbal-369" / "tone-render" / "README.md"
MODEL = ROOT / "model" / "cymbal_candidate.py"


def _git(*args):
    """(returncode, stdout). Never raises: a repository this test cannot read is
    a REFUSAL to report, not a contradiction to assert."""
    import subprocess
    try:
        r = subprocess.run(["git", *args], cwd=ROOT, capture_output=True,
                           text=True, timeout=60)
    except (OSError, subprocess.SubprocessError) as exc:      # pragma: no cover
        return 127, str(exc)
    return r.returncode, r.stdout


def _provenance_records() -> list:
    """Every committed scorecard record carrying BOTH provenance fields."""
    import json
    out = []
    for p in sorted((ROOT / "docs" / "scorecard").rglob("*.json")):
        try:
            d = json.loads(p.read_text())
        except (ValueError, OSError):
            continue
        if isinstance(d, dict) and "commit" in d and "sources_dirty" in d:
            out.append((p, d))
    return out


def test_no_committed_scorecard_record_was_produced_from_a_dirty_tree():
    """#429: `tone-render.json` was the one record in eleven whose own
    provenance said its figures could not be reproduced from any commit --
    `sources_dirty: true`, at a commit whose tree predates the revision-4 model
    change it was measuring. The field that exists to tell a reader that was
    doing its job, and nothing was reading it.

    Generalised to every record that carries the two fields, so the next one
    cannot regress the same way. It is deliberately NOT generalised to commit
    reachability: three of the records here name commits that are not in this
    clone at all (feature branches that merged under different shas), so an
    ancestry gate over all of them would be unsatisfiable -- and an
    unsatisfiable gate trains everyone to ignore gates."""
    recs = _provenance_records()
    assert len(recs) >= 11, f"expected the scorecard's provenance records; found {len(recs)}"
    dirty = [str(p.relative_to(ROOT)) for p, d in recs if d["sources_dirty"] is not False]
    assert not dirty, ("records whose sources were dirty when they were written: "
                       + ", ".join(dirty))
    malformed = [str(p.relative_to(ROOT)) for p, d in recs
                 if not (isinstance(d["commit"], str) and len(d["commit"]) == 40)]
    assert not malformed, malformed


@pytest.mark.skipif(not RECORD.exists(), reason="the render record is not committed")
def test_the_render_record_is_reproducible_from_a_commit_in_this_history():
    """The record's own commit must be reachable from HEAD and its tree must
    contain the model revision the write-up is about. The committed record
    named `28db319a`, which is in the history but whose `cymbal_candidate.py`
    has no revision 4 -- so every headline figure of step 10 was unreachable
    from any commit in the PR that reported it."""
    import json
    d = json.loads(RECORD.read_text())
    sha = d["commit"]
    if _git("cat-file", "-e", f"{sha}^{{commit}}")[0] != 0:
        pytest.skip(f"REFUSED: commit {sha[:8]} is not in this clone "
                    "(shallow checkout or a pruned branch) -- cannot answer")
    assert _git("merge-base", "--is-ancestor", sha, "HEAD")[0] == 0, (
        f"{sha[:8]} is not an ancestor of HEAD: the record cannot be "
        "reproduced from this history")
    rc, text = _git("show", f"{sha}:model/cymbal_candidate.py")
    assert rc == 0, f"model/cymbal_candidate.py is absent at {sha[:8]}"
    assert "REVISION 4" in text.upper(), (
        f"the tree at {sha[:8]} has no revision 4 of the model, so it cannot "
        "be the tree that produced this record")


@pytest.mark.skipif(not RECORD.exists(), reason="the render record is not committed")
def test_every_recorded_property_flag_is_a_real_boolean():
    """`h-edt-falls-with-tone` serialised as `1.0`, not `true`: `x < 1.0 and
    y > 1.0` returns the second operand, which was an np.bool_, and
    `json.dumps(..., default=float)` sent it through the float fallback. Truthy,
    so every consumer worked -- and an `is True` assertion on it would not."""
    import json
    d = json.loads(RECORD.read_text())
    for name, p in d["verdict"]["properties"].items():
        assert isinstance(p["ok"], bool), (name, type(p["ok"]).__name__, p["ok"])
    for col in d["verdict"]["columns"].values():
        assert isinstance(col["h_minus_l_ok"], bool) and isinstance(col["h_edt10_ok"], bool)
    assert isinstance(d["preservation"]["ok"], bool)
    assert isinstance(d["superposition"]["ok"], bool)


def test_the_verdicts_flags_are_booleans_before_they_are_serialised():
    """...and the same thing checked at the source, so a record regenerated on a
    different numpy cannot reintroduce it.

    The per-column flags are here because the first pass at #429 fixed only the
    property the issue named. `h_edt10_ok` has the identical defect from the
    identical cause -- `d_hed`'s values come from a numpy ratio, so
    `max(...) <= TIME_TOL` is a numpy comparison -- and it survived a `bool()`
    cast aimed at one site. That is what made a per-site cast the wrong fix."""
    ref = tre.fischer_curves()
    col = ref["50"]
    v = tre.verdict(_synthetic(col["h_minus_l_anchored_db"], col["h_edt10_ratio"]), ref)
    for name, p in v["properties"].items():
        assert type(p["ok"]) is bool, (name, type(p["ok"]).__name__)
    for dec, c in v["columns"].items():
        assert type(c["h_minus_l_ok"]) is bool, (dec, type(c["h_minus_l_ok"]).__name__)
        assert type(c["h_edt10_ok"]) is bool, (dec, type(c["h_edt10_ok"]).__name__)


def test_the_serialiser_cannot_turn_a_flag_into_a_number():
    """The root cause, tested where it lives rather than at each of its outlets:
    `default=float` served every numpy scalar, and `float(np.True_)` is `1.0`.
    The first two assertions are the injected control -- they state what the old
    default did, so this test would have been red before the fix and cannot pass
    by accident afterwards."""
    import json
    import numpy as np
    assert json.dumps(np.True_, default=float) == "1.0"           # the defect
    assert json.dumps(np.float64(0.5), default=float) == "0.5"    # ...and why it looked fine
    assert json.dumps(np.True_, default=tn.json_leaf) == "true"
    assert json.dumps(np.False_, default=tn.json_leaf) == "false"
    assert json.dumps(np.int64(20), default=tn.json_leaf) == "20"
    assert json.dumps(np.float64(0.5), default=tn.json_leaf) == "0.5"
    # a nested flag, which is how it actually reached the record
    assert json.dumps({"ok": np.True_}, default=tn.json_leaf) == '{"ok": true}'


def test_the_808s_decay_columns_agree_to_what_the_writeup_says():
    """The DECAY confound the write-up bounds rather than assumes. Both figures
    it quotes -- the worst cross-column disagreement and the range of the
    columns' own spans -- are re-derived from `fischer_curves()` here, because
    #429 found both stated wrong (0.5 dB for a computed 0.601, and 7.15 for a
    7.14 that was read off a rounded table)."""
    import re
    ref = tre.fischer_curves()
    cols = {k: v["h_minus_l_anchored_db"] for k, v in ref.items()}
    worst = max(max(c[t] for c in cols.values()) - min(c[t] for c in cols.values())
                for t in tn.CODES)
    spans = [max(c.values()) - min(c.values()) for c in cols.values()]
    assert worst == pytest.approx(0.601, abs=5e-4), worst
    assert min(spans) == pytest.approx(7.143, abs=5e-4), spans
    assert max(spans) == pytest.approx(8.086, abs=5e-4), spans

    flat = re.sub(r"\s+", " ", README.read_text())
    m = re.findall(r"DECAY columns to \*\*([\d.]+) dB\*\*.*?spans run ([\d.]+)–([\d.]+) dB",
                   flat)
    assert len(m) == 1, "the DECAY-confound sentence moved; it is now unchecked"
    got = [float(x) for x in m[0]]
    assert got[0] == pytest.approx(round(worst, 3), abs=5e-4), got
    assert got[1] == pytest.approx(min(spans), abs=5e-3), got
    assert got[2] == pytest.approx(max(spans), abs=5e-3), got

    d = re.findall(r"the same to within ([\d.]+) dB in all five of its DECAY columns",
                   re.sub(r"\s+", " ", MODEL.read_text()))
    assert len(d) == 1 and float(d[0]) == pytest.approx(round(worst, 3), abs=5e-4), d


@pytest.mark.skipif(not RECORD.exists(), reason="the render record is not committed")
def test_the_committed_record_says_what_the_writeup_says():
    import json
    d = json.loads(RECORD.read_text())
    assert d["preservation_complete"] is True
    assert d["sources_dirty"] is False
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


# ===========================================================================
# #413: the knob renderer on the SHIPPED structure
# ===========================================================================
# Registers first (free, exact); the renders after (minutes, module-cached).
import numpy as np                      # noqa: E402
import run_case as rc                   # noqa: E402
import cymbal_vca_drive as vd           # noqa: E402

TEST_SECONDS = 5.0


def _good_row(late_l=True):
    """EDT10 rising, L's late T20 qualified and rising (as #656's renders measure
    it), H's late T20 refused at the top two positions (as they also do)."""
    lt = (565.0, 1220.0, 1890.0, 2540.0, 3110.0) if late_l else (None,) * 5
    return {d: {"H_edt10_ms": h, "L_edt10_ms": l, "H_t20_late_ms": ht, "L_t20_late_ms": t}
            for d, h, l, t, ht in zip(tre.DECAY_ORDER, (80, 151, 268, 330, 405), (287, 573, 869, 1798, 1810),
                                      lt, (207.0, 414.0, 641.0, None, None))}


_TAUS = None


def _taus():
    """The real register image's tau table (cheap: no render)."""
    global _TAUS
    if _TAUS is None:
        _TAUS = tre.decay_taus(cc.TONE_ANCHOR)
    return _TAUS


_ALL_DISTINCT = {p: False for p in [("00", "25"), ("25", "50"), ("50", "75"), ("75", "10"), ("00", "10")]}
_FISCHER = dict(zip(tre.DECAY_ORDER, (265.0, 432.0, 721.0, 962.0, 1094.0)))


# ---- start red: a no-op knob, a saturated knob and a non-finite number -------
def test_a_noop_knob_is_red():
    """A stub CY branch that ignores DECAY renders five identical waveforms with
    five identical measures. Every property that is about DECAY moving must be
    FAIL, not PASS and not REFUSED."""
    flat = {d: {"H_edt10_ms": 151.0, "L_edt10_ms": 573.0, "H_t20_late_ms": None,
                "L_t20_late_ms": None} for d in tre.DECAY_ORDER}
    ident = {p: True for p in _ALL_DISTINCT}
    v = tre.decay_row_verdict(flat, ident, _FISCHER)
    for k in ("distinct-renders", "h-edt10-rises", "l-edt10-rises", "h-edt10-direction-matches-808"):
        assert v[k]["status"] == tre.FAIL, (k, v[k])


def test_a_clean_row_passes_and_an_unqualified_late_t20_is_refused_not_passed():
    v = tre.decay_row_verdict(_good_row(late_l=False), dict(_ALL_DISTINCT), _FISCHER, taus=_taus())
    for k in ("distinct-renders", "h-edt10-rises", "l-edt10-rises", "h-edt10-direction-matches-808",
              "decay-taus-realised"):
        assert v[k]["status"] == tre.PASS, (k, v[k])
    assert v["late-t20"]["status"] == tre.REFUSED     # nothing qualified in the gated band


def test_a_clean_row_with_l_qualified_and_rising_passes_the_late_t20_gate():
    """#656: L gated, H reported. H refused at 7.5/10 does not gate, and is
    still in the verdict with its own REFUSED status -- not dropped."""
    v = tre.decay_row_verdict(_good_row(), dict(_ALL_DISTINCT), _FISCHER, taus=_taus())
    lt = v["late-t20"]
    assert lt["status"] == tre.PASS and lt["gated_bands"] == ["L"]
    assert lt["bands"]["H"]["status"] == tre.REFUSED and lt["bands"]["H"]["gated"] is False
    assert lt["ungated"]["H"]["status"] == tre.REFUSED


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), None, "1200"])
def test_a_missing_or_nonfinite_gated_late_t20_is_refused(bad):
    row = _good_row()
    row["75"]["L_t20_late_ms"] = bad
    v = tre.decay_row_verdict(row, dict(_ALL_DISTINCT), _FISCHER, taus=_taus())
    assert v["late-t20"]["status"] == tre.REFUSED
    assert v["late-t20"]["bands"]["L"]["refused_at"] == ["75"]


def test_a_plateau_or_reversal_in_the_gated_band_fails():
    """The #656 shape: 7.5 and 10.0 equal (one envelope), or the 0.14 % reversal."""
    for v75, v10 in ((2540.0, 2540.0), (3653.91, 3648.81)):
        row = _good_row()
        row["75"]["L_t20_late_ms"], row["10"]["L_t20_late_ms"] = v75, v10
        v = tre.decay_row_verdict(row, dict(_ALL_DISTINCT), _FISCHER, taus=_taus())
        assert v["late-t20"]["status"] == tre.FAIL, (v75, v10)


def test_a_late_t20_gate_over_no_band_or_an_unknown_band_is_refused():
    """The vacuous gate: an empty band list would make `all([])` a pass."""
    for gated in ((), ("Ln",)):
        v = tre.decay_row_verdict(_good_row(), dict(_ALL_DISTINCT), _FISCHER, taus=_taus(), gated=gated)
        assert v["late-t20"]["status"] == tre.REFUSED, gated


def test_the_tau_property_refuses_without_a_register_image():
    v = tre.decay_row_verdict(_good_row(), dict(_ALL_DISTINCT), _FISCHER)
    assert v["decay-taus-realised"]["status"] == tre.REFUSED
    partial = {d: r for d, r in _taus().items() if d != "10"}
    assert tre.taus_verdict(partial)["status"] == tre.REFUSED


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), None])
def test_a_non_finite_measure_is_refused_never_passed(bad):
    row = _good_row()
    row["50"]["H_edt10_ms"] = bad
    v = tre.decay_row_verdict(row, dict(_ALL_DISTINCT), _FISCHER)
    assert v["h-edt10-rises"]["status"] == tre.REFUSED
    assert v["h-edt10-direction-matches-808"]["status"] == tre.REFUSED


def test_a_plateau_in_the_808_establishes_no_direction_but_a_reversal_still_fails():
    plateau = dict(_FISCHER, **{"10": _FISCHER["75"] * 0.995})       # the TONE-10 row's shape
    v = tre.decay_row_verdict(_good_row(), dict(_ALL_DISTINCT), plateau)
    p = v["h-edt10-direction-matches-808"]
    assert p["status"] == tre.PASS and p["undetermined_steps"] == ["75->10"]
    reversed_ours = _good_row()
    reversed_ours["50"]["H_edt10_ms"], reversed_ours["75"]["H_edt10_ms"] = 330, 268     # a real reversal
    v = tre.decay_row_verdict(reversed_ours, dict(_ALL_DISTINCT), _FISCHER)
    assert v["h-edt10-direction-matches-808"]["status"] == tre.FAIL
    flat = {d: 100.0 for d in tre.DECAY_ORDER}                                          # no direction at all
    v = tre.decay_row_verdict(_good_row(), dict(_ALL_DISTINCT), flat)
    assert v["h-edt10-direction-matches-808"]["status"] == tre.REFUSED


def test_a_missing_audio_comparison_is_refused():
    v = tre.decay_row_verdict(_good_row(), {}, _FISCHER)
    assert v["distinct-renders"]["status"] == tre.REFUSED


def test_the_late_t20_is_checked_where_it_qualifies():
    row = _good_row(late_l=False)
    for d, t in (("00", 570.0), ("25", 1232.0), ("50", 600.0)):      # falls: wrong
        row[d]["L_t20_late_ms"] = t
    v = tre.decay_row_verdict(row, dict(_ALL_DISTINCT), _FISCHER)
    assert v["late-t20"]["bands"]["L"]["status"] == tre.REFUSED
    assert v["late-t20"]["bands"]["L"]["monotone_where_qualified"] is False
    assert v["late-t20"]["status"] == tre.REFUSED   # refusals outrank an unqualified FAIL here


# ---- the register image ------------------------------------------------------
def test_the_anchor_writes_nothing_for_any_of_the_sixteen_sounds():
    for s in dx.SOUND_NAMES:
        assert dict(cc.shipped_knob_kit(cc.TONE_ANCHOR, cc.DECAY_ANCHOR, sound=s)) \
            == dict(dx.kit_with_sounds(s)), s


def test_decay_scale_is_strictly_increasing_and_one_at_the_anchor():
    k = [cc.decay_scale(d) for d in tre.DECAY_ORDER]
    assert all(b > a for a, b in zip(k, k[1:])) and cc.decay_scale(cc.DECAY_ANCHOR) == 1.0


def test_the_five_decay_images_are_pairwise_distinct():
    imgs = [tuple(cc.shipped_knob_kit(cc.TONE_ANCHOR, d)) for d in tre.DECAY_ORDER]
    assert len(set(imgs)) == 5


def test_the_decay_network_is_the_vca_drive_tools():
    res = {d: ohm for _, _, ohm, d in vd.ENV_RES}
    assert cc.DECAY_R93_OHM == res["R93"] and cc.DECAY_VR2_OHM == vd.ENV_POT["ohm"]


def test_nothing_outside_the_cymbal_envelopes_is_ever_written():
    own = cc.shipped_knob_owned_addresses()
    for t in tre.CODES:
        for d in tre.DECAY_ORDER:
            r = tre.owned_addresses_check(t, d)
            assert r["status"] == tre.PASS, (t, d, r["stray"])
    # and the check can fail: widen a write by hand
    base = dict(dx.kit_with_sounds("CY"))
    assert dx.A_MODE + dx.M_CYHI * dx.MODE_STRIDE + 2 not in own and base


def test_an_unknown_knob_position_is_refused_not_snapped():
    for bad in (3.0, "99", 11):
        with pytest.raises(cc.Refused):
            cc.shipped_knob_kit(bad, "25")
        with pytest.raises(cc.Refused):
            cc.shipped_knob_kit("50", bad)


def test_no_decay_position_sits_on_the_rate_floor_and_every_ask_is_realised():
    """#656 acceptance 1: E_CYL and E_CYD realise every DECAY ask within the
    frozen 5 % and no code is on its mantissa floor. The Q0.16 register this
    replaced is kept in the same table and still shows the floor: E_CYL rate 1
    (tau 1.3653 s) at both 7.5 and 10.0."""
    f = tre.rate_register_floor()
    assert f["q016_slowest_tau_s"] == pytest.approx(1.3653, abs=1e-3)
    p = f["positions"]
    for d in tre.DECAY_ORDER:
        for n in ("E_CYL", "E_CYD"):
            assert p[d][n]["at_floor"] is False and p[d][n]["within_tolerance"] is True, (d, n, p[d][n])
    assert p["75"]["E_CYL"]["code"] != p["10"]["E_CYL"]["code"]
    assert p["75"]["E_CYL"]["q016"]["code"] == p["10"]["E_CYL"]["q016"]["code"] == "0x00001"
    assert p["75"]["E_CYL"]["q016"]["at_floor"] and p["10"]["E_CYL"]["q016"]["at_floor"]
    # the old path's clamp, executed against the old register: k=8 and k=4 are one code
    assert dx.rate_reg(dx.CY_TAU_LOW * 8.0) == dx.rate_reg(dx.CY_TAU_LOW * 4.0) == 1


def test_the_q16_floor_control_activates_and_reddens_the_register_property():
    """THE #656 DEFECT as an executing mutant: builds, gives E_CYL one code at
    7.5 and 10.0 (activation), is not the clean image, and the tau property
    FAILS on it while it PASSES on the clean image."""
    ecl = dx.A_ENV + dx.E_CYL * dx.ENV_STRIDE + 2
    assert dict(tre.q16_kit("50", "75"))[ecl] == dict(tre.q16_kit("50", "10"))[ecl] == 1
    assert tre.q16_kit("50", "10") != cc.shipped_knob_kit("50", "10")
    assert tre.q16_kit("50", "25") == cc.shipped_knob_kit("50", "25")       # the anchor writes nothing
    assert tre.taus_verdict(tre.decay_taus("50", tre.q16_kit))["status"] == tre.FAIL
    assert tre.taus_verdict(tre.decay_taus("50"))["status"] == tre.PASS
    assert tre.taus_verdict(tre.decay_taus("50", tre.saturated_kit))["status"] == tre.FAIL


# ---- renders -----------------------------------------------------------------
@pytest.fixture(scope="module")
def clean_row():
    return tre.sweep_row(cc.TONE_ANCHOR, seconds=TEST_SECONDS)


def test_the_anchor_render_is_render_drum_solo_bit_for_bit():
    r = tre.anchor_equivalence()
    assert r["registers_differing"] == [] and r["audio_bit_identical"] and r["status"] == tre.PASS


def test_the_clean_row_passes_every_decay_property(clean_row):
    row, ident, _ = clean_row
    v = tre.decay_row_verdict(row, ident, tre.fischer_h_edt(cc.TONE_ANCHOR),
                              taus=tre.decay_taus(cc.TONE_ANCHOR))
    for k in ("distinct-renders", "h-edt10-rises", "l-edt10-rises", "h-edt10-direction-matches-808",
              "decay-taus-realised"):
        assert v[k]["status"] == tre.PASS, (k, v[k])
    assert not any(ident.values())
    # late-t20 needs the 7 s record (TEST_SECONDS is shorter); the committed
    # record's tests below carry it


def test_the_historical_saturation_is_caught(clean_row):
    """THE CONTROL. DECAY 10.0 given 7.5's scale: builds, executes, renders
    7.5 and 10.0 bit-identical, and the DECAY properties go red while the clean
    row above passes. `blind` is what the mutant does NOT move."""
    _, _, clean_audio = clean_row
    mrow, mident, maudio = tre.sweep_row(cc.TONE_ANCHOR, kit_fn=tre.saturated_kit,
                                         seconds=TEST_SECONDS,
                                         _audio={d: clean_audio[d] for d in ("00", "25", "50")})
    assert tre.saturated_kit("50", "10") == tre.saturated_kit("50", "75")       # it activated
    assert tre.saturated_kit("50", "10") != cc.shipped_knob_kit("50", "10")     # and is not the clean path
    assert mident[("75", "10")] is True
    v = tre.decay_row_verdict(mrow, mident, tre.fischer_h_edt(cc.TONE_ANCHOR),
                              taus=tre.decay_taus(cc.TONE_ANCHOR, tre.saturated_kit))
    assert v["decay-taus-realised"]["status"] == tre.FAIL
    assert v["distinct-renders"]["status"] == tre.FAIL
    assert v["h-edt10-rises"]["status"] == tre.FAIL
    assert v["l-edt10-rises"]["status"] == tre.FAIL
    # blind: endpoints 00 vs 10 are still distinct, so a gate that compared only
    # the endpoints would have passed this mutant
    assert ("00", "10") in mident and mident[("00", "10")] is False
    assert v["distinct-renders"]["identical_pairs"] == ["75=10"]


def test_the_hats_are_bit_exact_at_the_corners():
    res = tre.hat_preservation(tones=("00", "10"), decays=("00", "10"))
    assert res and all(res.values()), res


# ---- the committed #413 knob-render record -----------------------------------
# The render tests above run only the TONE-50 row (a row is minutes). These read
# the committed record so a regression in the other four rows, or a late-T20
# claim, turns something red without rendering. They recompute every verdict
# from the record's own measured numbers with the CURRENT verdict function, so
# a hand-edited status cannot pass and a gate change that would flip a recorded
# row cannot land silently.
KNOB_RECORD = ROOT / "docs" / "scorecard" / "cymbal-369" / "knob-render" / "knob-render.json"
_EDT_GATES = ("h-edt10-rises", "l-edt10-rises", "h-edt10-direction-matches-808")
_knob_skip = pytest.mark.skipif(not KNOB_RECORD.exists(), reason="the knob-render record is not committed")


def _knob_record() -> dict:
    import json
    return json.loads(KNOB_RECORD.read_text())


def _recorded_identical(verdict: dict) -> dict:
    pairs = list(zip(tre.DECAY_ORDER, tre.DECAY_ORDER[1:])) + [(tre.DECAY_ORDER[0], tre.DECAY_ORDER[-1])]
    same = set(verdict["distinct-renders"]["identical_pairs"])
    return {(a, b): f"{a}={b}" in same for a, b in pairs}


def _recompute(d: dict, tone: str, row: dict | None = None) -> dict:
    return tre.decay_row_verdict(row if row is not None else d["rows"][tone],
                                 _recorded_identical(d["verdicts"][tone]),
                                 tre.fischer_h_edt(tone), taus=tre.decay_taus(tone))


@_knob_skip
def test_the_committed_knob_record_covers_every_tone_row_and_passes_the_edt10_gate():
    d = _knob_record()
    assert sorted(d["rows"]) == sorted(tre.CODES) == sorted(d["verdicts"])
    for t in tre.CODES:
        assert sorted(d["rows"][t]) == sorted(tre.DECAY_ORDER), t
        rec = d["verdicts"][t]
        assert rec["distinct-renders"]["status"] == tre.PASS, t
        assert rec["distinct-renders"]["identical_pairs"] == [], t
        v = _recompute(d, t)
        for k in _EDT_GATES:
            assert v[k]["status"] == tre.PASS, (t, k, v[k])
            assert rec[k]["status"] == v[k]["status"], (t, k, "recorded status != recomputed")


# What the record measured after #656, pinned so a change in EITHER direction is
# seen: L's late T20 qualified and rising on TONE 00/25/50; refused at the slow
# end on TONE 75 and 10, which #660 localises to E_CYL's headroom (not the rate).
_L_REFUSED_AT = {"00": [], "25": [], "50": [], "75": ["10"], "10": ["50", "75", "10"]}


@_knob_skip
def test_the_committed_knob_record_gates_l_late_t20_and_keeps_h_visible():
    """#656: the gated band L qualifies at all five DECAY positions and rises on
    TONE 00/25/50, and is REFUSED (never a pass) where _L_REFUSED_AT says; the
    recorded statuses are what the numbers give under the CURRENT verdict
    function; H keeps its own recorded status (REFUSED somewhere on every row)
    and names #661; no E_CYL / E_CYD code is on the floor."""
    d = _knob_record()
    assert d["gate"]["late_t20_gated_bands"] == ["L"] and "late-t20" in d["gate"]["clean"]
    for t in tre.CODES:
        v = _recompute(d, t)
        rec = d["verdicts"][t]
        assert v["decay-taus-realised"]["status"] == rec["decay-taus-realised"]["status"] == tre.PASS, t
        assert rec["late-t20"]["status"] == v["late-t20"]["status"], t
        L = rec["late-t20"]["bands"]["L"]
        assert L["refused_at"] == _L_REFUSED_AT[t], (t, L["refused_at"])
        want = tre.PASS if not _L_REFUSED_AT[t] else tre.REFUSED
        assert v["late-t20"]["status"] == want, (t, v["late-t20"]["status"])
        for b in ("H", "L"):
            assert rec["late-t20"]["bands"][b]["status"] == v["late-t20"]["bands"][b]["status"], (t, b)
        assert rec["late-t20"]["ungated"]["H"]["status"] != tre.PASS, t
        assert rec["late-t20"]["ungated"]["H"]["issue"] == 661, t
    floor = d["rate_register_floor"]["positions"]
    for x in tre.DECAY_ORDER:
        for n in ("E_CYL", "E_CYD"):
            assert floor[x][n]["at_floor"] is False and floor[x][n]["within_tolerance"] is True, (x, n)


@_knob_skip
def test_the_knob_record_check_turns_red_on_a_planted_regression_off_the_anchor_row():
    """The control for the two tests above: a reversal planted in a non-TONE-50
    row (which no render test here exercises) must make the recompute FAIL."""
    import copy
    d = _knob_record()
    for t in [c for c in tre.CODES if c != cc.TONE_ANCHOR]:
        row = copy.deepcopy(d["rows"][t])
        row["50"]["H_edt10_ms"], row["75"]["H_edt10_ms"] = row["75"]["H_edt10_ms"], row["50"]["H_edt10_ms"]
        row["25"]["L_edt10_ms"] = row["00"]["L_edt10_ms"]
        row["10"]["L_t20_late_ms"] = row["75"]["L_t20_late_ms"]          # the #656 plateau
        v = _recompute(d, t, row)
        assert v["h-edt10-rises"]["status"] == tre.FAIL, t
        assert v["l-edt10-rises"]["status"] == tre.FAIL, t
        assert v["h-edt10-direction-matches-808"]["status"] == tre.FAIL, t
        # a plateau where every position qualified is FAIL; where one is refused
        # the row stays REFUSED -- red either way, never PASS
        qualified = all(tre._finite(row[x]["L_t20_late_ms"]) for x in tre.DECAY_ORDER)
        assert v["late-t20"]["status"] == (tre.FAIL if qualified else tre.REFUSED), t


CONFIRM_RECORD = KNOB_RECORD.parent / "confirmation.json"


@pytest.mark.skipif(not CONFIRM_RECORD.exists(), reason="the confirmation record is not committed")
def test_the_confirmation_record_reproduces_the_gate_on_untouched_conditions():
    """#656 confirmation (tools/probes/cymbal_late_t20.py confirm): TONE 25 at
    nine wiper fractions, four between the knob codes -- L qualifies and rises
    at every one; TONE 75 with the strike 7919 frames later -- L refuses at
    DECAY 10 exactly as the selection grid did (#660), and nowhere else. From a
    clean tree."""
    import json
    c = json.loads(CONFIRM_RECORD.read_text())
    assert c["sources_dirty"] is False and c["rate_exp_bits"] == dx.RATE_EXP_BITS
    a = c["intermediate_wipers_tone25"]
    assert a["L"]["status"] == "PASS" and len(a["rows"]) == 9
    vals = [a["rows"][k]["L"]["t20_late_ms"] for k in sorted(a["rows"], key=float)]
    assert all(y > x for x, y in zip(vals, vals[1:])), vals
    b = c["shifted_phase_tone75"]
    assert b["L"]["status"] == "REFUSED" and b["L"]["refused_at"] == [4]
    assert b["hit_frame"] != rc.DRUM_SOLO_HIT_FRAME


# ---- the command's own exit decision (run_knob_render, not just the verdict) --
# The record tests above recompute verdicts from committed numbers; none of them
# reaches the EXIT the command itself returns. These do, with every expensive
# render behind run_knob_render replaced by fakes.
_IDENT_PAIRS = dict(_ALL_DISTINCT)
_ALL_IDENTICAL = {p: True for p in _ALL_DISTINCT}
_MUTANT_MUST_REDDEN = ("distinct-renders", "h-edt10-rises", "l-edt10-rises", "late-t20")


def _flat_row():
    """A knob that does nothing: every measure the same at every position,
    the late T20 of L qualified but flat (a plateau, FAIL)."""
    return {d: {"H_edt10_ms": 151.0, "L_edt10_ms": 573.0, "H_t20_late_ms": None,
                "L_t20_late_ms": 1220.0} for d in tre.DECAY_ORDER}


def _install_knob_fakes(monkeypatch, *, clean_row, clean_ident, mutant_row, mutant_ident,
                        q16_row=None, q16_ident=None):
    """`clean_row` is what every real TONE row measures, `mutant_row` what the
    saturation control measures, `q16_row` the Q0.16-floor control (default:
    the mutant's); the ident maps say which DECAY pairs rendered identical
    audio. decay_taus is the REAL register table (no render). Returns the
    sweep_row calls: True a clean row, else the control's kit function name."""
    import copy
    calls = []

    def fake_sweep(tone, kit_fn=None, seconds=tre.SWEEP_SECONDS, decays=tre.DECAY_ORDER,
                   _audio=None):
        calls.append(True if kit_fn is None else kit_fn.__name__)
        if kit_fn is None:
            row, ident = clean_row, clean_ident
        elif kit_fn is tre.q16_kit and q16_row is not None:
            row, ident = q16_row, q16_ident
        else:
            row, ident = mutant_row, mutant_ident
        return copy.deepcopy(row), dict(ident), {d: np.zeros(2) for d in decays}

    monkeypatch.setattr(tre, "sweep_row", fake_sweep)
    monkeypatch.setattr(tre, "anchor_equivalence",
                        lambda: {"registers_differing": [], "audio_bit_identical": True,
                                 "status": tre.PASS})
    monkeypatch.setattr(tre, "owned_addresses_check",
                        lambda t, d: {"stray": {}, "status": tre.PASS})
    monkeypatch.setattr(tre, "hat_preservation", lambda **k: {"OH@TONE00/DECAY00": True})
    monkeypatch.setattr(tre, "fischer_h_edt", lambda t, path=None: dict(_FISCHER))
    return calls


def _run_knob_render(monkeypatch, tmp_path, **kw):
    import json
    calls = _install_knob_fakes(monkeypatch, **kw)
    out = tmp_path / "knob-render.json"
    code = tre.run_knob_render(out)
    assert calls == [True] * len(tre.CODES) + ["saturated_kit", "q16_kit"], calls   # rows, then controls
    return code, json.loads(out.read_text())


def test_knob_render_exits_zero_only_with_every_declared_property_green(monkeypatch, tmp_path):
    """L's late T20 qualified and rising on every row, H refused at 7.5/10
    (reported, ungated), both controls red where they must be: exit 0."""
    code, rec = _run_knob_render(monkeypatch, tmp_path,
                                 clean_row=_good_row(), clean_ident=_IDENT_PAIRS,
                                 mutant_row=_flat_row(), mutant_ident=_ALL_IDENTICAL)
    assert code == 0
    assert rec["verdicts"]["00"]["late-t20"]["status"] == tre.PASS
    assert rec["verdicts"]["00"]["late-t20"]["bands"]["H"]["status"] == tre.REFUSED   # visible
    assert set(rec["saturation_control"]["red"]) == set(_MUTANT_MUST_REDDEN) \
        | {"h-edt10-direction-matches-808", "decay-taus-realised"}
    assert rec["saturation_control"]["misses"] == [] and rec["q16_floor_control"]["misses"] == []


def test_knob_render_is_red_on_the_656_late_t20_reversal(monkeypatch, tmp_path):
    """The record #413 committed: TONE 00's L rises then falls by 0.14 % at
    7.5 -> 10.0. That used to be reported and exit 0; it now gates."""
    clean = _good_row()
    clean["75"]["L_t20_late_ms"], clean["10"]["L_t20_late_ms"] = 3653.91, 3648.81
    code, rec = _run_knob_render(monkeypatch, tmp_path,
                                 clean_row=clean, clean_ident=_IDENT_PAIRS,
                                 mutant_row=_flat_row(), mutant_ident=_ALL_IDENTICAL)
    assert code == 1
    assert rec["verdicts"]["00"]["late-t20"]["status"] == tre.FAIL


def test_knob_render_is_red_when_the_q16_control_passes_the_late_t20(monkeypatch, tmp_path):
    """A Q0.16-floor control whose renders still give a rising L late T20 has
    not reproduced the defect at the acoustic gate: a miss, exit 1."""
    code, rec = _run_knob_render(monkeypatch, tmp_path,
                                 clean_row=_good_row(), clean_ident=_IDENT_PAIRS,
                                 mutant_row=_flat_row(), mutant_ident=_ALL_IDENTICAL,
                                 q16_row=_good_row(), q16_ident=_IDENT_PAIRS)
    assert code == 1
    assert rec["q16_floor_control"]["misses"] == ["late-t20"]


def test_knob_render_is_red_on_a_failed_clean_decay_verdict(monkeypatch, tmp_path):
    """A clean row whose EDT10 does not rise (the old path's flat 7.5 == 10.0)
    must fail the command, not only the verdict helper."""
    code, rec = _run_knob_render(monkeypatch, tmp_path,
                                 clean_row=_flat_row(), clean_ident=_IDENT_PAIRS,
                                 mutant_row=_flat_row(), mutant_ident=_ALL_IDENTICAL)
    assert code == 1
    assert rec["saturation_control"]["red"], "the control still ran"


def test_knob_render_is_red_on_a_refused_clean_decay_verdict(monkeypatch, tmp_path):
    row = _good_row()
    row["50"]["H_edt10_ms"] = None                    # the measure refused: not PASS, not data
    code, rec = _run_knob_render(monkeypatch, tmp_path,
                                 clean_row=row, clean_ident=_IDENT_PAIRS,
                                 mutant_row=_flat_row(), mutant_ident=_ALL_IDENTICAL)
    assert code == 1
    assert rec["verdicts"]["50"]["h-edt10-rises"]["status"] == tre.REFUSED


def test_knob_render_is_red_on_an_uncaught_mutant(monkeypatch, tmp_path):
    """Preservation and every clean verdict green, but the injected saturation
    changed nothing: the control has no teeth and the command must say so."""
    code, rec = _run_knob_render(monkeypatch, tmp_path,
                                 clean_row=_good_row(), clean_ident=_IDENT_PAIRS,
                                 mutant_row=_good_row(), mutant_ident=_IDENT_PAIRS)
    assert code == 1
    # decay-taus-realised still reads the REAL saturated register image, so it
    # alone is red; every acoustic property the fake left green is a miss
    assert rec["saturation_control"]["red"] == ["decay-taus-realised"]
    assert rec["saturation_control"]["misses"] == list(_MUTANT_MUST_REDDEN)


def test_knob_render_is_red_on_a_mutant_that_reddens_only_one_property(monkeypatch, tmp_path):
    """The control must redden its INTENDED observables. Here the audio went
    identical (distinct-renders red) but the EDT10 rises still pass: half-caught
    is not caught."""
    code, rec = _run_knob_render(monkeypatch, tmp_path,
                                 clean_row=_good_row(), clean_ident=_IDENT_PAIRS,
                                 mutant_row=_good_row(), mutant_ident=_ALL_IDENTICAL)
    assert code == 1
    assert rec["saturation_control"]["red"] == ["distinct-renders", "decay-taus-realised"]
    assert rec["saturation_control"]["misses"] == ["h-edt10-rises", "l-edt10-rises", "late-t20"]
