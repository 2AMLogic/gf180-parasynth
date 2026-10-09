#!/usr/bin/env python3
"""Residual drum DC coupling: which non-cymbal voices carry a defect? (#152)

    python3 tools/probes/residual_dc.py --declared      the limits + frozen conditions
    python3 tools/probes/residual_dc.py --controls      MOVED/BLIND: properties x defects
    python3 tools/probes/residual_dc.py --reference     what an external recording can say
    python3 tools/probes/residual_dc.py --screen  [--condition dev|confirm] [--voices A,B]
    python3 tools/probes/residual_dc.py --detail  [--condition dev|confirm]   CH / RS / BD / HT
    python3 tools/probes/residual_dc.py --batch-spec    the tools/run_all.py job list
    python3 -m pytest tools/probes/test_residual_dc.py -q

SCOPE. Diagnostic code and evidence only. No production enable, no RTL, no
image change. #510/#552/#553 own cymbal carry-through, #556 owns MA/RS
brightness, #220/#350 own modal arithmetic, #558/#591-#593 own tom/conga.

THE QUESTION IS TWO QUESTIONS, and the apparatus keeps them apart.

  1. WHAT KIND of sub-20 Hz energy does a voice carry? A STANDING OFFSET (a
     level that persists for many cycles of the corner) or the SKIRT of a
     finite burst (the Fourier content of a pulse shorter than ~1/20 Hz)?
     A DC blocker removes the first and cannot remove the second.
  2. IS IT A SOUND DEFECT? That needs an external reference whose capture
     chain could have carried DC. Model self-comparison cannot establish it,
     and an AC-coupled capture cannot prove DC absent (REFUSED, `--reference`).

TWO INDEPENDENT STATISTICS decide question 1; the class is read off BOTH and
they are required to agree, otherwise the class is MIXED and is REFUSED as a
class (a result, not a fault):

  beta  share of the sub-20 Hz energy BELOW the blocker's corner fc.
        Corner-relative: "what could a blocker at fc remove".
  S     spectral flatness of the 0-20 Hz band, `phi * (2 n - 1)` with phi the
        f = 0 bin's share and n the number of bins below 20 Hz. A burst much
        shorter than 1/(20 Hz) has a FLAT spectrum there, S = 1 exactly; a
        standing offset puts everything in one bin, S = 2n-1 (79 at a 2 s
        window). Burst-relative, and independent of fc.

Both are read on a FIXED onset-aligned analysis window (`ANALYSIS_S`), so
leading silence and trailing padding cannot move them: a statistic that
depends on how long you watched was the CY's phi (0.888 -> 0.391 when the clip
went 0.6 -> 2.4 s, dc_blocker.py), and is the reason this one is windowed
by declaration.

ABSOLUTE band energy is reported beside every share. Removing LF raises an
HF share without adding HF (`share_change_kind`).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import pathlib
import subprocess
import sys
from dataclasses import dataclass

import numpy as np

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "model"))

FS = 32768.0                       # int16 full scale
LSB = 1.0 / FS

# ===========================================================================
# DECLARED BEFORE ANY MODEL OR CORPUS DATA WAS READ (git history carries the
# ordering: this block lands in the commit that adds the module, with the
# estimator still a stub and the tests red).
# ===========================================================================
ANALYSIS_S = 2.0          # fixed onset-aligned window; clips shorter -> REFUSED
STABILITY_S = 1.0         # beta/S are recomputed on the first 1.0 s: must agree
ONSET_FRAC = 0.02         # onset = first |x| > 2 % of peak (as dc_blocker.onset_index)
# Class limits. Justified from the PRIOR dc_blocker.py --screen record, not from
# anything measured here: its beta separated offset (CY 0.98, CH 0.97) from
# skirt (RS 0.38, HT 0.36, BD 0.45) with a gap 0.45..0.97; the limits sit in the
# gap with margin for its own window drift (BD moved 0.375 -> 0.455 = 0.08).
BETA_OFFSET_MIN = 0.80
BETA_SKIRT_MAX = 0.60
BETA_STABLE_TOL = 0.10    # |beta(1.0 s) - beta(2.0 s)| above this -> REFUSED
# S limits: a flat spectrum is S = 1; allow 2x for a burst that is not
# infinitely short. A standing offset is S = 2n-1 = 79; 8 is ~10 % of that.
S_SKIRT_MAX = 2.0
S_OFFSET_MIN = 8.0
EXTENT_FLOOR_FRAC = 0.01  # >= 20 Hz envelope: still "sounding" above 1 % of peak
EXTENT_MAX_FRAC = 0.90    # sounding past 90 % of the window -> window-dependent
MIN_PEAK_LSB = 16.0       # a clip peaking below this is silence for this purpose
MIN_BINS_BELOW_FC = 4     # band resolution at use
MIN_BINS_SUB20 = 10
RES_MARGIN_DB = 6.0       # sub-20 energy must clear the quantisation floor by this
OFFSET_PEAK_FRAC = 0.02   # a "standing offset defect" needs |window mean| >= 2 % of peak
HF_ADDED_DB = 3.0         # absolute 5-20 kHz rise that counts as synthesis
HF_FLAT_DB = 0.2          # ... and below which a share rise is LF removal
CH_REST_TOL_DB = 1.0      # H_CH: float causal-from-rest prediction vs measured
CH_SHORTFALL_MIN_DB = 3.0 # H_CH is only interesting if steady - measured >= this
WINDOW_EDGES_MS = (0.0, 10.0, 50.0, 200.0, 1000.0, 2000.0)   # onset-relative

BANDS = {"sub20": (0.0, 20.0), "body": (20.0, 700.0), "mid": (700.0, 5000.0),
         "hf": (5000.0, 20000.0)}

# Frozen conditions. `confirm` is NOT inspected until the limits above are
# committed; it changes gain, velocity AND render length, and it must not
# change a verdict. Limits are not revisited after it is read.
CONDITIONS = {
    "dev":     dict(seconds=2.40, gain=0.45, vel=1.00),
    "confirm": dict(seconds=4.80, gain=0.75, vel=0.60),
}
SUBJECTS = ("CH", "RS")
CONTROLS = ("BD", "HT")
SCREEN_VOICES = ("BD", "SD", "LT", "LC", "MT", "MC", "HT", "HC", "RS", "CL",
                 "CP", "MA", "CB", "OH", "CH")          # every voice except CY
LEAD_FRAMES = 10


# ===========================================================================
# Estimators. Pure functions of (samples, sample rate, corner in Hz).
# ===========================================================================
def onesided(x, sr):
    """(power, freqs): one-sided power spectrum, every bin but DC/Nyquist
    doubled, so Parseval is exact. Rectangular window, nothing normalised."""
    x = np.asarray(x, float)
    p = np.abs(np.fft.rfft(x)) ** 2
    p[1:(-1 if len(x) % 2 == 0 else None)] *= 2.0
    return p, np.fft.rfftfreq(len(x), 1.0 / sr)


def band_db(x, sr, lo, hi):
    """ABSOLUTE band energy, dB re full scale (mean-square form). Falls when
    energy is removed; does not rise when other energy is."""
    x = np.asarray(x, float) / FS
    p, f = onesided(x, sr)
    p = p / len(x) ** 2
    return 10.0 * math.log10(float(p[(f >= lo) & (f < hi)].sum()) + 1e-30)


def band_share_pct(x, sr, lo, hi):
    """NORMALISED. Never a verdict."""
    p, f = onesided(np.asarray(x, float), sr)
    return 100.0 * float(p[(f >= lo) & (f < hi)].sum()) / (float(p.sum()) + 1e-30)


def onset_index(x, frac=ONSET_FRAC):
    a = np.abs(np.asarray(x, float))
    pk = float(a.max()) if a.size else 0.0
    return int(np.argmax(a > frac * pk)) if pk > 0 else 0


def quantisation_floor_db(sr, hi=20.0):
    """dB re FS that white 16-bit quantisation noise puts in [0, hi) Hz, in
    `band_db`'s units: (LSB^2/12) * hi / (sr/2)."""
    return 10.0 * math.log10((LSB ** 2 / 12.0) * hi / (sr / 2.0))


def corner_hz(k, sr):
    """The implemented blocker's corner for pole 1 - 2^-K (dc_blocker.py)."""
    return sr / (2.0 * math.pi * (1 << int(k)))


class Estimator:
    """The measuring instrument as one replaceable object, so the injected-bug
    controls can swap a single decision and run the SAME fixtures."""

    def window(self, x, sr):
        """Onset-aligned fixed window. Returns (segment, onset) or raises
        Refused when the clip cannot supply `ANALYSIS_S` after its onset."""
        x = np.asarray(x, float)
        i0 = onset_index(x)
        n = int(round(ANALYSIS_S * sr))
        if len(x) - i0 < n:
            raise Refused(f"only {(len(x) - i0) / sr:.2f} s after the onset, "
                          f"{ANALYSIS_S:.2f} s declared")
        return x[i0:i0 + n], i0

    def prep(self, seg):
        return seg

    def stats(self, x, sr, fc):
        seg, i0 = self.window(x, sr)
        seg = self.prep(seg)
        return _stats_of(seg, sr, fc, i0)

    def band(self, x, sr, lo, hi):
        return band_db(x, sr, lo, hi)


def _stats_of(seg, sr, fc, i0=0):
    nan = float('nan')  # STUB (red start)
    return dict(n=len(seg), n20=int((np.fft.rfftfreq(len(seg), 1.0/sr) < 20).sum()), nfc=int((np.fft.rfftfreq(len(seg), 1.0/sr) < fc).sum()), phi=nan, flat=nan, beta=nan, sub20_db=nan, onset=i0, peak_lsb=nan)
    p, f = onesided(seg, sr)
    m20 = f < 20.0
    sub = float(p[m20].sum())
    n20 = int(m20.sum())
    nfc = int((f < fc).sum())
    phi = float(p[0]) / (sub + 1e-30)
    beta = float(p[f < fc].sum()) / (sub + 1e-30)
    return dict(n=len(seg), n20=n20, nfc=nfc, phi=phi, flat=phi * (2 * n20 - 1),
                beta=beta, sub20_db=band_db(seg, sr, 0.0, 20.0), onset=i0,
                peak_lsb=float(np.abs(seg).max()))


class Refused(Exception):
    """The apparatus cannot answer. A first-class outcome, not a failure."""


def sounding_extent(seg, sr):
    """Samples from the window start to the last point the >= 20 Hz content
    exceeds 1 % of its own peak. Brick-wall in the same FFT the bands are read
    from (stateless: a causal filter would add the very tail under test)."""
    X = np.fft.rfft(seg)
    X[np.fft.rfftfreq(len(seg), 1.0 / sr) < 20.0] = 0.0
    a = np.abs(np.fft.irfft(X, n=len(seg)))
    pk = float(a.max())
    if pk <= 0:
        return 0
    idx = np.nonzero(a > EXTENT_FLOOR_FRAC * pk)[0]
    return int(idx[-1]) + 1


REAL = Estimator()


def classify(x, sr, fc, est: Estimator = REAL):
    """(label, detail). label is one of OFFSET, SKIRT, MIXED, REFUSED.
    MIXED = the two independent statistics disagree: reported, not forced."""
    x = np.asarray(x, float)
    if not np.all(np.isfinite(x)):
        return "REFUSED", dict(reason="non-finite samples")
    if float(np.abs(x).max()) < MIN_PEAK_LSB:
        return "REFUSED", dict(reason="silent / below 16 LSB peak")
    try:
        st = est.stats(x, sr, fc)
    except Refused as e:
        return "REFUSED", dict(reason=str(e))
    seg, _ = est.window(x, sr)
    if st["nfc"] < MIN_BINS_BELOW_FC or st["n20"] < MIN_BINS_SUB20:
        return "REFUSED", dict(reason=f"band resolution: {st['nfc']} bins below fc, "
                                      f"{st['n20']} below 20 Hz", **st)
    ext = sounding_extent(seg, sr)
    if ext > EXTENT_MAX_FRAC * len(seg):
        return "REFUSED", dict(reason=f"still sounding at {ext / sr:.2f} s of a "
                                      f"{len(seg) / sr:.2f} s window", **st)
    floor = quantisation_floor_db(sr)
    st["margin_db"] = st["sub20_db"] - floor
    if st["margin_db"] < RES_MARGIN_DB:
        return "REFUSED", dict(reason=f"sub-20 Hz energy {st['margin_db']:+.1f} dB over the "
                                      f"quantisation floor, {RES_MARGIN_DB} required", **st)
    # stability: the same statistic on the first STABILITY_S of the window
    h = _stats_of(est.prep(seg[:int(round(STABILITY_S * sr))]), sr, fc)
    st["beta_half"], st["flat_half"] = h["beta"], h["flat"]
    if abs(h["beta"] - st["beta"]) > BETA_STABLE_TOL:
        return "REFUSED", dict(reason=f"beta moves {h['beta']:.2f} -> {st['beta']:.2f} with the window", **st)
    off = st["beta"] >= BETA_OFFSET_MIN and st["flat"] >= S_OFFSET_MIN
    skirt = st["beta"] <= BETA_SKIRT_MAX and st["flat"] <= S_SKIRT_MAX
    st["ext_ms"] = 1e3 * ext / sr
    return ("OFFSET" if off else "SKIRT" if skirt else "MIXED"), st


def window_means(x, sr, edges_ms=WINDOW_EDGES_MS):
    """Mean sample value (LSB) in onset-relative windows. Leading silence is
    cut at the onset; a window past the clip's end is nan, never zero."""
    x = np.asarray(x, float)
    i0 = onset_index(x)
    out = []
    for a, b in zip(edges_ms[:-1], edges_ms[1:]):
        s, e = i0 + int(a * sr / 1e3), i0 + int(b * sr / 1e3)
        out.append(float(x[s:e].mean()) if e <= len(x) and e > s else float("nan"))
    return out


def share_change_kind(base, cand, sr, est=None):
    """Read a candidate against its baseline: LF_REMOVAL, HF_ADDED or OTHER.
    A share rise with flat ABSOLUTE HF is LF removal and not an improvement.
    `est.band` is the HF reading (replaceable by an injected defect)."""
    band = (est or REAL).band
    d_hf = band(cand, sr, *BANDS["hf"]) - band(base, sr, *BANDS["hf"])
    d_sh = band_share_pct(cand, sr, *BANDS["hf"]) - band_share_pct(base, sr, *BANDS["hf"])
    if d_hf >= HF_ADDED_DB:
        return "HF_ADDED", d_hf, d_sh
    if d_sh > 0 and abs(d_hf) <= HF_FLAT_DB:
        return "LF_REMOVAL", d_hf, d_sh
    return "OTHER", d_hf, d_sh


# ===========================================================================
# Independently known synthetic signals. The answer to "offset or skirt" is
# fixed by CONSTRUCTION, never by running the estimator on the model.
# ===========================================================================
def _t(sr, seconds):
    return np.arange(int(round(seconds * sr))) / sr


def _q(x):
    """Quantise to int16 the way the block's output does."""
    return np.clip(np.round(np.asarray(x, float) * FS), -32768, 32767)


def tone_burst(sr, f0=300.0, tau=0.03, amp=0.3, seconds=ANALYSIS_S + 0.4):
    """Zero-mean decaying sine: audible content, no DC by construction."""
    t = _t(sr, seconds)
    return amp * np.exp(-t / tau) * np.sin(2 * np.pi * f0 * t)


def fx_offset(sr=48000, off=0.08):
    """GROUND TRUTH: a STANDING OFFSET. Zero-mean ring plus a constant that
    persists for the whole clip. Class OFFSET by construction."""
    x = tone_burst(sr)
    return _q(x + off)


def fx_skirt(sr=48000, amp=0.3, dur=0.010):
    """GROUND TRUTH: a FINITE BURST with non-zero mean (a 10 ms half-sine
    pulse) -- sub-20 Hz energy exists and is entirely the pulse's skirt.
    Its spectrum is flat to 20 Hz (|X(20)|/|X(0)| = 0.99). Class SKIRT."""
    n = int(round(ANALYSIS_S * sr)) + int(0.4 * sr)
    x = np.zeros(n)
    m = int(round(dur * sr))
    x[:m] = amp * np.sin(np.pi * np.arange(m) / m)
    return _q(x)


def fx_zero_mean(sr=48000, amp=0.3):
    """GROUND TRUTH: zero-mean short burst (Hann-windowed integer-cycle 2 kHz
    sine). Nothing for a blocker to do and nothing to classify: the answer is
    SKIRT or REFUSED, and never OFFSET."""
    n = int(round(ANALYSIS_S * sr)) + int(0.4 * sr)
    m = int(round(0.010 * sr))
    t = np.arange(m) / sr
    x = np.zeros(n)
    x[:m] = amp * np.hanning(m) * np.sin(2 * np.pi * 2000.0 * t)
    return _q(x)


def add_hf(x, sr=48000, amp=0.05, f0=9000.0, tau=0.05):
    """GENUINELY ADDED high frequency: a decaying 9 kHz ring."""
    t = np.arange(len(x)) / sr
    return _q(np.asarray(x, float) / FS + amp * np.exp(-t / tau) * np.sin(2 * np.pi * f0 * t))


def remove_offset(x, off_lsb):
    """The LF-removal counterpart: same signal, standing offset taken out."""
    return np.asarray(x, float) - off_lsb


# ===========================================================================
# Injected defects: each swaps ONE decision of the instrument. The suite must
# turn red on every one (`controls_matrix`). The first is the exact wrong
# conditioner this issue names (whole-clip-mean subtraction, the old
# `excitation_energy.condition_meansub`).
# ===========================================================================
class MeanSubtract(Estimator):
    """Subtracts the window's mean first: erases the very offset it classifies."""
    name = "whole-clip-mean subtraction"

    def prep(self, seg):
        return seg - seg.mean()


class ClipStartWindow(Estimator):
    """Windows from the clip start, not the onset: leading silence leaks in."""
    name = "clip-start window (not onset-relative)"

    def window(self, x, sr):
        x = np.asarray(x, float)
        n = int(round(ANALYSIS_S * sr))
        if len(x) < n:
            raise Refused("short")
        return x[:n], 0


class WholeClipWindow(Estimator):
    """Whole clip, not a fixed window: trailing padding changes the answer."""
    name = "whole-clip window (padding-dependent)"

    def window(self, x, sr):
        x = np.asarray(x, float)
        i0 = onset_index(x)
        return x[i0:], i0


class FixedRate(Estimator):
    """Assumes 48 kHz whatever the clip's rate: bins are mislabelled."""
    name = "fixed 48 kHz bin map"

    def stats(self, x, sr, fc):
        return super().stats(x, 48000, fc)


class ShareBand(Estimator):
    """Reads the HF band as a SHARE: LF removal looks like HF synthesis."""
    name = "HF as normalised share"

    def band(self, x, sr, lo, hi):
        return 10.0 * math.log10(band_share_pct(x, sr, lo, hi) / 100.0 + 1e-30)


MUTANTS = (MeanSubtract, ClipStartWindow, WholeClipWindow, FixedRate, ShareBand)


# ===========================================================================
# Properties: each is a predicate on an Estimator, true for the real one.
# `controls_matrix` runs every property against every injected defect.
# VALIDATED DOMAIN of each: 48 kHz (and 96 kHz for P_rate), corner 7.46 Hz,
# clips with >= ANALYSIS_S after onset, int16-quantised, amplitude >= 0.05 FS.
# ===========================================================================
FC = 7.46


def _cls(est, x, sr=48000):
    return classify(x, sr, FC, est)[0]


def p_offset_is_offset(est):
    return _cls(est, fx_offset()) == "OFFSET"


def p_burst_is_skirt(est):
    return _cls(est, fx_skirt()) == "SKIRT"


def p_zero_mean_never_offset(est):
    return _cls(est, fx_zero_mean()) in ("SKIRT", "REFUSED")


def p_added_hf_not_confused_with_lf_removal(est):
    """HF really added -> HF_ADDED. Offset removed -> LF_REMOVAL (share rises,
    absolute HF flat). Both must be read correctly by the same rule."""
    base = fx_offset()
    k_add = share_change_kind(base, add_hf(base), 48000, est)[0]
    k_rem = share_change_kind(base, remove_offset(base, 0.08 * FS), 48000, est)[0]
    return k_add == "HF_ADDED" and k_rem == "LF_REMOVAL"


def p_lead_silence_invariant(est):
    for mk in (fx_offset, fx_skirt):
        x = mk()
        y = np.concatenate([np.zeros(int(0.5 * 48000)), x])
        a, b = est.stats(x, 48000, FC), est.stats(y, 48000, FC)
        if not all(math.isclose(a[k], b[k], rel_tol=1e-9, abs_tol=1e-9) for k in ("phi", "beta", "flat")):
            return False
    return True


def p_pad_invariant(est):
    for mk in (fx_offset, fx_skirt):
        x = mk()
        y = np.concatenate([x, np.zeros(int(5.0 * 48000))])
        a, b = est.stats(x, 48000, FC), est.stats(y, 48000, FC)
        if not all(math.isclose(a[k], b[k], rel_tol=1e-9, abs_tol=1e-9) for k in ("phi", "beta", "flat")):
            return False
    return True


def p_polarity_invariant(est):
    return all(_cls(est, s * mk()) == _cls(est, mk()) for mk in (fx_offset, fx_skirt) for s in (-1.0,)) \
        and _cls(est, -fx_offset()) == "OFFSET"


def p_scale_invariant(est):
    for mk, want in ((fx_offset, "OFFSET"), (fx_skirt, "SKIRT")):
        for g in (0.5, 2.0):
            x = mk()
            if _cls(est, _q(x / FS * g)) != want:
                return False
    return True


def p_rate_invariant(est):
    """Same analogue signal at 96 kHz, corner given in Hz: same class."""
    return _cls(est, fx_offset(96000), 96000) == "OFFSET" and _cls(est, fx_skirt(96000), 96000) == "SKIRT"


PROPERTIES = (
    ("offset->OFFSET", p_offset_is_offset),
    ("burst->SKIRT", p_burst_is_skirt),
    ("zero-mean!=OFFSET", p_zero_mean_never_offset),
    ("HF-add vs LF-removal", p_added_hf_not_confused_with_lf_removal),
    ("lead-silence", p_lead_silence_invariant),
    ("tail-pad", p_pad_invariant),
    ("polarity", p_polarity_invariant),
    ("scale", p_scale_invariant),
    ("sample-rate", p_rate_invariant),
)


def controls_matrix():
    """{defect name: {property: MOVED|BLIND}} plus the clean row. MOVED = the
    property turned red under the defect (the control did its job)."""
    rows = {"(clean)": {n: ("ok" if f(REAL) else "RED") for n, f in PROPERTIES}}
    for M in MUTANTS:
        est = M()
        row = {}
        for n, f in PROPERTIES:
            try:
                ok = f(est)
            except Refused:
                ok = False
            row[n] = "blind" if ok else "MOVED"
        rows[M.name] = row
    return rows


def print_matrix(rows):
    names = [n for n, _ in PROPERTIES]
    print("%-40s" % "defect \\ property" + " ".join("%-20s" % n for n in names))
    for d, r in rows.items():
        print("%-40s" % d + " ".join("%-20s" % r[n] for n in names))
