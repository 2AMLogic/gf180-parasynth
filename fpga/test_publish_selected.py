"""Timing evidence must cover the actual PLL core clock and fit the device."""
import copy
import hashlib
import json
import math
from pathlib import Path
import runpy
import subprocess
import sys
import pytest

import publish_selected
from publish_selected import timing_summary


def valid_report():
    return {"fmax": {"clk_core": {"constraint": 725 / 59, "achieved": 15.0}},
            "utilization": {"MULT18X18D": {"used": 104, "available": 156},
                            "TRELLIS_FF": {"used": 14008, "available": 83640}},
            "critical_paths": [{"from": "clk_core", "to": "clk_core"}]}


def test_valid_timing_report():
    result = timing_summary(valid_report())
    assert result["state"] == "PASS"
    assert result["core_constraint_mhz"] == pytest.approx(725 / 59)


def test_real_pinned_linux_timing_report():
    fixture = Path(__file__).parent / "reports/selected/linux-85f/timing.json"
    report = json.loads(fixture.read_text())
    result = timing_summary(report)
    assert result["state"] == "PASS"
    assert result["clocks"]["$glbnet$clk_core"]["achieved"] == pytest.approx(12.8869304657)


@pytest.mark.parametrize("wrong", [12.288, 12.287, 12.289, 25., float("nan")])
def test_nearby_but_wrong_core_clocks_refuse(wrong):
    report = valid_report()
    report["fmax"]["clk_core"]["constraint"] = wrong
    with pytest.raises(ValueError, match="clock constraint"):
        timing_summary(report)


@pytest.mark.parametrize("mutation", ["absent-clock", "wrong-clock", "missed-timing", "nan", "overfull", "no-paths"])
def test_wrong_or_incomplete_report_refuses(mutation):
    report = copy.deepcopy(valid_report())
    if mutation == "absent-clock": report["fmax"] = {}
    if mutation == "wrong-clock": report["fmax"]["clk_core"]["constraint"] = 25
    if mutation == "missed-timing": report["fmax"]["clk_core"]["achieved"] = 12
    if mutation == "nan": report["fmax"]["clk_core"]["achieved"] = math.nan
    if mutation == "overfull": report["utilization"]["MULT18X18D"]["available"] = 28
    if mutation == "no-paths": report["critical_paths"] = []
    with pytest.raises(ValueError): timing_summary(report)


# ----------------------- a recorded commit that git cannot show (#1690) ----
ROOT = publish_selected.ROOT
WORKFLOW = "https://example.invalid/runs/1"
TOP, LPF, TRANSCRIPT = "rtl-sketch/synth_top.v", "fpga/boards/ulx3s.lpf", "docs/sim.txt"
COMMIT, SIM_COMMIT = "1" * 40, "2" * 40


def _recorded(name):
    """What `git show <commit>:<name>` answers: the bytes in this checkout
    where it has them -- the non-historical guards compare the record with the
    checkout -- and fixed bytes otherwise."""
    path = ROOT / name
    return path.read_bytes() if path.is_file() else f"built {name}\n".encode()


def _artifact(tmp_path, *, with_simulation=False):
    """The least a publication reads before its first `git show`: the build
    report, the selected configuration on the 85k with true source digests,
    beside the pinned toolchain record. `with_simulation` adds what only the
    --historical path reads."""
    build, pinned = tmp_path / "build", tmp_path / "oss-cad-pinned"
    build.mkdir()
    pinned.mkdir()
    report = {"configuration": {"OSC2X": 1, "FILTER2X": 1, "pulse_duty_percent": 47.90},
              "source_commit": COMMIT, "device": "85k", "package": "CABGA381",
              "source_sha256": {TOP: hashlib.sha256(_recorded(TOP)).hexdigest()}}
    if with_simulation:
        report["simulation_evidence"] = {
            "commit": SIM_COMMIT, "transcript": TRANSCRIPT,
            "sha256": hashlib.sha256(_recorded(TRANSCRIPT)).hexdigest()}
    (build / "report.json").write_text(json.dumps(report))
    (pinned / "toolchain.json").write_text(json.dumps(
        {"sha256": publish_selected.toolchain_pin.SHA256,
         "release": publish_selected.toolchain_pin.VERSION}))
    return tmp_path


UNREADABLE = "fatal: invalid object name {name}\n"


def _git_show(fail):
    """check_output replacement: `git show <commit>:<name>` answers the recorded
    bytes, and fails where `fail(commit, name)` says, as git does for an object
    the clone does not hold -- answering as the call makes it: stderr is
    attached to the exception only when the call captured it, and otherwise goes
    to this process's stderr, as an inherited one would. Returns the replacement
    and the log of (command, keywords) it saw."""
    calls = []

    def check_output(cmd, **kwargs):
        assert cmd[:2] == ["git", "show"], cmd
        calls.append((cmd, kwargs))
        commit, name = cmd[2].split(":", 1)
        if not fail(commit, name):
            return _recorded(name)
        captured = kwargs.get("stderr") == subprocess.PIPE
        if not captured:
            sys.stderr.write(UNREADABLE.format(name=name))
        raise subprocess.CalledProcessError(
            128, cmd, stderr=UNREADABLE.format(name=name).encode() if captured else None)
    return check_output, calls


def _argv(monkeypatch, artifact, out, *flags):
    monkeypatch.setattr(sys, "path", list(sys.path))
    monkeypatch.setattr(sys, "argv", ["publish_selected.py", str(artifact),
                                      "--workflow-url", WORKFLOW, "--out", str(out), *flags])


def _cli(monkeypatch, artifact, out, *flags):
    """The shipped __main__ block, in process: its exit status. A failure the
    block does not render leaves as the exception itself, which a real
    invocation prints as a traceback."""
    _argv(monkeypatch, artifact, out, *flags)
    with pytest.raises(SystemExit) as stopped:
        runpy.run_path(publish_selected.__file__, run_name="__main__")
    return stopped.value.code


_SITES = [
    pytest.param(False, lambda commit, name: name == TOP,
                 id="source-75"),
    pytest.param(False, lambda commit, name: name == LPF,
                 id="build-input-87"),
    pytest.param(True, lambda commit, name: name == TRANSCRIPT,
                 id="transcript-93"),
    pytest.param(True, lambda commit, name: commit == SIM_COMMIT and name.startswith("rtl-sketch/"),
                 id="simulated-core-98"),
]


@pytest.mark.parametrize("historical, fail", _SITES)
def test_a_recorded_commit_git_cannot_show_is_refused(
        tmp_path, monkeypatch, capsys, historical, fail):
    """publish()'s four `git show` calls had no handler for the class a failed
    git raises, so a commit the clone does not hold left the CLI as a traceback
    at exit 1. The recorded commit being readable is a precondition, so REFUSED,
    exit 2: the command and git's own reason on stderr, nothing published. The
    publisher runs by hand on a downloaded artifact (fpga/SELECTED.md), in
    whatever checkout the operator has. The calls capture stderr (bytes: no
    text=True), or git's reason would reach the terminal beside the REFUSED
    line instead of inside it."""
    artifact = _artifact(tmp_path, with_simulation=historical)
    out = tmp_path / "publication"
    check_output, calls = _git_show(fail)
    monkeypatch.setattr(subprocess, "check_output", check_output)
    assert _cli(monkeypatch, artifact, out, *(["--historical"] if historical else [])) == 2
    assert calls and all(kwargs.get("stderr") == subprocess.PIPE for _, kwargs in calls)
    printed = capsys.readouterr()
    assert printed.out == ""
    assert printed.err.startswith("REFUSED: Command '['git', 'show', '")
    assert "fatal: invalid object name" in printed.err
    assert "Traceback" not in printed.err
    assert not out.exists()


def test_an_execution_error_in_publish_is_not_masked_as_a_refusal(tmp_path, monkeypatch):
    """The handler names the class a failed child raises and nothing wider: an
    exception it does not name is an execution error and leaves the CLI as it
    arrived (tools/refusal.py)."""
    artifact = _artifact(tmp_path)
    out = tmp_path / "publication"

    def bug(*_args, **_kwargs):
        raise TypeError("genuine bug")
    monkeypatch.setattr(subprocess, "check_output", bug)
    _argv(monkeypatch, artifact, out)
    with pytest.raises(TypeError, match="genuine bug"):
        runpy.run_path(publish_selected.__file__, run_name="__main__")
    assert not out.exists()
