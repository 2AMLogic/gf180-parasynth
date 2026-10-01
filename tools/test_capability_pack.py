#!/usr/bin/env python3
"""The capability-gated comparison pack (#136), with no plugin in the path.

    python -m pytest tools/test_capability_pack.py -q

TWO JOBS, KEPT APART -- and the split matters here more than usual.

**The gate.** `tools/capability_pack.py` exists to demonstrate that a pack
needing pitch, waveform and a sustained filter can run against Mini V3, whose
RIG-LEVEL verdict is False because of envelope timing alone. That is a claim
about the verdict table and it is tested as one: the pack must be cleared, the
same pack with `envelope_timing` added must be REFUSED, and the refusal must
name the capability.

**The measurements.** The gate passing says nothing about whether the pack
measures anything real, and a demonstration whose measuring half is a stub is a
demonstration of nothing. So `measure_oscillator` and `measure_filter_pair` are
exercised against CLOSED-FORM signals: a mathematical ramp and a 50 % rectangle
at a commanded note, and a one-pole at two corners an octave apart. Their answers
are known from arithmetic, not from this repository -- the distinction
`CLAUDE.md` draws when it says an estimator calibrated on our own model is not
validated.

The two halves also fail differently, which is the useful part. A broken gate
parks work that was never blocked (#122's failure). A broken measurement
publishes a number about a knob position. Only the first is visible by reading
the verdict table.
"""
from __future__ import annotations

import math
import pathlib
import sys

import numpy as np
import pytest

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT / "model"))
sys.path.insert(0, str(ROOT / "audition"))

import capability_pack as cp                                        # noqa: E402
import refprofile as rp                                             # noqa: E402
from dsp import SR, note_hz                                         # noqa: E402


# ===========================================================================
# closed-form signals. The answers come from arithmetic, not from this repo.
# ===========================================================================
#
# WRONG BEFORE IT WAS RIGHT, and the record is worth more than the fix.
#
# The first version of these generators used `note_hz(note)` directly. `saw` then
# identified at MIDI 36 and REFUSED at 48 and 60 -- "the record does not repeat at
# 130.8 Hz (per-period residual 5.7 % of the cycle)". That read exactly like a
# defect in `waveform_id` at higher pitches, and it is a defect in the STIMULUS:
# 48000 / 130.813 is 367.02 samples, so a naive ramp's discontinuity lands at a
# different sub-sample position every cycle and the record genuinely does not
# repeat. A real oscillator is bandlimited and has no such jitter.
#
# So the test signals are built at the nearest frequency with an INTEGER number
# of samples per period -- 0.3 cents off the note, which `refine_f0` accepts
# (its window is 50 cents) and which `measure_oscillator` reports as
# `cents_from_commanded`. Had the threshold been loosened instead, the suite
# would have "passed" while hiding that the estimator is sensitive to period
# jitter, which is the one thing Mini V3's +0.14 cents makes load-bearing here.
def integer_period_hz(note: int, sr: int = SR) -> float:
    """The frequency nearest `note` whose period is a whole number of samples."""
    return sr / round(sr / float(note_hz(int(note))))


def _t(seconds=0.5, sr=SR):
    return np.arange(int(seconds * sr)) / sr


def saw(f0, seconds=0.5, amp=0.5, sr=SR):
    """A ramp: one jump per cycle, which is what `waveform_id` counts."""
    return amp * (2.0 * ((f0 * _t(seconds, sr)) % 1.0) - 1.0)


def rect(f0, duty=0.5, seconds=0.5, amp=0.5, sr=SR):
    """A rectangle at a stated duty: two jumps per cycle."""
    ph = (f0 * _t(seconds, sr)) % 1.0
    return amp * np.where(ph < duty, 1.0, -1.0)


def saw_at(note, **kw):
    return saw(integer_period_hz(note), **kw)


def rect_at(note, duty=0.5, **kw):
    return rect(integer_period_hz(note), duty=duty, **kw)


def one_pole(x, fc, sr=SR):
    """A one-pole low-pass with a KNOWN corner, so the centroid pair has a
    known direction. Not our ladder: the point is a filter nothing in this
    repository tuned."""
    a = math.exp(-2.0 * math.pi * fc / sr)
    y = np.zeros(len(x))
    s = 0.0
    for i, v in enumerate(x):
        s = a * s + (1.0 - a) * v
        y[i] = s
    return y


# ===========================================================================
# the gate
# ===========================================================================
def test_the_pack_consumes_exactly_the_three_capabilities_it_claims():
    assert cp.MINIV3_PACK.requires == ("pitch", "waveform", "sustained_filter")
    for cap in cp.MINIV3_PACK.requires:
        assert cap in rp.CAPABILITIES
    assert "envelope_timing" not in cp.MINIV3_PACK.requires, \
        "the whole point of this pack is that it does not need envelope timing"


def test_the_pack_is_cleared_against_a_rig_whose_overall_verdict_is_NO():
    """**#136's payoff as an executable fact.** Mini V3's rig-level verdict is
    False, it is not in `qualified_rigs()`, and this pack may still run against
    it -- which was unexpressible before the capability map and is why two
    waveforms at two pitches sat behind an envelope calibration."""
    pack = cp.MINIV3_PACK
    assert rp.RIG_VERDICTS[pack.rig]["qualified"] is False
    assert pack.rig not in rp.qualified_rigs()
    rows = cp.gate(pack)                                 # must not raise
    assert [r["capability"] for r in rows] == list(pack.requires)
    assert all(r["qualified"] is True for r in rows)
    assert all(r["why"] for r in rows), "a cleared capability still says why"


def test_the_same_pack_with_envelope_timing_added_is_REFUSED_by_capability():
    """The control that ships with the gate. Same rig, same host, one capability
    added: a gate nobody has watched reject anything is a gate nobody should
    trust."""
    with pytest.raises(rp.Refused) as e:
        cp.gate(cp.MINIV3_ENVELOPE_PACK)
    why = str(e.value)
    assert "envelope_timing" in why, why
    assert "NOT QUALIFIED" in why, why
    assert "pitch, waveform, sustained_filter" in why, \
        f"the refusal must say what the rig IS good for: {why}"


def test_the_gate_reports_the_capabilities_the_pack_does_not_use():
    """The finding, not a footnote: the pack runs while the rig's envelope timing
    reads NO, and the two causality axes read "no verdict" rather than NO."""
    rows = {r["capability"]: r["verdict"] for r in cp.not_required(cp.MINIV3_PACK)}
    assert rows["envelope_timing"] == "NO"
    assert rows["pitch_causality"] == "no verdict"
    assert rows["filter_causality"] == "no verdict"


def test_the_cli_separates_FAIL_from_REFUSED():
    """Three outcomes, three exit codes, and the two that are easy to conflate
    are the ones that matter: a gate rejection is FAIL (somebody has to change
    the pack or measure the capability) and a missing plugin host is REFUSED
    (nothing was attempted and the pack is fine)."""
    assert cp.main(["--list"]) == cp.OK
    assert cp.main(["--gate"]) == cp.OK
    assert cp.main(["--gate", "--pack", cp.MINIV3_ENVELOPE_PACK.name]) == cp.FAIL


def test_a_run_without_the_plugin_host_REFUSES_and_does_not_fail():
    """The answer on almost every host in this fleet, and it must not look like a
    broken pack. Skipped where dawdreamer IS installed, because there the
    precondition holds and this test has nothing to assert."""
    try:
        import dawdreamer                                       # noqa: F401
    except ImportError:
        pass
    else:
        pytest.skip("dawdreamer is installed here, so the refusal cannot fire")
    with pytest.raises(rp.Refused) as e:
        cp.build_rig(cp.MINIV3_PACK)
    why = str(e.value)
    assert "no plugin host" in why, why
    assert "REFUSED and not FAIL" in why, why
    assert cp.main(["--run"]) == cp.REFUSED


# ===========================================================================
# the measurements, on signals whose answers are arithmetic
# ===========================================================================
@pytest.mark.parametrize("note", [36, 48])
def test_a_mathematical_ramp_reads_back_as_a_saw_at_the_commanded_note(note):
    got = cp.measure_oscillator(saw_at(note), note)
    assert got["waveform"] == "saw", got
    assert got["f0_hz"] == pytest.approx(integer_period_hz(note), rel=1e-3), got
    assert abs(got["cents_from_commanded"]) < 1.0, got


@pytest.mark.parametrize("note", [36, 48])
def test_a_mathematical_rectangle_reads_back_with_its_duty_MEASURED(note):
    """"pulse:50.0%" and not "square": the duty is reported, never assumed. A
    rig at 47.9 % duty answering "square" is the shape of a published result that
    is wrong by a measurable amount nobody looked at."""
    got = cp.measure_oscillator(rect_at(note, duty=0.5), note)
    assert got["waveform"].startswith("pulse:"), got
    assert float(got["waveform"].split(":")[1].rstrip("%")) == pytest.approx(50.0, abs=1.5)


def test_an_off_duty_rectangle_is_not_rounded_to_fifty_percent():
    """The control for the test above: if the duty were assumed rather than
    measured, both of these would answer 50 %."""
    got = cp.measure_oscillator(rect_at(36, duty=0.35), 36)
    assert got["waveform"].startswith("pulse:"), got
    assert float(got["waveform"].split(":")[1].rstrip("%")) == pytest.approx(35.0, abs=2.0)


def test_the_two_waveforms_are_told_apart_at_both_pitches():
    """The pack's own discrimination matrix. Two waveforms at two pitches buys
    nothing if the pair is not separable at either."""
    for note in (36, 48):
        assert cp.measure_oscillator(saw_at(note), note)["waveform"] == "saw"
        assert cp.measure_oscillator(rect_at(note), note)["waveform"].startswith("pulse:")


def test_an_octave_down_record_is_refused_rather_than_mislabelled():
    """Mini V3's Range default answered MIDI 48 with 65.42 Hz, exactly half of
    130.81. The measurement must REFUSE a record that is not at the commanded
    note, because a waveform label attached to the wrong octave is a result."""
    got = cp.measure_oscillator(saw(integer_period_hz(48) / 2.0), 48)
    assert "refused" in got, got
    assert "waveform" not in got
    assert "fundamental" in got["refused"], got


def test_silence_is_refused_and_not_reported_as_a_waveform():
    got = cp.measure_oscillator(np.zeros(int(0.5 * SR)), 36)
    assert "refused" in got and "waveform" not in got


def test_the_closed_filter_position_measures_darker_than_the_open_one():
    """A one-pole at 3000 Hz and at 300 Hz on the same ramp. The direction is
    arithmetic; the only thing being checked is that the pair measurement reads
    it the right way round -- the failure that would make an open/closed pack
    report its own sign error as a finding about the rig.

    **The assertion is the DIRECTION and the ORDERING, not a magnitude.** The
    first version asserted `ratio > 2.0` and measured 1.74, and the honest reading
    of that is not "loosen it to 1.7": a power-weighted centroid over 20..20 kHz on
    a ramp sits near the fundamental whatever the corner is, so the absolute ratio
    is a property of the stimulus's spectrum as much as of the filter. A threshold
    picked after seeing 1.74 would be a number chosen to pass. What IS a property
    of the measurement is that a WIDER corner gap must read as a larger ratio, and
    that is asserted instead."""
    x = saw_at(36)
    wide = cp.measure_filter_pair(one_pole(x, 3000.0), one_pole(x, 300.0))
    narrow = cp.measure_filter_pair(one_pole(x, 3000.0), one_pole(x, 1500.0))
    assert wide["darker_when_closed"] is True, wide
    assert wide["centroid_open_hz"] > wide["centroid_closed_hz"], wide
    assert wide["centroid_ratio_open_over_closed"] > 1.0, wide
    assert (wide["centroid_ratio_open_over_closed"]
            > narrow["centroid_ratio_open_over_closed"] > 1.0), (wide, narrow)


def test_an_open_closed_pair_that_did_not_move_reports_a_ratio_of_one():
    """The control: a cutoff knob connected to nothing gives the same centroid
    twice. The pack must report a ratio of 1 rather than a number that looks like
    a measurement of a filter."""
    y = one_pole(saw_at(36), 1000.0)
    got = cp.measure_filter_pair(y, y)
    assert got["centroid_ratio_open_over_closed"] == pytest.approx(1.0, abs=1e-6)
    assert got["darker_when_closed"] is False


def test_measure_assembles_a_report_for_the_whole_pack_over_a_stub_rig():
    """The shipping `render`/`measure` path with a stub in place of the plugin, so
    the step wiring is exercised on every host. A pack whose step ids and
    measurement keys only line up on a machine with dawdreamer is a pack nobody
    can review."""
    class Stub:
        """Mini V3's two methods the pack uses, over closed-form audio."""
        def __init__(self):
            self.knob = 1.0

        def set_point(self, cut, res):
            self.knob = cut
            return cut

        def osc_tone(self, wave, note, seconds=0.5):
            x = saw_at(note) if wave == "saw" else rect_at(note)
            # the knob as a corner only for the stub's own sake: 1.0 open,
            # 0.30 nearly closed. Nothing is claimed about the real taper.
            return one_pole(x, 200.0 + 6000.0 * self.knob ** 2)

    res = cp.measure(cp.MINIV3_PACK, cp.render(cp.MINIV3_PACK, Stub()))
    assert res["rig"] == "miniv3" and res["host"] == "dawdreamer"
    assert res["requires"] == ["pitch", "waveform", "sustained_filter"]
    assert set(res["steps"]) == {"saw-36", "saw-48", "square-36", "square-48"}
    assert res["steps"]["saw-36"]["waveform"] == "saw"
    assert res["steps"]["square-48"]["waveform"].startswith("pulse:")
    assert res["filter_pair"]["darker_when_closed"] is True


def test_every_step_in_the_pack_says_what_it_is_for():
    """A step with no reason is a render nobody can interpret afterwards -- the
    same rule `refprofile`'s clips and `NOT_RUN`'s entries carry."""
    assert len(cp.MINIV3_PACK.steps) == 6
    for s in cp.MINIV3_PACK.steps:
        assert len(s.why) > 40, s
        assert s.kind in ("oscillator", "filter")
        if s.kind == "oscillator":
            assert s.wave and s.note is not None
        else:
            assert s.cutoff_knob is not None


def test_the_pack_crosses_its_two_axes_rather_than_sampling_a_diagonal():
    """Two waveforms at two pitches is four renders, not two. A diagonal (saw
    low, square high) cannot separate a pitch defect from a waveform defect."""
    osc = [(s.wave, s.note) for s in cp.MINIV3_PACK.steps if s.kind == "oscillator"]
    waves = {w for w, _ in osc}
    notes = {n for _, n in osc}
    assert len(waves) == 2 and len(notes) == 2
    assert set(osc) == {(w, n) for w in waves for n in notes}
