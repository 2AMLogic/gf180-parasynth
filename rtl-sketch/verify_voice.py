#!/usr/bin/env python3
"""Bit-exact verification of voice_dp.v against model/voice_fx.py (VoiceFx).

The model is the specification. Scenarios are played through ONE continuous
voice (contract 5.3, DR 0003); the model's writes are translated one-to-one
into DR 0007 register writes; tb_voice.v applies them at the register port
in the frames the model applies them; and every output sample AND every tap
of contract 16.4 -- the three oscillators with their increments and
reciprocals, mixed, ae, fe, cut, g, kc, k_eff, the ladder's y, the VCA's v,
the pre-rail out_v -- and the final state (phases, increment accumulators,
envelope levels and segments) are compared with NO tolerance.

Scenarios (--set full; --set quick is the same list at shorter lengths, what
the test suite runs; --only picks by key):

  default    the default patch (saw, saw, square), one note from reset
  waves2     pulse25 / tri / sine at res 1.05, drive 3, wide cutoff envelope,
             full tracking, a glide from a fifth below
  para       a paraphonic multi-trigger phrase through the reference host
  waveforms  every waveform in every oscillator slot, at low and high notes,
             with the shapes and weights changed mid-note
  waves3     the Model D shapes revision 9 adds -- shark-tooth, reverse
             sawtooth, wide (29 %) and narrow (15 %) rectangular
  noise      white and pink alone through a swept ladder; three oscillators
             and pink together (the mixer's four sources); the noise weight
             at 65535 into a 30 Hz filter, where the mixer saturates
  modulation oscillator 3 as the LFO: a 4 Hz triangle to pitch with OSC-3
             CONTROL off; a 2 Hz reverse saw to the cutoff; the wheel swept
             with both destinations on and OSC-3 CONTROL ON, which is
             oscillator 3 modulating its own pitch; and both depths at the
             register maximum, where the octave word saturates
  notes      every NOTE_INC entry (0..127) with the default detunes; the
             increments where 5.5's clamp fires (note 127 + 24 semitones =
             2^24 - 1); the powers of two where the reciprocal clamps; inc =
             1, 2, 3; inc >= 2^23 where both PolyBLEP windows can open;
             inc = 0 (a stalled oscillator); a slew through 0
  glide      up two octaves and down three at the reference rate, landing
             exactly; glide at its maximum (an octave per frame); glide = 1
             (the max(1, .) floor every frame); GLIDE <- 0 mid-glide (snap);
             a jump mid-glide
  gate       GATE_ON; TRIG in attack, decay and sustain; GATE_OFF in attack;
             GATE_ON during a release (attack from the released level);
             adjacent GATE_OFF / GATE_ON; TRIG with the gate off; a legato
             pitch change without TRIG (DR 0003)
  silence    a note whose release runs all the way to level 0 -- through
             the max(1, .) floor of 8.3 -- while the ladder is still driven
             at the mixer's level (DR 0005); and a self-oscillating filter
             that keeps singing after the VCA has closed
  extremes   the all-maximum control image; the all-zero image; a negative
             cutoff span with full tracking; weights that saturate the mixer;
             k that saturates k_eff; ogain that saturates the ladder's word;
             vol that reaches the rail; rate = 0; d_dec = 0; a_inc = 0
  audition   the reference sequences of contract 16: 04-lead-glide,
             07-growl-bass and 08-self-osc-whistle through KeyHost (the first
             0.8 s of each) -- full set only

Exit status (a CI job asserting that a negative control fails must require
exactly 1):
  0  every sample and every tap identical, final state identical
  1  the simulation ran to completion and something differed
  2  it did not run: tool missing, compile error, timeout, short output

  --inject NAME     compile with -DINJECT_BUG_VOICE_<NAME>; the negative controls
  --expect-fail     exit 0 only if the comparison gave 1
  --rtl FILE        simulate FILE in place of voice_dp.v (the red run of
                    docs/verification-rules.md: stubs/voice_dp_stub.v, every
                    output X, must give exit 1 with the frames reported as X)
  --set full|quick  which scenario lengths (default full)
  --only a,b,c      run only these scenario keys
  --compare-only F  skip generation and simulation, compare F to the expected
"""
from __future__ import annotations
import argparse, os, subprocess, sys
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "model")); sys.path.insert(0, os.path.join(ROOT, "audition"))
import numpy as np
import voice_fx as vf
import dsp
from dsp import SR, PHASE_MASK
from verify_ladder import tool

A = dict(INC=0x00, WAVE=0x04, W=0x08, WN=0x0B, GLIDE=0x0C, VOL=0x0D, AMP=0x10, FILT=0x14,
         CUT_LO=0x18, CUT_HI=0x19, TRACK=0x1A, NSEL=0x1B, K=0x1C, GAIN=0x1D, OGAIN=0x1E,
         MROUTE=0x1F, GATE_ON=0x20, GATE_OFF=0x21, TRIG=0x22,
         MMIX=0x24, MWHEEL=0x25, MPD=0x26, MFD=0x27)
WAVE_CODE = vf.WAVE_CODE
FULL24 = (1 << 24) - 1
FIELDS = ["sample", "osc0", "osc1", "osc2", "inc0", "inc1", "inc2", "sh0", "sh1", "sh2",
          "r0", "r1", "r2", "mixed", "ae", "fe", "cut", "g", "kc", "k_eff", "y19", "v", "out_v",
          "phase2x0", "phase2x1", "phase2x2"]
STATE_FIELDS = ["phase0", "phase1", "phase2", "inc_acc0", "inc_acc1", "inc_acc2",
                "level_a", "level_f", "seg_a", "seg_f", "phase2x0", "phase2x1", "phase2x2"]
RTL_FILES = ["tb_voice.v", "voice_dp.v", "recip_div.v", "ladder_dp_n.v",
             "osc_2x_saw_path.v", "polyblep_saw_pair.v", "osc_substep_pair.v",
             "decimate_2x_tm_sym.v", "osc_2x_saw_bank.v", "rate_conv_2x.v"]
F1CAL = "surge-type2-clean-v1"
#: plan074 D negative control: the image names the calibration, the register
#: port receives the LEGACY gain/ogain words. The model still renders the
#: requested image, so the bench must mismatch. Set by --f1cal-fault.
F1CAL_FAULT = None
BUGS = ["SQUARE_SIGN", "ENV_FLOOR", "ENV_RATE_EXP", "KEFF", "MIX_SAT", "GLIDE_FLOOR", "RECIP_CLAMP", "TRIG_RESET", "OUT_SAT", "OSC_SMOOTH_ON", "OSC2X_HEADROOM", "OSC2X_OFF", "FILTER2X_OFF", "PULSE2X_OFF",
        "LFSR_TAP", "NOISE_SEL", "SHARK_MIX", "MOD_NODELAY"]


# ---- the model's writes as register writes -----------------------------------
def patch_to_writes(regs: dict, f0: int) -> list:
    """The register image VoiceFx._apply_patch installs at the first frame of
    a play() call, as (frame, flag, addr, data). GLIDE is written before any
    INC of the same frame, as the model applies it (5.2)."""
    out = []
    for k, s in enumerate(regs["waves"]): out.append((f0, 0, A["WAVE"] + k, WAVE_CODE[s]))
    for k, wgt in enumerate(regs["weights"]): out.append((f0, 0, A["W"] + k, int(wgt)))
    for base, key in ((A["AMP"], "amp"), (A["FILT"], "fenv")):
        for j, v in enumerate(regs[key]): out.append((f0, 0, base + j, int(v)))
    out.append((f0, 0, A["WN"], int(regs["weights"][3])))
    out += [(f0, 0, A["CUT_LO"], int(regs["cut_lo"])), (f0, 0, A["CUT_HI"], int(regs["cut_hi"])),
            (f0, 0, A["K"], int(regs["k"])), (f0, 0, A["GAIN"], int(regs["gain"])), (f0, 0, A["OGAIN"], int(regs["ogain"])),
            (f0, 0, A["VOL"], int(regs["vol"])), (f0, 0, A["GLIDE"], int(regs["glide"]))]
    out += [(f0, 0, A["NSEL"], int(regs.get("nsel", 0))), (f0, 0, A["MROUTE"], int(regs.get("mroute", 0))),
            (f0, 0, A["MMIX"], int(regs.get("mmix", 0))), (f0, 0, A["MWHEEL"], int(regs.get("mwheel", 0))),
            (f0, 0, A["MPD"], int(regs.get("mpd", 0))), (f0, 0, A["MFD"], int(regs.get("mfd", 0)))]
    return out


def model_writes_to_regs(writes: list, f0: int) -> list:
    out = []
    for w in writes:
        f, op, args = int(w[0]) + f0, w[1], w[2:]
        if op == "INC":
            k, v = args[0], int(args[1]); jump = bool(args[2]) if len(args) > 2 else False
            out.append((f, int(jump), A["INC"] + k, v))
        elif op == "TRACK": out.append((f, 0, A["TRACK"], int(args[0])))
        elif op == "GATE":  out.append((f, 0, A["GATE_ON"] if int(args[0]) else A["GATE_OFF"], 0))
        elif op == "TRIG":  out.append((f, 0, A["TRIG"], 0))
        elif op == "GLIDE": out.append((f, 0, A["GLIDE"], int(args[0])))
        elif op == "MWHEEL": out.append((f, 0, A["MWHEEL"], int(args[0])))
        else: raise ValueError(op)
    return out


# ---- scenarios ------------------------------------------------------------------
def _incs(note, detune=(0.0, 0.07, -12.0)):
    return vf.VoiceFx.note_incs(note, detune)


def _note_writes(note, regs, f=0, jump=True):
    return [(f, "INC", k, v, jump) for k, v in enumerate(_incs(note, regs["detune"]))] + \
           [(f, "TRACK", vf.VoiceFx.note_track(note, regs["track"]))]


def scenarios(which: str, only=None) -> list:
    """(key, name, regs, writes, n) -- each is one VoiceFx.play() call on the
    continuing voice. `which` is 'full' or 'quick'."""
    q = which == "quick"
    v = vf.VoiceFx()
    S = []

    def add(key, name, regs, writes, n):
        assert all(0 <= int(w[0]) < n for w in writes), (key, n, [w for w in writes if not 0 <= int(w[0]) < n])
        if only is None or key in only:
            S.append((key, name, regs, writes, n))

    # -- default: the default patch, one note from reset --------------------------
    d = 0.025 if q else 0.3
    r = v.note_on(45, d)
    add("default", "default patch (saw, saw, square), note 45 from reset", r["regs"], r["writes"], r["n"])

    # -- waves2: the other shapes, hot resonance and drive, glide from a fifth below
    r = v.note_on(69, d, glide_from=62, waves=("pulse25", "tri", "sine"), detune=(0.0, 0.03, -12.0),
                  mix=(1.0, 0.7, 0.9), cutoff=(200, 9000), q=1.05, drive=3.0, track=0.9, glide_s=0.05,
                  amp=(0.002, 0.1, 0.6, 0.08), fenv=(0.001, 0.2, 0.3, 0.05))
    add("waves2", "pulse25 / tri / sine, res 1.05, drive 3, glide from 62", r["regs"], r["writes"], r["n"])

    # -- para: a paraphonic multi-trigger phrase through the reference host --------
    n = int((0.05 if q else 0.3) * SR)
    regs = v.patch_regs(waves=("square", "saw", "saw"), detune=(0.0, 0.05, -12.0), q=0.9, drive=2.0,
                        cutoff=(300, 5000), glide_s=0.03)
    ev = [(0, "on", 60), (n // 7, "on", 64), (2 * n // 7, "on", 67), (3 * n // 7, "off", 64),
          (4 * n // 7, "off", 60), (9 * n // 14, "on", 48), (5 * n // 7, "off", 67), (6 * n // 7, "off", 48)]
    host = vf.KeyHost(priority="last", trigger="multi", glide="legato", mode="para")
    add("para", "paraphonic multi-trigger phrase, KeyHost (last, multi, legato glide, para)",
        regs, host.writes(ev, regs, first_from_reset=False), n)

    # -- waveforms: every shape in every slot, low and high notes, changed mid-note --
    n = int((0.01 if q else 0.06) * SR)
    rot = [(("square", "pulse25", "tri"), 100), (("sine", "saw", "pulse25"), 24), (("tri", "sine", "square"), 108),
           (("saw", "square", "sine"), 115), (("pulse25", "tri", "saw"), 60)]
    for i, (waves, note) in enumerate(rot):
        regs = v.patch_regs(waves=waves, detune=(0.0, 0.07, -12.0), mix=(1.0, 0.9, 0.8), q=0.7, drive=1.8,
                            cutoff=(300, 8000), track=0.5)
        # a new play() call re-writes the image: with the gate left on this is SET_WAVE / SET_WEIGHT mid-note (5.2)
        writes = _note_writes(note, regs) + ([(0, "GATE", 1)] if i == 0 else [])
        add("waveforms", f"waveforms {waves} at note {note}" + (" (shapes and weights changed mid-note)" if i else ""),
            regs, writes, n)

    # -- waves3: the Model D shapes contract rev 9 adds (W1, W3, W4, W5) --------
    n = int((0.012 if q else 0.06) * SR)
    rot = [(("shark", "revsaw", "pulse29"), 33), (("pulse15", "shark", "revsaw"), 96),
           (("revsaw", "pulse29", "pulse15"), 69), (("pulse29", "pulse15", "shark"), 21),
           (("shark", "shark", "shark"), 108)]
    for i, (waves, note) in enumerate(rot):
        regs = v.patch_regs(waves=waves, detune=(0.0, 0.07, -12.0), mix=(1.0, 0.9, 0.8), q=0.7,
                            drive=1.8, cutoff=(300, 8000), track=0.5)
        writes = _note_writes(note, regs) + ([(0, "GATE", 1)] if i == 0 else [])
        add("waves3", f"shark / revsaw / wide / narrow {waves} at note {note}", regs, writes, n)

    # -- noise: the fourth mixer source, both colours, through the ladder --------
    n = int((0.05 if q else 0.25) * SR)
    for nsel, name in ((0, "white"), (1, "pink")):
        regs = v.patch_regs(mix=(0.0, 0.0, 0.0), noise=1.0, nsel=nsel, cutoff=(200, 9000),
                            q=0.9, drive=2.0, amp=(0.002, 0.15, 0.7, 0.08))
        add("noise", f"{name} noise alone at mixer weight 1.0, swept filter",
            regs, _note_writes(48, regs) + [(0, "GATE", 1), (n - 200, "GATE", 0)], n)
    regs = v.patch_regs(waves=("saw", "pulse29", "shark"), mix=(1.0, 0.6, 0.4), noise=0.8, nsel=1,
                        cutoff=(400, 6000), q=1.02, drive=2.4, track=0.4)
    add("noise", "three oscillators AND pink noise, res 1.02: the mixer's four sources",
        regs, _note_writes(40, regs) + [(0, "GATE", 1)], n)
    regs = v.patch_regs(mix=(0.0,), noise=1.0, nsel=0, cutoff=(30, 30), q=0.2, drive=0.5)
    regs["weights"] = [0, 0, 0, 65535]                       # the weight register at its maximum
    add("noise", "white noise at weight 65535 into a 30 Hz filter: the mixer saturates",
        regs, [(0, "GATE", 1)], 400 if q else 1200)

    # -- modulation: oscillator 3 as the LFO (M1-M9) -----------------------------
    n = int((0.08 if q else 0.4) * SR)
    lo = dsp.phase_inc(4.0)                                   # osc 3 in LO range
    regs = v.patch_regs(waves=("saw", "saw", "tri"), mix=(1.0, 0.9, 0.0), cutoff=(500, 5000),
                        q=0.8, drive=2.0, osc_mod=True, osc3_ctl=False, mod_wheel=1.0, mod_mix=0.0)
    w = [(0, "INC", 0, _incs(57)[0], True), (0, "INC", 1, _incs(57)[1], True),
         (0, "INC", 2, lo, True), (0, "TRACK", vf.VoiceFx.note_track(57, regs["track"])), (0, "GATE", 1)]
    add("modulation", "vibrato: osc 3 triangle at 4 Hz to pitch, wheel full, OSC-3 CONTROL off", regs, w, n)
    regs = v.patch_regs(waves=("saw", "saw", "revsaw"), mix=(1.0, 0.0, 0.0), cutoff=(440, 440),
                        q=1.0, drive=2.0, track=0.0, filt_mod=True, osc3_ctl=False,
                        mod_wheel=1.0, mod_mix=0.0)
    w = [(0, "INC", 0, _incs(45)[0], True), (0, "INC", 2, dsp.phase_inc(2.0), True),
         (0, "TRACK", 0), (0, "GATE", 1)]
    add("modulation", "filter sweep: osc 3 reverse saw at 2 Hz to the cutoff, wheel full", regs, w, n)
    # the wheel swept mid-note, the mix panned from oscillator 3 to noise, and
    # OSC-3 CONTROL ON -- oscillator 3 modulating its own pitch (M9's loop)
    regs = v.patch_regs(waves=("saw", "square", "tri"), mix=(1.0, 0.5, 0.3), noise=0.2, nsel=1,
                        cutoff=(300, 7000), q=0.9, drive=2.2, osc_mod=True, filt_mod=True,
                        osc3_ctl=True, mod_wheel=0.0, mod_mix=0.35)
    w = _note_writes(52, regs) + [(0, "GATE", 1)]
    for j in range(8):
        w.append((j * (n // 9) + 10, "MWHEEL", (j * 32767) // 7))
    add("modulation", "wheel swept 0 -> full with BOTH destinations on and OSC-3 CONTROL on "
        "(oscillator 3 modulating its own pitch, M9)", regs, w, n)
    # the extremes of the path: both depths at the register maximum (the octave
    # word saturates at +-4), the pan past its top, the wheel at its top
    regs = v.patch_regs(waves=("square", "saw", "pulse15"), mix=(1.0, 0.8, 0.5), noise=1.0, nsel=0,
                        cutoff=(200, 12000), q=1.0, drive=2.0, osc_mod=True, filt_mod=True, osc3_ctl=True)
    regs["mpd"] = regs["mfd"] = 65535
    regs["mmix"] = 65535                                      # past MMIX_FULL: clamps to noise only
    regs["mwheel"] = 65535
    w = _note_writes(64, regs) + [(0, "GATE", 1), (n // 2, "MWHEEL", 0)]
    add("modulation", "both depths at 65535 (the octave word saturates at +-4), mmix past its top, "
        "wheel at 65535 then 0", regs, w, n)

    # -- notes: the full note range and the increments where 5.5's clamps fire ----
    per = 12 if q else 40
    regs = v.patch_regs(waves=("saw", "square", "pulse25"), detune=(0.0, 0.07, -12.0), mix=(1.0, 1.0, 1.0),
                        q=0.5, drive=1.5, cutoff=(200, 12000), track=0.3, amp=(0.001, 0.1, 1.0, 0.1))
    writes = [(0, "GATE", 1)]
    for note in range(128):
        writes += _note_writes(note, regs, f=note * per)
    add("notes", "every NOTE_INC entry 0..127, detune (0, +0.07, -12), 3 shapes with PolyBLEP", regs, writes, 128 * per)
    per = 100 if q else 400
    steps = [_incs(127, (24.0, 23.24, 23.0)),                       # 2^24 - 1 (clamped), and just below the clamp
             [1, 2, 3],                                             # tiny: e = -15, -14, -14
             [1 << 15, 1 << 16, 1 << 23],                           # powers of two: m = 2^15, r clamps to 65535
             [(1 << 23) + 1, (1 << 24) - 1, (1 << 24) - 2],         # both PolyBLEP windows can open
             [0, dsp.phase_inc(dsp.note_hz(60)), 0],                # inc = 0 stalls oscillators 0 and 2
             [3, 3, 3]]                                             # then a slew through 0 (below)
    writes = [(0, "TRACK", 0)]
    for i, incs in enumerate(steps):
        writes += [(i * per, "INC", k, int(x), True) for k, x in enumerate(incs)]
    f = len(steps) * per
    writes += [(f, "GLIDE", 2692)] + [(f, "INC", k, 0, False) for k in range(3)]   # acc 768 -> 0 at max(1, .) per frame
    n = f + (900 if q else 1200)
    add("notes", "increments at 5.5's clamp, 1..3, powers of two, >= 2^23, 0 (stalled), and a slew through 0",
        regs, writes, n)

    # -- glide: up, down, exact landing, maximum, floor, snap, jump mid-glide -------
    regs = v.patch_regs(glide_s=(0.02 if q else vf.GLIDE_REF_S))
    oct_frames = int(round(1.0 / (np.log2(1.0 + regs["glide"] / (1 << 24)))))   # frames per octave at this rate
    writes = [(0, "GATE", 1)] + _note_writes(48, regs, 0, jump=True)
    f = 50
    writes += _note_writes(72, regs, f, jump=False); f += 2 * oct_frames + 200          # up two octaves, lands
    writes += _note_writes(36, regs, f, jump=False); f += 3 * oct_frames + 200          # down three, lands
    writes += [(f, "GLIDE", FULL24)] + _note_writes(60, regs, f, jump=False); f += 40    # an octave per frame
    writes += [(f, "GLIDE", 1)] + _note_writes(61, regs, f, jump=False); f += 200        # d = 0 -> 1 LSB of Q24.8 per frame
    writes += [(f, "GLIDE", regs["glide"])] + _note_writes(84, regs, f, jump=False); f += oct_frames // 2
    writes += [(f, "GLIDE", 0)]; f += 50                                                 # mid-glide: snaps to the target
    writes += [(f, "GLIDE", regs["glide"])] + _note_writes(48, regs, f, jump=False); f += oct_frames // 2
    writes += _note_writes(55, regs, f, jump=True); f += 100                             # a jump mid-glide
    add("glide", f"glide: +2 oct, -3 oct at {oct_frames} frames/oct, max rate, glide = 1, GLIDE <- 0 mid-glide, jump",
        regs, writes, f)

    # -- gate: GATE_ON / TRIG / GATE_OFF in every segment (DR 0003) ---------------
    regs = v.patch_regs(amp=(0.005, 0.02, 0.6, 0.05), fenv=(0.002, 0.03, 0.3, 0.04), q=0.8, drive=2.0)
    a = 240                                                                  # the amp attack, frames
    writes = _note_writes(57, regs) + [
        (0, "GATE", 1), (a // 2, "TRIG"), (a + 400, "TRIG"), (a + 1200, "TRIG"),  # attack, decay, sustain
        (a + 1300, "GATE", 0), (a + 1400, "GATE", 1),                             # release; attack from the released level
        (a + 1450, "GATE", 0),                                                    # release from a partial attack
        (a + 1500, "GATE", 0), (a + 1501, "GATE", 1),                             # adjacent frames
        (a + 1900, "GATE", 0), (a + 1950, "TRIG"), (a + 2000, "GATE", 1),         # TRIG with the gate off, then on
    ] + _note_writes(64, regs, a + 2100) + [(a + 2200, "GATE", 0)]               # legato pitch change, no TRIG
    add("gate", "GATE_ON, TRIG in attack / decay / sustain, GATE_OFF in attack, GATE_ON in release, adjacent, TRIG off, legato",
        regs, writes, a + 2300)

    # -- silence: a release that runs to exactly zero; a filter that keeps singing --
    rel = 0.045 if q else 0.12                                              # rate 121 (floor 542) / 45 (floor 1457)
    regs = v.patch_regs(q=0.9, drive=2.5, vol=0.9, amp=(0.003, 0.1, 0.75, rel), fenv=(0.002, 0.1, 0.5, rel))
    gate_n = 1200 if q else 4800
    n = gate_n + (7000 if q else 16800)
    writes = _note_writes(40, regs) + [(0, "GATE", 1), (gate_n, "GATE", 0)]
    add("silence", f"note 40 at res 0.9, drive 2.5, vol 0.9; gate {gate_n} frames then a release of {n - gate_n} frames to level 0",
        regs, writes, n)
    regs = v.patch_regs(waves=("sine",), detune=(0.0,), mix=(1.0,), cutoff=(200, 2600), q=1.06, drive=0.5,
                        track=0.9, vol=0.9, amp=(0.005, 0.1, 0.85, rel), fenv=(0.005, 0.2, 0.5, rel))
    gate_n = 1500 if q else 4800
    n = gate_n + (6000 if q else 12000)
    writes = _note_writes(69, regs) + [(0, "GATE", 1), (gate_n, "GATE", 0)]
    add("silence", "self-oscillating whistle (res 1.06): the VCA closes while the ladder still sings", regs, writes, n)

    # -- extremes: the register image at its limits ---------------------------------
    n = 400 if q else 960
    regs = v.patch_regs()
    regs["weights"] = [(1 << 16) - 1] * 4
    regs["amp"] = regs["fenv"] = (FULL24, FULL24, FULL24, 65535)
    regs["cut_lo"] = regs["cut_hi"] = 65535
    regs["k"], regs["gain"], regs["ogain"] = (1 << 17) - 1, (1 << 20) - 1, (1 << 20) - 1
    regs["vol"], regs["glide"] = 65535, FULL24
    regs["nsel"] = 1
    regs["mmix"] = regs["mwheel"] = regs["mpd"] = regs["mfd"] = 65535
    regs["mroute"] = 7
    writes = [(0, "INC", k, FULL24, True) for k in range(3)] + [(0, "TRACK", 65535), (0, "GATE", 1),
                                                                 (n // 2, "INC", 0, 1, False), (n // 2, "TRIG")]
    add("extremes", "the all-maximum control image (weights, envelopes, cutoff, k, gain, ogain, vol, glide, inc)", regs, writes, n)
    regs = v.patch_regs()
    regs["weights"] = [0] * 4; regs["amp"] = regs["fenv"] = (0, 0, 0, 0); regs["cut_lo"] = regs["cut_hi"] = 0
    regs["k"] = regs["gain"] = regs["ogain"] = regs["vol"] = regs["glide"] = 0
    writes = [(0, "INC", k, 0, True) for k in range(3)] + [(0, "TRACK", 0), (0, "GATE", 1)]
    add("extremes", "the all-zero image: silent (14)", regs, writes, 200 if q else 480)
    n = 1500 if q else 3000
    regs = v.patch_regs(q=0.6, drive=2.0, amp=(0.002, 0.05, 0.9, 0.1), fenv=(0.05, 0.05, 0.2, 0.1))
    regs["cut_lo"], regs["cut_hi"] = 9000, 100                              # negative span
    regs["weights"] = [30000, 30000, 30000, 30000]                           # the mixer saturates
    regs["k"] = (1 << 17) - 1                                               # k_eff saturates near the kc peak
    regs["ogain"] = (1 << 20) - 1                                           # the ladder's 19-bit word saturates
    regs["vol"] = 65535                                                     # the rail
    writes = _note_writes(52, regs) + [(0, "TRACK", 65535), (0, "GATE", 1), (n // 2, "TRACK", 0), (n - 300, "GATE", 0)]
    add("extremes", "negative span, track 65535, saturating weights, k / ogain / vol at maximum", regs, writes, n)
    regs = v.patch_regs(amp=(0.002, 0.01, 0.5, 0.1), fenv=(0.002, 0.01, 0.5, 0.1))
    regs["amp"] = regs["amp"][:3] + (0,); regs["fenv"] = regs["fenv"][:3] + (0,)      # rate = 0: 1 LSB per frame
    writes = _note_writes(50, regs) + [(0, "GATE", 1), (150, "GATE", 0)]
    add("extremes", "rate = 0 (release at one LSB per frame)", regs, writes, 400)
    regs = v.patch_regs()
    regs["amp"] = (regs["amp"][0], 0, 0, regs["amp"][3]); regs["fenv"] = (regs["fenv"][0], 0, FULL24, regs["fenv"][3])
    writes = _note_writes(50, regs) + [(0, "GATE", 1)]
    add("extremes", "d_dec = 0 with sus = 0 (DECAY holds at FULL) and sus = FULL (SUSTAIN at once)", regs, writes, 500)
    regs = v.patch_regs()
    regs["amp"] = (0,) + regs["amp"][1:]; regs["fenv"] = (0,) + regs["fenv"][1:]
    writes = [(0, "GATE", 0), (100, "GATE", 1)]                             # a_inc = 0: ATTACK holds the level
    add("extremes", "a_inc = 0 (the attack holds the current level)", regs, writes, 300)
    regs = v.patch_regs()
    regs["k"] = regs["gain"] = regs["ogain"] = 0
    writes = _note_writes(50, regs) + [(0, "GATE", 1)]
    add("extremes", "k = gain = ogain = 0", regs, writes, 200)

    # -- audition: the reference sequences of contract 16 (the first 0.8 s) --------
    if not q:
        import patches
        for name, seq, dur in patches.MONO:
            if name not in ("04-lead-glide", "07-growl-bass", "08-self-osc-whistle"):
                continue
            n = int(0.8 * SR)
            kw0 = dict(seq[0][3]); kw0.pop("blep", None); glide_on = bool(kw0.pop("glide", False)); kw0.pop("gate", None)
            regs = vf.VoiceFx.patch_regs(**kw0)
            host = vf.KeyHost(glide="always" if glide_on else "off")
            events = []
            for start, note, dd, kw in seq:
                g = kw.get("gate", None); g = dd * 0.8 if g is None else g
                on = int(start * SR); off = min(n - 1, on + max(1, int(g * SR)))
                if on < n: events.append((on, "on", note)); events.append((off, "off", note))
            add("audition", f"reference sequence {name} through KeyHost, first 0.8 s", regs, host.writes(events, regs), n)

    # -- f1cal: plan074 D. OPT-IN ONLY (it must be named in --only), so the
    # existing quick/full sets and their recorded frame counts do not move.
    # The calibrated operating point's words at the F1 cutoffs (res 0, drive
    # 1.0), then at resonance 1.0 and 1.1 (past self-oscillation onset) at
    # 1 kHz: the resonant/self-oscillating path the reciprocal ogain feeds.
    # REVSAW, not saw: with --filter2x, three 2x SAW oscillators plus the 2x
    # filter do not finish frame 0 inside this bench's budget (go at cycle 48
    # of 256; synth_top pulses go at cycle 8) -- "datapath still busy at the
    # end of frame 0", measured with the legacy words too, so it is not the
    # calibration. The ladder sees a sawtooth either way.
    if only is not None and "f1cal" in only:
        n = int((0.03 if q else 0.1) * SR)
        for i, (cut, res) in enumerate(((250, 0.0), (1000, 0.0), (4000, 0.0),
                                        (1000, 1.0), (1000, 1.1))):
            regs = v.patch_regs(waves=("revsaw", "revsaw", "revsaw"), detune=(0.0, 0.0, 0.0),
                                mix=(1.0, 0.0, 0.0), cutoff=(cut, cut), q=res, drive=1.0,
                                track=0.0, amp=(0.001, 0.25, 1.0, 0.05),
                                filter_calibration=F1CAL)
            writes = _note_writes(45, regs) + ([(0, "GATE", 1)] if i == 0 else [])
            add("f1cal", f"{F1CAL} at {cut} Hz, res {res}, drive 1.0 "
                         f"(gain {regs['gain']}, ogain {regs['ogain']})", regs, writes, n)
    return S


# ---- coverage from the model's trace ------------------------------------------------
def coverage(v: vf.VoiceFx, regs: dict, writes: list, phases0: list, trig, gate) -> str:
    t = v.trace
    notes = []
    win = both = pow2 = zero = 0
    for k, o in enumerate(v.oscs):
        inc = t["incs"][k]
        ph = (phases0[k] + np.concatenate([[0], np.cumsum(inc[:-1])])) & PHASE_MASK
        a = ph < inc; b = (vf.CYCLE - ph) < inc
        if o.shape in ("square", "pulse25"):
            ph2 = (ph + (vf.CYCLE - (vf.CYCLE // 2 if o.shape == "square" else vf.CYCLE // 4))) & PHASE_MASK
            a |= ph2 < inc; b |= (vf.CYCLE - ph2) < inc
        if o.blep:
            win += int((a | b).sum()); both += int((a & b).sum())
        pow2 += int(((inc > 0) & ((inc & (inc - 1)) == 0)).sum())
        zero += int((inc == 0).sum())
    acc = sum(t["osc"][k] * int(regs["weights"][k]) for k in range(3))
    acc = acc + t["noise"] * int(regs["weights"][3])
    mix_sat = int(((acc >> 15) > 32767).sum() + ((acc >> 15) < -32768).sum())
    out_v = (t["vca"] * int(regs["vol"])) >> 15
    out_sat = int((out_v > 32767).sum() + (out_v < -32768).sum())
    cut_lo = int((t["cut"] == vf.CUT_MIN).sum()); cut_hi = int((t["cut"] == vf.CUT_MAX).sum())
    keff_sat = int((t["k_eff"] == (1 << vf.K_BITS) - 1).sum())
    y_sat = int((np.abs(t["ladder"]) >= (1 << 18) - 1).sum())
    g_on = int(gate.sum()); n = len(gate)
    ae0_off = int(((gate == 0) & (t["amp_env"] == 0)).sum())
    moving = sum(int((np.diff(t["incs"][k]) != 0).sum()) for k in range(3))
    notes.append(f"blep windows {win}" + (f" (both {both})" if both else ""))
    if pow2: notes.append(f"power-of-two inc {pow2}")
    if zero: notes.append(f"inc=0 {zero}")
    if mix_sat: notes.append(f"mixer sat {mix_sat}")
    if out_sat: notes.append(f"rail {out_sat}")
    if cut_lo or cut_hi: notes.append(f"cut clamp lo {cut_lo} hi {cut_hi}")
    if keff_sat: notes.append(f"k_eff sat {keff_sat}")
    if y_sat: notes.append(f"ladder sat19 {y_sat}")
    if moving: notes.append(f"inc changes {moving}")
    if int(regs["weights"][3]):
        notes.append(f"noise w {int(regs['weights'][3])}" + (" (pink)" if regs.get("nsel") else " (white)"))
    if int(regs.get("mroute", 0)) & 3:
        mv = np.abs(t["mod_sig"]).max()
        notes.append(f"mod route {int(regs['mroute'])}, |mod_sig| max {int(mv)}, "
                     f"cut {int(t['cut'].min())}..{int(t['cut'].max())}")
    notes.append(f"gate on {g_on}/{n}, trig {int(trig.sum())}" + (f", ae=0 after gate-off {ae0_off}" if ae0_off else ""))
    return "; ".join(notes)


# ---- generate: run the model, write the writes and the expected taps -------------
def generate(outdir: str, which: str, only=None, verbose=True, oversample_2x=False,
             filter_2x=False, pulse_2x=False):
    v = vf.VoiceFx(oversample_2x=oversample_2x,
                   oversample_pulse_2x=pulse_2x,
                   rate_converted_ladder=filter_2x,
                   preserve_filter_headroom=filter_2x,
                   causal_filter=filter_2x,
                   pulse479_filter_candidate=filter_2x)
    v.reset()
    all_writes, expected, f0 = [], [], 0
    report = []
    for key, name, regs, writes, n in scenarios(which, only):
        phases0 = [o.phase for o in v.oscs]
        y = v.play(regs, writes, n)                     # the voice persists across scenarios
        t = v.trace
        pw = patch_to_writes(regs, f0)
        if F1CAL_FAULT == "LEGACY_WORDS" and regs.get("filter_calibration"):
            _, lg, log = vf.ladder_regs(regs["res"], regs["drive"])
            pw = [(f, fl, ad, lg if ad == A["GAIN"] else log if ad == A["OGAIN"] else d)
                  for f, fl, ad, d in pw]
        all_writes += pw + model_writes_to_regs(writes, f0)
        er = [[vf.recip_of(int(i)) for i in t["incs"][k]] for k in range(3)]
        vol = int(regs["vol"])
        for i in range(n):
            row = [int(y[i])] + [int(t["osc"][k][i]) for k in range(3)] + [int(t["incs"][k][i]) for k in range(3)] \
                + [er[k][i][0] + 15 for k in range(3)] + [er[k][i][1] for k in range(3)] \
                + [int(t["mixed"][i]), int(t["amp_env"][i]), int(t["filt_env"][i]), int(t["cut"][i]), int(t["g"][i]),
                   int(t["kc"][i]), int(t["k_eff"][i]), int(t["ladder"][i]), int(t["vca"][i]), (int(t["vca"][i]) * vol) >> 15] \
                + [int(t["phase2"][k][i]) for k in range(3)]
            expected.append(row)
        cov = coverage(v, regs, writes, phases0, t["trig"], t["gate"])
        report.append(dict(key=key, name=name, f0=f0, n=n, writes=len(writes), cov=cov))
        if verbose:
            print(f"  [{key}] {name}: frames {f0}..{f0 + n - 1} ({n}), {len(writes)} writes; {cov}")
        f0 += n
    state = [o.phase for o in v.oscs] + [o.inc_acc for o in v.oscs] + \
            [v.amp_env.level, v.filt_env.level, v.amp_env.seg, v.filt_env.seg] + list(v._os2_phase)
    os.makedirs(outdir, exist_ok=True)
    with open(os.path.join(outdir, "voice_writes.txt"), "w") as fh:
        fh.writelines("%d %d %d %d\n" % w for w in all_writes)
    with open(os.path.join(outdir, "voice_expected.txt"), "w") as fh:
        fh.writelines(" ".join(str(x) for x in row) + "\n" for row in expected)
        fh.write("STATE " + " ".join(str(x) for x in state) + "\n")
    if verbose:
        print(f"  {len(expected)} frames, {len(all_writes)} register writes; final amp level {state[6]}, "
              f"filt level {state[7]}")
    return expected, state, all_writes, report


# ---- compare: every field of every frame, then the state ---------------------------
def compare(expected, state, report, out_file, name="verify_voice") -> int:
    try:
        lines = [l.split() for l in open(out_file) if l.strip()]
    except OSError:
        print(f"{name}: no RTL output at {out_file}"); return 2
    rows = [l for l in lines if l[0] != "STATE"]
    st = [l for l in lines if l[0] == "STATE"]
    n = len(expected)
    if len(rows) < n or not st:
        print(f"{name}: RTL produced {len(rows)} of {n} frames{' and no STATE line' if not st else ''} -- did not run to completion")
        return 2
    mism = {f: 0 for f in FIELDS}
    first = {}
    per_scn = [[0, 0] for _ in report]                            # per segment: sample mismatches, tap mismatches
    scn_of = []
    for i, r in enumerate(report):
        scn_of += [i] * r["n"]
    cycles = []
    xs = 0
    for i in range(n):
        got = rows[i][1:1 + len(FIELDS)]
        cycles.append(int(rows[i][1 + len(FIELDS)]))
        for j, f in enumerate(FIELDS):
            g = got[j]
            try:
                gv = int(g)
            except ValueError:
                gv = None; xs += 1
            if gv != expected[i][j]:
                mism[f] += 1
                per_scn[scn_of[i]][0 if f == "sample" else 1] += 1
                if f not in first: first[f] = (i, expected[i][j], g, report[scn_of[i]]["key"])
    st_got = st[0][1:]
    st_mism = [(f, e, g) for f, e, g in zip(STATE_FIELDS, state, st_got) if str(e) != g]
    total = sum(mism.values())
    print(f"{name}: cycles from go to sample_valid: best {min(cycles)}, mean {sum(cycles) / len(cycles):.1f}, "
          f"worst {max(cycles)} (of 256 - 8 in the chip)")
    for r, (s, t) in zip(report, per_scn):
        print(f"  [{r['key']}] {r['n']} frames: {s} sample mismatches, {t} tap mismatches")
    if total == 0 and not st_mism:
        print(f"{name}: PASS -- {n} frames, every sample and every tap identical to the model; final state identical")
        return 0
    print(f"{name}: FAIL -- {mism['sample']} of {n} samples differ; tap mismatches: "
          + ", ".join(f"{f} {c}" for f, c in mism.items() if c and f != "sample")
          + (f"; {xs} undefined/X" if xs else ""))
    for f in FIELDS:
        if f in first:
            i, e, g, key = first[f]
            print(f"  first {f} mismatch at frame {i} [{key}]: model {e}, RTL {g}")
    if st_mism:
        print("  final state differs: " + ", ".join(f"{f} model {e} RTL {g}" for f, e, g in st_mism))
    return 1


def simulate(outdir: str, defines: list, rtl: str = None, timeout_s: float = 3600.0,
             simulator: str = "iverilog") -> str | None:
    import shutil
    iverilog, vvp = tool("iverilog"), tool("vvp")
    if simulator == "iverilog" and (not iverilog or not vvp):
        print("verify_voice: iverilog/vvp not on PATH (or set OSS_CAD_SUITE)"); return None
    vvp_file = os.path.join(outdir, "tb_voice.vvp")
    out_file = os.path.join(outdir, "voice_rtl_out.txt")
    if os.path.exists(out_file): os.remove(out_file)
    files = [rtl if (f == "voice_dp.v" and rtl) else f for f in RTL_FILES]
    if simulator == "verilator":
        verilator = shutil.which("verilator")
        if not verilator:
            print("verify_voice: Verilator not on PATH"); return None
        compiler = [verilator, "--binary", "--timing", "-Wno-fatal", "--top-module", "tb_voice",
                    "--Mdir", os.path.join(outdir, "obj_voice"), "-o", vvp_file]
        runner = [vvp_file]
    else:
        compiler = [iverilog, "-g2012", "-o", vvp_file]
        runner = [vvp, "-n", vvp_file]
    r = subprocess.run(compiler + [f"-D{d}" for d in defines] + files,
                       cwd=HERE, capture_output=True, text=True)
    if r.returncode != 0:
        print(f"verify_voice: {simulator} compile failed:\n" + r.stdout + r.stderr); return None
    try:
        r = subprocess.run(runner + [f"+wr={os.path.join(outdir, 'voice_writes.txt')}",
                            f"+exp={os.path.join(outdir, 'voice_expected.txt')}", f"+out={out_file}"],
                           cwd=HERE, capture_output=True, text=True, timeout=timeout_s)
    except subprocess.TimeoutExpired:
        print("verify_voice: simulation timed out"); return None
    sys.stdout.write("".join("  sim: " + l + "\n" for l in r.stdout.splitlines() if l.startswith("tb_voice")))
    if r.returncode != 0 or not os.path.exists(out_file):
        print(f"verify_voice: {simulator} run failed:\n" + r.stdout + r.stderr); return None
    return out_file


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--outdir", default=os.path.join(HERE, "build"))
    ap.add_argument("--simulator", choices=("iverilog", "verilator"), default="iverilog")
    ap.add_argument("--set", default="full", choices=("full", "quick"))
    ap.add_argument("--only", default=None, help="comma-separated scenario keys")
    ap.add_argument("--inject", default=None, choices=BUGS, help="INJECT_BUG_VOICE_<NAME> to compile in")
    ap.add_argument("--define", action="append", default=[], help="additional Verilog define")
    ap.add_argument("--osc2x", action="store_true",
                    help="enable the integrated 2x voice model and RTL path")
    ap.add_argument("--pulse2x", action="store_true", help="select 2x rectangular oscillators")
    ap.add_argument("--filter2x", action="store_true",
                    help="enable causal reconstructed 2x filter and the pulse-duty challenger")
    ap.add_argument("--expect-fail", action="store_true")
    ap.add_argument("--rtl", default=None, metavar="FILE", help="simulate FILE in place of voice_dp.v")
    ap.add_argument("--compare-only", default=None, metavar="FILE")
    ap.add_argument("--f1cal-fault", default=None, choices=("LEGACY_WORDS",),
                    help="with --only f1cal: deliver the legacy gain/ogain words while the "
                         "image names the calibration; must mismatch (use --expect-fail)")
    a = ap.parse_args(argv)
    global F1CAL_FAULT
    F1CAL_FAULT = a.f1cal_fault
    a.outdir = os.path.abspath(a.outdir)              # the bench runs with cwd = rtl-sketch
    if "VOICE_OSC_2X" in a.define:
        ap.error("use --osc2x to enable the model and RTL together; do not pass VOICE_OSC_2X via --define")
    if a.filter2x or a.pulse2x:
        a.osc2x = True
    only = set(a.only.split(",")) if a.only else None
    print(f"verify_voice: model VoiceFx() (contract rev 4), scenario set '{a.set}'"
          + (f", only {sorted(only)}" if only else ""))
    expected, state, writes, report = generate(a.outdir, a.set, only,
                                               oversample_2x=a.osc2x,
                                               filter_2x=a.filter2x, pulse_2x=a.pulse2x)
    if a.compare_only:
        status = compare(expected, state, report, a.compare_only)
    else:
        defines = list(a.define) + (["VOICE_OSC_2X"] if a.osc2x else [])
        if a.filter2x:
            defines.append("VOICE_FILTER_2X")
        if a.pulse2x:
            defines.append("VOICE_PULSE_2X")
        if a.inject:
            defines.append(f"INJECT_BUG_VOICE_{a.inject}")
        rtl = os.path.relpath(os.path.abspath(a.rtl), HERE) if a.rtl else None
        print(f"verify_voice: simulating {rtl or 'voice_dp.v'} ({', '.join(RTL_FILES[2:])}; "
              f"defines {', '.join(defines) or '(none)'}), {len(expected)} frames, "
              f"{len(writes)} writes at the register port")
        out = simulate(a.outdir, defines, rtl, simulator=a.simulator)
        status = 2 if out is None else compare(expected, state, report, out)
    if a.expect_fail:
        if status == 1:
            print(f"verify_voice: negative control {a.inject or ''} CAUGHT (comparison failed as required)")
            return 0
        print(f"verify_voice: NEGATIVE CONTROL NOT CAUGHT (status {status})")
        return 1 if status == 0 else 2
    return status


if __name__ == "__main__":
    sys.exit(main())
