#!/usr/bin/env python3
"""fpga/release/glide_boundary.py -- RTL evidence for the release's increment
and glide domain, and for where #247 begins.

The release validator (`qualified_domain.py`) admits a programmed oscillator
increment only inside [INC_LO, INC_HI] -- the phase increments of MIDI note 0
and MIDI note 127 -- and admits a glide only between two such increments.
That is a CLAIM about the RTL until the RTL has run those transitions against
the model. This bench runs them, through the existing voice bench
(`rtl-sketch/verify_voice.py`: its model generation, its register-port
simulation and its every-tap comparison), with its scenario list replaced by
the cases below. Nothing in verify_voice.py is edited.

Build flags are the release's: OSC2X=1 FILTER2X=1, PULSE2X=0. Two levels:

  --level voice    voice_dp at its register write port through verify_voice:
                   every tap of every frame compared. Only the ADMITTED cases
                   run here: the component bench launches the voice at cycle
                   48, and at increments near 2^23 three 2x saws do not finish
                   in that window ("datapath still busy at the end of frame
                   0") -- #248's late component launch, not a result about the
                   production schedule.
  --level wrapper  the SHIPPED path: the case's writes are planned by
                   fpga/uart_host.py (setup as live writes, transitions as
                   device-scheduled events), captured as the exact bytes, and
                   replayed through the Arty wrapper's UART pins by
                   fpga/verify_uart_bridge.py --replay, I2S decoded from the
                   wire and compared with the integer model. Production
                   launch; this is where #247 was found.

CASES, and the verdict each must give (exit status is the verdict):

  accept-default   every boundary transition the validator ADMITS, on the
  accept-pulse29   release waveform sets (saw, saw, square) and pulse29 x3:
                   LO -> HI at 0.02 s/oct (the whole range, one glide), HI ->
                   LO at the maximum rate (an octave per frame), a semitone
                   into HI at the reference rate, the glide = 1 floor at HI,
                   and a retarget mid-glide at the top.     MUST exit 0.
  accept-247-shape #247's stimulus shape (slow glide at 2.0 s/oct retargeted
                   every 40 frames, its noise/modulation patch) scaled so
                   the top increment is INC_HI.                MUST exit 0.
  probe-247-incs   #247's own increments and shape WITHOUT the drum-filter
                   route and strikes (RECORD). fpga/release/probe_247.py, on
                   #247's own bench, shows its mismatch needs route = 1 plus
                   drum strikes and does not need a glide or a high increment.

  The injected-bug control is voice-level: --inject GLIDE_FLOOR must turn
  accept-default red (the glide = 1 floor at LO engages it).
  probe-sub-nyq    CHARACTERISATION (RECORD), not a gate: a glide just below 2^23
  probe-cross-nyq  (0x780000 -> 0x7FFFFF), one across it (0x7C0000 ->
  probe-127-up12   0x840000), and plan080's example (MIDI 127 at +12
                   semitones: HI -> 2*HI). Either outcome is recorded; these
                   locate the boundary, they do not move the validator's.

    .venv/bin/python fpga/release/glide_boundary.py --case accept-default
    .venv/bin/python fpga/release/glide_boundary.py --case accept-default --inject GLIDE_FLOOR --expect-fail
    .venv/bin/python fpga/release/glide_boundary.py --level wrapper --case accept-247-shape

Exit statuses are the benches': 0 identical, 1 differed, 2 did not run.
--expect-fail exits 0 only when the comparison gave 1.
"""
from __future__ import annotations

import argparse
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path[:0] = [HERE, os.path.join(ROOT, "rtl-sketch"), os.path.join(ROOT, "model"),
                os.path.join(ROOT, "audition")]

import qualified_domain as qd          # noqa: E402  the bounds under test
import verify_voice as vv              # noqa: E402
import voice_fx as vf                  # noqa: E402

FULL24 = (1 << 24) - 1
LO, HI = qd.INC_LO, qd.INC_HI
SLOW = vf.glide_reg(0.02)              # 12118: 0.02 s per octave
REF = vf.glide_reg(vf.GLIDE_REF_S)     # 2692: the preset rate


def _incs3(base, spread=1000):
    """Three distinct increments at or below `base` (or at/above it when
    base is LO), so each oscillator's slew is exercised separately."""
    if base <= LO:
        return [LO, LO + spread, LO + 2 * spread]
    return [base, base - spread, base - 2 * spread]


def _set(f, incs, jump):
    return [(f, "INC", k, int(v), jump) for k, v in enumerate(incs)]


def accept_writes():
    """The admitted boundary transitions. Returns (writes, n)."""
    w = [(0, "GATE", 1), (0, "GLIDE", SLOW)] + _set(0, _incs3(LO), True)
    f = 20
    # 1. the whole range in one glide, LO -> HI at 0.02 s/oct (~10.6 octaves)
    w += _set(f, _incs3(HI), False); f += 10400
    # 2. HI -> LO at the maximum rate, an octave per frame
    w += [(f, "GLIDE", FULL24)] + _set(f, _incs3(LO), False); f += 60
    # 3. back to HI by jump, then a semitone into HI at the reference rate
    semi = int(round(HI / 2 ** (1 / 12)))
    w += [(f, "GLIDE", REF)] + _set(f, _incs3(semi), True); f += 20
    w += _set(f, _incs3(HI), False); f += 700
    # 4. the glide = 1 floor at the top: d = (acc * 1) >> 24 ~ 66 LSB of Q24.8
    w += _set(f, _incs3(semi), True); f += 10
    w += [(f, "GLIDE", 1)] + _set(f, _incs3(HI), False); f += 300
    # 5. a retarget mid-glide: HI -> LO at the reference rate, back up at 200 frames
    w += [(f, "GLIDE", REF)] + _set(f, _incs3(HI), True); f += 10
    w += _set(f, _incs3(LO), False); f += 200
    w += _set(f, _incs3(HI), False); f += 1200
    # 6. the glide = 1 floor at the BOTTOM: (acc * 1) >> 24 == 0 below 65536,
    #    so only the max(1, .) floor moves it -- 1 LSB of Q24.8 per frame
    w += [(f, "GLIDE", REF)] + _set(f, _incs3(LO + 3000), True); f += 10
    w += [(f, "GLIDE", 1)] + _set(f, _incs3(LO), False); f += 800
    return w, f


def control_247_writes():
    w = [(0, "GATE", 1), (0, "GLIDE", REF)] + _set(0, [0xC00000, 0xC80000, 0xD00000], True)
    w += _set(40, [0xFF0000, 0xF80000, 0xF00000], False)
    return w, 400


def probe_writes(src, dst):
    w = [(0, "GATE", 1), (0, "GLIDE", REF)] + _set(0, src, True)
    w += _set(40, dst, False)
    return w, 600


def pattern_247(lo, hi, retargets=16, gap=40):
    """#247's stimulus shape (verify_deadline.py _extreme on
    verify/three-saw-deadline): jumps to `lo`, then glides alternating to
    `hi` and back every `gap` frames, at glide_s = 2.0 -- a slow glide that
    is retargeted long before it lands. The wrapper's event pacing spreads
    each retarget's three writes over ~126 frames; the shape is kept."""
    w = [(0, "GATE", 1)] + _set(0, lo, True)
    f = 40
    for i in range(retargets):
        w += _set(f, hi if i % 2 == 0 else lo, False)
        f += gap
    return w, f + 400


def _regs_247(waves):
    """_extreme's voice patch: noise, filter modulation, the mod mix and
    wheel up, oscillator modulation OFF (#247 reproduced identically with
    it on), glide 2.0 s/oct."""
    return vf.VoiceFx.patch_regs(waves=waves, detune=(0.0, 0.0, 0.0), mix=(1.0, 0.8, 0.7),
                                 noise=0.3, nsel=1, cutoff=(200, 12000), q=0.9, drive=1.6,
                                 amp=(0.002, 0.2, 0.8, 0.1), fenv=(0.001, 0.2, 0.5, 0.1),
                                 track=0.0, vol=0.45, glide_s=2.0, mod_mix=0.5, mod_wheel=1.0,
                                 mod_pitch=0.05, osc_mod=False, filt_mod=True, osc3_ctl=True)


LO247, HI247 = (0xC00000, 0xC80000, 0xD00000), (0xFF0000, 0xF80000, 0xF00000)
# the same shape scaled into the admitted range: the top endpoint at INC_HI,
# the same ratios as #247's endpoints
_S = HI / 0xFF0000
LO_IN = tuple(int(v * _S) for v in LO247)
HI_IN = tuple(int(v * _S) for v in HI247)
# and between the admitted range and 2^23: characterisation only
_T = 0x7FFFFF / 0xFF0000
LO_SUB = tuple(int(v * _T) for v in LO247)
HI_SUB = tuple(int(v * _T) for v in HI247)

CASES = {
    "accept-default":  (("saw", "saw", "square"), accept_writes, 0),
    "accept-pulse29":  (("pulse29",) * 3, accept_writes, 0),
    "accept-247-shape": (("saw", "saw", "saw"), lambda: pattern_247(LO_IN, HI_IN), 0),
    "probe-247-incs":  (("saw", "saw", "saw"), lambda: pattern_247(LO247, HI247), None),
    "probe-247-shape-sub-nyq": (("saw", "saw", "saw"), lambda: pattern_247(LO_SUB, HI_SUB), None),
    "probe-247-fast":  (("saw", "saw", "saw"), control_247_writes, None),
    "probe-sub-nyq":   (("saw", "saw", "saw"),
                        lambda: probe_writes([0x780000] * 3, [0x7FFFFF, 0x7FF000, 0x7FE000]), None),
    "probe-cross-nyq": (("saw", "saw", "saw"),
                        lambda: probe_writes([0x7C0000] * 3, [0x840000, 0x838000, 0x830000]), None),
    "probe-127-up12":  (("saw", "saw", "saw"),
                        lambda: probe_writes([HI] * 3, [2 * HI, 2 * HI - 1000, 2 * HI - 2000]), None),
}


def regs_for(case):
    waves = CASES[case][0]
    return _regs_247(waves) if "247" in case else _regs(waves)


def scenarios_for(case):
    waves, make, _expect = CASES[case]

    def scenarios(which, only=None):
        regs = regs_for(case)
        writes, n = make()
        assert all(0 <= w[0] < n for w in writes), case
        return [(case, f"{case}: {waves}", regs, writes, n)]
    return scenarios


# ---- the wrapper level: the shipped UART path ---------------------------------
def wrapper_capture(case, prefix):
    """Plan the case's writes through uart_host exactly as the CLI would and
    write the capture. Frame-0 writes are the live setup (preceded by the
    release's voice image); later writes are device-scheduled events, spaced
    at least one event packet apart (the wire's own pacing) in their order."""
    sys.path.insert(0, os.path.join(ROOT, "fpga"))
    import uart_host as uh
    import synth_top_model as stm
    waves, make, _ = CASES[case]
    regs = regs_for(case)
    writes, n = make()
    gap = 1          # dues 1 frame apart: preloaded events, the device fires 2 a frame

    def reg(w):
        f, op, *a = w
        if op == "INC":
            return (1 if a[2] else 0, 0, stm.A_INC + a[0], int(a[1]))
        if op == "GLIDE":
            return (0, 0, stm.A_GLIDE, int(a[0]))
        if op == "GATE":
            return (0, 0, stm.A_GATE_ON if a[0] else stm.A_GATE_OFF, 0)
        raise ValueError(op)
    # the case's whole voice image, in voice_image_writes' order, then the
    # registers it leaves out (weights, noise and modulation)
    cmds = [("write", 0, 0, stm.A_WAVE + k, vf.WAVE_CODE[s]) for k, s in enumerate(regs["waves"])]
    for base, key in ((stm.A_AMP, "amp"), (stm.A_FILT, "fenv")):
        cmds += [("write", 0, 0, base + j, int(v)) for j, v in enumerate(regs[key])]
    for addr, key in ((stm.A_CUT_LO, "cut_lo"), (stm.A_CUT_HI, "cut_hi"), (stm.A_K, "k"),
                      (stm.A_GAIN, "gain"), (stm.A_OGAIN, "ogain"), (stm.A_GLIDE, "glide"),
                      (stm.A_VOL, "vol"), (stm.A_NSEL, "nsel"), (stm.A_MROUTE, "mroute"),
                      (stm.A_MMIX, "mmix"), (stm.A_MWHEEL, "mwheel"), (stm.A_MPD, "mpd"),
                      (stm.A_MFD, "mfd")):
        cmds.append(("write", 0, 0, addr, int(regs[key])))
    cmds += [("write", 0, 0, stm.A_W + k, int(g)) for k, g in enumerate(regs["weights"])]
    cmds += [("write", *reg(w)) for w in writes if w[0] == 0]
    prev = None
    for w in sorted((w for w in writes if w[0] > 0), key=lambda w: w[0]):
        due = w[0] if prev is None else max(w[0], prev + gap)
        cmds.append(("event", due, *reg(w)))
        prev = due
    rows = uh.plan_show(cmds)
    verdict = uh.preflight(rows)
    if verdict["verdict"] != "FEASIBLE":
        raise SystemExit(f"glide_boundary: REFUSED -- {case} is not deliverable: {verdict['reason']}")
    uh.write_capture(prefix, rows, origin=0)
    return prefix


def run_wrapper(case, outdir, expect_fail):
    sys.path.insert(0, os.path.join(ROOT, "fpga"))
    import verify_uart_bridge as vub
    os.makedirs(outdir, exist_ok=True)
    prefix = wrapper_capture(case, os.path.join(outdir, "capture"))
    status = vub.main(["--replay", prefix, "--replay-name", case, "--outdir", outdir])
    if expect_fail:
        if status == 1:
            print(f"glide_boundary: control {case} CAUGHT (comparison failed as required)")
            return 0
        print(f"glide_boundary: CONTROL NOT CAUGHT (status {status})")
        return 1 if status == 0 else 2
    return status


def _regs(waves):
    return vf.VoiceFx.patch_regs(waves=waves, detune=(0.0, 0.0, 0.0), mix=(0.6, 0.5, 0.4),
                                 cutoff=(400, 12000), q=0.3, glide_s=vf.GLIDE_REF_S)


# ---- the record ------------------------------------------------------------------
# what each run must give for the release's claims to hold (probes: recorded)
EXPECT = {"voice:accept-default": "PASS", "voice:accept-pulse29": "PASS",
          "voice:accept-247-shape": "PASS", "voice:accept-default+GLIDE_FLOOR": "CAUGHT",
          "wrapper:accept-default": "PASS", "wrapper:accept-pulse29": "PASS",
          "wrapper:accept-247-shape": "PASS",
          "wrapper:probe-247-incs": "RECORD", "wrapper:probe-247-shape-sub-nyq": "RECORD",
          "wrapper:probe-247-fast": "RECORD", "wrapper:probe-sub-nyq": "RECORD",
          "wrapper:probe-cross-nyq": "RECORD", "wrapper:probe-127-up12": "RECORD"}


def _key(cmd: str) -> str:
    a = cmd.split()
    level = a[a.index("--level") + 1] if "--level" in a else "voice"
    key = f"{level}:{a[a.index('--case') + 1]}"
    return key + (f"+{a[a.index('--inject') + 1]}" if "--inject" in a else "")


def summarize(run_json: str, out_dir: str) -> int:
    """Turn a tools/run_all.py record of the batch into the committed
    evidence: every run's exit status and verdict lines, whether it met its
    expectation, and each wrapper run's identity (sources, ROMs, stimulus)."""
    import json
    import shutil
    runs = json.load(open(run_json))
    os.makedirs(out_dir, exist_ok=True)
    verdicts, idents, missing = {}, {}, set(EXPECT)
    for r in runs:
        k = _key(r["cmd"])
        missing.discard(k)
        exp = EXPECT[k]
        lines = [l for l in r["out"].splitlines()
                 if any(t in l for t in ("PASS", "FAIL", "CAUGHT", "mismatch", "busy", "did not run"))]
        caught = r["rc"] == 0 and any("CAUGHT" in l and "NOT CAUGHT" not in l for l in lines)
        met = {"PASS": r["rc"] == 0 and not caught, "CAUGHT": caught, "RECORD": r["rc"] in (0, 1)}[exp]
        verdicts[k] = {"rc": r["rc"], "state": r["state"], "expected": exp, "met": met,
                       "lines": lines[-6:]}
        if k.startswith("wrapper:"):
            case = k.split(":", 1)[1]
            d = os.path.join(ROOT, "build", "release-glide", f"wrapper-{case}", case)
            ident = json.load(open(os.path.join(d, "run_identity.json")))
            idents[case] = ident["identity"]["sources"]
            dst = os.path.join(out_dir, case)
            os.makedirs(dst, exist_ok=True)
            for f in ("run_identity.json", "verification.json", "transcript.txt"):
                shutil.copy(os.path.join(d, f), dst)
            for f in ("capture.cmds", "capture.plan.json"):
                shutil.copy(os.path.join(os.path.dirname(d), f), dst)
    rec = {"tool": "fpga/release/glide_boundary.py", "inc_lo": LO, "inc_hi": HI,
           "nyquist_inc": 1 << 23, "missing_runs": sorted(missing),
           "all_expectations_met": not missing and all(v["met"] for v in verdicts.values()),
           "verdicts": verdicts, "wrapper_identities": idents}
    with open(os.path.join(out_dir, "summary.json"), "w") as fh:
        json.dump(rec, fh, indent=1)
        fh.write("\n")
    for k, v in verdicts.items():
        print(f"  {k:36s} rc {v['rc']}  expected {v['expected']:6s} met {v['met']}")
    print(f"glide_boundary: all expectations met: {rec['all_expectations_met']}"
          + (f"; MISSING {sorted(missing)}" if missing else ""))
    return 0 if rec["all_expectations_met"] else 1


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--case", choices=sorted(CASES))
    ap.add_argument("--summarize", metavar="RUN_ALL_JSON", default=None,
                    help="write evidence/glide-boundary/ from a tools/run_all.py record")
    ap.add_argument("--outdir", default=None)
    ap.add_argument("--expect-fail", action="store_true")
    ap.add_argument("--inject", default=None, help="voice level: passed through to verify_voice")
    ap.add_argument("--level", choices=("voice", "wrapper"), default="voice")
    a = ap.parse_args(argv)
    if a.summarize:
        return summarize(a.summarize, os.path.join(HERE, "evidence", "glide-boundary"))
    if not a.case:
        ap.error("--case is required")
    outdir = a.outdir or os.path.join(ROOT, "build", "release-glide", f"{a.level}-{a.case}")
    if a.level == "wrapper":
        if a.inject:
            ap.error("--inject is a voice-level option")
        print(f"glide_boundary: wrapper case {a.case}; INC_LO {LO}, INC_HI {HI}")
        return run_wrapper(a.case, os.path.abspath(outdir), a.expect_fail)
    vv.scenarios = scenarios_for(a.case)
    args = ["--osc2x", "--filter2x", "--outdir", outdir]
    if a.expect_fail:
        args.append("--expect-fail")
    if a.inject:
        args += ["--inject", a.inject]
    print(f"glide_boundary: case {a.case}; INC_LO {LO}, INC_HI {HI} (0x{HI:06X}); "
          f"2^23 = {1 << 23}")
    return vv.main(args)


if __name__ == "__main__":
    sys.exit(main())
