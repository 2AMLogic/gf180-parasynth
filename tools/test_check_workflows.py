"""Controls for `check_workflows.step_would_block_pull_request` (issue #307),
and for the shipped #906 roadmap step order (issue #940).

`tools/test_manifest.py` and `tools/test_check_surge_waveform_comment.py` each
guard their own CI wiring by asserting a substring appears in a named job's
`run:` strings -- which only proves the step EXISTS. Measured during #304's
review: a step with `if: false` or `continue-on-error: true` still matches
that substring check and leaves the guard green, even though neither can ever
block a pull request. These are unit-level controls against constructed YAML,
not full-source mutation, exercising the helper both tests now call.

The #940 section below is the other shape on purpose: the injected defect
there is a mutation of the REAL rungs.yml / nightly.yml parsed shape (the
reverted #906 order), because what ships is the ordering of lines and steps
in those files -- a lookalike fixture could pass while the real files drifted.
"""
from __future__ import annotations

import pathlib
import sys

import yaml

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import check_workflows as cw                                        # noqa: E402
import push_dag_records as pdr                                      # noqa: E402

REPO = pathlib.Path(__file__).resolve().parents[1]
RUNGS = REPO / ".github" / "workflows" / "rungs.yml"
NIGHTLY = REPO / ".github" / "workflows" / "nightly.yml"


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


# =============================================================================
# #1178, the #1168 shape one level down: a step `if:` that keeps the step off
# every pull_request run is not "blocking". The step arm must treat any `if:`
# it cannot prove always-true as "cannot say" -- the same safe direction
# `constant_true` gave `continue-on-error` in #1158.
# =============================================================================
def test_a_step_if_gated_to_push_events_does_not_block():
    """The defeating input (verification-rules rule 8): `github.event_name ==
    'push'` is true on no pull_request run, so the gate never runs there --
    previously reported as blocking because the step arm recognised only
    constant `false`."""
    wf = _workflow("""\
  guard:
    runs-on: ubuntu-latest
    steps:
      - name: the gate
        if: ${{ github.event_name == 'push' }}
        run: python tools/check_surge_waveform_comment.py
""")
    blocks, reason = cw.step_would_block_pull_request(wf, "guard", "check_surge_waveform_comment.py")
    assert blocks is False
    assert "github.event_name" in reason
    assert "pull_request" in reason


def test_a_step_if_gated_on_github_ref_does_not_block():
    wf = _workflow("""\
  guard:
    runs-on: ubuntu-latest
    steps:
      - name: the gate
        if: ${{ github.ref == 'refs/heads/nope' }}
        run: python tools/check_surge_waveform_comment.py
""")
    blocks, reason = cw.step_would_block_pull_request(wf, "guard", "check_surge_waveform_comment.py")
    assert blocks is False
    assert "github.ref" in reason


def test_a_step_if_gated_on_base_ref_does_not_block():
    wf = _workflow("""\
  guard:
    runs-on: ubuntu-latest
    steps:
      - name: the gate
        if: ${{ github.base_ref == 'nope' }}
        run: python tools/check_surge_waveform_comment.py
""")
    blocks, reason = cw.step_would_block_pull_request(wf, "guard", "check_surge_waveform_comment.py")
    assert blocks is False
    assert "github.base_ref" in reason


def test_an_empty_step_if_does_not_block():
    """`if: ''` evaluates falsy on the runner, so the step never runs -- not a
    spelling of "unconditional"."""
    wf = yaml.safe_load("""\
jobs:
  guard:
    runs-on: ubuntu-latest
    steps:
      - name: the gate
        if: ''
        run: python tools/check_surge_waveform_comment.py
""")
    blocks, _ = cw.step_would_block_pull_request(wf, "guard", "check_surge_waveform_comment.py")
    assert blocks is False


def test_two_matching_steps_where_the_second_is_unconditional_block():
    """One gated twin must not launder the pair: the unconditional one still
    fails a pull_request run, so the predicate answers for the job."""
    wf = _workflow("""\
  guard:
    runs-on: ubuntu-latest
    steps:
      - name: the gated twin
        if: ${{ github.event_name == 'push' }}
        run: python tools/check_surge_waveform_comment.py
      - name: the gate
        run: python tools/check_surge_waveform_comment.py
""")
    blocks, reason = cw.step_would_block_pull_request(wf, "guard", "check_surge_waveform_comment.py")
    assert blocks is True
    assert "the gate" in reason


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
# #1178 positive controls: the explicit always-true `if:` spellings still
# block. Without these, the conservative direction above could pass because
# the step arm refuses everything.
# =============================================================================
def test_a_step_if_always_still_blocks():
    wf = _workflow("""\
  guard:
    runs-on: ubuntu-latest
    steps:
      - name: the gate
        if: always()
        run: python tools/check_surge_waveform_comment.py
""")
    blocks, _ = cw.step_would_block_pull_request(wf, "guard", "check_surge_waveform_comment.py")
    assert blocks is True


def test_a_step_if_success_still_blocks():
    wf = _workflow("""\
  guard:
    runs-on: ubuntu-latest
    steps:
      - name: the gate
        if: success()
        run: python tools/check_surge_waveform_comment.py
""")
    blocks, _ = cw.step_would_block_pull_request(wf, "guard", "check_surge_waveform_comment.py")
    assert blocks is True


def test_a_step_if_not_cancelled_still_blocks():
    wf = _workflow("""\
  guard:
    runs-on: ubuntu-latest
    steps:
      - name: the gate
        if: ${{ !cancelled() }}
        run: python tools/check_surge_waveform_comment.py
""")
    blocks, _ = cw.step_would_block_pull_request(wf, "guard", "check_surge_waveform_comment.py")
    assert blocks is True


def test_a_step_if_true_in_its_spellings_still_blocks():
    """Bare YAML `true`, the string `'true'`, and `${{ true }}` (which loads
    as a string, #1158) are all spellings of "unconditional"."""
    for spelling in ("true", "${{ true }}"):
        job = {"runs-on": "ubuntu-latest", "steps": [
            {"name": "the gate", "if": spelling,
             "run": "python tools/check_surge_waveform_comment.py"}]}
        wf = {"jobs": {"guard": job}}
        blocks, _ = cw.step_would_block_pull_request(wf, "guard", "check_surge_waveform_comment.py")
        assert blocks is True, spelling
    wf = yaml.safe_load("""\
jobs:
  guard:
    runs-on: ubuntu-latest
    steps:
      - name: the gate
        if: 'true'
        run: python tools/check_surge_waveform_comment.py
""")
    blocks, _ = cw.step_would_block_pull_request(wf, "guard", "check_surge_waveform_comment.py")
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


# =============================================================================
# #787: the nightly now pushes its own records commit to main. dag.yml skips
# any push whose head commit message contains `[dag]` -- that guard is what
# stops the nightly's push from re-triggering the per-push job into a commit
# loop. The coupling is only as strong as the two strings staying in step
# (rule 8: the input that defeats the guard is a reworded subject), so pin
# both sides of the contract against the real files.
# =============================================================================
def test_every_commit_the_nightly_pushes_carries_the_dag_guard_string():
    nightly = yaml.safe_load(
        (REPO / ".github" / "workflows" / "nightly.yml").read_text())
    runs = [str(s.get("run", ""))
            for j in (nightly.get("jobs") or {}).values()
            for s in (j.get("steps") or []) if isinstance(s, dict)]
    assert any("tools/push_dag_records.py" in r for r in runs), \
        "nightly.yml no longer drives the records-commit tool"

    # The commit moved out of the YAML into the tool during #818's retry fix,
    # so the guard pins the tool's COMMIT_SUBJECT constant rather than scraped
    # YAML: a reworded subject would defeat dag.yml's guard silently.
    assert "[dag]" in pdr.COMMIT_SUBJECT, \
        "the nightly commit's [dag] guard string was removed or reworded"

    dag = yaml.safe_load((REPO / ".github" / "workflows" / "dag.yml").read_text())
    guard = str((dag.get("jobs") or {}).get("regenerate", {}).get("if", ""))
    assert "[dag]" in guard, "dag.yml's own-commit guard has been removed or reworded"
# =============================================================================
# The shipped #906 order itself (#940): check-before-run inside rungs.yml's
# "the roadmap matches the evidence" step, and check-before-record across
# nightly.yml's pair. tools/test_rungs_gate.py pins the semantics of
# compile_dag.main(); these pin the YAML that ships. Injected defect (rule 8):
# the reverted order, applied to the REAL files' parsed shape.
# =============================================================================
def _step(workflow: dict, name: str) -> dict:
    for job in workflow["jobs"].values():
        for s in job.get("steps") or []:
            if isinstance(s, dict) and s.get("name") == name:
                return s
    raise AssertionError(f"no step named {name!r}")


def test_real_rungs_roadmap_step_checks_before_run():
    ok, reason = cw.roadmap_check_precedes_run_in_step(
        yaml.safe_load(RUNGS.read_text()))
    assert ok, reason


def test_real_nightly_check_step_precedes_record_step():
    ok, reason = cw.roadmap_check_step_precedes_record_step(
        yaml.safe_load(NIGHTLY.read_text()))
    assert ok, reason


def test_reverting_rungs_to_the_pre_906_order_is_red():
    """The injected defect: #906's exact regression, applied to the real
    step's run block -- run-then-check, the gate reading its own output."""
    wf = yaml.safe_load(RUNGS.read_text())
    step = _step(wf, cw.ROADMAP_STEP)
    lines = step["run"].split("\n")
    i_check = next(i for i, l in enumerate(lines) if "--check" in l)
    i_run = next(i for i, l in enumerate(lines) if "--run" in l)
    assert i_check < i_run, "the real file has already regressed"
    lines[i_check], lines[i_run] = lines[i_run], lines[i_check]
    step["run"] = "\n".join(lines)
    ok, reason = cw.roadmap_check_precedes_run_in_step(wf)
    assert not ok
    assert "--run" in reason and "--check" in reason and "#906" in reason


def test_reverting_nightly_to_record_before_check_is_red():
    """The injected defect: the pair swapped, on the real job's steps list."""
    wf = yaml.safe_load(NIGHTLY.read_text())
    for job in wf["jobs"].values():
        steps = job.get("steps") or []
        names = [s.get("name") for s in steps if isinstance(s, dict)]
        if (cw.ROADMAP_CHECK_STEP in names
                and cw.ROADMAP_RECORD_STEP in names):
            i_c, i_r = names.index(cw.ROADMAP_CHECK_STEP), names.index(cw.ROADMAP_RECORD_STEP)
            assert i_c < i_r, "the real file has already regressed"
            steps[i_c], steps[i_r] = steps[i_r], steps[i_c]
            break
    else:
        raise AssertionError("the pair is not in one nightly job")
    ok, reason = cw.roadmap_check_step_precedes_record_step(wf)
    assert not ok
    assert "precedes" in reason and "#906" in reason


def test_a_check_step_that_also_rewrites_is_red():
    """--check and --run on one invocation judges nothing: the step would
    rewrite the README it is supposedly judging."""
    wf = yaml.safe_load(NIGHTLY.read_text())
    _step(wf, cw.ROADMAP_CHECK_STEP)["run"] = \
        "python tools/compile_dag.py --check --run"
    ok, reason = cw.roadmap_check_step_precedes_record_step(wf)
    assert not ok
    assert "--run" in reason


def test_a_renamed_roadmap_step_is_red_not_silently_exempt():
    """A rename must move the finding somewhere loud, never turn the gate
    off: the guard reports that it can no longer find what it guards."""
    wf = yaml.safe_load(RUNGS.read_text())
    _step(wf, cw.ROADMAP_STEP)["name"] = "roadmap (renamed)"
    ok, reason = cw.roadmap_check_precedes_run_in_step(wf)
    assert not ok
    assert "no step named" in reason and "nothing to guard" in reason


def test_a_run_only_roadmap_step_has_no_check_to_order():
    wf = _workflow("""\
  python:
    steps:
      - name: the roadmap matches the evidence
        run: |
          python tools/compile_dag.py --run || true  # a RED node is a result
    """)
    ok, reason = cw.roadmap_check_precedes_run_in_step(wf)
    assert not ok
    assert "--check" in reason


def test_an_inline_comment_cannot_smuggle_a_flag():
    """The flag scan strips shell comments first: a commented-out `--check`
    above a live `--run` is still run-only."""
    wf = _workflow("""\
  python:
    steps:
      - name: the roadmap matches the evidence
        run: |
          # python tools/compile_dag.py --check
          python tools/compile_dag.py --run || true
    """)
    ok, reason = cw.roadmap_check_precedes_run_in_step(wf)
    assert not ok
    assert "--check" in reason


# =============================================================================
# main() wiring: the gate as the `python` job actually runs it.
# =============================================================================
def _minimal_workflow_text() -> str:
    return """\
name: minimal
on: workflow_dispatch
jobs:
  a:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with: { fetch-depth: 0 }
      - run: echo hi
"""


def test_main_is_green_on_the_real_workflow_tree(tmp_path, monkeypatch, capsys):
    import shutil
    for f in ("rungs.yml", "nightly.yml"):
        shutil.copy(REPO / ".github" / "workflows" / f, tmp_path / f)
    monkeypatch.setattr(cw, "WF", tmp_path)
    assert cw.main() == 0
    out = capsys.readouterr().out
    assert "roadmap order:" in out


def test_main_is_red_when_the_shipped_order_is_reverted(tmp_path, monkeypatch):
    import shutil
    for f in ("rungs.yml", "nightly.yml"):
        shutil.copy(REPO / ".github" / "workflows" / f, tmp_path / f)
    text = (tmp_path / "rungs.yml").read_text()
    check_line = ("          python tools/compile_dag.py --check"
                  "        # the committed README vs the committed evidence\n")
    run_line = ("          python tools/compile_dag.py --run || true"
                "  # a RED node is a result\n")
    assert check_line in text and run_line in text
    (tmp_path / "rungs.yml").write_text(
        text.replace(check_line + run_line, run_line + check_line))
    monkeypatch.setattr(cw, "WF", tmp_path)
    assert cw.main() == 1


def test_main_refuses_to_pass_when_no_workflow_carries_the_steps(
        tmp_path, monkeypatch, capsys):
    """Both gates renamed away = the guard has been evaded, and main() says
    so in the exit code rather than reporting a green tree."""
    (tmp_path / "minimal.yml").write_text(_minimal_workflow_text())
    monkeypatch.setattr(cw, "WF", tmp_path)
    assert cw.main() == 1
    err = capsys.readouterr().err
    assert err.count("nothing to guard") == 2



def test_a_job_level_continue_on_error_does_not_block():
    wf = _workflow("""\
  guard:
    runs-on: ubuntu-latest
    continue-on-error: true
    steps:
      - name: the gate
        run: python tools/check_surge_waveform_comment.py
""")
    blocks, reason = cw.step_would_block_pull_request(wf, "guard", "check_surge_waveform_comment.py")
    assert blocks is False
    assert "continue-on-error" in reason


@pytest.mark.parametrize("level", ["job", "step"])
def test_an_expression_form_continue_on_error_does_not_block(level):
    """#1158: `${{ true }}` loads as a string; `is True` missed it."""
    job_line = "    continue-on-error: ${{ true }}\n" if level == "job" else ""
    step_line = "        continue-on-error: ${{ true }}\n" if level == "step" else ""
    wf = _workflow("""\
  guard:
    runs-on: ubuntu-latest
""" + job_line + """\
    steps:
      - name: the gate
        run: python tools/check_surge_waveform_comment.py
""" + step_line)
    blocks, reason = cw.step_would_block_pull_request(wf, "guard", "check_surge_waveform_comment.py")
    assert blocks is False
    assert "continue-on-error" in reason


@pytest.mark.parametrize("value", ["false", "${{ false }}", None, False])
def test_constant_true_rejects_provably_false_forms(value):
    assert cw.constant_true(value) is False


@pytest.mark.parametrize("value", [
    True, "true", "${{ true }}", "yes",
    "${{ github.event_name != 'push' }}",  # true on a pull_request run
], ids=repr)
def test_constant_true_treats_anything_not_provably_false_as_neutralising(value):
    """#1158 review: the safe direction. The conditional expression is the
    input that defeats a narrow predicate (rule 8)."""
    assert cw.constant_true(value) is True


# =============================================================================
# #1178: `_constant_true_if` is deliberately NOT `not _constant_false` -- it
# is the small explicit always-true set, and everything else (including
# conditionals that happen to be true on a push) is refused.
# =============================================================================
@pytest.mark.parametrize("value", [
    None, True, "true", "${{ true }}", "True",
    "always()", "${{ always() }}", "ALWAYS()",
    "success()", "${{ success() }}",
    "!cancelled()", "${{ !cancelled() }}",
], ids=repr)
def test_constant_true_if_accepts_exactly_the_explicit_always_true_set(value):
    assert cw._constant_true_if(value) is True


@pytest.mark.parametrize("value", [
    False, "false", "${{ false }}", "",
    "${{ github.event_name == 'push' }}",
    "${{ github.ref == 'refs/heads/nope' }}",
    "${{ github.base_ref == 'nope' }}",
    0, [], "yes",
], ids=repr)
def test_constant_true_if_refuses_everything_else(value):
    """`yes` is YAML-1.1 truthiness, not a GitHub `if:` constant; non-string
    non-bool values cannot be shown always-true either."""
    assert cw._constant_true_if(value) is False


# =============================================================================
# #1235: workflow injected-defect controls must be gated, not only Makefile ones
# =============================================================================
import argparse
import re
import shlex
import shutil
from dataclasses import dataclass, field

import run_controls
from test_run_controls import (
    FLAGS,
    VERIFIERS,
    _mutant_matches_baseline,
)

# `test_run_controls.py` exempts `verify_voice_scoped.py` and `verify_i2s_tx.py`
# by name in `make controls`; scanning them here ensures workflow uses of either
# script are classified and accounted for rather than silently skipped.
WORKFLOW_VERIFIERS = VERIFIERS + ("verify_voice_scoped.py", "verify_i2s_tx.py")


@dataclass(frozen=True)
class StepGatedEntry:
    reason: str
    clean_baseline: str | None = None
    superset: bool = False
    open_issue: str | None = None


# Explicit allowlist of bare step-gated RTL mutant rows across `.github/workflows/*.yml`.
# Key: (workflow_filename, job_name, step_name, normalized_command).
# Every entry must carry a non-empty `reason` AND either `clean_baseline` (verified to
# appear earlier in the same job as an active clean run) or `open_issue` (tracked gap).
STEP_GATED_ALLOWLIST: dict[tuple[str, str, str, str], StepGatedEntry] = {
    (
        "rungs.yml",
        "rtl",
        "the integration checks can still fail",
        "python rtl-sketch/verify_ctl.py --inject SPI_ADDR7 --expect-fail",
    ): StepGatedEntry(
        reason="gated by earlier step 'I2 -- the control link carries every register write' via Actions fail-fast",
        clean_baseline="python rtl-sketch/verify_ctl.py",
    ),
    (
        "rungs.yml",
        "rtl",
        "the integration checks can still fail",
        "python rtl-sketch/verify_ctl.py --inject SPI_DATA24 --expect-fail",
    ): StepGatedEntry(
        reason="gated by earlier step 'I2 -- the control link carries every register write' via Actions fail-fast",
        clean_baseline="python rtl-sketch/verify_ctl.py",
    ),
    (
        "rungs.yml",
        "rtl",
        "the integration checks can still fail",
        "python rtl-sketch/verify_synth_top.py --inject VOICE_MASTER_PRESHIFT --expect-fail",
    ): StepGatedEntry(
        reason="default-config verify_synth_top mutant has no default-config clean step in job rtl yet (#1221)",
        open_issue="#1221",
    ),
    (
        "rungs.yml",
        "rtl",
        "the integration checks can still fail",
        "python rtl-sketch/verify_synth_top.py --inject I2S_SHIFT --expect-fail",
    ): StepGatedEntry(
        reason="default-config verify_synth_top mutant has no default-config clean step in job rtl yet (#1221)",
        open_issue="#1221",
    ),
    (
        "rungs.yml",
        "rtl",
        "the selected 2x path and integration reject disabling mutations",
        "python rtl-sketch/verify_voice.py --set quick --only default --osc2x --inject OSC2X_OFF --expect-fail --outdir build/voice-osc2x-control",
    ): StepGatedEntry(
        reason="gated by earlier superset step 'M1 -- selected 2x Moog voice bit-exact (quick set)' (--set quick --osc2x includes default)",
        clean_baseline="python rtl-sketch/verify_voice.py --set quick --osc2x --outdir build/voice-osc2x",
        superset=True,
    ),
    (
        "rungs.yml",
        "rtl",
        "the selected 2x path and integration reject disabling mutations",
        "python rtl-sketch/verify_synth_top.py --osc2x --inject VOICE_OSC2X_OFF --expect-fail --outdir build/top-osc2x-control",
    ): StepGatedEntry(
        reason="gated by earlier exact-config step 'I4 -- selected 2x path at the pins, SPI in to I2S out'",
        clean_baseline="python rtl-sketch/verify_synth_top.py --osc2x",
    ),
    (
        "rungs.yml",
        "rtl",
        "the selected 2x path and integration reject disabling mutations",
        "python rtl-sketch/verify_synth_top.py --m5a-smoke --filter2x --inject VOICE_FILTER2X_OFF --expect-fail --outdir build/top-filter2x-control",
    ): StepGatedEntry(
        reason="gated by earlier exact-config step 'I4b -- reconstructed filter candidate through SPI to I2S'",
        clean_baseline="python rtl-sketch/verify_synth_top.py --m5a-smoke --filter2x --outdir build/top-m5a-filter2x",
    ),
    (
        "rungs.yml",
        "rtl-full",
        "M5A -- Verilator integrated disabling control",
        "python rtl-sketch/verify_synth_top.py --simulator verilator --m5a-smoke --filter2x --inject VOICE_FILTER2X_OFF --expect-fail --outdir build/top-filter2x-verilator-control",
    ): StepGatedEntry(
        reason="Verilator --m5a-smoke --filter2x mutant has no clean Verilator smoke step in rtl-full on main yet (#1212)",
        open_issue="#1212",
    ),
    (
        "nightly.yml",
        "controls",
        "the control link -- the exact defect that shipped",
        "python rtl-sketch/verify_ctl.py --inject SPI_ADDR7 --expect-fail",
    ): StepGatedEntry(
        reason="nightly controls step had no clean verify_ctl.py run on main yet (#1222)",
        open_issue="#1222",
    ),
    (
        "nightly.yml",
        "controls",
        "the control link -- the exact defect that shipped",
        "python rtl-sketch/verify_ctl.py --inject SPI_DATA24 --expect-fail",
    ): StepGatedEntry(
        reason="nightly controls step had no clean verify_ctl.py run on main yet (#1222)",
        open_issue="#1222",
    ),
    (
        "nightly.yml",
        "controls",
        "the chip at its pins",
        "python rtl-sketch/verify_synth_top.py --inject VOICE_MASTER_PRESHIFT --expect-fail",
    ): StepGatedEntry(
        reason="nightly controls step had no clean verify_synth_top.py run on main yet (#1222)",
        open_issue="#1222",
    ),
    (
        "nightly.yml",
        "controls",
        "the chip at its pins",
        "python rtl-sketch/verify_synth_top.py --inject VOICE_DRUM_CLAMP16 --expect-fail",
    ): StepGatedEntry(
        reason="nightly controls step had no clean verify_synth_top.py run on main yet (#1222)",
        open_issue="#1222",
    ),
    (
        "nightly.yml",
        "controls",
        "the chip at its pins",
        "python rtl-sketch/verify_synth_top.py --inject I2S_SHIFT --expect-fail",
    ): StepGatedEntry(
        reason="nightly controls step had no clean verify_synth_top.py run on main yet (#1222)",
        open_issue="#1222",
    ),
    (
        "nightly.yml",
        "controls",
        "the chip at its pins",
        "python rtl-sketch/verify_synth_top.py --inject I2S_DELAY --expect-fail",
    ): StepGatedEntry(
        reason="nightly controls step had no clean verify_synth_top.py run on main yet (#1222)",
        open_issue="#1222",
    ),
    (
        "nightly.yml",
        "controls",
        "the chip at its pins",
        "python rtl-sketch/verify_synth_top.py --inject MODAL_NUM_HOLD --expect-fail",
    ): StepGatedEntry(
        reason="nightly controls step had no clean verify_synth_top.py run on main yet (#1222)",
        open_issue="#1222",
    ),
    (
        "nightly.yml",
        "controls",
        "the chip at its pins",
        "python rtl-sketch/verify_synth_top.py --inject DRUM_ENV_FLOOR --expect-fail",
    ): StepGatedEntry(
        reason="nightly controls step had no clean verify_synth_top.py run on main yet (#1222)",
        open_issue="#1222",
    ),
    (
        "nightly.yml",
        "controls",
        "the right channel -- i2s_tx on its own bench (#619)",
        "python rtl-sketch/verify_i2s_tx.py --inject I2S_SWAP --expect-fail",
    ): StepGatedEntry(
        reason="standalone i2s_tx bench exemption (#619; test_run_controls.py exempt), preceded by clean verify_i2s_tx.py in the same run: | block",
        clean_baseline="python rtl-sketch/verify_i2s_tx.py",
    ),
}

# Pre-#1223 transition entry: on `origin/main` @ `a8fd2139`, `rungs.yml` `pulse2x-rtl`
# still invokes `tools/run_all.py` (#1217; PR #1223 converts it to `tools/run_controls.py`
# with `@baseline=pulse-smoke` / `@after=pulse-smoke`). Once #1223 lands on main, the row
# classifies directly as `batch_gated` and this entry can be retired so reverting
# `run_controls.py` -> `run_all.py` on that one line is also refused.
PENDING_RUN_ALL_ALLOWLIST: dict[tuple[str, str, str, str], str] = {
    (
        "rungs.yml",
        "pulse2x-rtl",
        "pulse candidate, transport, deadlines and disabling control",
        "python rtl-sketch/verify_synth_top.py --m5a-smoke --filter2x --pulse2x --simulator verilator --inject VOICE_PULSE2X_OFF --expect-fail --outdir build/pulse-off",
    ): "open #1217 / PR #1223 converts this run_all.py batch row to run_controls.py @baseline=pulse-smoke / @after=pulse-smoke",
}


@dataclass
class MutantScan:
    batch_gated: list[str] = field(default_factory=list)
    step_gated: list[str] = field(default_factory=list)
    pending_batch: list[str] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)


def _strip_shell_comment(line: str) -> str:
    """Strip unquoted `# ...` shell comments while keeping `#` inside quotes."""
    quote: str | None = None
    escaped = False
    for i, ch in enumerate(line):
        if escaped:
            escaped = False
            continue
        if ch == "\\" and quote != "'":
            escaped = True
            continue
        if quote is not None:
            if ch == quote:
                quote = None
        elif ch in ("'", '"'):
            quote = ch
        elif ch == "#" and (i == 0 or line[i - 1].isspace()):
            return line[:i].rstrip()
    return line.rstrip()


def _logical_shell_lines(run_node: yaml.ScalarNode) -> list[tuple[int, str]]:
    """Yield (1-based YAML line, logical shell line) with comments stripped and
    trailing backslash continuations joined."""
    base = run_node.start_mark.line + 1
    literal = run_node.style == "|"
    out: list[tuple[int, str]] = []
    buf = ""
    buf_line = base
    for idx, raw in enumerate(run_node.value.split("\n")):
        line_no = base + 1 + idx if literal else base
        stripped = _strip_shell_comment(raw)
        if not stripped.strip():
            if buf:
                out.append((buf_line, buf))
                buf = ""
            continue
        if not buf:
            buf_line = line_no
        if stripped.endswith("\\") and not stripped.endswith("\\\\"):
            buf += stripped[:-1] + " "
        else:
            buf += stripped
            out.append((buf_line, buf.strip()))
            buf = ""
    if buf.strip():
        out.append((buf_line, buf.strip()))
    return out


def _split_shell_statements(line: str) -> list[tuple[list[str], str | None]]:
    """Split a logical shell line on unquoted `;`, `&`, `&&`, `|`, `||` while
    preserving `2>&1`-style fd redirects and quoted arguments."""
    lex = shlex.shlex(line, posix=True, punctuation_chars="<>;&|")
    lex.whitespace_split = True
    toks = list(lex)
    stmts: list[tuple[list[str], str | None]] = []
    cur: list[str] = []
    for tok in toks:
        if tok in (";", "&", "&&", "|", "||"):
            if cur:
                stmts.append((cur, tok))
                cur = []
        else:
            cur.append(tok)
    if cur:
        stmts.append((cur, None))
    return stmts


def _line_for_subcommand(
    raw_lines: list[str], start_line: int, end_line: int, needle: str, fallback: int
) -> int:
    """Locate the exact 1-based YAML line of a sub-command inside a folded or
    continued `run:` block; fall back to the block's logical start line."""
    lo = max(1, start_line)
    hi = min(len(raw_lines), max(start_line, end_line))
    for ln in range(lo, hi + 1):
        raw = raw_lines[ln - 1]
        if not raw.lstrip().startswith("#") and needle in " ".join(raw.split()):
            return ln
    return fallback


def _matches_verifier_and_flag(toks: list[str]) -> bool:
    has_verifier = any(
        t == v or t.endswith("/" + v) for t in toks for v in WORKFLOW_VERIFIERS
    )
    has_flag = any(
        t == f or t.startswith(f + "=") for t in toks for f in FLAGS
    )
    return has_verifier and has_flag


def _is_clean_verifier(toks: list[str]) -> bool:
    has_verifier = any(
        t == v or t.endswith("/" + v) for t in toks for v in WORKFLOW_VERIFIERS
    )
    if not has_verifier:
        return False
    all_mutant_flags = set(FLAGS) | set(run_controls.MUTANT_FLAGS)
    has_mutant_flag = any(
        t == f or t.startswith(f + "=") for t in toks for f in all_mutant_flags
    )
    return not has_mutant_flag and not any("INJECT_BUG" in t for t in toks)


def _step_is_success_gated(value: object) -> bool:
    """Step-gated mutants rely on Actions fail-fast (`success()`), so `always()`
    and `!cancelled()` are defeaters: they run the `--expect-fail` mutant even
    after its earlier clean step failed."""
    if value is None:
        return True
    if isinstance(value, bool):
        return value is True
    if isinstance(value, str):
        s = value.strip()
        if s.startswith("${{") and s.endswith("}}"):
            s = s[3:-2].strip()
        return s.lower() in {"true", "success()"}
    return False


def _step_or_job_neutralised(
    job_dict: dict,
    jname: str,
    step_dict: dict,
    sname: str,
    *,
    require_success_gate: bool,
) -> str | None:
    if cw._constant_false(job_dict.get("if")):
        return f"job {jname!r} has constant-false if: {job_dict.get('if')!r}"
    if cw.constant_true(job_dict.get("continue-on-error")):
        return f"job {jname!r} sets continue-on-error: {job_dict.get('continue-on-error')!r}"
    if cw.constant_true(step_dict.get("continue-on-error")):
        return f"step {sname!r} sets continue-on-error: {step_dict.get('continue-on-error')!r}"
    if step_dict.get("shell") is not None:
        return f"step {sname!r} overrides shell ({step_dict.get('shell')!r})"
    if require_success_gate:
        if not _step_is_success_gated(step_dict.get("if")):
            return (
                f"step {sname!r} has if: {step_dict.get('if')!r}, which does not "
                f"fail-fast on earlier clean steps"
            )
    elif not cw._constant_true_if(step_dict.get("if")):
        return f"step {sname!r} has non-unconditional if: {step_dict.get('if')!r}"
    return None


def _run_controls_arg_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(add_help=False, exit_on_error=False)
    ap.add_argument("cmds", nargs="+")
    ap.add_argument("--serial", action="store_true")
    ap.add_argument("--jobs", type=int, default=None)
    ap.add_argument("--timeout", type=float, default=None)
    ap.add_argument("--tail", type=int, default=4)
    ap.add_argument("--json")
    return ap


def _mapping_get(node: yaml.Node, key: str) -> yaml.Node | None:
    if isinstance(node, yaml.MappingNode):
        for k, v in node.value:
            if isinstance(k, yaml.ScalarNode) and k.value == key:
                return v
    return None


def classify_workflow_mutants(
    wf_dir: pathlib.Path = REPO / ".github" / "workflows",
    *,
    allowlist: dict[tuple[str, str, str, str], StepGatedEntry] = STEP_GATED_ALLOWLIST,
    pending_run_all: dict[tuple[str, str, str, str], str] = PENDING_RUN_ALL_ALLOWLIST,
) -> MutantScan:
    """Walk every `.github/workflows/*.yml|*.yaml` `run:` row matching
    `WORKFLOW_VERIFIERS` x `FLAGS` and classify each into `batch_gated` or
    `step_gated` (plus the pre-#1223 `pending_batch` transition entry); record
    any unclassifiable, config-mismatched, neutralised, or stale-allowlist item
    in `problems` with its `.github/workflows/<file>:<line>:` prefix."""
    scan = MutantScan()
    matched_allowlist: set[tuple[str, str, str, str]] = set()
    matched_pending: set[tuple[str, str, str, str]] = set()
    graduated_batch: set[tuple[str, str, str, str]] = set()
    rc_parser = _run_controls_arg_parser()

    files = sorted(wf_dir.glob("*.yml")) + sorted(wf_dir.glob("*.yaml"))
    for path in files:
        rel = f".github/workflows/{path.name}"
        text = path.read_text()
        raw_lines = text.splitlines()
        doc = yaml.safe_load(text) or {}
        root = yaml.compose(text)
        jobs_node = _mapping_get(root, "jobs") if root is not None else None
        if not isinstance(jobs_node, yaml.MappingNode):
            continue
        jobs_dict = doc.get("jobs") or {}

        for jkey_node, job_node in jobs_node.value:
            if not isinstance(jkey_node, yaml.ScalarNode):
                continue
            jname = jkey_node.value
            job_dict = jobs_dict.get(jname) or {}
            steps_node = _mapping_get(job_node, "steps")
            steps_list = job_dict.get("steps") or []
            if not isinstance(steps_node, yaml.SequenceNode):
                continue

            seen_clean_in_job: set[str] = set()

            for step_idx, step_node in enumerate(steps_node.value):
                step_dict = steps_list[step_idx] if step_idx < len(steps_list) else {}
                if not isinstance(step_dict, dict):
                    continue
                sname = str(step_dict.get("name", f"<step {step_idx}>"))
                run_node = _mapping_get(step_node, "run")
                if not isinstance(run_node, yaml.ScalarNode):
                    continue
                run_start = run_node.start_mark.line + 1
                run_end = run_node.end_mark.line + 1
                errexit_disabled = False

                for line_no, logical in _logical_shell_lines(run_node):
                    try:
                        stmts = _split_shell_statements(logical)
                    except ValueError as exc:
                        if any(v in logical for v in WORKFLOW_VERIFIERS) or "run_controls" in logical:
                            scan.problems.append(
                                f"{rel}:{line_no}: unparseable shell quoting on verifier line: {exc}"
                            )
                        continue

                    for toks, trailing_op in stmts:
                        if toks[:2] == ["set", "+e"]:
                            errexit_disabled = True
                            continue
                        if toks[:2] == ["set", "-e"]:
                            errexit_disabled = False
                            continue

                        swallowed = (
                            errexit_disabled
                            or trailing_op in ("||", "&", "|")
                            or (bool(toks) and toks[0] == "!")
                        )
                        norm_cmd = " ".join(toks)

                        # 1. `tools/run_controls.py` batch
                        rc_idx = next(
                            (
                                i
                                for i, t in enumerate(toks)
                                if t == "run_controls.py" or t.endswith("/run_controls.py")
                            ),
                            None,
                        )
                        if rc_idx is not None:
                            neut = _step_or_job_neutralised(
                                job_dict, jname, step_dict, sname, require_success_gate=False
                            )
                            if neut or swallowed:
                                why = neut or f"shell exit status swallowed ({trailing_op or 'set +e / !'})"
                                scan.problems.append(f"{rel}:{line_no}: {why}: {norm_cmd}")
                                continue
                            try:
                                parsed_rc = rc_parser.parse_args(toks[rc_idx + 1:])
                                specs = run_controls.parse(parsed_rc.cmds)
                                run_controls.validate(specs)
                            except (argparse.ArgumentError, ValueError, run_controls.Refused) as exc:
                                scan.problems.append(
                                    f"{rel}:{line_no}: invalid run_controls.py batch: {exc}"
                                )
                                continue

                            baselines = {
                                s["group"]: s for s in specs if s["role"] == "baseline"
                            }
                            for spec in specs:
                                try:
                                    sub_toks = shlex.split(spec["cmd"])
                                except ValueError as exc:
                                    scan.problems.append(
                                        f"{rel}:{line_no}: unparseable run_controls sub-command: {exc}"
                                    )
                                    continue
                                sub_norm = " ".join(sub_toks)
                                sub_line = _line_for_subcommand(
                                    raw_lines, run_start, run_end, sub_norm, line_no
                                )
                                if _matches_verifier_and_flag(sub_toks) or spec["role"] == "mutant":
                                    graduated_batch.add((path.name, jname, sname, sub_norm))
                                    if spec["role"] != "mutant":
                                        scan.problems.append(
                                            f"{rel}:{sub_line}: bare mutant in run_controls.py batch "
                                            f"(must be @after=<group>): {sub_norm}"
                                        )
                                    else:
                                        base_cmd = baselines[spec["group"]]["cmd"]
                                        if not _mutant_matches_baseline(base_cmd, spec["cmd"]):
                                            scan.problems.append(
                                                f"{rel}:{sub_line}: @after={spec['group']} config "
                                                f"mismatch vs @baseline={spec['group']} "
                                                f"({base_cmd!r} vs {sub_norm!r})"
                                            )
                                        else:
                                            scan.batch_gated.append(f"{rel}:{sub_line}: {sub_norm}")
                                elif _is_clean_verifier(sub_toks):
                                    seen_clean_in_job.add(sub_norm)
                            continue

                        # 2. Direct bare verifier mutant in a `run:` step / block
                        if _matches_verifier_and_flag(toks):
                            key = (path.name, jname, sname, norm_cmd)
                            entry = allowlist.get(key)
                            if entry is not None:
                                matched_allowlist.add(key)
                            neut = _step_or_job_neutralised(
                                job_dict, jname, step_dict, sname, require_success_gate=True
                            )
                            if neut or swallowed:
                                why = neut or f"shell exit status swallowed ({trailing_op or 'set +e / !'})"
                                scan.problems.append(f"{rel}:{line_no}: {why}: {norm_cmd}")
                                continue
                            if entry is None:
                                scan.problems.append(
                                    f"{rel}:{line_no}: un-allowlisted bare mutant in "
                                    f"{jname!r} / {sname!r}: {norm_cmd}"
                                )
                                continue
                            if entry.clean_baseline is not None:
                                want_clean = " ".join(shlex.split(entry.clean_baseline))
                                if want_clean not in seen_clean_in_job:
                                    scan.problems.append(
                                        f"{rel}:{line_no}: step-gated mutant missing active "
                                        f"preceding clean baseline {want_clean!r} in job {jname!r}: "
                                        f"{norm_cmd}"
                                    )
                                    continue
                                if not entry.superset and not _mutant_matches_baseline(
                                    want_clean, norm_cmd
                                ):
                                    scan.problems.append(
                                        f"{rel}:{line_no}: step-gated mutant config mismatch vs "
                                        f"clean_baseline ({want_clean!r} vs {norm_cmd!r})"
                                    )
                                    continue
                            scan.step_gated.append(f"{rel}:{line_no}: {norm_cmd}")
                            continue

                        # 3. Clean direct verifier invocation (records active clean twin for later steps)
                        if _is_clean_verifier(toks):
                            neut = _step_or_job_neutralised(
                                job_dict, jname, step_dict, sname, require_success_gate=False
                            )
                            if not neut and not swallowed:
                                seen_clean_in_job.add(norm_cmd)
                            continue

                        # 4. Wrapper command (e.g. `run_all.py`, `bash -c`) carrying quoted mutant args
                        is_run_all = any(
                            t == "run_all.py" or t.endswith("/run_all.py") for t in toks
                        )
                        neut = _step_or_job_neutralised(
                            job_dict, jname, step_dict, sname, require_success_gate=False
                        )
                        for arg in toks:
                            if not any(v in arg for v in WORKFLOW_VERIFIERS):
                                continue
                            try:
                                arg_toks = shlex.split(arg)
                            except ValueError:
                                arg_toks = arg.split()
                            if not _matches_verifier_and_flag(arg_toks):
                                continue
                            sub_norm = " ".join(arg_toks)
                            sub_line = _line_for_subcommand(
                                raw_lines, run_start, run_end, sub_norm, line_no
                            )
                            pkey = (path.name, jname, sname, sub_norm)
                            if is_run_all and pkey in pending_run_all:
                                matched_pending.add(pkey)
                                if not neut and not swallowed:
                                    scan.pending_batch.append(f"{rel}:{sub_line}: {sub_norm}")
                                    continue
                            why = (
                                neut
                                or (
                                    f"shell exit status swallowed ({trailing_op or 'set +e / !'})"
                                    if swallowed
                                    else "ungated mutant in run_all.py / wrapper batch "
                                         "(use tools/run_controls.py with @baseline=/@after=)"
                                )
                            )
                            scan.problems.append(f"{rel}:{sub_line}: {why}: {sub_norm}")

    for key in allowlist:
        if key not in matched_allowlist:
            scan.problems.append(
                f".github/workflows/{key[0]}:1: stale step-gated allowlist entry "
                f"matched no workflow row: {key!r}"
            )
    for pkey in pending_run_all:
        if pkey not in matched_pending and pkey not in graduated_batch:
            scan.problems.append(
                f".github/workflows/{pkey[0]}:1: stale pending run_all allowlist entry "
                f"matched neither run_all nor batch_gated row: {pkey!r}"
            )
    return scan


def _copy_workflows(tmp_path: pathlib.Path) -> pathlib.Path:
    dst = tmp_path / ".github" / "workflows"
    shutil.copytree(REPO / ".github" / "workflows", dst)
    return dst


def _convert_pulse2x_to_run_controls(rungs_text: str) -> str:
    old = (
        '          python tools/run_all.py --jobs 2 --timeout 900 --json build/pulse2x-ci.json\n'
        '          "python rtl-sketch/verify_voice.py --set quick --only waves3 --filter2x --pulse2x --outdir build/pulse-voice"\n'
        '          "python rtl-sketch/verify_voice.py --set quick --only waves3 --filter2x --pulse2x --simulator verilator --outdir build/pulse-voice-verilator"\n'
        '          "python rtl-sketch/verify_synth_top.py --m5a-smoke --filter2x --pulse2x --simulator verilator --outdir build/pulse-smoke"\n'
        '          "python rtl-sketch/verify_synth_top.py --m5a-smoke --filter2x --pulse2x --simulator verilator --inject VOICE_PULSE2X_OFF --expect-fail --outdir build/pulse-off"\n'
    )
    new = (
        '          python tools/run_controls.py --jobs 2 --timeout 900 --json build/pulse2x-ci.json\n'
        '          "python rtl-sketch/verify_voice.py --set quick --only waves3 --filter2x --pulse2x --outdir build/pulse-voice"\n'
        '          "python rtl-sketch/verify_voice.py --set quick --only waves3 --filter2x --pulse2x --simulator verilator --outdir build/pulse-voice-verilator"\n'
        '          "@baseline=pulse-smoke python rtl-sketch/verify_synth_top.py --m5a-smoke --filter2x --pulse2x --simulator verilator --outdir build/pulse-smoke"\n'
        '          "@after=pulse-smoke python rtl-sketch/verify_synth_top.py --m5a-smoke --filter2x --pulse2x --simulator verilator --inject VOICE_PULSE2X_OFF --expect-fail --outdir build/pulse-off"\n'
    )
    assert old in rungs_text or new in rungs_text, "pulse2x-rtl step text drifted"
    return rungs_text.replace(old, new, 1) if old in rungs_text else rungs_text


def test_step_gated_allowlist_entries_cite_a_baseline_or_open_issue():
    for key, entry in STEP_GATED_ALLOWLIST.items():
        assert entry.reason.strip(), f"empty reason on {key}"
        assert bool(entry.clean_baseline) or bool(entry.open_issue), (
            f"allowlist entry {key} must name either clean_baseline or open_issue")


def test_live_workflow_mutants_are_classified_with_no_stale_exemptions():
    scan = classify_workflow_mutants()
    assert scan.problems == [], "\n".join(scan.problems)
    assert len(scan.step_gated) == 17
    assert len(scan.batch_gated) + len(scan.pending_batch) == 1


def test_pulse2x_run_controls_batch_is_classified_as_batch_gated(tmp_path):
    wf_dir = _copy_workflows(tmp_path)
    rungs = wf_dir / "rungs.yml"
    rungs.write_text(_convert_pulse2x_to_run_controls(rungs.read_text()))
    scan = classify_workflow_mutants(wf_dir)
    assert scan.problems == [], "\n".join(scan.problems)
    assert len(scan.batch_gated) == 1
    assert scan.pending_batch == []
    assert len(scan.step_gated) == 17


def test_injected_a_bare_mutant_in_run_controls_batch_fails_with_file_and_line(tmp_path):
    """Rule 8 / AC 4(a): a mutated copy of rungs.yml with a new bare mutant in
    a `run_controls` batch fails with its file:line."""
    wf_dir = _copy_workflows(tmp_path)
    rungs = wf_dir / "rungs.yml"
    text = _convert_pulse2x_to_run_controls(rungs.read_text())
    anchor = '          "python rtl-sketch/verify_synth_top.py --filter2x --pulse2x --simulator verilator --outdir build/pulse-drums"\n'
    bare = '          "python rtl-sketch/verify_synth_top.py --m5a-smoke --filter2x --pulse2x --simulator verilator --inject I2S_SHIFT --expect-fail --outdir build/pulse-bare"\n'
    assert anchor in text
    rungs.write_text(text.replace(anchor, anchor + bare, 1))
    scan = classify_workflow_mutants(wf_dir)
    assert len(scan.problems) == 1
    assert scan.problems[0].startswith(".github/workflows/rungs.yml:")
    assert "I2S_SHIFT" in scan.problems[0] and "run_controls" in scan.problems[0]


def test_injected_b_bare_mutant_in_unallowlisted_step_fails_with_file_and_line(tmp_path):
    """Rule 8 / AC 4(b): a bare mutant in a new un-allowlisted step fails with
    its file:line."""
    wf_dir = _copy_workflows(tmp_path)
    rungs = wf_dir / "rungs.yml"
    text = rungs.read_text()
    target = "      - name: F1 -- ladder bit-exact\n        run: python rtl-sketch/verify_ladder.py\n"
    injected = (
        target
        + "      - name: rogue un-allowlisted mutant\n"
        + "        run: python rtl-sketch/verify_ctl.py --inject SPI_NOSEC --expect-fail\n"
    )
    assert target in text
    rungs.write_text(text.replace(target, injected, 1))
    scan = classify_workflow_mutants(wf_dir)
    assert len(scan.problems) == 1
    assert re.match(r"^\.github/workflows/rungs\.yml:\d+:", scan.problems[0])
    assert "SPI_NOSEC" in scan.problems[0]


def test_injected_c_batch_mutant_with_mismatched_baseline_config_fails(tmp_path):
    """Rule 8 / AC 4(c): an @after= mutant whose @baseline= differs in config
    fails with its file:line."""
    wf_dir = _copy_workflows(tmp_path)
    rungs = wf_dir / "rungs.yml"
    text = _convert_pulse2x_to_run_controls(rungs.read_text())
    old_base = '"@baseline=pulse-smoke python rtl-sketch/verify_synth_top.py --m5a-smoke --filter2x --pulse2x --simulator verilator --outdir build/pulse-smoke"'
    bad_base = '"@baseline=pulse-smoke python rtl-sketch/verify_synth_top.py --m5a-smoke --filter2x --simulator verilator --outdir build/pulse-smoke"'
    assert old_base in text
    rungs.write_text(text.replace(old_base, bad_base, 1))
    scan = classify_workflow_mutants(wf_dir)
    assert len(scan.problems) == 1
    assert re.match(r"^\.github/workflows/rungs\.yml:\d+:", scan.problems[0])
    assert "pulse-smoke" in scan.problems[0]


def test_stale_allowlist_entry_fails():
    """AC 3: if an allowlist entry no longer matches any workflow row, the
    guard fails rather than letting stale exemptions rot."""
    extra = dict(STEP_GATED_ALLOWLIST)
    extra[(
        "rungs.yml",
        "rtl",
        "the integration checks can still fail",
        "python rtl-sketch/verify_ctl.py --inject NONEXISTENT_MUTANT --expect-fail",
    )] = StepGatedEntry(reason="stale test entry (#1235)", open_issue="#1235")
    scan = classify_workflow_mutants(allowlist=extra)
    assert len(scan.problems) == 1
    assert "NONEXISTENT_MUTANT" in scan.problems[0] and "stale" in scan.problems[0]


def test_commented_out_allowlisted_mutant_trips_stale_allowlist(tmp_path):
    """Edge case: commenting out an allowlisted mutant row removes it from the
    live scan (comments are stripped) and trips the stale-exemption check."""
    wf_dir = _copy_workflows(tmp_path)
    nightly = wf_dir / "nightly.yml"
    text = nightly.read_text()
    target = "          python rtl-sketch/verify_i2s_tx.py --inject I2S_SWAP --expect-fail\n"
    assert target in text
    nightly.write_text(
        text.replace(
            target,
            "          # python rtl-sketch/verify_i2s_tx.py --inject I2S_SWAP --expect-fail\n"
            "          python rtl-sketch/verify_ctl.py # --inject BOGUS_INLINE --expect-fail\n",
            1,
        )
    )
    scan = classify_workflow_mutants(wf_dir)
    assert len(scan.problems) == 1
    assert "I2S_SWAP" in scan.problems[0] and "stale" in scan.problems[0]


def test_unallowlisted_mutant_in_folded_run_all_batch_fails_with_file_and_line(tmp_path):
    """Edge case (`>-` folded batch + `run_all.py`): `run_all.py` runs commands
    independently, so a new mutant inside a `run_all.py` batch fails."""
    wf_dir = _copy_workflows(tmp_path)
    rungs = wf_dir / "rungs.yml"
    text = rungs.read_text()
    anchor = '          "python rtl-sketch/verify_synth_top.py --filter2x --pulse2x --simulator verilator --outdir build/pulse-drums"\n'
    rogue = '          "python rtl-sketch/verify_ctl.py --inject SPI_ADDR7 --expect-fail --outdir build/rogue"\n'
    assert anchor in text
    rungs.write_text(text.replace(anchor, anchor + rogue, 1))
    scan = classify_workflow_mutants(wf_dir)
    assert len(scan.problems) == 1
    assert re.match(r"^\.github/workflows/rungs\.yml:\d+:", scan.problems[0])
    assert "run_all" in scan.problems[0] and "SPI_ADDR7" in scan.problems[0]


@pytest.mark.parametrize("patch", [
    {"if": False},
    {"if": "${{ !cancelled() }}"},
    {"if": "always()"},
    {"if": "${{ github.event_name == 'push' }}"},
    {"continue-on-error": True},
])
def test_if_neutralised_or_soft_failing_mutant_step_fails(tmp_path, patch):
    """Edge case (`if:`-neutralised / fail-fast-bypassing steps): a step-gated
    mutant with `if: false` never runs, with `always()` / `!cancelled()` runs
    even after its clean step failed, and with `continue-on-error` swallows red."""
    wf_dir = _copy_workflows(tmp_path)
    rungs = wf_dir / "rungs.yml"
    doc = yaml.safe_load(rungs.read_text())
    step = _step(doc, "M5A -- Verilator integrated disabling control")
    step.update(patch)
    rungs.write_text(yaml.safe_dump(doc, sort_keys=False))
    scan = classify_workflow_mutants(wf_dir)
    assert scan.problems, f"expected failure for {patch!r}"
    assert any(p.startswith(".github/workflows/rungs.yml:") for p in scan.problems)


def test_deleting_step_gated_clean_baseline_fails(tmp_path):
    """Rule 8 on step-gated rows that cite a clean baseline: deleting the
    preceding clean command turns the mutant ungated and must fail."""
    wf_dir = _copy_workflows(tmp_path)
    nightly = wf_dir / "nightly.yml"
    text = nightly.read_text()
    clean_line = "          python rtl-sketch/verify_i2s_tx.py\n"
    assert clean_line in text
    nightly.write_text(text.replace(clean_line, "", 1))
    scan = classify_workflow_mutants(wf_dir)
    assert len(scan.problems) == 1
    assert re.match(r"^\.github/workflows/nightly\.yml:\d+:", scan.problems[0])
    assert "verify_i2s_tx.py" in scan.problems[0]


def test_shell_exit_swallow_on_step_gated_mutant_fails(tmp_path):
    """Rule 8: appending `|| true` to an allowlisted mutant swallows a surviving
    mutant's exit status and must fail."""
    wf_dir = _copy_workflows(tmp_path)
    nightly = wf_dir / "nightly.yml"
    text = nightly.read_text()
    target = "          python rtl-sketch/verify_i2s_tx.py --inject I2S_SWAP --expect-fail\n"
    assert target in text
    nightly.write_text(
        text.replace(
            target,
            "          python rtl-sketch/verify_i2s_tx.py --inject I2S_SWAP --expect-fail || true\n",
            1,
        )
    )
    scan = classify_workflow_mutants(wf_dir)
    assert scan.problems
    assert any(re.match(r"^\.github/workflows/nightly\.yml:\d+:", p) for p in scan.problems)


def test_backslash_continued_literal_run_controls_batch_is_classified(tmp_path):
    """Edge case: multi-line `run: |` block joining a `run_controls.py` batch
    across backslash continuations."""
    wf_dir = _copy_workflows(tmp_path)
    (wf_dir / "extra.yml").write_text(
        "name: extra\n"
        "on: workflow_dispatch\n"
        "jobs:\n"
        "  j:\n"
        "    runs-on: ubuntu-latest\n"
        "    steps:\n"
        "      - uses: actions/checkout@v4\n"
        "      - name: continued batch\n"
        "        run: |\n"
        "          python tools/run_controls.py --jobs 2 \\\n"
        '            "@baseline=ctl python rtl-sketch/verify_ctl.py --outdir build/a" \\\n'
        '            "@after=ctl python rtl-sketch/verify_ctl.py --inject SPI_ADDR7 --expect-fail --outdir build/b"\n'
    )
    scan = classify_workflow_mutants(wf_dir)
    assert scan.problems == [], "\n".join(scan.problems)
    assert any(b.startswith(".github/workflows/extra.yml:") for b in scan.batch_gated)


def test_dropping_prefixes_from_pulse2x_run_controls_batch_fails(tmp_path):
    """Issue #1235 motivation: if `@baseline=`/`@after=` prefixes are dropped
    from `pulse2x-rtl` in a `run_controls.py` batch, the guard fails at rungs.yml:78
    -- `PENDING_RUN_ALL_ALLOWLIST` never exempts a bare `run_controls.py` row."""
    wf_dir = _copy_workflows(tmp_path)
    rungs = wf_dir / "rungs.yml"
    rungs.write_text(
        rungs.read_text().replace("python tools/run_all.py", "python tools/run_controls.py", 1)
    )
    scan = classify_workflow_mutants(wf_dir)
    assert len(scan.problems) == 1
    assert scan.problems[0].startswith(".github/workflows/rungs.yml:78:")
    assert "VOICE_PULSE2X_OFF" in scan.problems[0] and "run_controls" in scan.problems[0]


