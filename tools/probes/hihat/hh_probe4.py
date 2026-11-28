"""Part 4: the correction.

Part 2's "cost 7.2, every band green" used the offline emulator for a knob the
emulator was never validated for -- the LOW band's envelope peak. Scaling a
captured signal by 2 is not what doubling E_CYL's peak does to the block: the
envelope's one-LSB floor lengthens the tail, so the low band gains MORE than
2x of energy over a 2 s window (T20 899 -> 1031 ms). Part 3 caught it.

So: the post-filter knob, which IS inside the emulator's validated scope, is
re-confirmed on the block; the envelope knob is swept on the block only.
"""
from __future__ import annotations
_ROOT = __import__("pathlib").Path(__file__).resolve().parents[3]
import json, os, sys, time

WT = str(_ROOT)
sys.path.insert(0, os.path.join(WT, "model"))
sys.path.insert(0, os.path.join(WT, "audition"))

import numpy as np
import audio_measure as am
import drums_fx as dx
from dsp import SR
from modal_fixed import RAW, BP, HP
from hh_probe import (say, render, fast_band_energy, fmt, dfmt, cost, gate,
                      CY_BANDS, HW_CY, out_dir)

OUT: dict = {}

def t20_text(e):
    """`T20 <ms>`, or the refusal's own reason. A refused estimate is never
    printed as a NaN standing where a number was expected (#1965)."""
    return f"T20 {e.value*1e3:4.0f} ms" if e.ok else f"T20 REFUSED: {e.reason}"

def t20_row(e):
    return e.value if e.ok else f"REFUSED: {e.reason}"

def cy_kit(f0=dx.CY_HI_HZ, q=dx.CY_HI_Q, num=BP, low_gain=1.0):
    img = dict(dx.kit_with_sounds("CY"))
    for a, v in dx.mode_writes(dx.M_CYHI, f0, q, dx.AMP_CY_HI, num):
        img[a] = v
    if low_gain != 1.0:
        for a, v in dx.env_writes(dx.E_CYL, dx.CY, dx.CY_TAU_LOW,
                                  dx.PEAK_CYL * low_gain):
            img[a] = v
    return sorted(img.items())

def main():
    say("== PART 4: post-filter knob on the block; envelope knob swept on the block ==")
    _, x0 = render(dx.kit_with_sounds("CY"), 2.0)
    base = fast_band_energy(x0, CY_BANDS)
    say(f"  shipped   {fmt(base)}   cost {cost(base, HW_CY):5.1f}")
    say(f"  machine   {fmt(HW_CY)}")

    say("\n-- A. the ONE change the offline emulator is validated for: M_CYHI --")
    say("   (the emulator is exact for a post-filter: part 1 measured 0.02 points")
    say("   against the fixed-point render, and CH/OH reproduced to 0.0 points)")
    rows = []
    for f0, q in ((dx.CY_HI_HZ, dx.CY_HI_Q), (10750., 4.0), (10500., 4.0), (10500., 3.0)):
        _, x = render(cy_kit(f0=f0, q=q), 2.0)
        sh = fast_band_energy(x, CY_BANDS)
        t20 = am.schroeder_t20(x, SR)
        rows.append((f0, q, list(sh), cost(sh, HW_CY), t20_row(t20)))
        say(f"   M_CYHI {f0:6.0f} Hz Q {q:3.1f} BP  {fmt(sh)}  cost {cost(sh, HW_CY):5.1f}"
            f"  {t20_text(t20)}")
    say(f"   machine                     {fmt(HW_CY)}        T20  798 ms")
    say("   -> the 9-13 kHz shoulder closes and OVERSHOOTS with the filter that")
    say("      already exists. Its shipped Q 2.5 is the whole difference.")
    OUT["cy_postfilter_only"] = rows

    say("\n-- B. FIT on the block: what is left after that, and is it a filter? --")
    best = None
    for q in (3.0, 4.0):
        for g in (1.0, 1.15, 1.3, 1.5, 1.75, 2.0):
            _, x = render(cy_kit(f0=10750., q=q, low_gain=g), 2.0)
            sh = fast_band_energy(x, CY_BANDS)
            c = cost(sh, HW_CY)
            t20 = am.schroeder_t20(x, SR)
            say(f"   Q {q:3.1f}  E_CYL x{g:4.2f}  {fmt(sh)}  cost {c:5.1f}"
                f"  {t20_text(t20)}")
            if best is None or c < best[0]:
                best = (c, q, g, list(sh), t20)
    c, q, g, sh, t20 = best
    say(f"\n   best on the block: M_CYHI 10750 Hz Q {q} BP, E_CYL x{g}")
    say(f"     machine  {fmt(HW_CY)}")
    say(f"     best     {fmt(sh)}   cost {c:.1f}  (shipped {cost(base, HW_CY):.1f})")
    gate(sh, "best, no filter added, fixed-point render")
    t20 = t20.require(f"best-fit CY T20 on the block, E_CYL x{g}")
    say(f"     T20 {t20*1e3:.0f} ms vs the machine's 798 ms "
        f"(the cymbal T20 test allows +-20 %: 638-958 ms)")
    say("   E_CYL's peak is ALREADY a fit in drums_fx.py (the three CY envelope")
    say("   peaks were searched against four measurements at once). Changing it")
    say("   is re-fitting, and it is labelled a FIT, not a circuit value.")
    OUT["cy_block_fit"] = dict(cost=c, q=q, low_gain=g, shares=sh, t20=t20)

    json.dump(OUT, open(out_dir() / "hh_part4.json", "w"), indent=2, default=float)
    say("\n(written hh_part4.json)")

if __name__ == "__main__":
    main()
