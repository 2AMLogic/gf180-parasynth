#!/usr/bin/env python3
"""Commit the nightly's slow-node records to main, retrying onto a moved main.

WHY (PR #818 review). The nightly's full-rtl job runs for up to ~2h on a cron.
Commits landed on origin/main inside that window roughly one night in three
over the 14 days measured in review (9 commits in 14 days x 3h windows). The
step this replaces ended in a bare `git push`: on a non-fast-forward rejection
it failed under bash -e and the records were persisted nowhere but the job
artifact -- the exact #787 status quo this job exists to end. The loss was
also invisible: `--check --strict` runs after this step and D3 is RED today by
design, so a rejected push and a clean night ended the job the same colour.

This drives commit-and-push as Python (CLAUDE.md: anything with logic is
Python -- a bash retry loop here would sit next to the repo's documented
`$?`-after-a-pipe and errexit incidents). On a rejected push it:

  1. fetches origin/main,
  2. rebases the [dag] commit onto it,
  3. resolves the conflict in docs/dag-results.json by UNION MERGE: per node,
     theirs (origin/main -- the fresher fast-node records dag.yml re-records
     on every plain push) wins, EXCEPT slow nodes, where ours wins: the
     nightly is the only producer of slow-node evidence, and losing it is the
     #787 loss all over again,
  4. regenerates README.md's DAG block from the merged records -- the
     document is derived from the records it shows, never hand-merged -- and
     stages both,
  5. continues the rebase and pushes again, bounded by ATTEMPTS.

dag.yml carries the same bare push but is deliberately left alone: its
exposure is minutes on a push-triggered run under a concurrency group, not
hours on a cron.
"""
from __future__ import annotations
import json
import os
import pathlib
import subprocess
import sys

TOOLS = pathlib.Path(__file__).resolve().parent
REPO = TOOLS.parent

RECORDS = "docs/dag-results.json"
README = "README.md"
DAG = REPO / "docs" / "dag.json"

# The subject is load-bearing: dag.yml's job-level `if:` skips any push whose
# head commit message contains `[dag]`, so this push cannot re-trigger the
# per-push job into a commit loop. test_check_workflows.py pins this constant
# against dag.yml's guard -- reword it in both places or neither.
COMMIT_SUBJECT = "[dag] regenerate the roadmap from evidence"
COMMIT_BODY = ("Automated. Status is derived by running each node's evidence, "
               "not asserted. See tools/compile_dag.py.")

# One push, then two retry rounds (fetch + rebase + resolved conflict) before
# giving up. The records still reach the artifact on failure (`upload-artifact`
# carries `if: always()`), but the bounded budget is spent trying not to need
# it: the artifact is the place #787 says is not enough.
ATTEMPTS = 3


def run(*a: str, **kw) -> subprocess.CompletedProcess:
    return subprocess.run(a, capture_output=True, text=True, **kw)


def out(*a: str) -> str:
    """stdout of a git command that must succeed."""
    r = run(*a)
    if r.returncode != 0:
        sys.stderr.write(r.stderr)
        raise SystemExit(f"push_dag_records: `{' '.join(a[:3])}` exited "
                         f"{r.returncode}")
    return r.stdout


def union_merge(ours: dict, theirs: dict, slow_ids: set) -> dict:
    """Per-node union of the two records dicts; slow nodes keep ours."""
    merged = dict(theirs)
    for nid, rec in ours.items():
        if nid in slow_ids or nid not in merged:
            merged[nid] = rec
    return merged


def slow_ids() -> set:
    try:
        nodes = json.loads(DAG.read_text())["nodes"]
    except (OSError, ValueError, KeyError) as e:
        raise SystemExit(f"push_dag_records: cannot read {DAG}: {e}")
    return {i for i, n in nodes.items() if n.get("slow")}


def regenerate_readme() -> int:
    """Re-render README.md's DAG block from the merged records."""
    return run(sys.executable, str(TOOLS / "compile_dag.py")).returncode


def resolve_conflict(ours_sha: str) -> None:
    """Resolve a rebase conflict in RECORDS/README by union merge + re-render.

    `ours_sha` is the [dag] commit being replayed (captured before the rebase,
    so no reliance on REBASE_HEAD or the ours/theirs inversion). Theirs is
    origin/main, already fetched.
    """
    ours = json.loads(out("git", "show", f"{ours_sha}:{RECORDS}"))
    theirs = json.loads(out("git", "show", f"origin/main:{RECORDS}"))
    merged = union_merge(ours, theirs, slow_ids())
    pathlib.Path(RECORDS).write_text(
        json.dumps(merged, indent=2, sort_keys=True) + "\n")

    # The README outside the DAG block belongs to origin/main (prose edits
    # from intervening merges); our side only ever touched the block, which
    # the regeneration below rewrites from the merged records anyway.
    pathlib.Path(README).write_text(out("git", "show", f"origin/main:{README}"))
    if regenerate_readme() != 0:
        raise SystemExit("push_dag_records: compile_dag.py failed to "
                         "re-render README from the merged records")
    out("git", "add", RECORDS, README)


def main() -> int:
    if run("git", "rev-parse", "--show-toplevel").returncode != 0:
        print("push_dag_records: not inside a git repository", file=sys.stderr)
        return 2
    os.chdir(REPO)

    rc = run(sys.executable, str(TOOLS / "compile_dag.py")).returncode
    if rc != 0:
        print(f"push_dag_records: compile_dag.py exited {rc} -- not committing",
              file=sys.stderr)
        return rc

    staged = run("git", "diff", "--quiet", "--", README, RECORDS)
    if staged.returncode == 0:
        print("the records are unchanged")
        return 0
    if staged.returncode != 1:
        print(f"push_dag_records: git diff exited {staged.returncode}",
              file=sys.stderr)
        return 2

    out("git", "config", "user.name", "dag-bot")
    out("git", "config", "user.email", "noreply@anthropic.com")
    out("git", "add", README, RECORDS)
    out("git", "commit", "-m", COMMIT_SUBJECT, "-m", COMMIT_BODY)
    ours_sha = out("git", "rev-parse", "HEAD").strip()

    for attempt in range(1, ATTEMPTS + 1):
        push = run("git", "push")
        if push.returncode == 0:
            print(f"records pushed (attempt {attempt})")
            return 0
        if attempt == ATTEMPTS:
            print(f"push_dag_records: push rejected {ATTEMPTS} times -- the "
                  f"records are in this job's artifact but not on main",
                  file=sys.stderr)
            return 1
        print(f"push rejected (attempt {attempt}); rebasing onto the new main",
              file=sys.stderr)
        out("git", "fetch", "origin", "main")
        rebase = run("git", "rebase", "origin/main")
        if rebase.returncode != 0:
            resolve_conflict(ours_sha)
            cont = run("git", "rebase", "--continue",
                       env={**os.environ, "GIT_EDITOR": "true"})
            if cont.returncode != 0:
                sys.stderr.write(cont.stdout + cont.stderr)
                print("push_dag_records: git rebase --continue failed; the "
                      "rebase is left in progress for diagnosis",
                      file=sys.stderr)
                return 1
    return 1  # unreachable: the loop returns on push success or final failure


if __name__ == "__main__":
    sys.exit(main())
