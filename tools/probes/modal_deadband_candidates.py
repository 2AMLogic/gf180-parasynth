#!/usr/bin/env python3
"""Candidate mechanisms for the deadband, measured only enough to brief the repair (#350).

    python3 tools/probes/modal_deadband_candidates.py [--out F.json]

NOT a recommendation and NOT a registered sweep: no value is proposed here. A
follow-up that proposes `state_q` (state fractional bits) or any other dial must
register it under tools/sensitivity.py when it makes the proposal.

PREDICTION, derived without running anything. The floor in `acc >> CF` loses up
to one state LSB per sample, always toward -infinity. A pole pair with
per-sample decay (1 - r) can only pull a state back toward zero by (1 - r)*|y|
per sample, so the state stops contracting once (1 - r)*|y| ~ 1 LSB: the
deadband sits at |y| ~ k / (1 - r) STATE LSB, k of order 1, WHATEVER the number
of fractional bits (the quantum and the contraction both scale with it). So:
  * the deadband in state LSB is ~ independent of state_q;
  * referred to the output it shrinks by 2^-dSQ for dSQ more fractional bits;
  * with 1 - r = pi f0 / (Q fs): LT (90 Hz, Q25) 1/(1-r) = 4.2e3 state LSB.
The measured departure levels in deadband.json (6.0k-7.9k) are 1.4-1.9 x that.
Rounding (round-half-up) is the hypothesis PR #543 measured not to fix the BD
pedestal; it is re-run here on the one-mode known answer for the record.
"""
from __future__ import annotations

import argparse
import json
import math
import pathlib
import sys

import numpy as np

sys.path[:0] = [str(pathlib.Path(__file__).resolve().parent)]
import modal_deadband as md        # noqa: E402
import modal_fixed as mf           # noqa: E402
import provenance as pv        # noqa: E402

N = 96000


def one(f0, q, ping_out_lsb, sq=15, sb=28, rounding=False):
    """Ping sized in OUTPUT-referred terms (Q15 LSB), so every sq sees the same
    signal. The STATE is read from TwinBank (ModalFx's output word is Q15
    whatever sq is, so comparing it to a state-unit float was wrong: the first
    run of this script put every sq > 15 'departing at 0 ms'; recorded in the
    README's wrong-then-right count)."""
    a1, a2 = mf.pole_regs(f0, q)
    b = md.TwinBank(N, modes=1, nums=1, headroom=0, out_bits=sb, state_bits=sb, state_q=sq, rounding=rounding)
    for t in range(N):
        b.step([ping_out_lsb if t == 10 else 0], [(a1, a2, 65535)], [mf.RAW])
    y, yf = b.rec_y[:, 0], b.rec_f[:, 0]
    dep = md.departure(y, yf, 0)
    return dict(departs_ms=None if dep is None else round(dep[1]), float_level_state_lsb=None if dep is None else round(dep[2], 1),
                float_level_q15_lsb=None if dep is None else round(dep[2] / (1 << (sq - 15)), 2),
                tail=md.tail_shape(y), one_over_1mr=round(q * 48000 / (math.pi * f0), 0))


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=pathlib.Path)
    a = ap.parse_args(argv)
    res = {"rounding_vs_floor_LT_ping_10000": {"floor": one(90.0, 25.0, 10000), "round-half-up": one(90.0, 25.0, 10000, rounding=True)},
           "state_q_sweep": {}}
    for (f0, q, name) in ((90.0, 25.0, "LT 90Hz Q25"), (50.0, 22.3, "BD-like 50Hz Q22.3")):
        res["state_q_sweep"][name] = {str(sq): one(f0, q, 1000, sq=sq, sb=32) for sq in (15, 17, 19, 21)}
    res["provenance"] = md.provenance(
        "python3 tools/probes/modal_deadband_candidates.py" + (f" --out {a.out}" if a.out else ""),
        extra_inputs={f: pv.file_sha(md.ROOT / f) for f in
                      ("tools/probes/modal_deadband_candidates.py", "tools/probes/modal_deadband.py")})
    print(json.dumps(res, indent=1))
    if a.out:
        a.out.write_text(json.dumps(res, indent=1) + "\n")


if __name__ == "__main__":
    main()
