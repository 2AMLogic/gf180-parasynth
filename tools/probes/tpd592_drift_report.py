#!/usr/bin/env python3
"""#592: say what KIND of difference the record comparator found.

`tpd592_rerun_compare.py` says how many leaves differ between two records and
exits 1 on any. That alone cannot be read: 22 leaves of `E` in a zero-drop row
(about 1e-5, against a bound of 0.004) and 22 rows that changed class are the
same count and opposite news. This reports, per field, how many rows differ,
which estimator classes and true case drops they carry, and the largest
absolute change.

    tools/probes/tpd592_drift_report.py docs/scorecard/tom-pitch-drop-592/{knownanswer,rerun-8fedbf4/knownanswer}.json

A NUMERIC leaf has the same numeric type on both sides (a bool is not a number here:
`ok: true -> 1` is a changed verdict, not a drift of one). Everything else --
a class, a verdict, a reason, a case identity, a key on one side only, a
different number of rows -- is STRUCTURAL and listed. `provenance` is not
compared, as in the comparator.

Exit status is 0 for a report, and the report's reader decides what the
differences mean: it does not judge whether a run still holds. A record that
parses but cannot be read -- not a JSON object, or one without `rows` -- is
REFUSED (exit 2) naming the file, rather than a traceback (exit 1, which
tools/exit_contract.py reads as FAIL) or a report on nothing (#1757).
"""
import json
import pathlib
import sys
from collections import defaultdict

_REFUSAL_TOOLS = str(pathlib.Path(__file__).resolve().parents[2] / "tools")
if _REFUSAL_TOOLS not in sys.path:
    sys.path.append(_REFUSAL_TOOLS)
import refusal  # noqa: E402
import manifest as mf  # noqa: E402

if __package__:
    from .tpd592_rerun_compare import diff, field_diff
else:
    from tpd592_rerun_compare import diff, field_diff


class Refused(refusal.Refused, RuntimeError):
    """A stated missing precondition, distinct from pass and from fail."""


def _num(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def classify(a: dict, b: dict):
    """-> (numeric, structural, summary): `numeric` maps a row field to one
    (row index, class, true case E, abs change) per differing row;
    `structural` and `summary` are lists of strings."""
    numeric, structural = defaultdict(list), []
    summary = [k for k in sorted(set(a) | set(b))
               if k not in ("rows", "provenance") and field_diff(a, b, k)]
    old_rows, new_rows = a.get("rows"), b.get("rows")
    if not isinstance(old_rows, list) or not isinstance(new_rows, list):
        return numeric, field_diff(a, b, "rows"), summary
    if len(old_rows) != len(new_rows):
        structural.append(f"rows: {len(old_rows)} -> {len(new_rows)}")
    for i, (x, y) in enumerate(zip(old_rows, new_rows)):
        path = f"rows[{i}]"
        if not isinstance(x, dict) or not isinstance(y, dict):
            structural.extend(diff(x, y, path))
            continue
        for k in sorted(set(x) | set(y)):
            changes = field_diff(x, y, k, path)
            if not changes:
                continue
            u, v = x.get(k), y.get(k)
            if _num(u) and type(u) is type(v):
                case = x.get("case")
                drop = case.get("E") if isinstance(case, dict) else None
                numeric[k].append((i, x.get("class"), drop, abs(u - v)))
            else:
                structural.extend(changes)
    return numeric, structural, summary


def report(a: dict, b: dict) -> str:
    numeric, structural, summary = classify(a, b)
    lines = ["numeric leaves:" if numeric else "numeric leaves: none"]
    for k, d in sorted(numeric.items()):
        classes = ", ".join(sorted({str(c) for _, c, _e, _d in d}))
        values = {e for _, _, e, _ in d if _num(e)}
        labels = [f"{e:g}" for e in sorted(values, reverse=True)]
        if any(not _num(e) for _, _, e, _ in d):
            labels.append("unknown")
        drops = ", ".join(labels)
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
    try:
        recs = [json.loads(open(p).read()) for p in paths]
        for path, rec in zip(paths, recs):
            mf.require_keys(rec, "rows", f"the record at {path}", refused=Refused)
    except Refused as e:
        print(f"REFUSED: {e}", file=sys.stderr)
        return 2
    print(report(*recs))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
