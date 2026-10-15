#!/usr/bin/env python3
"""Snapshot `ladder_headroom._ring_ratio` over the audit grid and the #548
defect points, as JSON, so a before/after comparison is a diff of two files.

    python tools/ring_ratio_compare.py snapshot OUT.json
    python tools/ring_ratio_compare.py diff BEFORE.json AFTER.json

A point that raises is recorded as {"refused": reason}; nothing is dropped.
"""
import json
import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, os.path.join(ROOT, "model"))
sys.path.insert(0, os.path.join(ROOT, "audition"))

DEFECT_POINTS = ((1131.0, 0.97), (800.0, 0.98))


def snapshot() -> dict:
    import ladder_headroom as lh
    import reference_rigs as rr
    dev = rr.OurLadder("ours")
    pts = [(c, r) for r in lh.REPORT_RES for c in lh.REPORT_CUTS] + list(DEFECT_POINTS)
    out = {}
    for c, r in pts:
        try:
            v = {"ratio": lh._ring_ratio(dev, c, r)}
        except Exception as exc:                     # noqa: BLE001 -- recorded, not hidden
            v = {"refused": f"{type(exc).__name__}: {exc}"}
        out[f"{c:g}@{r}"] = v
    return out


def diff(a: dict, b: dict) -> int:
    n = 0
    for k in sorted(set(a) | set(b)):
        if a.get(k) != b.get(k):
            n += 1
            print(f"CHANGED {k}: {a.get(k)} -> {b.get(k)}")
    print(f"{n} of {len(set(a) | set(b))} points changed")
    return 0


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "snapshot":
        with open(sys.argv[2], "w") as fh:
            json.dump(snapshot(), fh, indent=1, sort_keys=True)
    elif len(sys.argv) == 4 and sys.argv[1] == "diff":
        sys.exit(diff(json.load(open(sys.argv[2])), json.load(open(sys.argv[3]))))
    else:
        sys.exit(__doc__)
