import sys
import hashlib
import json
import pytest

import build_selected as build


def test_nonzero_tool_with_stale_artifact_cannot_pass(tmp_path):
    artifact = tmp_path / "old.bit"
    artifact.write_bytes(b"old successful build")
    result = build.run_stage([sys.executable, "-c", "raise SystemExit(1)"],
                             tmp_path, tmp_path / "job.log", artifact)
    assert result["state"] == "FAIL"
    assert result["exit_code"] == 1
    assert result["artifact_sha256"] is None
    assert not artifact.exists()


def test_success_without_new_output_cannot_pass(tmp_path):
    result = build.run_stage([sys.executable, "-c", "pass"], tmp_path,
                             tmp_path / "job.log", tmp_path / "missing.bit")
    assert result["state"] == "FAIL"


def test_missing_tool_is_refused(tmp_path):
    result = build.run_stage([str(tmp_path / "missing-tool")], tmp_path,
                             tmp_path / "job.log", tmp_path / "missing.bit")
    assert result["state"] == "REFUSED"


def test_resume_requires_exact_sources_configuration_and_netlist(tmp_path):
    artifact = tmp_path / "synth.json"
    artifact.write_bytes(b"frozen netlist")
    report = tmp_path / "report.json"
    prior = {"configuration": {"OSC2X": 1, "FILTER2X": 1, "pulse_duty_percent": 47.9},
             "source_sha256": {"core.v": "source-hash"},
             "stages": {"synthesis": {"state": "PASS", "exit_code": 0,
                        "artifact": str(artifact),
                        "artifact_sha256": hashlib.sha256(artifact.read_bytes()).hexdigest()}}}
    report.write_text(json.dumps(prior))
    assert build.reuse_synthesis(report, prior)["artifact"] == str(artifact)
    with pytest.raises(RuntimeError, match="source"):
        build.reuse_synthesis(report, {**prior, "source_sha256": {"core.v": "changed"}})
    with pytest.raises(RuntimeError, match="configuration"):
        build.reuse_synthesis(report, {**prior, "configuration": {"OSC2X": 0}})
    artifact.write_bytes(b"stale netlist")
    with pytest.raises(RuntimeError, match="netlist"):
        build.reuse_synthesis(report, prior)
