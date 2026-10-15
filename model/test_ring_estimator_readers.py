#!/usr/bin/env python3
"""Every reader of a FREE ring's pitch goes through `ring_estimator` (issue #703).

    python3 -m pytest model/test_ring_estimator_readers.py -q

`ring_estimator.ring_frequency` is the one policy for what counts as a
measurement of a free ring (#548). These readers used to call
`am.dominant_frequency` directly, so a below-onset DEAD-BAND limit cycle (a
steady 6e-4 FS, 20 LSB, at 1131 Hz on the shipped filter) or a plain decay was
reported as an `f_osc` and flowed into tracking-error figures.

The controls are synthetic records whose answer is known by construction -- a
steady 6e-4 FS tone, an exponential decay, a sustained tone -- never the filter
being measured. Each routed call-site family must REFUSE the first two (a
refusal / `None`, never a frequency) and still answer the third.

Not routed, by reading (verification-rules rule 8 -- why no defeating input):
  * `rig_qualification.py` (`dominant_frequency` over the band of a commanded
    NOTE): a note-driven render, not a free ring.
  * `promoted_measures`, `measure_conga_body_spread`, `audio_measure`,
    `test_808_acceptance`, `test_moog_acceptance`: driven / struck voices.
    Assumed from names and docstrings -- skimmed, not traced.
  * `tools/measure_mono_m1a_reference.py`, `tools/measure_mono_m5a_reference.py`
    (Mini V3 free rings -- `_measure_open_cutoff`, M1A's three-ring phrase):
    STILL on `am.dominant_frequency`, deliberately. Both files are hashed into
    the attack qualification (`suite-v3.json` `sources_sha256`) and the
    attack-context analysis basis; editing them makes those records REFUSED
    as stale, and re-running them needs the Mini V3 rig this host lacks.
    Routing them is a follow-up that must regenerate those records.
"""
import math
import os
import sys

import numpy as np
import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "..", "audition"))
sys.path.insert(0, os.path.join(HERE, "..", "tools"))

import ladder_candidates as lc                                      # noqa: E402
import reference_compare as rc                                      # noqa: E402
import reference_integrity as ri                                    # noqa: E402
import reference_rigs as rr                                         # noqa: E402
import ring_estimator as re_                                        # noqa: E402
import sound_report as sr                                           # noqa: E402
from dsp import SR                                                  # noqa: E402



def _tone(f, seconds, amp):
    t = np.arange(int(seconds * SR)) / SR
    return amp * np.sin(2 * math.pi * f * t)


def dead_band(f=1131.0, seconds=0.8):
    """The fixed-point dead-band limit cycle: steady 6e-4 FS (20 LSB)."""
    return _tone(f, seconds, 6e-4)


def decay(f=400.0, seconds=0.8):
    """An ordinary below-onset decay: -70 dB across the window."""
    t = np.arange(int(seconds * SR)) / SR
    return 0.2 * np.exp(-t / 0.0115) * np.sin(2 * math.pi * f * t)


def sustained(f=1000.0, seconds=0.8):
    return _tone(f, seconds, 0.2)


BAD = [pytest.param(dead_band, id="dead-band"), pytest.param(decay, id="decay")]


def test_the_controls_are_what_they_claim_to_be():
    """The fixtures themselves: the estimator refuses both and answers the
    sustained one. Without this a control could 'pass' on a bad fixture."""
    for bad in (dead_band(), decay()):
        with pytest.raises(re_.Refused):
            re_.ring_frequency(bad, SR)
    assert abs(re_.ring_frequency(sustained(), SR) - 1000.0) < 1.0


@pytest.mark.parametrize("make", BAD)
def test_reference_compare_tracking_refuses(make):
    assert rc._tracking_f_osc(make()) is None


def test_reference_compare_tracking_still_answers_a_sustained_ring():
    assert abs(rc._tracking_f_osc(sustained()) - 1000.0) < 1.0


class _FakeDev:
    def __init__(self, ring_fn):
        self.ring_fn = ring_fn

    def ring(self, knob, res, seconds=0.5):
        return self.ring_fn(knob, seconds)


@pytest.mark.parametrize("make", BAD)
def test_calibrate_knob_refuses_instead_of_substituting_0_hz(make):
    dev = _FakeDev(lambda knob, seconds: make(seconds=seconds))
    with pytest.raises(re_.Refused):
        rc.calibrate_knob(dev, "miniv3", 800.0, res=0.98)


def test_calibrate_knob_still_bisects_a_sustained_ring():
    dev = _FakeDev(lambda knob, seconds: sustained(100.0 + 1900.0 * knob, seconds))
    knob = rc.calibrate_knob(dev, "miniv3", 800.0, res=0.98, iters=12)
    assert abs((100.0 + 1900.0 * knob) - 800.0) < 5.0


@pytest.mark.parametrize("make", BAD)
def test_ladder_candidates_cents_refuses(make):
    with pytest.raises(lc.Refused):
        lc._cents_of_ring(make(), 1131.0)


def test_ladder_candidates_cents_still_answers_a_sustained_ring():
    assert abs(lc._cents_of_ring(sustained(), 1000.0)) < 1.0


class _FakeLadder:
    def __init__(self, ring_fn):
        self.ring_fn = ring_fn

    def ring(self, cut, res, seconds=0.5):
        # the integrity fake's ring_fn takes (cut, res, seconds); the others (cut, seconds)
        try:
            return self.ring_fn(cut, seconds)
        except TypeError:
            return self.ring_fn(cut, res, seconds)


@pytest.mark.parametrize("make", BAD)
def test_sound_report_track_spread_is_none_on_a_non_ring(make, monkeypatch):
    fake = _FakeLadder(lambda cut, seconds: make(f=cut, seconds=seconds))
    monkeypatch.setattr(sr, "_ladder", lambda ctx: fake)
    assert sr.m_track_spread({}) is None


def test_sound_report_track_spread_still_answers_sustained_rings(monkeypatch):
    fake = _FakeLadder(lambda cut, seconds: sustained(cut * 1.01, seconds))
    monkeypatch.setattr(sr, "_ladder", lambda ctx: fake)
    spread = sr.m_track_spread({})
    assert spread is not None and spread < 0.5


@pytest.mark.parametrize("make", BAD)
def test_reference_integrity_tracking_refuses(make, monkeypatch):
    monkeypatch.setattr(rr, "OurLadder",
                        lambda: _FakeLadder(lambda cut, res, seconds: make(f=cut, seconds=seconds)),
                        raising=True)
    assert all(v is None for v in ri._tracking_once("ours", {}).values())


def test_reference_integrity_tracking_still_answers_sustained_rings(monkeypatch):
    class L:
        def ring(self, cut, res, seconds=0.8):
            return sustained(cut * 1.01, seconds)
    monkeypatch.setattr(rr, "OurLadder", L, raising=True)
    errs = ri._tracking_once("ours", {})
    assert all(v is not None and abs(v - 1.0) < 0.05 for v in errs.values()), errs
