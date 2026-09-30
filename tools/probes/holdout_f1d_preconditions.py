#!/usr/bin/env python3
"""Do the F1 estimators have a number to give at the setting F1D is about to be
sealed at? Run BEFORE the seal was written, and it reads no reference.

    python3 tools/probes/holdout_f1d_preconditions.py --cut 500

WHY THIS EXISTS. CLAUDE.md: "an unsatisfiable gate is worse than no gate", and
the same is true of a holdout. A sealed setting whose estimators can only REFUSE
is not a held-out measurement, it is a held-out refusal -- and the refusal would
be indistinguishable from a real finding about the filter. So the apparatus's
preconditions are checked at the sealed setting first:

  * the plateau band `run_case._ref_band` uses at this cutoff has enough points
    of the frozen probe grid (`reference_compare.FREQS`, the grid
    `refprofile.clip_specs` renders every clip on) to support
    `audio_measure.dc_plateau_db` and `plateau_db`;
  * our own side renders at the setting and `filt_corner`,
    `filt_lowband_gain` and `filt_rolloff` each return a value rather than a
    refusal, with the rolloff band inside the grid and above the truncation
    floor.

WHAT IT DELIBERATELY DOES NOT DO. **It reads no reference and computes no
distance.** There is no frozen Surge clip at this cutoff -- that is the point of
the seal: the reference is rendered later, by somebody else, and the error is
read after that. Everything printed here is a property of OUR side and of the
grid, so running it cannot tell anybody how well the filter matches at the
sealed setting. That asymmetry is the whole mechanism; see `tools/holdout.py`.

Recorded output, res 0, probe amp 0.25, on `reference_compare.FREQS`
(geomspace 40 Hz .. 12 kHz, 32 points):

    cut 500 Hz   plateau band 40.0 .. 125.0 Hz, 7 grid points
                 corner  OK  216.439 Hz (fit residual 0.005 dB)
                 lowband OK  -0.332 dB
                 rolloff OK  -17.480 dB/oct over 476.2 .. 1515.1 Hz, 6 points

    cut 2000 Hz  plateau band 40.0 .. 500.0 Hz, 14 grid points
                 corner  OK  846.243 Hz
                 lowband OK  -0.079 dB
                 rolloff OK  -17.410 dB/oct over 1861.7 .. 5923.7 Hz, 7 points

**Both candidates satisfy the preconditions, which is why they are both
printed here.** The choice between them was made on a rule fixed before either
was run (the lowest unanchored gap, for the bass reason in
`docs/scorecard/holdout/F1D.json`), not on these numbers -- and 2000 Hz is kept
in this docstring so that is checkable rather than asserted.
"""
from __future__ import annotations

import argparse
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "model"))
sys.path.insert(0, str(ROOT / "tools"))

import numpy as np                                                   # noqa: E402

import refprofile as rp                                              # noqa: E402
import run_case as rc                                                # noqa: E402


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--cut", type=float, default=500.0, help="commanded cutoff, Hz")
    ap.add_argument("--res", type=float, default=0.0, help="our resonance control")
    ap.add_argument("--open-hz", type=float, default=20000.0)
    a = ap.parse_args(argv)

    import reference_compare as rcmp
    freqs = np.asarray([float(f) for f in rcmp.FREQS], dtype=np.float64)
    amp = rp.PROBE_AMP
    band = rc._ref_band(freqs, a.cut)
    n_band = int(((freqs >= band[0]) & (freqs <= band[1])).sum())
    print(f"grid            {len(freqs)} points {freqs[0]:.1f} .. {freqs[-1]:.1f} Hz "
          f"(reference_compare.FREQS)")
    print(f"plateau band    {band[0]:.1f} .. {band[1]:.1f} Hz   {n_band} grid points")

    import f1_filter_path as fp
    path = fp.SelectedFilterPath()
    ours, _info = path.curve(freqs, a.cut, a.res, amp)
    ours_open, _oinfo = path.curve(freqs, a.open_hz, a.res, amp)
    import audio_measure as am
    open_plateau = am.plateau_db(freqs, ours_open, rc._ref_band(freqs, a.cut))

    corner = rc.filt_corner(a.cut)(freqs, ours)
    lowband = rc.filt_lowband_gain(a.cut, open_plateau)(freqs, ours)
    rolloff = rc.filt_rolloff(a.cut)(freqs, ours)
    ok = True
    for name, est in (("corner", corner), ("lowband", lowband), ("rolloff", rolloff)):
        if est.ok:
            print(f"{name:<15} OK    {est.value:.3f}   {est.detail}")
        else:
            ok = False
            print(f"{name:<15} REFUSED  {est.reason}   {est.detail}")
    if rolloff.ok:
        rb = rolloff.detail["band_hz"]
        n_roll = int(((freqs >= rb[0]) & (freqs <= rb[1])).sum())
        print(f"rolloff band    {rb[0]:.1f} .. {rb[1]:.1f} Hz   {n_roll} grid points")
    print("\nNO REFERENCE WAS READ: there is no frozen clip at this cutoff, so no "
          "distance was computed here.")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
