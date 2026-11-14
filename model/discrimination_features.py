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

So every period below is stated in SECONDS and every band edge in Hz.
`test_extra_features_are_rate_independent` is the assertion, and
`test_mpd_would_have_caught_a_sample_indexed_fold` is the red control that
shows the test can fail -- a sample-indexed fold is carried alongside purely
so that something in this file demonstrably separates 44.1 k from 48 k.

AND STATING A PERIOD IN SECONDS IS NOT ENOUGH ON ITS OWN, WHICH IS #767. For
most of this file's life the seconds were converted with `round(sr * T)` and
the cycle fold used `dominant_period`, an integer lag, and the claim was that
the rounding is "under 1 % at both rates". It is -- and under 1 % was not
small enough. The FRACTIONAL part of a fold period is not the same at the two
rates (5.0 ms is 220.5 samples at 44.1 kHz and exactly 240 at 48 kHz; a
440 Hz period is 100.23 against 109.09), consecutive rows walk by that much
per row, and the walk went into the row statistics as if it were the
oscillator: `jit.cycledshape` read 0.0141 against 0.1729 on the same 880 Hz
clip, eight times the tolerance. The periods are now EXACT and the rows are
read by band-limited interpolation -- see FOLD_SINC_TAPS and `_fold_at` --
and the worst gap anywhere on a 44-value f0 sweep in both resampling
directions is 0.0142 against 0.0200.

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

# THE FOLD INTERPOLATOR, in samples of kernel half-width -- #767.
#
# The defect. Every fold in this file used to cut rows at an INTEGER number of
# samples, and the fractional part of the period the row is supposed to be is
# not the same at 44.1 kHz as at 48 kHz. Two consequences, measured:
#   * `jit.cycledshape` folds at `dominant_period`, an integer lag. A 440 Hz
#     period is 100.23 samples at 44.1 kHz and 109.09 at 48 kHz, so row i
#     starts i*0.23 samples late on one side and i*0.09 early on the other --
#     the rows WALK, and the walk is a property of the sample grid.
#   * `mpd*` folds at `round(sr*T)`: 220 samples at 44.1 kHz where 5.0 ms is
#     220.5, against exactly 240 at 48 kHz. The module docstring already says
#     every period here is stated in SECONDS; `round` was the one place that
#     quietly made it samples again.
# Worst measured 44.1k->48k gap before the repair: 0.159 in
# `jit.cycledshape.seg0` at 880 Hz against a 0.02 tolerance, 8x over.
#
# The repair is to fold at the EXACT period and read each row position by
# band-limited interpolation -- `_fold_at`. This constant is that
# interpolator's only dial: the half-width, in samples, of the Hann-windowed
# sinc kernel. It is a point on a committed grid --
# docs/sensitivity/fold-interpolator.json, which is where to argue about the
# value -- and an integer because tools/sensitivity.py reads a shipped
# python-constant as one.
#
# -1 is not a width. It selects the integer-lag fold that shipped BEFORE this
# repair, and exists so `tools/probes/fold_rate_sweep.py` can measure the
# defect against the code that SHIPS rather than against a copy of it. It is
# never the shipped value; `test_the_fold_interpolator_ships_a_real_width`
# asserts that.
FOLD_SINC_TAPS = 16

# THE OCTAVE MARGIN, in thousandths of a normalised autocorrelation -- #774.
#
# The defect. `dominant_period` was a bare argmax of the autocorrelation over
# the whole 120..1200 Hz lag band, and on a clip whose true period falls near
# a HALF SAMPLE it returned TWO periods: 880 Hz built at 48 kHz (where
# `_square` emits 55-sample periods) and resampled down to 44.1 kHz has a true
# period of 50.53 samples there, and the function returned 101. `jit.period_ms`
# then read 2.22 ms against 1.15 ms on the other side of the same rate pair,
# and the five columns folded at that period followed it -- a pitch error
# reported as a pitch measurement, on the fourth-ranked attribution bucket in
# docs/discrimination.md.
#
# THE MECHANISM, MEASURED, AND IT IS NOT THE ONE #774 SUSPECTED. The issue
# guessed the lag-T peak was "split between two integer lags while the lag-2T
# peak is not". Taking the autocorrelation's max over +-2 lags, so a split
# peak cannot hide, refutes that: r[T] at the best integer lag near a
# fractional T is simply LOWER (0.9857 -> 0.9660), because the waveform does
# not line up there. Meanwhile 2T is fractional too, but when frac(T) ~ 0.5
# the lag 2T lands NEAR an integer (frac(T)=0.531 => dist(2T,int)=0.062), so
# r[2T] barely moves. The deciding margin is only +0.0029 to +0.0053, against
# an intrinsic preference for T of -0.0141 on an aligned clip.
# tools/probes/dominant_period_octave.py is that measurement, and it is where
# the numbers in this paragraph come from.
#
# The repair is NOT a different estimator. It is a bound on the choice between
# octave candidates: among the integer SUBMULTIPLES of the winning lag, return
# the shortest one that is a local peak of the autocorrelation and is within
# this margin of the winning peak. Submultiples only, because the defect is an
# integer-multiple pick and a rule over every lag in the band would change the
# estimator on clips that have no octave problem; a local peak, because a clip
# that is not periodic at all has an autocorrelation that DECAYS from lag 0,
# so every shorter lag beats the winner on height while none of them is a
# peak. See `_octave_corrected`.
#
# 20 thousandths is not fitted to the failing clips. RATE_TOL_ABS["ratio"] is
# 0.02: it is the resolution this module already states for a correlation-unit
# column, i.e. the difference below which it declines to call two such numbers
# different. A margin at that resolution is 3.8x the +0.0053 the worst failing
# clip needs and is the same anchoring JIT_DPHASE_FLOOR_PPM takes from
# RATE_TOL_ABS["ppm"]. It is a point on a committed grid --
# docs/sensitivity/dominant-period-octave-margin.json, which is where to argue
# about the value -- and an integer in thousandths because
# tools/sensitivity.py reads a shipped python-constant as an integer.
#
# 0 is not a margin. It selects the bare argmax that shipped BEFORE this
# repair and is on the grid so the sweep prices the repair and not only the
# dial inside it, exactly as -1 is on FOLD_SINC_TAPS's grid and 0 is on
# dphase-ar1-floor's. It is never the shipped value;
# `test_the_octave_margin_ships_a_real_margin` asserts that.
JIT_OCTAVE_MARGIN_PERMILLE = 20


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


def _read_at(x: np.ndarray, pos: np.ndarray, taps: int) -> np.ndarray:
    """`x` read at FRACTIONAL sample positions, by Hann-windowed sinc.

    Band-limited on purpose, for the reason `_resample` is: a linear
    interpolation has its own high-frequency roll-off, and a roll-off that
    varies with the sub-sample offset is exactly the kind of artefact that
    looks like a rate-dependent feature and is not one.

    Two properties are load-bearing:
      * at an INTEGER position the kernel is 1 at that sample and exactly 0
        at every other (`sinc` of a non-zero integer is 0), so this reduces
        to plain indexing and a fold whose period happens to be whole
        reproduces `_fold` to the bit. `test_the_fold_reduces_to_indexing_on_a_whole_period`
        is that assertion.
      * the kernel is normalised to unit sum, so the read has no DC droop
        that varies with the offset. Normalising is a no-op at an integer
        position, so it does not cost the property above.

    Outside the clip `x` is zero, which is the same convention `_fold` has by
    dropping the remainder.
    """
    x = np.asarray(x, float)
    if taps <= 0:
        return x[np.clip(np.rint(pos).astype(int), 0, len(x) - 1)]
    xp = np.concatenate([np.zeros(taps), x, np.zeros(taps)])
    i0 = np.floor(pos).astype(int)
    out = np.zeros(pos.shape, float)
    wsum = np.zeros(pos.shape, float)
    for k in range(-taps + 1, taps + 1):
        d = pos - (i0 + k)
        w = np.where(np.abs(d) < taps,
                     np.sinc(d) * (0.5 + 0.5 * np.cos(np.pi * d / taps)), 0.0)
        out += w * xp[np.clip(i0 + k + taps, 0, len(xp) - 1)]
        wsum += w
    return out / np.where(np.abs(wsum) < _EPS, 1.0, wsum)


def _fold_at(x: np.ndarray, period: float, taps: int | None = None) -> np.ndarray:
    """(n_folds, P) at an EXACT, possibly fractional, `period`. #767.

    `_fold`'s sibling and the one the shipped features use. Row i starts at
    `i * period` samples -- not at `i * round(period)` -- so consecutive rows
    stay aligned to the waveform however the period falls between two
    samples, and the P = `round(period)` columns of a row are read at
    `period / P` apart so a row still spans exactly one period. The row COUNT
    and the column count are unchanged from `_fold`; only the alignment is.

    `taps` overrides FOLD_SINC_TAPS and exists so the sweep instrument can
    measure the shipped code rather than a copy of it. `taps < 0` selects the
    integer-lag `_fold` that shipped before #767, which is how the probe
    reproduces the defect.
    """
    taps = FOLD_SINC_TAPS if taps is None else int(taps)
    P = int(round(period))
    if P < 2 or not np.isfinite(period):
        return np.empty((0, max(P, 1)))
    if taps < 0:
        return _fold(np.asarray(x, float), P)
    x = np.asarray(x, float)
    step = period / P
    n = int(np.floor((len(x) - 1 - (P - 1) * step) / period)) + 1
    if n < 2:
        return np.empty((0, P))
    pos = (np.arange(n, dtype=float)[:, None] * period
           + np.arange(P, dtype=float)[None, :] * step)
    return _read_at(x, pos, taps)


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


def mpd_features(x: np.ndarray, sr: int, taps: int | None = None) -> tuple:
    """Multi-period fold at fixed periods stated in SECONDS -- and folded at
    `sr * T` exactly, not at `round(sr * T)`.

    THE ROUNDING WAS THE ONE PLACE THIS MODULE STATED A PERIOD IN SECONDS AND
    THEN USED SAMPLES. 5.0 ms is 220.5 samples at 44.1 kHz and exactly 240 at
    48 kHz, so the rounded stride was 0.23 % short on one side of the study's
    rate pair and exact on the other, and `rowcorr` on a tone whose period is
    near-commensurate with the stride read the difference: -0.1339 against
    -0.1613 at 660 Hz, against a 0.02 tolerance. See FOLD_SINC_TAPS and #767.

    `taps` is the interpolator override, for the sweep instrument only."""
    vals, names = [], []
    for s, seg in enumerate(_segments(np.asarray(x, float), MPD_SEGS)):
        for T in MPD_PERIODS_S:
            st = _fold_stats(_fold_at(seg, sr * T, taps))
            for k in ("shapevar", "dshape", "rowcorr", "enstd"):
                vals.append(st[k])
                names.append(f"mpd{T * 1e3:.2f}ms.{k}.seg{s}")
    return np.asarray(vals, float), names


# ---------------------------------------------------------------------------
def _octave_corrected(r: np.ndarray, lo: int, hi: int, best: int,
                      margin: float) -> int:
    """The shortest integer submultiple of `best` that is a local peak of `r`
    and within `margin` of `r[best]`, in units of `r[0]`. #774.

    THE ARGMAX IS NOT WRONG ABOUT WHICH LAGS CORRELATE; IT IS WRONG ABOUT
    WHICH OF TWO OCTAVES TO RETURN. So this does not search the band -- it
    searches `best/k` for integer k, shortest candidate first, and returns
    `best` unchanged when none of them qualifies. Two conditions, and each is
    load-bearing:

      * +-1 LAG of `round(best/k)` is searched, and one lag is enough by
        arithmetic rather than by result: `best` is `k * T` rounded to a whole
        sample, so `best / k` is within `0.5 / k` of the true `T` and
        `round(best / k)` is within 1 of it. A wider window would start
        admitting lags that are not the submultiple.
      * THE CANDIDATE MUST BE A LOCAL PEAK. This is the condition that keeps
        the rule from changing anything on a clip with no octave problem: an
        aperiodic clip's autocorrelation DECAYS from lag 0, so every shorter
        lag is taller than the winner while none of them is a peak, and a
        height-only rule would halve the period of every noise burst in the
        corpus.

    What defeats it, stated rather than left to be discovered
    (docs/verification-rules.md rule 8): a signal whose true period really is
    the longer lag AND which has a real local autocorrelation peak at half of
    it within `margin` -- a pulse train whose alternate cycles differ by less
    than the margin, for instance. Such a clip is reported an octave high, and
    how shallow an alternation that takes is the constraint column of
    docs/sensitivity/dominant-period-octave-margin.json rather than a
    reassurance here. `test_the_octave_margin_refuses_the_input_that_defeats_it`
    is the executable form."""
    if margin <= 0.0 or best <= lo:
        return best
    floor = r[best] - margin * r[0]
    for k in range(best // lo, 1, -1):
        c = int(round(best / k))
        cand = [j for j in range(max(lo, c - 1), min(hi - 1, c + 1) + 1)
                if j >= 1 and r[j] >= r[j - 1] and r[j] >= r[j + 1]]
        if not cand:
            continue
        j = max(cand, key=lambda j: r[j])
        if r[j] >= floor:
            return j
    return best


def dominant_period(x: np.ndarray, sr: int, band=JIT_BAND_HZ,
                    octave_margin: float | None = None) -> int:
    """Lag of the largest autocorrelation peak inside `band`, in samples,
    resolved to the shortest octave that peak is consistent with.

    Deterministic and signal-derived: an argmax over a stated lag range, then
    one comparison against the stated JIT_OCTAVE_MARGIN_PERMILLE. Returns 0
    when the window holds fewer than JIT_MIN_CYCLES of the winning lag, and
    the caller must then refuse rather than report -- a drift slope over three
    cycles is not a measurement.

    THE BARE ARGMAX RETURNED TWO PERIODS ON SOME CLIPS and the record is worth
    more than the fix: 880 Hz built at 48 kHz and resampled to 44.1 kHz came
    back as 101 samples against 55 on the other side of the same rate pair, an
    OCTAVE, so `jit.period_ms` reported 2.22 ms and 1.15 ms for one clip read
    at two rates. It was not a fold-geometry defect and #767's repair could
    not reach it; it is #774. See JIT_OCTAVE_MARGIN_PERMILLE for the measured
    mechanism and `_octave_corrected` for what the margin bounds.

    `octave_margin` overrides that constant, in the same thousandths, and
    exists so `tools/probes/octave_margin_sweep.py` can sweep the dial against
    the code that SHIPS rather than against a copy of it. It does not change a
    shipped number: it defaults to off. 0 selects the bare argmax."""
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
    best = int(lo + np.argmax(r[lo:hi + 1]))
    permille = (JIT_OCTAVE_MARGIN_PERMILLE if octave_margin is None
                else float(octave_margin))
    return _octave_corrected(r, lo, hi, best, permille / 1000.0)


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
                    trace: dict | None = None, taps: int | None = None) -> tuple:
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
    if len(x) // p < JIT_MIN_CYCLES:
        return np.zeros(len(names)), names
    # THE FOLD IS AT THE REFINED PERIOD, NOT AT THE INTEGER LAG. `p` is an
    # argmax over whole sample lags, so it is the true period rounded; folding
    # at it makes row i start i*(pr-p) samples out, and THAT walk -- not the
    # oscillator -- was most of what `cycledshape` reported. It is also not
    # the same walk at the two rates (100.23 samples at 44.1 kHz against
    # 109.09 at 48 kHz for 440 Hz), which is #767. `p` still gates: it is the
    # cycle count the refusal below is about, and it does not depend on the
    # refinement succeeding.
    f = refine_frequency(x, sr, sr / p)
    pr = sr / f
    F = _fold_at(x, pr, taps)
    if F.shape[0] < JIT_MIN_CYCLES:
        return np.zeros(len(names)), names
    st = _fold_stats(F)
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
def extra_features(x: np.ndarray, sr: int, taps: int | None = None) -> tuple:
    """Every column this module adds, in one fixed order.

    `taps` overrides FOLD_SINC_TAPS for every fold below and is the sweep
    instrument's handle on the shipped code; it defaults to off."""
    parts = [mpd_features(x, sr, taps), jitter_features(x, sr, taps=taps),
             cqt_features(x, sr), multiscale_features(x, sr)]
    vals = np.concatenate([p[0] for p in parts])
    names = [n for p in parts for n in p[1]]
    return vals, names


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


def _bl_square(f0, sr, dur=0.24):
    """A square wave whose period is EXACTLY 1/f0 seconds at any rate.

    THE INPUT THAT DEFEATS THE REPAIR #767 MADE (verification-rules rule 8).
    `_square` lays each period down as a whole number of samples, so a clip
    built at 44.1 kHz is exactly integer-periodic THERE and fractional only on
    the resampled side. A fold repair could therefore pass the rate test by
    being right on one side of every pair, for the same reason the original
    integer fold passed the single-stimulus version of the test. This
    generator has no sample grid in it: odd harmonics at 1/k, summed
    analytically up to 0.45*sr, so the period is 84.32 samples at 44.1 kHz and
    91.78 at 48 kHz for the 523 Hz panel entry -- fractional at BOTH, and
    neither side is the side the fold happens to be aligned to.

    Band-limited at 0.45*sr rather than at Nyquist so that the two rates carry
    the same harmonics up to 19.8 kHz and `_resample` is not the thing being
    measured."""
    n = int(sr * dur)
    t = np.arange(n) / float(sr)
    out = np.zeros(n)
    k = 1
    while k * f0 < 0.45 * sr:
        out += np.sin(2 * np.pi * k * f0 * t) / k
        k += 2
    return (4.0 / np.pi) * out


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
# THE PANEL IS THE CLASS, NOT THE THREE ROWS THAT WERE FILED. #767 arrived as
# three `(stimulus, column)` entries and a sweep over f0 showed that was a
# property of which f0 values this tuple happened to hold: `jit.cycledshape`
# was over tolerance at 220, 440, 523, 587 and 880 Hz and `mpd7.07ms.rowcorr`
# at 392, 523 and 880 Hz, none of which the panel contained. Every f0 the
# sweep found is here now, so a repair that works at 440 Hz and not at 880 Hz
# cannot pass.
#
# Three entries are controls rather than cases, and each is load-bearing:
#   * the jittered one is the POSITIVE control -- a floor or a fold that
#     silenced a column everywhere would pass a panel of jitterless clips.
#   * `523Hz bandlimited` is the input that defeats the repair: `_square`
#     builds whole-sample periods, so on every other entry one side of the
#     pair is exactly integer-periodic and a fold could be right there by
#     accident. `_bl_square` has no sample grid in it. See that function.
#   * the two `@48k` entries reverse the resampling direction. Without them
#     "follows the sample rate" and "follows whichever side was resampled"
#     are the same measurement -- and they are not the same mechanism:
#     `jit.cycledshape` turned out to follow the second and `mpd*.rowcorr`
#     the first. tools/probes/fold_rate_sweep.py is where that is measured.
RATE_PANEL = (
    # label,              f0,    jitter, seed, kind,       build rate
    ("220Hz",             220.0, 0.0,    0,    "square",   44100),
    ("330Hz",             330.0, 0.0,    0,    "square",   44100),
    ("392Hz",             392.0, 0.0,    0,    "square",   44100),
    ("440Hz",             440.0, 0.0,    0,    "square",   44100),
    ("523Hz",             523.0, 0.0,    0,    "square",   44100),
    ("587Hz",             587.0, 0.0,    0,    "square",   44100),
    ("660Hz",             660.0, 0.0,    0,    "square",   44100),
    ("880Hz",             880.0, 0.0,    0,    "square",   44100),
    ("330Hz+0.1%jitter",  330.0, 0.001,  3,    "square",   44100),
    ("523Hz bandlimited", 523.0, 0.0,    0,    "blsquare", 44100),
    ("523Hz@48k",         523.0, 0.0,    0,    "square",   48000),
)
# WHY 880 Hz BUILT AT 48 kHz IS STILL NOT IN THE PANEL, now that the reason it
# was excluded has gone. It was kept out because `dominant_period` returned
# 101 samples there -- two periods, an octave error -- so six jit columns moved
# between the rates for a reason that had nothing to do with the fold. #774
# repaired that, and the clip's `jit.cycledshape.seg0` gap at the shipped width
# is now 0.0182 against this module's own 0.0200 tolerance, so it would PASS
# here. It is still out, and the reason is the 9 % of headroom in that
# sentence: what is left on that clip is a fold-geometry residual at the top of
# the dial, and #775 is the open question of whether a 0.12 relative tolerance
# on a column whose whole range is 0.02 means anything. Adding this entry is a
# one-line change the day #775 settles that; adding it now would couple the
# module's main rate test to the tightest margin on the grid. The octave claim
# itself is asserted directly, by
# `test_dominant_period_returns_the_same_octave_at_both_rates` below, over the
# whole class rather than this one clip.

RATE_PAIR = {44100: 48000, 48000: 44100}


def _rate_stimulus(f0, jitter, seed, sr=44100, dur=0.24, decay_s=0.08,
                   kind="square"):
    """One panel entry at `sr`, with the exponential decay the original
    single-stimulus test used."""
    if kind == "blsquare":
        assert not jitter, "_bl_square has no per-period length to jitter"
        x = _bl_square(f0, sr, dur=dur)
    elif kind == "square":
        x = _square(f0, sr, dur=dur, jitter=jitter, seed=seed)
    else:
        raise AssertionError(f"unknown stimulus kind {kind!r}")
    return x * np.exp(-np.arange(len(x)) / (decay_s * sr))


# WHAT IS LEFT OF THE QUARANTINE. #763 landed three `(stimulus, column)`
# entries here -- 440 Hz `jit.cycledshape.seg0` and the two 660 Hz
# `mpd5.00ms.rowcorr` segments -- as a deliberately falsifiable holding pen
# for #767. #767 repaired the mechanism (see FOLD_SINC_TAPS), the entries
# stopped being over tolerance, and an entry that is no longer red turns this
# test red by design, so they are gone rather than kept as decoration. The
# dict stays because the machinery below is the thing that makes a future
# quarantine falsifiable in BOTH directions, and writing that machinery is
# the expensive part; an empty dict costs nothing and asserts nothing.
RATE_KNOWN_RED: dict = {}


def _rate_pair(label, f0, jitter, seed, kind, build_sr):
    """One panel entry as the two clips the test compares, with the
    apparatus's preconditions asserted at the point of use rather than
    assumed -- a stimulus that is not what the panel says it is would make
    this whole test a pass that means nothing."""
    other = RATE_PAIR[build_sr]
    if jitter:
        # a clip that was supposed to carry jitter and does not is a
        # jitterless clip in disguise, and as a positive control it would
        # prove nothing
        ks = _square_periods(f0, build_sr, jitter=jitter, seed=seed)
        assert len(set(ks)) > 1, (
            f"{label}: the generator rounded the jitter away; this panel "
            "entry is not the positive control it claims to be")
    if kind == "blsquare":
        # the input that defeats the fold repair only defeats it if its
        # period really is fractional at both rates
        for sr in (build_sr, other):
            frac = abs((sr / f0) - round(sr / f0))
            assert frac > 0.2, (
                f"{label}: {sr / f0:.3f} samples per period at {sr} is within "
                f"{frac:.3f} of a whole number, so this entry does not test "
                "what it is in the panel to test")
    a = _rate_stimulus(f0, jitter, seed, sr=build_sr, kind=kind)
    return a, build_sr, _resample(a, build_sr, other), other


def _rate_gaps(taps=None):
    """Every column of every panel entry that is over tolerance between the
    two rates, as (label, name, value at the build rate, value at the other,
    gap). `taps` overrides the fold interpolator so the SAME panel can be run
    against the integer fold this repair replaced -- see
    `test_the_integer_fold_this_replaced_is_still_red_on_this_panel`."""
    over = []
    for label, f0, jitter, seed, kind, build_sr in RATE_PANEL:
        a, sr_a, b, sr_b = _rate_pair(label, f0, jitter, seed, kind, build_sr)
        va, names = extra_features(a, sr_a, taps)
        vb, _ = extra_features(b, sr_b, taps)
        for nm, x, y in zip(names, va, vb):
            tol = max(RATE_TOL_REL * max(abs(x), abs(y)), RATE_TOL_ABS[_unit_of(nm)])
            if abs(x - y) > tol:
                over.append((label, nm, round(float(x), 4), round(float(y), 4),
                             round(abs(float(x - y)), 4)))
    return over


def test_extra_features_are_rate_independent():
    """THE test in this file. The reference is 44.1 kHz and our renders are
    48 kHz, so a feature that moves with the rate is a sample-rate detector
    wearing a measurement's name."""
    over = _rate_gaps()
    bad = [r for r in over if (r[0], r[1]) not in RATE_KNOWN_RED]
    assert not bad, f"rate-dependent columns: {bad[:8]}"

    # the quarantine, checked from both sides, so a tolerance cannot be
    # widened to fit by the back door
    quarantined = {(r[0], r[1]): r[4] for r in over}
    grew = {k: (v, RATE_KNOWN_RED[k]) for k, v in quarantined.items()
            if v > RATE_KNOWN_RED[k]}
    assert not grew, (f"a known-red column got worse (gap, bound): {grew} -- "
                      "and this is not a bound to raise without a reason")
    stale = sorted(set(RATE_KNOWN_RED) - set(quarantined))
    assert not stale, (f"{stale} is in RATE_KNOWN_RED but is no longer "
                       "rate-dependent: delete the entry. A quarantine nobody "
                       "removes stops being a quarantine")


def test_the_integer_fold_this_replaced_is_still_red_on_this_panel():
    """THE INJECTED-BUG CONTROL FOR THE TEST ABOVE, and the record of what
    #767 actually was.

    A green rate test proves nothing unless the same panel can be made red,
    and the honest way to make it red is the code that used to ship: an
    integer-lag fold, which `_fold_at(..., taps=-1)` still is. Every column
    named below was over tolerance before the repair and is not after it, so
    if this test ever goes quiet the repair has stopped being load-bearing
    and `test_extra_features_are_rate_independent` has stopped being a test.

    The nine f0 values are the point. #767 was filed with three
    `(stimulus, column)` rows; the class is wider than the panel that found
    it, and this asserts the WHOLE class is red on the old fold."""
    over = _rate_gaps(taps=-1)
    red = {(label, nm) for label, nm, _, _, _ in over}
    must = {
        ("220Hz", "jit.cycledshape.seg0"),
        ("440Hz", "jit.cycledshape.seg0"),
        ("523Hz", "jit.cycledshape.seg0"),
        ("587Hz", "jit.cycledshape.seg0"),
        ("880Hz", "jit.cycledshape.seg0"),
        ("392Hz", "mpd7.07ms.rowcorr.seg0"),
        ("523Hz", "mpd7.07ms.rowcorr.seg0"),
        ("660Hz", "mpd5.00ms.rowcorr.seg0"),
        ("660Hz", "mpd5.00ms.rowcorr.seg1"),
        ("880Hz", "mpd7.07ms.rowcorr.seg0"),
        # the two controls have to be red here too, or they are not controls:
        ("523Hz bandlimited", "jit.cycledshape.seg0"),
        ("523Hz@48k", "jit.cycledshape.seg0"),
    }
    missing = sorted(must - red)
    assert not missing, (
        f"the integer fold no longer moves {missing} between 44.1 k and 48 k, "
        "so the rate test's green is not evidence that the fractional fold is "
        "doing anything -- #767")


# THE CLASS #774 WAS ABOUT, as the panel the repair is read against.
# `_square` quantises its period to whole samples of the BUILD rate, so the
# variable is not f0: the whole class is three emitted periods -- 56 and 55
# samples at 48 kHz and 51 at 44.1 kHz -- which is why a 1 Hz ladder finds
# every failure inside 850..880 Hz built at 48 kHz and 857..873 Hz built at
# 44.1 kHz, and nothing outside it. See
# docs/sensitivity/dominant-period-octave-margin.json.
#
# THE SPLIT IS LOAD-BEARING. #774 was filed with three f0 values, all built at
# 48 kHz, because the sweep instrument that found them shipped with a 25 Hz
# ladder that has no value between 855 and 870. The 44.1 kHz-built half of the
# class was invisible in that framing, so it is the half nothing about this
# repair was selected against: the margin's value is RATE_TOL_ABS["ratio"] and
# the three filed values are the selection set, and CONFIRM is a set neither
# touched.
OCTAVE_SELECT = ((850.0, 48000), (875.0, 48000), (880.0, 48000))
OCTAVE_CONFIRM = ((860.0, 44100), (865.0, 44100), (870.0, 44100))
# Immediately outside the band in both directions. They passed before the
# repair too, which is the point: a margin that changed them would be
# reporting its own presence rather than the signal's period.
OCTAVE_EDGES = ((845.0, 48000), (885.0, 48000), (856.0, 44100), (874.0, 44100))


def _octave_ratio(f0, build_sr, octave_margin=None):
    """The period `dominant_period` reports for one clip read at both rates,
    as a ratio. 1.0 for an estimator that agrees with itself about which
    partial it is measuring; ~2 or ~0.5 on an octave error."""
    other = RATE_PAIR[build_sr]
    a = _rate_stimulus(f0, 0.0, 0, sr=build_sr)
    b = _resample(a, build_sr, other)
    pa = dominant_period(a, build_sr, octave_margin=octave_margin)
    pb = dominant_period(b, other, octave_margin=octave_margin)
    assert pa > 0 and pb > 0, (f0, build_sr, pa, pb)
    return (pb / float(other)) / (pa / float(build_sr))


def test_dominant_period_returns_the_same_octave_at_both_rates():
    """#774, AND IT REPLACES A TEST THAT ASSERTED THE DEFECT WAS PRESENT.

    `test_dominant_period_picks_a_different_octave_at_the_two_rates` lived here
    until the repair landed: it asserted that 880 Hz built at 48 kHz and
    resampled down came back as 101 samples against 55 on the other side --
    EXACTLY an octave -- so that the day it stopped reproducing somebody had to
    come and delete it. This is that deletion, and the assertion is now the
    other way round.

    Both halves of the class are checked and the second one matters more: the
    44.1 kHz-built rows are not what the repair was selected against. The
    tolerance is the 5 % `tools/probes/fold_rate_sweep.py` refuses its own
    pairs at, so a clip this test calls agreed is a clip that instrument does
    not refuse."""
    for name, panel in (("selected on", OCTAVE_SELECT),
                        ("held out from selection", OCTAVE_CONFIRM),
                        ("outside the band", OCTAVE_EDGES)):
        for f0, build_sr in panel:
            ratio = _octave_ratio(f0, build_sr)
            assert abs(ratio - 1.0) < 0.05, (
                f"{f0} Hz built at {build_sr} ({name}): dominant_period reports "
                f"periods {ratio:.4f} apart at the two rates, which is not the "
                "same partial -- #774")


def test_the_bare_argmax_this_replaced_is_still_wrong_on_that_class():
    """THE INJECTED-BUG CONTROL FOR THE TEST ABOVE, and the record of what
    #774 actually was.

    A green octave test proves nothing unless the same panel can be made red,
    and the honest way to make it red is the code that used to ship: a bare
    argmax over the lag band, which `octave_margin=0` still is. Every value in
    both halves of the class must be an octave out there -- the FULL class,
    not the three rows #774 was filed with, because a repair selected on half
    its class is how the one-directional framing happened in the first
    place.

    The edges are asserted from the other side in the same breath: if a margin
    were doing the work there too, this control would make them red as well
    and the panel would stop being able to tell the two apart."""
    for f0, build_sr in OCTAVE_SELECT + OCTAVE_CONFIRM:
        ratio = _octave_ratio(f0, build_sr, octave_margin=0)
        assert abs(ratio - 2.0) < 0.05 or abs(ratio - 0.5) < 0.05, (
            f"{f0} Hz built at {build_sr}: the bare argmax no longer returns a "
            f"different octave ({ratio:.4f}), so the green above is not "
            "evidence that the margin is doing anything -- #774")
    for f0, build_sr in OCTAVE_EDGES:
        assert abs(_octave_ratio(f0, build_sr, octave_margin=0) - 1.0) < 0.05, (
            f"{f0} Hz built at {build_sr} is outside the class and must agree "
            "with the bare argmax too; if it does not, OCTAVE_EDGES is not the "
            "control it claims to be")


def test_the_octave_margin_ships_a_real_margin():
    """0 is not a margin. It selects the bare argmax that shipped before #774,
    is carried for the control above and for the sweep instrument, and must
    never be the value that ships -- a probe hook left switched on is a repair
    that was reverted without anyone editing the repair."""
    assert JIT_OCTAVE_MARGIN_PERMILLE >= 1, JIT_OCTAVE_MARGIN_PERMILLE
    assert JIT_OCTAVE_MARGIN_PERMILLE in (5, 10, 20, 30, 50, 100, 200), (
        f"{JIT_OCTAVE_MARGIN_PERMILLE} is not a point on the committed grid in "
        "docs/sensitivity/dominant-period-octave-margin.json")


def _alternating_square(f0, sr, depth, dur=0.24):
    """A square at `f0` whose alternate periods are scaled by `1 - depth`, so
    its TRUE period is two of `_square`'s periods. The same construction
    `tools/probes/octave_margin_sweep.py` sweeps the dial's constraint on."""
    x = _square(f0, sr, dur=dur)
    period = round(sr / f0)
    return x * np.where((np.arange(len(x)) // period) % 2 == 1, 1.0 - depth, 1.0)


def test_the_octave_margin_refuses_the_input_that_defeats_it():
    """A GUARD SHIPS WITH THE INPUT THAT DEFEATS IT --
    docs/verification-rules.md rule 8. This is that input, and it defeats the
    margin rather than being defeated by it.

    A square whose alternate periods are scaled by `1 - depth` really has
    period 2P, and really does have a local autocorrelation peak at P. So a
    margin wide enough to swallow the gap between them reports the clip an
    octave HIGH -- the mirror image of #774. What is asserted is the BOUNDARY,
    in both directions, so neither side can move unnoticed:

      * at depth 0.20 the gap is +0.0137, under the shipped 20 thousandths,
        and the shipped function returns P. That is the defeat, asserted, not
        a possibility mentioned in a docstring.
      * at depth 0.25 the gap is +0.0292, over it, and the shipped function
        returns the true 2P. So the margin is not simply blind to period
        doubling.
      * at depth 0.10 the gap is NEGATIVE and the BARE ARGMAX returns P too.
        That bounds what the margin costs: the window it loses and the argmax
        did not is 0.12..0.25 of alternation depth, not every period-doubled
        signal.

    400 Hz at both rates, because 2P must fall inside the 120..1200 Hz lag
    band for the estimator to be able to choose it at all."""
    for sr in (44100, 48000):
        period = round(sr / 400.0)
        for depth, expect, why in ((0.20, period, "the margin swallows the gap"),
                                   (0.25, 2 * period, "the gap exceeds the margin")):
            x = _alternating_square(400.0, sr, depth)
            got = dominant_period(x, sr)
            assert got == expect, (
                f"{sr} Hz, alternation depth {depth}: dominant_period returns "
                f"{got}, expected {expect} ({why}). The dial's constraint has "
                "moved -- re-run tools/probes/octave_margin_sweep.py and read "
                "keeps_2t in docs/sensitivity/dominant-period-octave-margin.json")
        shallow = _alternating_square(400.0, sr, 0.10)
        assert dominant_period(shallow, sr, octave_margin=0) == period, (
            f"{sr} Hz, alternation depth 0.10: the BARE ARGMAX no longer "
            "returns the half period, so this depth no longer bounds what the "
            "margin itself costs")


def test_the_octave_margin_does_not_move_the_rate_panel():
    """WHAT THE REPAIR MUST NOT HAVE CHANGED, asserted rather than assumed.

    #774's margin is a change to the estimator every `jit.*` column is seeded
    by, so the honest question is not only "did it fix the class" but "did it
    move anything else". RATE_PANEL is the panel every number on
    `docs/discrimination.md` about the `jit.stability` bucket was measured on,
    and that page says those numbers are unaffected by this repair. This is
    that claim: every clip of every panel entry, at both rates, returns the
    SAME lag under the shipped margin as under the bare argmax it replaced.

    If this ever goes red, the `jit.stability` figures on that page are
    carrying an unmeasured correction from #774 as well as from #767, and the
    page says they are not."""
    for label, f0, jitter, seed, kind, build_sr in RATE_PANEL:
        a, sr_a, b, sr_b = _rate_pair(label, f0, jitter, seed, kind, build_sr)
        for x, sr, side in ((a, sr_a, "built"), (b, sr_b, "resampled")):
            bare = dominant_period(x, sr, octave_margin=0)
            assert bare > 0, (label, side)
            assert dominant_period(x, sr) == bare, (
                f"{label} ({side} at {sr}): the octave margin moves this clip "
                f"from {bare} samples, so the jit.stability numbers in "
                "docs/discrimination.md are not the numbers they were -- #774")


def test_the_octave_check_does_not_touch_a_whole_sample_period():
    """THE VALIDATION CASE WITH AN ANSWER THIS MODULE DID NOT PRODUCE.

    300 Hz divides both 44100 and 48000, so the period is a whole number of
    samples at both rates and the lag is arithmetic: 147 and 160. An octave
    rule that moved these would be reporting its own threshold rather than the
    signal's period, and the margin must therefore be invisible here at every
    width on its grid -- which is `exact` in the sweep artefact, asserted here
    against hand-written numbers rather than against the sweep."""
    for sr, lag in ((44100, 147), (48000, 160)):
        x = _rate_stimulus(300.0, 0.0, 0, sr=sr)
        for margin in (0, 5, 10, 20, 30, 50, 100, 200):
            assert dominant_period(x, sr, octave_margin=margin) == lag, (
                f"{sr} Hz at margin {margin}: expected the arithmetic {lag}")


def test_the_fold_interpolator_ships_a_real_width():
    """`taps < 0` is the legacy integer fold, carried for the control above
    and for the sweep instrument. It is not a width and must never be the
    value that ships -- a probe hook left switched on is a repair that was
    reverted without anyone editing the repair."""
    assert FOLD_SINC_TAPS >= 1, FOLD_SINC_TAPS
    assert FOLD_SINC_TAPS in (1, 2, 4, 8, 16, 32), (
        f"{FOLD_SINC_TAPS} is not a point on the committed grid in "
        "docs/sensitivity/fold-interpolator.json")


def test_the_fold_reduces_to_indexing_on_a_whole_period():
    """THE VALIDATION CASE WITH AN ANSWER THIS MODULE DID NOT PRODUCE.

    When the period is a whole number of samples there is nothing to
    interpolate, and the windowed sinc is 1 at that sample and exactly 0 at
    every other, so `_fold_at` must reproduce `_fold` to float precision. The
    answer comes from arithmetic -- `sinc` of a non-zero integer -- not from
    a reference run of our own code, which is what makes it a validation and
    not a regression lock.

    300 Hz is the case that matters: 44100/300 = 147 and 48000/300 = 160 are
    BOTH whole, so a fold at 300 Hz is exactly aligned at both rates and the
    repair must be a no-op there. If it is not, the interpolator is adding
    something of its own and every number above is partly that."""
    sr = 44100
    x = _rate_stimulus(300.0, 0.0, 0, sr=sr)
    for period in (147, 160.0, 220.0):
        a = _fold(x, int(period))
        b = _fold_at(x, float(period))
        assert a.shape == b.shape, (period, a.shape, b.shape)
        assert np.max(np.abs(a - b)) < 1e-9, (period, np.max(np.abs(a - b)))
    # ...and the end-to-end consequence: 300 Hz has no fold misalignment at
    # either rate, so it was never one of #767's f0 values, on EITHER fold.
    for taps in (-1, FOLD_SINC_TAPS):
        va, names = extra_features(x, sr, taps)
        vb, _ = extra_features(_resample(x, sr, 48000), 48000, taps)
        d = dict(zip(names, va))
        e = dict(zip(names, vb))
        nm = "jit.cycledshape.seg0"
        assert abs(d[nm] - e[nm]) < RATE_TOL_ABS["ratio"], (taps, d[nm], e[nm])


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
    exactly; give the same oscillator per-period jitter and the
    cycle-to-cycle correlation must fall, by more the more jitter it has.

    THE MARGIN GOT SMALLER WHEN #767 LANDED, AND THAT IS THE REPAIR WORKING,
    NOT A REGRESSION -- it is worth the paragraph because the number moved in
    the direction that usually means something broke. This fold used to cut
    rows at an INTEGER lag, so on a 410 Hz clip (107.56 samples per period)
    every row started a fixed fraction of a sample later than the last
    whether the oscillator was steady or not, and that walk was counted in
    with the jitter. cyclecorr fell 0.0207 from stable to 0.5 %-jittered.
    Folding at the exact period removes the walk and the fall is 0.0146:
    the oscillator's own share of what used to be reported, with the sample
    grid's share taken out.

    AND THE THRESHOLD IS NO LONGER A BARE 0.02, which is what made this test
    red when the walk was removed and which nothing anchored in the first
    place. A feature has to separate the mechanism it exists to see by more
    than it separates two sample rates of the same clip, so the margin is
    read against THAT: the jittered clip's own 44.1k->48k gap in the same
    column, measured here rather than quoted. It is 0.0002, and the margin
    clears it by better than sixty times.

    What this test cannot do anything about, recorded because it is the
    uncomfortable half of the same measurement: 0.0146 is also eight times
    SMALLER than `RATE_TOL_REL` x 1.0 = 0.12, the tolerance the rate test
    applies to this column. The rate test would not notice a rate dependence
    in `jit.cyclecorr` the size of everything the column measures. That is a
    tolerance problem, not a fold problem; filed as #775, not fixed here."""
    sr = 44100
    read = lambda x, s=sr: dict(zip(*reversed(jitter_features(x, s))))  # noqa: E731
    graded = [read(_square(410.0, sr, jitter=j, seed=3))["jit.cyclecorr.seg0"]
              for j in (0.0, 0.001, 0.005, 0.02)]
    assert graded[0] > 0.99, graded
    assert all(b < a for a, b in zip(graded, graded[1:])), (
        f"cyclecorr did not fall monotonically with jitter: {graded}")

    stable = _square(410.0, sr, jitter=0.0)
    drift = _square(410.0, sr, jitter=0.005, seed=3)
    d, j = read(stable), read(drift)
    assert d["jit.valid.seg0"] == 1.0 and j["jit.valid.seg0"] == 1.0
    # the anchor: the same column, the same clip, the two rates
    j48 = read(_resample(drift, 44100, 48000), 48000)
    rate_gap = abs(j["jit.cyclecorr.seg0"] - j48["jit.cyclecorr.seg0"])
    assert rate_gap < 1e-3, rate_gap
    margin = d["jit.cyclecorr.seg0"] - j["jit.cyclecorr.seg0"]
    assert margin > 20.0 * max(rate_gap, 1e-6), (margin, rate_gap)
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


def test_the_one_residual_the_fold_repair_cannot_reach_is_the_stimulus():
    """WHAT #767 DID NOT FIX, MEASURED RATHER THAN ASSERTED.

    Over 44 values of f0 in both resampling directions
    (tools/probes/fold_rate_sweep.py), the repaired fold leaves exactly one
    stimulus over tolerance that is not the octave error above: 200 Hz built
    at 48 kHz, in `mpd2.50ms.rowcorr`, at a gap of 0.131 against 0.120.

    It is not fold geometry and the control says so. 2.5 ms is 120 samples at
    48 kHz and the 200 Hz period is 240, so consecutive rows are exact
    negatives of each other and `rowcorr` saturates at -1.0000 -- where
    RATE_TOL_REL x 1.0 is its whole scale and any difference at all shows up
    full size. The difference is in the CLIP, not the fold: `_square` lays
    down hard +-1 edges on the build rate's sample grid, and a half period of
    110.25 samples cannot exist at 44.1 kHz, so the resampled side is a
    band-limited square and the built side is an ideal one.

    `_bl_square` is the falsifier: it is band-limited identically at both
    rates, and on it the same column at the same f0 in the same direction
    comes back inside tolerance. If that ever stopped being true the
    diagnosis here would be wrong and the residual WOULD be the fold."""
    f0, build_sr, nm = 200.0, 48000, "mpd2.50ms.rowcorr.seg0"

    def gap(kind):
        a = _rate_stimulus(f0, 0.0, 0, sr=build_sr, kind=kind)
        b = _resample(a, build_sr, RATE_PAIR[build_sr])
        va = dict(zip(*reversed(mpd_features(a, build_sr))))
        vb = dict(zip(*reversed(mpd_features(b, RATE_PAIR[build_sr]))))
        tol = max(RATE_TOL_REL * max(abs(va[nm]), abs(vb[nm])),
                  RATE_TOL_ABS[_unit_of(nm)])
        return abs(va[nm] - vb[nm]), tol, va[nm], vb[nm]

    hard, tol, a, b = gap("square")
    assert hard > tol, (
        f"the 200 Hz residual no longer reproduces ({hard:.4f} against "
        f"{tol:.4f}); re-run tools/probes/fold_rate_sweep.py and rewrite this")
    assert round(a, 4) == -1.0, a      # the saturated side, which is the point
    limited, tol2, _, _ = gap("blsquare")
    assert limited < tol2, (
        f"a stimulus band-limited the SAME WAY at both rates is also over "
        f"tolerance ({limited:.4f} against {tol2:.4f}) -- then the residual "
        "is the fold after all and this test's diagnosis is wrong")
    assert limited < 0.1 * hard, (limited, hard)


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
        # the EXACT stride, because that is what `mpd_features` folds at
        # since #767; `round(sr*T)` would be testing code that no longer
        # ships. The non-commensurability is asserted on the same quantity.
        p = sr * T
        assert abs(366.4 / p - round(366.4 / p)) > 0.02, (T, p)
        if _fold_stats(_fold_at(x, p))["dshape"] > 0.05:
            walked += 1
    assert walked == len(MPD_PERIODS_S), (
        "a stable tone did NOT walk between rows at every stride, so the fold "
        "might be cycle-aligned after all")
