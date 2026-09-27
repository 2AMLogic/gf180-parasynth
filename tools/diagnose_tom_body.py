#!/usr/bin/env python3
"""Where the tom/conga "body spectrum" energy lives, reference versus model (#334).

`body spectrum` (tools/run_case.py `_split_db(sound, 0, 0.150)`) is
10*log10(E[split..2000] / E[20..split]) over the first 150 ms of the prepared
strike. All six tom/conga positions read 11-18 dB short on it. This asks one
question before any preset is touched: is the missing above-split energy of
the SAME kind in all six, and which kind?

The above-split energy of each side is split three ways, each independent of
the model:
  early      the band-passed (split..2000 Hz) energy in the first 10 ms: the
             strike transient
  harmonic   after 10 ms, energy within +-max(15 Hz, 6 %) of k * f0 (k >= 2),
             f0 measured on that side: a non-sinusoidal ring
  other      after 10 ms, everything else in the band: noise or inharmonic modes
Each is reported in dB relative to the window's total energy, so the six
voices and the two sides compare directly.

The run_case chain supplies both signals (load_reference, render_drum_solo,
prepare), so the numbers are the scorecard's own inputs. The kit digest is
checked against R1's pin, so "the selected engine" is asserted, not assumed.
"""
from __future__ import annotations

import argparse
import json
import math
import pathlib
import subprocess
import sys

import numpy as np
from scipy.signal import butter, sosfiltfilt

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "model"), str(ROOT / "tools")]
import run_case as rc  # noqa: E402

VOICES = {"LT": "D03A", "LC": "D04A", "MT": "D05A", "MC": "D06A", "HT": "D07A", "HC": "D08A"}
EARLY_S, WIN_S, TOP_HZ = 0.010, 0.150, 2000.0


def _db(x):
    return round(10 * math.log10(x), 3) if x > 0 else None


def tol_hz(k: int, f0: float) -> float:
    """Half-width of the k-th harmonic's mask: 6 % covers the onset pitch
    drop's residue, but it is capped at 0.2 f0 so the masks never cover more
    than 40 % of the spectrum. Uncapped, at k >= 8 the masks overlapped and
    every broadband component in the band read as "harmonic"."""
    return max(15.0, min(0.06 * k * f0, 0.2 * f0))


def decompose(y, sr, split, f0) -> dict:
    """Energy fractions of a prepared strike's first 150 ms (the scorer's window)."""
    w = np.asarray(y[: int(WIN_S * sr)], dtype=np.float64)
    total = float(np.sum(w * w))
    if total <= 0:
        raise rc.Refused("silent window")
    sos = butter(4, [split, TOP_HZ], btype="bandpass", fs=sr, output="sos")
    hb = sosfiltfilt(sos, np.concatenate([np.zeros(sr // 10), w]))[sr // 10:]
    ne = int(EARLY_S * sr)
    e_early = float(np.sum(hb[:ne] ** 2)) / total
    e_late_band = float(np.sum(hb[ne:] ** 2)) / total
    # Which part of the late band energy sits on harmonics: a Blackman-Harris
    # windowed spectrum of the late segment, so the fundamental's truncation
    # leakage (a rectangular cut of a decaying 100-400 Hz ring spreads -27 dB
    # across the band) does not read as "other". Only the SHARE comes from the
    # spectrum; the energy comes from the same band-pass the scorer uses.
    late = w[ne:]
    m = len(late)
    t = 2 * np.pi * np.arange(m) / m
    bh = 0.35875 - 0.48829 * np.cos(t) + 0.14128 * np.cos(2 * t) - 0.01168 * np.cos(3 * t)
    n = 1 << int(math.ceil(math.log2(m * 8)))
    spec = np.abs(np.fft.rfft(late * bh, n)) ** 2
    f = np.fft.rfftfreq(n, 1.0 / sr)
    band = (f >= split) & (f < TOP_HZ)
    harm = np.zeros_like(band)
    k = 2
    while k * f0 < TOP_HZ + 0.1 * f0:
        harm |= np.abs(f - k * f0) <= tol_hz(k, f0)
        k += 1
    sb = float(spec[band].sum())
    share_h = float(spec[band & harm].sum()) / sb if sb > 0 else 0.0
    e_h, e_o = e_late_band * share_h, e_late_band * (1 - share_h)
    lev = spec * ((float(np.sum(late * late)) / total) / float(spec.sum()))
    return {"early_db": _db(e_early), "harmonic_db": _db(e_h),
            "other_db": _db(e_o),
            "early_share": round(e_early / (e_early + e_h + e_o), 3),
            "harmonic_share": round(e_h / (e_early + e_h + e_o), 3),
            "other_share": round(e_o / (e_early + e_h + e_o), 3),
            # after 10 ms only, and what flat (inharmonic) noise would give:
            # a harmonic ring reads ~1.0, noise reads ~mask_coverage
            "late_harmonic_share": round(share_h, 3),
            "mask_coverage": round(float((band & harm).sum()) / float(band.sum()), 3),
            "harmonic_levels_db": _harmonic_levels(lev, f, f0, 1.0)}


def _harmonic_levels(spec, f, f0, total):
    out = {}
    for k in range(1, 6):
        sel = np.abs(f - k * f0) <= tol_hz(k, f0)
        out[f"h{k}"] = _db(float(spec[sel].sum()) / total)
    return out


BLOCKS = ((0.010, 0.030), (0.030, 0.060), (0.060, 0.100), (0.100, 0.150),
          (0.150, 0.300), (0.300, 0.600))


def time_profile(y, sr, split) -> dict:
    """Band-passed (split..2000 Hz) and full-band RMS per block, dB re the
    prepared strike's peak, plus the same for the file's last 200 ms. If the
    above-split energy decays with the ring it belongs to the strike; if it
    sits at the tail's level it is the recording's floor, not the instrument."""
    y = np.asarray(y, dtype=np.float64)
    sos = butter(4, [split, TOP_HZ], btype="bandpass", fs=sr, output="sos")
    hb = sosfiltfilt(sos, y)
    pk = float(np.max(np.abs(y)))

    def rms_db(x):
        r = float(np.sqrt(np.mean(x * x))) if len(x) else 0.0
        return round(20 * math.log10(r / pk), 2) if r > 0 else None
    out = {}
    for a, b in BLOCKS:
        i, j = int(a * sr), min(len(y), int(b * sr))
        if j - i > sr // 200:
            out[f"{int(a*1e3)}-{int(b*1e3)}ms"] = {"band": rms_db(hb[i:j]), "full": rms_db(y[i:j])}
    tail = slice(max(0, len(y) - sr // 5), len(y))
    out["file_tail_200ms"] = {"band": rms_db(hb[tail]), "full": rms_db(y[tail]),
                              "at_s": round(len(y) / sr, 3)}
    return out


def fine_profile(y, sr, split, f0) -> dict:
    """2 ms blocks over the first 60 ms: above-split and full-band RMS (dB re
    peak), and the above-split band's spectral centroid and harmonic share
    over 10-30 ms, where both sides hold nearly all of that energy."""
    y = np.asarray(y, dtype=np.float64)
    sos = butter(4, [split, TOP_HZ], btype="bandpass", fs=sr, output="sos")
    hb = sosfiltfilt(sos, y)
    pk = float(np.max(np.abs(y)))
    step = int(0.002 * sr)
    band, full = [], []
    for i in range(0, int(0.060 * sr), step):
        band.append(round(20 * math.log10(max(float(np.sqrt(np.mean(hb[i:i + step] ** 2))), 1e-12) / pk), 1))
        full.append(round(20 * math.log10(max(float(np.sqrt(np.mean(y[i:i + step] ** 2))), 1e-12) / pk), 1))
    seg = y[int(0.010 * sr):int(0.030 * sr)]
    m = len(seg)
    t = 2 * np.pi * np.arange(m) / m
    bh = 0.35875 - 0.48829 * np.cos(t) + 0.14128 * np.cos(2 * t) - 0.01168 * np.cos(3 * t)
    n = 1 << 16
    sp = np.abs(np.fft.rfft(seg * bh, n)) ** 2
    f = np.fft.rfftfreq(n, 1.0 / sr)
    bnd = (f >= split) & (f < TOP_HZ)
    cen = float((f[bnd] * sp[bnd]).sum() / sp[bnd].sum())
    return {"band_2ms_db": band, "full_2ms_db": full, "centroid_10_30ms_hz": round(cen, 1),
            "peak_bin_10_30ms_hz": round(float(f[bnd][np.argmax(sp[bnd])]), 1),
            "peak_bin_over_f0": round(float(f[bnd][np.argmax(sp[bnd])]) / f0, 3)}


def kit_identity() -> dict:
    import drums_fx as dx
    pinned = json.loads((ROOT / "fpga/release/r1-candidate.json").read_text())["kit"]["sha256"]
    got = dx._kit_sha256(dx.kit_808())
    return {"pinned_r1": pinned, "current": got, "matches": got == pinned}


def diagnose(refdir: pathlib.Path) -> dict:
    rows = {}
    for voice, case in VOICES.items():
        ref_x, ref_sr, rel, setting = rc.load_reference(voice, refdir)
        ours_x, ours_sr = rc.render_drum_solo(voice)
        ref_y = rc.prepare(ref_x, ref_sr, side=rel)
        ours_y = rc.prepare(ours_x, ours_sr, side=f"our {voice}")
        split = rc.SPLIT_HZ[voice]
        row = {"case": case, "reference": rel, "split_hz": split}
        for side, (y, sr) in (("reference", (ref_y, ref_sr)), ("ours", (ours_y, ours_sr))):
            bs = rc._split_db(voice, 0.0, WIN_S)(y, sr)
            f0 = rc._f0(voice, 0.010, 0.200)(y, sr)
            if not bs.ok or not f0.ok:
                raise rc.Refused(f"{voice} {side}: {bs.reason or f0.reason}")
            row[side] = {"body_spectrum_db": round(bs.value, 3), "f0_hz": round(f0.value, 2),
                         "sr": sr, **decompose(y, sr, split, f0.value),
                         "time_profile": time_profile(y, sr, split),
                         "fine_profile": fine_profile(y, sr, split, f0.value)}
        r, o = row["reference"], row["ours"]
        row["deficit_db"] = {k: (None if r[k] is None or o[k] is None else round(o[k] - r[k], 2))
                             for k in ("early_db", "harmonic_db", "other_db")}
        rows[voice] = row
        print(f"{voice} ({case}) body {o['body_spectrum_db']:+.1f} vs {r['body_spectrum_db']:+.1f} | "
              f"ref shares early/harm/other {r['early_share']}/{r['harmonic_share']}/{r['other_share']} | "
              f"ours {o['early_share']}/{o['harmonic_share']}/{o['other_share']} | "
              f"ours-ref dB early {row['deficit_db']['early_db']} harm {row['deficit_db']['harmonic_db']} "
              f"other {row['deficit_db']['other_db']}", flush=True)
    return rows


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--refs", default=str(rc.configured_refs()))
    ap.add_argument("--out", type=pathlib.Path, required=True)
    a = ap.parse_args(argv)
    kit = kit_identity()
    print(f"kit: current {kit['current'][:12]} vs R1 pin {kit['pinned_r1'][:12]} -> "
          f"{'MATCH' if kit['matches'] else 'DIFFERENT'}")
    try:
        rows = diagnose(pathlib.Path(a.refs))
    except rc.Refused as e:
        print(f"REFUSED: {e}")
        return 2
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True).stdout.strip()
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text(json.dumps({"kit": kit, "commit": head, "rows": rows}, indent=1) + "\n")
    print(f"wrote {a.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
