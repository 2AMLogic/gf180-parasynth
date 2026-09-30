#!/usr/bin/env python3
"""Ground truth for the oscillator / envelope / glide / noise estimators.

    .venv/bin/python -m pytest model/test_reference_voice.py -q      # ~40 s

Same discipline as `model/test_reference_compare.py`, and for the same reason:
`docs/discrimination.md` section 8.1 records two published numbers that were a
measurement window rather than a filter. Every estimator here is checked
against a signal whose answer is known in closed form BEFORE it is pointed at
a reference, and each one is also checked to REFUSE the case it cannot answer
-- an estimator that always returns a number is the failure mode, not a
convenience.

The last group is the aliasing control: PolyBLEP switched off must be visible,
or the oscillator comparison has no power.
"""
import ast
import contextlib
import io
import math
import os
import sys

import numpy as np
import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "..", "audition"))

import audio_measure as am                                          # noqa: E402
import reference_voice as rv                                        # noqa: E402
import voice_fx as vf                                               # noqa: E402

SR = 48000


# ===========================================================================
# closed-form waveforms
# ===========================================================================
def ideal(shape: str, f0: float, n: int, sr: int = SR, duty: float = 0.25,
          kmax: int = 200) -> np.ndarray:
    """A band-limited ideal waveform from its Fourier series: no aliasing by
    construction, so it is also the reference an aliasing measurement is
    compared against."""
    t = np.arange(n) / sr
    y = np.zeros(n)
    for k in range(1, kmax + 1):
        if k * f0 >= sr / 2:
            break
        if shape == "saw":
            y += np.sin(2 * math.pi * k * f0 * t) / k
        elif shape == "square":
            if k % 2:
                y += np.sin(2 * math.pi * k * f0 * t) / k
        elif shape == "tri":
            if k % 2:
                y += (-1) ** ((k - 1) // 2) * np.sin(2 * math.pi * k * f0 * t) / k ** 2
        elif shape == "pulse":
            y += math.sin(math.pi * k * duty) * np.cos(2 * math.pi * k * f0 * t) / k
        else:
            raise ValueError(shape)
    return y


def ideal_harmonics(shape: str, kmax: int = 9, duty: float = 0.25) -> dict:
    """h2..hk of each ideal waveform relative to its own fundamental, in dB,
    in closed form. `None` where the harmonic is exactly absent."""
    def a(k):
        if shape == "saw":
            return 1.0 / k
        if shape == "square":
            return 1.0 / k if k % 2 else 0.0
        if shape == "tri":
            return 1.0 / k ** 2 if k % 2 else 0.0
        if shape == "pulse":
            return abs(math.sin(math.pi * k * duty)) / k
        raise ValueError(shape)
    a1 = a(1)
    # `sin(pi*4*0.25)` is 1.2e-16 in floating point, not 0: an exact null has
    # to be recognised by magnitude, not by equality, or the ground truth
    # asks the estimator for a harmonic at -327 dB.
    return {k: (None if abs(a(k)) < 1e-12 else 20 * math.log10(abs(a(k)) / a1))
            for k in range(2, kmax + 1)}


def lfsr16(n: int, seed: int = 0xACE1) -> np.ndarray:
    """A maximal-length 16-bit Galois LFSR, x^16 + x^14 + x^13 + x^11 + 1.
    Period 65535 samples = 1.36531 s at 48 kHz -- the number this repository
    needs a measurement against."""
    st, out = seed, np.empty(n)
    for i in range(n):
        lsb = st & 1
        st >>= 1
        if lsb:
            st ^= 0xB400
        out[i] = 1.0 if lsb else -1.0
    return out


def pink(n: int, seed: int = 3) -> np.ndarray:
    """Exactly -3.01 dB/octave by construction: white shaped by 1/sqrt(f)."""
    rng = np.random.default_rng(seed)
    X = np.fft.rfft(rng.standard_normal(n))
    f = np.arange(len(X))
    X[1:] /= np.sqrt(f[1:])
    X[0] = 0
    y = np.fft.irfft(X, n)
    return y / np.abs(y).max()


# ===========================================================================
# 1. the harmonic series of a waveform, at a KNOWN fundamental
# ===========================================================================
@pytest.mark.parametrize("shape", ["saw", "square", "tri", "pulse"])
def test_harmonic_series_matches_the_closed_form_of_each_ideal_waveform(shape):
    f0 = 110.0
    x = ideal(shape, f0, SR)
    s = am.harmonic_signature(x, SR, f0=f0, kmax=9)
    want = ideal_harmonics(shape)
    for k, w in want.items():
        got = s[f"h{k}"]
        if w is None:
            assert got is None, f"{shape} h{k}: reported {got} for a harmonic that is absent"
        else:
            assert got is not None, f"{shape} h{k}: refused a harmonic that is present at {w:.2f}"
            assert abs(got - w) < 0.25, (shape, k, got, w)


def test_the_25_percent_pulse_null_is_reported_as_absent_not_as_the_floor():
    """A 25 % pulse has an exact null at every 4th harmonic. An estimator that
    hands back the noise in that bin as a level would make two different duty
    cycles look alike."""
    s = am.harmonic_signature(ideal("pulse", 110.0, SR), SR, f0=110.0, kmax=9)
    assert s["h4"] is None and s["h8"] is None, (s["h4"], s["h8"])
    assert s["h2"] is not None and abs(s["h2"] + 3.01) < 0.25, s["h2"]


# ===========================================================================
# 2. noise: colour, and whether it repeats
# ===========================================================================
def test_psd_slope_reads_white_as_zero_and_pink_as_minus_three():
    rng = np.random.default_rng(11)
    w = am.psd_slope_db_oct(rng.standard_normal(SR * 4), (100.0, 12000.0), SR)
    p = am.psd_slope_db_oct(pink(SR * 4), (100.0, 12000.0), SR)
    assert w.ok and p.ok, (w.reason, p.reason)
    assert abs(w.value) < 0.5, w.value
    assert abs(p.value + 3.01) < 0.6, p.value


def test_psd_slope_refuses_a_tone():
    """A sine is not a noise colour. The straightness check has to reject it,
    or every oscillator in the study reads as some exotic noise."""
    t = np.arange(SR * 4) / SR
    e = am.psd_slope_db_oct(np.sin(2 * math.pi * 1000 * t), (100.0, 12000.0), SR)
    assert not e.ok, e.value


def test_repeat_period_finds_a_16_bit_lfsr_at_its_exact_period():
    """65535 samples at 48 kHz = 1.365313 s. This is the number the noise
    target is written against."""
    e = am.repeat_period(lfsr16(int(3.5 * SR)), SR, max_lag_s=3.0)
    assert e.ok, e.reason
    assert abs(e.value - 65535 / SR) < 2.0 / SR, (e.value, 65535 / SR)
    assert e.detail["corr"] > 0.95, e.detail


def test_repeat_period_refuses_noise_that_does_not_repeat():
    rng = np.random.default_rng(5)
    e = am.repeat_period(rng.standard_normal(int(3.5 * SR)), SR, max_lag_s=3.0)
    assert not e.ok, f"claimed a repeat at {e.value} s in white noise"


# ===========================================================================
# 3. envelope segment shape
# ===========================================================================
def test_segment_shape_has_the_closed_form_index_of_each_candidate_law():
    """linear 0.0000; RC charge and RC discharge both +0.3808 (the index is a
    property of the path, not its direction -- `span`'s sign gives that);
    convex t^2 -0.2500. Those are what the envelope comparison reads."""
    n = 4800
    t = np.linspace(0, 1, n)
    lin = am.segment_shape(t, SR)
    up = am.segment_shape(1 - np.exp(-4 * t), SR)
    dn = am.segment_shape(np.exp(-4 * t), SR)
    cx = am.segment_shape(t ** 2, SR)
    assert lin.ok and up.ok and dn.ok and cx.ok
    assert abs(lin.value) < 0.005, lin.value
    assert abs(up.value - 0.3808) < 0.004, up.value
    assert abs(dn.value - 0.3808) < 0.004, dn.value
    assert abs(cx.value + 0.25) < 0.005, cx.value
    assert up.detail["span"] > 0 and dn.detail["span"] < 0, "span must give the direction"


def test_segment_shape_reports_the_10_to_90_time_of_a_linear_ramp():
    n = SR // 10                                    # 0.1 s ramp
    e = am.segment_shape(np.linspace(0, 1, n), SR)
    assert e.ok
    assert abs(e.detail["t_10_90_s"] - 0.08) < 0.002, e.detail


def test_segment_shape_refuses_a_segment_that_does_not_move():
    assert not am.segment_shape(np.ones(1000), SR).ok


# ===========================================================================
# 4. the glide law
# ===========================================================================
def _traj(law, f0=110.0, f1=440.0, dur=0.4):
    t = np.arange(int(dur * SR)) / SR
    if law == "constant-rate":
        return f0 * (f1 / f0) ** (t / dur)
    if law == "constant-time":
        return f1 + (f0 - f1) * np.exp(-t / (dur / 4))
    if law == "linear-hz":
        return f0 + (f1 - f0) * t / dur
    raise ValueError(law)


@pytest.mark.parametrize("law", ["constant-rate", "constant-time", "linear-hz"])
def test_glide_law_identifies_each_synthesised_law(law):
    e = am.glide_law(_traj(law), SR)
    assert e.ok, e.reason
    assert e.detail["law"] == law, (law, e.detail["r2"])
    others = [v for k, v in e.detail["r2"].items() if k != law]
    assert e.value - max(others) > 0.004, (law, e.detail["r2"])


def test_glide_law_reports_the_rate_of_a_constant_rate_glide():
    """Two octaves in 0.4 s is 6000 cents/s, exactly."""
    e = am.glide_law(_traj("constant-rate", 110.0, 440.0, 0.4), SR)
    assert abs(e.detail["cents_per_s"] - 6000.0) < 30.0, e.detail
    assert abs(e.detail["octaves"] - 2.0) < 0.01


def test_glide_law_refuses_a_static_pitch():
    assert not am.glide_law(np.full(SR // 4, 220.0), SR).ok


# ===========================================================================
# 5. START RED: the aliasing measurement must see PolyBLEP switched off
# ===========================================================================
def _osc(shape, note, seconds=0.3, blep=True):
    from audition.dsp import note_hz                               # noqa: F401
    o = vf.OscFx(shape, blep=blep)
    inc = vf.phase_inc(vf.note_hz(note))
    return np.asarray(o.render(int(seconds * SR), inc), dtype=np.float64) / 32768.0


@pytest.mark.parametrize("shape,note", [("saw", 88), ("square", 88), ("saw", 76)])
def test_polyblep_off_is_visible_in_the_aliasing_measure(shape, note):
    """The injected control for every oscillator number in the study. If the
    measure cannot see the anti-aliasing switched off, it cannot adjudicate
    one synthesiser's oscillator against another's."""
    f0 = vf.note_hz(note)
    on = am.inharmonic_fraction_db(_osc(shape, note, blep=True), f0, SR).require("blep on")
    off = am.inharmonic_fraction_db(_osc(shape, note, blep=False), f0, SR).require("blep off")
    assert off - on > 6.0, (shape, note, on, off)


def test_an_ideal_waveform_has_less_inharmonic_energy_than_ours():
    """Sanity on the direction of the measure: the closed-form band-limited
    saw must read lower than any real oscillator at the same pitch."""
    f0 = vf.note_hz(88)
    # `ideal` is the closed-form band-limited saw: alias-free by construction,
    # so this reading IS the estimator's own floor and is requested on
    # purpose with `min_headroom_db=None` (#115).
    a = am.inharmonic_fraction_db(ideal("saw", f0, int(0.3 * SR)), f0, SR,
                                  min_headroom_db=None).require("ideal")
    b = am.inharmonic_fraction_db(_osc("saw", 88), f0, SR).require("ours")
    assert a < b, (a, b)


# ===========================================================================
# 6. envelope ripple -- the "zipper" measure, against its closed form
# ===========================================================================
def test_envelope_ripple_is_at_the_floor_for_a_smooth_ramp():
    """A smooth envelope must read as having no stepping. If it does not, the
    measure will call every sweep grainy and adjudicate nothing."""
    n = SR
    e = am.envelope_ripple_db(np.linspace(1.0, 4.0, n), SR)
    assert e.ok, e.reason
    assert e.value < -70.0, e.value


@pytest.mark.parametrize("rate", [500.0, 1000.0, 2000.0])
def test_envelope_ripple_matches_the_closed_form_of_a_known_staircase(rate):
    """A staircase of step d on a ramp of mean m, stepping fast compared with
    the high-pass window, has residual RMS d/sqrt(12) -- so the ripple is
    20*log10(d / (sqrt(12) * m)). Checked at three step RATES, because the
    measure under-reads when the steps are slower than its own window and
    that limit has to be pinned, not discovered later."""
    n = SR
    t = np.linspace(0.0, 1.0, n)
    span = 3.0
    d = span / rate                      # `rate` steps per second over the ramp
    steps = np.round((1.0 + span * t) / d) * d
    e = am.envelope_ripple_db(steps, SR, hp_hz=40.0)
    want = 20 * math.log10(d / (math.sqrt(12) * float(np.mean(steps))))
    assert e.ok, e.reason
    assert abs(e.value - want) < 1.0, (rate, e.value, want)
    assert abs(e.detail["ripple_rate_hz"] - rate) / rate < 0.10, e.detail


def test_envelope_ripple_under_reads_when_the_steps_are_slower_than_its_window():
    """The validity condition, asserted rather than assumed: at 15 steps per
    second against a 40 Hz high-pass the moving average partly tracks the
    staircase and the measure reads 4.1 dB LOW. Anyone quoting a ripple checks
    `ripple_rate_hz` against `hp_hz` first, and this test is why."""
    n, span, rate = SR, 3.0, 15.0
    t = np.linspace(0.0, 1.0, n)
    d = span / rate
    steps = np.round((1.0 + span * t) / d) * d
    e = am.envelope_ripple_db(steps, SR, hp_hz=40.0)
    want = 20 * math.log10(d / (math.sqrt(12) * float(np.mean(steps))))
    assert e.ok
    assert e.value < want - 3.0, (e.value, want)   # measured: 4.1 dB low at 15 steps/s


def test_envelope_ripple_band_limit_attenuates_the_hilbert_artefact():
    """The analytic envelope of a real sinusoid is not exactly constant, and
    its residual sits AT AND ABOVE the carrier. Without the band limit the
    measure reports that as stepping -- it read -32.4 dB on a render that had
    none. With `lp_hz` under the carrier it must fall away.

    The artefact is measured at f0 itself, not at the 2*f0 this test first
    asserted; that assertion was wrong and is corrected here rather than
    loosened, because WHERE the artefact sits is what decides where `lp_hz`
    has to go."""
    n = SR
    t = np.arange(n) / SR
    x = np.sin(2 * math.pi * 2000.0 * t) * (1.0 + 0.5 * t)      # smooth swell, no steps
    env = am.analytic_envelope(x)
    raw = am.envelope_ripple_db(env, SR)
    lim = am.envelope_ripple_db(env, SR, lp_hz=800.0)
    assert raw.ok and lim.ok
    assert raw.detail["ripple_rate_hz"] >= 1900.0, raw.detail    # at or above the carrier
    # measured: -75.3 dB unlimited, -93.2 dB at lp_hz = 800 on this signal.
    # A moving-average low-pass ATTENUATES the artefact rather than removing it,
    # so `ripple_rate_hz` can still name the carrier afterwards -- 18 dB smaller.
    assert lim.value < raw.value - 15.0, (raw.value, lim.value)


def test_envelope_ripple_refuses_silence():
    assert not am.envelope_ripple_db(np.zeros(SR), SR).ok


# ===========================================================================
# 8. WHICH WAVEFORM IS IT?  the qualification `reference_rigs.py` lacked
#
# `model/reference_rigs.py` asked Surge for a saw, got a 50 % pulse, and
# published it as a saw for a whole study -- because the request and the label
# agreed with each other and nothing compared either with the signal.
#
# The qualification is time-domain first, and these tests are why. "Odd
# harmonics only" is a test for 50 % DUTY, not for "square"; two saws do not
# always make a comb; and a rule of thumb dressed as a test is the failure this
# project keeps repeating. The last group is the one that matters: DELIBERATELY
# WRONG setups that the qualification has to reject, because a check that has
# only ever seen correct configurations has not been shown to reject anything.
# ===========================================================================
def dual_saw(f0: float, n: int, offset: float = 0.5, sr: int = SR,
             detune: float = 0.0, kmax: int = 200) -> np.ndarray:
    """Two saws `offset` of a period apart -- Surge's Classic oscillator at
    Shape +100 %. At offset 0.5 the odd harmonics cancel and the result is a
    saw at 2*f0; at offset 0 and no detune it is simply a saw at twice the
    amplitude. NEITHER is a comb, which is why a comb test cannot find one."""
    t = np.arange(n) / sr
    y = np.zeros(n)
    for k in range(1, kmax + 1):
        if k * f0 >= sr / 2:
            break
        if k * f0 * (1 + detune) < sr / 2:
            y += np.sin(2 * math.pi * k * f0 * (1 + detune) * t
                        + 2 * math.pi * k * offset) / k
        y += np.sin(2 * math.pi * k * f0 * t) / k
    return y


def shark(f0: float, n: int, sr: int = SR, mix: float = 0.5) -> np.ndarray:
    """A saw/triangle hybrid -- Mini V3's 'saw-triangular'. It has no closed
    form here, so the qualification must REFUSE it rather than hand back
    whichever named waveform is least wrong."""
    return mix * ideal("saw", f0, n, sr) + (1 - mix) * ideal("tri", f0, n, sr)


# -- the time-domain descriptors, against shapes whose answer is arithmetic ---
@pytest.mark.parametrize("f0", [55.0, 110.0, 440.0, 1760.0])
@pytest.mark.parametrize("shape,duty,want_rect,discontinuous", [
    ("saw", 0.5, 0.42, True), ("tri", 0.5, 0.51, False), ("square", 0.5, 0.98, True),
    ("pulse", 0.25, 0.98, True),
])
def test_cycle_descriptors_match_the_geometry_of_each_shape(f0, shape, duty, want_rect,
                                                            discontinuous):
    """Both descriptors have to hold over the study's whole pitch range: at
    1760 Hz a period is 27 samples and every threshold expressed as a fraction
    of the peak-to-peak stops working."""
    x = ideal(shape, f0, SR, duty=duty)
    cyc, jit = am.cycle_average(x, f0, SR)
    assert jit < 0.005, jit
    assert abs(am.rectangularity(cyc) - want_rect) < 0.06, am.rectangularity(cyc)
    assert am.midpoint_crossings(cyc) == 2, am.midpoint_crossings(cyc)
    r = am.step_ratio(x)
    assert (r >= 4.0) == discontinuous, (shape, f0, r)


@pytest.mark.parametrize("d", [0.10, 0.25, 0.40, 0.50, 0.52, 0.75])
def test_duty_is_measured_in_the_time_domain_to_a_percent(d):
    """The quantity the whole mapping turned on. A spectrum cannot tell d from
    1 - d; the period can."""
    f0 = 110.0
    cyc, _ = am.cycle_average(ideal("pulse", f0, SR, duty=d), f0, SR)
    got = am.duty_cycle(cyc)
    assert min(abs(got - d), abs(got - (1 - d))) < 0.01, (d, got)


# -- identification ----------------------------------------------------------
@pytest.mark.parametrize("shape,duty,fam", [
    ("saw", 0.5, "saw"), ("tri", 0.5, "tri"), ("square", 0.5, "pulse"),
    ("pulse", 0.25, "pulse"), ("pulse", 0.10, "pulse"), ("pulse", 1 / 3, "pulse"),
])
def test_identifies_each_ideal_waveform_and_reports_its_duty(shape, duty, fam):
    f0 = 110.0
    w = am.waveform_id(ideal(shape, f0, SR, duty=duty), f0, SR)
    assert w.ok and w.family == fam, w
    if fam == "pulse":
        assert min(abs(w.duty - duty), abs(w.duty - (1 - duty))) < 0.01, w


def test_identifies_a_sine_from_one_line():
    f0 = 110.0
    w = am.waveform_id(np.sin(2 * math.pi * f0 * np.arange(SR) / SR), f0, SR)
    assert w.ok and w.label == "sine", w


def test_a_forty_nine_percent_square_is_still_a_square():
    """The reason the qualification cannot be "odd harmonics only": a 49 %
    square HAS even harmonics, at -30 dB. It is still a square, and a test
    that refused it would be testing duty, not shape."""
    f0 = 110.0
    s = am.harmonic_signature(ideal("pulse", f0, SR, duty=0.49), SR, f0=f0, kmax=9)
    assert s["h2"] is not None, "a 49 % square does have an even harmonic"
    w = am.waveform_id(ideal("pulse", f0, SR, duty=0.49), f0, SR)
    assert w.ok and w.family == "pulse" and abs(w.duty - 0.49) < 0.01, w
    assert am.waveform_matches(w, "square")[0]


def test_the_analogue_squares_fifty_two_percent_duty_is_measured_not_refused():
    """Mini V3 models the Model D's hand-trimmed square at ~52 %, h2 at about
    -24 dB. The duty is the finding; refusing the row would throw away the
    reference this study exists to use."""
    f0 = 110.0
    w = am.waveform_id(ideal("pulse", f0, SR, duty=0.52), f0, SR)
    assert w.ok and abs(w.duty - 0.52) < 0.01, w
    assert am.waveform_matches(w, "square")[0], am.waveform_matches(w, "square")


# -- the refusals ------------------------------------------------------------
def test_a_fifty_percent_pulse_does_not_qualify_as_a_saw():
    """THE BUG, in one assertion. Surge's Classic Shape is bipolar, so the
    normalised 0.0 that `WAVES` used for "saw" is -100 %: a pulse at the Width
    setting. Both are 1/n series."""
    f0 = 110.0
    w = am.waveform_id(ideal("square", f0, SR), f0, SR)
    assert w.family == "pulse" and abs(w.duty - 0.5) < 0.01, w
    ok, why = am.waveform_matches(w, "saw")
    assert not ok and "measured pulse:" in why, why
    assert am.waveform_matches(w, "square")[0], "and it IS a square"


def test_a_dual_saw_half_a_period_apart_is_refused_as_a_subharmonic_claim():
    """Surge's Shape +100 % at 50 % width. The odd harmonics cancel, so the
    record is a saw at 2*f0 -- and the commanded f0 is not its fundamental."""
    f0 = 110.0
    w = am.waveform_id(dual_saw(f0, SR, offset=0.5), f0, SR)
    assert not w.ok and "repeats at" in w.reason, w


def test_a_dual_saw_a_quarter_period_apart_is_refused_by_its_two_ramps():
    """No comb, no cancelled fundamental -- and two ramps per period. Only the
    time domain sees this one."""
    f0 = 110.0
    w = am.waveform_id(dual_saw(f0, SR, offset=0.25), f0, SR)
    assert not w.ok and "midpoint crossings" in w.reason, w


def test_two_aligned_saws_are_a_saw_and_are_reported_as_one():
    """The control on the control. At zero offset and zero detune two saws sum
    to a saw, so the honest answer is "saw" -- a qualification that refused it
    would be pattern-matching the setup, not the signal."""
    f0 = 110.0
    w = am.waveform_id(dual_saw(f0, SR, offset=0.0), f0, SR)
    assert w.ok and w.label == "saw", w


def test_a_shark_tooth_is_refused_rather_than_named():
    f0 = 110.0
    w = am.waveform_id(shark(f0, SR), f0, SR)
    assert not w.ok, w
    assert not am.waveform_matches(w, "shark")[0]


def test_refuses_when_too_few_harmonics_fit_in_the_qualification_band():
    """At 4 kHz the band holds two harmonics, and a 1/3 pulse is
    indistinguishable from a saw until the 3rd. No answer is the answer."""
    f0 = 4000.0
    w = am.waveform_id(ideal("saw", f0, SR), f0, SR)
    assert not w.ok and "harmonics below" in w.reason, w


@pytest.mark.parametrize("note_hz,want", [(110.0, True), (1760.0, True)])
def test_a_band_limited_saw_is_still_a_saw_at_the_top_of_the_range(note_hz, want):
    """The rule the 12 kHz band exists for. Our own saw at 1760 Hz is 3.2 dB
    down in h9 at 15.8 kHz -- the band-limiting the aliasing study is there to
    measure -- and it is still a saw."""
    x = _osc("saw", 93 if note_hz > 1000 else 45)
    w = am.waveform_id(x, note_hz, SR)
    assert w.ok == want and w.label == "saw", w


# -- deliberately wrong SETUPS, which are the controls that matter -----------
def unison(f0: float, n: int, voices: int = 3, cents: float = 7.0, sr: int = SR):
    """A saw with detuned partners -- the "Unison Voices" left on that this
    repository has already been bitten by once."""
    y = np.zeros(n)
    for i in range(voices):
        det = (i - (voices - 1) / 2) * cents / 1200.0
        y += ideal("saw", f0 * 2 ** det, n, sr)
    return y


def chorused(x, sr: int = SR, depth_ms: float = 3.0, rate: float = 0.6):
    """A modulated delay in the path -- an FX slot nobody switched off."""
    n = len(x)
    t = np.arange(n) / sr
    d = (depth_ms * 1e-3 * sr) * (1.0 + np.sin(2 * math.pi * rate * t)) / 2 + 2
    return 0.5 * (x + np.interp(np.arange(n) - d, np.arange(n), x, left=0.0))


def notched(f0: float, n: int, sr: int = SR, k_cut: int = 2,
            depth: float = 0.30) -> np.ndarray:
    """A saw through an EQ that takes 10 dB out of its 2nd harmonic. Every
    time-domain test passes -- it repeats exactly, it crosses its midpoint
    twice, it still has its discontinuity -- and the harmonic series is the
    only thing that can refuse it."""
    t = np.arange(n) / sr
    y = np.zeros(n)
    for k in range(1, 201):
        if k * f0 >= sr / 2:
            break
        y += np.sin(2 * math.pi * k * f0 * t) * (depth if k == k_cut else 1.0) / k
    return y


def test_unison_left_on_is_rejected_by_the_repetition_precondition():
    f0 = 110.0
    y = unison(f0, SR)
    assert am.waveform_id(ideal("saw", f0, SR), f0, SR).ok, "control: the clean saw qualifies"
    w = am.waveform_id(y, f0, SR)
    assert not w.ok and "does not repeat" in w.reason, w


def test_an_effect_left_in_the_path_is_rejected_by_the_repetition_precondition():
    f0 = 110.0
    w = am.waveform_id(chorused(ideal("saw", f0, SR)), f0, SR)
    assert not w.ok and "does not repeat" in w.reason, w


def test_a_periodic_but_filtered_path_is_rejected_by_the_spectral_check():
    """The one a repetition test cannot see. An EQ does not disturb the period
    at all; the harmonic series is what refuses it."""
    f0 = 110.0
    y = notched(f0, SR)
    cyc, jit = am.cycle_average(y, f0, SR)
    assert jit < 0.01, "an EQ leaves the record perfectly periodic"
    assert am.midpoint_crossings(cyc) == 2 and am.step_ratio(y) >= 4.0, \
        "and leaves it one ramp per period"
    w = am.waveform_id(y, f0, SR)
    assert not w.ok and "not a saw" in w.reason, w


def dc_blocked(x, sr: int = SR, fc: float = 40.0) -> np.ndarray:
    """One-pole high pass: what Surge does to its Classic oscillator. A square
    through it is no longer flat-topped -- each half decays from 0.283 to
    0.087 before the next edge -- so a "fraction of the period near the two
    levels" test reads 0.43 for it and would call a perfectly good square
    something else."""
    a = math.exp(-2 * math.pi * fc / sr)
    y = np.empty_like(np.asarray(x, dtype=float))
    prev_x = prev_y = 0.0
    for i, v in enumerate(x):
        prev_y = a * (prev_y + v - prev_x)
        prev_x = v
        y[i] = prev_y
    return y


def test_a_dc_blocked_square_is_still_a_square():
    """The measurement that forced the family test to count JUMPS instead of
    flatness. This is not hypothetical: it is what Surge's Classic oscillator
    actually hands back."""
    f0 = 110.0
    y = dc_blocked(ideal("square", f0, SR))[SR // 4:]
    cyc, _ = am.cycle_average(y, f0, SR)
    assert am.rectangularity(cyc) < 0.60, \
        "a flatness test would not recognise this, which is the point"
    assert len(am.pulse_edges(cyc)) == 2 and am.step_ratio(y) >= 4.0
    w = am.waveform_id(y, f0, SR)
    assert w.ok and w.family == "pulse" and abs(w.duty - 0.5) < 0.02, w


def test_a_dc_blocked_narrow_pulse_keeps_its_measured_duty():
    """The duty comes from the two jump positions, not from a midpoint, so a
    tilt that moves every level does not move the answer."""
    f0 = 110.0
    y = dc_blocked(ideal("pulse", f0, SR, duty=0.25))[SR // 4:]
    cyc, _ = am.cycle_average(y, f0, SR)
    got = am.duty_cycle(cyc)
    assert min(abs(got - 0.25), abs(got - 0.75)) < 0.02, got
    assert am.waveform_matches(am.waveform_id(y, f0, SR), "pulse25")[0]


@pytest.mark.parametrize("shape,duty,want", [
    ("saw", 0.5, 1), ("square", 0.5, 2), ("pulse", 0.25, 2), ("pulse", 0.10, 2),
])
def test_jump_count_is_the_family_and_it_survives_a_dc_block(shape, duty, want):
    f0 = 110.0
    for y in (ideal(shape, f0, SR, duty=duty),
              dc_blocked(ideal(shape, f0, SR, duty=duty))[SR // 4:]):
        cyc, _ = am.cycle_average(y, f0, SR)
        assert am.step_ratio(y) >= 4.0
        assert len(am.pulse_edges(cyc)) == want, (shape, am.pulse_edges(cyc))


# -- the fundamental is measured, not assumed --------------------------------
@pytest.mark.parametrize("cents", [0.0, 0.14, -3.0, 20.0])
def test_refine_f0_recovers_a_small_mistuning(cents):
    """Mini V3 plays +0.14 cents sharp. That is inaudible and it is the whole
    difference between a 0.6 % per-period residual and a 25 % one."""
    f0 = 440.0
    f = f0 * 2 ** (cents / 1200.0)
    x = ideal("saw", f, SR)
    e = am.refine_f0(x, f0, SR)
    assert e.ok, e
    assert abs(1200 * math.log2(e.value / f)) < 0.02, (cents, e.value, f)


def test_refine_f0_refuses_a_rig_playing_the_wrong_note():
    """The Mini V3 'Range' default is the Model D's sub-audio octave. A rig an
    octave out is not a measurement with an offset, it is a different note."""
    f0 = 440.0
    e = am.refine_f0(ideal("saw", 220.0, SR), f0, SR)
    assert not e.ok and "fundamental is below the commanded" in e.reason, e
    # and a note that is simply mistuned, not transposed, refuses differently
    e2 = am.refine_f0(ideal("saw", f0 * 2 ** (80 / 1200), SR), f0, SR)
    assert not e2.ok and "no component within 50 cents" in e2.reason, e2


def test_the_mistuning_that_breaks_repetition_is_repaired_by_measuring_it():
    """End to end: a saw 0.14 cents sharp fails the repetition precondition at
    the COMMANDED pitch and passes at the measured one, with the same audio."""
    f0 = 1760.0
    x = ideal("saw", f0 * 2 ** (0.14 / 1200.0), SR)
    assert not am.waveform_id(x, f0, SR).ok
    f = am.refine_f0(x, f0, SR).require("refine")
    w = am.waveform_id(x, f, SR)
    assert w.ok and w.label == "saw", w


# ===========================================================================
# 9. THE REPORT ITSELF PRINTS WHAT THE MODULE SAYS IT PRINTS
#
# `reference_voice`'s docstring promises two jobs: COMPARE, and TARGET for the
# shark-tooth we do not have. Section 4 of the oscillator report is the whole
# of the TARGET half, and for three commits it sat AFTER `_floor_fmt`'s
# returns -- unreachable, so `--stage osc` never printed it and a later commit
# edited its text (DR 0017, issue #48) without that text ever reaching a
# reader. A section nobody can see is the same failure class as a measurement
# nobody made: the interface claims something the implementation does not do.
#
# These tests drive `report_osc` directly on synthetic rows -- no plugin, no
# audio -- so they run anywhere and fail red the moment a section stops
# printing. The AST check is the general form: no statement in this module may
# sit after a return in the same block, which is what made the section
# invisible in the first place.
# ===========================================================================
def _row(device, wave, note, **kw):
    """One `measure()`-shaped row, with every key `report_osc` reads present
    and defaulted, so a test states only the fields it is about."""
    r = dict(device=device, wave=wave, note=note, f0_cmd=vf.note_hz(note),
             tag=f"{device}/{wave}/{note}", verified=True, verify_why="",
             wave_label=wave, wave_reason="", steady=True, valid=True,
             invalid_harmonics=[], period_residual=0.0002, crossings=2, jumps=1,
             step_ratio=8.0, duty_measured=0.5, inharmonic_db=-90.0,
             half_period_corr=None, rectangularity=None, n_valid=8)
    for k in range(2, rv.KMAX + 1):
        r[f"h{k}"] = 20 * math.log10(1.0 / k)
    r.update(kw)
    return r


def _osc_report(rows):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rv.report_osc(rows)
    return buf.getvalue()


def _shark_rows(notes=(45, 81)):
    return [_row("miniv3", "shark", n, verified=False, steady=True,
                 verify_why="no closed form to check the shape against",
                 wave_label=None, inharmonic_db=-61.0 + n / 10.0) for n in notes]


def test_the_oscillator_report_prints_every_section_it_numbers():
    """Sections 0 through 4 are the report's own contract. Section 4 was
    unreachable code for three commits and no run ever said so."""
    rows = [_row("ideal", "saw", 45, inharmonic_db=-113.0),
            _row("ours", "saw", 45, inharmonic_db=-88.0)] + _shark_rows()
    out = _osc_report(rows)
    for section in ("0. WAVEFORM QUALIFICATION", "1. HARMONIC SERIES",
                    "2. WHICH RECTANGULAR IS OURS?", "3. ALIASING",
                    "4. TARGET"):
        assert section in out, (section, out)


def test_the_shark_tooth_target_section_reports_the_rows_it_is_about():
    """The TARGET half of the study: Mini V3's shark-tooth, STEADY but not
    VERIFIED, one line per pitch with its harmonics and its aliasing."""
    rows = [_row("ours", "saw", 45), _row("ideal", "saw", 45)] + _shark_rows()
    out = _osc_report(rows).split("4. TARGET", 1)[1]
    for note in (45, 81):
        assert f"{vf.note_hz(note):7.1f} Hz" in out, (note, out)
    assert "BLEP and BLAMP" in out and "measure_shark_blamp.py" in out, out


def test_the_target_section_is_silent_when_no_shark_row_qualifies():
    """A row that is not STEADY is not a target description -- the heading
    prints, the unqualified row does not."""
    rows = [_row("ours", "saw", 45), _row("ideal", "saw", 45)] + [
        _row("miniv3", "shark", 45, verified=False, steady=False,
             verify_why="does not repeat at the commanded pitch")]
    out = _osc_report(rows).split("4. TARGET", 1)[1]
    assert f"{vf.note_hz(45):7.1f} Hz" not in out, out


def test_no_statement_in_reference_voice_sits_after_a_return():
    """The general form of the defect: a refactor inserted two functions into
    the middle of `report_osc` and left its last section attached to
    `_floor_fmt`, after that function's returns. Python raises nothing for
    that -- it simply never runs the code."""
    src = os.path.join(HERE, "reference_voice.py")
    tree = ast.parse(open(src).read(), filename=src)
    dead = []
    for node in ast.walk(tree):
        body = getattr(node, "body", None)
        if not isinstance(body, list):
            continue
        for i, stmt in enumerate(body[:-1]):
            if isinstance(stmt, (ast.Return, ast.Raise, ast.Continue, ast.Break)):
                dead.append((stmt.lineno, body[i + 1].lineno))
    assert not dead, (f"unreachable statements in {src}, as "
                      f"(return at line, dead code at line): {dead}")
