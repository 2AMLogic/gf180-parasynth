#!/usr/bin/env python3
"""Why does #411 revision 4 (Hh1's own amp raised to AMP_MAX) fail to move
`tools/cymbal_mid.py`'s over-skirt metric toward the 808, and is that a
clipping artifact or the metric's own structure?

WHY THIS EXISTS. `model/cymbal_candidate.py`'s REVISION 4 docstring predicts,
before the render, that a flat +3.17 dB gain on Hh1's own amp register should
move the mid band's over-skirt residual (`cymbal_mid.measure_mid`'s
`over_leak_edt_bp_only_db`) from revision 3's +0.04 dB toward the 808's
+4.41 dB. The render instead measures -0.55 dB -- the WRONG direction, not
merely short of the prediction. Two candidate explanations, both testable
without touching the frozen measurement instruments:

  1. CLIPPING. Revision 3's own docstring warns "the cymbal's VCAs clip and
     the bands are re-levelled" (revision 3, `model/cymbal_candidate.py`).
     Both Hh1 and every other active cymbal mode share ONE per-sample
     accumulator (`ModalFxHP3.step`'s `mix`), saturated ONCE per sample
     (`modal_fixed.sat`, `self.OB` bits) after summing every mode. Pushing
     Hh1's amp toward its Q0.16 ceiling could push that sum over the rail,
     and a hard per-sample clip is NOT a flat gain -- it would redistribute
     energy across frequency bands unevenly and could plausibly explain a
     result that moves the wrong way.
  2. GAIN INVARIANCE. `amp` in `ModalFxHP3.step` (`mix += (y * amp) >> 16`)
     is a scalar multiplying Hh1's ENTIRE filtered output, at every sample,
     identically at every frequency -- it is applied AFTER Hh1's IIR
     filtering, not inside it. If M (0.891-1.782 kHz) and L (2-5 kHz) both
     derive (almost) entirely from that same post-Hh1 signal, then scaling
     it by a constant k cannot change the M/L energy ratio: both scale by
     k^2 together. `over_leak_edt_bp_only_db` compares M's measured energy
     against a leak PREDICTION built from L's measured energy in the SAME
     render (`cymbal_mid.leak_dominance_db`), so if that reasoning holds,
     the metric is close to gain-invariant with respect to Hh1's own amp by
     CONSTRUCTION -- no bug required, and no amount of raising or lowering
     Hh1's own amp register (alone) could have satisfied acceptance item 1.

WHAT THIS MEASURES. `modal_fixed.sat` is monkeypatched (this probe's own
import, not a change to any frozen module) to count every call and every
value that would have been clamped, for the full CY render at revision 3's
amp and at revision 4's amp. `dx.output_fx`'s own hard 16-bit rail is checked
by the render's own returned peak sample amplitude (it already saturates via
`np.clip`, so no patch is needed there -- the peak magnitude relative to
32768 says how much headroom was left).

RESULT (measured 2026-09-28, commit history: this probe's own branch,
`--out` below records the exact commit): ZERO saturation events in either
render (0 of 25,152,000 `sat()` calls in each), and the two renders' peak
`mix` magnitude and peak output sample are within noise of each other
(peak output ~0.286 of full scale in both, ~11 dB of headroom). Explanation
1 (clipping) is REFUTED by direct measurement. Explanation 2 (gain
invariance) is therefore the standing account of why the render moved the
metric by less than 1 dB in the wrong direction rather than by the predicted
+3.17 dB or more toward the 808 -- consistent with the metric being close to
insensitive, by its own construction, to a flat gain on Hh1's own amp alone.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import subprocess
import sys

import numpy as np

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "model"), str(ROOT / "tools")]
import cymbal_candidate as cc          # noqa: E402
import cymbal_candidate_eval as ce     # noqa: E402
import modal_fixed as mf               # noqa: E402


def _spy():
    counts = {"n": 0, "clip": 0, "max_mix_abs": 0}
    orig_sat = mf.sat

    def spy_sat(v, bits):
        counts["n"] += 1
        lo, hi = -(1 << (bits - 1)), (1 << (bits - 1)) - 1
        if v < lo or v > hi:
            counts["clip"] += 1
        counts["max_mix_abs"] = max(counts["max_mix_abs"], abs(v))
        return orig_sat(v, bits)

    return counts, spy_sat


def probe(variant: str) -> dict:
    """One full CY render at `variant`'s calibration, with `modal_fixed.sat`
    call-counted for the render's duration only."""
    counts, spy_sat = _spy()
    orig_sat = mf.sat
    mf.sat = spy_sat
    cc.mf.sat = spy_sat
    try:
        ce.VARIANT = variant
        cal = ce.calibrate()
        yc, sr = cc.render(ce.kit_with_levels(cal["amps"]), "CY")
    finally:
        mf.sat = orig_sat
        cc.mf.sat = orig_sat
    peak = float(np.max(np.abs(yc)))
    return {
        "variant": variant,
        "hh1_amp": cal["per_band"]["low"]["amp"],
        "sat_calls": counts["n"],
        "sat_clipped": counts["clip"],
        "sat_clip_fraction": counts["clip"] / max(counts["n"], 1),
        "max_mix_magnitude": counts["max_mix_abs"],
        "peak_output_sample_full_scale": peak,
        "peak_output_headroom_db": None if peak <= 0 else round(-20.0 * np.log10(peak), 2),
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--out", type=pathlib.Path, default=None)
    ap.add_argument("--variants", nargs="+", default=["full", "candidate4"])
    a = ap.parse_args(argv)
    res = {"probes": [probe(v) for v in a.variants]}
    for p in res["probes"]:
        print(f"{p['variant']:12s} Hh1 amp {p['hh1_amp']:.6f}  sat calls {p['sat_calls']:>10d}  "
              f"clipped {p['sat_clipped']:>4d} ({100 * p['sat_clip_fraction']:.4f} %)  "
              f"peak {p['peak_output_sample_full_scale']:.4f} full-scale "
              f"({p['peak_output_headroom_db']:.2f} dB headroom)")
    res["commit"] = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True,
                                   text=True).stdout.strip()
    res["sources_dirty"] = subprocess.run(["git", "diff", "--quiet", "HEAD", "--", "model", "tools"],
                                          cwd=ROOT).returncode != 0
    if a.out:
        a.out.parent.mkdir(parents=True, exist_ok=True)
        a.out.write_text(json.dumps(res, indent=1, default=float) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
