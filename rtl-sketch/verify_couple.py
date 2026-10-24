#!/usr/bin/env python3
"""NEW (#552). Known-answer, width-extreme verification of drum_dcblock (the contract 15.10
shared-bus DC-coupling stage, in drum_kit.v) against model/drums_fx.DcBlockFx.

The drum stream (verify_drums.py) cannot reach the corners that matter for WIDTH: a real kit
never holds a bus at full scale for 20 000 frames and then reverses it. This drives both buses
(22-bit mix, 19-bit body, the production widths) directly with:

  * the independent closed-form answers of 15.10 (asserted on the EXPECTED stream before it is
    trusted, so a model that drifted from the contract cannot grade the RTL): +1 gives 1024
    ones then 0; -1 gives one -1 then 0; a positive impulse is never discharged; a negative
    impulse of -m gives m ones; a full-scale reversal from rest gives exactly lo - hi / hi - lo,
    which needs n + 1 output bits;
  * hold-hi / hold-lo to rest (the charge reaches hi*2^K and lo*2^K + 2^K - 1, which need all
    n + K bits) and the reversals out of them;
  * the dead zone's endpoints (+-1, +-1023, +-1024, +-1025 steps) with the enable on and off,
    enable toggling while bypassed (the charge must keep tracking), seeded full-scale random
    segments, and a RESET with a full charge (vector bit 45), after which the filter restarts at 0.

Status: 0 identical, 1 differed, 2 NO VERDICT (tool missing, compile failure, simulation
failure, short or absent output). A traceback is NO VERDICT, never a pass. Unknown (x) output is
counted as a mismatch.

  --inject NAME   compile -DINJECT_BUG_DRUM_COUPLE_<NAME> (BYPASS, NARROW_ACC, NARROW_OUT,
                  SHIFT_LOGICAL, FROZEN)
  --expect-fail   exit 0 only if the comparison gave 1 (a caught control), else 1; a NO VERDICT
                  stays 2 and is NOT a caught control
"""
from __future__ import annotations
import argparse, os, random, subprocess, sys
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(ROOT, "model"))
import drums_fx as dx
from verify_ladder import tool

K = dx.COUPLE_K
NMIX, NBODY = dx.MIX_BITS, dx.BODY_BITS                  # 22, 19
REST = 40000                                             # >> 2^K * ln(2^(n+K)): the charge settles exactly


def lim(n): return -(1 << (n - 1)), (1 << (n - 1)) - 1


def build_vectors():
    """[(en, x_mix, x_body, rst)], the segment log. rst = the stage is reset before that sample
    (a RESET, or `from rest` for the closed-form segments)."""
    rng = random.Random(552)
    v, log = [], []

    def seg(name, count, en, fm, fb, rst=False):
        log.append((name, len(v), count))
        for i in range(count):
            v.append((en, fm(i, NMIX), fb(i, NBODY), int(rst and i == 0)))

    hi = lambda i, n: lim(n)[1]
    lo = lambda i, n: lim(n)[0]
    const = lambda c: (lambda i, n: c)
    zero = const(0)
    impulse = lambda m: (lambda i, n: m if i == 0 else 0)
    rnd = lambda i, n: rng.randint(*lim(n))
    small = lambda i, n: rng.randint(-1500, 1500)
    for en in (1, 0):
        seg(f"+1 const en={en}", 1100, en, const(1), const(1), rst=True)
        seg(f"zero (drain) en={en}", 1100, en, zero, zero)
        seg(f"-1 const en={en}", 1100, en, const(-1), const(-1), rst=True)
        seg(f"zero en={en}", 1100, en, zero, zero)
        seg(f"+impulse 1000 en={en}", 1100, en, impulse(1000), impulse(1000), rst=True)
        seg(f"-impulse 1000 en={en}", 1100, en, impulse(-1000), impulse(-1000), rst=True)
        for c in (1023, 1024, 1025, -1023, -1024, -1025):
            seg(f"dead-zone step {c} en={en}", 1500, en, const(c), const(c), rst=True)
    # the width extremes, enable on: rest at hi (charge hi*2^K), full reversal to lo (output lo - hi),
    # rest at lo (charge lo*2^K + 2^K - 1), full reversal to hi (output hi - lo)
    seg("rest at +hi", REST, 1, hi, hi, rst=True)
    seg("reversal to lo", REST, 1, lo, lo)
    seg("reversal to hi", REST, 1, hi, hi)
    seg("zero", 3000, 1, zero, zero)
    # bypassed history, then a re-enable: the output must step to x - floor(acc/2^K), nothing cleared
    seg("bypassed noise", 4000, 0, small, small)
    seg("re-enabled", 4000, 1, small, small)
    seg("toggling enable on random full scale", 6000, 1, rnd, rnd)
    for k in range(60):
        seg("en toggle", 100, k & 1, small if k % 3 else rnd, rnd if k % 3 else small)
    seg("charge to full before reset", REST // 2, 1, hi, lo)
    seg("after reset", 3000, 1, small, small, rst=True)
    return v, log


def expected(v):
    em, eb = dx.DcBlockFx(K, in_bits=NMIX), dx.DcBlockFx(K, in_bits=NBODY)
    out = []
    for i, (en, xm, xb, rst) in enumerate(v):
        if rst:
            em.reset(); eb.reset()
        ym, yb = em.step(xm), eb.step(xb)
        out.append((ym if en else xm, yb if en else xb))
    return out, (em, eb)


def closed_form_checks(v, log, exp):
    """The contract's own numbers, asserted on the expected stream with NO use of the model's
    answers for the comparison: a drifted model fails here, not silently grades the RTL."""
    seg = {(n, s): (s, c) for n, s, c in log}
    def at(name, nth=0):
        hits = [(s, c) for n, s, c in log if n == name]
        return hits[nth]
    bad = []
    s, c = at("+1 const en=1"); ys = [e[0] for e in exp[s:s + c]]
    if ys[:1024] != [1] * 1024 or any(ys[1024:]): bad.append("+1: not 1024 ones then 0")
    s, c = at("-1 const en=1"); ys = [e[0] for e in exp[s:s + c]]
    if ys[0] != -1 or any(ys[1:]): bad.append("-1: not one -1 then 0")
    s, c = at("+impulse 1000 en=1"); ys = [e[0] for e in exp[s:s + c]]
    if ys[0] != 1000 or any(ys[1:]): bad.append("positive impulse below 2^K was discharged")
    s, c = at("-impulse 1000 en=1"); ys = [e[0] for e in exp[s:s + c]]
    if ys[0] != -1000 or ys[1:1001] != [1] * 1000 or any(ys[1001:]): bad.append("-1000 impulse not -1000 then 1000 ones")
    for bus, n in ((0, NMIX), (1, NBODY)):
        l, h = lim(n)
        s, c = at("reversal to lo"); y = exp[s][bus]
        if y != l - h: bad.append(f"bus {bus}: reversal to lo gave {y}, want {l - h}")
        if exp[s - 1][bus] != 0: bad.append(f"bus {bus}: not at rest before the reversal")
        s, c = at("reversal to hi"); y = exp[s][bus]
        if y != h - l: bad.append(f"bus {bus}: reversal to hi gave {y}, want {h - l}")
    return bad


def simulate(defines, outdir, nv, timeout_s=900.0):
    iverilog, vvp = tool("iverilog"), tool("vvp")
    if not iverilog or not vvp:
        print("verify_couple: iverilog/vvp not on PATH (or set OSS_CAD_SUITE): NO VERDICT"); return None
    vvp_file, out_file = os.path.join(outdir, "tb_couple.vvp"), os.path.join(outdir, "couple_rtl_out.txt")
    if os.path.exists(out_file): os.remove(out_file)
    srcs = [os.path.join(HERE, f) for f in ("tb_couple.v", "drum_kit.v")]
    r = subprocess.run([iverilog, "-g2012", "-s", "tb_couple", "-o", vvp_file] + [f"-D{d}" for d in defines] + srcs,
                       cwd=HERE, capture_output=True, text=True)
    if r.returncode != 0:
        print("verify_couple: iverilog failed (NO VERDICT):\n" + r.stdout + r.stderr); return None
    try:
        r = subprocess.run([vvp, "-n", vvp_file, f"+vec={os.path.join(outdir, 'couple_vec.hex')}", f"+out={out_file}",
                            f"+n={nv}"], cwd=HERE, capture_output=True, text=True, timeout=timeout_s)
    except subprocess.TimeoutExpired:
        print("verify_couple: simulation timed out (NO VERDICT)"); return None
    if r.returncode != 0 or not os.path.exists(out_file):
        print("verify_couple: vvp failed (NO VERDICT):\n" + r.stdout + r.stderr); return None
    return out_file


def compare(exp, out_file, log):
    rows = []
    for line in open(out_file).read().splitlines():
        t = line.split()
        rows.append(tuple(None if x == "x" else int(x) for x in t))
    if len(rows) < len(exp) or any(len(r) != 2 for r in rows[:len(exp)]):
        print(f"verify_couple: RTL produced {len(rows)} rows, model {len(exp)}: NO VERDICT"); return 2
    mism = [0, 0]; xs = 0; first = [None, None]; per_seg = {}
    for i, (e, r) in enumerate(zip(exp, rows)):
        for b in (0, 1):
            if r[b] is None: xs += 1
            if r[b] != e[b]:
                mism[b] += 1
                if first[b] is None: first[b] = (i, e[b], r[b])
                for n, s, c in log:
                    if s <= i < s + c: per_seg[n] = per_seg.get(n, 0) + 1
    if not any(mism):
        print(f"verify_couple: PASS -- {len(exp)} samples x 2 buses, RTL identical to model"); return 0
    print(f"verify_couple: FAIL -- mismatches mix {mism[0]}, body {mism[1]} of {len(exp)} samples each ({xs} undefined/X)")
    for b, nm in ((0, "mix"), (1, "body")):
        if first[b]:
            i, e, r = first[b]; print(f"  first {nm} mismatch at sample {i}: model {e}, RTL {'x' if r is None else r}")
    print("  mismatches by segment: " + ", ".join(f"{n}: {c}" for n, c in sorted(per_seg.items(), key=lambda t: -t[1])[:8]))
    return 1


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--inject", default=None); ap.add_argument("--expect-fail", action="store_true")
    ap.add_argument("--outdir", default=os.path.join(HERE, "build"))
    a = ap.parse_args(argv)
    v, log = build_vectors()
    exp, (em, eb) = expected(v)
    bad = closed_form_checks(v, log, exp)
    if bad:
        print("verify_couple: the model disagrees with the contract's own numbers (NO VERDICT):\n  " + "\n  ".join(bad)); return 2
    os.makedirs(a.outdir, exist_ok=True)
    with open(os.path.join(a.outdir, "couple_vec.hex"), "w") as fh:
        fh.writelines(f"{(rst << 45) | (en << 44) | ((xm & 0x3FFFFF) << 22) | (xb & 0x7FFFF):016x}\n" for en, xm, xb, rst in v)
    print(f"verify_couple: {len(v)} samples; model charge bits needed {em.acc_bits}/{eb.acc_bits} "
          f"(limit {em.acc_limit_bits}/{eb.acc_limit_bits}), output bits {em.out_bits}/{eb.out_bits} "
          f"(limit {em.out_limit_bits}/{eb.out_limit_bits}); closed-form checks passed")
    if (em.acc_bits, eb.acc_bits, em.out_bits, eb.out_bits) != (em.acc_limit_bits, eb.acc_limit_bits, em.out_limit_bits, eb.out_limit_bits):
        print("verify_couple: stimulus does not reach every bit of the proven widths (NO VERDICT)"); return 2
    defines = [f"INJECT_BUG_DRUM_COUPLE_{a.inject}"] if a.inject else []
    print(f"verify_couple: simulating drum_dcblock{' with ' + defines[0] if defines else ''}")
    out = simulate(defines, a.outdir, len(v))
    status = 2 if out is None else compare(exp, out, log)
    if a.expect_fail:
        if status == 1:
            print(f"verify_couple: negative control {a.inject} CAUGHT (comparison failed as required)"); return 0
        print(f"verify_couple: NEGATIVE CONTROL NOT CAUGHT (status {status})"); return 1 if status == 0 else 2
    return status


if __name__ == "__main__":
    sys.exit(main())
