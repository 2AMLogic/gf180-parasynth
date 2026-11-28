"""Timing evidence must cover the actual PLL core clock and fit the device,
and a refused stored simulation is a NO-VERDICT publication, never a traceback."""
import copy
import hashlib
import json
import math
from pathlib import Path
import runpy
import subprocess
import sys
import pytest

import publish_selected as pub
import build_selected
import publish_selected
from publish_selected import timing_summary

ROOT = publish_selected.ROOT
WORKFLOW = "https://example.invalid/runs/1"
REFUSAL = "synth_top.v differs from recorded simulation 36137df"
TRANSCRIPT = "docs/scorecard/mono-m5a-miniv3/saw-cutoff-20khz-i2s.txt"


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


# ---- a build report or toolchain record that cannot be read is REFUSED (#1621)

TOOLCHAIN_KEYS = ("sha256", "release")
REPORT_KEYS = ("configuration", "device", "source_commit", "source_sha256",
               "simulation_evidence", "stages", "package", "state")
KEYS = [("toolchain", k) for k in TOOLCHAIN_KEYS] + [("report", k) for k in REPORT_KEYS]
URL = "https://example.invalid/workflow"


def _records():
    """Both records with every key publish() reads and no valid value: the guard
    at the first read refuses a missing key before any value is checked."""
    toolchain = {"sha256": pub.toolchain_pin.SHA256, "release": pub.toolchain_pin.VERSION}
    return toolchain, {k: "x" for k in REPORT_KEYS}


def _record_artifact(tmp_path, toolchain, report):
    art = tmp_path / "artifact"
    art.mkdir()
    (art / "report.json").write_text(json.dumps(report))
    (art / "toolchain.json").write_text(json.dumps(toolchain))
    return art


def _damaged(which, key, how=None):
    toolchain, report = _records()
    record = toolchain if which == "toolchain" else report
    if how == "missing":
        record.pop(key)
    elif how == "null":
        record[key] = None
    return toolchain, report


@pytest.mark.parametrize("which,key", KEYS, ids=[f"{w}:{k}" for w, k in KEYS])
@pytest.mark.parametrize("how", ["missing", "null"])
def test_a_record_lacking_a_key_publish_reads_is_refused_naming_it(tmp_path, which, key, how):
    """`report[...]` and `toolchain[...]` were read with nothing checking either
    record, so a partial one was a `KeyError` traceback (exit 1) where the
    publisher's own refusals are ValueError."""
    toolchain, report = _damaged(which, key, how)
    with pytest.raises(ValueError, match=repr(key)):
        pub.publish(_record_artifact(tmp_path, toolchain, report), tmp_path / "out", URL)


@pytest.mark.parametrize("which,key", KEYS, ids=[f"{w}:{k}" for w, k in KEYS])
def test_the_entry_point_reports_a_record_lacking_a_key_as_refused(tmp_path, capsys, which, key):
    toolchain, report = _damaged(which, key, "missing")
    art = _record_artifact(tmp_path, toolchain, report)
    rc = pub.main([str(art), "--workflow-url", URL, "--out", str(tmp_path / "out")])
    out = capsys.readouterr().out
    assert rc == 2 and "REFUSED" in out and repr(key) in out, out
    assert not (tmp_path / "out").exists()


@pytest.mark.parametrize("which", ["toolchain", "report"])
@pytest.mark.parametrize("whole", [{}, [], 5, None], ids=["empty", "list", "number", "null"])
def test_a_record_that_carries_nothing_readable_is_refused(tmp_path, capsys, which, whole):
    toolchain, report = _damaged(which, None)
    if which == "toolchain":
        toolchain = whole
    else:
        report = whole
    art = _record_artifact(tmp_path, toolchain, report)
    with pytest.raises(ValueError):
        pub.publish(art, tmp_path / "out", URL)
    rc = pub.main([str(art), "--workflow-url", URL, "--out", str(tmp_path / "out")])
    out = capsys.readouterr().out
    assert rc == 2 and "REFUSED" in out, out
    if whole == {}:
        for key in (TOOLCHAIN_KEYS if which == "toolchain" else REPORT_KEYS):
            assert repr(key) in out, (key, out)
    assert not (tmp_path / "out").exists()


def test_the_committed_publication_records_carry_the_keys_publish_reads():
    # the guards above must be satisfiable against the records that ship
    d = Path(__file__).parent / "reports/selected/linux-85f"
    report = json.loads((d / "report.json").read_text())
    toolchain = json.loads((d / "toolchain.json").read_text())
    assert all(report[k] is not None for k in REPORT_KEYS)
    assert all(toolchain[k] is not None for k in TOOLCHAIN_KEYS)


def git_bytes(spec):
    return subprocess.check_output(["git", "show", spec], cwd=ROOT)


def built_artifact(tmp_path, state="BUILT"):
    """A build of THIS commit, laid out like the CI download: the build
    directory beside build/oss-cad-pinned/toolchain.json. Its recorded hashes
    are the committed bytes, which is what a clean build of HEAD would record,
    so every check before the stored-simulation call passes. `state` leaves
    what build_selected.main() leaves for it: a refused build runs no stage,
    and a failed route has no config and never reaches the bitstream stage."""
    artifact = tmp_path / "download"
    build, pinned = artifact / "fpga-selected-85k", artifact / "oss-cad-pinned"
    build.mkdir(parents=True)
    pinned.mkdir()
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    sources = {str(p.relative_to(ROOT)): hashlib.sha256(git_bytes(f"{commit}:{p.relative_to(ROOT)}")).hexdigest()
               for p in build_selected.sources()}
    stages = {}
    for name, filename in (("synthesis", "ecp5.json"), ("place_route", "ecp5.config"), ("bitstream", "ecp5.bit")):
        (build / filename).write_bytes(f"{name} of {commit}\n".encode())
        stages[name] = {"state": "PASS", "exit_code": 0, "artifact": str(build / filename),
                        "artifact_sha256": hashlib.sha256((build / filename).read_bytes()).hexdigest()}
    (build / "timing.json").write_text(json.dumps(valid_report()))
    evidence = {"commit": commit, "transcript": TRANSCRIPT,
                "sha256": hashlib.sha256(git_bytes(f"{commit}:{TRANSCRIPT}")).hexdigest(),
                "core_files_and_roms_identical": True,
                "scope": "reused complete-phrase SPI-to-I2S core simulation; FPGA wrapper is not covered"}
    report = {"configuration": {"OSC2X": 1, "FILTER2X": 1, "pulse_duty_percent": 47.90},
              "source_commit": commit, "source_sha256": sources, "device": "85k", "package": "CABGA381",
              "stages": stages, "bitstream_built": True, "hardware_playback_tested": False,
              "state": "BUILT", "simulation_evidence": evidence}
    if state == "REFUSED":
        report.update(state="REFUSED", reason=REFUSAL, stages={}, bitstream_built=False)
        for path in build.iterdir():
            path.unlink()
    if state == "FAIL":
        report.update(state="FAIL", bitstream_built=False)
        report["stages"]["place_route"].update(state="FAIL", exit_code=1, artifact_sha256=None)
        del report["stages"]["bitstream"]
        for filename in ("ecp5.config", "ecp5.bit"):
            (build / filename).unlink()
    (build / "report.json").write_text(json.dumps(report))
    (pinned / "toolchain.json").write_text(json.dumps(
        {"sha256": publish_selected.toolchain_pin.SHA256, "release": publish_selected.toolchain_pin.VERSION}))
    return artifact, evidence


def raising(exc):
    def raiser(*_):
        raise exc
    return raiser


@pytest.mark.parametrize("report_state", ["BUILT", "REFUSED", "FAIL"])
def test_refused_stored_simulation_is_a_no_verdict_publication_not_a_traceback(tmp_path, monkeypatch, report_state):
    artifact, _ = built_artifact(tmp_path, state=report_state)
    monkeypatch.setattr(build_selected, "simulation_evidence", raising(RuntimeError(REFUSAL)))
    out = tmp_path / "publication"
    summary = publish_selected.publish(artifact, out, WORKFLOW)
    assert summary["state"] == "NO-VERDICT"
    assert summary["reason"] == REFUSAL
    assert summary["bitstream_qualified"] is False
    assert summary["simulation_evidence"] is None
    assert (out / "report.json").is_file()
    assert json.loads((out / "publication.json").read_text()) == summary


def test_cli_exits_2_without_a_traceback_when_the_stored_simulation_is_refused(tmp_path, monkeypatch, capsys):
    artifact, _ = built_artifact(tmp_path)
    monkeypatch.setattr(build_selected, "simulation_evidence", raising(RuntimeError(REFUSAL)))
    out = tmp_path / "publication"
    monkeypatch.setattr(sys, "argv", ["publish_selected.py", str(artifact), "--workflow-url", WORKFLOW, "--out", str(out)])
    with pytest.raises(SystemExit) as stopped:
        runpy.run_path(publish_selected.__file__, run_name="__main__")
    assert stopped.value.code == 2
    assert json.loads(capsys.readouterr().out) == json.loads((out / "publication.json").read_text())


@pytest.mark.parametrize("site, bug", [
    pytest.param("timing_summary", RuntimeError("genuine bug"), id="runtime-error-after-the-call"),
    pytest.param("simulation_evidence", TypeError("genuine bug"), id="other-exception-at-the-call")])
def test_a_genuine_bug_is_not_masked_as_a_refusal(tmp_path, monkeypatch, site, bug):
    artifact, evidence = built_artifact(tmp_path)
    monkeypatch.setattr(build_selected, "simulation_evidence", lambda: evidence)
    monkeypatch.setattr(publish_selected if site == "timing_summary" else build_selected, site, raising(bug))
    with pytest.raises(type(bug), match="genuine bug"):
        publish_selected.publish(artifact, tmp_path / "publication", WORKFLOW)
    assert not (tmp_path / "publication").exists()


@pytest.mark.parametrize("historical", [False, True])
def test_the_same_build_qualifies_when_its_stored_simulation_is_current(tmp_path, monkeypatch, historical):
    artifact, evidence = built_artifact(tmp_path)
    consulted = []
    monkeypatch.setattr(build_selected, "simulation_evidence", lambda: consulted.append(1) or evidence)
    summary = publish_selected.publish(artifact, tmp_path / "publication", WORKFLOW, historical=historical)
    assert (summary["state"], summary["bitstream_qualified"]) == ("BUILT-TIMING-PASS", True)
    assert "reason" not in summary
    assert bool(consulted) is not historical


# ----------------------- a recorded commit that git cannot show (#1690) ----
TOP, LPF, SIM_TRANSCRIPT = "rtl-sketch/synth_top.v", "fpga/boards/ulx3s.lpf", "docs/sim.txt"
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
              "source_sha256": {TOP: hashlib.sha256(_recorded(TOP)).hexdigest()},
              # publish() refuses a report lacking any key it reads (#1621),
              # before its first `git show`; none of these is read until after.
              "simulation_evidence": {}, "stages": {}, "state": "BUILT"}
    if with_simulation:
        report["simulation_evidence"] = {
            "commit": SIM_COMMIT, "transcript": SIM_TRANSCRIPT,
            "sha256": hashlib.sha256(_recorded(SIM_TRANSCRIPT)).hexdigest()}
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
    pytest.param(True, lambda commit, name: name == SIM_TRANSCRIPT,
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
