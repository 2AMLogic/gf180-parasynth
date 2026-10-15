"""Known answers for tools/perceptual_gate.py (#379): synthetic signals whose
answer is fixed by construction, the invariances the gate declares, and
controls that must move the feature they target. No corpus needed; the gate's
proof against the real 808 is `perceptual_gate.py prove` (docs/scorecard/gate-379/)."""
from __future__ import annotations

import math
import pathlib
import sys

import numpy as np
import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import perceptual_gate as g  # noqa: E402

SR = 48000


def strike(f=200.0, tau=0.15, sr=SR, dur=1.0, lead=0.02, chirp=0.0, noise=0.0, seed=0, beat=0.0):
    t = np.arange(int(dur * sr)) / sr
    ph = 2 * np.pi * (f * t + chirp * f * 0.03 * (1 - np.exp(-t / 0.03)))
    y = np.sin(ph)
    if beat:
        y = y + np.sin(2 * np.pi * (f + beat) * t)
    if noise:
        y = y + noise * np.random.default_rng(seed).standard_normal(len(t))
    y = y * np.exp(-t / tau)
    return np.concatenate([np.zeros(int(lead * sr)), y]), sr


def dist(sound, a, b):
    T = g.Target(*a, sound, "t")
    return T.distance(*b, "c")


def test_identical_is_zero():
    x = strike()
    d = dist("LT", x, x)
    for f, v in d.items():
        assert v is None or v < 1e-9, (f, v)


def test_gain_lead_and_rate_do_not_move_it():
    x, sr = strike(noise=0.05)
    y = np.concatenate([np.zeros(333), x * 0.3])
    d = dist("LT", (x, sr), (y, sr))
    assert d["spec"] < 1e-3 and d["decay"] < 0.05 and d["pitch"] < 0.5
    # the same strike synthesised at 44.1 kHz reads ~0 against the 48 kHz one
    z = strike(noise=0.0, sr=44100)
    d2 = dist("LT", strike(), z)
    assert d2["spec"] < 0.01 and d2["pitch"] < 1.0 and d2["attack"] < 0.2


def test_pitch_offset_reads_its_cents_and_not_shape():
    d = dist("LT", strike(200.0), strike(200.0 * 2 ** (100 / 1200)))
    assert d["pitch"] == pytest.approx(100.0, abs=3.0)
    assert d["pitch_shape"] < 10.0


def test_slide_reads_as_shape_not_offset():
    d = dist("LT", strike(200.0), strike(200.0, chirp=0.12))
    assert d["pitch_shape"] > 60.0
    assert d["pitch"] < 15.0


def test_decay_reads_a_doubled_tau_and_not_a_new_noise_draw():
    a = strike(f=3000.0, tau=0.10, noise=1.0, seed=1)
    same = strike(f=3000.0, tau=0.10, noise=1.0, seed=2)
    long = strike(f=3000.0, tau=0.20, noise=1.0, seed=2)
    d_same, d_long = dist("CP", a, same)["decay"], dist("CP", a, long)["decay"]
    assert d_same < 1.0
    assert d_long > 4.0


def test_click_moves_impulse():
    # the declared seed: one sample at half the peak where the hit is 20 dB down
    x, sr = strike(f=5000.0, tau=0.2, noise=0.5, seed=3)
    y, _ = g.seed(x, sr, "click", "SD", None)
    assert np.count_nonzero(y != x) == 1
    d = dist("SD", (x, sr), (y, sr))
    assert d["impulse"] > 6.0
    assert d["spec"] < 0.02                   # a summed spectrogram barely sees it


def test_brighter_moves_centroid_up():
    x, sr = strike(f=1000.0, noise=0.3, seed=4)
    y, _ = g.seed(x, sr, "brighter", "SD", None)
    T = g.Target(x, sr, "SD", "t")
    at, ao = T.a, g.analyse(g.condition(y, sr), T.plan)
    w = at["loud"] > 0
    assert np.mean(ao["centroid"][w]) > np.mean(at["centroid"][w]) + 0.5


def test_beating_moves_modulation():
    a = strike(f=2000.0, tau=0.4)
    b = strike(f=2000.0, tau=0.4, beat=40.0)
    assert dist("CY", a, b)["modulation"] > 3.0


def test_silence_and_a_cut_strike_refuse():
    with pytest.raises(g.Refused):
        g.condition(np.zeros(SR), SR)
    t = np.arange(SR) / SR
    cut = np.cos(2 * np.pi * 200 * t) * np.exp(-t / 0.1)     # begins AT its peak
    with pytest.raises(g.Refused):
        g.condition(cut, SR)


def test_verdict_is_per_feature_with_no_combined_score():
    bar = {"spec": 0.1, "decay": 2.0, "pitch": None}
    v = g.verdict({"spec": 0.05, "decay": 2.5, "pitch": None}, bar)
    assert v["verdict"] == "FAIL" and v["failing"] == ["decay"]
    assert v["worst_feature"] == "decay" and v["worst_ratio"] == pytest.approx(1.25)
    assert g.verdict({"spec": 0.1, "decay": 2.0}, bar)["verdict"] == "PASS"
    assert g.verdict({"spec": None, "decay": 1.0}, bar)["verdict"] == "REFUSED"


def test_neighbours_are_one_knob_step():
    assert g.neighbours("BD", "bd8/BD5050.WAV") == [
        "bd8/BD2550.WAV", "bd8/BD7550.WAV", "bd8/BD5025.WAV", "bd8/BD5075.WAV"]
    assert g.neighbours("CY", "cy8/CY5025.WAV") == [
        "cy8/CY2525.WAV", "cy8/CY7525.WAV", "cy8/CY5000.WAV", "cy8/CY5050.WAV"]
    assert g.neighbours("LT", "lt8/LT50.WAV") == ["lt8/LT25.WAV", "lt8/LT75.WAV"]
    assert g.neighbours("OH", "oh8/OH10.WAV") == ["oh8/OH75.WAV"]
    assert g.neighbours("CB", "cb8/CB.WAV") == []


def test_weak_ratio_is_the_measured_median_tuning_step():
    assert g.WEAK_R == pytest.approx(2 ** (105.4 / 1200))
    assert 1.0 < g.WEAK_R < 1.10          # inside reference 1.7's +-10 % f0 tolerance


@pytest.mark.parametrize("defect", g.DEFECTS)
def test_every_seed_changes_the_signal(defect):
    x, sr = strike(f=300.0, noise=0.1, seed=5)
    T = g.Target(x, sr, "LT", "t")
    y, ysr = g.seed(x, sr, defect, "LT", T)
    assert ysr != sr or not np.allclose(y[:len(x)], x)


@pytest.fixture
def inject():
    def _set(name):
        assert name in g.INJECTIONS
        g.INJECT.add(name)
    yield _set
    g.INJECT.clear()


def test_injection_pitch_rms_averages_a_slide_away(inject):
    a, b = strike(200.0, tau=0.4), strike(200.0, tau=0.4, chirp=0.12)
    assert dist("LT", a, b)["pitch_shape"] > 60.0
    inject("pitch-rms")
    assert dist("LT", a, b)["pitch_shape"] < 40.0


def test_injection_max_weights_reads_a_longer_ring_as_a_centroid_error(inject):
    a, b = strike(f=3000.0, tau=0.05, noise=0.2, seed=6), strike(f=3000.0, tau=0.15, noise=0.2, seed=6)
    clean = dist("CY", a, b)["centroid"]
    inject("centroid-max-weights")
    assert dist("CY", a, b)["centroid"] > 3 * clean + 0.5


def test_injection_target_span_hides_a_candidate_that_rings_on(inject):
    # identical until the target is gated off; the candidate rings on after
    b, sr = strike(f=3000.0, tau=0.3, noise=0.5, seed=7, dur=1.0)
    a = b.copy()
    a[int(0.12 * sr):] = 0.0
    clean = dist("MA", (a, sr), (b, sr))["spec"]
    assert clean > 0.5
    inject("span-target-only")
    assert dist("MA", (a, sr), (b, sr))["spec"] < 0.5 * clean


# --- #588: the rate path -----------------------------------------------------
def _take_44k(**kw):
    """A 44.1 kHz 'take' made the way Fischer's are: it has been through a
    resampler, so it carries resampler images a native 48 kHz render lacks."""
    x, sr = strike(**kw)
    return g.resample_to(x, sr, 44100), 44100


def test_match_rate_is_the_explicit_round_trip():
    # the fix is exactly "send the candidate through the target's own rate":
    # nothing is added to the signal, so it must equal doing that by hand.
    tgt = _take_44k(noise=0.05, seed=1)
    cand = strike(f=210.0, noise=0.05, seed=2)
    T = g.Target(*tgt, "LT", "t")
    a = T.distance(*cand, "c", match_rate=True)
    b = T.distance(g.resample_to(cand[0], cand[1], 44100), 44100, "c")
    for f in a:
        assert (a[f] is None and b[f] is None) or a[f] == pytest.approx(b[f], abs=1e-9), f


def test_match_rate_is_a_no_op_when_the_rates_already_agree():
    # 48 kHz target and a native 48 kHz candidate; a 44.1 kHz take against a
    # 44.1 kHz candidate. Neither may move.
    for tgt, cand in ((strike(noise=0.05, seed=1), strike(f=210.0, noise=0.05, seed=2)),
                      (_take_44k(noise=0.05, seed=1), _take_44k(f=210.0, noise=0.05, seed=2))):
        T = g.Target(*tgt, "LT", "t")
        a, b = T.distance(*cand, "c", match_rate=True), T.distance(*cand, "c")
        assert a == b


def test_match_rate_default_leaves_the_bar_inputs_alone():
    # bar_for / prove build their bar from candidate_path inputs with the
    # default; match_rate must be opt-in or every bar silently moves.
    import inspect
    assert inspect.signature(g.Target.distance).parameters["match_rate"].default is False


def test_a_genuine_body_click_still_fails_after_the_rate_fix():
    # harmful counterexample: matching the rate must not hide a real click.
    tgt = _take_44k()
    clean = strike()
    x, sr = clean
    bad = x.copy()
    bad[int(0.4 * sr)] += 0.5 * np.max(np.abs(x))
    T = g.Target(*tgt, "LT", "t")
    ok = T.distance(*clean, "ok", match_rate=True)["impulse"]
    clicked = T.distance(bad, sr, "click", match_rate=True)["impulse"]
    assert clicked > ok + 3.0, (clicked, ok)


def test_a_noise_floor_does_not_buy_a_better_match():
    # harmful counterexample: a candidate must not win by adding a floor. The
    # impulse crest may fall, but the spectrum and decay must still say so.
    tgt = _take_44k(noise=0.0)
    clean, noisy = strike(), strike(noise=0.02, seed=3)
    T = g.Target(*tgt, "LT", "t")
    dc, dn = T.distance(*clean, "c", match_rate=True), T.distance(*noisy, "n", match_rate=True)
    assert dn["spec"] > dc["spec"] and dn["decay"] >= dc["decay"], (dc, dn)
