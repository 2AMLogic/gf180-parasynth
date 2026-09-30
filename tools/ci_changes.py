#!/usr/bin/env python3
"""Decide which CI suites a change can affect (issue #457).

Every workflow that gates on this runs a small `changes` job first; each suite
job `needs:` it and is skipped only when this tool says, explicitly, that its
inputs were not touched. A skipped job still reports -- GitHub records a job
skipped by its `if:` as a success, so a required check is never left at
"Expected -- waiting".

THREE SCOPES, AND ONLY TWO OF THEM SKIP ANYTHING.

    none   every changed path is agent/orchestration tooling that no suite
           reads: `.claude/`, `.agents/`, `.loom/`, `.github/labels.yml`
    docs   every changed path is `none` or a prose Markdown file (see
           `prose_markdown`); only the doc-claim checker and its controls run
    full   anything else -- the whole suite, unchanged

`full` is the default for every path this module does not positively
recognise, for an empty diff, for an event it does not know, and for any git
failure. A filter that wrongly skips a real change is strictly worse than no
filter: `rungs.yml` is the capability DAG's permanent regression gate, and a
promise conditioned on which directory you edited is not a promise unless the
condition errs toward running.

WHY `**/*.md` IS NOT "DOCS". The issue proposed it, and measuring the tree
refuted it: Markdown here is test input. `fpga/test_ext_io_timing.py` hashes
`fpga/ARTY.md` (the very file in #456, the PR that motivated this),
`spec/reference/test_numeric_contract.py` parses `spec/NUMERIC-CONTRACT.md`,
`fpga/release/release_manifest.py` hashes `docs/deadline/README.md`,
`tools/compile_dag.py --check` compares `README.md`, `model/tom_drop_docs.py`
reads `docs/tr808-reference.md`, and the DR-numbering check globs
`spec/decision-records/*.md`. So a Markdown file is prose only if it lives
where prose lives AND nothing outside the docs-tier's own tooling names it.
"""
from __future__ import annotations

import argparse
import os
import pathlib
import subprocess
import sys
from typing import Callable, Iterable

ROOT = pathlib.Path(__file__).resolve().parent.parent

NONE, DOCS, FULL = "none", "docs", "full"

TOOLING_PREFIXES = (".claude/", ".agents/", ".loom/")
TOOLING_FILES = frozenset({".github/labels.yml"})

# Where prose lives. Markdown under model/, tools/, spec/, fpga/, pnr/,
# rtl-sketch/, refaudio/ and refprofile/ sits beside the code that reads it
# and is always `full`.
PROSE_DIRS = ("docs/", ".github/")

# Excluded from the "does anything name this document" scan: trees no suite
# reads, and the tools the docs scope itself runs (plus this classifier and its
# tests, whose fixtures name documents without reading them).
REFERENCE_SCAN_EXCLUDES = (
    ":(exclude)*.md",
    ":(exclude).loom",
    ":(exclude).claude",
    ":(exclude).agents",
    ":(exclude)tools/check_doc_claims.py",
    ":(exclude)tools/test_check_doc_claims.py",
    ":(exclude)tools/ci_changes.py",
    ":(exclude)tools/test_ci_changes.py",
)


def git(*args: str, cwd: pathlib.Path = ROOT) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)


def name_is_referenced(name: str, cwd: pathlib.Path = ROOT) -> bool:
    """Whether any tracked non-Markdown file outside the excluded trees
    contains `name`. An error answers True: unknown means `full`."""
    r = git("grep", "-q", "-F", "-e", name, "--", ".", *REFERENCE_SCAN_EXCLUDES, cwd=cwd)
    return r.returncode != 1


def prose_markdown(path: str) -> bool:
    if not path.endswith(".md"):
        return False
    return "/" not in path or path.startswith(PROSE_DIRS)


def classify(path: str, referenced: Callable[[str], bool]) -> tuple[str, str]:
    """One changed path -> (scope, reason)."""
    if path.startswith(TOOLING_PREFIXES) or path in TOOLING_FILES:
        return NONE, "agent/orchestration tooling no suite reads"
    if path.endswith(".md"):
        if not prose_markdown(path):
            return FULL, "Markdown beside code, outside docs/ and the top level"
        base = path.rsplit("/", 1)[-1]
        if referenced(base):
            return FULL, f"{base!r} is named by a tracked non-Markdown file, so a suite may read it"
        return DOCS, "prose Markdown no suite names"
    return FULL, "not positively classified, so it runs everything"


def scope_of(paths: Iterable[str], referenced: Callable[[str], bool],
             report: Callable[[str], None] = print) -> str:
    paths = sorted(set(paths))
    if not paths:
        report("no changed paths found -- cannot tell what changed, so: full")
        return FULL
    scopes = set()
    for p in paths:
        s, why = classify(p, referenced)
        scopes.add(s)
        report(f"  {s:4}  {p}  ({why})")
    if FULL in scopes:
        return FULL
    if DOCS in scopes:
        return DOCS
    return NONE


def changed_paths(event: str, ref: str, base: str = "origin/main",
                  cwd: pathlib.Path = ROOT) -> tuple[list[str] | None, str]:
    """The paths this run's commit changes, or (None, why) if it must run
    everything. `--no-renames` so a rename reports its old path too."""
    if event == "pull_request":
        # The checkout is GitHub's merge of the PR into its base: first
        # parent is the base, so HEAD^1..HEAD is exactly what merging adds.
        parents = git("rev-list", "--parents", "-n", "1", "HEAD", cwd=cwd)
        if parents.returncode != 0 or len(parents.stdout.split()) != 3:
            return None, "pull_request checkout is not a two-parent merge commit"
        since = "HEAD^1"
    elif event == "push":
        if ref == "refs/heads/main":
            return None, "a push to main always runs everything"
        mb = git("merge-base", base, "HEAD", cwd=cwd)
        if mb.returncode != 0:
            return None, f"no merge-base with {base}: {mb.stderr.strip()}"
        since = mb.stdout.strip()
    else:
        return None, f"event {event!r} always runs everything"
    d = git("diff", "--name-only", "--no-renames", since, "HEAD", cwd=cwd)
    if d.returncode != 0:
        return None, f"git diff {since} HEAD failed: {d.stderr.strip()}"
    return [p for p in d.stdout.splitlines() if p], f"diff {since}..HEAD"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--event", default=os.environ.get("GITHUB_EVENT_NAME", ""))
    ap.add_argument("--ref", default=os.environ.get("GITHUB_REF", ""))
    ap.add_argument("--base", default="origin/main",
                    help="what a push to a non-main branch is compared against")
    ap.add_argument("--paths", nargs="*", default=None,
                    help="classify these paths instead of reading git (dry run)")
    ap.add_argument("--github-output", default=os.environ.get("GITHUB_OUTPUT"))
    a = ap.parse_args(argv)

    if a.paths is not None:
        paths, how = a.paths, "paths given on the command line"
    else:
        paths, how = changed_paths(a.event, a.ref, a.base)
    print(f"ci_changes: event={a.event!r} ref={a.ref!r}; {how}")
    scope = FULL if paths is None else scope_of(paths, name_is_referenced)
    print(f"scope={scope}")
    if a.github_output:
        with open(a.github_output, "a") as f:
            f.write(f"scope={scope}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
