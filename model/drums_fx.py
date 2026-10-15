"""Fully integer drum section, TR-808-shaped: the executable specification of
contract section 15 (DR 0008). Nothing evaluated per frame is float; float is
allowed only in the host conversions at the bottom (Hz, seconds and levels to
register values), the same rule as model/voice_fx.py.

What the 808 is, per docs/tr808-reference.md, and what that makes this block:

  * BD, SD, toms are BRIDGED-T RESONATORS pinged by a pulse and left to ring:
    two-pole resonators, i.e. modes of the modal bank (model/modal_fixed.py).
    They are coefficient presets, not hardware. Nothing resets their state
    at a hit; a hit while ringing interferes with the ring, as the circuit
    does (reference 2, "retrigger while ringing").
  * CH, OH, CB are SIX SQUARE-WAVE OSCILLATORS summed to a 7-level staircase
    -- not noise -- through a band-pass, an asymmetric "swing" VCA and a
    high-pass. The band-pass and high-passes are modes of the same bank with
    a numerator selected (BP: 1 - z^-2, HP: (1 - z^-1)^2); the six squares
    are phase accumulators; the swing VCA is x4 on the positive half, /8 on
    the negative, through the ladder's tanh table (contract Appendix C).
  * SD's snap and CP are WHITE NOISE from one shared source (the 808 has one
    noise generator) through a high-pass / band-pass mode and an envelope;
    the clap's envelope is three bursts 10 ms apart plus a tail.

So the block is: 8 edge-triggered stops -> 12 exponential-decay envelopes
(fired by a stop, optionally held, re-struck for bursts, choked by another
stop) -> 16 routing PATHS, each `v = nl(source) * (env_a + env_b) >> (15 +
att)` on ONE multiplier, summed exactly into the mix bus or into one mode's
excitation -> the modal bank (12 modes, the first 6 with numerators) -> the
body bus. Two buses leave: `dmix` (21-bit exact sum of the paths routed to
MIX) and `body` (the bank's 19-bit Q4.15 word). The instrument's output stage
(`output_fx`) sums them with the voice under three gains and clamps ONCE
(contract 12): there is no clip inside this block other than the modal state
word's sat28 (part of the bank's arithmetic since rev 1) and the tap's rail.

Formats (contract 15.1):
    stops       8 bits; a 0->1 change between consecutive frames fires
    accent      Q0.15 per stop, 16 bits (32768 = 1.0; 65535 = 2.0 is legal)
    envelope    level 24-bit unsigned Q0.24; peak 24; rate Q0.16; hold 8 bits
                (frames); bursts 2 bits; period 9 bits; t 11-bit frame counter
                decay: level -= max(1, (level * rate) >> 16) -- the voice's
                release rule, dead zone closed by the max(1, .), section 8.3
    path        src 5 bits, e1/e2 4 bits, nl 2, att 3, dest 4 (22 bits)
    sources     Q1.15: NOISE (16 LFSR bits per frame), SQSUM (six squares,
                +-5461 each), PULSE (32767), SQPAIR (squares 4 + 5, +-16383
                each), SQ i (one square alone, +-16383, i = 0..5 at src 5..10),
                TAP m = sat16(y1[m] >> 3), the mode's state / 8
    envsum      ENV(e1) + ENV(e2), ENV(e) = level >> 9; index 15 reads 32767
    v           17 bits; dmix and every exc exact, 21 bits
    LFSR        31 bits, x^31 + x^15 + x^13 + x^11 + 1, seed 1, 16 steps per frame
    oscillators 6 x 24-bit phase accumulators, host-written increments
    bank        ModalFx(modes=12, nums=6, headroom=0, out_bits=19)
"""
from __future__ import annotations
import dataclasses
import math
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "audition"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numpy as np
from dsp import SR, PHASE_BITS, PHASE_MASK, phase_inc
from fixed import LadderFx, sat, usat
from voice_fx import LADDER_CFG, VOL_REF
import modal_fixed
from modal_fixed import ModalFx, RAW, BP, HP, pole_regs

# ---- sizes (contract 15.1) ----------------------------------------------------
N_STOPS, N_ENV, N_PATH, N_MODES, N_NUMS, N_OSC = 11, 18, 23, 16, 11, 6
ENV_BITS, RATE_Q, ACCENT_BITS = 24, 16, 16
HOLD_BITS, BURST_BITS, PERIOD_BITS, T_BITS = 8, 2, 9, 11
T_MAX = (1 << T_BITS) - 1
BURST_C = 53248                  # 13/16 in Q0.16: each re-strike is 13/16 of the last (15.3)
LFSR_BITS, LFSR_SEED = 31, 1     # x^31 + x^15 + x^13 + x^11 + 1 (15.4)
LFSR_TAPS = (30, 15, 17, 19)     # state bits XORed for the new bit: delays 31, 16, 18, 20
LFSR_MASK = (1 << LFSR_BITS) - 1
NOISE_BITS = 16                  # LFSR steps per frame = bits per noise word
SQ_STEP, SQPAIR_STEP = 5461, 16383   # six squares sum to +-32766; the pair to +-32766
SQPAIR = (4, 5)                  # the 808's trimmed oscillators 5 and 6 (800 and 540 Hz)
TAP_SHIFT = 3                    # TAP m = sat16(y1[m] >> 3): the state / 8, rails at +-8.0 (15.5)
SRC_OFF, SRC_NOISE, SRC_SQSUM, SRC_PULSE, SRC_SQPAIR, SRC_SQ, SRC_TAP = 0, 1, 2, 3, 4, 5, 16
NL_LIN, NL_SWING, NL_TANH = 0, 1, 2
# Revision 10 widens the path word's envelope and destination fields to 5 bits
# each (22 -> 25 bits). At revision 8 both were 4 bits, which put a hard
# ceiling of 12 addressable envelopes and 15 addressable modes on the block --
# DEST_MIX was 15 and so was the last mode, and the two collided the moment
# MODES reached 16. The sentinels move with the fields: an envelope index of
# ENV_FULL reads full scale, any other index at or above N_ENV reads zero, and
# a destination of DEST_MIX is the mix bus.
ENV_NONE = 30                    # any index >= N_ENV that is not ENV_FULL
DEST_MIX, ENV_FULL = 31, 31
MIX_BITS = 22                    # 23 paths x 17-bit values, exact
BODY_HR, BODY_BITS = 0, modal_fixed.OUT_BITS
FULL24 = (1 << ENV_BITS) - 1

# ---- the register map (contract 15.1): 8-bit address, 32-bit value ----------
# ENV grew to 18 entries (0x40..0x87) and MODE to 16 (64 bytes), so PATH moved
# from 0x80 to 0x90 and MODE from 0xC0 to 0xB0: at 0xC0 the last mode's `num`
# register would have been 0xFF, which is RESET.
A_STOPS, A_ACCENT, A_OSC, A_ENV, A_PATH, A_MODE, A_RESET = 0x00, 0x10, 0x20, 0x40, 0x90, 0xB0, 0xFF
# Revision 16 (#551, contract 15.10): the shared-bus DC coupling's enable. 0x30 is in
# the unused gap 0x26..0x3F, 10 addresses clear of OSC_INC (..0x25) and 16 below ENV
# (0x40..0x87), so neither block can grow into it by one or two entries; the gaps
# 0x88..0x8F and 0xA7..0xAF were rejected because ENV and PATH have each grown
# already and 0x88 is the very next address after ENV's last register.
A_COUPLE = 0x30
ENV_STRIDE, MODE_STRIDE = 4, 4   # ENV: +0 ctl, +1 peak, +2 rate, +3 frate; MODE: +0 a1, +1 a2, +2 amp, +3 num
REG_BITS = dict(stops=N_STOPS, accent=ACCENT_BITS, osc_inc=PHASE_BITS, env_ctl=27, peak=ENV_BITS,
                rate=RATE_Q, frate=RATE_Q, path=25, a1=26, a2=26, amp=16, num=2)


def env_ctl(stop: int, choke: int = 15, hold: int = 0, bursts: int = 0, period: int = 0) -> int:
    """ENV_CTL word: [3:0] stop, [7:4] choke, [15:8] hold, [17:16] bursts, [26:18] period.
    A stop or choke index >= 8 means never."""
    return ((usat(stop, 4)) | (usat(choke, 4) << 4) | (usat(hold, HOLD_BITS) << 8)
            | (usat(bursts, BURST_BITS) << 16) | (usat(period, PERIOD_BITS) << 18))


def path_word(src: int, e1: int, e2: int = ENV_NONE, nl: int = NL_LIN, att: int = 0,
              dest: int = DEST_MIX) -> int:
    """PATH word (25 bits, revision 10): [4:0] src, [9:5] e1, [14:10] e2,
    [16:15] nl, [19:17] att, [24:20] dest. An envelope index of ENV_FULL (31)
    reads as full scale and any other index >= N_ENV as zero, so e2 = ENV_NONE
    is 'no second envelope'; dest = DEST_MIX (31) is the mix bus."""
    return (usat(src, 5) | (usat(e1, 5) << 5) | (usat(e2, 5) << 10) | (usat(nl, 2) << 15)
            | (usat(att, 3) << 17) | (usat(dest, 5) << 20))


def s26(v: int) -> int:
    """A 26-bit two's-complement field as a signed integer."""
    v &= (1 << 26) - 1
    return v - (1 << 26) if v & (1 << 25) else v


# ---- the noise source (contract 15.4) -------------------------------------------
def lfsr_frame(state: int) -> tuple[int, int]:
    """One frame of the LFSR: 16 steps of
        s <- (s << 1) | (s[30] ^ s[15] ^ s[17] ^ s[19])
    on a 31-bit state, the recurrence b[n] = b[n-31] + b[n-16] + b[n-18] +
    b[n-20] over GF(2), whose characteristic polynomial x^31 + x^15 + x^13 +
    x^11 + 1 is primitive (2^31 - 1 is prime, so irreducible is enough;
    test_drums_fx checks it). Returns (new state, noise word): the 16 bits
    shifted in, oldest first, read as a signed Q1.15 value. Every bit is one
    output of a maximal-length sequence and consecutive words are disjoint
    16-bit windows of it. Period 2^31 - 1 bits, 12.4 h.

    Why a pentanomial and not the strawman's trinomial: from the sparse
    reset state a trinomial LFSR's ones density recovers slowly (the
    Mersenne Twister's zero-excess problem): x^31 + x^3 + 1 measured a
    +351 mean over the first 200 000 words and +117 over the next 100 000,
    a 0.5 % ones deficit; this polynomial measures -5 and -29, inside a
    uniform source's fluctuation. All four taps are at bit 15 or above, so
    the 16 new bits of a frame are a function of the old state alone."""
    s = state
    for _ in range(NOISE_BITS):
        bit = 0
        for t in LFSR_TAPS:
            bit ^= (s >> t) & 1
        s = ((s << 1) | bit) & LFSR_MASK
    w = s & 0xFFFF
    return s, (w - 0x10000 if w & 0x8000 else w)


def lfsr_frame_leap(state: int) -> tuple[int, int]:
    """The same 16 steps computed at once, as the RTL does: new bit i (i = 0
    the first) is the XOR of s[t - i] over the taps, all from the OLD state
    since every tap is at bit 15 or above."""
    bits = 0
    for i in range(NOISE_BITS):
        b = 0
        for t in LFSR_TAPS:
            b ^= (state >> (t - i)) & 1
        bits = (bits << 1) | b
    s = ((state << NOISE_BITS) | bits) & LFSR_MASK
    return s, (bits - 0x10000 if bits & 0x8000 else bits)


# ---- one envelope (contract 15.3) ------------------------------------------------
class EnvFx:
    """Exponential decay with a fire, an optional hold, optional re-strikes
    and a choke. Per frame, before the paths read it:

        fired (its stop went 0->1 this frame):
            level <- usat24((peak * accent[stop]) >> 15); strike <- level; t <- 0
        else:
            t <- min(t + 1, 2047)
            t < hold:                        level unchanged (the 1 ms pulse)
            bursts >= k and t == k * period  (k = 1, 2, 3):
                                             strike <- (strike * 53248) >> 16; level <- strike
            otherwise:                       dec <- (level * rate) >> 16
                                             level <- max(0, level - max(1, dec))
        choked (its choke stop went 0->1 this frame): level <- 0
        ENV = level >> 9                      (Q0.15, what the paths multiply by)

    THE FINAL STRIKE (contract revision 14, 15.3; plan084, the clap's L2).
    With FRATE = 0 -- the reset value, and every envelope but the clap's burst
    -- nothing below changes and the envelope is bit-identical to revision 10.
    With FRATE != 0:
        fired:     fcap <- level (the accent-scaled fire level, captured ONCE)
        re-strike k == bursts (the LAST one):
                   strike <- fcap; level <- strike       (not 13/16 of the last)
        decay once t > bursts * period:  rate FRATE instead of RATE
        choked:    fcap <- 0 as well, so no final strike can follow a choke
    PEAK and ACCENT are read only when the envelope fires, so a mid-note write
    of either cannot change the final strike; RATE and FRATE are read on every
    decay frame, so a mid-note write of either applies from the next frame.
    `fcap` is state, cleared by RESET.

    The `max(1, dec)` is the voice's (8.3): below level = 2^16 / rate the
    product truncates to zero and the tail would never end; with it the tail
    below that floor is one LSB per frame and reaches exactly zero. `floor`
    exists to measure that, like LadderFx's `interp`."""
    __slots__ = ("stop", "choke", "hold", "bursts", "period", "peak", "rate", "frate",
                 "level", "strike", "t", "fcap", "floor", "n_fire", "n_choke", "n_restrike",
                 "n_final", "n_floor")

    def __init__(self, floor: bool = True):
        self.stop = self.choke = self.hold = self.bursts = self.period = 0
        self.peak = self.rate = self.frate = 0
        self.floor = floor
        self.n_fire = self.n_choke = self.n_restrike = self.n_final = self.n_floor = 0   # coverage, not state
        self.reset()

    def reset(self):
        self.level = self.strike = self.t = self.fcap = 0

    def set_ctl(self, word: int):
        self.stop, self.choke = word & 15, (word >> 4) & 15
        self.hold, self.bursts, self.period = (word >> 8) & 255, (word >> 16) & 3, (word >> 18) & 511

    def frame(self, fire: int, accents):
        if self.stop < N_STOPS and (fire >> self.stop) & 1:
            self.level = usat((self.peak * accents[self.stop]) >> 15, ENV_BITS)
            self.strike = self.level
            self.fcap = self.level
            self.t = 0
            self.n_fire += 1
        else:
            self.t = min(self.t + 1, T_MAX)
            t = self.t
            last = self.bursts * self.period
            if t < self.hold:
                pass
            elif self.period and any(t == k * self.period for k in (1, 2, 3) if k <= self.bursts):
                if self.frate and t == last:
                    self.strike = self.fcap                  # the final strike: the fire level
                    self.n_final += 1
                else:
                    self.strike = (self.strike * BURST_C) >> 16
                self.level = self.strike
                self.n_restrike += 1
            else:
                rate = self.frate if (self.frate and t > last) else self.rate
                dec = (self.level * rate) >> RATE_Q
                if dec == 0:
                    self.n_floor += self.level > 0
                    if self.floor:
                        dec = 1
                self.level = max(0, self.level - dec)
        if self.choke < N_STOPS and (fire >> self.choke) & 1:
            self.level = 0
            self.fcap = 0
            self.n_choke += 1

    @property
    def out(self) -> int:
        return self.level >> (ENV_BITS - 15)

    @property
    def floor_level(self) -> int:
        """Below this level the exponential step is zero and the tail is
        linear at one LSB per frame: 2^16 / rate (rate = 0: everything)."""
        return (1 << RATE_Q) // self.rate + 1 if self.rate else FULL24


# ---- output coupling: the DC blocker (#165, EXPERIMENTAL -- default OFF) -----
# The block has no DC blocking anywhere (#152). `SRC_PULSE` is +32767 gated by
# an envelope and never changes sign (mean/|mean| = 1.000 exactly), `NL_SWING`
# rectifies a bipolar source into a positive-mean one, and the all-pole (RAW)
# modes those drive have DC gains of 18 to 23,899. The machine does not: every
# voice leaves its circuit through a coupling capacitor.
#
# THE CORNER IS THE MACHINE'S, NOT A TASTE. The BD's output buffer Q44 couples
# through C49 0.47 uF into the bias divider R176 100 kOhm || R177 82 kOhm =
# 45.05 kOhm (reference 2, "Topology", verified: W14a/SN p.9). That is
#     f = 1 / (2*pi*C*R) = 1 / (2*pi * 0.47e-6 * 45050) = 7.52 Hz,
# and a one-pole blocker with a = 1 - 2^-10 sits at SR/(2*pi*2^10) = 7.457 Hz
# -- 0.8 % low, which is 25x inside the +-20 % capacitor tolerance reference 2
# quotes for the voice circuits. K is therefore READ OFF THE CIRCUIT: no knob
# was turned to choose it. (If the divider is not the AC load the corner is
# 1.86-3.39 Hz instead, i.e. K = 11 or 12; `couple_k` sweeps it and the
# measured cost of each is in tools/probes/dc_blocker.py.)
COUPLE_OFF, COUPLE_EXC, COUPLE_BUS, COUPLE_POST = "none", "exc", "bus", "post"
COUPLE_PLACEMENTS = (COUPLE_OFF, COUPLE_EXC, COUPLE_BUS, COUPLE_POST)
COUPLE_K = 10                    # pole 1 - 2^-K; 7.457 Hz at SR = 48 kHz


def signed_bits(v: int) -> int:
    """Width of the narrowest two's-complement word holding v."""
    v = int(v)
    return (v if v >= 0 else ~v).bit_length() + 1


class DcBlockFx:
    """One-pole DC blocker, integer, one register and two adders:

        d   <- acc >> K                 (the registered estimate)
        y   <- x - d
        acc <- acc + y

    so acc[n] = (1 - 2^-K) acc[n-1] + x[n] and

        H(z) = (1 - z^-1) / (1 - (1 - 2^-K) z^-1),

    a ZERO AT z = 1 -- the DC null is exact, not approximate -- a pole at
    1 - 2^-K, and a gain of 2/(2 - 2^-K) = 1.0005 at Nyquist. `y` is formed
    from a REGISTERED value, so the shift and one adder are all that stand
    between a bus and the next stage.

    TRUNCATION AND THE DEAD ZONE. `acc >> K` is an arithmetic shift (floor),
    which is what the RTL does. For a constant x the accumulator reaches a
    fixed point anywhere in [x*2^K, x*2^K + 2^K - 1] and y is then exactly 0:
    a standing offset is removed COMPLETELY, unlike the envelope's decay
    (15.3) there is no residue to close with a max(1, .). From rest a positive
    constant stops at x*2^K and a negative one at x*2^K + 2^K - 1 (floor is
    not odd-symmetric): x = -1 leaves ONE nonzero sample, x = +1 leaves 1024.
    What truncation does cost is up to one LSB of asymmetry on the way there,
    counted in `n_trunc`.

    STATE IS NOT RESET BY A HIT. A capacitor does not know a stop fired, and
    the five exclusive pairs share one circuit: a retune mid-ring must carry
    the charge across. Only A_RESET (15.8) clears it.

    FINITE WIDTHS (contract 15.10). With `in_bits = n` (the declared bus
    width, signed) the input lies in [lo, hi] = [-2^(n-1), 2^(n-1) - 1]. The
    update f(acc) = acc - floor(acc / 2^K) is NONDECREASING in acc (acc + 1
    raises the floor by at most one), so if acc <= hi*2^K + 2^K - 1 then
    f(acc) <= f(that) = that - hi, and acc' = f(acc) + x <= that; likewise
    acc >= lo*2^K is closed. Hence acc is in [-2^(n+K-1), 2^(n+K-1) - 1]: n + K
    bits signed. The REACHABLE extremes are slightly inside that invariant
    (hold hi from below lands exactly on hi*2^K; hold lo from above rests at
    lo*2^K + 2^K - 1, floor again) and both need all n + K bits, which
    the near-limit test drives and asserts is tight. The output
    y = x - floor(acc / 2^K) has floor(acc / 2^K) in [lo, hi] too, so
    y is in [lo - hi, hi - lo] = +-(2^n - 1): n + 1 bits signed, and the
    full-scale reversal reaches it. An input outside [lo, hi] is a contract
    violation and ASSERTS here (`in_bits` set), where RTL would wrap."""
    __slots__ = ("k", "acc", "n_trunc", "acc_bits", "in_bits", "out_bits",
                 "in_limit_bits", "acc_limit_bits", "out_limit_bits")

    def __init__(self, k: int = COUPLE_K, in_bits: int | None = None):
        self.k = int(k)
        self.in_limit_bits = in_bits
        self.acc_limit_bits = None if in_bits is None else in_bits + self.k
        self.out_limit_bits = None if in_bits is None else in_bits + 1
        self.n_trunc = 0            # coverage, NOT state: `reset` leaves these
        self.acc_bits = 0           # alone, as EnvFx leaves its own counters
        self.in_bits = 0            # the widest INPUT seen: what the RTL word
        self.out_bits = 0           # has to be measured AGAINST, because the
        self.reset()                # declared bus width is not what arrives

    def reset(self):
        self.acc = 0

    def step(self, x: int) -> int:
        x = int(x)
        lim = self.in_limit_bits
        if lim is not None:
            assert -(1 << (lim - 1)) <= x < (1 << (lim - 1)), \
                f"bus value {x} outside the declared {lim}-bit signed width"
        d = self.acc >> self.k                       # arithmetic: floor
        self.n_trunc += (self.acc != (d << self.k))
        y = x - d
        self.acc += y
        self.acc_bits = max(self.acc_bits, signed_bits(self.acc))
        self.in_bits = max(self.in_bits, signed_bits(x))
        self.out_bits = max(self.out_bits, signed_bits(y))
        if lim is not None:
            assert self.acc_bits <= self.acc_limit_bits, f"charge needs {self.acc_bits} bits"
            assert self.out_bits <= self.out_limit_bits, f"output needs {self.out_bits} bits"
        return y


def dc_block(x, k: int = COUPLE_K):
    """`DcBlockFx` over an array, bit for bit -- the SAME filter, so a
    placement after the output stage's clamp is compared against the others
    on identical arithmetic and any difference is the placement."""
    f = DcBlockFx(k)
    return np.array([f.step(int(v)) for v in np.asarray(x).ravel()], dtype=np.int64)


# ---- the drum section --------------------------------------------------------------
class DrumsFx:
    """8 stops, N_ENV envelopes, N_PATH paths, six squares, one LFSR and the
    modal bank. `write` is the register interface of 15.1; `play` applies a
    list of (frame, addr, value) writes at frame starts (4.3) and returns
    the two buses. All control registers reset to 0 (15.8) and the block is
    silent until programmed."""

    def __init__(self, envs=N_ENV, paths=N_PATH, modes=N_MODES, nums=N_NUMS, floor=True,
                 couple: str = COUPLE_OFF, couple_k: int = COUPLE_K):
        self.E, self.P, self.M = envs, paths, modes
        self.bank = ModalFx(modes=modes, nums=nums, headroom=BODY_HR, out_bits=BODY_BITS)
        self.tanh = LadderFx(**LADDER_CFG)
        self.floor = floor
        # EXPERIMENTAL (#165). `couple` is OFF by default, so a DrumsFx built
        # the old way is bit for bit the old block -- tools/probes/dc_blocker.py::test_coupling_off_is_the_block_bit_for_bit pins that.
        assert couple in COUPLE_PLACEMENTS, couple
        self.couple, self.couple_k = couple, int(couple_k)
        self.dc_exc = [DcBlockFx(couple_k) for _ in range(modes)]
        self.dc_dmix, self.dc_body = DcBlockFx(couple_k), DcBlockFx(couple_k)
        # THE PRODUCTION COUPLING (revision 16, contract 15.10): K = COUPLE_K
        # fixed, whatever `couple_k` says, widths from the declared bus words,
        # enable from the register at A_COUPLE. Both blockers run EVERY frame;
        # the enable only selects what leaves the block.
        self.cc_dmix = DcBlockFx(COUPLE_K, in_bits=MIX_BITS)
        self.cc_body = DcBlockFx(COUPLE_K, in_bits=BODY_BITS)
        self.couple_en = 0
        self.raw_dmix = self.raw_body = 0
        self.n_tapsat = 0            # coverage: taps that hit the +-8.0 rail (15.5)
        self.n_late_writes = 0       # writes scheduled past the end of a play()
        self.trace = {}
        self.reset()

    def reset(self):
        self.stops = self.stops_prev = 0
        self.accent = [0] * N_STOPS
        self.osc_inc = [0] * N_OSC
        self.phase = [0] * N_OSC
        self.lfsr = LFSR_SEED
        if not hasattr(self, "envs"):
            self.envs = [EnvFx(self.floor) for _ in range(self.E)]
        for env in self.envs:                    # state and control to 0; the coverage counters stay
            env.reset(); env.set_ctl(0); env.peak = env.rate = env.frate = 0
        self.paths = [0] * self.P
        self.a1 = [0] * self.M
        self.a2 = [0] * self.M
        self.amp = [0] * self.M
        self.num = [0] * self.M
        self.bank.reset()
        if hasattr(self, "dc_dmix"):          # A_RESET is the only thing that
            for f in self.dc_exc:             # discharges the coupling (15.8);
                f.reset()                     # a hit, a choke or a retune does
            self.dc_dmix.reset()              # not, because a capacitor cannot
            self.dc_body.reset()              # know one happened
            self.cc_dmix.reset()              # revision 16: the production charge
            self.cc_body.reset()              # and its enable go with them (15.8)
        self.couple_en = 0

    # ---- control (contract 15.1) ---------------------------------------------
    def write(self, addr: int, value: int):
        addr, value = int(addr), int(value)
        if addr == A_RESET:
            self.reset()
        elif addr == A_COUPLE:
            if self.couple != COUPLE_OFF:
                raise ValueError("A_COUPLE written on a DrumsFx built with an experimental "
                                 f"couple={self.couple!r}: the register is the production "
                                 "semantics and must not be silently overridden (15.10)")
            self.couple_en = value & 1               # bits 31..1 are reserved: ignored
        elif addr == A_STOPS:
            self.stops = value & ((1 << N_STOPS) - 1)
        elif A_ACCENT <= addr < A_ACCENT + N_STOPS:
            self.accent[addr - A_ACCENT] = value & 0xFFFF
        elif A_OSC <= addr < A_OSC + N_OSC:
            self.osc_inc[addr - A_OSC] = value & PHASE_MASK
        elif A_ENV <= addr < A_ENV + self.E * ENV_STRIDE:
            e, f = divmod(addr - A_ENV, ENV_STRIDE)
            if f == 0:
                self.envs[e].set_ctl(value)
            elif f == 1:
                self.envs[e].peak = value & FULL24
            elif f == 2:
                self.envs[e].rate = value & 0xFFFF
            else:
                self.envs[e].frate = value & 0xFFFF   # revision 14: the final strike (15.3)
        elif A_PATH <= addr < A_PATH + self.P:
            self.paths[addr - A_PATH] = value & ((1 << 25) - 1)
        elif A_MODE <= addr < A_MODE + self.M * MODE_STRIDE:
            m, f = divmod(addr - A_MODE, MODE_STRIDE)
            if f == 0:
                self.a1[m] = s26(value)
            elif f == 1:
                self.a2[m] = s26(value)
            elif f == 2:
                self.amp[m] = value & 0xFFFF
            else:
                self.num[m] = value & 3
        # any other address: no register, the write is ignored (15.1)

    # ---- one frame (contract 15.2) ---------------------------------------------
    def _env_value(self, e: int) -> int:
        if e < self.E:
            return self.envs[e].out
        return 32767 if e == ENV_FULL else 0

    def _nonlinear(self, x: int, nl: int) -> int:
        """LIN: x. SWING: x4 on the positive half, /8 on the negative, then
        tanh. TANH: tanh. The tanh is the ladder's (11.3) on the Q1.15 value
        scaled to its Q4.20 argument, so 1.0 maps to tanh(1.0) = 0.76 and
        the swing's 4.0 to the table's clamp (15.5)."""
        if nl == NL_LIN:
            return x
        if nl == NL_SWING:
            x = (x << 2) if x > 0 else (x >> 3)
        return self.tanh.tanh_fx(sat(x << 5, 24))

    def frame(self):
        fire = self.stops & ~self.stops_prev & ((1 << N_STOPS) - 1)
        self.stops_prev = self.stops
        for env in self.envs:                                   # 15.3, before the paths
            env.frame(fire, self.accent)
        self.lfsr, noise = lfsr_frame(self.lfsr)                # 15.4
        sq = [SQ_STEP if p < (1 << (PHASE_BITS - 1)) else -SQ_STEP for p in self.phase]
        sqsum = sum(sq)
        sq1 = [SQPAIR_STEP if p < (1 << (PHASE_BITS - 1)) else -SQPAIR_STEP for p in self.phase]
        sqpair = sq1[SQPAIR[0]] + sq1[SQPAIR[1]]
        for i in range(N_OSC):
            self.phase[i] = (self.phase[i] + self.osc_inc[i]) & PHASE_MASK
        y1 = self.bank.y1
        dmix = 0
        exc = [0] * self.M
        vals = []
        for w in self.paths:                                    # 15.5, in order
            src, e1, e2 = w & 31, (w >> 5) & 31, (w >> 10) & 31
            nl, att, dest = (w >> 15) & 3, (w >> 17) & 7, (w >> 20) & 31
            if src == SRC_NOISE:
                s = noise
            elif src == SRC_SQSUM:
                s = sqsum
            elif src == SRC_PULSE:
                s = 32767
            elif src == SRC_SQPAIR:
                s = sqpair
            elif SRC_SQ <= src < SRC_SQ + N_OSC:
                s = sq1[src - SRC_SQ]
            elif SRC_TAP <= src < SRC_TAP + self.M:
                s = y1[src - SRC_TAP] >> TAP_SHIFT
                self.n_tapsat += not (-32768 <= s <= 32767)
                s = sat(s, 16)
            else:
                s = 0
            s = self._nonlinear(s, nl)
            v = (s * (self._env_value(e1) + self._env_value(e2))) >> (15 + att)
            vals.append(v)
            if dest == DEST_MIX:
                dmix += v
            elif dest < self.M:
                exc[dest] += v
        if self.couple == COUPLE_EXC:
            # The diagnosis's literal reading: AC-couple each mode's
            # excitation, BEFORE the all-pole gain multiplies its mean. It is
            # also what reference 5's Q62 tells us not to do -- the excitation
            # is asymmetric by design -- so it is measured, not assumed.
            exc = [self.dc_exc[m].step(exc[m]) for m in range(self.M)]
        coefs = list(zip(self.a1, self.a2, self.amp))
        body = self.bank.step(exc, coefs, self.num)             # 15.6
        # Revision 16 (15.10): the production coupling, on the shared buses,
        # after the bank and before the output stage's clamp. It always runs;
        # the enable selects its output, so the charge tracks the bus while
        # bypassed and a re-enable reads the charge the whole history made.
        self.raw_dmix, self.raw_body = dmix, body
        cd, cb = self.cc_dmix.step(dmix), self.cc_body.step(body)
        if self.couple_en:
            dmix, body = cd, cb
        if self.couple == COUPLE_BUS:
            # The machine's placement: after the nonlinearities, the envelopes
            # and the resonators, before the output stage's single clamp (12).
            # A linear filter commutes with a sum, so ONE blocker on a bus is
            # the superposition of a per-path or per-mode blocker at the same
            # corner -- two registers for sixteen voices. `test_..._commutes`
            # is that claim, measured.
            dmix = self.dc_dmix.step(dmix)
            body = self.dc_body.step(body)
        return dmix, body, fire, noise, sqsum, exc, vals

    def play(self, writes, n: int):
        """n frames; `writes` is a list of (frame, addr, value), applied at the
        start of their frame in list order. Returns (dmix, body) as int64 /
        int32 arrays; `self.trace` holds the per-frame internals. A write at a
        frame at or past `n` is dropped and counted in `self.n_late_writes` --
        it could not have affected any sample -- and a negative frame raises."""
        ev = {}
        self.n_late_writes = 0
        for f, a, v in writes:
            f = int(f)
            assert f >= 0, f"write ({f}, {a:#x}, {v}) before frame 0"
            if f >= n:
                # A write scheduled after the last frame cannot affect any
                # sample, so it is DROPPED rather than rejected. The
                # coefficient sequences of 15.7.1 run to 60 ms past a hit, and
                # a caller rendering a shorter passage than that must not have
                # to know it -- `render(..., 0.012)` in an acceptance suite is
                # a legitimate thing to ask for. Counted so a test can see how
                # many were dropped; a negative frame is still a caller error.
                self.n_late_writes += 1
                continue
            ev.setdefault(f, []).append((a, v))
        dmix = np.empty(n, dtype=np.int64)
        body = np.empty(n, dtype=np.int32)
        fire = np.empty(n, dtype=np.int64)
        noise = np.empty(n, dtype=np.int64)
        env = np.empty((self.E, n), dtype=np.int64)
        exc = np.empty((n, self.M), dtype=np.int64)
        raw_dmix = np.empty(n, dtype=np.int64)
        raw_body = np.empty(n, dtype=np.int64)
        for f in range(n):
            for a, v in ev.get(f, ()):
                self.write(a, v)
            d, b, fi, nz, _, ex, _ = self.frame()
            dmix[f], body[f], fire[f], noise[f] = d, b, fi, nz
            exc[f] = ex
            raw_dmix[f], raw_body[f] = self.raw_dmix, self.raw_body
            for e in range(self.E):
                env[e, f] = self.envs[e].out
        self.trace = dict(dmix=dmix, body=body, fire=fire, noise=noise, env=env, exc=exc,
                          dmix_raw=raw_dmix, body_raw=raw_body)
        return dmix, body


# ---- the instrument's output stage (contract 12) ------------------------------------
def output_fx(v, vol: int, dmix, dvol: int, body, bvol: int) -> np.ndarray:
    """sample = sat16((v * vol + dmix * dvol + body * bvol) >> 15): the voice's
    VCA output (9), the drum mix bus and the body bus under three Q0.15 gains,
    summed exactly, shifted, clamped once -- the one hard rail. With dvol =
    bvol = 0 it is the voice's rev-3 formula bit for bit."""
    v = np.asarray(v, dtype=np.int64); dmix = np.asarray(dmix, dtype=np.int64)
    body = np.asarray(body, dtype=np.int64)
    acc = v * int(vol) + dmix * int(dvol) + body * int(bvol)
    return np.clip(acc >> 15, -32768, 32767).astype(np.int16)


# ---- host conversions (contract 15.7, informative) ---------------------------------
def rate_reg(tau_s: float) -> int:
    """Q0.16 decay rate for an amplitude time constant of tau seconds:
    round((1 - exp(-1 / (tau * SR))) * 2^16), at least 1; tau <= 0 is instant
    (65535). The voice's release conversion with tau in place of release/4."""
    if tau_s <= 0.0:
        return 65535
    return max(1, usat(int(round((1.0 - math.exp(-1.0 / (tau_s * SR))) * (1 << RATE_Q))), RATE_Q))


def accent_reg(level: float) -> int:
    return usat(int(round(level * 32768)), ACCENT_BITS)


def peak_reg(level: float) -> int:
    return usat(int(round(level * FULL24)), ENV_BITS)


def amp_reg(level: float) -> int:
    return usat(int(round(level * 65536)), 16)


def osc_inc_reg(hz: float) -> int:
    return usat(phase_inc(hz), PHASE_BITS)


def mode_regs(f0_hz: float, q: float, amp: float, num: int = RAW) -> tuple[int, int, int, int]:
    """(a1, a2, amp, num) for one mode: the pole pair of modal_fixed.pole_regs
    (docs/tr808-reference.md section 14's formula) and a Q0.16 level."""
    a1, a2 = pole_regs(f0_hz, q)
    return a1 & ((1 << 26) - 1), a2 & ((1 << 26) - 1), amp_reg(amp), num


def mode_writes(m: int, f0_hz: float, q: float, amp: float, num: int = RAW) -> list:
    base = A_MODE + m * MODE_STRIDE
    return [(base + i, v) for i, v in enumerate(mode_regs(f0_hz, q, amp, num))]


def env_writes(e: int, stop: int, tau_s: float, peak: float, *, choke: int = 15,
               hold: int = 0, bursts: int = 0, period: int = 0,
               final_tau: float | None = None) -> list:
    """`final_tau` (revision 14) writes FRATE: None writes nothing (the register
    keeps what it had -- 0 from reset), 0 writes 0 (the feature OFF, which a
    preset that shares an envelope with a final-strike sound MUST do), and a
    time constant writes its rate."""
    base = A_ENV + e * ENV_STRIDE
    w = [(base, env_ctl(stop, choke, hold, bursts, period)),
         (base + 1, peak_reg(peak)), (base + 2, rate_reg(tau_s))]
    if final_tau is not None:
        w.append((base + 3, rate_reg(final_tau) if final_tau > 0 else 0))
    return w


# ---- the reference kit (contract Appendix G, informative) -----------------------------
# Stops, in the order the host sees them.
# ---- the eleven circuits and the sixteen sounds -------------------------------
# The TR-808's sixteen named sounds are ELEVEN sound-generator circuits: five of
# them carry two sounds each, selected by a panel switch, and the two cannot
# sound together (reference 4 SW8, 5 SW11, 7/8 SW12). So a STOP here is a
# circuit, not a sound, and the second sound of a pair is a set of coefficient
# and envelope writes into the circuit it shares -- `preset_writes`.
# The first eight are unchanged from revision 8, so every register image and
# every test written against them still means the same thing; MT, CL and CY are
# appended.
BD, SD, LT, HT, CH, OH, CP, CB, MT, CL, CY = range(11)
STOP_NAMES = ("BD", "SD", "LT", "HT", "CH", "OH", "CP", "CB", "MT", "CL", "CY")
# The sixteen sounds, and the circuit each one plays on. Where two sounds share
# a circuit the first listed is the one `kit_808()` loads.
SOUND_STOP = dict(BD=BD, SD=SD, LT=LT, LC=LT, MT=MT, MC=MT, HT=HT, HC=HT,
                  RS=CL, CL=CL, CP=CP, MA=CP, CB=CB, CY=CY, OH=OH, CH=CH)
SOUND_NAMES = ("BD", "SD", "LT", "LC", "MT", "MC", "HT", "HC",
               "RS", "CL", "CP", "MA", "CB", "CY", "OH", "CH")
PAIRS = (("LT", "LC"), ("MT", "MC"), ("HT", "HC"), ("RS", "CL"), ("CP", "MA"))
# Modes 0..7 are the filters (a numerator selected), 8..15 the bridged-T bodies.
# The bank is instantiated with NUMS = 11, so modes 8, 9 and 10 COULD carry a
# numerator and do not: `num` reads 0 (RAW) for them and the excitation history
# they keep is never read. That is three spare filters, not three wasted modes.
M_HATBP, M_OHHP, M_CHHP, M_SDN, M_CPBP, M_CBBP, M_CYBP, M_CYHI, \
    M_BD, M_SDLO, M_SDHI, M_LT, M_MT, M_HT, M_RS1, M_RS2 = range(16)
# Envelopes.
E_BDX, E_BDCLICK, E_SDX, E_SDN, E_LTX, E_HTX, E_CH, E_OH, E_CPBURST, E_CPTAIL, \
    E_CBA, E_CBB, E_MTX, E_RSX, E_RSG, E_CYS, E_CYD, E_CYL = range(18)
OSC_HZ = (205.3, 369.6, 304.4, 522.7, 800.0, 540.0)   # the HD14584 bank, reference 1.5
FRAME = 1.0 / SR

# ---- kit levels (chosen, not the circuit's) ----------------------------------
# `model/drums_fx_render.py --balance` sets these so each sound alone peaks on
# its bus in the proportions of Roland's chart (reference 1.6, "normal Vpp"),
# the loudest at 0.5 x full scale. They are the kit's, not the block's.
CHART_VPP = dict(BD=3.5, SD=3.0, LT=3.5, LC=3.5, MT=3.0, MC=3.0, HT=3.5, HC=3.5,
                 RS=3.0, CL=2.5, CP=6.0, MA=3.0, CB=3.5, CY=3.5, OH=3.5, CH=3.0)
BUS_TARGET = {n: round(0.5 * min(v, 3.5) / 3.5, 4) for n, v in CHART_VPP.items()}
AMP_TOM = {"LT": 0.0048081, "MT": 0.0061911, "HT": 0.0099312,
           "LC": 0.0093529, "MC": 0.0122886, "HC": 0.0206735}
# RE-BALANCED with the pitch drop corrected, by `drums_fx_render.py --balance`
# unchanged: the procedure did not move, its input did. Sweeping f0 by x1.7 and
# back detunes the resonator while the pulse is still in it, so the OLD drop was
# costing the toms 38-44 % of their ring; with the measured x1.06 they reach the
# chart's proportions on 0.56-0.62 x the exciter. Nothing but the six tom/conga
# positions moved (every other voice re-balances at x1.00 +- 0.01).
AMP_CY_HI = 1.0
# RE-BALANCED for #388's RS_LO_X_ATT, by `drums_fx_render.py --balance`
# unchanged -- again the procedure did not move, its input did. Attenuating the
# excitation into the 455 Hz network takes 7.05 dB off the whole rimshot,
# because that mode was setting the voice's peak; the gate peak puts it back.
# PEAK_RSG is the LAST thing in the RS path (`frame`: nonlinearity, then
# envelope, then att), so this is a pure gain and moves neither the distortion
# nor the two modes' ratio. `--balance` reports x2.2532 for RS and x1.00 +- 0.08
# for the other fifteen, i.e. nothing else moved.
PEAK_RSG, PEAK_CLG, PEAK_MA = 0.7728, 0.5, 0.5395
# The gate peak revisions 10 to 14 shipped -- what the PUBLISHED R0 and R1 Arty
# images were verified with -- kept so `kit_808_rev14()` can undo the re-balance
# above (see its `undo`), and through it `kit_808_rev11()`.
PEAK_RSG_REV14 = 0.343
# ---- the clap, contract revision 14 (plan081 C / plan084: "L2") ---------------
# FROZEN from the confirmed experiment (docs/scorecard/clap-d12a/README.md
# section 10; final-strike.json): four strikes at period 511 frames (0, 10.6,
# 21.3, 31.9 ms), the first three at the kept 4 ms decay and 13/16 re-strike,
# the LAST at the accent-scaled fire level (L = 1.00) decaying at 20 ms, and the
# tail's tau at its one-record measured 80 ms (was the R348 x C138 estimate 47).
CP_BURST_TAU, CP_BURSTS, CP_PERIOD = 4e-3, 3, 511
# The tail's tau and level are registered tunables (#559: cp-tail-tau,
# cp-tail-peak in docs/sensitivity/registry.json), so they are integers at
# column 0 that tools/sensitivity.py can read. CP_TAIL_TAU_MS / 1000 is
# bit-for-bit the 80e-3 it replaced, and 10 ** (0 / 20) is exactly 1.0.
CP_TAIL_TAU_MS = 80
CP_TAIL_PEAK_DB = 0
CP_TAIL_PEAK = 0.22
CP_FINAL_TAU, CP_TAIL_TAU = 20e-3, CP_TAIL_TAU_MS / 1000
CP_TAIL_PEAK_LEVEL = CP_TAIL_PEAK * 10 ** (CP_TAIL_PEAK_DB / 20)
# The RS/CL exciter, by position. The RIMSHOT's is low on purpose: both taps
# go through the swing VCA, and a tap that drives the tanh into its rail comes
# out as a flat-topped burst whose decay is the GATE's 22 ms rather than the
# resonators' 4.7 and 2.4 ms. HARDWARE-MEASURED [rs8/RS.WAV, `decay_fit`]: the
# machine is tau 3.2 ms, t(-20 dB) 9.0 ms. At the exciter's usual 0.25 ours
# measured 5.55 / 13.85 -- 50 % long; at 0.06 it is 4.30 / 9.60. The gate peak
# is raised to hold the level, so the sound is the same loudness and a shorter
# shape. The distortion is still there: `test_rimshot_is_distorted_and_that_is
# _the_sound` measures it against the same voice with the VCA set to LIN.
PEAK_RSX, PEAK_CLX = 0.06, 0.5545
PEAK_CYS, PEAK_CYD, PEAK_CYL = 0.324, 0.432, 0.0864
CL_ATT, MA_ATT = 0, 0
RS_ATT, CY_ATT = 0, 0
# Path slots, in the order kit_808() writes them. Named so `preset_writes` can
# reach into the image and switch a shared circuit's position.
(P_BDX, P_BDCLICK, P_SDLO, P_SDHI, P_SDN, P_LTX, P_MTX, P_HTX, P_SQBP, P_CH,
 P_OH, P_CPN, P_CPOUT, P_CBA, P_CBB, P_RS1X, P_RS2X, P_RS1OUT, P_RS2OUT,
 P_CYBP, P_CYS, P_CYD, P_CYL) = range(23)

# ---- the bass drum, entirely from docs/tr808-reference.md section 2 -----------
# VERIFIED IN A SOURCE [W14a section 5; computed with section 1.2 from R161,
# R165, R166, R170, C41/C42]: the bridged-T's f0 is **49.4 Hz**, and Werner
# measures ~49.5. Roland's tuning chart says 56 Hz ("18 ms"); the reference
# calls that "typical and variable" and tells the implementer to treat 50-56 Hz
# as the target. The two are not interchangeable, and rev 5 shipped the chart's
# f0 with the CIRCUIT's Q table -- which is inconsistent: section 2's decay
# table (Q 2.3/5.2/22.3/63/84 against tau 15/33/144/408/544 ms) satisfies
# tau = Q/(pi f0) to 1.5 % at f0 = 49.4 and only to 12.8 % at 56. Shipping
# Q = 22.3 at 56 Hz gives tau = 127 ms where the same table says 144. So the
# short decay was never a DECAY fault: it was the pitch error, propagated.
# DR 0009 resolves the conflict inside the reference in favour of the computed
# value, which two sample sets also corroborate (49-51 Hz).
BD_HZ, BD_HZ_CHART = 49.4, 56.0
# VERIFIED IN A SOURCE [section 2, W14a section 6]: Q against the VR6 DECAY
# knob position, the feedback buffer's own law. The DECAY CONTROL IS NOT A
# FAULT -- this is the table rev 5 already shipped. Knob positions are the
# panel's 0..10; the reference tabulates VR6's 0..1.
BD_DECAY_Q = {0.0: 2.3, 1.0: 5.2, 5.0: 22.3, 9.0: 63.0, 10.0: 84.0}
# VERIFIED IN A SOURCE [section 2, W14a section 8.1; SN p.6]: while Q43 is on
# it shorts R165, the foot resistance falls and f0 rises to ~130 Hz at Q ~ 6
# for ~4 ms ("the ON period of Q43 ... equals 4 ms"; Werner measures ~6 ms).
# It is the SAME resonator retuned, not a second one, so the host writes the
# attack coefficients at the hit and the body's 4 ms later.
BD_ATTACK_HZ, BD_ATTACK_Q, BD_ATTACK_MS = 130.0, 6.0, 4.0
# VERIFIED IN A SOURCE [section 4, SN text]: with the tom's germanium diodes
# conducting the foot resistance collapses and f0 rises at the start of a hard
# hit, settling back as the ring decays -- "amplitude-dependent and gradual,
# not a stepped envelope", and "accent changes the pitch envelope". This is the
# toms' "doom" sweep. The EXISTENCE of the drop, its accent dependence and its
# gradual shape are the source's. The MAGNITUDE was marked [inferred] at x1.7
# and is now the fitted law evaluated at a stated reference setting, with a
# measured value nearby (see below).
#
# HARDWARE-MEASURED [99 clean-digital tom files and 66 conga files of a real
# TR-808, 808 From Mars; model/tom_pitch_probe.py behind its own gate;
# docs/tom-pitch-drop-measurement.md (#110); the law, its constants and four
# held-out splits in model/tom_drop_fit.py and docs/tom-pitch-drop-law.json;
# before/after against the recordings in docs/tom-pitch-drop-correction.md]:
#
#   onset f0 / settled f0, median over 11 TUNING positions x 3 voices
#     no accent  x1.063 (n 23)    accent  x1.140 (n 33)    more  x1.236 (n 33)
#
# x1.7 OCCURS NOWHERE IN THE CORPUS. The largest drop in any of the 99 files is
# x1.344. The inferred magnitude was 3x too large at the loudest hit measured
# and 11x too large at an unaccented one -- DR 0009's kick again, a magnitude
# marked [inferred] that the machine contradicts.
#
# The measurement found THREE separate faults, which is why one constant became
# four:
#   1. THE MAGNITUDE, above. TOM_DROP_RATIO is now the fitted law (K (1 - A0))
#      evaluated at one stated reference setting -- accent 1.0, the TUNING pot
#      at its centre, the TOM position of the circuit -- x1.060, against that
#      cell's own measured median of x1.054. It is still the single knob that
#      sets the size of the sweep.
#   2. THE CLAMP. The old law scaled the excess by min(max(accent, 0), 1),
#      which hands the FULL drop to an unaccented hit, where the machine does
#      x1.06. Correcting the magnitude alone would have left the accent curve
#      wrong at the bottom. The drop has a THRESHOLD instead: germanium diodes
#      do not conduct below a drive, so below TOM_DROP_ACCENT_0 there is no
#      drop at all, and above it the excess is linear in accent. Measured
#      excess is 0.054 / 0.143 / 0.239 at the three recorded accent levels --
#      a straight line that does not pass through the origin.
#   3. THE TUNING POT, which the old sequence ignored entirely. LT at More
#      Accent runs x1.169 at 82 Hz and x1.325 at 101 Hz: the excess nearly
#      doubles across the pot. Section 4's circuit reading predicts a
#      dependence; the direction and size here are the measurement's.
#
# The TOM and CONGA positions of one circuit have different thresholds, and
# that is NOT an f0 effect: HT and LC are BOTH nominally 185 Hz, on the same
# bridged-T with a capacitor switched (SW8), and unaccented their drops differ
# by 11x (excess 0.061 against 0.0055). It is the switch, not the frequency --
# which is also why the tuning term below is normalised to each POSITION's own
# nominal rather than to an absolute frequency.
TOM_DROP_RATIO = 1.060        # onset / settled at accent 1.0, pot centre, TOM
TOM_DROP_ACCENT_0 = 0.670     # accent below which the diodes do not conduct
TOM_DROP_ACCENT_0_CONGA = 1.064
TOM_DROP_TUNING_G = 3.58      # d ln(excess) / d (f0/f0_nominal), tom position
TOM_DROP_TUNING_G_CONGA = 7.46
# The TUNING pot spans +-10 % (reference 1.7), which is also the span the 99
# files cover, so the tuning term is clamped there: past the pot's own range
# the law would be extrapolating outside every file it was measured from.
TOM_DROP_TUNING_SPAN = 0.10
# UNCHANGED, AND DELIBERATELY. exp(-3t/60 ms) is tau = 20 ms against a measured
# 24.5 ms at Accent, and the exponential beat a linear ramp in 88 of 89 rows,
# so the shape and the duration are the parts of 15.7.1 that survived contact
# with the hardware. The measured tau is itself accent-dependent -- 13 / 24.5 /
# 33 ms at the three levels -- and this law is not. That is a known and
# reported deviation, not an oversight: docs/tom-pitch-drop-correction.md.
TOM_DROP_MS, TOM_DROP_STEPS = 60.0, 6

# ---- the snare, reference section 3 -------------------------------------------
# SD snappy filter: the pole is VERIFIED IN A SOURCE (reference 3's 2.75 kHz /
# Q 0.7); the NUMERATOR is MEASURED -- a band-pass, not the high-pass 3 calls it
# (17.22). The amp is the SNAPPY knob's measured curve at 5.0.
SD_NOISE_HZ, SD_NOISE_Q, SD_NOISE_AMP = 2750.0, 0.7, 0.2059
# SD high; TONE = this pair's ratio, and the RATIO here is MEASURED (17.24): the
# machine at TONE 5.0 puts the upper partial 1.42x the lower (+3.0 dB), on both
# the SNAPPY-up and SNAPPY-down file. Rev 6 shipped 0.394 (-8.1 dB) -- 11 dB of
# the snare's front end missing. The pair is then scaled together to the kit's
# own peak, which is unchanged at 0.46 FS.
SD_HI_HZ, SD_HI_Q, SD_HI_AMP = 336.0, 9.9, 0.008865
# SNAPPY = this peak x M_SDN's amp, reference 3. The RATE is MEASURED (17.25):
# the machine's snappy burst measures T20 63-78 ms over six files; reference
# 3's 15 ms is R186 x C51, the CHARGE path, and gives 34 ms. The peak holds the
# noise share at the machine's 27.7 % with the new rate.
SD_NOISE_TAU, SD_NOISE_PEAK = 30e-3, 0.3046

# ---- the toms and congas, reference section 4's component-value table ---------
# VERIFIED IN A SOURCE [SN p.6 "Voices are switched by SW8"]: the tom and the
# conga of each pair are ONE resonator with a capacitor switched in or out, so
# they are one circuit and cannot sound together. INFERRED [reference 4]: the
# (f0, Q) of each position, from R1/R2/C1/C2 with the germanium diodes open.
# The table is self-consistent under tau = Q / (pi f0) to better than 5 % at
# every one of the six positions, which is why reference 4's Q column is used
# here in preference to section 14's (its MC row says Q 32 where the same
# row's tau 44 ms implies 38).
#   name: (f0 Hz, Q, Roland chart decay s)
TOM_PRESET = {"LT": (90.0, 25.0, 0.200), "LC": (185.0, 44.7, 0.180),
              "MT": (135.0, 24.0, 0.130), "MC": (280.0, 34.0, 0.100),
              "HT": (185.0, 25.0, 0.100), "HC": (400.0, 43.1, 0.080)}
# HARDWARE-MEASURED [Fischer s/n 103852, LC50/MC50/HC50.WAV, `drum_verify.decay_fit`
# over -3..-30 dB, R^2 0.999 on all three]: tau 76.9 / 38.7 / 34.3 ms. The THREE
# TOM rows of reference 4's table land on the machine (LT 88.4 computed vs 87.6
# measured, MT 56.6 vs 57.7, HT 43.0 vs 41.7 -- all inside 3 %); its three CONGA
# rows do not, and they are long by 12-30 % (LC 94.6 vs 76.9, MC 43.2 vs 38.7,
# HC 44.6 vs 34.3). So the conga Q above is the machine's, Q = pi f0 tau at the
# CHART's f0, and reference 4's inferred Q column is amended -- reference 1.7
# already says every high-Q figure is a +-50 % nominal, which is exactly the
# quantity that moved. f0 is left at the chart's (the machine reads 200 / 282 /
# 413 Hz, i.e. +8 / +1 / +3 %, inside reference 1.7's +-10 %).
TOM_HW_TAU = {"LT": 0.0876, "LC": 0.0769, "MT": 0.0577, "MC": 0.0387,
              "HT": 0.0417, "HC": 0.0343}
# ---- rimshot and claves, reference section 5 ---------------------------------
# VERIFIED IN A SOURCE [SN p.6 "RS/CL", SW11]: one circuit, two bridged-T
# networks, switch-selected; the chart gives RS "H" 1667 / "L" 455 Hz and
# CL 2500 Hz. INFERRED [reference 5, from R315/R316/C115/C116 and the switch
# wiring on p.9]: RS low 455 Hz Q 6.7, RS high 1786 Hz Q 13.5, CL 2500 Hz with
# the feedback wired for high Q. The schematic's 1786 is preferred to the
# chart's 1667 (7 % apart, inside reference 1.7's +-10 %) on DR 0009's rule.
RS_LO_HZ, RS_LO_Q = 455.0, 6.7
RS_HI_HZ, RS_HI_Q = 1786.0, 13.5
CL_HZ, CL_Q = 2500.0, 200.0
# THE TWO NETWORKS' RELATIVE DRIVE (#388). Exciting both bridged-T bodies with
# the SAME pulse -- which is what the circuit does -- does NOT give the two
# modes the circuit's relative level, because the bank's RAW numerator is
# all-pole and the circuit's networks are band-pass. The two impulse responses
# are normalised differently, and the difference is a pure function of f0 and Q:
#
#   bank, y[n] = x[n] + a1 y[n-1] + a2 y[n-2]:  peak ~ 1 / sin(w0)
#   circuit, H(s) = H0 (w0/Q) s / (s^2 + (w0/Q) s + w0^2):  peak ~ H0 w0 / Q
#
# High re low, from the shipping constants above at 48 kHz:
#   bank     20 log10( sin(w_lo) / sin(w_hi) )        = -11.80 dB
#   circuit  20 log10( (f_hi/Q_hi) / (f_lo/Q_lo) )    =  +5.79 dB
# so an equal pulse into both modes puts our high mode 17.60 dB below where the
# same pulse into the same two networks puts the circuit's. That is a property
# of the DISCRETISATION, not of the rimshot: it is the same 1/sin(w0) that any
# all-pole mode carries, and it only becomes audible where one voice sums two
# modes an octave and a half apart.
#
# HARDWARE-MEASURED [Fischer s/n 103852, rs8/RS.WAV; tools/probes/rs_guard_band.py
# compare]: the machine's 1711 Hz mode sits +6.5 dB ABOVE its 457 Hz mode. The
# CIRCUIT's closed form above predicts +5.79 dB with no recording in it at all.
# The two agree to 0.7 dB, and rev 14 shipped -12.1 dB -- so the 18.7 dB defect
# #388 scored is the discretisation's, and the circuit's own transfer function
# names the correction before the reference is consulted.
#
# The correction is applied where the circuit applies its own summing weights:
# on the excitation into the LOW network, as a right shift on its path word.
# `att` is 3 bits of 6.02 dB, so 3 (18.06 dB) is the nearest step to 17.60 and
# is 0.46 dB from it -- inside the +-2.4 dB the balance estimator declares for
# itself. Attenuating the low mode rather than lifting the high one is not a
# free choice: PEAK_RSX 0.06 already puts the low mode's tap at ~1.0 x full
# scale (0.06 x 1/sin(w_lo) = 1.008), so the 8x has to come off the loud mode
# or the tap saturates. CL disconnects P_RS1X entirely, so this reaches the
# rimshot and nothing else.
#
# IT MOVES THE VOICE'S LEVEL, AND THAT IS REPAIRED SEPARATELY. The 455 Hz mode
# was setting the rimshot's peak, so attenuating it takes 7.05 dB off the whole
# voice: `drums_fx_render.py --balance` reports RS at 0.190 FS against its
# 0.4286 share of Roland's chart. PEAK_RSG carries the x2.2532 back (see it);
# separating the two is the point, because the balance estimator level-matches
# and would have scored a rimshot 7 dB too quiet as fixed.
#
# 18.06 dB OF DRIVE BUYS 10.1 dB OF BALANCE, not 18: both taps go through the
# swing VCA's tanh, and the low tap was sitting in its compression, so a 18 dB
# smaller tap comes out only ~8 dB smaller. Measured, not argued -- `confirm`
# reads the high mode at -12.14 dB re the low at att 0 and -2.04 dB at att 3.
RS_LO_X_ATT = 3
# VERIFIED IN A SOURCE [SN "this switching is provided to eliminate noise
# leaking from IC20"]: both voices are gated by JFET Q74 through C112 0.022 uF
# / R305 1 MOhm, a ~22 ms window. It is what stops the claves, whose resonator
# alone would ring for 25 ms at Q 200.
RS_GATE_TAU = 22e-3
# ---- maracas, reference section 8 -------------------------------------------
# INFERRED [reference 8, Sallen-Key on Q68 with C132 = C133 = 0.001 uF,
# R339 3.3 k, R340 68 k]: a 2-pole high-pass at 10.6 kHz, Q 2.3. The envelope
# is R341 470 k / C134 0.033 uF, ~15 ms; the chart's decay is 25-35 ms, so
# tau 12 ms (T20 28 ms) is the value that satisfies both.
MA_HP_HZ, MA_HP_Q, MA_TAU = 10600.0, 2.3, 12e-3
# ---- the cowbell, reference section 9 ------------------------------------------
# CB band-pass: MEASURED -- fitted to the reference unit's 16 partials, closing
# reference 9's open item (DR 0010)
CB_BP_HZ, CB_BP_Q, CB_BP_AMP = 1100.0, 2.8, 0.02176
# MEASURED: the reference tail is tau 98 ms
CB_TAU_B = 100e-3
# ---- cymbal, reference section 10 -------------------------------------------
# VERIFIED IN A SOURCE [W14b section 4, "around 3440 Hz" and "around 7100 Hz";
# SN p.13 values R56/R57/C13/C14 and R58/R59/C15/C16]: the six-square sum is
# band-passed by TWO filters, and the 7.1 kHz one is the hats' band as well.
# The Q of both is INFERRED at 6 [reference 10].
CY_LO_HZ, CY_Q = 3453.0, 6.0
# The two post-filters the cymbal's high band gets. Reference 10 names three
# (Hh1 2.5 kHz Q 0.97 on the low band, Hh2 unspecified resonant, Hh3 resonant
# ~10.5 kHz) and the bank has room for two, so which two is an evidence
# question and was settled by measurement -- see CY_FIT below.
CY_HI_HZ, CY_HI_Q = 10500.0, 2.5
# The three VCA envelopes. The short one is VERIFIED only as "its decay time is
# short" [SN] -- 20 ms is INFERRED. The low band's ~100 ms is reference 10's
# own "what to implement". The middle band is the DECAY knob: VR2 2 MOhm || R93
# 470 k into C41 1 uF reaches ~0.38 s at the top [W14b section 7], and Roland's
# chart's 350 / 800 / 1200 ms decays are T20-like, so the chart's mid 800 ms is
# tau 347 ms -- which the RC at the knob's midpoint (1 M || 470 k = 320 k, i.e.
# 320 ms) corroborates to 8 %.
# HARDWARE-MEASURED [Fischer s/n 103852, cy8/CY5025.WAV -- TONE 5.0, DECAY 2.5.
# THIS LABEL WAS WRONG HERE FOR MONTHS and said "DECAY 5.0, i.e. Roland's own
# chart condition". It is not that condition: the Fischer filename's second code
# is DECAY and "25" means 2.5, so the chart's mid-DECAY cymbal is CY5050. Found
# and recorded by `tools/probe_new_voice_knobs.py` findings 2 and 4 and never
# carried back here; corrected under #102, which needed to know which recording
# its development case actually is. NOTHING MEASURED MOVES -- every number below
# was taken on this file and is still a number about this file. What moves is
# what the file is, and therefore what the fit below generalises to.]
# Schroeder T20 (validated to 0.01 % against
# a closed-form damped sinusoid in test_audio_measure) and the band-energy split
# from a zero-phase 8th-order Butterworth bank, both estimators checked against
# a two-tone signal of known split before anything here was quoted:
#
#   real CY5025:  T20 798 ms,  energy  1.1 % <2k / 10.3 % 2-5k / 53.2 % 5-9k
#                              / 23.3 % 9-13k / 6.0 % >13k;  FFT peak 3153 Hz
#
# THREE THINGS IN REFERENCE 10 DO NOT SURVIVE THAT MEASUREMENT.
#  1. The cymbal's long tail is the LOW band, not a high one. The 2-5 kHz band
#     measures T20 1244 ms against 745 ms at 5-9 kHz on the same file, and the
#     voice's FFT peak sits at 3153 Hz -- the 3.45 kHz band-pass ringing on.
#     Reference 10 says "DECAY changes only the middle band's RC" and calls the
#     low band "fixed, medium"; every band lengthens with the knob (see
#     CY_DECAY_TAU) and the low one lengthens most.
#  2. The 9-13 kHz shoulder (23 % of the machine's energy) cannot be made with
#     an 11.7 kHz two-pole HIGH-pass: the (1 - z^-1)^2 numerator keeps rising to
#     Nyquist, so that filter puts 42 % of its output above 13 kHz where the
#     machine has 6. A BAND-pass at 10.5 kHz does have the machine's shape, and
#     10.5 kHz is reference 10's own figure for Hh3.
#  3. Hh1 (2.5 kHz on the low band) is worth less than Hh3. With sixteen modes
#     the bank can host either but not both; keeping Hh3 fits the machine's
#     band split roughly twice as well (cost 0.56 against 1.48 on the fit
#     below), at the price of 2.1 % of the energy below 2 kHz where the machine
#     has 1.1 %. Hh1 is the documented omission.
# FITTED to the recording, not derived: the three envelopes and their levels
# were searched against FOUR measurements of the same file at once -- the
# log-envelope tau over -3..-30 dB, t(-20 dB), the Schroeder T20 and the
# five-band energy split -- because fitting any one of them alone moves the
# others the wrong way. Matching the Schroeder T20 to 3 % on its own left the
# voice's audible decay 46 % long (tau 374 ms against 256); the values below
# give tau 243 / t-20 318 / T20 903 against the machine's 256 / 308 / 798.
CY_TAU_SHORT, CY_TAU_DECAY, CY_TAU_LOW = 12e-3, 140e-3, 500e-3
# The DECAY knob, from the same five files. RE-LABELLED under #102 and nothing
# re-measured: the five T20s are 435 / 798 / 1281 / 1674 / 1888 ms, and the
# question was only which knob each belongs to. The filename codes sort
# 00 < 10 < 25 < 50 < 75 lexically while the knobs they mean are 0 < 2.5 < 5 <
# 7.5 < 10, so reading the directory listing in order puts "10" (knob 10.0,
# the LONGEST decay) in the knob-2.5 slot. That is the whole defect, and it
# manufactured the exclusion this comment used to carry: with the codes decoded,
# the five are MONOTONIC in the knob and nothing is out of order. The recordist's
# own file lengths confirm the decode independently -- 1.50 / 2.00 / 2.50 / 3.50 /
# 4.00 s for codes 00 / 25 / 50 / 75 / 10, i.e. he gave the longer settings more
# room (`tools/probe_new_voice_knobs.py` finding 2). The knob scales E_CYD and
# E_CYL together, which is what the per-band measurement shows and not what
# reference 10 says.
#
# The 1888 ms file IS still truncation-affected (#118: a backward integral over a
# record that ends before the decay does reports the cut), and so, it turns out,
# are its neighbours -- `docs/scorecard/results/D14A.json` records `total decay`
# as INVALID on the reference side of CY5025 for exactly that reason. These T20s
# are kept as the record of what was measured, not as a law to fit: the fitted
# CY DECAY law lives in `test_discrimination.fit_laws` ("CY.decay_tau") and uses
# `measure_tau`, which needs only 27 dB of record.
CY_DECAY_T20 = {0.0: 0.435, 2.5: 0.798, 5.0: 1.281, 7.5: 1.674, 10.0: 1.888}
# What the fit above achieved, recorded so a regression can see it move:
#   ours: T20 824 ms, energy 1.7 / 6.5 / 57.4 / 15.6 / 6.4 %
# What the fit achieved, on a render the same length as the reference file
# (2.00 s), recorded so a regression can see it move:
CY_FIT = dict(tau_s=0.243, t20_s=0.318, schroeder_t20_s=0.903,
              shares=(0.013, 0.063, 0.584, 0.153, 0.067),
              real_tau_s=0.2564, real_t20_s=0.3077, real_schroeder_t20_s=0.798,
              real_shares=(0.011, 0.103, 0.532, 0.233, 0.060))


# ---- what each constant above rests on, as data (#114) ------------------------
# The comments above are the justification and they stay: they say things no
# schema can. What follows is the machine-readable half, so that "which of
# these is inferred?" and "which fit had nothing held out?" are queries rather
# than a grep plus judgement -- the exact step that found #95 by someone going
# looking instead of the system pointing.
#
# STATUS is one of four, deliberately narrow:
#   derived-from-circuit  traceable to the hardware's own documentation: a
#                         component-value computation, a service-note
#                         statement, or Roland's published table. Nothing of
#                         ours was measured or fitted to obtain it.
#   measured              read off a recording of a real machine, and carrying
#                         the sample it was read from (n, spread, source, date).
#   inferred              nobody measured it and no source states it: a reading
#                         of the circuit that could be wrong.
#   fitted                searched against data, which is NOT a measurement of
#                         the quantity: it carries what it was fitted on and
#                         what, if anything, was held out.
#
# `prose_tag` records the comment tag an entry was migrated from, so the
# mapping from prose to status is itself auditable rather than assumed --
# and it does not always agree. TOM_DROP_RATIO's comment says HARDWARE-MEASURED
# and the value is the fitted law evaluated at a reference setting; the entry
# says `fitted` and records the difference.
#
# A constant that is NOT here has no provenance record: `provenance_of` raises
# UntaggedConstant rather than defaulting, and `unregistered_constants()` lists
# every one. This pass covers the constants whose comments carry one of the
# four prose tags; the remaining kit levels (AMP_TOM, CHART_VPP, BUS_TARGET and
# the other PEAK_*) are `drums_fx_render.py --balance` output and are still
# unregistered, which the listing says out loud instead of implying a status.
PROV_DERIVED = "derived-from-circuit"
PROV_MEASURED = "measured"
PROV_INFERRED = "inferred"
PROV_FITTED = "fitted"
PROV_STATUSES = (PROV_DERIVED, PROV_MEASURED, PROV_INFERRED, PROV_FITTED)
PROSE_TAGS = ("VERIFIED IN A SOURCE", "HARDWARE-MEASURED", "INFERRED", "FITTED")
HOLDOUT_NONE = "none"
# The registry's own vocabulary: module constants that describe provenance
# rather than the drum section, and so are not themselves candidates for it.
PROV_SCHEMA_NAMES = ("PROV_DERIVED", "PROV_MEASURED", "PROV_INFERRED", "PROV_FITTED",
                     "PROV_STATUSES", "PROSE_TAGS", "HOLDOUT_NONE",
                     "PROV_SCHEMA_NAMES", "PROVENANCE")


class UntaggedConstant(LookupError):
    """Asked for the provenance of a constant that has none.

    REFUSED, not a default: a constant with no record is exactly the case this
    registry exists to make visible, so answering `inferred` (or anything else)
    would put a guess where the absence of evidence belongs."""


@dataclasses.dataclass(frozen=True)
class Provenance:
    """What one constant rests on. `status` is closed; the rest is per status.

    A `measured` entry must carry its sample (n, spread, date) and a `fitted`
    one what it was fitted on and what was held out -- `holdout=HOLDOUT_NONE`
    where nothing was, which is a statement rather than an omission. Neither is
    a courtesy: a fit whose holdout field could be left empty is a fit that
    silently becomes a fact, which is what happened to the tom pitch drop."""

    status: str
    source: str
    justification: str
    prose_tag: str = ""
    n: int | None = None
    spread: str | None = None
    date: str | None = None
    fitted_on: str | None = None
    holdout: str | None = None
    docs: tuple[str, ...] = ()
    notes: str = ""

    def __post_init__(self):
        if self.status not in PROV_STATUSES:
            raise ValueError(f"status {self.status!r} is not one of {PROV_STATUSES}")
        if self.prose_tag and self.prose_tag not in PROSE_TAGS:
            raise ValueError(f"prose_tag {self.prose_tag!r} is not one of {PROSE_TAGS}")
        for field in ("source", "justification"):
            if not str(getattr(self, field) or "").strip():
                raise ValueError(f"every Provenance needs a {field}")
        if self.status == PROV_MEASURED:
            missing = [f for f in ("n", "spread", "date")
                       if getattr(self, f) is None or getattr(self, f) == ""]
            if missing:
                raise ValueError(f"a measured constant must carry its sample; "
                                 f"{', '.join(missing)} missing")
            if int(self.n) <= 0:
                raise ValueError(f"n = {self.n} is not a sample")
        if self.status == PROV_FITTED:
            missing = [f for f in ("fitted_on", "holdout") if not getattr(self, f)]
            if missing:
                raise ValueError(f"a fitted constant must say what it was fitted on and "
                                 f"what was held out ({HOLDOUT_NONE!r} if nothing); "
                                 f"{', '.join(missing)} missing")
        elif self.fitted_on or self.holdout:
            raise ValueError(f"only a {PROV_FITTED} constant has fitted_on / holdout")
        # `holdout` is HOLDOUT_NONE or names what was held out, and nothing in
        # between: the first draft of this registry said "none: one record, the
        # fit and its check are the same file", which reads as a confession and
        # compares as a holdout, so `fits_without_holdout()` missed the cymbal.
        if self.holdout and self.holdout != HOLDOUT_NONE \
                and self.holdout.strip().lower().startswith(HOLDOUT_NONE):
            raise ValueError(f"holdout {self.holdout!r} says {HOLDOUT_NONE!r} and then "
                             f"qualifies it; write holdout=HOLDOUT_NONE and put the "
                             f"reason in notes, or no query can count it")

    @property
    def has_holdout(self) -> bool:
        """A fit checked on data it was not chosen on. HOLDOUT_NONE is False."""
        return self.status == PROV_FITTED and self.holdout != HOLDOUT_NONE


_TOM_CORPUS = ("docs/tom-pitch-drop-measurement.md", "docs/tom-pitch-drop-law.json",
               "docs/tom-pitch-drop-correction.md")
_TOM_FIT_ON = ("152 usable rows of docs/tom-pitch-drop-results.json (89 tom, 63 conga) "
               "through model/tom_drop_fit.py: excess = max(0, K (accent - A0)) exp(G u), "
               "K = 0.18245")
_TOM_HOLDOUT = ("four splits in docs/tom-pitch-drop-law.json: accent B unseen, even "
                "tuning positions unseen, MT unseen, pot ends unseen (excess rms "
                "0.018-0.029, max 0.085)")
_TOM_CONGA_NOTE = ("nothing is held out of the conga terms: the four splits in "
                   "docs/tom-pitch-drop-law.json test the TOM law, and the conga rows "
                   "carry one usable accent level (B), the other sitting at the "
                   "measurement floor")
_CY_FILE = "Fischer s/n 103852, cy8/CY5025.WAV (TONE 5.0, DECAY 2.5)"
_CY_FIT_ON = ("four measurements of that one file at once: log-envelope tau over "
              "-3..-30 dB, t(-20 dB), the Schroeder T20 and a five-band energy split")
_CY_NOTE = ("nothing is held out: there is one record, so the fit and its check are the "
            "same file -- and matching the Schroeder T20 alone left the voice's audible "
            "decay 46 % long (tau 374 ms against 256)")
_CP_EXPERIMENT = ("the D12A final-strike experiment, frozen at 4059834 before any render "
                  "(docs/scorecard/clap-d12a/README.md section 10, final-strike.json); "
                  "selection used the #253 development offsets only")
_CP_HOLDOUT = ("8 fresh noise offsets (3301 5557 8803 10501 14009 16411 18503 20011) "
               "rendered after selection and never looked at before it: L2 passes 8/8 "
               "ratio and 8/8 decay, median |ratio err| 0.47 dB")

_PROVENANCE_TABLE = (
    # ---- rimshot and claves exciter levels (the comment above PEAK_RSX) ----
    (("PEAK_RSX",), Provenance(
        status=PROV_FITTED, prose_tag="HARDWARE-MEASURED",
        source="Fischer s/n 103852, rs8/RS.WAV through model/drum_verify.decay_fit",
        justification="the RS exciter level, set low so the distorted tap decays like "
                      "the machine's rimshot instead of the 22 ms gate",
        fitted_on="rs8/RS.WAV: the machine is tau 3.2 ms / t(-20 dB) 9.0 ms; ours "
                  "measures 5.55 / 13.85 ms at the usual 0.25 and 4.30 / 9.60 at 0.06",
        holdout=HOLDOUT_NONE, date="2026-09-18",
        notes="one file and one knob: the level was chosen against the same recording "
              "that defines the target, so nothing checks it out of sample"),),
    (("RS_LO_X_ATT",), Provenance(
        status=PROV_DERIVED, prose_tag="HARDWARE-MEASURED",
        source="the two impulse responses' closed forms at RS_LO_HZ/Q and "
               "RS_HI_HZ/Q -- bank 1/sin(w0) against circuit H0 w0/Q -- derived and "
               "swept by tools/probes/rs_mode_drive.py",
        justification="18.06 dB (att 3) on the pulse into the 455 Hz network, the "
                      "nearest of the path word's 6.02 dB steps to the +17.60 dB the "
                      "closed forms require; without it an equal pulse into both "
                      "bridged-T bodies puts our high mode 11.80 dB below the low "
                      "where the circuit's sits 5.79 dB above",
        docs=("docs/scorecard/results/D10A.json",),
        notes="DERIVED, and the comment above it carries HARDWARE-MEASURED: the +5.79 "
              "dB the circuit's own transfer functions predict is CONFIRMED by the "
              "Fischer s/n 103852 recording's +6.6 dB (rs8/RS.WAV), two independent "
              "routes agreeing to 0.7 dB -- but the recording is the check, not the "
              "source, and no value was fitted to it. Corrects a DISCRETISATION error, "
              "not a rimshot parameter: 1/sin(w0) is carried by every all-pole mode and "
              "only becomes audible where one voice sums two modes 1.5 octaves apart"),),
    (("PEAK_CLX",), Provenance(
        status=PROV_FITTED,
        source="model/drums_fx_render.py --balance against CHART_VPP (reference 1.6)",
        justification="the claves exciter level, balanced so CL alone peaks at its "
                      "share of Roland's chart",
        fitted_on="BUS_TARGET['CL'], i.e. the chart's normal Vpp for CL",
        holdout=HOLDOUT_NONE,
        notes="shares its assignment with PEAK_RSX, whose comment's "
              "HARDWARE-MEASURED tag is not a claim about this value"),),
    # ---- the bass drum ----
    (("BD_HZ",), Provenance(
        status=PROV_DERIVED, prose_tag="VERIFIED IN A SOURCE",
        source="docs/tr808-reference.md section 2 / W14a section 5, computed with "
               "section 1.2 from R161, R165, R166, R170, C41/C42",
        justification="the bridged-T's f0, 49.4 Hz; Werner measures ~49.5 and two "
                      "sample sets corroborate 49-51 Hz",
        docs=("docs/tr808-reference.md",),
        notes="DR 0009 resolves the conflict with Roland's chart (BD_HZ_CHART) in "
              "favour of this value: section 2's Q table satisfies tau = Q/(pi f0) to "
              "1.5 % here and to 12.8 % at 56 Hz"),),
    (("BD_HZ_CHART",), Provenance(
        status=PROV_DERIVED, prose_tag="VERIFIED IN A SOURCE",
        source="Roland's tuning chart via docs/tr808-reference.md section 2",
        justification="the chart's 56 Hz, kept for comparison and not used to tune "
                      "the BD",
        notes="the reference calls the chart figure 'typical and variable' and tells "
              "the implementer to treat 50-56 Hz as the target"),),
    (("BD_DECAY_Q",), Provenance(
        status=PROV_DERIVED, prose_tag="VERIFIED IN A SOURCE",
        source="docs/tr808-reference.md section 2 / W14a section 6: the VR6 feedback "
               "buffer's own law",
        justification="Q against the DECAY knob position, the reference's own table "
                      "(the panel's 0..10 against VR6's 0..1)"),),
    (("BD_ATTACK_HZ", "BD_ATTACK_Q", "BD_ATTACK_MS"), Provenance(
        status=PROV_DERIVED, prose_tag="VERIFIED IN A SOURCE",
        source="docs/tr808-reference.md section 2 / W14a section 8.1; SN p.6",
        justification="while Q43 shorts R165 the foot resistance falls and the SAME "
                      "resonator rises to ~130 Hz at Q ~6 for ~4 ms",
        notes="the SN gives 4 ms for Q43's ON period; Werner measures ~6 ms"),),
    # ---- the toms' diode pitch drop ----
    (("TOM_DROP_RATIO",), Provenance(
        status=PROV_FITTED, prose_tag="HARDWARE-MEASURED",
        source="docs/tom-pitch-drop-law.json, from 99 tom and 66 conga files of a real "
               "TR-808 (808 From Mars) measured by model/tom_pitch_probe.py",
        justification="onset / settled at the stated reference setting -- accent 1.0, "
                      "TUNING pot centre, TOM position -- and still the one knob that "
                      "scales the whole sweep",
        fitted_on=_TOM_FIT_ON, holdout=_TOM_HOLDOUT, date="2026-09-18",
        spread="the corpus's largest drop in any of the 99 files is x1.344; x1.7, which "
               "this constant used to be, occurs nowhere in it",
        docs=_TOM_CORPUS,
        notes="FITTED, and the comment above it says 'measured': 1.060 is the law "
              "evaluated at accent 1.0 (excess 0.0602, = K (1 - A0)) where that cell's "
              "own measured median excess is 0.0543, i.e. x1.054. The difference is "
              "small and the label is not -- test_tom_drop_law pins it to the law"),),
    (("TOM_DROP_ACCENT_0",), Provenance(
        status=PROV_FITTED, prose_tag="HARDWARE-MEASURED",
        source="docs/tom-pitch-drop-law.json, tom rows",
        justification="the accent below which the germanium diodes do not conduct: the "
                      "drop has a THRESHOLD, not the old min(max(accent, 0), 1) clamp "
                      "that handed an unaccented hit the full drop",
        fitted_on=_TOM_FIT_ON, holdout=_TOM_HOLDOUT, date="2026-09-18",
        spread="measured excess 0.054 / 0.143 / 0.239 at the three recorded accent "
               "levels: a straight line that does not pass through the origin",
        docs=_TOM_CORPUS),),
    (("TOM_DROP_ACCENT_0_CONGA",), Provenance(
        status=PROV_FITTED, prose_tag="HARDWARE-MEASURED",
        source="docs/tom-pitch-drop-law.json, conga rows",
        justification="the conga position's own threshold: HT and LC are both nominally "
                      "185 Hz on one bridged-T with a capacitor switched, and unaccented "
                      "their drops differ by 11x (excess 0.061 against 0.0055)",
        fitted_on="the conga rows' Accent level only, with K and G held: A0 = 1.0636, "
                  "which predicts no drop at all unaccented (measured 0.0041)",
        holdout=HOLDOUT_NONE, date="2026-09-18", docs=_TOM_CORPUS,
        notes=_TOM_CONGA_NOTE),),
    (("TOM_DROP_TUNING_G",), Provenance(
        status=PROV_FITTED, prose_tag="HARDWARE-MEASURED",
        source="docs/tom-pitch-drop-law.json, tom rows",
        justification="d ln(excess) / d (f0 / f0_nominal) for the tom position: the "
                      "TUNING pot the old sequence ignored (LT at More Accent runs "
                      "x1.169 at 82 Hz and x1.325 at 101 Hz)",
        fitted_on="one slope over nine cells, log excess ~ cell + G u: 3.584 +- 0.142, "
                  "rms_log 0.085",
        holdout=_TOM_HOLDOUT, date="2026-09-18", docs=_TOM_CORPUS,
        notes="G is fitted per POSITION, not pooled: 3.58 for the toms against 7.46 for "
              "the congas is not a disagreement to average away"),),
    (("TOM_DROP_TUNING_G_CONGA",), Provenance(
        status=PROV_FITTED, prose_tag="HARDWARE-MEASURED",
        source="docs/tom-pitch-drop-law.json, conga rows",
        justification="the conga position's tuning slope, about twice the tom's",
        fitted_on="the conga cells' Accent level: 7.461 +- 0.085, rms_log 0.032",
        holdout=HOLDOUT_NONE, date="2026-09-18", docs=_TOM_CORPUS,
        notes=_TOM_CONGA_NOTE),),
    (("TOM_DROP_TUNING_SPAN",), Provenance(
        status=PROV_DERIVED,
        source="docs/tr808-reference.md 1.7: the TUNING pot spans +-10 %",
        justification="the tuning term is clamped to the pot's own range, which is also "
                      "the span the 99 measured files cover",
        notes="past it the law would extrapolate outside every file it was fitted to"),),
    (("TOM_DROP_MS", "TOM_DROP_STEPS"), Provenance(
        status=PROV_INFERRED,
        source="contract 15.7.1 as shipped at revision 5",
        justification="exp(-3 t / 60 ms), i.e. tau 20 ms, kept deliberately: the "
                      "exponential beat a linear ramp in 88 of 89 measured rows",
        docs=("docs/tom-pitch-drop-correction.md",),
        notes="the machine's relaxation tau is accent-dependent (13 / 24.5 / 33 ms at "
              "the three levels) and this law is not -- a reported deviation, not an "
              "oversight"),),
    # ---- the snare (#385) ----
    (("SD_NOISE_HZ", "SD_NOISE_Q", "SD_NOISE_AMP"), Provenance(
        status=PROV_FITTED, prose_tag="VERIFIED IN A SOURCE",
        source="docs/tr808-reference.md section 3 ('2-pole HP 2.75 kHz Q 0.7') and "
               "docs/drum-verification.md section 8.1",
        justification="the snappy noise path's pole (2.75 kHz, Q 0.7, unchanged from the "
                      "source) read as a BAND-PASS instead of the source's stated "
                      "high-pass, plus the amp the mode is driven at",
        fitted_on="section 8.1: the same pole read as a band-pass fits the machine's "
                  "measured noise-band spectrum to 1.9 dB weighted rms, against the "
                  "high-pass's 5.2 dB, matching the best unconstrained single mode "
                  "(2938 Hz Q 0.75, 1.90 dB) and within 0.1 dB of a two-mode cascade; "
                  "SD_NOISE_AMP is set by drums_fx_render.py --balance to the SNAPPY "
                  "knob's measured transfer curve at its 5.0 point (noise/tone amplitude "
                  "0.619, -4.2 dB, mean over 25 files x five TONE positions)",
        holdout=HOLDOUT_NONE, date="2026-09-18",
        notes="none of the four labels fits this whole entry cleanly. The pole's numbers "
              "(2.75 kHz, Q 0.7) ARE VERIFIED IN A SOURCE and unchanged; what's fitted is "
              "the TOPOLOGY reading the source leaves open -- spec/NUMERIC-CONTRACT.md's "
              "17.22 records that the schematic reading behind it is still not settled -- "
              "and the amp, which the balance procedure sets rather than a raw sample. "
              "FITTED is the closer of the four because both the topology choice and the "
              "amp are searches against data, not a transcription of one; the pole's own "
              "exact match to source is recorded here in the justification instead of "
              "under its own status so the whole mode_writes() call keeps one entry"),),
    (("SD_HI_HZ", "SD_HI_Q", "SD_HI_AMP"), Provenance(
        status=PROV_FITTED,
        source="docs/drum-verification.md section 8.6 ('The balance')",
        justification="SD's second body mode: TONE is this pair's amplitude ratio over "
                      "SD_LO's, and rev 6 shipped 0.394 (-8.1 dB) -- 11 dB of the snare's "
                      "front end missing",
        fitted_on="a(336)/a(173) on the SNAPPY-up and SNAPPY-down files at TONE 5.0 "
                  "(1.43x / 1.41x, mean 1.42x, +3.0 dB); the pair is then scaled together "
                  "to the kit's own peak, unchanged at 0.46 FS",
        holdout=HOLDOUT_NONE, date="2026-09-18",
        notes="the comment's bare 'MEASURED (17.24)' names the machine's ratio, not this "
              "register value directly: SD_HI_AMP is set so the pair REPRODUCES that "
              "ratio in this model, which is a fit against a measured target rather than "
              "a sample expressed in the same units -- the same shape as TOM_DROP_RATIO. "
              "SD_HI_HZ/SD_HI_Q are the circuit's existing body-mode frequency and Q, "
              "carried into this entry because they share the one mode_writes() call and "
              "its one comment; nothing in this file additionally sources them"),),
    (("SD_NOISE_TAU", "SD_NOISE_PEAK"), Provenance(
        status=PROV_FITTED,
        source="docs/drum-verification.md section 8.6 ('The burst')",
        justification="the SD noise envelope's rate: reference 3 infers 15.5 ms from "
                      "R186 x C51, the CHARGE path, but the machine's discharge burst "
                      "measures 30-40 ms",
        fitted_on="short-time-RMS T20 of the snappy burst over six files -- SD2550 "
                  "63.2 ms, SD5050 69.3, SD5075 67.5, SD7550 67.6, SD1050 67.4, SD5010 "
                  "77.9 ms -- with a damped-envelope tau fit on the three whose R^2 >= "
                  "0.97 (29.1 / 30.0 / 34.2 ms); SD_NOISE_PEAK holds the noise share at "
                  "the machine's measured 27.7 %, section 8.1's SNAPPY-5.0 point",
        holdout=HOLDOUT_NONE, date="2026-09-18",
        notes="one corpus supplies both the target and the check, so nothing here is "
              "held out. Contract item 17.25 records that whether the discharge path is "
              "a different resistance from reference 3's 15.5 ms charge estimate, or "
              "Q48's VCA law stretches the envelope, is still open"),),
    # ---- the toms and congas themselves ----
    (("TOM_PRESET",), Provenance(
        status=PROV_INFERRED, prose_tag="INFERRED",
        source="docs/tr808-reference.md section 4's component table (R1/R2/C1/C2 with "
               "the germanium diodes open)",
        justification="the (f0, Q) of each of the six positions; the table is "
                      "self-consistent under tau = Q / (pi f0) to better than 5 %",
        notes="SW8's one-circuit fact is VERIFIED IN A SOURCE (SN p.6), the numbers are "
              "not. The three CONGA Q values here are no longer reference 4's: they are "
              "Q = pi f0 tau at the chart f0 from TOM_HW_TAU's measurement, which is a "
              "`measured` basis inside an `inferred` table -- per-name granularity "
              "cannot say that, so it is said here"),),
    (("TOM_HW_TAU",), Provenance(
        status=PROV_MEASURED, prose_tag="HARDWARE-MEASURED",
        source="Fischer s/n 103852, one file per position (LC50/MC50/HC50.WAV named in "
               "the comment) through model/drum_verify.decay_fit over -3..-30 dB",
        justification="the measured amplitude time constant of each tom and conga "
                      "position, which amends reference 4's inferred conga Q column",
        n=6,
        spread="R^2 0.999 on all three congas; the three TOM rows agree with reference "
               "4's computed tau inside 3 % (88.4/87.6, 56.6/57.7, 43.0/41.7 ms) and "
               "the three CONGA rows are 12-30 % shorter than it",
        date="2026-09-18",
        notes="f0 is left at the chart's: the machine reads 200 / 282 / 413 Hz for the "
              "congas, inside reference 1.7's +-10 %"),),
    # ---- rimshot, claves, maracas ----
    (("RS_LO_HZ", "RS_LO_Q", "RS_HI_HZ", "RS_HI_Q"), Provenance(
        status=PROV_INFERRED, prose_tag="INFERRED",
        source="docs/tr808-reference.md section 5, from R315/R316/C115/C116 and the "
               "switch wiring on SN p.9",
        justification="the two bridged-T networks of the RS/CL circuit: 455 Hz Q 6.7 "
                      "and 1786 Hz Q 13.5",
        notes="the chart's RS 'H' 1667 / 'L' 455 Hz is VERIFIED IN A SOURCE; the "
              "schematic's 1786 is preferred to it (7 % apart, inside reference 1.7's "
              "+-10 %) on DR 0009's rule"),),
    (("CL_HZ", "CL_Q"), Provenance(
        status=PROV_INFERRED, prose_tag="INFERRED",
        source="docs/tr808-reference.md section 5: IC20b's feedback wired for high Q",
        justification="2500 Hz is the chart's; Q 200 is a reading of 'high Q' and no "
                      "source states a number",
        notes="what stops the claves is RS_GATE_TAU's ~22 ms window, not this Q -- the "
              "resonator alone would ring for 25 ms"),),
    (("RS_GATE_TAU",), Provenance(
        status=PROV_DERIVED, prose_tag="VERIFIED IN A SOURCE",
        source="SN, 'this switching is provided to eliminate noise leaking from IC20': "
               "JFET Q74 through C112 0.022 uF / R305 1 MOhm",
        justification="the ~22 ms gate window both RS and CL are gated by"),),
    (("MA_HP_HZ", "MA_HP_Q"), Provenance(
        status=PROV_INFERRED, prose_tag="INFERRED",
        source="docs/tr808-reference.md section 8: Sallen-Key on Q68 with "
               "C132 = C133 = 0.001 uF, R339 3.3 k, R340 68 k",
        justification="a 2-pole high-pass at 10.6 kHz, Q 2.3"),),
    (("MA_TAU",), Provenance(
        status=PROV_INFERRED, prose_tag="INFERRED",
        source="docs/tr808-reference.md section 8: R341 470 k / C134 0.033 uF (~15 ms) "
               "against the chart's 25-35 ms decay",
        justification="tau 12 ms (T20 28 ms) is the value that satisfies both"),),
    # ---- the cowbell (#385) ----
    (("CB_BP_HZ", "CB_BP_Q", "CB_BP_AMP"), Provenance(
        status=PROV_FITTED,
        source="spec/decision-records/0010-one-gate-per-oscillator.md",
        justification="the cowbell's band-pass, closing reference 9's open item -- "
                      "reference 5's 900 Hz / Q 4 was a choice and Sound On Sound's "
                      "2.64 kHz is refuted",
        fitted_on="a 2-pole band-pass fit to 16 identified partials of the reference "
                  "unit, with the duty cycle and the two gates' relative level free "
                  "(rms residual 2.8 dB over 16 partials)",
        holdout=HOLDOUT_NONE, date="2026-09-18",
        notes="DR 0010 does not mention a held-out check: the fit and its 2.8 dB "
              "residual are reported on the same 16 partials, the same shape as "
              "PEAK_RSX / PEAK_CYL's single-record fits"),),
    (("CB_TAU_B",), Provenance(
        status=PROV_FITTED,
        source="spec/decision-records/0010-one-gate-per-oscillator.md",
        justification="the cowbell tail, replacing the previous 30 ms",
        fitted_on="least-squares fit of the log envelope over -3..-30 dB (tau 98 ms; "
                  "T20 = 2.303 tau ~= 226 ms)",
        holdout=HOLDOUT_NONE, date="2026-09-18",
        notes="DR 0010 does not mention a held-out check, so nothing here is held "
              "out either -- one record, and the fit and its report are the same "
              "file"),),
    # ---- cymbal ----
    (("CY_LO_HZ",), Provenance(
        status=PROV_DERIVED, prose_tag="VERIFIED IN A SOURCE",
        source="W14b section 4, 'around 3440 Hz'; SN p.13 R56/R57/C13/C14",
        justification="the cymbal's low band-pass; the six-square sum is band-passed by "
                      "two filters and the 7.1 kHz one is the hats' band as well"),),
    (("CY_Q",), Provenance(
        status=PROV_INFERRED, prose_tag="INFERRED",
        source="docs/tr808-reference.md section 10",
        justification="Q 6 for both cymbal band-passes; no source states it"),),
    (("CY_HI_HZ",), Provenance(
        status=PROV_DERIVED,
        source="docs/tr808-reference.md section 10's Hh3, 'resonant ~10.5 kHz'",
        justification="the high band's post filter, and a BAND-pass: an 11.7 kHz "
                      "two-pole HIGH-pass puts 42 % of its output above 13 kHz where "
                      "the machine has 6 %",
        notes="which two of reference 10's three post-filters the bank hosts was settled "
              "by measurement on " + _CY_FILE + ": keeping Hh3 over Hh1 fits the band "
              "split about twice as well (cost 0.56 against 1.48). Hh1 is the documented "
              "omission"),),
    (("CY_HI_Q",), Provenance(
        status=PROV_INFERRED,
        source="docs/tr808-reference.md section 10 (Hh3's Q is unspecified there)",
        justification="Q 2.5, the same reading as the hats' high-passes"),),
    (("CY_TAU_SHORT", "CY_TAU_DECAY", "CY_TAU_LOW",
      "PEAK_CYS", "PEAK_CYD", "PEAK_CYL"), Provenance(
        status=PROV_FITTED, prose_tag="FITTED",
        source=_CY_FILE,
        justification="the cymbal's three envelopes AND their levels, searched against "
                      "the recording rather than derived from reference 10",
        fitted_on=_CY_FIT_ON, holdout=HOLDOUT_NONE, date="2026-09-18",
        notes=_CY_NOTE + ". Fitting any one of the four measurements alone moves the "
              "others the wrong way. The recording's identity was itself wrong here for "
              "months (labelled DECAY 5.0; it is DECAY 2.5) and was corrected under #102 "
              "without re-measuring anything: what moved is what the file is, and "
              "therefore what this fit generalises to"),),
    (("CY_DECAY_T20",), Provenance(
        status=PROV_MEASURED, prose_tag="HARDWARE-MEASURED",
        source="Fischer s/n 103852, cy8/CY50{00,25,50,75,10}.WAV -- TONE 5.0, the second "
               "code being DECAY, so '10' is knob 10.0",
        justification="the cymbal's Schroeder T20 against the DECAY knob",
        n=5,
        spread="435 / 798 / 1281 / 1674 / 1888 ms, monotonic in the knob once the "
               "filename codes are decoded (the recordist's own file lengths -- 1.50 / "
               "2.00 / 2.50 / 3.50 / 4.00 s -- confirm the decode independently)",
        date="2026-09-18",
        notes="kept as the record of what was measured, NOT as a law to fit: a backward "
              "integral over a record that ends before the decay does reports the cut "
              "(#118), and docs/scorecard/results/D14A.json records 'total decay' as "
              "INVALID on the reference side of CY5025. The fitted DECAY law lives in "
              "test_discrimination.fit_laws and uses measure_tau, which needs only "
              "27 dB of record. RE-LABELLED under #102; nothing re-measured"),),
    (("CY_FIT",), Provenance(
        status=PROV_FITTED, prose_tag="FITTED",
        source=_CY_FILE,
        justification="what the cymbal fit achieved, recorded so a regression can see it "
                      "move: ours tau 243 / t(-20 dB) 318 / Schroeder T20 903 ms against "
                      "the machine's 256 / 308 / 798",
        fitted_on=_CY_FIT_ON, holdout=HOLDOUT_NONE, date="2026-09-18",
        notes="the real_* fields are the measurement, not the fit: they are CY5025's own "
              "numbers, on a render the same length as the reference file. " + _CY_NOTE),),
    # ---- the clap (contract revision 14) ----
    (("CP_TAIL_TAU",), Provenance(
        status=PROV_MEASURED,
        source="docs/scorecard/clap-d12a/README.md section 10 (final-strike.json)",
        justification="the clap tail's 80 ms, replacing reference 7's R348 x C138 "
                      "estimate of 47 ms",
        n=1, spread="none stated: one record", date="2026-09-26",
        notes="its prose tag is a bare 'MEASURED (plan084)' beside the kit_808() write, "
              "not one of the four block tags"),),
    (("CP_BURST_TAU", "CP_BURSTS", "CP_PERIOD", "CP_FINAL_TAU"), Provenance(
        status=PROV_FITTED,
        source="docs/scorecard/clap-d12a/README.md section 10 (final-strike.json)",
        justification="four strikes at period 511 frames (0, 10.6, 21.3, 31.9 ms), the "
                      "first three at the kept 4 ms decay and 13/16 re-strike and the "
                      "LAST at the accent-scaled fire level decaying at 20 ms",
        fitted_on=_CP_EXPERIMENT, holdout=_CP_HOLDOUT, date="2026-09-26",
        notes="the one fit here with a real holdout: the level was selected on the "
              "development offsets and confirmed on eight offsets nobody had looked at"),),
)

PROVENANCE: dict[str, Provenance] = {n: p for names, p in _PROVENANCE_TABLE for n in names}


def provenance_of(name: str) -> Provenance:
    """What constant `name` rests on, or REFUSE.

    Raises UntaggedConstant for a constant with no record, including one that
    does not exist: both are 'this registry cannot tell you', and neither is a
    status."""
    try:
        return PROVENANCE[name]
    except KeyError:
        raise UntaggedConstant(
            f"{name}: no provenance record in model/drums_fx.py. Add one to "
            f"PROVENANCE (statuses: {', '.join(PROV_STATUSES)}) rather than reading the "
            f"constant as though its basis were known") from None


def constants_by_status() -> dict[str, tuple[str, ...]]:
    """Every registered constant grouped by status, in registry order."""
    return {s: tuple(n for n, p in PROVENANCE.items() if p.status == s)
            for s in PROV_STATUSES}


def fits_without_holdout() -> tuple[str, ...]:
    """Every fitted constant checked only on what it was fitted to."""
    return tuple(n for n, p in PROVENANCE.items()
                 if p.status == PROV_FITTED and not p.has_holdout)


def _source_text() -> str:
    with open(os.path.abspath(__file__), encoding="utf-8") as fh:
        return fh.read()


def _module_assignments(src: str = None):
    """(line, names) for every module-level assignment, in source order."""
    import ast
    out = []
    for node in ast.parse(src if src is not None else _source_text()).body:
        targets = (node.targets if isinstance(node, ast.Assign)
                   else [node.target] if isinstance(node, ast.AnnAssign) else [])
        names = []
        for t in targets:
            elts = t.elts if isinstance(t, (ast.Tuple, ast.List)) else [t]
            names += [e.id for e in elts if isinstance(e, ast.Name)]
        if names:
            out.append((node.lineno, tuple(names)))
    return out


def module_constants(src: str = None) -> tuple[str, ...]:
    """Every module-level ALL-CAPS constant, in source order."""
    seen = []
    for _, names in _module_assignments(src):
        seen += [n for n in names if n == n.upper() and n[0].isalpha() and n not in seen]
    return tuple(seen)


def unregistered_constants(src: str = None) -> tuple[str, ...]:
    """Module constants with no provenance record, the registry's own aside.

    Not an accusation: the register map's field widths rest on the contract, not
    on evidence about a circuit. It is the list a reader needs before treating
    the registry as complete."""
    return tuple(n for n in module_constants(src)
                 if n not in PROVENANCE and n not in PROV_SCHEMA_NAMES)


def prose_tagged_constants(src: str = None) -> dict[str, tuple[str, ...]]:
    """{constant: prose tags} read out of the comments, not the registry.

    The comment block is the run of `#` lines immediately above an assignment,
    and only the FIRST assignment under a block is attributed to it -- which is
    how the comments read, and why this cannot be a grep: a tag names the
    constant under it, while a grep names a line number. It exists so that
    adding a tagged constant and forgetting to register it turns a test red."""
    src = src if src is not None else _source_text()
    lines = src.splitlines()
    out = {}
    for line, names in _module_assignments(src):
        block = []
        i = line - 2
        while i >= 0 and lines[i].lstrip().startswith("#"):
            block.append(lines[i])
            i -= 1
        text = "\n".join(block)
        tags = tuple(t for t in PROSE_TAGS if t in text)
        if tags:
            for n in names:
                if n not in PROV_SCHEMA_NAMES:      # the header above the vocabulary
                    out[n] = tags                   # quotes all four tags by name
    return out


def bd_decay_q(knob: float) -> float:
    """Q of the BD body mode for a DECAY knob position in 0..10, from the
    reference's own table, interpolated geometrically (the law is a resistive
    divider's, and log Q tracks the tabulated points to 1 % where they are
    dense). Float; host side only."""
    ks = sorted(BD_DECAY_Q)
    k = min(max(float(knob), ks[0]), ks[-1])
    return float(math.exp(np.interp(k, ks, [math.log(BD_DECAY_Q[x]) for x in ks])))


def bd_attack_writes(frame: int, restore: list = None) -> list:
    """The BD attack window as host writes (reference section 2): the body
    mode is retuned to BD_ATTACK_HZ / BD_ATTACK_Q in the frame of the hit and
    back after BD_ATTACK_MS. Two writes each way -- a1 and a2 only, because
    the level and the numerator do not move: the circuit shorts a resistor,
    it does not change the gain.

    `restore` is the (a1, a2) register PAIR to write back, and the caller
    passes what was in the image before the hit. In the circuit Q43 shorts
    R165 and then releases, so the resonator returns to whatever the DECAY
    knob currently sets -- NOT to a fixed preset. Recomputing the preset here
    would silently overwrite a host that had retuned the decay, which is
    exactly what it did to `test_808_acceptance.bd_at_decay`. Omitted, it
    restores the kit's own DECAY 5.0 setting."""
    n = int(round(BD_ATTACK_MS * 1e-3 * SR))
    hot = mode_writes(M_BD, BD_ATTACK_HZ, BD_ATTACK_Q, 0.0)[:2]
    if restore is None:
        restore = [v for _, v in mode_writes(M_BD, BD_HZ, bd_decay_q(5.0), 0.0)[:2]]
    base = A_MODE + M_BD * MODE_STRIDE
    return ([(frame, a, v) for a, v in hot]
            + [(frame + n, base + i, v) for i, v in enumerate(restore)])


# Which of a circuit's two positions the panel switch is in. The pair share one
# bridged-T (SW8) and their nominal frequencies are 2x apart, so the tuning the
# host has written says which one is selected -- and it must, because the two
# positions have different pitch-drop thresholds and the same mode number.
TOM_PAIR = {M_LT: ("LT", "LC"), M_MT: ("MT", "MC"), M_HT: ("HT", "HC")}


def tom_position(mode: int, f0_hz: float) -> str | None:
    """'LT'/'LC'/... for a tom circuit tuned to f0_hz, else None.

    Nearest of the circuit's OWN two nominals in log frequency -- only the two,
    never all six: LC and HT are both nominally 185 Hz and a global nearest
    would confuse them."""
    names = TOM_PAIR.get(mode)
    if not names:
        return None
    if not (f0_hz > 0.0):
        return names[0]
    return min(names, key=lambda n: abs(math.log(f0_hz / TOM_PRESET[n][0])))


def tom_drop_excess(mode: int, f0_hz: float, accent: float) -> float:
    """The onset excess of the diode pitch drop: f0 starts at
    f0_hz * (1 + excess) and relaxes back.

        excess = TOM_DROP_RATIO_EXCESS
                 * max(0, accent - A0) / (1 - TOM_DROP_ACCENT_0)
                 * exp(G * (f0 / f0_nominal - 1))

    Three terms, one per fault the measurement found: the magnitude, the
    accent THRESHOLD in place of the old clamp, and the TUNING pot the old
    sequence ignored. A0 and G are the selected position's -- tom or conga.
    Normalised so that a tom at accent 1.0 with the pot at its centre gives
    exactly TOM_DROP_RATIO, which is therefore still the one knob that scales
    the whole sweep (and is still what a probe monkey-patching it moves).

    An unknown mode, or a hit at or below the diodes' threshold, gives 0.0 --
    no sweep at all, which is what the machine does to an unaccented conga."""
    name = tom_position(mode, f0_hz)
    if name is None:
        return 0.0
    conga = name in ("LC", "MC", "HC")
    a0 = TOM_DROP_ACCENT_0_CONGA if conga else TOM_DROP_ACCENT_0
    g = TOM_DROP_TUNING_G_CONGA if conga else TOM_DROP_TUNING_G
    drive = max(0.0, accent - a0) / (1.0 - TOM_DROP_ACCENT_0)
    if drive <= 0.0:
        return 0.0
    u = f0_hz / TOM_PRESET[name][0] - 1.0
    u = min(max(u, -TOM_DROP_TUNING_SPAN), TOM_DROP_TUNING_SPAN)
    return (TOM_DROP_RATIO - 1.0) * drive * math.exp(g * u)


def tom_pitch_drop_writes(frame: int, mode: int, f0_hz: float, q: float, amp: float,
                          accent: float = 1.0) -> list:
    """The toms' diode pitch drop as host writes (reference section 4): f0
    starts at (1 + `tom_drop_excess`) x its small-signal value and relaxes back
    over TOM_DROP_MS in TOM_DROP_STEPS. Q is held: the diodes move the foot
    resistance, which the reference treats as an f0 effect.

    Emits the steps even when the excess is zero -- the write count is part of
    15.7.1 and of the link budget (`fpga/link_budget.py`), and a sequence whose
    length depended on the accent would make a host's timing depend on what it
    played. They are then writes of the settled coefficients, which is what the
    machine's own foot resistance is doing.

    EACH STEP HOLDS THE INTERVAL'S MEAN, NOT ITS LEFT EDGE. A coefficient
    written at t_i is held until t_{i+1}, so writing exp(-3 t_i / T) holds the
    curve's HIGHEST value across the whole interval and the staircase sits
    above the law everywhere. Over six steps that is not a rounding error: the
    left-edge hold reads back 22-38 % high through `tom_pitch_probe` and misses
    the law by 0.0055 rms in f0 where the interval mean misses it by 0.0029 --
    half the error, at the same six steps and the same fourteen writes. The
    mean of exp(-3t/T) over one step of T/S is (S/3)(e^(-3i/S) - e^(-3(i+1)/S));
    the last write is the endpoint itself, because nothing is held after it.
    Measured three ways in docs/tom-pitch-drop-correction.md."""
    out = []
    excess = tom_drop_excess(mode, f0_hz, accent)
    s = TOM_DROP_STEPS
    for i in range(s + 1):
        if i < s:
            shape = (s / 3.0) * (math.exp(-3.0 * i / s) - math.exp(-3.0 * (i + 1) / s))
        else:
            shape = math.exp(-3.0)
        hz = f0_hz * (1.0 + excess * shape)
        f = frame + int(round(i / s * TOM_DROP_MS * 1e-3 * SR))
        out += [(f, a, v) for a, v in mode_writes(mode, hz, q, amp)[:2]]
    return out


# ---- the hats' two filter Qs, registered tunables (#559) ----------------------
# Integers x10 at column 0 so tools/sensitivity.py can read what ships
# (docs/sensitivity/registry.json: ch-hp-q, hat-bp-q). The shipped values are
# reference 10/11's readings, both marked [inferred] there: Q 6 from R58/R59
# (the bridged-T formula, without the stage's input C11/R55 network) and Q 2.5
# from R153/R155 under an equal-C unity-gain Sallen-Key reading of Q31.
HAT_BP_Q_X10 = 60
CH_HP_Q_X10 = 25
# #559's CANDIDATE, DISABLED: NOT what kit_808() loads. The CH high-pass Q
# alone, selected on DEV conditions and confirmed on untouched ones against the
# #379 gate's WEAK bar (docs/scorecard/chcp-559/README.md). The hat band-pass Q
# (HAT_BP_Q_X10 30) improved CH further and OH too, but regressed CY, so it is
# NOT in the candidate. It stays off until #379/#560 qualify an acceptance
# policy AND the image is proven to play it (model -> RTL -> I2S, deadlines):
# until then `kit_808_candidate_559()` is how to hear or verify it.
CANDIDATE_559 = {"CH_HP_Q_X10": 5}


def kit_808(tuning: dict | None = None) -> list:
    """The reference kit as a list of (addr, value) writes: every number is
    docs/tr808-reference.md's where it gives one (tagged there), and marked
    'chosen' here where it does not. Levels (`amp`, peaks) are balanced by
    `model/drums_fx_render.py --balance` so that each voice alone peaks near
    -6 dBFS on its bus at accent 1.0, in the proportions of Roland's tuning
    chart; they are the kit's, not the circuit's.

    `tuning` overrides the registered tunables by name (HAT_BP_Q_X10,
    CH_HP_Q_X10) and nothing else; None, the default, is the shipped kit
    exactly. An unknown name REFUSES rather than being ignored."""
    t = {"HAT_BP_Q_X10": HAT_BP_Q_X10, "CH_HP_Q_X10": CH_HP_Q_X10}
    for k, v in (tuning or {}).items():
        if k not in t:
            raise KeyError(f"kit_808 tuning: {k!r} is not a registered tunable ({', '.join(t)})")
        t[k] = int(v)
    w = []
    for i, hz in enumerate(OSC_HZ):
        w.append((A_OSC + i, osc_inc_reg(hz)))
    # modes: filters first (numerators), then the bodies
    w += mode_writes(M_HATBP, 7117.0, t["HAT_BP_Q_X10"] / 10, 0.0, BP)   # hats' band-pass, reference 10/11; tapped only
    w += mode_writes(M_OHHP, 7800.0, 2.5, 0.45, HP)          # OH high-pass, reference 11
    w += mode_writes(M_CHHP, 11700.0, t["CH_HP_Q_X10"] / 10, 0.69, HP)   # CH high-pass, reference 11
    w += mode_writes(M_SDN, SD_NOISE_HZ, SD_NOISE_Q, SD_NOISE_AMP, BP)   # SD snappy filter, reference 3
    w += mode_writes(M_CPBP, 1071.0, 1.6, 0.0, BP)           # CP band-pass, reference 7; tapped only
    w += mode_writes(M_CBBP, CB_BP_HZ, CB_BP_Q, CB_BP_AMP, BP)   # CB band-pass, reference 9 (DR 0010)
    w += mode_writes(M_BD, BD_HZ, bd_decay_q(5.0), 0.003309, RAW)   # BD at DECAY 5.0. f0 and Q are both
                                                             # VERIFIED IN A SOURCE (reference 2's
                                                             # component-value f0 and its own Q table),
                                                             # not the chart's 56 Hz -- DR 0009; two
                                                             # sample sets corroborate at 48.8-51.6 Hz
    w += mode_writes(M_SDLO, 173.0, 16.3, 0.002673, RAW)      # SD low, later units, reference 3
    w += mode_writes(M_SDHI, SD_HI_HZ, SD_HI_Q, SD_HI_AMP, RAW)   # SD high; TONE = this pair's ratio
    w += mode_writes(M_LT, *TOM_PRESET["LT"][:2], AMP_TOM["LT"], RAW)   # LT, reference 4
    w += mode_writes(M_MT, *TOM_PRESET["MT"][:2], AMP_TOM["MT"], RAW)   # MT, reference 4
    w += mode_writes(M_HT, *TOM_PRESET["HT"][:2], AMP_TOM["HT"], RAW)   # HT, reference 4
    # RS / CL: two bridged-T networks on one circuit (reference 5). Both are
    # TAPPED, not mixed -- their sum goes through the swing VCA, so amp is 0.
    w += mode_writes(M_RS1, RS_LO_HZ, RS_LO_Q, 0.0, RAW)
    w += mode_writes(M_RS2, RS_HI_HZ, RS_HI_Q, 0.0, RAW)
    # CY: the low band-pass (the high one is M_HATBP, shared with the hats) and
    # the low band's post high-pass, which is the only cymbal filter that
    # reaches the body bus on its own amp.
    w += mode_writes(M_CYBP, CY_LO_HZ, CY_Q, 0.0, BP)          # tapped only
    w += mode_writes(M_CYHI, CY_HI_HZ, CY_HI_Q, AMP_CY_HI, BP)
    # envelopes: the pulse-shaper's kick is a 0.1 ms exponential (reference 2, "what to implement");
    # the bodies' exciters are 0.25 so that an accent of 2.0 keeps the BD's state (the bank's
    # loudest ring, ~720 x the kick) under a quarter of the 28-bit rail
    w += env_writes(E_BDX, BD, 0.1e-3, 0.25)
    w += env_writes(E_BDCLICK, BD, 0.0, 0.06, hold=48)       # the 1 ms pulse leaking through, reference 2
    w += env_writes(E_SDX, SD, 0.1e-3, 0.25)
    w += env_writes(E_SDN, SD, SD_NOISE_TAU, SD_NOISE_PEAK)   # SNAPPY = this peak x M_SDN's amp, reference 3
    w += env_writes(E_LTX, LT, 0.1e-3, 0.25)
    w += env_writes(E_MTX, MT, 0.1e-3, 0.25)
    w += env_writes(E_HTX, HT, 0.1e-3, 0.25)
    w += env_writes(E_CH, CH, 20e-3, 1.0)                    # reference 11
    w += env_writes(E_OH, OH, 150e-3, 1.0, choke=CH)         # DECAY knob mid; CH chokes it, reference 11
    w += env_writes(E_CPBURST, CP, CP_BURST_TAU, 0.69, bursts=CP_BURSTS, period=CP_PERIOD,
                    final_tau=CP_FINAL_TAU)                  # L2: four strikes, the last at the fire level
    w += env_writes(E_CPTAIL, CP, CP_TAIL_TAU, CP_TAIL_PEAK_LEVEL)   # the tail, tau MEASURED (plan084)
    w += env_writes(E_CBA, CB, 5e-3, 0.5)                    # two-slope envelope, reference 9
    w += env_writes(E_CBB, CB, CB_TAU_B, 0.5)                # cowbell tail, reference 9 (DR 0010)
    w += env_writes(E_RSX, CL, 0.1e-3, PEAK_RSX)             # the RS/CL exciter pulse
    w += env_writes(E_RSG, CL, RS_GATE_TAU, PEAK_RSG)        # Q74's ~22 ms gate, reference 5
    w += env_writes(E_CYS, CY, CY_TAU_SHORT, PEAK_CYS)       # CY high band, short fixed
    w += env_writes(E_CYD, CY, CY_TAU_DECAY, PEAK_CYD)       # CY high band, the DECAY knob
    w += env_writes(E_CYL, CY, CY_TAU_LOW, PEAK_CYL)         # CY low band; the knob moves it too
    # paths
    paths = [
        path_word(SRC_PULSE, E_BDX, dest=M_BD),
        path_word(SRC_PULSE, E_BDCLICK, dest=DEST_MIX),
        path_word(SRC_PULSE, E_SDX, dest=M_SDLO),
        path_word(SRC_PULSE, E_SDX, dest=M_SDHI),              # both from the pulse (reference 3: cascade is subtle)
        path_word(SRC_NOISE, E_SDN, dest=M_SDN),
        path_word(SRC_PULSE, E_LTX, dest=M_LT),
        path_word(SRC_PULSE, E_MTX, dest=M_MT),
        path_word(SRC_PULSE, E_HTX, dest=M_HT),
        path_word(SRC_SQSUM, ENV_FULL, dest=M_HATBP),          # the six squares, always on, into the band-pass
        path_word(SRC_TAP + M_HATBP, E_CH, nl=NL_SWING, dest=M_CHHP),
        path_word(SRC_TAP + M_HATBP, E_OH, nl=NL_SWING, dest=M_OHHP),
        path_word(SRC_NOISE, ENV_FULL, dest=M_CPBP),           # noise, always on, into the clap band-pass
        path_word(SRC_TAP + M_CPBP, E_CPBURST, E_CPTAIL, nl=NL_TANH, dest=DEST_MIX),
        # the cowbell's two oscillators are gated SEPARATELY (reference 9: each has its own
        # transistor gate) and summed after: nl(a) + nl(b), never nl(a + b), which would make
        # the 260 Hz difference tone the machine has not got (15.5)
        path_word(SRC_SQ + SQPAIR[0], E_CBA, E_CBB, nl=NL_SWING, dest=M_CBBP),
        path_word(SRC_SQ + SQPAIR[1], E_CBA, E_CBB, nl=NL_SWING, dest=M_CBBP),
        # RS / CL. Both resonators are excited by the same pulse and both taps
        # are summed on the MIX bus through the swing VCA, which is reference
        # 5's Q62 -- "the distortion is the sound; do not skip it". The two
        # taps are distorted SEPARATELY where the circuit distorts their sum;
        # the cost of that is measured in test_808_acceptance.
        path_word(SRC_PULSE, E_RSX, att=RS_LO_X_ATT, dest=M_RS1),
        path_word(SRC_PULSE, E_RSX, dest=M_RS2),
        path_word(SRC_TAP + M_RS1, E_RSG, nl=NL_SWING, att=RS_ATT, dest=DEST_MIX),
        path_word(SRC_TAP + M_RS2, E_RSG, nl=NL_SWING, att=RS_ATT, dest=DEST_MIX),
        # CY. The six squares into the low band-pass (the high band is already
        # on M_HATBP for the hats); then the three swing VCAs of reference 10.
        # The two HIGH-band VCAs are one path: they share a destination, and
        # v = nl(s) * (ENV(a) + ENV(b)) is exactly the sum of the two paths
        # they would otherwise be.
        path_word(SRC_SQSUM, ENV_FULL, dest=M_CYBP),
        path_word(SRC_TAP + M_HATBP, E_CYS, nl=NL_SWING, att=CY_ATT, dest=M_CHHP),
        path_word(SRC_TAP + M_HATBP, E_CYD, nl=NL_SWING, att=CY_ATT, dest=M_CYHI),
        path_word(SRC_TAP + M_CYBP, E_CYL, nl=NL_SWING, att=CY_ATT, dest=DEST_MIX),
    ]
    assert len(paths) == N_PATH, (len(paths), N_PATH)
    for p, word in enumerate(paths):
        w.append((A_PATH + p, word))
    return w


def kit_808_candidate_559() -> list:
    """#559's DISABLED candidate kit: kit_808() with CANDIDATE_559's two hat
    filter Qs and nothing else. Not loaded by anything that ships; it exists so
    the candidate can be heard, measured and driven through the RTL benches
    (`rtl-sketch/verify_drums.py --kit candidate-559`) before anyone enables it."""
    return kit_808(tuning=CANDIDATE_559)


# ---- the kit an image that predates revision 14 plays --------------------------
# The published R1 Arty image (fpga/reports/arty/integrated-baseline-2025.1,
# built at fpga/release/release_manifest.IMAGE_SOURCE_COMMIT) is contract
# revision 11 RTL: it has no ENV_FRATE register and no final strike. Sending it
# revision 14's `kit_808()` would program four strikes at period 511 and an
# 80 ms tail on a clap that cannot play the fourth at the fire level -- a clap
# nobody verified. So the kit a host sends is a property of the IMAGE it drives,
# not of the tree it runs from.
#
# KIT808 as revision 11 stated it (revisions 12 and 13 moved no table): the
# hash in spec/reference/test_tables.py's REV11 pin, computed the same way
# (sha256 of the decimal words `addr << 32 | value`, comma-joined).
KIT808_REV11_SHA256 = "a43fe2a7d596a417ae3c9949fe43f94cc8e64482f7cac6ede5bc271009a5ff19"


class KitRefused(RuntimeError):
    """A frozen kit no longer reproduces the image it is frozen against."""


def _kit_sha256(kit: list) -> str:
    import hashlib
    return hashlib.sha256(",".join(str((int(a) << 32) | int(v)) for a, v in kit)
                          .encode()).hexdigest()


def kit_808_rev11() -> list:
    """The reference kit a revision-11 image plays: `kit_808_rev14()` with
    revision 14's three clap writes undone -- ENV_CTL[8] back to three strikes
    at period 480, no ENV_FRATE[8] write at all (the register does not exist
    there), and ENV_RATE[9] back to the 47 ms tail. Every other write is
    revision 14's, in its order. It starts from revision 14 rather than the live
    kit because revision 15 (#388) moved two rimshot writes the revision-11
    image also never had: undoing one revision's changes at a time is what keeps
    each `KitRefused` message pointing at the revision that actually moved.

    FROZEN BY HASH, CHECKED AT THE POINT OF USE: the result must hash to
    KIT808_REV11_SHA256, or this REFUSES (KitRefused). A later change to any
    other kit value moves `kit_808()` for the tree, but it did not move the
    published image; deriving this kit would then silently send the old image
    bytes it was never verified with. Refusing makes that a decision someone
    has to take (freeze the literal, or cut a new release), not a drift."""
    burst = A_ENV + E_CPBURST * ENV_STRIDE
    tail = A_ENV + E_CPTAIL * ENV_STRIDE
    undo = {burst: env_ctl(CP, 15, 0, 2, 480),          # three strikes, 10 ms apart
            tail + 2: rate_reg(47e-3)}                   # the R348 x C138 tail
    kit = [(a, undo.get(a, v)) for a, v in kit_808_rev14() if a != burst + 3]
    got = _kit_sha256(kit)
    if got != KIT808_REV11_SHA256:
        raise KitRefused(f"kit_808_rev11() hashes to {got[:12]}, not revision 11's "
                         f"KIT808 {KIT808_REV11_SHA256[:12]}: kit_808() changed a write "
                         "the revision-11 image was verified with; freeze the literal "
                         "image or cut a new release")
    return kit


# ...and as revision 14 stated it, the kit the PUBLISHED R1 player preview was
# built and measured with (fpga/release/r1-kit.json holds the same writes by
# value). Revision 15 is #388's two rimshot writes; see `kit_808_rev14`.
KIT808_REV14_SHA256 = "321a93546cfa5ffab03b3cf91557580ea7655ada933ce380c81cd07597a9b683"


def kit_808_rev14() -> list:
    """The reference kit a revision-14 image plays: `kit_808()` with revision
    15's TWO rimshot writes undone (#388) -- PATH[15] back to an UNATTENUATED
    pulse into the 455 Hz network, and ENV_PEAK[14] back to the gate peak that
    went with it. Revision 15 adds and removes no write, so the address list and
    the order are `kit_808()`'s exactly.

    The two go together and neither may be undone alone: RS_LO_X_ATT without
    PEAK_RSG's x2.2532 is a rimshot 7.05 dB below its share of Roland's chart,
    which is a kit no release ever had. Both register fields exist in revision
    14 and both new words would be ACCEPTED by that image -- which is exactly
    why they have to be undone here rather than left to work by accident: a host
    driving the published image gets the rimshot that image was measured with.

    FROZEN BY HASH, CHECKED AT THE POINT OF USE, on the same contract as
    `kit_808_rev11`: the result must hash to KIT808_REV14_SHA256 or this
    REFUSES (KitRefused)."""
    undo = {A_PATH + P_RS1X: path_word(SRC_PULSE, E_RSX, dest=M_RS1),
            A_ENV + E_RSG * ENV_STRIDE + 1: peak_reg(PEAK_RSG_REV14)}
    kit = [(a, undo.get(a, v)) for a, v in kit_808()]
    got = _kit_sha256(kit)
    if got != KIT808_REV14_SHA256:
        raise KitRefused(f"kit_808_rev14() hashes to {got[:12]}, not revision 14's "
                         f"KIT808 {KIT808_REV14_SHA256[:12]}: kit_808() changed a write "
                         "the revision-14 image was verified with; freeze the literal "
                         "image or cut a new release")
    return kit


# The kit each supported image revision plays. A host names the image it
# drives; it does not assume the tree's.
KITS_BY_REVISION = {11: kit_808_rev11, 14: kit_808_rev14, 15: kit_808}


def poles_from_regs(a1_reg: int, a2_reg: int, fs: int = SR) -> tuple[float, float]:
    """The inverse of `pole_regs`: (f0, Q) of the resonator a coefficient pair
    actually encodes. Float; host side only. Used to read a circuit's CURRENT
    tuning out of a register image rather than assuming the kit's."""
    a1, a2 = s26(a1_reg) / (1 << 24), s26(a2_reg) / (1 << 24)
    r = math.sqrt(max(-a2, 1e-12))
    w = math.acos(max(-1.0, min(1.0, a1 / (2.0 * r))))
    f0 = w * fs / (2.0 * math.pi)
    tau = -1.0 / (fs * math.log(min(r, 1.0 - 1e-15)))
    return f0, math.pi * f0 * tau


# ---- the second sound of each shared circuit (the panel switch) --------------
# Five circuits carry two sounds (reference 4 SW8, 5 SW11, 7/8 SW12). Switching
# one is a set of register writes, not hardware: `preset_writes(name)` returns
# the image that puts that sound's circuit into that sound's position. It is
# idempotent and complete for all sixteen -- calling it for the sound the kit
# already loads rewrites the same values -- so a caller never has to know which
# of a pair is the default.
def preset_writes(sound: str) -> list:
    """The (addr, value) writes that select `sound` on its circuit."""
    n = sound.upper()
    if n in TOM_PRESET:
        mode = {"LT": M_LT, "LC": M_LT, "MT": M_MT, "MC": M_MT, "HT": M_HT, "HC": M_HT}[n]
        f0, q, _ = TOM_PRESET[n]
        return mode_writes(mode, f0, q, AMP_TOM[n], RAW)
    if n == "RS":
        return (mode_writes(M_RS1, RS_LO_HZ, RS_LO_Q, 0.0, RAW)
                + mode_writes(M_RS2, RS_HI_HZ, RS_HI_Q, 0.0, RAW)
                + [(A_PATH + P_RS1X, path_word(SRC_PULSE, E_RSX, att=RS_LO_X_ATT,
                                               dest=M_RS1)),
                   (A_PATH + P_RS2X, path_word(SRC_PULSE, E_RSX, dest=M_RS2)),
                   (A_PATH + P_RS1OUT, path_word(SRC_TAP + M_RS1, E_RSG, nl=NL_SWING,
                                                 att=RS_ATT, dest=DEST_MIX)),
                   (A_PATH + P_RS2OUT, path_word(SRC_TAP + M_RS2, E_RSG, nl=NL_SWING,
                                                 att=RS_ATT, dest=DEST_MIX))]
                + env_writes(E_RSX, CL, 0.1e-3, PEAK_RSX)
                + env_writes(E_RSG, CL, RS_GATE_TAU, PEAK_RSG))
    if n == "CL":
        # The claves position disconnects the 455 Hz network ("routed via R320
        # can be ignored because of its minimized level") and wires IC20b's
        # feedback for high Q. It does NOT go through the swing VCA -- that is
        # the rimshot's Q62 -- so the output path is linear.
        return (mode_writes(M_RS1, RS_LO_HZ, RS_LO_Q, 0.0, RAW)
                + mode_writes(M_RS2, CL_HZ, CL_Q, 0.0, RAW)
                + [(A_PATH + P_RS1X, path_word(SRC_OFF, ENV_NONE, dest=DEST_MIX)),
                   (A_PATH + P_RS2X, path_word(SRC_PULSE, E_RSX, dest=M_RS2)),
                   (A_PATH + P_RS1OUT, path_word(SRC_OFF, ENV_NONE, dest=DEST_MIX)),
                   (A_PATH + P_RS2OUT, path_word(SRC_TAP + M_RS2, E_RSG, nl=NL_LIN,
                                                 att=CL_ATT, dest=DEST_MIX))]
                + env_writes(E_RSX, CL, 0.1e-3, PEAK_CLX)
                + env_writes(E_RSG, CL, RS_GATE_TAU, PEAK_CLG))
    if n == "CP":
        return (mode_writes(M_CPBP, 1071.0, 1.6, 0.0, BP)
                + [(A_PATH + P_CPN, path_word(SRC_NOISE, ENV_FULL, dest=M_CPBP)),
                   (A_PATH + P_CPOUT, path_word(SRC_TAP + M_CPBP, E_CPBURST, E_CPTAIL,
                                                nl=NL_TANH, dest=DEST_MIX))]
                + env_writes(E_CPBURST, CP, CP_BURST_TAU, 0.69, bursts=CP_BURSTS, period=CP_PERIOD,
                             final_tau=CP_FINAL_TAU)
                + env_writes(E_CPTAIL, CP, CP_TAIL_TAU, CP_TAIL_PEAK_LEVEL))
    if n == "MA":
        # Same noise source, same buffer (IC19), SW12 selects: the band-pass
        # becomes Q68's Sallen-Key HIGH-pass and the three-burst envelope
        # becomes one ~12 ms decay. The gate is a transistor, so the swing
        # nonlinearity stands in for reference 8's "a little asymmetric
        # clipping is authentic but not essential".
        return (mode_writes(M_CPBP, MA_HP_HZ, MA_HP_Q, 0.0, HP)
                + [(A_PATH + P_CPN, path_word(SRC_NOISE, ENV_FULL, dest=M_CPBP)),
                   (A_PATH + P_CPOUT, path_word(SRC_TAP + M_CPBP, E_CPBURST,
                                                nl=NL_SWING, att=MA_ATT, dest=DEST_MIX))]
                + env_writes(E_CPBURST, CP, MA_TAU, PEAK_MA, final_tau=0)   # FRATE OFF: no CP leak
                + env_writes(E_CPTAIL, CP, 1e-3, 0.0))
    if n in ("BD", "SD", "CB", "CY", "OH", "CH"):
        return []                       # not a shared circuit: the kit is the sound
    raise KeyError(f"{sound}: not one of the sixteen ({', '.join(SOUND_NAMES)})")


def kit_with_sounds(*sounds: str, kit: list = None) -> list:
    """kit_808() with each named sound selected on its circuit. Later writes to
    the same address win, so the result is a flat register image."""
    img = dict(kit if kit is not None else kit_808())
    for name in sounds:
        for a, v in preset_writes(name):
            img[a] = v
    return sorted(img.items())


# ---- the reference host (informative): hits and patterns to writes ------------------
def _kit_amp(kit: list, mode: int) -> float:
    """The amp register a kit image writes for one mode, back as a level."""
    a = A_MODE + mode * MODE_STRIDE + 2
    for addr, v in kit or ():
        if addr == a:
            return v / 65536.0
    return 0.0


def _kit_poles(kit: list, mode: int) -> list:
    """The (a1, a2) register pair a kit image writes for one mode, or None."""
    base = A_MODE + mode * MODE_STRIDE
    got = {}
    for addr, v in kit or ():
        if addr in (base, base + 1):
            got[addr] = v
    return [got[base], got[base + 1]] if len(got) == 2 else None


def hit_writes(hits, kit: list = None, start_frame: int = 0, coef_seq: bool = True) -> list:
    """hits: (frame, stop, accent 0..2.0). Each hit writes its stop's accent
    and raises the stop bit in that frame; the bit is dropped in the next
    frame so the next hit is an edge again. Two hits of one stop in
    consecutive frames cannot both fire (no 0->1 between them) -- a host
    leaves a frame between hits, as a slice-based host does by construction.

    `coef_seq` (the default) also emits the two coefficient sequences the
    reference describes and no register image can hold, because they are
    changes over time to ONE mode's coefficients rather than settings: the
    BD's 4 ms attack window (section 2) and the toms' diode pitch drop
    (section 4). They are the host's, not the block's -- the block already
    lets any mode be retuned on any frame (contract 15.6) -- which is why
    they live here and not in `kit_808()`, and why Appendix G is unchanged
    by them. `coef_seq=False` is the bare register image, for a host that
    sequences coefficients itself."""
    kit = kit or []
    w = [(start_frame, a, v) for a, v in kit]
    by_frame = {}
    for f, s, a in hits:
        by_frame.setdefault(int(f), []).append((int(s), float(a)))
    if coef_seq:
        tom_mode = {LT: M_LT, MT: M_MT, HT: M_HT}
        for f, s_, a_ in hits:
            if s_ == BD:
                w += bd_attack_writes(int(f), _kit_poles(kit, M_BD))
            elif s_ in tom_mode:
                # Read the tuning OUT of the image rather than assuming the
                # kit's: the congas are the same circuit at a different f0 and
                # Q (reference 4 SW8), and they share the diode mechanism, so
                # a sequence that hard-coded 90 Hz would sweep a conga from the
                # wrong place -- and would silently overwrite a host that had
                # retuned a tom, which is the fault `bd_attack_writes` already
                # had to be fixed for.
                m = tom_mode[s_]
                poles = _kit_poles(kit, m)
                if poles is None:
                    continue
                f0, q = poles_from_regs(poles[0], poles[1])
                w += tom_pitch_drop_writes(int(f), m, f0, q, _kit_amp(kit, m), a_)
    mask = 0
    events = {}
    for f in sorted(by_frame):
        bits = 0
        for s, a in by_frame[f]:
            w.append((f, A_ACCENT + s, accent_reg(a)))
            bits |= 1 << s
        events.setdefault(f, 0)
        events[f] |= bits
        events.setdefault(f + 1, 0)
    for f in sorted(events):
        new = events[f]
        if new != mask:
            w.append((f, A_STOPS, new))
            mask = new
    return sorted(w, key=lambda t: t[0])


PATTERN_808 = {"BD": "X...x...X..x..x.", "SD": "....X.......X...", "CH": "x.x.x.x.x.x.x.x.",
               "OH": "..x.......x.....", "CP": "............X...", "CB": "........x.......",
               "LT": "..............x.", "HT": "...........x...."}


def pattern_hits(pattern: dict, bpm: float = 118.0, bars: int = 2, swing: float = 0.0,
                 start_s: float = 0.0) -> list:
    """engines.render_groove's format: stop name -> 16-char step string, 'x' a
    hit at 1.0, 'X' accented at 1.4, 'o' soft at 0.6, '.' a rest. Returns hits."""
    step = 60.0 / bpm / 4.0
    hits = []
    for rep in range(bars):
        for name, row in pattern.items():
            s = SOUND_STOP[name] if name in SOUND_STOP else STOP_NAMES.index(name)
            for i, c in enumerate(row):
                if c == ".":
                    continue
                a = 1.4 if c == "X" else (0.6 if c == "o" else 1.0)
                t = start_s + (rep * 16 + i) * step + (swing * step if i % 2 else 0.0)
                hits.append((int(round(t * SR)), s, a))
    return hits
