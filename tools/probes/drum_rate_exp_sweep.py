#!/usr/bin/env python3
"""#656: sweep the drum envelope rate-exponent width (RATE_EXP_BITS).

    python3 tools/probes/drum_rate_exp_sweep.py [--out docs/sensitivity/drum-rate-exp-bits-sweep.txt]

Grid, decision rule, the 5 % tau tolerance and the shape prediction are in
docs/sensitivity/drum-rate-exp-bits-plan.json, committed before this file and
before any point. This instrument REFUSES (exit 2) if that plan is uncommitted
or dirty, so a grid edited after seeing a number cannot pass through it.

Per width it reports, in a fixed-width table `tools/sensitivity.py` re-extracts:

  worst_err_pct       the objective: worst |realised / asked - 1| over every
                      DECAY ask of E_CYL and E_CYD at 101 wiper positions
  cyl_knob_pct        the same at the five knob codes only, E_CYL
  cyd_knob_pct        the same, E_CYD
  sim_check_pct       worst disagreement between rate_code_tau (the closed
                      form) and a tau fitted to the integer EnvFx's level from
                      -3 to -20 dB at the five codes -- the closed form is
                      checked, not trusted
  tail_slow_pct       the same fit from -20 to -40 dB: how much SLOWER the
                      integer decay runs there than the closed form says,
                      because (level * m) >> (16 + e) truncates a product of a
                      few LSB. A property of the envelope since revision 1,
                      identical at every width for the same realised tau.
                      (Wrong-then-right 1: the first run fitted -6..-40 dB as
                      one window and read this tail bias, 3.8-4.6 %, as a
                      closed-form disagreement.)
  floor_db            the linear-floor onset (2^(16+e)/m) at the slowest ask,
                      dB re full scale
  flops_added         18 envelopes x (RATE + FRATE) x width
  clocks              the drum schedule (68), unchanged: the shift is
                      combinational on the existing product

The asks are the anchor's REALISED tau (the shipped Q0.16 code of CY_TAU_LOW /
CY_TAU_DECAY: the kit was fitted through that quantisation, so that is the
anchor sound) times cymbal_candidate's DECAY network ratio.

Exit 0 written, 2 REFUSED.
"""
from __future__ import annotations

import argparse
import json
import math
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "model"), str(ROOT / "audition")]
import drums_fx as dx  # noqa: E402
import cymbal_candidate as cc  # noqa: E402

PLAN = ROOT / "docs" / "sensitivity" / "drum-rate-exp-bits-plan.json"
OUT = ROOT / "docs" / "sensitivity" / "drum-rate-exp-bits-sweep.txt"
CLOCKS = 68                         # drum_dp.v: 1 + ENVS + 1 + 2*PATHS + 2


class Refused(RuntimeError):
    pass


def _git(*args) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True)


def assert_plan_committed() -> dict:
    rel = str(PLAN.relative_to(ROOT))
    if _git("ls-files", "--error-unmatch", rel).returncode != 0:
        raise Refused(f"{rel} is not committed: a grid stated after the results is not a grid")
    if _git("diff", "--quiet", "HEAD", "--", rel).returncode != 0:
        raise Refused(f"{rel} differs from HEAD: commit the plan before sweeping")
    return json.loads(PLAN.read_text())


def asks(n: int = 101) -> dict:
    """{envelope name: [(wiper fraction, asked tau)]} plus the five knob codes."""
    out = {}
    for name, e, tau in (("E_CYL", dx.E_CYL, dx.CY_TAU_LOW), ("E_CYD", dx.E_CYD, dx.CY_TAU_DECAY)):
        anchor = dx.rate_code_tau(dx.rate_reg(tau))
        base = cc.decay_timing_ohm(cc.DECAY_ANCHOR)
        dense = [(i / (n - 1), anchor * (cc.DECAY_R93_OHM + i / (n - 1) * cc.DECAY_VR2_OHM) / base)
                 for i in range(n)]
        knob = [(cc.DECAY_POS[c], anchor * cc.decay_scale(c)) for c in ("00", "25", "50", "75", "10")]
        out[name] = {"dense": dense, "knob": knob}
    return out


def fitted_tau(code: int, tau_hint: float, lo_db: float = -20.0, hi_db: float = -3.0) -> float:
    """Fire one EnvFx at full scale with `code` and fit ln(level) between lo_db and hi_db."""
    env = dx.EnvFx()
    env.peak, env.rate = dx.FULL24, code
    env.set_ctl(dx.env_ctl(0))
    acc = [32768] * dx.N_STOPS
    env.frame(1, acc)
    pk = env.level
    xs, ys = [], []
    for i in range(1, int(6 * tau_hint * dx.SR) + 200):
        env.frame(0, acc)
        if env.level <= 0:
            break
        r = env.level / pk
        if 10 ** (lo_db / 20) <= r <= 10 ** (hi_db / 20):
            xs.append(i)
            ys.append(math.log(env.level))
    if len(xs) < 20:
        return math.nan
    nx = len(xs)
    mx, my = sum(xs) / nx, sum(ys) / nx
    slope = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / sum((x - mx) ** 2 for x in xs)
    return -1.0 / (slope * dx.SR)


def point(bits: int, a: dict) -> dict:
    def err(tau):
        got = dx.rate_code_tau(dx.rate_code(tau, exp_bits=bits))
        return abs(got / tau - 1.0)
    worst = max(err(t) for v in a.values() for _, t in v["dense"])
    knob = {name: max(err(t) for _, t in v["knob"]) for name, v in a.items()}
    sim = tail = 0.0
    for v in a.values():
        for _, t in v["knob"]:
            code = dx.rate_code(t, exp_bits=bits)
            f = fitted_tau(code, dx.rate_code_tau(code))
            g = fitted_tau(code, dx.rate_code_tau(code), lo_db=-40.0, hi_db=-20.0)
            if not (math.isfinite(f) and math.isfinite(g)):
                raise Refused(f"width {bits}: the EnvFx decay at tau {t:.4f} s never crossed the fit window")
            sim = max(sim, abs(f / dx.rate_code_tau(code) - 1.0))
            tail = max(tail, g / dx.rate_code_tau(code) - 1.0)
    slow = max(t for v in a.values() for _, t in v["dense"])
    c = dx.rate_code(slow, exp_bits=bits)
    m, e = c & dx.RATE_M_MASK, c >> dx.RATE_Q
    floor = (1 << (dx.RATE_Q + e)) / m
    return {"RATE_EXP_BITS": bits, "worst_err_pct": 100 * worst,
            "cyl_knob_pct": 100 * knob["E_CYL"], "cyd_knob_pct": 100 * knob["E_CYD"],
            "sim_check_pct": 100 * sim, "tail_slow_pct": 100 * tail, "floor_db": 20 * math.log10(floor / (1 << dx.ENV_BITS)),
            "flops_added": 2 * dx.N_ENV * bits, "clocks": CLOCKS}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--out", type=pathlib.Path, default=OUT)
    a = ap.parse_args(argv)
    try:
        plan = assert_plan_committed()
        grid = [int(v) for v in plan["grid"]]
        tol = float(plan["selection"]["tau_tolerance_relative"])
        ask = asks()
        rows = [point(b, ask) for b in grid]
    except Refused as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 2
    passing = [r["RATE_EXP_BITS"] for r in rows if r["worst_err_pct"] <= 100 * tol]
    select = min(passing) if passing else None
    head = _git("rev-parse", "--short=12", "HEAD").stdout.strip()
    cols = ("RATE_EXP_BITS", "worst_err_pct", "cyl_knob_pct", "cyd_knob_pct", "sim_check_pct",
            "tail_slow_pct", "floor_db", "flops_added", "clocks")
    lines = [
        "Issue #656 drum RATE_EXP_BITS sweep -- written by `python3 tools/probes/drum_rate_exp_sweep.py`.",
        "Grid, rule, tolerance and prediction: docs/sensitivity/drum-rate-exp-bits-plan.json (committed before this ran).",
        f"provenance: commit {head}  SR {dx.SR}  asks: anchor realised tau x DECAY network ratio, 101 wiper positions",
        f"anchor realised: E_CYL {dx.rate_code_tau(dx.rate_reg(dx.CY_TAU_LOW)):.4f} s (asked constant "
        f"{dx.CY_TAU_LOW}), E_CYD {dx.rate_code_tau(dx.rate_reg(dx.CY_TAU_DECAY)):.4f} s (asked constant "
        f"{dx.CY_TAU_DECAY})",
        f"selection: smallest width with worst_err_pct <= {100 * tol:g} -> "
        + (f"RATE_EXP_BITS = {select}" if select is not None else "NONE (no width meets the tolerance)"),
        "",
        "rate_exp_bits -- worst realised-tau error against the DECAY law's asks, and what each width costs",
        " ".join(cols),
    ]
    for r in rows:
        lines.append(" ".join(
            str(r[c]) if isinstance(r[c], int) else f"{r[c]:.4f}" for c in cols))
    lines.append("")
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text("\n".join(lines) + "\n")
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
