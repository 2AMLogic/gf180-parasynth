"""Part 2: Hh1 with its numerator actually enabled, the CY low-band residual,
and the same question for CH and OH.

Reads nothing from part 1 except its conclusions; re-derives its own baselines.
"""
from __future__ import annotations
_ROOT = __import__("pathlib").Path(__file__).resolve().parents[3]
import json, math, os, sys, time

WT = str(_ROOT)
SCRATCH = ("/private/tmp/claude-501/-Users-joseph-dev-2amlogic/"
           "b50207d1-0443-45b1-85ae-6193fbb61d1b/scratchpad")
sys.path.insert(0, os.path.join(WT, "model"))
sys.path.insert(0, os.path.join(WT, "audition"))
sys.path.insert(0, SCRATCH)

import numpy as np
from scipy.signal import lfilter
import audio_measure as am
import drums_fx as dx
import measure_harness as mh
from dsp import SR
from modal_fixed import RAW, BP, HP, pole_regs
from hh_probe import (say, provenance, sallen_key_hp, render, biquad, response,
                      unity_amp, fast_band_energy, fmt, dfmt, cost, chain, gate,
                      CY_BANDS, HAT_BANDS, HW_CY, REC_CY, HW_CH, HW_OH,
                      REC_CH, REC_OH, capture_cy, PRE)

OUT: dict = {}

def trim_onset(x, frac=0.02):
    """drum_verify.trim_onset: drop everything before the first sample above
    `frac` of the peak."""
    a = np.abs(x)
    i = int(np.argmax(a > frac * a.max()))
    return x[i:]

def main():
    prov = provenance()
    say("== provenance ==", json.dumps(prov["sha256_16"]), prov["source_commit"])

    f_hh1, q_hh1 = sallen_key_hp(1.5e-9, 22e3, 82e3)

    # ---------------- CY baseline ------------------------------------------
    _, x0 = render(dx.kit_with_sounds("CY"), 2.0)
    base = fast_band_energy(x0, CY_BANDS)
    say(f"\n== CY baseline (re-derived) {fmt(base)}  cost {cost(base, HW_CY):.1f} ==")

    # ---------------- STEP 1 redone: Hh1 with its NUMERATOR on -------------
    say("\n== STEP 1 (corrected): Hh1 restored, numerator actually enabled ==")
    say("  The first run of this used nums=12 with the new filter at mode 16, so")
    say("  `m < NUMS` was false and the mode ran ALL-POLE. That is a defect in the")
    say("  probe, found by the result being absurd (cost 108.6). nums=17 here.")
    say(f"  Hh1 = {f_hh1:.0f} Hz, Q {q_hh1:.3f}, HP numerator (DERIVED).")
    amp = unity_amp(f_hh1, q_hh1, HP)
    img = dict(dx.kit_with_sounds("CY"))
    for a, v in dx.mode_writes(16, f_hh1, q_hh1, amp, HP):
        img[a] = v
    img[dx.A_PATH+dx.P_CYL] = dx.path_word(dx.SRC_TAP+dx.M_CYBP, dx.E_CYL,
                                           nl=dx.NL_SWING, att=dx.CY_ATT, dest=16)
    _, x1 = render(sorted(img.items()), 2.0, modes=17, nums=17)
    sh1 = fast_band_energy(x1, CY_BANDS)
    say(f"    machine   {fmt(HW_CY)}")
    say(f"    baseline  {fmt(base)}   cost {cost(base, HW_CY):5.1f}")
    say(f"    + Hh1     {fmt(sh1)}   cost {cost(sh1, HW_CY):5.1f}")
    say(f"    delta     {dfmt(sh1, base)}")
    gate(sh1, "Hh1 restored (17 modes, HP numerator)")
    OUT["hh1_corrected"] = dict(f0=f_hh1, q=q_hh1, amp=amp, shares=list(sh1),
                                cost=cost(sh1, HW_CY), cost_baseline=cost(base, HW_CY))

    # ---------------- where the residual actually is -----------------------
    say("\n== FIT (labelled): what closes the rest, with NO new filter ==")
    say("  Two knobs that are not filters: the DECAY band's post-filter Q (the")
    say("  shipped 2.5 is inherited from the HATS' Sallen-Key, not from Hh2/Hh3 --")
    say("  reference 10 calls Hh3 3rd-order and leaves Hh2's values unspecified),")
    say("  and the LOW band's level E_CYL (already a FIT in drums_fx.py).")
    vs, vd, vl = capture_cy()
    short_fixed = chain(vs, (11700., 2.5, HP, 0.69))
    best = None
    for num in (BP, HP):
        for f0 in np.arange(8000., 14001., 250.):
            for q in (1.5, 2.0, 2.5, 3.0, 3.5, 4.0, 5.0, 6.0):
                hi = chain(vd, (float(f0), q, num, unity_amp(f0, q, num)))
                for g in (1.0, 1.4, 2.0, 2.8, 4.0, 5.6, 8.0):
                    sh = fast_band_energy(short_fixed + hi + g*vl, CY_BANDS)
                    c = cost(sh, HW_CY)
                    if best is None or c < best[0]:
                        best = (c, float(f0), q, int(num), g, list(sh))
    c, f0, q, num, g, sh = best
    say(f"    best, still THREE filters: decay post-filter {f0:.0f} Hz Q {q} "
        f"{'BP' if num == BP else 'HP'}, low band x{g}")
    say(f"      machine {fmt(HW_CY)}")
    say(f"      best    {fmt(sh)}   cost {c:.1f}  (baseline {cost(base, HW_CY):.1f})")
    gate(sh, "retuned, no filter added")
    OUT["retune_no_new_filter"] = dict(cost=c, f0=f0, q=q, num=num, low_gain=g, shares=sh)

    # ---------------- CH and OH --------------------------------------------
    say("\n== CH / OH: bands 3-6k / 6-9k / 9-13k / >13k ==")
    say("  reference 11 gives each hat exactly ONE post-filter (OH Q26, CH Q31)")
    say("  and the model has both. So for the hats the question is not whether a")
    say("  filter is missing -- none is -- but whether the one that is there can")
    say("  be made to match by retuning.")
    hats = {}
    for name, span, ref, rec in (("CH", 0.70, HW_CH, REC_CH), ("OH", 1.20, HW_OH, REC_OH)):
        for s in ((0.70, 1.20) if name == "OH" else (0.70,)):
            _, x = render(dx.kit_with_sounds(name), s, stop=dx.SOUND_STOP[name])
            sh = fast_band_energy(trim_onset(x), HAT_BANDS)
            mark = "" if abs(max(a-b for a, b in zip(sh, rec))) > 0.02 else "   <- reproduces"
            say(f"    {name} @ {s:.2f} s  {fmt(sh)}{mark}")
        say(f"    {name} RECORDED {fmt(rec)}   (drum-verification.md 4.4)")
        say(f"    {name} machine  {fmt(ref)}")
        hats[name] = span

    # capture each hat's post-filter input and sweep the one filter it has
    say("\n== FIT (labelled): sweep each hat's SINGLE post-filter ==")
    for name, stop, mode, path, env, span, ref, f_ref, q_ref in (
            ("CH", dx.CH, dx.M_CHHP, dx.P_CH, dx.E_CH, 0.70, HW_CH, 11700., 2.5),
            ("OH", dx.OH, dx.M_OHHP, dx.P_OH, dx.E_OH, 1.20, HW_OH, 7800., 2.5)):
        img = dict(dx.kit_with_sounds(name))
        for a, v in dx.mode_writes(16, 1000., 1.0, 0.0, RAW):
            img[a] = v
        img[dx.A_MODE + 16*dx.MODE_STRIDE + 0] = 0
        img[dx.A_MODE + 16*dx.MODE_STRIDE + 1] = 0
        img[dx.A_PATH+path] = dx.path_word(dx.SRC_TAP+dx.M_HATBP, env,
                                           nl=dx.NL_SWING, dest=16)
        d, _ = render(sorted(img.items()), span, stop=stop, modes=17, nums=11)
        v = d.trace["exc"][:, 16].astype(np.float64)
        amp_shipped = {"CH": 0.69, "OH": 0.45}[name]
        recon = chain(v, (f_ref, q_ref, HP, amp_shipped))
        _, xt = render(dx.kit_with_sounds(name), span, stop=stop)
        true_sh = fast_band_energy(trim_onset(xt), HAT_BANDS)
        re_sh = fast_band_energy(trim_onset(recon), HAT_BANDS)
        say(f"\n  {name}: emulator check  true {fmt(true_sh)} | offline {fmt(re_sh)}")
        try:
            e = mh.assert_precondition(true_sh, re_sh, 0.015,
                                       what=f"{name} emulator")
        except SystemExit as exc:
            say(f"    {exc}")
            continue
        say(f"    precondition OK ({e*100:.2f} points)")
        say(f"    the VCA output the post-filter receives: "
            f"{fmt(fast_band_energy(trim_onset(v), HAT_BANDS))}")
        best = None
        for num in (BP, HP, RAW):
            for f0 in np.arange(3000., 16001., 250.):
                for q in (0.7, 1.0, 1.4, 2.0, 2.5, 3.0, 4.0, 5.0, 6.0, 8.0):
                    y = chain(v, (float(f0), q, num, unity_amp(f0, q, num)))
                    sh = fast_band_energy(trim_onset(y), HAT_BANDS)
                    c = cost(sh, ref)
                    if best is None or c < best[0]:
                        best = (c, float(f0), q, int(num), list(sh))
        c, f0, q, num, sh = best
        nm = {0: "RAW", 1: "BP", 2: "HP"}[num]
        say(f"    shipped   {f_ref:.0f} Hz Q {q_ref} HP -> {fmt(true_sh)}   "
            f"cost {cost(true_sh, ref):.1f}")
        say(f"    best 1-pf {f0:.0f} Hz Q {q} {nm} -> {fmt(sh)}   cost {c:.1f}")
        say(f"    machine   {fmt(ref)}")
        gate(sh, f"{name} best single retuned post-filter", ref=ref, bands=HAT_BANDS)
        OUT[f"{name}_sweep"] = dict(cost=c, f0=f0, q=q, num=num, shares=sh,
                                    shipped_cost=cost(true_sh, ref),
                                    shipped=list(true_sh))

    json.dump(OUT, open(os.path.join(SCRATCH, "hh_part2.json"), "w"), indent=2, default=float)
    say("\n(written hh_part2.json)")

if __name__ == "__main__":
    main()
