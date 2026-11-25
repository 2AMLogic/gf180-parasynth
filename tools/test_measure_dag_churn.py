"""Validation for tools/measure_dag_churn.py against a REAL git history (#967).

The instrument produces the number the PR records ("would have suppressed"),
so a mis-classified bucket or a mis-parsed hunk range is a wrong headline
number, delivered with confidence. These tests pin it two ways:

  * the bucket classifier, against the same buckets the writer implements
    (verdict / detail-beyond-timing / re-anchor / unmeasurable / suppressed);
  * the whole replay, end-to-end, on a synthetic history built commit by
    commit with content this test chooses -- so the classification and the
    hunk counts are checked against git's actual diff output, not against a
    model of it.

And the precondition: a shallow clone cannot answer the staleness question,
so replay() must REFUSE -- a tool that answers when it cannot is worse than
one that is absent.
"""
from __future__ import annotations
import json, pathlib, subprocess
import pytest

import measure_dag_churn as mc                                     # noqa: E402


def _git(repo, *a, check=True):
    r = subprocess.run(["git", *a], cwd=str(repo), capture_output=True, text=True)
    if check:
        assert r.returncode == 0, f"git {' '.join(a)} failed: {r.stderr}"
    return r.stdout.strip()


def _commit(repo, msg):
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", msg)
    return _git(repo, "rev-parse", "HEAD")


README_TEMPLATE = """# board

commits: {commits}

{lead}
<!-- DAG:BEGIN -->
| {header} |
|---|
{row}
<!-- DAG:END -->

tail one
tail two
"""

# Nine filler lines: with -U3, two hunks merge when fewer than 2*3+1 lines
# separate the changed regions, and this test pins the counter hunk OUTSIDE
# the DAG block as a distinct hunk from the row inside it.
LEAD = "\n".join(f"lead {i}" for i in range(1, 10))


@pytest.fixture
def hist(tmp_path):
    """A synthetic main: init -> work(docs) -> [dag] -> work(covered) -> [dag].

    The bot semantics are the real ones: a record anchored at the pre-bot
    HEAD sits at the [dag] commit's parent, and the [dag] commit re-stamps it
    at its own parent plus a timing-only detail refresh. dag1's rewrite
    (covers untouched between the two recorded shas) is exactly what the
    #967 rule would suppress; dag2's (same, but the covered file changed in
    between) is a re-anchor the rule must keep. Returns (path, shas dict)."""
    repo = tmp_path / "synth"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "t")
    (repo / "docs").mkdir()
    (repo / "suite_x").mkdir()
    (repo / "suite_x" / "test.py").write_text("def test_x(): pass\n")
    (repo / "f.txt").write_text("v1\n")
    (repo / "g.txt").write_text("g1\n")
    (repo / "docs" / "dag.json").write_text(json.dumps(
        {"nodes": {"X": {"name": "x", "suite": "suite_x/test.py",
                         "covers": ["f.txt"]}}}))
    (repo / "docs" / "dag-results.json").write_text("{}\n")
    (repo / "README.md").write_text(
        README_TEMPLATE.format(commits=1, header="`X`", row="", lead=LEAD))
    c1 = _commit(repo, "init")

    def stage(sha, secs, commits):
        rec = {"X": {"passed": True, "sha": sha,
                     "detail": f"105 passed in {secs}"}}
        (repo / "docs" / "dag-results.json").write_text(
            json.dumps(rec, indent=2, sort_keys=True) + "\n")
        (repo / "README.md").write_text(README_TEMPLATE.format(
            commits=commits, header="`X`",
            row=f"| `X` | x | **GREEN** | 105 passed in {secs} |",
            lead=LEAD))

    (repo / "g.txt").write_text("g2\n")
    stage(c1, "3.20s", 1)          # the record anchored at the pre-bot HEAD
    w1 = _commit(repo, "work: docs only, evidence recorded")

    stage(w1, "3.45s", 2)
    dag1 = _commit(repo, "[dag] regenerate the roadmap from evidence")

    (repo / "f.txt").write_text("v2 -- covered change\n")
    w2 = _commit(repo, "work: the covered file")

    stage(w2, "3.51s", 3)
    dag2 = _commit(repo, "[dag] regenerate the roadmap from evidence")

    return repo, {"c1": c1, "w1": w1, "dag1": dag1, "w2": w2, "dag2": dag2}


def test_replay_classifies_suppressible_and_reanchor_commits(hist):
    repo, sha = hist
    rows = mc.replay(repo, 10)
    by = {r["commit"]: r for r in rows}
    assert set(by) == {sha["dag1"][:8], sha["dag2"][:8]}, \
        "only the [dag] commits are replayed"

    d1 = by[sha["dag1"][:8]]
    assert d1["records_changed"] == 1 and d1["suppressed"] == 1
    assert d1["reasons"]["X"] == "suppressed"
    assert d1["hunks_results"] == 1
    assert d1["hunks_readme"] == 2, "one hunk in the block, one counter hunk out"
    assert d1["hunks_readme_dagblock"] == 1
    assert d1["hunks_readme_other"] == 1

    d2 = by[sha["dag2"][:8]]
    assert d2["records_changed"] == 1 and d2["suppressed"] == 0
    assert d2["reasons"]["X"] == "covers changed -> re-anchor required"


def test_classify_rewrite_buckets(hist):
    repo, sha = hist
    nodes = json.loads((repo / "docs" / "dag.json").read_text())["nodes"]
    old = {"passed": True, "sha": sha["c1"], "detail": "105 passed in 3.20s"}

    assert mc.classify_rewrite(repo, old,
                               {"passed": False, "sha": sha["w2"],
                                "detail": "1 failed in 3.20s"},
                               nodes["X"], sha["w2"]) == "verdict changed"
    assert mc.classify_rewrite(repo, old,
                               {"passed": True, "sha": sha["w2"],
                                "detail": "104 passed in 3.20s"},
                               nodes["X"], sha["w2"]) == \
        "detail changed beyond timing"
    # a recorded sha outside history is unmeasurable, not unchanged
    assert mc.classify_rewrite(repo,
                               {"passed": True, "sha": "f" * 40,
                                "detail": "105 passed in 3.20s"},
                               {"passed": True, "sha": sha["w2"],
                                "detail": "105 passed in 3.45s"},
                               nodes["X"], sha["w2"]) == \
        "covers unmeasurable -> rewrite kept"


def test_refuses_a_shallow_clone(tmp_path, hist):
    repo, _ = hist
    clone = tmp_path / "shallow"
    r = subprocess.run(["git", "clone", "-q", "--depth", "1",
                        f"file://{repo}", str(clone)], capture_output=True,
                       text=True)
    assert r.returncode == 0, r.stderr
    with pytest.raises(SystemExit, match="REFUSED"):
        mc.replay(clone, 10)


def test_refuses_a_history_with_no_dag_commits(tmp_path):
    repo = tmp_path / "plain"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "t")
    (repo / "x.txt").write_text("x\n")
    _commit(repo, "no dag commits here")
    with pytest.raises(SystemExit, match="REFUSED"):
        mc.replay(repo, 10)
