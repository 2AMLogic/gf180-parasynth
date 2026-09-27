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
