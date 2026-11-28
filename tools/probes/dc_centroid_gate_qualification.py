#!/usr/bin/env python3
"""Qualify the DC-blocker centroid preservation gate.

The gate in ``dc_blocker.py`` measures the centroid over the entire FFT.  A
10 Hz contaminant is outside the protected audible band, but it still changes
that global number.  This probe uses a closed-form two-tone signal so the
failure is independent of the drum model.

Exit status: 0 qualified, 1 not qualified, 2 REFUSED -- ``--json`` names a
destination that cannot receive the record, so nothing was measured, or the
record was lost after the case ran.
"""
from __future__ import annotations

import argparse
import math
import pathlib

import numpy as np

SR = 48_000
N = SR
MAIN_HZ = 1_000.0
CONTAMINANT_HZ = 10.0
TARGET_SHIFT_PCT = 15.8


def centroid_hz(x: np.ndarray, lo: float = 0.0, hi: float = SR / 2) -> float:
    spectrum = np.abs(np.fft.rfft(x)) ** 2
    freqs = np.fft.rfftfreq(len(x), 1.0 / SR)
    mask = (freqs >= lo) & (freqs <= hi)
    return float((freqs[mask] * spectrum[mask]).sum() /
                 (spectrum[mask].sum() + 1e-30))


def case(contaminant_amplitude: float = 0.40) -> dict[str, float]:
    t = np.arange(N, dtype=float) / SR
    clean = np.sin(2 * np.pi * MAIN_HZ * t)
    contaminated = clean + contaminant_amplitude * np.sin(
        2 * np.pi * CONTAMINANT_HZ * t)
    global_before = centroid_hz(contaminated)
    global_after = centroid_hz(clean)
    protected_before = centroid_hz(contaminated, lo=20.0)
    protected_after = centroid_hz(clean, lo=20.0)
    return {
        "global_before_hz": global_before,
        "global_after_hz": global_after,
        "global_shift_pct": 100 * (global_after - global_before) / global_before,
        "protected_before_hz": protected_before,
        "protected_after_hz": protected_after,
        "protected_shift_pct": 100 * (protected_after - protected_before) /
        protected_before,
    }


def _prepare_destination(path: str) -> str | None:
    """Make ``path`` able to receive the record before the case runs: a
    refusal reason, or None.

    A missing parent is made here.  A directory given as the file, or a parent
    that cannot be made, is a refusal with nothing measured; found after the
    case instead, it was a ``FileNotFoundError`` and exit 1 -- the status of
    "measured and disagreed" (#2040)."""
    target = pathlib.Path(path)
    if target.is_dir():
        return f"--json {path} is a directory; name the file to write"
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        return f"--json {path}: its parent directory cannot be created ({exc.strerror or exc})"
    return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", type=str)
    args = parser.parse_args(argv)
    if args.json:
        refused = _prepare_destination(args.json)
        if refused:
            print(f"REFUSED: {refused}")
            return 2
    result = case()
    print("global centroid:    "
          f"{result['global_before_hz']:.3f} -> {result['global_after_hz']:.3f} Hz "
          f"({result['global_shift_pct']:+.3f}%)")
    print("20 Hz+ centroid:    "
          f"{result['protected_before_hz']:.3f} -> {result['protected_after_hz']:.3f} Hz "
          f"({result['protected_shift_pct']:+.3f}%)")
    if not (abs(result["global_shift_pct"]) >= TARGET_SHIFT_PCT - 0.2):
        return 1
    if not (abs(result["protected_shift_pct"]) < 0.01):
        return 1
    if args.json:
        import json
        try:
            with open(args.json, "w") as output:
                json.dump(result, output, indent=2)
        except OSError as exc:
            print(f"REFUSED: --json {args.json} was not written ({exc.strerror or exc}); "
                  "the case above was measured, its record does not exist")
            return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
