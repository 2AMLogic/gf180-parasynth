#!/usr/bin/env python3
"""Does a multi-scale spectral distance resolve OUR errors above its OWN floor?

One question, asked the way `docs/bd-repeatability-measurement.md` asks it of
every tolerance on the board: **a distance is usable only if the error we care
about is larger than the distance's floor.** Citing a loss function's training
success says nothing about that. This measures it.

    .venv/bin/python tools/probes/audio_distance_floor.py
    .venv/bin/python tools/probes/audio_distance_floor.py --json out.json

Exit status follows the repository's verifier convention:

    0   every experiment ran
    2   REFUSED -- a precondition of the apparatus is unmet (no reference
        audio on this host), so nothing was attempted

WHAT IS MEASURED, AND WHY EACH ONE

`E0 ground truth`  Four distances are implemented here from their published
    definitions. Each is checked against a signal pair whose answer is known in
    closed form BEFORE it is used on anything: identical inputs must give
    exactly 0, and a pure x2 gain must give exactly 1.0 (relative L1 and
    spectral convergence) and exactly ln 2 (log-magnitude). An estimator
    calibrated on our own signals is not validated; these are analytic.

`E1 determinism`   Two renders of one patch through our integer model. The
    model is deterministic, so this must be exactly 0 -- it is the control that
    says a nonzero floor below came from the signal and not from the harness.

`E2 apparatus floor`  One REAL recording against itself, moved by k samples and
    zero-padded back. A 4-sample head trim cannot be the TR-808; whatever the
    distance reads here is the distance measuring the editor. This is the
    control that convicted the band-split metric in #126, run on a new metric.

`E3 alignment`     The same shift, out to 10 ms, because `audio_measure`'s own
    docstring states onset positions are good to about 10 ms and no better. If
    the distance reads more at 10 ms of misalignment than it reads for a real
    defect, it is an alignment meter.

`E4 property sweeps`  Analytic, single-property perturbations of one real
    recording: extra exponential decay (T20 error, closed form), broadband
    gain, high-band tilt (partial imbalance), and resampling (f0). Each sweep
    passes through the TR-808's own measured session-to-session spread --
    2.74 % f0, 1.30 % T20, 0.159 dB band split -- so every reading can be put
    beside the floor it has to beat.

`E5 the tom pitch drop`  The defect this was written against, now FIXED:
    `spec/NUMERIC-CONTRACT.md` 15.7.1 shipped the drop at x1.7 where
    `docs/tom-pitch-drop-measurement.md` measured the hardware at
    x1.06 / x1.14 / x1.24, and #154 corrected it. The experiment stands
    unchanged and is now a discrimination case rather than a live defect: we
    render LT at each ratio through the real model and ask whether a spectral
    distance separates them. A distance that cannot is blind to the largest
    single error the drum section has had.

`E6 identifiability`  The exchange rate. For each distance, the broadband gain
    error that reads the SAME value as a 5 % decay error. A scalar that gives
    one number to two unrelated faults cannot say which one to fix.

`E7 ceiling`       Distance between DIFFERENT voices of the kit -- the largest
    value the metric can plausibly produce on this material. Every reading
    above is reported as a fraction of it, because "0.3" means nothing without
    knowing where the top is.

WHAT THIS PROBE DOES NOT DO

It does not touch `model/audio_measure.py` and adds nothing to the scorecard.
The distances here are implemented for measurement, not for adoption; if one
were adopted it would be re-implemented where the board can see it.

It cannot measure a LEARNED embedding distance (OpenL3, VGGish, CLAP,
EnCodec). `torch` is not installed in this venv and there is no model
checkpoint on this host, so every such number would be recalled rather than
measured. That is stated as an absence in `docs/audio-distance-metrics.md`,
not filled in.
"""
from __future__ import annotations

import argparse
import json
import math
import pathlib
import sys

import numpy as np

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "model"))
sys.path.insert(0, str(ROOT / "tools"))

REFDIR = pathlib.Path("/tmp/tr808-ref")

#: The TR-808's own session-to-session spread, docs/bd-repeatability-measurement.md
MACHINE_FLOOR = {"f0_pct": 2.74, "t20_pct": 1.30, "band_db": 0.159}

#: Every multi-scale distance in the literature is a mean over FFT sizes. These
#: are DDSP's / the Turian-Henry benchmark's six, which are also the six most
#: commonly published.
SCALES = (2048, 1024, 512, 256, 128, 64)

#: Magnitudes below this fraction of the reference's own peak magnitude are
#: clamped before any logarithm. Without a clamp the log terms are dominated by
#: silent bins, where the ratio of two near-zero numbers is unbounded and has
#: nothing to do with the sound. The value is stated, not tuned: -100 dB.
LOG_FLOOR_REL = 1e-5


class Refused(Exception):
    """A precondition failed, so nothing was attempted. Distinct from a
    failure: REFUSED means no evidence, not bad evidence."""


# ---------------------------------------------------------------------------
# The distances, from their published definitions
# ---------------------------------------------------------------------------
def _stft_mag(x: np.ndarray, n: int) -> np.ndarray:
    hop = n // 4
    if len(x) < n:
        x = np.pad(x, (0, n - len(x)))
    w = np.hanning(n + 1)[:n]
    idx = np.arange(0, len(x) - n + 1, hop)
    if len(idx) == 0:
        idx = np.array([0])
    frames = np.stack([x[i:i + n] * w for i in idx])
    return np.abs(np.fft.rfft(frames, axis=1))


def _pair(a: np.ndarray, b: np.ndarray) -> tuple:
    n = max(len(a), len(b))
    return (np.pad(a, (0, n - len(a))), np.pad(b, (0, n - len(b))))


def distances(a: np.ndarray, b: np.ndarray) -> dict:
    """Four published multi-scale spectral distances, on one pair.

    `mss_l1`    DDSP's multi-scale spectrogram term, L1 on linear magnitude,
                divided by the reference's own L1 norm so the number is
                relative and dimensionless. 1.0 means "off by as much as the
                reference itself contains".
    `mss_log`   the same, on log magnitude: the mean absolute log-magnitude
                difference in nats. 0.693 is a factor of two, i.e. 6.02 dB,
                averaged over every time-frequency bin.
    `mrstft`    Yamamoto et al.'s multi-resolution STFT loss: spectral
                convergence (Frobenius, relative) plus the log-magnitude L1,
                summed. This is the form most codebases actually ship.
    `mel_dac`   the Descript Audio Codec's multi-scale mel L1, at its own
                published window lengths and mel-bin counts, mean absolute log
                difference in nats.
    """
    a, b = _pair(np.asarray(a, float), np.asarray(b, float))
    out = {k: 0.0 for k in ("mss_l1", "mss_log", "mrstft", "mel_dac")}
    for n in SCALES:
        A, B = _stft_mag(a, n), _stft_mag(b, n)
        floor = max(A.max(), 1e-30) * LOG_FLOOR_REL
        Ac, Bc = np.maximum(A, floor), np.maximum(B, floor)
        denom = np.abs(A).sum()
        out["mss_l1"] += (np.abs(A - B).sum() / denom) if denom > 0 else 0.0
        out["mss_log"] += float(np.abs(np.log(Ac) - np.log(Bc)).mean())
        fro = np.linalg.norm(A)
        sc = float(np.linalg.norm(A - B) / fro) if fro > 0 else 0.0
        out["mrstft"] += sc + float(np.abs(np.log(Ac) - np.log(Bc)).mean())
    for k in out:
        out[k] /= len(SCALES)
    out["mel_dac"] = _mel_dac(a, b)
    return out


#: DAC's published configuration: window lengths with matched mel-bin counts.
DAC_MEL = ((32, 5), (64, 10), (128, 20), (256, 40), (512, 80), (1024, 160), (2048, 320))


def _mel_filters(n_fft: int, n_mels: int, sr: int) -> np.ndarray:
    def hz2mel(f):
        return 2595.0 * np.log10(1.0 + f / 700.0)

    def mel2hz(m):
        return 700.0 * (10.0 ** (m / 2595.0) - 1.0)

    lo, hi = hz2mel(0.0), hz2mel(sr / 2.0)
    pts = mel2hz(np.linspace(lo, hi, n_mels + 2))
    bins = np.floor((n_fft + 1) * pts / sr).astype(int)
    bins = np.clip(bins, 0, n_fft // 2)
    fb = np.zeros((n_mels, n_fft // 2 + 1))
    for m in range(n_mels):
        l, c, r = bins[m], bins[m + 1], bins[m + 2]
        if c > l:
            fb[m, l:c] = (np.arange(l, c) - l) / (c - l)
        if r > c:
            fb[m, c:r] = (r - np.arange(c, r)) / (r - c)
    # EMPTY FILTERS ARE DROPPED, and this is not tidying. At 48 kHz a 2048-point
    # FFT with 320 mel bands puts several low bands entirely between two FFT
    # bins, so their response is identically zero. Both signals then clamp to
    # the log floor, the band contributes exactly 0 to the mean for ANY input,
    # and the distance is diluted towards zero by however many such bands there
    # are. The ground-truth check caught this: a pure x2 gain read 0.6247
    # instead of ln 2 = 0.6931, a 10 % under-report with no defect present.
    keep = fb.sum(axis=1) > 0
    return fb[keep]


_MELCACHE: dict = {}


def _mel_dac(a: np.ndarray, b: np.ndarray, sr: int = 48000) -> float:
    total = 0.0
    for n_fft, n_mels in DAC_MEL:
        key = (n_fft, n_mels, sr)
        if key not in _MELCACHE:
            _MELCACHE[key] = _mel_filters(n_fft, n_mels, sr)
        fb = _MELCACHE[key]
        A = _stft_mag(a, n_fft) @ fb.T
        B = _stft_mag(b, n_fft) @ fb.T
        floor = max(A.max(), 1e-30) * LOG_FLOOR_REL
        total += float(np.abs(np.log(np.maximum(A, floor))
                              - np.log(np.maximum(B, floor))).mean())
    return total / len(DAC_MEL)


# ---------------------------------------------------------------------------
# Analytic single-property perturbations
# ---------------------------------------------------------------------------
def perturb_decay(x: np.ndarray, sr: int, pct: float) -> np.ndarray:
    """Change T20 by `pct` per cent, in closed form and nothing else.

    A signal decaying at D dB/s has T20 = 20/D. Multiplying by exp(-k t) adds
    8.686 k dB/s. For a target T20' = T20 (1 + p): D' = D/(1+p), so the
    ADDITIONAL rate is D' - D, which is negative for p > 0 -- a longer decay
    needs a rising exponential. Both signs are exact; neither is a fit.

    The reference T20 is measured off the signal with the same estimator the
    board uses, so `pct` is a per cent of the real thing and not of a guess."""
    import audio_measure as am
    # SECONDS. `schroeder_t20` returns -20/slope with slope in dB per second,
    # so its value is already a time in seconds -- 0.537 for the bass drum,
    # which docs/scorecard/README.md quotes as "BD 537" in ms. Dividing by 1000
    # here produced a 0.54 ms decay, a rate of 37 000 dB/s, and a perturbation
    # of exp(+295) -- the sweep read 1e128 and then NaN. The ground-truth gate
    # does not catch this one, because the bug is in the SIGNAL, not the metric.
    t20 = am.schroeder_t20(x, sr).require("T20 of the unperturbed signal")
    d_now = 20.0 / t20
    d_want = 20.0 / (t20 * (1.0 + pct / 100.0))
    k = (d_want - d_now) / 8.685889638065035
    t = np.arange(len(x)) / sr
    return x * np.exp(-k * t)


def perturb_gain(x: np.ndarray, sr: int, db: float) -> np.ndarray:
    return x * (10.0 ** (db / 20.0))


def perturb_tilt(x: np.ndarray, sr: int, db: float, split_hz: float = 200.0) -> np.ndarray:
    """Exactly `db` of gain on everything above `split_hz`, zero-phase, and
    nothing on the band below. This is the shape of a partial-imbalance or a
    band-split error, applied as a known quantity."""
    from scipy.signal import butter, sosfiltfilt
    sos = butter(4, split_hz / (sr / 2.0), btype="highpass", output="sos")
    hi = sosfiltfilt(sos, x)
    return x + hi * (10.0 ** (db / 20.0) - 1.0)


def perturb_f0(x: np.ndarray, sr: int, cents: float) -> np.ndarray:
    """Resample by 2**(cents/1200), then trim or pad back to length.

    This is a tape-speed change: it moves f0 by exactly `cents` AND scales
    every time constant by the inverse. It is reported as such. There is no
    way to move a real recording's f0 alone without a pitch-shifter, and a
    pitch-shifter is an estimator we would then have to validate."""
    r = 2.0 ** (cents / 1200.0)
    n = int(round(len(x) / r))
    t = np.linspace(0.0, len(x) - 1.0, n)
    y = np.interp(t, np.arange(len(x)), x)
    if len(y) < len(x):
        y = np.pad(y, (0, len(x) - len(y)))
    return y[:len(x)]


def shift(x: np.ndarray, k: int) -> np.ndarray:
    """Move the whole signal later by k samples, zero-padded. k may be
    negative, which trims the head -- the editor's own degree of freedom."""
    if k >= 0:
        return np.concatenate([np.zeros(k), x])[:len(x)]
    return np.concatenate([x[-k:], np.zeros(-k)])


# ---------------------------------------------------------------------------
# Signals
# ---------------------------------------------------------------------------
def load_ref(name: str) -> tuple:
    import soundfile as sf
    p = REFDIR / name
    if not p.exists():
        raise Refused(f"{p} is not on this host; no reference audio, so nothing was measured")
    x, sr = sf.read(str(p))
    if x.ndim > 1:
        x = x.mean(axis=1)
    return np.asarray(x, float), int(sr)


def render_lt(drop_ratio: float, seconds: float = 1.2) -> tuple:
    """One LT strike through the real integer model, with the diode pitch
    drop's ratio set to `drop_ratio`.

    The ratio is a module constant that `tom_pitch_drop_writes` reads at call
    time, so setting it and restoring it renders the same design with one
    number changed and nothing else -- which is precisely the comparison
    `docs/tom-pitch-drop-measurement.md` leaves open."""
    import drums_fx as dx
    old = dx.TOM_DROP_RATIO
    try:
        dx.TOM_DROP_RATIO = float(drop_ratio)
        n = int(seconds * dx.SR)
        d = dx.DrumsFx()
        dm, bd = d.play(dx.hit_writes([(int(0.01 * dx.SR), dx.SOUND_STOP["LT"], 1.0)],
                                      dx.kit_with_sounds("LT")), n)
        g = dx.accent_reg(0.45)
        out = dx.output_fx(np.zeros(n), 0, dm, g, bd, g)
        return np.asarray(out, dtype=np.float64) / 32768.0, dx.SR
    finally:
        dx.TOM_DROP_RATIO = old


def norm(x: np.ndarray) -> np.ndarray:
    p = float(np.abs(x).max())
    return x / p if p > 0 else x


# ---------------------------------------------------------------------------
# The experiments
# ---------------------------------------------------------------------------
def e0_ground_truth() -> dict:
    """Known answers, computed before anything else is trusted."""
    rng = np.random.default_rng(20260918)
    x = rng.standard_normal(24000) * 0.3
    same = distances(x, x)
    doubled = distances(x, x * 2.0)
    checks = {
        "identical -> 0": {k: same[k] for k in same},
        "x2 gain": {k: doubled[k] for k in doubled},
        "expected": {"mss_l1 identical": 0.0, "mss_l1 x2": 1.0,
                     "mss_log identical": 0.0, "mss_log x2": math.log(2.0),
                     "mrstft x2": 1.0 + math.log(2.0),
                     "mel_dac x2": math.log(2.0)},
    }
    # The log floor is part of the definition, not an error, and it has a
    # measurable cost: a few bins of a broadband signal sit below -100 dB of
    # peak, so a pure x2 gain does not read EXACTLY ln 2. The deviation is
    # recorded rather than hidden, and the gate is set an order of magnitude
    # above it -- wide enough for the clamp, far too tight for the mel defect
    # above (which was 7e-2).
    checks["log_clamp_deviation"] = {
        "mss_log": abs(doubled["mss_log"] - math.log(2.0)),
        "mel_dac": abs(doubled["mel_dac"] - math.log(2.0)),
    }
    ok = (max(abs(v) for v in same.values()) < 1e-12
          and abs(doubled["mss_l1"] - 1.0) < 1e-9
          and abs(doubled["mss_log"] - math.log(2.0)) < 1e-4
          and abs(doubled["mrstft"] - (1.0 + math.log(2.0))) < 1e-4
          and abs(doubled["mel_dac"] - math.log(2.0)) < 1e-4)
    checks["ok"] = bool(ok)
    return checks


def e1_determinism() -> dict:
    a, _ = render_lt(1.7)
    b, _ = render_lt(1.7)
    d = distances(norm(a), norm(b))
    d["bit_identical"] = bool(np.array_equal(a, b))
    return d


def e2_e3_alignment(x: np.ndarray, sr: int) -> dict:
    out = {}
    for k in (1, 2, 4, 8, 16, 48, 96, 240, 480):
        out[f"{k} samples ({1000.0 * k / sr:.3f} ms)"] = distances(x, shift(x, k))
    return out


def e4_sweeps(x: np.ndarray, sr: int) -> dict:
    out = {}
    out["decay_pct"] = {f"{p:g}": distances(x, perturb_decay(x, sr, p))
                        for p in (0.0, MACHINE_FLOOR["t20_pct"], 2.5, 5.0, 10.0, 25.0, 50.0)}
    out["gain_db"] = {f"{p:g}": distances(x, perturb_gain(x, sr, p))
                      for p in (0.0, 0.05, 0.159, 0.5, 1.0, 3.0, 6.0)}
    out["tilt_db"] = {f"{p:g}": distances(x, perturb_tilt(x, sr, p))
                      for p in (0.0, MACHINE_FLOOR["band_db"], 0.5, 1.0, 3.0, 8.0)}
    cents_floor = 1200.0 * math.log2(1.0 + MACHINE_FLOOR["f0_pct"] / 100.0)
    out["f0_cents"] = {f"{p:g}": distances(x, perturb_f0(x, sr, p))
                       for p in (0.0, cents_floor, 50.0, 100.0, 165.0, 400.0, 1200.0)}
    out["f0_cents_floor_equivalent"] = cents_floor
    return out


def render_bd(f0_hz: float | None = None, seconds: float = 1.2) -> tuple:
    """One BD strike through the model, optionally retuned.

    `kit_808()` reads `BD_HZ` when it builds the mode's coefficients, so this
    moves f0 and NOTHING else -- no time constant, no level, no partial
    balance. That is what `perturb_f0` cannot do: resampling a recording is a
    tape-speed change, which scales every decay by the inverse of the pitch
    ratio, so a sweep taken that way cannot say whether the distance responded
    to the pitch or to the decay that came with it. This sweep can."""
    import drums_fx as dx
    old = dx.BD_HZ
    try:
        if f0_hz is not None:
            dx.BD_HZ = float(f0_hz)
        n = int(seconds * dx.SR)
        d = dx.DrumsFx()
        dm, bd = d.play(dx.hit_writes([(int(0.01 * dx.SR), dx.SOUND_STOP["BD"], 1.0)],
                                      dx.kit_with_sounds("BD")), n)
        g = dx.accent_reg(0.45)
        out = dx.output_fx(np.zeros(n), 0, dm, g, bd, g)
        return np.asarray(out, dtype=np.float64) / 32768.0, dx.SR
    finally:
        dx.BD_HZ = old


def e4b_pure_f0() -> dict:
    """f0 alone, on our own bass drum, with every other property held.

    The reference point is the shipped 49.4 Hz. Each rung is quoted in cents
    AND in the per-property units the board already uses, so the two can be
    read against each other directly."""
    import run_case as rc
    import audio_measure as am
    base_hz = 49.4
    rungs = [0.0, 1200.0 * math.log2(1.0 + MACHINE_FLOOR["f0_pct"] / 100.0),
             100.0, 165.0, 400.0, 1200.0]
    a, sr = render_bd(base_hz)
    a = norm(rc.prepare(a, sr))
    out = {"base_hz": base_hz, "rungs": {}}
    for c in rungs:
        hz = base_hz * 2.0 ** (c / 1200.0)
        b, s2 = render_bd(hz)
        b = norm(rc.prepare(b, s2))
        d = distances(a, b)
        d["f0_hz"] = hz
        d["f0_error_pct"] = 100.0 * (hz - base_hz) / base_hz
        out["rungs"][f"{c:g} cents"] = d
    return out


def e5_tom(seconds_full: float = 1.2) -> dict:
    """The shipped x1.7 against the measured hardware ratios, on our own LT.

    Reported twice: over the whole 1.2 s render, and over the first 60 ms
    alone -- the window the drop actually lives in. The difference between
    those two columns is how much a whole-file distance dilutes a transient
    defect."""
    import run_case as rc
    ratios = {"1.70 shipped": 1.7, "1.24 more-accent": 1.236,
              "1.14 accent": 1.140, "1.06 unaccented": 1.063, "1.00 no drop": 1.0}
    rendered = {}
    for k, r in ratios.items():
        y, sr = render_lt(r, seconds_full)
        rendered[k] = (norm(rc.prepare(y, sr)), sr)
    base, sr = rendered["1.70 shipped"]
    n60 = int(0.060 * sr)
    out = {"full_clip_s": seconds_full, "drop_window_ms": 60.0, "vs_shipped": {}}
    for k, (y, _) in rendered.items():
        if k == "1.70 shipped":
            continue
        out["vs_shipped"][k] = {
            "full": distances(base, y),
            "first_60ms": distances(base[:n60], y[:n60]),
        }
    # what the per-property estimator reads, for the same renders
    out["per_property_pitch_drop_hz"] = {}
    for k, (y, s) in rendered.items():
        try:
            e = rc._pitch_drop("LT")(y, s)
            out["per_property_pitch_drop_hz"][k] = (float(e.value) if e.ok else None)
        except Exception as exc:                                  # pragma: no cover
            out["per_property_pitch_drop_hz"][k] = f"refused: {exc}"
    # the floor, on the same signals and in the same units
    a, _ = render_lt(1.7, seconds_full)
    b, _ = render_lt(1.7, seconds_full)
    out["floor_same_patch_twice"] = distances(norm(rc.prepare(a, sr)),
                                              norm(rc.prepare(b, sr)))
    out["floor_1_sample_shift"] = distances(base, shift(base, 1))
    out["floor_1_sample_shift_first_60ms"] = distances(base[:n60], shift(base, 1)[:n60])
    # The alignment floor on THE SAME SIGNAL as the defect. Comparing the tom's
    # defect against a bass drum's shift sweep would be comparing two different
    # signals' floors, which is the sort of cross-signal borrowing this
    # repository has been bitten by before.
    out["alignment_on_this_signal"] = {
        f"{k} samples ({1000.0 * k / sr:.3f} ms)": distances(base, shift(base, k))
        for k in (1, 4, 16, 48, 96, 240, 480)}
    return out


# ---------------------------------------------------------------------------
# E8: the guard hypothesis, tested rather than asserted
# ---------------------------------------------------------------------------
def inject_hf_tone(x: np.ndarray, sr: int, dbfs: float = -40.0, hz: float = 12000.0):
    t = np.arange(len(x)) / sr
    return x + (10.0 ** (dbfs / 20.0)) * np.sin(2 * np.pi * hz * t)


def inject_quantise(x: np.ndarray, sr: int, bits: int = 6):
    q = 2.0 ** (bits - 1)
    return np.round(x * q) / q


def inject_tail_noise(x: np.ndarray, sr: int, dbfs: float = -45.0, t0: float = 0.25):
    rng = np.random.default_rng(4242)
    y = x.copy()
    i = min(len(x), int(t0 * sr))
    y[i:] = y[i:] + (10.0 ** (dbfs / 20.0)) * rng.standard_normal(len(x) - i)
    return y


def e8_blind_spot() -> dict:
    """Does a spectral distance see a defect the BOARD's own metrics miss?

    This is the positive case for adopting one as a guard, and it is the only
    experiment here that could argue FOR adoption, so it is run on the board's
    real estimator list rather than on a proxy. Our own BD render is the
    subject; three defects are injected that a fixed-point drum machine can
    actually have; and for each one both sides are reported -- what each of
    `DRUM_PLAN["BD"]`'s three metrics reads against its own tolerance, and what
    each distance reads against its own floor.

    A defect that every per-property metric passes and every distance flags is
    a blind spot the guard would have caught. A defect both miss is a blind
    spot neither covers. Which of those we have is a measurement."""
    import run_case as rc
    a, sr = render_bd()
    base = norm(rc.prepare(a, sr))
    floor = distances(base, shift(base, 1))
    out = {"floor_1_sample_shift": floor, "injections": {}}
    for name, fn, note in (
            ("hf_tone -40 dBFS 12 kHz", inject_hf_tone,
             "a spurious tone: clock or LFO feedthrough, the classic mixed-signal defect"),
            ("6-bit requantisation", inject_quantise,
             "a fixed-point word narrowed: broadband noise correlated with the signal"),
            ("tail noise -45 dBFS after 250 ms", inject_tail_noise,
             "a noise floor that does not decay with the voice"),
    ):
        y = norm(fn(base, sr))
        rec = {"note": note, "distances": distances(base, y), "per_property": {}}
        for mname, units, est, tol in rc.DRUM_PLAN["BD"]:
            ea, eb = est(base, sr), est(y, sr)
            if not (ea.ok and eb.ok):
                rec["per_property"][mname] = {
                    "verdict": "no verdict",
                    "reason": (ea.reason or eb.reason)}
                continue
            ref, got = float(ea.value), float(eb.value)
            t, basis = tol(ref, {})
            rec["per_property"][mname] = {
                "units": units, "unperturbed": ref, "injected": got,
                "error": abs(got - ref), "tolerance": t, "basis": basis,
                "normalised": (abs(got - ref) / t) if t else None,
                "verdict": "pass" if abs(got - ref) <= t else "FAIL"}
        rec["all_per_property_pass"] = all(
            v.get("verdict") == "pass" for v in rec["per_property"].values())
        out["injections"][name] = rec
    return out


def e6_exchange(x: np.ndarray, sr: int) -> dict:
    """The gain error that reads the same as a 5 % decay error.

    Solved by bisection on a strictly increasing function of |dB|, to 0.001 dB.
    If that gain is small, the scalar cannot tell a level mistake from a decay
    mistake -- and our renders are peak-normalised, so a level mistake is the
    one error the board deliberately does not score."""
    target = distances(x, perturb_decay(x, sr, 5.0))
    out = {"target_5pct_decay": target, "equivalent_gain_db": {}}
    for key in ("mss_l1", "mss_log", "mrstft", "mel_dac"):
        lo, hi = 0.0, 12.0
        if distances(x, perturb_gain(x, sr, hi))[key] < target[key]:
            out["equivalent_gain_db"][key] = ">12"
            continue
        for _ in range(40):
            mid = 0.5 * (lo + hi)
            if distances(x, perturb_gain(x, sr, mid))[key] < target[key]:
                lo = mid
            else:
                hi = mid
        out["equivalent_gain_db"][key] = round(0.5 * (lo + hi), 4)
    return out


def e7_ceiling() -> dict:
    """Different voices of the same machine: how big does this metric get."""
    import run_case as rc
    pairs = [("bd8/BD5050.WAV", "sd8/SD5050.WAV"),
             ("bd8/BD5050.WAV", "ch8/CH.WAV"),
             ("lt8/LT50.WAV", "mt8/MT50.WAV")]
    out = {}
    for pa, pb in pairs:
        try:
            a, sa = load_ref(pa)
            b, sb = load_ref(pb)
        except Refused as exc:
            out[f"{pa} vs {pb}"] = f"refused: {exc}"
            continue
        a = norm(rc.prepare(a, sa))
        b = norm(rc.prepare(b, sb))
        out[f"{pa} vs {pb}"] = distances(a, b)
    return out


def e9_noise_floor() -> dict:
    """How much of a LOG-domain distance is the reference's own noise floor?

    Our renders lead with exact digital silence and decay to exact zero. The
    Fischer recordings are a 1994 converter's output and do neither --
    `run_case.prepare` already has to subtract their DC from the pre-onset
    region. A log-magnitude distance compares every bin, including the ones
    where our side is silence and theirs is a converter, and a decaying one-shot
    spends most of its duration there.

    Measured two ways on one real recording: against itself gated below -60 dBFS
    of peak (the tail's noise removed, the voice untouched), and against itself
    with everything after the voice replaced by exact digital silence."""
    import run_case as rc
    out = {}
    try:
        raw, sr = load_ref("bd8/BD5050.WAV")
    except Refused as exc:
        return {"refused": str(exc)}
    x = norm(rc.prepare(raw, sr))
    pk = float(np.abs(x).max())
    for db in (-60.0, -50.0, -40.0):
        g = x.copy()
        g[np.abs(g) < pk * 10.0 ** (db / 20.0)] = 0.0
        out[f"gated below {db:g} dBFS"] = distances(x, g)
    z = x.copy()
    i = min(len(z), int(1.0 * sr))
    z[i:] = 0.0
    out["tail after 1.0 s zeroed"] = distances(x, z)
    # NOT a noise-floor estimate, and kept with that said out loud because it
    # read -3.6 dBFS and looked like one. `run_case.prepare` has already
    # trimmed the signal to 1 ms before the onset, so this window is the front
    # of the strike, not the silence ahead of it. Measuring the references own
    # converter noise means reading the RAW file before prepare() touches it.
    out["first_1ms_rms_dbfs_NOT_a_noise_floor"] = float(
        20.0 * np.log10(max(np.sqrt(np.mean(x[:int(0.001 * sr)] ** 2)), 1e-12) / pk))
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--json", type=pathlib.Path)
    ap.add_argument("--ref", default="bd8/BD2550.WAV",
                    help="the real recording the floor and the sweeps are taken on. "
                         "NOT bd8/BD5050.WAV, which is the board's own BD reference: "
                         "post-#132 its T20 is REFUSED (the record ends before the "
                         "decay does) and D01A's decay metric is a no-verdict for the "
                         "same reason. The decay sweep here needs a T20 to aim at.")
    a = ap.parse_args(argv)

    res: dict = {"scales": list(SCALES), "log_floor_rel": LOG_FLOOR_REL,
                 "machine_floor": MACHINE_FLOOR, "reference_file": a.ref}

    res["E0_ground_truth"] = e0_ground_truth()
    if not res["E0_ground_truth"]["ok"]:
        print("REFUSED: the distances do not reproduce their own closed-form answers")
        print(json.dumps(res["E0_ground_truth"], indent=2))
        return 2

    try:
        import run_case as rc
        raw, sr = load_ref(a.ref)
    except Refused as exc:
        print(f"REFUSED: {exc}")
        return 2
    x = norm(rc.prepare(raw, sr))

    # PRECONDITION, asserted at the point of use. The decay sweep perturbs the
    # signal's OWN measured T20 by a stated percentage, so a reference whose
    # T20 the estimator refuses cannot carry that sweep -- and #132's
    # truncation guard refuses exactly the file the board uses for BD. Crashing
    # with a traceback is not a verdict; REFUSED is.
    import audio_measure as am
    t20 = am.schroeder_t20(x, sr)
    if not t20.ok:
        print(f"REFUSED: {a.ref} has no usable T20, so the decay sweep has "
              f"nothing to aim at -- {t20.reason}")
        print("  files in bd8/ whose T20 the current estimator does accept:")
        for q in sorted(REFDIR.glob("bd8/*.WAV")):
            try:
                z, zs = load_ref(f"bd8/{q.name}")
            except Refused:
                continue
            zz = norm(rc.prepare(z, zs))
            e = am.schroeder_t20(zz, zs)
            if e.ok:
                print(f"    bd8/{q.name}  T20 {e.value * 1000:.1f} ms")
        return 2
    res["reference_t20_ms"] = t20.value * 1000.0

    res["E1_determinism"] = e1_determinism()
    res["E2_E3_alignment"] = e2_e3_alignment(x, sr)
    res["E4_sweeps"] = e4_sweeps(x, sr)
    res["E4b_pure_f0_via_model"] = e4b_pure_f0()
    res["E5_tom_pitch_drop"] = e5_tom()
    res["E6_exchange_rate"] = e6_exchange(x, sr)
    res["E7_ceiling"] = e7_ceiling()
    res["E8_blind_spot"] = e8_blind_spot()
    res["E9_noise_floor"] = e9_noise_floor()

    if a.json:
        a.json.write_text(json.dumps(res, indent=1, sort_keys=True))
        print(f"wrote {a.json}")
    report(res)
    return 0


def _row(label: str, d: dict) -> str:
    return (f"  {label:<34s} {d['mss_l1']:>10.5f} {d['mss_log']:>10.5f} "
            f"{d['mrstft']:>10.5f} {d['mel_dac']:>10.5f}")


HEAD = f"  {'':<34s} {'mss_l1':>10s} {'mss_log':>10s} {'mrstft':>10s} {'mel_dac':>10s}"


def report(r: dict) -> None:
    p = print
    p("=" * 92)
    p(f"multi-scale spectral distance: floor, sensitivity, identifiability")
    p(f"scales {r['scales']}   log floor {r['log_floor_rel']:g} of peak   "
      f"reference {r['reference_file']}")
    p("=" * 92)

    p("\nE0  ground truth (must hold before any number below is read)")
    g = r["E0_ground_truth"]
    p(HEAD)
    p(_row("identical inputs (expect 0)", g["identical -> 0"]))
    p(_row("x2 gain (expect 1 / .6931 / 1.6931 / .6931)", g["x2 gain"]))
    p(f"  verdict: {'OK' if g['ok'] else 'FAILED'}")

    p("\nE1  two renders of one patch, our integer model")
    p(HEAD)
    p(_row(f"bit-identical={r['E1_determinism']['bit_identical']}", r["E1_determinism"]))

    p("\nE2/E3  the SAME recording, moved by k samples  (the apparatus floor)")
    p(HEAD)
    for k, v in r["E2_E3_alignment"].items():
        p(_row(k, v))

    p("\nE4  single-property perturbations of that recording")
    for name, sweep in r["E4_sweeps"].items():
        if not isinstance(sweep, dict):
            continue
        p(f"\n  -- {name} --")
        p(HEAD)
        for k, v in sweep.items():
            p(_row(k, v))

    p("\nE4b  f0 moved ALONE through the model (no tape-speed confound)")
    b = r["E4b_pure_f0_via_model"]
    p(f"  base {b['base_hz']} Hz")
    p(HEAD)
    for k, v in b["rungs"].items():
        p(_row(f"{k}  ({v['f0_error_pct']:+.2f} %, {v['f0_hz']:.2f} Hz)", v))

    p("\nE5  the tom pitch drop: the pre-#154 x1.7 against the measured hardware")
    t = r["E5_tom_pitch_drop"]
    p(f"  full clip {t['full_clip_s']} s; the drop lives in the first {t['drop_window_ms']:g} ms")
    p(HEAD)
    p(_row("FLOOR same patch twice", t["floor_same_patch_twice"]))
    p(_row("FLOOR 1-sample shift (full)", t["floor_1_sample_shift"]))
    p(_row("FLOOR 1-sample shift (60 ms)", t["floor_1_sample_shift_first_60ms"]))
    for k, v in t["vs_shipped"].items():
        p(_row(f"{k}  [full clip]", v["full"]))
        p(_row(f"{k}  [first 60 ms]", v["first_60ms"]))
    p("  per-property estimator, same renders (Pitch drop, Hz):")
    for k, v in t["per_property_pitch_drop_hz"].items():
        p(f"    {k:<24s} {v}")

    p("\nE6  identifiability: the GAIN error that reads the same as a 5 % DECAY error")
    p(HEAD)
    p(_row("5 % decay error", r["E6_exchange_rate"]["target_5pct_decay"]))
    e = r["E6_exchange_rate"]["equivalent_gain_db"]
    p(f"  equivalent gain (dB): " + "  ".join(f"{k}={e[k]}" for k in e))

    p("\nE5a  the SAME tom render, misaligned by k samples")
    p(HEAD)
    for k, v in r["E5_tom_pitch_drop"]["alignment_on_this_signal"].items():
        p(_row(k, v))

    p("\nE7  ceiling: different voices of the same machine")
    p(HEAD)
    for k, v in r["E7_ceiling"].items():
        if isinstance(v, dict):
            p(_row(k, v))
        else:
            p(f"  {k:<34s} {v}")
    p("\nE8  does a distance see a defect the BOARD's three BD metrics miss?")
    b = r["E8_blind_spot"]
    p(HEAD)
    p(_row("FLOOR 1-sample shift", b["floor_1_sample_shift"]))
    for k, v in b["injections"].items():
        p(_row(k, v["distances"]))
        p(f"      board's own metrics all pass: {v['all_per_property_pass']}")
        for mn, mv in v["per_property"].items():
            if mv.get("verdict") in ("pass", "FAIL"):
                p(f"        {mn:<22s} {mv['unperturbed']:>10.4f} -> {mv['injected']:>10.4f} "
                  f"{mv['units']:<3s} err {mv['error']:.4f} / tol {mv['tolerance']:.4f} "
                  f"= {mv['normalised']:.3f}  {mv['verdict']}")
            else:
                p(f"        {mn:<22s} {mv['verdict']}: {mv['reason']}")
    p("\nE9  how much of a log-domain distance is the recording's own noise floor?")
    p(HEAD)
    for k, v in r["E9_noise_floor"].items():
        if isinstance(v, dict):
            p(_row(k, v))
        else:
            p(f"  {k:<34s} {v}")
    p("")


if __name__ == "__main__":
    sys.exit(main())
