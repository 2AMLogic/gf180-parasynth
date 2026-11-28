#!/usr/bin/env python3
"""Would merging this branch actually LOSE a file? Answer by merging it.

WHY THIS EXISTS, AND WHY THE FIRST VERSION WAS WRONG. I raised the alarm three
times that a pull request "would delete" work it had nothing to do with -- and
twice I was wrong, because I compared the wrong two things:

    git diff main branch      (two-dot)  -> 29 "deletions"
    git diff main...branch    (three-dot) ->  0 deletions
    the actual merge                      ->  nothing lost

Two-dot asks "how do these two trees differ", so everything `main` gained since
the branch point shows up as a deletion *from the branch's perspective*. That is
an artefact of the comparison, not a property of the branch. GitHub shows
three-dot, and a merge uses the merge base correctly -- including a SQUASH
merge, which merges first and squashes the result.

So squash merging was never the problem. My comparison was.

THE CHECK. Do not reason about diffs at all: perform the merge with
`git merge-tree` and ask whether any path present in the base is absent from
the result and not deleted on purpose by this branch. That answers the question
being asked, rather than a related one that happens to be easy to compute.

Exit 0 clean, 1 if the merge would lose something, 2 if it could not run --
this repository's convention, where 2 means no evidence rather than no problem.

"Could not run" includes `git` itself being absent. Until #1742 that was not
so: neither subprocess site caught the executable being missing, a
`FileNotFoundError` escaped `main()`, and the process exited 1 -- the file-loss
code -- before any tree had been examined. Both sites now start git through
`_run`, which refuses.
"""
from __future__ import annotations
import argparse, subprocess, sys


def _run(*a: str) -> subprocess.CompletedProcess:
    """Start `git`, or REFUSE (exit 2) when it cannot be started at all.

    OSError is caught rather than its FileNotFoundError subclass because a
    `git` that is on PATH but not executable fails the same way (EACCES), and
    is the same unmet precondition. It is printed with the subcommand so a
    reader can tell WHICH of the two sites refused."""
    try:
        return subprocess.run(["git", *a], capture_output=True, text=True)
    except OSError as exc:
        print(f"REFUSED: cannot run git {' '.join(a)}: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc


def git(*a: str) -> str:
    r = _run(*a)
    if r.returncode != 0:
        print(f"git {' '.join(a)} failed: {r.stderr.strip()}", file=sys.stderr)
        raise SystemExit(2)
    return r.stdout


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--base", default="origin/main")
    ap.add_argument("--head", default="HEAD")
    a = ap.parse_args()

    merge_base = git("merge-base", a.base, a.head).strip()
    if not merge_base:
        print("could not find a merge base", file=sys.stderr)
        return 2

    # Actually merge. Do not infer from a diff -- inferring from the wrong diff
    # is what produced three false alarms.
    r = _run("merge-tree", "--write-tree", a.base, a.head)
    if r.returncode != 0:
        print(f"MERGE CONFLICT between {a.base} and {a.head} -- resolve before merging",
              file=sys.stderr)
        for ln in r.stdout.splitlines()[:12]:
            print(f"    {ln}", file=sys.stderr)
        return 1
    tree = r.stdout.splitlines()[0].strip()

    def paths(ref: str) -> set[str]:
        return set(git("ls-tree", "-r", "--name-only", ref).splitlines())

    in_base, in_result = paths(a.base), paths(tree)
    # Deleted on purpose by this branch: present at the merge base, gone at head.
    deliberate = paths(merge_base) - paths(a.head)
    lost = sorted(in_base - in_result - deliberate)

    if deliberate & in_base:
        for f in sorted(deliberate & in_base):
            print(f"  removed on purpose by this branch: {f}")
    if not lost:
        print(f"merge is clean; nothing in {a.base} is lost "
              f"({len(in_result)} files in the result)")
        return 0

    print(f"\nTHE MERGE WOULD LOSE {len(lost)} file(s) present in {a.base} "
          f"that this branch did not remove:\n", file=sys.stderr)
    for f in lost[:20]:
        print(f"    {f}", file=sys.stderr)
    if len(lost) > 20:
        print(f"    ... and {len(lost) - 20} more", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
