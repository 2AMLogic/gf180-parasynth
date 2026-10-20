#!/usr/bin/env python3
"""#592: compare a committed tom pitch-drop record with its re-run at a later head.

Every field except `provenance` is compared exactly (floats bit-for-bit, as
serialised). Prints each difference and exits 1 if there is any, 0 if the
records are identical.

Precondition, asserted at the point of use: before comparing, the comparator
is shown the re-run with one injected change (a numeric leaf nudged, or an
extra key when there is no numeric leaf) and must report it. If it cannot,
the result is REFUSED (exit 2) rather than "identical".

    tools/probes/tpd592_rerun_compare.py docs/scorecard/tom-pitch-drop-592/{development,rerun-8fedbf4/development}.json
"""
import copy
import json
import math
import sys


def diff(a, b, path="", out=None):
    out = [] if out is None else out
    if isinstance(a, dict) and isinstance(b, dict):
        for k in sorted(set(a) | set(b)):
            if path == "" and k == "provenance":
                continue
            if k not in a or k not in b:
                out.append(f"{path}/{k}: present in {'old' if k in a else 'new'} only")
            else:
                diff(a[k], b[k], f"{path}/{k}", out)
    elif isinstance(a, list) and isinstance(b, list):
        if len(a) != len(b):
            out.append(f"{path}: length {len(a)} -> {len(b)}")
        else:
            for i, (x, y) in enumerate(zip(a, b)):
                diff(x, y, f"{path}[{i}]", out)
    elif isinstance(a, float) and isinstance(b, float) and math.isnan(a) and math.isnan(b):
        pass
    elif type(a) is not type(b) or a != b:
        out.append(f"{path}: {a!r} -> {b!r}")
    return out


def _nudge(rec):
    """Return a copy of rec with exactly one change the comparator must see."""
    m = copy.deepcopy(rec)
    stack = [m]
    while stack:
        node = stack.pop()
        items = node.items() if isinstance(node, dict) else enumerate(node) if isinstance(node, list) else ()
        for k, v in items:
            if node is m and k == "provenance":
                continue
            if isinstance(v, float) and math.isfinite(v):
                node[k] = v + max(abs(v), 1.0) * 1e-9
                return m
            if isinstance(v, (dict, list)):
                stack.append(v)
    m["__injected__"] = True
    return m


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 2:
        print(__doc__)
        return 2
    old, new = (json.loads(open(p).read()) for p in argv)
    if not diff(new, _nudge(new)):
        print("REFUSED: comparator did not see an injected change")
        return 2
    d = diff(old, new)
    for line in d:
        print(line)
    print(f"{argv[0]} vs {argv[1]}: {'IDENTICAL' if not d else f'{len(d)} differences'} (provenance excluded)")
    return 1 if d else 0


if __name__ == "__main__":
    raise SystemExit(main())
