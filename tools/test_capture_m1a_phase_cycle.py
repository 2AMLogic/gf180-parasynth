"""Known answers for the phase-cycle capture's precondition checks.

Each apparatus failure this repository has already met is synthesised here and
must REFUSE: demo clicks, a dropout, digital silence, a sub-audio octave, an
isolated oscillator whose level moves. A clean held saw must pass. These run
without the plugin."""
import math

import numpy as np
import pytest

import capture_m1a_phase_cycle as c

SR = c.ref.SR
ON = .1
F36 = 440 * 2 ** ((36 - 69) / 12)


def saw(hz, amp=.1, seconds=None, partials=24):
    n = round((seconds or c.seconds_for(ON)) * SR)
    t = np.arange(n) / SR
    x = sum(np.sin(2 * np.pi * k * hz * t) / k for k in range(1, partials + 1) if k * hz < SR / 2)
    gate = (t >= ON) & (t < ON + c.GATE_S)
    return amp * x * gate


def test_clean_isolated_oscillators_pass():
    ev1 = c.check_take(saw(F36), SR, ON, "osc1_open")
    assert abs(ev1["cents_from_midi"]) < .1 and ev1["transient_events"] == 0
    ev2 = c.check_take(saw(2 * F36 * 2 ** (-3.49 / 1200)), SR, ON, "osc2_open")
    assert ev2["cents_from_octave"] == pytest.approx(-3.49, abs=.2)


def test_clicks_refused():
    x = saw(F36)
    rng = np.random.default_rng(1)
    for t in (3.3, 7.1):
        i = round(t * SR)
        x[i:i + 12] += .03 * rng.standard_normal(12)
    with pytest.raises(c.Refused, match="transient"):
        c.check_take(x, SR, ON, "full")


def test_dropout_refused():
    x = saw(F36)
    x[round(5 * SR):round(5.002 * SR)] = 0.
    with pytest.raises(c.Refused, match="exact zeros"):
        c.check_take(x, SR, ON, "full")


def test_silence_refused():
    with pytest.raises(c.Refused, match="silent"):
        c.check_take(np.zeros(round(c.seconds_for(ON) * SR)), SR, ON, "full")


def test_sub_audio_octave_refused():
    with pytest.raises(c.Refused, match="MIDI 36|below the commanded"):
        c.check_take(saw(F36 / 2), SR, ON, "osc1_open")
    with pytest.raises(c.Refused, match="octave|below the commanded"):
        c.check_take(saw(F36), SR, ON, "osc2_open")


def test_moving_isolated_level_refused():
    x = saw(F36)
    t = np.arange(len(x)) / SR
    x = x * (1 + .1 * np.sin(2 * np.pi * .3 * t))      # 1.7 dB wobble
    with pytest.raises(c.Refused, match="level moves"):
        c.check_take(x, SR, ON, "osc1_open")
    c.check_take(x, SR, ON, "full")                    # the full patch may beat


def test_isolated_sum_residual_known():
    a, b = saw(F36), saw(2 * F36, .05)
    assert c.sum_residual_db(a + b, a, b, ON, SR) < -200
    assert c.sum_residual_db(a + b + .01 * a, a, b, ON, SR) == pytest.approx(
        20 * math.log10(.01 * np.sqrt(np.mean(a[round(1.1 * SR):] ** 2)) /
                        np.sqrt(np.mean((a + b)[round(1.1 * SR):] ** 2))), abs=.3)


def test_expected_settings_follow_the_frozen_record():
    import json
    manifest = json.loads((c.FROZEN / "manifest.json").read_text())
    assert c.expected_settings(manifest, "full")["filter_cutoff"][2] == .5
    assert c.expected_settings(manifest, "osc1_open")["osc2_level"][2] == 0.
    both = c.expected_settings(manifest, "both_open")
    assert both["filter_cutoff"][2] == 1. and both["osc2_level"][2] == .3 and both["filter_contour"][2] == 0.


def test_slow_psi_cycle_swell_is_not_a_click_but_clicks_on_it_are():
    # the full patch's high band swells ~2 dB once per 3.8 s psi cycle; the
    # unmodified detector flagged exactly that (wrong-then-right)
    x = saw(F36, partials=16)           # a full patch: the 1 kHz filter leaves ~16 partials
    t = np.arange(len(x)) / SR
    swell = 1 + .25 * np.exp(-((((t - 1.9) % 3.8) - 1.9) / .2) ** 2)
    ev = c.check_take(x * swell, SR, ON, "full")
    assert ev["transient_events"] == 0
    unmodified = c.ri.transient_report((x * swell)[round(1.1 * SR):round(12.1 * SR)], SR,
                                       skip_s=0., block_ms=c.TRANSIENT_BLOCK_MS)
    assert unmodified["n_events"] >= 2          # the defect the detrend removes
    y = x * swell
    y[round(4.0 * SR):round(4.0 * SR) + 12] += .15 * np.random.default_rng(2).standard_normal(12)
    with pytest.raises(c.Refused, match="transient"):
        c.check_take(y, SR, ON, "full")
