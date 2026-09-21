"""Fully integer voice -- monophonic, or paraphonic when the host assigns held
keys to oscillators: oscillators with an on-chip glide, PolyBLEP, mixer,
envelopes and the cutoff path (g and resonance-compensation ROMs), around
`fixed.LadderFx`, with the VCA after the filter and a host volume. No float
anywhere in the per-sample signal path, so that RTL can be bit-exact against
it. One continuous voice: `VoiceFx.play` applies control writes at frame
boundaries to state that persists between notes (DR 0003).

This closes the gap `fixed_render.py` describes: the ladder was integer, but the
oscillators and envelopes were float, quantised to Q1.15 at the filter input.

Signal chain (the Minimoog's order, DR 0005; the audition's `engines.mono_note`
applied the amplitude envelope before the filter):

    3 x (glide -> phase accumulator -> waveform -> PolyBLEP -> 3-tap LPF) Q1.15
      -> mixer (Q0.15 weights, saturating)                     Q1.15
      -> ladder, g and kc from the cutoff ADSR via ROMs        Q4.15   (19-bit output word)
      -> x amplitude ADSR (the VCA)                            Q4.15
      -> x vol, saturated                                      Q1.15

Where float is still allowed -- and this is the only place: computing NOTE-ON
REGISTER VALUES and ROM CONTENTS from the patch's physical units. Hz -> phase
increment (`dsp.phase_inc`), seconds -> envelope rate, the tanh/sine/g tables.
In hardware those are the host's job or a ROM's; the same convention `fixed.py`
already uses for its coefficient. Everything evaluated per sample is integer.

Every one of those conversions clamps its result to the width of the register
it lands in (`REG_BITS`, NUMERIC-CONTRACT.md 5.1, via `fixed.usat`), so the
model can never hold a value a register-limited implementation cannot. The two
that can hit the clamp with musically plausible input are a_inc (attacks
shorter than two frames) and rate (releases shorter than ~7 us); the sweep in
test_voice_fx.py walks every conversion over its input domain and pins where
each clamp fires. And every LEGAL register value is handled per sample: inc = 0
stalls its oscillator with the PolyBLEP at zero rather than raising.

Formats:
    phase           24-bit accumulator (dsp.PHASE_BITS)
    waveform        Q1.15
    PolyBLEP        increment normalised at note-on to a 16-bit mantissa m and
                    exponent e (inc = m * 2^e); reciprocal r = floor(2^31 / m),
                    16 bits. Per sample the fraction ph/inc in Q0.16 is one
                    16x16 multiply: ((ph >> e) * r) >> 15. Polynomial in Q0.16,
                    correction in Q1.15. Both widths are parameters, so the
                    sizing can be measured (`voice_fx_sweep.py`).
    mixer           Q0.15 weights, floor-normalised so they sum to <= 1.0;
                    32-bit accumulator, >> 15, saturated to Q1.15
    envelope        24-bit unsigned level, Q0.24. Attack and decay are linear
                    ramps (an increment per frame), matching the float model
                    that was auditioned. Release is exponential: subtract a
                    fraction of the level each frame, L -= max(1, (L*rate)>>16)
                    with rate in Q0.16. The max(1, .) is load-bearing -- without
                    it the shifted product truncates to zero below
                    2^16/rate and the note never ends. Output Q0.15 = L >> 9.
    glide           Q0.24 register, the ratio per frame minus 1 (0 = off);
                    the increment slews in a Q24.8 accumulator by
                    max(1, (acc * glide) >> 24) per frame toward its target:
                    constant cents per frame, exact landing (DR 0004)
    cutoff          integer Hz, 15 bits, clamped to [30, 21600]
    g ROM           128 entries x Q0.16, edge-sampled every 256 Hz, linearly
                    interpolated on the low 8 bits of the cutoff (same read
                    convention as the tanh table)
    kc ROM          32 entries x Q1.15, every 1024 Hz, the same interpolation:
                    the loop gain at which the linearised ladder starts to
                    self-oscillate, over 4. k_eff = (k * kc) >> 15 per frame,
                    so res = 1 is the onset at every cutoff (DR 0006)
    ladder          fixed.LadderFx: 24-bit state, 20 fraction bits, 16-entry
                    interpolated tanh, 19-bit output (Q4.15) so that the
                    resonant peak has headroom and nothing clips before the
                    output stage (DR 0005)
    VCA, vol        (y * ae) >> 15, then (v * vol) >> 15 saturated to Q1.15:
                    the one output clamp, reached only if the host raises vol
                    past the reference 0.45

Note-on (DR 0003): GATE_ON and TRIG re-enter ATTACK from the current level;
no write resets a phase or the ladder. Key priority, single/multi trigger,
glide policy and paraphonic allocation are the host's: `KeyHost` is the
reference host, informative in the contract.
"""
from __future__ import annotations
import math
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "audition"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numpy as np
import dsp
from dsp import SR, PHASE_BITS, PHASE_MASK, note_hz, phase_inc
import fixed
from fixed import LadderFx, sat, shl, usat

CYCLE = 1 << PHASE_BITS
ENV_BITS = 24
RATE_Q = 16
MANT_BITS = 16
RECIP_BITS = 16
GROM_BITS = 7                    # 128-entry cutoff -> g ROM
KROM_BITS = 5                    # 32-entry cutoff -> resonance compensation ROM (DR 0006)
K_BITS = LadderFx.K_BITS         # the ladder's k port: Q3.14, 17 bits; k_eff saturates there
CUT_MIN, CUT_MAX = 30, 21600     # Hz; the float model clips to [30, 0.45*SR]
VOL_REF = 14746                  # reference host's output volume, 0.45 in Q0.15 (DR 0005)
LADDER_OUT_BITS = 19             # ladder output word Q4.15, +-8.0: headroom for the peak (DR 0005)
GLIDE_BITS = 24                  # glide register: ratio per frame - 1, Q0.24; 0 = off (DR 0004)
INC_FRAC = 8                     # the slewed increment carries 8 fraction bits, Q24.8
GLIDE_REF_S = 0.09               # reference host: 90 ms per octave (engines.mono_note's 90 ms glide)
LADDER_CFG = dict(state_bits=24, state_q=20, tanh_entries=16, interp=True, out_bits=LADDER_OUT_BITS)
INC_BITS = PHASE_BITS            # the increment register is as wide as the phase
INC_MAX = (1 << INC_BITS) - 1
WEIGHT_BITS = 16                 # Q0.15 mixer weight; 1.0 = 32768 needs the 16th bit
CUT_BITS = 16                    # cut_lo, cut_hi, track_hz: integer Hz (proposed width)
VOL_BITS = 16                    # vol: Q0.15 (DR 0005)

# ---- the noise source (docs/minimoog-reference.md N1-N8) --------------------
# The Model D's fourth mixer source. "The Minimoog contains a noise generator
# using a transistor generating white noise... amplified to produce white, pink
# or red noise... White or pink noise is used for audio and pink or red for
# modulation" [verified: SM 2.2.3, 2.5].
LFSR_BITS = 31                   # x^31 + x^15 + x^13 + x^11 + 1: the polynomial the
LFSR_TAPS = (30, 15, 17, 19)     #   drum section already carries (contract 15.4)
LFSR_MASK = (1 << LFSR_BITS) - 1
NOISE_BITS = 16                  # LFSR steps per frame = bits per white word
# The drum section seeds the same polynomial with 1. Seeding the VOICE with 1
# too would make the two noise sources the SAME signal sample for sample, and
# two correlated noises sum at +6 dB where two independent ones sum at +3. This
# seed is the drums' state advanced LFSR_LAG steps -- not a multiple of 16, so
# the 16-bit words are not merely a time shift of each other; two shifts of one
# m-sequence cross-correlate at -1/(2^31 - 1).
LFSR_LAG = 1060921
VOICE_LFSR_SEED = 0x7F215FF7
# Q1.15 headroom shift, applied to ALL THREE colours equally. Drawing 1431
# labels the white, pink and red outputs -4 dBm each, and 5.27 specifies both
# white and pink at "-5 +-3 dB", so the colours leave the noise board at the
# same level [verified]. The pink network's crest factor is 4.6, so equal-RMS
# colours need 13 dB of peak headroom and this chip's rail is hard. Shifting
# all three by 2 keeps them equal AND keeps pink's measured peak at 0.66 of
# full scale. It costs white 12 dB against an oscillator at the same mixer
# weight -- a deviation, recoverable with the noise weight, and the alternative
# was clipping 0.05 % of pink's samples.
NOISE_SHIFT = 2
# Drawing 1431's "-3 db/OCTAVE FILTER": a 10 k series resistor from the white
# emitter follower with two shunt R-C legs to ground (3.3 k + 0.12 uF, 240 R +
# 0.033 uF). Bilinear-transformed at SR, with the make-up gain that equalises
# its noise power with white's folded into the numerator.
PINK_B = (912164, -741208, -117831)      # Q21, gain-equalised
PINK_A = (-28689, 12348)                 # Q14
PINK_BQ, PINK_AQ, PINK_Q = 21, 14, 27    # b, a, and the wide state's fraction bits
PINK_SB = 32                             # pink state word: Q5.27, +-16 full scales
RED_G = 904                              # Q0.16: one pole at 106.1 Hz (R914 10k, C908 0.15 uF)
RED_GAIN = 29841                         # Q14: equalises red's noise power with white's

# ---- modulation (docs/minimoog-reference.md M1-M9) --------------------------
# "There are two modulation signals available in the Minimoog; the output of
# Oscillator 3 and noise... The Modulation Mix amplifier selects either or
# both, sums them and routes them to the Modulation Amount Control in the
# Left-hand controller" [verified: SM 2.4]; the MOD MIX pot PANS between the
# two ("the wiper of R23 is connected to ground... it pans between the two
# modulation signals").
EXP_BITS = 6                     # 2^f ROM: 64 entries + guard, over one octave
OCT_Q = 12                       # the octave word: Q3.12 signed
OCT_SAT = 1 << (OCT_Q + 2)       # ... saturated to +-4 octaves. Not a musical limit -- four
                                 # octaves is five times the Model D's deepest setting (M7/M8)
                                 # -- but a width one: it holds the exp2 shift to 12..18
                                 # places, so the RTL's barrel shifter is seven wide and not
                                 # sixteen.
MOD_BITS = 16                    # mmix, mwheel, mpd, mfd registers
MROUTE_BITS = 3
MR_OSC, MR_FILT, MR_OSC3 = 1, 2, 4   # oscillator mod / filter mod / OSC-3 CONTROL
NSEL_BITS = 1
MMIX_FULL = 1 << 15              # mmix = 32768 is noise only; 0 is oscillator 3 only
# The two depths the service manual pins, as Q3.12 octaves of PEAK deviation:
#   pitch   "The oscillator should change 13 to 23 semitones" at full wheel
#           [verified: SM 5.37]; 18 semitones of total swing is +-0.75 octave
#   filter  440 Hz "when pitch is low", "a minimum of 2.4 kHz" when high
#           [verified: SM 5.19]; at least 1.224 octaves of peak deviation.
#           1.30 leaves margin over that floor [inferred]
MPD_REF_OCT = 0.75
MFD_REF_OCT = 1.30

# ---- oscillator waveforms (docs/minimoog-reference.md W1-W7) ----------------
# Drawing 1448 "WAVEFORM SWITCHING MINI D": saw and triangle reach the waveform
# switch at the same +-1.75 V [verified: SM 2.3], and the shark-tooth position
# taps the junction of R030 (47 k, from the saw) and R031 (10 k, from the
# triangle) -- a divider between two stiff sources, so 10/57 saw, 47/57
# triangle [inferred from verified values].
SHARK_W_SAW = 5749               # Q0.15, 10/57
SHARK_W_TRI = 27019              # Q0.15, 47/57; the two sum to exactly 32768
# The three rectangular widths. SW6's second deck selects 0 V, -1.5 V or -2.5 V
# from a ground / 1.5 k / 1 k / 7.5 k / -10 V divider [verified: drawing 1448],
# and the service manual pins the ends of that control range: 0 V gives "a
# square wave output", -2.5 V "a 15 percent duty cycle" [verified: SM 2.3].
# Duty is linear in the threshold because the ramp is, so the middle tap is
# 50 - 1.5 * (50 - 15) / 2.5 = 29 % [inferred].
DUTY_SQUARE = 1 << (PHASE_BITS - 1)                     # 50 %
DUTY_WIDE = 4865393                                     # 29 % of 2^24
DUTY_NARROW = 2516582                                   # 15 % of 2^24
DUTY_P25 = 1 << (PHASE_BITS - 2)                        # 25 %: NOT a Model D width. Kept
                                                        #   because contract rev 4 shipped it
DUTY_479 = int(round(CYCLE * 0.479))                    # model-only M5A sweep challenger
DUTY = dict(square=DUTY_SQUARE, pulse25=DUTY_P25, pulse29=DUTY_WIDE,
            pulse15=DUTY_NARROW, pulse479=DUTY_479)
WAVE_CODE = dict(saw=0, square=1, pulse25=2, tri=3, sine=4,
                 shark=5, revsaw=6, pulse29=7, pulse15=8)
WAVE_BITS = 4
# pulse479 intentionally has no WAVE_CODE: it is a model-only experimental
# challenger and must not be mistaken for a waveform supported by RTL.
BLEP_SHAPES = ("saw", "square", "pulse25", "pulse29", "pulse15", "shark", "revsaw", "pulse479")
TWO_EDGE = ("square", "pulse25", "pulse29", "pulse15", "pulse479")

# Register widths of the control image, NUMERIC-CONTRACT.md 5.1. Each host
# conversion below clamps to the width named here; the sweep test walks them.
REG_BITS = dict(inc=INC_BITS, w=WEIGHT_BITS, wn=WEIGHT_BITS,
                a_inc=ENV_BITS, d_dec=ENV_BITS, sus=ENV_BITS, rate=RATE_Q,
                cut_lo=CUT_BITS, cut_hi=CUT_BITS, track_hz=CUT_BITS,
                k=LadderFx.K_BITS, gain=LadderFx.GAIN_BITS, ogain=LadderFx.GAIN_BITS,
                glide=GLIDE_BITS, vol=VOL_BITS, nsel=NSEL_BITS, mmix=MOD_BITS,
                mwheel=MOD_BITS, mpd=MOD_BITS, mfd=MOD_BITS, mroute=MROUTE_BITS)

_SINE = dsp._QUARTER.astype(np.int64)   # 256-entry quarter wave, midpoint-sampled


def sat16(v):
    return np.clip(v, -32768, 32767)


# ---- waveforms from a 24-bit phase, Q1.15 -----------------------------------
def sine_fx(ph: np.ndarray) -> np.ndarray:
    """Same table and symmetry as dsp.sine_from_phase, without the final /32768."""
    idx = (ph >> (PHASE_BITS - 10)) & 1023
    quad, i = idx >> 8, idx & 255
    q = np.where(quad & 1, _SINE[255 - i], _SINE[i])
    return np.where(quad & 2, -q, q)


def _saw_fx(ph):
    return (ph >> (PHASE_BITS - 16)) - 32768


def _tri_fx(ph):
    v = ph >> (PHASE_BITS - 17)                           # 0 .. 131071
    return np.where(v < 65536, v - 32768, 98303 - v)


def naive_fx(shape: str, ph: np.ndarray) -> np.ndarray:
    """The nine shapes of contract 6.4. Six of them are the Model D's waveform
    switch (triangle, shark-tooth, sawtooth, square, wide and narrow
    rectangular), with the reverse sawtooth that oscillator 3 has in place of
    the shark-tooth [verified: drawing 1448; SM 2.16, 2.18]; `sine` and
    `pulse25` are this chip's own and are not Model D shapes."""
    ph = np.asarray(ph, dtype=np.int64)
    if shape == "saw":
        return _saw_fx(ph)
    if shape == "revsaw":                                 # osc 3's Q20 inverter (SM 2.3)
        return sat16(-_saw_fx(ph))
    if shape in DUTY:
        return np.where(ph < DUTY[shape], 32767, -32768)
    if shape == "tri":
        return _tri_fx(ph)
    if shape == "shark":                                  # R030 / R031 on the waveform switch
        return sat16((SHARK_W_SAW * _saw_fx(ph) + SHARK_W_TRI * _tri_fx(ph)) >> 15)
    if shape == "sine":
        return sine_fx(ph)
    raise ValueError(shape)


def clamp16(v: int) -> int:
    """Scalar sat16. The per-frame modulation path of 6.9 is a Python loop, so
    it cannot use the numpy form without paying for an array per sample."""
    return -32768 if v < -32768 else (32767 if v > 32767 else v)


def naive_one(shape: str, ph: int) -> int:
    """`naive_fx` for ONE phase, in plain Python integers -- the modulation
    source tap of 6.9, which is evaluated inside a per-frame loop.
    `test_moog_acceptance` walks every shape over the whole 24-bit phase and
    requires this to equal `naive_fx` exactly, so the two cannot drift."""
    ph = int(ph) & PHASE_MASK
    if shape == "saw":
        return (ph >> (PHASE_BITS - 16)) - 32768
    if shape == "revsaw":
        return clamp16(-((ph >> (PHASE_BITS - 16)) - 32768))
    d = DUTY.get(shape)
    if d is not None:
        return 32767 if ph < d else -32768
    if shape == "tri":
        v = ph >> (PHASE_BITS - 17)
        return v - 32768 if v < 65536 else 98303 - v
    if shape == "shark":
        saw = (ph >> (PHASE_BITS - 16)) - 32768
        v = ph >> (PHASE_BITS - 17)
        tri = v - 32768 if v < 65536 else 98303 - v
        return clamp16((SHARK_W_SAW * saw + SHARK_W_TRI * tri) >> 15)
    if shape == "sine":
        return int(sine_fx(np.array([ph], dtype=np.int64))[0])
    raise ValueError(shape)


# ---- PolyBLEP ---------------------------------------------------------------
def recip_of(inc: int, mant_bits: int = MANT_BITS, recip_bits: int = RECIP_BITS):
    """Note-on: inc = m * 2^e with m in [2^(MB-1), 2^MB). Returns (e, r) with
    r = floor(2^(RB+MB-1) / m), clamped to RB bits (only m = 2^(MB-1) hits the
    clamp, a 1-LSB error). One integer division per note-on.

    inc = 0 is a legal register value (it is the reset value, and any 24-bit
    write is accepted) and has no mantissa to divide by. It returns (0, 0),
    the reset value of the (e, r) state. Neither is observable: with inc = 0
    the phase never enters either PolyBLEP window, so `blep_fx` is identically
    zero whatever (e, r) hold, and the oscillator outputs the naive waveform
    at its stalled phase (DC)."""
    if inc == 0:
        return 0, 0
    e = inc.bit_length() - mant_bits
    m = shl(inc, -e)
    r = min((1 << (recip_bits + mant_bits - 1)) // m, (1 << recip_bits) - 1)
    return e, r


def frac_q16(x: np.ndarray, e, r, mant_bits: int = MANT_BITS, recip_bits: int = RECIP_BITS):
    """x / inc in Q0.16, for 0 <= x <= inc. `e` and `r` may be per-sample
    arrays (glide). ((x >> e) * r) >> (RB-1) = x * 2^MB / inc."""
    e = np.asarray(e); r = np.asarray(r)
    p = np.where(e >= 0, x >> np.maximum(e, 0), x << np.maximum(-e, 0))
    u = (p * r) >> (recip_bits - 1)
    u = np.minimum(u, (1 << mant_bits) - 1)
    return u << (16 - mant_bits) if mant_bits <= 16 else u >> (mant_bits - 16)


def blep_fx(ph: np.ndarray, inc, e, r, mant_bits=MANT_BITS, recip_bits=RECIP_BITS):
    """Correction to SUBTRACT from a naive saw at its wrap, Q1.15. Same sign
    convention as dsp._blep: -(1-t/dt)^2 just after the wrap, +(1-(1-t)/dt)^2
    just before it. `inc`, `e`, `r` scalar or per-sample."""
    inc = np.asarray(inc); e = np.asarray(e); r = np.asarray(r)
    c = np.zeros_like(ph)
    a = ph < inc
    if a.any():
        ea = e if e.ndim == 0 else e[a]; ra = r if r.ndim == 0 else r[a]
        s = 65536 - frac_q16(ph[a], ea, ra, mant_bits, recip_bits)      # 1 .. 65536
        c[a] = -((s * s) >> 17)                                          # -32768 .. 0
    q = CYCLE - ph
    b = q < inc
    if b.any():
        eb = e if e.ndim == 0 else e[b]; rb = r if r.ndim == 0 else r[b]
        s = 65536 - frac_q16(q[b], eb, rb, mant_bits, recip_bits)       # 1 .. 65535
        c[b] = (s * s) >> 17                                             # 0 .. 32767
    return c


class OscFx:
    """One oscillator: 24-bit phase accumulator, waveform, PolyBLEP on the
    discontinuous shapes, and an optional causal 3-tap output filter. `inc` may
    be an int (held note) or an int array (glide); the reciprocal is then
    recomputed whenever inc changes -- an integer divide per changed sample,
    which in hardware is a sequential divider (24 clocks of the 256-clock
    frame) or a Newton step. The spec is the exact floor quotient either way."""

    def __init__(self, shape: str, blep: bool = True,
                 mant_bits: int = MANT_BITS, recip_bits: int = RECIP_BITS,
                 smooth: bool = False):
        self.set_shape(shape, blep)
        self.MB, self.RB = mant_bits, recip_bits
        self.smooth = smooth
        self._smooth_d1 = 0
        self._smooth_d2 = 0
        self.phase = 0
        self.inc_tgt = 0                 # SET_INC's value: where the glide is going
        self.inc_acc = 0                 # the increment now, Q24.8 (contract 6.7)
        self._cache = {}

    def set_shape(self, shape: str, blep: bool = True):
        self.shape, self.blep = shape, blep and shape in BLEP_SHAPES

    def set_inc(self, v: int, jump: bool = False, glide: int = 0):
        """SET_INC k, v [, jump]: the target increment. The current increment
        follows at once when `jump` is set or the glide register is 0;
        otherwise the slew of 6.7 walks it there frame by frame."""
        self.inc_tgt = int(v)
        if jump or glide == 0:
            self.inc_acc = int(v) << INC_FRAC

    def slew(self, n: int, glide) -> np.ndarray:
        """The increment for each of the next n frames (DR 0004). Frame j uses
        inc_acc >> 8 as it stands at the start of the frame; at the end of the
        frame the accumulator moves toward the target by a fixed RATIO of
        itself -- a constant number of cents per frame, so a two-octave glide
        takes twice as long as a one-octave one and lands exactly:

            d       = max(1, (inc_acc * glide) >> 24)
            inc_acc = min(tgt, inc_acc + d)   if tgt > inc_acc
                    = max(tgt, inc_acc - d)   if tgt < inc_acc

        `glide` is the Q0.24 register (an int) or a per-frame array; 0 snaps
        to the target. The max(1, .) is the envelope's lesson: without it a
        small increment times a small rate truncates to no motion at all."""
        out = np.empty(n, dtype=np.int64)
        gl = np.broadcast_to(np.asarray(glide, dtype=np.int64), (n,))
        acc, tgt = self.inc_acc, self.inc_tgt << INC_FRAC
        for j in range(n):
            out[j] = acc >> INC_FRAC
            if acc != tgt:
                g = int(gl[j])
                if g == 0:
                    acc = tgt
                else:
                    d = max(1, (acc * g) >> GLIDE_BITS)
                    acc = min(tgt, acc + d) if tgt > acc else max(tgt, acc - d)
        self.inc_acc = acc
        return out

    def _er(self, inc: int):
        er = self._cache.get(inc)
        if er is None:
            er = self._cache[inc] = recip_of(inc, self.MB, self.RB)
        return er

    def render(self, n: int, inc) -> np.ndarray:
        """n samples, Q1.15 as int64. Phase is output before it is incremented."""
        if np.ndim(inc) == 0:
            inc = int(inc)
            ph = (self.phase + inc * np.arange(n, dtype=np.int64)) & PHASE_MASK
            self.phase = (self.phase + inc * n) & PHASE_MASK
            e, r = self._er(inc)
            inc_a = inc
        else:
            inc = np.asarray(inc, dtype=np.int64)
            assert len(inc) == n
            ph = (self.phase + np.concatenate([[0], np.cumsum(inc[:-1])])) & PHASE_MASK
            self.phase = int((self.phase + inc.sum()) & PHASE_MASK)
            er = np.array([self._er(int(v)) for v in inc], dtype=np.int64)
            e, r = er[:, 0], er[:, 1]
            inc_a = inc
        if not self.blep:
            raw = naive_fx(self.shape, ph)
            return self._smooth(raw) if self.smooth else raw
        c = blep_fx(ph, inc_a, e, r, self.MB, self.RB)           # the correction at the wrap
        if self.shape == "saw":
            raw = sat16(_saw_fx(ph) - c)
            return self._smooth(raw) if self.smooth else raw
        if self.shape == "revsaw":
            # Q20 inverts the CORRECTED sawtooth (SM 2.3), so the band-limited
            # reverse saw is the band-limited saw negated, not a second BLEP.
            raw = sat16(-sat16(_saw_fx(ph) - c))
            return self._smooth(raw) if self.smooth else raw
        if self.shape == "shark":
            # The switch mixes the two BUFFERED waveform outputs through R030
            # and R031, so the correction the saw already carries is what the
            # junction sees. The step at the wrap is 10/57 of the saw's.
            raw = sat16((SHARK_W_SAW * sat16(_saw_fx(ph) - c)
                         + SHARK_W_TRI * _tri_fx(ph)) >> 15)
            return self._smooth(raw) if self.smooth else raw
        ph2 = (ph + (CYCLE - DUTY[self.shape])) & PHASE_MASK
        raw = sat16(naive_fx(self.shape, ph) + c
                    - blep_fx(ph2, inc_a, e, r, self.MB, self.RB))
        return self._smooth(raw) if self.smooth else raw

    def _smooth(self, raw: np.ndarray) -> np.ndarray:
        """Causal 3-tap binomial low-pass, matching the RTL oscillator tap.

        The coefficients are powers of two: (x + 2*x[-1] + x[-2]) / 4.
        This removes the top-of-band residual that aliases in the high-note
        saw while leaving the fundamental unchanged. The two history words
        are state, so note boundaries do not manufacture a new transient.
        """
        x = np.asarray(raw, dtype=np.int64)
        out = np.empty_like(x)
        d1, d2 = int(self._smooth_d1), int(self._smooth_d2)
        for i, v in enumerate(x):
            out[i] = (int(v) + 2 * d1 + d2) >> 2
            d2, d1 = d1, int(v)
        self._smooth_d1, self._smooth_d2 = d1, d2
        return out


# ---- mixer ------------------------------------------------------------------
def mix_weights(mix, q: int = 15) -> list:
    """Q0.q weights, floor-normalised: for non-negative mix levels they sum
    to at most 1.0, so a normalised mixer cannot clip. Unnormalised weights
    are allowed and saturate. A mix that sums to zero (every oscillator off)
    has nothing to normalise by and gives every weight 0. Each weight is
    clamped to its q+1-bit register (16 bits at q = 15)."""
    tot = float(sum(mix))
    if tot <= 0.0:
        return [0] * len(mix)
    return [usat(int(math.floor(m / tot * (1 << q))), q + 1) for m in mix]


def mix_fx(signals, weights, q: int = 15) -> np.ndarray:
    """Sum of Q1.15 signals x Q0.q weights, >> q, saturated to Q1.15."""
    acc = np.zeros_like(signals[0], dtype=np.int64)
    for s, w in zip(signals, weights):
        acc += s * int(w)
    return sat16(acc >> q)

# ---- the noise source (contract 6.10; docs/minimoog-reference.md N1-N8) -----
def lfsr_frame(state: int) -> tuple:
    """One frame of the LFSR: 16 steps of
        s <- (s << 1) | (s[30] ^ s[15] ^ s[17] ^ s[19])
    on a 31-bit state. Character for character the drum section's
    `drums_fx.lfsr_frame` (contract 15.4) -- the same primitive pentanomial
    x^31 + x^15 + x^13 + x^11 + 1, the same 16 steps, the same signed Q1.15
    word. It is DUPLICATED rather than imported so the voice model does not
    depend on the drum model; `test_moog_acceptance` asserts the two agree bit
    for bit from a common seed, which is what makes the duplication safe.

    The two are given DIFFERENT seeds on purpose (VOICE_LFSR_SEED): sharing
    one generator would make the voice's noise and the drums' the same signal,
    and two identical noises sum at +6 dB where two independent ones sum at
    +3."""
    w = 0
    for t in LFSR_TAPS:                      # every tap is at bit 15 or above, so all
        w ^= (state >> (t - 15)) & 0xFFFF    #   16 new bits are a function of the OLD state
    s = ((state << NOISE_BITS) | w) & LFSR_MASK
    return s, (w - 0x10000 if w & 0x8000 else w)


class NoiseFx:
    """The Model D's noise board (drawing 1431) as three integer signals.

      white   the LFSR word, >> NOISE_SHIFT
      pink    white through a biquad: the bilinear transform of the drawing's
              "-3 db/OCTAVE FILTER" (10 k series, two shunt R-C legs), with the
              make-up gain that equalises its noise power with white's
      red     pink through one pole at 106 Hz (R914 10 k, C908 0.15 uF; the
              drawing labels the section "100 Hz Lowpass Filter"), again
              power-equalised

    All three leave at the same RMS because the instrument's do: the drawing
    labels white, pink and red -4 dBm each [verified]. The pink state is a
    32-bit Q5.27 word, not Q1.15, because the biquad's pole at 0.9889 would
    otherwise amplify its own truncation noise by 39 dB.
    """

    def __init__(self, seed: int = VOICE_LFSR_SEED):
        self.seed = int(seed)
        self.reset()

    def reset(self):
        """RESET: contract 14. The LFSR returns to its seed, not to zero -- an
        all-zero LFSR is a fixed point and would be silent forever."""
        self.lfsr = self.seed
        self.x1 = self.x2 = 0
        self.y1 = self.y2 = 0
        self.rl = 0

    # b*x is Q(PINK_BQ+15); a*Y is Q(PINK_AQ+PINK_Q). One shift lines them up
    # so the whole biquad is ONE floor, not two.
    BX_SHIFT = PINK_AQ + PINK_Q - (PINK_BQ + 15)

    def step(self) -> tuple:
        """One frame: (white, pink, red), each Q1.15."""
        self.lfsr, x0 = lfsr_frame(self.lfsr)
        t = ((PINK_B[0] * x0 + PINK_B[1] * self.x1 + PINK_B[2] * self.x2) << self.BX_SHIFT) \
            - (PINK_A[0] * self.y1 + PINK_A[1] * self.y2)
        y0 = sat(t >> PINK_AQ, PINK_SB)
        self.x2, self.x1 = self.x1, x0
        self.y2, self.y1 = self.y1, y0
        self.rl = sat(self.rl + (((y0 - self.rl) * RED_G) >> 16), PINK_SB)
        return (x0 >> NOISE_SHIFT,
                clamp16(y0 >> (PINK_Q - 15 + NOISE_SHIFT)),
                clamp16((self.rl * RED_GAIN) >> (PINK_AQ + PINK_Q - 15 + NOISE_SHIFT)))

    def render(self, n: int):
        """n frames as three int64 arrays, for measurement. The voice itself
        calls `step` inside its own per-frame loop."""
        w = np.empty(n, dtype=np.int64); p = np.empty(n, dtype=np.int64); r = np.empty(n, dtype=np.int64)
        for i in range(n):
            w[i], p[i], r[i] = self.step()
        return w, p, r


# ---- 2^x for the modulation path (contract 6.9) -----------------------------
def make_exp_rom(bits: int = EXP_BITS) -> np.ndarray:
    """2^bits + 1 entries of (2^(i/2^bits) - 1) * 32768, EDGE-sampled over one
    octave, with the guard entry. Stored biased so the top entry (2.0) still
    fits in 16 bits; `exp2_q` adds the 32768 back."""
    return np.array([int(round((2.0 ** (i / (1 << bits))) * 32768)) - 32768
                     for i in range((1 << bits) + 1)], dtype=np.int64)


EXP_ROM = make_exp_rom()
_EXP_FB = OCT_Q - EXP_BITS               # fraction bits below the ROM index


def clamp_oct(v: int) -> int:
    """Saturate an octave word to Q3.12's +-4 octaves (OCT_SAT)."""
    return -OCT_SAT if v < -OCT_SAT else (OCT_SAT - 1 if v > OCT_SAT - 1 else v)


def exp2_q(oct_q12: int) -> tuple:
    """2^(o / 2^OCT_Q) as (mantissa, shift) with the value = (v * mantissa) >> shift.

    `o` is a SIGNED Q3.12 octave word, saturated to +-4 octaves by `clamp_oct`.
    Its integer part becomes the shift and its fraction the ROM read, so there
    is no barrel shifter on the mantissa and no exponent register: one
    interpolated ROM read and one variable right shift, of 12 .. 19 places.
    o = 0 gives (32768, 15),
    i.e. exactly v -- so an unmodulated voice is bit-identical to one with no
    modulation path at all."""
    i = oct_q12 >> OCT_Q                                     # arithmetic: floor
    fr = oct_q12 & ((1 << OCT_Q) - 1)
    idx = fr >> _EXP_FB
    fq = fr & ((1 << _EXP_FB) - 1)
    mant = 32768 + int(EXP_ROM[idx]) + ((int(EXP_ROM[idx + 1] - EXP_ROM[idx]) * fq) >> _EXP_FB)
    return mant, 15 - i


def mod_pan(osc3: int, noise: int, mmix: int) -> int:
    """The MOD MIX pot: a PAN, not two levels. "The wiper of R23 is connected
    to ground and, therefore, when the MODULATION MIX potentiometer is
    rotated, it pans between the two modulation signals" [verified: SM 2.4].
    mmix = 0 is oscillator 3 alone, MMIX_FULL is noise alone; the two weights
    sum to exactly MMIX_FULL, so the result is a convex combination and can
    only reach the rail when a source already is there."""
    m = mmix if mmix < MMIX_FULL else MMIX_FULL
    return clamp16((osc3 * (MMIX_FULL - m) + noise * m) >> 15)


# ---- envelope ---------------------------------------------------------------
class AdsrFx:
    """Integer ADSR, mirroring dsp.adsr's segment shapes.

    Level L is ENV_BITS unsigned. Per frame the level is OUTPUT, then updated:
      attack   L += a_inc (ceil(2^EB / attack_frames)); clamps at full
      decay    L -= d_dec (ceil((full - sus) / decay_frames)); clamps at sus
      sustain  L = sus
      release  L -= max(1, (L * rate) >> RATE_Q); clamps at 0
    rate = round((1 - exp(-4 / (release_s * SR))) * 2^RATE_Q), min 1, so the
    exponential matches the float's exp(-4 t / release). The `env_bits`
    parameter exists to measure the dead zone, exactly as fixed.LadderFx's
    `state_q` does.

    Every register is clamped to its width (a_inc, d_dec, sus to env_bits;
    rate to rate_q bits). Two conversions reach the clamp: an attack shorter
    than two frames (< 41.67 us) gives a_inc = 2^24, clamped to 2^24 - 1,
    which still completes the attack in one update from any level; a release
    of 7.07 us or less (release_s <= 4 / (17 ln 2 * SR)), including 0, gives
    rate = 2^16 = 1.0, clamped to 65535, which releases full scale to zero in
    three updates instead of one. release_s <= 0 means instant, as the float
    model's max(1e-9, .) does; the model no longer divides by it."""
    ATTACK, DECAY, SUSTAIN = 0, 1, 2

    def __init__(self, a_s, d_s, sus, r_s, env_bits: int = ENV_BITS, rate_q: int = RATE_Q):
        self.EB, self.RQ = env_bits, rate_q
        self.full = (1 << env_bits) - 1
        self.level, self.seg = 0, self.ATTACK
        self.set(a_s, d_s, sus, r_s)

    @staticmethod
    def regs_from(a_s, d_s, sus, r_s, env_bits: int = ENV_BITS, rate_q: int = RATE_Q) -> tuple:
        """The host's conversion of seconds and a level to the four registers
        (contract 5.5): (a_inc, d_dec, sus, rate), each clamped to its width
        (see the class docstring for where the clamps fire). Float; host side."""
        full = (1 << env_bits) - 1
        a, d = max(1, int(a_s * SR)), max(1, int(d_s * SR))
        a_inc = usat(-(-(1 << env_bits) // a), env_bits)
        sus_r = usat(int(round(sus * full)), env_bits)
        d_dec = usat(-(-(full - sus_r) // d), env_bits)
        decay = math.exp(-4.0 / (r_s * SR)) if r_s > 0.0 else 0.0
        rate = max(1, usat(int(round((1.0 - decay) * (1 << rate_q))), rate_q))
        return a_inc, d_dec, sus_r, rate

    def set(self, a_s, d_s, sus, r_s):
        self.set_regs(*self.regs_from(a_s, d_s, sus, r_s, self.EB, self.RQ))

    def set_regs(self, a_inc: int, d_dec: int, sus: int, rate: int):
        """SET_ENV: the four registers. Level and segment are state, untouched."""
        self.a_inc, self.d_dec, self.sus, self.rate = int(a_inc), int(d_dec), int(sus), int(rate)

    def render(self, n: int, gate, trig=None, q: int = 15) -> np.ndarray:
        """n frames. `gate` is an int (on for the first `gate` frames, the
        single-note form) or a per-frame 0/1 array; `trig` a per-frame 0/1
        array of frames at whose START the segment is set to ATTACK with the
        level unchanged (GATE_ON and TRIG, DR 0003). Q0.q out (int64)."""
        out = np.empty(n, dtype=np.int64)
        if np.ndim(gate) == 0:
            gate_a = (np.arange(n) < int(gate)).astype(np.int64)
        else:
            gate_a = np.asarray(gate, dtype=np.int64)
        trig_a = None if trig is None else np.asarray(trig, dtype=np.int64)
        L, seg = self.level, self.seg
        full, sus, a_inc, d_dec, rate, RQ = (self.full, self.sus, self.a_inc,
                                             self.d_dec, self.rate, self.RQ)
        sh = self.EB - q
        for i in range(n):
            if trig_a is not None and trig_a[i]:
                seg = self.ATTACK
            out[i] = L >> sh
            if gate_a[i]:
                if seg == self.ATTACK:
                    L += a_inc
                    if L >= full:
                        L, seg = full, self.DECAY
                elif seg == self.DECAY:
                    L -= d_dec
                    if L <= sus:
                        L, seg = sus, self.SUSTAIN
                else:
                    L = sus
            else:
                dec = (L * rate) >> RQ
                L -= dec if dec else 1
                if L < 0:
                    L = 0
        self.level, self.seg = L, seg
        return out

    @property
    def floor_level(self) -> int:
        """Below this level the exponential step truncates to zero and the
        release continues at 1 LSB per frame (linear). 2^RQ / rate."""
        return (1 << self.RQ) // self.rate + 1


# ---- cutoff -> coefficient ROM ---------------------------------------------
# Huovilainen's tuning polynomial (DAFx-04; DR 0011). The one-pole cascade's
# corner is not at the frequency the naive g = 1 - exp(-2*pi*f/fs) puts it:
# four poles and the half-sample feedback delay pull the loop's resonant
# frequency away from the commanded cutoff, by more at the top of the range
# than the bottom. Huovilainen publishes the correction as a cubic in the
# cutoff normalised to the BASE rate, and Surge XT's `LP Vintage Ladder`
# Type 2 applies it (sst-filters `VintageLadders.h`, namespace Huov, constants
# m18730 / m04955 / mneg06490 / m09988; original implementation Victor
# Lazzarini for Csound 5).
#
# WE APPLY IT TOO, AND HAVE SINCE DR 0011. `fcr()` below is the polynomial and
# `model/fixed.py`'s `cut_to_coeff` is `c * CUT_TRIM * fcr(c)`, so the shipped
# coefficient ROM carries it. The sentence that used to stand here -- "we did
# not" -- described the state BEFORE DR 0011 and was read in the present tense
# by a later reader, who concluded from it that a measured corner discrepancy
# was explained by our not applying the polynomial. It is not: we do.
#
# What "we did not" referred to: before DR 0011 the self-oscillation frequency
# drifted 9.84 percentage points over 100 Hz .. 10 kHz against Surge Type 2's
# 0.62. That is the defect the polynomial fixed, not an open one.
#
# NOTE ON THE CONSTANT. Both `docs/discrimination.md` section 8.3 and
# `model/reference_rigs.py` print the quadratic term as 0.4995. The source they
# cite spells it `m04955` -- 0.4955 -- and that is what is used here. The two
# differ by 0.03 percentage points on the measurement, so the number was never
# going to be caught by a measurement; it is caught by reading the source.
FCR_C3, FCR_C2, FCR_C1, FCR_C0 = 1.8730, 0.4955, -0.6490, 0.9988
# One constant scale on top, chosen at res = 1.05 on the free-ring probe
# (DR 0011). It removes the frequency-INDEPENDENT part of the residual, which
# the polynomial does not touch; the residual's size depends on resonance, so
# the operating point is part of the decision.
CUT_TRIM = 1.030


def fcr(cut_hz):
    """Huovilainen's tuning polynomial at a commanded cutoff in Hz. `fc` is
    normalised to the BASE rate, not the oversampled one -- which is how both
    Surge and Csound's original evaluate it."""
    fc = np.asarray(cut_hz, dtype=np.float64) / SR
    return ((FCR_C3 * fc + FCR_C2) * fc + FCR_C1) * fc + FCR_C0


def make_g_rom(bits: int = GROM_BITS, oversample: int = 2, tune: bool = True) -> np.ndarray:
    """2^bits + 1 entries of g = 1 - exp(-2*pi*f'/fs) in Q0.16, EDGE-sampled at
    a COMMANDED cutoff f = i * (32768 >> bits) Hz, where f' = f * CUT_TRIM *
    fcr(f) is the tuned frequency the one-pole cascade actually has to run at
    to resonate at f (DR 0011). fs is the ladder's oversampled rate. The +1 is
    the interpolation guard entry.

    `tune=False` is the untuned ROM of contract revisions 1-6 -- the negative
    control for DR 0011, and nothing else."""
    fs = SR * oversample
    step = (1 << 15) >> bits
    f = np.arange((1 << bits) + 1) * step
    ft = f * CUT_TRIM * fcr(f) if tune else f.astype(np.float64)
    return np.clip(np.round((1.0 - np.exp(-2.0 * math.pi * ft / fs)) * 65536), 0, 65535).astype(np.int64)


def g_from_cut(cut_hz: np.ndarray, rom: np.ndarray, bits: int = GROM_BITS) -> np.ndarray:
    """Q0.16 coefficient from integer Hz, linear interpolation between entries."""
    cut = np.asarray(cut_hz, dtype=np.int64)
    fb = 15 - bits
    i = cut >> fb
    frac = cut & ((1 << fb) - 1)
    return rom[i] + (((rom[i + 1] - rom[i]) * frac) >> fb)


# ---- cutoff -> resonance compensation ROM (DR 0006) -------------------------
def tanh_bin0_slope(ladder_cfg: dict = None) -> float:
    """Slope of the ladder's interpolated tanh table in its first bin, in
    state units: the small-signal gain every stage sees. TANH16[1] / 2^13 for
    the 16-entry table over [0, 4): 8025 / 8192 = 0.9796."""
    cfg = dict(LADDER_CFG if ladder_cfg is None else ladder_cfg)
    tbl = LadderFx(**cfg).tbl
    n = len(tbl)
    return tbl[1] / (32768.0 * fixed.TANH_DOMAIN / n)


def k_onset(cut_hz: int, g_rom: np.ndarray = None, bits: int = GROM_BITS,
            oversample: int = 2, slope: float = None):
    """Small-signal onset of self-oscillation at one integer cutoff, from the
    linearised loop the fixed-point ladder is inside its tanh table's first
    bin: four one-poles H1 = G / (1 - (1-G) z^-1) with G = g(cut)/2^16 * s0
    (g from the g ROM as the hardware reads it, s0 the bin-0 tanh slope), and
    the half-sample feedback delay Hfb = (z^-1 + z^-2)/2, at the oversampled
    rate. The loop gain is k * H1^4 * Hfb; oscillation starts where its phase
    is -180 degrees and its magnitude 1. Returns (k, f_osc_hz): the k at
    which the loop gain is exactly 1 there, and the frequency it oscillates
    at. Float, for ROM building and measurement only.

    phase(H1) = -atan2((1-G) sin w, 1 - (1-G) cos w), phase(Hfb) = -1.5 w:
    the total is continuous and decreasing from 0 at w = 0 to below -pi at
    w = pi, so one bisection finds the crossing."""
    rom = make_g_rom(bits, oversample) if g_rom is None else g_rom
    s0 = tanh_bin0_slope() if slope is None else slope
    g = int(g_from_cut(np.array([cut_hz]), rom, bits)[0])
    G = g / 65536.0 * s0
    a = 1.0 - G
    fs = SR * oversample

    def phase(w):
        return -4.0 * math.atan2(a * math.sin(w), 1.0 - a * math.cos(w)) - 1.5 * w
    lo, hi = 0.0, math.pi
    for _ in range(80):
        mid = 0.5 * (lo + hi)
        if phase(mid) > -math.pi:
            lo = mid
        else:
            hi = mid
    w = 0.5 * (lo + hi)
    h1 = G / abs(1.0 - a * complex(math.cos(w), -math.sin(w)))
    mag = h1 ** 4 * abs(math.cos(0.5 * w))
    return 1.0 / mag, w * fs / (2.0 * math.pi)


def make_k_rom(bits: int = KROM_BITS, g_bits: int = GROM_BITS, oversample: int = 2) -> np.ndarray:
    """2^bits + 1 entries of k_onset(cut) / 4 in unsigned Q1.15, EDGE-sampled
    at cut = i * (32768 >> bits) Hz, with the guard entry. Read by
    kc_from_cut with the same interpolation as the g ROM. 1.0 (32768) means
    'k = 4 * res', the uncompensated filter; the table is 1.001 at 30 Hz and
    peaks at 1.216 near 11 kHz. Each entry is evaluated at its cutoff clamped
    to [CUT_MIN, CUT_MAX]: entry 0 at 30 Hz because g(0) = 0 has no resonance
    at all, and the entries above 21600 Hz (never read) at the clamp."""
    rom = make_g_rom(g_bits, oversample)
    step = (1 << 15) >> bits
    out = []
    for i in range((1 << bits) + 1):
        cut = min(max(CUT_MIN, i * step), CUT_MAX)
        k, _ = k_onset(cut, rom, g_bits, oversample)
        out.append(int(round(k / 4.0 * 32768)))
    return np.array(out, dtype=np.int64)


def kc_from_cut(cut_hz: np.ndarray, rom: np.ndarray, bits: int = KROM_BITS) -> np.ndarray:
    """Q1.15 resonance compensation from integer Hz, linear interpolation
    between entries: the g ROM's read, with fewer index bits."""
    return g_from_cut(cut_hz, rom, bits)


def k_effective(k_q14, kc_q15) -> np.ndarray:
    """The k the ladder runs on this frame: (k * kc) >> 15, saturated to the
    ladder's 17-bit port (8.0 in Q3.14). k is the host's 4*res in Q3.14; kc
    is the ROM's Q1.15 factor for this frame's cutoff."""
    k_q14 = np.asarray(k_q14, dtype=np.int64); kc_q15 = np.asarray(kc_q15, dtype=np.int64)
    return np.minimum((k_q14 * kc_q15) >> 15, (1 << K_BITS) - 1)


# ---- the voice --------------------------------------------------------------
_DECIM2_TAPS = np.array((39,54,-44,-138,34,323,72,-609,-397,957,1133,
                         -1296,-2819,1544,10175,14712,10175,1544,-2819,
                         -1296,1133,957,-397,-609,72,323,34,-138,-44,54,39), dtype=np.int64)
_OS2_SUBSTEP_GAIN_Q15 = 27853  # 0.85 headroom keeps the Q1.15 FIR output below its rail.

def _render_2x(o: OscFx, n: int, inc, history: np.ndarray, phase2: int) -> tuple[np.ndarray, np.ndarray, int]:
    """Render a saw oscillator at 2x, then apply the reference decimator."""
    inc_a = np.broadcast_to(np.asarray(inc, dtype=np.int64), (n,))
    phase0 = int(o.phase)
    base_inc = np.repeat(inc_a // 2, 2)
    smooth = o.smooth
    o.smooth = False
    o.phase = int(phase2)
    if o.blep:
        # The RTL reuses the base-rate reciprocal, with the exponent reduced
        # for the half-rate step. Recomputing recip_of(inc//2) differs by one
        # for odd 16-bit increments, which produced rare 1-LSB block errors.
        phase = (int(phase2) + np.concatenate(([0], np.cumsum(base_inc[:-1])))) & PHASE_MASK
        er = np.array([o._er(int(v)) for v in inc_a], dtype=np.int64)
        exp2 = np.repeat(er[:, 0] - 1, 2)
        recip2 = np.repeat(er[:, 1], 2)
        correction = blep_fx(phase, base_inc, exp2, recip2, o.MB, o.RB)
        hi = sat16(_saw_fx(phase) - correction)
        o.phase = int((int(phase2) + int(base_inc.sum())) & PHASE_MASK)
    else:
        hi = o.render(2 * n, base_inc)
    o.smooth = smooth
    next_phase2 = int(o.phase)
    o.phase = int((phase0 + int(inc_a.sum())) & PHASE_MASK)
    # The decimator's ringing can exceed full scale around a PolyBLEP edge.
    # Preserve headroom before the RTL's saturating Q1.15 output stage.
    hi = (np.asarray(hi, dtype=np.int64) * _OS2_SUBSTEP_GAIN_Q15) >> 15
    joined = np.concatenate((history, hi))
    y = np.convolve(joined, _DECIM2_TAPS, mode="full")
    filtered = y[len(history):len(history) + 2*n]
    next_history = joined[-len(history):].copy()
    return sat16(filtered[1::2] >> 15).astype(np.int64), next_history, next_phase2

class VoiceFx:
    """One voice: three oscillators, two envelopes, one ladder, and the state
    they keep between notes. `play` is the per-frame contract; `note` renders
    one note from reset (contract 16's reference sequences); `KeyHost` below
    is the reference host that turns key events into control writes."""

    def __init__(self, blep: bool = True, mant_bits: int = MANT_BITS,
                 recip_bits: int = RECIP_BITS, env_bits: int = ENV_BITS,
                 grom_bits: int = GROM_BITS, krom_bits: int = KROM_BITS,
                 ladder_cfg: dict = None, g_exact: bool = False, k_comp: bool = True,
                 oversample_2x: bool = False, rate_converted_ladder: bool = False,
                 preserve_filter_headroom: bool = False,
                 causal_filter: bool = False,
                 pulse479_filter_candidate: bool = False):
        """`g_exact=True` bypasses the ROM and lets LadderFx compute g from Hz in
        float. NOT integer -- exists only to measure what the ROM costs.
        `k_comp=False` runs the ladder on the host's k with no compensation,
        rev 1's behaviour -- exists only to measure what DR 0006 changes."""
        self.blep, self.MB, self.RB = blep, mant_bits, recip_bits
        self.EB, self.GB, self.KB = env_bits, grom_bits, krom_bits
        self.ladder_cfg = dict(LADDER_CFG if ladder_cfg is None else ladder_cfg)
        self.g_exact, self.k_comp = g_exact, k_comp
        self.oversample_2x = oversample_2x
        self.rate_converted_ladder = bool(rate_converted_ladder)
        self.preserve_filter_headroom = bool(preserve_filter_headroom)
        self.causal_filter = bool(causal_filter)
        self.pulse479_filter_candidate = bool(pulse479_filter_candidate)
        if self.rate_converted_ladder and self.g_exact:
            raise ValueError("rate-converted ladder requires the rate-matched integer g ROM")
        if self.rate_converted_ladder and self.ladder_cfg.get("oversample", 2) not in (2, 4):
            raise ValueError("rate-converted ladder supports only 2x or 4x")
        self.g_rom = make_g_rom(grom_bits, self.ladder_cfg.get("oversample", 2))
        self.k_rom = make_k_rom(krom_bits, grom_bits, self.ladder_cfg.get("oversample", 2))
        self.trace = {}
        self._os2_history = [np.zeros(30, dtype=np.int64) for _ in range(3)]
        self._os2_phase = [0, 0, 0]
        self.reset()

    def reset(self):
        """RESET: every state register of contract 14 to zero."""
        self._os2_history = [np.zeros(30, dtype=np.int64) for _ in range(3)]
        self._os2_phase = [0, 0, 0]
        # Preserve the established single-rate waveform unless the measured
        # oversampled saw path is selected. The old three-tap smoother was an
        # experiment that attenuated upper harmonics and must not be implicit.
        self.oscs = [OscFx("saw", self.blep, self.MB, self.RB, smooth=False) for _ in range(3)]
        self.amp_env = AdsrFx(0.005, 0.25, 0.75, 0.12, env_bits=self.EB)
        self.filt_env = AdsrFx(0.004, 0.30, 0.25, 0.10, env_bits=self.EB)
        if self.rate_converted_ladder:
            from filter_rate_chain import RateConvertedLadder
            self.ladder = RateConvertedLadder(self.ladder_cfg.get("oversample", 2),
                                              self.ladder_cfg,
                                              preserve_headroom=self.preserve_filter_headroom,
                                              causal=self.causal_filter)
        else:
            self.ladder = LadderFx(**self.ladder_cfg)
        self.noise = NoiseFx()
        self.track_hz, self.gate, self.glide = 0, 0, 0
        self.mod_sig = 0                             # the registered modulation value (6.9)
        self.weights = [0, 0, 0, 0]
        self.nsel = self.mmix = self.mwheel = self.mpd = self.mfd = self.mroute = 0
        for o in self.oscs:
            o.phase = o.inc_tgt = o.inc_acc = 0

    # ---- host-side conversions (contract 5.5): float in, registers out ----
    @staticmethod
    def patch_regs(*, waves=("saw", "saw", "square"), detune=(0.0, 0.07, -12.0),
                   mix=(1.0, 0.8, 0.5), noise=0.0, nsel=0, cutoff=(400, 4000), q=0.62, drive=1.6,
                   amp=(0.005, 0.25, 0.75, 0.12), fenv=(0.004, 0.30, 0.25, 0.10),
                   track=0.35, vol=None, glide_s=GLIDE_REF_S,
                   mod_mix=0.0, mod_wheel=0.0, mod_pitch=MPD_REF_OCT, mod_filter=MFD_REF_OCT,
                   osc_mod=False, filt_mod=False, osc3_ctl=True, **_ignored) -> dict:
        """The patch's physical units as the control image, less the per-note
        registers (inc, track_hz, gate). Same names and defaults as
        engines.mono_note. `vol` in 0..1 (reference 0.45); `glide_s` is the
        time per octave at the constant-rate glide of DR 0004."""
        waves = tuple(waves) + ("saw",) * (3 - len(waves))     # a patch with fewer than
        detune = tuple(detune) + (0.0,) * (3 - len(detune))      # three oscillators leaves
        mix = tuple(mix) + (0.0,) * (3 - len(mix))               # the rest silent: w = 0
        weights = mix_weights(list(mix) + [noise])               # FOUR mixer sources (6.10)
        k, gain, ogain = LadderFx(**LADDER_CFG).regs(q, drive)     # clamped to 17 / 20 / 20 bits
        return dict(waves=waves, detune=detune, weights=weights,
                    cut_lo=usat(int(round(cutoff[0])), CUT_BITS),
                    cut_hi=usat(int(round(cutoff[1])), CUT_BITS),
                    res=q, drive=drive, k=k, gain=gain, ogain=ogain,
                    amp=AdsrFx.regs_from(*amp), fenv=AdsrFx.regs_from(*fenv), track=track,
                    vol=VOL_REF if vol is None else usat(int(round(vol * 32768)), VOL_BITS),
                    glide=glide_reg(glide_s),
                    nsel=usat(int(nsel), NSEL_BITS),
                    mmix=usat(int(round(mod_mix * MMIX_FULL)), MOD_BITS),
                    mwheel=usat(int(round(mod_wheel * MMIX_FULL)), MOD_BITS),
                    mpd=usat(int(round(mod_pitch * (1 << OCT_Q))), MOD_BITS),
                    mfd=usat(int(round(mod_filter * (1 << OCT_Q))), MOD_BITS),
                    mroute=((MR_OSC if osc_mod else 0) | (MR_FILT if filt_mod else 0)
                            | (MR_OSC3 if osc3_ctl else 0)))

    @staticmethod
    def note_incs(note, detune) -> list:
        """inc[k] for a note at each oscillator's detune (contract 6.3),
        clamped to the 24-bit register (it fires only at or above 48 kHz)."""
        f0 = note_hz(note)
        return [usat(phase_inc(f0 * 2.0 ** (dt / 12.0)), INC_BITS) for dt in detune]

    @staticmethod
    def note_track(note, track) -> int:
        return usat(int(round(track * note_hz(note) * 4.0)), CUT_BITS)

    # ---- the per-frame contract --------------------------------------------
    def play(self, regs: dict, writes: list, n: int) -> np.ndarray:
        """n frames of the voice from its current state, with `writes` --
        (frame, op, *args) -- applied at the start of their frames in list
        order (contract 4.3). Ops: ("INC", k, v, jump), ("TRACK", hz),
        ("GATE", 0|1), ("TRIG",), ("GLIDE", v), ("MWHEEL", v). The patch
        registers in `regs` are applied at frame 0. Returns int16."""
        self._apply_patch(regs)
        ev = {}
        for w in writes:
            f = int(w[0])
            assert 0 <= f < n, f"write {w} outside 0..{n-1}"
            ev.setdefault(f, []).append(w[1:])
        frames = sorted(ev)
        incs = [np.empty(n, dtype=np.int64) for _ in self.oscs]
        track = np.empty(n, dtype=np.int64)
        gate = np.empty(n, dtype=np.int64)
        trig = np.zeros(n, dtype=np.int64)
        glide = np.empty(n, dtype=np.int64)
        mw = np.empty(n, dtype=np.int64)
        bounds = [0] + [f for f in frames if f > 0] + [n]
        for f0, f1 in zip(bounds, bounds[1:]):
            for op, *args in ev.get(f0, []):            # step 1: apply control, in order
                if op == "INC":
                    k, v, jump = args[0], args[1], (args[2] if len(args) > 2 else False)
                    self.oscs[k].set_inc(v, jump, self.glide)
                elif op == "TRACK":
                    self.track_hz = int(args[0])
                elif op == "GATE":
                    self.gate = int(args[0])
                    if self.gate:
                        trig[f0] = 1                     # GATE_ON restarts the attack
                elif op == "TRIG":
                    trig[f0] = 1
                elif op == "GLIDE":
                    self.glide = int(args[0])
                elif op == "MWHEEL":
                    self.mwheel = int(args[0])      # the wheel is played, so it is per-frame
                else:
                    raise ValueError(op)
            m = f1 - f0
            for o, arr in zip(self.oscs, incs):
                arr[f0:f1] = o.slew(m, self.glide)
            track[f0:f1] = self.track_hz
            gate[f0:f1] = self.gate
            glide[f0:f1] = self.glide
            mw[f0:f1] = self.mwheel
        return self._render(incs, track, gate, trig, n, mw)

    def _apply_patch(self, r: dict):
        for o, shape in zip(self.oscs, r["waves"]):
            if self.pulse479_filter_candidate and shape == "pulse29":
                shape = "pulse479"
            o.set_shape(shape, self.blep)
        self.weights = list(r["weights"]) + [0] * (4 - len(r["weights"]))   # osc 0..2, then noise
        self.amp_env.set_regs(*r["amp"]); self.filt_env.set_regs(*r["fenv"])
        self.cut_lo, self.cut_hi = int(r["cut_lo"]), int(r["cut_hi"])
        self.res, self.drive = r["res"], r["drive"]
        self.k_reg, self.gain, self.ogain = int(r["k"]), int(r["gain"]), int(r["ogain"])
        self.vol = int(r["vol"])
        self.glide = int(r["glide"])
        self.nsel = int(r.get("nsel", 0))
        self.mmix = int(r.get("mmix", 0))
        self.mwheel = int(r.get("mwheel", 0))
        self.mpd = int(r.get("mpd", 0))
        self.mfd = int(r.get("mfd", 0))
        self.mroute = int(r.get("mroute", 0))
        self.regs = r

    def _modulate(self, incs, n, mw):
        """Contract 6.9 and 6.10 for n frames: the noise board, the modulation
        mix, and the increments the oscillators actually run on.

        This is SEQUENTIAL and has to be. The modulation source is oscillator
        3, and when OSC-3 CONTROL is on oscillator 3 is also a modulation
        DESTINATION -- the Model D wires the mod bus into all three
        oscillators' CV summers and only SW2 takes oscillator 3 off it
        [verified: SM 2.18]. So frame n's oscillator-3 phase depends on frame
        n-1's oscillator-3 output. The chip breaks that loop with a register:
        `mod_sig` is computed at the END of a frame and read at the START of
        the next -- one frame, 20.8 us, four orders of magnitude below any
        modulation rate the instrument reaches.

        The tap is oscillator 3's NAIVE waveform, before PolyBLEP. The
        modulation path is a control voltage: it is never summed into the
        mixer and never heard, so band-limiting it would only buy area. At
        LO-range rates the PolyBLEP window never opens and the two are
        identical anyway -- which the acceptance suite checks rather than
        assumes.

        Returns (inc_mod, white, pink, red, mant_f, sh_f, mod_sig)."""
        nz = self.noise
        inc_l = [[int(v) for v in a] for a in incs]
        inc_m = [np.empty(n, dtype=np.int64) for _ in range(3)]
        white = np.empty(n, dtype=np.int64)
        pink = np.empty(n, dtype=np.int64)
        red = np.empty(n, dtype=np.int64)
        mant_f = np.empty(n, dtype=np.int64)
        sh_f = np.empty(n, dtype=np.int64)
        msig = np.empty(n, dtype=np.int64)
        osc_mod = bool(self.mroute & MR_OSC)
        filt_mod = bool(self.mroute & MR_FILT)
        dest3 = bool(self.mroute & MR_OSC3)
        mmix = self.mmix if self.mmix < MMIX_FULL else MMIX_FULL
        a_osc = MMIX_FULL - mmix
        mpd, mfd, nsel = self.mpd, self.mfd, self.nsel
        shape3 = self.oscs[2].shape
        ph3 = self.oscs[2].phase
        m = self.mod_sig
        mwl = [int(v) for v in mw]
        for i in range(n):
            w, p, r = nz.step()
            white[i] = w; pink[i] = p; red[i] = r
            msig[i] = m
            amt = clamp16((m * mwl[i]) >> 15)                    # the wheel
            if filt_mod:
                mf, sf = exp2_q(clamp_oct((amt * mfd) >> 15))
            else:
                mf, sf = 32768, 15
            mant_f[i] = mf; sh_f[i] = sf
            if osc_mod:
                mp, sp = exp2_q(clamp_oct((amt * mpd) >> 15))
                for k in range(3):
                    v = inc_l[k][i]
                    if k < 2 or dest3:
                        v = (v * mp) >> sp
                        v = 0 if v < 0 else (INC_MAX if v > INC_MAX else v)
                    inc_m[k][i] = v
            else:
                inc_m[0][i] = inc_l[0][i]; inc_m[1][i] = inc_l[1][i]; inc_m[2][i] = inc_l[2][i]
            o3 = naive_one(shape3, ph3)                          # the tap, before the advance
            ph3 = (ph3 + inc_m[2][i]) & PHASE_MASK
            m = mod_pan(o3, r if nsel else p, mmix)              # 2.5: pink or RED for modulation
        self.mod_sig = m
        return inc_m, white, pink, red, mant_f, sh_f, msig

    def _render(self, incs, track, gate, trig, n, mw=None) -> np.ndarray:
        """Steps 2..8 of contract 4.2 for n frames. Integer only."""
        if mw is None:
            mw = np.full(n, self.mwheel, dtype=np.int64)
        incs, white, pink, red, mant_f, sh_f, msig = self._modulate(incs, n, mw)
        sig = []
        phase2_trace = []
        for k, (o, inc) in enumerate(zip(self.oscs, incs)):
            phase2_start = self._os2_phase[k]
            ia = np.broadcast_to(np.asarray(inc, dtype=np.int64), (n,))
            phase2_step = (ia // 2) * 2
            if self.oversample_2x and o.shape == "saw":
                rendered, self._os2_history[k], self._os2_phase[k] = _render_2x(
                    o, n, inc, self._os2_history[k], self._os2_phase[k])
                sig.append(rendered)
            else:
                if self.oversample_2x:
                    self._os2_phase[k] = (self._os2_phase[k] + int(np.sum(phase2_step))) & PHASE_MASK
                sig.append(o.render(n, inc))
            if self.oversample_2x:
                phase2_trace.append((phase2_start + np.cumsum(phase2_step)) & PHASE_MASK)
            else:
                phase2_trace.append(np.zeros(n, dtype=np.int64))
        n_audio = pink if self.nsel else white                   # 2.5: WHITE or pink for audio
        mixed = mix_fx(sig + [n_audio], self.weights)            # step 3, four sources
        ae = self.amp_env.render(n, gate, trig)                  # step 4
        fe = self.filt_env.render(n, gate, trig)
        span = self.cut_hi - self.cut_lo                         # step 5: cutoff
        cut = np.clip(self.cut_lo + ((span * fe) >> 15) + track, CUT_MIN, CUT_MAX)
        cut = np.clip((cut * mant_f) >> sh_f, CUT_MIN, CUT_MAX)  # step 5b: filter modulation
        lad = self.ladder
        kc = kc_from_cut(cut, self.k_rom, self.KB)
        k_eff = k_effective(self.k_reg, kc) if self.k_comp else np.full(n, self.k_reg, dtype=np.int64)
        regs = dict(k=self.k_reg, gain=self.gain, ogain=self.ogain, k_q14=k_eff)
        if self.g_exact:   # measurement only: float exp inside LadderFx
            y = lad.process(mixed.astype(np.int16), cut.astype(np.float64), self.res, self.drive, **regs)
            g = None
        else:                                                    # step 6: ladder
            g = g_from_cut(cut, self.g_rom, self.GB)
            y = lad.process(mixed.astype(np.int16), None, self.res, self.drive, g_q16=g, **regs)
        y = y.astype(np.int64)
        v = (y * ae) >> 15                                       # step 7: VCA, after the filter
        out = sat16((v * self.vol) >> 15)                        # step 8: volume, the one output clamp
        self.trace = dict(osc=sig, mixed=mixed, amp_env=ae, filt_env=fe, cut=cut, g=g,
                          kc=kc, k_eff=k_eff, ladder=y, vca=v, incs=incs, gate=gate, trig=trig,
                          white=white, pink=pink, red=red, noise=n_audio, mod_sig=msig,
                          mant_f=mant_f, sh_f=sh_f, mwheel=mw, phase2=phase2_trace,
                          filter_reconstruction=getattr(lad, "last_reconstruction", None),
                          filter_decimation=(getattr(getattr(lad, "converter", None),
                                                     "last_decimation", None)))
        return out.astype(np.int16)

    # ---- one note from reset: the reference sequences of contract 16 --------
    def note_on(self, note, dur, *, gate=None, glide_from=None, **patch) -> dict:
        """Register image and writes for one note of `dur` seconds, the gate
        on for `gate` seconds (default 0.8 dur). `glide_from`: the increment
        starts at that note's and glides. Same patch keywords as
        engines.mono_note plus `vol` and `glide_s`."""
        n = int(dur * SR)
        gate = dur * 0.8 if gate is None else gate
        gate_n = min(n, max(1, int(gate * SR)))
        regs = self.patch_regs(**patch)
        writes = []
        if glide_from is not None:
            for k, v in enumerate(self.note_incs(glide_from, regs["detune"])):
                writes.append((0, "INC", k, v, True))
            for k, v in enumerate(self.note_incs(note, regs["detune"])):
                writes.append((0, "INC", k, v, False))
        else:
            for k, v in enumerate(self.note_incs(note, regs["detune"])):
                writes.append((0, "INC", k, v, True))
        writes.append((0, "TRACK", self.note_track(note, regs["track"])))
        writes.append((0, "GATE", 1))
        if gate_n < n:
            writes.append((gate_n, "GATE", 0))
        return dict(n=n, gate_n=gate_n, regs=regs, writes=writes)

    def run(self, r: dict) -> np.ndarray:
        return self.play(r["regs"], r["writes"], r["n"])

    def note(self, note, dur, **kw) -> np.ndarray:
        self.reset()
        return self.run(self.note_on(note, dur, **kw))


def glide_reg(seconds_per_octave: float) -> int:
    """Host conversion for the glide register (DR 0004): the Q0.24 ratio per
    frame that covers one octave in `seconds_per_octave`; 0 for off."""
    if not seconds_per_octave or seconds_per_octave <= 0:
        return 0
    return usat(max(1, int(round((2.0 ** (1.0 / (seconds_per_octave * SR)) - 1.0) * (1 << GLIDE_BITS)))),
                GLIDE_BITS)


# ---- the reference host ------------------------------------------------------
class KeyHost:
    """Turns key events into the voice's control writes. This is the host
    firmware's job in the product (DR 0002) and is INFORMATIVE in the
    contract; the policies are parameters so that DR 0003 / 0004 can state
    a default and keep the alternatives measurable.

      priority  'last' | 'low' | 'high'   which held key sounds (mono)
      trigger   'single' | 'multi'        TRIG on every new key while one is
                                          held (multi) or only when no key
                                          was held (single: legato)
      glide     'off' | 'always' | 'legato'
                                          slew never / on every new pitch /
                                          only when a key was already held
      mode      'mono' | 'para'           para: held keys go to oscillators
                                          0..2 in press order, the rest
                                          double the newest key
    """
    def __init__(self, priority="last", trigger="single", glide="always", mode="mono"):
        assert priority in ("last", "low", "high") and trigger in ("single", "multi")
        assert glide in ("off", "always", "legato") and mode in ("mono", "para")
        self.priority, self.trigger, self.glide, self.mode = priority, trigger, glide, mode

    def sounding(self, held: list) -> list:
        """Which note each oscillator plays, from the held list (press order)."""
        if self.mode == "para":
            if not held:
                return None
            return [held[i] if i < len(held) else held[-1] for i in range(3)]
        if not held:
            return None
        n = {"last": held[-1], "low": min(held), "high": max(held)}[self.priority]
        return [n, n, n]

    def writes(self, events: list, regs: dict, first_from_reset: bool = True) -> list:
        """events: (frame, 'on'|'off', note), any order. Returns the write list
        for VoiceFx.play. From reset the first pitch is always a jump: there
        is no previous pitch to glide from."""
        held, out, prev = [], [], None
        cur = None                                           # per-osc notes sounding
        for f, kind, note in sorted(events, key=lambda e: (e[0], e[1] == "on")):
            was_held = bool(held)
            if kind == "on":
                if note in held:
                    held.remove(note)
                held.append(note)
            else:
                if note not in held:
                    continue
                held.remove(note)
            new = self.sounding(held)
            if new is None:                                  # last key up
                out.append((f, "GATE", 0))
                cur = None
                continue
            if new != cur:
                jump = (self.glide == "off") or (self.glide == "legato" and not was_held) \
                       or (prev is None and first_from_reset)
                for k, (nt, dt) in enumerate(zip(new, regs["detune"])):
                    if cur is None or nt != cur[k] or jump:
                        out.append((f, "INC", k, phase_inc(note_hz(nt) * 2.0 ** (dt / 12.0)), jump))
                lead = new[-1] if self.mode == "para" else new[0]
                out.append((f, "TRACK", VoiceFx.note_track(lead, regs["track"])))
                cur = new
                prev = lead
            if kind == "on":
                if not was_held:
                    out.append((f, "GATE", 1))               # GATE_ON restarts the attack
                elif self.trigger == "multi":
                    out.append((f, "TRIG",))
        return out


def render_mono_fx(seq, dur_total, voice: VoiceFx = None, host: KeyHost = None) -> np.ndarray:
    """engines.render_mono's sequence format -- (start_s, note, dur_s, kwargs),
    the gate on for kwargs['gate'] or 0.8 dur -- through ONE continuous voice
    from reset, the reference host turning it into writes. A note's `glide`
    flag selects the host's 'always' glide; the patch is the first note's
    kwargs (one patch per sequence). int16 out."""
    voice = VoiceFx() if voice is None else voice
    n = int(dur_total * SR)
    kw0 = dict(seq[0][3]); kw0.pop("blep", None)
    glide_on = bool(kw0.pop("glide", False))
    kw0.pop("gate", None)
    regs = VoiceFx.patch_regs(**kw0)
    host = KeyHost(glide="always" if glide_on else "off") if host is None else host
    events = []
    for start, note, d, kw in seq:
        g = kw.get("gate", None)
        g = d * 0.8 if g is None else g
        on = int(start * SR)
        off = min(n - 1, on + max(1, int(g * SR)))
        if on < n:
            events.append((on, "on", note)); events.append((off, "off", note))
    voice.reset()
    return voice.play(regs, host.writes(events, regs), n)
