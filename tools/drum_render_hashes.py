#!/usr/bin/env python3
"""SHA-256 of the 16 shipped drum renders, as `perceptual_gate.rank` sees them (#379).

Used to show a ranking's inputs are unchanged across a rebase without re-running
the gate: run it in two checkouts and compare. Python model only, single process.

    python tools/drum_render_hashes.py [--root <checkout>] > hashes.json

The hash covers the float64 samples and the rate. An incomplete kit or an
empty, silent or non-finite render refuses with exit 2 before publishing hashes.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import sys

import numpy as np


# The shipped kit (model/drums_fx.py SOUND_NAMES). Keep the expectation on
# this side of --root so a target checkout cannot redefine its own coverage.
EXPECTED_SOUNDS = frozenset("BD SD LT LC MT MC HT HC RS CL CP MA CB CY OH CH".split())


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--root", type=pathlib.Path, default=pathlib.Path(__file__).resolve().parents[1])
    a = ap.parse_args(argv)
    root = a.root.resolve()
    sys.path[:0] = [str(root / "tools"), str(root / "model")]
    import perceptual_gate as pg
    if pathlib.Path(pg.__file__).resolve() != (root / "tools/perceptual_gate.py").resolve():
        print(f"REFUSED: perceptual_gate was not loaded from {root}/tools", file=sys.stderr)
        return 2
    sounds = getattr(pg, "SOUNDS16", None)
    if (not isinstance(sounds, (tuple, list)) or len(sounds) != len(EXPECTED_SOUNDS)
            or not all(isinstance(s, str) for s in sounds) or set(sounds) != EXPECTED_SOUNDS):
        print(f"REFUSED: expected the 16 distinct shipped voices in {root}, got {sounds!r}", file=sys.stderr)
        return 2
    out, bad = {}, []
    for s in sounds:
        y, sr = pg.render_ours(s)
        y = np.ascontiguousarray(np.asarray(y, dtype=np.float64))
        if y.size == 0 or not np.all(np.isfinite(y)) or not np.any(y):
            bad.append(s)
        out[s] = {"n": int(y.size), "sr": int(sr),
                  "sha256": hashlib.sha256(y.tobytes() + str(int(sr)).encode()).hexdigest()}
    if bad:
        print(f"REFUSED: empty, silent or non-finite render: {bad}", file=sys.stderr)
        return 2
    print(json.dumps({"root": str(root), "sounds": out}, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
