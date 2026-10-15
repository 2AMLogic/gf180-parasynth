"""Known-answer qualification of tools/pitch_trajectory.py (#557).

Every signal here has a frequency law fixed by a CLOSED-FORM PHASE written in
this file, not by the estimator and not by the BD model:
    f(t) = f_inf + (f_0 - f_inf) exp(-t/tg)
    phi(t) = 2 pi [ f_inf t + (f_0 - f_inf) tg (1 - exp(-t/tg)) ]
The truth the estimator is scored against is `true_f(t)` from that formula.

Controls (verification rule 2/4):
  - FLAT_TRAJECTORY is the original defect (no glide: 50.3 -> 49.4 Hz).  A
    missing-glide signal is generated independently here and must read ~0 glide
    where the 58->50 Hz signal reads ~250 cents.
  - a constant tuning shift is a PERMITTED difference: offset moves, shape and
    glide do not.
  - `blind_estimator` (a mutant that returns the reference line for every
    frame) must fail the glide qualification -- so the glide tests are not
    decoration.
"""
from __future__ import annotations

import math
import pathlib
import sys

import numpy as np
import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import importlib, os  # noqa: E402

# START RED (rule 1): PT_IMPL=pitch_trajectory_stub runs this suite against a
# stub with the right names and no behaviour.
pt = importlib.import_module(os.environ.get("PT_IMPL", "pitch_trajectory"))

CENTS = lambda a, b: 1200 * math.log2(a / b)


def true_f(t, f0, finf, tg):
    return finf + (f0 - finf) * np.exp(-t / tg)


def bd_like(f0=58.0, finf=50.0, tg=0.020, tau=0.150, sr=48000, dur=0.6, lead=0.02,
            gain=1.0, attack_burst_hz=None, attack_burst_s=0.004, noise=0.0, seed=0):
    t = np.arange(int(dur * sr)) / sr
    ph = 2 * np.pi * (finf * t + (f0 - finf) * tg * (1 - np.exp(-t / tg)))
    y = np.sin(ph) * np.exp(-t / tau)
    if attack_burst_hz:
        n = int(attack_burst_s * sr)
        y[:n] = np.sin(2 * np.pi * attack_burst_hz * t[:n]) * np.exp(-t[:n] / tau)
    if noise:
        y = y + noise * np.random.default_rng(seed).standard_normal(len(t))
    return np.concatenate([np.zeros(int(lead * sr)), gain * y]), sr


def traj(sig, f_ref=52.0, **kw):
    x, sr = sig
    return pt.trajectory(x, sr, f_ref, **kw)


# --------------------------------------------------------------------------
# known answers
# --------------------------------------------------------------------------
@pytest.mark.parametrize("sr", [48000, 44100])
def test_steady_50hz_reads_50hz(sr):
    tr = traj(bd_like(50.0, 50.0, sr=sr))
    f = tr.f[tr.live & (tr.t >= 0.020)]
    assert len(f) >= 20
    # measured worst case on this signal; the bound is the measurement + margin
    assert np.max(np.abs([CENTS(v, 50.0) for v in f])) < 15.0
    assert abs(pt.glide_cents(tr)) < 12.0   # measured apparatus bias: -8.5 cents at 44.1 kHz


@pytest.mark.parametrize("sr", [48000, 44100])
def test_exponential_glide_tracks_closed_form(sr):
    sig = bd_like(58.0, 50.0, tg=0.020, sr=sr)
    tr = traj(sig)
    m = tr.live & (tr.t >= 0.020) & (tr.t <= 0.290)
    err = [abs(CENTS(f, true_f(t, 58.0, 50.0, 0.020))) for f, t in zip(tr.f[m], tr.t[m])]
    assert len(err) >= 30
    assert max(err) < 12.0, max(err)
    # sustained glide: early-window mean vs late-window mean, both read from
    # the closed form the same way
    def truth_mean(w):
        tt = np.arange(w[0], w[1], 1 / 48000)
        return float(np.mean(true_f(tt, 58.0, 50.0, 0.020)))
    want = CENTS(truth_mean(pt.EARLY_WINDOW_S), truth_mean(pt.LATE_WINDOW_S))
    assert pt.glide_cents(tr) == pytest.approx(want, abs=15.0)
    # WRONG-THEN-RIGHT: first written as `want > 100`; the closed form says a
    # tg = 20 ms glide reads ~56 cents between a 20-50 ms and an 80-130 ms
    # window (most of it is done by 20 ms).  The bound is the formula's.
    assert 40.0 < want < 80.0


def test_missing_glide_control_reads_flat():
    """The historical defect: 50.3 -> 49.4 Hz (~30 cents), generated apart."""
    flat = traj(bd_like(50.3, 49.4, tg=0.020))
    gl = traj(bd_like(58.0, 50.0, tg=0.020))
    assert abs(pt.glide_cents(flat)) < 15.0
    assert pt.glide_cents(gl) - pt.glide_cents(flat) > 40.0
    # the two differ in SHAPE although their medians are near each other
    assert pt.shape_cents(gl, flat) > 40.0


def test_onset_shift_and_gain_do_not_move_it():
    base = traj(bd_like())
    assert pt.glide_cents(base) > 40.0      # precondition: an invariance of a flat reading is vacuous
    for lead in (0.005, 0.0377, 0.2):
        for gain in (0.01, 1.0, 0.3):
            t2 = traj(bd_like(lead=lead, gain=gain))
            assert abs(pt.glide_cents(t2) - pt.glide_cents(base)) < 8.0
            assert abs(pt.offset_cents(base, t2)) < 8.0
            assert pt.shape_cents(base, t2) < 8.0


def test_constant_tuning_shift_is_offset_not_shape():
    a = traj(bd_like(58.0, 50.0))
    sh = 2 ** (100 / 1200)
    b = traj(bd_like(58.0 * sh, 50.0 * sh), f_ref=52.0 * sh)
    assert pt.offset_cents(a, b) == pytest.approx(100.0, abs=10.0)
    assert pt.shape_cents(a, b) < 15.0
    assert pt.glide_cents(b) == pytest.approx(pt.glide_cents(a), abs=15.0)


def test_attack_retune_does_not_leak_into_sustained_glide():
    """A 4 ms 130 Hz burst at the onset (the shipped attack retune's shape) must
    not read as a glide.  The estimator cannot SEE the burst (window ~15 ms,
    hop 5 ms): that is a stated limit, not a claim -- asserted below so the
    limit cannot silently become a capability claim."""
    assert pt.glide_cents(traj(bd_like(58.0, 50.0))) > 40.0      # precondition: it can see a real glide
    plain = traj(bd_like(50.0, 50.0))
    burst = traj(bd_like(50.0, 50.0, attack_burst_hz=130.0))
    assert abs(pt.glide_cents(burst) - pt.glide_cents(plain)) < 10.0
    assert not hasattr(pt, "attack_excursion_cents")


def test_noise_degrades_gracefully():
    tr = traj(bd_like(noise=0.003))
    assert pt.glide_cents(tr) > 35.0


# --------------------------------------------------------------------------
# REFUSED, never a number
# --------------------------------------------------------------------------
def test_refuses_silent_nan_inf():
    z = np.zeros(48000)
    with pytest.raises(pt.Refused, match="silent"):
        pt.trajectory(z, 48000, 52.0)
    x, sr = bd_like()
    for bad in (np.nan, np.inf, -np.inf):
        y = x.copy()
        y[5000] = bad
        with pytest.raises(pt.Refused, match="non-finite"):
            pt.trajectory(y, sr, 52.0)


def test_refuses_cut_into_the_strike():
    x, sr = bd_like(lead=0.0)
    x = x[int(0.01 * sr):]
    with pytest.raises(pt.Refused, match="cut into the strike"):
        pt.trajectory(x, sr, 52.0)


def test_refuses_short_signal_and_bad_reference():
    x, sr = bd_like(dur=0.04)
    with pytest.raises(pt.Refused):
        pt.trajectory(x, sr, 52.0)
    x, sr = bd_like()
    for f in (float("nan"), 1.0, 1e6):
        with pytest.raises(pt.Refused, match="reference frequency"):
            pt.trajectory(x, sr, f)


def test_short_decay_refuses_instead_of_zero_glide():
    """tau = 6 ms: the hit is dead before the late window.  A glide of 0.0
    here would be a refusal rendered as a perfect score."""
    short = bd_like(tau=0.006, dur=0.6)
    try:
        tr = traj(short)
    except pt.Refused:
        return                      # refused at construction: acceptable
    with pytest.raises(pt.Refused):
        pt.glide_cents(tr)


def test_comparison_refuses_without_colive_frames():
    a = traj(bd_like(tau=0.150))
    b = traj(bd_like(tau=0.150))
    b.live[:] = False
    with pytest.raises(pt.Refused, match="co-live"):
        pt.offset_cents(a, b)
    with pytest.raises(pt.Refused, match="co-live"):
        pt.shape_cents(a, b)


# --------------------------------------------------------------------------
# the qualification has teeth: a blind estimator fails it
# --------------------------------------------------------------------------
def blind_trajectory(x, sr, f_ref, **kw):
    """MUTANT: right frames and liveness, but every frame reports the
    reference line -- the 'flat trajectory' defect inside the instrument."""
    tr = pt.trajectory(x, sr, f_ref, **kw)
    tr.f = np.full_like(tr.f, tr.f0)
    return tr


def test_blind_estimator_is_caught_by_the_glide_qualification():
    sig = bd_like(58.0, 50.0)
    good = pt.trajectory(*sig, 52.0)
    blind = blind_trajectory(*sig, 52.0)
    assert pt.glide_cents(good) > 40.0
    assert abs(pt.glide_cents(blind)) < 1e-6          # the mutant cannot see it
    # the same assertion test_exponential_glide_tracks_closed_form makes:
    with pytest.raises(AssertionError):
        want = 56.0
        assert pt.glide_cents(blind) == pytest.approx(want, abs=15.0)


# --------------------------------------------------------------------------
# glide_cents must REFUSE where its known answer is wrong (#602)
# --------------------------------------------------------------------------
# A constant-pitch decaying sinusoid has a TRUE glide of ZERO, so every
# answered reading is its own error.  The floor is the largest steady-tone
# error the estimator is documented to have (docs/bd-pitch-baseline-request.md,
# "steady-tone worst error from 20 ms: 13.9 cents (48k)"); a reading that can be
# further from the truth than that is not data, it must be a refusal.
GLIDE_FLOOR_CENTS = 13.9
F_REF = 52.0


def zero_glide_tone(f0, tau, phase, sr):
    """10 ms of silence then 0.5 sin(2 pi f0 t + phase) exp(-t/tau): true glide 0."""
    t = np.arange(sr) / sr
    return np.concatenate([np.zeros(sr // 100),
                           0.5 * np.sin(2 * np.pi * f0 * t + phase) * np.exp(-t / tau)]), sr


def zero_glide_reading(f0, tau, phase, sr):
    """The glide_cents of the zero-glide tone, or None when it REFUSES."""
    try:
        return pt.glide_cents(pt.trajectory(*zero_glide_tone(f0, tau, phase, sr), F_REF))
    except pt.Refused:
        return None


def bd_tau(knob, f0):
    import bd_glide_phase_sweep as sweep
    return sweep.bd_q(knob) / (math.pi * f0)


# knobs chosen on 48 kHz; 44.1 kHz and the offset f0 grid are held out (see
# test_zero_glide_answers_hold_on_untouched_conditions)
SELECT_KNOBS = (0.0, 0.3, 0.6, 1.0, 1.5, 2.5, 5.0, 10.0)
SELECT_F0S = (40.0, 43.5, 46.0, 48.0, 50.0, 52.0, 54.0, 56.0, 60.0, 65.0)
HELDOUT_KNOBS = (0.15, 0.45, 0.8, 1.2, 2.0, 3.7, 7.5, 8.8)
HELDOUT_F0S = (41.25, 44.75, 47.25, 49.25, 51.25, 53.25, 55.25, 58.25, 63.25)


def answered_violations(rates, knobs, f0s, floor=GLIDE_FLOOR_CENTS):
    """(sr, knob, f0, phase, reading) for every ANSWERED cell further than
    `floor` from the true 0, and the number of cells that answered."""
    bad, answered = [], 0
    for sr in rates:
        for k in knobs:
            for f0 in f0s:
                for ph in (0.0, math.pi / 2):
                    g = zero_glide_reading(f0, bd_tau(k, f0), ph, sr)
                    if g is None:
                        continue
                    answered += 1
                    if not abs(g) <= floor:
                        bad.append((sr, k, f0, round(ph, 2), round(g, 1)))
    return bad, answered


def test_named_wrong_answers_are_refused_or_within_floor():
    """The two cells #602 names: knob 0.6 / 52 Hz read -68 c; knob 0 / 40-43.5 Hz
    read +76..+97 c.  Each must now be a refusal or within the floor."""
    cells = [(0.6, 52.0)] + [(0.0, f) for f in (40.0, 41.5, 43.5)]
    for k, f0 in cells:
        for ph in (0.0, math.pi / 2):
            g = zero_glide_reading(f0, bd_tau(k, f0), ph, 48000)
            assert g is None or abs(g) <= GLIDE_FLOOR_CENTS, (k, f0, ph, g)


def test_zero_glide_answers_never_exceed_the_floor():
    bad, answered = answered_violations((48000,), SELECT_KNOBS, SELECT_F0S)
    assert not bad, f"{len(bad)} answered cell(s) beyond {GLIDE_FLOOR_CENTS} c: {bad[:6]}"
    # a refuse-everything estimator also passes the line above; it must not be this one
    assert answered >= 20, answered


def test_zero_glide_answers_hold_on_untouched_conditions():
    """Criteria were chosen on 48 kHz with SELECT_*; these were not used."""
    bad, answered = answered_violations((44100, 48000), HELDOUT_KNOBS, HELDOUT_F0S)
    assert not bad, f"{len(bad)} answered cell(s) beyond {GLIDE_FLOOR_CENTS} c: {bad[:6]}"
    bad, answered = answered_violations((44100,), SELECT_KNOBS, SELECT_F0S)
    assert not bad, f"{len(bad)} answered cell(s) beyond {GLIDE_FLOOR_CENTS} c at 44.1 kHz: {bad[:6]}"


@pytest.mark.parametrize("sr", [48000, 44100])
@pytest.mark.parametrize("knob", [2.5, 5.0, 7.5, 10.0])
def test_declared_557_conditions_still_answer(sr, knob):
    """A fix that refuses everything is not a fix: the DECLARED knobs at the
    pitch the BD is declared at (tone 50) must still produce a number."""
    for ph in (0.0, math.pi / 2):
        assert zero_glide_reading(50.0, bd_tau(knob, 50.0), ph, sr) is not None


def test_old_min_frames_only_gate_is_caught(monkeypatch):
    """INJECTED BUG (rule 8): restore the gate that counted live frames and
    nothing else.  The known-answer check above must then fail, or it would
    pass an estimator that has the original defect."""
    monkeypatch.setattr(pt, "_qualify_glide", lambda tr: None)
    bad, _ = answered_violations((48000,), SELECT_KNOBS, SELECT_F0S)
    assert bad, "the MIN_FRAMES-only gate passed the known-answer sweep: the sweep cannot see #602"
    named = zero_glide_reading(52.0, bd_tau(0.6, 52.0), 0.0, 48000)
    assert named is not None and abs(named) > GLIDE_FLOOR_CENTS
