#!/usr/bin/env python3
"""Three deterministic decompositions, borrowed from neural-vocoder
discriminators, offered to `model/test_discrimination.py` as extra FEATURE
COLUMNS.

Nothing here is learned. There is no encoder, no embedding, no parameter
fitted to the reference and no model selected by looking at a result. Each
function is a fixed transform of one conditioned clip into a fixed-length
vector with fixed names, and the vector is handed to the same L2 logistic
regression, read on the same knob-equivalent yardstick, and attributed with
the same permutation importance the study already uses. That is the whole
point: a learned judge would score better and mean less.

    MPD  -- multi-period fold (HiFi-GAN, BigVGAN). Fold the 1-D signal into
            2-D at a fixed stride and take statistics ACROSS rows. It is a
            NON-STATIONARITY detector and nothing more specific: the stride
            is not commensurate with any oscillator here (131 Hz at 48 kHz is
            366.4 samples, 800 Hz is 60.0, and the strides are 78..480), so a
            perfectly stable tone already walks in phase from row to row.
            IT IS NOT A DRIFT MEASURE and is not read as one. What it adds
            over the study's 320 log-mel/MFCC columns is that those are
            within-window aggregates and these are not.
    JIT  -- the actual cycle trajectory: find the signal's own period, then
            track that partial's PHASE cycle by cycle. This is the probe for
            #56 ("three stable oscillators do not sound like three analogue
            ones"). It carries its own falsifying control -- see
            `test_static_detuning_is_not_reported_as_drift`, because a mix of
            perfectly stable detuned oscillators BEATS, and a feature that
            cannot tell beating from drift is measuring beating.
    DECISION ON MPD (#138, 2026-10-01): KEEP THE CODE, DO NOT CARRY THE COLUMNS.
            docs/discrimination.md 5c measured all six strides at <= +0.002 of
            pooled accuracy each (48 columns). The columns are already out of
            every default path: `features(..., extra=False)` and
            `discrimination_run --features base` do not call this module. Only
            `--features plus|both` does, and that arm exists to reproduce 5c.
            Deleting `mpd_features` would change the 190-column vector, the
            names every recorded result was attributed against and the
            12 self-tests, to remove code nothing on the board imports, and
            would erase `test_mpd_would_have_caught_a_sample_indexed_fold` and
            `test_a_fixed_stride_fold_is_not_a_drift_measure`, which are the
            record of WHY a fixed-stride fold is not a drift measure. Nothing
            promotes from MPD; do not add it to a new arm.
    CQT  -- constant-Q log-frequency sub-bands (BigVGAN v2). Complementary to
            the mel ladder the study already has, not better than it: 6 bands
            per octave is finer BELOW 1 kHz, where mel spacing is linear at
            ~65 Hz, and coarser above it. The MS-SB-CQT paper makes the same
            point about CQT against STFT.
    MS   -- the same band energy at several time resolutions. #109's shape:
            the rimshot is -18.8 dB at 4 ms and +1.0 dB at 10 ms, and one
            window averages a sign change into a single number.

THE RATE TRAP, AND WHY EVERY PERIOD HERE IS IN SECONDS
------------------------------------------------------
The reference is 44.1 kHz and our renders are 48 kHz. A literal HiFi-GAN MPD
folds at 2, 3, 5, 7, 11 *samples*, which at two different rates is two
different fold frequencies -- so such a feature separates the two sides
perfectly while measuring nothing but the sample rate. That is the exact
failure `docs/failure-modes.md` is about: an estimator that answers in the
place where evidence belongs.

So every period below is stated in SECONDS and converted with
`round(sr * T)`, every band edge is stated in Hz, and the longest fold is
constrained so that the integer rounding is under 1 % at both rates.
`test_extra_features_are_rate_independent` is the assertion, and
`test_mpd_would_have_caught_a_sample_indexed_fold` is the red control that
shows the test can fail -- a sample-indexed fold is carried alongside purely
so that something in this file demonstrably separates 44.1 k from 48 k.

Run the self-tests:

    .venv/bin/python -m pytest model/discrimination_features.py -q
"""
from __future__ import annotations

import numpy as np

# ---------------------------------------------------------------------------
# Fixed, pre-registered constants. Nothing below is chosen by looking at a
# result; each is pinned to a documented quantity of the instrument.
# ---------------------------------------------------------------------------

# Fold periods, in seconds. The ladder is geometric with ratio sqrt(2) from
# 10 ms (100 Hz) to 1.77 ms (566 Hz), which brackets the fundamentals of the
# 808's six square oscillators (205.3 .. 800 Hz, drums_fx.OSC_HZ) and the tom
# and body modes (50 .. 400 Hz). The shortest is 78 samples at 44.1 kHz, so
# `round(sr*T)` costs at most 0.6 % of a period at either rate.
MPD_PERIODS_S = (10.0e-3, 7.07e-3, 5.0e-3, 3.54e-3, 2.5e-3, 1.77e-3)
MPD_SEGS = 2                    # attack half, tail half

# The dominant-period probe. Lags are searched in a band, in Hz, so the search
# is rate-independent; 120..1200 Hz covers every bridged-T body and every
# square oscillator except the 205.3 Hz one's sub-octave.
JIT_BAND_HZ = (120.0, 1200.0)
JIT_MIN_CYCLES = 6              # below this the drift slope is not estimated

# Constant-Q sub-bands: geometric edges, 6 per octave, 40 Hz .. 16 kHz.
# 6/octave is 2 semitones -- fine enough to put the cowbell's measured 558.35
# and 823.70 Hz partials three bands apart and the toms' 80/90/100 Hz TUNING
# positions in distinct bands, which a 40-band mel ladder (linear, ~65 Hz
# wide, below 1 kHz) cannot do.
CQT_FMIN, CQT_FMAX, CQT_BPO = 40.0, 16000.0, 6
CQT_SEGS = 2

# Multi-scale: the same six bands at three window lengths from the onset.
MS_WINDOWS_S = (4.0e-3, 10.0e-3, 25.0e-3)
MS_BANDS_HZ = ((20.0, 200.0), (200.0, 700.0), (700.0, 2000.0),
               (2000.0, 5000.0), (5000.0, 9000.0), (9000.0, 18000.0))

# What the rate-independence test is allowed to call "the same number" at
# 44.1 kHz and 48 kHz: a relative tolerance, floored by an ABSOLUTE one in the
# column's own units, because a relative test on a column whose correct value
# is zero can never pass. Each floor is stated against what the column has to
# be able to see, and `test_the_rate_tolerance_is_far_below_the_effect` holds
# the two apart so the tolerance can never quietly grow into the signal:
#   ppm  20     -- measured: 0.1 % per-period jitter reads 14 534 ppm, and the
#                  44.1 k -> 48 k artefact on the same clip reads 0.78 ppm.
#   dB   0.5    -- under the 1 dB the graded controls move.
#   ratio 0.02  -- correlations and normalised RMS, on 0..1-ish scales.
RATE_TOL_REL = 0.12
RATE_TOL_ABS = {"ppm": 20.0, "db": 0.5, "ratio": 0.02, "ms": 0.05, "count": 1.0}

# THE FLOOR UNDER `jit.dphase_ar1`, in ppm of a period -- a PHYSICAL floor,
# not an epsilon, and it is read on the PHASE SCATTER, which is the quantity
# `jit.phasejit_ppm` already reports and `RATE_TOL_ABS["ppm"]` above already
# states a floor for. #763.
#
# The defect. `dphase_ar1` is the lag-1 autocorrelation of the per-cycle
# period error. On a clip whose period is perfectly stable there is no period
# error to correlate, so the column reported the autocorrelation of whatever
# numerical noise was left -- and that noise is NOT the same at the two rates
# this study compares. Measured (ppm of a period; ar1 on numpy 2.5.4):
#
#     stimulus                      phasejit 44.1k / 48k    dphase_ar1 44.1k / 48k
#     330 Hz square, no jitter        0.000 /  0.780           +0.312 / +0.348
#     440 Hz square, no jitter        0.000 /  0.318           +0.225 / -0.203   <- SIGN FLIP
#     660 Hz square, no jitter        0.000 /  0.711           +0.294 / -0.048   <- SIGN FLIP
#     330 Hz, 0.1 % jitter         5724.6   / 5724.3           +0.606 / +0.605
#
# At 44.1 kHz the generated square's periods are whole samples, so its phase
# residual is float rounding; at 48 kHz it is `resample_poly` residue. Both
# are under 1 ppm, which is the same ~0.8 ppm artefact `RATE_TOL_ABS["ppm"]`
# is already set against -- and 20 ppm is already this module's statement of
# what it cannot see. So 20 ppm is the floor here too: below it the column
# REFUSES (returns the module's zero, as every other refusal in this file
# does) rather than reporting the autocorrelation of a resampler. The rest of
# the row is still reported; this is a refusal of one column.
#
# WHAT THE FIRST ATTEMPT GOT WRONG, because it is the more useful record.
# The obvious quantity to floor is the one `ar1` is computed from -- the
# per-cycle period error, `np.diff(lin).std()`. A 20 ppm floor on THAT
# silences `test_static_detuning_is_not_reported_as_drift`: +0.5 Hz of static
# detuning over 0.24 s is only 12 % of a beat cycle, so its per-cycle period
# error is 4.56 ppm even though its phase scatter is 54.53 ppm and its `ar1`
# is 0.948. A floor on the differenced residual has to fit between 0.80 and
# 4.56 ppm; a floor on the phase scatter has between 0.78 and 54.53, which is
# a factor of 70 rather than a factor of 5.7. The gate reads the phase
# scatter for that reason, not because it is the handier variable.
#
# 20 is a point on the plateau of a committed sweep -- see
# docs/sensitivity/dphase-ar1-floor.json, which is where to argue about the
# value. An integer because tools/sensitivity.py reads a shipped
# python-constant as one.
JIT_DPHASE_FLOOR_PPM = 20


def _unit_of(name: str) -> str:
    if "ppm" in name:
        return "ppm"
    if name.startswith(("cqt", "ms")) or "_db" in name or "enstd" in name:
        return "db"
    if "period_ms" in name:
        return "ms"
    if "ncycles" in name:
        return "count"
    return "ratio"

_EPS = 1e-20


# ---------------------------------------------------------------------------
def _segments(x: np.ndarray, n_seg: int):
    b = np.linspace(0, len(x), n_seg + 1).astype(int)
    return [x[b[i]:max(b[i + 1], b[i] + 1)] for i in range(n_seg)]


def _fold(x: np.ndarray, p: int) -> np.ndarray:
    """(n_folds, p). Rows are consecutive periods; nothing is padded, the
    remainder is dropped, so every row is a whole period."""
    n = len(x) // p
    return x[:n * p].reshape(n, p) if n >= 2 else np.empty((0, p))


def _fold_stats(F: np.ndarray) -> dict:
    """Statistics ACROSS folds. Every one is a ratio or a correlation, so a
    pure gain on the clip cancels and the numbers are comparable between a
    reference recording and one of our renders."""
    if F.shape[0] < 2:
        return dict(shapevar=0.0, dshape=0.0, rowcorr=1.0, enstd=0.0)
    ref = float(np.sqrt((F ** 2).mean()) + _EPS)
    # how much each within-period sample position varies from fold to fold
    shapevar = float(F.std(axis=0).mean() / ref)
    # consecutive-period shape change
    d = np.diff(F, axis=0)
    dshape = float(np.sqrt((d ** 2).mean()) / ref)
    # consecutive-period correlation: 1.0 for an exactly repeating waveform
    a, b = F[:-1], F[1:]
    az = a - a.mean(axis=1, keepdims=True)
    bz = b - b.mean(axis=1, keepdims=True)
    den = np.sqrt((az ** 2).sum(1) * (bz ** 2).sum(1)) + _EPS
    rowcorr = float(np.mean((az * bz).sum(1) / den))
    # drift of the fold's own energy, in dB
    e = 10.0 * np.log10((F ** 2).mean(axis=1) + _EPS)
    enstd = float(e.std())
    return dict(shapevar=shapevar, dshape=dshape, rowcorr=rowcorr, enstd=enstd)


def mpd_features(x: np.ndarray, sr: int) -> tuple:
    """Multi-period fold at fixed periods stated in SECONDS."""
    vals, names = [], []
    for s, seg in enumerate(_segments(np.asarray(x, float), MPD_SEGS)):
        for T in MPD_PERIODS_S:
            p = int(round(sr * T))
            st = _fold_stats(_fold(seg, p)) if p >= 2 else _fold_stats(np.empty((0, 1)))
            for k in ("shapevar", "dshape", "rowcorr", "enstd"):
                vals.append(st[k])
                names.append(f"mpd{T * 1e3:.2f}ms.{k}.seg{s}")
    return np.asarray(vals, float), names


# ---------------------------------------------------------------------------
def dominant_period(x: np.ndarray, sr: int, band=JIT_BAND_HZ) -> int:
    """Lag of the largest autocorrelation peak inside `band`, in samples.

    Deterministic and signal-derived: an argmax over a stated lag range, no
    threshold tuned on anything. Returns 0 when the window holds fewer than
    JIT_MIN_CYCLES of the winning lag, and the caller must then refuse rather
    than report -- a drift slope over three cycles is not a measurement."""
    x = np.asarray(x, float)
    if not np.any(x):
        return 0
    # Condition on the span between the first and last non-zero sample: the
    # JIT_MIN_CYCLES gate and the mean must not count digital silence the
    # caller padded the window with (#617).
    nz = np.nonzero(x)[0]
    x = x[nz[0]:nz[-1] + 1]
    if len(x) < 16:
        return 0
    lo, hi = int(sr / band[1]), int(sr / band[0])
    hi = min(hi, len(x) // JIT_MIN_CYCLES)
    if hi <= lo + 1:
        return 0
    n = 1 << int(np.ceil(np.log2(2 * len(x))))
    X = np.fft.rfft(x - x.mean(), n)
    r = np.fft.irfft(X * np.conj(X), n)[:hi + 1]
    if r[0] <= 0:
        return 0
    return int(lo + np.argmax(r[lo:hi + 1]))


def refine_frequency(x: np.ndarray, sr: int, f0: float,
                     span: float = 0.03, n: int = 241) -> float:
    """The frequency inside +-`span` of `f0` with the largest coherent
    projection. A grid argmax, so it is deterministic and has no threshold;
    `n` is odd so `f0` itself is always on the grid."""
    x = np.asarray(x, float)
    t = np.arange(len(x)) / sr
    fs = f0 * np.linspace(1.0 - span, 1.0 + span, n)
    m = np.abs(np.exp(-2j * np.pi * np.outer(fs, t)) @ x)
    return float(fs[int(np.argmax(m))])


def jitter_features(x: np.ndarray, sr: int, dphase_floor_ppm: float | None = None,
                    trace: dict | None = None) -> tuple:
    """Fold at the signal's OWN dominant period and ask whether consecutive
    cycles repeat. This is #56 as a number.

    `cyclecorr` is 1.0 for a waveform that repeats exactly at that period --
    which is what a fixed phase increment produces and what an analogue
    oscillator does not.

    THE FIRST VERSION OF THIS FUNCTION WAS A SAMPLE-RATE DETECTOR, and the
    record is worth more than the fix. It timed each cycle by the peak of a
    cross-correlation against the first cycle, refined with a parabola. That
    estimator's bias depends on where the true period falls between two
    samples, so the identical signal at 44.1 kHz and 48 kHz reported 0 and
    -1025 ppm of "drift" -- a difference of the sample grid, reported in the
    place where an oscillator's instability belongs. It was caught by
    `test_extra_features_are_rate_independent` before any number was quoted.

    What replaces it: the frequency is refined by coherent projection, then
    each cycle-length block is projected onto that frequency and the PHASE of
    the projection is read. A single-bin projection's phase is unbiased
    whatever the sample alignment. `phasejit_ppm` is the scatter of that
    phase about a straight line, as parts per million of a period;
    `phasecurv_ppm` is its quadratic term, which is an oscillator whose pitch
    moves over the note rather than one that is noisy about a fixed pitch.

    Every column is refused -- zeroed, with `jit.valid` at 0 -- when the
    window does not hold JIT_MIN_CYCLES of the winning period.
    `jit.dphase_ar1` is refused on its own, with the rest of the row still
    reported, when the phase scatter is below JIT_DPHASE_FLOOR_PPM -- there
    is then no period instability for its autocorrelation to be about. See
    that constant.

    `dphase_floor_ppm` overrides that floor and exists so
    `tools/dphase_floor_sweep.py` can sweep the dial against the code that
    SHIPS rather than against a copy of it; `trace`, if a dict is passed,
    receives the scatters the gate reads. Neither changes a shipped number:
    both default to off."""
    x = np.asarray(x, float)
    names = ["jit.cyclecorr.seg0", "jit.cycledshape.seg0", "jit.phasejit_ppm.seg0",
             "jit.phasecurv_ppm.seg0", "jit.dphase_ar1.seg0", "jit.enstd_db.seg0",
             "jit.period_ms.seg0", "jit.ncycles.seg0", "jit.valid.seg0"]
    p = dominant_period(x, sr)
    if p < 4:
        return np.zeros(len(names)), names
    F = _fold(x, p)
    if F.shape[0] < JIT_MIN_CYCLES:
        return np.zeros(len(names)), names
    st = _fold_stats(F)
    f = refine_frequency(x, sr, sr / p)
    pr = sr / f
    ncyc = int(len(x) / pr)
    if ncyc < JIT_MIN_CYCLES:
        return np.zeros(len(names)), names
    # A CONSTANT-LENGTH, WINDOWED projection, hopping one period at a time.
    # Both properties are load-bearing and the second version of this function
    # got the first one wrong: cutting each block at `round(i*pr)` makes the
    # blocks 145 and 146 samples long by turns, each spanning a different
    # fraction of a cycle, and the leakage that varies with it reported
    # 293 ppm of "jitter" on a signal that has none. A fixed window length
    # removes it, and a Hann window keeps the square's own harmonics out of
    # the bin. The exponent uses the ABSOLUTE sample index, so the phase is
    # referenced to one clock across the whole clip and a one-sample shift of
    # a window over a stationary tone does not move it.
    L = max(8, int(round(4.0 * pr)))
    if len(x) < L + int(round(pr)) * (JIT_MIN_CYCLES - 1):
        return np.zeros(len(names)), names
    w = np.hanning(L)
    ncyc = 1 + (len(x) - L) // max(1, int(round(pr)))
    if ncyc < JIT_MIN_CYCLES:
        return np.zeros(len(names)), names
    ph, amp = [], []
    for i in range(ncyc):
        a = int(round(i * pr))
        b = a + L
        if b > len(x):
            break
        n = np.arange(a, b)
        z = complex(np.sum(w * x[a:b] * np.exp(-2j * np.pi * f * n / sr)))
        ph.append(np.angle(z))
        amp.append(abs(z))
    ncyc = len(ph)
    if ncyc < JIT_MIN_CYCLES:
        return np.zeros(len(names)), names
    amp = np.asarray(amp)
    if amp.max() <= _EPS:
        return np.zeros(len(names)), names
    ph = np.unwrap(np.asarray(ph))
    idx = np.arange(ncyc, dtype=float)
    lin = ph - np.polyval(np.polyfit(idx, ph, 1), idx)
    quad = np.polyfit(idx, ph, 2)[0] if ncyc >= 4 else 0.0
    # THE COLUMN THAT MAKES THE JITTER CLAIM FALSIFIABLE, AND THE FIRST TRY
    # AT IT DID NOT WORK. Phase scatter alone cannot tell instability from
    # beating: two PERFECTLY stable detuned oscillators swing the dominant
    # partial's phase at the beat rate, and +2 Hz of static detuning reads
    # 17 473 ppm where 0.1 % per-cycle jitter reads 14 534. So a phasejit
    # number is not a drift number and is never quoted as one.
    #
    # The obvious fix -- the lag-1 autocorrelation of the phase residual --
    # ALSO fails, and the reason is worth the line: per-cycle length jitter
    # INTEGRATES into a random-walk phase, which is as smooth as a beat.
    # Measured: beating 0.997, jitter 0.986. Indistinguishable.
    #
    # What separates them is the lag-1 autocorrelation of the DIFFERENCED
    # residual, i.e. of the per-cycle period error rather than the phase. A
    # random walk differences to white (0.60-0.68 measured); a beat and a
    # glide difference to another smooth curve (0.80-1.00). The gap is real
    # but it is a gap, not an order of magnitude, and
    # `test_static_detuning_is_not_reported_as_drift` asserts its size so it
    # cannot quietly close.
    #
    # ...AND THE GATE UNDER IT IS PHYSICAL, NOT AN EPSILON. `d.std() > 1e-20`
    # was the whole precondition until #763, and float rounding alone clears
    # it by four orders of magnitude, so on a clip with no real period
    # instability this column reported the autocorrelation of the noise --
    # which is not the same noise at 44.1 kHz as at 48 kHz. The epsilon stays
    # as what it actually is, a divide-by-zero guard; the REFUSAL is the
    # physical floor. See JIT_DPHASE_FLOOR_PPM.
    k = 1e6 / (2.0 * np.pi)
    d = np.diff(lin)
    pjit = k * float(lin.std())         # phase scatter, ppm of a period
    dscat = k * float(d.std())          # per-cycle period error, ppm of a period
    floor = JIT_DPHASE_FLOOR_PPM if dphase_floor_ppm is None else float(dphase_floor_ppm)
    refused = not (len(d) >= 4 and d.std() > _EPS and pjit > floor)
    if trace is not None:
        trace.update(phasejit_ppm=pjit, dphase_scatter_ppm=dscat,
                     dphase_floor_ppm=floor, ncycles=float(ncyc), refused=refused)
    if refused:
        ar1 = 0.0
    else:
        a1 = float(np.corrcoef(d[:-1], d[1:])[0, 1])
        ar1 = a1 if np.isfinite(a1) else 0.0
    vals = [st["rowcorr"], st["dshape"], pjit, k * float(quad), ar1,
            st["enstd"], 1e3 * pr / sr, float(ncyc), 1.0]
    return np.asarray(vals, float), names


# ---------------------------------------------------------------------------
_CQT_EDGES: dict = {}


def cqt_edges() -> np.ndarray:
    """Geometric band edges, in Hz. Rate-independent by construction."""
    if "e" not in _CQT_EDGES:
        n = int(np.round(CQT_BPO * np.log2(CQT_FMAX / CQT_FMIN)))
        _CQT_EDGES["e"] = CQT_FMIN * 2.0 ** (np.arange(n + 1) / CQT_BPO)
    return _CQT_EDGES["e"]


def cqt_features(x: np.ndarray, sr: int, floor_db: float = -80.0) -> tuple:
    """Log energy in constant-Q sub-bands, per time segment.

    Integrated from one power spectrum per segment rather than from a true
    variable-resolution CQT: the band EDGES are what resolve the partials,
    and a genuine per-band window length would make the low bands longer than
    the segment they are measured in. Stated because it is a real difference
    from the BigVGAN v2 discriminator this borrows from."""
    x = np.asarray(x, float)
    e = cqt_edges()
    vals, names = [], []
    for s, seg in enumerate(_segments(x, CQT_SEGS)):
        w = np.hanning(len(seg)) if len(seg) > 1 else np.ones(len(seg))
        P = np.abs(np.fft.rfft(seg * w)) ** 2
        f = np.fft.rfftfreq(len(seg), 1.0 / sr)
        tot = float(P.sum()) + _EPS
        for k in range(len(e) - 1):
            m = (f >= e[k]) & (f < e[k + 1])
            # energy DENSITY per band, normalised by the clip's total: a ratio,
            # so a pure gain cancels exactly as it does for the log-mel columns
            v = float(P[m].sum()) / tot if m.any() else 0.0
            vals.append(max(floor_db, 10.0 * np.log10(v + _EPS)))
            names.append(f"cqt{e[k]:.0f}Hz.seg{s}")
    return np.asarray(vals, float), names


# ---------------------------------------------------------------------------
def multiscale_features(x: np.ndarray, sr: int, floor_db: float = -80.0) -> tuple:
    """The same six bands measured over 4, 10 and 25 ms from the onset, plus
    the two differences between consecutive scales.

    The differences are linear combinations of the absolutes and so add
    nothing a linear classifier could not already form. They are here for
    ATTRIBUTION: #109's finding is a sign change between two scales, and a
    permutation importance can only name it if it is a column."""
    x = np.asarray(x, float)
    vals, names = [], []
    tab = {}
    for T in MS_WINDOWS_S:
        n = max(8, int(round(sr * T)))
        seg = x[:n]
        w = np.hanning(len(seg))
        P = np.abs(np.fft.rfft(seg * w)) ** 2
        f = np.fft.rfftfreq(len(seg), 1.0 / sr)
        tot = float(P.sum()) + _EPS
        for lo, hi in MS_BANDS_HZ:
            m = (f >= lo) & (f < hi)
            v = float(P[m].sum()) / tot if m.any() else 0.0
            db = max(floor_db, 10.0 * np.log10(v + _EPS))
            tab[(T, lo)] = db
            vals.append(db)
            names.append(f"ms{T * 1e3:.0f}ms.{lo:.0f}-{hi:.0f}Hz.seg0")
    for a, b in zip(MS_WINDOWS_S[:-1], MS_WINDOWS_S[1:]):
        for lo, hi in MS_BANDS_HZ:
            vals.append(tab[(a, lo)] - tab[(b, lo)])
            names.append(f"msd{a * 1e3:.0f}-{b * 1e3:.0f}ms.{lo:.0f}-{hi:.0f}Hz.seg0")
    return np.asarray(vals, float), names


# ---------------------------------------------------------------------------
def extra_features(x: np.ndarray, sr: int, dphase_floor_ppm: float | None = None,
                   jit_trace: dict | None = None) -> tuple:
    """Every column this module adds, in one fixed order.

    `dphase_floor_ppm` and `jit_trace` are `jitter_features`' two hooks,
    forwarded. Until #768 they stopped here -- the hooks shipped with #763 but
    nothing above this function could reach them, so the floor could be swept
    (`tools/dphase_floor_sweep.py` calls `jitter_features` directly) and could
    not be turned OFF in a discrimination run. Both default to off, so a
    caller that passes neither gets exactly the shipped numbers."""
    parts = [mpd_features(x, sr),
             jitter_features(x, sr, dphase_floor_ppm=dphase_floor_ppm, trace=jit_trace),
             cqt_features(x, sr), multiscale_features(x, sr)]
    vals = np.concatenate([p[0] for p in parts])
    names = [n for p in parts for n in p[1]]
    return vals, names


#: The four states one clip's `jit.dphase_ar1.seg0` can be in. They are
#: distinct on purpose: `value == 0` cannot tell them apart, because
#: `jitter_features` zeroes EVERY `jit.*` column when the window does not hold
#: JIT_MIN_CYCLES, and zeroes this one alone when the floor refuses it. A
#: census that counted zeroes would report the first as the second.
ROW_REPORTED = "reported"
ROW_FLOOR = "floor_refused"
ROW_DEGENERATE = "degenerate_refused"
ROW_INVALID = "row_invalid"
ROW_STATES = (ROW_REPORTED, ROW_FLOOR, ROW_DEGENERATE, ROW_INVALID)


def dphase_row_state(x: np.ndarray, sr: int,
                     dphase_floor_ppm: float | None = None) -> tuple[str, dict]:
    """Which of ROW_STATES this clip's `jit.dphase_ar1.seg0` is in, and the
    trace the decision was read from. #768's counting primitive.

    READ FROM THE REFUSAL DECISION, NEVER FROM THE VALUE. `jitter_features`
    sets `refused` from three conditions at once --

        refused = not (len(d) >= 4 and d.std() > _EPS and pjit > floor)

    -- and only the third is the physical floor #763 added. The other two are
    the pre-existing divide-by-zero guard. A census that reported every
    refusal as a floor refusal would credit the floor with rows it did not
    touch, which is the direction that would make #763 look bigger than it is.
    So `floor_refused` is asserted to be the third condition specifically, and
    a refusal for either of the other two is reported as `degenerate_refused`.

    `row_invalid` is the fourth state and it is NOT a refusal of this column:
    it is the whole row being refused (`jit.valid.seg0` is 0) because the
    window did not hold JIT_MIN_CYCLES of the winning period. Those early
    returns leave before `trace.update`, so an empty trace is exactly that
    case -- the one signal that separates "already invalid" from
    "floor-refused" without re-deriving the gate here."""
    trace: dict = {}
    vals, names = extra_features(x, sr, dphase_floor_ppm=dphase_floor_ppm,
                                 jit_trace=trace)
    row = dict(zip(names, vals))
    if not trace:
        assert row["jit.valid.seg0"] == 0.0, (
            "an empty trace with jit.valid set: jitter_features returned "
            "through a path that neither filled the trace nor zeroed the row, "
            "so this census cannot say which state the row is in")
        return ROW_INVALID, trace
    if not trace["refused"]:
        return ROW_REPORTED, trace
    # refused -- now say BY WHAT. The floor is the `pjit > floor` leg.
    if trace["phasejit_ppm"] <= trace["dphase_floor_ppm"]:
        return ROW_FLOOR, trace
    return ROW_DEGENERATE, trace


def census_counts(states) -> dict:
    """ROW_STATES -> how many, every key present even at zero. A side with no
    refusals is printed as `0 / total`, not omitted (#768's edge case)."""
    out = {k: 0 for k in ROW_STATES}
    for s in states:
        out[s] += 1
    out["total"] = sum(out[k] for k in ROW_STATES)
    return out


def extra_names(sr: int = 44100, n: int = 4096) -> list:
    return extra_features(np.zeros(n), sr)[1]


def extra_group(nm: str) -> str | None:
    """Which attribution bucket a name from this module belongs to, or None
    if it is not one of ours. Keeps `feature_groups` from having to know the
    naming scheme twice."""
    if nm.startswith("mpd"):
        return "mpd." + nm.split(".")[1]
    if nm.startswith("jit."):
        # SPLIT ON PURPOSE. `period_ms`, `ncycles` and `valid` say what the
        # dominant partial's FREQUENCY is -- a pitch error, which this study
        # already measures and which has nothing to do with #56. The rest say
        # how steady that partial is. Attributing them to one bucket would let
        # a tuning error be reported as oscillator instability.
        return ("jit.period" if any(k in nm for k in ("period_ms", "ncycles", "valid"))
                else "jit.stability")
    if nm.startswith("cqt"):
        hz = float(nm[3:].split("Hz")[0])
        for lo, hi in ((0, 200), (200, 700), (700, 2000), (2000, 5000),
                       (5000, 9000), (9000, 20000)):
            if lo <= hz < hi:
                return f"cqt.{lo}-{hi}Hz"
        return "cqt.9000-20000Hz"
    if nm.startswith("msd"):
        return "ms.scale-difference"
    if nm.startswith("ms"):
        return "ms." + nm.split(".")[0][2:]
    return None


# ===========================================================================
# Self-tests. The study has withdrawn published figures before; nothing here
# is quoted until these pass.
# ===========================================================================
def _square_periods(f0, sr, dur=0.24, jitter=0.0, seed=0):
    """The WHOLE-SAMPLE period lengths `_square` actually emits.

    Split out of `_square` because the jitter a caller asks for is not
    necessarily the jitter it gets: each period is rounded to an integer
    number of samples, so `jitter=0.001` at 440 Hz / 44.1 kHz (100.23 samples
    per period) is rounded away entirely and the "jittered" clip is the
    jitterless one under another name. Any test that uses a jittered stimulus
    as a POSITIVE control has to assert that the jitter survived the
    generator, and this is what it asserts on."""
    rng = np.random.default_rng(seed)
    ks, n, total = [], int(sr * dur), 0
    while total < n:
        p = (1.0 / f0) * (1.0 + jitter * rng.standard_normal())
        k = max(2, int(round(sr * p)))
        ks.append(k)
        total += k
    return ks


def _square(f0, sr, dur=0.24, jitter=0.0, seed=0):
    """A square wave at f0, optionally with per-period length jitter. The
    jitter is what an analogue oscillator has and a phase accumulator does
    not, so it is the signal these features exist to see."""
    out = []
    for k in _square_periods(f0, sr, dur, jitter, seed):
        out += [1.0] * (k // 2) + [-1.0] * (k - k // 2)
    return np.asarray(out[:int(sr * dur)], float)


def _resample(x, sr_in, sr_out):
    """Band-limited, because the test must measure the FEATURE and not the
    resampler. A linear interpolation was used here first and its own
    high-frequency roll-off moved the 9-18 kHz multi-scale columns by 2.8 dB,
    which looked exactly like a rate-dependent feature and was not one."""
    from math import gcd
    from scipy.signal import resample_poly
    g = gcd(int(sr_in), int(sr_out))
    return resample_poly(x, int(sr_out) // g, int(sr_in) // g)


# The stimulus panel the rate test runs. ONE stimulus is not a rate test:
# `jit.dphase_ar1.seg0` moved 0.25 -> 0.35 on the 330 Hz clip (a gap a
# 12 % relative tolerance happens to straddle depending on the numpy build)
# and +0.23 -> -0.20 on the 440 Hz one -- a SIGN FLIP, which no tolerance
# should ever admit and which the single-stimulus version of this test could
# not see. #763. The jittered entry is the positive control: a floor that
# silenced the column everywhere would pass a panel of jitterless clips.
RATE_PANEL = (
    ("330Hz", 330.0, 0.0, 0),
    ("440Hz", 440.0, 0.0, 0),
    ("660Hz", 660.0, 0.0, 0),
    ("330Hz+0.1%jitter", 330.0, 0.001, 3),
)


def _rate_stimulus(f0, jitter, seed, sr=44100, dur=0.24, decay_s=0.08):
    """One panel entry at `sr`, with the exponential decay the original
    single-stimulus test used."""
    x = _square(f0, sr, dur=dur, jitter=jitter, seed=seed)
    return x * np.exp(-np.arange(len(x)) / (decay_s * sr))


# Columns this panel finds rate-dependent that #763 did NOT repair, each with
# the gap measured on BOTH numpy builds (1.26.4/scipy 1.11.4 and 2.5.4/scipy
# 1.18.1 -- identical to four decimals) and a bound a little above it. Filed
# as #767: the mechanism is fold geometry (an integer-sample fold period whose
# FRACTIONAL part differs between the rates), not the missing physical
# precondition #763 is about, and putting both in one PR would have left
# neither with its own control.
#
# A quarantine is a tolerance widened to fit unless it is falsifiable in BOTH
# directions, so the test below asserts each entry is STILL over tolerance as
# well as still under its bound. A stale entry -- one whose column got fixed
# -- turns this red, which is how #767 gets closed completely rather than
# mostly.
RATE_KNOWN_RED = {
    ("440Hz", "jit.cycledshape.seg0"): 0.030,
    ("660Hz", "mpd5.00ms.rowcorr.seg0"): 0.032,
    ("660Hz", "mpd5.00ms.rowcorr.seg1"): 0.032,
}


def test_extra_features_are_rate_independent():
    """THE test in this file. The reference is 44.1 kHz and our renders are
    48 kHz, so a feature that moves with the rate is a sample-rate detector
    wearing a measurement's name."""
    bad, quarantined = [], {}
    for label, f0, jitter, seed in RATE_PANEL:
        if jitter:
            # the apparatus's precondition: a stimulus that was supposed to
            # carry jitter and does not is a jitterless clip in disguise, and
            # as a positive control it would prove nothing
            ks = _square_periods(f0, 44100, jitter=jitter, seed=seed)
            assert len(set(ks)) > 1, (
                f"{label}: the generator rounded the jitter away; this panel "
                "entry is not the positive control it claims to be")
        a = _rate_stimulus(f0, jitter, seed)
        b = _resample(a, 44100, 48000)
        va, names = extra_features(a, 44100)
        vb, _ = extra_features(b, 48000)
        for nm, x, y in zip(names, va, vb):
            tol = max(RATE_TOL_REL * max(abs(x), abs(y)), RATE_TOL_ABS[_unit_of(nm)])
            if abs(x - y) <= tol:
                continue
            if (label, nm) in RATE_KNOWN_RED:
                quarantined[(label, nm)] = abs(x - y)
                continue
            bad.append((label, nm, round(x, 4), round(y, 4)))
    assert not bad, f"rate-dependent columns: {bad[:8]}"

    # the quarantine, checked from both sides
    grew = {k: (v, RATE_KNOWN_RED[k]) for k, v in quarantined.items()
            if v > RATE_KNOWN_RED[k]}
    assert not grew, (f"a known-red column got worse (gap, bound): {grew} -- "
                      "#767, and this is not a bound to raise without a reason")
    stale = sorted(set(RATE_KNOWN_RED) - set(quarantined))
    assert not stale, (f"{stale} is in RATE_KNOWN_RED but is no longer "
                       "rate-dependent: delete the entry (see #767). A "
                       "quarantine nobody removes stops being a quarantine")


def test_the_dphase_floor_refuses_the_input_that_defeats_it():
    """THE GUARD SHIPS WITH THE INPUT THAT DEFEATS IT (verification-rules
    rule 8). Ask it at write time: what satisfies `dphase_ar1 is a real
    number` while violating its intent?

    The answer is the 48 kHz side of this study's own rate pair. A square
    generated at 44.1 kHz has whole-sample periods, so its phase residual is
    float rounding and reads 0.000 ppm; band-limit it to 48 kHz and
    `resample_poly` leaves 0.3-0.8 ppm of residue, which is not zero, is not
    the instrument, and is enough for a lag-1 autocorrelation to come back
    looking like a measurement -- +0.225 at 44.1 kHz against -0.203 at
    48 kHz on the 440 Hz clip, a SIGN FLIP. That input is what this test
    feeds the guard.

    So the floor has to sit above that residue and below the smallest real
    mechanism the column must still see. Both edges are asserted here, with
    their margins, because a floor is only honest next to what it must not
    hide -- the same contract `test_the_rate_tolerance_is_far_below_the_effect`
    holds for the sibling column."""
    def scatter(x, sr):
        trace: dict = {}
        vals, names = jitter_features(x, sr, trace=trace)
        return (trace["phasejit_ppm"], dict(zip(names, vals))["jit.dphase_ar1.seg0"],
                trace["refused"])

    # 1. the defeating input, at BOTH rates: refused, and the column is zero
    for label, f0 in (("330Hz", 330.0), ("440Hz", 440.0), ("660Hz", 660.0)):
        a = _rate_stimulus(f0, 0.0, 0)
        b = _resample(a, 44100, 48000)
        for sr, x in ((44100, a), (48000, b)):
            ppm, ar1, refused = scatter(x, sr)
            assert refused and ar1 == 0.0, (label, sr, ppm, ar1)
            assert ppm < 0.25 * JIT_DPHASE_FLOOR_PPM, (
                f"{label} at {sr}: a clip with no period instability scatters "
                f"{ppm:.3f} ppm, within 4x of the {JIT_DPHASE_FLOOR_PPM} ppm "
                "floor -- the margin this guard needs has closed")

    # 2. a pure stable tone is refused too: 0.47 ppm, and nothing to report
    sr = 44100
    t = np.arange(int(0.24 * sr)) / sr
    ppm, ar1, refused = scatter(np.sin(2 * np.pi * 410.0 * t), sr)
    assert refused and ar1 == 0.0, (ppm, ar1)

    # 3. and the other edge: real jitter is still reported, at both rates,
    #    with matching values. Its jitter must survive the generator first --
    #    a positive control that is secretly jitterless proves the opposite
    #    of what it claims (440 Hz at 44.1 kHz rounds 0.1 % away entirely).
    ks = _square_periods(330.0, sr, jitter=0.001, seed=3)
    assert len(set(ks)) > 1, "the generator rounded the jitter away"
    a = _rate_stimulus(330.0, 0.001, 3)
    b = _resample(a, 44100, 48000)
    pa, ra, fa = scatter(a, 44100)
    pb, rb, fb = scatter(b, 48000)
    assert not fa and not fb, (pa, pb)
    assert min(pa, pb) > 50.0 * JIT_DPHASE_FLOOR_PPM, (
        f"0.1 % jitter scatters {min(pa, pb):.1f} ppm against a "
        f"{JIT_DPHASE_FLOOR_PPM} ppm floor -- the gap the floor must leave "
        "open has closed")
    assert abs(ra - rb) <= RATE_TOL_ABS["ratio"], (ra, rb)
    assert ra > 0.3 and rb > 0.3, (ra, rb)

    # 4. the smallest mechanism the detuning control depends on: +0.5 Hz of
    #    static detuning, 54.53 ppm of scatter against a 20 ppm floor. This
    #    is the TIGHT edge -- 2.7x, not 50x -- and if it ever closes,
    #    test_static_detuning_is_not_reported_as_drift goes silent rather
    #    than red, which is the failure this assertion exists to prevent.
    beat = np.sin(2 * np.pi * 410.0 * t) + 0.7 * np.sin(2 * np.pi * 410.5 * t)
    ppm, ar1, refused = scatter(beat, sr)
    assert not refused, (
        f"+0.5 Hz detuning scatters {ppm:.2f} ppm and is refused by a "
        f"{JIT_DPHASE_FLOOR_PPM} ppm floor -- the beating control cannot run")
    assert ppm > 2.0 * JIT_DPHASE_FLOOR_PPM, ppm
    assert ar1 > 0.80, ar1


def test_the_floor_census_counts_a_refusal_from_the_decision_not_the_zero():
    """#768's counter, with the input that defeats it and the inverted
    comparison that must turn it red.

    WHAT THE COUNTER IS FOR. #768 asks how many rows of the published
    sixteen-voice run fall under JIT_DPHASE_FLOOR_PPM, on the 44.1 kHz
    reference side and our 48 kHz render side separately. The obvious
    implementation counts `jit.dphase_ar1.seg0 == 0`, and it is wrong: this
    module zeroes EVERY `jit.*` column when the window does not hold
    JIT_MIN_CYCLES, so a row that was already invalid before #763 existed
    reads as a floor refusal and the floor gets credited with it. The three
    states are therefore separated here, and the separation is asserted on
    inputs whose state is known independently of the counter.

    THE INPUT THAT DEFEATS IT (verification-rules rule 8): a silent window.
    `np.zeros` is refused by the floor on the value test -- its column is 0.0
    -- and is NOT a floor refusal; it never reaches the gate at all. That is
    the first case below, and `ROW_INVALID` is what distinguishes it.

    NO CONTROL FOR `degenerate_refused`, stated rather than skipped. That
    state is the pre-#763 `len(d) >= 4 and d.std() > _EPS` leg, with
    _EPS = 1e-20. JIT_MIN_CYCLES = 6 already forces `len(d) >= 5`, and no
    synthetic input was found that clears six cycles while scattering less
    than 1e-20 of a period -- float rounding alone is four orders of
    magnitude above it, which is the measurement #763 was built on. The state
    is counted separately anyway, so if one ever occurs it is not silently
    added to the floor's tally."""
    sr = 44100
    t = np.arange(int(0.24 * sr)) / sr

    # 1. silence: the whole row is refused, so it is NOT the floor's doing
    state, trace = dphase_row_state(np.zeros(int(0.24 * sr)), sr)
    assert state == ROW_INVALID, (state, trace)
    assert trace == {}, trace

    # 2. under the floor: a stable tone, 0.47 ppm against 20 ppm
    under = np.sin(2 * np.pi * 410.0 * t)
    state, trace = dphase_row_state(under, sr)
    assert state == ROW_FLOOR, (state, trace)
    assert trace["phasejit_ppm"] <= JIT_DPHASE_FLOOR_PPM, trace

    # 3. over the floor: 0.1 % per-period jitter, ~1e3 ppm. Its jitter must
    #    survive the generator first, or this is a jitterless clip wearing a
    #    positive control's name (the same precondition the sweep asserts).
    ks = _square_periods(330.0, sr, jitter=0.001, seed=3)
    assert len(set(ks)) > 1, "the generator rounded the jitter away"
    over = _square(330.0, sr, jitter=0.001, seed=3)
    state, trace = dphase_row_state(over, sr)
    assert state == ROW_REPORTED, (state, trace)
    assert trace["phasejit_ppm"] > JIT_DPHASE_FLOOR_PPM, trace

    # 4. the counts, with every key present even at zero
    c = census_counts([dphase_row_state(x, sr)[0] for x in (under, over, under)])
    assert c == {ROW_REPORTED: 1, ROW_FLOOR: 2, ROW_DEGENERATE: 0,
                 ROW_INVALID: 0, "total": 3}, c

    # 5. START RED. The counter's one comparison is `phasejit_ppm <= floor`.
    #    Invert it and the two rows whose state is known swap, so cases 2 and
    #    3 above are load-bearing rather than decorative. This is the defect
    #    the control exists for, run here rather than asserted to have been
    #    run once.
    def inverted(x):
        """dphase_row_state with its one comparison the wrong way round."""
        trace: dict = {}
        extra_features(x, sr, jit_trace=trace)
        if not trace:
            return ROW_INVALID
        if not trace["refused"]:
            return ROW_REPORTED
        return (ROW_FLOOR if trace["phasejit_ppm"] >= trace["dphase_floor_ppm"]
                else ROW_DEGENERATE)

    assert inverted(under) == ROW_DEGENERATE, (
        "the inverted comparison still calls the under-floor row a floor "
        "refusal: case 2 is not testing the comparison")
    assert dphase_row_state(under, sr)[0] != inverted(under)

    # 6. ...and the floor is what decides it, not the signal. Turn the floor
    #    off the way the A/B arm does and the under-floor row is reported.
    state, trace = dphase_row_state(under, sr, dphase_floor_ppm=0.0)
    assert state == ROW_REPORTED, (state, trace)
    assert trace["dphase_floor_ppm"] == 0.0, trace


def test_the_rate_tolerance_is_far_below_the_effect():
    """A tolerance is only honest next to the effect it must not hide. The
    phase-jitter column's rate artefact is ~0.8 ppm and its floor is 20 ppm;
    the smallest oscillator instability it exists to see is three orders of
    magnitude above that, and this test fails if that gap ever closes."""
    sr = 44100
    stable = jitter_features(_square(410.0, sr, jitter=0.0), sr)
    drift = jitter_features(_square(410.0, sr, jitter=0.001, seed=3), sr)
    d = dict(zip(stable[1], stable[0]))
    j = dict(zip(drift[1], drift[0]))
    assert d["jit.phasejit_ppm.seg0"] < RATE_TOL_ABS["ppm"]
    assert j["jit.phasejit_ppm.seg0"] > 50.0 * RATE_TOL_ABS["ppm"], j["jit.phasejit_ppm.seg0"]


def test_mpd_would_have_caught_a_sample_indexed_fold():
    """The red control for the test above: a LITERAL HiFi-GAN fold at 2, 3,
    5, 7, 11 samples is carried here purely to show the rate-independence
    check can fail. If this ever passes, the check above is vacuous.

    The signal is a tone at 14700 Hz, which is exactly 44100/3: folded at
    three samples it is perfectly stationary at 44.1 kHz and is not at
    48 kHz, so a sample-indexed fold reads a difference of nine dozen ppm of
    sample clock as a difference of instrument."""
    t = np.arange(int(0.24 * 44100)) / 44100.0
    a = np.sin(2 * np.pi * 14700.0 * t)
    b = _resample(a, 44100, 48000)
    moved = 0
    for p in (2, 3, 5, 7, 11):
        sa = _fold_stats(_fold(a, p))
        sb = _fold_stats(_fold(b, p))
        for k in sa:
            if abs(sa[k] - sb[k]) / max(abs(sa[k]), abs(sb[k]), 1e-6) > 0.12:
                moved += 1
    assert moved >= 6, ("a sample-indexed fold did NOT move between 44.1 k and "
                        "48 k, so the rate-independence test proves nothing")


def test_a_pure_gain_cancels():
    """Every column is a ratio, a correlation or a normalised dB, so scaling
    the clip must not move any of them. Level matching already removes gain,
    but a feature that survives it without this property would reintroduce
    the corpus's -2.4 dBFS peak limiting as a cue."""
    a = _square(220.0, 44100) * np.exp(-np.arange(int(0.24 * 44100)) / (0.05 * 44100))
    va, names = extra_features(a, 44100)
    vb, _ = extra_features(a * 0.137, 44100)
    bad = [(n, x, y) for n, x, y in zip(names, va, vb)
           if abs(x - y) / max(abs(x), abs(y), 1.0) > 1e-6]
    assert not bad, f"gain-dependent columns: {bad[:8]}"


def test_jitter_separates_a_stable_oscillator_from_a_drifting_one():
    """#56, as a closed-form check. A phase-accumulator square repeats
    exactly; give the same oscillator 0.5 % per-period jitter and the
    cycle-to-cycle correlation must fall and the lag spread must rise."""
    sr = 44100
    stable = _square(410.0, sr, jitter=0.0)
    drift = _square(410.0, sr, jitter=0.005, seed=3)
    vs, names = jitter_features(stable, sr)
    vd, _ = jitter_features(drift, sr)
    d = dict(zip(names, vs))
    j = dict(zip(names, vd))
    assert d["jit.valid.seg0"] == 1.0 and j["jit.valid.seg0"] == 1.0
    assert d["jit.cyclecorr.seg0"] > 0.99, d["jit.cyclecorr.seg0"]
    assert j["jit.cyclecorr.seg0"] < d["jit.cyclecorr.seg0"] - 0.02, (
        d["jit.cyclecorr.seg0"], j["jit.cyclecorr.seg0"])
    assert j["jit.phasejit_ppm.seg0"] > 3.0 * max(d["jit.phasejit_ppm.seg0"], 1.0), (
        d["jit.phasejit_ppm.seg0"], j["jit.phasejit_ppm.seg0"])


def test_jitter_refuses_rather_than_reports_on_too_few_cycles():
    """REFUSED is a first-class outcome. A drift slope over three cycles is
    not a measurement and must come back flagged invalid, not plausible."""
    sr = 44100
    # 4 ms: shorter than JIT_MIN_CYCLES of even the fastest lag the search is
    # allowed to return, so there is no period it could honestly report.
    x = _square(150.0, sr, dur=0.004)
    v, names = jitter_features(x, sr)
    assert dict(zip(names, v))["jit.valid.seg0"] == 0.0
    assert dominant_period(x, sr) == 0


def test_cqt_resolves_two_partials_a_fifth_apart_that_mel_smears():
    """The cowbell's measured 558.35 / 823.70 Hz. A constant-Q ladder at 6
    bands per octave must put them in different bands with an empty band
    between; this is the property the linear 380-560 / 1500-2100 Hz splits
    the study uses cannot have."""
    sr = 44100
    n = int(0.24 * sr)
    t = np.arange(n) / sr
    x = np.sin(2 * np.pi * 558.35 * t) + np.sin(2 * np.pi * 823.70 * t)
    v, names = cqt_features(x, sr)
    seg0 = [(nm, val) for nm, val in zip(names, v) if nm.endswith(".seg0")]
    peaks = [i for i, (nm, val) in enumerate(seg0)
             if val > -20.0]
    assert len(peaks) >= 2, seg0
    assert max(peaks) - min(peaks) >= 2, f"partials not separated: {peaks}"


def test_multiscale_sees_a_sign_change_one_window_averages_away():
    """#109's shape, constructed. A clip that is bright for 4 ms and dull
    afterwards must give a scale-difference column of one sign while the
    25 ms column alone reports the opposite."""
    sr = 44100
    n = int(0.24 * sr)
    t = np.arange(n) / sr
    hi = np.sin(2 * np.pi * 6000 * t)
    lo = np.sin(2 * np.pi * 300 * t)
    env = np.where(t < 0.004, 1.0, 0.0)
    x = hi * env + lo * (1 - env) * np.exp(-t / 0.05)
    v, names = multiscale_features(x, sr)
    d = dict(zip(names, v))
    assert d["ms4ms.5000-9000Hz.seg0"] > d["ms25ms.5000-9000Hz.seg0"] + 10.0
    assert d["msd4-10ms.5000-9000Hz.seg0"] > 5.0
    assert d["msd4-10ms.200-700Hz.seg0"] < -5.0


def test_every_column_has_a_stable_name_and_an_attribution_bucket():
    """A number with no actionable name is not a deliverable. Every column
    must land in exactly one bucket, and the name set must not depend on the
    signal -- otherwise two clips would produce different-length rows."""
    v1, n1 = extra_features(np.zeros(4096), 44100)
    v2, n2 = extra_features(_square(300.0, 44100), 44100)
    assert n1 == n2 and len(v1) == len(v2) == len(n1)
    assert len(set(n1)) == len(n1), "duplicate feature names"
    for nm in n1:
        assert extra_group(nm) is not None, nm
        assert ".seg" in nm, nm


def test_no_column_is_nan_or_infinite_on_a_silent_or_a_full_scale_clip():
    for x in (np.zeros(4096), np.ones(4096), np.full(4096, -1.0)):
        v, names = extra_features(x, 44100)
        bad = [n for n, val in zip(names, v) if not np.isfinite(val)]
        assert not bad, bad


def test_static_detuning_is_not_reported_as_drift():
    """THE CONTROL THAT MAKES #56 FALSIFIABLE.

    Perfectly stable oscillators already produce a changing waveform, because
    detuned ones BEAT. A feature that fires on static detuning is measuring
    beating and calling it drift, and #56 -- "three stable oscillators do not
    sound like three analogue ones" -- would get a number that means nothing.

    So two things are asserted here. First, that the phase-scatter column on
    its own FAILS this control, which is why it is never quoted alone.
    Second, that the differenced-residual autocorrelation passes it with a
    stated margin."""
    sr = 44100
    n = int(0.24 * sr)
    t = np.arange(n) / sr

    def read(x):
        v, nm = jitter_features(x, sr)
        return dict(zip(nm, v))

    detuned = [read(np.sin(2 * np.pi * 410.0 * t) + 0.7 * np.sin(2 * np.pi * (410.0 + d) * t))
               for d in (0.5, 2.0, 8.0)]
    jittered = [read(_square(410.0, sr, jitter=j, seed=3)) for j in (0.001, 0.005, 0.02)]
    stable = read(np.sin(2 * np.pi * 410.0 * t))

    # 1. the falsifier: scatter ALONE does not separate the two mechanisms
    assert max(r["jit.phasejit_ppm.seg0"] for r in detuned) > \
        min(r["jit.phasejit_ppm.seg0"] for r in jittered), (
            "phase scatter separated beating from jitter -- if this ever becomes true "
            "the comment in jitter_features is wrong and the column may be quoted alone")

    # 2. what does separate them, with its margin
    beat = min(r["jit.dphase_ar1.seg0"] for r in detuned)
    jit = max(r["jit.dphase_ar1.seg0"] for r in jittered)
    assert beat > jit + 0.20, f"beating {beat:.3f} vs jitter {jit:.3f}: the gap has closed"
    assert beat > 0.80 and jit < 0.72, (beat, jit)

    # 3. a steady pitch glide is a smooth mechanism too, and must land with
    #    the beats rather than with the jitter
    glide = read(np.sin(2 * np.pi * (410.0 * t + 0.5 * 410.0 * 0.01 / 0.24 * t ** 2)))
    assert glide["jit.dphase_ar1.seg0"] > 0.80, glide["jit.dphase_ar1.seg0"]

    # 4. and a perfectly stable single tone reports no jitter at all
    assert stable["jit.phasejit_ppm.seg0"] < RATE_TOL_ABS["ppm"]


def test_a_fixed_stride_fold_is_not_a_drift_measure():
    """The MPD columns are a non-stationarity view, not a drift view, and the
    docstring says so. This is the arithmetic: the strides are not
    commensurate with the oscillators, so a PERFECTLY stable tone already
    walks from row to row. If this ever stopped being true the MPD columns
    could be read as cycle-aligned, and they cannot be."""
    sr = 48000
    n = int(0.24 * sr)
    t = np.arange(n) / sr
    x = np.sin(2 * np.pi * 131.0 * t)              # 366.4 samples per cycle
    walked = 0
    for T in MPD_PERIODS_S:
        p = int(round(sr * T))
        assert abs(366.4 / p - round(366.4 / p)) > 0.02, (T, p)
        if _fold_stats(_fold(x, p))["dshape"] > 0.05:
            walked += 1
    assert walked == len(MPD_PERIODS_S), (
        "a stable tone did NOT walk between rows at every stride, so the fold "
        "might be cycle-aligned after all")
