"""Controls for tools/ci_changes.py and the workflow wiring that consumes it.

The only failure of a change filter that matters is a false skip: a real
change classified as one no suite reads. So most of these are that failure,
injected, and required to come back `full`.
"""
from __future__ import annotations

import pathlib
import subprocess
import sys

import pytest
import yaml

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import ci_changes as cc                                              # noqa: E402

REPO = pathlib.Path(__file__).resolve().parents[1]
WF = REPO / ".github" / "workflows"

FULL_IF = ("${{ !cancelled() && needs.changes.outputs.scope != 'none'"
           " && needs.changes.outputs.scope != 'docs' }}")
DOCS_IF = "${{ needs.changes.outputs.scope == 'docs' }}"

GATED = {
    "rungs.yml": {"pulse2x-rtl", "reference-controls", "m5a-fast", "python", "rtl"},
    "arty.yml": {"spi-i2s"},
    "provenance.yml": {"stages-and-controls"},
    "trials.yml": {"trial-deadline", "trial-release-bound", "trial-physical"},
}


def nothing_named(_name: str) -> bool:
    return False


def everything_named(_name: str) -> bool:
    return True


def scope(paths, referenced=nothing_named):
    return cc.scope_of(paths, referenced, report=lambda _l: None)


# =============================================================================
# The issue's four scenarios, and the fail-safe.
# =============================================================================
def test_tooling_only_change_runs_nothing_substantive():
    assert scope([".claude/skills/x/SKILL.md", ".agents/a.json",
                  ".loom/scripts/w.sh", ".github/labels.yml"]) == cc.NONE


def test_prose_only_change_runs_the_doc_claim_check():
    assert scope(["docs/target.md", "NOTES.md", ".github/CONFIGURATION.md"]) == cc.DOCS


def test_prose_plus_tooling_is_still_docs():
    assert scope(["docs/target.md", ".claude/settings.json"]) == cc.DOCS


def test_model_change_runs_everything():
    assert scope(["docs/target.md", "model/voice.py"]) == cc.FULL


@pytest.mark.parametrize("path", [
    "newdir/file.txt",            # the injected unclassified control
    "newdir/notes.md",            # Markdown, but not where prose lives
    "loom.sh", "package.json", ".gitignore", "Makefile",
    ".github/workflows/rungs.yml", ".github/ISSUE_TEMPLATE/task.yml",
    ".github/labels.yaml",        # near-miss of the one exact tooling file
    ".claudex/x", "claude/x",     # near-misses of the tooling prefixes
    "docs/dag.json", "docs/surge-waveform-mapping.txt",
    "fpga/ARTY.md", "spec/NUMERIC-CONTRACT.md",
    "spec/decision-records/0001-x.md", "model/README.md", "tools/x.md",
])
def test_anything_not_positively_recognised_runs_everything(path):
    assert scope([path]) == cc.FULL, path
    assert scope([path, ".claude/x"]) == cc.FULL, path


def test_prose_a_suite_names_runs_everything():
    assert scope(["docs/target.md"], everything_named) == cc.FULL


def test_the_reference_check_asks_by_basename():
    asked = []
    cc.classify("docs/deadline/README.md", lambda n: asked.append(n) or False)
    assert asked == ["README.md"]


def test_no_changed_paths_runs_everything():
    assert scope([]) == cc.FULL


# =============================================================================
# The reference scan, against the real tree.
# =============================================================================
def test_the_file_that_motivated_this_is_test_input():
    # #456 edited fpga/ARTY.md; fpga/test_ext_io_timing.py hashes it. Even if
    # the directory rule went away, the reference scan must still catch it.
    assert cc.name_is_referenced("ARTY.md")
    assert cc.name_is_referenced("tr808-reference.md")


def test_an_unnamed_document_is_not_referenced():
    assert not cc.name_is_referenced("no-such-document-issue-457.md")


def test_a_git_error_counts_as_referenced(tmp_path):
    assert cc.name_is_referenced("anything.md", cwd=tmp_path)


# =============================================================================
# Which commits are compared, on a real (temporary) repository.
# =============================================================================
def _git(repo, *args):
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True,
                   env={"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
                        "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t",
                        "HOME": str(repo), "PATH": "/usr/bin:/bin:/usr/local/bin"})


def _commit(repo, rel, text, msg):
    p = repo / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", msg)


@pytest.fixture
def repo(tmp_path):
    """main: model/voice.py. feature: a docs edit. main then moves on with a
    model edit, so a wrong base would attribute main's model change to the PR."""
    _git(tmp_path, "init", "-q", "-b", "main")
    _commit(tmp_path, "model/voice.py", "a\n", "base")
    _git(tmp_path, "checkout", "-q", "-b", "feature")
    _commit(tmp_path, "docs/target.md", "prose\n", "docs")
    _git(tmp_path, "checkout", "-q", "main")
    _commit(tmp_path, "model/voice.py", "b\n", "main moves on")
    return tmp_path


def test_pull_request_compares_the_merge_against_its_base(repo):
    _git(repo, "merge", "-q", "--no-ff", "-m", "merge", "feature")
    paths, _ = cc.changed_paths("pull_request", "refs/pull/1/merge", cwd=repo)
    assert paths == ["docs/target.md"]


def test_pull_request_without_a_merge_commit_runs_everything(repo):
    paths, why = cc.changed_paths("pull_request", "refs/pull/1/merge", cwd=repo)
    assert paths is None and "merge commit" in why


def test_branch_push_compares_the_whole_branch_against_main(repo):
    _git(repo, "checkout", "-q", "feature")
    _commit(repo, "docs/other.md", "more\n", "second docs commit")
    paths, _ = cc.changed_paths("push", "refs/heads/feature", base="main", cwd=repo)
    assert paths == ["docs/other.md", "docs/target.md"]


def test_branch_push_with_no_base_runs_everything(repo):
    paths, _ = cc.changed_paths("push", "refs/heads/feature", base="origin/nope", cwd=repo)
    assert paths is None


def test_a_rename_out_of_code_reports_the_code_path(repo):
    _git(repo, "checkout", "-q", "feature")
    _git(repo, "mv", "model/voice.py", "docs/voice.md")
    _git(repo, "commit", "-q", "-m", "rename")
    paths, _ = cc.changed_paths("push", "refs/heads/feature", base="main", cwd=repo)
    assert "model/voice.py" in paths
    assert scope(paths) == cc.FULL


@pytest.mark.parametrize("event,ref", [
    ("push", "refs/heads/main"), ("workflow_dispatch", "refs/heads/feature"),
    ("schedule", "refs/heads/main"), ("", ""),
])
def test_main_and_other_events_run_everything(repo, event, ref):
    paths, _ = cc.changed_paths(event, ref, base="main", cwd=repo)
    assert paths is None


def test_main_writes_the_scope_output(tmp_path):
    out = tmp_path / "out"
    cc.main(["--paths", ".claude/x", "--github-output", str(out)])
    cc.main(["--paths", "newdir/x", "--github-output", str(out)])
    assert out.read_text() == "scope=none\nscope=full\n"


# =============================================================================
# The wiring. A classifier nobody consults, or consulted in a form that skips
# when it fails, is the same false green one level up.
# =============================================================================
def _load(name):
    return yaml.safe_load((WF / name).read_text())


def _eval_full_if(scope_output: str) -> bool:
    """FULL_IF with `!cancelled()` true and the output substituted -- the
    exact expression the wiring test pins, so this is its truth table."""
    expr = FULL_IF[3:-2].replace("!cancelled()", "True").replace("&&", "and")
    expr = expr.replace("needs.changes.outputs.scope", repr(scope_output))
    return eval(expr)


def test_the_gate_runs_on_everything_but_an_explicit_skip():
    assert _eval_full_if("full")
    assert _eval_full_if("")          # changes job failed: no output -> run
    assert _eval_full_if("garbage")
    assert not _eval_full_if("none")
    assert not _eval_full_if("docs")


@pytest.mark.parametrize("wf", sorted(GATED))
def test_every_suite_job_is_gated_fail_safe(wf):
    jobs = _load(wf)["jobs"]
    ch = jobs["changes"]
    assert ch["outputs"]["scope"] == "${{ steps.classify.outputs.scope }}"
    step = next(s for s in ch["steps"] if s.get("id") == "classify")
    assert "tools/ci_changes.py" in step["run"]
    co = next(s for s in ch["steps"] if s.get("uses", "").startswith("actions/checkout"))
    assert str(co["with"]["fetch-depth"]) == "0"
    for name in GATED[wf]:
        job = jobs[name]
        needs = job.get("needs")
        needs = [needs] if isinstance(needs, str) else needs
        assert "changes" in needs, f"{wf}:{name} does not need changes"
        assert job.get("if") == FULL_IF, f"{wf}:{name} if: {job.get('if')!r}"
    ungated = set(jobs) - GATED[wf] - {"changes", "doc-claims", "rtl-full"}
    assert not ungated, f"{wf}: jobs not accounted for: {ungated}"


def test_docs_scope_runs_the_claim_checker_and_its_controls():
    job = _load("rungs.yml")["jobs"]["doc-claims"]
    assert job["if"] == DOCS_IF
    runs = "\n".join(s.get("run", "") for s in job["steps"])
    assert "pytest tools/test_check_doc_claims.py" in runs
    assert "python tools/check_doc_claims.py" in runs


def test_the_python_job_runs_these_controls():
    import check_workflows as cw
    blocks, why = cw.step_would_block_pull_request(
        WF / "rungs.yml", "python", "tools/test_ci_changes.py")
    assert blocks, why
