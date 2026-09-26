#!/usr/bin/env python3
"""Our ladder against three independent software emulations of the Minimoog's.

    .venv/bin/python model/reference_compare.py --stage all --out /tmp/refcmp
    .venv/bin/python model/reference_compare.py --report /tmp/refcmp

Why this exists. `docs/discrimination.md` section 8 concluded that no Minimoog
validation was possible: the only free hardware corpus (Legowelt's 222 WAVs
from Minimoog #5529) ships no panel settings, and the parameter-labelled
datasets were rejected for being rendered from software synths. That purity
argument produced ZERO validation instead of imperfect validation. A software
reference gives up the one thing the hardware corpus could never give --
**you can set it to a known patch and set ours to the same patch** -- and that
is a controlled experiment, which sound-matching against unlabelled recordings
could never be.

What this does NOT claim, and the label goes on every result: agreeing with
these three means "consistent with high-quality emulations", not "sounds like
a Minimoog". None of the three is a Minimoog. Two of them are commercial
emulations whose internals are not inspectable at all.

**Surge XT gets its own verdict**, because it is not the same kind of
evidence as the other two. Surge is open source and its "LP Vintage Ladder"
subtype "Type 2" is `sst::filters::VintageLadder::Huov` -- Huovilainen's
DAFx-04 nonlinear ladder, the same published model DR 0001 implements. A
disagreement with Surge Type 2 is two implementations of one paper
disagreeing, which is a bug in one of them, not a difference of modelling
taste. Where they differ BY DESIGN is recorded in `docs/discrimination.md`
section 8 and re-derived from Surge's source, not guessed.

Method, and why each choice:

  * every filter is measured with a **stepped tone and a coherent
    projection** -- the measured transfer function. Not an impulse response
    (which presumes a linearity none of these four filters has) and never a
    spectral centroid. Diva has no audio input, so it is measured by
    noise excitation against a wide-open reference render, and
    `--stage validate` checks that method against the stepped tone on Surge,
    where both are possible.
  * **everything quoted is a ratio** -- dB relative to a passband plateau, dB
    per octave, a harmonic relative to its own fundamental, a frequency
    relative to another frequency. A fixed gain difference between two
    synthesisers cancels out of all of them by construction, so no result
    here can be a level mismatch in disguise.
  * **48 kHz everywhere**, which is our own SR. Nothing is resampled, so no
    result can be a resampler.
  * every estimator is ground-truthed against a closed-form signal in
    `model/test_reference_compare.py` before any number it produces is
    quoted, and three deliberately-broken ladders are carried through the
    same measurements so the comparison is known to have power.

The structural fingerprint, and the one thing to understand about it: h5
relative to h3 at self-oscillation. h2 is useless because `tanh` is odd, so
both candidate structures have none. But h5 - h3 is NOT settings-independent
-- it depends strongly on how hard the limit cycle drives the nonlinearity,
which h3 itself measures. So the headline comparison is **h5 - h3 at matched
h3**, which controls for drive, and the full trajectory is reported beside it.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
import time

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "..", "audition"))

import audio_measure as am                                          # noqa: E402
import reference_rigs as rr                                         # noqa: E402
import voice_fx as vf                                               # noqa: E402

SR = rr.SR

# The frequency grid every response measurement uses. Log-spaced, and wide
# enough that a 300 Hz corner has two clean octaves of stopband inside it
# before the digital one-poles start to flatten toward Nyquist.
FREQS = np.geomspace(40.0, 12000.0, 32)

# Resonance grids, each on its OWN device's control range. They are not
# commensurable and are never averaged across devices; what is compared is
# each device's trajectory and its endpoints.
RES_GRID = {
    "ours":   [1.02, 1.05, 1.10, 1.15, 1.20, 1.30, 1.45, 1.60, 1.80, 2.00],
    "surge":  [0.70, 0.80, 0.86, 0.90, 0.93, 0.95, 0.97, 0.99, 1.00],
    "diva":   [0.86, 0.90, 0.92, 0.94, 0.96, 0.98, 1.00],
    "miniv3": [0.70, 0.78, 0.84, 0.88, 0.92, 0.96, 1.00],
}

CUTOFFS_HZ = [100.0, 200.0, 400.0, 800.0, 1600.0, 3200.0, 6400.0]

# The matched operating point for the structural fingerprint: compare h5 - h3
# where each filter's limit cycle drives its nonlinearity equally hard, which
# is what h3 measures. -42 dB is inside every device's measured range.
MATCH_H3_DB = -42.0


# ===========================================================================
# devices
# ===========================================================================
def build(name):
    if name == "ours":
        return rr.OurLadder("ours")
    if name == "ours-1tanh":
        return rr.OurLadder("ours-1tanh", nonlin="feedback")
    if name == "ours-2pole":
        return rr.OurLadder("ours-2pole", stages=2)
    if name == "ours-skew30":
        return rr.OurLadder("ours-skew30", cut_skew=1.3)
    if name == "ours-tanh256":
        # NOT a defect: our ladder with a 256-entry tanh table instead of the
        # shipped 16. The hypothesis under test is that the excess 5th
        # harmonic the references show up is the LUT, not the structure.
        return rr.OurLadder("ours-tanh256", cfg=dict(tanh_entries=256))
    if name == "ours-huovtune":
        # "our ladder BEFORE DR 0011, plus Huovilainen's tuning polynomial" --
        # the candidate section 8.4 reported, and the change DR 0011 then took.
        # It has to be built against the UNTUNED cutoff ROM: the shipped one has
        # carried `CUT_TRIM * fcr()` since DR 0011, so `huov_fcr=True` against it
        # would apply the polynomial twice (`OurLadder.__init__` refuses that).
        return rr.OurLadder("ours-huovtune", huov_fcr=True,
                            g_rom=vf.make_g_rom(tune=False))
    if name == "surge-rk":
        return rr.SurgeRig("Type 1")
    if name == "surge-huov":
        return rr.SurgeRig("Type 2")
    if name == "diva":
        return rr.DivaRig("rough")
    if name == "miniv3":
        return rr.MiniV3Rig()
    raise KeyError(name)


OURS = ["ours"]
CONTROLS = ["ours-1tanh", "ours-2pole", "ours-skew30"]
PROBES = ["ours-tanh256", "ours-huovtune"]
REFS = ["surge-rk", "surge-huov", "diva", "miniv3"]
ALL = OURS + CONTROLS + REFS


def res_grid(name):
    name = "ours" if name.startswith("ours") else name
    return RES_GRID["surge" if name.startswith("surge") else
                    "diva" if name == "diva" else
                    "miniv3" if name == "miniv3" else "ours"]


def res_lo(name):
    """A resonance low enough that the response has no peak worth the name:
    where a corner and a slope mean something."""
    return 0.2 if name.startswith("ours") else 0.05


# ===========================================================================
# stage: the self-oscillation fingerprint
# ===========================================================================
def sig_row(y, tag):
    try:
        s = am.harmonic_signature(y, SR)
    except am.InsufficientEvidence as e:
        return dict(tag=tag, ok=False, why=str(e), rms=float(am.rms(y)))
    s.update(tag=tag, ok=True, rms=float(am.rms(y)))
    return s


def stage_selfosc(dev, name, cut_setting, cut_hz):
    rows = []
    for res in res_grid(name):
        t0 = time.time()
        y = dev.ring(cut_setting, res, seconds=1.2)
        r = sig_row(y, f"{name} res {res}")
        r.update(device=name, res=res, cut_setting=cut_setting, cut_hz=cut_hz,
                 secs=round(time.time() - t0, 2))
        rows.append(r)
        h3 = r.get("h3")
        print(f"  {name:12s} res {res:5.2f}  rms {r['rms']:.5f}  "
              f"f0 {r.get('f0', float('nan')):7.1f}  "
              f"h3 {h3 if h3 is None else round(h3, 1)}  "
              f"h5 {r.get('h5') if r.get('h5') is None else round(r['h5'], 1)}  "
              f"h7 {r.get('h7') if r.get('h7') is None else round(r['h7'], 1)}", flush=True)
    return rows


def selfosc_summary(rows):
    """h5 - h3 at each device's maximum resonance, and interpolated at the
    matched h3 of MATCH_H3_DB so the comparison is at equal drive."""
    out = {}
    for name in sorted({r["device"] for r in rows}):
        rs = [r for r in rows if r["device"] == name and r.get("ok")]
        live = [r for r in rs if r.get("h3") is not None and r.get("h5") is not None]
        top = max(rs, key=lambda r: r["res"]) if rs else None
        d = dict(device=name, n_points=len(rs), n_with_h5=len(live))
        if top:
            d["max_res"] = top["res"]
            d["max_res_h3"] = top.get("h3")
            d["max_res_h5"] = top.get("h5")
            d["max_res_h7"] = top.get("h7")
            d["max_res_h2"] = top.get("h2")
            d["max_res_rms"] = top["rms"]
            d["max_res_f0"] = top.get("f0")
            if top.get("h3") is not None and top.get("h5") is not None:
                d["max_res_spread"] = top["h5"] - top["h3"]
        if len(live) >= 2:
            h3s = np.array([r["h3"] for r in live])
            sp = np.array([r["h5"] - r["h3"] for r in live])
            o = np.argsort(h3s)
            h3s, sp = h3s[o], sp[o]
            if h3s[0] <= MATCH_H3_DB <= h3s[-1]:
                d["spread_at_matched_h3"] = float(np.interp(MATCH_H3_DB, h3s, sp))
            d["h3_range"] = [float(h3s[0]), float(h3s[-1])]
        out[name] = d
    return out


# ===========================================================================
# stage: the measured response -- corner, slope, resonant peak
# ===========================================================================
def response_curve(dev, name, cut_setting, res, amp=0.25):
    if name == "diva":
        return dev.noise_curve(FREQS, cut_setting, res, level=amp)
    return dev.tone_gain_db(FREQS, cut_setting, res, amp)


# The resonance at which each device's free ring stops decaying, MEASURED in
# `--stage selfosc` and written down here so the drive study can put every
# filter at the same fraction of its own threshold. Surge's Huovilainen
# subtype never reaches one -- its resonance is clamped below the onset by
# design -- so its control range's top is used instead, and labelled.
ONSET_RES = {"ours": 1.00, "ours-1tanh": 1.00, "ours-2pole": 1.00,
             "ours-skew30": 1.00, "ours-tanh256": 1.00, "ours-huovtune": 1.00,
             "surge-rk": 0.90, "surge-huov": 1.00, "diva": 0.90, "miniv3": 0.78}


def stage_peakdrive(dev, name, cut_setting, cut_hz):
    """The resonant peak at a matched fraction of each filter's own
    self-oscillation threshold, as the DRIVE falls.

    This exists because the first run measured the peak at one input level and
    got +8.5 dB for us against about +20 dB for all three references -- which
    is not a filter difference until the drive is controlled. A saturating
    resonant filter compresses its own resonance, so a peak height quoted
    without a drive is a statement about the level it was measured at. Here
    the same res (0.9 of each device's measured onset) is measured at five
    input levels 48 dB apart, and the small-signal end is where the linear
    claim lives."""
    rows = []
    res = 0.9 * ONSET_RES[name]
    for lv in (-60.0, -48.0, -36.0, -24.0, -12.0):
        amp = 10 ** (lv / 20.0)
        g = np.asarray(response_curve(dev, name, cut_setting, res, amp))
        r = response_row(FREQS, g, cut_hz, name, res, cut_setting)
        r.update(level_dbfs=lv, onset_res=ONSET_RES[name])
        rows.append(r)
        print(f"  {name:12s} in {lv:+6.1f} dBFS res {res:.3f}  peak "
              f"{r['peak_db'] and round(r['peak_db'], 1)} dB  Q "
              f"{r['peak_q'] and round(r['peak_q'], 2)}  corner "
              f"{r['corner_hz'] and round(r['corner_hz'], 1)}", flush=True)
    return rows


def stage_bigdrive(dev, name, cut_setting, cut_hz):
    """How far a signal has to be pushed before each filter's saturation
    engages at all. Surge's Huovilainen subtype scales its tanh argument by
    `thermal = 1/70`, so a full-scale +-1.0 signal reaches 0.014 into a
    function that is linear to one part in 10^4 there; this measures where
    that stops being true. Levels above 0 dBFS are not a patch anyone would
    use -- they are the diagnostic."""
    rows = []
    res = 0.5 * ONSET_RES[name]
    for lv in (-12.0, 0.0, 12.0, 20.0, 26.0, 32.0, 37.0):
        amp = 10 ** (lv / 20.0)
        y = dev.drive_tone(200.0, cut_setting, res, amp)
        try:
            s = am.harmonic_signature(y, SR, f_lo=100.0, f_hi=400.0)
            ok = True
        except am.InsufficientEvidence as e:
            s, ok = dict(why=str(e)), False
        r = dict(device=name, level_dbfs=lv, res=res, cut_hz=cut_hz, ok=ok,
                 out_rms_db=float(am.db(am.rms(y), 1.0)),
                 **{k: v for k, v in s.items() if k != "why"})
        rows.append(r)
        print(f"  {name:12s} in {lv:+6.1f} dBFS -> out {r['out_rms_db']:+7.2f} dB  "
              f"h3 {r.get('h3') if r.get('h3') is None else round(r['h3'], 1)}", flush=True)
    return rows


def response_row(freqs, g, cut_hz, name, res, cut_setting):
    ref_band = (freqs[0], max(freqs[0] * 2.5, (cut_hz or 400.0) * 0.25))
    row = dict(device=name, res=res, cut_setting=cut_setting, cut_hz=cut_hz,
               freqs=[float(f) for f in freqs], gain_db=[float(v) for v in g])
    c = am.corner_from_curve(freqs, g, ref_band=ref_band)
    row["corner_hz"] = c.value if c.ok else None
    row["corner_why"] = None if c.ok else c.reason
    ref = c.value if c.ok else cut_hz
    if ref:
        # Cap the stopband fit where the measurement's own floor starts: below
        # about -70 dB of the passband the stepped-tone projection is reading
        # the filter's truncation noise, the curve flattens, and a slope fitted
        # through that reads far too steep and then far too shallow. The fit's
        # residual catches it, but the band is chosen not to include it.
        plateau = am.plateau_db(freqs, g, ref_band)
        live = np.asarray(freqs)[np.asarray(g) > plateau - 70.0]
        top = float(live.max()) if len(live) else 9000.0
        band = (2.2 * ref, min(7.0 * ref, 9000.0, top))
        s = am.slope_db_oct(freqs, g, band)
        row["slope_db_oct"] = s.value if s.ok else None
        row["slope_band"] = list(band)
        row["slope_resid_db"] = (s.detail.get("residual_db") if s.ok
                                 else s.detail.get("residual_db"))
        row["slope_why"] = None if s.ok else s.reason
    p = am.peak_from_curve(freqs, g, ref_band=ref_band)
    row["peak_db"] = p.value if p.ok else None
    row["peak_hz"] = p.detail.get("f_peak") if p.ok else None
    row["peak_q"] = p.detail.get("q") if p.ok else None
    row["peak_why"] = None if p.ok else p.reason
    return row


def stage_response(dev, name, points):
    """`points` is [(cut_setting, cut_hz, res), ...]."""
    rows = []
    for cut_setting, cut_hz, res in points:
        t0 = time.time()
        g = response_curve(dev, name, cut_setting, res)
        r = response_row(FREQS, np.asarray(g), cut_hz, name, res, cut_setting)
        r["secs"] = round(time.time() - t0, 2)
        rows.append(r)
        print(f"  {name:12s} cut {str(cut_hz or cut_setting):>8s} res {res:5.2f}  "
              f"corner {r['corner_hz'] and round(r['corner_hz'], 1)}  "
              f"slope {r['slope_db_oct'] and round(r['slope_db_oct'], 1)}  "
              f"peak {r['peak_db'] and round(r['peak_db'], 1)} dB "
              f"Q {r['peak_q'] and round(r['peak_q'], 2)}", flush=True)
    return rows


# ===========================================================================
# stage: drive
# ===========================================================================
def stage_drive(dev, name, cut_setting, cut_hz, res, f_in, levels):
    rows = []
    for lv in levels:
        amp = 10 ** (lv / 20.0)
        y = dev.drive_tone(f_in, cut_setting, res, amp)
        try:
            s = am.harmonic_signature(y, SR, f_lo=f_in * 0.5, f_hi=f_in * 2.0)
            ok = True
        except am.InsufficientEvidence as e:
            s, ok = dict(why=str(e)), False
        r = dict(device=name, level_dbfs=lv, cut_hz=cut_hz, res=res, f_in=f_in,
                 out_rms_db=float(am.db(am.rms(y), 1.0)), ok=ok, **{k: v for k, v in s.items()
                                                                   if k != "why"})
        rows.append(r)
        print(f"  {name:12s} in {lv:+6.1f} dBFS -> out {r['out_rms_db']:+7.2f} dB  "
              f"h3 {r.get('h3') if r.get('h3') is None else round(r['h3'], 1)}  "
              f"h5 {r.get('h5') if r.get('h5') is None else round(r['h5'], 1)}", flush=True)
    return rows



# ===========================================================================
# the report
# ===========================================================================
ORDER = ["ours", "ours-tanh256", "ours-huovtune", "surge-huov", "surge-rk", "diva", "miniv3",
         "ours-1tanh", "ours-2pole", "ours-skew30"]
KIND = {"ours": "ours", "ours-tanh256": "probe", "ours-huovtune": "probe",
        "surge-huov": "reference (open source)", "surge-rk": "reference (open source)",
        "diva": "reference", "miniv3": "reference",
        "ours-1tanh": "INJECTED DEFECT", "ours-2pole": "INJECTED DEFECT",
        "ours-skew30": "INJECTED DEFECT"}


def _load(out, stage, dev):
    p = os.path.join(out, f"{stage}-{dev}.json")
    return json.load(open(p)) if os.path.exists(p) else None


def _f(v, n=1, w=8):
    return (" " * (w - 3) + "---") if v is None or (isinstance(v, float) and not np.isfinite(v)) \
        else f"{v:{w}.{n}f}"


def ideal_4pole_slope(band, fp):
    """The slope an IDEAL analogue 4-pole gives over the SAME band, so that a
    measured -21.6 dB/oct is read as "what a 4-pole does over 2.2 to 7 times
    its corner" and not as a shortfall against the asymptotic 24. Closed form:
    one pole contributes -6.0206 * (f/fp)^2 / (1 + (f/fp)^2) dB per octave."""
    lo, hi = band
    lf = np.linspace(math.log2(lo), math.log2(hi), 200)
    f = 2.0 ** lf
    g = -4 * 10 * np.log10(1 + (f / fp) ** 2)
    return float(np.polyfit(lf, g, 1)[0])


def report(out):                                                  # noqa: C901
    print("=" * 96)
    print("OUR LADDER AGAINST THREE SOFTWARE MINIMOOG-LINEAGE FILTERS")
    print("48 kHz throughout (our own SR; nothing resampled). Every number below is a RATIO,")
    print("so a gain difference between two synthesisers cannot appear as a result.")
    print("=" * 96)

    print("\n1. SELF-OSCILLATION FINGERPRINT   (cutoff commanded 261.6 Hz)")
    print(f"{'device':14s} {'kind':24s} {'onset':>6} {'f0':>7} {'h2':>7} {'h3':>7} "
          f"{'h5':>7} {'h7':>7} {'h5-h3':>7} {'@h3=-42':>8}")
    for d in ORDER:
        rows = _load(out, "selfosc", d)
        if not rows:
            continue
        live = [r for r in rows if r.get("ok")]
        osc = [r for r in live if r.get("h3") is not None or r["rms"] > 2e-3]
        onset = min((r["res"] for r in osc), default=None)
        top = max(live, key=lambda r: r["res"])
        pairs = sorted((r["h3"], r["h5"] - r["h3"]) for r in live
                       if r.get("h3") is not None and r.get("h5") is not None)
        at = None
        if len(pairs) >= 2 and pairs[0][0] <= MATCH_H3_DB <= pairs[-1][0]:
            at = float(np.interp(MATCH_H3_DB, [p[0] for p in pairs], [p[1] for p in pairs]))
        sp = (top["h5"] - top["h3"]) if (top.get("h5") is not None
                                         and top.get("h3") is not None) else None
        print(f"{d:14s} {KIND[d]:24s} {('%.2f' % onset) if onset else '  none':>6} "
              f"{_f(top.get('f0'), 1, 7)} {_f(top.get('h2'), 1, 7)} {_f(top.get('h3'), 1, 7)} "
              f"{_f(top.get('h5'), 1, 7)} {_f(top.get('h7'), 1, 7)} {_f(sp, 1, 7)} {_f(at, 1, 8)}")
    print("  h2..h7 at each device's OWN maximum resonance, dB relative to the fundamental.")
    print("  '---' = the estimator refused: at or under that record's own measured floor.")
    print("  '@h3=-42' is the fingerprint at MATCHED DRIVE -- h5-h3 interpolated to where every")
    print("  filter's limit cycle drives its nonlinearity equally hard. It is the only column of")
    print("  the four that is not confounded by how far past onset each device happens to sit.")

    print("\n2. SELF-OSCILLATION PITCH vs COMMANDED CUTOFF, at maximum resonance")
    for d in ORDER:
        rows = _load(out, "tracking", d)
        if not rows:
            continue
        rs = [(r.get("commanded_hz") or r["want_hz"], r.get("f_osc")) for r in rows]
        ok = [(c, f) for c, f in rs if f]
        if not ok:
            print(f"  {d:14s} no self-oscillation at any cutoff")
            continue
        errs = [(f / c - 1) * 100 for c, f in ok]
        circ = "" if _load(out, "tracking", d)[0].get("commanded_hz") else \
            "   [knob calibrated ON f_osc: this row is circular, see note]"
        print(f"  {d:14s} " + "  ".join(f"{c:.0f}:{e:+.2f}%" for (c, _), e in zip(ok, errs)))
        print(f"  {'':14s} spread across the range {max(errs) - min(errs):5.2f} "
              f"percentage points{circ}")
    print("  A frequency-INDEPENDENT offset is a single scale factor and is trivially removable.")
    print("  The spread is the defect: it is a cutoff control that does not mean the same thing")
    print("  at the two ends of its range. Mini V3's row is circular by construction -- its knob")
    print("  has no units, so it was bisected against f_osc itself -- and is not evidence.")

    print("\n3. MEASURED RESPONSE: -3 dB CORNER vs COMMANDED CUTOFF, and STOPBAND SLOPE")
    print(f"{'device':14s} {'cut Hz':>7} {'corner':>8} {'ratio':>6} {'slope':>7} "
          f"{'ideal4p':>8} {'resid':>6}")
    for d in ORDER:
        rows = _load(out, "response", d)
        if not rows:
            continue
        for r in rows:
            c, k, sl = r.get("cut_hz"), r.get("corner_hz"), r.get("slope_db_oct")
            ideal = (ideal_4pole_slope(r["slope_band"], k / 0.435)
                     if (sl is not None and k) else None)
            print(f"{d:14s} {c:7.0f} {_f(k, 1, 8)} {_f(k / c if k and c else None, 3, 6)} "
                  f"{_f(sl, 1, 7)} {_f(ideal, 1, 8)} {_f(r.get('slope_resid_db'), 2, 6)}")
    print("  'ratio' is measured corner / commanded cutoff. It is NOT expected to be 1: a 4-pole")
    print("  cascade is -3 dB at 0.435 of its pole frequency, and resonance lifts that back up.")
    print("  What matters is whether the ratio is CONSTANT across the range. 'ideal4p' is the")
    print("  slope a perfect analogue 4-pole gives over the same fit band, so that a measured")
    print("  -21 dB/oct is read against what 24 dB/oct actually looks like over 2.2 to 7x.")

    print("\n4. RESONANT PEAK vs DRIVE, at 0.9 of each filter's OWN self-oscillation threshold")
    print(f"{'device':14s} {'res':>6}   " + "  ".join(f"{lv:+d} dBFS" for lv in
                                                     (-60, -48, -36, -24, -12)))
    for d in ORDER:
        rows = _load(out, "peakdrive", d)
        if not rows:
            continue
        cells = []
        for r in rows:
            pk, q = r.get("peak_db"), r.get("peak_q")
            cells.append(f"{_f(pk, 1, 5)}/{'  --' if not (q and np.isfinite(q)) else '%4.1f' % q}")
        print(f"{d:14s} {rows[0]['res']:6.3f}   " + "  ".join(cells))
    print("  each cell: peak height over the passband, in dB / its -3 dB Q.")
    print("  The input level is referred to the PLUGIN's full scale, not to each filter's own")
    print("  input: table 5 gives the level at which each one actually starts to saturate, which")
    print("  is what makes the columns comparable.")

    print("\n5. WHERE THE SATURATION ENGAGES  (cutoff 800 Hz, res 0.5 of onset, 200 Hz in)")
    print(f"{'device':14s} " + "  ".join(f"{lv:+d}" for lv in (-12, 0, 12, 20, 26, 32, 37))
          + "     h3 = -40 dB at")
    for d in ORDER:
        rows = _load(out, "bigdrive", d)
        if not rows:
            continue
        lv = [r["level_dbfs"] for r in rows]
        h3 = [r.get("h3") for r in rows]
        cross = None
        for i in range(1, len(rows)):
            a, b = h3[i - 1], h3[i]
            if a is not None and b is not None and a <= -40.0 <= b:
                cross = lv[i - 1] + (lv[i] - lv[i - 1]) * (-40.0 - a) / (b - a)
                break
        print(f"{d:14s} " + "  ".join(("  --" if v is None else f"{v:4.0f}") for v in h3)
              + f"     {'not reached' if cross is None else '%+.1f dBFS' % cross}")
    print("  h3 of the output, dB relative to its fundamental, at each input level.")

    print("\n6. THE INJECTED DEFECTS -- does this comparison have any power?")
    for d in ("ours-1tanh", "ours-2pole", "ours-skew30"):
        rows = _load(out, "response", d)
        so = _load(out, "selfosc", d)
        bits = []
        if rows:
            rr_ = [r for r in rows if r.get("corner_hz")]
            if rr_:
                rat = [r["corner_hz"] / r["cut_hz"] for r in rr_]
                bits.append(f"corner/commanded {min(rat):.2f}-{max(rat):.2f}")
            sl = [r["slope_db_oct"] for r in rows if r.get("slope_db_oct")]
            if sl:
                bits.append(f"slope {min(sl):.1f} to {max(sl):.1f} dB/oct")
        if so:
            pairs = sorted((r["h3"], r["h5"] - r["h3"]) for r in so
                           if r.get("ok") and r.get("h3") is not None and r.get("h5") is not None)
            if len(pairs) >= 2 and pairs[0][0] <= MATCH_H3_DB <= pairs[-1][0]:
                bits.append("h5-h3 at matched h3 "
                            f"{np.interp(MATCH_H3_DB, [p[0] for p in pairs], [p[1] for p in pairs]):.1f} dB")
        print(f"  {d:14s} " + "; ".join(bits))
    print("  Compare each against `ours` in the same table. A defect that does not move any")
    print("  number here is a hole in the coverage, and is reported as one.")
    return 0


# ===========================================================================
# runner
# ===========================================================================
def calibrate_knob(dev, name, want_hz, lo=0.05, hi=0.98, iters=9, res=None):
    """For a device whose cutoff control has no units (Mini V3), find the knob
    position whose SELF-OSCILLATION lands at `want_hz`. Bisection on a
    measured frequency, not a guess at the taper."""
    res = res_grid(name)[-1] if res is None else res
    for _ in range(iters):
        mid = 0.5 * (lo + hi)
        y = dev.ring(mid, res, seconds=0.5)
        e = am.dominant_frequency(y, 25.0, 15000.0, SR)
        f = e.value if e.ok else 0.0
        if f > want_hz:
            hi = mid
        else:
            lo = mid
    return 0.5 * (lo + hi)


def cut_setting_for(dev, name, hz, cache):
    """The control value that commands `hz` on this device, and the Hz we are
    entitled to CALL it -- None where the device states no frequency."""
    if name.startswith("ours"):
        return hz, hz
    if name.startswith("surge"):
        # SurgeRig takes HERTZ and converts; handing it the normalised value
        # here is the bug that made the first run's Surge rows all read a
        # 13.75 Hz cutoff (every ring silent, every f0 26.5 Hz).
        return hz, dev.set_point(hz, 0.0)
    if name == "diva":
        return dev.freq_value(hz), None
    if name == "miniv3":
        key = f"miniv3-{hz:.0f}"
        if key not in cache:
            cache[key] = calibrate_knob(dev, name, hz)
        return cache[key], None
    raise KeyError(name)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--stage", default="selfosc",
                    choices=["selfosc", "response", "tracking", "drive", "peakdrive",
                             "bigdrive", "validate", "all"])
    ap.add_argument("--devices", default=",".join(ALL))
    ap.add_argument("--out", default="/tmp/refcmp")
    ap.add_argument("--report", action="store_true",
                    help="print the comparison from an --out directory and stop")
    a = ap.parse_args(argv)
    if a.report:
        return report(a.out)
    os.makedirs(a.out, exist_ok=True)
    devices = [d for d in a.devices.split(",") if d]
    stages = ["selfosc", "response", "tracking", "drive"] if a.stage == "all" else [a.stage]
    cache_path = os.path.join(a.out, "knobs.json")
    cache = json.load(open(cache_path)) if os.path.exists(cache_path) else {}

    for name in devices:
        print(f"\n===== {name} =====", flush=True)
        dev = build(name)
        out = {}
        def guard(key, fn):
            try:
                out[key] = fn()
            except NotImplementedError as e:
                out[key] = [dict(device=name, ok=False, not_answerable=str(e))]
                print(f"  {name}: {key} NOT ANSWERABLE -- {e}", flush=True)

        try:
            cs258, hz258 = cut_setting_for(dev, name, 261.6, cache)
            if "selfosc" in stages:
                print(" -- self-oscillation fingerprint", flush=True)
                guard("selfosc", lambda: stage_selfosc(dev, name, cs258, hz258 or 261.6))
            if "response" in stages:
                print(" -- measured response: corner and slope", flush=True)
                pts = []
                for hz in CUTOFFS_HZ:
                    cs, chz = cut_setting_for(dev, name, hz, cache)
                    pts.append((cs, chz if chz is not None else hz, res_lo(name)))
                guard("response", lambda: stage_response(dev, name, pts))
                print(" -- measured response: resonant peak versus resonance", flush=True)
                cs, chz = cut_setting_for(dev, name, 800.0, cache)
                pk = [(cs, chz if chz is not None else 800.0, r)
                      for r in ([0.2, 0.4, 0.6, 0.8, 0.9] if not name.startswith("ours")
                                else [0.3, 0.5, 0.7, 0.85, 0.95])]
                guard("peak", lambda: stage_response(dev, name, pk))
            if "tracking" in stages:
                print(" -- self-oscillation pitch versus cutoff", flush=True)
                rows = []
                res = res_grid(name)[-1]
                for hz in CUTOFFS_HZ:
                    cs, chz = cut_setting_for(dev, name, hz, cache)
                    y = dev.ring(cs, res, seconds=0.8)
                    e = am.dominant_frequency(y, 20.0, 20000.0, SR)
                    z = am.zero_crossing_frequency(y, SR)
                    f = z.value if (z.ok and e.ok and abs(z.value - e.value) / e.value < 0.02) \
                        else (e.value if e.ok else None)
                    rows.append(dict(device=name, commanded_hz=chz, want_hz=hz,
                                     cut_setting=cs, res=res, f_osc=f,
                                     rms=float(am.rms(y))))
                    print(f"  {name:12s} cut {hz:7.0f} (cmd {chz}) -> f_osc "
                          f"{f if f is None else round(f, 1)}", flush=True)
                out["tracking"] = rows
            if "peakdrive" in stages:
                print(" -- resonant peak versus drive, at 0.9 of each filter's own onset",
                      flush=True)
                cs, chz = cut_setting_for(dev, name, 800.0, cache)
                guard("peakdrive", lambda: stage_peakdrive(dev, name, cs, chz or 800.0))
            if "bigdrive" in stages:
                print(" -- where the saturation engages", flush=True)
                cs, chz = cut_setting_for(dev, name, 800.0, cache)
                guard("bigdrive", lambda: stage_bigdrive(dev, name, cs, chz or 800.0))
            if "drive" in stages:
                print(" -- drive", flush=True)
                cs, chz = cut_setting_for(dev, name, 800.0, cache)
                res = 0.6 if not name.startswith("ours") else 0.7
                guard("drive", lambda: stage_drive(dev, name, cs, chz or 800.0, res, 200.0,
                                                   [-48, -36, -24, -18, -12, -6, -3, 0]))
        finally:
            del dev
        for k, v in out.items():
            with open(os.path.join(a.out, f"{k}-{name}.json"), "w") as f:
                json.dump(v, f, indent=1, default=str)
        json.dump(cache, open(cache_path, "w"), indent=1)
    print(f"\nwritten to {a.out}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
