#!/usr/bin/env python3
"""Bit-exact verification of modal_dp.v against model/modal_fixed.py (ModalFx).

Same contract as verify_ladder.py: the model runs a stimulus, the RTL is
driven with the identical integers under iverilog, every output sample is
compared with no tolerance. Exit 0 identical / 1 mismatch / 2 did not run;
--inject NAME compiles -DINJECT_BUG_MODAL_<NAME>, --expect-fail inverts.

The bank here is the standalone four-mode one (NUMS = 2, headroom 10, the
19-bit word): the struck bar's segments of the rev-3 bench, a segment that
runs modes 0 and 1 as the 808's hat band-pass and hi-hat high-pass on noise
(the numerators of contract 15.6), and a coefficient-extremes segment. The
bank inside the drum section (12 modes, NUMS 6, headroom 0) is verified by
verify_drums.py.
"""
from __future__ import annotations
import argparse, os, sys
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(ROOT, "model"))
sys.path.insert(0, os.path.join(ROOT, "audition"))
import numpy as np
import modal_fixed
from modal_fixed import ModalFx, strike, f2q15, pole_regs, RAW, BP, HP
from dsp import SR, note_hz, lfsr_noise
from verify_ladder import tool, compare

NUMS = 2


def bar_stimulus():
    """(name, exc_q15, note): the struck bar's six segments of the rev-3
    bench, also what gen_modal_rom.py drives the ROM variants with. State
    carries across segments: the second hit lands while the first still rings."""
    segs = [("note 28, strike 1.0",                    f2q15(strike(0.30, seed=2)), 28),
            ("note 52, strike 0.5, while ringing",      f2q15(strike(0.20, seed=3, amount=0.5)), 52),
            ("note 88, strike 1.0",                     f2q15(strike(0.15, seed=4)), 88),
            ("note 100: top mode above 0.45 fs, zero coefficients",
                                                        f2q15(strike(0.10, seed=5)), 100)]
    n = int(0.10 * SR)
    sq = np.sign(np.sin(2 * np.pi * note_hz(40) * np.arange(n) / SR))
    segs.append(("note 40, full-scale square AT f0: drives the state to the rail", f2q15(sq), 40))
    segs.append(("note 40, silence: ring-down from the rail", np.zeros(int(0.15 * SR), np.int16), 40))
    return segs


def stimulus(m: ModalFx):
    """(name, exc_q15, coefs, num): the bar, then the numerators and the extremes."""
    raw = [RAW] * m.M
    segs = [(name, xq, m.coefficients(note), raw) for name, xq, note in bar_stimulus()]
    # the numerators: modes 0 and 1 as the 808 hat band-pass and OH high-pass on noise, 2 and 3 the bar
    bar = m.coefficients(52)
    hat = [pole_regs(7117.0, 6.0) + (65535,), pole_regs(7800.0, 2.5) + (65535,), bar[2], bar[3]]
    n = int(0.10 * SR)
    segs.append(("noise 0.5: mode 0 band-pass, mode 1 high-pass, 2-3 the bar", f2q15(lfsr_noise(n) * 0.5),
                 hat, [BP, HP, RAW, RAW]))
    segs.append(("DC 0.25 into the same: the numerators reject it, the bar does not",
                 np.full(int(0.05 * SR), 8192, np.int16), hat, [BP, HP, RAW, RAW]))
    # coefficient extremes: a1 = +2 - lsb, a2 = -2 on an unstable mode, num = 3 (reads as RAW)
    ext = [((1 << 25) - 1, -(1 << 25), 65535), (-(1 << 25), (1 << 25) - 1, 65535), bar[2], bar[3]]
    segs.append(("coefficient extremes: unstable modes rail, num = 3 reads as RAW",
                 f2q15(strike(0.05, seed=6, amount=0.1)), ext, [3, 3, RAW, RAW]))
    return segs


class Coverage:
    def __init__(self, m: ModalFx):
        self.m = m; self.y_sat = self.out_sat = 0
        self._sat = modal_fixed.sat; modal_fixed.sat = self._count
    def _count(self, v, bits):
        r = self._sat(v, bits)
        if r != v:
            if bits == self.m.OB: self.out_sat += 1
            else:                 self.y_sat += 1
        return r
    def restore(self): modal_fixed.sat = self._sat


def generate(outdir: str, verbose=True):
    m = ModalFx(nums=NUMS)                          # the proposed defaults, two numerator modes
    cov = Coverage(m)
    lines, expected = [], []
    zero_modes = 0
    for name, xq, coefs, num in stimulus(m):
        zero_modes += sum(1 for c in coefs if c == (0, 0, 0)) * len(xq)
        y = m.process(xq, coefs, num)
        numb = sum((int(num[k]) & 3) << (2 * k) for k in range(4))
        for i in range(len(xq)):
            s = f"{int(xq[i]) & 0xffff:04x}"
            for a1, a2, amp in coefs:
                s += f"{a1 & 0xfffffff:07x}{a2 & 0xfffffff:07x}"
            s += "".join(f"{amp:04x}" for _, _, amp in coefs)
            s += f"{numb:02x}{int(y[i]) & 0xffffff:06x}\n"
            lines.append(s); expected.append(int(y[i]))
        if verbose:
            print(f"  segment: {name}: {len(xq)} samples, a1_0={coefs[0][0]} a2_0={coefs[0][1]}")
    cov.restore()
    os.makedirs(outdir, exist_ok=True)
    open(os.path.join(outdir, "modal_vectors.hex"), "w").writelines(lines)
    open(os.path.join(outdir, "modal_expected.txt"), "w").writelines(f"{v}\n" for v in expected)
    if verbose:
        print(f"  {len(expected)} samples; model corners reached: state saturated {cov.y_sat}x, "
              f"output saturated {cov.out_sat}x, zero-coefficient mode on {zero_modes} mode-samples")
    return expected, cov


def simulate(rtl, tb, defines, outdir):
    """As verify_ladder.simulate, with this bench's file names and no parameter overrides."""
    import subprocess
    iverilog, vvp = tool("iverilog"), tool("vvp")
    if not iverilog or not vvp:
        print("verify_modal: iverilog/vvp not on PATH (or set OSS_CAD_SUITE)"); return None
    vvp_file = os.path.join(outdir, "tb_modal_fx.vvp"); out_file = os.path.join(outdir, "modal_rtl_out.txt")
    if os.path.exists(out_file): os.remove(out_file)
    r = subprocess.run([iverilog, "-g2012", "-o", vvp_file, f"-Ptb_modal_fx.NUMS={NUMS}"]
                       + [f"-D{d}" for d in defines] + [tb, rtl],
                       cwd=HERE, capture_output=True, text=True)
    if r.returncode != 0:
        print("verify_modal: iverilog failed:\n" + r.stdout + r.stderr); return None
    try:
        r = subprocess.run([vvp, "-n", vvp_file, f"+vec={os.path.join(outdir, 'modal_vectors.hex')}",
                            f"+out={out_file}"], cwd=HERE, capture_output=True, text=True, timeout=600)
    except subprocess.TimeoutExpired:
        print("verify_modal: simulation timed out"); return None
    sys.stdout.write("".join("  sim: " + l + "\n" for l in r.stdout.splitlines() if l.startswith("tb_modal_fx")))
    if r.returncode != 0 or not os.path.exists(out_file):
        print("verify_modal: vvp failed:\n" + r.stdout + r.stderr); return None
    return out_file


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--inject", default=None); ap.add_argument("--expect-fail", action="store_true")
    ap.add_argument("--compare-only", default=None, metavar="FILE")
    ap.add_argument("--rtl", default=os.path.join(HERE, "modal_dp.v"))
    ap.add_argument("--tb", default=os.path.join(HERE, "tb_modal_fx.v"))
    ap.add_argument("--outdir", default=os.path.join(HERE, "build"))
    a = ap.parse_args(argv)
    # #1199: the simulator runs under cwd=HERE; a relative --outdir must be
    # pinned to the CALLER's cwd or the vectors, the -o and the +vec=/+out=
    # paths resolve against two different directories. Same normalisation
    # verify_ctl/verify_synth_top ship.
    a.outdir = os.path.abspath(a.outdir)
    m = ModalFx(nums=NUMS)
    print(f"verify_modal: model ModalFx(coef Q2.{m.CF}, state {m.SB}-bit/{m.SQ} fraction, "
          f"headroom {m.HR}, {m.OB}-bit word, {m.M} modes, {m.NUMS} with numerators, {'rounding' if m.RND else 'floor'})")
    expected, _ = generate(a.outdir)
    if a.compare_only:
        status = compare(expected, a.compare_only, "verify_modal")
    else:
        defines = [f"INJECT_BUG_MODAL_{a.inject}"] if a.inject else []
        print(f"verify_modal: simulating {os.path.relpath(a.rtl, HERE)}{' with ' + defines[0] if defines else ''}")
        out = simulate(a.rtl, a.tb, defines, a.outdir)
        status = 2 if out is None else compare(expected, out, "verify_modal")
    if a.expect_fail:
        if status == 1:
            print(f"verify_modal: negative control {a.inject or ''} CAUGHT (comparison failed as required)"); return 0
        print(f"verify_modal: NEGATIVE CONTROL NOT CAUGHT (status {status})"); return 1 if status == 0 else 2
    return status


if __name__ == "__main__":
    sys.exit(main())
