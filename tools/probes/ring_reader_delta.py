#!/usr/bin/env python3
"""Before/after of the free-ring pitch readers routed through
`ring_estimator.ring_frequency` (issue #703), on the shipped `OurLadder`.

    python3 tools/probes/ring_reader_delta.py

OLD is the idiom the sites carried: the zero-crossing frequency when it agrees
with the spectral peak inside 2 % (3 % in `sound_report`), the peak otherwise.
NEW is `ring_frequency` (0.5 % agreement, sustain classifier, >= 10 cycles).
Both read the SAME rendered record, so any difference is the reader alone. A
row that newly refuses is printed as REFUSED with the estimator's reason,
never dropped. The Mini V3 rows (M1A / M5A, `calibrate_knob` on miniv3) need
the plugin rig and are not run here.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "model"))
sys.path.insert(0, os.path.join(ROOT, "audition"))

import audio_measure as am                                          # noqa: E402
import reference_compare as rc                                      # noqa: E402
import reference_integrity as ri                                    # noqa: E402
import reference_rigs as rr                                         # noqa: E402
import ring_estimator as re_                                        # noqa: E402

SR = rr.SR


def old(y, lo, hi, tol):
    e = am.dominant_frequency(y, lo, hi, SR)
    z = am.zero_crossing_frequency(y, SR)
    if z.ok and e.ok and abs(z.value - e.value) / e.value < tol:
        return z.value
    return e.value if e.ok else None


def new(y):
    try:
        return re_.ring_frequency(y, SR), ""
    except re_.Refused as exc:
        return None, str(exc)


def row(site, cut, y, lo, hi, tol):
    o, (n, why) = old(y, lo, hi, tol), new(y)
    fmt = lambda v: "None" if v is None else f"{v:9.3f}"
    verdict = f"REFUSED ({why})" if n is None else f"d={abs(n - o) / o * 100:.4f} %" if o else "-"
    print(f"{site:26s} cut {cut:7.0f}  old {fmt(o)}  new {fmt(n)}  {verdict}")
    return o, n


def main():
    lad = rr.OurLadder()
    res = rc.res_grid("ours")[-1]
    for hz in rc.CUTOFFS_HZ:
        row("reference_compare track", hz, lad.ring(hz, res, seconds=0.8), 20.0, 20000.0, 0.02)
    sp = {"old": [], "new": []}
    for c in (200.0, 800.0, 3200.0, 6400.0):
        o, n = row("sound_report m_track", c, lad.ring(c, 1.05, seconds=0.6),
                   c * 0.4, c * 2.2, 0.03)
        if o:
            sp["old"].append((o / c - 1) * 100)
        if n:
            sp["new"].append((n / c - 1) * 100)
    for k, v in sp.items():
        print(f"m_track_spread {k}: " +
              (f"{max(v) - min(v):.4f} pp over {len(v)} cuts" if len(v) >= 3 else f"None ({len(v)} cuts)"))
    for hz in ri.VAR_CUTS:
        row("reference_integrity var", hz, rr.OurLadder().ring(hz, 2.0, seconds=0.8),
            hz * 0.3, hz * 2.5, 0.02)


if __name__ == "__main__":
    main()
