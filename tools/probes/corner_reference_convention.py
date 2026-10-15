#!/usr/bin/env python3
"""Does the moving-median passband reference bias the corner that
`reference_compare.response_row` and `sound_report._corner` measure? (#546)

    python3 tools/probes/corner_reference_convention.py          # decide
    python3 tools/probes/corner_reference_convention.py --inject # controls

Both callers took `corner_from_curve(..., ref_band=(f0, max(floor, cut/4)))`
with no `ref_db`: the -3 dB point is read against the MEDIAN of a band whose top
edge moves with the commanded cutoff. #150/#164 replaced that for
`filt_corner` / `filt_rolloff` with the DC-plateau reference; these two callers
were left, with the question "does the bias apply to them?" recorded as open.

TRUTH IS NOT OURS. Four identical real poles at fp have |H|^2 = (1+x^2)^-4,
x = f/fp, so the -3 dB point is at x0 = sqrt(10**0.075 - 1) = 0.4343 of fp at
EVERY cutoff, by algebra. Nothing here is calibrated on our own ladder.

WHAT IT DECIDES, per caller, for each estimator convention:
  * the corner error against x0*fp at every cutoff the caller uses;
  * the cutoff-DEPENDENT part, as `sound_report.m_corner_spread` computes it
    (max-min of corner/cutoff over the mean) because a constant bias cancels
    there and only that part reads as cutoff-control drift;
  * KEEP if that spread is within THRESHOLD_PCT, otherwise MOVE.
THRESHOLD_PCT is the tolerance of the property the number feeds, read from
`sound_report.build_properties()` ("corner ratio drift", 3.0 %), not typed here:
an estimator contribution as large as the whole tolerance of the lock it feeds
cannot be told apart from a cutoff-control defect.

REFUSAL IS AN OUTCOME. Where `dc_plateau_db` refuses (band top past 0.4 of the
cutoff: 100 and 200 Hz on a 40 Hz grid floor) the candidate yields no corner,
and is reported as REFUSED, not scored as zero error.

CONFIRMATION on conditions not used to choose: a grid the decision did not see
(`geomspace(20, 18000, 32)`) and a ladder whose poles sit 1.3x the command
(a uniform skew moves every corner equally and must not change the spread).

Controls (--inject, and test_corner_reference_convention.py): the gate is fed a
known cutoff-dependent bias and must say MOVE; fed truth it must say KEEP.
"""
from __future__ import annotations

import pathlib
import sys

import numpy as np

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "model"))

import audio_measure as am                                          # noqa: E402
import reference_compare as rcmp                                    # noqa: E402
import sound_report as sr                                           # noqa: E402

X0 = float(np.sqrt(10 ** 0.075 - 1.0))     # -3 dB of (1+x^2)^-2: 0.4343 of fp
HELD_OUT_GRID = np.geomspace(20.0, 18000.0, 32)


def threshold_pct() -> float:
    """The tolerance of the lock `_corner` feeds, from where it is declared."""
    for p in sr.build_properties():
        if (p.voice, p.name) == ("LADDER", "corner ratio drift"):
            return float(p.tol)
    raise RuntimeError("sound_report no longer declares 'corner ratio drift'")


def four_pole_db(freqs, fp):
    return -40.0 * np.log10(1.0 + (np.asarray(freqs, float) / fp) ** 2)


def spread_pct(ratios) -> float | None:
    """`m_corner_spread`'s own formula."""
    r = [v for v in ratios if v is not None and np.isfinite(v)]
    return 100.0 * (max(r) - min(r)) / float(np.mean(r)) if len(r) > 3 else None


CALLERS = {
    # name: (grid, cutoffs, band rule exactly as the caller writes it)
    "sound_report._corner": (
        sr.FREQS, sr.CUTS, lambda F, c: (F[0], max(100.0, c * 0.25))),
    "reference_compare.response_row": (
        np.asarray(rcmp.FREQS), rcmp.CUTOFFS_HZ,
        lambda F, c: (F[0], max(F[0] * 2.5, c * 0.25))),
}


def corners(freqs, cuts, band_rule, convention, pole_scale=1.0):
    """{cutoff: (estimated corner / TRUE corner - 1, or None if refused)}."""
    out = {}
    for c in cuts:
        fp = c * pole_scale
        g, band = four_pole_db(freqs, fp), band_rule(freqs, c)
        e = (am.corner_from_curve(freqs, g, ref_band=band) if convention == "median"
             else am.corner_dc_referenced(freqs, g, ref_band=band, scale_hz=c))
        out[c] = (e.value / (X0 * fp) - 1.0) if e.ok else None
    return out


def verdict(err_by_cut: dict, threshold: float) -> str:
    """KEEP / MOVE / REFUSED from per-cutoff relative errors (None = refused).
    The spread is of corner/cutoff, i.e. of (1+err), as m_corner_spread is."""
    s = spread_pct([None if e is None else 1.0 + e for e in err_by_cut.values()])
    if s is None:
        return "REFUSED"
    return "KEEP" if s <= threshold else "MOVE"


def report(freqs_override=None, pole_scale=1.0, label="decision") -> dict:
    thr = threshold_pct()
    print(f"\n== {label}: truth = {X0:.4f} x pole at every cutoff; "
          f"threshold {thr:g} % (sound_report 'corner ratio drift' tol) ==")
    res = {}
    for name, (F, cuts, rule) in CALLERS.items():
        F = F if freqs_override is None else freqs_override
        print(f"\n{name}")
        print(f"  {'cutoff':>7} {'median ref':>12} {'DC-plateau':>12}")
        per = {conv: corners(F, cuts, rule, conv, pole_scale)
               for conv in ("median", "dc")}
        for c in cuts:
            m, d = per["median"][c], per["dc"][c]
            print(f"  {c:7.0f} {100*m:+11.2f}% " +
                  (f"{100*d:+11.2f}%" if d is not None else "     REFUSED"))
        for conv in ("median", "dc"):
            s = spread_pct([None if e is None else 1 + e for e in per[conv].values()])
            print(f"  {conv:>6}: cutoff-dependent spread "
                  f"{'n/a' if s is None else f'{s:.2f} %'} -> {verdict(per[conv], thr)}")
        res[name] = {conv: verdict(per[conv], thr) for conv in per}
    return res


def controls() -> list[str]:
    """The gate must turn red on a known cutoff-dependent bias and stay green
    on truth. Returns the failures."""
    thr, cuts = threshold_pct(), sr.CUTS
    bad = {c: 0.05 * (len(cuts) - i) for i, c in enumerate(cuts)}     # 30 % .. 5 %
    flat = {c: 0.30 for c in cuts}                  # constant bias cancels in a ratio
    fails = []
    for what, errs, want in (("injected cutoff-dependent bias", bad, "MOVE"),
                             ("truth (zero error)", {c: 0.0 for c in cuts}, "KEEP"),
                             ("constant 30 % bias", flat, "KEEP"),
                             ("everything refused", {c: None for c in cuts}, "REFUSED")):
        got = verdict(errs, thr)
        print(f"  control {what:32s} -> {got:8s} (want {want})"
              f"{'' if got == want else '   FAIL'}")
        if got != want:
            fails.append(what)
    # the real thing the gate exists for must also be red on the old convention
    F, c, rule = CALLERS["sound_report._corner"]
    real = verdict(corners(F, c, rule, "median"), thr)
    print(f"  control old convention on a closed-form 4-pole -> {real} (want MOVE)")
    if real != "MOVE":
        fails.append("old convention not flagged")
    return fails


def _legacy_response_row(freqs, g, cut_hz):
    """`response_row`'s corner and slope exactly as it stood before #546
    (median reference; slope band placed from that corner, or from the
    commanded cutoff when the corner failed)."""
    ref_band = (freqs[0], max(freqs[0] * 2.5, (cut_hz or 400.0) * 0.25))
    c = am.corner_from_curve(freqs, g, ref_band=ref_band)
    ref = c.value if c.ok else cut_hz
    slope = None
    if ref:
        plateau = am.plateau_db(freqs, g, ref_band)
        live = np.asarray(freqs)[np.asarray(g) > plateau - 70.0]
        top = float(live.max()) if len(live) else 9000.0
        s = am.slope_db_oct(freqs, g, (2.2 * ref, min(7.0 * ref, 9000.0, top)))
        slope = s.value if s.ok else None
    return (c.value if c.ok else None), slope


def stored_curves() -> None:
    """Before/after of every scored number `response_row` produces, over the 56
    real measured curves committed in docs/reference-compare-results.json."""
    import json
    d = json.load(open(ROOT / "docs" / "reference-compare-results.json"))
    print("\n== response_row before -> after on the stored measured curves ==")
    print(f"  {'key':24s}{'cut':>7}{'corner Hz':>20}{'slope dB/oct':>22}")
    n = moved = refused = 0
    for key in sorted(k for k in d if k.startswith("response-")):
        for r in d[key]:
            if "freqs" not in r:
                continue
            F, g = np.asarray(r["freqs"]), np.asarray(r["gain_db"])
            row = rcmp.response_row(F, g, r["cut_hz"], r["device"], r["res"],
                                    r["cut_setting"])
            oc, os_ = _legacy_response_row(F, g, r["cut_hz"])
            nc, ns = row["corner_hz"], row["slope_db_oct"]
            n += 1
            refused += nc is None and oc is not None
            moved += (nc != oc) or (ns != os_)
            f = lambda v, w: "refused" if v is None else f"{v:{w}}"        # noqa: E731
            print(f"  {key:24s}{r['cut_hz']:7.0f}"
                  f"{f(oc, '9.1f'):>10}->{f(nc, '8.1f'):>9}"
                  f"{f(os_, '9.2f'):>11}->{f(ns, '8.2f'):>9}")
    print(f"  {n} curves; {moved} changed a scored number; "
          f"{refused} lost a corner that the old convention read")


def main(argv) -> int:
    print("controls (the gate must be able to fail):")
    fails = controls()
    if "--inject" in argv:
        return 1 if fails else 0
    report()
    report(freqs_override=HELD_OUT_GRID, label="held-out grid 20 Hz-18 kHz, 32 pts")
    report(pole_scale=1.3, label="poles 1.3x the command (uniform skew)")
    stored_curves()
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
