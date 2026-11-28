"""Part 3: confirm the offline sweep's answers on the REAL fixed-point block,
as ordinary register writes -- no new mode, no new path, no new numerator
hardware -- and check that nothing else the cymbal is tested on moves.
"""
from __future__ import annotations
_ROOT = __import__("pathlib").Path(__file__).resolve().parents[3]
import json, os, sys

WT = str(_ROOT)
SCRATCH = ("/private/tmp/claude-501/-Users-joseph-dev-2amlogic/"
           "b50207d1-0443-45b1-85ae-6193fbb61d1b/scratchpad")
sys.path.insert(0, os.path.join(WT, "model"))
sys.path.insert(0, os.path.join(WT, "audition"))
sys.path.insert(0, SCRATCH)

import numpy as np
import audio_measure as am
import drums_fx as dx
from dsp import SR
from modal_fixed import RAW, BP, HP
from hh_probe import (say, render, fast_band_energy, fmt, dfmt, cost, gate,
                      CY_BANDS, HAT_BANDS, HW_CY, HW_CH, HW_OH)
from hh_probe2 import trim_onset

OUT: dict = {}
HW_CY_T20, HW_CY_PEAK = 0.798, 3153.0

def retuned_cy(q_hi=4.0, low_gain=2.0):
    img = dict(dx.kit_with_sounds("CY"))
    for a, v in dx.mode_writes(dx.M_CYHI, dx.CY_HI_HZ, q_hi, dx.AMP_CY_HI, BP):
        img[a] = v
    for a, v in dx.env_writes(dx.E_CYL, dx.CY, dx.CY_TAU_LOW, dx.PEAK_CYL*low_gain):
        img[a] = v
    return sorted(img.items())

def retuned_hat(name, f0, q, num, amp):
    mode = dx.M_CHHP if name == "CH" else dx.M_OHHP
    img = dict(dx.kit_with_sounds(name))
    for a, v in dx.mode_writes(mode, f0, q, amp, num):
        img[a] = v
    return sorted(img.items())

def main():
    say("== PART 3: the offline sweep's answers, run on the fixed-point block ==")
    say("   Register writes only. N_MODES stays 16, N_NUMS stays 11, N_PATH 23.")

    # ---------------- CY ----------------------------------------------------
    _, x0 = render(dx.kit_with_sounds("CY"), 2.0)
    base = fast_band_energy(x0, CY_BANDS)
    _, x1 = render(retuned_cy(), 2.0)
    sh = fast_band_energy(x1, CY_BANDS)
    say(f"\n  CY  <2k / 2-5k / 5-9k / 9-13k / >13k")
    say(f"    machine        {fmt(HW_CY)}")
    say(f"    shipped        {fmt(base)}   cost {cost(base, HW_CY):5.1f}")
    say(f"    M_CYHI Q 2.5->4.0, E_CYL x2   {fmt(sh)}   cost {cost(sh, HW_CY):5.1f}")
    say(f"    delta          {dfmt(sh, base)}")
    gate(sh, "CY retuned, fixed-point render")
    t20_b = am.schroeder_t20(x0, SR).require("CY T20 shipped")
    t20_r = am.schroeder_t20(x1, SR).require("CY T20 retuned")
    say(f"    Schroeder T20  shipped {t20_b*1e3:.0f} ms, retuned {t20_r*1e3:.0f} ms, "
        f"machine {HW_CY_T20*1e3:.0f} ms  (test tolerance +-20 %)")
    f0 = am.dominant_frequency(x1, 2000., 5000., SR).require("CY low line")
    say(f"    low line       {f0:.0f} Hz, machine {HW_CY_PEAK:.0f} Hz "
        f"(test tolerance +-20 %)")
    st = am.line_stability(x1, (2000., 9000.), SR, windows=4, tol_hz=40.,
                           threshold=2.0).require("CY line stability")
    say(f"    line stability {st:.2f} (test floor 0.60)")
    _, xch = render(dx.kit_with_sounds("CH"), 0.5, stop=dx.CH)
    cy_low = fast_band_energy(x1, CY_BANDS)[1]
    ch_low = fast_band_energy(xch, CY_BANDS)[1]
    say(f"    CY 2-5k {cy_low*100:.1f} % vs CH 2-5k {ch_low*100:.1f} % "
        f"(test wants CY >= 4x CH: {'OK' if cy_low >= 4*ch_low else 'FAILS'})")
    OUT["cy"] = dict(shipped=list(base), retuned=list(sh), cost_shipped=cost(base, HW_CY),
                     cost_retuned=cost(sh, HW_CY), t20_shipped=t20_b, t20_retuned=t20_r,
                     low_line_hz=f0, line_stability=st)

    # ---------------- CH / OH ----------------------------------------------
    for name, stop, span, ref, f0n, qn, numn, ampn, shipped in (
            ("CH", dx.CH, 0.70, HW_CH, 10750., 4.0, HP, 0.69, (11700., 2.5, HP)),
            ("OH", dx.OH, 1.20, HW_OH, 10000., 3.0, BP, 0.45, (7800., 2.5, HP))):
        _, xa = render(dx.kit_with_sounds(name), span, stop=stop)
        _, xb = render(retuned_hat(name, f0n, qn, numn, ampn), span, stop=stop)
        sa = fast_band_energy(trim_onset(xa), HAT_BANDS)
        sb = fast_band_energy(trim_onset(xb), HAT_BANDS)
        say(f"\n  {name}  3-6k / 6-9k / 9-13k / >13k")
        say(f"    machine   {fmt(ref)}")
        say(f"    shipped   {fmt(sa)}   cost {cost(sa, ref):5.1f}   "
            f"({shipped[0]:.0f} Hz Q {shipped[1]} HP)")
        nm = {0: 'RAW', 1: 'BP', 2: 'HP'}[numn]
        say(f"    retuned   {fmt(sb)}   cost {cost(sb, ref):5.1f}   "
            f"({f0n:.0f} Hz Q {qn} {nm})")
        gate(sb, f"{name} retuned, fixed-point render", ref=ref, bands=HAT_BANDS)
        ta = am.decay_tau(trim_onset(xa), SR)
        tb = am.decay_tau(trim_onset(xb), SR)
        say(f"    decay tau  shipped {ta.value*1e3 if ta.ok else float('nan'):.1f} ms, "
            f"retuned {tb.value*1e3 if tb.ok else float('nan'):.1f} ms")
        OUT[name] = dict(shipped=list(sa), retuned=list(sb),
                         cost_shipped=cost(sa, ref), cost_retuned=cost(sb, ref))

    # ---------------- INJECTED CONTROL, on the retuned kit ------------------
    say("\n== INJECTED CONTROL on the retuned CY kit ==")
    img = dict(retuned_cy())
    for a, v in dx.mode_writes(dx.M_CYHI, dx.CY_HI_HZ, 4.0, dx.AMP_CY_HI, RAW):
        img[a] = v                      # numerator BP -> RAW: an all-pole resonator
    _, xd = render(sorted(img.items()), 2.0)
    shd = fast_band_energy(xd, CY_BANDS)
    say(f"    defect (M_CYHI numerator BP -> RAW)  {fmt(shd)}")
    red = not gate(shd, "defect vs the tightened gate")
    tol_ship = (0.020, 0.060, 0.120, 0.120, 0.035)
    bad = [f"{lo/1000:g}-{hi/1000:g}k" for (lo, hi), g, r, t
           in zip(CY_BANDS, shd, HW_CY, tol_ship) if abs(g-r) > t]
    say(f"    shipped gate: " + ("RED on " + ",".join(bad) if bad else "GREEN -- blind to it"))
    OUT["control_numerator"] = dict(shares=list(shd), tight_red=red, shipped_red=bool(bad))

    json.dump(OUT, open(os.path.join(SCRATCH, "hh_part3.json"), "w"), indent=2, default=float)
    say("\n(written hh_part3.json)")

if __name__ == "__main__":
    try:
        main()
    except am.InsufficientEvidence as e:
        say(f"REFUSED  {e}")
        sys.exit(2)
