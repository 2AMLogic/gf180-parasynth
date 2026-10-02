#!/usr/bin/env python3
"""Can the DC-blocker probe's own gates produce a verdict at all? (#165)

    python3 -m pytest tools/probes/test_dc_blocker_apparatus.py -q

**These are the injected-bug controls for `tools/probes/dc_blocker.py`'s
preservation gates, and every one of them was RED when it was written.** They
are in their own file, committed before the repair, so git history carries the
ordering rather than a claim about it.

WHY THIS FILE EXISTS. `dc_blocker.py --measure` returned, on the candidate read
off the machine's own coupling network:

    CY   improvement MET (-14.64 dB)   preservation: BROKE t20_ms_pct,centroid_pct
    BD   control, must be preserved:   BROKE attack_samp
    HT   control, must be preserved:   BROKE hf_5k_20k_db,attack_samp
    CH   control, must be preserved:   BROKE body_20_700_db

Four of those five failures are the INSTRUMENT, not the candidate -- a correct
filter read by gates in a wrong state, which is the second root cause
`CLAUDE.md` names. Each test below pins one of them:

  1. `centroid_pct` is a GLOBAL spectral centroid against a 1 % allowance.
     Removing sub-20 Hz energy raises a global centroid mechanically, so the
     gate reads the candidate's INTENDED EFFECT as a preservation failure. The
     closed-form two-tone qualification in `dc_centroid_gate_qualification.py`
     already established this (a 10 Hz contaminant moves the global centroid
     15.8 % while the 1 kHz component does not move at all) and told the next
     agent to rerun the measurement with the qualified definition. It was never
     wired in. Above 20 Hz the CY centroid moves -0.02 %, not +8.96 %.

  2. `attack_samp` is `argmax(|x|) - onset`, which on an oscillatory voice is a
     RANK ORDER between lobes, not a timing. The BD's four largest samples are
     EQUAL to four decimal places in dB; removing its -45-count standing offset
     flips which plateau wins and the gate reports a 460-sample (9.6 ms)
     attack change while `onset_index` does not move by one sample. The HT does
     the same across a 0.54 dB lobe margin.

  3 and 4. `hf_5k_20k_db` on the HT and `body_20_700_db` on the CH are read on
     bands at -102 and -110 dBFS. One LSB of dither -- the smallest change the
     int16 output can express -- moves them by 8.69 dB and 4.26 dB. The
     allowances are 0.20 and 0.30 dB, i.e. 43x and 14x BELOW the instrument's
     own resolution. "An unsatisfiable gate is worse than no gate" (CLAUDE.md);
     these two were failing a control voice on dither.

  5. `t20_ms_pct` carries a 3 % allowance and is computed on a 2 ms frame grid.
     Dither cannot see a quantised estimator's step, so the resolution of one
     must come from its step size and not from a perturbation.

The shape to notice: tests 1-4 turn a FALSE RED into a pass, and test 5 keeps a
TRUE RED red. A repair that only did the first four would be a repair tuned to
let the candidate through.

A CLAIM THIS FILE USED TO MAKE AND NO LONGER DOES, kept here because the
withdrawal is the more useful record (CLAUDE.md: publish the wrong-then-right
rate). It said the CY's -6.96 % and the RS's +225 % decay changes were REAL
failures the repair had to keep red, and it was wrong on both counts for one
reason: `t20_ms` integrated the WHOLE spectrum.

  * The CY's -6.96 % was a sub-20 Hz pedestal inside a backward energy
    integral, not an audible decay change. On the >= 20 Hz band the CY's decay
    moves -0.36 %. Qualified in closed form by
    `dc_t20_gate_qualification.py` (452 ms global against a constructed 200).
  * The RS's +225 % is real and clip-invariant, but it is the blocker's own
    21 ms sub-20 Hz undershoot tail -- the quantity `sub20_dbfs` reports -- and
    the >= 20 Hz gate has NO VERDICT on the RS at all: a brick wall at 20 Hz
    leaks across a clip on a voice that reaches exact silence 25 ms in, so the
    estimate tracks the clip length (598 / 1198 / 2398 ms at 0.60 / 1.20 /
    2.40 s). That is REFUSED, and a refusal is not a pass.

So the anti-rescue control moved to where it can be stated without the
instrument: `test_the_repaired_gate_still_sees_a_real_decay_regression` in
`test_dc_t20_gate_qualification.py` feeds the repaired estimator a genuinely
10 % faster decay and requires it to stay red. The three tests below hold the
instrument side.
"""
from __future__ import annotations

import pathlib
import sys

import numpy as np

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools" / "probes"))
sys.path.insert(0, str(ROOT / "model"))

import dc_blocker as P                               # noqa: E402
import drums_fx as dx                                # noqa: E402

SR = P.SR


def _pair(voice, k=dx.COUPLE_K):
    """The uncoupled and coupled renders of one voice, as the reports take
    them -- same instrument, same basis (DR 0015)."""
    b, nb = P.render(voice, dx.COUPLE_OFF)
    c, nc = P.render(voice, dx.COUPLE_BUS, k)
    return (b, nb), (c, nc)


# ---------------------------------------------------------------------------
# 1. The centroid gate reads the candidate's intended effect
# ---------------------------------------------------------------------------
def test_the_centroid_gate_judges_above_the_band_it_is_removing():
    """RED BEFORE THE REPAIR. The 1 % centroid allowance must be read on the
    band it is protecting -- the audible one -- not on a global centroid that
    a DC blocker moves BY DESIGN.

    Ground truth is independent of this model and already committed:
    `dc_centroid_gate_qualification.py` shows a 10 Hz contaminant moving the
    global centroid 15.8 % while the >= 20 Hz centroid stays exactly at the
    1 kHz tone. This test is the same claim on the real CY render."""
    (b, _), (c, _) = _pair("CY")
    glob = 100.0 * (P.centroid_global_hz(c) / P.centroid_global_hz(b) - 1.0)
    qual = 100.0 * (P.centroid_hz(c) / P.centroid_hz(b) - 1.0)
    assert glob > 5.0, glob          # the global centroid really does move
    # (+8.96 % at the 0.60 s clip these records used to be taken at, +7.03 % at
    #  2.40 s -- the magnitude depends on how much silence the clip carries,
    #  which is itself a reason a GLOBAL centroid is a poor gate)
    assert abs(qual) < 0.10, qual    # and the audible band really does not
    # ...so the gate, read the qualified way, must not fire on the CY:
    m = P.measure(c)
    base = P.measure(b)
    assert abs(P.deltas(base, m)["centroid_pct"]) < 0.10


# ---------------------------------------------------------------------------
# 2. attack_samp answered a tie-break, not a timing
# ---------------------------------------------------------------------------
def test_the_attack_gate_does_not_answer_a_tie_between_equal_lobes():
    """RED BEFORE THE REPAIR. On the BD the four largest samples are equal, so
    `argmax` is choosing arbitrarily among them. The onset is unmoved and the
    waveform's envelope is unmoved; only the RANK of two plateaus half a
    period apart changed. An attack estimator must not report that as a
    460-sample attack change on a CONTROL voice."""
    for v, want in (("BD", 20.0), ("HT", 20.0)):
        (b, _), (c, _) = _pair(v)
        assert P.onset_index(b) == P.onset_index(c), v    # the onset did not move
        d = abs(P.attack_samples(c) - P.attack_samples(b))
        assert d <= want, (v, d, "attack moved where the onset did not")


def test_the_peak_is_ambiguous_and_the_probe_says_so():
    """The precondition, asserted at the point of use. `peak_margin_db` is how
    far the largest lobe leads the runner-up; where that margin is below the
    tolerance the attack is measured at, the attack is a rank order and the
    probe must REFUSE rather than answer.

    Measured on the current renders: BD 0.000 dB (an exact plateau), HT
    0.535 dB, RS 0.190 dB, CY 1.654 dB. The repaired estimator reads the FIRST
    arrival within `ATTACK_TOL_DB` of the peak precisely so that a flipped rank
    inside that band cannot move the answer."""
    margins = {v: P.peak_margin_db(P.render(v, dx.COUPLE_OFF)[0])
               for v in ("CY", "RS", "BD", "HT", "CH")}
    assert margins["BD"] < 0.01, margins          # an exact tie
    assert margins["HT"] < 1.0, margins           # inside the tolerance
    assert margins["CY"] > 1.0, margins           # genuinely resolved
    # The tolerance has to be above the ambiguity it exists to absorb, and
    # this is swept in `report_resolution` rather than asserted once.
    assert P.ATTACK_TOL_DB > margins["HT"], (P.ATTACK_TOL_DB, margins["HT"])


# ---------------------------------------------------------------------------
# 3 and 4. Gates below the instrument's own resolution
# ---------------------------------------------------------------------------
def test_a_band_at_the_noise_floor_is_refused_not_failed():
    """RED BEFORE THE REPAIR. The HT's 5-20 kHz band is at -102 dBFS and the
    CH's 20-700 Hz band at -110 dBFS. One LSB of dither moves them 8.69 dB and
    4.26 dB, so neither the declared 0.20/0.30 dB allowance nor the candidate's
    +0.30/-0.75 dB reading means anything. The probe must mark the gate
    RESOLUTION-LIMITED and not spend a control voice's verdict on dither."""
    res = {v: P.resolution(v) for v in ("HT", "CH")}
    assert res["HT"]["hf_5k_20k_db"] > 1.0, res["HT"]
    assert res["CH"]["body_20_700_db"] > 1.0, res["CH"]
    for v, prop in (("HT", "hf_5k_20k_db"), ("CH", "body_20_700_db")):
        (b, nb), (c, nc) = _pair(v)
        d = P.deltas(P.measure(b, nb), P.measure(c, nc))
        bad, _ = P.preserved(v, d, res[v])
        assert prop not in bad, (v, prop, d[prop], res[v][prop])


def test_a_resolution_limited_gate_is_reported_as_such_and_not_hidden():
    """The other half: widening a gate to its resolution is only honest if the
    report SAYS the gate was widened. A silent widening is how a candidate gets
    through a gate nobody is reading any more."""
    res = P.resolution("HT")
    (b, nb), (c, nc) = _pair("HT")
    d = P.deltas(P.measure(b, nb), P.measure(c, nc))
    _, limited = P.preserved("HT", d, res)
    assert "hf_5k_20k_db" in limited, limited


def test_the_resolution_of_a_quantised_estimator_is_its_step_not_its_jitter():
    """RED BEFORE THE REPAIR. T20 is read on a 2 ms frame grid, so dither moves
    it by exactly 0 and a naive perturbation study would call a 3 % allowance
    satisfiable on a voice whose whole decay is four frames. The RS's T20 is
    8 ms; one frame is 25 %.

    The repair must take the quantisation step into the resolution. If it did
    not, this would stay the unsatisfiable gate CLAUDE.md warns about."""
    res = P.resolution("CH")
    assert res["t20_ms_pct"] > 3.0, res           # one frame of a 48 ms decay
    assert P.resolution("CY")["t20_ms_pct"] < 1.0, P.resolution("CY")


def test_the_decay_gate_refuses_the_rimshot_rather_than_answering():
    """**The withdrawn claim, pinned as a refusal.** The >= 20 Hz decay estimate
    on the RS tracks the clip's length, so it is not a decay and the gate must
    REFUSE. A refusal is a third outcome: it must NOT appear as a preservation
    failure (that would be inventing a result) and it must NOT be silent."""
    refuses, short_ms, long_ms = P.decay_gate_refuses("RS")
    assert refuses, (short_ms, long_ms)
    # it tracks the doubling, which is what makes it the clip and not the voice.
    # At 4.80 s it does not fall 20 dB at all and the estimator returns nan,
    # which is the same refusal arriving by the other route.
    assert np.isnan(long_ms) or long_ms / short_ms > 1.8, (short_ms, long_ms)
    (b, nb), (c, nc) = _pair("RS")
    d = P.deltas(P.measure(b, nb), P.measure(c, nc))
    bad, _ = P.preserved("RS", d, P.resolution("RS"), P._refused_props("RS"))
    assert "t20_ms_pct" not in bad, bad          # not a failure
    assert P._refused_props("RS") == ("t20_ms_pct",)   # and not silent


def test_the_rimshot_sub20_undershoot_is_real_and_reported_as_improvement_not_decay():
    """The +225 % was not imaginary -- it is the blocker's own 21 ms sub-20 Hz
    undershoot tail, and the clip-invariance of the GLOBAL estimate is what says
    so. It belongs in the sub20 column, which is where it now is."""
    (b, nb), (c, nc) = _pair("RS")
    base, cand = P.measure(b, nb), P.measure(c, nc)
    d = P.deltas(base, cand)
    assert d["t20_global_pct"] > 100.0, d["t20_global_pct"]
    # and the sub-20 Hz column shows the energy that tail is made of: the
    # blocker removes LESS than its steady-state bound because of it.
    assert d["sub20_dbfs"] < 0.0, d["sub20_dbfs"]
    assert d["sub20_dbfs"] > -P.IMPROVE_SUB20_DB, d["sub20_dbfs"]


def test_the_cymbal_decay_was_truncated_at_the_clip_length_records_used_to_use():
    """The other half of the withdrawal: a Schroeder integral normalised by the
    energy inside the clip reads SHORT on a voice still ringing at the end. The
    CY's own T20 is 23 % longer at the clip length the records now use, and the
    only reason the old number looked stable is that nobody doubled the clip."""
    short, _ = P.render("CY", dx.COUPLE_OFF, seconds=0.60)
    long, _ = P.render("CY", dx.COUPLE_OFF, seconds=P.RENDER_S)
    t_short, t_long = P.t20_band_ms(short), P.t20_band_ms(long)
    assert t_long > 1.2 * t_short, (t_short, t_long)
    assert not P.decay_gate_refuses("CY")[0]     # and at RENDER_S it is stable


def test_a_refusal_cannot_hide_a_real_decay_failure_on_a_voice_that_resolves():
    """The control for the refusal machinery. On a voice whose decay gate DOES
    produce a verdict, a 10 % decay change must still be caught -- a refusal
    must remove one voice's gate, never the gate."""
    assert not P.decay_gate_refuses("CY")[0]
    d = {"peak_dbfs": 0.0, "body_20_700_db": 0.0, "mid_700_5k_db": 0.0,
         "hf_5k_20k_db": 0.0, "t20_ms_pct": -10.0, "attack_samp": 0.0,
         "centroid_pct": 0.0, "n_clip": 0.0}
    bad, _ = P.preserved("CY", d, P.resolution("CY"), P._refused_props("CY"))
    assert bad == ["t20_ms_pct"], bad


# ---------------------------------------------------------------------------
# 5. The resolution study is itself validated
# ---------------------------------------------------------------------------
def test_the_resolution_study_finds_nothing_where_there_is_nothing():
    """The control for the control. A gate on a band carrying real energy must
    NOT come back resolution-limited -- otherwise the widening would swallow
    every gate and the suite would be green by construction. The CY's own
    5-20 kHz band is at -34 dBFS and must resolve far better than its 0.20 dB
    allowance."""
    res = P.resolution("CY")
    assert res["hf_5k_20k_db"] < 0.02, res
    assert res["body_20_700_db"] < 0.05, res
    assert res["peak_dbfs"] < 0.01, res
    assert res["centroid_pct"] < 0.01, res
    # and so a CY HF change above the allowance would still be caught:
    d = {"peak_dbfs": 0.0, "body_20_700_db": 0.0, "mid_700_5k_db": 0.0,
         "hf_5k_20k_db": 0.5, "t20_ms_pct": 0.0, "attack_samp": 0.0,
         "centroid_pct": 0.0, "n_clip": 0.0}
    bad, limited = P.preserved("CY", d, res)
    assert bad == ["hf_5k_20k_db"], (bad, limited)
    assert "hf_5k_20k_db" not in limited


def test_one_lsb_is_the_smallest_change_the_block_can_express():
    """What makes 1 LSB the right perturbation, rather than a number chosen to
    get a convenient answer: the output is int16 (contract 12), so no design
    change can move a sample by less than one count. A gate that cannot tell
    the candidate from a 1-LSB reshuffle of the baseline cannot attribute what
    it reads to the candidate."""
    b, _ = P.render("CY", dx.COUPLE_OFF)
    assert np.asarray(b).dtype == np.int16, np.asarray(b).dtype
    rng = np.random.default_rng(0)
    y = np.clip(np.asarray(b, np.int64) + rng.integers(-1, 2, len(b)), -32768, 32767)
    assert float(np.abs(y - np.asarray(b, np.int64)).max()) == 1.0
