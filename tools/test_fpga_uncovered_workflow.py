"""The fpga-uncovered CI job's preconditions, asserted by its own suite (#853).

The job runs the 18 fpga/ test files no other CI job collects -- fpga/release/
by directory, eleven more by name -- after installing spec/trial-environment.json,
whose pins three of those files preflight (NO VERDICT on drift) and whose
pyserial the pty harness refuses without. This file is the job's guard, run by
the tools job's directory collection: the job still exists, the paths it names
exist, no ignore/deselect slipped in, and the floor step's number equals a live
collection of the same paths -- so the exclusion control cannot drift and an
exclusion cannot be achieved by silent non-collection.

Controls: the two pure helpers are asserted to REJECT the defect they exist to
catch, on hand-built job dicts, before they are trusted against the real file.
"""
import os
import re
import subprocess
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "rungs.yml"
JOB = "fpga-uncovered"
ENV_VAR = "FPGA_UNCOVERED_PATHS"
RELEASE_DIR = "fpga/release/"
NAMED = (
    "fpga/test_bringup_arty.py",
    "fpga/test_ext_io_extract.py",
    "fpga/test_ext_io_timing.py",
    "fpga/test_play_song.py",
    "fpga/test_record_compare.py",
    "fpga/test_uart_host_hold_refused.py",
    "fpga/test_uart_host_pty.py",
    "fpga/test_verify_flash.py",
    "fpga/test_verify_live_midi_unwrap.py",
    "fpga/test_verify_live_midi_output_path.py",
    "fpga/test_pads_rom.py",
)
BANNED = ("--ignore", "--ignore-glob", "--deselect", "-k ")


def paths(job: dict) -> tuple[str, ...]:
    """The collection list exactly as the job's two steps see it."""
    env = (job.get("env") or {}).get(ENV_VAR, "")
    return tuple(env.split())


def floor_of(job: dict) -> int:
    """The exclusion control's number, read from the floor step itself."""
    runs = [s["run"] for s in job.get("steps", []) if isinstance(s.get("run"), str)]
    hits = [re.search(r"FLOOR=(\d+)", r) for r in runs]
    hits = [m for m in hits if m]
    assert len(hits) == 1, f"expected exactly one FLOOR= step, found {len(hits)}"
    return int(hits[0].group(1))


def real_job() -> dict:
    workflow = yaml.safe_load(WORKFLOW.read_text())
    jobs = workflow.get("jobs") or {}
    assert JOB in jobs, (f"no job {JOB!r} in rungs.yml -- the #853 fpga gate "
                         "was deleted or renamed")
    return jobs[JOB]


# -- controls: each helper must reject the defect it exists to catch ----------

def test_control_paths_helper_rejects_a_lost_named_file():
    good = {"env": {ENV_VAR: " ".join((RELEASE_DIR, *NAMED))}}
    assert paths(good) == (RELEASE_DIR, *NAMED)
    lost = NAMED[:-1]                             # one file dropped silently
    bad = {"env": {ENV_VAR: " ".join((RELEASE_DIR, *lost))}}
    assert paths(bad) != (RELEASE_DIR, *NAMED)


def test_control_floor_helper_rejects_a_missing_or_double_floor():
    one = {"steps": [{"run": "FLOOR=320\nn=$(...)\n[ \"$n\" -ge \"$FLOOR\" ]"}]}
    assert floor_of(one) == 320
    none = {"steps": [{"run": "n=$(python -m pytest --collect-only -q)"}]}
    try:
        floor_of(none)
    except AssertionError:
        pass
    else:
        raise AssertionError("floor_of accepted a job with no FLOOR= step")
    two = {"steps": [{"run": "FLOOR=320"}, {"run": "FLOOR=321"}]}
    try:
        floor_of(two)
    except AssertionError:
        pass
    else:
        raise AssertionError("floor_of accepted two FLOOR= steps")


# -- the real workflow --------------------------------------------------------

def test_paths_env_names_exactly_the_measured_18():
    assert paths(real_job()) == (RELEASE_DIR, *NAMED)


def test_every_named_path_exists():
    for p in paths(real_job()):
        assert (ROOT / p).exists(), f"the job names {p}, which does not exist"


def test_job_gates_on_changes_scope_like_every_suite_job():
    """The canonical gate, restated at full strength, not a weaker copy.

    A substring check here once passed while the canonical
    ``test_every_suite_job_is_gated_fail_safe`` failed: the job was gated
    correctly but absent from ``GATED`` -- exactly the input that shipped on
    this PR. So assert the two things the canonical gate asserts (byte-exact
    ``if:``, registration in ``GATED``), deferring to it for everything else.
    """
    import test_ci_changes as gate

    job = real_job()
    assert job.get("needs") == "changes"
    assert str(job.get("if", "")) == gate.FULL_IF, (
        "the job's if: must be FULL_IF byte for byte, like every suite job")
    assert JOB in gate.GATED[WORKFLOW.name], (
        f"{JOB} must be registered in test_ci_changes.py's "
        f"GATED[{WORKFLOW.name!r}] registry -- an unregistered suite job is "
        "ungated, and only the canonical gate fails on that")


def test_job_fetches_full_history_for_trial_provenance():
    checkout = next(s for s in real_job()["steps"]
                    if str(s.get("uses", "")).startswith("actions/checkout"))
    assert str(checkout.get("with", {}).get("fetch-depth")) == "0", (
        "tools/trial.py records HEAD/origin/main/merge-base provenance; "
        "fpga-uncovered must fetch full history")


def test_job_installs_the_spec_not_hand_written_pins():
    runs = "\n".join(s["run"] for s in real_job()["steps"]
                     if isinstance(s.get("run"), str))
    assert "tools/trial_env.py bootstrap --no-toolchain" in runs, (
        "three of the 18 files preflight spec/trial-environment.json and the "
        "pty harness refuses without pyserial -- the job must install from "
        "the spec, not from hand-written versions")
    assert "pip install numpy" not in runs, (
        "the unpinned numpy/scipy/pytest the other jobs install read here as "
        "six red preflight refusals")


def test_no_ignores_or_deselects_in_the_job():
    runs = "\n".join(s["run"] for s in real_job()["steps"]
                     if isinstance(s.get("run"), str))
    for banned in BANNED:
        assert banned not in runs, (
            f"{banned!r} found in a run step: all 18 files measured runnable, "
            "so an exclusion here would be unjustified and silent")


def test_floor_equals_a_live_collection_of_the_same_paths():
    job = real_job()
    floor = floor_of(job)
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q",
         "-p", "no:cacheprovider", *paths(job)],
        cwd=ROOT, capture_output=True, text=True,
        env={k: v for k, v in os.environ.items() if k != "PYTEST_ADDOPTS"},
        check=False)
    out = proc.stdout + proc.stderr
    assert proc.returncode in (0, 5) and "ERROR collecting" not in out, (
        "the floor's own collection failed -- no verdict on the floor, "
        "never a stale claim:\n" + out[-800:])
    m = re.search(r"(\d+) tests? collected", proc.stdout)
    assert m, "could not read a collection count from pytest:\n" + proc.stdout[-400:]
    live = int(m.group(1))
    assert floor == live, (
        f"the job's floor ({floor}) != what it would collect today ({live}). "
        "If tests were added legitimately, bump FLOOR in rungs.yml; if tests "
        "were lost or excluded, this guard exists to surface exactly that.")


def test_release_dir_is_collected_by_directory_not_by_name():
    """A 17th uncovered file hiding in fpga/release/ must run, not vanish:
    the directory form collects it automatically; a file list would not."""
    assert paths(real_job())[0] == RELEASE_DIR
