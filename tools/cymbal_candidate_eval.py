#!/usr/bin/env python3
"""Evaluate the #369 §10 cymbal candidate (model/cymbal_candidate.py) at the shipped setting.

Order, fixed:
 1. LEVELS by the candidate's own rule: each band matched to the shipped kit's
    same band in the 1/3 octave at its centre (low 3.15 kHz, high bands
    10 kHz), first second of a strike. Amp up to its Q0.16 ceiling, the rest on
    the band's envelope peak (the VCA path is linear in the envelope).
 2. PRESERVATION: every one of the 16 sounds other than CY must render
    bit-identically to the shipped kit on the 19-mode layout (the remap and
    the added paths may touch nothing else). Hats included: D15A, D16A, OH.
 3. The shipped-vs-808 1/3-octave comparison (tools/cymbal_bands.thirds),
    repeated for the candidate: the 1-2.5 kHz strike excess and the 16-20 kHz
    deficit must close for the change to be worth taking forward.
 4. Band measures (energy share, EDT10, late T20) for 808 CY5025, shipped and
    candidate.
Knob tracking is NOT claimed here: the knob-law render (kit_at) is not the
instrument (#371) and gets its own labelled repair.
"""
from __future__ import annotations

import argparse
import json
import math
import pathlib
import subprocess
import sys

import numpy as np

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "model"), str(ROOT / "tools")]
import drums_fx as dx  # noqa: E402
import run_case as rc  # noqa: E402
import cymbal_candidate as cc  # noqa: E402
import cymbal_bands as cb  # noqa: E402

CENTRE = {"low": "3175", "decay": "10079", "short": "10079"}
LEVEL_MODE = {"low": cc.M_CYH1, "decay": dx.M_CYHI, "short": cc.M_CYH3B}
LEVEL_ENV = {"low": dx.E_CYL, "decay": dx.E_CYD, "short": dx.E_CYS}
AMP_MAX = 65535 / 65536


def calibrate() -> dict:
    shipped = dx.kit_808()
    unit = sorted(_variant(dict(cc.candidate_kit({cc.M_CYH1: 0.25, dx.M_CYHI: 0.25, cc.M_CYH3B: 0.25}))).items())
    out, amps = {}, {}
    for band in ("low", "decay", "short"):
        ys, sr = cc.render_shipped(cc.band_only(shipped, band), "CY")
        yc, _ = cc.render(cc.band_only(unit, band), "CY")
        # thirds() is dB re each render's own total; compare absolute energy instead
        es = _abs_third(ys, sr, CENTRE[band])
        ec = _abs_third(yc, sr, CENTRE[band])
        gain = math.sqrt(es / ec)
        amp = 0.25 * gain
        env_gain = 1.0
        if amp > AMP_MAX:
            env_gain, amp = amp / AMP_MAX, AMP_MAX
        out[band] = {"shipped_abs": es, "unit_abs": ec, "amp": amp, "env_gain": env_gain}
        amps[LEVEL_MODE[band]] = amp
        if env_gain != 1.0:
            base = dict(dx.kit_808())[dx.A_ENV + LEVEL_ENV[band] * dx.ENV_STRIDE + 1] / dx.FULL24
            if base * env_gain > 1.0:
                if VARIANT == "full":
                    raise SystemExit(f"REFUSED: {band} band needs envelope peak {base * env_gain:.3f} > 1.0")
                # a diagnostic ablation may fall short of the level rule; record by how much
                out[band]["level_short_db"] = round(20 * math.log10(base * env_gain), 2)
                env_gain = 1.0 / base
            amps[f"E_{band}"] = base * env_gain
    return {"per_band": out, "amps": amps}


def _abs_third(y, sr, fc):
    from scipy.signal import butter, sosfiltfilt
    f = float(fc)
    lo, hi = f / 2 ** (1 / 6), min(f * 2 ** (1 / 6), 0.45 * sr)
    o = int(0.01 * sr)                       # the strike frame (480): the first second after it
    x = sosfiltfilt(butter(4, [lo, hi], btype="bandpass", fs=sr, output="sos"), y)[o:o + sr]
    return float(np.sum(x * x))


VARIANT = "full"


def _variant(d):
    """Diagnostic ablation of the fixed candidate (not a selection): 'notilt'
    removes the level-stage differentiator -- Hh1 and Hh2 back to HP, and Hh3's
    1-pole stage made a pass-through (the bank has no (1 - z^-1) numerator, so
    Hh3 is its 2-pole alone there)."""
    if VARIANT == "notilt":
        for m in (cc.M_CYH1, dx.M_CYHI):
            d[dx.A_MODE + m * dx.MODE_STRIDE + 3] = cc.HP
        base = dx.A_MODE + cc.M_CYH3B * dx.MODE_STRIDE
        d[base], d[base + 1], d[base + 3] = 0, 0, 0
    return d


def kit_with_levels(amps, sound="CY"):
    k = cc.candidate_kit({m: v for m, v in amps.items() if isinstance(m, int)},
                         kit=dx.kit_with_sounds(sound))
    d = _variant(dict(k))
    for band, e in LEVEL_ENV.items():
        if f"E_{band}" in amps:
            d[dx.A_ENV + e * dx.ENV_STRIDE + 1] = dx.peak_reg(amps[f"E_{band}"])
    return sorted(d.items())


def preservation(amps) -> dict:
    res = {}
    for s in dx.SOUND_NAMES:
        if s == "CY":
            continue
        a, _ = rc.render_drum_solo(s)
        b, _ = cc.render(kit_with_levels(amps, s), s, seconds=rc.SOLO_SECONDS.get(s, 2.2))
        n = min(len(a), len(b))
        res[s] = bool(np.array_equal(a[:n], b[:n]) and len(a) == len(b))
    return res


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--refs", default=str(rc.configured_refs()))
    ap.add_argument("--out", type=pathlib.Path, required=True)
    ap.add_argument("--wavs", type=pathlib.Path, default=None, help="write shipped/candidate CY renders here")
    ap.add_argument("--variant", choices=("full", "notilt"), default="full")
    a = ap.parse_args(argv)
    global VARIANT
    VARIANT = a.variant
    refs = pathlib.Path(a.refs)
    cal = calibrate()
    print("levels:", json.dumps({k: {kk: round(vv, 5) for kk, vv in v.items()} for k, v in cal["per_band"].items()}))
    pres = preservation(cal["amps"])
    print("preservation (bit-identical to shipped):", pres)
    ys, sr = rc.render_drum_solo("CY")
    yc, _ = cc.render(kit_with_levels(cal["amps"]), "CY")
    rx, rsr = cb._load(refs / "cy8" / "CY5025.WAV")
    P = lambda y, s, side: rc.prepare(y, s, side=side)
    res = {"variant": VARIANT, "levels": cal, "preservation": pres, "bands": {}, "thirds": {}}
    for label, (y, s) in {"fischer_CY5025": (rx, rsr), "shipped": (ys, sr), "candidate": (yc, sr)}.items():
        res["bands"][label] = cb.measure(P(y, s, label), s)
    for w, (t0, t1) in {"0-50ms": (0.0, 0.05), "50-300ms": (0.05, 0.3), "300-1000ms": (0.3, 1.0)}.items():
        f = cb.thirds(P(rx, rsr, "808"), rsr, t0, t1)
        sh = cb.thirds(P(ys, sr, "shipped"), sr, t0, t1)
        ca = cb.thirds(P(yc, sr, "cand"), sr, t0, t1)
        res["thirds"][w] = {"shipped_minus_808": {k: round(sh[k] - f[k], 2) for k in f},
                            "candidate_minus_808": {k: round(ca[k] - f[k], 2) for k in f}}
        print(w, "shipped-808  ", " ".join(f"{k}:{sh[k] - f[k]:+.1f}" for k in f))
        print(w, "candidate-808", " ".join(f"{k}:{ca[k] - f[k]:+.1f}" for k in f))
    for label, m in res["bands"].items():
        print(f"{label:16s} H-L {m['H_minus_L_db']:6.2f}  H EDT {m['H']['edt10_ms']}  Ln EDT {m['Ln']['edt10_ms']}  "
              f"H T20 {m['H']['t20_late_ms']}")
    if a.wavs:
        from scipy.io import wavfile
        a.wavs.mkdir(parents=True, exist_ok=True)
        for name, y in (("shipped", ys), ("candidate", yc)):
            wavfile.write(a.wavs / f"CY5025-{name}{'' if VARIANT == 'full' or name == 'shipped' else '-' + VARIANT}.wav", sr, np.clip(y * 32767, -32768, 32767).astype(np.int16))
    res["commit"] = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True).stdout.strip()
    res["sources_dirty"] = subprocess.run(["git", "diff", "--quiet", "HEAD", "--", "model", "tools"], cwd=ROOT).returncode != 0
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text(json.dumps(res, indent=1, default=float) + "\n")
    return 0 if all(pres.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
