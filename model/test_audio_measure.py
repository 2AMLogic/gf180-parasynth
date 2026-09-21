#!/usr/bin/env python3
"""Ground truth for `model/audio_measure.py`.

Every estimator is exercised against signals built from a closed form, so the
right answer is known before the measurement is made. This file exists because
on 2026-09-18 four "defects" in this repository turned out to be measurement
errors: analysis code that had never been run against a signal whose answer was
known. An acceptance suite built on untested analysers measures the analysers.

    .venv/bin/python -m pytest model/test_audio_measure.py -q

The awkward cases are deliberate and each one is a real failure that happened:
tau shorter than the carrier period, a window holding less than one cycle,
several hits at unequal amplitudes, a strong attack over a weak long tail,
stationary noise (which has no decay time at all), silence, clipping and a
signal down at the quantisation floor.
"""
from __future__ import annotations

import math
import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import audio_measure as am
from audio_measure import InsufficientEvidence

SR = 48000


# ---------------------------------------------------------------------------
# signal generators -- closed forms, so the answer is known
# ---------------------------------------------------------------------------
def damped(f, tau, seconds=1.0, amp=1.0, phase=0.0, sr=SR):
    t = np.arange(int(seconds * sr)) / sr
    return amp * np.exp(-t / tau) * np.sin(2 * math.pi * f * t + phase)


def two_pole_ir(f0, q, n=4096, numerator="raw", sr=SR):
    """The impulse response of exactly the modal bank's recursion:
    y[n] = x[n] + a1 y[n-1] + a2 y[n-2], r = exp(-pi f0/(Q fs)), and the
    numerator the bank selects. Its magnitude response is known in closed form,
    which `analytic_response` computes."""
    r = math.exp(-math.pi * f0 / (q * sr))
    w = 2 * math.pi * f0 / sr
    a1, a2 = 2 * r * math.cos(w), -r * r
    x = np.zeros(n)
    if numerator == "raw":
        x[0] = 1.0
    elif numerator == "bp":                      # 1 - z^-2
        x[0], x[2] = 1.0, -1.0
    elif numerator == "hp":                      # (1 - z^-1)^2
        x[0], x[1], x[2] = 1.0, -2.0, 1.0
    y = np.zeros(n)
    for i in range(n):
        acc = x[i]
        if i >= 1:
            acc += a1 * y[i - 1]
        if i >= 2:
            acc += a2 * y[i - 2]
        y[i] = acc
    return y, (a1, a2)


def analytic_response(f0, q, numerator="raw", sr=SR, npts=200001):
    """|H(e^jw)| of the same filter, evaluated directly. Ground truth for the
    transfer-response estimators."""
    r = math.exp(-math.pi * f0 / (q * sr))
    w0 = 2 * math.pi * f0 / sr
    a1, a2 = 2 * r * math.cos(w0), -r * r
    f = np.linspace(0, sr / 2, npts)
    z = np.exp(-2j * math.pi * f / sr)
    num = {"raw": 1.0 + 0 * z, "bp": 1 - z ** 2, "hp": (1 - z) ** 2}[numerator]
    return f, np.abs(num / (1 - a1 * z - a2 * z ** 2))


def noise(n, seed, sr=SR):
    return np.random.default_rng(seed).normal(0.0, 1.0, n)


def band_noise(n, lo, hi, seed, sr=SR):
    """White noise masked to [lo, hi] in the frequency domain: stationary, with
    a known band and no line structure."""
    x = noise(n, seed)
    X = np.fft.rfft(x)
    f = np.fft.rfftfreq(n, 1 / sr)
    X[(f < lo) | (f > hi)] = 0
    return np.fft.irfft(X, n)


def square_mix(freqs, seconds=1.0, sr=SR, seed=0):
    """A sum of square waves at `freqs` with random phases -- the shape of the
    808's six-Schmitt-trigger bank."""
    rng = np.random.default_rng(seed)
    t = np.arange(int(seconds * sr)) / sr
    return sum(np.sign(np.sin(2 * math.pi * f * t + rng.uniform(0, 2 * math.pi))) for f in freqs)


# ===========================================================================
# tau, T20, and the two conventions
# ===========================================================================
def test_t20_is_ln10_times_tau():
    """The conversion itself, checked against a measured -20 dB crossing rather
    than against the formula it came from. tau = 39.5 ms is a T20 of 91 ms, not
    a "50 ms decay": confusing the two is how a decay control was declared
    broken."""
    tau = 0.0395
    t = np.arange(int(0.5 * SR)) / SR
    env = np.exp(-t / tau)
    t20 = float(t[np.argmax(env <= 0.1)])
    assert abs(t20 - am.t20_from_tau(tau)) <= 1e-4, f"T20 {t20*1e3:.2f} ms vs {am.t20_from_tau(tau)*1e3:.2f}"
    assert abs(am.t20_from_tau(tau) - 0.0910) <= 0.0005
    assert abs(am.tau_from_t20(am.t20_from_tau(tau)) - tau) <= 1e-12


@pytest.mark.parametrize("f,tau", [
    (56.0, 0.029), (56.0, 0.127), (56.0, 0.352),        # the 808 bass drum's three DECAY settings
    (90.0, 0.092), (185.0, 0.044), (173.0, 0.030),      # toms and snare
    (540.0, 0.025), (3450.0, 0.010), (7100.0, 0.003),   # cowbell, cymbal band, hat band
])
def test_decay_tau_recovers_a_known_time_constant(f, tau):
    """The central case: an exponentially damped sinusoid of known f and tau.
    +-5 %, which is far tighter than any tolerance the 808 suite applies, so
    the estimator is never the limiting factor there."""
    x = damped(f, tau, seconds=max(8 * tau, 0.05))
    e = am.decay_tau(x, SR)
    got = e.require("decay_tau")
    assert abs(got / tau - 1) <= 0.08, f"f={f} tau={tau*1e3:.1f} ms measured {got*1e3:.2f} ms ({e})"


def test_decay_tau_refuses_when_the_carrier_is_too_low_to_have_an_envelope():
    """56 Hz decaying with tau = 5 ms is 0.28 of a cycle: the decay's own
    bandwidth is wider than the carrier, so there is no envelope to fit and the
    analytic envelope ripples at twice the carrier. The estimator must refuse
    and hand the caller to `damped_sinusoid`, which is exact there -- a
    plausible number would be silently 15 % wrong."""
    x = damped(56.0, 0.005, seconds=0.6)
    e = am.decay_tau(x, SR)
    assert not e.ok, f"returned {e} for a decay shorter than its own carrier period"
    d = am.damped_sinusoid(x, SR)
    assert abs(d.tau.require("tau") / 0.005 - 1) <= 0.05
    assert abs(d.freq.require("freq") / 56.0 - 1) <= 0.05


@pytest.mark.parametrize("f,tau", [(56.0, 0.002), (56.0, 0.005), (130.0, 0.004), (90.0, 0.003)])
def test_damped_sinusoid_works_when_tau_is_shorter_than_the_period(f, tau):
    """tau shorter than one carrier period: 2 ms at 56 Hz is 0.11 of a cycle.
    An FFT cannot resolve the frequency and an envelope fit has almost nothing
    to fit, but the two-pole recursion is exact here -- this is the case that
    produced a wrong answer, and it is the case the bass drum's 4 ms / 130 Hz
    attack lives in."""
    x = damped(f, tau, seconds=0.05)
    d = am.damped_sinusoid(x, SR)
    assert abs(d.freq.require("freq") / f - 1) <= 0.02, f"f {d.freq} vs {f}"
    assert abs(d.tau.require("tau") / tau - 1) <= 0.05, f"tau {d.tau} vs {tau}"


def test_damped_sinusoid_works_on_less_than_one_cycle():
    """A 4 ms window at 130 Hz holds half a cycle. Ground truth for the check
    the bass-drum attack test makes."""
    x = damped(130.0, 0.015, seconds=0.004)
    d = am.damped_sinusoid(x, SR)
    assert abs(d.freq.require("freq") / 130.0 - 1) <= 0.05, f"{d.freq} vs 130 Hz on half a cycle"


def test_damped_sinusoid_matches_the_coefficients_that_generated_it():
    """The control path and the audio path must agree when nothing is wrong:
    `poles_to_freq_tau` on the written coefficients and `damped_sinusoid` on
    the rendered ring give the same answer. When they disagree, the defect is
    downstream of the coefficients."""
    for f0, q in ((56.0, 22.3), (185.0, 25.0), (336.0, 9.9)):
        ir, (a1, a2) = two_pole_ir(f0, q, n=int(0.6 * SR))
        cf, ct = am.poles_to_freq_tau(a1, a2, SR)
        d = am.damped_sinusoid(ir[8:], SR)
        assert abs(d.freq.require() / cf.require() - 1) <= 0.01
        assert abs(d.tau.require() / ct.require() - 1) <= 0.02
        assert abs(ct.require() - q / (math.pi * f0)) <= 0.05 * q / (math.pi * f0)


def test_decay_tau_refuses_stationary_noise():
    """Noise has no decay time. An estimator that returns one anyway will
    happily report a decay for a voice that never decays."""
    for seed in (1, 2, 3):
        e = am.decay_tau(noise(int(0.5 * SR), seed), SR)
        assert not e.ok, f"seed {seed}: reported tau {e.value} for stationary noise"


def test_decay_tau_refuses_silence():
    e = am.decay_tau(np.zeros(SR), SR)
    assert not e.ok and "silent" in e.reason
    with pytest.raises(InsufficientEvidence):
        e.require("silence")


def test_decay_tau_refuses_two_exponentials_and_measures_the_tail_when_told_where():
    """A strong short attack over a weak long tail is two exponentials. Fitted
    from the peak it is neither, and the estimator must refuse rather than
    return the average of the two. Given the tail's window it must get the tail
    right -- this is the clap, and the cowbell's two-slope envelope."""
    t = np.arange(int(0.6 * SR)) / SR
    carrier = np.sin(2 * math.pi * 300 * t)
    x = (1.0 * np.exp(-t / 0.004) + 0.10 * np.exp(-t / 0.120)) * carrier
    whole = am.decay_tau(x, SR)
    assert not whole.ok, f"fitted a single exponential to two: {whole}"
    tail = am.decay_tau(x, SR, start_s=0.045).require("tail")
    assert abs(tail / 0.120 - 1) <= 0.10, f"tail tau {tail*1e3:.1f} ms, truth 120 ms"


def test_decay_tau_reports_a_clipped_decay_as_not_exponential():
    """A clipped decay is flat then exponential. `clipped_fraction` sees it, and
    the fit must not quietly return a longer tau."""
    x = damped(120.0, 0.100, seconds=0.6, amp=4.0)
    xc = np.clip(x, -1.0, 1.0)
    assert am.clipped_fraction(xc, 1.0) > 0.01, "the test signal is not actually clipped"
    assert am.clipped_fraction(xc * 0.4, 1.0) == 0.0, "an unclipped signal must not be flagged"
    assert not am.decay_tau(xc, SR).ok, "a clipped decay was fitted as a single exponential"
    assert abs(am.decay_tau(xc, SR, start_s=0.25, end_s=0.55).require("tail") / 0.100 - 1) <= 0.08


def test_quantisation_floor_is_reported():
    """A signal a few LSB tall carries no usable spectrum, and saying so is part
    of the estimator's job."""
    x = np.round(damped(200.0, 0.05, seconds=0.3) * 2.0)
    assert am.quantisation_floor(x, 1.0) < 12.0
    assert am.quantisation_floor(damped(200.0, 0.05, seconds=0.3) * 20000, 1.0) > 80.0


# ===========================================================================
# the moving-average envelope: the specific failure of 2026-09-18
# ===========================================================================
def test_an_unsmoothed_envelope_reproduces_the_false_decay_defect():
    """The measurement error that was reported as a broken DECAY control.

    A 127 ms bass-drum ring at 56 Hz, fitted on the RAW |x| envelope, measures
    3.8 ms: the peak-finder lands on a carrier excursion and the fit never
    leaves the first cycle. Add the 1 ms click that sits on the drum section's
    mix bus and it measures 40 ms -- and 40 ms again at every other DECAY
    setting, because what is being measured is the click, not the drum. That is
    the "tau = 39.5 ms at all three settings" report.

    The analytic estimator must never silently return a wrong number here: it
    either refuses (a click plus a ring is two things, not one exponential) or
    it is right. Told where the ring starts, it is right."""
    t = np.arange(int(1.2 * SR)) / SR
    ring = np.exp(-t / 0.127) * np.sin(2 * math.pi * 56.0 * t)
    click = np.zeros_like(ring)
    click[:int(0.001 * SR)] = 1.0
    def naive(env):
        p = int(np.argmax(env)); pk = env[p]; tail = env[p:]
        b = np.where(tail < pk * 10 ** (-35 / 20.0))[0]
        fit = np.maximum(tail[:(b[0] if len(b) else len(tail))], pk * 1e-9)
        tt = np.arange(len(fit)) / SR
        w = fit / fit.max()
        A = np.vstack([tt, np.ones_like(tt)]).T
        sol, *_ = np.linalg.lstsq(A * w[:, None], np.log(fit) * w, rcond=None)
        return -1.0 / sol[0]
    assert naive(np.abs(ring)) < 0.010, "the raw-|x| fit was accurate here; re-derive this warning"
    assert 0.030 < naive(np.abs(ring + click)) < 0.050, "the click-dominated fit no longer lands near 40 ms"
    got = am.decay_tau(ring + click, SR)
    assert not got.ok, f"the analytic fit accepted a click plus a ring as one exponential: {got}"
    told = am.decay_tau(ring + click, SR, start_s=0.005).require("ring after the click")
    assert abs(told / 0.127 - 1) <= 0.08, f"told where the ring is, measured {told*1e3:.1f} ms"
    assert abs(am.decay_tau(ring, SR).require("clean ring") / 0.127 - 1) <= 0.08


def test_moving_average_envelope_ripples_where_the_analytic_one_does_not():
    """A 5 ms moving average spans 0.28 of a cycle at 56 Hz. The analytic
    envelope of a damped sinusoid is the exponential itself to a fraction of a
    dB; the moving average ripples at twice the carrier by several dB, which is
    what puts a peak-finder on the wrong sample."""
    t = np.arange(int(0.6 * SR)) / SR
    truth = np.exp(-t / 0.127)
    x = truth * np.sin(2 * math.pi * 56.0 * t)
    n0, n1 = int(0.004 * SR), int(0.38 * SR)                 # down to about -30 dB
    a = am.analytic_envelope(x)[n0:n1]
    m = am.moving_average_envelope(x, 5.0, SR)[n0:n1] * (math.pi / 2)   # mean|sin| correction
    ref = truth[n0:n1]
    err_a = float(np.max(np.abs(20 * np.log10(a / ref))))
    err_m = float(np.max(np.abs(20 * np.log10(np.maximum(m, 1e-12) / ref))))
    assert err_a <= 1.0, f"analytic envelope off by {err_a:.2f} dB"
    assert err_m >= 3.0, f"the moving average only rippled {err_m:.2f} dB; re-derive the guidance"


def test_analytic_envelope_of_a_damped_sinusoid_is_the_exponential():
    for f in (40.0, 56.0, 300.0, 7100.0):
        t = np.arange(int(0.3 * SR)) / SR
        truth = np.exp(-t / 0.05)
        e = am.analytic_envelope(truth * np.sin(2 * math.pi * f * t))
        n0, n1 = int(0.005 * SR), int(0.17 * SR)      # to about -30 dB; below that the
        err = float(np.max(np.abs(20 * np.log10(e[n0:n1] / truth[n0:n1]))))   # transform's own
        assert err <= 1.5, f"f={f}: analytic envelope off by {err:.2f} dB"    # ringing dominates


# ===========================================================================
# onsets
# ===========================================================================
def test_onsets_finds_hits_at_unequal_amplitudes():
    """Three hits at 1.0, 0.22 and 0.55 -- a solo render at accent 1.0, 0.6 and
    1.4. An absolute threshold misses the quiet one; measuring from the first
    onset to the global peak across all three invents an attack time of
    hundreds of ms, which is exactly what was reported for seven of eight
    voices."""
    n = int(2.4 * SR)
    x = np.zeros(n)
    truth = [0.05, 0.75, 1.45]
    for t0, amp in zip(truth, (1.0, 0.22, 0.55)):
        seg = damped(90.0, 0.08, seconds=0.6, amp=amp)
        i = int(t0 * SR)
        x[i:i + len(seg)] += seg
    got = am.onsets(x, SR, min_gap_s=0.1)
    assert len(got) == 3, f"found {len(got)} onsets at {[round(i/SR,3) for i in got]}, truth {truth}"
    for g, t0 in zip(got, truth):
        # 10 ms, which is what the Hilbert precursor leaves: enough to tell one
        # hit from another, nowhere near enough to measure an attack time.
        assert abs(g / SR - t0) <= 0.010, f"onset {g/SR:.4f} s vs {t0} s"


def test_onsets_are_not_hidden_by_the_hilbert_precursor():
    """The analytic envelope rises tens of ms BEFORE a sharp strike, because
    the Hilbert transform is not causal. With a 10 ms run-in before the first
    hit that precursor flattens the rise enough to hide it: the analytic
    detector found two onsets, in the wrong places, where the RMS detector
    finds three in the right ones."""
    n = int(1.6 * SR)
    x = np.zeros(n)
    truth = [0.010, 0.510, 1.010]
    for t0, amp in zip(truth, (1.0, 0.6, 1.4)):
        seg = damped(56.0, 0.127, seconds=0.5, amp=amp)
        i = int(t0 * SR)
        x[i:i + len(seg)] += seg
    got = am.onsets(x, SR, min_gap_s=0.1)
    assert len(got) == 3, f"found {[round(i/SR, 4) for i in got]}, truth {truth}"
    for g, t0 in zip(got, truth):
        # 10 ms, which is what the precursor leaves: enough to tell one hit
        # from another, nowhere near enough to measure an attack time.
        assert abs(g / SR - t0) <= 0.010, f"onset {g/SR:.4f} s vs {t0} s"


def test_onsets_on_silence_is_empty():
    assert am.onsets(np.zeros(SR), SR) == []


# ===========================================================================
# spectral lines
# ===========================================================================
def test_dominant_frequency_on_known_tones():
    for f in (56.0, 173.0, 336.0, 540.0, 800.0, 7100.0):
        x = damped(f, 0.2, seconds=0.5)
        got = am.dominant_frequency(x, f * 0.6, f * 1.6, SR).require()
        assert abs(got / f - 1) <= 0.01, f"{got:.2f} vs {f}"


def test_dominant_frequency_refuses_a_band_with_no_line():
    x = damped(200.0, 0.1, seconds=0.4)
    assert not am.dominant_frequency(x, 3000.0, 6000.0, SR).ok
    assert not am.dominant_frequency(np.zeros(SR), 100.0, 1000.0, SR).ok


def test_line_at_resolves_two_close_tones():
    """540 and 800 Hz together -- the cowbell's two trimmed oscillators."""
    t = np.arange(int(0.25 * SR)) / SR
    x = np.sin(2 * math.pi * 540 * t) + 0.32 * np.sin(2 * math.pi * 800 * t)
    lo = am.line_at(x, 540.0, SR)
    hi = am.line_at(x, 800.0, SR)
    assert abs(lo.require() - 540.0) <= 3.0
    assert abs(hi.require() - 800.0) <= 3.0
    ratio = 20 * math.log10(hi.detail["level"] / lo.detail["level"])
    assert abs(ratio - 20 * math.log10(0.32)) <= 1.5, f"level ratio {ratio:.2f} dB vs {20*math.log10(0.32):.2f}"


def test_spectral_lines_counts_a_known_comb_and_a_known_noise():
    """A six-square mixture at the 808's oscillator frequencies against
    band-limited noise of the same bandwidth. The count separates them here --
    but see the next test, which is the reason a count alone is not evidence of
    a topology."""
    band = (2000.0, 16000.0)
    comb = am.spectral_lines(square_mix((205.3, 369.6, 304.4, 522.7, 800.0, 540.0), 0.25), band, SR)
    noi = am.spectral_lines(band_noise(int(0.25 * SR), 2000.0, 16000.0, seed=7), band, SR)
    assert comb.count >= 20, f"six squares gave only {comb.count} lines"
    assert noi.count <= comb.count / 4, f"noise gave {noi.count} lines against the comb's {comb.count}"
    assert comb.peak_to_median > noi.peak_to_median


def test_a_peak_count_alone_does_not_prove_an_oscillator_bank():
    """Narrow-band noise shows plenty of prominent peaks, and they are not
    oscillators. This is why the 808 suite never asserts "N peaks means six
    oscillators": the peaks must also be in the SAME places in every window,
    and a matched noise control must fail the same test."""
    x = band_noise(int(1.0 * SR), 6000.0, 9000.0, seed=11)
    strict = am.spectral_lines(x, (6000.0, 9000.0), SR, threshold=4.0).count
    loose = am.spectral_lines(x, (6000.0, 9000.0), SR, threshold=2.0).count
    assert loose >= 20, f"noise gave {loose} peaks at threshold 2; the count depends on the threshold"
    assert strict <= loose / 4, "the count is supposed to depend strongly on the threshold"
    stab = am.line_stability(x, (6000.0, 9000.0), SR, windows=4, tol_hz=40.0, threshold=2.0).require()
    assert stab <= 0.35, f"noise line positions were stable at {stab:.2f}; the discriminator is broken"


def test_line_stability_separates_oscillators_from_noise():
    """The discriminator that a peak count is not. Free-running oscillators put
    their lines in the same places in every window; noise does not."""
    band = (2000.0, 16000.0)
    comb = am.line_stability(square_mix((205.3, 369.6, 304.4, 522.7, 800.0, 540.0), 0.4),
                             band, SR, windows=4, tol_hz=40.0, threshold=2.0).require()
    for seed in (3, 4, 5):
        noi = am.line_stability(band_noise(int(0.4 * SR), 2000.0, 16000.0, seed), band, SR,
                                windows=4, tol_hz=40.0, threshold=2.0).require()
        assert comb >= 0.75, f"oscillator lines only {comb:.2f} stable"
        assert noi <= 0.35, f"noise lines {noi:.2f} stable (seed {seed})"


def test_spectral_flatness_measures_density_not_determinism():
    """Why flatness must not be used to decide "oscillators or noise".

    These four signals are all sums of pure sinusoids -- perfectly
    deterministic, perfectly stable line positions -- and differ only in how
    many lines they pack into the same band. Their flatness spans more than
    three orders of magnitude, crossing whatever threshold anyone might pick,
    while `line_stability` reports 1.0 for every one of them. Flatness answers
    "how dense", not "is it deterministic", and reading it as the latter is how
    a false defect was reported on the hi-hats."""
    band = (2000.0, 16000.0)
    t = np.arange(int(0.25 * SR)) / SR
    flat = {}
    for n_lines in (100, 200, 300):
        rng = np.random.default_rng(5)
        x = sum(rng.uniform(0.3, 1.0) * np.sin(2 * math.pi * f * t + rng.uniform(0, 2 * math.pi))
                for f in np.linspace(band[0], band[1], n_lines))
        flat[n_lines] = am.spectral_flatness(x, band, SR)
        stab = am.line_stability(x, band, SR, windows=4, tol_hz=40.0, threshold=2.0).require()
        assert stab >= 0.95, f"{n_lines} pure tones were only {stab:.2f} stable"
    spread = max(flat.values()) / min(flat.values())
    assert spread > 50.0, (
        f"flatness varied only {spread:.0f}x across combs of 100 to 300 pure tones "
        f"({flat}); if it were insensitive to density it might be usable")
    noi = am.spectral_flatness(band_noise(int(0.25 * SR), *band, seed=9), band, SR)
    assert max(flat.values()) < noi, "the densest comb should still be flatter than noise"
    # Flatness is a continuum in density, so any threshold separating "comb"
    # from "noise" is really a threshold on density. On the real hi-hats --
    # six squares through a nonlinearity, a band-pass and a 45 ms envelope --
    # the measured values were 0.39 for the comb and 0.35 for the clap's true
    # noise, i.e. on the wrong side of every threshold. That is why the 808
    # suite decides with line stability and a matched noise control instead.


def test_spectral_lines_refuses_a_window_too_short_to_count():
    with pytest.raises(InsufficientEvidence):
        am.spectral_lines(np.zeros(256), (2000.0, 20000.0), SR)


# ===========================================================================
# transfer response -- filters
# ===========================================================================
@pytest.mark.parametrize("f0,q,num", [
    (7800.0, 2.5, "hp"), (11700.0, 2.5, "hp"),          # the two hi-hat high-passes
    (1071.0, 1.6, "bp"), (7117.0, 6.0, "bp"), (900.0, 4.0, "bp"),
])
def test_resonant_peak_matches_the_closed_form_response(f0, q, num):
    """The estimator against |H(e^jw)| evaluated directly. Note what is being
    checked: the frequency of the RESPONSE MAXIMUM, which for a resonant filter
    is not the same number as its nominal f0 -- the 808 suite compares against
    whichever the reference specifies, and converts."""
    ir, _ = two_pole_ir(f0, q, n=8192, numerator=num)
    f, H = analytic_response(f0, q, num)
    truth = float(f[int(np.argmax(H))])
    got = am.resonant_peak(ir, SR).require()
    assert abs(got / truth - 1) <= 0.01, f"{num} {f0} Hz Q{q}: peak {got:.0f} Hz vs closed form {truth:.0f} Hz"


@pytest.mark.parametrize("f0,q,num", [(1071.0, 1.6, "bp"), (7117.0, 6.0, "bp"), (900.0, 4.0, "bp")])
def test_bandwidth_q_matches_the_closed_form_response(f0, q, num):
    ir, _ = two_pole_ir(f0, q, n=16384, numerator=num)
    f, H = analytic_response(f0, q, num)
    i = int(np.argmax(H))
    half = H[i] / math.sqrt(2)
    lo = f[np.where(H[:i] < half)[0][-1]]
    hi = f[i + np.where(H[i:] < half)[0][0]]
    truth = f[i] / (hi - lo)
    got = am.bandwidth_q(ir, SR).require()
    assert abs(got / truth - 1) <= 0.05, f"Q {got:.2f} vs closed form {truth:.2f}"


def test_corner_3db_of_a_highpass_matches_the_closed_form():
    for f0, q in ((7800.0, 2.5), (2750.0, 0.7)):
        ir, _ = two_pole_ir(f0, q, n=16384, numerator="hp")
        f, H = analytic_response(f0, q, "hp")
        plateau = float(np.median(H[(f >= 0.65 * SR / 2) & (f <= 0.95 * SR / 2)]))
        truth = float(f[np.argmax(H >= plateau / math.sqrt(2))])
        got = am.corner_3db(ir, "highpass", SR).require()
        assert abs(got / truth - 1) <= 0.03, f"{f0} Hz Q{q}: corner {got:.0f} vs {truth:.0f}"


def test_resonant_peak_refuses_a_response_with_no_resonance():
    """A one-pole low-pass has no peak. Reporting its argmax as a centre
    frequency is a wrong answer that looks like a right one."""
    n = 8192
    a = math.exp(-2 * math.pi * 1000.0 / SR)
    ir = (1 - a) * a ** np.arange(n)
    assert not am.resonant_peak(ir, SR).ok
    with pytest.raises(InsufficientEvidence):
        am.resonant_peak(ir, SR).require("one-pole")


def test_transfer_refuses_a_silent_impulse_response():
    with pytest.raises(InsufficientEvidence):
        am.transfer(np.zeros(1024), SR)


def test_a_centroid_is_not_a_corner_frequency():
    """Both weightings of the centroid, on a known high-passed noise, land
    nowhere near the filter's corner -- and disagree with each other. This is
    the error that reported an open hat 41 % high; the corner is measured from
    the transfer response, never from the finished voice's centroid."""
    ir, _ = two_pole_ir(7800.0, 2.5, n=8192, numerator="hp")
    x = np.convolve(noise(int(0.3 * SR), 21), ir)[:int(0.3 * SR)]
    corner = am.corner_3db(ir, "highpass", SR).require()
    cp = am.spectral_centroid(x, (2000.0, 20000.0), SR, weight="power")
    ca = am.spectral_centroid(x, (2000.0, 20000.0), SR, weight="amplitude")
    assert abs(cp / corner - 1) > 0.15 or abs(ca / corner - 1) > 0.15, \
        "both centroids happened to land on the corner here; that is a coincidence, not a method"
    assert abs(ca - cp) / cp > 0.02, f"the two weightings agreed ({cp:.0f} vs {ca:.0f}); they answer different questions"


# ===========================================================================
# comparison: level and shape are separate claims
# ===========================================================================
def test_compare_does_not_hide_a_gain_error():
    """Two identical waveforms 6 dB apart: the shape figure says they match
    perfectly, and only the level figure shows the error. Quoting the shape
    figure alone -- which is what a normalise-both-then-diff helper does -- hides
    it completely."""
    b = damped(200.0, 0.08, seconds=0.5)
    a = b * 0.5
    c = am.compare(a, b)
    assert abs(c.level_db + 6.02) <= 0.05, f"level {c.level_db:.2f} dB"
    assert c.shape_db < -100.0, f"shape {c.shape_db:.1f} dB: identical waveforms"
    assert c.residual_db > -10.0, f"residual {c.residual_db:.1f} dB: a 6 dB gain error is not small"


# ===========================================================================
# bursts
# ===========================================================================
def test_envelope_bursts_finds_known_restrikes():
    """Three noise bursts at 5, 15 and 25 ms with descending levels, over a
    47 ms tail: the clap's shape, with the answer known. Two things make it
    measurable -- averaging over differently seeded renders (a single render of
    noise has envelope maxima everywhere), and a few ms of PRE-ROLL before the
    first burst, without which the analytic envelope has no run-in and the
    first burst reads low enough to come out in the wrong order."""
    pre, times, levels, tau = 0.015, (0.015, 0.025, 0.035), (1.0, 0.8, 0.65), 0.004
    n = int(0.3 * SR)
    renders = []
    for seed in range(16):
        env = np.zeros(n)
        env[int(pre * SR):] = 0.18 * np.exp(-np.arange(n - int(pre * SR)) / SR / 0.047)
        for t0, lv in zip(times, levels):
            i = int(t0 * SR)
            env[i:] += lv * np.exp(-np.arange(n - i) / SR / tau)
        renders.append(env * band_noise(n, 600.0, 1600.0, seed))
    e = am.average_envelope(renders)
    got = am.envelope_bursts(e, SR, window_s=0.045, level_frac=0.4, min_sep_s=0.005)
    assert len(got) == 3, f"found {len(got)} bursts at {[round(t*1e3,1) for t,_ in got]} ms"
    for (t, _), t0 in zip(got, times):
        assert abs(t - t0) <= 0.003, f"burst at {t*1e3:.1f} ms vs {t0*1e3:.0f} ms"
    assert [lv for _, lv in got] == sorted([lv for _, lv in got], reverse=True), \
        f"burst levels {[round(lv,3) for _, lv in got]} are not descending"
    # A noise-excited envelope is never a perfectly smooth exponential even
    # after averaging, so the single-exponential check is loosened -- not
    # removed, or a two-slope tail would pass.
    tail = am.decay_tau(e, SR, start_s=0.055, is_envelope=True,
                        max_residual_db=8.0).require("clap tail")
    assert abs(tail / 0.047 - 1) <= 0.15, f"tail tau {tail*1e3:.1f} ms vs 47 ms"


def test_envelope_bursts_on_a_single_decay_finds_one():
    """A plain exponential decay is one strike, not a train of them. Numerical
    ripple puts local maxima all over an envelope; only the dip requirement
    keeps this from reading as three bursts, which it did before."""
    e = am.analytic_envelope(damped(1000.0, 0.03, seconds=0.2))
    assert len(am.envelope_bursts(e, SR, window_s=0.10)) == 1


def test_decay_tau_on_an_already_made_envelope():
    """Taking the analytic envelope of an envelope measures the wrong thing:
    a low-pass positive signal is not a modulated carrier. `is_envelope=True`
    is the difference between 47 ms and 89 ms on the clap's tail."""
    t = np.arange(int(0.4 * SR)) / SR
    env = 0.18 * np.exp(-t / 0.047)
    good = am.decay_tau(env, SR, is_envelope=True).require("envelope")
    assert abs(good / 0.047 - 1) <= 0.05, f"{good*1e3:.1f} ms vs 47 ms"
    wrong = am.decay_tau(env, SR)
    assert (not wrong.ok) or abs(wrong.value / 0.047 - 1) > 0.25, \
        "treating an envelope as a carrier happened to work; re-derive the warning"


# ===========================================================================
# instantaneous frequency
# ===========================================================================
def test_instantaneous_frequency_tracks_a_known_glide():
    """A known exponential glide from 150 Hz to 90 Hz: the shape of the toms'
    diode pitch drop. Measured on the analytic phase, not on zero crossings."""
    n = int(0.4 * SR)
    t = np.arange(n) / SR
    f = 90.0 + 60.0 * np.exp(-t / 0.05)
    x = np.sin(2 * math.pi * np.cumsum(f) / SR) * np.exp(-t / 0.15)
    inst = am.instantaneous_frequency(x, SR, smooth_ms=2.0)
    i0, i1 = int(0.005 * SR), int(0.25 * SR)
    assert abs(inst[i0] / f[i0] - 1) <= 0.05, f"start {inst[i0]:.1f} vs {f[i0]:.1f} Hz"
    assert abs(inst[i1] / f[i1] - 1) <= 0.05, f"end {inst[i1]:.1f} vs {f[i1]:.1f} Hz"
    assert inst[i0] / inst[i1] > 1.4, "the glide was not seen at all"


def test_instantaneous_frequency_is_flat_for_a_steady_tone():
    x = damped(185.0, 0.2, seconds=0.5)
    inst = am.instantaneous_frequency(x, SR, smooth_ms=2.0)
    seg = inst[int(0.005 * SR):int(0.20 * SR)]
    assert abs(float(np.median(seg)) / 185.0 - 1) <= 0.01
    assert float(np.std(seg)) <= 2.0, f"a steady tone wobbled by {np.std(seg):.2f} Hz"


# ===========================================================================
# GROUND TRUTH FOR THE MONOSYNTH ADDITIONS (model/test_moog_acceptance.py)
#
# The voice's estimators, on the same terms as everything above: a signal
# whose answer is known in closed form, and a case each one must refuse.
# ===========================================================================
def _sine(f, n, amp=1.0, sr=SR):
    return amp * np.sin(2 * math.pi * f * np.arange(n) / sr)


def _noise(n, amp=1.0, seed=11):
    return amp * np.random.default_rng(seed).standard_normal(n)


def _series(f0, n, kmax, sr=SR):
    """An ideal 1/k harmonic series with no partial above Nyquist."""
    return sum(_sine(k * f0, n, 1.0 / k, sr) for k in range(1, kmax + 1))


def test_tonality_separates_a_tone_from_noise_where_a_percentile_does_not():
    """`tonality_db` is max-over-median, and a single sustained tone reads far
    above noise on it. `spectral_lines(...).peak_to_median` is a 99th
    percentile and reads 1.0 for the same tone -- the two answer different
    questions, which is why both exist."""
    n = 1 << 14
    tone, noi = _sine(1000.0, n), _noise(n)
    assert am.tonality_db(tone) > 40.0
    assert am.tonality_db(noi) < 15.0
    assert am.spectral_lines(tone, band=(20.0, 20000.0)).peak_to_median < 2.0


@pytest.mark.parametrize("amp", [1.0, 1e-3, 1e-5])
def test_tone_amplitude_recovers_a_known_amplitude_under_noise(amp):
    """Exact on a clean tone at any amplitude, and within 15 % with the tone
    20 dB UNDER a broadband floor: the rejection is 2/sqrt(N) of the noise
    amplitude. That is what makes the ladder's stopband readable where the
    signal is a fraction of an LSB."""
    n = 1 << 16
    assert abs(am.tone_amplitude(_sine(2000.0, n, amp), 2000.0).require() - amp) \
        < 1e-9 + 0.001 * amp
    noisy = _sine(2000.0, n, amp) + _noise(n, amp * 10)
    assert abs(am.tone_amplitude(noisy, 2000.0).require() - amp) < 0.15 * amp


def test_tone_amplitude_is_blind_to_the_other_partials():
    n = 1 << 15
    x = _sine(500.0, n, 0.1) + _sine(1000.0, n, 1.0) + _sine(1500.0, n, 1.0)
    assert abs(am.tone_amplitude(x, 500.0).require() - 0.1) < 0.005


def test_tone_amplitude_refuses_silence_a_short_record_and_a_bad_frequency():
    assert not am.tone_amplitude(np.zeros(48000), 1000.0).ok
    assert not am.tone_amplitude(_sine(1000.0, 96), 1000.0).ok        # 2 periods
    assert not am.tone_amplitude(_sine(1000.0, 48000), 30000.0).ok    # above Nyquist
    with pytest.raises(InsufficientEvidence):
        am.tone_amplitude(np.zeros(48000), 1000.0).require("stopband")


@pytest.mark.parametrize("f", [55.0, 440.0, 3333.0, 10000.0])
def test_zero_crossing_frequency_on_a_known_tone(f):
    assert abs(am.zero_crossing_frequency(_sine(f, 1 << 15, 0.5)).require() / f - 1.0) < 1e-4


def test_zero_crossing_frequency_survives_an_envelope_that_smears_an_fft():
    """60 dB of decay across the record. The crossings barely move -- 0.21 %
    low, the bias the envelope puts on the linear interpolation of each
    crossing -- where a windowed FFT of the same signal is spread by the
    envelope itself. Callers that need better than 0.5 % must measure a ring
    that is not decaying, which is what the self-oscillation onset is."""
    x = damped(300.0, 0.05, seconds=0.34)
    assert abs(am.zero_crossing_frequency(x).require() / 300.0 - 1.0) < 5e-3


def test_zero_crossing_frequency_refuses_silence_and_a_single_cycle():
    assert not am.zero_crossing_frequency(np.zeros(1000)).ok
    assert not am.zero_crossing_frequency(_sine(20.0, 1000)).ok


def test_harmonic_powers_recovers_a_known_series():
    """Amplitudes 1, 1/2, 1/3 ... must read -6.02, -9.54, -12.04 dB ..."""
    n = 1 << 15
    p = am.harmonic_powers(_series(220.0, n, 8), 220.0, range(1, 9))
    rel = 10 * np.log10(p / p[0])
    for k in range(2, 9):
        assert abs(rel[k - 1] - 20 * math.log10(1.0 / k)) < 0.15, k


def test_harmonic_powers_refuses_a_partial_above_nyquist_and_a_dense_f0():
    n = 1 << 14
    with pytest.raises(InsufficientEvidence):
        am.harmonic_powers(_sine(440.0, n), 440.0, [60])
    with pytest.raises(InsufficientEvidence):
        am.harmonic_powers(_sine(2.0, n), 2.0, [1])
    with pytest.raises(InsufficientEvidence):
        am.harmonic_powers(np.zeros(n), 440.0, [1])


@pytest.mark.parametrize("share", [0.01, 0.1])
def test_inharmonic_fraction_recovers_a_planted_inharmonic_tone(share):
    """Plant a tone at 1.5 f0 carrying a known share of the energy; the
    estimator must read that share back within 0.6 dB."""
    n = 1 << 15
    harm = _series(500.0, n, 11)
    a = math.sqrt(2 * share / (1 - share) * (harm ** 2).sum() / n)
    got = am.inharmonic_fraction_db(harm + _sine(1.5 * 500.0, n, a), 500.0).require()
    assert abs(got - 10 * math.log10(share)) < 0.6, got


def test_inharmonic_fraction_reads_a_clean_series_at_the_window_floor():
    """With nothing inharmonic present the measure returns its own leakage
    floor -- a refusal in all but name.

    **This test used to assert `-58 < got < -50`, the Hann figure the docstring
    quoted.** That is #92 in a test: it pinned a floor that is not a constant,
    and it would have had to be re-pinned by hand for any window or guard
    change. What is asserted now is the PROPERTY -- the reading is the floor,
    whatever the floor is -- against the floor the estimator measured for this
    exact call. Blackman-Harris puts it near -88 dB rather than -54."""
    e = am.inharmonic_fraction_db(_series(500.0, 1 << 15, 11), 500.0)
    assert e.ok, e.reason
    assert abs(e.detail["headroom_db"]) < 1.0, \
        f"value {e.value:.2f} dB, measured floor {e.detail['floor_db']:.2f} dB"
    assert e.value < -70.0, \
        f"the Blackman-Harris floor should be far below the Hann -54 dB, got {e.value:.2f}"


def test_inharmonic_fraction_refuses_silence_and_a_spectrum_full_of_guards():
    assert not am.inharmonic_fraction_db(np.zeros(1 << 14), 500.0).ok
    assert not am.inharmonic_fraction_db(_series(20.0, 1 << 12, 400), 20.0).ok


def test_foldback_finds_a_planted_image_and_ignores_the_real_harmonics():
    """Image one harmonic of a 1318.5 Hz saw where sampling would put it, with
    2 % of the energy, and require the estimator to find that 2 % -- and to
    read a series with no image at all as empty."""
    n = 1 << 15
    f0 = 1318.5
    harm = _series(f0, n, int(SR / 2 / f0) - 1)
    share = 0.02
    a = math.sqrt(2 * share / (1 - share) * (harm ** 2).sum() / n)
    e = am.foldback_alias_db(harm + _sine(am.fold_frequency(25 * f0), n, a), f0)
    assert e.detail["images"] > 20 and e.detail["collided"] == 0
    assert abs(e.require() - 10 * math.log10(share)) < 0.8, e
    assert math.isfinite(e.detail["alias_band_power_dbfs"])
    assert math.isfinite(e.detail["total_signal_power_dbfs"])
    assert 0.0 < e.detail["alias_band_rms_fs"] < 1.0
    assert am.foldback_alias_db(harm, f0).require() < -60.0


def test_foldback_absolute_band_and_total_energy_track_gain_but_fraction_does_not():
    n = 1 << 15
    f0 = 1318.5
    harm = _series(f0, n, int(SR / 2 / f0) - 1)
    image = _sine(am.fold_frequency(25 * f0), n, 0.03)
    quiet = am.foldback_alias_db(harm + image, f0)
    loud = am.foldback_alias_db(2.0 * (harm + image), f0)
    quiet.require("quiet alias")
    loud.require("loud alias")
    assert loud.detail["alias_band_power_dbfs"] - quiet.detail["alias_band_power_dbfs"] == pytest.approx(6.0206, abs=0.02)
    assert loud.detail["total_signal_power_dbfs"] - quiet.detail["total_signal_power_dbfs"] == pytest.approx(6.0206, abs=0.02)
    assert loud.value == pytest.approx(quiet.value, abs=1e-10)


def test_foldback_refuses_a_low_note_where_the_images_are_dense():
    """At 82 Hz the predicted images collide with real harmonics and cover the
    spectrum; the estimator must say so rather than return a number. The
    acceptance suite measures that register with `inharmonic_fraction_db`
    instead, and says which it used."""
    e = am.foldback_alias_db(_series(82.4, 1 << 15, 140), 82.4)
    assert not e.ok, e
    with pytest.raises(InsufficientEvidence):
        e.require("note 40 aliasing")


def test_fold_frequency_is_the_sampling_image():
    assert am.fold_frequency(1000.0) == pytest.approx(1000.0)
    assert am.fold_frequency(47000.0) == pytest.approx(1000.0)
    assert am.fold_frequency(49000.0) == pytest.approx(1000.0)
    assert am.fold_frequency(24000.0) == pytest.approx(24000.0)


def test_max_sample_step_finds_a_planted_click():
    x = _sine(100.0, 4800, 0.1)
    assert am.max_sample_step(x) < 0.002
    x[2000] += 0.5
    assert am.max_sample_step(x) > 0.49
    with pytest.raises(InsufficientEvidence):
        am.max_sample_step([1.0])


def test_longest_plateau_counts_a_stair_step():
    assert am.longest_plateau(np.arange(100)) == 1
    assert am.longest_plateau(np.repeat(np.arange(10), 7)) == 7
    assert am.longest_plateau(np.zeros(50)) == 50


def test_event_slices_splits_a_gate_into_notes():
    g = np.zeros(1000, dtype=int)
    g[100:200] = 1
    g[500:900] = 1
    assert am.event_slices(g) == [(100, 200), (500, 900)]
    assert am.event_slices(np.zeros(10)) == []
    assert am.event_slices(np.ones(10)) == [(0, 10)]


# ===========================================================================
# choosing the right envelope, and knowing when tau cannot be measured
# ===========================================================================
def test_analytic_and_rms_envelopes_agree_on_a_damped_sinusoid():
    """One component: both envelopes are the same exponential, so either
    estimator may be used and they must not disagree."""
    for f, tau in ((300.0, 0.050), (7100.0, 0.020)):
        x = damped(f, tau, seconds=8 * tau)
        a = am.decay_tau(x, SR).require("analytic")
        r = am.decay_tau(x, SR, envelope="rms", rms_window_ms=3.0).require("rms")
        assert abs(a / tau - 1) <= 0.08 and abs(r / tau - 1) <= 0.10, f"{a*1e3:.1f} / {r*1e3:.1f} vs {tau*1e3}"


def test_rms_envelope_is_the_right_envelope_for_a_broadband_voice():
    """A dense inharmonic comb under one exponential envelope: the shape of a
    hi-hat. Its INSTANTANEOUS amplitude swings by tens of dB as the components
    beat -- that is the signal, not an artefact -- so the analytic envelope is
    not monotone and its maximum lands on a beat. The short-time RMS is the
    envelope, and only it recovers the decay.

    This is the failure that made the open hat's "envelope" rise for 50 ms
    after the strike."""
    tau = 0.060
    t = np.arange(int(0.5 * SR)) / SR
    rng = np.random.default_rng(3)
    comb = sum(np.sin(2 * math.pi * f * t + rng.uniform(0, 2 * math.pi))
               for f in rng.uniform(6000, 14000, 40))
    x = comb * np.exp(-t / tau)
    a_env = am.analytic_envelope(x)
    r_env = am.rms_envelope(x, 5.0, SR)
    i0, i1 = int(0.004 * SR), int(0.10 * SR)
    swing = am.db(a_env[i0:i1].max(), np.median(a_env[i0:i1]))
    assert swing > 10.0, f"the comb's instantaneous amplitude only swung {swing:.1f} dB"
    assert am.db(r_env[i0:i1].max(), np.median(r_env[i0:i1])) < 8.0, "the RMS envelope is not smooth"
    got = am.decay_tau(x, SR, envelope="rms", rms_window_ms=5.0).require("rms decay")
    assert abs(got / tau - 1) <= 0.12, f"rms envelope measured {got*1e3:.1f} ms vs {tau*1e3:.0f} ms"
    a = am.decay_tau(x, SR)
    assert (not a.ok) or abs(a.value / tau - 1) > 0.15, \
        "the analytic envelope happened to work on a broadband voice; re-derive the rule"


def test_rms_envelope_recovers_a_noise_burst_decay():
    """Noise under an exponential, several seeds: the clap and the snare's
    snap. The analytic envelope of noise is Rayleigh-distributed sample by
    sample; the RMS envelope is the decay."""
    for seed in (1, 2, 3):
        tau = 0.047
        n = int(0.4 * SR)
        x = band_noise(n, 600.0, 1600.0, seed) * np.exp(-np.arange(n) / SR / tau)
        # One realisation of noise has a ragged RMS envelope even at 6 ms, so
        # the single-exponential check is loosened -- not removed, or a
        # two-slope tail would pass. The clap averages renders instead.
        got = am.decay_tau(x, SR, envelope="rms", rms_window_ms=6.0,
                           max_residual_db=11.0).require(f"seed {seed}")
        assert abs(got / tau - 1) <= 0.15, f"seed {seed}: {got*1e3:.1f} ms vs 47 ms"


@pytest.mark.parametrize("tau", [0.029, 0.127, 0.352])
def test_damped_sinusoid_refuses_a_tau_its_residual_cannot_resolve(tau):
    """The two-pole fit is exact on a clean signal and badly biased on a
    quantised one. A 16-bit integer ring at 56 Hz with tau = 127 ms has
    1 - r = 1.6e-4 per sample; the least-squares fit's own residual is of the
    same order, and it reported 63 ms -- half the truth -- with a residual that
    looked excellent.

    So the estimator must refuse tau when the fit residual is comparable with
    the per-sample decay, while still reporting the FREQUENCY, which survives.
    Where it does answer, it must be right."""
    x = np.round(damped(56.0, tau, seconds=min(9 * tau, 3.0), amp=16000.0)).astype(float)
    d = am.damped_sinusoid(x, SR)
    assert abs(d.freq.require("frequency") / 56.0 - 1) <= 0.01, "the frequency must survive quantisation"
    if d.tau.ok:
        assert abs(d.tau.value / tau - 1) <= 0.15, \
            f"accepted tau {d.tau.value*1e3:.1f} ms against a truth of {tau*1e3:.0f} ms"
    else:
        assert "unresolvable" in d.tau.reason
    env = am.decay_tau(x, SR).require("envelope tau")
    assert abs(env / tau - 1) <= 0.10, f"the envelope fit must still work: {env*1e3:.1f} vs {tau*1e3:.0f} ms"


def test_natural_frequency_from_peak_matches_the_closed_form():
    """f0, the -3 dB corner and the resonant peak are three different numbers
    for a resonant filter. At Q 2.5 the corner sits about 28 % below f0 and the
    peak about 4 % above it, so comparing a measured corner with a table's f0
    is an error of that size -- which is how a hi-hat high-pass looked 22 %
    wrong when it was 2 % right."""
    for f0, q in ((7800.0, 2.5), (11700.0, 2.5)):
        ir, _ = two_pole_ir(f0, q, n=16384, numerator="hp")
        peak = am.resonant_peak(ir, SR).require("peak")
        corner = am.corner_3db(ir, "highpass", SR).require("corner")
        assert abs(am.natural_frequency_from_peak(peak, q) / f0 - 1) <= 0.03, \
            f"peak {peak:.0f} Hz implies f0 {am.natural_frequency_from_peak(peak, q):.0f}, truth {f0}"
        assert corner < f0 * 0.85, f"the -3 dB corner {corner:.0f} Hz is not well below f0 {f0}"
        assert peak > f0, f"the resonant peak {peak:.0f} Hz is not above f0 {f0}"
    with pytest.raises(InsufficientEvidence):
        am.natural_frequency_from_peak(1000.0, 0.7)


# --- rms and peak themselves ------------------------------------------------
# These exist because a mutation control caught the library out: inflating
# `rms` by 5 % passed all sixty tests in this file. `rms` had no ground truth
# of its own -- only `rms_envelope` did -- yet it underlies every envelope,
# every level comparison and the `compare` level term. A 5 % level error is
# about half a dB, which is audible and was invisible here.

def test_rms_of_a_sinusoid_is_amplitude_over_root_two():
    """The one closed form everybody knows, and the one that was missing."""
    for amp in (0.1, 0.5, 1.0, 3.0):
        # A whole number of cycles, so there is no partial-period bias.
        n = am.SR_DEFAULT
        x = amp * np.sin(2 * np.pi * 100.0 * np.arange(n) / am.SR_DEFAULT)
        assert am.rms(x) == pytest.approx(amp / np.sqrt(2.0), rel=1e-4), amp


def test_rms_of_a_constant_is_its_magnitude():
    for c in (-2.0, -0.25, 0.0, 0.25, 2.0):
        assert am.rms(np.full(1000, c)) == pytest.approx(abs(c), abs=1e-12), c


def test_rms_of_a_square_wave_is_its_amplitude():
    # np.sign() would return exactly 0 at the hundred zero crossings of a
    # 50 Hz sine at 44.1 kHz, pulling the rms 0.1 % below the amplitude and
    # failing this for a reason that says nothing about `rms`. Use a real
    # two-valued square.
    s = np.sin(2 * np.pi * 50.0 * np.arange(am.SR_DEFAULT) / am.SR_DEFAULT)
    x = np.where(s >= 0.0, 0.7, -0.7)
    assert am.rms(x) == pytest.approx(0.7, rel=1e-9)


def test_rms_of_nothing_is_zero_not_a_crash():
    assert am.rms(np.zeros(0)) == 0.0
    assert am.rms(np.zeros(100)) == 0.0


def test_peak_is_the_largest_magnitude_either_sign():
    assert am.peak(np.array([0.1, -0.9, 0.5])) == pytest.approx(0.9)
    assert am.peak(np.array([-0.1, 0.9, -0.5])) == pytest.approx(0.9)


# ---------------------------------------------------------------------------
# Schroeder T20 and the band split -- the two estimators the sixteen-sound kit
# added, each against a signal whose answer is known in closed form.
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("tau", [0.005, 0.030, 0.100, 0.400])
def test_schroeder_t20_equals_ln10_tau_on_a_damped_sinusoid(tau):
    """The identity the estimator rests on: for x = exp(-t/tau) sin(wt) the
    backward-integrated energy falls exactly 20 dB in ln(10)*tau seconds, so
    `schroeder_t20` and `t20_from_tau` must agree with no fit error."""
    n = int(12 * tau * SR)
    t = np.arange(n) / SR
    x = np.exp(-t / tau) * np.sin(2 * np.pi * 440.0 * t)
    e = am.schroeder_t20(x, SR)
    assert e.ok, e.reason
    assert abs(e.value / am.t20_from_tau(tau) - 1) < 0.01, \
        f"tau {tau}: T20 {e.value*1e3:.2f} ms, closed form {am.t20_from_tau(tau)*1e3:.2f} ms"


def test_schroeder_t20_refuses_a_recording_that_was_cut_before_it_decayed():
    """The failure that matters: a sample editor-trimmed while the voice is
    still sounding. The integral runs to the end of the array, so a truncated
    decay reads SHORT -- and short by an amount that depends on where the cut
    is, which is indistinguishable from a real short decay. Three of the
    reference TR-808 one-shots this project measures against (RS, CL, MA) are
    trimmed at 20-40 ms, so this is the estimator refusing real data, not a
    hypothetical."""
    tau = 0.200
    t = np.arange(int(0.030 * SR)) / SR          # 30 ms of a 200 ms decay
    x = np.exp(-t / tau) * np.sin(2 * np.pi * 8000.0 * t)
    e = am.schroeder_t20(x, SR)
    assert not e.ok, f"accepted a cut decay and returned {e.value*1e3:.1f} ms"
    assert "ends before" in e.reason


def test_schroeder_t20_reads_a_two_exponential_decay_between_its_parts():
    """What it is FOR: an envelope that is not one exponential. A loud fast
    part over a quiet slow one has no single tau -- `decay_tau` refuses it --
    but it does have a time to -20 dB, and it must lie between the two parts'
    own."""
    fast, slow = 0.010, 0.300
    t = np.arange(int(3.0 * SR)) / SR
    x = (np.exp(-t / fast) + 0.05 * np.exp(-t / slow)) * np.sin(2 * np.pi * 3000.0 * t)
    assert not am.decay_tau(x, SR).ok, "decay_tau should refuse two exponentials"
    e = am.schroeder_t20(x, SR)
    assert e.ok, e.reason
    assert am.t20_from_tau(fast) < e.value < am.t20_from_tau(slow), \
        f"T20 {e.value*1e3:.1f} ms outside [{am.t20_from_tau(fast)*1e3:.1f}, {am.t20_from_tau(slow)*1e3:.1f}]"


def test_band_energy_splits_a_two_tone_signal():
    """30 % of the power at 3 kHz and 70 % at 7 kHz, by construction."""
    t = np.arange(SR) / SR
    x = np.sqrt(2 * 0.30) * np.sin(2 * np.pi * 3000 * t) + np.sqrt(2 * 0.70) * np.sin(2 * np.pi * 7000 * t)
    got = am.band_energy(x, ((200, 5000), (5000, 9000)), SR)
    assert abs(got[0] - 0.30) < 0.01 and abs(got[1] - 0.70) < 0.01, got


def test_band_energy_disagrees_with_a_windowed_fft_on_a_decaying_signal():
    """The reason `band_energy` exists rather than a sum of FFT bins. Two
    tones, the high one decaying fast and the low one slowly: the ENERGY is
    mostly in the high tone, but a Hann window over the whole file sees mostly
    the low one's tail. Both estimators are right about what they measure; only
    one of them answers "where is the energy"."""
    t = np.arange(int(2.0 * SR)) / SR
    x = 4.0 * np.exp(-t / 0.010) * np.sin(2 * np.pi * 7000 * t) \
        + 0.30 * np.exp(-t / 0.600) * np.sin(2 * np.pi * 3000 * t)
    lo_e, hi_e = am.band_energy(x, ((200, 5000), (5000, 9000)), SR)
    f, S = am.spectrum(x, SR)
    P = S ** 2
    lo_f = P[(f >= 200) & (f < 5000)].sum() / P.sum()
    assert hi_e > 0.6, f"the energy really is in the high tone ({hi_e:.2f})"
    assert lo_f > 0.6, f"the windowed FFT really does see the low one ({lo_f:.2f})"


def test_poles_to_freq_tau_inverts_the_pole_placement():
    """`coef_freq_tau` in the acceptance suite reads a mode's coefficients back
    into (f0, tau); this is that conversion against the placement that made
    them, over the whole range the TR-808 kit uses."""
    import modal_fixed
    for f0, q in ((49.4, 84.0), (173.0, 16.3), (455.0, 6.7), (2500.0, 200.0), (10600.0, 2.3)):
        a1, a2 = modal_fixed.pole_regs(f0, q, 24, SR)
        ef, et = am.poles_to_freq_tau(a1 / (1 << 24), a2 / (1 << 24), SR)
        got_f, got_tau = ef.require("f0"), et.require("tau")
        assert abs(got_f / f0 - 1) < 0.001, f"{f0} Hz Q {q}: read back {got_f:.3f} Hz"
        assert abs(got_tau / (q / (math.pi * f0)) - 1) < 0.01, \
            f"{f0} Hz Q {q}: read back tau {got_tau*1e3:.3f} ms"


# ---------------------------------------------------------------------------
# #118 -- the truncation guard is a LENGTH, not a level.
#
# These exist because the old guard, `tail_db > -35 dB`, was nearly vacuous:
# the backward integral of ANY finite record falls towards -inf at its last
# sample, so a level criterion cannot see where the record was cut. The first
# test below is the exact case that passed it at -18.4 % error.
# ---------------------------------------------------------------------------
def _exp_decay(tau, seconds, f=220.0, sr=SR):
    t = np.arange(int(seconds * sr)) / sr
    return np.exp(-t / tau) * np.sin(2 * math.pi * f * t)


def test_schroeder_t20_refuses_a_mild_truncation_a_level_guard_cannot_see():
    """The measured case from `docs/analysis-conventions.md` section 4: a
    100 ms cut of a decay whose true T20 is 92.10 ms reads **-18.4 %**, and the
    old level guard saw **-79 dB against a -35 dB requirement** and passed it.

    The second assertion is the point of the test -- it asserts that the LEVEL
    criterion is still comfortably satisfied, so if anyone reinstates it as the
    truncation test this case goes green again and the test goes red."""
    x = _exp_decay(0.040, 0.100)
    e = am.schroeder_t20(x, SR)
    assert not e.ok, f"accepted a 100 ms cut of a 92 ms T20 and returned {e.value*1e3:.1f} ms"
    assert "ends before" in e.reason
    assert e.detail["tail_db"] < -35.0 - 10.0, \
        ("the old LEVEL guard passes this record comfortably (tail_db "
         f"{e.detail['tail_db']:.1f} dB) -- which is why the guard is a length")
    assert e.detail["tail_in_t20s"] < am.MIN_TAIL_T20


def test_schroeder_t20_accepts_a_record_that_does_contain_its_decay():
    """The other half: the guard must not refuse a record that is long enough.
    300 ms of the same decay reads the closed-form answer to 0.0 %."""
    e = am.schroeder_t20(_exp_decay(0.040, 0.300), SR)
    assert e.ok, e.reason
    assert abs(e.value / am.t20_from_tau(0.040) - 1) < 0.01, e.value


# ---------------------------------------------------------------------------
# #139 -- a LENGTH guard is satisfied by silence
#
# #118 replaced a level criterion with a length one, and length counts SAMPLES
# IN THE RECORD rather than SIGNAL IN THE TAIL. Appending zeros adds no
# information and cannot change what the decay was, but it converts a correct
# refusal into an accepted wrong answer. These tests are the counterexample
# from the issue, both of its directions, and the #103-style invariance whose
# absence let it through.
# ---------------------------------------------------------------------------
def test_appending_silence_cannot_rescue_a_refused_decay():
    """#139's verified counterexample, exactly. A 2.0 s record of a tau 200 ms
    decay reads its closed-form T20. Cut to 0.30 s it is refused, correctly.
    Cut to 0.30 s and padded back to 2.0 s with DIGITAL SILENCE it was
    accepted at **-48 %**.

    Zeros carry no information about a decay that was already cut off. A guard
    that a `np.zeros` call can satisfy is not a guard."""
    x = _exp_decay(0.200, 2.000)
    exact = am.t20_from_tau(0.200)
    full = am.schroeder_t20(x, SR)
    assert full.ok and abs(full.value / exact - 1) < 0.01, full

    cut = x[:int(0.300 * SR)]
    assert not am.schroeder_t20(cut, SR).ok, "the cut record must still be refused"

    padded = np.concatenate([cut, np.zeros(int(1.700 * SR))])
    e = am.schroeder_t20(padded, SR)
    assert not e.ok, (
        "1.7 s of np.zeros turned a refusal into an accepted answer of "
        f"{(e.value or 0)*1e3:.1f} ms against an exact {exact*1e3:.1f} ms "
        f"({100*((e.value or 0)/exact - 1):+.1f} %)")
    # And the point of the test, stated the way #118's test states its own:
    # the criterion the pad DEFEATED is still comfortably satisfied by it. If
    # anyone reinstates the array's length as the truncation test, this record
    # goes green again and this assertion goes red.
    d = e.detail
    array_tail_t20s = (len(padded) - d["sounding_samples"] + d["after_hi_ms"] * 1e-3 * SR) \
        / (d["t20_ms"] * 1e-3 * SR)
    assert array_tail_t20s >= am.MIN_TAIL_T20, \
        f"the ARRAY holds {array_tail_t20s:.2f} T20s after the -25 dB point"
    assert d["tail_in_t20s"] < am.MIN_TAIL_T20, \
        f"the SOUNDING record holds only {d['tail_in_t20s']:.3f}"
    assert d["trailing_silence_ms"] == pytest.approx(1700.0, abs=1.0), d


@pytest.mark.parametrize("pad_s", [0.2, 1.7, 5.0])
def test_appending_silence_cannot_change_an_accepted_decay_either(pad_s):
    """The invariance, stated as an invariance (#103): appending digital
    silence to a record that already contains its decay must not move the
    measurement. The refusal above and this are the same property read in its
    two directions, and neither alone is the test."""
    x = _exp_decay(0.040, 0.500)
    base = am.schroeder_t20(x, SR).require("unpadded")
    got = am.schroeder_t20(np.concatenate([x, np.zeros(int(pad_s * SR))]), SR)
    assert got.ok, got.reason
    assert abs(got.value / base - 1) < 0.01, \
        f"{pad_s} s of trailing silence moved T20 from {base*1e3:.2f} to {got.value*1e3:.2f} ms"


def test_a_genuinely_quiet_tail_is_not_refused_for_being_quiet():
    """The direction that makes the criterion hard, and the one a naive
    "the tail must have energy" rule gets wrong: **a real decay's tail IS
    low-energy.** 16-bit quantisation puts this record's tail at about
    -96 dBFS, three orders of magnitude under its own peak, and it must be
    measured, not refused."""
    x = _exp_decay(0.040, 0.600)
    q = np.round(x * 32767.0) / 32767.0          # a real 16-bit record's floor
    e = am.schroeder_t20(q, SR)
    assert e.ok, f"refused a genuine quiet tail: {e.reason}"
    assert abs(e.value / am.t20_from_tau(0.040) - 1) < 0.02, e.value


def test_a_decay_that_ends_in_its_own_noise_floor_is_not_refused():
    """The same direction again with a noise floor rather than quantisation:
    a decay recorded onto a -80 dBFS floor still contains its decay, and the
    criterion must read the tail's signal rather than demand a level."""
    rng = np.random.default_rng(139)
    x = _exp_decay(0.040, 0.600) + rng.normal(0.0, 1e-4, int(0.600 * SR))
    e = am.schroeder_t20(x, SR)
    assert e.ok, f"refused a decay sitting on its own noise floor: {e.reason}"
    assert abs(e.value / am.t20_from_tau(0.040) - 1) < 0.05, e.value


@pytest.mark.parametrize("pad_ms", [0.0, 2.0, 100.0])
def test_schroeder_t20_is_unchanged_by_leading_silence(pad_ms):
    """Invariance (#103): prepending digital silence cannot change how long a
    decay took. The backward integral shifts by exactly the pad, so both the
    -5 dB and the -25 dB index shift by it and the fitted slope cannot move."""
    x = _exp_decay(0.040, 0.500)
    base = am.schroeder_t20(x, SR).require("no pad")
    got = am.schroeder_t20(np.concatenate([np.zeros(int(pad_ms * 1e-3 * SR)), x]), SR)
    assert got.ok, got.reason
    assert abs(got.value - base) < 1e-9, f"{pad_ms} ms of silence moved T20 by {(got.value-base)*1e3:.6f} ms"


def test_schroeder_t20_is_unchanged_by_scaling():
    """Invariance (#103): a decay TIME cannot depend on the gain it was
    recorded at. The curve is normalised to its own first sample."""
    x = _exp_decay(0.040, 0.500)
    assert abs(am.schroeder_t20(x * 1e-4, SR).require() - am.schroeder_t20(x, SR).require()) < 1e-12


# ---------------------------------------------------------------------------
# #119 / #92 -- the aliasing floor is measured per call, never quoted.
# ---------------------------------------------------------------------------
def _additive_saw(f0, seconds=0.5, sr=SR):
    """Alias-free by construction: every partial is below Nyquist, so the
    reading of an aliasing estimator on it IS that estimator's floor."""
    n = int(seconds * sr)
    t = np.arange(n) / sr
    y = np.zeros(n)
    for k in range(1, int(sr / 2 / f0)):
        y += np.sin(2 * math.pi * k * f0 * t) / k
    return y / np.abs(y).max()


def _naive_saw(f0, seconds=0.5, sr=SR):
    t = np.arange(int(seconds * sr)) / sr
    y = 2 * ((f0 * t) % 1.0) - 1.0
    return y / np.abs(y).max()


@pytest.mark.parametrize("f0", [111.0, 261.626, 441.0])
def test_inharmonic_fraction_db_reading_of_an_alias_free_signal_is_its_floor(f0):
    """The property that makes the floor believable: on a signal with nothing
    inharmonic in it, the value and the measured floor must be the same
    number, because the value is entirely leakage. If the floor estimator were
    wrong this is where it shows."""
    e = am.inharmonic_fraction_db(_additive_saw(f0), f0, SR)
    assert e.ok, e.reason
    assert abs(e.detail["headroom_db"]) < 1.0, \
        f"f0 {f0}: value {e.value:.2f} dB but floor read {e.detail['floor_db']:.2f} dB"


def test_inharmonic_fraction_db_floor_is_not_a_constant():
    """#92's failure, in the estimator it was found in. The floor swings ~60 dB
    with f0 -- between an f0 that lands on a bin centre and one that does not --
    so a floor quoted in a docstring is wrong for almost every call. It must
    come back in `detail`, measured."""
    on_bin = am.inharmonic_fraction_db(_additive_saw(440.0), 440.0, SR)
    off_bin = am.inharmonic_fraction_db(_additive_saw(441.0), 441.0, SR)
    assert on_bin.ok and off_bin.ok
    assert "floor_db" in on_bin.detail and "floor_db" in off_bin.detail
    assert on_bin.detail["floor_db"] < off_bin.detail["floor_db"] - 50.0, \
        (f"on-bin floor {on_bin.detail['floor_db']:.1f} dB, off-bin "
         f"{off_bin.detail['floor_db']:.1f} dB -- a constant would be a lie")


def test_inharmonic_fraction_db_window_is_blackman_harris_not_hann():
    """#119: the swap is free. It buys 35 dB of floor at the same guard width
    and moves the answer on a genuinely aliased signal by ~0 dB.

    Both halves are asserted, because a window change that moved the ANSWER
    would invalidate every aliasing number already on record."""
    f0, n = 441.0, int(0.5 * SR)

    def hann_reading(x):
        p = np.abs(np.fft.rfft(x * np.hanning(n))) ** 2
        harm = am._harmonic_mask(n, f0, SR, 5)
        return 10.0 * math.log10(p[~harm].sum() / p.sum())

    free, naive = _additive_saw(f0), _naive_saw(f0)
    got_floor = am.inharmonic_fraction_db(free, f0, SR).require("floor")
    assert got_floor < hann_reading(free) - 30.0, \
        f"Blackman-Harris floor {got_floor:.2f} dB vs Hann {hann_reading(free):.2f} dB"
    got_answer = am.inharmonic_fraction_db(naive, f0, SR).require("answer")
    assert abs(got_answer - hann_reading(naive)) < 0.5, \
        f"the window moved the ANSWER: {got_answer:.2f} vs {hann_reading(naive):.2f} dB"


def test_inharmonic_fraction_db_is_unchanged_by_scaling():
    """Invariance (#103): it is a FRACTION of total energy, so a gain change
    cannot move it. A level-sensitive ratio is a bug."""
    x = _naive_saw(441.0)
    a = am.inharmonic_fraction_db(x, 441.0, SR).require()
    b = am.inharmonic_fraction_db(x * 1e-3, 441.0, SR).require()
    assert abs(a - b) < 1e-9, f"{a:.6f} vs {b:.6f} dB"


# ---------------------------------------------------------------------------
# #150 -- the passband reference a corner is measured against
#
# `plateau_db` takes the median of a band that MOVES WITH THE COMMANDED
# CUTOFF, so it catches a different amount of the filter's own droop at each
# cutoff and biases the corner by a different amount at each cutoff.
# `dc_plateau_db` extrapolates to DC instead, which is shape-agnostic for any
# real filter because |H(f)|^2 is even in f.
# ---------------------------------------------------------------------------
def _allpole_db(freqs, fc, poles):
    return -(10.0 * poles) * np.log10(1.0 + (np.asarray(freqs, float) / fc) ** 2)


@pytest.mark.parametrize("poles", [2, 4, 6])
@pytest.mark.parametrize("fc", [250.0, 1000.0, 4000.0])
def test_dc_plateau_db_recovers_the_dc_gain_of_an_ideal_filter(poles, fc):
    """The closed form: every one of these curves is 0 dB at DC by
    construction, at every cutoff and every pole count. The median of the same
    band is not, and by a different amount each time -- which is the defect."""
    f = np.geomspace(40.0, 12000.0, 32)
    band = (f[0], max(f[0] * 2.5, fc * 0.25))
    g = _allpole_db(f, fc, poles)
    e = am.dc_plateau_db(f, g, band, scale_hz=fc)
    assert e.ok, e.reason
    assert abs(e.value) < 0.05, f"DC level read {e.value:+.3f} dB, closed form 0.000"
    assert abs(e.detail["band_median_db"]) >= abs(e.value), \
        "the median must be the more biased of the two, or this repair is pointless"


def test_dc_plateau_db_is_offset_equivariant():
    """A gain applied to the whole curve must move the reference by exactly
    that gain and nothing else -- it is a LEVEL, and a level estimator that is
    not equivariant under a level change is not measuring one."""
    f = np.geomspace(40.0, 12000.0, 32)
    g = _allpole_db(f, 1000.0, 4)
    a = am.dc_plateau_db(f, g, (40.0, 250.0), scale_hz=1000.0).require()
    b = am.dc_plateau_db(f, g - 7.5, (40.0, 250.0), scale_hz=1000.0).require()
    assert abs((a - b) - 7.5) < 1e-9, f"{a:.9f} vs {b:.9f}"


def test_dc_plateau_db_refuses_a_band_that_is_not_a_passband():
    """The precondition, asserted at the point of use. `gain_db` must be
    flat-plus-f^2 over the band for the intercept to mean anything; a band with
    a notch in it is not a passband, and the answer would be an extrapolation
    dressed as a measurement. So it REFUSES rather than reporting."""
    f = np.geomspace(40.0, 12000.0, 32)
    g = _allpole_db(f, 4000.0, 4)                        # band top at 0.25 fc
    g = g - 25.0 * np.exp(-((np.log2(f / 200.0)) ** 2) / 0.05)   # a notch inside it
    e = am.dc_plateau_db(f, g, (40.0, 1000.0), scale_hz=4000.0)
    assert not e.ok
    assert "not the passband" in e.reason
    assert e.detail["fit_residual_db"] > am.MAX_PLATEAU_EXTRAPOLATION_DB
    # And the same curve without the notch is measured, so the refusal is the
    # notch and not the band.
    ok = am.dc_plateau_db(f, _allpole_db(f, 4000.0, 4), (40.0, 1000.0), scale_hz=4000.0)
    assert ok.ok and abs(ok.value) < 0.05, ok


def test_dc_plateau_db_refuses_a_band_with_too_few_points():
    f = np.asarray([40.0, 48.1, 57.8, 69.5])
    e = am.dc_plateau_db(f, _allpole_db(f, 1000.0, 4), (40.0, 45.0), scale_hz=1000.0)
    assert not e.ok and "three measured points" in e.reason


def test_corner_from_curve_ref_db_overrides_the_band_median():
    """The kwarg #150 needs, and the guarantee every existing caller relies on:
    without `ref_db` nothing about this function moves."""
    f = np.geomspace(40.0, 12000.0, 32)
    g = _allpole_db(f, 250.0, 4)
    band = (40.0, 100.0)
    base = am.corner_from_curve(f, g, ref_band=band)
    assert base.ok and base.value == pytest.approx(124.96, rel=0.005)
    assert base.detail["plateau_db"] == base.detail["band_median_db"]
    fixed = am.corner_from_curve(f, g, ref_band=band, ref_db=0.0)
    assert fixed.value == pytest.approx(107.80, rel=0.005)
    assert abs(fixed.value / (250.0 * math.sqrt(10 ** 0.075 - 1)) - 1) < 0.01
    assert fixed.detail["band_median_db"] == pytest.approx(-0.904, abs=0.01)


@pytest.mark.parametrize("poles", [2, 4, 6])
def test_dc_plateau_db_refuses_a_band_that_reaches_the_corner(poles):
    """The domain limit #150 asked for, asserted at the point of use rather
    than described. On this project's grid `_ref_band`'s top edge is pinned at
    100 Hz, so at a commanded 100 Hz the "passband" band reaches the cutoff
    itself and the f^2 expansion reads 7 to 19 % high depending on pole count.
    That is a REFUSAL, not a number with a caveat."""
    f = np.geomspace(40.0, 12000.0, 32)
    e = am.dc_plateau_db(f, _allpole_db(f, 100.0, poles), (40.0, 100.0), scale_hz=100.0)
    assert not e.ok and "past the" in e.reason
    assert e.detail["band_top_over_cutoff"] == pytest.approx(1.0)
    # And the boundary itself is admitted, because the Filters family lives on
    # it: a commanded 250 Hz puts the band top at 0.4 of the cutoff.
    ok = am.dc_plateau_db(f, _allpole_db(f, 250.0, poles), (40.0, 100.0), scale_hz=250.0)
    assert ok.ok, ok.reason
    assert ok.detail["band_top_over_cutoff"] == pytest.approx(0.4)
