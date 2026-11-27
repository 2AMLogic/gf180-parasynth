"""The CI apt dependency steps get one retry-wrapped entry point -- and this
suite is its contract (issue #1062).

Why this exists: `pulse2x-rtl` died red on PR #1059 with no test run at all --
the step `sudo apt-get update && sudo apt-get install -y ...` hit an Azure
mirror timeout and apt exited 100 (run 44522458312, attempt 1). The log shows
apt had already retried the same mirror IP four times *inside one invocation*,
so the recovery that matters is a fresh `apt-get update` + install attempt,
not a bigger in-apt retry count. Nine call sites across four workflow files
carried the unprotected one-shot sequence.

The cases below are ordered by the only failure mode that matters here: a
transient mirror outage must not red the run, and a persistent one must end
visibly within the step's own limits -- never hang to the job timeout, never
run an install after a failed update, and never retry without bound.

Everything runs against a scripted apt stand-in: no mirror contact, no real
delays (`--delay 0`), no sudo (`--no-sudo`), no root.
"""
from __future__ import annotations

import json
import os
import pathlib
import subprocess
import sys
import textwrap
import time

import pytest
import yaml

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ci_apt_install as cap                                     # noqa: E402

PY = sys.executable
ROOT = pathlib.Path(__file__).resolve().parents[1]
WF = ROOT / ".github" / "workflows"

# The fetch failure verbatim from run 44522458312, job 135554392901: what a
# scripted stand-in must emit for a fault sequence to be the recorded one.
FETCH_ERR = [
    "Err:3 https://security.ubuntu.com/ubuntu noble/universe amd64 iverilog amd64 12.0-2build2",
    "  Could not connect to azure.archive.ubuntu.com:80 (52.252.75.106), connection timed out [IP: 52.252.75.106 80]",
    "E: Unable to fetch some archives, maybe run apt-get update or try with --fix-missing?",
]


FAKE_APT = textwrap.dedent(r"""
    import json, os, pathlib, sys, time
    plan = json.loads(pathlib.Path(os.environ["CI_APT_FAKE_PLAN"]).read_text())
    log = pathlib.Path(os.environ["CI_APT_FAKE_LOG"])
    done = log.read_text().splitlines() if log.exists() else []
    args = sys.argv[1:]
    phase = "update" if args[:1] == ["update"] else "install"
    if len(done) < len(plan):
        step = plan[len(done)]
    else:
        step = {"status": 3}
    record = {"phase": phase, "args": args}
    if step.get("phase", phase) != phase:
        record["unexpected"] = True
        status = 3
    else:
        status = int(step.get("status", 0))
    with log.open("a") as fh:
        fh.write(json.dumps(record) + "\n")
    if step.get("sleep"):
        time.sleep(float(step["sleep"]))
    for line in step.get("echo", []):
        print(line, flush=True)
    sys.exit(status)
""")


@pytest.fixture
def fake_apt(tmp_path, monkeypatch):
    """A stand-in `apt-get` driven by a JSON plan, appending one JSONL record
    per invocation -- written BEFORE any scripted sleep, so an invocation the
    wrapper kills mid-hang is still recorded. Plan entries: {"phase":
    "update"|"install", "status": N, "echo": [...], "sleep": seconds}. An
    invocation past the plan's end, or whose phase disagrees with the script,
    is recorded with "unexpected" and exits 3 -- so a test that over-runs its
    script cannot look like success."""
    script = tmp_path / "fake-apt-get"
    script.write_text(f"#!{PY}\n{FAKE_APT}")
    script.chmod(0o755)
    plan = tmp_path / "plan.json"
    log = tmp_path / "invocations.jsonl"
    plan.write_text("[]")
    monkeypatch.setenv("CI_APT_FAKE_PLAN", str(plan))
    monkeypatch.setenv("CI_APT_FAKE_LOG", str(log))

    def set_plan(*steps):
        plan.write_text(json.dumps(list(steps)))

    def log_entries():
        if not log.exists():
            return []
        return [json.loads(line) for line in log.read_text().splitlines()]

    return script, set_plan, log_entries


def run_helper(fake_apt, packages, *extra, cap_invoker=None):
    script, _, _ = fake_apt
    cmd = [PY, str(ROOT / "tools" / "ci_apt_install.py"),
           "--no-sudo", "--apt", str(script), "--delay", "0", *extra, *packages]
    return subprocess.run(cmd, capture_output=True, text=True, timeout=120)


# --- the defect, recorded: the one-shot sequence the workflows ship today ----

def test_control_one_shot_sequence_stops_at_the_first_failed_install(fake_apt):
    """Start-red record (docs/verification-rules.md): the workflows' current
    `update && install` shape, against the exact fault from run 44522458312.

    The scripted plan holds a *successful second install* that a retrying
    wrapper would reach. The one-shot sequence must NOT reach it -- that is
    the defect: exit 100 with the recovery sitting unconsumed in the script.
    If this control ever fails because apt "helpfully" retried inside the
    one-shot chain, the fault is no longer reproduced and every wrapper test
    below is measuring nothing."""
    script, set_plan, log_entries = fake_apt
    set_plan(
        {"phase": "update", "status": 0},
        {"phase": "install", "status": 100, "echo": FETCH_ERR},
        {"phase": "install", "status": 0},
    )
    packages = ["iverilog", "autoconf", "flex", "libfl-dev", "bison", "help2man"]
    r = subprocess.run(
        f"{script} update && {script} install -y {' '.join(packages)}",
        shell=True, capture_output=True, text=True, timeout=60)
    assert r.returncode == 100, "the one-shot sequence must propagate apt's 100"
    entries = log_entries()
    assert [e["phase"] for e in entries] == ["update", "install"], \
        "execution must stop before the scripted successful second install"
    assert any("Could not connect to azure.archive.ubuntu.com" in line
               for line in r.stdout.splitlines()), "apt's diagnostic must be retained"


# --- the helper's contract ---------------------------------------------------

def test_clean_run_is_one_update_then_one_install(fake_apt):
    """Acceptance criterion 1: a clean install takes one attempt and no retry."""
    script, set_plan, log_entries = fake_apt
    set_plan({"phase": "update", "status": 0}, {"phase": "install", "status": 0})
    r = run_helper(fake_apt, ["iverilog", "tcl"])
    assert r.returncode == 0
    entries = log_entries()
    assert [e["phase"] for e in entries] == ["update", "install"]
    assert entries[1]["args"] == ["install", "-y", "iverilog", "tcl"], \
        "the install must receive the original package list verbatim"
    assert not any(e.get("unexpected") for e in entries)


def test_first_failed_install_is_retried_with_a_fresh_update(fake_apt):
    """Acceptance criterion 2: update ok, install exits 100 on a fetch failure
    -> record it, then a FRESH update+install succeeds."""
    script, set_plan, log_entries = fake_apt
    set_plan(
        {"phase": "update", "status": 0},
        {"phase": "install", "status": 100, "echo": FETCH_ERR},
        {"phase": "update", "status": 0},
        {"phase": "install", "status": 0},
    )
    r = run_helper(fake_apt, ["iverilog"])
    assert r.returncode == 0
    entries = log_entries()
    assert [e["phase"] for e in entries] == ["update", "install", "update", "install"], \
        "the retry must re-run update, not resume at install"
    assert entries[1]["args"] == entries[3]["args"] == ["install", "-y", "iverilog"]
    assert "Err:3 https://security.ubuntu.com/ubuntu" in r.stdout, \
        "apt's first-attempt diagnostics must be retained"
    assert "attempt=1 phase=install status=100" in r.stderr
    assert "attempt=2 phase=install status=0" in r.stderr


def test_repeated_install_failure_is_bounded_and_propagates_apt_status(fake_apt):
    """Acceptance criterion 3, persistence half: the second install failing
    ends the step nonzero with apt's own status -- and nothing runs after it."""
    script, set_plan, log_entries = fake_apt
    set_plan(
        {"phase": "update", "status": 0},
        {"phase": "install", "status": 100, "echo": FETCH_ERR},
        {"phase": "update", "status": 0},
        {"phase": "install", "status": 100, "echo": FETCH_ERR},
    )
    r = run_helper(fake_apt, ["iverilog", "verilator"])
    assert r.returncode == 100, "the final apt status must survive to the step"
    entries = log_entries()
    assert len(entries) == 4, "two attempts, then stop -- no third try"
    assert not any(e.get("unexpected") for e in entries), \
        "the wrapper must not run past its scripted bound"
    assert "giving up" in r.stderr


def test_a_failed_update_never_reaches_install(fake_apt):
    """Acceptance criterion 3: install only ever runs after a successful
    update within the same attempt -- on the first failure AND the retry."""
    script, set_plan, log_entries = fake_apt
    set_plan({"phase": "update", "status": 100}, {"phase": "update", "status": 100})
    r = run_helper(fake_apt, ["iverilog"])
    assert r.returncode == 100
    entries = log_entries()
    assert [e["phase"] for e in entries] == ["update", "update"], \
        "no install may follow a failed update"
    assert not any(e.get("unexpected") for e in entries)


def test_failed_update_then_successful_retry_succeeds(fake_apt):
    script, set_plan, log_entries = fake_apt
    set_plan(
        {"phase": "update", "status": 100},
        {"phase": "update", "status": 0},
        {"phase": "install", "status": 0},
    )
    r = run_helper(fake_apt, ["iverilog"])
    assert r.returncode == 0
    assert [e["phase"] for e in log_entries()] == ["update", "update", "install"]


def test_a_hanging_phase_is_killed_within_its_timeout(fake_apt):
    """An apt that never returns must not ride the job timeout: the wrapper's
    own clock ends the step with a distinct, visible status."""
    script, set_plan, log_entries = fake_apt
    set_plan({"phase": "update", "status": 0},
             {"phase": "install", "status": 0, "sleep": 60})
    started = time.monotonic()
    r = run_helper(fake_apt, ["iverilog"], "--phase-timeout", "2")
    elapsed = time.monotonic() - started
    assert r.returncode == cap.TIMEOUT_STATUS
    assert elapsed < 30, f"timeout path took {elapsed:.0f}s -- not bounded"
    assert "timeout" in r.stderr
    # the killed child's partial invocation must still be recorded
    assert [e["phase"] for e in log_entries()] == ["update", "install"]


FORKING_APT = textwrap.dedent(r"""
    import os, pathlib, subprocess, sys, time
    marker = pathlib.Path(os.environ["CI_APT_FORK_MARKER"])
    if sys.argv[1:2] == ["update"]:
        sys.exit(0)
    child = subprocess.Popen(
        [sys.executable, "-c",
         "import pathlib, sys, time; time.sleep(1.5); "
         "pathlib.Path(sys.argv[1]).write_text('outlived the step')",
         str(marker)])
    time.sleep(5)
    child.wait()
    sys.exit(0)
""")


def test_phase_timeout_kills_the_descendant_tree(tmp_path):
    """Regression for the Judge's scenario at 55874edf (#1064): with the phase
    run by a bare subprocess.run(timeout=...), the expiry reached only the
    direct child. The scripted apt returned success for update, and on
    install forked a child that wrote a marker 1.5 s later while its parent
    slept 5 s: the wrapper returned 124 in 0.57 s, the marker was absent at
    return and APPEARED afterwards -- a fetcher outliving the reported
    failure. The phase must run under timeout(1), whose default mode signals
    the whole process group: no descendant may survive the step."""
    standin = tmp_path / "forking-apt"
    standin.write_text(f"#!{PY}\n{FORKING_APT}")
    standin.chmod(0o755)
    marker = tmp_path / "outlived.marker"
    started = time.monotonic()
    r = subprocess.run(
        [PY, str(ROOT / "tools" / "ci_apt_install.py"), "--no-sudo",
         "--apt", str(standin), "--deadline", "30", "--phase-timeout",
         "0.5", "--delay", "0", "iverilog"],
        capture_output=True, text=True, timeout=60,
        env={**os.environ, "CI_APT_FORK_MARKER": str(marker)})
    elapsed = time.monotonic() - started
    assert r.returncode == cap.TIMEOUT_STATUS
    assert elapsed < 4, f"timeout path took {elapsed:.1f}s -- rode the 5s parent"
    assert "timeout" in r.stderr
    time.sleep(2.0)  # well past the forked child's 1.5 s write deadline
    assert not marker.exists(), \
        "a forked apt descendant outlived the reported timeout"


def test_deadline_bounds_the_whole_step(fake_apt):
    """`--deadline` caps the entire helper run: with none left, no phase
    starts, and the step ends nonzero inside its own limits."""
    script, set_plan, log_entries = fake_apt
    set_plan({"phase": "update", "status": 0})
    started = time.monotonic()
    r = run_helper(fake_apt, ["iverilog"], "--deadline", "0.001",
                   "--phase-timeout", "30")
    assert r.returncode == cap.TIMEOUT_STATUS
    assert time.monotonic() - started < 30
    assert log_entries() == [], "no apt invocation may start past the deadline"


def test_phase_timeout_is_clamped_to_the_remaining_deadline(fake_apt):
    """A generous per-phase timeout must not defeat a tight deadline."""
    script, set_plan, log_entries = fake_apt
    set_plan({"phase": "update", "status": 0, "sleep": 60})
    started = time.monotonic()
    r = run_helper(fake_apt, ["iverilog"], "--deadline", "3",
                   "--phase-timeout", "120")
    elapsed = time.monotonic() - started
    assert r.returncode == cap.TIMEOUT_STATUS
    assert elapsed < 30, f"clamp failed: ran {elapsed:.0f}s with deadline 3s"


def test_packages_are_required(fake_apt):
    r = subprocess.run(
        [PY, str(ROOT / "tools" / "ci_apt_install.py"), "--no-sudo"],
        capture_output=True, text=True, timeout=30)
    assert r.returncode != 0


def test_sudo_is_prefixed_by_default_and_the_chain_runs_under_timeout_1():
    """Production is `sudo timeout ... apt-get ...`: sudo stays first (the old
    chain's elevation), and the timeout(1) link sits INSIDE it so its
    process-group signal reaches root-owned apt children -- the non-root
    wrapper cannot signal them (#1064). Tests opt out with --no-sudo and get
    the same timeout link without the elevation."""
    assert cap.phase_command(True, "apt-get", "update", [], 420) == \
        ["sudo", "timeout", "--signal=TERM", "--kill-after=15s", "420s",
         "apt-get", "update"]
    assert cap.phase_command(True, "apt-get", "install", ["iverilog"], 420) == \
        ["sudo", "timeout", "--signal=TERM", "--kill-after=15s", "420s",
         "apt-get", "install", "-y", "iverilog"]
    assert cap.phase_command(False, "/tmp/fake", "install", ["iverilog"],
                             0.5) == \
        ["timeout", "--signal=TERM", "--kill-after=15s", "0.5s",
         "/tmp/fake", "install", "-y", "iverilog"]


# --- the workflows ship the helper: nine call sites, three files --------------
#
# Baseline measured at origin/main 4132b497 (issue #1062): nine
# `sudo apt-get update && sudo apt-get install -y <pkgs>` steps, none retried.
# Asserted per file as a multiset of package tuples, so a job renaming or
# reordering cannot hide a dropped call site.

EXPECTED_INVOCATIONS = {
    "arty.yml": [("iverilog", "tcl")],
    "nightly.yml": [("iverilog",), ("iverilog",)],
    "rungs.yml": [
        ("iverilog", "autoconf", "flex", "libfl-dev", "bison", "help2man"),
        ("iverilog",),
        ("iverilog",),
        ("iverilog",),
        ("iverilog", "verilator"),
        # Was pulse_audio.yml; main re-homed it into rtl-full (2d6da2f6).
        ("autoconf", "flex", "libfl-dev", "bison", "help2man"),
    ],
}


def _run_strings(path: pathlib.Path) -> list[str]:
    doc = yaml.safe_load(path.read_text())
    runs = []
    for job in (doc.get("jobs") or {}).values():
        for step in (job or {}).get("steps") or []:
            if isinstance(step, dict) and isinstance(step.get("run"), str):
                runs.append(step["run"])
    return runs


def test_all_nine_steps_call_the_helper_with_their_original_packages():
    total = 0
    for name, expected in EXPECTED_INVOCATIONS.items():
        calls = []
        for run in _run_strings(WF / name):
            tokens = run.split()
            for i, tok in enumerate(tokens):
                if tok == "tools/ci_apt_install.py":
                    assert tokens[0] in ("python3", "python"), run
                    calls.append(tuple(tokens[i + 1:]))
        assert sorted(calls) == sorted(expected), \
            f"{name}: helper call sites or packages drifted from the baseline"
        total += len(calls)
    assert total == 9


def test_no_workflow_step_invokes_apt_get_directly():
    """The one-shot chain must be gone everywhere -- not just where the flake
    was observed. Rule: search for the defect's class, not one call site."""
    for path in sorted(WF.glob("*.y*ml")):
        for run in _run_strings(path):
            assert "apt-get" not in run, f"{path.name}: {run}"
