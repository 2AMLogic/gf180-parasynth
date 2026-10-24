"""Measure `run_case.filt_rolloff`'s grid systematic on a closed-form 4-pole.

Issue #169.  Ground truth is `|1/(1+jf/fp)|^4`; nothing here is rendered.  The
estimator is run on the profile grid (`reference_compare.FREQS`) at the pole
frequencies the existing tests use, and on a fine sweep of pole frequencies to
separate a finite-grid observation from a bound.

    python tools/rolloff_systematic.py          # prints the tables
"""
from __future__ import annotations

import pathlib
import sys

import numpy as np

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "model"))

import reference_compare as rcmp   # noqa: E402
import run_case as rc              # noqa: E402

IDEAL_FP = 250.0


def ideal_4pole_db(f, fp):
    return -80.0 * np.log10(np.abs(1 + 1j * np.asarray(f) / fp))


def slope(f, fp):
    """(slope dB/oct, corner Hz), or raises if the estimator refuses."""
    e = rc.filt_rolloff(IDEAL_FP)(f, ideal_4pole_db(f, fp))
    if not e.ok:
        raise RuntimeError(f"fp={fp}: refused: {e.reason}")
    return e.value, e.detail["corner_hz"]


def sweep(f, fps):
    return [(fp,) + slope(f, fp) for fp in fps]


def main() -> int:
    f = np.asarray(rcmp.FREQS, dtype=np.float64)
    print(f"grid: reference_compare.FREQS, n={len(f)}, {f[0]:.1f}-{f[-1]:.1f} Hz, "
          f"step ratio {f[1]/f[0]:.4f}")
    print("\npole fp   slope dB/oct   corner Hz   (test_run_case pole set)")
    r3 = sweep(f, (250.0, 312.5, 500.0))
    for fp, s, c in r3:
        print(f"{fp:8.1f}  {s:12.3f}  {c:10.3f}")
    v = [r[1] for r in r3]
    print(f"spread over 2:1 = {max(v)-min(v):.3f} dB/oct; "
          f"250 vs 312.5 (25 %) = {abs(v[0]-v[1]):.3f} dB/oct")

    fps = 200.0 * 2 ** (np.arange(0, 41) / 20.0)      # 200..800 Hz, 3.5 % steps
    rf = sweep(f, fps)
    s = np.array([r[1] for r in rf])
    print(f"\nfine sweep fp 200-800 Hz ({len(fps)} points): slope min {s.min():.3f} "
          f"max {s.max():.3f} spread {s.max()-s.min():.3f} dB/oct")
    pairs = []
    for a in np.linspace(200.0, 640.0, 89):
        pairs.append((a, abs(slope(f, 1.25 * a)[0] - slope(f, a)[0])))
    w = max(pairs, key=lambda p: p[1])
    print(f"worst |slope(1.25 fp) - slope(fp)| over fp 200-640 step 5 Hz: "
          f"{w[1]:.3f} dB/oct at fp={w[0]:.1f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
