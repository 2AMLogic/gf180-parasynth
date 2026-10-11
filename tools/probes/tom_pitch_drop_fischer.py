"""#592: the toms' diode pitch drop -- magnitude and relaxation -- against the
Fischer takes, with a phase-aware estimator that cannot read onset phase as a
drop.

WHY A NEW ESTIMATOR. The #379 gate's pitch track reads +37..+39 cents at its
first frame for a band-pass resonator whose pitch never moves
(`tools/probes/test_tom_conga_gate.py`). So the 808 LT's "74 cents" against
our "35" is not a drop measurement. This file measures the drop as a phase
CURVATURE: with f(t) = f_inf (1 + E exp(-t / tau)) the phase is

    theta(t) = psi + 2 pi f_inf t - 2 pi f_inf E tau exp(-t / tau)

where psi = phi0 + 2 pi f_inf E tau. The onset phase phi0 is a FREE
parameter (inside psi), so a constant-pitch tone of any onset phase has no
curvature and reads E = 0. Late in the ring only psi is visible, which is
exactly the degeneracy between "a phase offset" and "an integrated drop"; the
drop is identified ONLY by the curvature the early window shows, and when the
window cannot show it the answer is REFUSED / NO-DROP, never a number.

TWO INDEPENDENT ROUTES, which must agree or the condition is REFUSED:

  A  waveform: an FFT-domain raised-cosine band-pass, then non-linear least
     squares of A exp(-t/tau_a) cos(theta(t)) on the samples (scipy
     least_squares, multi-start);
  B  crossings: a Butterworth band-pass (sosfiltfilt), interpolated zero
     crossings, and the half-cycle count n/2 = a + f_inf t - c exp(-t/tau)
     fitted LINEARLY in (a, f_inf, c) on a grid of tau, refined in 1-D.

They share only the onset rule and the window rule (which are frozen
constants below), not a filter, a residual or an optimiser.

Commands
  knownanswer  analytic decaying tones (closed-form phase, NO DrumsFx):
               every f/phase/decay/drop cell against the frozen bounds.
  controls     the stub and the injected defects, MOVED/BLIND per property.
  measure      the Fischer takes of one pre-registered split, and the shipped
               engine (and the production host's staircase) at each take's
               tuning; REFUSES rather than answers on any failed precondition.
  fit          development rows only -> the frozen discrepancy record.
  confirm      the reserved rows, ONCE: refuses if the record already exists.

Preconditions asserted at the point of use: frozen full sha256 per take,
44.1/48 kHz, finite, non-silent; our unity render bit-exact against
`run_case.render_drum_solo`; the production host's tom-bend writes equal the
reference `hit_writes` staircase the render plays.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import pathlib
import statistics
import subprocess
import sys

import numpy as np
from scipy.optimize import least_squares, minimize_scalar
from scipy.signal import butter, hilbert, sosfiltfilt

ROOT = pathlib.Path(__file__).resolve().parents[2]
for _p in (ROOT / "tools", ROOT / "model", ROOT / "fpga", ROOT / "tools" / "probes"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

PREREG = ROOT / "docs" / "scorecard" / "tom-pitch-drop-592" / "prereg.json"
TOMS = ("LT", "MT", "HT")


class Refused(RuntimeError):
    """A first-class outcome: the tool declines to produce a number."""

    def __init__(self, reason: str, detail: str = ""):
        super().__init__(f"{reason}: {detail}" if detail else reason)
        self.reason = reason


# ---------------------------------------------------------------------------
# FROZEN estimator constants (#592 preregistration names them; changing any of
# them after the corpus is read invalidates the record)
# ---------------------------------------------------------------------------
ONSET_FRAC = 0.10        # t = 0 at the first sample with |x| >= this x max|x|
COARSE_WIN = (0.040, 0.240)   # s after onset: spectral peak for the band centre
COARSE_BAND = (40.0, 600.0)   # Hz
T_START = 0.004          # s after onset: the window starts here (strike excluded)
T_END_MAX = 0.250        # s after onset: the window never runs past this
END_DB = 40.0            # ... nor past the band envelope falling this far below its peak
FLOOR_MARGIN_DB = 15.0   # ... nor within this of the take's own band floor
MIN_CYCLES = 8.0         # fewer cycles in the window -> REFUSED (too short)
SILENCE = 1e-4           # max|x| below this -> REFUSED (silent)
SRS = (44100, 48000)
ENV_BAND = (0.50, 2.00)   # the WINDOW rule's envelope (shared): Butterworth band, x f_coarse
LP_B = 3.0                # route B: Butterworth low-pass (order 2) at this x f_coarse; no high-pass
TAU_RANGE = (0.002, 0.200)       # s: the relaxation the fit may report
DETECT_SIGMA = 3.0       # |E| / se below this -> NO-DROP (tau not identifiable)
# ... or |E| below this absolute floor: 0.003 is ~5 cents (the gate's pitch
# JND). Without it the white-residual se called a 0.3-cent deterministic drift
# in our own engine's NO-drop render "significant" (LT: E 0.0002, se 0.00004)
# and the row was refused as tau-at-bound instead of read as NO-DROP.
E_MIN_DETECT = 0.003
TAU_REL_SE_MAX = 0.5     # a DROP whose tau has a larger relative se -> REFUSED
MIN_OBS_HALFCYCLES = 4   # half-cycles inside [T_START, T_START + 2 tau] needed to see the curvature
# Agreement between routes A and B, and the known-answer bounds. Fixed on the
# analytic suite (knownanswer) BEFORE any corpus read; prereg.json repeats them.
KA_E_ABS, KA_E_REL = 0.004, 0.10     # base + lti families
KA_LN_TAU = 0.15
KA_F_REL = 2e-3
KA_HARSH_E_ABS, KA_HARSH_LN_TAU = 0.015, 0.30   # the harsh family (H2 -30, H3 -40, -20 dB burst)
# The estimator's demonstrated systematic-error envelope: the worst harsh-family
# error over 6 seeds x 3 f x 3 phases x 5 drops (252 DROP reads) was |e_E|
# 0.0130 and |ln tau| 0.234 (measured 2026-10-11 while building this file,
# before any corpus read). Every corpus uncertainty is the statistical se and
# this floor in quadrature; the white-residual se alone UNDER-states the error
# by up to 13x on that family (seed 4, 135 Hz: e 0.013, se 0.001).
U_E_FLOOR = 0.015
U_LN_TAU_FLOOR = 0.30
# Route agreement (and the render <-> staircase carry check) at the same
# envelope: two routes, or two signals, differing by less than the stated
# systematic error cannot be told apart, so a tighter rule refuses noise. It
# WAS 0.006, and refused our own shipped HT staircase (routes 0.065 vs 0.075:
# a six-step staircase is not an exponential) before any corpus read.
AGREE_E_ABS = U_E_FLOOR  # |E_A - E_B| allowed beyond 2 x combined se
AGREE_LN_TAU = 0.35      # |ln tau_A / tau_B| allowed (DROP only)

#: Injected defects (rule 5/8). Each is declared against the property it must
#: redden; `controls` prints the full MOVED/BLIND matrix.
INJECTIONS = ("stub", "fixed-onset-phase", "first-frame", "omit-host-writes",
              "confirm-pools-dev", "no-hash-check", "no-agreement-check")
INJECT: set = set()


# ---------------------------------------------------------------------------
# shared rules: onset and window
# ---------------------------------------------------------------------------
def check_signal(x, sr, what="signal"):
    x = np.asarray(x, dtype=np.float64)
    if x.ndim != 1 or len(x) == 0:
        raise Refused("malformed", f"{what}: not a 1-D signal")
    if not np.all(np.isfinite(x)):
        raise Refused("non-finite", what)
    if not (float(np.max(np.abs(x))) >= SILENCE):
        raise Refused("silent", what)
    if sr not in SRS:
        raise Refused("sample-rate", f"{what}: {sr} Hz is not one of {SRS}")
    return x


def onset_index(x) -> int:
    a = np.abs(x)
    return int(np.argmax(a >= ONSET_FRAC * a.max()))


def coarse_f(x, sr, i0) -> float:
    a, b = i0 + int(COARSE_WIN[0] * sr), min(len(x), i0 + int(COARSE_WIN[1] * sr))
    if b - a < int(0.05 * sr):
        raise Refused("too-short", "less than 50 ms after onset for the band centre")
    seg = x[a:b] * np.hanning(b - a)
    n = 1 << 18
    S = np.abs(np.fft.rfft(seg, n))
    f = np.fft.rfftfreq(n, 1 / sr)
    m = (f > COARSE_BAND[0]) & (f < COARSE_BAND[1])
    k = int(np.argmax(np.where(m, S, 0)))
    if 1 <= k < len(S) - 1:   # parabolic refinement on the log magnitude
        y0, y1, y2 = np.log(S[k - 1:k + 2] + 1e-300)
        d = 0.5 * (y0 - y2) / (y0 - 2 * y1 + y2) if (y0 - 2 * y1 + y2) != 0 else 0.0
        return float((k + d) * sr / n)
    return float(f[k])


def band_envelope(x, sr, fc):
    """The envelope the WINDOW rule reads (and only it): a Butterworth
    band-pass around fc, Hilbert magnitude. Neither route fits this signal."""
    sos = butter(2, [ENV_BAND[0] * fc / (sr / 2), ENV_BAND[1] * fc / (sr / 2)], btype="bandpass",
                 output="sos")
    return np.abs(hilbert(sosfiltfilt(sos, x)))


def window(env, sr, i0) -> tuple[int, int]:
    """[start, end) sample indices of the usable window: from T_START after
    the onset to the first of T_END_MAX, END_DB below the band envelope's peak,
    or FLOOR_MARGIN_DB above the band floor (the record's last 10 %)."""
    a = i0 + int(round(T_START * sr))
    peak = float(np.max(env[i0:])) if len(env) > i0 else 0.0
    tail = env[int(0.9 * len(env)):]
    floor = float(np.median(tail)) if len(tail) else 0.0
    lim = max(peak * 10 ** (-END_DB / 20), floor * 10 ** (FLOOR_MARGIN_DB / 20))
    b_max = min(len(env), i0 + int(round(T_END_MAX * sr)))
    below = np.nonzero(env[a:b_max] < lim)[0]
    b = a + int(below[0]) if len(below) else b_max
    return a, b


def prepare(x, sr):
    """The shared front end: checked signal, onset, band centre, window."""
    x = check_signal(x, sr)
    i0 = onset_index(x)
    fc = coarse_f(x, sr, i0)
    a, b = window(band_envelope(x, sr, fc), sr, i0)
    if (b - a) / sr * fc < MIN_CYCLES:
        raise Refused("too-short", f"window holds {(b - a) / sr * fc:.1f} cycles < {MIN_CYCLES}")
    return x, i0, fc, a, b


# ---------------------------------------------------------------------------
# route A: waveform NLS on the UNFILTERED samples
# ---------------------------------------------------------------------------
# No band-pass: a high-pass edge turns a sin-phase onset's own DC content into
# a slow offset that the fit then reads as curvature (measured while building
# this: a 0.25 fc edge moved an analytic E 0.15 to 0.12 at sin phase and left
# cos phase alone -- a phase-dependent bias, the very thing this file exists to
# avoid). The samples are box-car averaged in blocks of `step` for speed: a
# box-car of 1/36 period has a time spread of `step` samples and a pure delay
# of half a block, which the time axis absorbs (block centres).
HARMONICS = 3            # route A models k = 1..HARMONICS of the SAME phase theta(t)


def _basis_a(q, t, phi0_fixed=None):
    """Columns for the linear part of route A: for each harmonic k,
    exp(-r_k t) [cos k theta0, sin k theta0], plus a constant. The onset phase
    and every amplitude are LINEAR coefficients -- the phase is never assumed.
    theta0(t) = 2 pi f t - 2 pi f E tau exp(-t / tau)."""
    f, E, lntau = q[0], q[1], q[2]
    rates = q[3:]
    tau = math.exp(lntau)
    th = 2 * math.pi * f * t - 2 * math.pi * f * E * tau * np.exp(-t / tau)
    cols = []
    for k, r in enumerate(rates, start=1):
        g = np.exp(-r * t)
        if k == 1 and phi0_fixed is not None:
            # INJECTED ('fixed-onset-phase'): the onset phase assumed (sin
            # onset, an all-pole resonator's), so psi = phi0 + 2 pi f E tau and
            # any phase offset can only be explained as an integrated drop
            cols.append(g * np.cos(th + phi0_fixed + 2 * math.pi * f * E * tau))
        else:
            cols += [g * np.cos(k * th), g * np.sin(k * th)]
    cols.append(np.ones_like(t))
    return np.column_stack(cols)


def _proj_resid(q, t, seg, phi0_fixed=None):
    M = _basis_a(q, t, phi0_fixed)
    coef, *_ = np.linalg.lstsq(M, seg, rcond=None)
    return seg - M @ coef


def route_a(x, sr) -> dict:
    """Variable projection: the non-linear parameters are (f_inf, E, ln tau,
    one decay rate per harmonic); amplitudes and phases are solved linearly at
    every step. Uncertainty from the projected Jacobian (Golub-Pereyra)."""
    x, i0, fc, a, b = prepare(x, sr)
    step = max(1, int(sr // (12 * HARMONICS * fc)))
    nb = (b - a) // step
    seg = x[a:a + nb * step].reshape(nb, step).mean(axis=1)
    t = (a + (np.arange(nb) * step) + (step - 1) / 2 - i0) / sr
    e = band_envelope(x, sr, fc)[a:a + nb * step:step]
    k = e > e.max() * 1e-3
    sl, _ = np.polyfit(t[k], np.log(e[k]), 1)
    rate0 = max(-sl, 0.1)
    phi0 = -math.pi / 2 if "fixed-onset-phase" in INJECT else None
    nr = HARMONICS
    lo = np.array([fc * 0.8, -0.3, math.log(TAU_RANGE[0])] + [0.0] * nr)
    hi = np.array([fc * 1.25, 0.6, math.log(TAU_RANGE[1])] + [400.0] * nr)
    best = None
    for E0 in (0.0, 0.08):
        for tau0 in (0.008, 0.03):
            q0 = np.clip(np.array([fc, E0, math.log(tau0)] + [rate0 * k_ for k_ in range(1, nr + 1)]),
                         lo + 1e-9, hi - 1e-9)
            try:
                r = least_squares(_proj_resid, q0, bounds=(lo, hi), args=(t, seg, phi0),
                                  x_scale="jac", max_nfev=200)
            except (ValueError, np.linalg.LinAlgError):
                continue
            if not (r.success and np.isfinite(r.cost) and np.all(np.isfinite(r.x))):
                continue    # an unconverged result never supplies the parameters
            if best is None or r.cost < best.cost:
                best = r
    if best is None:
        raise Refused("fit-failed", "route A: no start converged")
    q, J, res = best.x, best.jac, best.fun
    n_lin = _basis_a(q, t, phi0).shape[1]
    dof = max(1, len(res) - len(q) - n_lin)
    s2 = float(res @ res) / dof
    # The residual is treated as white at the block rate. On a recording it is
    # not: that is what route B and the agreement rule are for.
    cov = np.linalg.pinv(J.T @ J) * s2
    se = np.sqrt(np.maximum(np.diag(cov), 0))
    tau = math.exp(q[2])
    return {"route": "A", "f_inf": float(q[0]), "E": float(q[1]), "tau": tau,
            "se_E": float(se[1]), "se_tau": float(tau * se[2]), "se_f": float(se[0]),
            "fc": fc, "t0": i0 / sr, "win": [(a - i0) / sr, (b - i0) / sr], "rate1": float(q[3]),
            "rms_rel": float(math.sqrt(s2) / (np.sqrt(np.mean(seg ** 2)) + 1e-300))}


# ---------------------------------------------------------------------------
# route B: zero crossings, half-cycle count, linear in (a, f_inf, c, zigzag)
# ---------------------------------------------------------------------------
def lowpass_butter(x, sr, fc):
    return sosfiltfilt(butter(2, LP_B * fc / (sr / 2), output="sos"), x)


def crossings(y, a, b, sr):
    s = y[a:b]
    k = np.nonzero(np.signbit(s[:-1]) != np.signbit(s[1:]))[0]
    return (a + k + s[k] / (s[k] - s[k + 1])) / sr


def _lin_fit(t, n, tau, rate=0.0):
    """An offset or an even harmonic shifts rising and falling crossings in
    OPPOSITE directions; the alternating columns absorb that zig-zag (constant,
    and decaying at the ring's own envelope rate, which is how a harmonic's
    share of the ring decays) instead of letting it leak into the smooth terms."""
    z = np.where(np.arange(len(t)) % 2 == 0, 1.0, -1.0)
    X = np.column_stack([np.ones_like(t), t, -np.exp(-t / tau), z, z * np.exp(-rate * t)])
    coef, *_ = np.linalg.lstsq(X, n, rcond=None)
    r = n - X @ coef
    return coef, float(r @ r), X


def fit_halfcycles(t, n, rate=0.0) -> dict:
    """n (cycles) = a + f t - c exp(-t / tau) (+ zig-zag): linear in (a, f, c)
    for each tau; a log grid, then a bounded 1-D refinement. E = c / (f tau)."""
    grid = np.exp(np.linspace(math.log(TAU_RANGE[0]), math.log(TAU_RANGE[1]), 160))
    sse = [_lin_fit(t, n, g, rate)[1] for g in grid]
    j = int(np.argmin(sse))
    lo, hi = grid[max(0, j - 1)], grid[min(len(grid) - 1, j + 1)]
    r = minimize_scalar(lambda lt: _lin_fit(t, n, math.exp(lt), rate)[1],
                        bounds=(math.log(lo), math.log(hi)), method="bounded",
                        options={"xatol": 1e-6})
    if not (r.success and np.isfinite(r.x) and np.isfinite(r.fun)):
        raise Refused("fit-failed", "route B: bounded refinement did not converge")
    tau = math.exp(r.x)
    (a_, f, c, _z1, _z2), s, X = _lin_fit(t, n, tau, rate)
    # full Jacobian in (a, f, c, zig, zig-decaying, ln tau) for the covariance
    J = np.column_stack([X, -c * np.exp(-t / tau) * t / tau])
    dof = max(1, len(t) - 6)
    s2 = s / dof
    cov = np.linalg.pinv(J.T @ J) * s2
    E = c / (f * tau)
    g = np.array([0.0, -E / f, 1 / (f * tau), 0.0, 0.0, -E])     # dE / d(params)
    se_E = float(math.sqrt(max(g @ cov @ g, 0)))
    return {"f_inf": float(f), "E": float(E), "tau": tau, "se_E": se_E,
            "se_tau": float(tau * math.sqrt(max(cov[5, 5], 0))), "se_f": float(math.sqrt(max(cov[1, 1], 0))),
            "rms_cycles": float(math.sqrt(s2))}


def route_b(x, sr) -> dict:
    x, i0, fc, a, b = prepare(x, sr)
    y = lowpass_butter(x, sr, fc)
    tc = crossings(y, a, b, sr) - i0 / sr
    if len(tc) < 2 * MIN_CYCLES:
        raise Refused("too-short", f"route B: {len(tc)} crossings")
    # the ring's envelope rate, from the peaks between crossings (route B's own)
    seg = np.abs(y[a:b])
    edges = np.searchsorted(np.arange(a, b) / sr - i0 / sr, tc)
    pk = np.array([seg[u:v].max() for u, v in zip(edges[:-1], edges[1:]) if v > u])
    tm = 0.5 * (tc[:-1] + tc[1:])[:len(pk)]
    ok = pk > 0
    rate = max(0.0, -float(np.polyfit(tm[ok], np.log(pk[ok]), 1)[0])) if ok.sum() > 4 else 0.0
    n = np.arange(len(tc)) * 0.5
    out = fit_halfcycles(tc, n, rate)
    out.update({"route": "B", "fc": fc, "t0": i0 / sr, "win": [(a - i0) / sr, (b - i0) / sr],
                "n_cross": int(len(tc)), "_tc": tc})
    return out


# ---------------------------------------------------------------------------
# classification and agreement
# ---------------------------------------------------------------------------
def classify(r: dict) -> dict:
    """DROP (E and tau identified), NO-DROP (E indistinguishable from 0: tau is
    not identifiable and is not reported), or REFUSED with the reason."""
    E, se = r["E"], r["se_E"]
    if not (math.isfinite(E) and math.isfinite(se)) or se <= 0:
        return {"class": "REFUSED", "reason": "fit-failed", "detail": "non-finite E or se"}
    if abs(E) < max(DETECT_SIGMA * se, E_MIN_DETECT):
        return {"class": "NO-DROP", "E": E, "se_E": se, "E_upper": abs(E) + DETECT_SIGMA * se}
    tau = r["tau"]
    if tau <= TAU_RANGE[0] * 1.05 or tau >= TAU_RANGE[1] * 0.95:
        return {"class": "REFUSED", "reason": "tau-at-bound", "detail": f"tau {tau * 1e3:.2f} ms"}
    # minimum observable relaxation: enough half-cycles where the curvature lives
    halfc = 2 * r["f_inf"] * max(0.0, min(2 * tau, r["win"][1] - r["win"][0]))
    if halfc < MIN_OBS_HALFCYCLES:
        return {"class": "REFUSED", "reason": "relaxation-unobservable",
                "detail": f"{halfc:.1f} half-cycles within 2 tau of the window start"}
    if r["se_tau"] / tau > TAU_REL_SE_MAX:
        return {"class": "REFUSED", "reason": "tau-unidentifiable",
                "detail": f"se(tau)/tau {r['se_tau'] / tau:.2f}"}
    return {"class": "DROP", "E": E, "se_E": se, "tau": tau, "se_tau": r["se_tau"]}


def estimate(x, sr) -> dict:
    """Both routes, classified, and their agreement. Returns a dict whose
    `class` is DROP, NO-DROP or REFUSED (with `reason`)."""
    if "stub" in INJECT:
        return {"class": "NO-DROP", "E": 0.0, "se_E": 1.0, "f_inf": 0.0, "A": {}, "B": {}}
    if "first-frame" in INJECT:
        return first_frame_estimate(x, sr)
    try:
        ra, rb = route_a(x, sr), route_b(x, sr)
    except Refused as e:
        return {"class": "REFUSED", "reason": e.reason, "detail": str(e)}
    ca, cb = classify(ra), classify(rb)
    out = {"A": {**_clean(ra), **ca}, "B": {**_clean(rb), **cb},
           "f_inf": 0.5 * (ra["f_inf"] + rb["f_inf"]), "t0": ra["t0"], "win": ra["win"]}
    for c in (ca, cb):
        if c["class"] == "REFUSED":
            return {**out, "class": "REFUSED", "reason": c["reason"], "detail": c.get("detail", "")}
    if "no-agreement-check" not in INJECT:
        if ca["class"] != cb["class"]:
            return {**out, "class": "REFUSED", "reason": "route-disagreement",
                    "detail": f"A {ca['class']} vs B {cb['class']}"}
        tol = AGREE_E_ABS + 2 * math.hypot(ra["se_E"], rb["se_E"])
        if abs(ra["E"] - rb["E"]) > tol:
            return {**out, "class": "REFUSED", "reason": "route-disagreement",
                    "detail": f"E A {ra['E']:.4f} vs B {rb['E']:.4f} (tol {tol:.4f})"}
        if ca["class"] == "DROP" and abs(math.log(ra["tau"] / rb["tau"])) > AGREE_LN_TAU:
            return {**out, "class": "REFUSED", "reason": "route-disagreement",
                    "detail": f"tau A {ra['tau'] * 1e3:.1f} vs B {rb['tau'] * 1e3:.1f} ms"}
    # the reported value is route A's (the waveform fit); B is the check
    res = {**out, "class": ca["class"], "E": ra["E"], "se_E": ra["se_E"],
           "u_E": math.sqrt(ra["se_E"] ** 2 + U_E_FLOOR ** 2)}
    if ca["class"] == "NO-DROP":
        res["E_upper"] = max(abs(ra["E"]) + DETECT_SIGMA * ra["se_E"], E_MIN_DETECT)
    if ca["class"] == "DROP":
        res.update(tau=ra["tau"], se_tau=ra["se_tau"],
                   u_ln_tau=math.sqrt((ra["se_tau"] / ra["tau"]) ** 2 + U_LN_TAU_FLOOR ** 2),
                   cents0=1200 * math.log2(1 + ra["E"]),
                   E10=ra["E"] * math.exp(-0.010 / ra["tau"]))
    return res


def first_frame_estimate(x, sr) -> dict:
    """INJECTED ('first-frame'): the gate's kind of reading -- the instantaneous
    frequency over the first 5 ms frame against the median of the rest, with
    no phase model. Reads an onset phase as a drop."""
    x = check_signal(x, sr)
    i0 = onset_index(x)
    fc = coarse_f(x, sr, i0)
    y = band_envelope_signal(x, sr, fc)
    ph = np.unwrap(np.angle(hilbert(y)))
    fi = np.diff(ph) * sr / (2 * math.pi)
    n5 = int(0.005 * sr)
    first = float(np.mean(fi[i0:i0 + n5]))
    rest = float(np.median(fi[i0 + n5:i0 + int(0.2 * sr)]))
    E = first / rest - 1
    return {"class": "DROP", "E": E, "se_E": 1e-4, "tau": 0.005, "se_tau": 0.0, "f_inf": rest}


def band_envelope_signal(x, sr, fc):
    sos = butter(2, [ENV_BAND[0] * fc / (sr / 2), ENV_BAND[1] * fc / (sr / 2)], btype="bandpass",
                 output="sos")
    return sosfiltfilt(sos, x)


def _clean(r):
    return {k: v for k, v in r.items() if not k.startswith("_")}


# ---------------------------------------------------------------------------
# analytic known answers -- closed-form phase, nothing from DrumsFx
# ---------------------------------------------------------------------------
def analytic(f_inf, E, tau, tau_a, phi0, *, sr=48000, seconds=0.6, lead=0.01, noise_db=None,
             h2_db=None, h2_phase=0.0, h3_db=None, strike_db=None, burst_db=None, seed=0, **_):
    """A decaying tone whose phase is written in closed form:
    theta(t) = phi0 + 2 pi f (t + E tau (1 - exp(-t / tau))), i.e. f(t) =
    f (1 + E exp(-t / tau)) exactly. Optional impairments, each a known
    confound for a pitch estimator: H2/H3 locked to theta (an even harmonic
    moves zero crossings), a 1 ms broadband strike, a decaying noise burst (the
    808 tom's noise path) and a stationary white floor."""
    n = int(seconds * sr)
    t = np.arange(n) / sr - lead
    y = np.zeros(n)
    m = t >= 0
    tt = t[m]
    th = phi0 + 2 * math.pi * f_inf * (tt + (E * tau * (1 - np.exp(-tt / tau)) if E else 0.0))
    y[m] = 0.5 * np.exp(-tt / tau_a) * np.cos(th)
    if h2_db is not None:
        y[m] += 0.5 * 10 ** (h2_db / 20) * np.exp(-2 * tt / tau_a) * np.cos(2 * th + h2_phase)
    if h3_db is not None:
        y[m] += 0.5 * 10 ** (h3_db / 20) * np.exp(-3 * tt / tau_a) * np.cos(3 * th + 0.3)
    rng = np.random.default_rng(seed)
    if strike_db is not None:       # a 1 ms broadband strike at the onset
        k = m & (t < 0.001)
        y[k] += 0.5 * 10 ** (strike_db / 20) * rng.standard_normal(int(k.sum()))
    if burst_db is not None:        # a noise burst decaying at 15 ms
        y[m] += 0.5 * 10 ** (burst_db / 20) * np.exp(-tt / 0.015) * rng.standard_normal(int(m.sum()))
    if noise_db is not None:
        y += 0.5 * 10 ** (noise_db / 20) * rng.standard_normal(n)
    return y


def lti_resonator(f0, tau_a, numer, *, sr=48000, seconds=0.6, lead=480, noise_db=-70.0, seed=0):
    """scipy's two-pole resonator with a chosen numerator: (1,) all-pole
    (sin-phase onset), (1, 0, -1) band-pass and (1, -1) AC zero (cos-ish
    onset). NO pitch change at all -- the #558 gate reads +37..+39 cents on the
    last two. This estimator must read NO-DROP on every one."""
    from scipy.signal import lfilter
    n = int(seconds * sr)
    imp = np.zeros(n)
    imp[lead] = 1.0
    w, r = 2 * math.pi * f0 / sr, math.exp(-1.0 / (tau_a * sr))
    y = lfilter(list(numer), [1.0, -2 * r * math.cos(w), r * r], imp)
    y = y / np.abs(y).max() * 0.5
    y += 0.5 * 10 ** (noise_db / 20) * np.random.default_rng(seed).standard_normal(n)
    return y


KA_F = (82.0, 90.0, 101.0, 135.0, 185.0, 205.0)
KA_PHASE = (0.0, math.pi / 2, math.pi, 1.0)
KA_DROPS = ((0.0, 0.020), (0.02, 0.020), (0.06, 0.020), (0.15, 0.012), (0.06, 0.035), (0.04, 0.010))
KA_NUMER = {"all-pole": (1.0,), "band-pass (1 - z^-2)": (1.0, 0.0, -1.0), "AC zero (1 - z^-1)": (1.0, -1.0)}
#: the #558 known-answer f0/tau pairs for the three toms, plus the tuning-range edges
KA_LTI = {"LT": (90.0, 0.0876), "MT": (135.0, 0.0577), "HT": (185.0, 0.0417),
          "LT-low": (82.0, 0.0876), "HT-high": (205.0, 0.0417)}


def ka_tau_a(f):
    """A tom-like amplitude decay: Q ~ 25, tau_a = Q / (pi f)."""
    return 25.0 / (math.pi * f)


def ka_cases(full=True):
    """Three families. 'base': every f x phase x drop with H2 -35 dB, a -6 dB
    strike and a -70 dB floor. 'harsh': H2 -30 dB at a phase offset, H3
    -40 dB and a -20 dB decaying noise burst. 'lti': scipy resonators of three
    numerators with no pitch change."""
    cases = []
    for f in KA_F:
        for ph in (KA_PHASE if full else KA_PHASE[:2]):
            for E, tau in KA_DROPS:
                cases.append(dict(family="base", f_inf=f, E=E, tau=tau, tau_a=ka_tau_a(f), phi0=ph,
                                  noise_db=-70.0, h2_db=-35.0, strike_db=-6.0,
                                  sr=44100 if f < 100 else 48000))
    for i, f in enumerate((90.0, 135.0, 185.0) if full else (90.0,)):
        for ph in (0.0, math.pi / 2):
            for E, tau in KA_DROPS:
                cases.append(dict(family="harsh", f_inf=f, E=E, tau=tau, tau_a=ka_tau_a(f), phi0=ph,
                                  noise_db=-70.0, h2_db=-30.0, h2_phase=0.7, h3_db=-40.0, burst_db=-20.0,
                                  strike_db=-6.0, seed=i + 1, sr=48000 if i % 2 else 44100))
    for name, (f, ta) in KA_LTI.items():
        for nm, num in KA_NUMER.items():
            if full or name == "LT":
                cases.append(dict(family="lti", name=name, numer=num, numer_name=nm, f_inf=f, E=0.0,
                                  tau=None, tau_a=ta, phi0=None, sr=48000))
    return cases


def ka_signal(c):
    if c["family"] == "lti":
        return lti_resonator(c["f_inf"], c["tau_a"], c["numer"], sr=c["sr"])
    return analytic(**c)


def ka_observable(c) -> bool:
    """Is the case inside the declared observable domain? Judged on the TRUE
    parameters: a drop whose curvature spans fewer than MIN_OBS_HALFCYCLES
    half-cycles is outside it, and a REFUSED there is the correct answer."""
    return c["E"] == 0.0 or 2 * c["f_inf"] * 2 * c["tau"] >= MIN_OBS_HALFCYCLES


def ka_check(case, est) -> dict:
    """One analytic case against the FROZEN bounds."""
    E, tau = case["E"], case["tau"]
    if est["class"] == "REFUSED":
        if not ka_observable(case):
            return {"ok": True, "why": "", "refused_out_of_domain": True}
        return {"ok": False, "why": f"REFUSED {est.get('reason')} inside the observable domain"}
    if E == 0.0:
        lim = KA_HARSH_E_ABS if case.get("family") == "harsh" else KA_E_ABS
        ok = est["class"] == "NO-DROP" or abs(est["E"]) <= lim
        return {"ok": ok, "why": "" if ok else f"zero drop read as E {est['E']:.4f} ({est['class']})"}
    if est["class"] != "DROP":
        return {"ok": False, "why": f"drop {E} read as {est['class']}"}
    eE = abs(est["E"] - E)
    eT = abs(math.log(est["tau"] / tau))
    eF = abs(est["f_inf"] / case["f_inf"] - 1)
    harsh = case.get("family") == "harsh"
    e_abs, ln_tau = (KA_HARSH_E_ABS, KA_HARSH_LN_TAU) if harsh else (KA_E_ABS, KA_LN_TAU)
    ok = eE <= e_abs + KA_E_REL * E and eT <= ln_tau and eF <= KA_F_REL
    return {"ok": ok, "why": "" if ok else f"E {est['E']:.4f} vs {E} (|e| {eE:.4f}), tau "
            f"{est['tau'] * 1e3:.1f} vs {tau * 1e3:.1f} ms, f {est['f_inf']:.2f} vs {case['f_inf']}"}


def knownanswer(full=True, verbose=True) -> dict:
    rows = []
    for c in ka_cases(full):
        est = estimate(ka_signal(c), c["sr"])
        chk = ka_check(c, est)
        rows.append({"case": {k: v for k, v in c.items() if k != "numer"}, "class": est["class"],
                     "reason": est.get("reason"), "E": est.get("E"), "tau": est.get("tau"),
                     "f_inf": est.get("f_inf"), **chk})
        if verbose and not chk["ok"]:
            print("KA FAIL", {k: v for k, v in c.items() if k != "numer"}, chk["why"], flush=True)
    n_ok = sum(r["ok"] for r in rows)
    drops = [r for r in rows if r["class"] == "DROP" and r["case"]["E"] > 0]
    summary = {"n": len(rows), "n_ok": n_ok,
               "n_refused_out_of_domain": sum(bool(r.get("refused_out_of_domain")) for r in rows),
               "max_abs_E_error": max((abs(r["E"] - r["case"]["E"]) for r in drops), default=None),
               "max_abs_ln_tau_error": max((abs(math.log(r["tau"] / r["case"]["tau"])) for r in drops),
                                           default=None),
               "zero_drop_read_as_DROP": sum(1 for r in rows if r["case"]["E"] == 0 and r["class"] == "DROP")}
    if verbose:
        print(f"known answers: {n_ok} / {len(rows)} within the frozen bounds; " + json.dumps(_json(summary)),
              flush=True)
    return {**summary, "rows": rows}


def _sha(p: pathlib.Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def git(*a) -> str:
    p = subprocess.run(["git", *a], cwd=ROOT, capture_output=True, text=True)
    if p.returncode != 0:
        raise Refused("git", f"git {' '.join(a)} failed ({p.returncode}): {p.stderr.strip()}")
    return p.stdout.strip()


def _json(o):
    if isinstance(o, (bool, np.bool_)):
        return bool(o)
    if isinstance(o, (int, np.integer)):
        return int(o)
    if isinstance(o, (float, np.floating)):
        o = float(o)
        return round(o, 6) if math.isfinite(o) else str(o)
    if isinstance(o, dict):
        return {str(k): _json(v) for k, v in o.items() if not str(k).startswith("_")}
    if isinstance(o, (list, tuple)):
        return [_json(v) for v in o]
    if isinstance(o, np.ndarray):
        return _json(o.tolist())
    return o


# ---------------------------------------------------------------------------
# our side: the shipped engine and the production host's realised staircase
# ---------------------------------------------------------------------------
STOP = {"LT": "LT", "MT": "MT", "HT": "HT"}


def _kit(sound: str, ratio: float) -> dict:
    """The shipped kit for `sound`, its TUNING moved by `ratio` exactly as
    tom_conga_gate.render does (f0 x r, Q x r: tau held)."""
    import drums_fx as dx
    kit = dict(dx.kit_with_sounds(sound))
    m = {"LT": dx.M_LT, "MT": dx.M_MT, "HT": dx.M_HT}[sound]
    f0, q, _ = dx.TOM_PRESET[sound]
    if ratio != 1.0:
        for a, v in dx.mode_writes(m, f0 * ratio, q * ratio, dx.AMP_TOM[sound], dx.RAW):
            kit[a] = v
    return kit


def _stop(sound):
    import drums_fx as dx
    return {"LT": dx.LT, "MT": dx.MT, "HT": dx.HT}[sound]


def _mode(sound):
    import drums_fx as dx
    return {"LT": dx.M_LT, "MT": dx.M_MT, "HT": dx.M_HT}[sound]


def reference_bend(sound: str, ratio: float, accent: float = 1.0) -> list:
    """The tom-bend writes the reference `hit_writes` emits for one hit:
    (frame - hit, addr, value) at the mode's coefficient pair, from the hit on."""
    import drums_fx as dx
    import run_case as rc
    kit = _kit(sound, ratio)
    hit = rc.DRUM_SOLO_HIT_FRAME
    base = dx.A_MODE + _mode(sound) * dx.MODE_STRIDE
    w = dx.hit_writes([(hit, _stop(sound), accent)], sorted(kit.items()))
    return [(f - hit, a, v) for f, a, v in w if f >= hit and a in (base, base + 1)]


def host_bend(sound: str, ratio: float, accent: float = 1.0) -> list:
    """The PRODUCTION host's tom-bend writes (fpga/spi_host.MusicHost) for the
    first hit on a fresh image of the same kit: (frame - hit, addr, value).
    A single first hit, so #598's repeated-hit contamination cannot enter."""
    import spi_host as sh
    kit = _kit(sound, ratio)
    hit = 4800
    h = sh.MusicHost(kit=sorted(kit.items()))
    h.hits([(hit, _stop(sound), accent)])
    return [(w.frame - hit, w.addr, w.data) for w in h.w if w.tag == "tom-bend"]


def staircase(sound: str, ratio: float) -> dict:
    """The realised staircase: the production host's writes, asserted equal to
    the reference's (else REFUSED), decoded back to f0 through the same
    register -> pole conversion the block runs, plus what the law asks for."""
    import drums_fx as dx
    ref, host = reference_bend(sound, ratio), host_bend(sound, ratio)
    if sorted(ref) != sorted(host):
        raise Refused("host-staircase-mismatch",
                      f"{sound} x{ratio:.4f}: production host {len(host)} writes, reference {len(ref)}")
    base = dx.A_MODE + _mode(sound) * dx.MODE_STRIDE
    by = {}
    for f, a, v in ref:
        by.setdefault(f, {})[a] = v
    steps = []
    for f in sorted(by):
        d = by[f]
        if base in d and base + 1 in d:
            steps.append((f, dx.poles_from_regs(d[base], d[base + 1])[0]))
    kit = _kit(sound, ratio)
    f0, q = dx.poles_from_regs(kit[base], kit[base + 1])
    return {"steps": steps, "f0_image": f0, "q_image": q, "n_writes": len(ref),
            "E_law": dx.tom_drop_excess(_mode(sound), f0, 1.0),
            "horizon_ms": dx.TOM_DROP_MS, "law_tau_ms": dx.TOM_DROP_MS / 3.0,
            "settled_hz": steps[-1][1] if steps else None}


def staircase_tone(st: dict, sr: int = 48000, seconds: float = 0.6, lead: int = 480) -> np.ndarray:
    """A tone whose instantaneous frequency IS the realised staircase (held
    between writes, the kit's f0 before the first), decaying at the image's
    tau = Q / (pi f0). Its estimate is the staircase's own (E, tau) as this
    estimator reads them."""
    n = int(seconds * sr)
    f = np.full(n, st["f0_image"])
    for (fr, hz), nxt in zip(st["steps"], st["steps"][1:] + [(n, None)]):
        f[lead + fr:lead + nxt[0]] = hz
    ph = np.concatenate([[0.0], np.cumsum(2 * math.pi * f[lead:-1] / sr)])
    tau_a = st["q_image"] / (math.pi * st["f0_image"])
    y = np.zeros(n)
    tt = np.arange(n - lead) / sr
    y[lead:] = 0.5 * np.exp(-tt / tau_a) * np.cos(ph)
    return y


def render_ours(sound: str, ratio: float) -> tuple:
    """`run_case.render_drum_solo`'s exact render with the TUNING moved; the
    'omit-host-writes' injection drops the tom-bend writes from what the
    engine plays (and from nothing else)."""
    import drums_fx as dx
    import run_case as rc
    stop = dx.SOUND_STOP[sound]
    n = int(rc.SOLO_SECONDS.get(sound, 2.2) * dx.SR)
    kit = _kit(sound, ratio)
    hit = rc.DRUM_SOLO_HIT_FRAME
    w = dx.hit_writes([(hit, stop, 1.0)], sorted(kit.items()))
    if "omit-host-writes" in INJECT:
        base = dx.A_MODE + _mode(sound) * dx.MODE_STRIDE
        w = [(f, a, v) for f, a, v in w if not (f >= hit and a in (base, base + 1))]
    d = dx.DrumsFx(nums=dx.N_MODES)
    dm, bd = d.play(w, n)
    gg = dx.accent_reg(0.45)
    out = np.asarray(dx.output_fx(np.zeros(n), 0, dm, gg, bd, gg), dtype=np.float64) / 32768.0
    return out, dx.SR


def assert_unity_is_shipped(sounds=TOMS):
    """Precondition: the ratio-1.0 render IS the production solo render."""
    import run_case as rc
    for s_ in sounds:
        if not np.array_equal(render_ours(s_, 1.0)[0], rc.render_drum_solo(s_)[0]):
            raise Refused("not-shipped-engine", f"render_ours({s_}, 1.0) != run_case.render_drum_solo")


def measure_ours(sound: str, ratio: float) -> dict:
    """Our side of one condition: the render's estimate, the staircase's
    estimate, the law's numbers, and the carry check render <-> staircase."""
    st = staircase(sound, ratio)
    y, sr = render_ours(sound, ratio)
    er = estimate(y, sr)
    es = estimate(staircase_tone(st, sr), sr)
    carry = carry_check(er, es)
    return {"render": er, "staircase_est": es, "carry": carry,
            "law": {k: st[k] for k in ("E_law", "horizon_ms", "law_tau_ms", "f0_image", "settled_hz", "n_writes")},
            "steps_hz": [[f, hz] for f, hz in st["steps"]]}


def carry_check(er: dict, es: dict) -> dict:
    """Does the render carry the staircase? Compared ROUTE BY ROUTE (A on the
    render against A on the staircase tone, B against B), so the check does
    not need the staircase to BE an exponential: a six-step staircase held at
    f0 (1 + E e^-3) after 60 ms is not one, and on HT the two routes read it
    0.010 apart (E 0.065 vs 0.075) -- a misfit of the model, not a carry
    failure. Each route: same class, E within AGREE_E_ABS + 2 se, tau within
    AGREE_LN_TAU."""
    if er.get("reason") in ("silent", "non-finite", "sample-rate", "too-short", "malformed") or \
            es.get("reason") in ("silent", "non-finite", "sample-rate", "too-short", "malformed"):
        return {"ok": False, "why": f"REFUSED render {er.get('reason')} / staircase {es.get('reason')}"}
    out = {"ok": True, "why": ""}
    for rt in ("A", "B"):
        r, q = er.get(rt, {}), es.get(rt, {})
        if not r or not q or r.get("class") == "REFUSED" or q.get("class") == "REFUSED":
            return {"ok": False, "why": f"route {rt}: render {r.get('class')} {r.get('reason', '')} / "
                                        f"staircase {q.get('class')} {q.get('reason', '')}"}
        if r["class"] != q["class"]:
            return {"ok": False, "why": f"route {rt}: render {r['class']} vs staircase {q['class']}"}
        if r["class"] == "NO-DROP":
            continue
        dE = abs(r["E"] - q["E"])
        dT = abs(math.log(r["tau"] / q["tau"]))
        out[f"dE_{rt}"], out[f"dlntau_{rt}"] = dE, dT
        if dE > AGREE_E_ABS + 2 * math.hypot(r["se_E"], q["se_E"]) or dT > AGREE_LN_TAU:
            return {**out, "ok": False, "why": f"route {rt}: render E {r['E']:.4f} tau {r['tau'] * 1e3:.1f} ms "
                                                f"vs staircase E {q['E']:.4f} tau {q['tau'] * 1e3:.1f} ms"}
    return out


# ---------------------------------------------------------------------------
# the corpus
# ---------------------------------------------------------------------------
def load_prereg(path: pathlib.Path = PREREG) -> dict:
    if not path.exists():
        raise Refused("no-prereg", str(path))
    return json.loads(path.read_text())


def take_rel(cond: str) -> str:
    return f"{cond[:2].lower()}8/{cond}.WAV"


def load_take(refs: pathlib.Path, cond: str, sha256: dict) -> tuple:
    import perceptual_gate as pg
    rel = take_rel(cond)
    p = refs / rel
    if not p.exists():
        raise Refused("missing-take", str(p))
    if "no-hash-check" not in INJECT:
        want = sha256.get(rel)
        if want is None:
            raise Refused("unregistered-take", f"{rel} is not in the frozen table")
        got = _sha(p)
        if got != want:
            raise Refused("wrong-hash", f"{rel}: sha256 {got[:16]} is not the frozen {want[:16]}")
    x, sr = pg.load_wav(p)
    x = np.asarray(x, dtype=np.float64)
    if x.ndim > 1:
        x = x.mean(axis=1)
    check_signal(x, sr, rel)
    return x, sr


def measure_rows(refs: pathlib.Path, prereg: dict, split: str) -> dict:
    """Every condition of one split: the 808 take's estimate, its tuning ratio
    against the same sound's TUNING 5.0 take (route A's f_inf), and ours at
    that ratio. A REFUSED side is kept, with its reason, never dropped."""
    sha = prereg["corpus"]["sha256"]
    conds = [c for c, sp in prereg["split_of"].items() if sp == split]
    assert_unity_is_shipped()
    ref_f = {}
    rows = {}
    for c in conds:
        s_ = c[:2]
        row = {"take": take_rel(c), "split": split}
        try:
            if s_ not in ref_f:
                x50, sr50 = load_take(refs, f"{s_}50", sha)
                e50 = estimate(x50, sr50)
                if e50["class"] == "REFUSED":
                    raise Refused("no-tuning-reference", f"{s_}50: {e50.get('reason')}")
                ref_f[s_] = e50["f_inf"]
            x, sr = load_take(refs, c, sha)
            e808 = estimate(x, sr)
            row["808"] = e808
            if e808["class"] == "REFUSED":
                raise Refused("808-" + e808["reason"], e808.get("detail", ""))
            ratio = 1.0 if c.endswith("50") else e808["f_inf"] / ref_f[s_]
            row["tuning_ratio"] = ratio
            row["ours"] = measure_ours(s_, ratio)
            if not row["ours"]["carry"]["ok"]:
                raise Refused("staircase-carry", row["ours"]["carry"]["why"])
            if row["ours"]["render"]["class"] == "REFUSED":
                raise Refused("ours-" + row["ours"]["render"]["reason"], "")
            row["discrepancy"] = discrepancy(e808, row["ours"]["render"])
            row["class"] = "MEASURED"
        except Refused as e:
            row["class"] = "REFUSED"
            row["reason"] = e.reason
            row["detail"] = str(e)
        rows[c] = row
        print(f"{c} [{split}] {row['class']} {row.get('reason', '')} " + (
            f"808 E {row['808']['E']:+.4f} tau {row['808'].get('tau', float('nan')) * 1e3:.1f} ms | ours E "
            f"{row['ours']['render']['E']:+.4f} tau {row['ours']['render'].get('tau', float('nan')) * 1e3:.1f} ms"
            if row["class"] == "MEASURED" else ""), flush=True)
    return rows


def discrepancy(e808: dict, eo: dict) -> dict:
    """808 minus ours, with the combined uncertainty. Onset excess in cents;
    tau as a log ratio (only when both are DROP)."""
    c8 = 1200 * math.log2(1 + e808["E"])
    co = 1200 * math.log2(1 + eo["E"])
    # d cents / dE = 1200 / ((1 + E) ln 2)
    u = math.hypot(e808["u_E"] * 1200 / ((1 + e808["E"]) * math.log(2)),
                   eo["u_E"] * 1200 / ((1 + eo["E"]) * math.log(2)))
    out = {"cents0_808": c8, "cents0_ours": co, "d_cents": c8 - co, "u_cents": u,
           "class_808": e808["class"], "class_ours": eo["class"]}
    if e808["class"] == "DROP" and eo["class"] == "DROP":
        out.update(ln_tau_ratio=math.log(e808["tau"] / eo["tau"]),
                   u_ln_tau=math.hypot(e808["u_ln_tau"], eo["u_ln_tau"]),
                   tau_808_ms=e808["tau"] * 1e3, tau_ours_ms=eo["tau"] * 1e3)
    return out


def confirm_record(refs: pathlib.Path, pr: dict, fit_path: pathlib.Path) -> dict:
    fit_rec = json.loads(fit_path.read_text())
    res = {"fit_record": {"path": str(fit_path), "sha256": _sha(fit_path)}}
    if fit_rec.get("status") != "FITTED":
        # Nothing was fitted, so there is no hypothesis to confirm. Measuring
        # the reserved rows now would expose them to this estimator while
        # testing nothing; they are left UNREAD for a successor, and the
        # judgement is the rule's own REFUSED.
        res["rows"] = {c: {"split": "confirmation", "class": "NOT-READ",
                           "reason": "development fit REFUSED: reserved row not measured",
                           "take": take_rel(c)}
                       for c, sp in pr["split_of"].items() if sp == "confirmation"}
        res["reserved_rows_read"] = False
    else:
        res["rows"] = measure_rows(refs, pr, "confirmation")
        res["reserved_rows_read"] = True
    res["judgement"] = judge_confirmation(res["rows"], fit_rec, pr)
    return res


def describe(refs: pathlib.Path, prereg: dict) -> dict:
    """POST-HOC DIAGNOSTIC, development takes only (it refuses nothing else
    because it reads nothing else): each take's estimate with BOTH routes'
    numbers, including the ones `measure` refused, and our unity renders. It
    selects nothing and confirms nothing; it exists so a refused row's reason
    is inspectable rather than a single word."""
    sha = prereg["corpus"]["sha256"]
    out = {"post_hoc": True, "takes": {}, "ours_unity": {}}
    for c, sp in prereg["split_of"].items():
        if sp != "development":
            continue
        try:
            x, sr = load_take(refs, c, sha)
            out["takes"][c] = estimate(x, sr)
        except Refused as e:
            out["takes"][c] = {"class": "REFUSED", "reason": e.reason, "detail": str(e)}
        e = out["takes"][c]
        print(c, e["class"], e.get("reason", ""), e.get("detail", ""), " | ".join(
            f"{rt} {e[rt].get('class')} E {e[rt].get('E', float('nan')):+.4f} tau "
            f"{e[rt].get('tau', float('nan')) * 1e3:.1f} ms" for rt in ("A", "B") if rt in e), flush=True)
    for s_ in TOMS:
        out["ours_unity"][s_] = measure_ours(s_, 1.0)
    return out


# ---------------------------------------------------------------------------
# fit (development only) and confirm (reserved, once)
# ---------------------------------------------------------------------------
def misbound_rows(rows: dict, prereg: dict, split: str) -> list:
    """Condition ids whose identity does not bind them to `split`: the id must
    be in the frozen prereg with that split, the row's own label must agree,
    and its take path must be the id's. A label alone is data, not identity."""
    split_of = prereg.get("split_of")
    if not split_of:
        raise Refused("prereg-no-split_of", "cannot bind rows to the frozen split")
    return [k for k, r in rows.items()
            if split_of.get(k) != split or r.get("split") != split or r.get("take") != take_rel(k)]


def fit(dev_rows: dict, prereg: dict) -> dict:
    rules = prereg["rules"]
    bad = misbound_rows(dev_rows, prereg, "development")
    if bad:
        raise Refused("fit-reads-non-development",
                      f"fit was handed rows not bound to the frozen development split: {bad}")
    use = {k: r for k, r in dev_rows.items() if r["class"] == "MEASURED"}
    out = {"n_dev": len(dev_rows), "n_usable": len(use),
           "refused": {k: r.get("reason") for k, r in dev_rows.items() if r["class"] != "MEASURED"}}
    if len(use) < rules["min_dev_rows"]:
        out["status"] = "REFUSED"
        out["reason"] = f"not-identifiable: {len(use)} usable development rows < {rules['min_dev_rows']}"
        return out
    dc = [r["discrepancy"]["d_cents"] for r in use.values()]
    lt = [r["discrepancy"]["ln_tau_ratio"] for r in use.values() if "ln_tau_ratio" in r["discrepancy"]]
    out["median_d_cents"] = statistics.median(dc)
    out["median_ln_tau_ratio"] = statistics.median(lt) if len(lt) >= rules["min_dev_rows"] else None
    out["per_sound"] = {s_: {"median_d_cents": statistics.median(
        [r["discrepancy"]["d_cents"] for k, r in use.items() if k.startswith(s_)] or [float("nan")])}
        for s_ in TOMS}
    jnd = rules["jnd_cents"]
    out["magnitude_proposal"] = ("none: within JND" if abs(out["median_d_cents"]) < jnd else
                                 ("808 larger" if out["median_d_cents"] > 0 else "808 smaller"))
    if out["median_ln_tau_ratio"] is None:
        out["tau_proposal"] = "REFUSED: too few rows with both taus identified"
    else:
        out["tau_proposal"] = ("none: within threshold" if abs(out["median_ln_tau_ratio"]) < math.log(
            rules["tau_ratio_threshold"]) else ("808 slower" if out["median_ln_tau_ratio"] > 0 else "808 faster"))
    out["status"] = "FITTED"
    return out


def judge_confirmation(conf_rows: dict, fit_rec: dict, prereg: dict) -> dict:
    """The frozen confirmation rule, applied once to the reserved rows."""
    rules = prereg["rules"]
    if "confirm-pools-dev" in INJECT:
        rows = dict(conf_rows)
        rows.update(fit_rec.get("_dev_rows", {}))
    else:
        rows = dict(conf_rows)
    # validate EVERYTHING supplied before any use: filtering first would make
    # a supplied development row invisible to this check
    leak = misbound_rows(rows, prereg, "confirmation")
    use = {k: r for k, r in rows.items() if r["class"] == "MEASURED"}
    out = {"n_reserved": len(conf_rows), "n_usable": len(use), "development_rows_in_confirmation": leak,
           "refused": {k: r.get("reason") for k, r in rows.items() if r["class"] != "MEASURED"},
           "independence": prereg["adequacy"]["independent_confirmation"]}
    if leak:
        out["magnitude"] = out["relaxation"] = {"verdict": "REFUSED", "reason": "confirmation-pooled-development"}
        out["decision"] = decide(out, prereg)
        return out
    if fit_rec.get("status") != "FITTED":
        out["magnitude"] = out["relaxation"] = {"verdict": "REFUSED", "reason": "no development fit"}
        out["decision"] = decide(out, prereg)
        return out
    if len(use) < rules["min_confirm_rows"]:
        out["magnitude"] = out["relaxation"] = {
            "verdict": "REFUSED", "reason": f"not-identifiable: {len(use)} usable < {rules['min_confirm_rows']}"}
        out["decision"] = decide(out, prereg)
        return out
    frac = rules["confirm_frac"]
    jnd = rules["jnd_cents"]
    # magnitude
    m_dev = fit_rec["median_d_cents"]
    dc = {k: r["discrepancy"]["d_cents"] for k, r in use.items()}
    uc = {k: r["discrepancy"]["u_cents"] for k, r in use.items()}
    if abs(m_dev) < jnd:
        agree = sum(abs(dc[k]) <= max(jnd, 2 * uc[k]) for k in dc) / len(dc)
        out["magnitude"] = {"proposal": "no change", "frac_within": agree,
                            "verdict": "CONFIRMED" if agree >= frac else "NOT-CONFIRMED"}
    else:
        sign = 1 if m_dev > 0 else -1
        same = sum(sign * dc[k] > 0 for k in dc) / len(dc)
        resolved = sum(abs(dc[k]) > 2 * uc[k] for k in dc) / len(dc)
        med = statistics.median(dc.values())
        pred_better = sum(abs(dc[k] - m_dev) < abs(dc[k]) for k in dc) / len(dc)
        ok = same >= frac and resolved >= frac and abs(med) >= jnd and sign * med > 0 and pred_better >= frac
        out["magnitude"] = {"proposal": fit_rec["magnitude_proposal"], "dev_median_d_cents": m_dev,
                            "conf_median_d_cents": med, "frac_same_sign": same, "frac_resolved_2u": resolved,
                            "frac_dev_offset_predicts_better": pred_better,
                            "verdict": "CONFIRMED" if ok else "NOT-CONFIRMED"}
    # relaxation
    lt_dev = fit_rec.get("median_ln_tau_ratio")
    lt = {k: r["discrepancy"]["ln_tau_ratio"] for k, r in use.items() if "ln_tau_ratio" in r["discrepancy"]}
    ut = {k: r["discrepancy"]["u_ln_tau"] for k, r in use.items() if "ln_tau_ratio" in r["discrepancy"]}
    thr = math.log(rules["tau_ratio_threshold"])
    if lt_dev is None or len(lt) < rules["min_confirm_rows"]:
        out["relaxation"] = {"verdict": "REFUSED", "reason": f"not-identifiable: {len(lt)} rows with both taus"}
    elif abs(lt_dev) < thr:
        agree = sum(abs(lt[k]) <= max(thr, 2 * ut[k]) for k in lt) / len(lt)
        out["relaxation"] = {"proposal": "no change", "frac_within": agree,
                             "verdict": "CONFIRMED" if agree >= frac else "NOT-CONFIRMED"}
    else:
        sign = 1 if lt_dev > 0 else -1
        same = sum(sign * lt[k] > 0 for k in lt) / len(lt)
        resolved = sum(abs(lt[k]) > 2 * ut[k] for k in lt) / len(lt)
        med = statistics.median(lt.values())
        pred_better = sum(abs(lt[k] - lt_dev) < abs(lt[k]) for k in lt) / len(lt)
        ok = same >= frac and resolved >= frac and abs(med) >= thr and sign * med > 0 and pred_better >= frac
        out["relaxation"] = {"proposal": fit_rec["tau_proposal"], "dev_median_ln_tau_ratio": lt_dev,
                             "conf_median_ln_tau_ratio": med, "frac_same_sign": same,
                             "frac_resolved_2u": resolved, "frac_dev_offset_predicts_better": pred_better,
                             "verdict": "CONFIRMED" if ok else "NOT-CONFIRMED"}
    out["decision"] = decide(out, prereg)
    return out


def decide(conf: dict, prereg: dict) -> dict:
    """justified / unsupported / not identifiable, per quantity, with the
    independence limitation attached rather than dropped."""
    d = {}
    for q in ("magnitude", "relaxation"):
        v = conf[q]
        if v["verdict"] == "REFUSED":
            d[q] = "not identifiable"
        elif v["verdict"] == "CONFIRMED" and v.get("proposal") not in (None, "no change"):
            # prereg adequacy.accent_confound: the law's E is accent-scaled and
            # the Fischer accent is undocumented, so a confirmed magnitude
            # discrepancy cannot identify a TOM_DROP_RATIO defect
            d[q] = ("not identifiable (accent confound): discrepancy confirmed as a measured effect"
                    if q == "magnitude" else
                    "repair supported on reserved (previously exposed) conditions, at this unit's "
                    "undocumented accent")
        elif v["verdict"] == "CONFIRMED":
            d[q] = "no repair: the shipped law agrees within the frozen threshold"
        else:
            d[q] = "unsupported"
    d["independent_confirmation"] = conf["independence"]
    d["scope"] = prereg["adequacy"]["scope"]
    return d


# ---------------------------------------------------------------------------
# controls: the stub and every injection, MOVED / BLIND per property
# ---------------------------------------------------------------------------
def _prop_zero_drop_phase():
    """Constant-pitch tones of four onset phases, and the three LTI
    numerators, read NO-DROP (or |E| within KA_E_ABS)."""
    bad = 0
    for ph in KA_PHASE:
        c = dict(family="base", f_inf=135.0, E=0.0, tau=0.02, tau_a=ka_tau_a(135.0), phi0=ph,
                 noise_db=-70.0, h2_db=-35.0, strike_db=-6.0, sr=48000)
        bad += not ka_check(c, estimate(ka_signal(c), 48000))["ok"]
    for nm, num in KA_NUMER.items():
        c = dict(family="lti", f_inf=90.0, E=0.0, tau=None, tau_a=0.0876, numer=num, sr=48000)
        bad += not ka_check(c, estimate(ka_signal(c), 48000))["ok"]
    return bad == 0


def _prop_nonzero_drop():
    bad = 0
    for E, tau in ((0.06, 0.020), (0.15, 0.012)):
        for ph in (0.0, math.pi / 2):
            c = dict(family="base", f_inf=135.0, E=E, tau=tau, tau_a=ka_tau_a(135.0), phi0=ph,
                     noise_db=-70.0, h2_db=-35.0, strike_db=-6.0, sr=48000)
            bad += not ka_check(c, estimate(ka_signal(c), 48000))["ok"]
    return bad == 0


def beating_tone(sr=48000):
    """The defeating input for the agreement rule: a 135 Hz drop (E 0.06,
    tau 20 ms) plus a second partial 3 % above at -6 dB. The beat moves the
    zero crossings (route B) differently from the waveform fit (route A): A
    reads DROP and B NO-DROP, so `estimate` must REFUSE (route-disagreement).
    Without the agreement rule it would answer with route A's number."""
    f, ta = 135.0, ka_tau_a(135.0)
    return (analytic(f, 0.06, 0.02, ta, 0.3, sr=sr)
            + analytic(f * 1.03, 0.0, 0.02, ta * 1.5, 1.0, sr=sr) * 10 ** (-6 / 20))


def _prop_route_disagreement_refused():
    est = estimate(beating_tone(), 48000)
    return est["class"] == "REFUSED" and est.get("reason") == "route-disagreement"


def _prop_staircase_carry():
    """The shipped LT render carries the production host's staircase."""
    m = measure_ours("LT", 1.0)
    return bool(m["carry"]["ok"]) and m["render"]["class"] == "DROP"


def _prop_hash_refusal(tmp: pathlib.Path):
    import wave
    p = tmp / "lt8" / "LT50.WAV"
    p.parent.mkdir(parents=True, exist_ok=True)
    y = (analytic(90.0, 0.05, 0.02, ka_tau_a(90.0), 0.0, sr=44100) * 32767).astype("<i2")
    with wave.open(str(p), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(44100)
        w.writeframes(y.tobytes())
    try:
        load_take(tmp, "LT50", {"lt8/LT50.WAV": "0" * 64})
    except Refused as e:
        return e.reason == "wrong-hash"
    return False


def _prop_confirmation_disjoint():
    pre = {"rules": {"min_confirm_rows": 2, "confirm_frac": 0.75, "jnd_cents": 5.0, "tau_ratio_threshold": 1.25},
           "adequacy": {"independent_confirmation": "x", "scope": "x"},
           "split_of": {"LT00": "confirmation", "LT10": "confirmation",
                        "LT25": "development", "LT50": "development", "LT75": "development"}}

    def row(c, split, d):
        return {"split": split, "take": take_rel(c), "class": "MEASURED",
                "discrepancy": {"d_cents": d, "u_cents": 1.0, "ln_tau_ratio": 0.0, "u_ln_tau": 0.1}}
    conf = {"LT00": row("LT00", "confirmation", 1.0), "LT10": row("LT10", "confirmation", -1.0)}
    dev = {"LT25": row("LT25", "development", 40.0), "LT50": row("LT50", "development", 41.0),
           "LT75": row("LT75", "development", 39.0)}
    fit_rec = {"status": "FITTED", "median_d_cents": 40.0, "median_ln_tau_ratio": 0.0,
               "magnitude_proposal": "808 larger", "tau_proposal": "none: within threshold", "_dev_rows": dev}
    res = judge_confirmation(conf, fit_rec, pre)
    return (not res["development_rows_in_confirmation"]) and res["magnitude"]["verdict"] == "NOT-CONFIRMED"


PROPERTIES = ("zero_drop_any_phase", "nonzero_drop_recovered", "route_disagreement_refused",
              "staircase_carried", "wrong_hash_refused", "confirmation_disjoint")
#: each injection's declared target: the property it MUST move
DECLARED = {"stub": "nonzero_drop_recovered", "fixed-onset-phase": "zero_drop_any_phase",
            "first-frame": "zero_drop_any_phase", "omit-host-writes": "staircase_carried",
            "confirm-pools-dev": "confirmation_disjoint", "no-hash-check": "wrong_hash_refused",
            "no-agreement-check": "route_disagreement_refused"}


def properties(tmp: pathlib.Path) -> dict:
    out = {}
    for name, fn in (("zero_drop_any_phase", _prop_zero_drop_phase),
                     ("nonzero_drop_recovered", _prop_nonzero_drop),
                     ("route_disagreement_refused", _prop_route_disagreement_refused),
                     ("staircase_carried", _prop_staircase_carry),
                     ("wrong_hash_refused", lambda: _prop_hash_refusal(tmp)),
                     ("confirmation_disjoint", _prop_confirmation_disjoint)):
        try:
            out[name] = bool(fn())
        except Exception as e:  # noqa: BLE001 -- an exception is NO VERDICT, recorded as such
            out[name] = f"NO-VERDICT: {type(e).__name__}: {e}"
    return out


def controls(tmp: pathlib.Path, verbose=True) -> dict:
    """Clean, then each injection, then clean again. A control is CAUGHT only
    if the clean run holds every property (condition 1), the mutant ran to a
    verdict on every property (condition 2) and its declared property is the
    one that moved (condition 3). Anything else is NO VERDICT."""
    global INJECT
    INJECT = set()
    clean = properties(tmp)
    res = {"clean": clean, "mutants": {}}
    for m in INJECTIONS:
        INJECT = {m}
        try:
            got = properties(tmp)
        finally:
            INJECT = set()
        mat = {p: ("NO-VERDICT" if not isinstance(got[p], bool) else
                   "MOVED" if got[p] != clean[p] else "BLIND") for p in PROPERTIES}
        ran = all(isinstance(v, bool) for v in got.values())
        caught = all(v is True for v in clean.values()) and ran and mat[DECLARED[m]] == "MOVED"
        res["mutants"][m] = {"matrix": mat, "declared": DECLARED[m],
                             "verdict": "CAUGHT" if caught else "NO-VERDICT" if not ran else "MISSED"}
    res["clean_again"] = properties(tmp)
    if verbose:
        w = max(len(p) for p in PROPERTIES)
        print("clean:", clean)
        print(f"{'injection':22s} " + " ".join(f"{p[:w]:>{w}s}" for p in PROPERTIES) + "  verdict")
        for m, r in res["mutants"].items():
            print(f"{m:22s} " + " ".join(f"{r['matrix'][p]:>{w}s}" for p in PROPERTIES) + f"  {r['verdict']}")
        print("clean again:", res["clean_again"])
    return res


# ---------------------------------------------------------------------------
# provenance and the CLI
# ---------------------------------------------------------------------------
DIRTY_SCOPE = ("model", "tools", "fpga", str(PREREG.relative_to(ROOT)))


def provenance(refs) -> dict:
    import drums_fx  # noqa: F401
    import run_case  # noqa: F401
    import spi_host  # noqa: F401
    dirty = git("status", "--porcelain", "--", *DIRTY_SCOPE)
    if dirty:
        raise Refused("dirty-tree", "uncommitted changes in " + ", ".join(DIRTY_SCOPE) + ":\n" + dirty)
    return {"commit": git("rev-parse", "HEAD"), "dirty": False, "read_at": "start",
            "origin_main": git("rev-parse", "origin/main"),
            "prereg_sha256": _sha(PREREG) if PREREG.exists() else None,
            "refs": None if refs is None else str(refs), "argv": sys.argv,
            "python": sys.version.split()[0], "numpy": np.__version__,
            "scipy": __import__("scipy").__version__}


def close_provenance(prov: dict) -> dict:
    end = git("rev-parse", "HEAD")
    prov["commit_at_end"] = end
    prov["head_moved_during_run"] = end != prov["commit"]
    prov["dirty_at_end"] = bool(git("status", "--porcelain", "--", *DIRTY_SCOPE))
    return prov


def verification_failures(cmd: str, res: dict) -> list:
    """Why a knownanswer/controls record must NOT exit green (empty = holds).
    A failed check is exit 1; a scientific REFUSED is exit 0; a Refused
    precondition is exit 2."""
    why = []
    if cmd == "knownanswer":
        if res["n_ok"] != res["n"]:
            why.append(f"known answers: {res['n_ok']} / {res['n']} ok")
    elif cmd == "controls":
        why += [f"control {m}: {r['verdict']}" for m, r in res["mutants"].items() if r["verdict"] != "CAUGHT"]
        for key in ("clean", "clean_again"):
            why += [f"{key}.{p}: {v!r}" for p, v in res[key].items() if v is not True]
    return why


def main(argv=None) -> int:
    global INJECT
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    k = sub.add_parser("knownanswer")
    k.add_argument("--out", type=pathlib.Path, required=True)
    c = sub.add_parser("controls")
    c.add_argument("--out", type=pathlib.Path, required=True)
    m = sub.add_parser("measure")
    m.add_argument("--refs", type=pathlib.Path, required=True)
    m.add_argument("--split", choices=("development",), required=True,
                   help="only development: the reserved rows are read by `confirm`, once")
    m.add_argument("--out", type=pathlib.Path, required=True)
    f = sub.add_parser("fit")
    f.add_argument("record", type=pathlib.Path)
    f.add_argument("--out", type=pathlib.Path, required=True)
    cf = sub.add_parser("confirm")
    cf.add_argument("--refs", type=pathlib.Path, required=True)
    cf.add_argument("--fit", type=pathlib.Path, required=True)
    cf.add_argument("--out", type=pathlib.Path, required=True)
    d = sub.add_parser("describe", help="POST-HOC: per-take estimates of the DEVELOPMENT takes only")
    d.add_argument("--refs", type=pathlib.Path, required=True)
    d.add_argument("--out", type=pathlib.Path, required=True)
    for p_ in (k, c, m, f, cf, d):
        p_.add_argument("--inject", choices=INJECTIONS, default=None)
    a = ap.parse_args(argv)
    INJECT = {a.inject} if a.inject else set()
    try:
        if a.cmd == "confirm" and a.out.exists():
            raise Refused("already-confirmed", f"{a.out} exists: the reserved rows are evaluated ONCE")
        prov = provenance(getattr(a, "refs", None))
        if a.cmd == "knownanswer":
            res = knownanswer(full=True)
        elif a.cmd == "controls":
            import tempfile
            with tempfile.TemporaryDirectory() as td:
                res = controls(pathlib.Path(td))
        elif a.cmd == "measure":
            res = {"rows": measure_rows(a.refs, load_prereg(), a.split)}
        elif a.cmd == "fit":
            rec = json.loads(a.record.read_text())
            res = fit(rec["rows"], load_prereg())
            res["from_record"] = {"path": str(a.record), "sha256": _sha(a.record)}
            print(json.dumps(_json(res), indent=1))
        elif a.cmd == "describe":
            res = describe(a.refs, load_prereg())
        else:
            res = confirm_record(a.refs, load_prereg(), a.fit)
            print(json.dumps(_json(res["judgement"]), indent=1))
        res["inject"] = sorted(INJECT)
        res["provenance"] = close_provenance(prov)
    except Refused as e:
        print("REFUSED:", e)
        return 2
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text(json.dumps(_json(res), indent=1) + "\n")
    failures = verification_failures(a.cmd, res)
    for w in failures:
        print("FAILED:", w)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
