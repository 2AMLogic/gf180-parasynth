#!/usr/bin/env python3
"""Qualify the M1A attack measurement against the known-answer suite.

    qualify_m1a_attack.py suite v3      run the suite, write suite-v3.json
    qualify_m1a_attack.py domain        derive + publish the qualified domain

THE DOMAIN IS STATED IN OBSERVABLES. On real audio the true shape, spectrum
and duration are unknown, so a domain written in terms of them ("shapes p in
{0.5, 1, 2}") cannot be checked against a reading. The domain is therefore a
region of what the fit RETURNS -- its 10-90 % value, its explained-energy
ratio, whether it sits on a search boundary -- and its error bound is the
worst known-answer error among every suite case whose fit landed in that
region, whatever the case's true shape or spectrum. A reading inside the
region inherits that bound; a reading outside it is UNQUALIFIED.

The rule (fixed before applying it to any M1A audio):

* a fit on a search boundary (the 32-sample ramp minimum or the maximum) is
  never qualified, whatever value it reports;
* for each explained-ratio floor in EXPLAINED_FLOORS (strictest first), find
  the widest contiguous range [lo, hi] of the reported 10-90 value, with lo
  and hi on VALUE_EDGES, in which every suite case has |error| <= BOUND_MS
  and there are at least MIN_CASES cases;
* the published domain is the whole table (every floor with its range and
  measured bound); a reading is qualified if ANY row covers it, and it
  inherits that row's measured bound.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import attack_known_answer as kaa                                    # noqa: E402

OUT = ROOT / "docs/scorecard/mono-m1a-miniv3/attack-qualification"
BOUND_MS = 1.0          # the v2 gate's refusal level, unchanged
MIN_CASES = 30
EXPLAINED_FLOORS = (0.99, 0.98, 0.97, 0.95, 0.9, 0.8, 0.7)
VALUE_EDGES = (0.25, 0.5, 0.75, 1.0, 1.5, 2.0, 3.0, 4.0, 5.0, 6.0, 8.0, 10.0,
               12.0, 15.0, 20.0)


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def measurable(rows):
    return [r for r in rows if "refused" not in r and not r.get("search_boundary")]


def widest_range(rows, floor, bound=BOUND_MS, min_cases=MIN_CASES):
    """Widest contiguous [lo, hi] of reported value (on VALUE_EDGES) where
    every case with explained >= floor has |error| <= bound; None if none
    holds MIN_CASES cases."""
    pool = [r for r in measurable(rows) if r["explained_ratio"] >= floor]
    best = None
    edges = VALUE_EDGES
    for i, lo in enumerate(edges):
        for hi in edges[i + 1:]:
            inside = [r for r in pool if lo <= r["measured_1090_ms"] <= hi]
            if len(inside) < min_cases:
                continue
            worst = max(abs(r["error_ms"]) for r in inside)
            if worst > bound:
                continue
            key = (hi - lo, len(inside))
            if best is None or key > best[0]:
                best = (key, {"explained_min": floor, "lo_ms": lo, "hi_ms": hi,
                              "cases": len(inside),
                              "max_abs_error_ms": round(worst, 4),
                              "mean_error_ms": round(sum(r["error_ms"] for r in inside)
                                                     / len(inside), 4)})
    return best[1] if best else None


def derive_domain(rows, bound=BOUND_MS, min_cases=MIN_CASES):
    table = []
    for floor in EXPLAINED_FLOORS:
        row = widest_range(rows, floor, bound, min_cases)
        if row:
            table.append(row)
    return table


def covering_row(domain, fit):
    """The domain row that qualifies this fit, or None."""
    if fit.get("search_boundary"):
        return None
    for row in domain:
        if (fit["explained_ratio"] >= row["explained_min"]
                and row["lo_ms"] <= fit["attack_10_90_ms"] <= row["hi_ms"]):
            return row
    return None


def breakdown(rows):
    """Error by true condition -- where the estimator fails, for the doc."""
    out = {}
    for key in ("spectrum", "shape"):
        for v in sorted({r[key] for r in rows}):
            sel = [r for r in rows if r[key] == v and "refused" not in r]
            ok = measurable(sel)
            out[f"{key}={v}"] = {
                "cases": len(sel),
                "search_boundary": sum(1 for r in sel if r.get("search_boundary")),
                "max_abs_error_ms_off_boundary": round(max((abs(r["error_ms"]) for r in ok),
                                                           default=0.), 3),
                "explained_range": [min(r["explained_ratio"] for r in sel),
                                    max(r["explained_ratio"] for r in sel)]}
    return out


def git(*args):
    return subprocess.check_output(["git", *args], cwd=ROOT, text=True).strip()


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("suite")
    s.add_argument("estimator", choices=("v2", "v3"))
    sub.add_parser("domain")
    a = ap.parse_args(argv)
    OUT.mkdir(parents=True, exist_ok=True)
    sources = {rel: sha(ROOT / rel) for rel in (
        "tools/attack_known_answer.py", "tools/attack_fit_v3.py",
        "tools/measure_mono_m1a_reference.py")}
    if a.cmd == "suite":
        rows = kaa.run_suite(estimator=a.estimator)
        (OUT / f"suite-{a.estimator}.json").write_text(json.dumps(
            {"schema": "m1a-attack-known-answer-v1", "estimator": a.estimator,
             "source_commit": git("rev-parse", "HEAD"),
             "source_dirty": bool(git("status", "--porcelain", "--", "tools")),
             "sources_sha256": sources, "rows": rows}, indent=1) + "\n")
        print(f"{len(rows)} cases -> suite-{a.estimator}.json")
        return 0
    report = {"schema": "m1a-attack-qualification-v1", "bound_ms": BOUND_MS,
              "min_cases": MIN_CASES, "source_commit": git("rev-parse", "HEAD")}
    for est in ("v2", "v3"):
        doc = json.loads((OUT / f"suite-{est}.json").read_text())
        rows = doc["rows"]
        report[est] = {"cases": len(rows),
                       "search_boundary_hits": sum(1 for r in rows if r.get("search_boundary")),
                       "domain": derive_domain(rows),
                       "breakdown": breakdown(rows)}
    (OUT / "qualification.json").write_text(json.dumps(report, indent=2) + "\n")
    for est in ("v2", "v3"):
        print(est, "boundary hits", report[est]["search_boundary_hits"])
        for row in report[est]["domain"]:
            print("  ", row)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
