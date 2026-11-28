#!/usr/bin/env python3
"""#592: compare a committed tom pitch-drop record with its re-run at a later head.

Every field except `provenance` is compared exactly (floats bit-for-bit, as
serialised). Prints each difference and exits 1 if there is any, 0 if the
records are identical.

Precondition, asserted at the point of use: before comparing, the comparator
is shown the re-run with one injected change (a numeric leaf nudged, or an
extra key when there is no numeric leaf) and must report it. If it cannot,
the result is REFUSED (exit 2) rather than "identical".

A record that parses but cannot be read -- not a JSON object, or one without
`rows` -- is REFUSED (exit 2) naming the file: not a traceback (exit 1, which
tools/exit_contract.py reads as FAIL, "the comparison ran and disagreed"), and
not `IDENTICAL` on two files that carry nothing to compare (#1757).

    tools/probes/tpd592_rerun_compare.py docs/scorecard/tom-pitch-drop-592/{development,rerun-8fedbf4/development}.json
"""
import copy
import json
import math
import pathlib
import sys

_REFUSAL_TOOLS = str(pathlib.Path(__file__).resolve().parents[2] / "tools")
if _REFUSAL_TOOLS not in sys.path:
    sys.path.append(_REFUSAL_TOOLS)
import refusal  # noqa: E402
import manifest as mf  # noqa: E402


class Refused(refusal.Refused, RuntimeError):
    """A stated missing precondition, distinct from pass and from fail."""


def field_diff(a, b, key, path=""):
    """Compare one record field, including its presence on either side."""
    field = f"{path}/{key}"
    if key in a and key in b:
        return diff(a[key], b[key], field)
    if key in a or key in b:
        return [f"{field}: present in {'old' if key in a else 'new'} only"]
    return []


def diff(a, b, path="", out=None):
    out = [] if out is None else out
    if isinstance(a, dict) and isinstance(b, dict):
        for k in sorted(set(a) | set(b)):
            if path == "" and k == "provenance":
                continue
            out.extend(field_diff(a, b, k, path))
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
    try:
        for path, rec in zip(argv, (old, new)):
            mf.require_keys(rec, "rows", f"the record at {path}", refused=Refused)
    except Refused as e:
        print(f"REFUSED: {e}")
        return 2
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
