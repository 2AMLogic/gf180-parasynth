"""Known answers and controls for tools/cymbal_tone_nodal.py (#369 step 10).

The tool's own `--check` is the gate; these are the known answers underneath it,
plus the bindings that stop it drifting away from the two things it must agree
with: step 5's recorded per-band figures (`docs/scorecard/cymbal-369/candidate3/`)
and step 9's TONE law (`tools/cymbal_tone_knob.alpha_of`).

No recording is read. Every number here is transfer-function arithmetic.
"""
from __future__ import annotations

import math
import pathlib
import sys

import numpy as np
import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "model"), str(ROOT / "tools")]
import drums_fx as dx                    # noqa: E402
import cymbal_candidate as cc            # noqa: E402
import cymbal_tone_knob as ctk           # noqa: E402
import cymbal_tone_nodal as tn           # noqa: E402
import cymbal_tone_realisation as tr     # noqa: E402


# ---------------------------------------------------------------------------
# known answers: the measurement reads back what was planted
# ---------------------------------------------------------------------------
def test_a_planted_single_pole_target_is_recovered_with_no_error():
    """If the target IS one real pole at a network pole, the realisation that
    carries that pole must read essentially zero shape error."""
    cfg = tn.config()
    p = 4219.0
    hz = tr.THIRDS
    target = tn.realised_db(hz, (p,), 0)
    err = tn.shape_error_db("low", 0.5, (p,), 0, cfg, target=target)
    assert err < 1e-6, err


def test_a_planted_offset_does_not_change_the_shape_error():
    """Shape is offset-free by construction: the level rule absorbs a uniform
    gain error, so a planted 6 dB step must read as nothing."""
    cfg = tn.config()
    hz = tr.THIRDS
    base = tn.target_db("low", hz, 0.5, cfg)
    a = tn.shape_error_db("low", 0.5, (4219.0,), 0, cfg, target=base)
    b = tn.shape_error_db("low", 0.5, (4219.0,), 0, cfg, target=base + 6.0)
    assert abs(a - b) < 1e-9, (a, b)


def test_a_planted_shape_error_reads_back_at_its_planted_size():
    """Tilt the target by a known amount per octave over the band and the
    reading must grow with it, monotonically and in the right ballpark."""
    cfg = tn.config()
    hz = tr.THIRDS
    lo, hi = tn.active_hz("low")
    base = tn.target_db("low", hz, 0.5, cfg)
    got = []
    for per_oct in (0.0, 1.0, 2.0, 4.0):
        tilt = per_oct * np.log2(hz / tr.CENTRE_HZ["low"])
        got.append(tn.shape_error_db("low", 0.5, (4219.0,), 0, cfg, target=base + tilt))
    assert got == sorted(got), got
    # over 2-8 kHz (2 octaves), a 4 dB/octave tilt de-trended about the centre
    # cannot read less than about 2 dB or more than the full 8 dB span.
    assert 2.0 < got[-1] < 8.0, got
    assert hi / lo == pytest.approx(4.0, rel=0.02)


def test_more_than_two_poles_in_one_section_refuses():
    with pytest.raises(tn.Refused):
        tn.realised_db(tr.THIRDS, (100.0, 200.0, 300.0), 0)


def test_a_coefficient_outside_the_register_refuses():
    """`real_pole_regs` raises rather than clipping; a silently clamped
    coefficient is a different filter."""
    with pytest.raises(ValueError):
        cc.real_pole_regs([1e-9, 1e-9])          # a1 -> 2.0, outside Q2.24


# ---------------------------------------------------------------------------
# bindings: this tool must not be able to disagree with steps 5 and 9
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("band,recorded", [("low", 0.45), ("decay", 1.41), ("short", 1.35)])
def test_rev3_against_figure_9_reproduces_step_5s_recorded_figure(band, recorded):
    """`docs/scorecard/cymbal-369/candidate3/README.md`'s shape-error column.

    This is the test that caught the sub-chain accounting: an earlier draft put
    Hh3's own 5195 Hz pole into the realisation as well as into
    `band_chain_db`, and read the short band at 1.55 dB against step 5's 1.35.
    """
    got = tn.rev3_realisation(band, tn.config())["worst_fig9_db"]
    assert got == pytest.approx(recorded, abs=0.06), (band, got, recorded)


def test_the_tone_law_is_step_9s_and_not_a_second_copy():
    cfg = tn.config()
    for code in tn.CODES:
        assert tn.alpha_of(code, cfg) == ctk.alpha_of(tn.FRAC[code], mapping="linear")


def test_the_rails_are_step_9s_assignment():
    assert tn.RAILS == ctk.RAILS


def test_the_bound_is_step_5s_bound_unchanged():
    """A step may not move the goalposts it is judged against."""
    assert tn.SHAPE_BOUND_DB == tr.SHAPE_BOUND_DB == 3.0


# ---------------------------------------------------------------------------
# the finding: Figure 9's Ht1 window is blind where the low band lives
# ---------------------------------------------------------------------------
def test_the_two_routes_agree_on_the_band_figure_9_fully_plots():
    """Ht3 is drawn 20 Hz-20 kHz, so the nodal solution and the digitised curve
    must agree there. This is the external grounding for the nodal route: if it
    failed, the low-band finding below would be evidence against the network,
    not against Figure 9's window."""
    p = tn.properties(tn.config())["nodal-grounded"]
    assert p["ok"], p
    assert p["value_db"] <= tn.CROSS_BOUND_DB


def test_the_two_routes_disagree_where_figure_9_stops_plotting():
    p = tn.properties(tn.config())["fig9-window-blind"]
    assert p["ok"], p
    assert p["value_db"] > 3.0, p          # not marginal: several dB


def test_revision_3s_low_band_is_outside_its_own_bound_at_every_tone_position():
    r = tn.rev3_realisation("low", tn.config())
    assert min(r["per_tone_nodal_db"].values()) > tn.SHAPE_BOUND_DB, r
    assert r["worst_fig9_db"] < tn.SHAPE_BOUND_DB, r     # and inside it against Figure 9


# ---------------------------------------------------------------------------
# the choice is an enumeration over the circuit, not a fit
# ---------------------------------------------------------------------------
def test_every_chosen_pole_is_a_pole_of_the_network():
    ch = tn.chosen()
    net = tn.network_poles(tn.alpha_of(tn.ANCHOR, tn.config()))
    for band, v in ch.items():
        for p in v["poles"]:
            assert min(abs(p - q) for q in net) <= tn.POLE_TOL_HZ, (band, p, net)


def test_revision_3s_short_band_pole_is_NOT_a_pole_of_the_network():
    """1511.2 Hz comes from Figure 9's 2-pole window fit of Ht3. The network's
    nearest pole is 1625.4 Hz. Step 10 replaces it for that reason and not
    because it reads better -- it reads 0.45 dB WORSE (1.34 -> 1.79)."""
    net = tn.network_poles(0.5)
    assert min(abs(1511.2 - q) for q in net) > tn.POLE_TOL_HZ
    cfg = tn.config()
    assert tn.rev3_realisation("short", cfg)["worst_nodal_db"] < \
        tn.chosen(cfg)["short"]["worst_db"]


def test_asymptotic_poles_are_inadmissible_for_the_band_they_cannot_shape():
    """Over 6.3-16 kHz the network's four sub-2 kHz poles read within 0.09 dB of
    each other, so choosing among them would be fitting."""
    assert not tn.admissible("short", 130.0)
    assert not tn.admissible("short", 1625.4)
    assert tn.admissible("short", 4219.0)
    assert tn.admissible("low", 4219.0)
    assert tn.admissible("low", 1625.4)
    assert not tn.admissible("low", 488.6)
    spread = []
    cfg = tn.config()
    for p in (130.0, 488.6, 713.5, 1625.4):
        spread.append(tn.shape_error_db("short", 0.5, (p,), 1, cfg))
    assert max(spread) - min(spread) < 0.15, spread


def test_the_empty_realisation_is_always_admitted():
    """The admissibility rule must never be able to make a band unrealisable on
    its own -- step 5's 'the two stages cancel' choice is always on the list."""
    cfg = tn.config()
    for band in tn.BANDS:
        opts = tn.enumerate_realisations(band, 0.5, cfg)
        assert ((), tn.BASE_ZEROS[band]) in opts, band


def test_the_low_band_takes_the_networks_top_pole_and_one_new_section():
    ch = tn.chosen()
    assert ch["low"]["poles"] == [4219.0], ch["low"]
    assert ch["low"]["new_sections"] == 1
    assert ch["low"]["hp3_decode"] == 0          # no shared-decode change
    assert ch["low"]["worst_db"] < 1.0


def test_the_decay_band_keeps_the_empty_realisation_and_costs_nothing():
    ch = tn.chosen()
    assert ch["decay"]["poles"] == []
    assert ch["decay"]["new_sections"] == 0
    assert ch["decay"]["worst_db"] < tn.SHAPE_BOUND_DB


def test_choose_refuses_when_nothing_the_bank_can_hold_clears_the_bound():
    """Dropping the LEVEL stage from the target leaves a shape no admissible
    realisation reaches -- the refusal path, exercised."""
    with pytest.raises(tn.Refused):
        tn.chosen(tn.config(defect="NO_LEVEL_STAGE"))


# ---------------------------------------------------------------------------
# the budget, and the register map that bounds it
# ---------------------------------------------------------------------------
def test_mode_19_is_the_last_mode_the_register_map_can_address():
    """Contract 15.1: `A_RESET` is decoded BEFORE the mode range, so mode 19's
    `num` register (0xFF) is unwritable and mode 20 has no address at all."""
    assert dx.A_MODE + 19 * dx.MODE_STRIDE + 3 == dx.A_RESET
    assert dx.A_MODE + 20 * dx.MODE_STRIDE > 0xFF
    assert tn.MAP_LAST_MODE == 19


def test_mode_19_never_reads_its_numerator_so_the_unwritable_register_is_harmless():
    """`ModalFx.step` only decodes `num` for modes below N_NUMS."""
    assert 19 >= cc.N_NUMS
    bud = tn.budget()
    assert bud["last_mode"] == 19
    assert bud["last_mode_num_writable"] is False
    assert bud["last_mode_needs_num"] is False
    assert bud["addressable"] is True


def test_the_budget_is_exactly_one_more_mode_and_one_more_path():
    bud = tn.budget()
    assert (bud["modes"], bud["paths"], bud["nums"]) == (20, 25, 11)
    assert bud["new_sections"] == 1
    assert bud["modes"] <= bud["bank_pad"]


def test_a_second_new_section_would_not_be_addressable():
    """The margin is exactly zero: this realisation spends the last mode."""
    ch = {b: dict(v) for b, v in tn.chosen().items()}
    ch["decay"]["new_sections"] = 1               # pretend the decay band needed one too
    assert tn.budget(ch)["addressable"] is False


# ---------------------------------------------------------------------------
# the TONE gain table
# ---------------------------------------------------------------------------
def test_the_tone_gain_is_zero_at_the_anchor_by_construction():
    g = tn.tone_gain_db()
    assert all(v == 0.0 for v in g[tn.ANCHOR].values()), g[tn.ANCHOR]


def test_tone_mainly_attenuates_the_third_band():
    """W14b §7's own words, as a quantity: the short band's span over the knob
    must dwarf the other two -- but "mainly" is not "only", and §7 says so
    ("also shifts the others... weakly-separated, non-orthogonal controls").

    Wrong-then-right 1 of step 10: this first asserted 10x and was
    unsatisfiable. The measured spans are 51.1 dB (short) against 8.3 (low) and
    7.4 (decay) -- a ratio of 6.2, not 10. Caught by running the gate before
    committing it, which is the whole point of that rule.
    """
    g = tn.tone_gain_db()
    span = {b: max(g[c][b] for c in tn.CODES) - min(g[c][b] for c in tn.CODES)
            for b in tn.BANDS}
    assert span["short"] > 5 * max(span["low"], span["decay"]), span
    assert span["short"] > span["low"] + span["decay"], span
    # ...and the other two are NOT zero: the controls really are non-orthogonal.
    assert span["low"] > 3.0 and span["decay"] > 3.0, span


def test_the_short_band_rises_monotonically_with_tone():
    seq = [tn.tone_gain_db()[c]["short"] for c in tn.CODES]
    assert seq == sorted(seq), seq
    assert all(b > a for a, b in zip(seq, seq[1:])), seq


def test_inverting_the_wiper_is_blind_at_the_anchor_and_caught_elsewhere():
    """1 - 0.5 = 0.5, so the anchor maps to itself: every anchored quantity is
    blind to an inverted law. That blindness is why `short-band-monotone`
    exists as a separate property, and it must catch it."""
    g = tn.tone_gain_db(tn.config(defect="ALPHA_INVERTED"))
    assert all(v == 0.0 for v in g[tn.ANCHOR].values())
    p = tn.properties(tn.config(defect="ALPHA_INVERTED"))
    assert not p["short-band-monotone"]["ok"]


# ---------------------------------------------------------------------------
# the gate itself
# ---------------------------------------------------------------------------
def test_the_gate_passes_on_the_current_tree():
    """CLAUDE.md: run a gate against the current state before committing it.
    An unsatisfiable gate is worse than no gate."""
    ok, lines = tn.check()
    assert ok, "\n".join(lines)


def test_every_injected_defect_turns_at_least_one_property_red():
    for d in tn.DEFECTS:
        p = tn.properties(tn.config(defect=d))
        red = [n for n in tn.PROPERTIES if not p[n]["ok"] and not p[n]["undecided"]]
        assert red, f"{d} turned no property red -- a control that cannot fail"


def test_the_blind_pairs_are_verified_blind():
    for defect, prop in tn.BLIND_PAIRS:
        p = tn.properties(tn.config(defect=defect))
        assert p[prop]["ok"], f"{defect} was asserted blind to {prop} and moved it"


def test_a_refused_realisation_is_reported_undecided_not_red():
    """Four properties read off the chosen realisation. When `chosen` refuses
    they cannot be evaluated, and counting them as catches would overstate the
    control matrix fourfold."""
    p = tn.properties(tn.config(defect="NO_LEVEL_STAGE"))
    for n in ("poles-from-network", "poles-in-band", "one-register-set",
              "budget-addressable"):
        assert p[n]["undecided"], n
    assert p["chosen-in-bound"]["undecided"] is None
    assert not p["chosen-in-bound"]["ok"]


def test_the_record_is_serialisable_and_carries_its_bounds():
    import json
    rec = tn.record()
    blob = json.loads(json.dumps(rec, default=float))
    assert blob["bounds"]["shape_db"] == 3.0
    assert blob["anchor"] == "50"
    assert blob["budget"]["modes"] == 20
    assert set(blob["tone_gain_db"]) == set(tn.CODES)
