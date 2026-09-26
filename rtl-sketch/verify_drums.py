#!/usr/bin/env python3
"""Bit-exact verification of drum_kit.v (drum_dp.v + modal_dp.v) against
model/drums_fx.py (DrumsFx).

The Python model IS the specification. This script

  1. builds a write stream (the kit of Appendix G, hits, retunes, register
     extremes and a RESET) and runs DrumsFx on it, recording both buses for
     every frame; writes build/drum_writes.hex and build/drum_expected.txt;
  2. compiles tb_drums.v + drum_kit.v + drum_dp.v + modal_dp.v with iverilog
     and runs it; the bench applies the same writes at the same frame
     boundaries and dumps "mix_out body_out" per frame;
  3. compares the two, value for value, with NO tolerance (verify_ladder's
     comparator on the interleaved stream), and reports the first mismatch.

Exit status, as verify_ladder.py: 0 identical, 1 differed, 2 did not run.

  --inject NAME     compile with -DINJECT_BUG_<NAME> (DRUM_ENV_FLOOR, DRUM_LEVEL_TRIG,
                    DRUM_LFSR_TAP, DRUM_TAP_NOSAT, DRUM_LAST_PATH, DRUM_SQ_LONE,
                    MODAL_NUM_HOLD, MODAL_EXC_NOCLEAR, ...)
  --jitter K        apply each frame's writes K clocks after its tick (the timing
                    contract's negative control)
  --expect-fail     exit 0 only if the comparison gave 1
  --short           a quarter of the stimulus, for a quick check
"""
from __future__ import annotations
import argparse, os, subprocess, sys
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(ROOT, "model"))
sys.path.insert(0, os.path.join(ROOT, "audition"))
import numpy as np
import modal_fixed
import drums_fx as dx
from dsp import SR
from verify_ladder import tool, compare


def stimulus(short: bool = False):
    """The write stream and its length. State carries across segments."""
    S = 0.25 if short else 1.0
    kit = dx.kit_808()
    hits, writes = [], []
    f = 10
    # 1. every stop soloed at accent 1.0 -- eleven of them since revision 10, so the cymbal's three
    #    VCA paths and the RS/CL pair's four are all driven, and the BD and the THREE tom circuits
    #    carry the coefficient sequences of 15.7
    for s in range(dx.N_STOPS):
        hits.append((f, s, 1.0)); f += int(0.12 * S * SR) + 1
    # 2. the same stop hit in consecutive frames fires once; 1-0-1 fires twice; a rewrite of 1 does not
    writes += [(f, dx.A_STOPS, 1 << dx.CH), (f + 1, dx.A_STOPS, 1 << dx.CH), (f + 2, dx.A_STOPS, 0),
               (f + 3, dx.A_STOPS, 1 << dx.CH), (f + 4, dx.A_STOPS, 0), (f + 5, dx.A_STOPS, 1 << dx.CH),
               (f + 6, dx.A_STOPS, 1 << dx.CH), (f + 7, dx.A_STOPS, 0)]
    f += int(0.05 * SR)
    # 3. all eleven in one frame at accent 2.0: the loudest hit the kit's registers allow
    hits += [(f, s, 2.0) for s in range(dx.N_STOPS)]
    f += int(0.15 * S * SR)
    # 4. a bar of the groove with accents, the OH -> CH choke, and the BD retuned while it rings
    bpm = 140.0 if not short else 400.0
    hits += dx.pattern_hits(dx.PATTERN_808, bpm=bpm, bars=1, start_s=f / SR)
    for i, q in enumerate((dx.bd_decay_q(9.0), dx.bd_decay_q(1.0))):      # retune the BD every half bar
        fr = f + int((0.6 + 0.9 * i) * SR * 60 / bpm)
        # BD_HZ_CHART is used here only as A DIFFERENT FREQUENCY to retune to while the mode
        # rings -- it is not the kit's f0, which is the circuit's 49.4 Hz (DR 0009)
        writes += [(fr, a, v) for a, v in dx.mode_writes(dx.M_BD, dx.BD_HZ_CHART, q, 0.0)[:2]]
    f += int(60.0 / bpm * 4 * SR) + int(0.05 * SR)
    # 4b. revision 11, the clap's final strike (15.3; plan084). Retrigger one frame BEFORE, AT and
    #     one AFTER the final-strike boundary, each at a different accent so a stale captured level
    #     shows; mid-note PEAK / ACCENT / RATE / FRATE writes; a choke between the third strike and
    #     the final one (no ghost); and CP -> MA -> CP switched while each is still sounding.
    LAST, P = dx.CP_BURSTS * dx.CP_PERIOD, dx.CP_PERIOD
    eb = dx.A_ENV + dx.E_CPBURST * dx.ENV_STRIDE
    h = [f, f + LAST - 1]
    h += [h[-1] + LAST, h[-1] + LAST + LAST + 1]
    hits += [(h[0], dx.CP, 1.0), (h[1], dx.CP, 0.5), (h[2], dx.CP, 2.0), (h[3], dx.CP, 1.0)]
    writes += [(h[3] + 100, eb + 1, dx.peak_reg(0.2)), (h[3] + 101, dx.A_ACCENT + dx.CP, dx.accent_reg(1.5)),
               (h[3] + 300, eb + 2, dx.rate_reg(8e-3)), (h[3] + LAST + 50, eb + 3, dx.rate_reg(5e-3))]
    f = h[3] + LAST + 1200
    writes += [(f, a, v) for a, v in dx.preset_writes("CP")]
    writes.append((f, eb, dx.env_ctl(dx.CP, dx.CB, 0, dx.CP_BURSTS, P)))           # choke CP by CB
    hits += [(f + 2, dx.CP, 1.0), (f + 2 + 2 * P + 20, dx.CB, 1.0)]
    f += LAST + 600
    writes += [(f, a, v) for a, v in dx.preset_writes("CP")]                        # choke off again
    hits.append((f + 2, dx.CP, 1.0))
    writes += [(f + 2 + P + 7, a, v) for a, v in dx.preset_writes("MA")]            # CP -> MA, CP ringing
    hits.append((f + 2 + P + 40, dx.CP, 1.0))                                       # strike MA
    writes += [(f + 2 + P + 400, a, v) for a, v in dx.preset_writes("CP")]          # MA -> CP, MA ringing
    hits.append((f + 2 + P + 430, dx.CP, 1.0))
    f += P + 430 + LAST + 1500
    # 5. register extremes on the last three paths and the spare mode, every stop again at accent 2.0:
    #    a tap of the ringing BD into an unstable mode (the state rails, the body word saturates),
    #    two full envelopes on one path (envsum 65534), att 7, nl = TANH on a raw source, the last
    #    path index in use, and EVERY lone-square source index 0..5 (15.4) so no SQ decode is unreached
    # The mode used for the unstable-coefficient corner is the LAST one (15), which is ABOVE NUMS
    # and therefore always RAW -- the same corner the revision-8 stimulus reached through M_SPARE.
    # A second unstable mode is loaded at index 7, which is BELOW NUMS = 11, so the numerator
    # datapath is exercised at the state rail too; that combination did not exist before.
    UNST_HI, UNST_NUM = dx.N_MODES - 1, 7
    ext = [(dx.A_PATH + dx.N_PATH - 3,
            dx.path_word(dx.SRC_TAP + dx.M_BD, dx.ENV_FULL, dx.ENV_FULL, dx.NL_LIN, 0, UNST_HI)),
           (dx.A_PATH + dx.N_PATH - 2,
            dx.path_word(dx.SRC_SQSUM, dx.ENV_FULL, dx.ENV_FULL, dx.NL_TANH, 7, dx.DEST_MIX)),
           (dx.A_PATH + dx.N_PATH - 1,
            dx.path_word(dx.SRC_NOISE, dx.E_OH, dx.E_CH, dx.NL_SWING, 0, dx.DEST_MIX)),
           (dx.A_PATH + dx.N_PATH - 4,
            dx.path_word(dx.SRC_TAP + dx.M_BD, dx.ENV_FULL, dx.ENV_FULL, dx.NL_LIN, 0, UNST_NUM))]
    for m in (UNST_HI, UNST_NUM):
        ext += [(dx.A_MODE + m * 4, (1 << 25) - 1), (dx.A_MODE + m * 4 + 1, (1 << 26) - (1 << 25)),
                (dx.A_MODE + m * 4 + 2, 65535), (dx.A_MODE + m * 4 + 3, 3)]
    writes += [(f, a, v) for a, v in ext]
    hits += [(f + 2, s, 2.0) for s in range(dx.N_STOPS)]
    f += int(0.08 * S * SR)
    # 5b. every lone-square index in turn on path 15, each through the swing VCA into the mix:
    #     src 5..10 must each decode to its own oscillator, which INJECT_BUG_DRUM_SQ_LONE breaks
    for i in range(6):
        writes.append((f, dx.A_PATH + dx.N_PATH - 1,
                       dx.path_word(dx.SRC_SQ + i, dx.ENV_FULL, nl=dx.NL_SWING, dest=dx.DEST_MIX)))
        f += int(0.006 * S * SR) + 1
    writes.append((f, dx.A_PATH + dx.N_PATH - 1, dx.path_word(dx.SRC_OFF, dx.ENV_FULL)))
    f += int(0.01 * S * SR)
    # 6. every envelope at its extremes: rate 0 (one LSB per frame), rate 65535, hold 255, 3 bursts at
    #    period 511, peak 0 and FULL, choke by its own stop; then hits
    for e in range(dx.N_ENV):
        writes += [(f, dx.A_ENV + e * 4, dx.env_ctl(e % dx.N_STOPS,
                                                    choke=(e + 1) % dx.N_STOPS if e % 3 == 0 else 15,
                                                    hold=255 if e % 4 == 1 else 0, bursts=3 if e % 4 == 2 else 0,
                                                    period=511 if e % 4 == 2 else 0)),
                   (f, dx.A_ENV + e * 4 + 1, 0 if e % 5 == 0 else dx.FULL24),
                   (f, dx.A_ENV + e * 4 + 2, 0 if e % 4 == 3 else (65535 if e % 4 == 1 else 3)),
                   # revision 11: FRATE on the bursting envelopes (the final strike, at an ordinary
                   # and at the extreme rate) and on burst-less ones (FRATE from the first decay)
                   (f, dx.A_ENV + e * 4 + 3, (dx.rate_reg(20e-3) if e % 8 == 2 else 65535) if e % 4 == 2
                    else (1 if e % 4 == 3 else 0))]
    hits += [(f + 3, s, 2.0) for s in range(dx.N_STOPS)]
    f += int(0.08 * S * SR)
    # 7. RESET mid-run, the kit again, one hit of each of BD and OH, then silence: decay to nothing
    writes.append((f, dx.A_RESET, 0))
    writes += [(f + 1, a, v) for a, v in kit]
    hits += [(f + 2, dx.BD, 1.0), (f + 2, dx.OH, 1.0)]
    f += int(0.5 * S * SR)
    n = f
    all_writes = sorted(dx.hit_writes(hits, kit) + writes, key=lambda t: t[0])
    return [w for w in all_writes if w[0] < n], n


class Coverage:
    """Counts the clamps the model takes, from outside the model."""
    def __init__(self, d: dx.DrumsFx):
        self.d = d; self.y_sat = self.out_sat = 0
        self._sat = modal_fixed.sat; modal_fixed.sat = self._count
    def _count(self, v, bits):
        r = self._sat(v, bits)
        if r != v:
            if bits == self.d.bank.OB: self.out_sat += 1
            else:                      self.y_sat += 1
        return r
    def restore(self): modal_fixed.sat = self._sat


def generate(outdir: str, short: bool = False, verbose: bool = True):
    writes, n = stimulus(short)
    d = dx.DrumsFx()
    cov = Coverage(d)
    dmix, body = d.play(writes, n)
    cov.restore()
    os.makedirs(outdir, exist_ok=True)
    with open(os.path.join(outdir, "drum_writes.hex"), "w") as fh:
        fh.writelines(f"{f:06x}{a & 0xff:02x}{v & 0xffffffff:08x}\n" for f, a, v in writes)
    expected = []
    for i in range(n):
        expected += [int(dmix[i]), int(body[i])]
    with open(os.path.join(outdir, "drum_expected.txt"), "w") as fh:
        fh.writelines(f"{dmix[i]} {body[i]}\n" for i in range(n))
    if verbose:
        fires = [int(np.sum((d.trace["fire"] >> s) & 1)) for s in range(dx.N_STOPS)]
        env = d.envs
        print(f"  {n} frames, {len(writes)} writes; fires per stop {fires}; "
              f"envelope fires {sum(e.n_fire for e in env)}, chokes {sum(e.n_choke for e in env)}, "
              f"re-strikes {sum(e.n_restrike for e in env)}, floor steps (dec = 0, level > 0) {sum(e.n_floor for e in env)}")
        print(f"  model corners reached: bank state saturated {cov.y_sat}x, body word saturated {cov.out_sat}x, "
              f"taps at the rail {d.n_tapsat}x; |dmix| max {np.abs(dmix).max()} of 2^21, "
              f"|exc| max {np.abs(d.trace['exc']).max()} of 2^20; "
              f"tail: last 100 frames |dmix| {np.abs(dmix[-100:]).max()}, |body| {np.abs(body[-100:]).max()}")
    return expected, n, d


def simulate(defines, outdir, n, jitter=0, timeout_s=1800.0):
    iverilog, vvp = tool("iverilog"), tool("vvp")
    if not iverilog or not vvp:
        print("verify_drums: iverilog/vvp not on PATH (or set OSS_CAD_SUITE)"); return None
    vvp_file = os.path.join(outdir, "tb_drums.vvp"); out_file = os.path.join(outdir, "drum_rtl_out.txt")
    if os.path.exists(out_file): os.remove(out_file)
    srcs = [os.path.join(HERE, f) for f in ("tb_drums.v", "drum_kit.v", "drum_dp.v", "modal_dp.v")]
    r = subprocess.run([iverilog, "-g2012", "-o", vvp_file] + [f"-D{d}" for d in defines] + srcs,
                       cwd=HERE, capture_output=True, text=True)
    if r.returncode != 0:
        print("verify_drums: iverilog failed:\n" + r.stdout + r.stderr); return None
    try:
        r = subprocess.run([vvp, "-n", vvp_file, f"+writes={os.path.join(outdir, 'drum_writes.hex')}",
                            f"+out={out_file}", f"+frames={n}", f"+jitter={jitter}"],
                           cwd=HERE, capture_output=True, text=True, timeout=timeout_s)
    except subprocess.TimeoutExpired:
        print("verify_drums: simulation timed out"); return None
    sys.stdout.write("".join("  sim: " + l + "\n" for l in r.stdout.splitlines() if l.startswith("tb_drums")))
    if r.returncode != 0 or not os.path.exists(out_file):
        print("verify_drums: vvp failed:\n" + r.stdout + r.stderr); return None
    return out_file


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--inject", default=None); ap.add_argument("--expect-fail", action="store_true")
    ap.add_argument("--jitter", type=int, default=0)
    ap.add_argument("--short", action="store_true")
    ap.add_argument("--compare-only", default=None, metavar="FILE")
    ap.add_argument("--outdir", default=os.path.join(HERE, "build"))
    a = ap.parse_args(argv)
    print(f"verify_drums: model DrumsFx({dx.N_ENV} envelopes, {dx.N_PATH} paths, bank {dx.N_MODES} modes / "
          f"{dx.N_NUMS} with numerators, headroom {dx.BODY_HR}, {dx.BODY_BITS}-bit body word)")
    expected, n, _ = generate(a.outdir, a.short)
    if a.compare_only:
        status = compare(expected, a.compare_only, "verify_drums")
    else:
        defines = [f"INJECT_BUG_{a.inject}"] if a.inject else []
        print(f"verify_drums: simulating drum_kit.v{' with ' + defines[0] if defines else ''}"
              f"{f', writes jittered {a.jitter} clocks into the frame' if a.jitter else ''}")
        out = simulate(defines, a.outdir, n, a.jitter)
        status = 2 if out is None else compare(expected, out, "verify_drums")
    if a.expect_fail:
        if status == 1:
            print(f"verify_drums: negative control {a.inject or 'jitter'} CAUGHT (comparison failed as required)"); return 0
        print(f"verify_drums: NEGATIVE CONTROL NOT CAUGHT (status {status})"); return 1 if status == 0 else 2
    return status


if __name__ == "__main__":
    sys.exit(main())
