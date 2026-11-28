#!/usr/bin/env python3
"""The shared tail of the compare_*_candidate.py scripts: the provenance
block every candidate delta record carries, and the path handling and
output guard its main() wraps the write in.

This is the block that was byte-identical in `compare_drum_i2s_candidate.py`,
`compare_ensemble_candidate.py` and `compare_m5a_i2s_candidate.py`, extracted
once (issue #1187) so it cannot drift into three competing shapes -- the same
reason `tools/provenance.py` exists for the render/analyse/accept scheme.

FAIL-CLOSED, BY DECISION. The git calls run with `check=True`: a git that
cannot answer raises `CalledProcessError`, the caller's `main()` renders it
as REFUSED (exit 2), and no record is written. That is deliberately NOT
`provenance.git()`'s contract (empty string on any failure, #615): a delta
record whose provenance says `source_commit: ""` with `source_dirty: False`
is an answer from an apparatus that could not answer, and it looks exactly
like data. When #599 lands one failure encoding for git-state readers this
is a call site to migrate; until then fail-closed IS the contract, and
`tools/test_compare_candidate_tails.py` pins it.

The key set and value shapes are frozen to what the three comparators
already wrote into `docs/scorecard/`: `run_at_utc` keeps aware-isoformat's
`...+00:00` shape, not `provenance.now()`'s `%Y-%m-%dT%H:%M:%SZ`, and
`source_dirty` stays "does `git status --porcelain` say anything" -- not
`worktree_state()["dirty"]`'s diff-plus-untracked, fail-closed-on-no-commit
definition, which would change the meaning of every committed delta record.
"""
from __future__ import annotations

import datetime
import hashlib
import json
import pathlib
import subprocess

ROOT = pathlib.Path(__file__).resolve().parent.parent


def repo_path(text: str, root: pathlib.Path = ROOT) -> pathlib.Path:
    """Resolve a CLI path argument against the repository root: absolute
    paths as given, relative paths inside the repo."""
    path = pathlib.Path(text)
    return path if path.is_absolute() else root / path


def comparison_provenance(baseline_path, candidate_path, sources,
                          root: pathlib.Path = ROOT) -> dict:
    """The provenance block of a candidate delta record: which commit, which
    tree state, the exact bytes of both input records and of every source
    that produced them, and when.

    REFUSES (by raising, for the caller's main() to render) rather than
    recording when git cannot answer -- see the module docstring."""
    commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=root,
                            check=True, capture_output=True, text=True).stdout.strip()
    dirty = bool(subprocess.run(["git", "status", "--porcelain"], cwd=root,
                                check=True, capture_output=True, text=True).stdout.strip())
    return {
        "source_commit": commit, "source_dirty": dirty,
        "input_sha256": {
            "baseline_record": hashlib.sha256(pathlib.Path(baseline_path).read_bytes()).hexdigest(),
            "candidate_record": hashlib.sha256(pathlib.Path(candidate_path).read_bytes()).hexdigest()},
        "source_sha256": {name: hashlib.sha256((root / name).read_bytes()).hexdigest()
                          for name in sources},
        "run_at_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    }


def write_report(report: dict, out_text: str, tool: str,
                 root: pathlib.Path = ROOT) -> pathlib.Path | None:
    """Write one delta record, refusing an `--out` that escapes the tree it
    describes.

    Returns the resolved output path, or None when `out_text` resolves
    outside `root` -- the caller renders that as REFUSED, exit 2, with the
    same `<tool>: REFUSED -- ...` line the three scripts printed inline.

    Raises ValueError, having written nothing, when the report holds a NaN
    or Infinity. `json.dumps` writes those as bare tokens by default and
    they are not JSON (#1406, #1409), so this is the backstop behind every
    guard that keeps a non-finite number from getting this far (#1636); the
    caller's main() renders it as REFUSED, exit 2. The record is serialised
    before anything on disk is touched, so a file already at `out` is left
    exactly as it was."""
    out = repo_path(out_text, root=root)
    if not out.resolve().is_relative_to(root):
        print(f"{tool}: REFUSED -- output must remain inside the repository")
        return None
    try:
        text = json.dumps(report, indent=2, allow_nan=False) + "\n"
    except ValueError as exc:
        raise ValueError(f"the report holds a number that is not JSON ({exc})") from exc
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text)
    return out
