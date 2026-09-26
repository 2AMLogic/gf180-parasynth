#!/usr/bin/env python3
"""Rungs 2-4 of issue #46: the candidate ladders, in ONE fixed-point frame.

This module holds the *filters*. `tools/compare_ladder_candidates.py` holds the
six-dimension comparison that scores them, and `model/test_ladder_candidates.py`
holds the ground truth and the injected controls. Nothing here decides anything.

WHAT IS AND IS NOT CLAIMED ABOUT THE CANDIDATES
-----------------------------------------------
Issue #46's rungs name two published models (D'Angelo & Valimaki 2013 and
2014). **We did not have the authors' reference code or errata available in
this environment**, so the candidates below are named for their STRUCTURE, not
for a paper, and no claim is made that they are bit-faithful reimplementations:

    shipped        Huovilainen (DAFx-04) exactly as `fixed.LadderFx` runs it --
                   explicit forward Euler, `tanh` in every stage, and a
                   HALF-SAMPLE feedback delay `(d1 + d2) / 2`. Rung 1.
    zdf-newton-2   the implicit (delay-free) form of the same ODE, solved with
                   a FIXED 2-iteration Newton. This is the structure DR 0001
                   rejected and named as its own reversal condition, so it is
                   the candidate that matters most regardless of which paper it
                   is attributed to.
    zdf-newton-3   the same at 3 iterations, the other half of DR 0001's
                   reversal condition.
    zdf-explicit   the delay-free loop resolved in CLOSED FORM instead of
                   iteratively: one linearisation of the cascade per
                   sub-sample, one divide, fixed latency. This is the shape of
                   an explicit delay-free-loop method (issue #46's rung 3) and
                   carries the issue's own caution -- it is not a true
                   zero-delay-feedback solver, it is a one-step correction.
    reference-ode  rung 4, OFFLINE ONLY: float, 32x oversampled, trapezoidal,
                   Newton to convergence. Not a shipping candidate; it is the
                   ground truth the others are scored against.

WHY THE GROUND TRUTH IS NOT ONE OF US
-------------------------------------
`CLAUDE.md`: *an estimator calibrated on our own model is not validated.* The
ladder has an answer that is known independently of every line of code in this
repository -- from the transfer function, not from a measurement:

    y3 = A^4 x / (1 + k A^4),   A(jw) = 1 / (1 + j w / wc)

    (1 + jW)^4 is real and negative only at W = 1, where it equals -4

so the ideal ladder self-oscillates **at exactly the cutoff, when k = 4**
(res = 1.0), for every cutoff, and its small-signal magnitude response is the
closed form `analytic_response_db` evaluates. That is textbook, and it is the
calibration signal: `assert_apparatus` measures `reference-ode` against the
closed form and REFUSES if it misses by more than `REFERENCE_FLOOR_DB`, so the
rung-4 model is ground truth only while it demonstrably is one.

The closed form is checkable by hand, which is the point. The self-oscillation
condition above needs no code at all, and the harness quotes every candidate's
tuning error against `f_osc = cutoff` rather than against our filter.

TUNED VERSUS NATIVE, SO THE COMPARISON IS OF ALGORITHMS
-------------------------------------------------------
Our shipped coefficient ROM bakes in `CUT_TRIM * fcr()` (DR 0011), which exists
precisely to cancel the discretisation's tuning error. Comparing a tuned filter
against untuned candidates would compare tuning corrections, not algorithms, so
every candidate is measured on the **untuned** ROM (`make_g_rom(tune=False)`)
by default -- its NATIVE tuning error -- and the shipped filter is additionally
reported as-ships. A tuning polynomial is available to every candidate equally
and at the same (zero) datapath cost, so it is not a discriminator.

COST IS COUNTED, NOT ASSERTED
-----------------------------
Every core counts its own `tanh` evaluations and integer divides in the inner
loop, so the hardware-cost dimension is instrumented rather than declared.
Multiplies and adds are declared per core from the source (`Core.MULS`,
`Core.ADDS`) because routing them through a counter would change the inner
loop; `test_ladder_candidates.py` pins the instrumented counts to the declared
ones so a core that changes shape cannot keep a stale cost.
"""
from __future__ import annotations

import math
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "..", "audition"))

import audio_measure as am                                          # noqa: E402
import fixed                                                        # noqa: E402
import reference_rigs as rr                                         # noqa: E402
import voice_fx as vf                                               # noqa: E402
from dsp import SR                                                  # noqa: E402
from fixed import sat, shl                                          # noqa: E402

OVERSAMPLE = 2
FS_OS = SR * OVERSAMPLE
FS_Q15 = 32768.0

# The rung-4 reference's oversampling. 32x at 48 kHz is 1.536 MHz. Its own
# response error against the closed form is then 0.02 dB or below at every
# cutoff measured here -- a MEASURED number (`reference_floor_db`), not an
# assumption, and it falls by 4x per doubling of this constant, which is how a
# second-order rule is supposed to behave and is the evidence that what is left
# really is discretisation.
REF_OVERSAMPLE = 32


class Refused(RuntimeError):
    """A precondition of the measurement is unmet, so nothing was measured.

    A first-class outcome, distinct from a candidate scoring badly: a tool that
    answers when it cannot is worse than one that is absent."""


def _div(a: int, b: int) -> int:
    """Integer divide truncating TOWARD ZERO, which is what a hardware divider
    does and what Python's `//` does not. The whole point of the Newton and
    delay-free cores is that they buy accuracy with a divider, so the divider
    has to be modelled honestly -- including its sign convention."""
    if b == 0:
        raise Refused("a candidate's solver divided by zero")
    q = abs(a) // abs(b)
    return -q if (a < 0) != (b < 0) else q


# ===========================================================================
# the cores: one ODE, four discretisations, one fixed-point frame
# ===========================================================================
class _Core(fixed.LadderFx):
    """Common frame. Every core inherits `LadderFx`'s formats, its `tanh_fx`
    table read, its `regs()` host conversion and its saturation, so a candidate
    cannot accidentally be compared at a different word length, a different
    table size or a different gain structure. The only thing a subclass changes
    is HOW THE LOOP IS SOLVED.

    `MULS` / `ADDS` are per oversampled sub-step, declared from the source.
    `n_tanh` / `n_div` are counted for real by the inner loop."""

    MULS = 0
    ADDS = 0
    ITERS = 0
    RUNG = 0
    SHIPPABLE = True
    COEF_LAW = "expo"
    DESC = ""

    def __init__(self, *a, stages=4, **kw):
        super().__init__(*a, **kw)
        self.stages = stages
        self.n_tanh = 0
        self.n_div = 0
        self.n_sub = 0                    # oversampled sub-steps executed
        self.n_clamp = 0                  # state saturations -- the overflow probe

    # -- shared scaffolding --------------------------------------------------
    def _prologue(self, x_q15, cutoff_hz, res, drive, g_q16, k, gain, ogain, k_q14):
        n = len(x_q15)
        g_tab, k_tab, gain, ogain = self.coefficients(
            cutoff_hz, res, drive, g_q16=g_q16, n=n, k=k, gain=gain,
            ogain=ogain, k_q14=k_q14)
        per_sample = np.ndim(k_tab) > 0
        out = np.empty(n, dtype=np.int16 if self.OB <= 16 else np.int32)
        return n, g_tab, k_tab, per_sample, gain, ogain, out

    def cost(self) -> dict:
        """Ops per oversampled sub-step, as executed. `tanh` and `divide` are
        counted; `multiply` and `add` are the declared constants."""
        if self.n_sub == 0:
            raise Refused("cost() before anything was rendered")
        return dict(tanh=self.n_tanh / self.n_sub, divide=self.n_div / self.n_sub,
                    multiply=float(self.MULS), add=float(self.ADDS),
                    substeps=self.n_sub, iterations=self.ITERS)


class ShippedCore(_Core):
    """Huovilainen as `fixed.LadderFx` ships it, transcribed so the inner loop
    can be instrumented. `test_ladder_candidates.py` asserts it is BIT-EXACT
    against `fixed.LadderFx` over a driven buffer -- if that ever parts, the
    baseline of this whole comparison is not the filter that ships, which is
    exactly the failure `CLAUDE.md` names ("check that the thing you are
    testing is the thing that ships").

    Multiplies per sub-step: `k*fb`, `xi*gain`, 4 x `g*diff`, and 1/2 of the
    output `*ogain` (once per output sample, i.e. per `os` sub-steps) = 6.5 at
    os=2; declared as 6 with the output counted separately in the report."""

    MULS, ADDS, RUNG = 6, 10, 1
    DESC = "explicit forward Euler, half-sample feedback delay (DAFx-04)"

    def process(self, x_q15, cutoff_hz, res, drive=1.0, *, g_q16=None,
                k=None, gain=None, ogain=None, k_q14=None):
        n, g_tab, k_tab, per_sample, gain, ogain, out = self._prologue(
            x_q15, cutoff_hz, res, drive, g_q16, k, gain, ogain, k_q14)
        os_, SQ, SB, OB, S = self.os, self.SQ, self.SB, self.OB, self.stages
        y, w = self.y, self.w
        d1, d2 = self.d1, self.d2
        TQ = SQ - fixed.SIG_Q
        kk = None if per_sample else int(k_tab)
        nt = nsub = nclamp = 0
        for i in range(n):
            xi = int(x_q15[i])
            g = int(g_tab[i])
            if per_sample:
                kk = int(k_tab[i])
            for _ in range(os_):
                fb = (d1 + d2) >> 1
                u = sat(shl(xi * gain, TQ - fixed.COEF_Q) - ((kk * fb) >> 14), SB)
                w0 = self.tanh_fx(u)
                nt += 1
                for s in range(S):
                    prev = w0 if s == 0 else w[s - 1]
                    inc = (g * shl(prev - w[s], TQ)) >> fixed.COEF_Q
                    raw = y[s] + inc
                    y[s] = sat(raw, SB)
                    if y[s] != raw:
                        nclamp += 1
                    w[s] = self.tanh_fx(y[s])
                    nt += 1
                d2, d1 = d1, y[S - 1]
                nsub += 1
            out[i] = sat((shl(y[S - 1], -TQ) * ogain) >> fixed.COEF_Q, OB)
        self.y, self.w, self.d1, self.d2 = y, w, d1, d2
        self.n_tanh += nt
        self.n_sub += nsub
        self.n_clamp += nclamp
        return out


class NewtonCore(_Core):
    """The implicit, delay-free form of the same ODE on a TRAPEZOIDAL
    integrator, solved with a FIXED number of Newton iterations -- DR 0001's
    own stated reversal condition, made runnable.

    **Trapezoidal, not backward Euler, and the reason is fairness.** Backward
    Euler on `Y' = w (T(P) - T(Y))` places its pole at `1/(1+G)`, so hitting
    the exact pole `e^-w` needs `G = e^w - 1`, which is 16.9 at the 0.45 fs
    cutoff clamp and does not fit the Q0.16 coefficient word at all. The
    trapezoidal rule places it at `(1-G)/(1+G)`, so the exact pole needs
    `G = tanh(w/2)`, which is in `[0, 1)` for every cutoff and fits the SAME
    word, the SAME 129-entry table and the SAME interpolated read. A candidate
    measured on a coefficient law its structure cannot use is a strawman, and
    `COEF_LAW` below is how each core asks for the one it needs.

        Y_new - Y_old = G [ (T(P_new) - T(Y_new)) + (T(P_old) - T(Y_old)) ]

    which rearranges to `Y + G T(Y) = rhs` with everything old folded into
    `rhs`, and one Newton step is

        Y <- Y - (Y + G T(Y) - rhs) / (1 + G T'(Y))

    `T'` costs no table: `d/dY tanh(Y) = 1 - tanh(Y)^2`, one multiply on a
    value the loop already has. The cost that matters is the **divider** --
    four per iteration -- and it is counted, not argued about.

    **Iterating does not make this exact.** For a linear `T` the per-stage
    Newton converges in one step, so what the outer iterations are actually
    doing is a Picard iteration on the FEEDBACK, whose residual falls like
    `(J k)^N`. A fixed `N` is therefore a fixed error that grows with loop gain
    -- high resonance and an open filter, which is DR 0001's objection word for
    word. This comparison exists to put a number on it."""

    RUNG = 2
    COEF_LAW = "tanh-half"
    DESC = "trapezoidal implicit stages, fixed-iteration Newton, delay-free loop"

    def __init__(self, *a, iters=2, **kw):
        super().__init__(*a, **kw)
        self.ITERS = int(iters)
        # Declared from the source, per oversampled sub-step:
        #   1                    xi * gain
        #   4 x 2                the per-stage constant: G*T(Y_old), G*P_old
        #   per iteration: 1     k * Y3
        #   per iteration per stage, 4 of them:
        #       G * P_new (the rhs), G * T(Y), T*T, G * T'          = 4
        #       `f << 16` is a shift, not a multiply
        self.MULS = 1 + 4 * 2 + self.ITERS * (1 + 4 * 4)
        self.ADDS = 1 + 4 * 2 + self.ITERS * (1 + 4 * 6)
        self.pu = 0                      # T(u) from the previous sub-step

    def reset(self):
        super().reset()
        self.pu = 0

    def process(self, x_q15, cutoff_hz, res, drive=1.0, *, g_q16=None,
                k=None, gain=None, ogain=None, k_q14=None):
        n, g_tab, k_tab, per_sample, gain, ogain, out = self._prologue(
            x_q15, cutoff_hz, res, drive, g_q16, k, gain, ogain, k_q14)
        os_, SQ, SB, OB, S = self.os, self.SQ, self.SB, self.OB, self.stages
        y, w = self.y, self.w
        TQ = SQ - fixed.SIG_Q
        kk = None if per_sample else int(k_tab)
        iters = self.ITERS
        pu = self.pu
        nt = nd = nsub = nclamp = 0
        for i in range(n):
            xi = int(x_q15[i])
            g = int(g_tab[i])
            if per_sample:
                kk = int(k_tab[i])
            for _ in range(os_):
                xg = shl(xi * gain, TQ - fixed.COEF_Q)
                y_old, w_old = list(y), list(w)
                # everything the trapezoid rule knows before the new sample:
                #   const_s = Y_old - G T(Y_old) + G P_old
                const = [y_old[s]
                         - ((g * shl(w_old[s], TQ)) >> fixed.COEF_Q)
                         + ((g * shl(pu if s == 0 else w_old[s - 1], TQ))
                            >> fixed.COEF_Q)
                         for s in range(S)]
                yg, tg = list(y), list(w)
                pu_new = pu
                for _it in range(iters):
                    u = sat(xg - ((kk * yg[S - 1]) >> 14), SB)
                    p = self.tanh_fx(u)
                    nt += 1
                    pu_new = p
                    for s in range(S):
                        rhs = const[s] + ((g * shl(p, TQ)) >> fixed.COEF_Q)
                        t = tg[s]
                        f = yg[s] + ((g * shl(t, TQ)) >> fixed.COEF_Q) - rhs
                        tp = 32768 - ((t * t) >> 15)          # T'(Y) in Q1.15
                        d = 65536 + ((g * tp) >> 15)          # 1 + G T' in Q0.16
                        nxt = yg[s] - _div(f << fixed.COEF_Q, d)
                        nd += 1
                        ys = sat(nxt, SB)
                        if ys != nxt:
                            nclamp += 1
                        yg[s] = ys
                        p = self.tanh_fx(ys)
                        nt += 1
                        tg[s] = p
                for s in range(S):
                    y[s], w[s] = yg[s], tg[s]
                pu = pu_new
                nsub += 1
            out[i] = sat((shl(y[S - 1], -TQ) * ogain) >> fixed.COEF_Q, OB)
        self.y, self.w, self.pu = y, w, pu
        self.n_tanh += nt
        self.n_div += nd
        self.n_sub += nsub
        self.n_clamp += nclamp
        return out


class ExplicitDFCore(_Core):
    """The delay-free loop resolved in CLOSED FORM: no iteration, one divide,
    fixed latency -- the property DR 0001 chose Huovilainen for.

    The stage update stays explicit (`Y += g (T(P) - T(Y_old))`, no per-stage
    divider). What changes is the feedback: instead of the half-sample delay
    `(d1 + d2)/2`, the cascade is linearised about the sub-step's starting
    state to get its instantaneous gain

        J = dY3/dU = g^4 T'(U) T'(Y0) T'(Y1) T'(Y2)

    and the loop `U = gain x - k Y3` is then solved exactly on the linearised
    map, one divide:

        Y3 = (Y3_pred + J k Y3_old) / (1 + J k)

    after which the cascade is re-run from the starting state with the
    corrected `U`. Two cascades and one divider per sub-step.

    **This is not a zero-delay-feedback solver**, and issue #46 says so in as
    many words. It is a single Newton step on the loop equation with the
    cascade's Jacobian evaluated at the OLD state, so it is exact only where
    the map is locally linear -- which is everywhere except the place the
    nonlinearity matters. The comparison measures how much that is worth."""

    MULS, ADDS, RUNG = 25, 25, 3
    SHIPPABLE = True
    DESC = "explicit stages, delay-free loop closed in one linearisation"

    def _cascade(self, u, y_old, w_old, g, S, TQ, SB, want_j, k=0):
        """One explicit pass from `y_old`. Returns (y_new, w_new, J_q16,
        n_tanh, n_clamp). `J` is the cascade's small-signal gain dY3/dU in
        Q0.16, computed only when asked for."""
        p = self.tanh_fx(u)
        nt, nclamp = 1, 0
        yn, wn = [0] * S, [0] * S
        j = 65536
        if want_j:
            tpu = 32768 - ((p * p) >> 15)
            j = (((j * g) >> 16) * tpu) >> 15
        for s in range(S):
            inc = (g * shl(p - w_old[s], TQ)) >> fixed.COEF_Q
            raw = y_old[s] + inc
            yn[s] = sat(raw, SB)
            if yn[s] != raw:
                nclamp += 1
            p = self.tanh_fx(yn[s])
            nt += 1
            wn[s] = p
            if want_j and s < S - 1:
                tp = 32768 - ((p * p) >> 15)
                j = (((j * g) >> 16) * tp) >> 15
        return yn, wn, j, nt, nclamp

    def process(self, x_q15, cutoff_hz, res, drive=1.0, *, g_q16=None,
                k=None, gain=None, ogain=None, k_q14=None):
        n, g_tab, k_tab, per_sample, gain, ogain, out = self._prologue(
            x_q15, cutoff_hz, res, drive, g_q16, k, gain, ogain, k_q14)
        os_, SQ, SB, OB, S = self.os, self.SQ, self.SB, self.OB, self.stages
        y, w = self.y, self.w
        TQ = SQ - fixed.SIG_Q
        kk = None if per_sample else int(k_tab)
        nt = nd = nsub = nclamp = 0
        for i in range(n):
            xi = int(x_q15[i])
            g = int(g_tab[i])
            if per_sample:
                kk = int(k_tab[i])
            for _ in range(os_):
                xg = shl(xi * gain, TQ - fixed.COEF_Q)
                y_old, w_old = list(y), list(w)
                u0 = sat(xg - ((kk * y_old[S - 1]) >> 14), SB)
                yn, wn, j, t1, c1 = self._cascade(u0, y_old, w_old, g, S, TQ, SB, True)
                jk = (j * kk) >> 14                          # J k in Q0.16
                y3 = _div(yn[S - 1] * 65536 + jk * y_old[S - 1], 65536 + jk)
                nd += 1
                u = sat(xg - ((kk * sat(y3, SB)) >> 14), SB)
                yn, wn, _j, t2, c2 = self._cascade(u, y_old, w_old, g, S, TQ, SB, False)
                for s in range(S):
                    y[s], w[s] = yn[s], wn[s]
                nt += t1 + t2
                nclamp += c1 + c2
                nsub += 1
            out[i] = sat((shl(y[S - 1], -TQ) * ogain) >> fixed.COEF_Q, OB)
        self.y, self.w = y, w
        self.n_tanh += nt
        self.n_div += nd
        self.n_sub += nsub
        self.n_clamp += nclamp
        return out


# ===========================================================================
# rung 4: the offline reference, in float, against an answer we did not invent
# ===========================================================================
def reference_ode(x_f: np.ndarray, cut_hz: float, res: float, drive: float = 1.0,
                  oversample: int = REF_OVERSAMPLE, tol: float = 1e-12,
                  max_iter: int = 40) -> np.ndarray:
    """Rung 4. The ladder ODE integrated to convergence: float, `oversample` x
    48 kHz, trapezoidal rule, Newton until the update is below `tol`.

    NOT A SHIPPING CANDIDATE. It has an unbounded iteration count by
    construction, which is the exact property DR 0001 rejects; issue #46 says
    rung 4 is an offline reference and shipping it would have to earn its cost.
    Its job here is to be the thing the shippable candidates are scored
    against, and its own credibility comes from `assert_apparatus`, which
    checks it against the analytic self-oscillation condition (k = 4 at exactly
    the cutoff) rather than against anything we wrote.

    Scaling mirrors `LadderFx.regs` exactly -- input `drive * vpu / 2Vt` into
    state units, output `2Vt / vpu * (1 + 2 res)` back out -- so its output is
    directly comparable with a fixed-point candidate's, sample for sample."""
    cfg = vf.LADDER_CFG
    vpu = fixed.LadderFx(**cfg).vpu
    fs = SR * oversample
    wn = 2.0 * math.pi * float(cut_hz) / fs
    # Trapezoidal, second order, with the coefficient that places the linear
    # pole exactly at exp(-w). Backward Euler was tried first and is FIRST
    # order: at 32x it missed the analytic response by 0.69 dB at the resonant
    # peak, halving per doubling, so 32x could not be ground truth for anything.
    # See "Wrong before it was right" in docs/ladder-rungs-2-4.md.
    g = math.tanh(0.5 * wn)
    k = 4.0 * float(res)
    gi = drive * vpu / fixed.VT2
    go = fixed.VT2 / vpu * (1.0 + 2.0 * float(res))
    # Plain Python floats, not a numpy array: this is a scalar recursion with
    # `oversample` sub-steps per sample, so numpy indexing dominates the cost.
    xs = [float(v) * gi for v in np.asarray(x_f, dtype=np.float64)]
    y = [0.0, 0.0, 0.0, 0.0]
    pu = 0.0                                 # T(u) at the previous sub-step
    out = np.empty(len(xs))
    th = math.tanh
    for i, xi in enumerate(xs):
        for _s in range(oversample):
            old = list(y)
            t_old = [th(v) for v in old]
            # trapezoid: everything known before the new sample
            const = [old[s] - g * t_old[s] + g * (pu if s == 0 else t_old[s - 1])
                     for s in range(4)]
            for _ in range(max_iter):
                p = th(xi - k * y[3])
                pu_new = p
                step = 0.0
                for s in range(4):
                    rhs = const[s] + g * p
                    cur = y[s]
                    t = th(cur)
                    nxt = cur - (cur + g * t - rhs) / (1.0 + g * (1.0 - t * t))
                    d = abs(nxt - cur)
                    if d > step:
                        step = d
                    y[s] = nxt
                    p = th(nxt)
                if step < tol:
                    break
            pu = pu_new
        out[i] = y[3] * go
    return out


# The stepped-tone analysis window. `audio_measure.tone_amplitude` is a
# RECTANGULAR coherent projection, exact only when the record holds a whole
# number of periods -- so the probe frequencies are snapped onto this window's
# own DFT grid rather than the window being fitted to the frequency. Measured
# consequence of not doing that: a 1.14 dB "discretisation error" at a 6.4 kHz
# cutoff that did not shrink when the reference's oversampling was quadrupled,
# because it was spectral leakage and not discretisation at all.
TONE_N = 16384
TONE_SETTLE = 8192


def snap_freqs(freqs, n: int = TONE_N) -> np.ndarray:
    """Move each probe frequency onto the analysis window's DFT grid."""
    k = np.maximum(np.round(np.asarray(freqs, dtype=np.float64) * n / SR), 1.0)
    return k * SR / n


def stepped_tone_db(render, freqs, amp: float, *, n: int = TONE_N,
                    settle: int = TONE_SETTLE, label: str = "probe") -> np.ndarray:
    """Gain in dB at each frequency, by the stepped-sine method every other
    transfer measurement in this repository uses -- one tone at a time, at a
    stated level, because the ladder's response depends on level by design and
    an impulse response cannot state one.

    `render(x)` takes float +-1.0 and returns float +-1.0. `freqs` must already
    be on the window's DFT grid (`snap_freqs`)."""
    t = np.arange(settle + n) / SR
    out = []
    for f in np.asarray(freqs, dtype=np.float64):
        y = render(amp * np.sin(2.0 * math.pi * f * t))
        a = am.tone_amplitude(y[settle:], f, SR).require(f"{label} {f:.1f} Hz")
        out.append(20.0 * math.log10(max(a, 1e-30) / amp))
    return np.array(out)


def reference_response_db(cut_hz: float, res: float, freqs, amp: float = 1e-4,
                          oversample: int = REF_OVERSAMPLE, *, n: int = TONE_N,
                          settle: int = TONE_SETTLE) -> np.ndarray:
    """The rung-4 reference's SMALL-SIGNAL gain, in dB, at each frequency.

    `amp` is small enough that `tanh(x) = x` to a part in 10^8, so the answer
    is the linear transfer function and can be checked against a closed form
    that owes nothing to this repository (`analytic_response_db`)."""
    return stepped_tone_db(
        lambda x: reference_ode(x, cut_hz, res, oversample=oversample),
        freqs, amp, n=n, settle=settle, label=f"reference {cut_hz:.0f} Hz")


PEAK_SPAN = (0.84, 1.19)         # the window, RELATIVE TO THE ANALYTIC PEAK
PEAK_POINTS = 13
PROBE_AMP = 0.003                # MEASURED: see `probe_amplitude_sweep`


def peak_of(freqs, gains_db) -> tuple:
    """Peak frequency and height from a sampled magnitude response, by a
    parabola through the three points around the maximum in LOG frequency --
    which is exact for a parabola and unbiased for anything peak-like sampled
    finely enough. Returns `(f_peak_hz, gain_db)`.

    Separating the peak's FREQUENCY from its HEIGHT is the whole point. A
    resonant peak sampled at a fixed grid reads a frequency error as a level
    deficit: the shipped filter's untuned ROM looked 3.4 dB short at the peak
    until the peak was located rather than assumed, and the deficit was its
    tuning error wearing a different hat."""
    f = np.log(np.asarray(freqs, dtype=np.float64))
    y = np.asarray(gains_db, dtype=np.float64)
    i = int(np.argmax(y))
    if i == 0 or i == len(y) - 1:
        raise Refused(f"the peak is at the edge of the search window "
                      f"({math.exp(f[i]):.0f} Hz); it is not bracketed")
    d = y[i - 1] - 2.0 * y[i] + y[i + 1]
    if d >= 0.0:
        raise Refused("the sampled response has no interior maximum")
    t = 0.5 * (y[i - 1] - y[i + 1]) / d
    step = f[i + 1] - f[i]
    return float(math.exp(f[i] + t * step)), float(y[i] - 0.25 * (y[i - 1] - y[i + 1]) * t)


def stage_max_lag_deg(law: str, cut_hz: float) -> float:
    """The greatest phase lag ONE stage of a given integrator can produce, in
    degrees, at a given cutoff. Closed form, no filter run:

        explicit (`expo`)      H = g / (1 - a z^-1),  a = 1 - g
                               the lag is maximised at `cos w = a` and equals
                               **arcsin(a)** -- which is below 45 degrees once
                               `g > 0.293`
        trapezoidal            H = 1 / (1 + j tan(w/2) / G)
                               the lag rises to 90 degrees as w -> pi, for any G

    This is why the delay-free explicit ladder stops self-oscillating: four
    stages of a lag below 45 degrees never reach the -180 the loop needs, and
    no amount of feedback changes a phase. It is arithmetic, not an artefact of
    our implementation, and `delay_free_oscillation_limit_hz` turns it into a
    frequency anyone can check by hand."""
    w = 2.0 * math.pi * float(cut_hz) / FS_OS
    if law == "expo":
        return math.degrees(math.asin(min(max(1.0 - (1.0 - math.exp(-w)), 0.0), 1.0)))
    if law == "tanh-half":
        return 90.0
    raise Refused(f"no such coefficient law {law!r}")


def delay_free_oscillation_limit_hz(law: str = "expo", stages: int = 4) -> float:
    """The cutoff above which a DELAY-FREE ladder on this integrator cannot
    self-oscillate at any feedback, in closed form.

    `stages * arcsin(1 - g) >= 180` needs `1 - g >= sin(180/stages)`, and
    `g = 1 - exp(-w)` gives

        f <= -fs_os / (2 pi) * ln( sin(180 / stages) )

    which is **5295.6 Hz** for four stages at 96 kHz. Above it the loop's phase
    never reaches -180 degrees, so the filter cannot sing however hard it is
    driven. `inf` for the trapezoidal law, whose stage lag reaches 90 degrees.
    """
    if law == "tanh-half":
        return float("inf")
    s = math.sin(math.pi / stages)
    return -FS_OS / (2.0 * math.pi) * math.log(s)


def analytic_peak(cut_hz: float, res: float) -> tuple:
    """Where the closed form's own resonant peak is, and how tall. The search
    window is centred HERE rather than on the cutoff, because at moderate
    resonance the ladder's peak sits well below its cutoff -- at res = 0.5 it
    is at 0.80 x, which fell outside a window centred on the cutoff and made
    every res = 0.5 point refuse."""
    dense = np.geomspace(0.35, 1.35, 20001) * float(cut_hz)
    a = analytic_response_db(cut_hz, res, dense)
    j = int(np.argmax(a))
    if j in (0, len(a) - 1):
        raise Refused(f"the closed form has no interior peak at {cut_hz:.0f} Hz, "
                      f"res {res}")
    return float(dense[j]), float(a[j])


def peak_probe(render, cut_hz: float, res: float, amp: float = PROBE_AMP,
               label: str = "probe", *, n: int = TONE_N,
               settle: int = TONE_SETTLE) -> dict:
    """Where the resonant peak actually is, and how tall, measured by a stepped
    tone on a log grid around the cutoff -- plus the passband gain a decade
    below it, which is the bass question at the same operating point.

    Returned against the closed form, so both numbers are errors against an
    answer that owes nothing to this repository:
        `cents`      peak frequency against the analytic peak frequency
        `peak_db`    peak height against the analytic peak height
        `pass_db`    gain at 0.1 x cutoff against the analytic gain there
    """
    f_a, g_a = analytic_peak(cut_hz, res)
    grid = np.unique(snap_freqs(f_a * np.geomspace(PEAK_SPAN[0], PEAK_SPAN[1],
                                                   PEAK_POINTS), n))
    lo = snap_freqs([max(0.1 * cut_hz, 2.0 * SR / n)], n)
    meas = stepped_tone_db(render, np.concatenate([lo, grid]), amp, n=n,
                           settle=settle, label=label)
    f_m, g_m = peak_of(grid, meas[1:])
    return dict(f_peak_hz=f_m, f_analytic_hz=f_a,
                cents=1200.0 * math.log2(f_m / f_a),
                peak_db=g_m - g_a,
                pass_db=float(meas[0] - analytic_response_db(cut_hz, res, lo)[0]),
                pass_hz=float(lo[0]))


def probe_amplitude_sweep(render, cut_hz: float, res: float,
                          amps=(0.05, 0.02, 0.005, 0.002, 0.001, 0.0005)) -> dict:
    """The small-signal probe level, SWEPT rather than chosen. Too loud and the
    `tanh` compresses the resonant peak; too quiet and the Q15 quantisation
    floor eats it. Both ends were measured before `PROBE_AMP` was set, because
    an amplitude assumed is a precondition assumed."""
    return {a: peak_probe(render, cut_hz, res, amp=a) for a in amps}


def analytic_response_db(cut_hz: float, res: float, freqs, *, zoh: bool = True) -> np.ndarray:
    """The ladder's small-signal response from the TRANSFER FUNCTION, not from
    any model in this repository:

        H(jw) = Z(w) * gi * go * A^4 / (1 + k A^4),  A = 1/(1 + j w/wc), k = 4 res

    where `gi`/`go` are `LadderFx.regs`'s input and output scalings and `Z` is
    the **zero-order hold** of the 48 kHz input, `sin(pi f T)/(pi f T)` with
    `T = 1/48000`. The only things borrowed from us are the gain structure and
    the input rate, both arithmetic; the filter itself is the textbook ladder.

    **The hold term is not a detail and leaving it out is an apparatus bug we
    made.** Every model here -- the rung-4 reference at 16x and every candidate
    at 2x -- holds each input sample for exactly `T` seconds, so each one's
    input really is a staircase and really does lose `sin(x)/x`. Omitting it
    read as a 1.02 dB "discretisation error" at a 6.4 kHz cutoff that did not
    shrink when the reference's oversampling was doubled -- because it was
    never discretisation. `zoh=False` is the bare filter, for a caller that
    has already removed the hold from both sides."""
    vpu = fixed.LadderFx(**vf.LADDER_CFG).vpu
    gi = vpu / fixed.VT2
    go = fixed.VT2 / vpu * (1.0 + 2.0 * float(res))
    f = np.asarray(freqs, dtype=np.float64)
    a = 1.0 / (1.0 + 1j * f / float(cut_hz))
    h = gi * go * a ** 4 / (1.0 + 4.0 * float(res) * a ** 4)
    if zoh:
        x = math.pi * f / SR
        h = h * np.where(x == 0.0, 1.0, np.sin(x) / np.where(x == 0.0, 1.0, x))
    return 20.0 * np.log10(np.abs(h))


REF_PROBE_FREQS = (0.25, 0.5, 0.8, 1.0, 1.25, 2.0)      # multiples of cutoff


def reference_floor_db(cuts=(200.0, 1000.0, 6400.0), res: float = 0.9,
                       oversample: int = REF_OVERSAMPLE, *, n: int = TONE_N,
                       settle: int = TONE_SETTLE) -> dict:
    """What the rung-4 reference's OWN discretisation costs, in dB of response
    error against `analytic_response_db`, at each cutoff. This is the floor
    below which no candidate's response error can be believed, and it is
    measured rather than assumed."""
    out = {}
    for c in cuts:
        fr = snap_freqs(np.array(REF_PROBE_FREQS) * c, n)
        err = reference_response_db(c, res, fr, oversample=oversample, n=n,
                                    settle=settle) \
            - analytic_response_db(c, res, fr)
        out[c] = float(np.abs(err).max())
    return out


# ===========================================================================
# the device: identical stimulus, identical control path, any core
# ===========================================================================
def _kick(cut: float, seconds: float, amp: float = 0.09):
    """The free-ring stimulus `reference_rigs.kick_then_silence` uses, in
    FLOAT units (+-1.0), so the rung-4 reference and the fixed-point cores are
    excited by the same waveform at the same level."""
    return rr.kick_then_silence(cut, seconds, amp)


def _cents_of_ring(y, cut: float) -> float:
    """`f_osc / commanded` as cents, by `reference_compare.py`'s own rule: the
    zero-crossing frequency when it agrees with the spectral peak inside 2 %,
    the peak otherwise. REFUSES rather than returning a number it cannot
    support."""
    e = am.dominant_frequency(y, 20.0, 20000.0, SR)
    z = am.zero_crossing_frequency(y, SR)
    if z.ok and e.ok and abs(z.value - e.value) / e.value < 0.02:
        f = z.value
    elif e.ok:
        f = e.value
    else:
        raise Refused(f"the ring at {cut:.0f} Hz has no measurable frequency")
    return 1200.0 * math.log2(f / float(cut))


class NullCore(_Core):
    """A core with the right ports and no behaviour: it returns silence.

    `docs/verification-rules.md` rule 1 -- START RED. Every stage of the
    comparison is run against this before any candidate is believed, and every
    stage must either REFUSE or score it worse than every real candidate. A
    stage that produces a plausible number for silence is not a measurement,
    and four harnesses in this repository have shipped in exactly that state."""

    MULS, ADDS, RUNG = 0, 0, 0
    SHIPPABLE = False
    DESC = "silence: the start-red stub"

    def process(self, x_q15, cutoff_hz, res, drive=1.0, *, g_q16=None,
                k=None, gain=None, ogain=None, k_q14=None):
        n, _g, _k, _ps, _gain, _og, out = self._prologue(
            x_q15, cutoff_hz, res, drive, g_q16, k, gain, ogain, k_q14)
        out[:] = 0
        self.n_sub += n * self.os
        return out


CORES = {
    "null": (NullCore, {}),
    "shipped": (ShippedCore, {}),
    "zdf-newton-2": (NewtonCore, dict(iters=2)),
    "zdf-newton-3": (NewtonCore, dict(iters=3)),
    "zdf-explicit": (ExplicitDFCore, {}),
}
SHIPPABLE = tuple(n for n, (c, _) in CORES.items() if c.SHIPPABLE)


# ===========================================================================
# the coefficient table: one shape, one width, one read, two laws
# ===========================================================================
def make_coef_rom(law: str = "expo", bits: int = None, oversample: int = OVERSAMPLE,
                  tune: bool = False) -> np.ndarray:
    """`2^bits + 1` Q0.16 words EDGE-sampled at a commanded cutoff of
    `i * (32768 >> bits)` Hz -- the same shape, the same width, the same
    interpolated `voice_fx.g_from_cut` read and the same guard entry as the
    shipped `voice_fx.make_g_rom`, built for whichever integrator asks for it:

        expo        `g = 1 - exp(-w)`   the exact pole of an EXPLICIT one-pole
        tanh-half   `G = tanh(w / 2)`   the exact pole of a TRAPEZOIDAL one

    Issue #46 requires identical fixed-point constraints between candidates.
    That is a constraint on the WORD and the TABLE, which is what silicon pays
    for, not on the arithmetic identity used to fill it -- filling the table
    with a law the candidate's integrator cannot use would measure a
    mis-tuning we chose, not the algorithm.

    `tune=True` additionally applies DR 0011's `CUT_TRIM * fcr()`, which only
    the shipped law has a fitted constant for; every candidate is compared
    UNTUNED, on its native tuning."""
    bits = vf.GROM_BITS if bits is None else bits
    fs = SR * oversample
    step = (1 << 15) >> bits
    f = np.arange((1 << bits) + 1) * step
    ft = f * vf.CUT_TRIM * vf.fcr(f) if tune else f.astype(np.float64)
    w = 2.0 * math.pi * ft / fs
    if law == "expo":
        v = 1.0 - np.exp(-w)
    elif law == "tanh-half":
        v = np.tanh(0.5 * w)
    else:
        raise Refused(f"no such coefficient law {law!r}")
    return np.clip(np.round(v * 65536.0), 0, 65535).astype(np.int64)


class Candidate:
    """One candidate at the host's own operating point, driven exactly as
    `reference_rigs.OurLadder` drives the shipped filter: the same coefficient
    ROM read, the same DR 0006 resonance compensation, the same stimuli.

    The knobs below are the INJECTED CONTROLS, and they are core-agnostic on
    purpose -- a control that only the baseline can carry proves nothing about
    a candidate:

        cut_skew      multiply the cutoff used for the ROM lookup without
                      changing the cutoff we claim to have commanded
        compensated   False drops DR 0006's per-frame k compensation
        stages        3 is the dropped-pole defect
        tanh_entries  via `cfg`; 4 is a deliberately crude nonlinearity
        tuned         the shipped ROM (True, as-ships) or the untuned one
                      (False, the default -- every candidate's NATIVE tuning)
    """

    def __init__(self, name: str, *, tuned: bool = False, stages: int = 4,
                 compensated: bool = False, cut_skew: float = 1.0,
                 drive: float = 1.0, cfg: dict = None):
        if name not in CORES:
            raise Refused(f"no such candidate {name!r}; have {sorted(CORES)}")
        self.name = name
        self.cls, self.kw = CORES[name]
        self.tuned, self.stages = bool(tuned), int(stages)
        self.compensated, self.cut_skew, self.drive = compensated, cut_skew, drive
        self.cfg = dict(vf.LADDER_CFG, **(cfg or {}))
        self.law = self.cls.COEF_LAW
        # PRECONDITION. DR 0006's compensation ROM is bisected on the SHIPPED
        # structure's linearised loop (four explicit one-poles and a
        # half-sample feedback delay, `voice_fx.k_onset`). Applying it to a
        # candidate with a different loop is a correct instrument in a wrong
        # state -- it would hide exactly the property the candidate is being
        # judged on, which is how much compensation its own structure needs.
        if compensated and (self.law != "expo" or self.cls is not ShippedCore):
            raise Refused(
                f"compensated=True is only meaningful for the shipped structure: "
                f"DR 0006's k ROM is derived from voice_fx.k_onset's loop, and "
                f"{name!r} is not that loop. Compare candidates uncompensated.")
        if tuned and self.law != "expo":
            raise Refused(
                f"tuned=True is DR 0011's CUT_TRIM * fcr(), fitted to the shipped "
                f"expo law at res = 1.05; {name!r} runs the {self.law!r} law and has "
                "no fitted constant. Compare candidates untuned.")
        self.g_rom = _g_rom(True) if (tuned and self.law == "expo") \
            else _rom(self.law, False)
        self.k_rom = _k_rom(self.tuned)
        self.last_cost = None
        self.last_clamp = None

    # -- the control path, identical for every core -------------------------
    def _regs(self, res, cut, drive=None):
        drive = self.drive if drive is None else drive
        k, gain, ogain = fixed.LadderFx(**self.cfg).regs(res, drive)
        g = int(vf.g_from_cut(np.array([cut * self.cut_skew]), self.g_rom)[0])
        if self.compensated:
            kc = int(vf.kc_from_cut(np.array([cut * self.cut_skew]), self.k_rom)[0])
            k = int(vf.k_effective(k, kc))
        return g, k, gain, ogain

    def _core(self):
        return self.cls(**self.cfg, stages=self.stages, **self.kw)

    def render(self, x_q15, cut, res, drive=None):
        """`x_q15` is the stimulus in Q15 units (+-32768) exactly as
        `reference_rigs.OurLadder._render` takes it -- NOT in +-1.0 -- so the
        rounding into `int16` is the identical float expression and the
        bit-exactness assertion in `assert_apparatus` is meaningful. The return
        is divided by 32768, i.e. +-1.0."""
        g, k, gain, ogain = self._regs(res, cut, drive)
        core = self._core()
        xq = np.clip(np.round(np.asarray(x_q15)), -32768, 32767).astype(np.int16)
        y = core.process(xq, None, res, self.drive if drive is None else drive,
                         g_q16=np.full(len(xq), g, dtype=np.int64),
                         k=k, gain=gain, ogain=ogain)
        self.last_cost = core.cost()
        self.last_clamp = core.n_clamp / max(core.n_sub, 1)
        return np.asarray(y, dtype=np.float64) / FS_Q15

    def render_modulated(self, x_q15, cut_hz_per_sample, res, *, quantum: int = 1,
                         smooth: bool = False):
        """The same filter with the cutoff MOVING every sample -- the condition
        issue #46 says a 'cheap' algorithm hides its real cost in, and the only
        condition under which the coefficient-update path costs anything.

        `quantum` rounds the commanded cutoff to that many Hz (the injected
        control for the movement axis); `smooth=True` bypasses the integer
        control path entirely and runs the coefficient in float, which is the
        differential reference `model/reference_movement.py` established."""
        n = len(x_q15)
        cut_f = np.asarray(cut_hz_per_sample, dtype=np.float64)
        mid = float(np.exp(np.log(np.clip(cut_f, 1e-6, None)).mean()))
        _, _, gain, ogain = self._regs(res, mid)
        k0 = fixed.LadderFx(**self.cfg).regs(res, self.drive)[0]
        ci = np.clip(np.round(cut_f / quantum) * quantum, vf.CUT_MIN,
                     vf.CUT_MAX).astype(np.int64)
        if smooth:
            # the candidate's OWN coefficient law, in float and at the exact
            # commanded cutoff -- the differential reference, not the shipped
            # law applied to a candidate that does not use it
            f_law = (fixed.tuned_cutoff(np.clip(cut_f, vf.CUT_MIN, vf.CUT_MAX))
                     if self.tuned else np.clip(cut_f, vf.CUT_MIN, vf.CUT_MAX))
            w = 2.0 * math.pi * f_law / FS_OS
            v = np.tanh(0.5 * w) if self.law == "tanh-half" else 1.0 - np.exp(-w)
            g = np.clip(np.round(v * 65536.0), 1, 65535).astype(np.int64)
        else:
            g = vf.g_from_cut(ci, self.g_rom)
        if self.compensated:
            k_tab = vf.k_effective(np.full(n, k0), vf.kc_from_cut(ci, self.k_rom))
        else:
            k_tab = np.full(n, k0, dtype=np.int64)
        core = self._core()
        xq = np.clip(np.round(np.asarray(x_q15)), -32768, 32767).astype(np.int16)
        y = core.process(xq, None, res, self.drive, g_q16=g, k=None, gain=gain,
                         ogain=ogain, k_q14=k_tab)
        self.last_cost = core.cost()
        self.last_clamp = core.n_clamp / max(core.n_sub, 1)
        return np.asarray(y, dtype=np.float64) / FS_Q15

    # -- the probes, byte for byte the stimuli reference_rigs.OurLadder uses --
    def ring(self, cut, res, seconds: float = 0.8, amp: float = 0.09):
        y = self.render(rr.kick_then_silence(cut, seconds, amp * FS_Q15), cut, res)
        return y[int(0.4 * len(y)):]

    def tone_gain_db(self, freqs, cut, res, amp):
        x, parts = rr.tone_train(freqs, amp * FS_Q15, 0.06, 0.20)
        y = self.render(x, cut, res)
        out = []
        for i0, nw, f in parts:
            a = am.tone_amplitude(y[i0:i0 + nw], f).require(f"{self.name} probe {f:.0f} Hz")
            out.append(20 * math.log10(max(a, 1e-12) / amp))
        return np.array(out)

    def drive_tone(self, f, cut, res, amp, seconds: float = 0.4):
        n = int(seconds * SR)
        x = amp * FS_Q15 * np.sin(2 * math.pi * f * np.arange(n) / SR)
        return self.render(x, cut, res)[int(0.15 * SR):]

    def chord_tone(self, f0, cut, res, amp, ratios=(1.0, 1.2599, 1.4983),
                   seconds: float = 0.4):
        """Several simultaneous tones through one filter -- issue #46's "test
        it with several simultaneous tones NOW", because paraphonic headroom is
        cheap to establish today and expensive to retrofit. A major triad, each
        note at `amp`, so the peak input is 3x a single note's and the question
        is whether the character survives it."""
        n = int(seconds * SR)
        t = np.arange(n) / SR
        x = sum(amp * FS_Q15 * np.sin(2 * math.pi * f0 * r * t) for r in ratios)
        return self.render(x, cut, res)[int(0.15 * SR):]


_ROM_CACHE: dict[tuple, np.ndarray] = {}
_KROM_CACHE: dict[bool, np.ndarray] = {}


def _rom(law: str, tuned: bool) -> np.ndarray:
    if (law, tuned) not in _ROM_CACHE:
        _ROM_CACHE[(law, tuned)] = make_coef_rom(law, tune=tuned)
    return _ROM_CACHE[(law, tuned)]


def _g_rom(tuned: bool) -> np.ndarray:
    """The shipped `expo` ROM. `voice_fx.make_g_rom` for `tuned=True`, so the
    as-ships baseline reads the exact bytes the hardware does."""
    return vf.make_g_rom(tune=tuned) if tuned else _rom("expo", False)


def _k_rom(tuned: bool) -> np.ndarray:
    """DR 0006's compensation ROM built against whichever cutoff ROM is in use.
    The compensation is DERIVED from the cutoff coefficients, so substituting
    one and not the other would measure two defects at once."""
    if tuned not in _KROM_CACHE:
        _KROM_CACHE[tuned] = (vf.make_k_rom() if tuned
                              else rr._k_rom_for(_g_rom(False)))
    return _KROM_CACHE[tuned]


# ===========================================================================
# preconditions, asserted at the point of use
# ===========================================================================
ANALYTIC_ONSET_RES = 1.0          # k = 4 -- from the transfer function, not from us
ANALYTIC_TUNING_CENTS = 0.0       # f_osc = cutoff exactly, at every cutoff
REFERENCE_FLOOR_DB = 0.05         # MEASURED: 32x costs 0.02 dB at res = 0.9


def assert_apparatus(quick: bool = True) -> dict:
    """Every precondition the comparison rests on, raising `Refused` rather
    than returning a score.

    1. the `tanh` table is the shipped 16 entries (DR 0006's `k_comp` ROM is
       derived from its bin-0 slope, and issue #46 puts the table out of scope)
    2. `ShippedCore` is BIT-EXACT against `fixed.LadderFx` -- the baseline of
       this comparison is the filter that ships, not a lookalike
    3. the same core, driven through `Candidate`, is bit-exact against
       `reference_rigs.OurLadder` -- the stimulus and control path are the ones
       every other measurement in this repository used
    4. the rung-4 reference reproduces the ANALYTIC small-signal response
       `H = gi go A^4 / (1 + k A^4)`, which is known from the transfer function
       and not from anything in this repository
    5. `make_coef_rom('expo')` reproduces `voice_fx.make_g_rom(tune=False)`
       byte for byte, so the candidates' table really is the shipped table's
       shape, width and read

    Returns the reference's measured response floor, because no candidate's
    response error can be believed below it."""
    if vf.LADDER_CFG["tanh_entries"] != 16:
        raise Refused(f"tanh table is {vf.LADDER_CFG['tanh_entries']} entries, not the "
                      "shipped 16; DR 0006's k_comp ROM is derived from its bin-0 slope")
    # (2) bit-exactness against the shipping model
    rng = np.random.default_rng(7)
    xq = (rng.integers(-20000, 20000, 2000)).astype(np.int16)
    g = int(vf.g_from_cut(np.array([1200]), _g_rom(True))[0])
    kc = int(vf.kc_from_cut(np.array([1200]), _k_rom(True))[0])
    ref = fixed.LadderFx(**vf.LADDER_CFG)
    k0 = ref.regs(1.4, 1.0)[0]
    k = int(vf.k_effective(k0, kc))
    args = dict(g_q16=np.full(len(xq), g, dtype=np.int64), k=k,
                gain=ref.regs(1.4, 1.0)[1], ogain=ref.regs(1.4, 1.0)[2])
    a = fixed.LadderFx(**vf.LADDER_CFG).process(xq, None, 1.4, 1.0, **args)
    b = ShippedCore(**vf.LADDER_CFG).process(xq, None, 1.4, 1.0, **args)
    if not np.array_equal(np.asarray(a), np.asarray(b)):
        n = int((np.asarray(a) != np.asarray(b)).sum())
        raise Refused(f"ShippedCore is not bit-exact against fixed.LadderFx "
                      f"({n} of {len(xq)} samples differ): the baseline of this "
                      "comparison is not the filter that ships")
    # (3) the device path is reference_rigs.OurLadder's
    ours = rr.OurLadder("ours").ring(800.0, 1.4, seconds=0.25)
    mine = Candidate("shipped", tuned=True, compensated=True).ring(800.0, 1.4, seconds=0.25)
    if len(ours) != len(mine) or not np.allclose(ours, mine, atol=1e-12):
        raise Refused("Candidate('shipped') does not reproduce reference_rigs.OurLadder: "
                      "the harness is not driving the shipped control path")
    # (5) the candidates' table is the shipped table
    if not np.array_equal(make_coef_rom("expo", tune=False), vf.make_g_rom(tune=False)):
        raise Refused("make_coef_rom('expo') is not voice_fx.make_g_rom(tune=False): "
                      "the candidates are not reading the shipped table's law")
    # (4) the rung-4 reference against the analytic answer
    floor = reference_floor_db((1000.0,) if quick else (200.0, 1000.0, 6400.0))
    worst = max(floor.values())
    if worst > REFERENCE_FLOOR_DB:
        raise Refused(f"the rung-4 reference misses the analytic small-signal response "
                      f"by {worst:.2f} dB at {REF_OVERSAMPLE}x, over the "
                      f"{REFERENCE_FLOOR_DB} dB this harness allows; it cannot be "
                      "ground truth for anything")
    return dict(reference_floor_db={k_: round(v, 3) for k_, v in floor.items()},
                worst_db=round(worst, 3), oversample=REF_OVERSAMPLE)
