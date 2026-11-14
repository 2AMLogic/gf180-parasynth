#!/usr/bin/env python3
"""Derive a READABLE VERDICT for a committed ORFS evidence directory.

Why this exists. `docs/dag.json`'s S1 node -- "Routed on gf180, DRC clean" --
pointed at `6_report.json`, and `tools/compile_dag.py` rendered it GREEN because
the file was on disk (#791). Two separate problems were hiding behind that:

  1. `6_report.json` declares no verdict at all. It is a flat metric dump, so
     there is nothing for a tool to honour.
  2. **It does not contain the fact the node's name asserts.** `6_report.json`
     has no DRC count of any kind -- `grep -ic drc` on it returns 0. The router
     DRC number lives in `5_2_route.json`. So a verdict derived from
     `6_report.json` alone would pass a run with 17,929 DRC errors, which is
     exactly the count this run's own iteration 0 had.

So the verdict is derived from the stage files that actually carry each fact,
and written out as JSON with a top-level `passed` key that compile_dag can read.

**REFUSED is a first-class outcome here** (CLAUDE.md). A required metric that is
absent does not default to pass or to fail: this tool exits 2 and writes nothing,
because a verdict assembled from keys that were not there looks exactly like a
verdict assembled from keys that were.

Usage:
  make_verdict.py <evidence-dir> [-o verdict.json]   write the verdict
  make_verdict.py <evidence-dir> --print             stdout only, write nothing
  make_verdict.py <evidence-dir> --check             exit 1 if the committed
                                                     verdict.json differs from
                                                     what this run derives

Exit: 0 verdict written/matches, 1 --check mismatch, 2 REFUSED.
"""
from __future__ import annotations
import argparse, json, os, sys


class Refusal(Exception):
    """A precondition of the measurement failed. Not a verdict either way."""


# Every gating fact, with the stage file that actually carries it. `must_be_zero`
# facts are counts; `must_be_positive` facts are slacks in ns.
#
# Rule 8 (docs/verification-rules.md): what satisfies this check while violating
# its intent? Two answers, both guarded elsewhere and named here so the next
# reader does not have to rediscover them:
#
#   * A HAND-EDITED verdict.json. The substring/key check in compile_dag cannot
#     tell a derived file from a typed one. `--check` plus
#     `test_make_verdict.py::test_the_committed_verdict_matches_the_committed_metrics`
#     is the guard: the committed verdict must equal what these inputs derive.
#   * A STALE evidence directory -- the right numbers for the wrong RTL. Nothing
#     in this file can see that; S1's `covers` list in docs/dag.json is what
#     turns the node STALE when the RTL moves, and that is where it belongs.
ZERO_COUNTS = [
    ("5_2_route.json", "detailedroute__route__drc_errors",
     "detailed-route DRC errors"),
    ("5_2_route.json", "detailedroute__antenna__violating__nets",
     "antenna-violating nets"),
    ("5_2_route.json", "detailedroute__antenna__violating__pins",
     "antenna-violating pins"),
    ("5_2_route.json", "detailedroute__flow__errors__count",
     "router flow errors"),
    ("6_report.json", "finish__flow__errors__count", "finish flow errors"),
    ("6_report.json", "finish__timing__drv__setup_violation_count",
     "setup violations"),
    ("6_report.json", "finish__timing__drv__hold_violation_count",
     "hold violations"),
]
POSITIVE_SLACKS = [
    ("6_report.json", "finish__timing__setup__ws", "setup worst slack (ns)"),
    ("6_report.json", "finish__timing__hold__ws", "hold worst slack (ns)"),
]

# Recorded, NOT gated -- and recorded precisely so it cannot be quietly dropped.
# `docs/pnr-synth-top.md` section 4.1 already discloses the 2 max-capacitance
# violations on this run. They do not gate S1 because S1's claim is a ROUTED,
# geometrically clean die: max-cap is an electrical DRV against the ORFS
# `max_cap` limit, measured here at a 12.29 MHz target with +43.6 ns of setup
# slack. Gating on it would make the node assert a failure the repo's own
# analysis says this run does not have. That is a JUDGEMENT CALL, which is why
# the number is in the output rather than in a comment.
DISCLOSED = [
    ("6_report.json", "finish__timing__drv__max_cap",
     "max-capacitance violations"),
    ("6_report.json", "finish__timing__drv__max_cap_limit",
     "max-capacitance worst limit"),
    ("6_report.json", "finish__timing__drv__max_slew", "max-slew violations"),
    ("6_report.json", "finish__timing__fmax", "ORFS fmax (Hz)"),
    ("5_2_route.json", "detailedroute__route__wirelength", "routed wirelength (um)"),
    ("6_report.json", "finish__design__instance__utilization", "core utilisation"),
]

NOT_ESTABLISHED = [
    "No sign-off DRC: ORFS's gf180 platform ships no KLayout DRC deck, so every "
    "DRC count here is the detailed router's own (docs/pnr-synth-top.md s.8).",
    "No LVS was run.",
    "A clean route says nothing about whether the design computes the right "
    "samples (docs/verification-rules.md rule 3).",
]


def _load(evidence_dir: str, fname: str) -> dict:
    path = os.path.join(evidence_dir, fname)
    if not os.path.exists(path):
        raise Refusal(f"{path} does not exist -- nothing to derive a verdict from")
    try:
        doc = json.load(open(path, encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as e:
        raise Refusal(f"{path} is present but unreadable ({type(e).__name__}: {e})")
    if not isinstance(doc, dict):
        raise Refusal(f"{path} holds a {type(doc).__name__}, not an ORFS metric object")
    return doc


def _metric(docs: dict, fname: str, key: str) -> float:
    """Exact key lookup. REFUSES on absence: a missing metric is not a zero.

    Deliberately not a suffix match like `summarize.py`'s `pick()` -- a suffix
    match silently picks a per-corner or per-class variant, and a verdict must
    name the exact metric it read.
    """
    if key not in docs[fname]:
        raise Refusal(f"{fname} has no {key!r}; this tool will not guess it "
                      f"(a verdict built from absent keys looks exactly like "
                      f"one built from present keys)")
    v = docs[fname][key]
    if not isinstance(v, (int, float)) or isinstance(v, bool):
        raise Refusal(f"{fname}:{key} is {v!r}, not a number")
    return v


def derive(evidence_dir: str) -> dict:
    """The verdict for one committed ORFS evidence directory. Raises Refusal."""
    needed = sorted({f for f, _, _ in ZERO_COUNTS + POSITIVE_SLACKS + DISCLOSED})
    docs = {f: _load(evidence_dir, f) for f in needed}

    checks, failures = [], []
    for fname, key, label in ZERO_COUNTS:
        v = _metric(docs, fname, key)
        ok = v == 0
        checks.append({"label": label, "source": fname, "metric": key,
                       "value": v, "requires": "== 0", "ok": ok})
        if not ok:
            failures.append(f"{label} = {v} (want 0)")
    for fname, key, label in POSITIVE_SLACKS:
        v = _metric(docs, fname, key)
        ok = v > 0
        checks.append({"label": label, "source": fname, "metric": key,
                       "value": v, "requires": "> 0", "ok": ok})
        if not ok:
            failures.append(f"{label} = {v} (want > 0)")

    disclosed = [{"label": label, "source": fname, "metric": key,
                  "value": _metric(docs, fname, key)}
                 for fname, key, label in DISCLOSED]

    passed = not failures
    return {
        "passed": passed,
        "detail": ("routed, 0 router DRC / 0 antenna / 0 setup+hold violations, "
                   "positive setup and hold slack"
                   if passed else "; ".join(failures)),
        "claim": "Routed on gf180 with zero detailed-route DRC and antenna "
                 "violations and positive setup/hold slack.",
        "derived_by": "pnr/orfs/make_verdict.py",
        "evidence_dir": os.path.basename(os.path.normpath(evidence_dir)),
        "checks": checks,
        "disclosed_not_gating": disclosed,
        "disclosed_reason":
            "max-capacitance violations are recorded, not gated: S1's claim is a "
            "routed, geometrically clean die, and max-cap is an electrical DRV "
            "against the ORFS max_cap limit at a target with +43.6 ns of setup "
            "slack. docs/pnr-synth-top.md section 4.1 discloses the same number. "
            "Gating on it would assert a failure the repo's own analysis of this "
            "run does not find.",
        "not_established_by_this_verdict": NOT_ESTABLISHED,
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("evidence_dir")
    ap.add_argument("-o", "--out", default=None,
                    help="output path (default: <evidence-dir>/verdict.json)")
    ap.add_argument("--print", dest="to_stdout", action="store_true",
                    help="print the verdict and write nothing")
    ap.add_argument("--check", action="store_true",
                    help="exit 1 if the existing verdict differs from the derived one")
    a = ap.parse_args(argv)

    try:
        v = derive(a.evidence_dir)
    except Refusal as e:
        print(f"REFUSED: {e}", file=sys.stderr)
        return 2

    text = json.dumps(v, indent=2, sort_keys=True) + "\n"
    out = a.out or os.path.join(a.evidence_dir, "verdict.json")

    if a.to_stdout:
        sys.stdout.write(text)
        return 0
    if a.check:
        if not os.path.exists(out):
            print(f"{out} does not exist -- run make_verdict.py to write it",
                  file=sys.stderr)
            return 1
        if open(out, encoding="utf-8").read() != text:
            print(f"{out} does not match what its own metric files derive -- "
                  f"it was hand-edited or the inputs changed", file=sys.stderr)
            return 1
        print(f"{out} matches its inputs (passed={v['passed']})")
        return 0

    open(out, "w", encoding="utf-8").write(text)
    print(f"wrote {out}: passed={v['passed']} -- {v['detail']}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
