"""Fixed-point model of the WHOLE CHIP at its top-level boundary --
rtl-sketch/synth_top.v's specification, the way model/voice_fx.py is
voice_dp.v's, model/drums_fx.py is drum_kit.v's and model/fixed.py is the
ladder's.

Every block in this design is bit-exact against a model of that block. None of
that says anything about what happens BETWEEN the blocks, and both integration
defects found so far (a drum mix bug and a gating bug) were found by listening,
not by a test. A third was found by writing this one: DR 0007 revision 1's
32-bit control frame could not carry 118 of the 155 register writes these two
models perform (rtl-sketch/verify_ctl.py).

WHAT IT MODELS, in the order synth_top.v runs it (ARCHITECTURE.md section 5):

  1. the register port. Writes reach the datapath in the drain at cycles 2..5,
     before `go` at cycle 8, so at frame granularity a write is atomic and
     lands at the START of its frame. Which frame that is, is the link's
     business; rtl-sketch/verify_ctl.py verifies the link separately and
     rtl-sketch/verify_synth_top.py derives the landing frame from the CS_N
     pin event and checks the chip agrees.
  2. the drum section (model/drums_fx.py, DrumsFx) on the SEC = 1 page: the
     REAL engine, not a placeholder. Two buses leave it, `dmix` (21 bits) and
     `body` (19 bits).
  3. the voice (model/voice_fx.py, VoiceFx) on the SEC = 0 page -- oscillators,
     mixer, envelopes, cutoff, ladder context 0, VCA -- taken UNSATURATED at
     `v`. This is a real difference from the voice alone: VoiceFx.play()
     returns sat16((v*vol)>>15) because the voice's own output is the chip's
     output when there is nothing else on the bus, but in the chip `v` is a
     19-bit word that meets the drum buses BEFORE the rail (contract 12).
  4. the drum gains and the optional drum filter: dacc = dmix*dvol +
     body*bvol exactly; with ROUTE.DFILT that word goes through ladder
     context 1 as sat16(dacc >> 15), with its own DCUT/DK/DGAIN/DOGAIN --
     and DCUT through the same g and kc ROMs the voice uses.
  5. the master mix, contract 12 / DR 0008:
         sample = sat16((v*vol + dmix*dvol + body*bvol) >> 15)
     ONE exact sum of three products, ONE shift, ONE clamp. Shifting each
     product before the sum is two floors instead of one and a different
     circuit; clamping either bus to 16 bits before its gain throws away
     headroom the gain could have recovered. Both orderings are what this
     model pins, and both have negative controls in voice_dp.v.
     With ROUTE.DFILT the two drum terms are replaced by the filter's output
     word at unity, d19 << 15 (ARCHITECTURE.md 4.1); DOGAIN is that path's
     level.
  6. the I2S stream: the sample strobed in frame f is transmitted in LRCLK
     period f+1 (contract 13, D = 1) on both channels.

Register map: DR 0007 revision 2 section 3 (SEC = 0) and contract 15.1
(SEC = 1). A write is (frame, flag, sec, addr, data).

HOW THE VOICE IS DRIVEN, and why it is exact. VoiceFx.play(regs, ops, n)
applies a whole register image at its frame 0 and a small set of per-frame
ops (INC, TRACK, GATE, TRIG, GLIDE) at the frames they name, and it keeps its
state -- oscillators, envelopes, ladder -- across calls. So this model walks
the write stream, cuts it into SEGMENTS at every frame where a register the
image covers changes, and plays one segment per image. The result is the
voice run register-write by register-write, at frame granularity, with no
assumption that the programming period is silent.
"""
from __future__ import annotations
import os, sys
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "audition"))
import numpy as np
import voice_fx as vf
import drums_fx as dx
from voice_fx import VoiceFx, LADDER_CFG, CUT_MIN, CUT_MAX
from fixed import LadderFx

SEC_VOICE, SEC_DRUM = 0, 1
WAVE_NAME = {v: k for k, v in vf.WAVE_CODE.items()}              # 9..15 all sine (DR 0007 s.3)

# ---- the SEC = 0 page (DR 0007 revision 2 section 3) --------------------------
A_INC, A_WAVE, A_W, A_WN = 0x00, 0x04, 0x08, 0x0B
A_GLIDE, A_VOL, A_DVOL, A_ROUTE = 0x0C, 0x0D, 0x0E, 0x0F
A_AMP, A_FILT = 0x10, 0x14
A_CUT_LO, A_CUT_HI, A_TRACK, A_NSEL = 0x18, 0x19, 0x1A, 0x1B
A_K, A_GAIN, A_OGAIN, A_MROUTE = 0x1C, 0x1D, 0x1E, 0x1F
A_MMIX, A_MWHEEL, A_MPD, A_MFD = 0x24, 0x25, 0x26, 0x27
A_GATE_ON, A_GATE_OFF, A_TRIG, A_RESET = 0x20, 0x21, 0x22, 0x23
A_DCUT, A_DK, A_DGAIN, A_DOGAIN, A_BVOL = 0x28, 0x29, 0x2A, 0x2B, 0x2C
A_NOP = 0x3F

# the widths the register keeps (DR 0007 section 3); the datum is 32 bits and
# the register takes its low bits, so the model must mask exactly as the RTL does
W24, W20, W17, W16, W15, W4, W3, W1 = (1 << 24) - 1, (1 << 20) - 1, (1 << 17) - 1, 0xFFFF, 0x7FFF, 15, 7, 1


def sat16(v):
    return np.clip(v, -32768, 32767)


class SynthTopModel:
    """One chip. `run(writes, n)` plays n frames with `writes` -- a list of
    (frame, flag, sec, addr, data) applied at the START of the frames they
    name, in list order -- and returns every signal a bench can see."""

    def __init__(self, *, oversample_2x: bool = False, filter_2x: bool = False):
        self.oversample_2x = oversample_2x or filter_2x
        self.voice = VoiceFx(oversample_2x=self.oversample_2x,
                             rate_converted_ladder=filter_2x,
                             preserve_filter_headroom=filter_2x,
                             causal_filter=filter_2x,
                             pulse479_filter_candidate=filter_2x)
        self.drums = dx.DrumsFx()
        self.dl = LadderFx(**LADDER_CFG)              # ladder context 1: the drum filter
        self.base = VoiceFx.patch_regs()              # for `res`/`drive`, which the register
        self.reset()                                  #   path never uses (g and k come as regs)

    # ---- contract 14 / 15.8: the reset pin, and the two soft resets ----------
    def reset(self):
        self.voice.reset()
        self.drums.reset()
        self.dl = LadderFx(**LADDER_CFG)          # ladder context 1 has no separate reset port
        self.img = dict(waves=[0, 0, 0], weights=[0, 0, 0, 0],
                        nsel=0, mmix=0, mwheel=0, mpd=0, mfd=0, mroute=0,
                        amp=[0, 0, 0, 0], fenv=[0, 0, 0, 0],
                        cut_lo=0, cut_hi=0, k=0, gain=0, ogain=0,
                        vol=0, dvol=0, bvol=0, glide=0, route=0,
                        dcut=0, dk=0, dgain=0, dogain=0)

    def _regs(self) -> dict:
        r = dict(self.base)
        i = self.img
        r.update(waves=[WAVE_NAME.get(w, "sine") for w in i["waves"]],
                 weights=list(i["weights"]), amp=list(i["amp"]), fenv=list(i["fenv"]),
                 cut_lo=i["cut_lo"], cut_hi=i["cut_hi"], k=i["k"], gain=i["gain"],
                 ogain=i["ogain"], vol=i["vol"], glide=i["glide"],
                 nsel=i["nsel"], mmix=i["mmix"], mwheel=i["mwheel"],
                 mpd=i["mpd"], mfd=i["mfd"], mroute=i["mroute"])
        return r

    # ---- one SEC = 0 write: exactly voice_dp.v's decode ----------------------
    def _write_voice(self, flag: int, addr: int, data: int):
        """Returns ("op", ...) for a write play() takes per frame, "image" for
        one that changes the register image (and so cuts a segment), "reset"
        for RESET, or None for a write with no effect."""
        i = self.img
        if addr <= 0x02:
            return ("INC", addr & 3, data & W24, bool(flag))
        if A_WAVE <= addr <= A_WAVE + 2:
            i["waves"][addr - A_WAVE] = data & W4; return "image"
        if A_W <= addr <= A_W + 2:
            i["weights"][addr - A_W] = data & W16; return "image"
        if addr == A_WN:
            i["weights"][3] = data & W16; return "image"            # the noise source (6.10)
        if addr == A_GLIDE:
            i["glide"] = data & W24; return ("GLIDE", data & W24)
        if addr == A_VOL:   i["vol"]   = data & W16; return "image"
        if addr == A_DVOL:  i["dvol"]  = data & W16; return "image"
        if addr == A_BVOL:  i["bvol"]  = data & W16; return "image"
        if addr == A_ROUTE: i["route"] = data & W1;  return "image"
        if A_AMP <= addr <= A_AMP + 3:
            j = addr - A_AMP
            i["amp"][j] = data & W24; return "image"
        if A_FILT <= addr <= A_FILT + 3:
            j = addr - A_FILT
            i["fenv"][j] = data & W24; return "image"
        if addr == A_CUT_LO: i["cut_lo"] = data & W16; return "image"
        if addr == A_CUT_HI: i["cut_hi"] = data & W16; return "image"
        if addr == A_TRACK:  return ("TRACK", data & W16)
        if addr == A_K:      i["k"]     = data & W17; return "image"
        if addr == A_GAIN:   i["gain"]  = data & W20; return "image"
        if addr == A_OGAIN:  i["ogain"] = data & W20; return "image"
        if addr == A_GATE_ON:  return ("GATE", 1)
        if addr == A_GATE_OFF: return ("GATE", 0)
        if addr == A_TRIG:     return ("TRIG",)
        if addr == A_NSEL:   i["nsel"]   = data & W1;  return "image"
        if addr == A_MROUTE: i["mroute"] = data & W3;  return "image"
        if addr == A_MMIX:   i["mmix"]   = data & W16; return "image"
        if addr == A_MWHEEL: i["mwheel"] = data & W16; return ("MWHEEL", data & W16)
        if addr == A_MPD:    i["mpd"]    = data & W16; return "image"
        if addr == A_MFD:    i["mfd"]    = data & W16; return "image"
        if addr == A_RESET:    return "reset"
        if addr == A_DCUT:   i["dcut"]   = data & W16; return "image"
        if addr == A_DK:     i["dk"]     = data & W17; return "image"
        if addr == A_DGAIN:  i["dgain"]  = data & W20; return "image"
        if addr == A_DOGAIN: i["dogain"] = data & W20; return "image"
        return None                                     # NOP, reserved: no effect

    # ---- the run ------------------------------------------------------------
    def run(self, writes, n: int) -> dict:
        writes = sorted(((int(f), int(fl), int(sc), int(a) & 0xFF, int(d) & 0xFFFFFFFF)
                         for f, fl, sc, a, d in writes), key=lambda w: w[0])
        for f, *_ in writes:
            assert 0 <= f < n, f"write at frame {f} outside 0..{n-1}"

        # 2. the drum section, register-exact by construction
        dwrites = [(f, a, d) for f, fl, sc, a, d in writes if sc == SEC_DRUM]
        dmix, body = self.drums.play(dwrites, n)
        dmix = np.asarray(dmix, dtype=np.int64)
        body = np.asarray(body, dtype=np.int64)

        # 3. the voice, segment by segment, plus the per-frame master registers
        vw = [w for w in writes if w[2] == SEC_VOICE]
        by_frame: dict[int, list] = {}
        for f, fl, sc, a, d in vw:
            by_frame.setdefault(f, []).append((fl, a, d))

        v = np.zeros(n, dtype=np.int64)
        vol = np.zeros(n, dtype=np.int64)
        dvol = np.zeros(n, dtype=np.int64)
        bvol = np.zeros(n, dtype=np.int64)
        route = np.zeros(n, dtype=np.int64)
        dcut = np.zeros(n, dtype=np.int64)
        dk = np.zeros(n, dtype=np.int64)
        dgain = np.zeros(n, dtype=np.int64)
        dogain = np.zeros(n, dtype=np.int64)

        cuts = sorted(by_frame)
        boundaries = sorted(set([0] + cuts + [n]))
        # walk the segments: [b, b_next) with one constant image
        for bi, b in enumerate(boundaries[:-1]):
            bnext = boundaries[bi + 1]
            ops = []
            for fl, a, d in by_frame.get(b, []):
                r = self._write_voice(fl, a, d)
                if r == "reset":
                    self.voice.reset()                  # contract 14: the voice's state AND image
                    self.reset_image_only()
                elif isinstance(r, tuple):
                    ops.append((0,) + r)                # relative to this segment's frame 0
            m = bnext - b
            if m <= 0:
                continue
            out = self.voice.play(self._regs(), ops, m)
            v[b:bnext] = self.voice.trace["vca"]
            i = self.img
            vol[b:bnext] = i["vol"]; dvol[b:bnext] = i["dvol"]; bvol[b:bnext] = i["bvol"]
            route[b:bnext] = i["route"]; dcut[b:bnext] = i["dcut"]; dk[b:bnext] = i["dk"]
            dgain[b:bnext] = i["dgain"]; dogain[b:bnext] = i["dogain"]

        # 4. the drum gains, exact (contract 12: no shift and no clamp here)
        dacc = dmix * dvol + body * bvol

        # 5. the drum filter on the runs of frames where ROUTE.DFILT is set
        d19 = np.zeros(n, dtype=np.int64)
        if route.any():
            dq = dacc >> 15                              # arithmetic, numpy int64
            dxin = sat16(dq).astype(np.int16)
            cut = np.clip(dcut, CUT_MIN, CUT_MAX)
            g = vf.g_from_cut(cut, self.voice.g_rom, self.voice.GB)
            kc = vf.kc_from_cut(cut, self.voice.k_rom, self.voice.KB)
            keff = vf.k_effective(dk, kc)
            s = 0
            while s < n:
                if not route[s]:
                    s += 1; continue
                e = s
                while e < n and route[e]:
                    e += 1
                seg = self.dl.process(dxin[s:e], None, 0.0, 1.0, g_q16=g[s:e],
                                      k=int(dk[s]), gain=int(dgain[s]), ogain=int(dogain[s]),
                                      k_q14=keff[s:e])
                d19[s:e] = np.asarray(seg, dtype=np.int64)
                s = e

        # 6. the master mix: one exact sum, one shift, one clamp
        acc = v * vol + np.where(route != 0, d19 << 15, dacc)
        sample = sat16(acc >> 15).astype(np.int64)

        # 7. the I2S stream: the sample strobed in frame f is sent in period f+1
        i2s = np.zeros(n, dtype=np.int64)
        i2s[1:] = sample[:-1]

        return dict(sample=sample, i2s=i2s, v=v, dmix=dmix, body=body, dacc=dacc,
                    d19=d19, vol=vol, dvol=dvol, bvol=bvol, route=route)

    def reset_image_only(self):
        """The SEC = 0 RESET (0x23) zeroes the voice's registers and state and
        leaves the drum section alone; the SEC = 1 RESET (0xFF) does the
        converse, and DrumsFx.write handles it."""
        self.img = dict(waves=[0, 0, 0], weights=[0, 0, 0, 0],
                        nsel=0, mmix=0, mwheel=0, mpd=0, mfd=0, mroute=0,
                        amp=[0, 0, 0, 0], fenv=[0, 0, 0, 0],
                        cut_lo=0, cut_hi=0, k=0, gain=0, ogain=0,
                        vol=0, dvol=0, bvol=0, glide=0, route=0,
                        dcut=0, dk=0, dgain=0, dogain=0)
