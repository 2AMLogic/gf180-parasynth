#!/usr/bin/env python3
"""Known-answer sweep of `pitch_trajectory.glide_cents` over the BD DECAY range
(#557 freeze, PR #601 review).

    python3 tools/bd_glide_phase_sweep.py            # table, 48 kHz and 44.1 kHz

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
this version sweeps knobs, so the record's DECAY conditions are covered by name.
"""
from __future__ import annotations

import math
import pathlib
import sys

import numpy as np

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import pitch_trajectory as pt                     # noqa: E402
import bd_pitch_predeclaration as bp              # noqa: E402  (ast reader only)

F_REF = 52.0
F0S = (45.0, 49.4, 55.0)
RATES = (48000, 44100)
# BD_DECAY_Q's own knots plus the record's Fischer knobs 2.5 and 7.5
KNOBS = (0.0, 1.0, 2.5, 5.0, 7.5, 9.0, 10.0)


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


def sweep(rates=RATES, knobs=KNOBS, f0s=F0S) -> list:
    return [cell(sr, k, f) for sr in rates for k in knobs for f in f0s]


def main() -> int:
    rows = sweep()
    for r in rows:
        v = f"{r['phase_only']:6.2f} c" if not r["refused"] else \
            (r["sin"] if isinstance(r["sin"], str) else r["cos"])
        print(f"{r['sr']:5d} knob={r['knob']:4.1f} Q={r['q']:5.1f} f0={r['f0']:4.1f} "
              f"tau={r['tau_ms']:6.1f}ms phase_only={v}")
    live = [r for r in rows if not r["refused"]]
    w = max(live, key=lambda r: r["phase_only"])
    print(f"worst phase_only {w['phase_only']:.2f} cents at {w['sr']} Hz, knob {w['knob']}, "
          f"f0 {w['f0']}; refused cells: {sum(r['refused'] for r in rows)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
