"""Rule-8 controls for the rtl-full simulator-identity gate (issue #1154).

The guard has two halves, and each ships with the input that defeats it:

* the Python half -- `tools/setup_ci_verilator.py --assert-path` must refuse
  (RuntimeError naming expected and actual) on the apt build `Verilator
  5.020-1`, on empty or garbage `--version` output, and on a missing
  `verilator`; it accepts only the qualified `Verilator 5.052 ` line.
* the YAML half -- the rtl-full phrase step must gate on
  `steps.verilator_identity.outcome`. The defeating input is the pre-#1154
  shape: a bare `if: ${{ !cancelled() }}` phrase step, which still runs after
  a failed step (a failure does not set `cancelled()`) and would emit the
  ungrounded M5A/M5B report under the apt simulator. Issue #1170 adds two
  more: wrong-polarity mentions (`|| ... == 'failure'`,
  `!= 'failure'` admits `skipped`) that reference the outcome without
  gating on its 'success' value, so the whole `if` string is compared.

Following the #940 pattern in `tools/test_check_workflows.py`, the YAML half
is tested against the REAL parsed `rungs.yml` -- what ships is the step wiring
in that file, and a lookalike fixture could pass while the real file drifted --
plus a mutation control that restores the bare-`!cancelled()` defect on the
real parsed shape and asserts the checker goes red on it.
"""
from __future__ import annotations

import copy
import pathlib
import subprocess
import sys

import pytest
import yaml

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import setup_ci_verilator as scv                                   # noqa: E402

REPO = pathlib.Path(__file__).resolve().parents[1]
RUNGS = REPO / ".github" / "workflows" / "rungs.yml"
PHRASE_STEP_NAME = ("pulse2x -- complete M5A and M5B phrases decoded from I2S "
                    "and scored independently")


# =============================================================================
# The Python half: the shared prefix check refuses on every defeating input.
# =============================================================================
def test_rejects_the_apt_verilator_5_020():
    with pytest.raises(RuntimeError, match="5.020"):
        scv.assert_simulator_identity("Verilator 5.020-1 (ubuntu 1:5.020-1)")


def test_rejects_empty_output():
    with pytest.raises(RuntimeError, match="no output"):
        scv.assert_simulator_identity("")


def test_rejects_whitespace_only_output():
    with pytest.raises(RuntimeError, match="no output"):
        scv.assert_simulator_identity("  \n\t\n")


def test_rejects_garbage_output():
    with pytest.raises(RuntimeError, match="not-the-simulator"):
        scv.assert_simulator_identity("not-the-simulator at all")


def test_accepts_the_qualified_5_052_build():
    line = ("Verilator 5.052 2026-09-06 rev ea338be9 "
            "(v5.052-ea338be9)")
    assert scv.assert_simulator_identity(line + "\n") == line


def test_accepts_extra_lines_after_the_qualified_first_line():
    # `verilator --version` writes one line today; the check is on the first
    # line, so trailing output must not turn a qualified build into a refusal.
    assert scv.assert_simulator_identity("Verilator 5.052 2026-09-06 rev ea338be9\nwarning: x\n")


def test_assert_path_accepts_a_qualified_path_verilator(monkeypatch, capsys):
    monkeypatch.setattr(
        scv.subprocess, "check_output",
        lambda *a, **k: "Verilator 5.052 2026-09-06 rev ea338be9\n",
    )
    scv.assert_path_identity()
    assert "Verilator 5.052" in capsys.readouterr().out


def test_assert_path_refuses_a_missing_verilator(monkeypatch):
    def missing(*args, **kwargs):
        raise FileNotFoundError("verilator")
    monkeypatch.setattr(scv.subprocess, "check_output", missing)
    with pytest.raises(RuntimeError, match="no verilator resolved from PATH"):
        scv.assert_path_identity()


def test_assert_path_refuses_a_failing_verilator(monkeypatch):
    def failing(*args, **kwargs):
        raise subprocess.CalledProcessError(2, "verilator --version", output="boom")
    monkeypatch.setattr(scv.subprocess, "check_output", failing)
    with pytest.raises(RuntimeError, match="exited 2"):
        scv.assert_path_identity()


# =============================================================================
# The YAML half: the shipped rtl-full wiring, checked against the real file.
# =============================================================================
def _rtl_full() -> dict:
    return yaml.safe_load(RUNGS.read_text())["jobs"]["rtl-full"]


def _phrase_step(job: dict) -> dict:
    for step in job["steps"]:
        if step.get("name") == PHRASE_STEP_NAME:
            return step
    pytest.fail(f"rtl-full has no step named {PHRASE_STEP_NAME!r}")


# The exact condition the phrase step ships (rungs.yml): a mention of the
# identity outcome is not a gate on it, so the whole string is compared.
EXPECTED_PHRASE_IF = "${{ !cancelled() && steps.verilator_identity.outcome == 'success' }}"


def _identity_gates_phrase(job: dict) -> tuple[bool, str]:
    """True only when the shipped wiring can refuse: a unique
    `verilator_identity` step running `--assert-path` immediately after the
    pinned-setup step, and a phrase-step `if` that is exactly the expected
    `!cancelled() && steps.verilator_identity.outcome == 'success'` gate.
    Anything less (the bare `!cancelled()` defect, or a mention of the
    outcome without the 'success' polarity) returns False."""
    identity_indices = [i for i, s in enumerate(job["steps"]) if s.get("id") == "verilator_identity"]
    if len(identity_indices) != 1:
        return False, f"expected one id: verilator_identity step, found {len(identity_indices)}"
    i = identity_indices[0]
    if "--assert-path" not in str(job["steps"][i].get("run", "")):
        return False, "verilator_identity step does not run --assert-path"
    setup = [j for j, s in enumerate(job["steps"])
             if str(s.get("run", "")).strip() == "python tools/setup_ci_verilator.py"]
    if i - 1 not in setup:
        return False, "verilator_identity step is not immediately after the pinned-setup step"
    cond = str(_phrase_step(job).get("if", ""))
    if "steps.verilator_identity.outcome" not in cond:
        return False, f"phrase step if does not reference the identity outcome: {cond!r}"
    if cond != EXPECTED_PHRASE_IF:
        # Issue #1170: a mention of the outcome is not a gate on it. Only the
        # exact shipped condition requires the identity step to have succeeded;
        # `|| ... == 'failure'` and `!= 'failure'` (admits `skipped`) mention
        # `verilator_identity.outcome` yet still run the phrases ungrounded.
        return False, (f"phrase step if must require the identity outcome to be "
                       f"exactly {EXPECTED_PHRASE_IF!r}, found: {cond!r}")
    return True, cond


def test_real_rungs_phrase_step_is_gated_on_the_identity_outcome():
    ok, detail = _identity_gates_phrase(_rtl_full())
    assert ok, detail


def test_bare_cancelled_phrase_step_is_rejected():
    # Rule 8: the defeating input itself. Restore the pre-#1154 defect on the
    # real parsed shape and the checker must go red -- a failed step does not
    # set cancelled(), so this wiring would run the phrases under apt 5.020-1.
    job = copy.deepcopy(_rtl_full())
    _phrase_step(job)["if"] = "${{ !cancelled() }}"
    ok, detail = _identity_gates_phrase(job)
    assert not ok
    assert "identity outcome" in detail


def test_or_failure_polarity_phrase_step_is_rejected():
    # Rule 8, issue #1170: mentioning the outcome is not gating on it. This
    # wiring runs the phrases on exactly the outcome the identity step exists
    # to catch (a failed identity still sets !cancelled()).
    job = copy.deepcopy(_rtl_full())
    _phrase_step(job)["if"] = "${{ !cancelled() || steps.verilator_identity.outcome == 'failure' }}"
    ok, detail = _identity_gates_phrase(job)
    assert not ok
    assert "'success'" in detail


def test_not_failure_polarity_phrase_step_is_rejected():
    # Rule 8, issue #1170: `!= 'failure'` mentions the outcome but admits
    # `skipped` (and every non-failure outcome), re-opening the #1154 hole.
    job = copy.deepcopy(_rtl_full())
    _phrase_step(job)["if"] = "${{ !cancelled() && steps.verilator_identity.outcome != 'failure' }}"
    ok, detail = _identity_gates_phrase(job)
    assert not ok
    assert "'success'" in detail


def test_a_missing_identity_step_is_rejected():
    job = copy.deepcopy(_rtl_full())
    job["steps"] = [s for s in job["steps"] if s.get("id") != "verilator_identity"]
    _phrase_step(job)["if"] = "${{ !cancelled() }}"
    ok, detail = _identity_gates_phrase(job)
    assert not ok
    assert "verilator_identity" in detail


def test_upload_artifact_stays_if_always():
    job = _rtl_full()
    upload = [s for s in job["steps"] if str(s.get("uses", "")).startswith("actions/upload-artifact")]
    assert upload, "rtl-full lost its upload-artifact step"
    assert upload[-1].get("if") == "always()"
