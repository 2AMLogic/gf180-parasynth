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
TAIL_RING_MAX = 0.05      # the last 10 % of the window may ring at <= 5 % of peak
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


def tail_ring_frac(seg, tail=0.10):
    """Std of the LAST `tail` of the window (linear trend removed) over the
    window's peak. A voice still ringing at the window end is not finished,
    and beta/S then depend on where the window was cut. Time-domain on
    purpose: the first version brick-wall filtered at 20 Hz and read the
    pulse's own sinc ringing as 'sounding' (wrong-then-right #2)."""
    seg = np.asarray(seg, float)
    pk = float(np.abs(seg).max())
    if pk <= 0:
        return 0.0
    t = seg[int(len(seg) * (1.0 - tail)):]
    n = np.arange(len(t))
    r = t - np.polyval(np.polyfit(n, t, 1), n)
    return float(r.std()) / pk


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
    ring = tail_ring_frac(est.prep(seg))
    st["tail_ring"] = ring
    if ring > TAIL_RING_MAX:
        return "REFUSED", dict(reason=f"still sounding at the window end: last 10 % rings at "
                                      f"{100 * ring:.1f} % of peak (limit {100 * TAIL_RING_MAX:.0f} %)", **st)
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
    """Same analogue signal at 8 and 96 kHz, corner given in Hz: same class."""
    return all(_cls(est, fx_offset(sr), sr) == "OFFSET" and _cls(est, fx_skirt(sr), sr) == "SKIRT"
               for sr in (8000, 96000))


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


# ===========================================================================
# External reference gate. What a recording can and cannot establish.
# ===========================================================================
REFS_ENV = "GF180_TR808_REFS"
REFS_FALLBACKS = ("/tmp/tr808-ref", str(pathlib.Path.home() / "dev" / "refs"))


def reference_gate(path=None, environ=None):
    """Assert the reference apparatus at the point of use. Returns a dict:

      status  REFUSED | AVAILABLE
      dc      REFUSED | PERMITTED   (may the recording's DC be read?)
      reasons list of strings

    The manifest schema is THIS PROBE'S requirement, not the corpus's: a
    manifest.json with `files: [{name, sha256}]`, `sample_rate`, and
    `capture_coupling` in {"ac", "dc"}. A corpus whose manifest lacks them is
    REFUSED here, and the fix is to add the declaration, not to relax this.

    **A capture that is AC-coupled or does not say cannot establish absence of
    DC.** Its zero mean is the capture chain's, so `dc` is REFUSED: a recording
    lacking an offset is not evidence the machine lacks one."""
    environ = os.environ if environ is None else environ
    cands = [path] if path else [environ.get(REFS_ENV), *REFS_FALLBACKS]
    cands = [c for c in cands if c]
    root = next((pathlib.Path(c) for c in cands if pathlib.Path(c).is_dir()), None)
    if root is None:
        return dict(status="REFUSED", dc="REFUSED", root=None,
                    reasons=[f"no reference directory ({REFS_ENV} unset or missing; tried "
                             f"{', '.join(cands) or 'nothing'})"])
    mf = root / "manifest.json"
    if not mf.is_file():
        return dict(status="REFUSED", dc="REFUSED", root=str(root),
                    reasons=[f"{mf} absent: no manifest, no hashes, no provenance"])
    try:
        m = json.loads(mf.read_text())
    except Exception as e:                                   # noqa: BLE001
        return dict(status="REFUSED", dc="REFUSED", root=str(root), reasons=[f"manifest unreadable: {e}"])
    reasons = []
    files = m.get("files")
    if not isinstance(files, list) or not files:
        reasons.append("manifest lists no files")
    else:
        for f in files:
            fp = root / str(f.get("name", ""))
            if not fp.is_file():
                reasons.append(f"{f.get('name')}: listed but missing")
            elif hashlib.sha256(fp.read_bytes()).hexdigest() != f.get("sha256"):
                reasons.append(f"{f.get('name')}: sha256 does not match the manifest")
    if not isinstance(m.get("sample_rate"), int) or m["sample_rate"] <= 0:
        reasons.append("manifest declares no sample_rate")
    coupling = m.get("capture_coupling")
    if coupling not in ("ac", "dc"):
        reasons.append("manifest does not declare capture_coupling (ac|dc)")
    if reasons:
        return dict(status="REFUSED", dc="REFUSED", root=str(root), reasons=reasons)
    if coupling == "ac":
        return dict(status="AVAILABLE", dc="REFUSED", root=str(root), sample_rate=m["sample_rate"],
                    reasons=["capture is AC-coupled: its absence of DC is the capture chain's, "
                             "so no raw-DC inference is permitted (shape/band comparison only)"])
    return dict(status="AVAILABLE", dc="PERMITTED", root=str(root), sample_rate=m["sample_rate"], reasons=[])


# ===========================================================================
# The model: production baseline, raw bus, clamped output
# ===========================================================================
LEAD = LEAD_FRAMES


def git(*a):
    try:
        return subprocess.run(["git", *a], cwd=ROOT, capture_output=True, text=True, check=True).stdout.strip()
    except Exception:                                        # noqa: BLE001
        return "?"


def provenance(cond_name=None):
    import drums_fx as dx
    dirty = "-dirty" if git("status", "--porcelain") else ""
    c = CONDITIONS.get(cond_name or "dev")
    init = dx.DrumsFx().couple_en
    ref = reference_gate()
    return (f"commit {git('rev-parse', '--short=12', 'HEAD')}{dirty}  SR {dx.SR}  "
            f"condition {cond_name or '-'} {c}  analysis {ANALYSIS_S:.1f}s onset-aligned  "
            f"K {dx.COUPLE_K} ({corner_hz(dx.COUPLE_K, dx.SR):.3f} Hz)\\n"
            f"production init: DrumsFx().couple_en = {init} (A_COUPLE=0x{dx.A_COUPLE:02X}; the enable "
            f"is OFF after reset and nothing in the shipped writes sets it); the '+coupling' column "
            f"writes A_COUPLE=1 at frame 0 -- a DIAGNOSTIC TOGGLE, an experiment, not a production state\\n"
            f"reference: {ref['status']} dc-inference={ref['dc']} {'; '.join(ref['reasons'])}")


_MEMO = {}


def render_bus(sound, enable, seconds, gain, vel):
    """(raw_bus int64, out int16, n_clip). raw = the bus sum BEFORE the output
    stage's clamp; out = the clamped int16 the block emits. Deterministic and
    integer, so memoised."""
    key = (sound, int(enable), seconds, gain, vel)
    if key in _MEMO:
        return _MEMO[key]
    import drums_fx as dx
    n = int(round(seconds * dx.SR))
    kit = dx.kit_with_sounds(sound)
    w = dx.hit_writes([(LEAD, dx.SOUND_STOP[sound], vel)], kit)
    if enable:
        w = sorted(list(w) + [(0, dx.A_COUPLE, 1)], key=lambda t: t[0])
    d = dx.DrumsFx()
    dmix, body = d.play(w, n)
    assert d.couple_en == int(bool(enable)), (d.couple_en, enable)   # the toggle is the toggle
    g = dx.accent_reg(gain)
    raw = (np.asarray(dmix, np.int64) * g + np.asarray(body, np.int64) * g) >> 15
    out = dx.output_fx(np.zeros(n), 0, dmix, g, body, g)
    n_clip = int(((raw > 32767) | (raw < -32768)).sum())
    _MEMO[key] = (raw, out, n_clip)
    return _MEMO[key]


def window_slice(x, i0, sr):
    return np.asarray(x, float)[i0:i0 + int(round(ANALYSIS_S * sr))]


def voice_row(sound, cond_name):
    """The uncoupled production baseline's classification, both signals."""
    import drums_fx as dx
    c = CONDITIONS[cond_name]
    raw, out, n_clip = render_bus(sound, 0, c["seconds"], c["gain"], c["vel"])
    fc = corner_hz(dx.COUPLE_K, dx.SR)
    lo, do = classify(out, dx.SR, fc)
    lr, dr = classify(raw, dx.SR, fc)
    i0 = onset_index(out)
    seg = window_slice(out, i0, dx.SR)
    pk = float(np.abs(seg).max()) or 1.0
    return dict(voice=sound, cond=cond_name, label=lo, detail=do, raw_label=lr, n_clip=n_clip,
                means=window_means(out, dx.SR), mean_frac=float(seg.mean()) / pk,
                peak_dbfs=20 * math.log10(pk / FS + 1e-30))


def fmt_row(r):
    d = r["detail"]
    if r["label"] == "REFUSED":
        return f"  {r['voice']:3s} REFUSED  {d.get('reason')}   (peak {r['peak_dbfs']:.1f} dBFS, clip {r['n_clip']})"
    return (f"  {r['voice']:3s} {r['label']:6s} beta {d['beta']:.3f}  S {d['flat']:7.2f}  "
            f"sub20 {d['sub20_db']:7.1f} dBFS (+{d['margin_db']:.0f} over floor)  "
            f"mean/peak {100 * r['mean_frac']:+6.2f} %  ring {100 * d['tail_ring']:.2f} %  "
            f"peak {r['peak_dbfs']:.1f} dBFS  clip {r['n_clip']}  raw={r['raw_label']}")


def screen(cond_name, voices=SCREEN_VOICES):
    print(provenance(cond_name))
    print("\nNON-CY SCREEN (uncoupled production baseline; class from beta AND S; nothing here is a sound verdict)")
    rows = []
    for v in voices:
        r = voice_row(v, cond_name)
        rows.append(r)
        print(fmt_row(r), flush=True)
    return rows


def _sub20_atten_db(base, cand, i0, sr):
    return (band_db(window_slice(base, i0, sr), sr, 0.0, 20.0)
            - band_db(window_slice(cand, i0, sr), sr, 0.0, 20.0))


def detail_row(sound, cond_name):
    """Uncoupled vs production-register coupled, on the SAME absolute window."""
    import drums_fx as dx
    from scipy.signal import lfilter
    c = CONDITIONS[cond_name]
    sr = dx.SR
    rb, ob, nb = render_bus(sound, 0, c["seconds"], c["gain"], c["vel"])
    rc, oc, nc = render_bus(sound, 1, c["seconds"], c["gain"], c["vel"])
    i0 = onset_index(ob)                                   # ONE onset for both sides
    k = dx.COUPLE_K
    fc = corner_hz(k, sr)
    wb, wc = window_slice(ob, i0, sr), window_slice(oc, i0, sr)
    measured = _sub20_atten_db(ob, oc, i0, sr)
    # STEADY bound: |H|^2 of the implemented discrete one-pole on the baseline spectrum
    p, f = onesided(wb, sr)
    m = f < 20.0
    a = 1.0 - 2.0 ** -k
    z = np.exp(-1j * 2 * np.pi * f[m] / sr)
    h2 = np.abs((1 - z) / (1 - a * z)) ** 2
    steady = -10 * math.log10(float((p[m] * h2).sum()) / (float(p[m].sum()) + 1e-30) + 1e-30)
    # FROM REST: an independent float one-pole (scipy) run causally over the baseline
    # raw bus -- not the integer DcBlockFx -- then the same window. No truncation LSB.
    yf = lfilter([1.0, -1.0], [1.0, -a], np.asarray(rb, float))
    yf = np.clip(yf, -32768, 32767)
    rest = _sub20_atten_db(ob, yf, i0, sr)
    bands = {n: band_db(wc, sr, *BANDS[n]) - band_db(wb, sr, *BANDS[n]) for n in BANDS}
    kind, d_hf, d_sh = share_change_kind(wb, wc, sr)
    cls_b = classify(ob, sr, fc)
    return dict(voice=sound, cond=cond_name, steady_db=steady, rest_db=rest, measured_db=measured,
                bands_db=bands, hf_kind=kind, hf_share_delta=d_sh, clip=(nb, nc),
                peak_delta_db=20 * math.log10((np.abs(wc).max() + 1e-9) / (np.abs(wb).max() + 1e-9)),
                label=cls_b[0], beta=cls_b[1].get("beta"), S=cls_b[1].get("flat"),
                reason=cls_b[1].get("reason"),
                sub20_base_db=band_db(wb, sr, 0.0, 20.0), sub20_coupled_db=band_db(wc, sr, 0.0, 20.0))


def fmt_detail(r):
    b = r["bands_db"]
    lab = f"{r['label']}" + (f" ({r['reason']})" if r["label"] == "REFUSED" else
                             f" beta {r['beta']:.3f} S {r['S']:.2f}")
    return (f"  {r['voice']:3s} [{lab}]\n"
            f"      sub-20 attenuation  steady-state bound {r['steady_db']:6.2f} dB   "
            f"causal-from-rest (float one-pole) {r['rest_db']:6.2f} dB   "
            f"measured (production A_COUPLE=1) {r['measured_db']:6.2f} dB\n"
            f"      absolute band change  sub20 {b['sub20']:+7.2f}  body {b['body']:+7.2f}  "
            f"mid {b['mid']:+7.2f}  HF {b['hf']:+7.2f} dB   peak {r['peak_delta_db']:+.2f} dB   "
            f"clip base/coupled {r['clip'][0]}/{r['clip'][1]}\n"
            f"      HF share change {r['hf_share_delta']:+.2f} pp -> read as {r['hf_kind']}")


def hypotheses(rows):
    """H_CH and H_RS as predeclared tests. Each states what it establishes."""
    out = []
    ch = rows.get("CH")
    if ch:
        short = ch["steady_db"] - ch["measured_db"]
        close = abs(ch["rest_db"] - ch["measured_db"])
        if ch["label"] == "REFUSED":
            v = f"NO VERDICT (class REFUSED: {ch['reason']})"
        elif short < CH_SHORTFALL_MIN_DB:
            v = f"NOT APPLICABLE: shortfall {short:.2f} dB < {CH_SHORTFALL_MIN_DB} dB, nothing to explain"
        elif close <= CH_REST_TOL_DB:
            v = (f"CONSISTENT: shortfall {short:.2f} dB vs steady bound; float causal-from-rest prediction "
                 f"within {close:.2f} dB of measured. (A prediction that fits; not an intervention.)")
        else:
            v = f"NOT SUPPORTED: from-rest prediction differs from measured by {close:.2f} dB (> {CH_REST_TOL_DB})"
        out.append(("H_CH  attenuation < steady bound because the causal blocker starts from rest", v))
    rs = rows.get("RS")
    if rs:
        if rs["label"] == "REFUSED":
            v = f"NO VERDICT (class REFUSED: {rs['reason']})"
        elif rs["label"] == "SKIRT":
            v = (f"SUPPORTED as a measurement: S {rs['S']:.2f} (flat = 1), beta {rs['beta']:.3f}: the 0-20 Hz band "
                 f"is the skirt of a finite burst; steady bound {rs['steady_db']:.2f} dB.")
        else:
            v = f"NOT SUPPORTED: class {rs['label']} (S {rs['S']:.2f}, beta {rs['beta']:.3f})"
        out.append(("H_RS  sub-20 Hz energy is largely the onset skirt", v))
    return out


def subject_verdict(rows_by_cond):
    """Per-subject ending, from the baseline rows of BOTH conditions. Never a
    sound-fidelity claim: that is a capability refusal while no DC-coupled
    reference exists."""
    labs = {c: r["label"] for c, r in rows_by_cond.items()}
    if "REFUSED" in labs.values():
        why = "; ".join(f"{c}: {r['detail'].get('reason')}" for c, r in rows_by_cond.items() if r["label"] == "REFUSED")
        return f"NO VERDICT (apparatus REFUSED: {why})"
    if len(set(labs.values())) != 1:
        return f"NO VERDICT (class differs across predeclared conditions: {labs}); limits NOT revisited"
    lab = next(iter(labs.values()))
    if lab == "SKIRT":
        return "NO STANDING OFFSET ESTABLISHED: sub-20 Hz energy is onset skirt in both conditions; a DC blocker cannot remove it"
    if lab == "MIXED":
        return "MIXED in both conditions: offset vs skirt not separable by the two statistics; no defect established"
    if all(abs(r["mean_frac"]) >= OFFSET_PEAK_FRAC for r in rows_by_cond.values()):
        return ("MODEL-SIDE STANDING OFFSET in both conditions (|mean|/peak >= "
                f"{100 * OFFSET_PEAK_FRAC:.0f} %); SOUND-FIDELITY DEFECT: capability REFUSED (no DC-coupled reference)")
    return "OFFSET-like but |mean|/peak below the declared magnitude: no defect established"


def detail(cond_name, voices=SUBJECTS + CONTROLS):
    print(provenance(cond_name))
    print("\nDETAIL: subjects CH, RS; controls BD, HT (diagnostic coupling toggle = experiment)")
    rows = {}
    for v in voices:
        rows[v] = detail_row(v, cond_name)
        print(fmt_detail(rows[v]), flush=True)
    print("\nHYPOTHESES (predeclared limits; status labelled)")
    for h, v in hypotheses(rows):
        print(f"  {h}\n      -> {v}")
    return rows


def batch_spec():
    py = "python3 tools/probes/residual_dc.py"
    jobs = [f"{py} --screen --condition dev", f"{py} --screen --condition confirm",
            f"{py} --detail --condition dev", f"{py} --detail --condition confirm"]
    return ('python3 tools/run_all.py --jobs 2 --timeout 3600 --json build/residual-dc-batch.json \\\n    '
            + " \\\n    ".join(f'"{j}"' for j in jobs))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    for flag in ("declared", "controls", "reference", "screen", "detail", "batch-spec"):
        ap.add_argument("--" + flag, action="store_true")
    ap.add_argument("--condition", choices=sorted(CONDITIONS), default="dev")
    ap.add_argument("--voices", default=None)
    a = ap.parse_args(argv)
    vs = tuple(a.voices.split(",")) if a.voices else None
    if a.declared:
        for k in ("ANALYSIS_S", "STABILITY_S", "BETA_OFFSET_MIN", "BETA_SKIRT_MAX", "BETA_STABLE_TOL",
                  "S_SKIRT_MAX", "S_OFFSET_MIN", "TAIL_RING_MAX", "MIN_PEAK_LSB", "RES_MARGIN_DB",
                  "OFFSET_PEAK_FRAC", "HF_ADDED_DB", "HF_FLAT_DB", "CH_REST_TOL_DB", "CH_SHORTFALL_MIN_DB"):
            print(f"{k} = {globals()[k]}")
        print("CONDITIONS =", json.dumps(CONDITIONS))
    if a.controls:
        rows = controls_matrix()
        print_matrix(rows)
        bad = [d for d, r in rows.items() if d != "(clean)" and "MOVED" not in r.values()]
        return 1 if bad or any(v == "RED" for v in rows["(clean)"].values()) else 0
    if a.reference:
        g = reference_gate()
        print(json.dumps(g, indent=1))
    if a.screen:
        screen(a.condition, vs or SCREEN_VOICES)
    if a.detail:
        detail(a.condition, vs or (SUBJECTS + CONTROLS))
    if a.__dict__["batch_spec"]:
        print(batch_spec())
    return 0


if __name__ == "__main__":
    sys.exit(main())
