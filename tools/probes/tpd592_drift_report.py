#!/usr/bin/env python3
"""#592: say what KIND of difference the record comparator found.

`tpd592_rerun_compare.py` says how many leaves differ between two records and
exits 1 on any. That alone cannot be read: 22 leaves of `E` in a zero-drop row
(about 1e-5, against a bound of 0.004) and 22 rows that changed class are the
same count and opposite news. This reports, per field, how many rows differ,
which estimator classes and true case drops they carry, and the largest
absolute change.

    tools/probes/tpd592_drift_report.py docs/scorecard/tom-pitch-drop-592/{knownanswer,rerun-8fedbf4/knownanswer}.json

A NUMERIC leaf is a number on both sides (a bool is not a number here:
`ok: true -> 1` is a changed verdict, not a drift of one). Everything else --
a class, a verdict, a reason, a case identity, a key on one side only, a
different number of rows -- is STRUCTURAL and listed. `provenance` is not
compared, as in the comparator.

Exit status is always 0: this is a report, and its reader decides what the
differences mean. It does not judge whether a run still holds.
"""
import json
import sys
from collections import defaultdict


def _num(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _same(u, v) -> bool:
    """Equal as the record serialises them. Python's `True == 1`, so a verdict
    that turned from `true` to `1` must not read as unchanged."""
    return isinstance(u, bool) == isinstance(v, bool) and u == v


def classify(a: dict, b: dict):
    """-> (numeric, structural, summary): `numeric` maps a row field to one
    (row index, class, true case E, abs change) per differing row;
    `structural` and `summary` are lists of strings."""
    numeric, structural = defaultdict(list), []
    if len(a["rows"]) != len(b["rows"]):
        structural.append(f"rows: {len(a['rows'])} -> {len(b['rows'])}")
    for i, (x, y) in enumerate(zip(a["rows"], b["rows"])):
        for k in sorted(set(x) | set(y)):
            u, v = x.get(k), y.get(k)
            if _same(u, v):
                continue
            if _num(u) and _num(v):
                numeric[k].append((i, x.get("class"), (x.get("case") or {}).get("E"), abs(u - v)))
            else:
                structural.append(f"rows[{i}]/{k}: {u!r} -> {v!r}")
    summary = [k for k in sorted(set(a) | set(b)) if k not in ("rows", "provenance") and not _same(a.get(k), b.get(k))]
    return numeric, structural, summary


def report(a: dict, b: dict) -> str:
    numeric, structural, summary = classify(a, b)
    lines = ["numeric leaves:" if numeric else "numeric leaves: none"]
    for k, d in sorted(numeric.items()):
        classes = ", ".join(sorted({str(c) for _, c, _e, _d in d}))
        drops = ", ".join(f"{e:g}" for e in sorted({float(e) for _i, _c, e, _d in d}, reverse=True))
        biggest = max(d_[3] for d_ in d)
        lines.append(f"  {k}: {len(d)} rows; class {classes}; case E {{{drops}}}; max |change| {biggest:.3g}")
    lines.append("structural differences: " + ("; ".join(structural) if structural else "none"))
    lines.append("summary fields that differ: " + (", ".join(summary) if summary else "none"))
    return "\n".join(lines)


def main(argv=None) -> int:
    paths = argv if argv is not None else sys.argv[1:]
    if len(paths) != 2:
        print(__doc__.strip().splitlines()[0] + "\nusage: tpd592_drift_report.py A.json B.json", file=sys.stderr)
        return 2
    a, b = (json.loads(open(p).read()) for p in paths)
    print(report(a, b))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
