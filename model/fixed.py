"""Fixed-point model of the ladder. Integers only -- no float anywhere in the
signal path -- so that RTL can be bit-exact against it.

Why this exists before any Verilog: in a feedback loop rounding error
recirculates, and two failures only appear in integers.

  * **Dead zone.** At a low cutoff `g` is tiny (g = 1-exp(-2*pi*30/96000) is
    about 0.00196). If the state word has too few fraction bits, `g * diff`
    truncates to zero and the filter simply stops responding. A float model
    cannot show you this.
  * **Limit cycles.** Truncation in a recursive loop can sustain a small
    oscillation forever -- a faint buzz that never decays into silence.

Both are width questions, so width is a parameter here and the point of the
module is to find the smallest one that still sounds like the filter.

The state is held in units of 2*Vt rather than in volts. The paper's stage is

    y += 2*Vt*g*( tanh(x/2Vt) - tanh(y/2Vt) )

and dividing through by 2*Vt turns that into

    Y += g*( tanh(X) - tanh(Y) )

so the tanh argument IS the state, the 2*Vt multiply disappears from the inner
loop, and the table is indexed by the state directly. One less multiplier in
the datapath, and no scaling constant to get wrong.

Formats:
    signal      Q1.15   int16, +-1.0
    state       Q(SB-SQ).SQ in units of 2*Vt; default 24-bit, 20 fraction
                bits, so +-8.0 -- tanh saturates by 4.0, leaving 6 dB of
                headroom for the resonant peak before the state clamps
    coefficient Q0.16   uint16
    tanh table  128 entries over [0,4), odd-symmetric, optional interpolation
"""
from __future__ import annotations
import math
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "audition"))
import numpy as np
import dsp
from dsp import SR

SIG_Q = 15
COEF_Q = 16
VT2 = 0.05                      # 2*Vt, the paper's scaling
TANH_DOMAIN = 4.0               # tanh(4) = 0.9993; beyond this, clamp
# The table's GUARD word: the value the top bin interpolates toward and the
# value the clamp returns above the domain. It is 32767 and is NOT
# tanh(TANH_DOMAIN) = 32745, which is a known wrong constant recorded in DR
# 0013 and NOT corrected: `rtl-sketch/drum_dp.v` reads the same ROM image with
# its own hardcoded clamp, so moving this word alone would silently break the
# DRUM section's bit-exactness. It is one line in a file the voice does not
# own. DR 0013 has the measurement that says what correcting it buys -- the
# top bin twelve times more accurate, and nothing else: h5 at
# self-oscillation does not move by 0.05 dB, because the 16-entry table's own
# worst error, 5.97e-3 at x = 0.625, is nine times larger.
TANH_GUARD = 32767                 # see above and DR 0013; tanh(4) would be 32745


def shl(v: int, k: int) -> int:
    """Shift left by k, or right by -k. The state can legitimately have fewer
    fraction bits than the Q1.15 signal; that is a design point to measure, not
    a crash."""
    return v << k if k >= 0 else v >> (-k)


def sat(v: int, bits: int) -> int:
    lo, hi = -(1 << (bits - 1)), (1 << (bits - 1)) - 1
    return lo if v < lo else (hi if v > hi else v)


def usat(v: int, bits: int) -> int:
    """Clamp to an unsigned register of `bits` bits, 0 .. 2^bits - 1. Every
    host-side conversion (NUMERIC-CONTRACT.md 5.5) passes its result through
    this, so the model can never hold a value the register of 5.1 cannot."""
    hi = (1 << bits) - 1
    return 0 if v < 0 else (hi if v > hi else v)


class LadderFx:
    K_BITS, GAIN_BITS = 17, 20      # register widths of k and of gain/ogain (contract 5.1)

    def __init__(self, state_bits=24, state_q=20, tanh_entries=128,
                 interp=True, volts_per_unit=0.13, oversample=2, out_bits=16):
        """`out_bits`: width of the saturated output word, Q(out_bits-16).15.
        16 is Q1.15 (rev 1 of the contract, where loud patches clipped here);
        the voice uses 19 (Q4.15, +-8.0) so that the resonant peak and the
        (1 + 2 res) passband compensation have headroom and the clip moves to
        the output stage behind the VCA (DR 0005)."""
        self.SB, self.SQ = state_bits, state_q
        self.OB = out_bits
        self.N, self.interp = tanh_entries, interp
        self.vpu, self.os = volts_per_unit, oversample
        self.dom_fx = int(TANH_DOMAIN * (1 << self.SQ))      # state value where tanh clamps
        # Sample points depend on how the table is READ. Interpolating between
        # entries requires them at bin EDGES (i/N); reading nearest-entry wants
        # them at bin MIDPOINTS ((i+0.5)/N), which halves the worst-case error.
        # Mixing the two puts a half-bin skew on every lookup -- measured as an
        # 8 dB penalty, i.e. interpolation appearing to make accuracy worse.
        off = 0.0 if interp else 0.5
        self.tbl = [int(round(math.tanh((i + off) / self.N * TANH_DOMAIN) * 32767))
                    for i in range(self.N)]
        self.reset()

    def reset(self):
        self.y = [0, 0, 0, 0]
        self.w = [0, 0, 0, 0]
        self.d1 = self.d2 = 0

    # ---- tanh(y / 2Vt) from the half table, odd symmetry, Q1.15 out --------
    def tanh_fx(self, y: int) -> int:
        neg = y < 0
        a = -y if neg else y
        if a >= self.dom_fx:
            r = TANH_GUARD
        else:
            pos = a * self.N
            idx = pos // self.dom_fx
            if self.interp:
                rem = pos - idx * self.dom_fx
                t0 = self.tbl[idx]
                t1 = self.tbl[idx + 1] if idx + 1 < self.N else TANH_GUARD
                r = t0 + ((t1 - t0) * rem) // self.dom_fx
            else:
                r = self.tbl[idx]
        return -r if neg else r

    def regs(self, res: float, drive: float = 1.0) -> tuple[int, int, int]:
        """The three ladder registers from the float controls -- the host's
        conversion (contract 5.5) -- each clamped to its register width.

        Widths, since the RTL has to carry them: k is 4*res in Q3.14 and needs
        17 bits from res = 1.0 (the clamp fires at res = 2.0); gain =
        drive*vpu/2Vt is 2.6*drive in Q4.16, 19 bits at drive 3 and 20 bits to
        drive 6.15, where the clamp fires; ogain = 2Vt/vpu*(1+2*res) in Q4.16
        is 17 bits to res 1.5. g is Q0.16 and reaches 61,659 at the 0.45*fs
        cutoff clamp (bit 15 set above ~10.6 kHz, so it is NOT a signed 16-bit
        quantity)."""
        k, gain, ogain = self.regs_unclamped(res, drive)
        return usat(k, self.K_BITS), usat(gain, self.GAIN_BITS), usat(ogain, self.GAIN_BITS)

    def regs_unclamped(self, res: float, drive: float = 1.0) -> tuple[int, int, int]:
        """`regs` before the register-width clamp: the one formula, so a caller
        that must REFUSE an out-of-range word rather than clamp it (a versioned
        calibration, `voice_fx.ladder_regs`) computes exactly what `regs` does."""
        k = int(round(4.0 * res * (1 << 14)))                              # Q3.14
        # Q1.15 audio -> state units (2*Vt). One constant: drive*vpu/(2*Vt).
        gain = int(round(drive * self.vpu / VT2 * (1 << COEF_Q)))
        # state units -> Q1.15 audio on the way out, with resonance gain comp
        ogain = int(round(VT2 / self.vpu * (1.0 + 0.5 * res * 4.0) * (1 << COEF_Q)))
        return k, gain, ogain

    def coefficients(self, cutoff_hz: np.ndarray, res: float, drive: float = 1.0,
                     *, g_q16: np.ndarray = None, n: int = None,
                     k: int = None, gain: int = None, ogain: int = None,
                     k_q14: np.ndarray = None):
        """The four integers the loop actually runs on, from the float controls.
        Split out so a testbench can hand the RTL exactly what the model used.

        `g_q16`, when given, supplies the per-sample Q0.16 coefficient directly
        instead of converting `cutoff_hz` here with a float exp. `voice_fx.py`
        passes it from its own integer ROM so the cutoff-modulation path is
        integer too. `k`, `gain`, `ogain`, when given, are used as the register
        values instead of `regs(res, drive)` -- so a test can drive the loop
        with any legal register contents, not only ones a patch produces.
        `k_q14`, when given, is the per-sample Q3.14 feedback coefficient (the
        voice's resonance-compensated k, DR 0006) in place of the constant k;
        the RTL's k port is per-sample already."""
        fs = SR * self.os
        if g_q16 is not None:
            g_tab = np.asarray(g_q16, dtype=np.int64)
            if n is not None:
                assert len(g_tab) == n, f"g_q16 has {len(g_tab)} entries, need {n}"
        else:
            # coefficient per sample, Q0.16 -- a real design would ROM this
            # DR 0011: the same tuning the ROM is built with, so the float
            # path and voice_fx's integer ROM describe ONE filter.
            g_tab = np.clip(
                np.round((1.0 - np.exp(-2.0 * math.pi
                                       * tuned_cutoff(np.clip(cutoff_hz, 20.0, fs * 0.45)) / fs))
                         * (1 << COEF_Q)), 1, (1 << COEF_Q) - 1).astype(np.int64)
        rk, rgain, rogain = self.regs(res, drive)
        k = rk if k is None else k
        gain = rgain if gain is None else gain
        ogain = rogain if ogain is None else ogain
        if k_q14 is not None:
            k = np.asarray(k_q14, dtype=np.int64)
            if n is not None:
                assert len(k) == n, f"k_q14 has {len(k)} entries, need {n}"
        return g_tab, k, gain, ogain

    def process(self, x_q15: np.ndarray, cutoff_hz: np.ndarray, res: float,
                drive: float = 1.0, *, g_q16: np.ndarray = None,
                k: int = None, gain: int = None, ogain: int = None,
                k_q14: np.ndarray = None):
        """x_q15: int16 samples. Returns int16 when out_bits is 16, else int32
        (Q(OB-16).15, saturated to OB bits). Everything between is integer."""
        os_, SQ, SB, OB = self.os, self.SQ, self.SB, self.OB
        n = len(x_q15)
        g_tab, k_tab, gain, ogain = self.coefficients(cutoff_hz, res, drive,
                                                      g_q16=g_q16, n=n, k=k, gain=gain,
                                                      ogain=ogain, k_q14=k_q14)
        k_per_sample = np.ndim(k_tab) > 0
        k = None if k_per_sample else int(k_tab)
        out = np.empty(n, dtype=np.int16 if OB <= 16 else np.int32)
        y, w = self.y, self.w
        d1, d2 = self.d1, self.d2
        TQ = SQ - SIG_Q                                 # shift Q1.15 -> state Q
        for i in range(n):
            xi = int(x_q15[i])
            g = int(g_tab[i])
            if k_per_sample:
                k = int(k_tab[i])
            for _ in range(os_):
                fb = (d1 + d2) >> 1
                u = sat(shl(xi * gain, TQ - COEF_Q) - ((k * fb) >> 14), SB)
                w0 = self.tanh_fx(u)
                for s in range(4):
                    prev = w0 if s == 0 else w[s - 1]
                    diff = prev - w[s]                     # Q1.15, +-2
                    inc = (g * shl(diff, TQ)) >> COEF_Q    # -> state Q
                    y[s] = sat(y[s] + inc, SB)
                    w[s] = self.tanh_fx(y[s])
                d2, d1 = d1, y[3]
            out[i] = sat((shl(y[3], -TQ) * ogain) >> COEF_Q, OB)
        self.y, self.w, self.d1, self.d2 = y, w, d1, d2
        return out


def tuned_cutoff(cutoff_hz):
    """The frequency the one-pole cascade is actually run at for a COMMANDED
    cutoff: `f * CUT_TRIM * fcr(f)`, Huovilainen's tuning polynomial and DR
    0011's constant. Hand this to any float reference (`dsp.ladder`) and it
    becomes the same filter as `LadderFx` at the commanded cutoff -- which is
    what makes a float/fixed comparison a measurement of QUANTISATION rather
    than of the tuning difference. Imported locally: voice_fx imports this
    module."""
    import voice_fx as _vf
    c = np.asarray(cutoff_hz, dtype=np.float64)
    return c * _vf.CUT_TRIM * _vf.fcr(c)


def f2q15(x: np.ndarray) -> np.ndarray:
    return np.clip(np.round(x * 32767), -32768, 32767).astype(np.int16)


def q15f(x: np.ndarray) -> np.ndarray:
    return x.astype(np.float64) / 32768.0
