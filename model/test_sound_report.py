#!/usr/bin/env python3
"""The injection machinery of `model/sound_report.py`, checked cheaply.

    .venv/bin/python -m pytest model/test_sound_report.py -q

**Why this file exists separately from `make controls`.** A control counts as
caught only if all three of these hold (docs/verification-rules.md 5):

  1. the clean run passes;
  2. the mutant **builds, activates and actually executes**;
  3. the intended assertion is the one that fails.

`make controls` runs the two historical-bug injections end to end and so
covers 1 and 3 -- but each of those is about ninety seconds of rendering, and
neither of them can distinguish "the patch was applied and the defect is
invisible" from "the patch silently did nothing." Condition 2 is the one this
repository has actually got wrong: it once shipped a negative control that
mutated a function signature into invalid Python and passed, proving only that
Python rejects syntax errors.

So this file asserts condition 2 directly and in under a second: that each
injection's `make` really does change the function the report will call, that
the changed function returns the historically broken answer rather than merely
being a different object, and that `run()`'s restore actually restores -- a
patch that leaked would make every LATER property in the same process wrong
while the report still printed clean numbers.
"""
from __future__ import annotations

import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import audio_measure as am                                          # noqa: E402
import drum_verify as dv                                            # noqa: E402
import sound_report as sr                                           # noqa: E402

SR = sr.SR


# ===========================================================================
# the registry: the historical-bug injections exist and declare real voices
# ===========================================================================
HISTORICAL = ("bd-ma-envelope", "sd-centroid-amp-weighted")


def test_the_historical_bug_injections_are_registered():
    """docs/verification-rules.md 5 -- a bug is not closed until it is an
    injection. These two are the measurement-layer counterparts of
    verify_ctl.py's SPI_ADDR7 / SPI_DATA24, which replay the exact broken SPI
    frame that shipped."""
    for name in HISTORICAL:
        assert name in sr.INJECTIONS, f"{name} is not registered; make controls would not run it"
        desc, voices, make = sr.INJECTIONS[name]
        assert desc.strip(), f"{name} has no description"
        assert voices, f"{name} declares no voice, so cmd_inject has nothing to check"
        assert callable(make)


def test_every_injection_declares_voices_the_report_actually_measures():
    """A defect declared against a voice no property covers reports `NO
    PROPERTY MOVED` forever -- an unsatisfiable control, which is worse than
    no control. Checked over the whole registry, not just the new pair."""
    measured = {p.voice for p in sr.build_properties()}
    for name, (_, voices, _) in sr.INJECTIONS.items():
        missing = [v for v in voices if v not in measured]
        assert not missing, f"{name} touches {missing}, which no property in build_properties() measures"


# ===========================================================================
# condition 2: the mutant activates, executes, and is undone
# ===========================================================================
def test_the_moving_average_injection_really_replaces_drum_verify_envelope():
    """`bd-ma-envelope` reinstates the 5 ms moving average that shipped as an
    envelope estimator. audio_measure.py rule 1: a 5 ms window spans 0.28 of a
    cycle at 56 Hz, so it ripples at the carrier rather than tracking the
    decay.

    This asserts the patched `dv.envelope` returns the *historically broken*
    answer -- numerically equal to `moving_average_envelope` and numerically
    different from the tuned per-voice RMS window -- not merely that some
    attribute was rebound. A patch installed as a pass-through would satisfy
    'the attribute changed' and change nothing the report can see."""
    _, _, make = sr.INJECTIONS["bd-ma-envelope"]
    ctx = {"_patches": []}
    original = dv.envelope

    t = np.arange(int(0.4 * SR)) / SR
    x = np.exp(-t / 0.10) * np.sin(2 * np.pi * 56.0 * t)
    clean = original(x, SR)

    make(ctx)
    try:
        assert dv.envelope is not original, "the injection did not rebind drum_verify.envelope at all"
        got = dv.envelope(x, SR)
        want = np.abs(am.moving_average_envelope(x, 5.0, SR))
        assert np.allclose(got, want), \
            "the patched envelope is not the 5 ms moving average this injection claims to reinstate"
        # And it is a real change, not a re-spelling of the correct estimator.
        rel = np.max(np.abs(got - clean)) / max(np.max(clean), 1e-12)
        assert rel > 0.05, \
            f"the patched envelope is within {rel:.3%} of the correct one; this mutant does not mutate"
    finally:
        for mod, attr, orig in ctx["_patches"]:
            setattr(mod, attr, orig)

    assert dv.envelope is original, "the injection leaked: drum_verify.envelope was not restored"


def test_the_moving_average_injection_ripples_at_the_carrier_it_cannot_resolve():
    """The mechanism, not just the identity: 5 ms holds 0.28 of a cycle at
    56 Hz, so the estimate swings with the waveform instead of tracking the
    decay. BD's tuned window is 12 ms (`drum_verify.ENV_WIN_MS`), 0.67 of a
    cycle, and the RMS of a squared sine over that is far flatter.

    **A wrong-then-right record, kept because it is the point of the file.**
    The first version of this test compared against `dv.envelope(x, SR)` --
    the signature DEFAULT, 4 ms -- and failed, correctly: 4 ms is *shorter*
    than the injected 5 ms and ripples 7.50 dB against the moving average's
    6.55 dB, so the injection measured as an improvement. The default window
    is not the window the acceptance path uses; `dv.measure` selects a
    per-voice one, and comparing against anything else measures a function
    nobody calls. Same shape as every failure in docs/failure-modes.md: a
    correct instrument in a wrong state.

    Measured on this tree at 56 Hz: tuned 12 ms 1.85 dB, injected 5 ms
    moving average 6.55 dB."""
    _, voices, make = sr.INJECTIONS["bd-ma-envelope"]
    assert voices == ["BD"]
    win = dv.ENV_WIN_MS["BD"]
    ctx = {"_patches": []}
    original = dv.envelope

    t = np.arange(int(0.5 * SR)) / SR
    x = np.sin(2 * np.pi * 56.0 * t)                      # no decay at all

    def ripple_db(e):
        seg = e[int(0.1 * SR):int(0.4 * SR)]
        return 20.0 * np.log10(np.max(seg) / max(np.min(seg), 1e-12))

    clean_db = ripple_db(original(x, SR, win_ms=win))
    make(ctx)
    try:
        broken_db = ripple_db(dv.envelope(x, SR, win_ms=win))
    finally:
        for mod, attr, orig in ctx["_patches"]:
            setattr(mod, attr, orig)

    assert clean_db < 3.0, (
        f"the tuned {win:g} ms window itself rippled {clean_db:.2f} dB on a steady 56 Hz carrier; "
        "this test's apparatus is the comparison, so a noisy baseline invalidates it")
    assert broken_db > clean_db + 3.0, (
        f"the 5 ms moving average rippled {broken_db:.2f} dB against the tuned window's "
        f"{clean_db:.2f} dB on a steady 56 Hz carrier; it should be markedly worse, not comparable")


def test_the_moving_average_injection_shortens_the_decay_it_is_asked_to_measure():
    """And the consequence the report actually prints: a rippling envelope
    crosses -20 dB early, so T20 reads short. On a closed-form 100 ms
    exponential at 56 Hz, measured on this tree, the tuned 12 ms window
    recovers tau 100.4 ms / T20 209.6 ms at R^2 0.993; the injected 5 ms
    moving average gives T20 156.0 ms at R^2 0.882.

    The truth is known here (tau is 100 ms by construction), which is what
    separates this from the end-to-end `make controls` run: that one shows
    BD T20 moving 308 -> 207 ms on a real render, but the render has no
    independently known answer, so it can only show the number CHANGED. This
    shows it changed in the wrong DIRECTION, against a signal whose decay was
    chosen before it was measured."""
    _, _, make = sr.INJECTIONS["bd-ma-envelope"]
    win = dv.ENV_WIN_MS["BD"]
    ctx = {"_patches": []}
    original = dv.envelope

    tau_s = 0.10
    t = np.arange(int(0.5 * SR)) / SR
    x = np.exp(-t / tau_s) * np.sin(2 * np.pi * 56.0 * t)

    clean = dv.decay_fit(original(x, SR, win_ms=win), SR)
    make(ctx)
    try:
        broken = dv.decay_fit(dv.envelope(x, SR, win_ms=win), SR)
    finally:
        for mod, attr, orig in ctx["_patches"]:
            setattr(mod, attr, orig)

    assert abs(clean["tau_ms"] - tau_s * 1e3) < 5.0, (
        f"the tuned window recovered tau {clean['tau_ms']:.2f} ms from a signal built with "
        f"{tau_s * 1e3:.0f} ms; the baseline of this comparison is not trustworthy")
    assert broken["t20_ms"] < 0.8 * clean["t20_ms"], (
        f"the injected moving average read T20 {broken['t20_ms']:.1f} ms against the tuned window's "
        f"{clean['t20_ms']:.1f} ms; the historical defect makes decays read SHORT and this does not")
    assert broken["r2"] < clean["r2"] - 0.05, (
        f"the injected fit's R^2 is {broken['r2']:.4f} against {clean['r2']:.4f}; a rippling envelope "
        "should fit an exponential visibly worse, and if it does not, the ripple is not reaching the fit")


def test_the_centroid_injection_switches_which_field_the_brightness_property_reads():
    """`sd-centroid-amp-weighted` does not patch a function -- it sets
    `ctx["centroid_weight"]`, and `m_sd_brightness` reads the other field of
    the measurement `drum_verify.measure` already computes. So the thing to
    assert is that the property's OUTPUT changes, on a real snare render,
    rather than that a dict key was set.

    One render is shared: `_meas` caches on the ctx, so flipping the key and
    re-reading isolates the weighting from every other source of variation."""
    _, voices, make = sr.INJECTIONS["sd-centroid-amp-weighted"]
    assert voices == ["SD"]
    ctx = {"_patches": []}
    power = sr.m_sd_brightness(ctx)
    assert power is not None, "the snare rendered silent; this test has no apparatus"

    make(ctx)
    amplitude = sr.m_sd_brightness(ctx)
    assert amplitude is not None
    assert not ctx["_patches"], "this injection should not need to patch a module attribute"

    assert amplitude > 2.0 * power, (
        f"the amplitude-weighted centroid read {amplitude:.0f} Hz against the power-weighted "
        f"{power:.0f} Hz; docs/drum-verification.md 3 withdrew the amplitude weighting because it "
        "reads a wide quiet noise floor as brightness, so on the snare it must read far higher")


def test_the_brightness_property_is_locked_and_the_lock_is_the_power_weighted_one():
    """A property with no lock reports `no lock recorded yet` and passes
    whatever it measures -- which would make the centroid injection
    unsatisfiable however far it moved the number."""
    key = ("SD", "brightness (power centroid)")
    assert key in sr.LOCKS, "the brightness property has no lock, so nothing can be out of tolerance"
    props = {(p.voice, p.name): p for p in sr.build_properties()}
    assert key in props, "the lock names a property build_properties() does not create"
    p = props[key]
    assert p.kind == "lock"
    assert sr.LOCKS[key] < 3000.0, (
        f"the locked brightness is {sr.LOCKS[key]:.0f} Hz -- high enough to be the amplitude-weighted "
        "value, which would mean the lock was taken with the defect in place")
