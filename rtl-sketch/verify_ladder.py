#!/usr/bin/env python3
"""Bit-exact verification of ladder_dp.v against model/fixed.py (LadderFx).

The Python model IS the specification. This script

  1. runs LadderFx on a fixed, deliberately nasty stimulus and writes the RTL's
     inputs (x, g, k, gain, ogain) and the model's output for every sample to
     build/ladder_vectors.hex;
  2. compiles tb_ladder.v + ladder_dp.v with iverilog and runs it; the bench
     dumps every y_out it sees to build/ladder_rtl_out.txt;
  3. compares the two, sample for sample, with NO tolerance, and reports the
     first mismatch, its size in LSB, and the worst error.

Exit status -- these are not interchangeable, and a CI job asserting that a
negative control fails must require exactly 1:

  0  every sample identical
  1  the simulation ran to completion and at least one sample differed
  2  it did not run, or the capture is not the declared output: tool missing,
     compile error, timeout, short or surplus output (count mismatch either way)

  --inject NAME     compile with -DINJECT_BUG_LADDER_<NAME>
  --expect-fail     invert the verdict: exit 0 only if the comparison gave 1.
                    This is how the negative controls are run.
  --tanh-n 16|256   table size; picks the RTL parameter and the model's
  --compare-only F  skip generation and simulation, compare F to the expected
"""
from __future__ import annotations
import argparse, math, os, shutil, subprocess, sys
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "model"))
sys.path.insert(0, os.path.join(ROOT, "audition"))
import numpy as np
import fixed, dsp
from dsp import SR


# ---- stimulus ---------------------------------------------------------------
# Five segments, chosen to reach every arithmetic corner the RTL has to get
# right, not to sound good. Each is (x_q15, cutoff_hz per sample, res, drive).
def stimulus():
    segs = []
    n = int(0.25 * SR)                                   # the README's patch
    x = dsp.osc("saw", dsp.ramp(n, dsp.phase_inc(110.0))) * 0.8
    segs.append(("saw 110 Hz, sweep 60->12 kHz, res 0.8, drive 2",
                 fixed.f2q15(x), np.geomspace(60.0, 12000.0, n), 0.8, 2.0))
    n = int(0.10 * SR)                                   # self-oscillation, k > 2^16
    segs.append(("near-silence, 400 Hz, res 1.08, drive 1",
                 fixed.f2q15(np.full(n, 1e-3)), np.full(n, 400.0), 1.08, 1.0))
    n = int(0.10 * SR)                                   # hard clipping, g > 2^15
    x = dsp.osc("square", dsp.ramp(n, dsp.phase_inc(55.0)))
    segs.append(("full-scale square 55 Hz, 15 kHz, res 1.0, drive 3",
                 fixed.f2q15(x), np.full(n, 15000.0), 1.0, 3.0))
    n = int(0.05 * SR)                                   # broadband
    segs.append(("LFSR noise 0.5, 2 kHz, res 0.9, drive 1.5",
                 fixed.f2q15(dsp.lfsr_noise(n) * 0.5), np.full(n, 2000.0), 0.9, 1.5))
    n = int(0.10 * SR)                                   # limit-cycle tail
    segs.append(("silence, 600 Hz, res 0.85, drive 2.5",
                 np.zeros(n, dtype=np.int16), np.full(n, 600.0), 0.85, 2.5))
    return segs


class Coverage:
    """Counts the saturation and clamp events the model takes, so the report
    can say which corners the stimulus actually reached. Instruments the model
    from outside; the model's arithmetic is untouched."""
    def __init__(self, f: fixed.LadderFx):
        self.f = f
        self.sb_calls = 0
        self.u_sat = self.y_sat = self.out_sat = self.tanh_clamp = self.tanh_calls = 0
        self._sat, self._tanh = fixed.sat, f.tanh_fx
        fixed.sat = self._count_sat
        f.tanh_fx = self._count_tanh

    def _count_sat(self, v, bits):
        r = self._sat(v, bits)
        if bits == self.f.OB:
            self.out_sat += (r != v)
        else:                       # process() calls sat(u) then sat(y) x4 per step
            pos = self.sb_calls % 5
            self.sb_calls += 1
            if r != v:
                if pos == 0: self.u_sat += 1
                else:        self.y_sat += 1
        return r

    def _count_tanh(self, y):
        self.tanh_calls += 1
        self.tanh_clamp += (abs(y) >= self.f.dom_fx)
        return self._tanh(y)

    def restore(self):
        fixed.sat = self._sat
        self.f.tanh_fx = self._tanh


OUT_BITS = 19       # the voice's ladder output word, Q4.15 (contract 11.2, DR 0005)


def generate(tanh_n: int, outdir: str, verbose=True, out_bits: int = OUT_BITS, nch: int = 1):
    """Run the model; write vectors + expected. Returns (expected list, coverage).

    `nch` > 1 builds NCH INDEPENDENT LadderFx instances and sends row j to
    channel j % nch -- the bench's assignment. Each channel therefore runs its
    own signal, its own cutoff sweep and its own resonance, which is the whole
    point: with every channel fed the identical sequence (what this script and
    tb_ladder_n.v did before) their states are identical, and a design that
    leaks state between channels still gives the right answer on every sample.
    """
    cfg = dict(state_bits=24, state_q=20, tanh_entries=tanh_n, interp=True, out_bits=out_bits)
    f = fixed.LadderFx(**cfg)
    chans = [f] if nch <= 1 else [fixed.LadderFx(**cfg) for _ in range(nch)]
    cov = Coverage(chans[0])
    lines, expected = [], []
    g_hi = k_hi = 0
    row0 = 0
    for name, xq, fc, res, drive in stimulus():
        g_tab, k, gain, ogain = f.coefficients(fc, res, drive)
        if nch <= 1:
            y = f.process(xq, fc, res, drive)
        else:
            y = np.empty(len(xq), dtype=np.int64)
            idx_all = np.arange(len(xq))
            for c in range(nch):
                idx = idx_all[(row0 + idx_all) % nch == c]
                if len(idx) == 0:
                    continue
                y[idx] = chans[c].process(xq[idx], None, res, drive, g_q16=g_tab[idx],
                                          k=k, gain=gain, ogain=ogain)
        row0 += len(xq)
        g_hi += int(np.sum(g_tab >= 32768)); k_hi += (k >= 65536) * len(xq)
        assert 0 < k < (1 << 17) and 0 < gain < (1 << 20) and 0 < ogain < (1 << 20)
        for i in range(len(xq)):
            xi, gi, yi = int(xq[i]), int(g_tab[i]), int(y[i])
            lines.append(f"{xi & 0xffff:04x}{gi:04x}{k:06x}{gain:06x}{ogain:06x}{yi & 0xffffff:06x}\n")
            expected.append(yi)
        if verbose:
            print(f"  segment: {name}: {len(xq)} samples, k={k} gain={gain} ogain={ogain}")
    cov.restore()
    os.makedirs(outdir, exist_ok=True)
    with open(os.path.join(outdir, "ladder_vectors.hex"), "w") as fh:
        fh.writelines(lines)
    with open(os.path.join(outdir, "ladder_expected.txt"), "w") as fh:
        fh.writelines(f"{v}\n" for v in expected)
    if verbose:
        print(f"  {len(expected)} samples; model corners reached: "
              f"u saturated {cov.u_sat}x, state saturated {cov.y_sat}x, "
              f"output saturated {cov.out_sat}x, tanh clamped {cov.tanh_clamp} of "
              f"{cov.tanh_calls} lookups; g>=2^15 on {g_hi} samples, k>=2^16 on {k_hi}")
    return expected, cov


# ---- simulation -------------------------------------------------------------
def tool(name: str) -> str | None:
    p = shutil.which(name)
    if p: return p
    suite = os.environ.get("OSS_CAD_SUITE")
    if suite and os.path.exists(os.path.join(suite, "bin", name)):
        return os.path.join(suite, "bin", name)
    return None


def simulate(rtl: str, tb: str, log2n: int, rom: str, defines: list[str],
             outdir: str, timeout_s: float = 600.0, out_bits: int = OUT_BITS, nch: int = 0) -> str | None:
    iverilog, vvp = tool("iverilog"), tool("vvp")
    if not iverilog or not vvp:
        print("verify_ladder: iverilog/vvp not on PATH (or set OSS_CAD_SUITE)")
        return None
    vvp_file = os.path.join(outdir, "tb_ladder.vvp")
    out_file = os.path.join(outdir, "ladder_rtl_out.txt")
    if os.path.exists(out_file): os.remove(out_file)
    tbm = "tb_ladder_n" if nch else "tb_ladder"          # the bench module the -P parameters address
    cmd = [iverilog, "-g2012", "-o", vvp_file,
           f"-P{tbm}.LOG2N={log2n}", f'-P{tbm}.ROM_FILE="{rom}"', f"-P{tbm}.OW={out_bits}"]
    if nch:
        cmd.append(f"-P{tbm}.NCH={nch}")
    cmd += [f"-D{d}" for d in defines] + [tb, rtl]
    r = subprocess.run(cmd, cwd=HERE, capture_output=True, text=True)
    if r.returncode != 0:
        print("verify_ladder: iverilog failed:\n" + r.stdout + r.stderr)
        return None
    try:   # cwd=HERE so the DUT's $readmemh(ROM_FILE) resolves like yosys's does
        r = subprocess.run([vvp, "-n", vvp_file,
                            f"+vec={os.path.join(outdir, 'ladder_vectors.hex')}",
                            f"+out={out_file}"],
                           cwd=HERE, capture_output=True, text=True, timeout=timeout_s)
    except subprocess.TimeoutExpired:
        print("verify_ladder: simulation timed out")
        return None
    sys.stdout.write("".join("  sim: " + l + "\n" for l in r.stdout.splitlines()
                             if l.startswith("tb_ladder") or "TIMEOUT" in l))
    if r.returncode != 0 or not os.path.exists(out_file):
        print("verify_ladder: vvp failed:\n" + r.stdout + r.stderr)
        return None
    return out_file


# ---- comparison -------------------------------------------------------------
def compare(expected: list[int], rtl_out: str, name: str = "verify_ladder") -> int:
    """0 identical, 1 mismatch, 2 absent/short/surplus output."""
    try:
        toks = open(rtl_out).read().split()
    except OSError:
        print(f"{name}: no RTL output at {rtl_out}")
        return 2
    got = []
    for t in toks:
        try: got.append(int(t))
        except ValueError: got.append(None)          # 'x' -- undefined
    n = len(expected)
    if len(got) != n:
        # one RTL value per expected value: a count mismatch in EITHER direction
        # is a malformed capture. Slicing the prefix off here let a surplus trace
        # pass as identical (#1366), and the count is checked before any value so
        # a wrong value plus surplus is still 2, not a caught defect's 1.
        if len(got) < n:
            print(f"{name}: RTL produced {len(got)} samples, model expected {n} -- "
                  "fewer, did not run to completion")
        else:
            print(f"{name}: RTL produced {len(got)} samples, model expected {n} -- "
                  "more, surplus output, not the declared capture")
        return 2
    first, mism, maxerr, xs, sq = None, 0, 0, 0, 0.0
    for i, (e, r) in enumerate(zip(expected, got)):
        if r is None:
            xs += 1; mism += 1
            if first is None: first = (i, e, r)
            continue
        if r != e:
            mism += 1
            if first is None: first = (i, e, r)
            maxerr = max(maxerr, abs(r - e))
            sq += (r - e) ** 2
    if mism == 0:
        print(f"{name}: PASS -- {n} samples, RTL identical to model")
        return 0
    i, e, r = first
    print(f"{name}: FAIL -- {mism} of {n} samples differ ({xs} undefined/X)")
    print(f"  first mismatch at sample {i}: model {e}, RTL {'x' if r is None else r}"
          + ("" if r is None else f", error {r - e:+d} LSB"))
    if mism > xs:
        rms_sig = math.sqrt(sum(v * v for v in expected) / n)
        rms_err = math.sqrt(sq / n)
        print(f"  worst |error| {maxerr} LSB; error RMS {rms_err:.1f} LSB "
              f"vs signal RMS {rms_sig:.1f} LSB ({20*math.log10(max(rms_err,1e-9)/max(rms_sig,1e-9)):+.1f} dB)")
    return 1


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--tanh-n", type=int, default=16, choices=(16, 256))
    ap.add_argument("--inject", default=None, help="INJECT_BUG_LADDER_<NAME> to compile in")
    ap.add_argument("--expect-fail", action="store_true")
    ap.add_argument("--compare-only", default=None, metavar="FILE")
    ap.add_argument("--rtl", default=os.path.join(HERE, "ladder_dp.v"))
    ap.add_argument("--tb", default=os.path.join(HERE, "tb_ladder.v"))
    ap.add_argument("--outdir", default=os.path.join(HERE, "build"))
    ap.add_argument("--nch", type=int, default=0,
                    help="verify ladder_dp_n.v with this many channels (tb_ladder_n.v): every sample "
                         "is driven to each channel in turn and every channel must match the model")
    ap.add_argument("--legacy-stimulus", action="store_true",
                    help="drive every row to every channel, as this bench did before: the control "
                         "that shows that stimulus cannot detect cross-channel state bleeding")
    ap.add_argument("--out-bits", type=int, default=OUT_BITS, choices=(16, 19),
                    help="ladder output width: 19 (Q4.15, the voice's) or 16 (Q1.15, rev 1)")
    a = ap.parse_args(argv)
    log2n = {16: 4, 256: 8}[a.tanh_n]
    rom = {16: "tanh16.hex", 256: "tanh256.hex"}[a.tanh_n]
    print(f"verify_ladder: model LadderFx(24-bit state, 20 fraction, {a.tanh_n}-entry interpolated tanh, "
          f"{a.out_bits}-bit output)")
    if a.nch and not a.legacy_stimulus:          # ladder_dp_n: row j to channel j % NCH
        expected, _ = generate(a.tanh_n, a.outdir, out_bits=a.out_bits, nch=a.nch)
    else:
        expected, _ = generate(a.tanh_n, a.outdir, out_bits=a.out_bits)
    if a.nch:
        a.rtl = os.path.join(HERE, "ladder_dp_n.v"); a.tb = os.path.join(HERE, "tb_ladder_n.v")
        if a.legacy_stimulus:                    # THE OLD BENCH, kept as the control that it is blind
            expected = [e for e in expected for _ in range(a.nch)]
    if a.compare_only:
        status = compare(expected, a.compare_only)
    else:
        defines = [f"INJECT_BUG_LADDER_{a.inject}"] if a.inject else []
        if a.legacy_stimulus: defines.append("LEGACY_STIMULUS")
        print(f"verify_ladder: simulating {os.path.relpath(a.rtl, HERE)} "
              f"(TANH_LOG2N={log2n}, {rom}{', ' + defines[0] if defines else ''}"
              f"{', NCH=%d' % a.nch if a.nch else ''})")
        out = simulate(a.rtl, a.tb, log2n, rom, defines, a.outdir, out_bits=a.out_bits, nch=a.nch)
        status = 2 if out is None else compare(expected, out)
    if a.expect_fail:
        if status == 1:
            print(f"verify_ladder: negative control {a.inject or ''} CAUGHT (comparison failed as required)")
            return 0
        print(f"verify_ladder: NEGATIVE CONTROL NOT CAUGHT (status {status})")
        return 1 if status == 0 else 2
    return status


if __name__ == "__main__":
    sys.exit(main())
