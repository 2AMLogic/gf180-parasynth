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
import subprocess

ROOT = pathlib.Path(__file__).resolve().parent.parent


def git(*args: str, root: pathlib.Path = ROOT) -> str:
    """Run a git command against `root`. Empty string on any failure -- a
    provenance field that cannot be determined is recorded as absent, never
    guessed at."""
    try:
        return subprocess.check_output(["git", "-C", str(root), *args], text=True,
                                        stderr=subprocess.DEVNULL)
    except Exception:
        return ""


def sha_of(*paths, root: pathlib.Path = ROOT) -> str:
    """A short content hash over one or more files, read in the given order."""
    h = hashlib.sha256()
    for p in paths:
        h.update(pathlib.Path(p).read_bytes())
    return h.hexdigest()[:12]


def file_sha(path, root: pathlib.Path = ROOT) -> str:
    """A content hash for one file, tagged with its algorithm and safe to
    write directly into a provenance record. `"missing"` (not an exception)
    when the file is not there -- a hash a reader can act on either way."""
    try:
        return "sha256:" + hashlib.sha256(pathlib.Path(path).read_bytes()).hexdigest()[:16]
    except OSError:
        return "missing"


def source_commit(root: pathlib.Path = ROOT) -> str:
    return (git("rev-parse", "--short", "HEAD", root=root).strip() or "?")


def worktree_state(root: pathlib.Path = ROOT) -> dict:
    """The commit is not enough. Several worktrees are commonly live against
    this repository at once, and a clean SHA that silently means "plus
    whatever was in the working tree" is worse than no SHA: a stale result is
    indistinguishable from a current one. So the uncommitted diff is hashed
    too -- tracked modifications from `git diff HEAD`, and every untracked
    file git would not ignore, by content. `dirty` says which of the two
    kinds of record this is."""
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
            "dirty": bool(diff.strip() or untracked),
            "uncommitted_sha256": h.hexdigest()[:16],
            "untracked_files": len(untracked)}


def now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
