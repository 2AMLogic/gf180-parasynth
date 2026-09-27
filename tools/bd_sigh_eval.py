#!/usr/bin/env python3
"""#379 fix 1: the BD's circuit-derived sigh (model/bd_circuit_sim.py,
drums_fx.bd_sigh_writes), judged by the perceptual gate on every Fischer BD
setting against THAT setting's own nearest-neighbour bar.

FROZEN BEFORE ANY CANDIDATE WAS RENDERED:
  development  the TONE 5.0 column and the DECAY 5.0 row (9 settings, the
               cymbal's rule from #371); untouched = the other 16
  candidates   BD_SIGH_VTRIG in {4, 8, 14} V (W14a 3's range and its middle);
               0 = shipped (no sigh), the baseline
  our render   the shipped kit, the BD body's Q from the shipped DECAY law
               (drums_fx.bd_decay_q) at the setting's DECAY; TONE is not a
               shipped BD control and stays at the kit's. The recordings'
               glide is TONE-independent (docs/scorecard/bd-sigh-379/).
  selection    the candidate with the smallest WORST BD ratio over the
               development settings, among those where no feature's ratio
               rises by more than NOISE on any development setting
  promotion    (operator rule) the ratio drops on the untouched settings, no
               feature worse beyond NOISE, no other sound moves (bit-identical)
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys
from multiprocessing import Pool

import numpy as np

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "model"), str(ROOT / "tools")]
import drums_fx as dx  # noqa: E402
import perceptual_gate as g  # noqa: E402

CODES = g.CODES
KNOB = {"00": 0.0, "25": 2.5, "50": 5.0, "75": 7.5, "10": 10.0}
SETTINGS = [t + d for t in CODES for d in CODES]
DEV = sorted({t + "50" for t in CODES} | {"50" + d for d in CODES})
UNTOUCHED = [s for s in SETTINGS if s not in DEV]
CANDIDATES = (0.0, 4.0, 8.0, 14.0)
NOISE = 0.05                      # ratio units: a feature "worse beyond noise"
HIT, SECONDS = 480, 2.2


def render_bd(decay_knob: float, v_trig: float, accent: float = 1.0) -> np.ndarray:
    dx.BD_SIGH_VTRIG = v_trig
    kit = dict(dx.kit_with_sounds("BD"))
    base = dx.A_MODE + dx.M_BD * dx.MODE_STRIDE
    amp = kit[base + 2] / 65536.0
    for a, v in dx.mode_writes(dx.M_BD, dx.BD_HZ, dx.bd_decay_q(decay_knob), amp, dx.RAW):
        kit[a] = v
    n = int(SECONDS * dx.SR)
    d = dx.DrumsFx()
    dm, bd = d.play(dx.hit_writes([(HIT, dx.SOUND_STOP["BD"], accent)], sorted(kit.items())), n)
    gg = dx.accent_reg(0.45)
    return np.asarray(dx.output_fx(np.zeros(n), 0, dm, gg, bd, gg), dtype=np.float64) / 32768.0


def _job(args):
    refs, setting, vt = args
    rel = f"bd8/BD{setting}.WAV"
    T = g.Target(*g.load_wav(refs / rel), "BD", rel)
    b = g.bar_for("BD", refs, T, rel)
    y = render_bd(KNOB[setting[2:]], vt)
    v = g.verdict(T.distance(y, dx.SR, f"BD vt={vt}"), b["bar"])
    return setting, vt, {"worst": v["worst_ratio"], "worst_feature": v["worst_feature"],
                         "ratios": {f: x.get("ratio") for f, x in v["features"].items()},
                         "bar_from": b["from"]}


def preservation(vt: float) -> dict:
    import run_case as rc
    out = {}
    dx.BD_SIGH_VTRIG = 0.0
    y0 = render_bd(5.0, 0.0)
    ship, _ = rc.render_drum_solo("BD")
    out["BD_sigh_off_equals_shipped"] = bool(np.array_equal(y0, ship))
    for s in dx.SOUND_NAMES:
        if s == "BD":
            continue
        dx.BD_SIGH_VTRIG = 0.0
        a, _ = rc.render_drum_solo(s)
        dx.BD_SIGH_VTRIG = vt
        b, _ = rc.render_drum_solo(s)
        out[s] = bool(np.array_equal(a, b))
    dx.BD_SIGH_VTRIG = 0.0
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--refs", type=pathlib.Path, required=True)
    ap.add_argument("--out", type=pathlib.Path, required=True)
    ap.add_argument("--jobs", type=int, default=4)
    a = ap.parse_args(argv)
    jobs = [(a.refs, s, vt) for vt in CANDIDATES for s in SETTINGS]
    with Pool(a.jobs) as pool:
        rows = pool.map(_job, jobs)
    res = {"dev": DEV, "untouched": UNTOUCHED, "noise": NOISE, "by": {}}
    for s, vt, r in rows:
        res["by"].setdefault(str(vt), {})[s] = r
    base = res["by"]["0.0"]
    summary = {}
    for vt in CANDIDATES[1:]:
        c = res["by"][str(vt)]
        worse = {s: [f for f, r in c[s]["ratios"].items()
                     if r is not None and base[s]["ratios"][f] is not None
                     and r > base[s]["ratios"][f] + NOISE] for s in SETTINGS}
        summary[str(vt)] = {
            "dev_worst": max(c[s]["worst"] for s in DEV),
            "dev_worse_features": {s: worse[s] for s in DEV if worse[s]},
            "untouched_worst_before_after": {s: (round(base[s]["worst"], 2), round(c[s]["worst"], 2))
                                             for s in UNTOUCHED},
            "untouched_worse_features": {s: worse[s] for s in UNTOUCHED if worse[s]},
            "untouched_dropped": sum(c[s]["worst"] < base[s]["worst"] - NOISE for s in UNTOUCHED),
        }
    ok = {vt: v for vt, v in summary.items() if not v["dev_worse_features"]}
    sel = min(ok, key=lambda k: ok[k]["dev_worst"]) if ok else None
    res["summary"] = summary
    res["baseline_dev_worst"] = max(base[s]["worst"] for s in DEV)
    res["selected"] = sel
    if sel is not None:
        res["preservation"] = preservation(float(sel))
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text(json.dumps(res, indent=1, default=float) + "\n")
    print(json.dumps({"baseline_dev_worst": res["baseline_dev_worst"], "selected": sel,
                      "summary": summary, "preservation": res.get("preservation")}, indent=1, default=float))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
