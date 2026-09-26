#!/usr/bin/env python3
"""fpga/release/probe_247.py -- which part of #247's stimulus the mismatch
needs, measured with #247's OWN bench, so the release excludes the right
domain.

#247 was found by `rtl-sketch/verify_deadline.py --scenario extreme-saw` on
branch verify/three-saw-deadline (PR #248): synth_top through its SPI pins,
production launch, I2S decoded from the wire, 1809 of 2168 periods differing
from the model. That stimulus combines several things at once: increments
>= 2^23, a slow glide retargeted every 40 frames, the VOICE ROUTED THROUGH
THE DRUM FILTER (route = 1, dcut = 600, the drum ladder carrying the voice's
k/gain/ogain), and the drum kit struck under it. The issue attributes the
mismatch to the glide. This probe varies one ingredient at a time.

The bench is imported, not copied or edited, from a checkout of that branch
(--deadline-root, pinned by --expect-sha); the variants re-use its image,
drum and Script helpers and its main(). Nothing on that branch is modified.

VARIANTS (increments as in #247 unless named):

  orig          #247's _extreme('saw') verbatim                 (reproduction)
  no-drumfilter route = 0 (voice through its own ladder), strikes kept
  no-strikes    route = 1 kept, the kit never struck
  jumps         every glide write replaced by a jump (flag 1)
  inrange       the same shape with every increment scaled so the top one is
                INC_HI (the release's ceiling), route/strikes as #247
  inrange-route0  inrange with route = 0

Exit statuses are verify_deadline's (0 match, 1 differ, 2 no run); a run's
verdict line is what this records, not a claim about the cause.

    .venv/bin/python fpga/release/probe_247.py --deadline-root /tmp/wt-deadline-ro --variant orig
"""
from __future__ import annotations

import argparse
import importlib.util
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
PINNED = "b45bc5da0dff114a86223bf4e818d88c278eb59d"      # PR #248 head at binding

LO247, HI247 = (0xC00000, 0xC80000, 0xD00000), (0xFF0000, 0xF80000, 0xF00000)


def load_bench(root: str):
    path = os.path.join(root, "rtl-sketch", "verify_deadline.py")
    if not os.path.exists(path):
        raise SystemExit(f"probe_247: REFUSED -- no verify_deadline.py under {root}")
    sys.path[:0] = [os.path.join(root, d) for d in ("rtl-sketch", "model", "audition", "fpga", "tools")]
    spec = importlib.util.spec_from_file_location("verify_deadline", path)
    vd = importlib.util.module_from_spec(spec)
    sys.modules["verify_deadline"] = vd
    spec.loader.exec_module(vd)
    return vd


def variant(vd, *, lo, hi, route=1, strikes=True, glide=True):
    """#247's _extreme (saw, not short, osc_mod off) with one ingredient changed."""
    vf, stm, dx = vd.vf, vd.stm, vd.dx
    regs = vf.VoiceFx.patch_regs(waves=("saw", "saw", "saw"), detune=(0.0, 0.0, 0.0),
                                 mix=(1.0, 0.8, 0.7), noise=0.3, nsel=1, cutoff=(200, 12000),
                                 q=0.9, drive=1.6, amp=(0.002, 0.2, 0.8, 0.1),
                                 fenv=(0.001, 0.2, 0.5, 0.1), track=0.0, vol=0.45, glide_s=2.0,
                                 mod_mix=0.5, mod_wheel=1.0, mod_pitch=0.05, osc_mod=False,
                                 filt_mod=True, osc3_ctl=True)
    s = vd.Script()
    s.put(0, vd.image_writes(regs, dvol=dx.accent_reg(0.3), bvol=dx.accent_reg(0.3),
                             route=route, dcut=600))
    s.put(0, vd.drum_image_writes())
    all_stops = (1 << dx.N_STOPS) - 1
    s.put(4, [(1, vd.SEC_V, stm.A_INC + k, lo[k]) for k in range(3)] + [(0, vd.SEC_V, stm.A_GATE_ON, 0)])
    gap = 40
    for i in range(32):
        tgt = hi if i % 2 == 0 else lo
        s.put(gap, [(0 if glide else 1, vd.SEC_V, stm.A_INC + k, tgt[k]) for k in range(3)])
        if strikes:
            s.put(2, [(0, vd.SEC_D, dx.A_STOPS, all_stops)])
            s.put(2, [(0, vd.SEC_D, dx.A_STOPS, 0)])
        else:
            s.put(4, [(0, vd.SEC_V, stm.A_NOP, 0)])
    return s.cmds, 260, dict(regs=regs, claim="three_2x_audible", drums=strikes)


def main(argv=None) -> int:
    import qualified_domain as qd
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--deadline-root", required=True)
    ap.add_argument("--expect-sha", default=PINNED)
    ap.add_argument("--variant", required=True,
                    choices=("orig", "no-drumfilter", "no-strikes", "jumps", "inrange", "inrange-route0"))
    ap.add_argument("--outdir", default=None)
    a = ap.parse_args(argv)
    sha = subprocess.run(["git", "-C", a.deadline_root, "rev-parse", "HEAD"],
                         capture_output=True, text=True).stdout.strip()
    if sha != a.expect_sha:
        print(f"probe_247: REFUSED -- {a.deadline_root} is at {sha or '?'}, expected {a.expect_sha}")
        return 2
    vd = load_bench(a.deadline_root)
    s = qd.INC_HI / 0xFF0000
    lo_in, hi_in = tuple(int(v * s) for v in LO247), tuple(int(v * s) for v in HI247)
    kw = {"orig": dict(lo=LO247, hi=HI247),
          "no-drumfilter": dict(lo=LO247, hi=HI247, route=0),
          "no-strikes": dict(lo=LO247, hi=HI247, strikes=False),
          "jumps": dict(lo=LO247, hi=HI247, glide=False),
          "inrange": dict(lo=lo_in, hi=hi_in),
          "inrange-route0": dict(lo=lo_in, hi=hi_in, route=0)}[a.variant]
    name = f"probe247-{a.variant}"
    vd.SPI_SCENARIOS[name] = lambda short=False: variant(vd, **kw)
    outdir = a.outdir or os.path.join(os.path.dirname(os.path.dirname(HERE)), "build", "probe247", a.variant)
    print(f"probe_247: variant {a.variant} {kw} on verify_deadline @ {sha[:12]}")
    return vd.main(["--scenario", name, "--outdir", outdir])


if __name__ == "__main__":
    sys.exit(main())
