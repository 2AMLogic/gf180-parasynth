#!/usr/bin/env python3
"""NEW (#552). The output stage of voice_dp.v (contract 12) with the COUPLED drum buses of
contract 15.10 driven at the ends of their declared words, against an INDEPENDENT closed form.

The voice is idle, so   sample = sat16((dmix*dvol + body*bvol) >> 15)   (ROUTE.DFILT = 0), and the
drum filter's input rail `dx` is the same sat16 of the same word (DFILT = 1). The expected value is
computed here in plain Python integers: it is not a model's output, so a model that shares the RTL's
truncation cannot grade it. A real kit never drives a bus to the end of a 23/20-bit word, so
the drum stream and the production top cannot show a truncation at this boundary; this does.

  --inject NAME   compile -DINJECT_BUG_VOICE_<NAME> (COUPLED_TRUNC, DACC39)
  --expect-fail   exit 0 only if the comparison gave 1; a NO VERDICT (2) is never a caught control

Status 0 identical, 1 differed, 2 NO VERDICT (tool missing, compile or simulation failure, short
output, a case the expected-value assertions reject). X output counts as a mismatch.
"""
from __future__ import annotations
import argparse, os, random, subprocess, sys
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from verify_ladder import tool

SRCS = ["tb_outpath.v", "voice_dp.v", "recip_div.v", "ladder_dp_n.v", "osc_2x_saw_path.v", "polyblep_saw_pair.v",
        "osc_substep_pair.v", "decimate_2x_tm_sym.v", "osc_2x_saw_bank.v", "rate_conv_2x.v"]
MIX_LO, MIX_HI = -(1 << 22), (1 << 22) - 1            # the coupled mix word, 23 bits
BODY_LO, BODY_HI = -(1 << 19), (1 << 19) - 1          # the coupled body word, 20 bits
G = 65535


def sat16(v): return max(-32768, min(32767, v))


def cases():
    rng = random.Random(552)
    c = []
    for dm in (MIX_LO, MIX_HI, 0, 1, -1, 1 << 21, -(1 << 21), (1 << 21) - 1, -(1 << 21) - 1):
        for bd in (BODY_LO, BODY_HI, 0, 1 << 18, -(1 << 18) - 1):
            for dvol, bvol in ((G, G), (1, 1), (1, G), (G, 1), (2, 0)):
                c.append((dm, bd, dvol, bvol, 0))
    # the sum of two full-scale products overflows 39 bits (2^22 * 65535 + 2^19 * 65535 > 2^38)
    c += [(MIX_HI, BODY_HI, G, G, 0), (MIX_LO, BODY_LO, G, G, 0), (MIX_HI, BODY_HI, G, G, 1), (MIX_LO, BODY_LO, G, G, 1)]
    # the unsaturated window, where a dropped sign/extension bit shows as a small wrong number
    c += [(MIX_HI, 0, 1, 0, 0), (MIX_LO, 0, 1, 0, 0), (0, BODY_HI, 0, 1, 0), (0, BODY_LO, 0, 1, 0)]
    for _ in range(120):
        c.append((rng.randint(MIX_LO, MIX_HI), rng.randint(BODY_LO, BODY_HI), rng.randint(0, G), rng.randint(0, G), rng.randint(0, 1)))
    for _ in range(60):
        c.append((rng.randint(-40000, 40000), rng.randint(-40000, 40000), rng.randint(0, 2000), rng.randint(0, 2000), rng.randint(0, 1)))
    return c


def want(case):
    dm, bd, dv, bv, df = case
    acc = dm * dv + bd * bv
    assert -(1 << 39) <= acc < (1 << 39)
    return sat16(acc >> 15)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--inject", default=None); ap.add_argument("--expect-fail", action="store_true")
    ap.add_argument("--outdir", default=os.path.join(HERE, "build"))
    a = ap.parse_args(argv)
    cs = cases(); os.makedirs(a.outdir, exist_ok=True)
    wide = sum(1 for c in cs if abs(c[0] * c[2] + c[1] * c[3]) >= (1 << 38))
    if not wide:
        print("verify_outpath: stimulus never exceeds 2^38 (NO VERDICT)"); return 2
    with open(os.path.join(a.outdir, "outpath_cases.hex"), "w") as fh:
        fh.writelines(f"{((dm & 0x7FFFFF) << 80) | ((bd & 0xFFFFF) << 60) | (dv << 32) | (bv << 16) | df:032x}\n"
                      for dm, bd, dv, bv, df in cs)
    iverilog, vvp = tool("iverilog"), tool("vvp")
    if not iverilog or not vvp:
        print("verify_outpath: iverilog/vvp not on PATH: NO VERDICT"); return 2
    vf, of = os.path.join(a.outdir, "tb_outpath.vvp"), os.path.join(a.outdir, "outpath_rtl_out.txt")
    if os.path.exists(of): os.remove(of)
    defs = [f"-DINJECT_BUG_VOICE_{a.inject}"] if a.inject else []
    r = subprocess.run([iverilog, "-g2012", "-s", "tb_outpath", "-o", vf] + defs + SRCS, cwd=HERE, capture_output=True, text=True)
    if r.returncode != 0:
        print("verify_outpath: iverilog failed (NO VERDICT):\n" + r.stdout + r.stderr); return 2
    try:
        r = subprocess.run([vvp, "-n", vf, f"+cases={os.path.join(a.outdir, 'outpath_cases.hex')}", f"+out={of}", f"+n={len(cs)}"],
                           cwd=HERE, capture_output=True, text=True, timeout=1800)
    except subprocess.TimeoutExpired:
        print("verify_outpath: simulation timed out (NO VERDICT)"); return 2
    if r.returncode != 0 or not os.path.exists(of) or "TIMEOUT" in r.stdout:
        print("verify_outpath: vvp failed (NO VERDICT):\n" + r.stdout + r.stderr); return 2
    rows = [l.split() for l in open(of).read().splitlines()]
    if len(rows) < len(cs):
        print(f"verify_outpath: {len(rows)} rows for {len(cs)} cases (NO VERDICT)"); return 2
    bad_s = bad_dx = xs = 0; first = None
    for i, (c, row) in enumerate(zip(cs, rows)):
        w = want(c)
        if row[0] == "x":
            xs += 1; bad_s += c[4] == 0; bad_dx += c[4] == 1
            first = first or (i, c, w, "x"); continue
        got = int(row[0]) if c[4] == 0 else int(row[1])
        if got != w:
            bad_s += c[4] == 0; bad_dx += c[4] == 1
            first = first or (i, c, w, got)
    print(f"verify_outpath: {len(cs)} cases ({wide} beyond 2^38), DFILT=0 sample and DFILT=1 filter-rail checked")
    status = 0 if not (bad_s or bad_dx) else 1
    if status:
        i, c, w, g = first
        print(f"verify_outpath: FAIL -- {bad_s} sample mismatches (DFILT=0), {bad_dx} drum-filter-rail mismatches "
              f"(DFILT=1), {xs} undefined; first: case {i} (dmix, body, dvol, bvol, dfilt) = {c}: want {w}, RTL {g}")
    else:
        print("verify_outpath: PASS -- every case identical to the closed form")
    if a.expect_fail:
        if status == 1:
            print(f"verify_outpath: negative control {a.inject} CAUGHT"); return 0
        print(f"verify_outpath: NEGATIVE CONTROL NOT CAUGHT (status {status})"); return 1
    return status


if __name__ == "__main__":
    sys.exit(main())
