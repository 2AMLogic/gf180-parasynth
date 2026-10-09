#!/usr/bin/env python3
"""Known-answer sweep of `pitch_trajectory.glide_cents` over the BD DECAY range
(#557 freeze, PR #601 review).

    python3 tools/bd_glide_phase_sweep.py            # worst cell: record knobs, edge scan

The signal is CLOSED FORM and independent of our model: a constant-pitch
decaying sinusoid  0.5 * sin(2 pi f0 t + phi) * exp(-t / tau)  after 10 ms of
silence, with tau = Q / (pi f0) and Q taken from `model/drums_fx.py`'s
BD_DECAY_Q table (read by ast, interpolated geometrically exactly as
`bd_decay_q` does).  Its true glide is ZERO, so two answers are known:

  * phase_only = |glide(cos onset) - glide(sin onset)| is pure apparatus
    artefact (the onset-phase term #558 found in the gate's pitch_shape);
  * where the ring has died before LATE_WINDOW_S, glide_cents must REFUSE --
    a short decay must never read as a 0-cent glide.

Each reading is either a finite float or the string "REFUSED: <reason>"; this
file never turns a refusal into a number.  The Judge's scratch sweep
(/tmp/judge601/phase_sweep.py, Q in {2.3, 5.2, 22.3, 63, 84}) is the basis;
this version sweeps the record's own knobs on a 0.5 Hz pitch grid plus an edge scan.
"""
from __future__ import annotations

import json
import math
import pathlib
import sys

import numpy as np

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import pitch_trajectory as pt                     # noqa: E402
import bd_pitch_predeclaration as bp              # noqa: E402  (ast reader only)

F_REF = 52.0
RATES = (48000, 44100)
# The grid is part of the tool, not of whoever ran it last: the first two
# versions of the record each read a coarse grid's worst cell as the worst case
# (PR #601 review).  Pitches: the whole BD range in 0.5 Hz steps, which is as
# fine as the reviewer's scan.  Knobs of the DECLARED conditions are read from
# the record (never typed here); the EDGE scan is separate and covers the
# knobs between refusal and measurability, where the reading is worst.
F0S = tuple(float(f) for f in np.arange(40.0, 65.0 + 1e-9, 0.5))
EDGE_KNOBS = tuple(round(float(k), 1) for k in np.arange(0.0, 1.5 + 1e-9, 0.1))
EDGE_F0S = F0S
EDGE_RATES = (48000,)


def record_knobs() -> tuple:
    """Every DECAY knob a condition in the frozen record uses, development and
    untouched, read from the record."""
    rec = json.loads(bp.RECORD.read_text())
    conds = rec["conditions"]["development"] + rec["conditions"]["untouched"]
    return tuple(sorted({float(c["model"]["decay_knob"]) for c in conds}))


def bd_q(knob: float) -> float:
    """model/drums_fx.py::bd_decay_q, re-derived from the ast-read table so
    importing the model is not needed (geometric interpolation, clamped)."""
    table = bp._module_constant("model/drums_fx.py", "BD_DECAY_Q")
    ks = sorted(table)
    k = min(max(float(knob), ks[0]), ks[-1])
    return float(math.exp(np.interp(k, ks, [math.log(table[x]) for x in ks])))


def glide(sr: int, f0: float, tau: float, phase: float):
    """glide_cents of the closed-form tone, or 'REFUSED: ...'."""
    t = np.arange(sr) / sr
    y = np.concatenate([np.zeros(sr // 100),
                        np.sin(2 * np.pi * f0 * t + phase) * np.exp(-t / tau)])
    try:
        return pt.glide_cents(pt.trajectory(0.5 * y, sr, F_REF))
    except pt.Refused as e:
        return f"REFUSED: {e}"


def cell(sr: int, knob: float, f0: float) -> dict:
    q = bd_q(knob)
    tau = q / (math.pi * f0)
    g_sin, g_cos = glide(sr, f0, tau, 0.0), glide(sr, f0, tau, math.pi / 2)
    ok = isinstance(g_sin, float) and isinstance(g_cos, float)
    return {"sr": sr, "knob": knob, "q": q, "f0": f0, "tau_ms": tau * 1e3,
            "sin": g_sin, "cos": g_cos,
            "phase_only": abs(g_cos - g_sin) if ok else None,
            "refused": not ok}


def sweep(rates=RATES, knobs=None, f0s=F0S) -> list:
    """Cells over the record's own knobs by default."""
    knobs = record_knobs() if knobs is None else knobs
    return [cell(sr, k, f) for sr in rates for k in knobs for f in f0s]


def edge_sweep() -> list:
    """Knobs between refusal and the record's lowest measurable knob."""
    return sweep(EDGE_RATES, EDGE_KNOBS, EDGE_F0S)


def worst(rows: list):
    live = [r for r in rows if not r["refused"]]
    return max(live, key=lambda r: r["phase_only"]) if live else None


def main() -> int:
    for name, rows in (("record knobs", sweep()), ("edge scan", edge_sweep())):
        w = worst(rows)
        print(f"{name}: {len(rows)} cells, {sum(r['refused'] for r in rows)} refused; " +
              (f"worst phase_only {w['phase_only']:.2f} cents at {w['sr']} Hz, knob {w['knob']}, "
               f"f0 {w['f0']}" if w else "no measurable cell"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
