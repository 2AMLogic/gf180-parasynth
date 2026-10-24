"""Controls for `check_workflows.step_would_block_pull_request` (issue #307).

`tools/test_manifest.py` and `tools/test_check_surge_waveform_comment.py` each
guard their own CI wiring by asserting a substring appears in a named job's
`run:` strings -- which only proves the step EXISTS. Measured during #304's
review: a step with `if: false` or `continue-on-error: true` still matches
that substring check and leaves the guard green, even though neither can ever
block a pull request. These are unit-level controls against constructed YAML,
not full-source mutation, exercising the helper both tests now call.
"""
from __future__ import annotations

import pathlib
import sys

import yaml

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import check_workflows as cw                                        # noqa: E402

REPO = pathlib.Path(__file__).resolve().parents[1]
RUNGS = REPO / ".github" / "workflows" / "rungs.yml"


def _workflow(job_yaml: str) -> dict:
    """One job's worth of YAML, wrapped in the minimal envelope the helper
    needs (`on:` is irrelevant to it and omitted on purpose)."""
    return yaml.safe_load(f"jobs:\n{job_yaml}")


# =============================================================================
# The two holes #304's review measured: present, matching, and green anyway.
# =============================================================================
def test_a_step_with_bare_if_false_does_not_block():
    wf = _workflow("""\
  guard:
    runs-on: ubuntu-latest
    steps:
      - name: the gate
        if: false
        run: python tools/check_surge_waveform_comment.py
""")
    blocks, reason = cw.step_would_block_pull_request(wf, "guard", "check_surge_waveform_comment.py")
    assert blocks is False
    assert "if:" in reason and "false" in reason.lower()


def test_a_step_with_continue_on_error_does_not_block():
    wf = _workflow("""\
  guard:
    runs-on: ubuntu-latest
    steps:
      - name: the gate
        continue-on-error: true
        run: python tools/check_surge_waveform_comment.py
""")
    blocks, reason = cw.step_would_block_pull_request(wf, "guard", "check_surge_waveform_comment.py")
    assert blocks is False
    assert "continue-on-error" in reason


def test_a_quoted_false_expression_is_also_constant_false():
    """`if:` need not be bare YAML `false` -- GitHub accepts the same
    constant written as a string expression. Not general expression
    evaluation, just the one extra spelling of the same constant."""
    wf = _workflow("""\
  guard:
    runs-on: ubuntu-latest
    steps:
      - name: the gate
        if: ${{ false }}
        run: python tools/check_surge_waveform_comment.py
""")
    blocks, reason = cw.step_would_block_pull_request(wf, "guard", "check_surge_waveform_comment.py")
    assert blocks is False


def test_a_job_level_if_excluding_pull_request_blocks_even_an_unconditional_step():
    """The fourth condition: the step itself is plain and would ordinarily
    block, but the JOB never runs on a pull_request at all."""
    wf = _workflow("""\
  guard:
    runs-on: ubuntu-latest
    if: github.event_name == 'workflow_dispatch' || github.event_name == 'schedule'
    steps:
      - name: the gate
        run: python tools/check_surge_waveform_comment.py
""")
    blocks, reason = cw.step_would_block_pull_request(wf, "guard", "check_surge_waveform_comment.py")
    assert blocks is False
    assert "pull_request" in reason


def test_the_real_rtl_full_job_is_correctly_excluded_from_pull_request():
    """The synthetic control above mirrors a real case already in this repo:
    `rungs.yml`'s `rtl-full` is `workflow_dispatch`/`schedule`-only, so its
    steps must never be usable as a pull_request guard."""
    workflow = yaml.safe_load(RUNGS.read_text())
    blocks, reason = cw.step_would_block_pull_request(workflow, "rtl-full", "verify_voice.py --set full")
    assert blocks is False
    assert "pull_request" in reason


# =============================================================================
# The positive control: an ordinary step DOES block. Without this, every
# negative control above could pass because the helper always returns False.
# =============================================================================
def test_an_ordinary_unconditional_step_blocks():
    wf = _workflow("""\
  guard:
    runs-on: ubuntu-latest
    steps:
      - name: the gate
        run: python tools/check_surge_waveform_comment.py
""")
    blocks, reason = cw.step_would_block_pull_request(wf, "guard", "check_surge_waveform_comment.py")
    assert blocks is True
    assert "the gate" in reason


def test_a_workflow_path_is_accepted_as_well_as_a_parsed_dict():
    blocks, _ = cw.step_would_block_pull_request(RUNGS, "m5a-fast", "verify-fast")
    assert blocks is True


# =============================================================================
# The 8 mutations #304's review already confirmed turn the ORIGINAL bare
# substring check red -- re-verified here against the refactored helper, one
# per distinct failure shape (a combination of two of these is not a
# different code path).
# =============================================================================
def test_a_deleted_step_is_not_found():
    wf = _workflow("""\
  guard:
    runs-on: ubuntu-latest
    steps:
      - name: unrelated
        run: echo hi
""")
    blocks, reason = cw.step_would_block_pull_request(wf, "guard", "check_surge_waveform_comment.py")
    assert blocks is False
    assert "no step" in reason


def test_a_step_neutered_to_echo_skipping_is_not_found():
    wf = _workflow("""\
  guard:
    runs-on: ubuntu-latest
    steps:
      - name: the gate
        run: echo skipping
""")
    blocks, reason = cw.step_would_block_pull_request(wf, "guard", "check_surge_waveform_comment.py")
    assert blocks is False
    assert "no step" in reason


def test_a_typo_d_path_does_not_match():
    wf = _workflow("""\
  guard:
    runs-on: ubuntu-latest
    steps:
      - name: the gate
        run: python tools/check_surge_wavefrom_comment.py
""")
    blocks, _ = cw.step_would_block_pull_request(wf, "guard", "check_surge_waveform_comment.py")
    assert blocks is False


def test_controls_pointed_at_a_different_file_do_not_match():
    wf = _workflow("""\
  guard:
    runs-on: ubuntu-latest
    steps:
      - name: the controls
        run: python -m pytest tools/test_check_doc_claims.py -q
""")
    blocks, _ = cw.step_would_block_pull_request(wf, "guard", "pytest tools/test_check_surge_waveform_comment.py")
    assert blocks is False


def test_a_step_relocated_to_a_different_job_is_not_found_in_the_original_job():
    """The guard must be job-scoped, not repository-scoped: a step present
    ANYWHERE in the workflow but not in the named job must still miss."""
    wf = _workflow("""\
  other-job:
    runs-on: ubuntu-latest
    steps:
      - name: relocated gate
        run: python tools/check_surge_waveform_comment.py
  guard:
    runs-on: ubuntu-latest
    steps:
      - name: unrelated
        run: echo hi
""")
    blocks, reason = cw.step_would_block_pull_request(wf, "guard", "check_surge_waveform_comment.py")
    assert blocks is False
    assert "guard" in reason


# =============================================================================
# Failure modes of the helper itself: a lookup that finds nothing must say
# what it did not find, not just report False.
# =============================================================================
def test_a_missing_job_name_is_reported_by_name():
    wf = _workflow("""\
  guard:
    runs-on: ubuntu-latest
    steps:
      - run: echo hi
""")
    blocks, reason = cw.step_would_block_pull_request(wf, "no-such-job", "anything")
    assert blocks is False
    assert "no-such-job" in reason


# =============================================================================
# Referenced-path check (issue #629, the #622 class). Constructed trees only:
# each control builds a throwaway repo so it cannot pass or fail on the live one.
# =============================================================================
import subprocess                                                   # noqa: E402

import pytest                                                       # noqa: E402

TRACKED = ["docs/verification-rules.md", "tools/check_workflows.py",
           "tools/run_all.py", "model/a.py", ".github/workflows/x.yml"]


def _scan(run_text: str, tracked=TRACKED, tmp=None, name="wf.yml"):
    lines = [(i, t) for i, t in enumerate(run_text.splitlines(), 1)]
    root = tmp or pathlib.Path("/nonexistent-root-for-controls")
    return cw.check_referenced_paths([(name, lines)], tracked, repo=root)


def test_start_red_the_exact_pre_622_path_fails_with_file_and_line():
    """The historical defect, verbatim from nightly.yml before #622."""
    s = _scan("echo go\n          R=docs/reference-comparison-results.md\n")
    assert s.checked == 1
    assert s.missing == ["wf.yml:2: docs/reference-comparison-results.md"]


def test_tracked_file_and_directory_pass_and_are_counted():
    s = _scan("python tools/run_all.py docs/ model\npytest model/a.py\n")
    assert s.missing == []
    assert s.checked == 3      # file, dir, file ("model" has no slash: not a candidate)


def test_working_tree_file_that_is_not_tracked_passes(tmp_path):
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "new.md").write_text("x")
    assert _scan("cat docs/new.md", tmp=tmp_path).missing == []


@pytest.mark.parametrize("text,field", [
    ("cat docs/$NAME.md", "skipped_dynamic"),
    ("cat docs/${{ matrix.x }}.md", "skipped_dynamic"),
    ("cat ${{ github.workspace }}/docs/x.md", "skipped_dynamic"),
    ("cat docs/*.md", "skipped_glob"),
    ("cat docs/a?.md", "skipped_glob"),
    ("cat docs/[ab].md", "skipped_glob"),
])
def test_defeating_inputs_are_skipped_with_a_reason_never_checked(text, field):
    s = _scan(text)
    assert getattr(s, field) == 1
    assert s.checked == 0 and s.missing == []


def test_generated_roots_are_skipped_not_checked():
    s = _scan("cat build/out.json .tools/bin/x")
    assert s.skipped_generated == 2 and s.checked == 0 and s.missing == []


def test_allowlist_is_narrow_a_listed_output_skips_but_an_unlisted_typo_fails(monkeypatch):
    monkeypatch.setitem(cw.GENERATED_ALLOW, "docs/dag-results.json", "tools/compile_dag.py")
    s = _scan("cat docs/dag-results.json docs/dag-resluts.json")
    assert s.skipped_generated == 1
    assert [m.split(": ")[1] for m in s.missing] == ["docs/dag-resluts.json"]


def test_no_tracked_source_tree_is_exempted_wholesale():
    for root in list(cw.GENERATED_ROOTS) + list(cw.GENERATED_ALLOW):
        first = root.split("/", 1)[0]
        assert first in ("build", ".tools") or "/" in root.rstrip("/"), (
            f"{root!r} exempts a whole top-level tree")


def test_comment_lines_and_urls_and_absolute_paths_are_out_of_scope():
    s = _scan("# docs/nope.md\ncurl https://x.org/docs/nope.md /usr/docs/nope.md\n")
    assert s.checked == 0 and s.missing == []


def test_trailing_punctuation_and_line_suffix_do_not_make_a_false_positive():
    s = _scan("echo see tools/check_workflows.py. and (tools/run_all.py:12)")
    assert s.missing == [] and s.checked == 2


def test_empty_population_is_refused_not_passed():
    s = _scan("echo hello\nls\n")
    assert s.refused and "no literal repo paths" in s.refused


def test_git_unavailable_is_an_error_the_caller_refuses_on(tmp_path):
    with pytest.raises(RuntimeError):
        cw.tracked_files(tmp_path)              # not a git repo


def _repo(tmp_path, workflow: str, makefile: str = ""):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    for rel in ("docs/a.md", "tools/t.py"):
        (tmp_path / rel).parent.mkdir(exist_ok=True)
        (tmp_path / rel).write_text("x")
    (tmp_path / ".github" / "workflows").mkdir(parents=True)
    (tmp_path / ".github" / "workflows" / "w.yml").write_text(workflow)
    if makefile:
        (tmp_path / "Makefile").write_text(makefile)
    subprocess.run(["git", "-C", str(tmp_path), "add", "-A"], check=True)
    return tmp_path


WF = """\
on: [push]
jobs:
  j:
    runs-on: ubuntu-latest
    steps:
      - run: |
          echo hi
          cat docs/a.md
          cat docs/gone.md
"""


def test_gather_reports_the_exact_yaml_line_of_a_literal_block(tmp_path):
    root = _repo(tmp_path, WF, "all:\n\tpython tools/t.py\n\tcat tools/missing.py\n")
    s = cw.check_referenced_paths(cw.gather_sources(root), cw.tracked_files(root), repo=root)
    assert s.missing == [".github/workflows/w.yml:9: docs/gone.md",
                         "Makefile:3: tools/missing.py"]
    assert s.checked == 4


def test_makefile_non_recipe_lines_are_not_scanned(tmp_path):
    root = _repo(tmp_path, WF, "X = docs/gone2.md\nall:\n\tpython tools/t.py\n")
    s = cw.check_referenced_paths(cw.gather_sources(root), cw.tracked_files(root), repo=root)
    assert all("gone2" not in m for m in s.missing)


def test_live_tree_is_green_and_nonempty():
    """Current-state gate: the repo's own workflows/Makefile must pass."""
    s = cw.check_referenced_paths(cw.gather_sources(), cw.tracked_files())
    assert s.missing == [] and s.refused is None and s.checked > 0


def test_main_exits_2_when_git_ls_files_fails(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("no git")
    monkeypatch.setattr(cw, "tracked_files", boom)
    assert cw.main() == 2


def test_main_exits_1_on_a_dangling_path(monkeypatch):
    monkeypatch.setattr(cw, "gather_sources",
                        lambda: [("w.yml", [(1, "cat docs/nope-nope.md")])])
    monkeypatch.setattr(cw, "tracked_files", lambda: TRACKED)
    assert cw.main() == 1
