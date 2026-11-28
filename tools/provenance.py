#!/usr/bin/env python3
"""Shared provenance primitives: commit identity, uncommitted-tree hashing,
and content hashes.

This is `tools/run_case.py`'s own provenance block (see its module docstring,
"PROVENANCE, AND THE EXIT CODE"), extracted so a second consumer -- the
render/analyse/accept manifest scheme in `tools/manifest.py` -- can produce
provenance blocks in the *same* shape instead of re-deriving a second,
competing format (issue #68's Implementation Guidance: "reuse
`tools/run_case.py`'s provenance block as the starting schema rather than
inventing a second one"). `tools/run_case.py` imports these same functions
under their old names, so this is a pure extraction: no result this repository
has already written changes shape or value.
"""
from __future__ import annotations

import datetime
import hashlib
import pathlib
import re
import subprocess

ROOT = pathlib.Path(__file__).resolve().parent.parent

# A commit id as a record cites it: abbreviated or full hex, 4 to 64 characters
# (7 or 40 under SHA-1, 64 under SHA-256). Nothing else is asked of git.
COMMIT_ID = re.compile(r"[0-9a-f]{4,64}", re.IGNORECASE)


def git(*args: str, root: pathlib.Path = ROOT) -> str:
    """Run a git command against `root`. Empty string on any failure -- a
    provenance field that cannot be determined is recorded as absent, never
    guessed at."""
    try:
        return subprocess.check_output(["git", "-C", str(root), *args], text=True,
                                        stderr=subprocess.DEVNULL)
    except Exception:
        return ""


def commit_on_origin_main(commit: str, root: pathlib.Path = ROOT) -> bool | None:
    """Is `commit` an ancestor of the LOCAL `origin/main`?

    `True` or `False` when git answered; `None` when it could not -- no
    `origin/main` ref, a commit this checkout cannot resolve (never fetched,
    collected, absent from a shallow clone), or git not answering at all.

    Reads the RETURN CODE, not stdout, which is why `git()` cannot be used
    here: its empty-string-on-failure contract (#615) cannot tell "not an
    ancestor" from "no answer", and those two must read differently. 0 is an
    ancestor, 1 is not, anything else is no answer. And `--is-ancestor`,
    not `cat-file -e`, for the reason `tools/compile_dag.py` gives (#824): a
    commit still in the object store but no longer reachable must not count.

    `commit` is a commit ID, not a name. `HEAD` and `origin/main` are ancestors
    of `origin/main` every time, so asking git about a symbolic name would pass
    a record that cites nothing any later reader can check out; a name is
    `None` even where git resolves it, and a leading `-` never reaches git.

    No fetch, ever: a measuring tool does not change the repository it
    measures. The answer is about the local `origin/main`, so a checkout that
    has not fetched since a branch merged answers False for a commit that is on
    the remote -- a false refusal, never a false acceptance.

    WHICH WAY IT IS WRONG. Two inputs make this answer False or None where the
    full history says True. Both refuse; neither admits:
      * a shallow clone. A commit the clone never had is None. Once `fetch
        --depth 1` has moved the shallow boundary past a commit the clone does
        have, git answers 1 for a true ancestor, because the walk stops at the
        graft (observed, git 2.43); `fetch --unshallow` restores True.
      * a commit that never landed as itself. A squash merge puts no branch
        commit on main. Neither does a rebase or an amend AFTER the record is
        written: the branch that merges is a rewritten one, and the sha the
        record cites is left behind. The helper cannot tell either from an
        abandoned branch, and refuses all three alike -- correctly, since
        nobody can check out what the record cites.

    WHAT THAT ASKS OF THIS REPOSITORY. A record is judged when it is READ, not
    when it is written: the answer a record carries was true of one checkout
    at one moment, so a reader asks again rather than trust it. A record
    written on a feature branch is refused on that branch and scored once its
    merge has landed and the reader has fetched -- PROVIDED the branch merges
    as written. Pull requests here do land as merge commits (504 of 504
    landings in the last 1000 first-parent commits of origin/main at
    `60bb3756`, 2026-11-28), which is what makes an unrewritten branch commit
    reachable. "As written" is the condition that fails in practice: 14 of
    the 26 committed board records at `60bb3756` cite a commit in no object
    store. D01A's is one. Merge #492 was a true merge, and its record cites
    `4b560cb`, a commit of the branch from BEFORE it was rebased onto main;
    the rebased "Regenerate D01A" landed as `e7c846fa` instead. So a record
    is scorable only if it cites a commit that has landed unrewritten: write
    one at a commit already on main, or do not rewrite the branch afterwards.
    """
    if not isinstance(commit, str) or not COMMIT_ID.fullmatch(commit):
        return None
    try:
        r = subprocess.run(["git", "-C", str(root), "merge-base", "--is-ancestor",
                            "--", commit, "origin/main"],
                           capture_output=True, timeout=30)
    except Exception:
        return None
    return {0: True, 1: False}.get(r.returncode)


def sha_of(*paths) -> str:
    """A short content hash over one or more files, read in the given order.

    No `root`: the paths are used as given. An unused `root=` parameter that
    call sites nonetheless passed was worse than none -- it read as though the
    hash were relative to a worktree when it never was."""
    h = hashlib.sha256()
    for p in paths:
        h.update(pathlib.Path(p).read_bytes())
    return h.hexdigest()[:12]


def file_sha(path) -> str:
    """A content hash for one file, tagged with its algorithm and safe to
    write directly into a provenance record. `"missing"` (not an exception)
    when the file is not there -- a hash a reader can act on either way.

    No `root`: as with `sha_of`, the path is used as given."""
    try:
        return "sha256:" + hashlib.sha256(pathlib.Path(path).read_bytes()).hexdigest()[:16]
    except OSError:
        return "missing"


def source_commit(root: pathlib.Path = ROOT) -> str:
    return (git("rev-parse", "--short", "HEAD", root=root).strip() or "?")


def worktree_state(root: pathlib.Path = ROOT) -> dict:
    """The commit is not enough. Fourteen worktrees are live on this repository
    at once, and a clean SHA that silently means "plus
    whatever was in the working tree" is worse than no SHA: a stale result is
    indistinguishable from a current one. So the uncommitted diff is hashed
    too -- tracked modifications from `git diff HEAD`, and every untracked
    file git would not ignore, by content. `dirty` says which of the two
    kinds of record this is.

    A clean tree is not enough either (#2043). A commit that exists only on a
    local branch or an unpushed worktree is one nobody else can check out, so
    a record citing it cannot be re-derived however clean the tree was.
    `on_origin_main` records `commit_on_origin_main`'s answer and `dirty` is
    True unless that answer is True: `False` (a branch) and `None` (no
    `origin/main`, a commit git cannot place, git not answering) both read
    dirty. It extends #615's rule -- an unanswered git is not a clean tree --
    from "is there a commit" to "can anybody else find it". The cost is that
    a record written on a feature branch reads dirty until the branch is
    merged with a merge commit and this checkout has fetched; the assumption
    behind that is `commit_on_origin_main`'s.

    What `dirty: false` therefore does NOT say: that the measured tree equals
    the commit's. It says so only when `uncommitted_sha256` is the hash of no
    diff and `untracked_files` is 0 as well. D02A on the committed board is
    the example -- a commit on origin/main, measured with six untracked files.
    """
    commit = git("rev-parse", "HEAD", root=root).strip()
    # `git` returns "" on failure, so an unanswered git must not read as an
    # empty diff: no commit means "cannot say", i.e. dirty (#615)
    on_origin_main = commit_on_origin_main(commit, root=root)
    h = hashlib.sha256()
    diff = git("diff", "HEAD", root=root)
    h.update(diff.encode())
    untracked = [f for f in git("ls-files", "--others", "--exclude-standard",
                                  root=root).split("\n") if f]
    for rel in sorted(untracked):
        f = root / rel
        try:
            h.update(rel.encode())
            h.update(hashlib.sha256(f.read_bytes()).digest())
        except OSError:
            h.update(b"?")
    return {"commit": source_commit(root=root),
            "described": git("describe", "--always", "--dirty", root=root).strip() or "?",
            "branch": git("rev-parse", "--abbrev-ref", "HEAD", root=root).strip() or "?",
            "dirty": bool(diff.strip() or untracked) or on_origin_main is not True,
            "on_origin_main": on_origin_main,
            "uncommitted_sha256": h.hexdigest()[:16],
            "untracked_files": len(untracked)}


def now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
