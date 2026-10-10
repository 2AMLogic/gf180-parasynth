#!/usr/bin/env python3
"""i2s_tx on its own, with the strobe early enough that the right channel is observable (#619).

verify_synth_top.py cannot discriminate INJECT_BUG_I2S_SWAP: the core strobes
its sample past cycle 127, where i2s_tx re-reads `held` for the right slot, so
the swapped stream is bit-identical (verify_synth_top prints a NOTE). That left
the right channel with no negative control at all. tb_i2s_tx.v strobes at
cycle 20, so `held` differs from `cur` at the right slot.

  verify_i2s_tx.py                      clean: every L and R word must match -> 0
  verify_i2s_tx.py --inject I2S_SWAP --expect-fail
                                        the control must be CAUGHT (exit 0) -- and
                                        must be caught as R_bad > 0 with L_bad == 0,
                                        so a wrong-channel failure is not confused
                                        with some other defect.

REFUSED (exit 2) is not a pass and not a fail: no iverilog, or the bench did
not print its result line.
"""
from __future__ import annotations
import argparse, os, re, shutil, subprocess, sys, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
LINE = re.compile(r"tb_i2s_tx: periods (\d+) L_bad (\d+) R_bad (\d+) LR_differ (\d+)")
# defect -> what the counters must look like when it is caught
SIGNATURE = {"I2S_SWAP": lambda p, l, r, d: l == 0 and r > 0 and d > 0}


def run(inject: str | None, outdir: str):
    iverilog, vvp = shutil.which("iverilog"), shutil.which("vvp")
    if not (iverilog and vvp):
        print("verify_i2s_tx: REFUSED -- iverilog/vvp not on PATH"); return None
    exe = os.path.join(outdir, "tb_i2s_tx.vvp")
    cmd = [iverilog, "-g2012", "-o", exe] + ([f"-DINJECT_BUG_{inject}"] if inject else []) + \
          [os.path.join(HERE, "tb_i2s_tx.v"), os.path.join(HERE, "i2s_tx.v")]
    c = subprocess.run(cmd, capture_output=True, text=True)
    if c.returncode:
        print("verify_i2s_tx: REFUSED -- compile failed:\n" + c.stdout + c.stderr); return None
    r = subprocess.run([vvp, "-n", exe], capture_output=True, text=True)
    m = LINE.search(r.stdout)
    if r.returncode or not m:
        print("verify_i2s_tx: REFUSED -- the bench printed no result line:\n" + r.stdout + r.stderr); return None
    return tuple(int(x) for x in m.groups())


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--inject")
    ap.add_argument("--expect-fail", action="store_true")
    ap.add_argument("--outdir")
    a = ap.parse_args(argv)
    if a.inject and a.inject not in SIGNATURE:
        print(f"verify_i2s_tx: REFUSED -- unknown control {a.inject}"); return 2
    outdir = a.outdir or tempfile.mkdtemp(prefix="verify_i2s_tx_")
    os.makedirs(outdir, exist_ok=True)
    got = run(a.inject, outdir)
    if got is None:
        return 2
    periods, l, r, d = got
    print(f"verify_i2s_tx: {periods} periods decoded; L_bad {l}, R_bad {r}, L!=R {d}")
    if periods < 30:
        print("verify_i2s_tx: REFUSED -- too few periods decoded to mean anything"); return 2
    failed = (l or r)
    if not a.expect_fail:
        print("verify_i2s_tx: " + ("FAIL" if failed else "PASS -- every L and R word matches the sample strobed a frame earlier"))
        return 1 if failed else 0
    if not failed:
        print(f"verify_i2s_tx: NEGATIVE CONTROL {a.inject} NOT CAUGHT"); return 1
    if not SIGNATURE[a.inject](periods, l, r, d):
        print(f"verify_i2s_tx: NEGATIVE CONTROL {a.inject} failed the WRONG way (expected R_bad > 0, L_bad == 0)"); return 1
    print(f"verify_i2s_tx: negative control {a.inject} CAUGHT (as the right channel differing from the left)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
