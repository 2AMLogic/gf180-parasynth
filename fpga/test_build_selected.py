import sys

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
