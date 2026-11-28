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
import json, pathlib, subprocess, sys
import pytest

import exit_contract                                               # noqa: E402
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


# ---- #1857: the exit status when nothing was replayed -----------------------
#
# `main` returned 0 after the totals, and `totals["commits"]` is 0 when every
# [dag] commit it found was skipped -- a merge, or no readable results file.
# Reproduced on origin/main @ 528a228b (2026-11-28): a history whose one [dag]
# commit has no results file printed "replayed 0 linear [dag] commits: 0
# record rewrites, 0 would have been suppressed (0%)" and exited 0. That
# "(0%)" is a zero that looks like data, which this tool's own docstring says
# it refuses rather than print.
#
# So ZERO REPLAYED is a refusal: the unit this instrument measures is a
# replayed linear commit, and with none the question "which rewrites would the
# rule have suppressed" was not put to any commit. ZERO RECORD REWRITES among
# replayed commits is a measured zero and stays 0. The control below pins
# the second over a window of exactly one such commit, so the same "(0%)"
# text is shown to be a result when a commit was replayed.
#
# Red-first on that tree, 2026-11-28: the refusal test failed with rc 0 and
# that control was green.


def test_nothing_replayed_is_refused_not_a_pass(tmp_path, capsys):
    repo = tmp_path / "synth"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "t")
    (repo / "README.md").write_text("# board\n")
    _commit(repo, "init")
    (repo / "README.md").write_text("# board\n\n[dag] touched\n")
    _commit(repo, "[dag] regenerate the roadmap from evidence")

    assert mc.main(["--repo", str(repo)]) == exit_contract.REFUSED
    out = capsys.readouterr().out
    assert "no results file at parent or commit -- skipped" in out, "the row is still on record"
    assert "would have been suppressed" not in out, "no zero that looks like data: " + out
    verdicts = [ln for ln in out.splitlines() if ln.startswith("REFUSED")]
    assert len(verdicts) == 1 and out.splitlines()[-1] == verdicts[0], out
    assert "0 of 1 [dag] commit(s) replayed" in verdicts[0], verdicts[0]

    proc = subprocess.run([sys.executable, mc.__file__, "--repo", str(repo)],
                          capture_output=True, text=True, timeout=120)
    assert (proc.returncode, proc.stderr) == (exit_contract.REFUSED, ""), (proc.stdout, proc.stderr)
    assert proc.stdout.splitlines()[-1].startswith("REFUSED:"), proc.stdout


def test_the_control_replayed_commits_still_pass_even_when_nothing_is_suppressed(hist, capsys):
    """THE CONTROL. `hist` replays two commits whose records were both
    rewritten, one of them suppressible. A third [dag] commit that rewrites
    no record adds a measured zero, and a window of that one commit alone
    prints "0 record rewrites, 0 would have been suppressed (0%)" over ONE
    replayed commit -- a result. Widening the rule to "nothing suppressed"
    or to "(0%) in the headline" turns this red."""
    repo, _ = hist
    assert mc.main(["--repo", str(repo)]) == exit_contract.PASS
    out = capsys.readouterr().out
    assert "replayed 2 linear [dag] commits: 2 record rewrites, 1 would have been suppressed (50%)" in out, out
    assert not any(ln.startswith("REFUSED") for ln in out.splitlines()), out

    readme = repo / "README.md"
    readme.write_text(readme.read_text().replace("tail one", "tail uno"))
    _commit(repo, "[dag] regenerate the roadmap from evidence")

    assert mc.main(["--repo", str(repo)]) == exit_contract.PASS
    out = capsys.readouterr().out
    assert "replayed 3 linear [dag] commits: 2 record rewrites, 1 would have been suppressed (50%)" in out, out
    assert "1 of 3 commits' docs/dag-results.json diffs would have been empty" in out, out

    assert mc.main(["--repo", str(repo), "--commits", "1"]) == exit_contract.PASS
    out = capsys.readouterr().out
    assert "replayed 1 linear [dag] commits: 0 record rewrites, 0 would have been suppressed (0%)" in out, out
    assert not any(ln.startswith("REFUSED") for ln in out.splitlines()), out
