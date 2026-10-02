#!/usr/bin/env python3
"""Does an output coupling correct the drum block, and what does it cost?

    python3 tools/probes/dc_blocker.py --limits      the declared allowances
    python3 tools/probes/dc_blocker.py --decay       the decay gate's precondition
    python3 tools/probes/dc_blocker.py --resolution  what the instrument can see
    python3 tools/probes/dc_blocker.py --screen      what a blocker CAN do, per voice
    python3 tools/probes/dc_blocker.py --clipping    s2's placement, where it is testable
    python3 tools/probes/dc_blocker.py --placement   four placements, measured
    python3 tools/probes/dc_blocker.py --measure     improvement AND preservation
    python3 tools/probes/dc_blocker.py --cutoff      the corner sweep
    python3 tools/probes/dc_blocker.py --continuous  repeated hits, chokes, retunes
    python3 tools/probes/dc_blocker.py --records     regenerate docs/dcblock/ entirely
    python3 -m pytest tools/probes/dc_blocker.py tools/probes/test_dc_blocker_apparatus.py -q

THE ANSWER, SO IT IS NOT BURIED IN NINE RECORDS
-----------------------------------------------
**ONE BLOCKER DOES NOT SUIT BOTH SUBJECTS, AND NOT BECAUSE IT HARMS THE
RIMSHOT: BECAUSE THE RIMSHOT HAS NO DC DEFECT TO REMOVE.**

  * THE CYMBAL is a standing offset. phi = 0.888 of its sub-20 Hz energy is in
    the f = 0 bin, which a zero at z = 1 nulls exactly. At the circuit's own
    corner (K = 10, 7.46 Hz) the blocker removes 14.85 dB of it and every
    declared preservation gate passes, decay included (-0.36 %). ACCEPTED.
  * THE RIMSHOT is its own onset skirt. phi = 0.043, so 95.7 % of its sub-20 Hz
    energy is the Fourier content of a 10 ms burst -- the pulse, not a fault --
    and a DC-nulling filter attenuates it only by |H(f)|. It reaches 2.70 dB
    against the declared 6.0, and `--screen` shows that no corner the hardware
    can build reaches 6 dB without moving fc into the band whose preservation
    is the constraint. NOT MET, and the requirement was the wrong one for this
    voice rather than the blocker being the wrong filter.

So #152's "one DC block fixes five voices" is refuted for at least one of the
five, and `--screen` says which of the others are in which class before anything
is rendered: BD 0.098, HT 0.023, CH 0.352 are all skirt, not offset.

READ `--decay`, `--resolution` AND `--screen` BEFORE ANY VERDICT TABLE
---------------------------------------------------------------------
`--decay` is the decay gate's precondition: a decay does not depend on how long
you watched. It rejected two defects that the verdict table could not show --
the CY's T20 was biased 23 % short by Schroeder truncation at the 0.60 s clip
every earlier record used, and the RS's banded T20 tracks the clip's length to
three figures (598 / 1198 / 2398 ms), so that gate is REFUSED on the RS rather
than answered. `RENDER_S` is 2.40 s because that is where the CY stops moving.

`--resolution` is what this instrument can see. Three of the preservation
allowances declared below were BELOW it -- the HT's 5-20 kHz band is at
-102 dBFS and one LSB of dither moves it 8.69 dB against a 0.20 dB allowance --
so they were failing CONTROL voices on dither, and `--measure` reported a
candidate as breaking preservation on three voices it had not touched. Every
allowance is now read against the larger of itself and that resolution, and any
gate so widened is marked `[res-limited]` wherever it appears.
`tools/probes/test_dc_blocker_apparatus.py` is the eight-control suite for this,
committed red.

`--screen` is what a blocker CAN do to a voice, derived from the voice's
uncoupled baseline with nothing rendered. It is the answer to "does one blocker
suit both subjects" that a pass/fail cannot give: the CY's sub-20 Hz energy is
88.8 % a standing offset, the RS's is 95.7 % its own onset skirt, and a zero at
z = 1 removes the first and not the second.

#152 established a DIAGNOSIS: the drum block has no DC blocking anywhere,
`SRC_PULSE` never changes sign (mean/|mean| = 1.000 exactly), the modes it
drives are all-pole with DC gains of 18 to 23,899, and the AC coupling the
circuit puts after every voice is unmodelled. #165 is the bounded prototype,
and "one DC block fixes five voices" is what it has to TEST. A positive
excitation and a large DC gain do not establish a cutoff, a placement or a
transient behaviour, and a blocker trades settling speed against bass.

WHAT THIS FILE MEASURES, AND HOW IT AVOIDS MEASURING ITSELF
-----------------------------------------------------------
Everything here is read off the block's own int16 output -- `output_fx`, the
one hard rail of contract 12 -- with NO conditioning, NO high-pass and NO
normalisation before the measurement. `tools/probes/excitation_energy.py`
conditions because it compares against a machine recording; this file compares
the block against itself, so conditioning would only add an instrument whose
own DC handling is the thing under test.

Every band figure is ABSOLUTE: dB relative to digital full scale, on a
rectangular-window FFT where Parseval is exact. Normalised band SHARES are
printed too, in their own columns, marked, and never used for a verdict --

    **removing low-frequency energy raises a normalised high-band percentage
    without adding one sample of high-frequency content.**

That is the trap #165 names, and `share_rise_is_lf_removal` is the rule that
catches it: a share that rises while the band's ABSOLUTE energy is flat is
LF removal, not synthesis.

UNCERTAINTY. The model is integer and deterministic: two renders of the same
configuration are bit-identical, so the model-side measurement uncertainty is
exactly zero and every difference below is a real difference, not noise.
`test_the_measurement_has_no_uncertainty_to_hide_behind` pins that.
"""
from __future__ import annotations

import argparse
import pathlib
import subprocess
import sys

import numpy as np

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "model"))

import drums_fx as dx                            # noqa: E402

SR = dx.SR
FS = 32768.0                                     # int16 full scale

# ===========================================================================
# THE LIMITS. Declared here, in the commit that adds this file, BEFORE the
# first measurement was taken -- git history is the evidence of the ordering.
# ===========================================================================
#
# A real high-pass changes low-frequency amplitude AND phase. "Unchanged" is
# not achievable and is not the criterion; "within a declared allowance" is,
# and the allowance is part of the deliverable (#165 s3).
#
# Where the numbers come from. The candidate corner is 7.457 Hz (`dx.COUPLE_K`
# = 10), read off the BD's own coupling network C49 / R176 || R177 (7.52 Hz,
# reference 2). A one-pole high-pass at fc attenuates a tone at f by
# f / sqrt(f^2 + fc^2):
#
#     BD   f0 ~ 50 Hz  -> -0.096 dB, +8.5 deg
#     HT   f0 ~ 190 Hz -> -0.007 dB, +2.2 deg
#     CH   band-pass at 11.7 kHz -> below any number this file can print
#
# so the allowances below are set at roughly 5x the predicted change for the
# control voices, which is tight enough that a placement or cutoff error
# cannot hide inside them, and loose enough that the honest phase change does
# not read as a regression.
IMPROVE_SUB20_DB = 6.0        # REQUIRED of CY and RS: sub-20 Hz absolute energy
                              # must fall by at least this much. 6 dB = a factor
                              # of 4 in energy; below that the correction is not
                              # worth a register, a state word and an adder.

LIMITS = {
    # property           allowance            what it protects
    "peak_dbfs":        {"BD": 0.5, "*": 0.3},   # headroom
    "body_20_700_db":   {"BD": 0.5, "*": 0.3},   # body, absolute
    "mid_700_5k_db":    {"*": 0.2},              # the middle, absolute
    "hf_5k_20k_db":     {"*": 0.2},              # HF, absolute: a DC blocker must
                                                 # not touch it AT ALL, and this is
                                                 # the anti-trap anchor
    "t20_ms_pct":       {"*": 3.0},              # decay, relative percent
    "attack_samp":      {"*": 2.0},              # onset -> peak, samples
    "centroid_pct":     {"*": 1.0},              # spectral centroid, relative percent
}
CONTROLS = ("BD", "HT", "CH")     # the diagnosis says these need nothing
SUBJECTS = ("CY", "RS")           # sustained rectified energy, and a short onset

# The bands. 20 Hz is the edge of the audible band and of the discrimination
# conditioning (`test_discrimination.HPF_HZ`); 700 Hz and 5 kHz are the splits
# of docs/discrimination.md 5a, so the shares below are comparable with the
# ones the tom question is argued in.
SUB20 = (0.0, 20.0)
BANDS = {"body_20_700_db": (20.0, 700.0), "mid_700_5k_db": (700.0, 5000.0),
         "hf_5k_20k_db": (5000.0, 20000.0)}

RENDER_S = 2.40                   # WAS 0.60, AND 0.60 WAS NOT LONG ENOUGH.
                                  # A Schroeder integral normalises by the
                                  # energy inside the clip, so a voice still
                                  # ringing at the end reads SHORT: the CY's own
                                  # T20 reads 428 / 540 / 558 / 558 ms at 0.60 /
                                  # 1.20 / 2.40 / 4.80 s, so every record taken
                                  # at 0.60 s was biased 23 % short. 2.40 s is
                                  # where it stops moving, which is a measured
                                  # choice (`--decay`), not a generous one.
RENDER_GAIN = 0.45                # the reference drum-bus gain (DR 0005), as
                                  # `test_discrimination.RENDER_GAIN`
LEAD_FRAMES = 10                  # the hit lands at frame 10, as every other probe


def provenance() -> str:
    def git(*a):
        try:
            return subprocess.run(["git", *a], cwd=ROOT, capture_output=True,
                                  text=True, check=True).stdout.strip()
        except Exception:
            return "?"
    dirty = "-dirty" if git("status", "--porcelain") else ""
    return (f"commit {git('rev-parse', '--short=12', 'HEAD')}{dirty}  "
            f"SR {SR}  render {RENDER_S:.2f}s  gain {RENDER_GAIN}  "
            f"K {dx.COUPLE_K} ({SR / (2 * np.pi * (1 << dx.COUPLE_K)):.3f} Hz)")


# ===========================================================================
# Rendering: one sound, one placement
# ===========================================================================
_RENDER_MEMO: dict = {}


def render(sound, couple=dx.COUPLE_OFF, k=dx.COUPLE_K, seconds=RENDER_S,
           hits=None, kit=None, extra=None, gain=None):
    """The block's int16 output for one sound, at one coupling placement.

    Returns (out_int16, n_clip). `n_clip` is how many samples the output
    stage's single clamp (contract 12) actually railed -- headroom, as a
    count rather than an adjective.

    COUPLE_POST is applied HERE, after the clamp, precisely because that is
    where it cannot be applied inside the block: it is the control that shows
    whether removing DC after clipping recovers the waveform the clipping
    destroyed. Same filter, same arithmetic (`dx.dc_block`), different side of
    the rail.

    MEMOISED for the default single-hit case only, and that is safe for exactly
    one reason: the model is integer and deterministic, so two renders of the
    same configuration are bit-identical --
    `test_the_measurement_has_no_uncertainty_to_hide_behind` is that claim as a
    test. The records need the same baseline a hundred times over and a 2.40 s
    render is not free."""
    gain = RENDER_GAIN if gain is None else gain
    memo = (sound, couple, int(k), round(float(seconds), 6), round(float(gain), 6)) \
        if hits is None and kit is None and extra is None else None
    if memo is not None and memo in _RENDER_MEMO:
        return _RENDER_MEMO[memo]
    n = int(seconds * SR)
    kit = kit if kit is not None else dx.kit_with_sounds(sound)
    hits = hits if hits is not None else [(LEAD_FRAMES, dx.SOUND_STOP[sound], 1.0)]
    inner = couple if couple in (dx.COUPLE_EXC, dx.COUPLE_BUS) else dx.COUPLE_OFF
    d = dx.DrumsFx(couple=inner, couple_k=k)
    w = dx.hit_writes(hits, kit)
    if extra:
        w = sorted(list(w) + list(extra), key=lambda t: t[0])
    dmix, body = d.play(w, n)
    g = dx.accent_reg(gain)
    acc = (np.asarray(dmix, np.int64) * g + np.asarray(body, np.int64) * g) >> 15
    n_clip = int(((acc > 32767) | (acc < -32768)).sum())
    out = dx.output_fx(np.zeros(n), 0, dmix, g, body, g)
    if couple == dx.COUPLE_POST:
        y = dx.dc_block(out, k)
        n_clip += int(((y > 32767) | (y < -32768)).sum())
        out = np.clip(y, -32768, 32767).astype(np.int16)
    if memo is not None:
        _RENDER_MEMO[memo] = (out, n_clip)
    return out, n_clip


# ===========================================================================
# The estimators. Each is validated against synthetic ground truth below;
# none is calibrated on the model it measures (docs/failure-modes.md).
# ===========================================================================
def onesided_power(x):
    """`(power, freqs)`: the one-sided power spectrum of `x`, with every bin
    except DC and Nyquist DOUBLED so Parseval is exact and the bands sum to the
    total. Rectangular window, nothing normalised.

    **ONE FUNCTION, SO ONE CONVENTION.** `dc_fraction` originally computed its
    own spectrum without the doubling, which made every floor it derived 3 dB
    optimistic -- the DC bin is not doubled and the skirt is, so an undoubled
    skirt understates itself by exactly a factor of two. The symptom was the
    floor failing to bound its own closed-form case by 2.96 dB, which looked
    like a broken derivation and was a mismatched convention between two
    functions that had to agree. They now cannot disagree."""
    x = np.asarray(x, float)
    p = np.abs(np.fft.rfft(x)) ** 2
    p[1:(-1 if len(x) % 2 == 0 else None)] *= 2.0
    return p, np.fft.rfftfreq(len(x), 1.0 / SR)


def band_energy_dbfs(x, lo, hi):
    """Absolute energy in [lo, hi) Hz, dB relative to digital full scale.

    Rectangular window, so Parseval is exact and the bands sum to the total
    with no window correction to get wrong. NOTHING is normalised: this number
    falls when energy is removed and does not rise when other energy is."""
    x = np.asarray(x, float) / FS
    p, f = onesided_power(x)
    p = p / len(x) ** 2
    return 10.0 * np.log10(float(p[(f >= lo) & (f < hi)].sum()) + 1e-30)


def band_share_pct(x, lo, hi):
    """**NORMALISED. Never a verdict.** The same band as a percentage of the
    clip's total energy -- the quantity the tom question is argued in, and the
    quantity that rises when low frequency is REMOVED."""
    x = np.asarray(x, float)
    X = np.fft.rfft(x)
    p = np.abs(X) ** 2
    p[1:(-1 if len(x) % 2 == 0 else None)] *= 2.0
    f = np.fft.rfftfreq(len(x), 1.0 / SR)
    return 100.0 * float(p[(f >= lo) & (f < hi)].sum()) / (float(p.sum()) + 1e-30)


def peak_dbfs(x):
    return 20.0 * np.log10(float(np.abs(np.asarray(x, float)).max()) / FS + 1e-30)


def onset_index(x, frac=0.02):
    x = np.abs(np.asarray(x, float))
    pk = float(x.max())
    return int(np.argmax(x > frac * pk)) if pk > 0 else 0


def peak_margin_db(x, order=8):
    """How far the largest sample leads the largest sample that is not in its
    own lobe, in dB. **The precondition for `attack_samples`**, measured rather
    than assumed.

    Below a margin of roughly a dB, "where is the peak" is a RANK ORDER between
    two lobes and not a timing: a change far too small to hear flips the
    answer. Measured on the current uncoupled renders --

        BD 0.000 dB   HT 0.535 dB   RS 0.190 dB   CY 1.654 dB   CH 0.336 dB

    -- the BD's four largest samples are EQUAL, so its `argmax` was choosing
    arbitrarily among them. `ATTACK_TOL_DB` is set above these margins for that
    reason, and `report_resolution` sweeps it so the choice is a curve and not
    a preference."""
    a = np.abs(np.asarray(x, float))
    pk = float(a.max())
    if pk <= 0:
        return float("nan")
    i = int(np.argmax(a))
    # Everything outside +-`order` samples of the winning sample: a different
    # lobe, not the same lobe's shoulder.
    other = np.delete(a, slice(max(0, i - order), i + order + 1))
    if not other.size or other.max() <= 0:
        return float("inf")
    return float(20.0 * np.log10(pk / other.max()))


# The attack tolerance. `attack_samples` reads the FIRST arrival within this
# many dB of the peak, so a flipped lobe rank INSIDE the band cannot move the
# answer. It is read off `peak_margin_db` above (the largest ambiguity on the
# five voices is the HT's 0.535 dB), not chosen for a result -- and
# `report_resolution --sweep` prints 0.5 / 1.0 / 2.0 dB side by side: at 0.5 dB
# the BD still jumps 460 samples, at 1.0 and 2.0 dB every voice is stable to
# <= 6 samples. The verdict does not depend on the value above 1 dB.
ATTACK_TOL_DB = 1.0
ATTACK_TOL_SWEEP = (0.5, 1.0, 2.0)   # the swept values; `resolution_of` takes
                                     # the baseline's spread across them as the
                                     # estimator's own resolution, and
                                     # `report_resolution` prints the sweep


def attack_samples(x, tol_db=None, frac=0.02):
    """Onset to the FIRST sample within `tol_db` of the clip's peak, in
    samples. The attack, as the only thing about it that a coupling capacitor
    can move.

    THIS IS NOT WHAT IT USED TO BE, and the change is a repair rather than a
    redefinition. It used to be `argmax(|x|) - onset`, which on an oscillatory
    voice answers "which lobe is biggest" and not "when did the attack
    finish". The BD's largest four samples are equal; removing its -45-count
    standing offset flipped the winner to a plateau half a period later and the
    gate reported a 460-sample (9.6 ms) attack change on a CONTROL voice whose
    onset had not moved by one sample. `test_the_attack_gate_does_not_answer_a_
    tie_between_equal_lobes` is that bug, kept.

    Reading the first arrival within a tolerance is monotone in the envelope
    and blind to the rank flip. Against ground truth it is biased slightly LATE
    for a slow carrier and slightly EARLY for a fast one (251 against 240
    samples at 400 Hz, 28 against 34 at 2 kHz) -- but it is used only for a
    CHANGE between two renders of the same voice, where a common bias cancels,
    and its 1-LSB resolution is 0-1 samples against the old estimator's 2."""
    tol_db = ATTACK_TOL_DB if tol_db is None else tol_db
    a = np.abs(np.asarray(x, float))
    pk = float(a.max())
    if pk <= 0:
        return float("nan")
    thr = pk * 10.0 ** (-tol_db / 20.0)
    return float(int(np.argmax(a >= thr)) - onset_index(a, frac))


T20_FRAME_MS = 2.0                # the quantisation step, named so the
                                  # resolution study can read it


def t20_ms(x, frame_ms=T20_FRAME_MS):
    """Time from the loudest frame to 20 dB below it, in ms, on a backward
    energy integral (Schroeder) so a decay that is not a clean exponential --
    the cymbal's is not -- still gets a defined number.

    Returns nan when the clip never falls 20 dB, which is a REFUSAL and not a
    zero: a number that cannot be measured must not be reported as one.

    **THIS IS THE DIAGNOSTIC, NOT THE GATE.** It integrates the whole spectrum,
    DC included, so a sub-20 Hz pedestal inflates it -- see `t20_band_ms`."""
    x = np.asarray(x, float)
    h = max(1, int(SR * frame_ms / 1e3))
    e = np.add.reduceat(x ** 2, np.arange(0, len(x) - len(x) % h, h))
    edc = np.cumsum(e[::-1])[::-1]
    if edc[0] <= 0:
        return float("nan")
    db = 10.0 * np.log10(edc / edc[0] + 1e-30)
    i0 = int(np.argmax(db <= -0.0))
    below = np.where(db <= -20.0)[0]
    return float("nan") if not len(below) else (below[0] - i0) * frame_ms


CENTROID_FLOOR_HZ = 20.0          # the qualified definition; see below


def band_limited(x, lo=CENTROID_FLOOR_HZ):
    """`x` with every FFT bin below `lo` zeroed. Zero-phase and STATELESS.

    A causal high-pass used as the analysis instrument would answer with a
    settling tail of its own, which is precisely the quantity under test. A
    brick wall in the same FFT the band energies are read from cannot."""
    x = np.asarray(x, float)
    X = np.fft.rfft(x)
    X[np.fft.rfftfreq(len(x), 1.0 / SR) < lo] = 0.0
    return np.fft.irfft(X, n=len(x))


def t20_band_ms(x, lo=CENTROID_FLOOR_HZ):
    """**THE DECAY GATE: T20 of the AUDIBLE band, >= `lo` Hz.**

    A global T20 cannot be a preservation gate for a DC blocker, for the same
    reason a global centroid cannot. `t20_ms` is a backward ENERGY integral, so
    a sub-20 Hz pedestal contributes to it at every instant -- and contributes
    MOST where the gate reads, late in the clip, where the backward integral is
    small and the pedestal's share of it is large. Removing the pedestal then
    moves the -20 dB crossing earlier for a reason that is not an audible decay
    change, and the gate reads the candidate's intended effect as a failure.

    THE GROUND TRUTH IS CLOSED FORM AND INDEPENDENT OF THIS MODEL:
    `tools/probes/dc_t20_gate_qualification.py` builds a tone whose T20 is
    200.0 ms by construction, adds a standing offset carrying 4 % of clip energy
    (the cymbal's own measured DC share), and measures the global estimator
    reading 452 ms against the >= 20 Hz estimator's 202 ms. Removing the
    pedestal and nothing else moves the global gate -55.75 % and the banded one
    +0.00 %.

    AND IT IS NOT A LOOSENED GATE. The same file feeds the repaired estimator a
    genuinely 10 % faster decay and it still reads -9.90 % against the 3 %
    allowance (`test_the_repaired_gate_still_sees_a_real_decay_regression`).
    A sub-20 Hz settling tail is not thereby ignored, either: it is exactly what
    `sub20_dbfs` measures, in its own column, as the improvement figure.

    **IT HAS A PRECONDITION OF ITS OWN AND IT IS NOT ALWAYS MET** -- see
    `decay_gate_refuses`. On a voice that reaches exact silence quickly the
    brick wall's sinc leaks onto the whole clip and this returns the clip's
    length, not a decay. That is REFUSED, not reported."""
    return t20_ms(band_limited(x, lo))


# ---------------------------------------------------------------------------
# THE DECAY GATE'S PRECONDITION: a decay does not depend on how long you watched
# ---------------------------------------------------------------------------
DECAY_INVARIANCE_PCT = 1.0        # how much doubling the clip may move the
                                  # estimate before the gate has no verdict
_DECAY_REFUSAL_CACHE: dict = {}


def decay_gate_refuses(sound, seconds=RENDER_S):
    """`(refuses, short_ms, long_ms)` for one voice's UNCOUPLED baseline.

    THE DOUBLING TEST. A decay is a property of the signal, so an estimate that
    moves when the observation window doubles is measuring the window. It is a
    NECESSARY condition only -- it cannot prove an estimate right -- and it was
    enough to reject two different defects on this instrument:

      * THE CYMBAL, by truncation. 428 ms at 0.60 s against 558 at 2.40 s. Fixed
        by the clip length, not by a refusal; `RENDER_S` is where it stops
        moving.
      * THE RIMSHOT, by leakage, and this one cannot be fixed by a longer clip:
        598 / 1198 / 2398 ms at 0.60 / 1.20 / 2.40 s is the clip's length to
        three figures. The rimshot reaches exact silence (to the LSB) about
        25 ms in, and the 20 Hz brick wall's kernel is a sinc spanning the clip,
        so the smeared onset ripple at -45 dB outweighs a tail that is not
        there. **The gate therefore has NO VERDICT on the rimshot's audible
        decay**, which is reported as REFUSED and is NOT a preservation
        failure. What bounds the rimshot instead is the absolute band energies
        (body/mid/HF all within 0.06 dB) and its peak.

    Qualified in closed form, both legs, by `dc_t20_gate_qualification.py`:
    a 200 ms decay in a 600 ms clip is invariant, a 500 ms decay in the same
    clip is not (464 against 500 ms).

    Measured on the baseline only and cached: it is a property of the voice and
    the estimator, not of the candidate, so the candidate cannot influence it --
    the same discipline `resolution_of` follows."""
    key = (sound, round(float(seconds), 6))
    if key in _DECAY_REFUSAL_CACHE:
        return _DECAY_REFUSAL_CACHE[key]
    a, _ = render(sound, dx.COUPLE_OFF, seconds=seconds)
    b, _ = render(sound, dx.COUPLE_OFF, seconds=2.0 * seconds)
    ta, tb = t20_band_ms(a), t20_band_ms(b)
    moved = float("inf") if (np.isnan(ta) or np.isnan(tb) or ta <= 0) \
        else abs(100.0 * (tb - ta) / ta)
    out = (moved > DECAY_INVARIANCE_PCT, ta, tb)
    _DECAY_REFUSAL_CACHE[key] = out
    return out


def centroid_global_hz(x):
    """**DIAGNOSTIC ONLY, never a verdict.** The spectral centroid over the
    whole spectrum, DC included -- the quantity the gate used to be read on, so
    the change is visible in the report rather than merely described."""
    x = np.asarray(x, float)
    p = np.abs(np.fft.rfft(x)) ** 2
    f = np.fft.rfftfreq(len(x), 1.0 / SR)
    return float((p * f).sum() / (p.sum() + 1e-30))


def centroid_hz(x, lo=CENTROID_FLOOR_HZ):
    """The spectral centroid of the AUDIBLE band, >= `lo` Hz. **The gate.**

    A global centroid cannot be a preservation gate for a DC blocker, because
    removing sub-20 Hz energy raises it MECHANICALLY: the denominator loses a
    term whose frequency is ~0 and every remaining term's weight rises. The
    gate would then read the candidate's intended effect as a failure, which is
    exactly what it did -- CY +8.96 % against a 1 % allowance, while the
    audible band moved -0.02 %.

    The ground truth is closed form, independent of this model, and already
    committed: `tools/probes/dc_centroid_gate_qualification.py` puts a 1 kHz
    tone next to a 10 Hz contaminant, removes the contaminant, and measures the
    global centroid moving 15.815 % while the >= 20 Hz centroid stays at
    exactly 1 kHz. That file's own conclusion -- "the existing candidate
    measurements must be rerun with the qualified preservation definition
    before any acceptance decision" -- is what this function finally wires in.

    20 Hz is not a new number here: it is `SUB20`'s edge, `test_discrimination.
    HPF_HZ`, and the edge of the `body_20_700_db` band."""
    x = np.asarray(x, float)
    p = np.abs(np.fft.rfft(x)) ** 2
    f = np.fft.rfftfreq(len(x), 1.0 / SR)
    m = f >= lo
    return float((p[m] * f[m]).sum() / (p[m].sum() + 1e-30))


def measure(x, n_clip=0):
    m = {"peak_dbfs": peak_dbfs(x), "n_clip": float(n_clip),
         "sub20_dbfs": band_energy_dbfs(x, *SUB20),
         "sub20_dc_dbfs": band_energy_dbfs(x, 0.0, 1e-9),
         "t20_ms": t20_band_ms(x),          # THE GATE: >= 20 Hz, qualified
         "t20_global_ms": t20_ms(x),        # diagnostic: the whole spectrum
         "attack_samp": float(attack_samples(x)),
         "peak_margin_db": peak_margin_db(x),
         "centroid_hz": centroid_hz(x),
         "centroid_global_hz": centroid_global_hz(x)}
    for name, (lo, hi) in BANDS.items():
        m[name] = band_energy_dbfs(x, lo, hi)
        m[name.replace("_db", "_pct")] = band_share_pct(x, lo, hi)
    return m


# ===========================================================================
# THE INSTRUMENT'S OWN RESOLUTION. A gate below it cannot produce a verdict.
# ===========================================================================
# CLAUDE.md: "Run a gate against the current state before committing it. An
# unsatisfiable gate is worse than no gate: it trains everyone to ignore gates,
# including the ones that work." Three of the gates declared above were below
# this instrument's resolution and were failing CONTROL voices on dither.
#
# WHY ONE LSB IS THE RIGHT PERTURBATION, rather than a convenient number: the
# block's output is int16 (contract 12), so no design change can move a sample
# by less than one count. A gate that cannot tell the candidate apart from a
# 1-LSB reshuffle of the baseline cannot attribute what it reads TO the
# candidate. The perturbation is dither -- it has nothing to do with a DC
# blocker -- so the resolution is measured independently of the thing on trial.
DITHER_PATTERNS = 8               # independent, seeded, so the study is
                                 # reproducible to the bit
RESOLUTION_PROPS = ("peak_dbfs", "body_20_700_db", "mid_700_5k_db",
                    "hf_5k_20k_db", "t20_ms_pct", "attack_samp", "centroid_pct")


def resolution_of(x, n_clip=0, patterns=DITHER_PATTERNS):
    """Each gated property's 1-LSB resolution on one clip: the largest change
    `patterns` independent +-1 LSB dither patterns produce.

    T20 IS A SPECIAL CASE AND THE REASON THIS FUNCTION IS NOT JUST A DITHER
    LOOP. `t20_ms` is read on a `T20_FRAME_MS` grid, so dither moves it by
    exactly zero -- a perturbation study alone would pronounce a 3 % allowance
    satisfiable on a voice whose entire decay is four frames. A QUANTISED
    estimator's resolution is its step size, so one frame as a percent of the
    baseline is folded in. On the RS (T20 = 8 ms) that is 25 %; on the CY
    (460 ms) 0.43 %.

    `attack_samp` IS THE SAME SHAPE OF PROBLEM and dither cannot see it either.
    The estimate is referenced to the peak through `ATTACK_TOL_DB`, so on a
    voice whose peak is a plateau the answer depends on that tolerance: the BD's
    baseline reads 376 / 355 / 328 samples at 0.5 / 1.0 / 2.0 dB. An estimator
    whose answer moves 48 samples when an arbitrary knob moves cannot resolve
    6 samples, so the SPREAD ACROSS THE DECLARED SWEEP, measured on the baseline
    alone, is the resolution. On the CY it is 7 samples, on the CH 0, and the
    declared 2-sample allowance stands wherever the spread is below it."""
    base = measure(x, n_clip)
    out = {p: 0.0 for p in RESOLUTION_PROPS}
    x64 = np.asarray(x, np.int64)
    for seed in range(patterns):
        rng = np.random.default_rng(seed)
        y = np.clip(x64 + rng.integers(-1, 2, len(x64)), -32768, 32767)
        d = deltas(base, measure(y, n_clip))
        for p in RESOLUTION_PROPS:
            v = abs(d[p])
            if not np.isnan(v) and v > out[p]:
                out[p] = v
    t20 = base["t20_ms"]
    if not np.isnan(t20) and t20 > 0:
        out["t20_ms_pct"] = max(out["t20_ms_pct"], 100.0 * T20_FRAME_MS / t20)
    a = [attack_samples(x, t) for t in ATTACK_TOL_SWEEP]
    out["attack_samp"] = max(out["attack_samp"], 1.0,   # 1: an integer index
                             float(max(a) - min(a)))
    return out


def resolution(voice, patterns=DITHER_PATTERNS):
    """The uncoupled baseline's resolution for one voice. The baseline, not the
    candidate: the question is what the INSTRUMENT can see, and the answer must
    not depend on what is on trial."""
    b, nb = render(voice, dx.COUPLE_OFF)
    return resolution_of(b, nb, patterns)


# ===========================================================================
# The acceptance rule (DR 0015: the property vector judges, not an aggregate)
# ===========================================================================
def allowance(prop, voice):
    d = LIMITS[prop]
    return d.get(voice, d.get("*"))


def deltas(base, cand):
    """Property-by-property change, in the units each limit is declared in."""
    d = {}
    for p in ("peak_dbfs", "body_20_700_db", "mid_700_5k_db", "hf_5k_20k_db"):
        d[p] = cand[p] - base[p]
    d["t20_ms_pct"] = 100.0 * (cand["t20_ms"] - base["t20_ms"]) / (base["t20_ms"] + 1e-30)
    d["t20_global_pct"] = 100.0 * (cand["t20_global_ms"] - base["t20_global_ms"]) \
        / (base["t20_global_ms"] + 1e-30)
    d["attack_samp"] = cand["attack_samp"] - base["attack_samp"]
    d["centroid_pct"] = 100.0 * (cand["centroid_hz"] - base["centroid_hz"]) / (base["centroid_hz"] + 1e-30)
    d["centroid_global_pct"] = 100.0 * (cand["centroid_global_hz"] - base["centroid_global_hz"]) \
        / (base["centroid_global_hz"] + 1e-30)
    d["sub20_dbfs"] = cand["sub20_dbfs"] - base["sub20_dbfs"]
    d["n_clip"] = cand["n_clip"] - base["n_clip"]
    return d


def preserved(voice, d, res=None, refuse=()):
    """Every declared limit, checked one at a time, against the LARGER of the
    declared allowance and the instrument's own resolution for that property on
    that voice. Returns `(broke, resolution_limited)`.

    `worst` is not computed anywhere in this file. DR 0015: the property
    vector is authoritative and an aggregate hides the thing you need to see.

    WHY THE RESOLUTION ENTERS THE RULE. Three of the allowances declared above
    are below what this instrument can see on some voices -- the HT's 5-20 kHz
    band is at -102 dBFS and one LSB of dither moves it 8.69 dB against a
    0.20 dB allowance. Judging against such a gate is not strict, it is
    meaningless: it was spending a CONTROL voice's verdict on dither.

    This is NOT a licence to widen a gate until the candidate fits. The
    widening is (a) computed from +-1 LSB dither on the UNCOUPLED baseline, so
    it cannot be influenced by the candidate, (b) returned separately as
    `resolution_limited` so the report has to say which gates were widened and
    by how much, and (c) powerless on a gate that resolves: the RS's +225 %
    decay is nine times its 25 % resolution and stays red
    (`test_the_true_rimshot_decay_failure_survives_the_repair`).

    `res=None` keeps the declared allowances exactly -- what the limits say
    before the instrument is consulted, which `--measure` prints beside the
    resolution-aware verdict so both are on the page.

    `refuse` NAMES PROPERTIES WITH NO VERDICT, and they are a third outcome
    rather than a quiet pass. A gate whose precondition fails (the rimshot's
    decay: `decay_gate_refuses`) is excluded from `bad` -- calling it a
    preservation failure would be inventing a result -- and every caller prints
    it as REFUSED. A refusal is NOT acceptance: it is a gate the candidate was
    never actually judged on, and it has to stay visible for that reason."""
    bad, limited = [], []
    for p in ("peak_dbfs", "body_20_700_db", "mid_700_5k_db", "hf_5k_20k_db",
              "t20_ms_pct", "attack_samp", "centroid_pct"):
        if p in refuse:
            continue
        a = allowance(p, voice)
        r = 0.0 if res is None else float(res.get(p, 0.0))
        if r > a:
            limited.append(p)
        v = d[p]
        if np.isnan(v) or abs(v) > max(a, r) + 1e-9:
            bad.append(p)
    if d["n_clip"] > 0:
        bad.append("n_clip")
    return bad, limited


# ===========================================================================
# THE SCREEN. What a DC blocker can do to a voice, from the voice's UNCOUPLED
# baseline alone -- before any blocker has been rendered.
# ===========================================================================
# #152's diagnosis -- a positive-only excitation into an all-pole mode with a
# DC gain of 18 to 23,899 -- says the drum block has unmodelled DC. #165 asks
# whether ONE blocker then fixes five voices. It does not, and the reason is
# sharper than a pass/fail: **the sub-20 Hz energy of these voices has two
# different origins, and a DC blocker can only remove one of them.**
#
#   * A STANDING OFFSET lands in the f = 0 bin. A one-pole blocker has an
#     EXACT zero at z = 1, so this is removed completely.
#   * THE ONSET ENVELOPE'S OWN SKIRT lands at 0 < f < 20 Hz. A 10 ms burst has
#     low-frequency content because it is 10 ms long; that content is the
#     pulse, not a fault, and the blocker attenuates it only by
#     |H(f)|, which at 7.46 Hz is -0.56 dB at the top of the band.
#
# So with phi = (f = 0 energy) / (total sub-20 energy),
#
#     E_after = sum_{0<f<20} S(f) |H(f)|^2  <=  (1 - phi) E_before
#     ==>  ATTENUATION >= -10 log10(1 - phi)                          (floor)
#
# and reaching 6 dB from the DC bin alone needs phi >= 1 - 10^-0.6 = 0.749.
# Everything above the floor has to come from attenuating the skirt, which
# means moving fc INTO the band whose preservation is the constraint. That is
# the trade-off, stated as an inequality instead of an opinion, and phi is
# computable from the baseline with no blocker in the circuit.
def dc_fraction(x):
    """phi: the share of a clip's sub-20 Hz energy that sits in the f = 0 bin.

    The f = 0 bin of a finite rectangular-windowed clip is the clip's MEAN
    squared, which is exactly the quantity a DC blocker nulls -- so this is not
    an approximation to the removable part, it IS the removable part.

    Read through `onesided_power`, which is the same convention
    `band_energy_dbfs` reports the attenuation in. See that function for the
    3 dB error this sharing exists to prevent."""
    p, f = onesided_power(x)
    dc = float(p[0])
    sub = float(p[(f >= 0.0) & (f < 20.0)].sum())
    return dc / (sub + 1e-30)


def sub20_below_fc_frac(x, k=dx.COUPLE_K):
    """**THE CLASS DISCRIMINATOR: what share of a voice's sub-20 Hz energy sits
    BELOW the blocker's own corner.** Above fc the filter passes; below it, it
    attenuates at 6 dB/octave. So this, and not phi, is what decides whether a
    blocker can help a voice.

    IT REPLACED phi FOR THAT JOB, AND THE REASON IS A MEASUREMENT ERROR WORTH
    KEEPING. `dc_fraction`'s f = 0 bin has a width of 1/T, so phi depends on the
    ANALYSIS WINDOW, not only on the signal: lengthening the clip from 0.60 s to
    2.40 s (which the decay gate's precondition forced) moved the CY's phi from
    0.8881 to 0.3907 and the CH's from 0.3518 to 0.0879, and a phi > 0.5 class
    label therefore reclassified the CY from "standing offset" to "skirt" with
    nothing about the cymbal having changed. The screen's conclusion was right
    and its statistic was wrong.

    This one is an integral over a FIXED band, so it is window-stable to a few
    percent across the same change --

        CY 0.991 -> 0.983    CH 0.976 -> 0.973     (blocker removes it)
        RS 0.403 -> 0.380    HT 0.381 -> 0.359     (blocker cannot)
        BD 0.375 -> 0.455

    -- and it separates the two classes by more than a factor of two at either
    window. phi is still printed, as a diagnostic, with its window stated."""
    p, f = onesided_power(np.asarray(x, float))
    fc = SR / (2.0 * np.pi * (1 << int(k)))
    sub = float(p[(f >= 0.0) & (f < 20.0)].sum())
    return float(p[(f >= 0.0) & (f < fc)].sum()) / (sub + 1e-30)


def attenuation_floor_db(phi):
    """-10 log10(1 - phi): the sub-20 Hz attenuation a DC-nulling filter
    delivers from the f = 0 bin alone, with NO help from the skirt. A lower
    bound on what any such filter achieves, and the whole of what one achieves
    whose corner is far below the band."""
    return float(-10.0 * np.log10(max(1e-12, 1.0 - min(phi, 1.0 - 1e-12))))


def _h2_onepole(f, k):
    """|H(f)|^2 of the filter that is actually implemented -- the DISCRETE
    one-pole with the pole at a = 1 - 2^-K and a zero at z = 1 -- not its
    analogue approximation. Using the real transfer function is what lets the
    prediction below be checked against the rendered integer filter as a
    prediction rather than a restatement."""
    a = 1.0 - 2.0 ** -int(k)
    w = 2.0 * np.pi * np.asarray(f, float) / SR
    z = np.exp(-1j * w)
    return np.abs((1.0 - z) / (1.0 - a * z)) ** 2


def steadystate_sub20_attenuation_db(x, k):
    """The sub-20 Hz attenuation the pole at 1 - 2^-K would give in STEADY
    STATE, computed from the UNCOUPLED spectrum and the filter's own transfer
    function, with nothing rendered. **An UPPER bound on what is achieved.**

    WHY IT IS A BOUND AND NOT AN EQUALITY -- and this was wrong before it was
    right. It was first written as `predicted_...` and asserted to match the
    render within 1.2 dB; on the CY it over-predicted by 5.73 dB and on the CH
    by 7.97 dB. Multiplying a spectrum by |H|^2 is a CIRCULAR, steady-state
    convolution; the filter that actually runs is causal, starts from rest, and
    answers the clip's opening step with a tail of its own whose time constant
    is 1/(2 pi fc) = 21 ms at K = 10. **That tail is itself sub-20 Hz energy**,
    so the finite causal filter always removes LESS than the steady-state figure
    says -- which is the same mechanism that triples the RS's decay.

    The bound is the useful direction. A NEGATIVE claim ("this voice cannot
    reach 6 dB") proved against the optimistic bound is robust: if the best case
    does not reach the target, the real filter certainly does not.

    Bracketed with `attenuation_floor_db` below, and the bracket is checked on
    all five voices by `test_the_screen_brackets_the_rendered_attenuation`."""
    p, f = onesided_power(x)
    m = (f >= 0.0) & (f < 20.0)
    before = float(p[m].sum())
    after = float((p[m] * _h2_onepole(f[m], k)).sum())
    return float(-10.0 * np.log10((after + 1e-30) / (before + 1e-30)))


def required_k(x, target_db=None, ks=range(16, 3, -1)):
    """The LOWEST corner (largest K) whose STEADY-STATE bound reaches
    `target_db` of sub-20 Hz attenuation, or None if no corner in `ks` does.
    K is a shift, so these are the only corners available.

    The optimistic bound on purpose: `None` then means "not reachable even in
    the best case", which is a claim the real filter cannot escape. A K that IS
    returned is a candidate to be measured, not a result."""
    target_db = IMPROVE_SUB20_DB if target_db is None else target_db
    for k in ks:
        if steadystate_sub20_attenuation_db(x, k) >= target_db:
            return int(k)
    return None


def share_rise_is_lf_removal(d, tol_db=0.2):
    """**THE TRAP, as a decision rule.** A high-band SHARE that rises while
    the same band's ABSOLUTE energy is flat to within `tol_db` did not gain a
    single harmonic: the denominator shrank. Returns True when that is what
    happened, and the caller must then refuse to read the share as an
    improvement.

    This is why every share column in this file has an absolute column
    beside it, and why a tom improvement from a DC blocker is to be treated
    as suspect until this rule has been applied to it (#165 s4)."""
    return abs(d["hf_5k_20k_db"]) <= tol_db and abs(d["mid_700_5k_db"]) <= tol_db


# ===========================================================================
# Reports
# ===========================================================================
def report_limits():
    print(provenance())
    print("\nDECLARED BEFORE THE FIRST MEASUREMENT (git: the commit that adds this file)\n")
    print(f"  REQUIRED improvement, {' and '.join(SUBJECTS)}:")
    print(f"    sub-20 Hz ABSOLUTE energy falls by >= {IMPROVE_SUB20_DB:.1f} dB\n")
    print("  PRESERVATION allowance, every voice (controls: " + ", ".join(CONTROLS) + ")\n")
    print(f"    {'property':18s} {'unit':10s} {'BD':>7s} {'others':>7s}")
    units = {"peak_dbfs": "dB", "body_20_700_db": "dB(abs)", "mid_700_5k_db": "dB(abs)",
             "hf_5k_20k_db": "dB(abs)", "t20_ms_pct": "%", "attack_samp": "samples",
             "centroid_pct": "%"}
    for p, u in units.items():
        print(f"    {p:18s} {u:10s} {allowance(p, 'BD'):7.2f} {allowance(p, 'CY'):7.2f}")
    print("    n_clip             count       must not increase")
    print("\n  centroid_pct is the >= 20 Hz centroid (CENTROID_FLOOR_HZ), not a global")
    print("  one: a global centroid rises MECHANICALLY when sub-20 Hz energy is")
    print("  removed, so the gate would read the candidate's intended effect as a")
    print("  failure. Qualified in closed form by dc_centroid_gate_qualification.py.")
    print("\n  t20_ms_pct is READ ON THE SAME >= 20 Hz BAND, for the same reason: a")
    print("  global T20 is a backward energy integral, so a sub-20 Hz pedestal")
    print("  inflates it most exactly where the gate reads. Qualified in closed form")
    print("  by dc_t20_gate_qualification.py, which also checks the repaired gate")
    print("  still goes red on a genuinely 10 % faster decay. The sub-20 Hz settling")
    print("  tail a blocker adds is not thereby ignored: it is the sub20 column.")
    print(f"\n  attack_samp is the first arrival within {ATTACK_TOL_DB:.1f} dB of the peak, not")
    print("  argmax(|x|): on a plateaued voice argmax answers a lobe RANK ORDER.")
    print("\n  Each allowance is read against the LARGER of itself and the instrument's")
    print("  own 1-LSB resolution for that property on that voice (--resolution). A")
    print("  gate below the resolution is marked [res-limited] wherever it is used.")
    print("\n  A normalised band SHARE is never a verdict; `share_rise_is_lf_removal`")
    print("  refuses one whose absolute band energy did not move.")
    return 0


def report_clipping(k=dx.COUPLE_K, gains=(0.45, 0.75, 0.95, 1.0)):
    """**#165 s2's placement argument, as a measurement instead of a sentence.**

    The argument is "removing DC after clipping cannot recover the waveform the
    clipping destroyed", and at the reference gain it CANNOT BE TESTED: nothing
    rails, `n_clip` is 0 on all five voices, and `bus` and `post` agree to
    0.02 dB. Reporting that agreement as support for the placement would be
    taking a null condition for evidence -- the two placements were never
    actually distinguished.

    So the condition is created: raise the bus gain until the output stage's
    clamp fires, then ask what each placement does. `bus` runs the blocker
    BEFORE the clamp (where a hardware coupling capacitor sits, ahead of the
    output amplifier's rail) and `post` after it.

    What to read. `clip` is how many samples railed; `restored dB` is how much
    sub-20 Hz energy each placement removes; `wave dB` is the RMS difference
    between the placement's output and the uncoupled-then-ideally-blocked
    reference -- the waveform error, which is the quantity the argument is
    about, not the DC reading. A placement that reduces the DC reading while
    leaving a larger waveform error is exactly the failure s2 warns about: both
    placements reduce the DC number, and that number alone cannot choose
    between them."""
    print(provenance())
    print("\nPLACEMENT UNDER CLIPPING. One voice at the reference gain never rails --")
    print("`n_clip` is 0 on all five -- so `bus` and `post` agree to 0.02 dB and the")
    print("s2 argument is UNTESTED there. A stack of simultaneous voices at full bus")
    print("gain is the condition the argument is about, and a player makes it.\n")
    print("  IDEAL = the same filter applied to the unclamped accumulator: the answer")
    print("  with no rail anywhere. Both placements are read against it.\n")
    print(f"  {'stack':26s} {'clip bus':>8s} {'clip post':>9s} {'err bus':>8s} "
          f"{'err post':>9s} {'sub20 bus':>9s} {'sub20 post':>10s}")
    print(f"  {'':26s} {'count':>8s} {'count':>9s} {'dBFS':>8s} {'dBFS':>9s} "
          f"{'dB':>9s} {'dB':>10s}")
    out = {}
    for stack in (("CY",), ("CY", "RS"), ("CY", "BD", "RS"),
                  ("CY", "BD", "RS", "CH", "HT")):
        off, n_off, acc_off = _stack(stack, dx.COUPLE_OFF, k)
        bus, n_bus, _ = _stack(stack, dx.COUPLE_BUS, k)
        post, n_post, _ = _stack(stack, dx.COUPLE_POST, k)
        ideal = dx.dc_block(np.asarray(acc_off, np.int64), k).astype(float)

        def err_db(y):
            e = np.asarray(y, float) - ideal
            return 20.0 * np.log10(float(np.sqrt((e ** 2).mean())) / FS + 1e-30)
        s0 = band_energy_dbfs(off, *SUB20)
        print(f"  {'+'.join(stack):26s} {n_bus:8d} {n_post:9d} {err_db(bus):8.2f} "
              f"{err_db(post):9.2f} {band_energy_dbfs(bus, *SUB20) - s0:+9.2f} "
              f"{band_energy_dbfs(post, *SUB20) - s0:+10.2f}")
        out["+".join(stack)] = {"clip_off": n_off, "clip_bus": n_bus,
                                "clip_post": n_post, "err_bus": err_db(bus),
                                "err_post": err_db(post)}
    print("\n  WHAT SEPARATES THEM, and it is not the DC reading: both placements")
    print("  remove the same sub-20 Hz energy to a hundredth of a dB, at every stack.")
    print("  **A blocker in the wrong place still reduces the DC number** -- s2's")
    print("  warning, measured rather than repeated. What separates them is the rail:")
    print("  removing the standing offset BEFORE the clamp buys back the headroom the")
    print("  offset was consuming, so `bus` rails on fewer samples and lands closer to")
    print("  the unclamped ideal. `post` filters a waveform the clamp has already")
    print("  flattened and cannot recover it. `bus` is the placement that ships.")
    print("\n  THE REFERENCE IS BIASED TOWARDS `post`, AND THAT IS STATED RATHER THAN")
    print("  HIDDEN. `ideal` = dc_block(unclamped accumulator) is exactly what `post`")
    print("  computes when nothing rails, so `post` reads -600 dBFS (bit-identical)")
    print("  on every stack that does not clip, while `bus` differs by about half an")
    print("  LSB (-96 dBFS) purely from the order of the integer rounding. The")
    print("  conclusion survives a reference that favours the loser: once the rail")
    print("  fires, `bus` is 1.21 dB closer to the ideal and rails 18 samples against")
    print("  25. A reference built the other way round would only widen that.")
    return out


def _stack(sounds, couple, k=dx.COUPLE_K, gain=1.0, seconds=0.50):
    """Several voices struck on the same frame at full bus gain, plus the
    PRE-CLAMP accumulator so an unclamped ideal can be computed.

    Rendered here rather than through `render` because the ideal needs `acc`,
    which `render` discards after counting rails -- and the ideal is the whole
    point: without it both placements are only comparable to each other."""
    n = int(seconds * SR)
    kit = dx.kit_with_sounds(*sounds)
    hits = sorted((LEAD_FRAMES, dx.SOUND_STOP[s], 1.0) for s in sounds)
    inner = couple if couple in (dx.COUPLE_EXC, dx.COUPLE_BUS) else dx.COUPLE_OFF
    d = dx.DrumsFx(couple=inner, couple_k=k)
    dmix, body = d.play(dx.hit_writes(hits, kit), n)
    g = dx.accent_reg(gain)
    acc = (np.asarray(dmix, np.int64) * g + np.asarray(body, np.int64) * g) >> 15
    n_clip = int(((acc > 32767) | (acc < -32768)).sum())
    out = dx.output_fx(np.zeros(n), 0, dmix, g, body, g)
    if couple == dx.COUPLE_POST:
        y = dx.dc_block(out, k)
        n_clip += int(((y > 32767) | (y < -32768)).sum())
        out = np.clip(y, -32768, 32767).astype(np.int16)
    return out, n_clip, acc.astype(float)


def report_decay(seconds=RENDER_S, clips=(0.60, 1.20, 2.40, 4.80)):
    """**The decay gate's precondition, per voice, before any decay number is
    read off it.** A decay does not depend on how long you watched.

    This report exists because the gate failed its own precondition on two of
    the five voices, for two different reasons, and both were invisible in the
    verdict table: the CY read 23 % short by Schroeder truncation and the RS read
    the clip's length by brick-wall leakage. Qualified in closed form, both legs,
    by `dc_t20_gate_qualification.py`."""
    print(provenance())
    print("\nTHE DECAY GATE'S PRECONDITION. A decay is a property of the signal, so an")
    print("estimate that MOVES when the observation window doubles is measuring the")
    print("window. Necessary, not sufficient -- it cannot prove an estimate right, and")
    print(f"it rejected two defects here. Allowance: {DECAY_INVARIANCE_PCT:.1f} % across a doubling.\n")
    print(f"  {'':5s} " + " ".join(f"{str(c) + ' s':>9s}" for c in clips) +
          f" {'verdict':>10s}  what the number is")
    for v in SUBJECTS + CONTROLS:
        vals = []
        for c in clips:
            b, _ = render(v, dx.COUPLE_OFF, seconds=c)
            vals.append(t20_band_ms(b))
        ref, ta, tb = decay_gate_refuses(v, seconds)
        # does it track the clip? compare the two longest clips it answered on
        seen = [(c, x) for c, x in zip(clips, vals) if not np.isnan(x)]
        tracks = len(seen) >= 2 and abs(
            seen[-1][1] / (seen[0][1] + 1e-30) - seen[-1][0] / seen[0][0]) < 0.25
        what = ("the CLIP, to within 25 % of the doubling ratio: LEAKAGE"
                if tracks else
                ("the voice's decay" if not ref else "not clip-invariant"))
        print(f"  {v:5s} " + " ".join(
            ("      nan" if np.isnan(x) else f"{x:9.1f}") for x in vals) +
            f" {'REFUSED' if ref else 'ok':>10s}  {what}")
    print(f"\n  The gates are measured at RENDER_S = {seconds:.2f} s, which is where the CY")
    print("  stops moving. The RS never does, so `t20_ms_pct` has NO VERDICT on the RS:")
    print("  it reaches exact silence about 25 ms in, and a 20 Hz brick wall's sinc")
    print("  spans the clip, so what the estimator integrates is its own smearing.")
    print("  WHAT BOUNDS THE RS INSTEAD: its absolute band energies (body, mid, HF),")
    print("  its peak, its attack and its >= 20 Hz centroid -- six gates that do")
    print("  produce verdicts. A refusal removes one gate, not the acceptance test.")
    return 0


def report_screen(k=None, ks=(8, 9, 10, 11, 12, 13)):
    """**Which voices a DC blocker can help, read off the uncoupled baselines
    before a blocker is rendered -- and then checked against the renders.**

    This is the answer to #165 s1 that a pass/fail cannot give: the two subjects
    do not need the same thing, because their sub-20 Hz energy does not have the
    same origin."""
    k = dx.COUPLE_K if k is None else k
    print(provenance())
    print("\nTHE SCREEN. beta is the share of a voice's sub-20 Hz energy BELOW the")
    print("blocker's own corner -- the part the filter attenuates. The rest is the onset")
    print("envelope's skirt, which straddles the corner and is passed. beta is the CLASS")
    print("DISCRIMINATOR and is window-stable; phi (the f = 0 bin alone) is printed")
    print("beside it as a diagnostic and is NOT, because that bin's width is 1/T: the")
    print("CY's phi fell 0.888 -> 0.391 when the clip went 0.60 -> 2.40 s and nothing")
    print("about the cymbal changed. The floor below is still read off phi, so it is a")
    print("window-dependent floor on a window-independent conclusion.\n")
    print("phi is the share in the f = 0 bin -- the part a zero at z = 1 removes")
    print("exactly. The rest the blocker only attenuates by |H(f)|. So")
    print("\n    attenuation >= -10 log10(1 - phi)   (floor, from the DC bin alone)")
    print(f"\nand {IMPROVE_SUB20_DB:.0f} dB from the DC bin alone needs phi >= "
          f"{1 - 10 ** (-IMPROVE_SUB20_DB / 10):.3f}. FLOOR and STEADY are both")
    print("computed from the uncoupled spectrum and the filter's own transfer function,")
    print("with NOTHING rendered; MEASURED is the integer filter run inside the block.")
    print("STEADY is an UPPER bound: a causal filter starting from rest answers the")
    print("clip's opening step with a tail of its own, and that tail is sub-20 Hz energy")
    print("too -- the same 21 ms tail that triples the rimshot's GLOBAL T20 while leaving")
    print("its audible band alone (--decay). MEASURED must")
    print("land between the two, which is a two-sided check of a model against an")
    print("implementation neither was fitted to.\n")
    print(f"  {'':5s} {'beta':>7s} {'phi':>7s} {'floor':>8s} {'steady':>8s} {'measured':>9s} "
          f"{'slack':>7s}  {'K for ' + str(int(IMPROVE_SUB20_DB)) + ' dB':>12s}  where the sub-20 Hz energy IS")
    print(f"  {'':5s} {'<fc':>7s} {'f=0':>7s} {'dB':>8s} {'dB':>8s} {'dB':>9s} {'dB':>7s}")
    out = {}
    for v in SUBJECTS + CONTROLS:
        b, nb = render(v, dx.COUPLE_OFF)
        c, nc = render(v, dx.COUPLE_BUS, k)
        phi = dc_fraction(b)
        floor = attenuation_floor_db(phi)
        pred = steadystate_sub20_attenuation_db(b, k)
        meas = -(measure(c, nc)["sub20_dbfs"] - measure(b, nb)["sub20_dbfs"])
        rk = required_k(b)
        beta = sub20_below_fc_frac(b, k)
        origin = ("BELOW the corner: a blocker removes it"
                  if beta > 0.5 else
                  "the ONSET ENVELOPE'S SKIRT, across the corner: it cannot")
        inside = "" if floor - 0.6 <= meas <= pred + 0.6 else "  **OUTSIDE BRACKET**"
        print(f"  {v:5s} {beta:7.4f} {phi:7.4f} {floor:8.2f} {pred:8.2f} {meas:9.2f} "
              f"{meas - floor:+7.2f}  {('K=' + str(rk)) if rk else 'NONE':>12s}  "
              f"{origin}{inside}")
        out[v] = {"phi": phi, "beta": beta, "floor": floor, "pred": pred,
                  "meas": meas, "k": rk}
    print(f"\n  'K for {IMPROVE_SUB20_DB:.0f} dB' is the LOWEST corner (largest K) that reaches the")
    print("  required improvement. K is a shift, so these are the only corners the")
    print(f"  hardware can build, and the circuit's own is K = {dx.COUPLE_K} "
          f"({SR / (2 * np.pi * (1 << dx.COUPLE_K)):.2f} Hz). A voice whose")
    print("  answer is a SMALLER K than that is asking for a corner the circuit does not")
    print("  have, and --cutoff is where what that costs is measured.\n")
    print("  WHAT THE SCREEN COSTS TO BEAT. The same prediction across the corners:\n")
    print(f"  {'':5s} " + " ".join(f"{'K=' + str(kk):>8s}" for kk in ks))
    for v in SUBJECTS + CONTROLS:
        b, _ = render(v, dx.COUPLE_OFF)
        print(f"  {v:5s} " +
              " ".join(f"{steadystate_sub20_attenuation_db(b, kk):8.2f}" for kk in ks))
    print("\n  (predicted sub-20 Hz attenuation, dB; --cutoff prints the preservation cost")
    print("   of each of these corners on the same voices)")
    return out


def report_resolution(sweep=ATTACK_TOL_SWEEP):
    """**What this instrument can see, before any gate is allowed a verdict.**

    CLAUDE.md: run a gate against the current state before committing it. Three
    of the allowances above turned out to sit below the resolution -- they were
    failing CONTROL voices on dither -- and one (`t20_ms_pct` on the RS) sits
    below one frame of its own quantisation grid.

    The attack tolerance is swept here rather than argued: a value below the
    lobe ambiguity it exists to absorb puts the BD back on its 460-sample
    cliff, and every value above it gives the same answer."""
    print(provenance())
    print("\nTHE INSTRUMENT'S OWN RESOLUTION: largest change from "
          f"{DITHER_PATTERNS} independent +-1 LSB")
    print("dither patterns on the UNCOUPLED baseline. One LSB is the smallest change the")
    print("int16 output (contract 12) can express, so a gate below this cannot attribute")
    print("what it reads to the candidate. T20 folds in one frame of its own grid, which")
    print("dither cannot see.\n")
    print(f"  {'':5s} {'peak':>8s} {'body':>9s} {'mid':>8s} {'HF':>9s} "
          f"{'T20':>8s} {'atk':>5s} {'cent':>8s}")
    print(f"  {'':5s} {'dB':>8s} {'dB':>9s} {'dB':>8s} {'dB':>9s} "
          f"{'%':>8s} {'samp':>5s} {'%':>8s}")
    bad = []
    for v in SUBJECTS + CONTROLS:
        r = resolution(v)
        flags = [p for p in RESOLUTION_PROPS if r[p] > allowance(p, v)]
        bad += [(v, p, r[p], allowance(p, v)) for p in flags]
        print(f"  {v:5s} {r['peak_dbfs']:8.4f} {r['body_20_700_db']:9.4f} "
              f"{r['mid_700_5k_db']:8.4f} {r['hf_5k_20k_db']:9.4f} "
              f"{r['t20_ms_pct']:8.3f} {r['attack_samp']:5.0f} {r['centroid_pct']:8.4f}"
              f"   {'ok' if not flags else 'BELOW RESOLUTION: ' + ','.join(flags)}")
    print("\n  GATES THAT CANNOT PRODUCE A VERDICT AS DECLARED\n")
    if not bad:
        print("    none")
    for v, p, r, a in bad:
        print(f"    {v} {p:18s} allowance {a:7.2f}   resolution {r:8.3f}   "
              f"{r / max(a, 1e-9):6.1f}x too tight")
    print("\n  ATTACK TOLERANCE SWEEP. The value is read off peak_margin_db, and the")
    print("  verdict must not depend on it above the ambiguity it absorbs.\n")
    print(f"  {'':5s} {'margin dB':>10s} " +
          " ".join(f"{'tol ' + str(t):>12s}" for t in sweep))
    for v in SUBJECTS + CONTROLS:
        b, _ = render(v, dx.COUPLE_OFF)
        c, _ = render(v, dx.COUPLE_BUS)
        cells = []
        for t in sweep:
            cells.append(f"{attack_samples(c, t) - attack_samples(b, t):+12.0f}")
        print(f"  {v:5s} {peak_margin_db(b):10.3f} " + " ".join(cells))
    print("\n  (columns are the coupled-minus-uncoupled attack change, in samples)")
    return 0


def _refused_props(voice, seconds=RENDER_S):
    """Which declared gates have no verdict on this voice, measured on its own
    uncoupled baseline. Today that is only the decay gate."""
    return ("t20_ms_pct",) if decay_gate_refuses(voice, seconds)[0] else ()


def _row(name, base, cand, voice, res=None, refuse=None):
    d = deltas(base, cand)
    refuse = _refused_props(voice) if refuse is None else refuse
    bad, limited = preserved(voice, d, res, refuse)
    note = "ok" if not bad else ",".join(bad)
    if refuse:
        note += "   [REFUSED: " + ",".join(refuse) + "]"
    if limited:
        note += "   [res-limited: " + ",".join(limited) + "]"
    print(f"  {name:22s} {d['sub20_dbfs']:+8.2f} {d['body_20_700_db']:+8.2f} "
          f"{d['mid_700_5k_db']:+8.2f} {d['hf_5k_20k_db']:+8.2f} "
          f"{d['peak_dbfs']:+7.2f} {d['t20_ms_pct']:+7.2f} {d['attack_samp']:+6.0f} "
          f"{d['centroid_pct']:+7.2f}  {note}")
    return d, bad


def _head():
    print(f"  {'':22s} {'sub20':>8s} {'body':>8s} {'mid':>8s} {'HF':>8s} "
          f"{'peak':>7s} {'T20':>7s} {'atk':>6s} {'cent':>7s}  limits")
    print(f"  {'':22s} {'dB abs':>8s} {'dB abs':>8s} {'dB abs':>8s} {'dB abs':>8s} "
          f"{'dB':>7s} {'%':>7s} {'samp':>6s} {'%':>7s}")


def report_placement(k=dx.COUPLE_K):
    """Placement is a MEASUREMENT here, not an argument. Four of them, on the
    two subjects, all against the same baseline on the same instrument."""
    print(provenance())
    print("\nPLACEMENT. Every number is a change from the uncoupled baseline of the")
    print("SAME voice on the SAME instrument (DR 0015: same qualified basis).\n")
    out = {}
    for v in SUBJECTS + CONTROLS:
        b, nb = render(v, dx.COUPLE_OFF)
        base = measure(b, nb)
        print(f"{v}   baseline: peak {base['peak_dbfs']:+.2f} dBFS  sub20 "
              f"{base['sub20_dbfs']:+.2f} dB  body {base['body_20_700_db']:+.2f} dB  "
              f"HF {base['hf_5k_20k_db']:+.2f} dB  T20 {base['t20_ms']:.1f} ms  "
              f"clip {int(base['n_clip'])}")
        res = resolution_of(b, nb)
        _head()
        for pl in (dx.COUPLE_EXC, dx.COUPLE_BUS, dx.COUPLE_POST):
            c, nc = render(v, pl, k)
            d, bad = _row(f"{pl}", base, measure(c, nc), v, res)
            out[(v, pl)] = (d, bad)
        print()
    return out


def report_measure(k=dx.COUPLE_K, placement=dx.COUPLE_BUS):
    """The deliverable: improvement and preservation, together, in one table."""
    print(provenance())
    print(f"\nIMPROVEMENT AND PRESERVATION, placement '{placement}', K = {k} "
          f"({SR / (2 * np.pi * (1 << k)):.3f} Hz)\n")
    print("  Absolute dB throughout. Shares are printed after, separately, and")
    print("  are not part of any verdict.\n")
    _head()
    rows, verdict = {}, {}
    for v in SUBJECTS + CONTROLS:
        b, nb = render(v, dx.COUPLE_OFF)
        c, nc = render(v, placement, k)
        base, cand = measure(b, nb), measure(c, nc)
        res = resolution_of(b, nb)
        d, bad = _row(f"{v}  ({'subject' if v in SUBJECTS else 'control'})", base, cand, v, res)
        rows[v] = (base, cand, d, bad)
        if v in SUBJECTS:
            verdict[v] = (d["sub20_dbfs"] <= -IMPROVE_SUB20_DB, bad)
        else:
            verdict[v] = (True, bad)
    print("\n  THE GATE THAT WAS WIRED WRONG, both ways, so the change is on the page.")
    print("  A GLOBAL spectral centroid is not a preservation gate for a DC blocker:")
    print("  removing sub-20 Hz energy raises it mechanically. Qualified >= 20 Hz.\n")
    print(f"  {'':10s} {'centroid >=20 Hz (the GATE)':>30s} {'centroid global (diagnostic)':>31s}")
    for v in SUBJECTS + CONTROLS:
        base, cand, d, _ = rows[v]
        print(f"  {v:10s} {base['centroid_hz']:11.1f} ->{cand['centroid_hz']:10.1f} Hz "
              f"{d['centroid_pct']:+6.2f}% {base['centroid_global_hz']:11.1f} ->"
              f"{cand['centroid_global_hz']:10.1f} Hz {d['centroid_global_pct']:+6.2f}%")
    print("\n  THE SECOND GATE WITH THE SAME DEFECT, found after the first was repaired.")
    print("  A GLOBAL T20 is a backward ENERGY integral over the whole spectrum, so a")
    print("  sub-20 Hz pedestal inflates it -- most where the gate reads, late in the")
    print("  clip. Removing the pedestal then shortens it for a reason that is not an")
    print("  audible decay change. Qualified >= 20 Hz by dc_t20_gate_qualification.py:")
    print("  a tone whose T20 is 200.0 ms by construction reads 452 ms globally and")
    print("  202 ms banded when a 4 % standing offset is present, and the repaired gate")
    print("  still reads -9.90 % on a genuinely 10 % faster decay.\n")
    print(f"  {'':10s} {'T20 >=20 Hz (the GATE)':>30s} {'T20 global (diagnostic)':>31s}")
    for v in SUBJECTS + CONTROLS:
        base, cand, d, _ = rows[v]
        print(f"  {v:10s} {base['t20_ms']:11.1f} ->{cand['t20_ms']:10.1f} ms "
              f"{d['t20_ms_pct']:+6.2f}% {base['t20_global_ms']:11.1f} ->"
              f"{cand['t20_global_ms']:10.1f} ms {d['t20_global_pct']:+6.2f}%")
    print("\n  NORMALISED SHARES (%% of clip energy) -- diagnostic only, never a verdict\n")
    print(f"  {'':10s} {'body %':>16s} {'mid %':>16s} {'HF %':>16s}   HF share")
    print(f"  {'':10s} {'before':>7s} {'after':>8s} {'before':>7s} {'after':>8s} "
          f"{'before':>7s} {'after':>8s}")
    for v in SUBJECTS + CONTROLS:
        base, cand, d, _ = rows[v]
        why = "LF removal, NOT new HF" if share_rise_is_lf_removal(d) else "absolute HF moved"
        print(f"  {v:10s} {base['body_20_700_pct']:7.2f} {cand['body_20_700_pct']:8.2f} "
              f"{base['mid_700_5k_pct']:7.2f} {cand['mid_700_5k_pct']:8.2f} "
              f"{base['hf_5k_20k_pct']:7.2f} {cand['hf_5k_20k_pct']:8.2f}   {why}")
    print("\nVERDICT (DR 0015: property vector, no aggregate)\n")
    ok = True
    for v in SUBJECTS:
        imp, bad = verdict[v]
        ok &= imp and not bad
        ref = _refused_props(v)
        print(f"  {v:4s} improvement >= {IMPROVE_SUB20_DB:.1f} dB sub-20: "
              f"{'MET' if imp else 'NOT MET'} ({rows[v][2]['sub20_dbfs']:+.2f} dB)   "
              f"preservation: {'ok' if not bad else 'BROKE ' + ','.join(bad)}"
              f"{'   NO VERDICT on ' + ','.join(ref) if ref else ''}")
    for v in CONTROLS:
        _, bad = verdict[v]
        ok &= not bad
        ref = _refused_props(v)
        print(f"  {v:4s} control, must be preserved: "
              f"{'ok' if not bad else 'BROKE ' + ','.join(bad)}"
              f"{'   NO VERDICT on ' + ','.join(ref) if ref else ''}")
    refused_any = {v: _refused_props(v) for v in SUBJECTS + CONTROLS}
    refused_any = {v: r for v, r in refused_any.items() if r}
    print(f"\n  {'ACCEPTED' if ok else 'NOT ACCEPTED'} against the limits declared in this file.")
    if refused_any:
        print("  A REFUSED gate is not a passed one. " + "; ".join(
            f"{v}: {','.join(r)}" for v, r in refused_any.items()) +
            " -- see `--decay` for why, and what bounds the voice instead.")
    return rows


def report_cutoff(ks=(8, 9, 10, 11, 12, 13), placement=dx.COUPLE_BUS):
    """The trade-off the brief says a blocker makes: settling speed against
    bass preservation. One column each, swept, so it is a curve and not an
    opinion."""
    print(provenance())
    print(f"\nCUTOFF SWEEP, placement '{placement}'. K is the pole 1 - 2^-K.\n")
    for v in SUBJECTS + CONTROLS:
        b, nb = render(v, dx.COUPLE_OFF)
        base = measure(b, nb)
        res = resolution_of(b, nb)
        print(f"{v}")
        print(f"  {'K':>3s} {'fc Hz':>8s} {'sub20':>8s} {'body':>8s} {'HF':>8s} "
              f"{'peak':>7s} {'T20 %':>7s} {'cent %':>7s}  limits")
        refuse = _refused_props(v)
        for k in ks:
            c, nc = render(v, placement, k)
            d = deltas(base, measure(c, nc))
            bad, _ = preserved(v, d, res, refuse)
            print(f"  {k:3d} {SR / (2 * np.pi * (1 << k)):8.3f} {d['sub20_dbfs']:+8.2f} "
                  f"{d['body_20_700_db']:+8.2f} {d['hf_5k_20k_db']:+8.2f} "
                  f"{d['peak_dbfs']:+7.2f} {d['t20_ms_pct']:+7.2f} {d['centroid_pct']:+7.2f}"
                  f"  {'ok' if not bad else ','.join(bad)}"
                  f"{'   [REFUSED: ' + ','.join(refuse) + ']' if refuse else ''}")
        print()
    return 0


# ===========================================================================
# Continuous playing: the state a blocker carries
# ===========================================================================
def _passage(sound, second=None, n_hits=8, spacing_ms=120.0, couple=dx.COUPLE_BUS,
             k=dx.COUPLE_K, switch_at=None):
    """A passage of repeated hits on one circuit, optionally retuned to the
    circuit's OTHER sound part-way through -- mid-ring, which is where state
    handling breaks (#165 s5)."""
    step = int(SR * spacing_ms / 1e3)
    n = LEAD_FRAMES + step * (n_hits + 1)
    kit = dx.kit_with_sounds(sound)
    hits = [(LEAD_FRAMES + i * step, dx.SOUND_STOP[sound], 1.0) for i in range(n_hits)]
    extra = []
    if second is not None and switch_at is not None:
        # The retune lands BETWEEN hits, while the previous hit is still
        # ringing: a panel switch is a register write, not a silence.
        f = LEAD_FRAMES + int(switch_at * step) + step // 2
        extra = [(f, a, v) for a, v in dx.preset_writes(second)]
        hits = [h for h in hits if h[0] < f] + \
               [(t, dx.SOUND_STOP[second], 1.0) for _, t, _ in [] ] + \
               [(t, dx.SOUND_STOP[second], 1.0) for t in
                [LEAD_FRAMES + i * step for i in range(n_hits)] if t > f]
    out, nclip = render(sound, couple, k, seconds=n / SR, hits=sorted(hits),
                        kit=kit, extra=extra)
    return out, nclip, step


# The bus placement is two blockers (dmix and body), each truncating by at most
# one LSB per sample from the shift -- the bound
# test_a_bus_blocker_is_the_superposition_of_per_path_blockers already asserts.
# So a coupled retune step may exceed the uncoupled one by this much and no
# more. Declared from that bound, NOT from the spread of any measurement.
SWITCH_STEP_TOL_LSB = 2.0


def _switch_steps(k, placement):
    """[(a, b, off_step, coupled_step)], the largest 1-sample step at the switch
    frame of each exclusive pair, in full-scale units, uncoupled then coupled."""
    rows = []
    for a_, b_ in dx.PAIRS:
        steps = []
        for couple in (dx.COUPLE_OFF, placement):
            out, _, step = _passage(a_, b_, couple=couple, k=k, switch_at=3)
            f = LEAD_FRAMES + 3 * step + step // 2
            w = np.asarray(out[f - 4:f + 5], float)
            steps.append(float(np.abs(np.diff(w)).max()) / FS)
        rows.append((a_, b_, steps[0], steps[1]))
    return rows


def report_continuous(k=dx.COUPLE_K, placement=dx.COUPLE_BUS):
    """Four things a one-shot cannot show: repeated hits, an overlap, a choke,
    and a retune mid-ring on a shared circuit."""
    print(provenance())
    print(f"\nCONTINUOUS PLAYING, placement '{placement}', K = {k}\n")

    print("  repeated hits: peak of each hit's window, dBFS, uncoupled -> coupled")
    print(f"  {'sound':6s} {'hit':>4s} {'off':>9s} {'on':>9s} {'delta':>7s}   drift")
    for v in SUBJECTS:
        a, _, step = _passage(v, couple=dx.COUPLE_OFF, k=k)
        b, _, _ = _passage(v, couple=placement, k=k)
        pa, pb = [], []
        for i in range(8):
            s = slice(LEAD_FRAMES + i * step, LEAD_FRAMES + (i + 1) * step)
            pa.append(peak_dbfs(a[s])); pb.append(peak_dbfs(b[s]))
        for i in (0, 7):
            print(f"  {v:6s} {i:4d} {pa[i]:9.3f} {pb[i]:9.3f} {pb[i] - pa[i]:+7.3f}")
        drift = (pb[7] - pa[7]) - (pb[0] - pa[0])
        print(f"  {v:6s} last-minus-first of the coupled/uncoupled delta: "
              f"{drift:+.4f} dB  -- state that wandered would show here\n")

    print("  retune mid-ring on a shared circuit (the five exclusive pairs)")
    print(f"  {'pair':10s} {'off':>10s} {placement:>10s} {'excess LSB':>11s}"
          "   largest 1-sample step at the switch frame")
    worst = float("-inf")
    for a_, b_, off, on in _switch_steps(k, placement):
        ex = (on - off) * FS
        worst = max(worst, ex)
        print(f"  {a_}->{b_:6s} {off:10.6f} {on:10.6f} {ex:+11.3f}")
    print("\n  A blocker holds charge across a retune, so the switch must not add a")
    print("  step of its own. The rule is: the coupled step exceeds the uncoupled one")
    print(f"  by no more than {SWITCH_STEP_TOL_LSB:g} LSB (1 LSB per blocker, two on the bus),")
    print(f"  the truncation bound. Worst observed excess {worst:+.3f} LSB: "
          f"{'within' if worst <= SWITCH_STEP_TOL_LSB else 'OVER'} it.\n")

    print("  choke (CH chokes OH) and overlap (a hit into a ring)")
    for name, hits, sound in (
            ("choke  OH then CH", [(10, dx.SOUND_STOP["OH"], 1.0),
                                   (10 + int(0.05 * SR), dx.SOUND_STOP["CH"], 1.0)], "OH"),
            ("overlap CY + CY", [(10, dx.SOUND_STOP["CY"], 1.0),
                                 (10 + int(0.08 * SR), dx.SOUND_STOP["CY"], 1.0)], "CY")):
        a, na = render(sound, dx.COUPLE_OFF, k, seconds=0.6, hits=hits)
        b, nb = render(sound, placement, k, seconds=0.6, hits=hits)
        d = deltas(measure(a, na), measure(b, nb))
        print(f"  {name:20s} sub20 {d['sub20_dbfs']:+7.2f} dB   HF {d['hf_5k_20k_db']:+6.2f} dB"
              f"   peak {d['peak_dbfs']:+6.2f} dB   clip {int(d['n_clip']):+d}")
    return 0


RECORDS = (
    ("limits.txt", report_limits, ()),
    ("decay.txt", report_decay, ()),
    ("clipping.txt", report_clipping, ()),
    ("resolution.txt", report_resolution, ()),
    ("screen.txt", report_screen, ()),
    ("placement.txt", report_placement, ()),
    ("measure-exc.txt", report_measure, (dx.COUPLE_EXC,)),
    ("measure-bus.txt", report_measure, (dx.COUPLE_BUS,)),
    ("measure-post.txt", report_measure, (dx.COUPLE_POST,)),
    ("cutoff.txt", report_cutoff, ()),
    ("continuous.txt", report_continuous, ()),
)


def write_records(outdir, k=dx.COUPLE_K):
    """Regenerate every committed record with ONE command.

    The records were produced by nine separate shell redirections, which is how
    a record set ends up half-stale: three of them were regenerated after a gate
    repair and the other six were not, so the same directory held two different
    instruments' numbers under one provenance line. One entry point cannot do
    that. The render memo makes it affordable -- the baselines are shared across
    every report."""
    import contextlib
    import io
    outdir = pathlib.Path(outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    for name, fn, args in RECORDS:
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            if fn is report_measure:
                fn(k, *args)
            elif fn in (report_screen, report_placement, report_continuous,
                        report_clipping):
                fn(k)
            else:
                fn()
        (outdir / name).write_text(buf.getvalue())
        print(f"wrote {outdir / name}  ({len(buf.getvalue().splitlines())} lines)")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--limits", action="store_true")
    ap.add_argument("--resolution", action="store_true")
    ap.add_argument("--screen", action="store_true")
    ap.add_argument("--placement", action="store_true")
    ap.add_argument("--measure", action="store_true")
    ap.add_argument("--cutoff", action="store_true")
    ap.add_argument("--continuous", action="store_true")
    ap.add_argument("--decay", action="store_true",
                    help="the decay gate's own precondition, per voice")
    ap.add_argument("--clipping", action="store_true",
                    help="s2's placement argument, under the clipping condition "
                         "that is the only one where it can be tested")
    ap.add_argument("-k", type=int, default=dx.COUPLE_K)
    ap.add_argument("--at", default=dx.COUPLE_BUS, choices=list(dx.COUPLE_PLACEMENTS))
    ap.add_argument("--records", metavar="DIR", nargs="?",
                    const=str(ROOT / "docs" / "dcblock"),
                    help="write EVERY report to its own file under DIR, so the "
                         "committed records are regenerated by one command "
                         "rather than by nine redirections")
    a = ap.parse_args(argv)
    if a.records:
        return write_records(pathlib.Path(a.records), a.k)
    if not any((a.limits, a.resolution, a.screen, a.placement, a.measure,
                a.cutoff, a.continuous, a.decay, a.clipping)):
        ap.print_help()
        return 2
    if a.limits:
        report_limits()
    if a.decay:
        report_decay()
    if a.clipping:
        report_clipping(a.k)
    if a.resolution:
        report_resolution()
    if a.screen:
        report_screen(a.k)
    if a.placement:
        report_placement(a.k)
    if a.measure:
        report_measure(a.k, a.at)
    if a.cutoff:
        report_cutoff(placement=a.at)
    if a.continuous:
        report_continuous(a.k, a.at)
    return 0


# ===========================================================================
# Self-tests: every estimator against ground truth it cannot have been fitted
# to, and every claim this file's reports make.
# ===========================================================================
def test_the_blocker_is_the_transfer_function_claimed():
    """H(z) = (1 - z^-1)/(1 - (1 - 2^-K) z^-1), measured, not asserted:
    an exact zero at DC and the declared corner, on the integer filter."""
    k = 10
    n = 1 << 16
    # DC in, nothing out -- and EXACTLY nothing, not "small": the fixed point
    # of the integer accumulator is reached and the output is identically 0.
    f = dx.DcBlockFx(k)
    y = np.array([f.step(10_000) for _ in range(n)])
    assert abs(y[-1]) == 0, y[-1]
    assert np.abs(y[n // 2:]).max() == 0, np.abs(y[n // 2:]).max()
    # The corner: the amplitude at fc must be 1/sqrt(2) of the passband.
    fc = SR / (2 * np.pi * (1 << k))
    t = np.arange(n) / SR
    def gain(hz):
        x = np.round(20000 * np.sin(2 * np.pi * hz * t)).astype(np.int64)
        o = dx.dc_block(x, k)[n // 2:]
        return float(np.abs(o).max()) / 20000.0
    assert abs(gain(fc) - 2 ** -0.5) < 0.03, (fc, gain(fc))
    assert abs(gain(1000.0) - 1.0) < 0.01, gain(1000.0)


def test_the_corner_is_the_machines_coupling_network():
    """`COUPLE_K` is read off C49 0.47 uF into R176 100k || R177 82k
    (reference 2's BD output buffer), not chosen. If either the constant or
    the reference value moves, this goes red."""
    r = 1.0 / (1.0 / 100e3 + 1.0 / 82e3)
    f_circuit = 1.0 / (2 * np.pi * 0.47e-6 * r)
    f_model = SR / (2 * np.pi * (1 << dx.COUPLE_K))
    assert abs(f_circuit - 7.52) < 0.05, f_circuit
    assert abs(f_model / f_circuit - 1.0) < 0.02, (f_model, f_circuit)


def test_coupling_off_is_the_block_bit_for_bit():
    """The prototype must not be able to change the shipped block by accident.
    Default construction and `couple='none'` are the same samples, exactly."""
    for v in ("CY", "RS", "BD"):
        a, _ = render(v, dx.COUPLE_OFF)
        n = int(RENDER_S * SR)
        d = dx.DrumsFx()
        dmix, body = d.play(dx.hit_writes([(LEAD_FRAMES, dx.SOUND_STOP[v], 1.0)],
                                          dx.kit_with_sounds(v)), n)
        g = dx.accent_reg(RENDER_GAIN)
        b = dx.output_fx(np.zeros(n), 0, dmix, g, body, g)
        assert np.array_equal(np.asarray(a), np.asarray(b)), v


def test_the_measurement_has_no_uncertainty_to_hide_behind():
    """Two renders of one configuration are bit-identical, so every delta this
    file prints is a real difference and not measurement scatter. DR 0015 asks
    for the measurement's own uncertainty; here it is exactly zero."""
    for couple in (dx.COUPLE_OFF, dx.COUPLE_BUS, dx.COUPLE_POST):
        a, na = render("CY", couple)
        b, nb = render("CY", couple)
        assert np.array_equal(a, b) and na == nb, couple


def test_band_energy_is_absolute_and_a_share_is_not():
    """The distinction the whole report rests on, on ground truth: remove the
    low tone from a two-tone signal and the HIGH tone's ABSOLUTE energy must
    not move, while its SHARE must rise. An instrument that cannot tell those
    apart would score a DC blocker as having synthesised harmonics."""
    n = int(SR * 0.5)
    t = np.arange(n) / SR
    lo = 8000 * np.sin(2 * np.pi * 100.0 * t)
    hi = 8000 * np.sin(2 * np.pi * 9000.0 * t)
    both = np.round(lo + hi).astype(np.int64)
    only = np.round(hi).astype(np.int64)
    a_both, a_only = band_energy_dbfs(both, 5000, 20000), band_energy_dbfs(only, 5000, 20000)
    s_both, s_only = band_share_pct(both, 5000, 20000), band_share_pct(only, 5000, 20000)
    assert abs(a_both - a_only) < 0.01, (a_both, a_only)      # absolute: unmoved
    assert s_only > s_both + 40.0, (s_both, s_only)           # share: doubled
    d = {"hf_5k_20k_db": a_only - a_both, "mid_700_5k_db": 0.0}
    assert share_rise_is_lf_removal(d)                        # and the rule says so


def test_the_trap_rule_does_not_fire_on_real_new_harmonics():
    """The other half of the control: when HF energy is genuinely ADDED, the
    rule must NOT call the share rise 'LF removal'. A rule that always fires
    would refuse every real improvement."""
    n = int(SR * 0.5)
    t = np.arange(n) / SR
    base = np.round(8000 * np.sin(2 * np.pi * 100.0 * t)).astype(np.int64)
    added = np.round(8000 * np.sin(2 * np.pi * 100.0 * t)
                     + 4000 * np.sin(2 * np.pi * 9000.0 * t)).astype(np.int64)
    d = {"hf_5k_20k_db": band_energy_dbfs(added, 5000, 20000) - band_energy_dbfs(base, 5000, 20000),
         "mid_700_5k_db": 0.0}
    assert d["hf_5k_20k_db"] > 10.0, d
    assert not share_rise_is_lf_removal(d)


def test_t20_recovers_a_known_decay_and_refuses_one_it_cannot_see():
    """The decay estimator against ground truth, and a number that was WRONG
    BEFORE IT WAS RIGHT: this assertion first shipped with `want = tau *
    ln(10)/2`, which is the -10 dB point, and the estimator was blamed for the
    27 ms gap. The backward energy integral of an amplitude decay exp(-t/tau)
    is exp(-2t/tau), so -20 dB of ENERGY is at

        t = tau * ln(100) / 2 = 2.3026 * tau,

    twice what was asserted. The estimator was right and the ground truth was
    not -- CLAUDE.md's "verify the method before the number", from the inside."""
    n = int(SR * 2.0)
    t = np.arange(n) / SR
    for tau in (0.02, 0.05, 0.2):
        x = np.round(20000 * np.sin(2 * np.pi * 300 * t) * np.exp(-t / tau)).astype(np.int64)
        want = 1e3 * tau * np.log(100.0) / 2.0
        got = t20_ms(x)
        assert abs(got - want) < 0.06 * want + 2.5, (tau, want, got)
    flat = np.full(int(SR * 0.1), 10000, dtype=np.int64)
    assert np.isnan(t20_ms(flat))            # REFUSED, not reported as 0


def test_attack_and_centroid_recover_ground_truth():
    """Both estimators against synthetic truth they cannot have been fitted to,
    and across three carriers rather than one -- the original single 400 Hz case
    could not have caught the lobe-rank defect that `attack_samples` was
    repaired for, because at 400 Hz the lobes are 1.25 ms apart."""
    n = int(SR * 0.3)
    t = np.arange(n) / SR
    for f0, att, tol in ((400, 0.005, 0.002), (2000, 0.005, 0.002),
                         (50, 0.020, 0.004)):
        env = np.minimum(t / att, 1.0) * np.exp(-t / 0.10)
        x = np.round(20000 * np.sin(2 * np.pi * f0 * t) * env).astype(np.int64)
        got = attack_samples(x)
        assert abs(got - att * SR) < tol * SR, (f0, att, got)
    tone = np.round(20000 * np.sin(2 * np.pi * 1234.0 * t)).astype(np.int64)
    assert abs(centroid_hz(tone) - 1234.0) < 12.0, centroid_hz(tone)
    assert abs(centroid_global_hz(tone) - 1234.0) < 12.0


def test_the_attack_estimator_is_blind_to_a_lobe_rank_flip():
    """THE REPAIR, on ground truth rather than on the BD. Two equal lobes a
    half-period apart, then a DC offset too small to hear that makes the second
    one win. `argmax(|x|)` jumps a half period; the first arrival within
    `ATTACK_TOL_DB` does not move.

    THE CONDITION THIS NEEDS, and it is the condition the BD is actually in:
    several consecutive lobes within the tolerance of the peak. Where exactly
    two lobes are tied and nothing else is near them, BOTH estimators flip and
    neither can do better -- which is why `peak_margin_db` exists as a
    precondition and is reported, rather than the repair being claimed as
    universal. A 50 Hz carrier decaying with tau = 10 s puts twenty half-cycles
    inside 1 dB, as the BD's rail-limited body does.

    The offset (0.3 % of peak) is smaller than the one the real BD carries
    (-45 counts on a 7400-count peak, 0.6 %), so the control is harder than the
    case it was written for."""
    n = int(SR * 0.3)
    t = np.arange(n) / SR
    x = 20000 * np.sin(2 * np.pi * 50.0 * t) * np.minimum(t / 0.020, 1.0) \
        * np.exp(-t / 10.0)
    a = np.round(x).astype(np.int64)
    b = np.round(x - 60).astype(np.int64)          # 0.3 % of peak
    assert peak_margin_db(a) < 0.1, peak_margin_db(a)     # the lobes really are tied
    old = abs(int(np.argmax(np.abs(b))) - int(np.argmax(np.abs(a))))
    assert old > 200, old                                 # argmax jumps a half period
    assert abs(attack_samples(b) - attack_samples(a)) <= 4, \
        (attack_samples(a), attack_samples(b))


def test_the_qualified_centroid_is_the_one_a_dc_blocker_cannot_flatter():
    """Closed form, independent of this model, and the same claim
    `dc_centroid_gate_qualification.py` makes: a sub-audio contaminant moves a
    GLOBAL centroid by a double-digit percentage while the audible content does
    not move at all. The gate must be read on the second quantity."""
    n = int(SR * 0.5)
    t = np.arange(n) / SR
    tone = 8000 * np.sin(2 * np.pi * 1000.0 * t)
    cont = tone + 8000 * np.sin(2 * np.pi * 10.0 * t)
    g0, g1 = centroid_global_hz(cont), centroid_global_hz(tone)
    q0, q1 = centroid_hz(cont), centroid_hz(tone)
    assert 100.0 * (g1 / g0 - 1.0) > 10.0, (g0, g1)
    assert abs(100.0 * (q1 / q0 - 1.0)) < 0.01, (q0, q1)


def test_the_attenuation_floor_is_the_dc_fraction_on_closed_form_signals():
    """The screen against ground truth it cannot have been fitted to: signals
    whose sub-20 Hz energy is split between the f = 0 bin and the skirt in a
    ratio set by construction.

    A constant `c` plus a 10 Hz tone of amplitude `A` over an integer number of
    periods has DC energy proportional to c^2 and one-sided skirt energy to
    A^2/2, so phi = c^2 / (c^2 + A^2/2) exactly. The floor must come out at
    -10 log10(1 - phi), and a settled blocker whose corner is far below the
    skirt must deliver essentially exactly that -- the floor is not merely a
    bound in its own derivation's regime, it is the answer.

    THE REGIME MATTERS AND IS WHY THIS CASE IS NOT THE VOICES. 4 s of clip with
    the last 2 s measured, so the K = 13 pole (171 ms) is settled to 12 time
    constants and the 10 Hz skirt is attenuated by only 0.04 dB. On a drum hit
    neither holds, which is what opens the bracket the next test checks."""
    n = int(SR * 4.0)
    half = n // 2
    t = np.arange(n) / SR
    for c, amp in ((8000.0, 1000.0), (3000.0, 3000.0), (300.0, 8000.0)):
        x = c + amp * np.sin(2 * np.pi * 10.0 * t)
        want = c ** 2 / (c ** 2 + amp ** 2 / 2.0)
        got = dc_fraction(x[half:])
        assert abs(got - want) < 1e-4, (c, amp, want, got)
        assert abs(attenuation_floor_db(got) + 10.0 * np.log10(1 - got)) < 1e-9
        y = dx.dc_block(np.round(x).astype(np.int64), 13)[half:]
        a = band_energy_dbfs(np.round(x[half:]).astype(np.int64), *SUB20)
        b = band_energy_dbfs(y, *SUB20)
        assert abs((a - b) - attenuation_floor_db(got)) < 0.2, \
            (c, amp, a - b, attenuation_floor_db(got))


def test_the_screen_brackets_the_rendered_attenuation_on_every_voice():
    """**What makes the screen evidence rather than a restatement.** Both bounds
    are computed from the UNCOUPLED spectrum with no blocker anywhere; the
    measurement is the integer filter run inside the block. On all five voices
    the measurement lands INSIDE the bracket:

      voice   floor    measured   steady state
      CY       9.51     14.64        17.61
      RS       0.19      2.78         2.79
      BD       0.45      2.60         2.70
      HT       0.10      2.65         2.66
      CH       1.88      5.03        11.69

    Five independent two-sided checks of a model against an implementation
    neither side was fitted to. The width of the bracket is itself informative:
    it is the filter's own start-up tail, which is largest exactly where the
    standing offset is largest (CY, CH).

    THIS TEST WAS WRONG BEFORE IT WAS RIGHT. It first asserted equality to
    1.2 dB and went red at 5.73 dB on the CY, because the steady-state figure
    cannot see the causal filter's opening transient. The assertion was the
    error, not the probe."""
    for v in SUBJECTS + CONTROLS:
        b, nb = render(v, dx.COUPLE_OFF)
        c, nc = render(v, dx.COUPLE_BUS, dx.COUPLE_K)
        lo = attenuation_floor_db(dc_fraction(b))
        hi = steadystate_sub20_attenuation_db(b, dx.COUPLE_K)
        meas = measure(b, nb)["sub20_dbfs"] - measure(c, nc)["sub20_dbfs"]
        assert lo - 0.6 <= meas <= hi + 0.6, (v, lo, meas, hi)
        assert hi > lo, (v, lo, hi)              # the bracket is not degenerate


def test_the_screen_separates_the_two_subjects_and_says_why():
    """THE FINDING, as an assertion. The CY's sub-20 Hz energy sits below the
    blocker's corner and the RS's straddles it, so no single corner serves both:

      CY  beta = 0.983  steady 14.93 dB   measured -14.85 dB   6 dB reachable
      RS  beta = 0.380  steady  2.71 dB   measured  -2.70 dB   6 dB NOT reachable
                                                               at ANY K >= 9

    The CY clears the 6 dB requirement at the circuit's own corner. To reach it
    the RS needs the SKIRT attenuated, and the OPTIMISTIC bound says no corner
    at or below the circuit's own (K >= 10, fc <= 7.46 Hz) gets there. Only
    K = 8 (29.8 Hz) does, at which --cutoff measures the RS breaking four of its
    preservation properties -- so the trade-off has no satisfiable point, which
    is the finding rather than a tuning failure.

    READ ON beta, NOT phi, AND THAT IS A CORRECTION. This test used to assert
    phi > 0.75 on the CY and it went RED when the clip length changed, at
    0.3907: the f = 0 bin is 1/T wide, so phi is a property of the ANALYSIS
    WINDOW as much as of the voice. The conclusion was unaffected and the
    statistic was wrong -- see `sub20_below_fc_frac`.

    If a future change moved the RS's sub-20 energy below the corner -- or the
    CY's above it -- this goes red, which is what it is for. The 6 dB target is
    not the claim; the SEPARATION is."""
    cy, _ = render("CY", dx.COUPLE_OFF)
    rs, _ = render("RS", dx.COUPLE_OFF)
    assert sub20_below_fc_frac(cy) > 0.90, sub20_below_fc_frac(cy)
    assert sub20_below_fc_frac(rs) < 0.50, sub20_below_fc_frac(rs)
    # and the discriminator is window-stable where phi is not
    cy_short, _ = render("CY", dx.COUPLE_OFF, seconds=0.60)
    assert abs(sub20_below_fc_frac(cy_short) - sub20_below_fc_frac(cy)) < 0.05
    assert abs(dc_fraction(cy_short) - dc_fraction(cy)) > 0.20     # phi is not
    # the optimistic bound reaches the target on the CY and cannot on the RS
    assert steadystate_sub20_attenuation_db(cy, dx.COUPLE_K) > IMPROVE_SUB20_DB
    assert steadystate_sub20_attenuation_db(rs, dx.COUPLE_K) < 0.5 * IMPROVE_SUB20_DB
    assert attenuation_floor_db(dc_fraction(rs)) < 0.5     # phi still bounds below
    assert required_k(cy) is not None
    # No corner at or below the circuit's own reaches it even optimistically:
    assert required_k(rs, ks=range(16, 9, -1)) is None, required_k(rs, ks=range(16, 9, -1))
    # and the one that could (K = 8) costs the RS its preservation, measured:
    b, nb = render("RS", dx.COUPLE_OFF)
    c, nc = render("RS", dx.COUPLE_BUS, 8)
    bad, _ = preserved("RS", deltas(measure(b, nb), measure(c, nc)),
                       resolution_of(b, nb), _refused_props("RS"))
    assert len(bad) >= 2, bad


def test_a_bus_blocker_is_the_superposition_of_per_path_blockers():
    """The hardware claim: a linear filter commutes with a sum, so ONE blocker
    on a bus is what a blocker on every path summing into it would be. Two
    registers, not twenty-three. Checked on the float filter to isolate the
    claim from integer truncation, and the truncation cost is measured beside
    it."""
    rng = np.random.default_rng(0)
    k = 10
    a = rng.integers(-5000, 5000, 4096)
    b = rng.integers(-5000, 5000, 4096)
    one = dx.dc_block(a + b, k)
    two = dx.dc_block(a, k) + dx.dc_block(b, k)
    err = float(np.abs(one - two).max())
    assert err <= 2.0, err                    # <= 1 LSB per blocker, from the shift
    assert float(np.abs(one).max()) > 1000.0


def test_the_coupled_switch_step_does_not_exceed_the_uncoupled_by_the_truncation_bound():
    """#165 s5, asserted rather than printed. The rule is not 'never exceeds':
    CP->MA exceeds by ~1 LSB, which is the filter's own declared truncation."""
    rows = _switch_steps(dx.COUPLE_K, dx.COUPLE_BUS)
    assert [(a, b) for a, b, _, _ in rows] == list(dx.PAIRS)
    for a_, b_, off, on in rows:
        assert (on - off) * FS <= SWITCH_STEP_TOL_LSB, (a_, b_, off, on)


def test_the_coupling_state_survives_a_hit_and_a_retune_but_not_a_reset():
    """#165 s5: a capacitor does not know a stop fired. The accumulator must
    carry across hits and across a panel switch, and only A_RESET may clear
    it -- a blocker that reset on every hit would re-emit the step it exists
    to remove."""
    d = dx.DrumsFx(couple=dx.COUPLE_BUS)
    d.play(dx.hit_writes([(10, dx.SOUND_STOP["RS"], 1.0)], dx.kit_with_sounds("RS")), 4000)
    assert d.dc_dmix.acc != 0
    held = d.dc_dmix.acc
    for a, v in dx.preset_writes("CL"):        # a retune: registers, not silence
        d.write(a, v)
    assert d.dc_dmix.acc == held
    d.write(dx.A_RESET, 0)
    assert d.dc_dmix.acc == 0


def test_the_accumulator_width_is_declared_and_not_exceeded():
    """What the RTL has to build. acc ~ x * 2^K, so a 22-bit mix bus needs
    22 + K + 1 bits; the model counts the widest it actually saw so the RTL
    word is measured rather than guessed.

    THE LOWER BOUND WAS WRONG BEFORE IT WAS RIGHT, and that is the interesting
    half. It read `acc_bits > MIX_BITS` -- the accumulator must be wider than
    the mix BUS -- and went red at `22 > 22`. The bus is 22 bits wide and the
    widest sample that actually reaches the blocker is 17, so the assertion was
    comparing the accumulator against a width the data never occupies. What the
    shift has to be measured against is the width that ARRIVES, which is why
    `DcBlockFx` now counts `in_bits`.

    AND THE FIRST REPLACEMENT WAS ALSO WRONG: `acc_bits >= in_bits + K - 1`
    went red at `22 >= 26`, because `acc` settles at the input's MEAN times
    2^K, not its PEAK times 2^K, and a drum hit's mean is far below its peak.
    `in_bits + K + 1` is therefore an upper bound on what a sustained rail
    would need and is not approached by any transient; the lower bound that is
    actually true of the design is that the accumulator is wider than its own
    input, which is what makes the shift do anything. Two wrong-then-right
    bounds on one assertion; both are recorded here rather than tidied away."""
    d = dx.DrumsFx(couple=dx.COUPLE_BUS)
    for v in ("BD", "SD", "CY", "RS", "OH"):
        d.write(dx.A_RESET, 0)
        d.play(dx.hit_writes([(10, dx.SOUND_STOP[v], 2.0)], dx.kit_with_sounds(v)),
               int(0.5 * SR))
    assert d.dc_dmix.acc_bits <= dx.MIX_BITS + dx.COUPLE_K + 1, d.dc_dmix.acc_bits
    assert d.dc_body.acc_bits <= dx.BODY_BITS + dx.COUPLE_K + 1, d.dc_body.acc_bits
    for f, name in ((d.dc_dmix, "dmix"), (d.dc_body, "body")):
        assert f.in_bits > 0, name
        assert f.acc_bits > f.in_bits, (name, f.acc_bits, f.in_bits)
        assert f.acc_bits <= f.in_bits + dx.COUPLE_K + 1, (name, f.acc_bits, f.in_bits)


if __name__ == "__main__":
    raise SystemExit(main())
