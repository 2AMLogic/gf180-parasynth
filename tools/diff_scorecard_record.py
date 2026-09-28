#!/usr/bin/env python3
"""Compare a regenerated scorecard record against the one already committed.

Why this exists as a tool rather than an eyeballed `git diff`: #429 required a
record to be regenerated from a clean tree because the committed one was written
from a dirty one, and the load-bearing claim of that regeneration is *"no figure
moved"*. A `git diff` cannot establish that -- it shows textual lines, so a
reordered key reads as a change and a number that moved in the fourth decimal
reads the same as one that moved in the first. This walks both records as trees
and reports every numeric leaf that moved, by how much, against an explicit
tolerance.

It REFUSES rather than reports when it cannot answer: a record that is not a
JSON object, a baseline that does not exist at the named revision, or a
structural difference (a key present in one side only) all exit 2 with
`REFUSED`, because none of those are "the figures agree" and none are "the
figures moved".

    python3 tools/diff_scorecard_record.py docs/scorecard/.../tone-render.json
    python3 tools/diff_scorecard_record.py NEW.json --baseline OLD.json

Exit status
    0  every numeric leaf agrees within tolerance (structure identical)
    1  at least one figure moved by more than the tolerance
    2  REFUSED -- the comparison could not be made
"""
from __future__ import annotations

import argparse
import json
import math
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Provenance fields are *expected* to move when a record is regenerated -- that
# is the entire point of regenerating it -- so they are reported separately and
# never counted as a figure moving.
PROVENANCE_KEYS = ("commit", "sources_dirty")


class Refused(Exception):
    """The comparison cannot be made. Distinct from "they differ"."""


def _leaves(obj, prefix=""):
    """Flatten to {dotted.path: leaf}. Lists are indexed."""
    out = {}
    if isinstance(obj, dict):
        for k, v in obj.items():
            out.update(_leaves(v, f"{prefix}.{k}" if prefix else str(k)))
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            out.update(_leaves(v, f"{prefix}[{i}]"))
    else:
        out[prefix] = obj
    return out


def _load(path: Path):
    try:
        d = json.loads(path.read_text())
    except OSError as exc:
        raise Refused(f"cannot read {path}: {exc}") from exc
    except ValueError as exc:
        raise Refused(f"{path} is not JSON: {exc}") from exc
    if not isinstance(d, dict):
        raise Refused(f"{path} is a {type(d).__name__}, not a record object")
    return d


def _load_rev(rev: str, relpath: str):
    r = subprocess.run(["git", "show", f"{rev}:{relpath}"], cwd=ROOT,
                       capture_output=True, text=True)
    if r.returncode != 0:
        raise Refused(f"{relpath} does not exist at {rev}: "
                      f"{r.stderr.strip() or 'git show failed'}")
    try:
        d = json.loads(r.stdout)
    except ValueError as exc:
        raise Refused(f"{relpath} at {rev} is not JSON: {exc}") from exc
    if not isinstance(d, dict):
        raise Refused(f"{relpath} at {rev} is a {type(d).__name__}, not a record")
    return d


def compare(old: dict, new: dict, rel_tol: float, abs_tol: float):
    """(moved, provenance, notes). Raises Refused on a structural difference."""
    a, b = _leaves(old), _leaves(new)
    only_old = sorted(set(a) - set(b))
    only_new = sorted(set(b) - set(a))
    if only_old or only_new:
        raise Refused(
            "the two records are not the same shape, so no figure can be "
            "compared. only in baseline: "
            f"{only_old or 'none'}; only in new: {only_new or 'none'}")

    moved, provenance, notes = [], [], []
    for k in sorted(a):
        av, bv = a[k], b[k]
        top = k.split(".")[0].split("[")[0]
        if top in PROVENANCE_KEYS:
            if av != bv:
                provenance.append((k, av, bv))
            continue
        if isinstance(av, bool) or isinstance(bv, bool):
            # a bool is an int in Python; compare it as an identity, and treat a
            # bool/number swap as a type change rather than a numeric move,
            # because `1.0` vs `true` is exactly the #429 defect.
            if type(av) is not type(bv) or av != bv:
                notes.append((k, f"{type(av).__name__} {av!r}",
                              f"{type(bv).__name__} {bv!r}"))
            continue
        if isinstance(av, (int, float)) and isinstance(bv, (int, float)):
            if math.isnan(av) and math.isnan(bv):
                continue
            if not math.isclose(av, bv, rel_tol=rel_tol, abs_tol=abs_tol):
                moved.append((k, float(av), float(bv)))
            continue
        if av != bv:
            notes.append((k, repr(av), repr(bv)))
    return moved, provenance, notes


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("new", type=Path, help="the regenerated record")
    p.add_argument("--baseline", type=Path,
                   help="compare against this file instead of a git revision")
    p.add_argument("--baseline-rev", default="HEAD",
                   help="revision to read the same path from (default HEAD)")
    p.add_argument("--rel-tol", type=float, default=1e-9)
    p.add_argument("--abs-tol", type=float, default=0.0)
    a = p.parse_args(argv)

    try:
        new = _load(a.new)
        if a.baseline:
            old = _load(a.baseline)
            where = str(a.baseline)
        else:
            try:
                rel = a.new.resolve().relative_to(ROOT).as_posix()
            except ValueError as exc:
                raise Refused(f"{a.new} is outside {ROOT}, so there is no "
                              "path to read from a revision; pass --baseline"
                              ) from exc
            old = _load_rev(a.baseline_rev, rel)
            where = f"{a.baseline_rev}:{rel}"
        moved, provenance, notes = compare(old, new, a.rel_tol, a.abs_tol)
    except Refused as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 2

    print(f"baseline: {where}")
    print(f"new:      {a.new}")
    print(f"tolerance: rel={a.rel_tol:g} abs={a.abs_tol:g}")
    for k, av, bv in provenance:
        print(f"  provenance  {k}: {av!r} -> {bv!r}")
    for k, av, bv in notes:
        print(f"  TYPE/VALUE  {k}: {av} -> {bv}")
    for k, av, bv in moved:
        d = bv - av
        print(f"  MOVED       {k}: {av!r} -> {bv!r}  (delta {d:+.6g})")
    n = len(moved) + len(notes)
    total = len(_leaves(new))
    if n == 0:
        print(f"no figure moved: {total} leaves compared, "
              f"{len(provenance)} provenance field(s) updated")
        return 0
    print(f"{n} of {total} leaves changed")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
