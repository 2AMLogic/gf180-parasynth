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


# =============================================================================
# The pair gate's duplicate-name hole (issue #1206): the dict keyed by step
# name let a second occurrence overwrite the first, so only the LAST
# occurrence was ever order-checked. These pin the ambiguity refusal that
# mirrors the in-step gate's (a duplicate is never last-wins).
# =============================================================================
def test_a_duplicate_record_step_name_is_refused_not_last_wins():
    """The issue's reproducer: record, check, record. The dict kept the last
    record step, so the gate went green while an ungated --run --slow rewrote
    the README before anything judged it -- the #906 harm itself."""
    wf = _workflow(f"""\
  full-rtl:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - name: {cw.ROADMAP_RECORD_STEP}
        run: python tools/compile_dag.py --run --slow
      - name: {cw.ROADMAP_CHECK_STEP}
        run: python tools/compile_dag.py --check
      - name: {cw.ROADMAP_RECORD_STEP}
        run: python tools/compile_dag.py --run --slow
    """)
    ok, reason = cw.roadmap_check_step_precedes_record_step(wf)
    assert not ok
    assert "ambiguous" in reason and cw.ROADMAP_RECORD_STEP in reason


def test_a_duplicate_check_step_name_is_also_refused():
    wf = _workflow(f"""\
  full-rtl:
    runs-on: ubuntu-latest
    steps:
      - name: {cw.ROADMAP_CHECK_STEP}
        run: python tools/compile_dag.py --check
      - name: {cw.ROADMAP_RECORD_STEP}
        run: python tools/compile_dag.py --run --slow
      - name: {cw.ROADMAP_CHECK_STEP}
        run: python tools/compile_dag.py --check
    """)
    ok, reason = cw.roadmap_check_step_precedes_record_step(wf)
    assert not ok
    assert "ambiguous" in reason and cw.ROADMAP_CHECK_STEP in reason


def test_a_duplicate_record_step_in_another_job_is_refused():
    """A duplicate in another job is equally ungated (per-job scoping was
    rejected in curation): the file is ambiguous, not merely mis-paired."""
    wf = _workflow(f"""\
  full-rtl:
    runs-on: ubuntu-latest
    steps:
      - name: {cw.ROADMAP_CHECK_STEP}
        run: python tools/compile_dag.py --check
      - name: {cw.ROADMAP_RECORD_STEP}
        run: python tools/compile_dag.py --run --slow
  other:
    runs-on: ubuntu-latest
    steps:
      - name: {cw.ROADMAP_RECORD_STEP}
        run: python tools/compile_dag.py --run --slow
    """)
    ok, reason = cw.roadmap_check_step_precedes_record_step(wf)
    assert not ok
    assert "ambiguous" in reason and cw.ROADMAP_RECORD_STEP in reason


def test_one_check_and_one_record_in_order_stay_green():
    """Rule-8 control on the fix itself: the ambiguity refusal must not make
    the clean pair unsatisfiable."""
    wf = _workflow(f"""\
  full-rtl:
    runs-on: ubuntu-latest
    steps:
      - name: {cw.ROADMAP_CHECK_STEP}
        run: python tools/compile_dag.py --check
      - name: {cw.ROADMAP_RECORD_STEP}
        run: python tools/compile_dag.py --run --slow
    """)
    ok, reason = cw.roadmap_check_step_precedes_record_step(wf)
    assert ok, reason


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
# #1192: the SET of compile_dag.py invocations. Both order gates above key on
# step NAME, so an invocation under any other name is invisible to them both
# -- the #1177 shape, present in the tree (dag.yml's regenerator, nightly's
# post-record strict check) rather than hypothetical. The set gate accounts
# for every invoking step: name-gated, or allowlisted with the measurement
# that says the ungated shape is deliberate (rule 8 form 2).
# =============================================================================
def _real_parsed(*filenames):
    """The real workflow files, freshly parsed -- each test mutates its own
    copy, so no mutation can leak into another test."""
    return [(n, yaml.safe_load((REPO / ".github" / "workflows" / n).read_text()))
            for n in filenames]


def _job_with_step(workflow: dict, step_name: str) -> dict:
    for job in workflow["jobs"].values():
        names = [s.get("name") for s in job.get("steps") or [] if isinstance(s, dict)]
        if step_name in names:
            return job
    raise AssertionError(f"no job carries a step named {step_name!r}")


def test_real_tree_every_compile_dag_invocation_is_accounted():
    parsed = _real_parsed("rungs.yml", "nightly.yml", "dag.yml")
    ok, reason = cw.compile_dag_invocation_set_accounted(parsed)
    assert ok, reason
    hits = {(f, s.get("name")) for f, doc in parsed
            for job in (doc.get("jobs") or {}).values()
            for s in (job.get("steps") or [])
            if isinstance(s, dict) and cw._compile_dag_invocations(str(s.get("run", "")))}
    assert len(hits) == 5, sorted(hits)
    assert ("dag.yml", "run the evidence and regenerate") in hits
    assert ("nightly.yml", "nothing is red or stale") in hits
    assert "3 name-gated" in reason and "2 allowlisted" in reason


def test_an_extra_run_step_under_a_new_name_in_rungs_is_set_red_and_order_green():
    """The injected defect, on the REAL parsed shape: a second --run under a
    name no gate selects. The premise assert proves the set gate is not
    redundant -- the name-keyed order gate stays green on the same mutation."""
    parsed = _real_parsed("rungs.yml", "nightly.yml", "dag.yml")
    rungs = parsed[0][1]
    _job_with_step(rungs, cw.ROADMAP_STEP)["steps"].append(
        {"name": "quietly refresh the roadmap",
         "run": "python tools/compile_dag.py --run || true"})
    ok, reason = cw.compile_dag_invocation_set_accounted(parsed)
    assert not ok
    assert "quietly refresh the roadmap" in reason and "#1192" in reason
    assert cw.roadmap_check_precedes_run_in_step(rungs)[0], \
        "premise: the name-keyed in-step gate is blind to the new step"


def test_an_extra_run_step_under_a_new_name_in_nightly_is_set_red_and_pair_green():
    parsed = _real_parsed("rungs.yml", "nightly.yml", "dag.yml")
    nightly = parsed[1][1]
    _job_with_step(nightly, cw.ROADMAP_RECORD_STEP)["steps"].append(
        {"name": "and again, quietly",
         "run": "python tools/compile_dag.py --run"})
    ok, reason = cw.compile_dag_invocation_set_accounted(parsed)
    assert not ok
    assert "and again, quietly" in reason
    assert cw.roadmap_check_step_precedes_record_step(nightly)[0], \
        "premise: the name-keyed pair gate is blind to the new step"


def test_a_commented_out_invocation_is_not_in_the_set():
    """The flag scan strips shell comments first, so a step that only talks
    about running compile_dag.py is not an invocation and not in the set."""
    parsed = _real_parsed("rungs.yml", "nightly.yml", "dag.yml")
    rungs = parsed[0][1]
    _job_with_step(rungs, cw.ROADMAP_STEP)["steps"].append(
        {"name": "only talks about it",
         "run": "# python tools/compile_dag.py --run"})
    ok, reason = cw.compile_dag_invocation_set_accounted(parsed)
    assert ok, reason


def test_the_set_gate_refuses_an_empty_match_set():
    parsed = [("minimal.yml",
               yaml.safe_load("jobs:\n  a:\n    steps:\n      - run: echo hi\n"))]
    ok, reason = cw.compile_dag_invocation_set_accounted(parsed)
    assert not ok and "vacuous" in reason


def test_without_the_dag_allowlist_entry_the_real_tree_is_red(monkeypatch):
    monkeypatch.delitem(
        cw.COMPILE_DAG_UNGUARDED_STEPS,
        ("dag.yml", "run the evidence and regenerate"))
    parsed = _real_parsed("rungs.yml", "nightly.yml", "dag.yml")
    ok, reason = cw.compile_dag_invocation_set_accounted(parsed)
    assert not ok
    assert "run the evidence and regenerate" in reason


def test_an_allowlisted_step_whose_flags_drifted_is_red():
    """Rule 8, on this guard itself: an exemption must not outlive the shape
    it excuses. Giving the dag bot's --run a --slow changes what the entry
    covers; the signature pin catches the drift and forces the reason to be
    re-read against the new shape."""
    parsed = _real_parsed("rungs.yml", "nightly.yml", "dag.yml")
    dag = parsed[2][1]
    step = next(s for job in dag["jobs"].values()
                for s in job.get("steps") or []
                if s.get("name") == "run the evidence and regenerate")
    step["run"] = step["run"].replace("compile_dag.py --run",
                                      "compile_dag.py --run --slow")
    ok, reason = cw.compile_dag_invocation_set_accounted(parsed)
    assert not ok
    assert "allowlisted" in reason and "--slow" in reason


def test_an_allowlist_entry_matching_no_step_is_reported(monkeypatch):
    monkeypatch.setitem(cw.COMPILE_DAG_UNGUARDED_STEPS,
                        ("dag.yml", "a ghost"), (((),), "superseded reason"))
    parsed = _real_parsed("rungs.yml", "nightly.yml", "dag.yml")
    ok, reason = cw.compile_dag_invocation_set_accounted(parsed)
    assert not ok
    assert "a ghost" in reason


def test_a_duplicated_record_step_name_is_red_in_both_gates():
    """#1196 review: a second, ungated `--run` under the gated record name,
    inserted before the check step, passed all three gates (the pair gate kept
    only the last same-named step). Real parsed nightly.yml, premise asserted."""
    parsed = _real_parsed("rungs.yml", "nightly.yml", "dag.yml")
    nightly = parsed[1][1]
    job = nightly["jobs"]["full-rtl"]
    i = next(i for i, s in enumerate(job["steps"])
             if s.get("name") == cw.ROADMAP_CHECK_STEP)
    job["steps"].insert(i, {"name": cw.ROADMAP_RECORD_STEP,
                            "run": "python tools/compile_dag.py --run"})
    ok, reason = cw.compile_dag_invocation_set_accounted(parsed)
    assert not ok and "occurs 2 times" in reason
    ok, reason = cw.roadmap_check_step_precedes_record_step(nightly)
    assert not ok and "ambiguous" in reason


def test_a_duplicated_allowlisted_step_name_is_red():
    parsed = _real_parsed("rungs.yml", "nightly.yml", "dag.yml")
    dag = parsed[2][1]
    job = next(j for j in dag["jobs"].values()
               if any(s.get("name") == "run the evidence and regenerate"
                      for s in j.get("steps") or []))
    step = next(s for s in job["steps"]
                if s.get("name") == "run the evidence and regenerate")
    job["steps"].append(dict(step))
    ok, reason = cw.compile_dag_invocation_set_accounted(parsed)
    assert not ok and "occurs 2 times" in reason


def test_a_lone_pair_named_step_without_its_partner_is_red():
    """A step carrying a gated NAME in a file the pair gate never fires on
    (its partner absent) looks guarded and is not: name membership alone
    must not account for it."""
    wf = _workflow("""\
  orphan:
    runs-on: ubuntu-latest
    steps:
      - name: the committed roadmap matches the committed evidence
        run: python tools/compile_dag.py --check
""")
    ok, reason = cw.compile_dag_invocation_set_accounted([("orphan.yml", wf)])
    assert not ok
    assert "record the slow evidence for the roadmap" in reason


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
    # dag.yml joined the fixture with #1192: the invocation-set gate reads it
    # (its regenerator is allowlisted), so green-on-real must include it.
    for f in ("rungs.yml", "nightly.yml", "dag.yml"):
        shutil.copy(REPO / ".github" / "workflows" / f, tmp_path / f)
    monkeypatch.setattr(cw, "WF", tmp_path)
    assert cw.main() == 0
    out = capsys.readouterr().out
    assert "roadmap order:" in out
    assert "compile_dag invocation set:" in out


def test_main_is_red_on_an_unaccounted_invocation(tmp_path, monkeypatch, capsys):
    import shutil
    for f in ("rungs.yml", "nightly.yml", "dag.yml"):
        shutil.copy(REPO / ".github" / "workflows" / f, tmp_path / f)
    (tmp_path / "sneaky.yml").write_text("""\
name: sneaky
on: workflow_dispatch
jobs:
  a:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - name: refresh the roadmap quietly
        run: python tools/compile_dag.py --run
""")
    monkeypatch.setattr(cw, "WF", tmp_path)
    assert cw.main() == 1
    err = capsys.readouterr().err
    assert "refresh the roadmap quietly" in err


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
