#!/usr/bin/env python3
"""#334 candidate: a numerator on the three tom/conga resonators (one mechanism).

tools/diagnose_tom_body.py localized the shared cause: in all six positions
98-100 % of the reference's above-split energy (the `body spectrum` numerator)
is the strike transient -- the first 10 ms after onset -- and ours is short
there by the same 11-18 dB the metric reports. Our tom is an ALL-POLE 2-pole
mode pinged by a 0.1 ms pulse, so above f0 it falls at 12 dB/octave. A
resonator with a numerator zero -- the modal bank's BP (1 - z^-2) or HP
((1 - z^-1)^2) -- falls 6 or 0 dB/octave less, which is the size of the gap.

The bank already implements numerators on its first N_NUMS = 11 modes; the tom
circuits sit on modes 11-13. BD/SDLO/SDHI (modes 8-10) are numerator-capable
but RAW, so a kit that swaps the two groups gives the toms numerators with no
RTL change. The bank's arithmetic is per mode and its mix is an exact integer
sum, so that remap is bit-identical to running the bank with nums = 16; this
experiment does the latter (asserted by the BD control below) and states it.

Candidate budget, fixed first: BP, HP. (Two of three; the third is held back.)
The mode's amp is scaled by 1 / |N(e^{jw0})| so the ring's level at f0 is
unchanged -- a candidate may not win by getting louder or quieter.

Selection rule (frozen): on the TOMS (D03A LT, D05A MT, D07A HT) the candidate
with the lowest worst body-spectrum distance, admissible only if every pitch /
pitch-drop and decay distance stays <= 1 and does not grow by > 0.10, and the
strike's bus peak stays within 1 dB of the baseline. Confirmation: the CONGAS
(D04A, D06A, D08A), untouched by selection: body spectrum must improve on all
three with pitch and decay preserved by the same rule.
"""
from __future__ import annotations

import argparse
import cmath
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

VOICES = {"LT": "D03A", "MT": "D05A", "HT": "D07A", "LC": "D04A", "MC": "D06A", "HC": "D08A"}
DEV = ("LT", "MT", "HT")
CIRCUIT = {"LT": dx.M_LT, "LC": dx.M_LT, "MT": dx.M_MT, "MC": dx.M_MT, "HT": dx.M_HT, "HC": dx.M_HT}
CANDIDATES = {"BP": dx.BP if hasattr(dx, "BP") else 1, "HP": dx.HP if hasattr(dx, "HP") else 2}
PLAN = {v: [m[0] for m in rc.DRUM_PLAN[v]] for v in VOICES}


def num_gain(kind: str, f0: float) -> float:
    z = cmath.exp(-1j * 2 * math.pi * f0 / dx.SR)
    return abs(1 - z * z) if kind == "BP" else abs((1 - z) ** 2)


def render(voice: str, kind: str | None, nums: int = dx.N_MODES) -> tuple:
    """render_drum_solo's exact render, with an optional numerator on the
    voice's circuit and its amp compensated at the sound's f0."""
    stop = dx.SOUND_STOP[voice]
    n = int(rc.SOLO_SECONDS.get(voice, 2.2) * dx.SR)
    d = dx.DrumsFx(nums=nums)
    kit = dict(dx.kit_with_sounds(voice))
    if kind is not None:
        m = CIRCUIT[voice]
        base = dx.A_MODE + m * dx.MODE_STRIDE
        f0 = dx.TOM_PRESET[voice][0]
        amp = kit[base + 2] / 65536.0 / num_gain(kind, f0)
        if amp >= 1.0:
            raise rc.Refused(f"{voice} {kind}: compensated amp {amp:.3f} does not fit Q0.16")
        kit[base + 2] = dx.amp_reg(amp)
        kit[base + 3] = CANDIDATES[kind]
    kit = sorted(kit.items())
    dm, bd = d.play(dx.hit_writes([(rc.DRUM_SOLO_HIT_FRAME, stop, 1.0)], kit), n)
    g = dx.accent_reg(0.45)
    out = dx.output_fx(np.zeros(n), 0, dm, g, bd, g)
    return np.asarray(out, dtype=np.float64) / 32768.0, dx.SR


def score(voice: str, x, sr, refdir) -> dict:
    ref_x, ref_sr, rel, _ = rc.load_reference(voice, refdir)
    req = PLAN[voice]
    metrics, _ = rc.drum_measurements(voice, x, sr, ref_x, ref_sr, rel, req)
    out = {}
    for k, m in metrics.items():
        if not m.get("valid"):
            out[k] = {"valid": False}
            continue
        out[k] = {"value": m["value"], "reference": m["reference"], "error": m["error"],
                  "distance": round(abs(m["error"]) / m["tolerance"], 3)}
    out["_peak_fs"] = round(float(np.max(np.abs(x))), 5)
    return out


def control_bit_identity() -> dict:
    """nums = 16 with every numerator still RAW must reproduce the shipped
    render exactly -- for a tom and for a sound that never touches the change."""
    res = {}
    for v in ("LT", "BD", "SD"):
        a, _ = rc.render_drum_solo(v)
        b, _ = render(v, None, nums=dx.N_MODES)
        res[v] = bool(np.array_equal(a, b))
    return res


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--refs", default=str(rc.configured_refs()))
    ap.add_argument("--out", type=pathlib.Path, required=True)
    a = ap.parse_args(argv)
    refdir = pathlib.Path(a.refs)
    ctl = control_bit_identity()
    print("bit-identity control (nums=16, all RAW):", ctl)
    if not all(ctl.values()):
        print("REFUSED: the nums=16 bank does not reproduce the shipped render")
        return 2
    rows = {}
    for kind in (None, "BP", "HP"):
        label = kind or "RAW (shipped)"
        rows[label] = {}
        for v in VOICES:
            x, sr = render(v, kind)
            rows[label][v] = score(v, x, sr, refdir)
            s = rows[label][v]
            print(f"{label:14s} {v} ({VOICES[v]}): " + "; ".join(
                f"{k} {s[k].get('value')} vs {s[k].get('reference')} (d {s[k].get('distance')})"
                for k in PLAN[v]) + f"; peak {s['_peak_fs']}", flush=True)
    base = rows["RAW (shipped)"]

    def verdict(kind, voices):
        why = []
        for v in voices:
            c, b = rows[kind][v], base[v]
            for k in PLAN[v]:
                if k == "body spectrum" or "distance" not in b[k]:
                    continue                  # the target, or already invalid at baseline
                if "distance" not in c[k]:
                    why.append(f"{v} {k} became invalid")
                elif c[k]["distance"] > 1 or c[k]["distance"] - b[k]["distance"] > 0.10:
                    why.append(f"{v} {k} {b[k]['distance']} -> {c[k]['distance']}")
            if abs(20 * math.log10(c["_peak_fs"] / b["_peak_fs"])) > 1.0:
                why.append(f"{v} bus peak {b['_peak_fs']} -> {c['_peak_fs']}")
        return why
    sel = {}
    for kind in CANDIDATES:
        why = verdict(kind, DEV)
        worst = max(rows[kind][v]["body spectrum"]["distance"] for v in DEV)
        sel[kind] = {"worst_dev_body_distance": worst, "admissible": not why, "reasons": why}
    base_worst = max(base[v]["body spectrum"]["distance"] for v in DEV)
    ok = sorted((s["worst_dev_body_distance"], k) for k, s in sel.items() if s["admissible"])
    chosen = ok[0][1] if ok and ok[0][0] < base_worst else None
    conf = None
    if chosen:
        why = verdict(chosen, ("LC", "MC", "HC"))
        improved = {v: (base[v]["body spectrum"]["distance"], rows[chosen][v]["body spectrum"]["distance"])
                    for v in ("LC", "MC", "HC")}
        conf = {"reasons": why, "body_spectrum_distance": improved,
                "passes": not why and all(b > c for b, c in improved.values())}
    res = {"control_bit_identity": ctl, "rows": rows, "selection": sel,
           "baseline_worst_dev_body_distance": base_worst, "chosen": chosen, "confirmation": conf,
           "commit": subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True,
                                    text=True).stdout.strip(),
           "sources_dirty": subprocess.run(["git", "diff", "--quiet", "HEAD", "--", "tools/probe_tom_numerator.py",
                                            "model/drums_fx.py", "model/modal_fixed.py", "tools/run_case.py"],
                                           cwd=ROOT).returncode != 0}
    print(json.dumps({k: res[k] for k in ("selection", "chosen", "confirmation")}, indent=1))
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text(json.dumps(res, indent=1) + "\n")
    print(f"wrote {a.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
