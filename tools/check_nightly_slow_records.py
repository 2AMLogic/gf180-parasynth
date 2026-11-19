#!/usr/bin/env python3
"""Gate: each nightly slow-node DAG record is corroborated by the run's own evidence.

    tools/check_nightly_slow_records.py \
      --published published.json --at-run at-run.json \
      --log full-rtl.log --head-sha <40-hex> [--run-id R] [--job-id J]

WHY. #820's criterion (a) originally demanded every slow record's `sha` equal
the verifying nightly's `headSha`. #967's `record_unchanged()` makes that
unsatisfiable on a quiet night: when a re-run repeats the verdict and the
normalized detail and nothing the node covers has moved, the record is kept
byte-for-byte and `sha` stays the staleness anchor (91398a0c...) -- a
provenance anchor, never a last-verified stamp. Scheduled run 44344077192
(2026-11-18, job 134941613343 @ 4a6eda4d) measured exactly that: every slow
verifier re-ran and passed, every node printed `keep ... (unchanged since
91398a0c)`, the commit step reported the records unchanged -- and the old
clause read red on machinery that measured green (issue #1058).

This gate implements the amended per-node rule. A node passes only on
execution evidence from the run, never on the stored boolean alone:

  rewritten -- the record's `sha` equals the run's full `headSha` AND the log
      carries the node's own `PASS` line from the recording step;
  retained  -- the log carries the node's own
      `keep <node> <detail> (unchanged since <anchor>)` line, the anchor
      matches the record's `sha` prefix and the detail is byte-equal to the
      record's `detail`, AND the published record is identical to the record
      at the run's own checkout;
  either way the published record must carry `passed == true`.

A node with neither form of execution evidence fails even when its record
says `passed`: the branch content #818 shipped (six bare records at
91398a0c) still fails, which is the defeat the original clause existed to
close. A line from another run -- a foreign anchor or a foreign detail --
fails, so a green step cannot launder a record it did not measure.

The six evidence lines this gate was first replayed against are pinned in
`tools/test_check_nightly_slow_records.py`, so the positive case survives
Actions' log retention.

Exit: 0 every node corroborated (or every requested control turned red),
      1 a node fails / the old rule rejects,
      3 REFUSED -- the apparatus cannot answer: required evidence missing or
        unusable (a gate that answers anyway would look exactly like data),
      4 a requested control did not turn the verdict red,
      2 usage / unreadable input.

Controls (--control name=node, repeatable) mutate the evidence the way
#1058's test plan requires -- withhold the node's line, substitute a line
from another run, corrupt the anchor, falsify the stored verdict -- and each
must turn that node red. A gate whose controls pass is itself a defect.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys

import refusal

#: The nodes nightly.yml records with `compile_dag.py --run --slow`; every
#: reader of this gate reads the same six from docs/dag.json's `slow: true`.
SLOW_NODES = ("F1", "F2", "M1", "D1", "I1", "I2")

#: `<ts>  keep  F1  <detail> (unchanged since 91398a0c)` -- the recorder's
#: quiet-night line (compile_dag.py's keep branch). The detail is the OLD
#: record's stored detail printed verbatim, so byte equality with the
#: record's `detail` is the right test; the anchor is `sha[:8]`.
KEEP_RE = re.compile(
    r"keep\s+(\S+)\s+(.*?)\s+\(unchanged since ([0-9a-f]{8})\)\s*$")

#: `<ts>  PASS  F1  <detail>` -- the recorder's rewritten line
#: (`{mark:6s} {i:3s} {detail}`). Only a PASS proves the run executed the
#: verifier; FAIL/REFUSE lines are captured so a rewritten record carrying a
#: FAIL line fails with a reason that names it.
FRESH_RE = re.compile(r"(?:^|\s)(PASS|FAIL|REFUSE)\s+(\S+)\s+(.+?)\s*$")

FULL_SHA_RE = re.compile(r"^[0-9a-f]{40}$")

EXIT_OK, EXIT_FAIL, EXIT_REFUSED, EXIT_CONTROL = 0, 1, 3, 4


class Refused(refusal.Refused, RuntimeError):
    """The apparatus cannot answer; say what is missing, never guess."""


def parse_log(lines: list[str]) -> tuple[dict, dict]:
    """Recording-step evidence lines, keyed by node.

    Keep lines are consumed first: a keep line's detail may itself contain
    `PASS -- ` (every ladder/modal/drum/integration detail does), which the
    fresh-line pattern would otherwise misread as a rewritten verdict.
    """
    keeps: dict[str, list[tuple[str, str]]] = {}
    fresh: dict[str, list[tuple[str, str]]] = {}
    for line in lines:
        m = KEEP_RE.search(line)
        if m:
            keeps.setdefault(m.group(1), []).append((m.group(2), m.group(3)))
            continue
        m = FRESH_RE.search(line)
        if m and m.group(2) in SLOW_NODES:
            fresh.setdefault(m.group(2), []).append((m.group(1), m.group(3)))
    return keeps, fresh


def load_json(path: pathlib.Path, what: str) -> dict:
    try:
        data = json.loads(path.read_text())
    except FileNotFoundError:
        raise Refused(f"{what} not found: {path}") from None
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise Refused(f"{what} is not readable JSON: {path}: {exc}") from None
    if not isinstance(data, dict) or not data:
        raise Refused(f"{what} is not a non-empty JSON object: {path}")
    return data


def check_node(node: str, rec: dict | None, at_run_rec, keeps, fresh,
               head_sha: str) -> tuple[bool, str]:
    """The amended per-node rule (#1058). (True, how) or (False, why)."""
    if not isinstance(rec, dict):
        return False, "no published record"
    if rec.get("passed") is not True:
        return False, (f"published record is not passed==true "
                       f"(passed={rec.get('passed')!r})")
    if rec.get("sha") == head_sha:
        # Rewritten tonight: the run's own execution must be in the log.
        if any(v == "PASS" and d == rec.get("detail")
               for v, d in fresh.get(node, [])):
            return True, "rewritten: sha == headSha with the node's PASS line"
        got = fresh.get(node)
        if got:
            return False, (f"record carries the run's headSha but the log's "
                           f"line(s) disagree: {got!r}")
        return False, ("record carries the run's headSha but the log carries "
                       "no PASS line for the node -- a green step cannot "
                       "supply a missing row, the recording command runs "
                       "`|| true`")
    # Retained (#967 quiet night): the run re-executed and kept the old
    # record, and the log line names the anchor and the detail.
    detail = rec.get("detail")
    anchor = rec.get("sha", "")
    if not isinstance(anchor, str) or len(anchor) < 8:
        return False, "record carries no usable sha anchor"
    matches = [(d, a) for d, a in keeps.get(node, [])
               if d == detail and a == anchor[:8]]
    if not matches:
        return False, ("no keep line for the node with matching detail and "
                       f"anchor {anchor[:8]} (have: {keeps.get(node)!r})")
    if at_run_rec != rec:
        return False, ("keep line corroborates but the published record "
                       "differs from the record at the run checkout")
    return True, ("retained: keep line matches anchor and detail; record "
                  "identical at the run checkout")


def evaluate(published: dict, at_run: dict, keeps: dict, fresh: dict,
             head_sha: str) -> dict[str, tuple[bool, str]]:
    return {n: check_node(n, published.get(n), at_run.get(n),
                          keeps, fresh, head_sha)
            for n in SLOW_NODES}


def old_rule(published: dict, head_sha: str) -> tuple[bool, int]:
    """#820's original predicate, kept so the replay can show it reject."""
    ok = [n for n in SLOW_NODES
          if isinstance(published.get(n), dict)
          and published[n].get("passed") is True
          and published[n].get("sha") == head_sha]
    return len(ok) == len(SLOW_NODES), len(ok)


# --- controls (#1058 test plan): each must turn its node red -----------------

def control_withhold(node, keeps, fresh, published, at_run):
    keeps.pop(node, None)
    fresh.pop(node, None)


def control_foreign(node, keeps, fresh, published, at_run):
    """Substitute a line from ANOTHER node -- a stand-in for another run's
    line: foreign anchor, foreign detail, renamed to `node`."""
    other = next((lines for n, lines in sorted(keeps.items()) if n != node),
                 None)
    if other:
        detail, anchor = other[0]
    else:
        detail, anchor = "substituted evidence line", "0" * 8
    keeps[node] = [(detail, anchor)]
    fresh.pop(node, None)


def control_wrong_anchor(node, keeps, fresh, published, at_run):
    """Keep the node's own detail but corrupt the anchor to c03c64e1 -- a
    real pre-#818 anchor that is not this record's."""
    if node not in keeps:
        raise Refused(f"control wrong-anchor={node}: the node has no keep "
                      "line to corrupt (rewritten night); replay it on a "
                      "quiet-night fixture")
    keeps[node] = [(d, "c03c64e1") for d, _a in keeps[node]]


def control_false_verdict(node, keeps, fresh, published, at_run):
    if not isinstance(published.get(node), dict):
        raise Refused(f"control false-verdict={node}: no published record "
                      "to falsify")
    published[node]["passed"] = False


CONTROLS = {
    "withhold": control_withhold,
    "foreign": control_foreign,
    "wrong-anchor": control_wrong_anchor,
    "false-verdict": control_false_verdict,
}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--published", required=True, type=pathlib.Path,
                    help="records read from main: origin/main's tip on a "
                         "no-op night, the attributable [dag] records commit "
                         "on a push night")
    ap.add_argument("--at-run", required=True, type=pathlib.Path, dest="at_run",
                    help="records at the run's own checkout "
                         "(git show <headSha>:docs/dag-results.json)")
    ap.add_argument("--log", required=True, type=pathlib.Path,
                    help="the full-rtl job log (the recording step's lines)")
    ap.add_argument("--head-sha", required=True, dest="head_sha",
                    help="the run's full 40-hex headSha")
    ap.add_argument("--run-id", default="", dest="run_id",
                    help="provenance label, echoed in the summary")
    ap.add_argument("--job-id", default="", dest="job_id",
                    help="provenance label, echoed in the summary")
    ap.add_argument("--old-rule", action="store_true",
                    help="evaluate #820's original sha-equality predicate "
                         "instead of the amended rule (replay evidence)")
    ap.add_argument("--control", action="append", default=[], dest="controls",
                    metavar="NAME=NODE",
                    help="apply a control and require it to turn the node "
                         "red: withhold=, foreign=, wrong-anchor=, "
                         "false-verdict=")
    args = ap.parse_args(argv)

    run_label = f"run {args.run_id}" if args.run_id else "run"
    job_label = f" job {args.job_id}" if args.job_id else ""

    try:
        if not FULL_SHA_RE.match(args.head_sha or ""):
            raise Refused(f"--head-sha must be a full 40-hex sha, got "
                          f"{args.head_sha!r}")
        published = load_json(args.published, "published records")
        at_run = load_json(args.at_run, "records at the run checkout")
        try:
            log_lines = args.log.read_text(errors="replace").splitlines()
        except FileNotFoundError:
            raise Refused(f"log not found: {args.log}") from None
        if not log_lines:
            raise Refused(f"log is empty: {args.log}")
        keeps, fresh = parse_log(log_lines)
        if not keeps and not fresh:
            raise Refused(
                "the log carries no recording-step lines (no `keep`, PASS, "
                "FAIL or REFUSE row naming a slow node) -- if this is not "
                "the full-rtl job's log, the execution evidence cannot be "
                "obtained from it; record REFUSED and name the log you have")

        if args.old_rule:
            if args.controls:
                ap.error("--old-rule takes no --control")
            ok, count = old_rule(published, args.head_sha)
            print(f"old rule (every sha == headSha): {count}/6 satisfy it "
                  f"--> {'PASS' if ok else 'FAIL'} ({run_label}"
                  f"{job_label}, head {args.head_sha[:8]})")
            return EXIT_OK if ok else EXIT_FAIL

        verdicts = evaluate(published, at_run, keeps, fresh, args.head_sha)
        width = max(len(n) for n in SLOW_NODES)
        for node in SLOW_NODES:
            ok, why = verdicts[node]
            print(f"  {'PASS' if ok else 'FAIL':4s} {node:{width}s}  {why}")
        npass = sum(1 for ok, _ in verdicts.values() if ok)
        print(f"(a) {npass}/6 slow nodes corroborated by {run_label}"
              f"{job_label} (head {args.head_sha[:8]}) --> "
              f"{'PASS' if npass == len(SLOW_NODES) else 'FAIL'}")

        if not args.controls:
            return EXIT_OK if npass == len(SLOW_NODES) else EXIT_FAIL

        # Control mode: every requested control must turn its node red.
        exit_code = EXIT_OK
        for spec in args.controls:
            name, _, node = spec.partition("=")
            if name not in CONTROLS:
                ap.error(f"unknown control {name!r}; known: "
                         f"{', '.join(sorted(CONTROLS))}")
            if node not in SLOW_NODES:
                ap.error(f"control node {node!r} is not one of "
                         f"{','.join(SLOW_NODES)}")
            keeps2 = {n: list(v) for n, v in keeps.items()}
            fresh2 = {n: list(v) for n, v in fresh.items()}
            pub2 = {n: dict(v) if isinstance(v, dict) else v
                    for n, v in published.items()}
            CONTROLS[name](node, keeps2, fresh2, pub2, at_run)
            v2 = evaluate(pub2, at_run, keeps2, fresh2, args.head_sha)
            caught = not v2[node][0]
            if caught:
                print(f"  CONTROL {spec}: caught (node fails: {v2[node][1]})")
            else:
                print(f"  CONTROL {spec}: NOT CAUGHT -- {v2[node][1]}")
                exit_code = EXIT_CONTROL
        if exit_code == EXIT_OK:
            print("all requested controls turned their node red")
        return exit_code
    except Refused as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return EXIT_REFUSED


if __name__ == "__main__":
    sys.exit(main())
