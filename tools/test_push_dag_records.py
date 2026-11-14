"""Controls for tools/push_dag_records.py (PR #818 review).

The bare `git push` this tool replaces loses the nightly's slow-node records
on a non-fast-forward rejection roughly one night in three (measured in the
PR review). The retry's correctness lives in two places, and each has a
control here:

- the UNION MERGE: slow nodes must keep ours (the nightly is the only
  producer of slow-node evidence) while fast nodes take theirs (dag.yml
  re-records them fresher on every plain push). Getting this backwards
  silently reverts one side's freshness -- the #600 "merge deleted the slow
  stamps wholesale" failure, rebuilt one level up.
- the driver: a rejected push must retry bounded, and must not commit (or
  touch any file) when the records are unchanged.

The injected-bug controls: swapping the slow-node branch in union_merge, or
making the driver commit on an unchanged tree, must turn these red.
"""
from __future__ import annotations
import json
import pathlib
import subprocess
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import push_dag_records as pdr                                   # noqa: E402


# --------------------------------------------------------------------- merge
SLOW = {"F1"}
OURS = {
    "F1": {"passed": True, "sha": "1" * 40, "detail": "fresh slow run"},
    "F3": {"passed": True, "sha": "2" * 40, "detail": "stale fast run"},
}
THEIRS = {
    "F1": {"passed": True, "sha": "0" * 40, "detail": "last night's slow run"},
    "F3": {"passed": True, "sha": "3" * 40, "detail": "fresher fast run"},
    "F9": {"passed": False, "sha": "3" * 40, "detail": "theirs-only node"},
}


def test_slow_nodes_keep_ours_even_when_theirs_is_newer_looking():
    merged = pdr.union_merge(OURS, THEIRS, SLOW)
    assert merged["F1"] == OURS["F1"], \
        "a slow node's record was taken from origin/main -- the nightly is " \
        "the only producer of slow evidence, so this is the #787 loss"


def test_fast_nodes_take_theirs():
    merged = pdr.union_merge(OURS, THEIRS, SLOW)
    assert merged["F3"] == THEIRS["F3"], \
        "a fast node's record was kept from ours when origin/main had a " \
        "fresher one from dag.yml"


def test_one_sided_records_survive():
    assert pdr.union_merge(OURS, {}, SLOW) == OURS
    merged = pdr.union_merge({}, THEIRS, SLOW)
    assert merged == THEIRS


# ------------------------------------------------------------------- driver
def _cp(rc: int, out_text: str = "", err: str = "") -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess([], rc, out_text, err)


class FakeGit:
    """Scripts the git/subprocess surface of the driver.

    `push_failures` leading pushes are rejected; after that pushes succeed.
    `rebase_conflict` makes every rebase report a conflict, so the driver
    takes the union-merge path.
    """

    def __init__(self, push_failures: int = 0, rebase_conflict: bool = False,
                 diff_quiet: bool = False):
        self.commands = []
        self.push_failures = push_failures
        self.rebase_conflict = rebase_conflict
        self.diff_quiet = diff_quiet

    def __call__(self, *a, **kw):
        self.commands.append(a)
        if a[0] == sys.executable:                      # compile_dag.py
            return _cp(0)
        sub = a[1]
        if sub == "push":
            if self.push_failures > 0:
                self.push_failures -= 1
                return _cp(1, "", "! [rejected] main -> main (non-fast-forward)")
            return _cp(0)
        if sub == "diff" and "--quiet" in a:
            return _cp(0 if self.diff_quiet else 1)
        if sub == "rev-parse":
            return _cp(0, "0" * 40 + "\n" if "HEAD" in a else str(pdr.REPO))
        if sub == "show":
            spec = a[-1]
            if spec.endswith(pdr.RECORDS):
                which = THEIRS if spec.startswith("origin/") else OURS
                return _cp(0, json.dumps(which))
            if spec.endswith(pdr.README):
                return _cp(0, "# readme\n")
            return _cp(1, "", "bad pathspec")
        if sub == "rebase":
            if "--continue" in a:
                return _cp(0)
            return _cp(1, "", "CONFLICT (content): both modified") \
                if self.rebase_conflict else _cp(0)
        return _cp(0)                                   # config/add/commit/fetch

    def count(self, sub: str) -> int:
        return sum(1 for c in self.commands if len(c) > 1 and c[1] == sub)


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    """Driver state re-pointed at tmp_path: no test may write the worktree's
    README.md or docs/dag-results.json."""
    monkeypatch.setattr(pdr, "REPO", tmp_path)
    monkeypatch.setattr(pdr, "RECORDS", "rec.json")
    monkeypatch.setattr(pdr, "README", "readme.md")
    monkeypatch.chdir(tmp_path)
    return tmp_path


def test_an_unchanged_tree_is_not_committed(isolated, monkeypatch):
    fake = FakeGit(diff_quiet=True)
    monkeypatch.setattr(pdr, "run", fake)
    assert pdr.main() == 0
    assert fake.count("commit") == 0, "committed although the records never moved"
    assert fake.count("push") == 0


def test_a_clean_push_needs_one_round(isolated, monkeypatch):
    fake = FakeGit()
    monkeypatch.setattr(pdr, "run", fake)
    assert pdr.main() == 0
    assert fake.count("commit") == 1
    assert fake.count("push") == 1
    commits = [c for c in fake.commands if len(c) > 1 and c[1] == "commit"]
    assert any("[dag]" in str(a) for a in commits), \
        "the pushed commit lost the [dag] guard string"


def test_a_moved_main_is_rebased_and_pushed_again(isolated, monkeypatch):
    fake = FakeGit(push_failures=1)
    monkeypatch.setattr(pdr, "run", fake)
    monkeypatch.setattr(pdr, "regenerate_readme", lambda: 0)
    assert pdr.main() == 0
    assert fake.count("push") == 2
    assert fake.count("commit") == 1, "a rebase must never mint a second commit"


def test_a_rebase_conflict_resolves_by_union_merge(isolated, monkeypatch):
    fake = FakeGit(push_failures=1, rebase_conflict=True)
    monkeypatch.setattr(pdr, "run", fake)
    monkeypatch.setattr(pdr, "regenerate_readme", lambda: 0)
    assert pdr.main() == 0

    merged = json.loads((isolated / pdr.RECORDS).read_text())
    assert merged["F1"] == OURS["F1"], "slow record lost in the conflict resolution"
    assert merged["F3"] == THEIRS["F3"], "fast record reverted in the resolution"
    assert fake.count("push") == 2


def test_the_retry_is_bounded(isolated, monkeypatch):
    fake = FakeGit(push_failures=99, rebase_conflict=True)
    monkeypatch.setattr(pdr, "run", fake)
    monkeypatch.setattr(pdr, "regenerate_readme", lambda: 0)
    assert pdr.main() == 1
    assert fake.count("push") == pdr.ATTEMPTS, "the retry ran past its bound"
