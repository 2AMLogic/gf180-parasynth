#!/usr/bin/env python3
"""Per-voice estimators promoted out of the discrimination study (#138).

`docs/discrimination.md` 5c ranked two hand-feature candidates that a learned
view pointed at: the low-band excess (`cqt.0-200Hz`) and the dominant
partial's period (`jit.period_ms`). The study harness
(`model/discrimination_features.py`) exists to FIND candidates; it computes
them as columns of a 190-column vector and is not a measurement you can hang a
tolerance on. These two estimators are DERIVED FROM those candidates; they are
NOT the same quantities, and a reading of one is not a reading of the other:

    lowband_level_db        10*log10( power in [lo, hi) / total power ), one
                            Hann-windowed spectrum of the whole clip. A RATIO,
                            so a pure gain cancels exactly.
                            The study's `cqt.0-200Hz` is a GROUP: 6-band-per-
                            octave constant-Q sub-bands up to 200 Hz, each over
                            `CQT_SEGS=2` time segments.
    dominant_period_ms      1000 / frequency of the strongest line in a
                            per-voice search band, parabolically interpolated.
                            The study's `jit.period_ms` is `dominant_period`
                            over a fixed 120-1200 Hz band on segment 0 only.

READ THIS BEFORE QUOTING EITHER NUMBER.

* `lowband_level_db` CANNOT SEE THE ONSET. One Hann window spans the whole
  240 ms clip, so the first 30 ms gets a mean amplitude weight of 0.05 and
  carries 0.15 % of the window's power budget (12.5 % if it were flat;
  pinned by `test_the_whole_clip_hann_all_but_ignores_the_first_30_ms`). The
  excitation-pulse excess `cqt.0-200Hz` was promoted for lives in exactly that
  region (docs/discrimination-trajectory.txt). This estimator is a whole-clip
  spectral balance, not a measure of that excess.
* It also SATURATES where the band holds nearly all the energy on both sides:
  BD, LT and MT read ~0 dB on the machine at every setting (knob travel 0.25,
  0.008 and 0.024 dB), so ours-minus-real of 0.0 there means the metric is
  blind, not that we agree (docs/promoted-bands-results.json).
* `dominant_period_ms` is validated on STEADY exponentially decaying sines
  (worst error 0.068 %, `measure_promoted_bands.period_error_on_known_cases`).
  An 808 tom glides downward over its first tens of ms; no gliding case has
  been measured, so its error on a tom is not known.

* They are measured on the CONDITIONED clip (`test_discrimination.condition`:
  onset-aligned, 240 ms, DC removed, 20 Hz high-passed). On a raw record the
  low-band number is dominated by DC and the converter's lead-in.
* A difference of two readings is in dB or ms of THIS estimator. It is neither
  the voice-frozen `voice_scale` ruler nor the per-comparison `ours_distance`
  ruler of docs/discrimination.md 3.1; those are knob-equivalents, these are
  not, and the two must not be compared.
* The period estimator reads the strongest line in a band the CALLER names.
  The study's 120-1200 Hz band excludes the toms' 80-100 Hz fundamentals, so a
  per-voice band is required and there is no default.

Both estimators REFUSE (an `audio_measure.Estimate` with ok=False) rather than
answer when their precondition fails; `REFUSED` is a result, not a pass.
"""
from __future__ import annotations

import math
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import audio_measure as am
from audio_measure import Estimate, _fail

#: Below this the band fraction is reading the estimator's own leakage and the
#: converter's noise floor, not the voice. A clip whose band sits lower than
#: this is REFUSED, not clamped: a clamp turns "very quiet" into one number
#: that every quiet voice agrees on, and two clamped readings then compare
#: equal. Measured against the leakage of a pure out-of-band tone in
#: `test_the_floor_is_below_what_a_real_band_reads_and_above_leakage`.
LOWBAND_FLOOR_DB = -70.0
#: Three cycles of the band's lower edge. Fewer and the band holds no line the
#: window can resolve (Hann main lobe is four bins wide).
MIN_CYCLES_OF_LOWER_EDGE = 3.0
DEFAULT_LOWBAND_HZ = (40.0, 200.0)   # the study's `cqt.0-200Hz`: CQT_FMIN..200


def lowband_level_db(x, sr: int, band=DEFAULT_LOWBAND_HZ, *,
                     floor_db: float = LOWBAND_FLOOR_DB) -> Estimate:
    """Level of `band` relative to the clip's total power, in dB (<= 0)."""
    x = am._as_float(x)
    lo, hi = float(band[0]), float(band[1])
    if not (0.0 < lo < hi < sr / 2.0):
        return _fail("band is not inside (0, Nyquist)", band=(lo, hi), sr=sr)
    need = int(math.ceil(MIN_CYCLES_OF_LOWER_EDGE * sr / lo))
    if len(x) < need:
        return _fail("clip shorter than three cycles of the band's lower edge",
                     n=len(x), need=need)
    if am.is_silent(x):
        return _fail("silent")
    w = np.hanning(len(x))
    P = np.abs(np.fft.rfft(x * w)) ** 2
    f = np.fft.rfftfreq(len(x), 1.0 / sr)
    tot = float(P.sum())
    if tot <= 0.0:
        return _fail("zero total power")
    frac = float(P[(f >= lo) & (f < hi)].sum()) / tot
    v = 10.0 * math.log10(frac) if frac > 0.0 else -math.inf
    if not v > floor_db:
        return _fail("band is below the estimator's own floor", level_db=v,
                     floor_db=floor_db)
    return Estimate(v, True, "", dict(band=(lo, hi), frac=frac))


def dominant_period_ms(x, sr: int, band, *, min_prominence_db: float = 12.0) -> Estimate:
    """Period, in ms, of the strongest line inside `band` (Hz).

    `band` has no default on purpose. The line must stand `min_prominence_db`
    over its local median -- 12 dB, twice `dominant_frequency`'s own 6 -- so a
    noise voice (hats, cymbal) whose 'strongest bin' is an accident REFUSES
    instead of reporting a period."""
    x = am._as_float(x)
    lo, hi = float(band[0]), float(band[1])
    if not (0.0 < lo < hi < sr / 2.0):
        return _fail("band is not inside (0, Nyquist)", band=(lo, hi), sr=sr)
    if len(x) < int(math.ceil(MIN_CYCLES_OF_LOWER_EDGE * sr / lo)):
        return _fail("clip shorter than three cycles of the band's lower edge",
                     n=len(x))
    est = am.dominant_frequency(x, lo, hi, sr, min_prominence_db=min_prominence_db)
    if not est.ok:
        return est
    return Estimate(1000.0 / est.value, True, "",
                    dict(freq_hz=float(est.value), **est.detail))
