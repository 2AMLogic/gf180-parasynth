#!/usr/bin/env python3
"""The TR-808 bass drum's bridged-T, simulated at circuit level from the
schematic's component values, to derive its pitch trajectory -- the "sigh" --
without fitting anything to a recording (#379).

WHERE EACH PART COMES FROM
  topology and values  docs/tr808-reference.md section 2 (SN p.9, W16 tables)
  bridged-T equations  W14a (Werner, Abel, Smith, DAFx-14) sections 5 and 7,
                       re-derived here as a state-space ODE with V_comm explicit
  pulse shaper         W14a eq. (3)-(4): a low shelf and a diode clamp
  feedback buffer      W14a eq. (6) in its audio band: V_fb = -g V_bt,
                       g = (R169 || k VR6) / R164 (C43 only blocks DC)
  attack shift         W14a 8.1: Q43 shorts R165 while the envelope generator
                       is high, BD_ATTACK_MS (the reference's 4 ms)
  the sigh             W14a 8.2 eq. (8): Q43's collector current against V_comm,
                       i_C = -log(1 + exp(-alpha (V_comm - V0))) m / alpha,
                       alpha 14.315, V0 -0.556 V, m 1.4765e-5 A/V (Werner's fit to
                       SPICE of the stock circuit -- the only constants here not
                       read off the schematic, and not fitted by us); eq. (9) is
                       the foot branch R166 -> V_C -> R165 with i_C drawn at V_C,
                       which KCL reduces to I_foot = (V_comm + R165 i_C)/(R165+R166).
  drive level          Roland's chart (reference 1.6): BD "normal" 3.5 Vpp at the
                       voice output. V_trig is set so V_bt reaches that, once, by
                       bisection on the SIMULATED circuit -- a published
                       measurement of the machine, not the Fischer takes.

DEVIATIONS, stated: the retrigger pulse (C39/R161/D52) and the op-amp clip are
left out -- the first is an amplitude patch at Q43's release, the second is not
reached at 3.5 Vpp. The output stage (tone low-pass, level, 3.4 Hz high-pass) is
after the resonator and does not move its pitch.

STATE: q1 = V_comm - u (across C41), q2 = V_bt - V_comm (across C42), with u the
shaped pulse at the op-amp's input (V- = V+ = u, ideal op-amp):
  node V-   : (V_bt - u)/R167 + C41 dq1/dt = 0
  node V_comm: C42 dq2/dt = C41 dq1/dt + I_foot(V_comm) + (V_comm - V_fb)/R170
                            + V_comm/R161
"""
from __future__ import annotations

import math

import numpy as np

# schematic values, reference section 2
R161, R162, R163, R164 = 1e6, 4.7e3, 100e3, 47e3
R165, R166, R167, R169, R170 = 47e3, 6.8e3, 1e6, 47e3, 470e3
C40, C41, C42 = 0.015e-6, 0.015e-6, 0.015e-6
VR6 = 500e3
# Werner eq. (8)
ALPHA, V0, M = 14.3150, -0.5560, 1.4765e-5
PULSE_S = 1e-3                 # the trigger logic's 1 ms pulse (W14a 3)
ATTACK_S = 4e-3                # Q43 on-time, reference section 2 / SN p.6
CHART_VPP_NORMAL = 3.5         # Roland's chart, reference 1.6
CHART_VPP_ACCENT = 10.0


def g_of_decay(knob_0_10: float) -> float:
    k = max(knob_0_10, 1e-6) / 10.0
    return (R169 * k * VR6 / (R169 + k * VR6)) / R164


def i_c(v):
    return -np.log1p(np.exp(-ALPHA * (v - V0))) * M / ALPHA


def simulate(v_trig: float, decay: float = 5.0, seconds: float = 0.6, fs: int = 48000,
             oversample: int = 16, sigh: bool = True, attack: bool = True, node: str = "bt") -> np.ndarray:
    """V_bt(t) at fs for one trigger of height v_trig volts. Semi-implicit
    (trapezoidal on the linear part, the foot current explicit) at
    fs*oversample -- the fastest time constant is the shaper's 67 us."""
    h = 1.0 / (fs * oversample)
    n = int(seconds * fs * oversample)
    g = g_of_decay(decay)
    # pulse shaper: y = x - k lp(x), lp tau = R162 R163 C40 / (R162 + R163); diode clamp
    k_sh = R163 / (R162 + R163)
    tau_sh = R162 * R163 * C40 / (R162 + R163)
    a_sh = h / (tau_sh + h)
    lp = 0.0
    q1 = q2 = 0.0
    out = np.empty(n)
    for i in range(n):
        t = i * h
        x = v_trig if t < PULSE_S else 0.0
        lp += a_sh * (x - lp)
        y = x - k_sh * lp
        u = y if y >= 0 else 0.71 * (math.exp(y) - 1.0)
        vc = u + q1
        vbt = vc + q2
        if attack and t < ATTACK_S:
            i_foot = vc / R166
        else:
            ic = (-math.log1p(math.exp(-ALPHA * (vc - V0))) * M / ALPHA) if sigh else 0.0
            i_foot = (vc + R165 * ic) / (R165 + R166)
        vfb = -g * vbt
        dq1 = -(q1 + q2) / (R167 * C41)
        dq2 = (C41 * dq1 + i_foot + (vc - vfb) / R170 + vc / R161) / C42
        q1 += h * dq1
        q2 += h * dq2
        out[i] = vbt if node == "bt" else vc
    return out[::oversample]


def v_trig_for_vpp(vpp: float, decay: float = 5.0) -> float:
    """Bisection on the simulated circuit for the drive that gives `vpp` at V_bt."""
    lo, hi = 0.5, 30.0
    for _ in range(30):
        mid = 0.5 * (lo + hi)
        y = simulate(mid, decay, seconds=0.12, oversample=8)
        if y.max() - y.min() < vpp:
            lo = mid
        else:
            hi = mid
    return 0.5 * (lo + hi)


def freq_track(y, fs=48000, hop_s=0.005, seconds=0.4):
    """Instantaneous frequency from zero-crossing periods (positive-going),
    sampled every hop_s. Crude but exact for a slowly varying sinusoid."""
    s = np.sign(y)
    zc = np.nonzero((s[:-1] <= 0) & (s[1:] > 0))[0]
    # sub-sample crossing
    tz = (zc + (-y[zc]) / (y[zc + 1] - y[zc] + 1e-30)) / fs
    per = np.diff(tz)
    tm = 0.5 * (tz[1:] + tz[:-1])
    grid = np.arange(0.0, seconds, hop_s)
    return grid, np.interp(grid, tm, 1.0 / per, left=np.nan, right=np.nan)


if __name__ == "__main__":
    import argparse
    import json
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--decay", type=float, default=5.0)
    ap.add_argument("--out", default=None)
    a = ap.parse_args()
    res = {}
    for lbl, vpp in (("normal", CHART_VPP_NORMAL), ("accent", CHART_VPP_ACCENT)):
        vt = v_trig_for_vpp(vpp, a.decay)
        y = simulate(vt, a.decay)
        t, f = freq_track(y)
        y0 = simulate(vt, a.decay, sigh=False)
        _, f0 = freq_track(y0)
        res[lbl] = {"v_trig": vt, "t_ms": (t * 1e3).round(1).tolist(), "f_hz": np.round(f, 2).tolist(),
                    "f_hz_no_sigh": np.round(f0, 2).tolist()}
        print(lbl, "V_trig %.2f V" % vt, "f(t) every 15 ms:", np.round(f[:40:3], 1))
        print(lbl, "no sigh          :", np.round(f0[:40:3], 1))
    if a.out:
        json.dump(res, open(a.out, "w"), indent=1)


# ---------------------------------------------------------------------------
# the quasi-static law a host can run: excess = h(A), A the V_bt amplitude
# ---------------------------------------------------------------------------
H_REF_VTRIG, H_REF_DECAY = 14.0, 10.0      # one slow, large note spans every amplitude
H_START_S = 0.010                           # after the attack window and its settling


def envelope(y, fs=48000):
    from scipy.signal import hilbert
    return np.abs(hilbert(y))


def sigh_table(fs=48000, points=12):
    """(amplitude V, excess) pairs from one simulated note, and the circuit's
    linear gain G = V_bt peak / V_trig (from a no-sigh note at the same drive).
    excess is f/f_lin - 1 read by zero crossings; amplitude by the analytic
    envelope at the same instants."""
    y = simulate(H_REF_VTRIG, H_REF_DECAY, seconds=1.5)
    lin = simulate(H_REF_VTRIG, H_REF_DECAY, seconds=1.5, sigh=False)
    t, f = freq_track(y, fs, hop_s=0.005, seconds=1.4)
    _, fl = freq_track(lin, fs, hop_s=0.005, seconds=1.4)
    env = envelope(y, fs)
    a = np.interp(t, np.arange(len(env)) / fs, env)
    ok = (t >= H_START_S) & np.isfinite(f) & np.isfinite(fl)
    ex = f[ok] / np.nanmedian(fl[ok]) - 1.0
    amp = a[ok]
    # monotone table on a log-amplitude grid
    grid = np.geomspace(max(amp.min(), 0.05), amp.max(), points)
    order = np.argsort(amp)
    tab = np.interp(grid, amp[order], np.maximum.accumulate(np.clip(ex[order], 0, None)))
    gain = float(np.abs(lin[int(H_START_S * fs):]).max() / H_REF_VTRIG)
    return {"amp_v": grid.round(4).tolist(), "excess": tab.round(5).tolist(), "gain": round(gain, 5),
            "f_lin_hz": float(np.nanmedian(fl[ok]))}


def predict(table, v_trig, tau_s, t):
    a = table["gain"] * v_trig * np.exp(-np.asarray(t) / tau_s)
    return np.interp(np.log(np.maximum(a, 1e-6)), np.log(table["amp_v"]), table["excess"],
                     left=0.0, right=table["excess"][-1])
