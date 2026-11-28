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
