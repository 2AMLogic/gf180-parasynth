#!/usr/bin/env python3
"""Does our filter step audibly when the cutoff MOVES?

    .venv/bin/python model/reference_movement.py --stage control
    .venv/bin/python model/reference_movement.py --stage sweep
    .venv/bin/python model/reference_movement.py --stage resonance
    .venv/bin/python model/reference_movement.py --stage audio-rate-mod
    .venv/bin/python model/reference_movement.py --stage plugins --out /tmp/refmove
    .venv/bin/python model/reference_movement.py --stage all --out /tmp/refmove

Every other filter measurement in this repository holds the cutoff STILL.
Zipper noise is by definition a thing that only happens while a control moves,
so none of them can see it -- and the appeal of a Moog filter is largely in
movement: fast envelope sweeps, filter wobble, a hand on the cutoff. This is
issue #46's "movement" criterion, and it had no test at all.

Five measurements, in increasing order of how much can go wrong with them:

  control   EXACT, no audio. Our cutoff control path is `cutoff in integer Hz
            -> g in Q0.16, from a 128-entry ROM read with linear
            interpolation`. Invert the realised g back to an effective cutoff
            and you get the control's resolution in CENTS at every cutoff,
            in closed form. This is the root cause if there is one.
  sweep     DIFFERENTIAL, ours only, and the strongest audio evidence
            available: render the same sweep twice through the same filter,
            once with the shipping integer control path and once with the
            cutoff and g in float. The difference IS the control path's
            contribution -- there is nothing else it can be.
  plugins   DEVICE-INDEPENDENT, all four filters: a steady carrier, the
            cutoff swept across it, and the ripple that survives a high-pass
            of the output's envelope (`audio_measure.envelope_ripple_db`,
            ground-truthed against a staircase of known step size).
  resonance The OTHER moving control (#53): `k` swept CONTINUOUSLY through
            the self-oscillation onset, in both directions, at three rates.
            Envelope ripple, as `plugins`, plus a differential against
            coarser `k` write intervals which is also the START-RED control.
  audio-rate-mod
            Oscillator 3 on the filter-modulation bus (`MR_FILT`) at an
            AUDIO rate (#53), three depths x three rates. The envelope-ripple
            estimator cannot answer here -- it reads the intended modulation,
            not the artefact -- so it REFUSES and the differential answers.

What Surge does, for contrast, read from `sst-filters`
`FilterCoefficientMaker_Impl.h`:

    tC[i] = (1 - smooth) * tC[i] + smooth * N[i];    // smooth = 0.2, per block
    dC[i] = (tC[i] - C[i]) * blockSizeInv;           // then a linear ramp
    ...and per oversampled sub-step:  C[i] += dFac * dC[i];

-- a one-pole low-pass on the coefficient TARGET followed by a linear ramp of
the coefficient across the block. At Surge's 32-sample block and 48 kHz that
first stage is a time constant of about 3 ms (a ~53 Hz corner). Surge
deliberately BAND-LIMITS its cutoff control. Ours has no smoothing of any
kind: the cutoff follows the filter envelope exactly, per sample. Neither is
obviously right -- smoothing costs 3 ms of lag on a fast envelope, which is
audible in a different way -- but it is a choice, and ours has not been made
deliberately.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "..", "audition"))

import audio_measure as am                                          # noqa: E402
import dsp                                                          # noqa: E402
import reference_rigs as rr                                         # noqa: E402
import voice_fx as vf                                               # noqa: E402
from fixed import LadderFx                                          # noqa: E402

SR = rr.SR
FS_OS = SR * 2                     # the ladder's oversampled rate
G_ROM = vf.make_g_rom()
K_ROM = vf.make_k_rom()


# ===========================================================================
# 1. the control path, in closed form
# ===========================================================================
def g_to_hz(g_q16: float) -> float:
    """Invert g = 1 - exp(-2*pi*f/fs_os). The effective cutoff a given
    coefficient actually realises."""
    g = min(max(float(g_q16) / 65536.0, 1e-12), 1 - 1e-12)
    return -math.log(1.0 - g) * FS_OS / (2.0 * math.pi)


def control_resolution(cut_hz: float) -> dict:
    """At this cutoff: the realised cutoff, the static error against the
    commanded one, and the size of ONE step of the control path -- the
    smallest change in commanded Hz that changes the coefficient at all --
    expressed in cents.

    Both quantisations are in here and neither is separable from the other by
    listening: the commanded cutoff is an INTEGER number of hertz (contract
    5.1, `cut_lo`/`cut_hi`/`track_hz` are 16-bit integer Hz) and `g` is an
    INTEGER in Q0.16. At 30 Hz one LSB of g is 0.78 % of g; at 4 kHz it is
    0.02 %. The staircase is therefore coarse at the bottom of the range and
    invisible at the top, which is the opposite of where a test that sweeps
    the top octave would look."""
    c = int(round(cut_hz))
    g = int(vf.g_from_cut(np.array([c]), G_ROM)[0])
    eff = g_to_hz(g)
    # walk up in commanded Hz until g changes: that is one step of the control
    step_hz, gn = 0, g
    while gn == g and step_hz < 4096:
        step_hz += 1
        gn = int(vf.g_from_cut(np.array([c + step_hz]), G_ROM)[0])
    eff_next = g_to_hz(gn)
    cents = 1200.0 * math.log2(max(eff_next, 1e-9) / max(eff, 1e-9)) if gn != g else float("nan")
    return dict(commanded_hz=c, g=g, effective_hz=eff,
                static_error_cents=1200.0 * math.log2(eff / max(c, 1e-9)),
                step_hz=step_hz, step_cents=cents,
                gain_step_db_at_24_db_oct=cents / 1200.0 * 24.0)


def stage_control():
    rows = [control_resolution(c) for c in
            (30, 40, 60, 80, 120, 200, 300, 500, 800, 1200, 2000, 3200, 6400, 10000, 16000)]
    print(f"{'cut Hz':>7} {'g':>6} {'effective':>10} {'static err':>11} "
          f"{'1 step':>8} {'step':>8} {'gain step':>10}")
    print(f"{'':>7} {'Q0.16':>6} {'Hz':>10} {'cents':>11} {'Hz':>8} {'cents':>8} "
          f"{'dB @24/oct':>10}")
    for r in rows:
        print(f"{r['commanded_hz']:7d} {r['g']:6d} {r['effective_hz']:10.2f} "
              f"{r['static_error_cents']:+11.2f} {r['step_hz']:8d} {r['step_cents']:8.2f} "
              f"{r['gain_step_db_at_24_db_oct']:10.3f}")
    return rows


# ===========================================================================
# 2. the differential sweep -- ours, exact
# ===========================================================================
def _regs(res, cut, drive=1.0, compensated=True):
    ref = LadderFx(**vf.LADDER_CFG)
    k, gain, ogain = ref.regs(res, drive)
    if compensated:
        kc = int(vf.kc_from_cut(np.array([int(cut)]), K_ROM)[0])
        k = int(vf.k_effective(k, kc))
    return k, gain, ogain


def sweep_pair(f_carrier: float, lo: float, hi: float, seconds: float,
               res: float = 0.3, amp: float = 0.25):
    """The same exponential cutoff sweep through the same ladder twice:
    the shipping integer control path, and the same sweep with the cutoff and
    `g` left in float. Returns (shipping, smooth, cut_float)."""
    n = int(seconds * SR)
    t = np.arange(n) / SR
    cut_f = lo * (hi / lo) ** (t / seconds)                 # float Hz, per sample
    x = np.round(amp * 32768 * np.sin(2 * math.pi * f_carrier * t)).astype(np.int16)
    k, gain, ogain = _regs(res, math.sqrt(lo * hi))
    # (a) the shipping path: integer Hz, then the Q0.16 ROM read
    cut_i = np.clip(np.round(cut_f), 30, 21600).astype(np.int64)
    g_i = vf.g_from_cut(cut_i, G_ROM)
    kc = vf.kc_from_cut(cut_i, K_ROM)
    k_eff = vf.k_effective(np.full(n, _regs(res, 1000, compensated=False)[0]), kc)
    a = LadderFx(**vf.LADDER_CFG).process(x, None, res, 1.0, g_q16=g_i,
                                          k=None, gain=gain, ogain=ogain, k_q14=k_eff)
    # (b) the smooth reference: the identical filter, float cutoff, float g
    b = LadderFx(**vf.LADDER_CFG).process(x, cut_f, res, 1.0,
                                          k=None, gain=gain, ogain=ogain, k_q14=k_eff)
    return np.asarray(a, float), np.asarray(b, float), cut_f


def stage_sweep(seconds_list=(0.1, 0.4, 1.6, 4.0)):
    rows = []
    print(f"{'sweep':>7} {'octaves/s':>10} {'range Hz':>13} {'residual':>10} "
          f"{'ripple ours':>12} {'ripple smooth':>14} {'rate':>8}")
    for lo, hi in ((60.0, 960.0), (500.0, 8000.0)):
        for s in seconds_list:
            a, b, _ = sweep_pair(2000.0 if lo > 100 else 220.0, lo, hi, s)
            resid = am.db(am.rms(a - b), am.rms(b))
            lp = (220.0 if lo < 100 else 800.0)
            ea = am.analytic_envelope(a)
            eb = am.analytic_envelope(b)
            ra = am.envelope_ripple_db(ea, SR, lp_hz=lp)
            rb = am.envelope_ripple_db(eb, SR, lp_hz=lp)
            r = dict(lo=lo, hi=hi, seconds=s, oct_per_s=math.log2(hi / lo) / s,
                     residual_db=resid,
                     ripple_shipping_db=ra.value if ra.ok else None,
                     ripple_smooth_db=rb.value if rb.ok else None,
                     ripple_rate_hz=ra.detail.get("ripple_rate_hz") if ra.ok else None)
            rows.append(r)
            print(f"{s:7.2f} {r['oct_per_s']:10.1f} {lo:6.0f}-{hi:<6.0f} {resid:10.1f} "
                  f"{(ra.value if ra.ok else float('nan')):12.1f} "
                  f"{(rb.value if rb.ok else float('nan')):14.1f} "
                  f"{(r['ripple_rate_hz'] or float('nan')):8.0f}", flush=True)
    return rows


def stage_injected():
    """START RED. Coarsen the cutoff control deliberately -- round the
    commanded cutoff to 32 Hz -- and the differential must rise by roughly the
    ratio of the step sizes. If it does not, the measurement has no power and
    nothing above it means anything."""
    print("  injected: the commanded cutoff rounded to 32 Hz steps")
    out = []
    for lo, hi, s in ((60.0, 960.0, 0.1), (500.0, 8000.0, 0.1)):
        n = int(s * SR)
        t = np.arange(n) / SR
        cut_f = lo * (hi / lo) ** (t / s)
        x = np.round(0.25 * 32768 * np.sin(2 * math.pi * (220.0 if lo < 100 else 2000.0)
                                           * t)).astype(np.int16)
        _, gain, ogain = _regs(0.3, math.sqrt(lo * hi))
        k_eff = vf.k_effective(np.full(n, _regs(0.3, 1000, compensated=False)[0]),
                               vf.kc_from_cut(np.clip(np.round(cut_f), 30, 21600).astype(np.int64),
                                              K_ROM))
        ref = LadderFx(**vf.LADDER_CFG).process(x, cut_f, 0.3, 1.0, k=None, gain=gain,
                                                ogain=ogain, k_q14=k_eff)
        res = {}
        for tag, cq in (("shipping (1 Hz)", 1), ("injected (32 Hz)", 32)):
            ci = np.clip(np.round(cut_f / cq) * cq, 30, 21600).astype(np.int64)
            y = LadderFx(**vf.LADDER_CFG).process(x, None, 0.3, 1.0,
                                                  g_q16=vf.g_from_cut(ci, G_ROM),
                                                  k=None, gain=gain, ogain=ogain, k_q14=k_eff)
            res[tag] = am.db(am.rms(np.asarray(y, float) - np.asarray(ref, float)),
                             am.rms(np.asarray(ref, float)))
        out.append(dict(lo=lo, hi=hi, **{k.split()[0]: v for k, v in res.items()}))
        print(f"    {lo:5.0f}-{hi:<6.0f} Hz:  " +
              "   ".join(f"{k} {v:+7.1f} dB" for k, v in res.items()) +
              f"    separation {res['injected (32 Hz)'] - res['shipping (1 Hz)']:+.1f} dB")
    return out


# ===========================================================================
# 3. all four filters, device-independent
# ===========================================================================
CARRIER = 2000.0
SWEEP_LO, SWEEP_HI = 500.0, 8000.0
LP_HZ = 800.0          # below the carrier: excludes the analytic envelope's own
                       # 2*f0 ripple, which read as -32 dB of 'stepping' that was
                       # not there. See audio_measure.envelope_ripple_db.
RATES_S = (0.4, 1.6, 4.0)   # slow enough that the step rate lands under LP_HZ


def stage_plugins(devices, cache):
    rows = []
    for name in devices:
        try:
            dev = _build(name)
        except Exception as e:                                       # noqa: BLE001
            print(f"  {name}: NOT AVAILABLE -- {e}", flush=True)
            continue
        for s in RATES_S:
            try:
                y = dev_sweep(dev, name, CARRIER, SWEEP_LO, SWEEP_HI, s, cache)
            except NotImplementedError as e:
                print(f"  {name}: NOT ANSWERABLE -- {e}", flush=True)
                break
            env = am.analytic_envelope(y)
            e = am.envelope_ripple_db(env, SR, lp_hz=LP_HZ)
            r = dict(device=name, seconds=s,
                     oct_per_s=math.log2(SWEEP_HI / SWEEP_LO) / s,
                     ripple_db=e.value if e.ok else None,
                     host_block=getattr(dev, "block", None),
                     ripple_rate_hz=e.detail.get("ripple_rate_hz") if e.ok else None,
                     why=None if e.ok else e.reason)
            rows.append(r)
            print(f"  {name:12s} {s:5.2f} s ({r['oct_per_s']:5.1f} oct/s)  ripple "
                  f"{(e.value if e.ok else float('nan')):7.1f} dB  at "
                  f"{(r['ripple_rate_hz'] or float('nan')):6.0f} Hz", flush=True)
        del dev
    return rows


# Parameter automation is applied per HOST BLOCK. At dawdreamer's default 512
# samples that is 93.75 Hz, and a swept cutoff then moves in 93.75 Hz steps --
# which is exactly what the first run measured on all three plugins, at
# identical 94 Hz, while ours (which moves per sample) sat at the floor. That
# was the harness stepping, not the plugins. 16 samples puts the automation
# rate at 3 kHz, well above the analysis band.
PLUGIN_BLOCK = 16


def _build(name, block=PLUGIN_BLOCK):
    if name == "ours":
        return None
    return {"surge-huov": lambda: rr.SurgeRig("Type 2", block=block),
            "surge-rk": lambda: rr.SurgeRig("Type 1", block=block),
            "miniv3": lambda: rr.MiniV3Rig(block=block),
            "diva": lambda: rr.DivaRig("rough", block=block)}[name]()


def dev_sweep(dev, name, carrier, lo, hi, seconds, cache):
    """A steady carrier through a cutoff swept exponentially from lo to hi."""
    if name == "ours":
        a, _, _ = sweep_pair(carrier, lo, hi, seconds)
        return a / 32768.0
    return dev.swept_cutoff(carrier, lo, hi, seconds, cache)


# ===========================================================================
# 4. the resonance swept CONTINUOUSLY through the self-oscillation threshold
#
# `model/test_moog_acceptance.py::test_a_cutoff_jump_mid_note_does_not_click`
# already steps `k_eff` between 0 and the onset as a 5 ms SQUARE WAVE. That is
# a pair of abrupt toggles; #53 asks for the other case, "sweeping resonance
# through the self-oscillation threshold", which excites the crossing
# differently -- the loop passes through unity gain slowly, and whatever the
# control does while it is there is audible for as long as it takes.
# ===========================================================================
RES_CUT_HZ = 2000                  # the cutoff is held STILL; only k moves
RES_CARRIER = 2000.0               # on the cutoff: see _res_carrier below
RES_SPAN = (0.6, 1.4)              # res; onset is res = 1.000 by construction
RES_BELOW = (0.1, 0.9)             # the same SPAN and rate, never reaching onset
RES_RATES_S = (0.2, 0.8, 3.2)
RES_HOLDS = (1, 96, 240)           # frames between k writes: shipping, 2 ms, 5 ms
RES_LP_HZ = 800.0                  # the SAME band as section 3, so the numbers compare
RES_MIN_SEP_DB = 6.0               # the START-RED bar; measured separation is ~24 dB


def onset_res(cut_hz: int = RES_CUT_HZ) -> float:
    """The `res` at which the compensated loop gain reaches 1 at this cutoff.

    k_eff = (4*res*2^14 * kc) >> 15 and kc = k_onset/4 in Q1.15 (DR 0006), so
    this is 1.000 by construction -- which is the point of the compensation
    ROM. Computed rather than assumed, because a measurement that claims to
    cross a threshold has to know where the threshold is."""
    k_on, _ = vf.k_onset(int(cut_hz), G_ROM)
    kc = int(vf.kc_from_cut(np.array([int(cut_hz)]), K_ROM)[0])
    return k_on * 32768.0 / (4.0 * kc)


def res_ramp_k(lo: float, hi: float, n: int, hold: int = 1) -> np.ndarray:
    """The per-frame `k` register for a linear resonance ramp -- the host's
    `4*res` in Q3.14, clamped to the 17-bit port (contract 5.1 / 5.5).

    `hold` is the number of frames between host writes. 1 is what ships (the
    chip's k port is per-frame); anything larger is the injected control."""
    res = np.linspace(lo, hi, n)
    if hold > 1:
        res = res[(np.arange(n) // hold) * hold]
    return np.clip(np.round(4.0 * res * (1 << 14)), 0,
                   (1 << LadderFx.K_BITS) - 1).astype(np.int64)


def resonance_render(lo: float, hi: float, seconds: float, hold: int = 1,
                     cut: int = RES_CUT_HZ, amp: float = 0.05,
                     mute_from: float = None) -> np.ndarray:
    """A steady carrier through a fixed cutoff while `k` ramps lo -> hi.

    `mute_from`: silence the input from this fraction of the render onward,
    which is how the self-oscillation is told apart from a resonant peak."""
    n = int(seconds * SR)
    t = np.arange(n) / SR
    x = np.round(amp * 32768 * np.sin(2 * math.pi * RES_CARRIER * t)).astype(np.int16)
    if mute_from is not None:
        x[int(mute_from * n):] = 0
    g = int(vf.g_from_cut(np.array([cut]), G_ROM)[0])
    kc = int(vf.kc_from_cut(np.array([cut]), K_ROM)[0])
    k_eff = vf.k_effective(res_ramp_k(lo, hi, n, hold), np.full(n, kc))
    _, gain, ogain = LadderFx(**vf.LADDER_CFG).regs(1.0, 1.6)
    y = LadderFx(**vf.LADDER_CFG).process(x, None, 1.0, 1.6,
                                          g_q16=np.full(n, g, dtype=np.int64),
                                          gain=gain, ogain=ogain, k_q14=k_eff)
    return np.asarray(y, float)


def ripple_verdict(e, lp_hz: float, *, avoid_hz: float = None, hp_hz: float = 40.0):
    """`envelope_ripple_db` with its own validity conditions ASSERTED rather
    than assumed, because this estimator has already produced two wrong
    answers in this repository (the 94 Hz host-block artefact of section 3,
    and the -32 dB Hilbert carrier residual before it was band-limited).

    Returns (value, verdict) with verdict one of:
      'ok'              the dominant residual rate is inside the analysis band
      'floor(carrier)'  the dominant rate is at or above the band limit, so
                        what dominates is the analytic envelope's own carrier
                        residual: the value is an UPPER BOUND on stepping
      'floor(ramp)'     the dominant rate is close to the high-pass corner, so
                        what dominates is the control's own trajectory leaking
                        through the high-pass: also an upper bound
      'REFUSED'         the estimator is reading something that is not an
                        artefact -- most importantly the INTENDED modulation,
                        which at audio rate is 40 dB larger than anything the
                        control path could add."""
    if not e.ok:
        return None, "REFUSED: " + e.reason
    r = e.detail.get("ripple_rate_hz", float("nan"))
    if avoid_hz:
        for h in (1, 2, 3):
            if abs(r - h * avoid_hz) < 0.15 * avoid_hz:
                return None, f"REFUSED: reads the intended modulation ({r:.0f} Hz)"
    if not math.isfinite(r) or r >= lp_hz:
        return e.value, "floor(carrier)"
    if r < 2.0 * hp_hz:
        return e.value, "floor(ramp)"
    return e.value, "ok"


def _res_row(lo, hi, seconds, hold):
    y = resonance_render(lo, hi, seconds, hold)
    e = am.envelope_ripple_db(am.analytic_envelope(y), SR, lp_hz=RES_LP_HZ)
    v, verdict = ripple_verdict(e, RES_LP_HZ)
    return dict(res_lo=lo, res_hi=hi, seconds=seconds, hold_frames=hold,
                hold_hz=SR / hold, ripple_db=v, verdict=verdict,
                ripple_rate_hz=e.detail.get("ripple_rate_hz") if e.ok else None), y


def stage_resonance():
    """START RED FIRST, then the table. Nothing here is printed as a number
    until the injected control has been shown to move it."""
    r_on = onset_res()
    print(f"  onset at res {r_on:.4f}; the ramp spans {RES_SPAN[0]} .. {RES_SPAN[1]}")
    if not RES_SPAN[0] < r_on < RES_SPAN[1]:
        print("  REFUSED: the ramp does not cross the onset")
        return dict(refused="the ramp does not cross the onset")

    # apparatus precondition: above the onset the filter has to SING, or this
    # is a measurement of a resonant peak and not of a threshold crossing.
    y = resonance_render(*RES_SPAN, 0.8, mute_from=0.5)
    env = am.analytic_envelope(y)
    n = len(env)
    grew = env[int(0.95 * n):].mean() / max(env[int(0.55 * n):int(0.6 * n)].mean(), 1e-9)
    print(f"  with the input muted at half way the envelope still grows "
          f"{20 * math.log10(grew):+.1f} dB: it self-oscillates")
    if grew <= 1.0:
        print("  REFUSED: the ramp never reaches self-oscillation")
        return dict(refused="the ramp never reaches self-oscillation")

    print("\n  START RED -- k written every N frames instead of every frame "
          "(0.8 s ramp, upward)")
    ctl, base = _res_row(*RES_SPAN, 0.8, 1)
    controls = [ctl]
    print(f"    {'write':>8} {'rate Hz':>8} {'ripple dB':>10} {'verdict':>14} "
          f"{'at Hz':>7} {'residual vs shipping':>21}")
    print(f"    {'1 (ship)':>8} {SR:8.0f} {ctl['ripple_db']:10.1f} {ctl['verdict']:>14} "
          f"{(ctl['ripple_rate_hz'] or float('nan')):7.0f} {'--':>21}")
    for hold in RES_HOLDS[1:]:
        row, y = _res_row(*RES_SPAN, 0.8, hold)
        row["residual_db"] = am.db(am.rms(y - base), am.rms(base))
        row["separation_db"] = row["ripple_db"] - ctl["ripple_db"]
        controls.append(row)
        print(f"    {hold:8d} {row['hold_hz']:8.0f} {row['ripple_db']:10.1f} "
              f"{row['verdict']:>14} {(row['ripple_rate_hz'] or float('nan')):7.0f} "
              f"{row['residual_db']:16.1f} dB")
    sep = max(r["separation_db"] for r in controls[1:])
    print(f"    separation {sep:+.1f} dB (bar {RES_MIN_SEP_DB:.0f} dB)")
    if sep < RES_MIN_SEP_DB:
        print("  REFUSED: the injected control did not move the measure")
        return dict(refused="injected control below the separation bar", controls=controls)

    print("\n  the shipping path, both directions, three rates")
    print(f"    {'ramp':>13} {'seconds':>8} {'res/s':>7} {'ripple dB':>10} "
          f"{'verdict':>14} {'at Hz':>7}")
    rows = []
    for lo, hi in (RES_SPAN, RES_SPAN[::-1], RES_BELOW):
        tag = f"{lo} -> {hi}"
        for s in RES_RATES_S:
            row, _ = _res_row(lo, hi, s, 1)
            row["crosses_onset"] = min(lo, hi) < r_on < max(lo, hi)
            rows.append(row)
            print(f"    {tag:>13} {s:8.2f} {abs(hi - lo) / s:7.2f} "
                  f"{row['ripple_db']:10.1f} {row['verdict']:>14} "
                  f"{(row['ripple_rate_hz'] or float('nan')):7.0f}", flush=True)
    return dict(onset_res=r_on, self_oscillation_growth_db=20 * math.log10(grew),
                controls=controls, separation_db=sep, rows=rows)


# ===========================================================================
# 5. oscillator 3 on the filter-modulation bus, at an AUDIO rate
#
# `test_the_mod_wheel_at_full_sweeps_the_cutoff_from_440_to_at_least_2400`
# already drives `MR_FILT` from oscillator 3, but as a square-wave LFO: it
# checks the DEPTH of the swing, at a rate where the cutoff is essentially
# static between edges. #53 asks the harder question -- the same bus at an
# audio rate, "far harder than a hand on a knob".
#
# The trajectory here comes out of `VoiceFx` itself (`trace['cut']`), not out
# of a reimplementation of the modulation arithmetic, because the thing being
# measured has to be the thing that ships.
# ===========================================================================
MOD_CUT_HZ = 2000                  # cut_lo == cut_hi, track 0: the base is exact
MOD_CARRIER = 2000.0               # the same carrier as section 3
MOD_DEPTHS = (0.25, 1.30, 3.90)    # octaves; 1.30 is MFD_REF_OCT, 3.90 is at the clamp
MOD_RATES = (110.0, 440.0, 1760.0)  # oscillator 3, in the audio band
MOD_SECONDS = 0.3
MOD_QUANT = (1, 8, 32)             # injected: the modulated cutoff rounded to N Hz
MOD_BLOCK = (1, 128, 512)          # injected: the cutoff held for N frames
MOD_LP_HZ = 800.0
MOD_MIN_SEP_DB = 6.0


def mod_trajectory(depth_oct: float, f3: float, seconds: float = MOD_SECONDS):
    """Run the shipping voice with oscillator 3 on `MR_FILT` at `f3`, and
    return (cut_shipping, cut_float, regs).

    `cut_shipping` is `trace['cut']` -- the integer-hertz cutoff the ladder
    actually ran on. `cut_float` is the SAME modulation with the arithmetic
    left in float: the Q3.12 octave word, the interpolated exp ROM and the
    integer-hertz register all removed, and nothing else. The difference
    between renders of the two is the control path's contribution, exactly as
    in section 2.

    Asserts, because both have been wrong before somewhere in this file:
      * the float reconstruction tracks the shipping register to within the
        control path's own resolution -- one hertz of register truncation
        plus a part in a thousand for the Q3.12 octave word and the exp ROM
        -- so it is the same modulation and not a different one. The bound is
        relative because the modulated cutoff reaches 21.6 kHz at the deepest
        depth, where a part in a thousand IS 22 Hz. A wrong reconstruction
        misses by hundreds of hertz, not by tens;
      * the realised cutoff actually MOVED by more than the commanded depth,
        so a clamped or mis-routed bus cannot read as a clean result."""
    regs = vf.VoiceFx.patch_regs(
        waves=("sine", "saw", "tri"), detune=(0.0, 0.0, 0.0), mix=(1.0, 0.0, 0.0),
        cutoff=(MOD_CUT_HZ, MOD_CUT_HZ), q=0.3, drive=1.0, track=0.0,
        amp=(0.001, 0.001, 1.0, 0.001), fenv=(0.001, 0.001, 1.0, 0.001),
        filt_mod=True, osc3_ctl=False, mod_wheel=1.0, mod_mix=0.0,
        mod_filter=depth_oct)
    v = vf.VoiceFx()
    n = int(seconds * SR)
    v.play(regs, [(0, "INC", 0, dsp.phase_inc(MOD_CARRIER), True),
                  (0, "INC", 2, dsp.phase_inc(f3), True),
                  (0, "TRACK", 0), (0, "GATE", 1)], n)
    t = v.trace
    cut = np.asarray(t["cut"], dtype=np.int64)
    amt = np.clip((np.asarray(t["mod_sig"], dtype=np.int64) * regs["mwheel"]) >> 15,
                  -32768, 32767)
    oct_f = amt * regs["mfd"] / 32768.0 / (1 << vf.OCT_Q)
    cut_f = np.clip(MOD_CUT_HZ * 2.0 ** oct_f, vf.CUT_MIN, vf.CUT_MAX)
    skew = np.abs(cut - np.round(cut_f))
    bound = 1.5 + 1e-3 * cut_f
    assert bool(np.all(skew <= bound)), (
        f"the float reference is not the same modulation: worst "
        f"{skew.max():.0f} Hz against a {bound[int(np.argmax(skew - bound))]:.0f} Hz bound")
    span = math.log2(cut.max() / cut.min())
    assert span > depth_oct, (f"the bus moved {span:.2f} octaves for a commanded "
                              f"+-{depth_oct:.2f}: clamped or not routed")
    return cut, cut_f, regs


def mod_pair(cut: np.ndarray, cut_f: np.ndarray, *, quant: int = 1, hold: int = 1,
             amp: float = 0.25):
    """The same carrier through the same ladder twice: the shipping integer
    control path (optionally coarsened) and the float one. `k` is held
    identical between the two, as in `sweep_pair`, so the differential is the
    cutoff path and nothing else."""
    n = len(cut)
    t = np.arange(n) / SR
    x = np.round(amp * 32768 * np.sin(2 * math.pi * MOD_CARRIER * t)).astype(np.int16)
    ci = cut
    if hold > 1:
        ci = ci[(np.arange(n) // hold) * hold]
    if quant > 1:
        ci = np.clip(np.round(ci / quant) * quant, vf.CUT_MIN, vf.CUT_MAX).astype(np.int64)
    k_reg, gain, ogain = LadderFx(**vf.LADDER_CFG).regs(0.3, 1.0)
    k_eff = vf.k_effective(np.full(n, k_reg), vf.kc_from_cut(cut, K_ROM))
    a = LadderFx(**vf.LADDER_CFG).process(x, None, 0.3, 1.0,
                                          g_q16=vf.g_from_cut(ci, G_ROM),
                                          gain=gain, ogain=ogain, k_q14=k_eff)
    b = LadderFx(**vf.LADDER_CFG).process(x, cut_f, 0.3, 1.0,
                                          gain=gain, ogain=ogain, k_q14=k_eff)
    return np.asarray(a, float), np.asarray(b, float)


def mod_attribution(cut: np.ndarray, cut_f: np.ndarray):
    """Split the differential between the two quantisers the modulation path
    puts in series, by rendering the intermediate that has one and not the
    other: `round(cut_f)` is the exact octave and exact exponential, landed in
    the same 16-bit integer-hertz register the shipping path uses.

        shipping -> mid   the Q3.12 octave word and the interpolated exp ROM
        mid -> float      the integer-hertz register and the g ROM

    Section 3's cutoff-sweep differential only ever contained the second of
    these. The modulation bus adds the first, and nothing had measured it."""
    mid = np.clip(np.round(cut_f), vf.CUT_MIN, vf.CUT_MAX).astype(np.int64)
    a, b = mod_pair(cut, cut_f)
    m, _ = mod_pair(mid, cut_f)
    ref = am.rms(b)
    return dict(total_db=am.db(am.rms(a - b), ref),
                exp_path_db=am.db(am.rms(a - m), ref),
                hz_path_db=am.db(am.rms(m - b), ref))


def stage_audio_rate_mod():
    """START RED FIRST, then the grid. Two injections, because they break the
    control path in different places: rounding the modulated cutoff (a
    coarser register) and holding it across a block (a slower host)."""
    cut, cut_f, regs = mod_trajectory(1.30, 440.0)
    base_a, base_b = mod_pair(cut, cut_f)
    base = am.db(am.rms(base_a - base_b), am.rms(base_b))
    print(f"  the bus: cutoff {cut.min()}-{cut.max()} Hz, "
          f"{math.log2(cut.max() / cut.min()):.2f} octaves, from oscillator 3 at 440 Hz")
    print("\n  START RED -- 1.30 oct at 440 Hz, differential against the float control path")
    print(f"    {'injection':>22} {'residual dB':>12} {'separation':>11}")
    print(f"    {'shipping':>22} {base:12.1f} {'--':>11}")
    controls = [dict(injection="shipping", residual_db=base, separation_db=0.0)]
    for q in MOD_QUANT[1:]:
        a, b = mod_pair(cut, cut_f, quant=q)
        r = am.db(am.rms(a - b), am.rms(b))
        controls.append(dict(injection=f"cutoff rounded to {q} Hz",
                             residual_db=r, separation_db=r - base))
        print(f"    {f'cutoff to {q} Hz':>22} {r:12.1f} {r - base:+10.1f} dB")
    for h in MOD_BLOCK[1:]:
        a, b = mod_pair(cut, cut_f, hold=h)
        r = am.db(am.rms(a - b), am.rms(b))
        controls.append(dict(injection=f"cutoff held {h} frames",
                             residual_db=r, separation_db=r - base))
        print(f"    {f'held {h} frames ({SR // h} Hz)':>22} {r:12.1f} {r - base:+10.1f} dB")
    sep = max(c["separation_db"] for c in controls[1:])
    print(f"    separation {sep:+.1f} dB (bar {MOD_MIN_SEP_DB:.0f} dB)")
    if sep < MOD_MIN_SEP_DB:
        print("  REFUSED: the injected controls did not move the measure")
        return dict(refused="injected controls below the separation bar", controls=controls)

    print("\n  the shipping path: depth x rate")
    print(f"    {'depth oct':>9} {'osc3 Hz':>8} {'cutoff Hz':>13} {'residual dB':>12} "
          f"{'ripple dB':>10}  verdict")
    rows = []
    for depth in MOD_DEPTHS:
        for f3 in MOD_RATES:
            c, cf, _ = mod_trajectory(depth, f3)
            a, b = mod_pair(c, cf)
            resid = am.db(am.rms(a - b), am.rms(b))
            e = am.envelope_ripple_db(am.analytic_envelope(a), SR, lp_hz=MOD_LP_HZ)
            v, verdict = ripple_verdict(e, MOD_LP_HZ, avoid_hz=f3)
            # The decisive precondition, and it needs no judgement: run the
            # SAME estimator on the float-control render, which by
            # construction has no control-path artefact in it at all. If that
            # reads the same number, the estimator is measuring something the
            # two renders share -- the intended modulation -- and not the
            # artefact. `avoid_hz` catches the first three harmonics; this
            # catches the intermodulation products it cannot enumerate.
            ef = am.envelope_ripple_db(am.analytic_envelope(b), SR, lp_hz=MOD_LP_HZ)
            if v is not None and ef.ok and abs(ef.value - v) < 3.0:
                verdict = (f"REFUSED: the artefact-free render reads the same "
                           f"({ef.value:.1f} dB)")
                v = None
            rows.append(dict(depth_oct=depth, mod_hz=f3, cut_lo=int(c.min()),
                             cut_hi=int(c.max()), residual_db=resid,
                             ripple_db=v, ripple_verdict=verdict,
                             ripple_float_db=ef.value if ef.ok else None,
                             ripple_rate_hz=e.detail.get("ripple_rate_hz") if e.ok else None))
            print(f"    {depth:9.2f} {f3:8.0f} {c.min():6d}-{c.max():<6d} {resid:12.1f} "
                  f"{('--' if v is None else f'{v:10.1f}'):>10}  {verdict}", flush=True)

    print("\n  where the differential comes from (1.30 oct at 440 Hz)")
    attr = mod_attribution(cut, cut_f)
    print(f"    total {attr['total_db']:.1f} dB = octave word + exp ROM "
          f"{attr['exp_path_db']:.1f} dB, integer-Hz register + g ROM "
          f"{attr['hz_path_db']:.1f} dB")
    return dict(controls=controls, separation_db=sep, rows=rows, attribution=attr)


# ===========================================================================
def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--stage", default="control",
                    choices=["control", "sweep", "injected", "resonance",
                             "audio-rate-mod", "plugins", "all"])
    ap.add_argument("--devices", default="ours,surge-huov,surge-rk,miniv3,diva")
    ap.add_argument("--out", default="/tmp/refmove")
    a = ap.parse_args(argv)
    os.makedirs(a.out, exist_ok=True)
    cp = os.path.join(a.out, "knobs.json")
    cache = json.load(open(cp)) if os.path.exists(cp) else {}
    stages = (["control", "sweep", "injected", "resonance", "audio-rate-mod", "plugins"]
              if a.stage == "all" else [a.stage])
    out = {}
    if "control" in stages:
        print("\n== 1. our cutoff control path, in closed form ==")
        out["control"] = stage_control()
    if "sweep" in stages:
        print("\n== 2. the differential sweep: shipping control path vs the same filter "
              "with a float one ==")
        out["sweep"] = stage_sweep()
    if "injected" in stages:
        print("\n== 3. START RED ==")
        out["injected"] = stage_injected()
    if "resonance" in stages:
        print("\n== 4. the resonance swept continuously through self-oscillation onset ==")
        out["resonance"] = stage_resonance()
    if "audio-rate-mod" in stages:
        print("\n== 5. oscillator 3 on the filter-modulation bus, at audio rate ==")
        out["audio_rate_mod"] = stage_audio_rate_mod()
    if "plugins" in stages:
        print(f"\n== 6. all filters, envelope ripple, carrier {CARRIER:.0f} Hz, "
              f"cutoff {SWEEP_LO:.0f}-{SWEEP_HI:.0f} Hz ==")
        out["plugins"] = stage_plugins([d for d in a.devices.split(",") if d], cache)
    for k, v in out.items():
        json.dump(v, open(os.path.join(a.out, f"{k}.json"), "w"), indent=1, default=str)
    json.dump(cache, open(cp, "w"), indent=1)
    return 0


if __name__ == "__main__":
    sys.exit(main())
