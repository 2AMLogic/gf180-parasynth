"""Fixed-point model of the modal resonator bank: the integer reference that
rtl-sketch/modal_dp.v is compared against, sample for sample, no tolerance.

STATUS: sizing proposed (contract section 15), measured by
`python3 model/modal_fixed.py`, not ratified. `audition/physical.py::modal` is
float and normalises its output afterwards, so it decides neither the absolute
scale nor the coefficient precision. Those are decided here the way
model/fixed.py decided the ladder's: by finding the smallest widths that still
behave like the float.

Each mode is   y[n] = x[n] + a1*y[n-1] + a2*y[n-2],   a1 = 2 r cos w, a2 = -r^2.

Since the drum section (model/drums_fx.py, DR 0008) the bank is the TR-808's
bridged-T bodies and its band-pass / high-pass filters as well as the struck
bar, so three things were added and nothing that existed was changed:

  * per-mode excitation: `exc` is one value per mode (a 1-D array is
    broadcast to every mode, which is the original bank);
  * a selectable numerator on the first `nums` modes -- RAW (all-pole, the
    resonator), BP (x = e - e[n-2], the (1 - z^-2) band-pass numerator) and
    HP (x = e - 2 e[n-1] + e[n-2], the (1 - z^-1)^2 high-pass numerator),
    from two history registers per mode; modes at or above `nums` are RAW
    whatever their `num` register says;
  * a 19-bit Q4.15 output word (`out_bits`), DR 0005's width, in place of
    the 16-bit one, and `headroom` as the drum section sets it (0).

The finding is the coefficient width. At MIDI 28 (41 Hz) the pole sits at
w = 0.0054 rad with r = 1 - 2.3e-5, and in direct form the pitch and the decay
live in the DIFFERENCE between a1 and 2 and between a2 and -1. Rounding a1 to
Q2.16 -- the sketch's 18-bit ports -- moves the pole angle by up to
2^-17 / (2 r sin w): at note 28 that is +2.4 % of the frequency, 41 cents,
and -1.9 dB SNR against the float, i.e. a different signal. Q2.24 is 0.005 %
and 48 dB; the SNR ceiling above that is the phase the residual pitch error
accumulates, not noise (Q2.28 reaches 60-70 dB). `sweep()` prints every
width. Pitch and decay error are the criteria; SNR is reported alongside.
The 808 bass drum at its longest decay (56 Hz, 1 - r = 5.9e-5) is inside the
range this sweep validated.

Formats (constructor parameters; the defaults are the sweep's choice):
    excitation   signed, EXC_BITS (21) wide: the drum section's exact sum of
                 up to 16 path values of 17 bits (contract 15.5); the strike
                 burst of the sweep is Q1.15 inside it
    coefficient  signed, 2 integer bits + CF fraction bits (CF=24: 26 bits)
    amp          Q0.16 unsigned; 1.0 is 65535
    num          0 RAW, 1 BP, 2 HP (3 reads as RAW), 2 bits, modes < nums only
    state        SB bits, SQ fraction bits; the excitation enters as
                 exc << (SQ - 15), or >> (15 - SQ) when SQ < 15
    output       sat(mix >> (SQ - 15 + HEADROOM), out_bits). The bank rings
                 up to 657x the strike at note 28 (measured on the float);
                 HEADROOM = 10 puts the bar's note 28 at -4 dBFS in a 16-bit
                 word and was the rev-3 proposal. The drum section runs the
                 bank at HEADROOM = 0 with a 19-bit word and balances the
                 voices with `amp` (contract 15.6).
"""
from __future__ import annotations
import math
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "audition"))
import numpy as np
from dsp import SR, note_hz

DEFAULT_MODES = ((1.0, 1.0, 1.0), (2.76, 0.6, 0.7), (5.40, 0.4, 0.5), (8.93, 0.25, 0.35))
RAW, BP, HP = 0, 1, 2            # the numerator register `num` (contract 15.6)
EXC_BITS = 21                    # excitation word: 16 paths x 17 bits, exact (contract 15.5)
OUT_BITS = 19                    # output word Q4.15 (DR 0005's width)


def sat(v: int, bits: int) -> int:
    lo, hi = -(1 << (bits - 1)), (1 << (bits - 1)) - 1
    return lo if v < lo else (hi if v > hi else v)


def shl(v: int, k: int) -> int:
    return v << k if k >= 0 else v >> (-k)


def strike(dur: float, seed: int = 2, amount: float = 1.0) -> np.ndarray:
    """physical.modal's excitation: a 1.6 ms Hann-windowed noise burst, float."""
    n = int(dur * SR)
    rng = np.random.default_rng(seed)
    exc = np.zeros(n)
    hit = max(1, int(0.0016 * SR))
    exc[:hit] = rng.uniform(-1, 1, hit) * np.hanning(hit) * amount
    return exc


def modal_float_raw(exc: np.ndarray, note: int, modes=DEFAULT_MODES) -> np.ndarray:
    """physical.modal's loop on a given excitation, WITHOUT the final
    normalisation -- the absolute-scale reference the fixed model is measured
    against."""
    n = len(exc)
    f0 = note_hz(note)
    out = np.zeros(n)
    for ratio, amp, decay in modes:
        f = f0 * ratio
        if f > SR * 0.45:
            continue
        r = math.exp(-1.0 / (decay * 0.9 * SR))
        w = 2 * math.pi * f / SR
        a1, a2 = 2 * r * math.cos(w), -r * r
        y1 = y2 = 0.0
        for t in range(n):
            y = exc[t] + a1 * y1 + a2 * y2
            y2, y1 = y1, y
            out[t] += amp * y
    return out


def pole_regs(f0_hz: float, q: float, coef_frac: int = 24, fs: int = SR) -> tuple[int, int]:
    """Host conversion (contract 15.6, informative): the Q2.CF coefficient
    pair of a two-pole resonator with centre f0 and quality factor Q, as
    docs/tr808-reference.md section 14 tabulates the 808's bridged-T bodies:
    r = exp(-pi f0 / (Q fs)), w = 2 pi f0 / fs, a1 = 2 r cos w, a2 = -r^2,
    each rounded and clamped to the register. Float; host side only."""
    r = math.exp(-math.pi * f0_hz / (q * fs))
    w = 2.0 * math.pi * f0_hz / fs
    qf = 1 << coef_frac
    lo, hi = -(2 << coef_frac), (2 << coef_frac) - 1
    a1 = max(lo, min(hi, int(round(2.0 * r * math.cos(w) * qf))))
    a2 = max(lo, min(hi, int(round(-r * r * qf))))
    return a1, a2


class ModalFx:
    def __init__(self, coef_frac=24, state_bits=28, state_q=15, headroom=10, modes=4,
                 rounding=False, out_bits=OUT_BITS, nums=0, exc_bits=EXC_BITS):
        self.CF, self.SB, self.SQ, self.HR, self.M = coef_frac, state_bits, state_q, headroom, modes
        self.OB, self.NUMS, self.EW = out_bits, nums, exc_bits
        # Floor. Round-half-up was measured (sweep) and buys nothing: the SNR
        # ceiling against the float is the accumulated PHASE of the residual
        # pitch error (-0.005 % at note 28 is 0.36 degrees after 0.5 s, -44 dB),
        # not truncation noise. So no rounding constant in the datapath.
        self.RND = (1 << (coef_frac - 1)) if rounding else 0
        self.reset()

    def reset(self):
        self.y1 = [0] * self.M
        self.y2 = [0] * self.M
        self.h1 = [0] * self.M          # excitation history, modes < nums (contract 15.6)
        self.h2 = [0] * self.M

    def coefficients(self, note: int, modes=DEFAULT_MODES) -> list[tuple[int, int, int]]:
        """(a1, a2, amp) integers per mode from physical.modal's float formulas:
        the struck bar, the bank's OPTIONAL preset family (contract 15.6). A
        mode above 0.45*SR is (0, 0, 0): the float model skips it, and a
        resonator with zero coefficients and zero amp contributes nothing.
        Modes beyond the bar's four are (0, 0, 0)."""
        f0 = note_hz(note)
        q = 1 << self.CF
        lo, hi = -(2 << self.CF), (2 << self.CF) - 1
        out = []
        for ratio, amp, decay in modes:
            f = f0 * ratio
            if f > SR * 0.45:
                out.append((0, 0, 0))
                continue
            r = math.exp(-1.0 / (decay * 0.9 * SR))
            w = 2 * math.pi * f / SR
            a1, a2 = 2 * r * math.cos(w), -r * r
            out.append((max(lo, min(hi, int(round(a1 * q)))),
                        max(lo, min(hi, int(round(a2 * q)))),
                        min(65535, int(round(amp * 65536)))))
        out += [(0, 0, 0)] * (self.M - len(out))
        assert len(out) == self.M
        return out

    def step(self, exc, coefs, num=None) -> int:
        """One sample: `exc` is one integer per mode (contract 15.6). Returns
        the output word, sat(mix >> (SQ - 15 + HR), out_bits). Integer only."""
        CF, SB, SQ = self.CF, self.SB, self.SQ
        osh = SQ - 15 + self.HR
        y1, y2, h1, h2 = self.y1, self.y2, self.h1, self.h2
        mix = 0
        for m in range(self.M):
            a1, a2, amp = coefs[m]
            e = shl(int(exc[m]), SQ - 15)
            if m < self.NUMS and num is not None:
                k = int(num[m])
                if k == BP:
                    x = e - h2[m]
                elif k == HP:
                    x = e - 2 * h1[m] + h2[m]
                else:
                    x = e
                h2[m], h1[m] = h1[m], e
            else:
                x = e
            acc = a1 * y1[m] + a2 * y2[m] + self.RND  # exact, Q(SQ+CF)
            y = sat((acc >> CF) + x, SB)               # shift, then clamp
            y2[m], y1[m] = y1[m], y
            mix += (y * amp) >> 16                   # Q.SQ, floor
        return sat(mix >> osh, self.OB)

    def process(self, exc_q15: np.ndarray, coefs, num=None) -> np.ndarray:
        """exc_q15: the excitation, int16 -- one value per sample (broadcast to
        every mode) or an (n, modes) array. Returns int32 (int16 when out_bits
        is 16). Everything between is integer."""
        exc = np.asarray(exc_q15)
        n = len(exc)
        out = np.empty(n, dtype=np.int16 if self.OB <= 16 else np.int32)
        bcast = exc.ndim == 1
        for t in range(n):
            e = [int(exc[t])] * self.M if bcast else [int(v) for v in exc[t]]
            out[t] = self.step(e, coefs, num)
        return out


def f2q15(x): return np.clip(np.round(x * 32767), -32768, 32767).astype(np.int16)
def q15f(x): return x.astype(np.float64) / 32768.0


# ---- sizing --------------------------------------------------------------------
def pole_error(note: int, cf: int, ratio=1.0, decay=1.0):
    """Analytic: what rounding a1, a2 to Q2.cf does to mode pitch and decay."""
    f = note_hz(note) * ratio
    r = math.exp(-1.0 / (decay * 0.9 * SR)); w = 2 * math.pi * f / SR
    q = 1 << cf
    a1q, a2q = round(2 * r * math.cos(w) * q) / q, round(-r * r * q) / q
    rq = math.sqrt(-a2q)
    wq = math.acos(max(-1.0, min(1.0, a1q / (2 * rq))))
    df = (wq - w) / w * 100.0
    dd = ((1.0 - r) / (1.0 - rq) - 1.0) * 100.0 if rq < 1.0 else float("inf")
    return df, dd


def snr_db(note: int, m: ModalFx, dur=0.5, seed=2) -> float:
    exc = strike(dur, seed)
    xq = f2q15(exc)
    ref = modal_float_raw(q15f(xq), note) / (1 << m.HR)      # same input, same scale
    m.reset()
    y = q15f(m.process(xq, m.coefficients(note)))
    e = y - ref
    return 20 * math.log10(max(np.sqrt((ref ** 2).mean()), 1e-12) / max(np.sqrt((e ** 2).mean()), 1e-12))


def sweep():
    print("coefficient fraction bits vs mode-0 pitch and decay error (analytic, note 28 / note 52):")
    for cf in (16, 18, 20, 22, 24, 26):
        d28 = pole_error(28, cf); d52 = pole_error(52, cf)
        print(f"  CF={cf:2d}: note 28 pitch {d28[0]:+7.3f} %  decay {d28[1]:+7.2f} %   "
              f"| note 52 pitch {d52[0]:+7.3f} %  decay {d52[1]:+7.2f} %")
    print("state width vs SNR against the float bank, CF=24 (0.5 s, strike 1.0):")
    for sb, sq in ((24, 11), (26, 13), (28, 15), (32, 19)):
        m = ModalFx(coef_frac=24, state_bits=sb, state_q=sq)
        print(f"  SB={sb} SQ={sq}: " + "  ".join(f"note {n:2d} {snr_db(n, m):5.1f} dB" for n in (28, 52, 88)))
    print("coefficient width vs SNR, SB=28 SQ=15:")
    for cf in (16, 20, 24, 28):
        m = ModalFx(coef_frac=cf)
        print(f"  CF={cf}: " + "  ".join(f"note {n:2d} {snr_db(n, m):5.1f} dB" for n in (28, 52, 88)))
    print("recursion rounding vs floor, CF=24 SB=28 SQ=15:")
    for rnd in (False, True):
        m = ModalFx(rounding=rnd)
        print(f"  {'round-half-up' if rnd else 'floor        '}: "
              + "  ".join(f"note {n:2d} {snr_db(n, m):5.1f} dB" for n in (28, 52, 88)))


if __name__ == "__main__":
    sweep()
