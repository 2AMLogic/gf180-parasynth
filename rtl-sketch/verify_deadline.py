#!/usr/bin/env python3
"""The PRODUCTION frame deadline, measured per frame through the chip's pins
(plan075 T1; the report is docs/deadline/README.md).

    .venv/bin/python rtl-sketch/verify_deadline.py --scenario threesaw-f1cal
    .venv/bin/python rtl-sketch/verify_deadline.py --scenario stress-saw
    .venv/bin/python rtl-sketch/verify_deadline.py --scenario stress-pulse --pulse2x
    .venv/bin/python rtl-sketch/verify_deadline.py --scenario arty-uart
    .venv/bin/python rtl-sketch/verify_deadline.py --scenario threesaw-f1cal \\
        --mutant late:160 --expect-fail                          # the overrun control

WHY THIS EXISTS. The component bench (tb_voice.v) used to launch the voice at
cycle 48 of the 256-cycle frame; synth_top launches at cycle 8. PR #235 found
three 2x saws plus the 2x filter "still busy at the end of frame 0" on the
component bench, which says nothing about the chip in either direction. This
driver runs a configuration through synth_top -- the SPI pin bench
(tb_top_bx.v) or the Arty wrapper's UART bench (tb_uart_bx.v) -- with
deadline_mon.vh attached as an extra simulation root, and records for EVERY
frame: go, voice start and last busy cycle, the sample strobe, drum start and
last busy cycle, the register writes and where they landed. Nothing in either
bench is edited; the monitor reads the synth_top instance hierarchically.

DEADLINES, from the RTL (deadline_mon.vh says why):
  sample slack = 254 - strobe cycle    (i2s_tx loads the next period at 255)
  busy slack   = 255 - last busy cycle (synth_top's overrun is busy at the tick)
A frame MISSES if it has no strobe, a negative slack, or busy at its tick.

VERDICT, from checked facts only:
  PASS (0)   every frame met both deadlines, zero missed frames, sticky
             `overrun` and link `overflow` both 0, every write landed before
             `go` of its frame and never while a datapath was busy, and every
             decoded I2S period equals model/synth_top_model.py's
  FAIL (1)   any of those failed -- a RESULT
  REFUSED (2) the apparatus could not establish the result: the simulation did
             not run, the monitor file is missing or short, the scenario did
             not reach what it claims to cover (e.g. three active 2x saws with
             the gate on), or `go` was not at synth_top's GO_CYCLE

  --expect-fail  exit 0 only if the verdict is FAIL *for a deadline reason*
                 (a miss / overrun), never for an I2S mismatch alone
  --json FILE    the full per-configuration record (slack histogram, worst
                 frames with their cycle attribution, provenance)
"""
from __future__ import annotations

import argparse
import contextlib
import hashlib
import io
import json
import os
import re
import sys
from collections import Counter

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
for _p in (HERE, os.path.join(ROOT, "model"), os.path.join(ROOT, "audition"),
           os.path.join(ROOT, "fpga")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import numpy as np                                  # noqa: E402
import voice_fx as vf                               # noqa: E402
import drums_fx as dx                               # noqa: E402
import synth_top_model as stm                       # noqa: E402
import verify_synth_top as vst                      # noqa: E402

SEC_V, SEC_D = 0, 1
MON_TOP = os.path.join(HERE, "deadline_mon_top.v")
MON_ARTY = os.path.join(HERE, "deadline_mon_arty.v")
SAMPLE_DEADLINE, BUSY_DEADLINE = 254, 255
TWO_X_SHAPES = {"saw"}                               # always through the 2x bank with OSC2X
RECT_SHAPES = {"square", "pulse25", "pulse29", "pulse15"}  # through it only with PULSE2X
#: surge-type2-clean-v1's words as PR #235 recorded them: res -> (gain, ogain).
#: The scenario takes them from model/voice_fx.py's calibration and asserts
#: they are these, so a changed calibration cannot silently change the case.
F1CAL = "surge-type2-clean-v1"
F1CAL_WORDS = {0.0: (42598, 100825), 1.0: (42598, 302474), 1.1: (42598, 322639)}


# ---- build-time mutants of voice_dp.v (never committed RTL) ------------------------
# voice_dp.v is bound by hash to the published Arty image (fpga/publish_arty.py);
# a control compiled into it by `ifdef would unbind that image. So each mutant
# is generated from the CURRENT voice_dp.v at run time, from anchors that must
# each occur exactly once, and REFUSES if one does not.
MUTANTS = {
    # late completion: the master mix held N extra cycles before the sample, so
    # the strobe and busy both finish N cycles late with every value unchanged
    # (the stall sits in S_OUT2, after every wait: a stall placed in S_DWAIT
    # overlapped the drum filter's own wait there and was absorbed -- a 15-cycle
    # "late" mutant moved no DFILT frame at all, which the boundary control caught)
    "late": [("    reg signed [18:0] d19;",
              "    reg [8:0] late_cnt;                // MUTANT late: the stall counter\n"),
             ("                S_IDLE: if (go) begin\n",
              "                    late_cnt <= 9'd0;                                  // MUTANT late\n"),
             ("                S_OUT2: begin\n", None)],
    # the CANDIDATE correction (docs/deadline/README.md): an oscillator whose
    # output comes from the 2x bank skips the scalar PolyBLEP window loop,
    # whose c_pp/c_ps only feed the scalar path it does not use
    "skip2xwin": [("                    if (!blep) state <= S_MIX;",
                   None)],
}


def make_mutant(spec: str, outdir: str) -> str:
    kind, _, arg = spec.partition(":")
    if kind not in MUTANTS:
        raise SystemExit(f"verify_deadline: REFUSED -- unknown mutant {kind!r}")
    src = open(os.path.join(HERE, "voice_dp.v")).read()
    for anchor, insert in MUTANTS[kind]:
        if src.count(anchor) != 1:
            raise SystemExit(f"verify_deadline: REFUSED -- mutant {kind} anchor occurs "
                             f"{src.count(anchor)} times in voice_dp.v: {anchor.strip()!r}")
        if kind == "late" and anchor.strip() == "S_OUT2: begin":
            src = src.replace(anchor, f"                S_OUT2: if (late_cnt != 9'd{int(arg)}) "
                                      f"late_cnt <= late_cnt + 9'd1; else begin   // MUTANT late\n")
        elif kind == "skip2xwin":
            src = src.replace(anchor, "                    if (!blep || (use_osc2x && shape_osc2x)) "
                                      "state <= S_MIX;   // MUTANT skip2xwin")
        elif anchor.endswith("\n"):
            src = src.replace(anchor, anchor + insert.replace("{N}", str(int(arg))))
        else:
            i = src.index(anchor)
            src = src[:i] + insert + src[i:]
    d = os.path.join(outdir, f"mutant-{kind}{arg}")
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "voice_dp.v"), "w") as fh:
        fh.write(src)
    return d


def chip_go_cycle() -> int:
    m = re.search(r"parameter\s+GO_CYCLE\s*=\s*(\d+)", open(os.path.join(HERE, "synth_top.v")).read())
    if not m:
        raise RuntimeError("synth_top.v GO_CYCLE not found")
    return int(m.group(1))


# ---- stimulus ------------------------------------------------------------------
def image_writes(regs: dict, *, dvol=0, bvol=0, route=0, dcut=1800):
    """EVERY voice-page register of the patch, as (flag, sec, addr, data): the
    legal preload. Nothing is left at its reset value by omission."""
    w = [(0, SEC_V, stm.A_WAVE + k, vf.WAVE_CODE[s]) for k, s in enumerate(regs["waves"])]
    w += [(0, SEC_V, stm.A_W + k, int(regs["weights"][k])) for k in range(3)]
    w.append((0, SEC_V, stm.A_WN, int(regs["weights"][3])))
    for base, key in ((stm.A_AMP, "amp"), (stm.A_FILT, "fenv")):
        w += [(0, SEC_V, base + j, int(v)) for j, v in enumerate(regs[key])]
    for addr, key in ((stm.A_CUT_LO, "cut_lo"), (stm.A_CUT_HI, "cut_hi"), (stm.A_K, "k"),
                      (stm.A_GAIN, "gain"), (stm.A_OGAIN, "ogain"), (stm.A_GLIDE, "glide"),
                      (stm.A_VOL, "vol"), (stm.A_NSEL, "nsel"), (stm.A_MROUTE, "mroute"),
                      (stm.A_MMIX, "mmix"), (stm.A_MWHEEL, "mwheel"), (stm.A_MPD, "mpd"),
                      (stm.A_MFD, "mfd")):
        w.append((0, SEC_V, addr, int(regs[key])))
    w += [(0, SEC_V, stm.A_DVOL, dvol), (0, SEC_V, stm.A_BVOL, bvol),
          (0, SEC_V, stm.A_ROUTE, route), (0, SEC_V, stm.A_DCUT, dcut),
          (0, SEC_V, stm.A_DK, int(regs["k"])), (0, SEC_V, stm.A_DGAIN, int(regs["gain"])),
          (0, SEC_V, stm.A_DOGAIN, int(regs["ogain"]))]
    return w


def drum_image_writes():
    w = [(0, SEC_D, a, v) for a, v in dx.kit_808()]
    w += [(0, SEC_D, dx.A_ACCENT + st, dx.accent_reg(1.0)) for st in range(dx.N_STOPS)]
    return w


def note_writes(note, regs, *, jump, gate):
    """INC x 3 (flag = jump), TRACK, then GATE_ON / TRIG / nothing."""
    w = [(1 if jump else 0, SEC_V, stm.A_INC + k, v)
         for k, v in enumerate(vf.VoiceFx.note_incs(note, regs["detune"]))]
    w.append((0, SEC_V, stm.A_TRACK, vf.VoiceFx.note_track(note, regs["track"])))
    if gate == "on":
        w.append((0, SEC_V, stm.A_GATE_ON, 0))
    elif gate == "trig":
        w.append((0, SEC_V, stm.A_TRIG, 0))
    return w


class Script:
    """(wait, flag, sec, addr, data) in send order, tb_top_bx's +cmd format."""
    def __init__(self):
        self.cmds = []

    def put(self, wait, writes):
        for i, (fl, sec, a, d) in enumerate(writes):
            self.cmds.append((wait if i == 0 else 0, fl, sec, a, d))


def sc_threesaw_f1cal(short=False):
    """PR #235's refused configuration, on the production schedule: three 2x
    SAW oscillators (mix 1/0/0 as in that scenario), the 2x filter, the
    calibrated words at the five F1 points. Preloaded completely before the
    note; the F1 points are control writes during playback."""
    seg = 300 if short else 720
    pts = ((250, 0.0), (1000, 0.0), (4000, 0.0), (1000, 1.0), (1000, 1.1))
    s = Script()
    regs = None
    for i, (cut, res) in enumerate(pts):
        r = vf.VoiceFx.patch_regs(waves=("saw", "saw", "saw"), detune=(0.0, 0.0, 0.0),
                                  mix=(1.0, 0.0, 0.0), cutoff=(cut, cut), q=res, drive=1.0,
                                  track=0.0, amp=(0.001, 0.25, 1.0, 0.05),
                                  filter_calibration=F1CAL)
        assert (r["gain"], r["ogain"]) == F1CAL_WORDS[res], (res, r["gain"], r["ogain"])
        if i == 0:
            regs = r
            s.put(0, image_writes(r))                     # the legal preload, gate still off
            s.put(4, note_writes(45, r, jump=True, gate="on"))
        else:
            s.put(seg, [(0, SEC_V, stm.A_CUT_LO, r["cut_lo"]), (0, SEC_V, stm.A_CUT_HI, r["cut_hi"]),
                        (0, SEC_V, stm.A_K, r["k"]), (0, SEC_V, stm.A_GAIN, r["gain"]),
                        (0, SEC_V, stm.A_OGAIN, r["ogain"])])
    return s.cmds, seg, dict(regs=regs, claim="three_2x_saws", drums=False)


def _stress(waves, short, *, alt_waves=None):
    """Everything that makes a frame long, together: three audible oscillators
    in the 2x bank at high pitch (PolyBLEP windows open often), glide AND
    oscillator modulation so every increment changes every frame (a reciprocal
    division per oscillator per frame), filter modulation, noise, the drum kit
    struck repeatedly under the note, the drum filter engaged, and control
    writes (cutoff, resonance, volume, wheel, waveform) during playback."""
    S = 0.4 if short else 1.0
    regs = vf.VoiceFx.patch_regs(waves=waves, detune=(0.0, 0.1, 7.0), mix=(1.0, 0.8, 0.7),
                                 noise=0.3, nsel=1, cutoff=(200, 12000), q=0.9, drive=1.6,
                                 amp=(0.002, 0.2, 0.8, 0.1), fenv=(0.001, 0.2, 0.5, 0.1),
                                 track=0.5, vol=0.45, glide_s=0.2, mod_mix=0.5, mod_wheel=1.0,
                                 osc_mod=True, filt_mod=True, osc3_ctl=True)
    s = Script()
    s.put(0, image_writes(regs, dvol=dx.accent_reg(0.3), bvol=dx.accent_reg(0.3)))
    s.put(0, drum_image_writes())
    all_stops = (1 << dx.N_STOPS) - 1
    gap = max(8, int(24 * S))

    def strike(wait, mask=all_stops):
        s.put(wait, [(0, SEC_D, dx.A_STOPS, mask)])
        s.put(2, [(0, SEC_D, dx.A_STOPS, 0)])

    s.put(4, note_writes(84, regs, jump=True, gate="on"))
    strike(2)
    s.put(gap, note_writes(96, regs, jump=False, gate=None))          # legato, glides up
    strike(gap)
    s.put(gap, [(0, SEC_V, stm.A_CUT_HI, 6000), (0, SEC_V, stm.A_K, 60000)])
    strike(gap, (1 << dx.BD) | (1 << dx.SD) | (1 << dx.CH))
    s.put(gap, note_writes(72, regs, jump=False, gate="trig"))        # retrigger, glides down
    strike(gap)
    s.put(gap, [(0, SEC_V, stm.A_ROUTE, 1), (0, SEC_V, stm.A_DCUT, 600)])   # the drum filter on
    strike(gap)
    s.put(gap, [(0, SEC_V, stm.A_MWHEEL, 20000), (0, SEC_V, stm.A_VOL, 20000)])
    strike(gap)
    if alt_waves:                                                    # waveform switched mid-note
        s.put(gap, [(0, SEC_V, stm.A_WAVE + k, vf.WAVE_CODE[w]) for k, w in enumerate(alt_waves)])
        strike(gap)
    s.put(gap, note_writes(108, regs, jump=False, gate=None))
    strike(gap)
    s.put(gap, [(0, SEC_V, stm.A_GATE_OFF, 0)])
    strike(gap)
    s.put(gap, note_writes(100, regs, jump=True, gate="on"))         # a jump, all three at once
    strike(2)
    if alt_waves:
        s.put(gap, [(0, SEC_V, stm.A_WAVE + k, vf.WAVE_CODE[w]) for k, w in enumerate(waves)])
    for _ in range(int(6 * S) + 2):
        strike(gap)
    s.put(gap, [(0, SEC_V, stm.A_ROUTE, 0), (0, SEC_V, stm.A_K, regs["k"])])
    strike(gap)
    # then the keyboard: legato runs across the whole MIDI range (glides between
    # far notes keep every increment moving), the drum filter toggled, the
    # cutoff swept -- more frames, more PolyBLEP-window phases, more overlaps
    rounds = (range(24, 128, 13), range(127, 20, -17), range(36, 128, 7)) if not short \
        else (range(60, 128, 17),)
    for ri, notes in enumerate(rounds):
        s.put(gap, [(0, SEC_V, stm.A_ROUTE, (ri + 1) & 1), (0, SEC_V, stm.A_CUT_HI, 3000 + 4000 * ri)])
        for j, nt in enumerate(notes):
            s.put(gap // 2, note_writes(nt, regs, jump=False, gate="trig" if j % 3 == 0 else None))
            strike(2, all_stops if j % 2 == 0 else (1 << dx.BD) | (1 << dx.OH))
    return s.cmds, int(200 * S) + 60, dict(regs=regs, claim="three_2x_audible", drums=True)


def _extreme(waves, short, osc_mod=False):
    """The REGISTER-LEGAL worst case, beyond the musical range: every
    increment >= 2^23 (half the phase circle), so both PolyBLEP windows of
    every edge open in almost every frame; a slow glide between two such
    values plus oscillator modulation, so all three reciprocals are recomputed
    every frame; noise, filter modulation, the drum filter engaged and the kit
    struck under it. Reached by INC writes (the register accepts any 24-bit
    increment; note 127 is 0x42xxxx), not by a note."""
    S = 0.4 if short else 1.0
    regs = vf.VoiceFx.patch_regs(waves=waves, detune=(0.0, 0.0, 0.0), mix=(1.0, 0.8, 0.7),
                                 noise=0.3, nsel=1, cutoff=(200, 12000), q=0.9, drive=1.6,
                                 amp=(0.002, 0.2, 0.8, 0.1), fenv=(0.001, 0.2, 0.5, 0.1),
                                 track=0.0, vol=0.45, glide_s=2.0, mod_mix=0.5, mod_wheel=1.0,
                                 mod_pitch=0.05, osc_mod=osc_mod, filt_mod=True, osc3_ctl=True)
    s = Script()
    s.put(0, image_writes(regs, dvol=dx.accent_reg(0.3), bvol=dx.accent_reg(0.3), route=1, dcut=600))
    s.put(0, drum_image_writes())
    all_stops = (1 << dx.N_STOPS) - 1
    lo, hi = (0xC00000, 0xC80000, 0xD00000), (0xFF0000, 0xF80000, 0xF00000)
    s.put(4, [(1, SEC_V, stm.A_INC + k, lo[k]) for k in range(3)] + [(0, SEC_V, stm.A_GATE_ON, 0)])
    gap = max(16, int(40 * S))
    for i in range(int(30 * S) + 2):
        tgt = hi if i % 2 == 0 else lo
        s.put(gap, [(0, SEC_V, stm.A_INC + k, tgt[k]) for k in range(3)])     # glide, flag 0
        s.put(2, [(0, SEC_D, dx.A_STOPS, all_stops)])
        s.put(2, [(0, SEC_D, dx.A_STOPS, 0)])
    return s.cmds, int(200 * S) + 60, dict(regs=regs, claim="three_2x_audible", drums=True)


def sc_extreme_saw(short=False):
    return _extreme(("saw", "saw", "saw"), short)


def sc_extreme_pulse(short=False):
    return _extreme(("square", "pulse29", "pulse15"), short)


def sc_extreme_saw_mod(short=False):
    """extreme-saw with oscillator modulation on: +1 cycle per oscillator
    (S_IM1). Also the reproduction of a MODEL/RTL divergence found here, not a
    deadline question: see docs/deadline/README.md."""
    return _extreme(("saw", "saw", "saw"), short, osc_mod=True)


def sc_extreme_pulse_mod(short=False):
    return _extreme(("square", "pulse29", "pulse15"), short, osc_mod=True)


def sc_stress_saw(short=False):
    return _stress(("saw", "saw", "saw"), short, alt_waves=("saw", "pulse29", "saw"))


def sc_stress_pulse(short=False):
    return _stress(("pulse29", "square", "pulse15"), short, alt_waves=("saw", "saw", "saw"))


SPI_SCENARIOS = {"threesaw-f1cal": sc_threesaw_f1cal, "stress-saw": sc_stress_saw,
                 "stress-pulse": sc_stress_pulse, "extreme-saw": sc_extreme_saw,
                 "extreme-pulse": sc_extreme_pulse, "extreme-saw-mod": sc_extreme_saw_mod,
                 "extreme-pulse-mod": sc_extreme_pulse_mod}


# ---- coverage: what the landed writes say the chip was doing, frame by frame --------
def image_timeline(model_writes, n):
    """Per frame, from the writes AT THE FRAMES THEY LANDED: waves, weights, gate,
    route, and whether a drum strike fired. Used to REFUSE a run that does not
    reach the configuration it claims."""
    waves = [0, 0, 0]; w = [0, 0, 0]; gate = 0; route = 0
    by_f = {}
    for f, fl, sec, a, d in model_writes:
        by_f.setdefault(f, []).append((fl, sec, a, d))
    rows = []
    for f in range(n):
        struck = 0
        for fl, sec, a, d in by_f.get(f, []):
            if sec == SEC_V:
                if stm.A_WAVE <= a < stm.A_WAVE + 3: waves[a - stm.A_WAVE] = d
                elif stm.A_W <= a < stm.A_W + 3: w[a - stm.A_W] = d
                elif a == stm.A_GATE_ON: gate = 1
                elif a == stm.A_GATE_OFF: gate = 0
                elif a == stm.A_ROUTE: route = d & 1
                elif a == stm.A_RESET: waves = [0, 0, 0]; w = [0, 0, 0]; gate = 0; route = 0
            elif a == dx.A_STOPS and d:
                struck = 1
        rows.append((tuple(waves), tuple(w), gate, route, struck))
    return rows


def coverage(tl, pulse2x):
    code2x = {vf.WAVE_CODE[s] for s in TWO_X_SHAPES}
    if pulse2x:
        code2x |= {vf.WAVE_CODE[s] for s in RECT_SHAPES}
    saw = vf.WAVE_CODE["saw"]
    c = Counter()
    for waves, w, gate, route, struck in tl:
        all2x = all(x in code2x for x in waves)
        if all(x == saw for x in waves) and gate:
            c["three_saws_gate_on"] += 1
        if all2x and gate:
            c["three_2x_gate_on"] += 1
            if all(x > 0 for x in w):
                c["three_2x_audible_gate_on"] += 1
                if struck:
                    c["three_2x_audible_with_strike"] += 1
                if route:
                    c["three_2x_audible_dfilt"] += 1
        if pulse2x and gate and sum(x in {vf.WAVE_CODE[s] for s in RECT_SHAPES} for x in waves) == 3:
            c["three_rect_2x_gate_on"] += 1
    return dict(c)


# ---- the schedule rows ----------------------------------------------------------
FIELDS = ("frame", "go", "v_start", "v_last", "strobe", "d_start", "d_last",
          "wr_n", "wr_first", "wr_last", "wr_in_compute", "busy_at_tick", "rwait", "oscwait", "dwait",
          "win", "w1", "im1", "sk", "ywait")


def read_sched(path):
    rows = []
    with open(path) as fh:
        for ln in fh:
            p = ln.split()
            if len(p) == len(FIELDS) + 1 and p[0] == "F":
                rows.append(dict(zip(FIELDS, (int(x) for x in p[1:]))))
    return rows


def cost_model(use):
    """The voice's completion as a sum of its data-dependent parts. If
    strobe - (rwait + oscwait + dwait + ywait + 2*w1 + win + im1 + 3*sk) is the
    SAME constant in every frame, the schedule is fully explained by those
    counts and its worst case is that constant plus each part's maximum --
    which is how a worst case is bounded rather than sampled."""
    if not use or any(r["strobe"] < 0 for r in use):
        return None
    base = Counter(r["strobe"] - (r["rwait"] + r["oscwait"] + r["dwait"] + r["ywait"]
                                  + 2 * r["w1"] + r["win"] + r["im1"] + 3 * r["sk"]) for r in use)
    return dict(base_values=dict(base.most_common(6)), explained=len(base) == 1)


def analyse_sched(rows, go_cycle, skip=2):
    """Slack per frame and the verdict facts. The first `skip` frames straddle
    reset (the voice has had no `go` yet in frame 0's first cycles)."""
    use = [r for r in rows if r["frame"] >= skip]
    miss, bad_go, wr_bad, late_wr = [], 0, 0, 0
    ss, bs, ds = [], [], []
    for r in use:
        if r["go"] != go_cycle:
            bad_go += 1
        s_sl = SAMPLE_DEADLINE - r["strobe"] if r["strobe"] >= 0 else None
        b_sl = BUSY_DEADLINE - r["v_last"] if r["v_last"] >= 0 else None
        d_sl = BUSY_DEADLINE - r["d_last"] if r["d_last"] >= 0 else None
        r["sample_slack"], r["busy_slack"], r["drum_slack"] = s_sl, b_sl, d_sl
        if s_sl is None or s_sl < 0 or r["busy_at_tick"] or (b_sl is not None and b_sl < 0):
            miss.append(r["frame"])
        if s_sl is not None: ss.append(s_sl)
        if b_sl is not None: bs.append(b_sl)
        if d_sl is not None: ds.append(d_sl)
        wr_bad += r["wr_in_compute"]
        if r["wr_n"] and r["wr_last"] >= go_cycle:
            late_wr += 1
    worst = sorted((r for r in use if r["sample_slack"] is not None),
                   key=lambda r: r["sample_slack"])[:5]
    hist = Counter((x // 8) * 8 for x in ss)
    return dict(frames=len(use), missed=len(miss), missed_frames=miss[:20], bad_go=bad_go,
                writes_in_compute=wr_bad, frames_with_write_after_go=late_wr,
                worst_sample_slack=min(ss) if ss else None,
                worst_busy_slack=min(bs) if bs else None,
                worst_drum_slack=min(ds) if ds else None,
                strobe_range=[min(r["strobe"] for r in use), max(r["strobe"] for r in use)] if use else None,
                voice_start=sorted({r["v_start"] for r in use}),
                drum_start=sorted({r["d_start"] for r in use}),
                max_rwait=max((r["rwait"] for r in use), default=0),
                max_oscwait=max((r["oscwait"] for r in use), default=0),
                max_dwait=max((r["dwait"] for r in use), default=0),
                max_active_windows=max((r["w1"] for r in use), default=0),
                cost_model=cost_model(use),
                write_cycles=[min((r["wr_first"] for r in use if r["wr_n"]), default=-1),
                              max((r["wr_last"] for r in use if r["wr_n"]), default=-1)],
                max_writes_per_frame=max((r["wr_n"] for r in use), default=0),
                slack_histogram_8=dict(sorted(hist.items())),
                worst_frames=worst)


# ---- the SPI path: tb_top_bx + monitor -------------------------------------------
@contextlib.contextmanager
def monitor_attached(mod, path, rtl_dir=None):
    """Append the monitor root to the source list a bench driver resolves (and,
    for a mutant, take voice_dp.v from `rtl_dir`). Restored on exit; the driver
    itself is not edited."""
    orig = mod.resolve_sources

    def with_monitor(_rtl_dir=None):
        return list(orig(rtl_dir or _rtl_dir)) + [(os.path.basename(path), path)]
    mod.resolve_sources = with_monitor
    try:
        yield
    finally:
        mod.resolve_sources = orig


def run_spi(scenario, *, osc2x, filter2x, pulse2x, inject, outdir, short, rtl_dir=None):
    cmds, tail, meta = SPI_SCENARIOS[scenario](short)
    os.makedirs(outdir, exist_ok=True)
    vst.write_cmds(os.path.join(outdir, "top_bx_cmds.txt"), cmds)
    defines = (["VOICE_OSC_2X"] if osc2x else []) + (["VOICE_FILTER_2X"] if filter2x else []) \
        + (["VOICE_PULSE_2X"] if pulse2x else [])
    if inject:
        defines.append(f"INJECT_BUG_{inject}")
    print(f"verify_deadline: {scenario}: {len(cmds)} writes over the SPI pins "
          f"({sum(1 for c in cmds if c[2] == SEC_V)} voice, {sum(1 for c in cmds if c[2] == SEC_D)} drum), "
          f"{tail} frames after the last; defines {', '.join(defines) or '(none)'}")
    with monitor_attached(vst, MON_TOP, rtl_dir):
        out = vst.simulate(defines, outdir, tail, rtl_dir=rtl_dir)
    if out is None:
        return None
    rep = "\n".join(out["report"])
    res = dict(bench="tb_top_bx (SPI pins)", report=out["report"], defines=defines,
               sources=out["sources"], sched=out["wrs"] + ".sched", meta=meta, cmds=len(cmds))
    mb, ms = vst.RE_BUSY.search(rep), vst.RE_STRB.search(rep)
    if not mb or not ms:
        res["refused"] = "the bench did not report its frame budget"
        return res
    res["busy_at_tick"], res["overrun"], res["overflow"] = (int(x) for x in mb.groups())
    res["strobed"], res["frames_no_sample"], res["worst_strobe_cycle"] = (int(x) for x in ms.groups())
    wr = vst.rows(out["wrs"])
    bad = sum(1 for want, got in zip(cmds, wr)
              if (int(got[1]), int(got[2]), int(got[3]), int(got[4])) != (want[1], want[2], want[3], want[4] & 0xFFFFFFFF))
    pred_bad = sum(1 for g in wr if len(g) <= 5 or int(g[5]) < 0 or int(g[5]) != int(g[0]))
    res.update(writes_sent=len(cmds), writes_seen=len(wr), writes_bad=bad, frame_pred_bad=pred_bad)
    model_writes = [(int(g[5]), int(g[1]), int(g[2]), int(g[3]), int(g[4])) for g in wr]
    n = max(f for f, *_ in model_writes) + tail + 1
    m = stm.SynthTopModel(oversample_2x=osc2x, filter_2x=filter2x, pulse_2x=pulse2x).run(model_writes, n)
    i2s = vst.rows(out["i2s"])
    mism = swap = width = 0
    first = None
    for r in i2s:
        p, left, right, nbl, nbr = (int(x) for x in r[:5])
        if p >= n:
            break
        if nbl != 32 or nbr != 32: width += 1
        if left != int(m["i2s"][p]):
            mism += 1
            if first is None: first = (p, int(m["i2s"][p]), left)
        if right != left: swap += 1
    res.update(periods=min(len(i2s), n), wire_mismatch=mism, swap=swap, width=width,
               first_mismatch=first, model_peak=int(np.abs(m["sample"]).max()),
               model_writes=model_writes, n_model=n)
    res["coverage"] = coverage(image_timeline(model_writes, n), pulse2x)
    return res


# ---- the Arty wrapper path: tb_uart_bx + monitor ------------------------------------
def arty_items():
    """UART-only control of the Arty wrapper: the stress patch as a live boot
    image, then due-scheduled note changes, drum strikes and control writes
    fired BY THE DEVICE during playback, plus live writes interleaved."""
    regs = vf.VoiceFx.patch_regs(waves=("saw", "saw", "saw"), detune=(0.0, 0.1, 7.0),
                                 mix=(1.0, 0.8, 0.7), noise=0.3, nsel=1, cutoff=(200, 12000),
                                 q=0.9, drive=1.6, amp=(0.002, 0.2, 0.8, 0.1),
                                 fenv=(0.001, 0.2, 0.5, 0.1), track=0.5, vol=0.45, glide_s=0.2,
                                 mod_mix=0.5, mod_wheel=1.0, osc_mod=True, filt_mod=True,
                                 osc3_ctl=True)
    items = [("write", *w) for w in image_writes(regs, dvol=dx.accent_reg(0.3), bvol=dx.accent_reg(0.3))]
    ev = []
    f = 0
    def at(df, writes):
        nonlocal f
        f += df
        for i, w in enumerate(writes):
            ev.append(("event", f + i, *w))      # one per frame: two UART slots, dues ordered
        f += len(writes)
    at(0, note_writes(84, regs, jump=True, gate="on"))
    at(3, [(0, SEC_D, dx.A_STOPS, (1 << dx.N_STOPS) - 1), (0, SEC_D, dx.A_STOPS, 0)])
    at(40, note_writes(96, regs, jump=False, gate=None))
    at(40, [(0, SEC_V, stm.A_ROUTE, 1), (0, SEC_V, stm.A_DCUT, 600), (0, SEC_V, stm.A_K, 60000)])
    at(20, [(0, SEC_D, dx.A_STOPS, (1 << dx.N_STOPS) - 1), (0, SEC_D, dx.A_STOPS, 0)])
    at(40, note_writes(72, regs, jump=False, gate="trig"))
    at(40, [(0, SEC_V, stm.A_WAVE + 1, vf.WAVE_CODE["pulse29"]), (0, SEC_V, stm.A_MWHEEL, 20000)])
    at(40, note_writes(108, regs, jump=True, gate=None))
    at(40, [(0, SEC_V, stm.A_WAVE + 1, vf.WAVE_CODE["saw"]), (0, SEC_V, stm.A_GATE_OFF, 0)])
    at(40, note_writes(100, regs, jump=True, gate="on"))
    items += ev
    items += [("write", 0, SEC_V, stm.A_CUT_HI, 8000), ("write", 0, SEC_V, stm.A_VOL, 18000)]  # live
    items.append(("wait", 400))
    return items, regs


def run_arty(*, inject, outdir, rtl_dir=None):
    import verify_uart_bridge as vub
    items, regs = arty_items()
    vub.SCENARIOS["deadline-arty"] = lambda: (items, {"tail": 200})
    print(f"verify_deadline: arty-uart: {sum(1 for i in items if i[0] == 'write')} live writes, "
          f"{sum(1 for i in items if i[0] == 'event')} device-scheduled events over the UART pin; "
          f"wrapper fpga/rtl/arty_a7_top.v, published configuration {vub.CONFIG}")
    cwd = os.getcwd()
    os.chdir(HERE)                    # the monitor's `include resolves from rtl-sketch
    try:
        with monitor_attached(vub.top, MON_ARTY, rtl_dir):
            run = vub.simulate("deadline-arty", inject, outdir)
    finally:
        os.chdir(cwd)
    if run is None:
        return None
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        ok, comp, detail = vub.analyze(run)
    res = dict(bench="tb_uart_bx (Arty wrapper, UART pins)", report=run["report"],
               defines=run["defines"], sched=run["files"]["wrs"] + ".sched",
               meta=dict(regs=regs, claim="three_2x_audible", drums=True), uart_ok=ok,
               uart_detail=detail)
    for k in ("busy_at_tick", "overrun", "overflow", "frames_no_sample", "worst_strobe_cycle",
              "wire_mismatch", "swap", "width", "writes_bad", "frame_pred_bad", "frame_no_pred",
              "collisions", "periods", "writes_seen", "writes_sent", "core_bad"):
        res[k] = comp.get(k)
    res["coverage"] = None            # the UART contract's frames are checked by analyze()
    return res


# ---- verdict ---------------------------------------------------------------------
def verdict(res, sched, go_cycle, claim_min=100):
    """(status, reasons, deadline_failed)."""
    reasons, deadline = [], []
    if sched["frames"] < 10:
        return 2, [f"REFUSED -- only {sched['frames']} frames in the schedule record"], False
    if sched["bad_go"]:
        return 2, [f"REFUSED -- go not at cycle {go_cycle} in {sched['bad_go']} frame(s)"], False
    cov = res.get("coverage")
    if cov is not None:
        need = {"three_2x_saws": "three_saws_gate_on",
                "three_2x_audible": "three_2x_audible_gate_on"}[res["meta"]["claim"]]
        if cov.get(need, 0) < claim_min:
            return 2, [f"REFUSED -- the stimulus reached '{need}' in {cov.get(need, 0)} frames "
                       f"(< {claim_min}): it does not cover the configuration it claims"], False
        if res["meta"]["drums"] and cov.get("three_2x_audible_with_strike", 0) < 3:
            return 2, ["REFUSED -- drums were never struck while three 2x oscillators sounded"], False
    if sched["missed"]:
        deadline.append(f"{sched['missed']} missed frame(s), first {sched['missed_frames'][:5]}")
    if res.get("busy_at_tick"):
        deadline.append(f"datapath busy at {res['busy_at_tick']} tick(s)")
    if res.get("overrun"):
        deadline.append("sticky overrun flag set")
    if res.get("frames_no_sample"):
        deadline.append(f"{res['frames_no_sample']} frame(s) with no sample")
    if sched["writes_in_compute"] or sched["frames_with_write_after_go"]:
        reasons.append(f"{sched['writes_in_compute']} write(s) during computation, "
                       f"{sched['frames_with_write_after_go']} frame(s) with a write at/after go")
    for k in ("overflow", "wire_mismatch", "swap", "width", "writes_bad", "frame_pred_bad",
              "frame_no_pred", "collisions", "core_bad"):
        if res.get(k):
            reasons.append(f"{k} {res[k]}")
    if res.get("writes_seen") is not None and res.get("writes_sent") is not None \
            and res["writes_seen"] != res["writes_sent"]:
        reasons.append(f"{res['writes_seen']} of {res['writes_sent']} writes reached the port")
    if "uart_ok" in res and not res["uart_ok"] and not deadline:
        reasons.append("UART contract check failed: " + "; ".join(res["uart_detail"][:3]))
    if deadline or reasons:
        return 1, deadline + reasons, bool(deadline)
    return 0, [], False


def provenance(defines):
    files = {n: hashlib.sha256(open(os.path.join(HERE, n), "rb").read()).hexdigest()[:12]
             for n in list(vst.SRCS) + ["tb_top_bx.v", "tb_uart_bx.v", "uart_bridge.v",
                                        "deadline_mon.vh", "deadline_mon_top.v", "deadline_mon_arty.v"]}
    files["fpga/rtl/arty_a7_top.v"] = hashlib.sha256(
        open(os.path.join(ROOT, "fpga/rtl/arty_a7_top.v"), "rb").read()).hexdigest()[:12]
    pv = vst.provenance([])
    return dict(commit=pv["commit"], files=files, generated=pv["generated"], defines=defines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--scenario", choices=sorted(SPI_SCENARIOS) + ["arty-uart"])
    ap.add_argument("--no-osc2x", action="store_true", help="legacy single-rate oscillators")
    ap.add_argument("--no-filter2x", action="store_true")
    ap.add_argument("--pulse2x", action="store_true")
    ap.add_argument("--inject", default=None, help="compile with -DINJECT_BUG_<NAME>")
    ap.add_argument("--mutant", default=None,
                    help="late:N (the late-completion control: N stall cycles) or skip2xwin "
                         "(the candidate correction); generated from voice_dp.v at run time")
    ap.add_argument("--write-mutant", default=None, metavar="SPEC",
                    help="only write the mutant voice_dp.v under --outdir and print its path")
    ap.add_argument("--expect-fail", action="store_true")
    ap.add_argument("--short", action="store_true")
    ap.add_argument("--outdir", default=None)
    ap.add_argument("--json", default=None)
    a = ap.parse_args(argv)
    osc2x, filter2x = not a.no_osc2x, not a.no_filter2x
    if not a.scenario and not a.write_mutant:
        ap.error("--scenario is required")
    go = chip_go_cycle()
    if a.write_mutant:
        d = make_mutant(a.write_mutant, os.path.abspath(a.outdir or os.path.join(HERE, "build", "deadline")))
        print(os.path.join(d, "voice_dp.v"))
        return 0
    tag = a.scenario + ("-p2x" if a.pulse2x else "") + (f"-{a.inject}" if a.inject else "") \
        + (f"-{a.mutant.replace(':', '')}" if a.mutant else "")
    outdir = os.path.abspath(a.outdir or os.path.join(HERE, "build", "deadline", tag))
    rtl_dir = make_mutant(a.mutant, outdir) if a.mutant else None
    if a.scenario == "arty-uart":
        if a.pulse2x or a.no_osc2x or a.no_filter2x:
            print("verify_deadline: REFUSED -- arty-uart runs the published Arty configuration "
                  "(OSC2X=1 FILTER2X=1 PULSE2X=0) only"); return 2
        res = run_arty(inject=a.inject, outdir=outdir, rtl_dir=rtl_dir)
    else:
        res = run_spi(a.scenario, osc2x=osc2x, filter2x=filter2x, pulse2x=a.pulse2x,
                      inject=a.inject, outdir=outdir, short=a.short, rtl_dir=rtl_dir)
    if res is None:
        print("verify_deadline: REFUSED -- the simulation did not run"); return _exit(2, a)
    for ln in res["report"]:
        print("  sim: " + ln)
    if "refused" in res:
        print(f"verify_deadline: REFUSED -- {res['refused']}"); return _exit(2, a)
    if not os.path.exists(res["sched"]):
        print(f"verify_deadline: REFUSED -- no schedule record at {res['sched']}: the monitor did not run")
        return _exit(2, a)
    rows = read_sched(res["sched"])
    sched = analyse_sched(rows, go)
    status, reasons, deadline_failed = verdict(res, sched, go)
    cfg = dict(OSC2X=int(osc2x), FILTER2X=int(filter2x), PULSE2X=int(a.pulse2x))
    print(f"verify_deadline: configuration {cfg}; bench {res['bench']}; go at cycle {go} (synth_top GO_CYCLE)")
    print(f"verify_deadline: {sched['frames']} frames scheduled; missed {sched['missed']}; "
          f"sticky overrun {res.get('overrun')}; busy at tick {res.get('busy_at_tick')}; "
          f"link overflow {res.get('overflow')}")
    print(f"verify_deadline: WORST SLACK sample {sched['worst_sample_slack']} cycles "
          f"(strobe cycles {sched['strobe_range']}, deadline 254); voice busy {sched['worst_busy_slack']} "
          f"(deadline 255); drum busy {sched['worst_drum_slack']}")
    print(f"verify_deadline: voice starts at cycle(s) {sched['voice_start'][:4]}, drum at "
          f"{sched['drum_start'][:4]}; writes in cycles {sched['write_cycles']} (max "
          f"{sched['max_writes_per_frame']} per frame), {sched['writes_in_compute']} during computation")
    print(f"verify_deadline: max per-frame waits: reciprocal {sched['max_rwait']}, 2x bank "
          f"{sched['max_oscwait']}, drum handshake {sched['max_dwait']} cycles; max active PolyBLEP "
          f"windows {sched['max_active_windows']}; cost model {sched['cost_model']}")
    for r in sched["worst_frames"][:3]:
        print(f"  worst frame {r['frame']}: strobe {r['strobe']} (slack {r['sample_slack']}), voice "
              f"{r['v_start']}..{r['v_last']}, drum {r['d_start']}..{r['d_last']}, waits r/osc/d/y "
              f"{r['rwait']}/{r['oscwait']}/{r['dwait']}/{r['ywait']}, windows {r['w1']}/{r['win']}, "
              f"im1 {r['im1']}, sk {r['sk']}")
    if res.get("coverage") is not None:
        print(f"verify_deadline: coverage (frames): {res['coverage']}")
    print(f"verify_deadline: I2S: {res.get('periods')} periods, {res.get('wire_mismatch')} differ from "
          f"the model; L!=R {res.get('swap')}; bad width {res.get('width')}; writes "
          f"{res.get('writes_seen')}/{res.get('writes_sent')}, corrupted {res.get('writes_bad')}, "
          f"off-prediction {res.get('frame_pred_bad')}")
    word = {0: "PASS", 1: "FAIL", 2: "REFUSED"}[status]
    print(f"verify_deadline: {word}" + (" -- " + "; ".join(reasons) if reasons else
                                        f" -- zero missed frames, worst sample slack "
                                        f"{sched['worst_sample_slack']} cycles, I2S identical to the model"))
    if a.json:
        rec = dict(scenario=a.scenario, configuration=cfg, bench=res["bench"], go_cycle=go,
                   inject=a.inject, mutant=a.mutant, status=word, reasons=reasons,
                   deadline_failed=deadline_failed,
                   schedule={k: v for k, v in sched.items()},
                   facts={k: res.get(k) for k in ("busy_at_tick", "overrun", "overflow",
                                                  "frames_no_sample", "worst_strobe_cycle",
                                                  "periods", "wire_mismatch", "swap", "width",
                                                  "writes_sent", "writes_seen", "writes_bad",
                                                  "frame_pred_bad", "collisions", "model_peak")},
                   coverage=res.get("coverage"), provenance=provenance(res["defines"]))
        os.makedirs(os.path.dirname(os.path.abspath(a.json)), exist_ok=True)
        with open(a.json, "w") as fh:
            json.dump(rec, fh, indent=1, default=str)
    if a.expect_fail:
        if status == 1 and deadline_failed:
            print(f"verify_deadline: negative control {a.inject or a.mutant} CAUGHT by the deadline check")
            return 0
        print(f"verify_deadline: NEGATIVE CONTROL NOT CAUGHT FOR ITS REASON (status {word}, "
              f"deadline failed {deadline_failed})")
        return 1 if status == 0 else 2 if status == 2 else 1
    return status


def _exit(st, a):
    if a.expect_fail:
        print(f"verify_deadline: NEGATIVE CONTROL NOT CAUGHT (status {st})")
    return st


if __name__ == "__main__":
    sys.exit(main())
