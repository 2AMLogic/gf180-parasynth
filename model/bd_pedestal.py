#!/usr/bin/env python3
"""BD DC pedestal: a qualified instrument, the mechanism, and a target (#220).

Three separate things live here, and the file keeps them apart:

  1. `tail_pedestal` -- the INSTRUMENT. The historical probe
     (`bd_excitation_probe.body_stats`) reads the pedestal as the plain mean of
     the record after 1.2 s, relative to the render peak. That is biased by any
     residual decaying oscillation whose window is not an integer number of
     cycles, and it answers (with a number that looks like data) when the tail
     has not finished decaying. This replacement fits  c + b*t + a*cos + s*sin
     to the tail, and REFUSES (status REFUSED, never a number) on a short record,
     a silent record, non-finite samples, a tail that is still decaying, a
     growing tail or an unresolved drift. Its own qualification is
     `test_bd_pedestal.py`, against signals constructed independently of the
     bank (known offsets, zero-offset decaying sinusoids, limit cycles, and a
     legitimate long decay that DEFEATS the fixed-window mean).

  2. `run_pole` -- the MECHANISM, by controlled arithmetic intervention on ONE
     mode of the real `ModalFx`: floor (shipped), round-half-up, a float
     recurrence with the SAME quantized coefficients, and a float recurrence with
     the IDEAL poles. Float-quantized vs float-ideal is the COEFFICIENT error;
     fixed vs float-quantized is the ARITHMETIC error; they are reported apart.

     The prediction that makes it falsifiable comes from the recurrence alone,
     not from a render. With eps = 1 - (a1 + a2) / 2^CF, a state y1 = y2 = y is a
     fixed point of  y' = (a1*y1 + a2*y2 + RND) >> CF  iff

         floor    : y <= 0 and |y| * eps < 1       (ONE-sided, negative, half-width 1/eps)
         rounding : |y| * eps <= 1/2               (two-sided, half-width 1/(2 eps))

     So the stall is a finite-wordlength deadband whose width is 1/eps in state
     LSB, set by how close to z = 1 the pole sits. Floor makes it one-sided and
     twice as wide; ROUNDING DOES NOT REMOVE IT. That refutes the issue's
     title-level hypothesis ("floor without rounding") as the CAUSE, while
     confirming the floor as the reason the pedestal is always negative.

  3. `target` -- the handoff for #350. See `TARGET_BASIS`.

Conditions are predeclared (DEVELOPMENT vs CONFIRMATION below) BEFORE the
intervention results were read; confirmation poles/state sizes are not used to
choose anything.
"""
from __future__ import annotations
import argparse
import json
import math
import os
import subprocess
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import modal_fixed as mf
import drums_fx as dx
from drums_fx import SR

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# ---------------------------------------------------------------- instrument -
T_START_S = 1.2           # tail window starts here (as the historical probe)
MIN_TAIL_S = 0.2          # shorter than this and a ~50 Hz oscillation is unresolved
RES_FRAC = 0.1            # a residual oscillation must be < this x |pedestal|
TAIL_S = 0.3              # the tail window length
TAIL_TAUS = 24            # record length in amplitude time constants (mechanism runs)
ZERO = 1e-9               # relative to peak: below this is numerically zero


class Refused(RuntimeError):
    """A precondition of the apparatus failed. Not a result, and not a fail."""


def _refuse(reason: str, **kw) -> dict:
    return dict(status="REFUSED", reason=reason, pedestal=None, pedestal_db=None, **kw)


GROW, DECAY = 1.02, 0.98  # half-window amplitude ratios that count as growing / still decaying
OFFSET_LEAK = 0.01        # offset differences below 1 % of the oscillation amplitude are fit leakage
MIN_CYCLES = 3            # an oscillation must complete this many cycles in a HALF window


def _dominant_hz(y: np.ndarray, sr: int):
    """Dominant frequency of the detrended tail, or None if it is flat."""
    n = len(y)
    d = y - np.polyval(np.polyfit(np.arange(n) / sr, y, 1), np.arange(n) / sr)
    if float(np.abs(d).max()) == 0.0:
        return None
    sp = np.abs(np.fft.rfft(d * np.hanning(n), 16 * n))
    hz0 = (int(np.argmax(sp[1:])) + 1) * sr / (16 * n)
    # Refine on the residual of the fit itself: an FFT bin is 1/T wide, and a
    # frequency error of a fraction of a bin leaks into the offset of a half window.
    from scipy.optimize import minimize_scalar
    t = np.arange(n) / sr
    bin_hz = sr / n

    def resid(h):
        return _fit(t, y, h, trend=True)[3]
    lo = max(hz0 - bin_hz, bin_hz * 0.5)
    return float(minimize_scalar(resid, bounds=(lo, hz0 + bin_hz), method="bounded",
                                 options=dict(xatol=1e-4)).x)


def _fit(t: np.ndarray, y: np.ndarray, hz, trend: bool = False):
    """Least squares of  c [+ b*t] [+ a*cos + s*sin].  Returns (c, b, amp, resid_rms).
    With hz None the oscillatory terms are dropped (they would be collinear with
    the constant and the fit would cancel a real pedestal against a huge one)."""
    cols = [np.ones_like(t)] + ([t] if trend else [])
    k = len(cols)
    if hz is not None:
        w = 2 * math.pi * hz
        cols += [np.cos(w * t), np.sin(w * t)]
    A = np.column_stack(cols)
    c, *_ = np.linalg.lstsq(A, y, rcond=None)
    r = y - A @ c
    amp = math.hypot(c[k], c[k + 1]) if hz is not None else math.sqrt(2.0) * float(r.std())
    return float(c[0]), float(c[1]) if trend else 0.0, amp, float(r.std())


def tail_pedestal(x, sr: int = SR, t_start: float = T_START_S,
                  res_frac: float = RES_FRAC) -> dict:
    """The DC pedestal of the tail of `x` relative to the record's peak, or a
    REFUSAL. Returns a dict; `status` is "OK" or "REFUSED" and a REFUSED result
    carries no pedestal. Units are those of `x` (LSB if `x` is integer)."""
    x = np.asarray(x, dtype=np.float64)
    if x.ndim != 1:
        return _refuse("not a 1-D record")
    if not np.all(np.isfinite(x)):
        return _refuse("non-finite samples (NaN/Inf) in the record")
    peak = float(np.abs(x).max()) if len(x) else 0.0
    if peak == 0.0:
        return _refuse("silent record: no peak to be relative to")
    i0 = int(t_start * sr)
    tail = x[i0:]
    if len(tail) < int(MIN_TAIL_S * sr):
        return _refuse(f"record too short: {len(tail) / sr:.3f} s of tail after "
                       f"{t_start} s, need {MIN_TAIL_S} s", peak=peak)
    t = np.arange(len(tail)) / sr
    span = t[-1] - t[0]
    zero = ZERO * peak
    hz = _dominant_hz(tail, sr)
    if hz is not None and hz < MIN_CYCLES / (span / 2):
        # Not resolvable in a half window: treat as unresolved unless negligible.
        c0, b0, amp0, rms0 = _fit(t, tail, None)
        if amp0 > zero and amp0 > res_frac * max(abs(c0), zero):
            return _refuse(f"slow component (a ramp, or an oscillation at {hz:.1f} Hz "
                           f"completing < {MIN_CYCLES} cycles in a half window of "
                           f"{span / 2:.3f} s): the tail is still moving, unresolved", peak=peak,
                           osc_hz=hz, osc_amp=amp0)
        hz = None
    amp_all = _fit(t, tail, hz, trend=True)[2]
    h = len(tail) // 2
    c1, _, a_lo, _ = _fit(t[:h], tail[:h], hz)
    c2, _, a_hi, _ = _fit(t[h:], tail[h:], hz)
    ped = c2                                      # the LATEST stationary offset
    drift = abs(c2 - c1)
    info = dict(peak=peak, osc_hz=hz, osc_amp=amp_all, osc_amp_first_half=a_lo,
                osc_amp_second_half=a_hi, drift=drift)
    if a_hi > zero and a_hi > GROW * a_lo:
        return _refuse("tail oscillation is GROWING across the window", **info)
    if a_hi > zero and a_hi < DECAY * a_lo and a_hi > res_frac * max(abs(ped), zero):
        return _refuse("tail is still decaying (oscillation "
                       f"{a_lo:.4g} -> {a_hi:.4g} against a pedestal {ped:.4g}): "
                       "the pedestal is unresolved, not zero", **info)
    if drift > res_frac * max(abs(ped), zero) + OFFSET_LEAK * max(a_lo, a_hi) and drift > zero:
        return _refuse(f"offset not stationary: {c1:.4g} -> {c2:.4g} between window "
                       "halves", **info)
    if abs(ped) <= zero:
        ped = 0.0
    db = 20 * math.log10(abs(ped) / peak) if ped != 0.0 else -math.inf
    return dict(status="OK", reason="", pedestal=ped, pedestal_db=db,
                limit_cycle=bool(a_hi > zero and a_hi > res_frac * abs(ped)), **info)


def naive_pedestal_db(x, sr: int = SR, t_start: float = T_START_S) -> float:
    """The historical estimator (`bd_excitation_probe.body_stats`), kept ONLY as
    the thing the qualification shows to be defeated."""
    x = np.asarray(x, np.float64)
    tail = x[int(t_start * sr):]
    return 20 * math.log10(max(1e-12, abs(float(tail.mean()))) / max(1e-12, np.abs(x).max()))


# ----------------------------------------------------------------- mechanism -
# Predeclared BEFORE the intervention results were read (commit order shows it):
# DEVELOPMENT picked the prediction; CONFIRMATION was run once against it.
DEVELOPMENT = dict(poles=dict(BD=(dx.BD_HZ, dx.bd_decay_q(5.0)), LT=(90.0, 25.0)),
                   state=((28, 15),), levels=(3000, 30000, 300000))
CONFIRMATION = dict(poles=dict(MT=(150.0, 30.0), SUB=(30.0, 40.0), HI=(400.0, 60.0)),
                    state=((28, 15), (32, 19), (24, 11)), levels=(1500, 20000, 200000))


def deadband_halfwidth(a1: int, a2: int, cf: int, rounding: bool) -> float:
    """Half-width, in state LSB, of the fixed-point set y1 = y2 = y (analytic)."""
    eps = 1.0 - (a1 + a2) / (1 << cf)
    return (0.5 if rounding else 1.0) / eps


def _float_ref(exc, a1, a2, n):
    from scipy.signal import lfilter
    return lfilter([1.0], [1.0, -a1, -a2], exc.astype(np.float64))[:n]


def ideal_coefs(f0: float, q: float, fs: int = SR) -> tuple[float, float]:
    r = math.exp(-math.pi * f0 / (q * fs))
    w = 2.0 * math.pi * f0 / fs
    return 2.0 * r * math.cos(w), -r * r


def run_pole(f0: float, q: float, level: int, *, sb: int = 28, sq: int = 15,
             rounding: bool = False, n: int = None, cf: int = 24,
             exc: np.ndarray = None) -> dict:
    """One mode, one ping, four arithmetics. State units: LSB of the Q.SQ state
    (the bank's own, before `amp`). Raises Refused if the run is not valid
    (non-finite, saturated state)."""
    a1, a2 = mf.pole_regs(f0, q, cf)
    tau = q / (math.pi * f0)                 # amplitude time constant, s
    if n is None:                            # long enough that the IDEAL tail is < 1e-8 of its peak
        n = int(max(1.5, TAIL_TAUS * tau + TAIL_S) * SR)
    t_start = n / SR - TAIL_S
    if exc is None:
        exc = np.zeros(n, dtype=np.int64)
        exc[10] = level
    n = len(exc)
    b = mf.ModalFx(coef_frac=cf, state_bits=sb, state_q=sq, modes=1, nums=1,
                   headroom=max(0, 15 - sq), out_bits=sb, rounding=rounding)
    y = np.empty(n)
    sat_lim = (1 << (sb - 1)) - 1
    for t in range(n):
        b.step([int(exc[t])], [(a1, a2, 1 << 16)], [mf.RAW])
        y[t] = b.y1[0]                          # the state itself, not the shifted output
    scale = 2.0 ** (sq - 15)
    yq = _float_ref(exc * scale, a1 / (1 << cf), a2 / (1 << cf), n)
    ai1, ai2 = ideal_coefs(f0, q)
    yi = _float_ref(exc * scale, ai1, ai2, n)
    if not np.all(np.isfinite(y)):
        raise Refused("non-finite state")
    if np.abs(y).max() >= sat_lim:
        raise Refused(f"state saturated ({np.abs(y).max():.0f} >= {sat_lim})")
    out = dict(a1=a1, a2=a2, eps=1.0 - (a1 + a2) / (1 << cf), peak=float(np.abs(y).max()))
    for name, sig in (("fixed", y), ("float_quantized", yq), ("float_ideal", yi)):
        out[name] = tail_pedestal(sig, t_start=t_start)
        out[name + "_tail_rms"] = float(np.sqrt(np.mean(sig[int(t_start * SR):] ** 2)))
    out["tail_fixed"] = y[int(t_start * SR):]
    out["tail_float_quantized"] = yq[int(t_start * SR):]
    out["record_s"] = n / SR
    out["predicted_halfwidth"] = deadband_halfwidth(a1, a2, cf, rounding)
    out["fixed_final"] = float(y[-1])
    return out


def mechanism_table(cfg: dict, rounding: bool) -> list:
    rows = []
    for name, (f0, q) in cfg["poles"].items():
        for sb, sq in cfg["state"]:
            for lev in cfg["levels"]:
                r = run_pole(f0, q, lev, sb=sb, sq=sq, rounding=rounding)
                tail = r.pop("tail_fixed")
                tmin, tmax = float(tail.min()), float(tail.max())
                kind = ("fixed_point" if tmin == tmax else
                        "limit_cycle" if r["fixed"]["status"] == "REFUSED" or r["fixed"].get("limit_cycle")
                        else "offset_with_ripple")
                rows.append(dict(
                    kind=kind, record_s=r["record_s"], peak_over_pedestal=None,
                    float_ideal_tail_rms=r["float_ideal_tail_rms"],
                    float_quantized_tail_rms=r["float_quantized_tail_rms"],
                    pole=name, f0=f0, q=round(q, 3), sb=sb, sq=sq, level=lev,
                    rounding=rounding, eps=r["eps"],
                    predicted_halfwidth_lsb=r["predicted_halfwidth"],
                    fixed_tail_min=tmin, fixed_tail_max=tmax,
                    fixed_pedestal=r["fixed"]["pedestal"],
                    fixed_status=r["fixed"]["status"], fixed_reason=r["fixed"]["reason"],
                    float_quantized_status=r["float_quantized"]["status"],
                    float_quantized_pedestal=r["float_quantized"]["pedestal"],
                    float_ideal_status=r["float_ideal"]["status"],
                    float_ideal_pedestal=r["float_ideal"]["pedestal"],
                    peak=r["peak"]))
    return rows


# ------------------------------------------- properties x interventions -----
MATRIX_POLES = (("BD", dx.BD_HZ, dx.bd_decay_q(5.0)), ("MT", 150.0, 30.0), ("HI", 400.0, 60.0))
MATRIX_LEVELS = (3000, 9000, 30000, 100000)   # where a trajectory lands is not smooth in level,
DEFECTS = {"none (shipped floor)": {}, "rounding": dict(rounding=True),   # so properties are
           "state +4 bits (SB32 SQ19)": dict(sb=32, sq=19),              # taken over the WORST level
           "float recurrence (cause removed)": dict(float_ref=True)}
PROPERTIES = ("pedestal sign always <= 0",
              "worst |pedestal| reaches >= 10 % of the analytic deadband 1/eps",
              "worst relative pedestal improves >= 6 dB vs shipped",
              "tail never decays: some level leaves |y| >= 5 % of 1/eps at 1.5+ s")


def _worst(runs: list):
    """(all signs <= 0, worst |ped| * eps, worst |ped|/peak, worst tail |y| * eps)."""
    ok = [r for r in runs if r["fixed"]["status"] == "OK"]
    ped = [r["fixed"]["pedestal"] for r in ok]
    return (all(v <= 0 for v in ped),
            max(abs(v) * r["eps"] for v, r in zip(ped, ok)),
            max(abs(v) / r["peak"] for v, r in zip(ped, ok)),
            max(float(np.abs(r["tail_fixed"]).max()) * r["eps"] for r in runs))


def property_matrix() -> dict:
    """Each property under each defect/intervention, and whether the defect
    MOVED it relative to the shipped floor or the property is BLIND to it.
    A property that never moves cannot tell the defect from its absence."""
    def one(f, q, lv, kw):
        r = run_pole(f, q, lv, **{k: v for k, v in kw.items() if k != "float_ref"})
        if kw.get("float_ref"):     # the positive control: same coefficients, no integer arithmetic
            r = dict(r, fixed=r["float_quantized"], tail_fixed=r["tail_float_quantized"])
        return r

    runs = {d: {n: [one(f, q, lv, kw) for lv in MATRIX_LEVELS] for n, f, q in MATRIX_POLES}
            for d, kw in DEFECTS.items()}
    base = {n: _worst(r) for n, r in runs["none (shipped floor)"].items()}
    out = {}
    for d in DEFECTS:
        w = {n: _worst(r) for n, r in runs[d].items()}
        val = {PROPERTIES[0]: all(w[n][0] for n in w),
               PROPERTIES[1]: all(w[n][1] >= 0.1 for n in w),
               PROPERTIES[2]: all(w[n][2] <= base[n][2] / 2.0 for n in w),
               PROPERTIES[3]: all(w[n][3] >= 0.05 for n in w)}
        out[d] = val
    ref = out["none (shipped floor)"]
    return {d: {p: dict(value=v[p], verdict="baseline" if d.startswith("none") else
                        ("MOVED" if v[p] != ref[p] else "BLIND")) for p in PROPERTIES}
            for d, v in out.items()}


# -------------------------------------------------------------- BD baseline --
def record_bd(kit: list, accent: float, dur_s: float = 1.5) -> dict:
    """One BD hit through the real write port, recording the BD mode's TRUE
    state (read from the shipped bank, not re-derived), the body bus and the
    final drum mix."""
    d = dx.DrumsFx()
    st, ex, cf, nm = [], [], [], []
    step = d.bank.step

    def hooked(exc, coefs, num):
        ex.append(int(exc[dx.M_BD]))
        cf.append(tuple(coefs[dx.M_BD]))
        nm.append(int(num[dx.M_BD]))
        o = step(exc, coefs, num)
        st.append(d.bank.y1[dx.M_BD])           # y after this step is y1 now
        return o

    d.bank.step = hooked
    n = int(dur_s * SR)
    dmix, body = d.play(dx.hit_writes([(10, dx.BD, accent)], kit), n)
    if len(st) != n:
        raise Refused(f"bank stepped {len(st)} times for {n} frames")
    import drums_fx_render as dr          # the reference gains of the shipped output stage
    final = dx.output_fx(np.zeros(n), 0, dmix, dx.accent_reg(dr.DVOL), body,
                         dx.accent_reg(dr.BVOL)).astype(np.int64)
    return dict(state=np.array(st, dtype=np.int64), body=body.astype(np.int64),
                dmix=np.asarray(dmix, dtype=np.int64), final=final,
                bd_exc=np.array(ex, dtype=np.int64), bd_coefs=cf, bd_num=nm)


def replay_bd_state(rec: dict) -> np.ndarray:
    """The BD mode alone, in a fresh one-mode `ModalFx`, driven by the RECORDED
    excitation and coefficients. Modes do not interact inside the bank (each
    has its own y1/y2/h1/h2), so this must equal the shipped bank's BD state bit
    for bit -- the check that the single-mode apparatus is the thing that ships."""
    b = mf.ModalFx(modes=1, nums=1, headroom=dx.BODY_HR, out_bits=dx.BODY_BITS)
    out = np.empty(len(rec["bd_exc"]), dtype=np.int64)
    for t, (e, c, k) in enumerate(zip(rec["bd_exc"], rec["bd_coefs"], rec["bd_num"])):
        b.step([int(e)], [c], [k])
        out[t] = b.y1[0]
    return out


def baseline_row(sig: np.ndarray, label: str) -> dict:
    r = tail_pedestal(sig, t_start=len(sig) / SR - TAIL_S)
    return dict(bus=label, status=r["status"], reason=r["reason"],
                peak_lsb=float(np.abs(sig).max()),
                pedestal_lsb=r["pedestal"], pedestal_db_rel_peak=r["pedestal_db"],
                osc_residual_lsb=r.get("osc_amp"),
                naive_historical_db=naive_pedestal_db(sig, t_start=T_START_S))


RECORDS_S = (1.5, 3.0, 6.0)   # the historical 1.5 s record, then longer ones for tails it left unresolved


def baseline(accents=(0.5, 0.7, 1.0, 1.4, 2.0), arms=None, records=RECORDS_S) -> list:
    """Per arm x accent x bus. The state bus is the BD mode's own state read from
    the shipped bank. A refused record is retried on a longer one; every attempt
    is kept, so a tail the historical 1.5 s window could not resolve is visible."""
    import bd_excitation_probe as bx
    arms = arms or {"RAW (shipped)": bx.bd_kit()}
    rows = []
    for arm, kit in arms.items():
        for a in accents:
            for dur in records:
                rec = record_bd(kit, a, dur)
                got = [dict(arm=arm, accent=a, record_s=dur, **baseline_row(rec[bus], bus))
                       for bus in ("state", "body", "dmix", "final")]
                rows += got
                if all(g["status"] == "OK" for g in got if g["bus"] in ("state", "body")):
                    break
    return rows


# ------------------------------------------------------------------- target --
BUDGET_LSB = 1.0          # |pedestal| of the FINAL int16 output, in LSB
TARGET_BASIS = (
    "Bound is a DECLARED OUTPUT QUANTIZATION BUDGET, not derived from any "
    "candidate: |DC pedestal| <= 1 LSB of the final int16 output. It is 1 and NOT "
    "0.5 because the output stage is a floor (output_fx: acc >> 15): any NEGATIVE "
    "body pedestal smaller than one LSB is quantized to exactly -1 LSB, so a "
    "0.5-LSB bound would be satisfiable only by an exactly-zero or positive tail "
    "(a first draft used 0.5; it was run against the shipped state and rejected: "
    "see wrong_then_right in the PR). 1 LSB is the floor-quantization floor the "
    "shipped kit already reaches at accents 0.7 and 1.4 and misses by 37x at "
    "0.5, 1.0 and 2.0, so the gate is satisfiable and not already met. It is a "
    "NECESSARY bound only. No audibility threshold for a decaying-tail pedestal "
    "is established: the Fischer TR-808 reference corpus is not present on the "
    "host that produced this record and no listening or reference-noise evidence "
    "is committed. The historical -40.2 dB is a baseline observation, not a target.")


def target(rows: list = None) -> dict:
    """The handoff record. `current_vs_bound` is filled from the baseline rows if
    given: the resolved FINAL-output pedestal per arm and accent, and whether it
    is inside the declared budget. It is the shipped state's standing against the
    bound, not an input to it."""
    cur = []
    for r in rows or []:
        if r["bus"] == "final" and r["status"] == "OK":
            cur.append(dict(arm=r["arm"], accent=r["accent"], record_s=r["record_s"],
                            pedestal_lsb=r["pedestal_lsb"],
                            inside_budget=abs(r["pedestal_lsb"]) <= BUDGET_LSB))
    return dict(current_vs_bound=cur, **_target_core())


def _target_core() -> dict:
    return dict(
        schema=1, issue=220, status="PARTIAL: budget bound only; audibility floor unestablished",
        units=dict(pedestal="LSB of the final int16 output (output_fx at the reference DVOL = BVOL = 0.45)",
                   relative="dB relative to the render peak of the same bus"),
        valid_domain=dict(record_s=">= 1.4 (tail window [1.2, end) of >= 0.2 s)",
                          tail="resolved: no residual decay, growth or drift above 10 % of the pedestal",
                          accents="0.5-2.0 (ACCENTS)"),
        bounds=dict(pedestal_abs_lsb_max=BUDGET_LSB, residual_osc_lsb_max=None,
                    decay_preservation="tau within the shipped kit's, to be set by #350 "
                                       "from tools/probe_tom_numerator.py-style comparison"),
        justification=TARGET_BASIS,
        missing_evidence=["listening or reference evidence for an audible pedestal "
                          "floor on a decaying BD tail (Fischer corpus unavailable here)",
                          "oscillatory-residual bound (limit cycles) -- needs the same"],
        bp_candidate="REMAINS BLOCKED: its measured pedestal and the deadband width "
                     "1/eps are independent of the excitation shape, see mechanism table",
    )


# --------------------------------------------------------------------- main --
def git_state() -> dict:
    def g(*a):
        return subprocess.run(["git", "-C", ROOT, *a], capture_output=True, text=True).stdout.strip()
    return dict(commit=g("rev-parse", "HEAD"), dirty=bool(g("status", "--porcelain", "--untracked-files=no")),
                origin_main=g("rev-parse", "origin/main"))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--out", required=True)
    ap.add_argument("--no-baseline", action="store_true")
    a = ap.parse_args(argv)
    res = dict(provenance=dict(**git_state(), command="python3 model/bd_pedestal.py "
                               + " ".join(argv if argv is not None else sys.argv[1:]),
                               windows=dict(tail="last 0.3 s of each record (1.2-1.5 s for the historical 1.5 s record)", records_s=list(RECORDS_S),
                                            res_frac=RES_FRAC)))
    res["mechanism"] = {k: dict(floor=mechanism_table(cfg, False),
                                rounding=mechanism_table(cfg, True))
                        for k, cfg in (("development", DEVELOPMENT), ("confirmation", CONFIRMATION))}
    res["property_matrix"] = property_matrix()
    if not a.no_baseline:
        import bd_excitation_probe as bx
        amp = bx.calibrate_amp(mf.BP, 1.0, 0)          # historical peak-matched BP candidate
        res["baseline"] = baseline(arms={"RAW (shipped)": bx.bd_kit(),
                                         "BP peak-matched (historical, blocked)": bx.bd_kit(mf.BP, amp, 1.0)})
        res["baseline_bp_amp"] = amp
    res["target"] = target(res.get("baseline"))
    os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
    with open(a.out, "w") as f:
        json.dump(res, f, indent=1, default=lambda o: float(o))
        f.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
