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
    lowband_onset_db        the SAME ratio over a stated window from the onset
                            instead of the whole clip. The time-resolved
                            reading docs/discrimination.md 5c twice says is
                            "needed and is not built".
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
* `lowband_onset_db` IS THAT TIME-RESOLVED READING, AND IT CANNOT BE A 30 ms
  ONE. 30 ms is 1.2 cycles of the 40 Hz lower edge and the Hann main lobe is
  four bins wide, so a 40 Hz edge is not resolvable there at all --
  `tools/probes/lowband_onset_window.py` measures the two failures this causes:
  -1.32 dB of error on a closed-form ratio at a 10 ms window, and a 135 Hz band
  reading 22 dB high at 20 ms because a 90 Hz line outside it leaks in. So the
  3-cycles-of-the-lower-edge precondition applies to the WINDOW, the shortest
  window a 40 Hz edge admits is 75 ms, and the default is 80 ms (3.2 cycles).
  **A shorter window is REFUSED, not answered.** What the 80 ms window buys,
  measured rather than asserted (`test_the_onset_window_gives_the_first_30_ms_
  its_own_weight`): the first 30 ms goes from 0.15 % of the analysis window's
  power budget to 19.8 %, a factor of 132, and from a mean amplitude weight of
  0.050 to 0.350, a factor of 7.0.
  WRONG-THEN-RIGHT: the first draft of this paragraph said "roughly twenty
  times", a number taken from neither of those two and from nothing else.
  Caught by computing both before committing, which is the only reason it is
  not in the file.
  It is still NOT a 30 ms reading and must not be quoted as one. To read 30 ms
  the caller must raise the band's lower edge to >= 100 Hz, and then it is a
  different band.
* It also SATURATES where the band holds nearly all the energy on both sides:
  BD, LT and MT read ~0 dB on the machine at every setting (knob travel 0.25,
  0.008 and 0.024 dB), so ours-minus-real of 0.0 there means the metric is
  blind, not that we agree (docs/promoted-bands-results.json).
* `dominant_period_ms` is validated on STEADY exponentially decaying sines
  (worst error 0.068 %, `measure_promoted_bands.period_error_on_known_cases`).
* ON A GLIDE IT READS THE SETTLED FREQUENCY, NOT THE NOTE'S MEAN, and that is
  now MEASURED rather than unknown (`measure_promoted_bands.glide_cases` /
  `glide_error_vs_settled_pct`; probe above). Against a downward glide of
  depth 0-40 % with a 30 ms glide constant it tracks f(end) to within
  +0.02..+2.08 %, while its error against the ENERGY-WEIGHTED MEAN
  instantaneous frequency reaches -11.3 %. Two consequences:
    - a reading of a tom is that tom's SETTLED pitch. Do not call it the pitch
      of the note.
    - the small bias toward the start of the glide DOES NOT CANCEL between two
      arms with different glide depths. 20 points of glide-depth difference is
      worth ~1.1 % of apparent pitch difference with no pitch difference at
      all -- the same order as the 0.44-2.01 % ours-minus-machine figures in
      docs/discrimination.md 5c. A pitch difference between arms is not
      evidence until their glide depths are known to match.

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

#: The onset window, in ms. DERIVED, not chosen: three cycles of the 40 Hz
#: default lower edge is 75.0 ms, which is the shortest window that band admits
#: at all (see the header), and 80 ms is the nearest round number above it --
#: 3.2 cycles, so the default does not sit exactly on its own precondition.
#: `tools/probes/lowband_onset_window.py` is where the boundary was measured.
ONSET_WINDOW_MS = 80.0

#: Largest edge-adjacent energy, as a fraction of the band's own energy, at
#: which a band share is still a reading of the BAND rather than of the WINDOW.
#:
#: WHY THERE IS A SECOND PRECONDITION AT ALL. The 3-cycle rule above bounds the
#: window against the band's LOWER EDGE; it does not bound it against where the
#: signal's lines actually are, and that is the failure that matters. A Hann
#: window of length T has a main lobe +-2/T wide, so a line within 2/T of a band
#: edge has part of its own energy on the far side of that edge and the share is
#: a reading of the straddle. FOUND, not foreseen: the #111 synthetic bass drum
#: (one damped 50 Hz sinusoid) read -0.798 dB in an 80 ms window where the
#: whole-clip reading is -0.0007, and all 16.8 % of the "out of band" energy was
#: BELOW 40 Hz -- the 50 Hz line's own lower skirt, 10 Hz from a band edge with
#: a 25 Hz half-width. Not an attack transient: the fixture's click contributes
#: nothing to it (click=0 reads -0.798 too).
#:
#: 0.05 is measured, not picked. Edge-adjacent energy over in-band energy is
#: 6e-6 for lines well inside the band, 2e-3 for a 60 Hz line (0.8 half-widths
#: above a 40 Hz edge at 80 ms), and 0.20-0.74 once a line is inside the lobe.
#: 0.05 is 25x the worst passing case and a quarter of the worst failing one.
EDGE_LEAK_MAX = 0.05


def _band_share_db(seg, sr: int, lo: float, hi: float, floor_db: float, *,
                   edge_guard: bool, **detail) -> Estimate:
    """10*log10(power in [lo, hi) / total power) of ONE Hann-windowed segment.

    Shared by both low-band estimators so the quantity cannot drift apart
    between the whole-clip and the windowed reading; a difference of the two
    then isolates WHEN the energy is, which is the whole point of the second
    one. `np.hanning` is looked up on this module's `np` on purpose -- that is
    what the rectangular-window control replaces.

    `edge_leak` is ALWAYS computed and always reported in `detail`, so the
    straddle `EDGE_LEAK_MAX` describes is visible in every record either way.
    `edge_guard` decides whether exceeding it REFUSES; see each caller for why
    they differ, which is a statement about what can be re-measured on this
    host and not about what is correct."""
    if am.is_silent(seg):
        return _fail("silent", **detail)
    w = np.hanning(len(seg))
    P = np.abs(np.fft.rfft(seg * w)) ** 2
    f = np.fft.rfftfreq(len(seg), 1.0 / sr)
    tot = float(P.sum())
    if tot <= 0.0:
        return _fail("zero total power", **detail)
    in_band = float(P[(f >= lo) & (f < hi)].sum())
    # Hann main-lobe half-width. A line nearer than this to an edge has its own
    # skirt on the far side of that edge.
    half = 2.0 * sr / len(seg)
    adjacent = float(P[(f >= max(0.0, lo - half)) & (f < lo)].sum()
                     + P[(f >= hi) & (f < hi + half)].sum())
    leak = (adjacent / in_band) if in_band > 0.0 else math.inf
    detail = dict(detail, edge_leak=leak, main_lobe_half_hz=half)
    if edge_guard and not leak <= EDGE_LEAK_MAX:
        return _fail(f"energy within one main lobe ({half:.1f} Hz) of a band "
                     f"edge is {leak:.3f} of the band's own, over the "
                     f"{EDGE_LEAK_MAX:g} this window can resolve: the share "
                     f"would be a reading of the straddle, not of the band",
                     band=(lo, hi), **detail)
    frac = in_band / tot
    v = 10.0 * math.log10(frac) if frac > 0.0 else -math.inf
    if not v > floor_db:
        return _fail("band is below the estimator's own floor", level_db=v,
                     floor_db=floor_db, **detail)
    return Estimate(v, True, "", dict(band=(lo, hi), frac=frac, **detail))


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
    # edge_guard=False, and this is a KNOWN HOLE rather than a judgement that
    # the guard does not apply here -- it applies to any band share. Turning it
    # on would turn some already-reported Fischer readings into refusals, and
    # `docs/promoted-bands-results.json` can only be re-measured on a host with
    # the reference packs. `detail["edge_leak"]` is populated regardless, so the
    # straddle is visible in every record taken meanwhile.
    # WHAT THE GUARD PREDICTS, stated so it can be checked rather than
    # discovered later: docs/discrimination.md 5c reports ours-minus-real as
    # "large only where the tuning crosses that edge (HT 7.5 +4.8 dB, LC 7.5
    # +3.5 dB)". A fundamental crossing the 200 Hz edge is exactly this
    # artefact, so the guard would most likely REFUSE those two readings. That
    # is a prediction, not a result: it needs the corpus run.
    return _band_share_db(x, sr, lo, hi, floor_db, edge_guard=False)


def lowband_onset_db(x, sr: int, band=DEFAULT_LOWBAND_HZ,
                     window_ms: float = ONSET_WINDOW_MS, *,
                     start_ms: float = 0.0,
                     floor_db: float = LOWBAND_FLOOR_DB) -> Estimate:
    """`lowband_level_db`'s ratio over `window_ms` from `start_ms`, not the clip.

    The conditioned clip is onset-aligned (`test_discrimination.condition`), so
    `start_ms=0` is the onset. THE WINDOW IS THE MEASUREMENT: a reading at one
    window is not comparable with a reading at another, and `detail` carries
    both numbers so a record cannot lose them.

    Four preconditions, each a REFUSAL rather than a number, because every one
    of them is a way to get an answer that looks like data:

    * the band must be inside (0, Nyquist);
    * **the window must hold `MIN_CYCLES_OF_LOWER_EDGE` cycles of the band's
      lower edge.** This is the one that matters: it refuses the 30 ms x 40 Hz
      reading the trajectory report's own resolution would suggest, which is
      arithmetically impossible rather than merely noisy (header, and
      `tools/probes/lowband_onset_window.py` measures both failure modes);
    * **the window must fit inside the clip.** Truncating it silently would
      answer for a shorter window than the caller named, and the caller would
      compare it with a full-length one;
    * **no line may sit within one Hann main lobe of a band edge.** The one
      that was not foreseen and was found by a test: the #111 synthetic bass
      drum's 50 Hz line is 10 Hz above the 40 Hz edge, and an 80 ms window's
      half-width is 25 Hz, so 16.8 % of its energy lands below the edge and the
      reading was -0.798 dB where the whole-clip one is -0.0007. See
      `EDGE_LEAK_MAX`. This is the precondition the 3-cycle rule does NOT
      imply, and it is why a short window needs two of them;
    * the window must not be silent, and its band must stand above the
      estimator's own leakage floor.

    So THIS ESTIMATOR REFUSES THE BASS DRUM at the default band and window, and
    that is the correct answer rather than a limitation to work around: a 50 Hz
    fundamental cannot be separated from a 40 Hz band edge in 80 ms by any
    window. A voice whose lines sit clear of both edges -- SD, where the
    -4.86..+6.79 dB finding is -- is readable.
    """
    x = am._as_float(x)
    lo, hi = float(band[0]), float(band[1])
    if not (0.0 < lo < hi < sr / 2.0):
        return _fail("band is not inside (0, Nyquist)", band=(lo, hi), sr=sr)
    need_ms = 1000.0 * MIN_CYCLES_OF_LOWER_EDGE / lo
    if not float(window_ms) >= need_ms:
        return _fail(f"a {float(window_ms):g} ms window cannot resolve a "
                     f"{lo:g} Hz lower edge; it holds "
                     f"{float(window_ms) * lo / 1000.0:.2f} of the "
                     f"{MIN_CYCLES_OF_LOWER_EDGE:g} cycles required",
                     window_ms=float(window_ms), need_window_ms=need_ms,
                     band=(lo, hi))
    a = int(round(float(start_ms) * sr / 1000.0))
    n = int(round(float(window_ms) * sr / 1000.0))
    if a < 0 or n <= 0:
        return _fail("window start or length is not positive",
                     start_ms=float(start_ms), window_ms=float(window_ms))
    if a + n > len(x):
        return _fail("window does not fit inside the clip; a truncated window "
                     "is a reading of a different window",
                     start_ms=float(start_ms), window_ms=float(window_ms),
                     clip_ms=1000.0 * len(x) / sr)
    return _band_share_db(x[a:a + n], sr, lo, hi, floor_db, edge_guard=True,
                          window_ms=float(window_ms), start_ms=float(start_ms))


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
