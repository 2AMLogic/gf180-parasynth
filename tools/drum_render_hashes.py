#!/usr/bin/env python3
"""SHA-256 of the 16 shipped drum renders, as `perceptual_gate.rank` sees them (#379).

Used to show a ranking's inputs are unchanged across a rebase without re-running
the gate: run it in two checkouts and compare. Python model only, single process.

    python tools/drum_render_hashes.py [--root <checkout>] > hashes.json

The hash covers the float64 samples and the rate. REFUSES (exit 2) if any render
is empty or non-finite, because a hash of silence or NaN would compare equal
across trees and prove nothing.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import sys

import numpy as np


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--root", type=pathlib.Path, default=pathlib.Path(__file__).resolve().parents[1])
    a = ap.parse_args(argv)
    root = a.root.resolve()
    sys.path[:0] = [str(root / "tools"), str(root / "model")]
    import perceptual_gate as pg
    assert pathlib.Path(pg.__file__).resolve().is_relative_to(root), pg.__file__
    out, bad = {}, []
    for s in pg.SOUNDS16:
        y, sr = pg.render_ours(s)
        y = np.ascontiguousarray(np.asarray(y, dtype=np.float64))
        if y.size == 0 or not np.all(np.isfinite(y)) or not np.any(y):
            bad.append(s)
        out[s] = {"n": int(y.size), "sr": int(sr),
                  "sha256": hashlib.sha256(y.tobytes() + str(int(sr)).encode()).hexdigest()}
    print(json.dumps({"root": str(root), "sounds": out}, indent=1))
    if bad:
        print(f"REFUSED: empty, silent or non-finite render: {bad}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
