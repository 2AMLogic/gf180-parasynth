#!/usr/bin/env python3
"""Which [dag] rewrites would the #967 freshness rule have suppressed?

Issue #967's measure-first step, kept as an instrument: replay the last N
`[dag] regenerate the roadmap from evidence` commits on main and classify,
for every record each one rewrote, whether the #967 rule -- same verdict,
same detail modulo its wall-clock tail, covers still fresh at the recorded
sha -- would have left the old record's bytes alone, and therefore removed
those lines from that commit's diff.

    python3 tools/measure_dag_churn.py                  # last 10 [dag] commits
    python3 tools/measure_dag_churn.py --commits 25 --repo /somewhere/clone

The classification is the WRITER'S OWN rule, imported from compile_dag, not a
reimplementation (#155): if the writer's normalisation changes, this
instrument moves with it.

Preconditions are asserted, not assumed: a shallow clone cannot answer the
staleness question (every recorded sha must diff), so the tool REFUSES rather
than printing zeros that look like data. A [dag] commit that is a merge, or
whose parent carries no readable results file, is reported as skipped rather
than counted as suppressed.

Exit status follows `tools/exit_contract.py` (#1857). A [dag] commit that was
REPLAYED is this instrument's unit of measurement, so a run that replayed
none -- every commit it found was skipped -- is REFUSED (2, one `REFUSED:`
line, the skipped rows still printed above it) rather than a "0 would have
been suppressed (0%)" headline: that zero is not a measurement. Zero record
REWRITES among replayed commits is a measured zero, and exits 0.
"""
from __future__ import annotations
import argparse, json, pathlib, re, subprocess, sys

TOOLS = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(TOOLS))
from compile_dag import normalise_detail                             # noqa: E402
from exit_contract import PASS, REFUSED                              # noqa: E402

ROOT = TOOLS.parent
RESULTS = "docs/dag-results.json"
DAGDEF = "docs/dag.json"
README = "README.md"


def git(repo, *a):
    return subprocess.run(["git", *a], cwd=str(repo), capture_output=True,
                          text=True, timeout=60)


def show(repo, commit: str, path: str):
    r = git(repo, "show", f"{commit}:{path}")
    return r.stdout if r.returncode == 0 else None


def count_hunks(repo, rng: str, path: str):
    r = git(repo, "diff", "-U3", rng, "--", path)
    if r.returncode != 0:
        return None
    return sum(1 for l in r.stdout.splitlines() if l.startswith("@@"))


def dag_block_lines(repo, commit: str):
    """1-based (first, last) line numbers of the DAG block in README at
    `commit`, or None when either marker is missing."""
    txt = show(repo, commit, README) or ""
    begin = txt.find("<!-- DAG:BEGIN -->")
    end = txt.find("<!-- DAG:END -->")
    if begin < 0 or end < 0:
        return None
    return txt[:begin].count("\n") + 1, txt[:end].count("\n") + 1


def hunks_in_block(repo, rng: str, block):
    """README hunks whose new-side range overlaps the DAG block. A context
    hunk (count 0) counts when its one new line sits inside the block."""
    if block is None:
        return None
    r = git(repo, "diff", "-U3", rng, "--", README)
    if r.returncode != 0:
        return None
    n = 0
    for line in r.stdout.splitlines():
        if not line.startswith("@@"):
            continue
        m = re.search(r"@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@", line)
        if not m:
            continue
        start = int(m.group(1))
        count = int(m.group(2)) if m.group(2) is not None else 1
        if start <= block[1] and start + max(count, 1) - 1 >= block[0]:
            n += 1
    return n


def covers_changed(repo, covers, a: str, b: str):
    """True/False: did any covered path differ between the two shas?
    None: git cannot answer (a sha outside history, a diff failure) -- which
    is `unmeasurable`, not `unchanged`."""
    if not a or not b:
        return None
    if not covers:
        return False          # nothing declared -> nothing can go stale
    r = git(repo, "diff", "--name-only", a, b, "--", *covers)
    if r.returncode != 0:
        return None
    return bool(r.stdout.strip())


def classify_rewrite(repo, old: dict, new: dict, node: dict, new_sha: str):
    """Why this rewrite happened, or 'suppressed' when the #967 rule would
    have kept the old record. Mirrors record_unchanged()'s three legs."""
    if old.get("passed") != new.get("passed"):
        return "verdict changed"
    if normalise_detail(old.get("detail", "")) != \
            normalise_detail(new.get("detail", "")):
        return "detail changed beyond timing"
    ch = covers_changed(repo, node.get("covers", []),
                        old.get("sha", ""), new_sha)
    if ch is None:
        return "covers unmeasurable -> rewrite kept"
    if ch:
        return "covers changed -> re-anchor required"
    return "suppressed"


def replay(repo, n_commits: int):
    shallow = git(repo, "rev-parse", "--is-shallow-repository")
    if shallow.returncode != 0 or shallow.stdout.strip() == "true":
        raise SystemExit(f"REFUSED: {repo} is shallow or not readable -- the "
                         f"staleness question needs full history. "
                         f"fetch-depth: 0 first; refusing rather than "
                         f"printing zeros that look like data.")
    r = git(repo, "log", r"--grep=\[dag\]", "--format=%H x%ax", f"-n{n_commits}")
    commits = [ln.split()[0] for ln in r.stdout.splitlines() if ln.strip()]
    if not commits:
        raise SystemExit(f"REFUSED: no [dag] commits found in {repo} -- "
                         f"nothing to replay.")

    rows = []
    for c in commits:
        parents = git(repo, "log", "-1", "--format=%P", c).stdout.split()
        date = git(repo, "log", "-1", "--format=%ad", "--date=format:%m-%d %H:%M",
                   c).stdout.strip()
        if len(parents) != 1:
            rows.append({"commit": c[:8], "date": date, "note":
                         "merge commit -- skipped, not a linear bot commit"})
            continue
        p = parents[0]
        old, new = {}, {}
        try:
            old = json.loads(show(repo, p, RESULTS) or "null")
            new = json.loads(show(repo, c, RESULTS) or "null")
        except json.JSONDecodeError:
            rows.append({"commit": c[:8], "date": date, "note":
                         "results file unreadable at parent or commit -- skipped"})
            continue
        if not isinstance(old, dict) or not isinstance(new, dict):
            rows.append({"commit": c[:8], "date": date, "note":
                         "no results file at parent or commit -- skipped"})
            continue
        try:
            nodes = json.loads(show(repo, c, DAGDEF))["nodes"]
        except (json.JSONDecodeError, KeyError, TypeError):
            nodes = {}

        changed = [k for k in set(old) | set(new) if old.get(k) != new.get(k)]
        reasons: dict[str, str] = {}
        for k in changed:
            if k not in old:
                reasons[k] = "record added"
            elif k not in new:
                reasons[k] = "record dropped"
            else:
                reasons[k] = classify_rewrite(repo, old[k], new[k],
                                              nodes.get(k, {}),
                                              new[k].get("sha", ""))
        block = dag_block_lines(repo, c)
        rows.append({
            "commit": c[:8], "date": date,
            "records_changed": len(changed),
            "suppressed": sum(1 for v in reasons.values() if v == "suppressed"),
            "hunks_results": count_hunks(repo, f"{p}..{c}", RESULTS),
            "hunks_readme": count_hunks(repo, f"{p}..{c}", README),
            "hunks_readme_dagblock": hunks_in_block(repo, f"{p}..{c}", block),
            "hunks_readme_other": (None if None in (count_hunks(repo, f"{p}..{c}", README),
                                                    hunks_in_block(repo, f"{p}..{c}", block))
                                   else count_hunks(repo, f"{p}..{c}", README)
                                   - hunks_in_block(repo, f"{p}..{c}", block)),
            "reasons": reasons,
            "note": "",
        })
    return rows


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--commits", type=int, default=10,
                    help="how many recent [dag] commits to replay (default 10)")
    ap.add_argument("--repo", default=str(ROOT),
                    help="repository to replay (default: this checkout)")
    a = ap.parse_args(argv)

    rows = replay(a.repo, a.commits)
    print(f"{'commit':<9} {'when':<12} {'recs':>4} {'kept':>4} "
          f"{'hunkR':>5} {'hunkRM':>6} {'inBlk':>5} {'other':>5}  note")
    totals = {"records": 0, "suppressed": 0, "empty_results_diff": 0,
              "commits": 0}
    for r in rows:
        if r.get("note") and "records_changed" not in r:
            print(f"{r['commit']:<9} {r['date']:<12} {'-':>4} {'-':>4} "
                  f"{'-':>5} {'-':>6} {'-':>5} {'-':>5}  {r['note']}")
            continue
        all_kept = r["records_changed"] > 0 \
            and r["records_changed"] == r["suppressed"]
        totals["commits"] += 1
        totals["records"] += r["records_changed"]
        totals["suppressed"] += r["suppressed"]
        totals["empty_results_diff"] += 1 if all_kept else 0
        print(f"{r['commit']:<9} {r['date']:<12} {r['records_changed']:>4} "
              f"{r['suppressed']:>4} "
              f"{r['hunks_results'] if r['hunks_results'] is not None else '-':>5} "
              f"{r['hunks_readme'] if r['hunks_readme'] is not None else '-':>6} "
              f"{r['hunks_readme_dagblock'] if r['hunks_readme_dagblock'] is not None else '-':>5} "
              f"{r['hunks_readme_other'] if r['hunks_readme_other'] is not None else '-':>5}  "
              f"{'results diff would be EMPTY' if all_kept else ''}")
        for k, why in r["reasons"].items():
            print(f"          {k:<4} {why}")
    if not totals["commits"]:
        skipped = "; ".join(f"{r['commit']}: {r['note']}" for r in rows if r.get("note"))
        print(f"\nREFUSED: replay measured nothing: 0 of {len(rows)} [dag] commit(s) replayed; "
              f"{skipped}")
        return REFUSED
    print()
    print(f"replayed {totals['commits']} linear [dag] commits: "
          f"{totals['records']} record rewrites, "
          f"{totals['suppressed']} would have been suppressed "
          f"({100 * totals['suppressed'] / max(totals['records'], 1):.0f}%), "
          f"{totals['empty_results_diff']} of {totals['commits']} commits' "
          f"docs/dag-results.json diffs would have been empty.")
    print("README hunks outside the DAG block (scorecard/history counters) are "
          "out of scope for #967 and are reported only to size what remains.")
    return PASS


if __name__ == "__main__":
    sys.exit(main())
