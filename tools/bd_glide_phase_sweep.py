#!/usr/bin/env python3
"""Known-answer sweep of `pitch_trajectory.glide_cents` over the BD DECAY range
(#557 freeze, PR #601 review).

    python3 tools/bd_glide_phase_sweep.py                # declared scope, fine grid
    python3 tools/bd_glide_phase_sweep.py range --jobs 2 # whole knob range, 0.1 steps

The signal is CLOSED FORM and independent of our model: a constant-pitch
decaying sinusoid  0.5 * sin(2 pi f0 t + phi) * exp(-t / tau)  after 10 ms of
silence, with tau = Q / (pi f0) and Q taken from `model/drums_fx.py`'s
BD_DECAY_Q table (read by ast, interpolated geometrically exactly as
`bd_decay_q` does).  Its true glide is ZERO, so two answers are known:

  * phase_only = |glide(cos onset) - glide(sin onset)| is pure apparatus
    artefact (the onset-phase term #558 found in the gate's pitch_shape);
  * abs_err = max(|glide(sin)|, |glide(cos)|) is the whole reading error;
  * where the ring has died before LATE_WINDOW_S, glide_cents must REFUSE --
    a short decay must never read as a 0-cent glide.

Each reading is either a finite float or the string "REFUSED: <reason>"; this
file never turns a refusal into a number.

RESOLUTION IS PART OF THE TOOL (PR #601, second review).  The first two
versions of the record read a coarse grid as the worst case: one knob (5), then
BD_DECAY_Q's knots x 3 pitches.  The Judge's finer grid found 34.2 cents at
knob 0.6, 52 Hz, 48 kHz -- above the rule's 27.8-cent apparatus part, which the
coarse grid had declared impossible.  So:

  * F0_GRID is 40..65 Hz in 0.5 Hz steps at both rates, always;
  * the SCOPED sweep runs every DECAY knob the record's Fischer conditions use
    (minus the predicted knob-0 refusals) on that grid -- that is what the test
    pins and what the record's worst-case claim is about;
  * the RANGE sweep runs knobs 0..10 in 0.1 steps on the same pitch grid; it is
    ~10k cells (minutes), so it is a CLI mode, and the record states its worst
    cell, which the test recomputes (one cell, cheap) and checks against the
    apparatus part.  `claim_violations` refuses a claimed worst below the swept
    one for the scope claimed.
"""
from __future__ import annotations

import argparse
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
F0_LO, F0_HI, F0_STEP = 40.0, 65.0, 0.5
F0_GRID = tuple(F0_LO + F0_STEP * i for i in range(int(round((F0_HI - F0_LO) / F0_STEP)) + 1))
RANGE_KNOB_STEP = 0.1
RANGE_KNOBS = tuple(round(RANGE_KNOB_STEP * i, 1) for i in range(101))   # 0.0 .. 10.0
CLAIM_TOL_CENTS = 0.05


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
            "abs_err": max(abs(g_sin), abs(g_cos)) if ok else None,
            "refused": not ok}


def _cell_args(a):
    return cell(*a)


def sweep(knobs, rates=RATES, f0s=F0_GRID, jobs: int = 1) -> list:
    args = [(sr, k, f) for sr in rates for k in knobs for f in f0s]
    if jobs <= 1:
        return [cell(*a) for a in args]
    from multiprocessing import Pool
    with Pool(jobs) as p:
        return p.map(_cell_args, args, chunksize=16)


def fischer_knobs(rec: dict) -> tuple:
    """Every DECAY knob a Fischer condition of the record uses (dev + untouched)."""
    conds = rec["conditions"]
    return tuple(sorted({float(c["model"]["decay_knob"])
                         for side in ("development", "untouched") for c in conds[side]
                         if (c.get("reference") or {}).get("corpus") == "fischer"}))


def scope_knobs(rec: dict) -> tuple:
    """fischer_knobs minus the record's unqualified_knobs (readings there are
    excluded by the refused-readings rule, so none enters a median)."""
    uq = set(rec["primary_metric"]["refused_readings"]["unqualified_knobs"])
    return tuple(k for k in fischer_knobs(rec) if k not in uq)


def scoped_sweep(rec: dict, jobs: int = 1) -> list:
    return sweep(scope_knobs(rec), jobs=jobs)


def worst(rows: list, key: str = "phase_only") -> dict:
    live = [r for r in rows if not r["refused"]]
    if not live:
        raise bp.Refused("every cell refused: no worst case exists")
    return max(live, key=lambda r: r[key])


def claim_violations(claim: dict, rows: list, floor: float) -> list:
    """The record's onset-phase claim against a sweep of the scope it claims.
    Violations, as strings: the grid coarser than F0_GRID / missing a rate /
    missing a scoped knob; a claimed worst BELOW the swept one (the defect the
    review caught twice); a claimed worst above it or at another cell (the
    record and the measurement drifted); a margin that is not floor / worst."""
    out = []
    got_f0 = sorted({r["f0"] for r in rows})
    if got_f0 != list(F0_GRID):
        out.append(f"pitch grid is not {F0_LO}-{F0_HI} Hz in {F0_STEP} Hz steps")
    if {r["sr"] for r in rows} != set(RATES):
        out.append(f"rates swept are not {RATES}")
    sc = claim.get("scope") or {}
    if sorted({r["knob"] for r in rows}) != sorted(sc.get("decay_knobs") or []):
        out.append(f"knobs swept {sorted({r['knob'] for r in rows})} are not the claimed "
                   f"scope {sc.get('decay_knobs')}")
    w = worst(rows)
    c = claim.get("worst_cents")
    if not isinstance(c, (int, float)):
        return out + [f"claimed worst {c!r} is not a number"]
    if c < w["phase_only"] - CLAIM_TOL_CENTS:
        out.append(f"claimed worst {c} c is BELOW the swept worst {w['phase_only']:.2f} c "
                   f"({w['sr']} Hz, knob {w['knob']}, {w['f0']} Hz)")
    elif c > w["phase_only"] + CLAIM_TOL_CENTS:
        out.append(f"claimed worst {c} c is above the swept worst {w['phase_only']:.2f} c")
    at = claim.get("worst_at") or {}
    if (at.get("sr"), at.get("decay_knob"), at.get("f0_hz")) != (w["sr"], w["knob"], w["f0"]):
        out.append(f"claimed worst cell {at} is not the swept one "
                   f"({w['sr']}, {w['knob']}, {w['f0']})")
    m = claim.get("margin_x")
    if not isinstance(m, (int, float)) or abs(m - floor / w["phase_only"]) > 0.01:
        out.append(f"margin {m!r} is not floor / worst = {floor / w['phase_only']:.3f}")
    return out


# The neighbourhood of the refusal edge where the range sweep found its worst
# cell (knob 0.6, 52 Hz, 48 kHz).  Cheap enough for a test (99 cells), so a
# range-wide claim below what this finds fails without the 10k-cell run.
# Input that defeats it (rule 8): a worst cell OUTSIDE this box -- only the
# `range` mode can see one, and its last run put the worst inside it.
EDGE_PROBE = {"rates": (48000,), "knobs": tuple(round(0.1 * i, 1) for i in range(11)),
              "f0s": tuple(50.0 + 0.5 * i for i in range(9))}


def edge_probe() -> list:
    return sweep(EDGE_PROBE["knobs"], rates=EDGE_PROBE["rates"], f0s=EDGE_PROBE["f0s"])


def range_claim_violations(claim: dict, probe_rows: list, floor: float) -> list:
    """The record's range-wide onset-phase figure against the edge probe and
    its own claimed cell, recomputed.  A claimed range-wide worst below the
    probe's worst is the coarse-grid error the review caught; so is one that
    says it does not exceed the apparatus part when its cell does."""
    out = []
    c = claim.get("worst_cents")
    if not isinstance(c, (int, float)):
        return [f"range-wide worst {c!r} is not a number"]
    w = worst(probe_rows)
    if c < w["phase_only"] - CLAIM_TOL_CENTS:
        out.append(f"range-wide worst {c} c is BELOW the edge probe's {w['phase_only']:.2f} c "
                   f"({w['sr']} Hz, knob {w['knob']}, {w['f0']} Hz)")
    at = claim.get("worst_at") or {}
    try:
        r = cell(at["sr"], at["decay_knob"], at["f0_hz"])
    except (KeyError, TypeError):
        return out + [f"range-wide worst cell {at!r} is not (sr, decay_knob, f0_hz)"]
    if r["refused"] or abs(r["phase_only"] - c) > CLAIM_TOL_CENTS:
        out.append(f"range-wide worst {c} c is not what its cell reads ({r['phase_only']})")
    if claim.get("exceeds_apparatus_part") is not (c > floor):
        out.append(f"exceeds_apparatus_part {claim.get('exceeds_apparatus_part')!r} but "
                   f"{c} vs {floor}")
    return out


def _print(rows: list, label: str) -> None:
    n_ref = sum(r["refused"] for r in rows)
    w = worst(rows)
    a = worst(rows, "abs_err")
    print(f"[{label}] {len(rows)} cells, {n_ref} refused")
    print(f"  worst phase_only {w['phase_only']:.2f} c at {w['sr']} Hz, knob {w['knob']}, "
          f"f0 {w['f0']} (sin {w['sin']:.2f}, cos {w['cos']:.2f})")
    print(f"  worst |reading| (true glide 0) {a['abs_err']:.2f} c at {a['sr']} Hz, "
          f"knob {a['knob']}, f0 {a['f0']} (sin {a['sin']:.2f}, cos {a['cos']:.2f})")
    for k in sorted({r["knob"] for r in rows}):
        kr = [r for r in rows if r["knob"] == k]
        live = [r for r in kr if not r["refused"]]
        if not live:
            print(f"  knob {k:4.1f}: all {len(kr)} REFUSED")
            continue
        kw = max(live, key=lambda r: r["phase_only"])
        ka = max(live, key=lambda r: r["abs_err"])
        print(f"  knob {k:4.1f}: refused {len(kr) - len(live):3d}/{len(kr)}  "
              f"phase_only max {kw['phase_only']:6.2f} c ({kw['sr']}, {kw['f0']} Hz)  "
              f"|reading| max {ka['abs_err']:6.2f} c ({ka['sr']}, {ka['f0']} Hz)")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("mode", nargs="?", choices=("scoped", "range"), default="scoped")
    ap.add_argument("--jobs", type=int, default=1)
    ap.add_argument("--json", type=pathlib.Path, help="write every cell here")
    a = ap.parse_args(argv)
    if a.mode == "scoped":
        rec = json.loads(bp.RECORD.read_text())
        rows = scoped_sweep(rec, jobs=a.jobs)
        _print(rows, f"scoped knobs {scope_knobs(rec)}")
        floor = bp.minimum_improvement_cents(rec, {"recording_glide_spread_cents": 0.0})
        ka = rec["primary_metric"]["onset_phase_known_answer"]
        bad = claim_violations(ka, rows, floor)
        bad += range_claim_violations(ka.get("range_wide") or {}, edge_probe(), floor)
        for b in bad:
            print(f"CLAIM VIOLATION {b}")
        print("record claim: " + ("MATCHES the sweep" if not bad else "WRONG"))
        rc = 1 if bad else 0
    else:
        rows = sweep(RANGE_KNOBS, jobs=a.jobs)
        _print(rows, f"range knobs 0..10 step {RANGE_KNOB_STEP}")
        rc = 0
    if a.json:
        a.json.write_text(json.dumps(rows, indent=0))
    return rc


if __name__ == "__main__":
    sys.exit(main())
