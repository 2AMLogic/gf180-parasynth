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
     The RS's T20 is 8 ms -- four frames -- so one frame is 25 %. Dither cannot
     see this (the quantisation hides it: the dithered spread is exactly 0), so
     the resolution of a quantised estimator must come from its step size, not
     from a perturbation. The RS's measured +225 % still exceeds that, so this
     one failure IS real, and the repair must keep it red.

The shape to notice: tests 1-4 turn a FALSE RED into a pass, and test 5 keeps a
TRUE RED red. A repair that only did the first four would be a repair tuned to
let the candidate through.
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
    assert glob > 8.0, glob          # the global centroid really does move
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
    res = P.resolution("RS")
    assert res["t20_ms_pct"] > 20.0, res          # one frame of an 8 ms decay
    assert P.resolution("CY")["t20_ms_pct"] < 1.0, P.resolution("CY")


def test_the_true_rimshot_decay_failure_survives_the_repair():
    """**The repair must not rescue the candidate.** The RS's T20 really does
    go from 8 ms to 26 ms under a 7.46 Hz coupling -- the blocker's own 21 ms
    undershoot tail -- and +225 % is nine times the 25 % the instrument can
    resolve. Widening the gate to the resolution must leave this red.

    This is the test that distinguishes a repair from a tuning."""
    res = P.resolution("RS")
    (b, nb), (c, nc) = _pair("RS")
    d = P.deltas(P.measure(b, nb), P.measure(c, nc))
    assert d["t20_ms_pct"] > 100.0, d["t20_ms_pct"]
    bad, _ = P.preserved("RS", d, res)
    assert "t20_ms_pct" in bad, (bad, d["t20_ms_pct"], res["t20_ms_pct"])


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
