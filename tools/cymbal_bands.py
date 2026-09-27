#!/usr/bin/env python3
"""Per-band energy and decay of a TR-808 cymbal strike (#369), qualified before use.

The 808 cymbal is three bands (docs/tr808-reference.md §10): a LOW band
(3.45 kHz band-pass -> Hh1 2.5 kHz high-pass, medium fixed decay) and two
HIGH bands that share the 7.1 kHz band-pass (one with the DECAY knob's RC,
one short and fixed, through the ~10.5 kHz Hh3). A recording holds only
their sum, so the bands are read two ways:

  by FREQUENCY  L = 2-5 kHz (the low band), H = 6-14 kHz (both high bands)
  by TIME       inside H, the short band dominates the first milliseconds
                and the DECAY band the tail, so H is read twice: EDT10 (the
                fall from the band's peak to -10 dB) and a LATE T20 (a line
                fitted from -10 to -30 dB, scaled to 20 dB)

Per band: energy share of the strike's first 1.0 s (dB re the whole signal
in 200 Hz-20 kHz), EDT10 and T20_late off the band's floor-subtracted
Schroeder curve. Every band-pass is zero-phase and run from prepare()'s
guaranteed lead (#101: never from mid-strike).

REFUSES, per quantity, rather than answering:
  * the band's floor-subtracted Schroeder curve does not reach -30 dB, or the
    record ends less than 15 dB (of envelope) after the -30 dB point;
  * the late line's residual on the Schroeder curve exceeds 1.5 dB;
  * the band holds less than 1e-6 of the energy.
Known answers: tools/test_cymbal_bands.py (synthetic three-band strikes with
planted shares and time constants; invariances; controls that must move).
"""
from __future__ import annotations

import argparse
import json
import math
import pathlib
import sys

import numpy as np
from scipy.signal import butter, sosfiltfilt

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "model"), str(ROOT / "tools")]
import run_case as rc  # noqa: E402

BANDS = {"L": (2000.0, 5000.0), "Ln": (2900.0, 4100.0), "H": (6000.0, 14000.0)}
# Ln: the low band's 3.45 kHz peak only. At its edges the shared 7.1 kHz Q 6
# band-pass is ~17 dB down (at 5 kHz only ~13 dB), so Ln is where the low
# band's own decay can be separated from the DECAY band's lower skirt.
TOTAL = (200.0, 20000.0)
ENERGY_S = 1.0
BLOCK_S = 0.005
LATE = (-10.0, -30.0)
FLOOR_MARGIN_DB = 10.0
MAX_RESID_DB = 1.5
TRUNC_MARGIN_DB = 15.0


class Refused(RuntimeError):
    pass


def _bp(y, sr, lo, hi):
    hi = min(hi, 0.45 * sr)
    sos = butter(4, [lo, hi], btype="bandpass", fs=sr, output="sos")
    o = rc.required_lead_samples(sr)
    return sosfiltfilt(sos, y)[o:]              # t = 0 as the scorer defines it


def _env_db(x, sr):
    n = int(BLOCK_S * sr)
    m = len(x) // n
    r = np.sqrt(np.mean(x[:m * n].reshape(m, n) ** 2, axis=1))
    return 20 * np.log10(np.maximum(r, 1e-12)), n


def band_decay(x, sr) -> dict:
    """EDT10 and late T20 of one band, off its backward-integrated (Schroeder)
    energy curve with the record's noise floor subtracted.

    The first version fitted the 5 ms RMS envelope directly and refused every
    808 file: six beating squares make that envelope wander 2-4 dB about its
    trend, so a 1.5 dB residual bound read the beating as "not exponential".
    The Schroeder curve is monotone by construction and averages the beating
    out; the floor (mean power of the last 100 ms) is subtracted first so a
    constant floor does not flatten the tail.
    Truncation guard: the band's 50 ms envelope at the record's end must be at
    least TRUNC_MARGIN_DB below its level where the curve crosses -30 dB, which
    bounds the missing-tail bias at that point to about 0.14 dB."""
    x = np.asarray(x, dtype=np.float64)
    p = x * x
    nf = max(4, int(0.1 * sr))
    floor = float(np.mean(p[-nf:]))
    sm = int(0.05 * sr)
    env = np.convolve(p, np.ones(sm) / sm, mode="same")
    pk = float(np.max(env))
    out = {"floor_db_re_peak": round(10 * math.log10(max(floor, 1e-30) / pk), 2)}
    q = np.maximum(p - floor, 0.0)
    sch = np.cumsum(q[::-1])[::-1]
    if sch[0] <= 0:
        raise Refused("band holds no energy above its floor")
    c = 10 * np.log10(np.maximum(sch / sch[0], 1e-30))
    t = np.arange(len(c)) / sr
    i10 = np.nonzero(c <= -10.0)[0]
    out["edt10_ms"] = round(1e3 * t[i10[0]], 2) if len(i10) else None
    i30 = np.nonzero(c <= LATE[1])[0]
    if not len(i10) or not len(i30):
        out["t20_late_ms"] = None
        out["t20_refused"] = "the energy curve does not reach -30 dB"
        return out
    a, b = i10[0], i30[0]
    lvl30 = 10 * math.log10(max(env[b], 1e-30) / pk)
    lvl_end = 10 * math.log10(max(float(np.mean(env[-sm:])), 1e-30) / pk)
    out["end_margin_db"] = round(lvl30 - lvl_end, 2)
    if lvl30 - lvl_end < TRUNC_MARGIN_DB:
        out["t20_late_ms"] = None
        out["t20_refused"] = (f"record too short: the envelope at the end is only {lvl30 - lvl_end:.1f} dB "
                              f"below its level at the -30 dB point (need {TRUNC_MARGIN_DB})")
        return out
    step = max(1, (b - a) // 400)
    tt, cc = t[a:b + 1:step], c[a:b + 1:step]
    slope, icpt = np.polyfit(tt, cc, 1)
    resid = float(np.max(np.abs(cc - (slope * tt + icpt))))
    out["late_residual_db"] = round(resid, 2)
    if resid > MAX_RESID_DB or slope >= 0:
        out["t20_late_ms"] = None
        out["t20_refused"] = f"late decay is not one exponential (Schroeder residual {resid:.1f} dB)"
        return out
    out["t20_late_ms"] = round(-20.0 / slope * 1e3, 2)
    return out


def measure(y, sr) -> dict:
    """`y` must come through run_case.prepare (the guaranteed lead)."""
    y = np.asarray(y, dtype=np.float64)
    o = rc.required_lead_samples(sr)
    n_e = int(ENERGY_S * sr)
    tot = _bp(y, sr, *TOTAL)
    e_tot = float(np.sum(tot[:n_e] ** 2))
    if e_tot <= 0:
        raise Refused("silent")
    res = {"sr": sr, "record_s": round((len(y) - o) / sr, 3)}
    for name, (lo, hi) in BANDS.items():
        xb = _bp(y, sr, lo, hi)
        eb = float(np.sum(xb[:n_e] ** 2))
        r = {"energy_share_db": round(10 * math.log10(eb / e_tot), 3) if eb > 1e-6 * e_tot else None}
        r.update(band_decay(xb, sr))
        res[name] = r
    res["H_minus_L_db"] = (None if res["H"]["energy_share_db"] is None or res["L"]["energy_share_db"] is None
                           else round(res["H"]["energy_share_db"] - res["L"]["energy_share_db"], 3))
    return res


THIRDS = tuple(1000.0 * 2 ** (k / 3) for k in range(0, 14))     # 1.0 .. 20.2 kHz centres


def thirds(y, sr, t0=0.0, t1=ENERGY_S) -> dict:
    """1/3-octave energy (dB re the 200 Hz-20 kHz energy) in [t0, t1) after
    t = 0, zero-phase from the lead. Finer than L/H: where inside a band the
    two sides differ."""
    y = np.asarray(y, dtype=np.float64)
    tot = _bp(y, sr, *TOTAL)
    a, b = int(t0 * sr), int(t1 * sr)
    e_tot = float(np.sum(tot[a:b] ** 2))
    out = {}
    for fc in THIRDS:
        lo, hi = fc / 2 ** (1 / 6), min(fc * 2 ** (1 / 6), 0.45 * sr)
        if lo >= hi:
            continue
        xb = _bp(y, sr, lo, hi)
        out[f"{fc:.0f}"] = round(10 * math.log10(max(float(np.sum(xb[a:b] ** 2)), 1e-30) / e_tot), 2)
    return out


def shipped_vs_fischer(refs: pathlib.Path) -> dict:
    """The SHIPPED cymbal (run_case.render_drum_solo, the kit R1 plays, which
    has no TONE/DECAY knob) against its anchor recording CY5025, on the
    band measures and 1/3-octave energy in three time windows."""
    x, sr = rc.render_drum_solo("CY")
    ours_y = rc.prepare(x, sr, side="shipped CY")
    rx, rsr = _load(refs / "cy8" / "CY5025.WAV")
    ref_y = rc.prepare(rx, rsr, side="CY5025")
    res = {"shipped": measure(ours_y, sr), "fischer_CY5025": measure(ref_y, rsr), "thirds": {}}
    for label, (t0, t1) in {"0-50ms": (0.0, 0.05), "50-300ms": (0.05, 0.3), "300-1000ms": (0.3, 1.0)}.items():
        res["thirds"][label] = {"shipped": thirds(ours_y, sr, t0, t1), "fischer": thirds(ref_y, rsr, t0, t1)}
    return res


def fischer(refs: pathlib.Path) -> dict:
    """Every Fischer CY file: CY{TONE}{DECAY}.WAV (DR 0022's decode)."""
    out = {}
    for tone in ("00", "10", "25", "50", "75"):
        for decay in ("00", "10", "25", "50", "75"):
            p = refs / "cy8" / f"CY{tone}{decay}.WAV"
            x, sr = _load(p)
            out[f"CY{tone}{decay}"] = measure(rc.prepare(x, sr, side=p.name), sr)
    return out


def _load(p):
    from scipy.io import wavfile
    sr, x = wavfile.read(str(p))
    x = np.asarray(x, dtype=np.float64)
    if x.ndim > 1:
        x = x.mean(axis=1)
    return x / 32768.0, sr


KNOB = {"00": 0.0, "10": 10.0, "25": 2.5, "50": 5.0, "75": 7.5}
CODES = ("00", "10", "25", "50", "75")
# FROZEN before any candidate is rendered or judged (#369 acceptance 3). The
# development subset is the settings the knob laws were fitted on (TONE 5.0
# column and DECAY 5.0 row; model/test_discrimination FIT_KNOBS reads 0/5/10
# of each) plus the D14A anchor CY5025. Everything else -- including CY2500,
# D14B's holdout -- is confirmation only.
DEVELOPMENT = tuple(sorted({f"CY50{d}" for d in CODES} | {f"CY{t}50" for t in CODES}))
CONFIRMATION = tuple(sorted(f"CY{t}{d}" for t in CODES for d in CODES if f"CY{t}{d}" not in DEVELOPMENT))


def ours(refs: pathlib.Path, seconds: float = 4.0) -> dict:
    """Our cymbal at every Fischer setting, through the kit's knob laws
    (model/test_discrimination.kit_at, the 'ours' arm) and render_drum_solo's
    exact render path: one strike at accent 1.0, both drum buses at 0.45."""
    import drums_fx as dx
    import test_discrimination as td
    laws = td.fit_laws(str(refs), all_sounds=True)
    out = {}
    n = int(seconds * dx.SR)
    for t in CODES:
        for d in CODES:
            kit = td.kit_at("CY", (KNOB[t], KNOB[d]), laws, "ours")
            dm, bd = dx.DrumsFx().play(dx.hit_writes([(rc.DRUM_SOLO_HIT_FRAME, dx.SOUND_STOP["CY"], 1.0)], kit), n)
            g = dx.accent_reg(0.45)
            y = np.asarray(dx.output_fx(np.zeros(n), 0, dm, g, bd, g), dtype=np.float64) / 32768.0
            out[f"CY{t}{d}"] = measure(rc.prepare(y, dx.SR, side=f"our CY{t}{d}"), dx.SR)
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--refs", default=str(rc.configured_refs()))
    ap.add_argument("--out", type=pathlib.Path, required=True)
    ap.add_argument("--ours", action="store_true", help="measure our cymbal at every setting instead")
    ap.add_argument("--shipped", action="store_true", help="the shipped kit against CY5025, with 1/3 octaves")
    a = ap.parse_args(argv)
    if a.shipped:
        res = shipped_vs_fischer(pathlib.Path(a.refs))
        for w, d in res["thirds"].items():
            print(w, " ".join(f"{fc}:{d['shipped'][fc] - d['fischer'][fc]:+.1f}" for fc in d["shipped"]))
        a.out.parent.mkdir(parents=True, exist_ok=True)
        a.out.write_text(json.dumps(res, indent=1) + "\n")
        return 0
    res = ours(pathlib.Path(a.refs)) if a.ours else fischer(pathlib.Path(a.refs))
    res["_split"] = {"development": list(DEVELOPMENT), "confirmation": list(CONFIRMATION)}
    print(f"{'file':8s} {'tone':>4s} {'decay':>5s} | {'L share':>7s} {'L EDT':>6s} {'L T20':>6s} | "
          f"{'H share':>7s} {'H EDT':>6s} {'H T20':>6s} | H-L")
    for k in sorted((k for k in res if not k.startswith("_")), key=lambda k: (KNOB[k[2:4]], KNOB[k[4:6]])):
        r = res[k]
        f = lambda v: "   REF" if v is None else f"{v:6.1f}"
        print(f"{k:8s} {KNOB[k[2:4]]:4.1f} {KNOB[k[4:6]]:5.1f} | {r['L']['energy_share_db']:7.2f} "
              f"{f(r['L']['edt10_ms'])} {f(r['L']['t20_late_ms'])} | Ln {f(r['Ln']['edt10_ms'])} "
              f"{f(r['Ln']['t20_late_ms'])} | {r['H']['energy_share_db']:7.2f} "
              f"{f(r['H']['edt10_ms'])} {f(r['H']['t20_late_ms'])} | {r['H_minus_L_db']:6.2f}")
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text(json.dumps(res, indent=1) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
