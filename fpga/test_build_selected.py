import subprocess
import sys
import hashlib
import json
import pytest

import build_selected as build


def _raising(exc):
    def raiser(*_args, **_kwargs):
        raise exc
    return raiser


def _git_that_fails(said, failing):
    """check_output replacement: the git command `failing` fails; `git rev-parse
    HEAD` answers if it is not that one; any other child (yosys, nextpnr) is one
    this test did not expect. A failing git answers as the call makes it --
    stderr is attached to the exception only when the call captured it, and
    otherwise goes to this process's stderr, as an inherited one would. Returns
    the replacement and the log of (command, keywords) it saw."""
    calls = []

    def check_output(cmd, **kwargs):
        assert cmd[0] == "git" and cmd[1:] in (failing, ["rev-parse", "HEAD"]), \
            f"unexpected child {cmd}"
        calls.append((cmd[1:], kwargs))
        if cmd[1:] != failing:
            return "deadbeef\n"
        captured = kwargs.get("stderr") == subprocess.PIPE
        if not captured and said:
            sys.stderr.write(said.decode() if isinstance(said, bytes) else said)
        raise subprocess.CalledProcessError(128, cmd, stderr=said if captured else None)
    return check_output, calls


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


# ------------------------------------------- a git that fails (#1690) ----
_Said = ["fatal: not a git repository", b"fatal: not a git repository", None]


@pytest.mark.parametrize("said", _Said, ids=["str", "bytes", "nothing"])
def test_git_failure_at_the_source_commit_is_refused(tmp_path, monkeypatch, capsys, said):
    """`git rev-parse HEAD` ran in the report literal, outside main()'s handler,
    so a git that failed left main() as a CalledProcessError traceback at exit 1
    and wrote no report: an earlier BUILT report in --out stayed on disk. It is
    a precondition that was not met -- the commit cannot be read -- so REFUSED,
    exit 2, the earlier record replaced. The call captures stderr with
    text=True, so git's own reason is a str on the exception; with nothing
    captured there is nothing to carry."""
    report_path = tmp_path / "report.json"
    report_path.write_text(json.dumps({"state": "BUILT", "bitstream_built": True}))
    check_output, calls = _git_that_fails(said, ["rev-parse", "HEAD"])
    monkeypatch.setattr(build.subprocess, "check_output", check_output)
    assert build.main(["--out", str(tmp_path)]) == 2
    assert [c for c, _ in calls] == [["rev-parse", "HEAD"]]
    assert (calls[0][1].get("text"), calls[0][1].get("stderr")) == (True, subprocess.PIPE)
    report = json.loads(report_path.read_text())
    assert report["state"] == "REFUSED"
    assert "git', 'rev-parse', 'HEAD" in report["reason"]
    assert ("fatal: not a git repository" in report["reason"]) == (said is not None)
    assert capsys.readouterr().out.strip() == report["reason"]
    assert report["bitstream_built"] is False and report["stages"] == {}
    assert [p.name for p in tmp_path.iterdir()] == ["report.json"]


def test_git_failure_under_the_handler_is_refused_with_what_git_said(tmp_path, monkeypatch):
    """The row's fix moves the rev-parse call under the existing handler, so
    what that handler does with a failed git is pinned on the call that was
    already under it: simulation_evidence()'s `git show`, run for real on a
    stored-simulation record built for the test. REFUSED, and the reason carries
    git's stderr -- bytes, because the call has no text=True -- which str() of
    the exception leaves out."""
    (tmp_path / "docs/scorecard/results").mkdir(parents=True)
    transcript = tmp_path / "transcript.txt"
    transcript.write_bytes(b"SPI-to-I2S transcript, built from abc123\n")
    (tmp_path / "docs/scorecard/results/M5A.json").write_text(json.dumps({"provenance": {
        "artefacts": {"spi_i2s": str(transcript)},
        "inputs": {"verification:SPI-I2S": "sha256:" + hashlib.sha256(
            transcript.read_bytes()).hexdigest()},
        "config": {"oscillator_oversample_2x": True, "filter_rate_converted": True}}}))
    (tmp_path / "top.v").write_bytes(b"module top;\n")
    core = tmp_path / "core.v"
    core.write_bytes(b"module core;\n")
    monkeypatch.setattr(build, "ROOT", tmp_path)
    monkeypatch.setattr(build, "sources", lambda: [tmp_path / "top.v", core])
    check_output, calls = _git_that_fails(b"fatal: bad object abc123", ["show", "abc123:core.v"])
    monkeypatch.setattr(build.subprocess, "check_output", check_output)
    assert build.main(["--out", str(tmp_path / "out")]) == 2
    assert [c for c, _ in calls] == [["rev-parse", "HEAD"], ["show", "abc123:core.v"]]
    assert calls[1][1] == {"cwd": tmp_path, "stderr": subprocess.PIPE}
    report = json.loads((tmp_path / "out" / "report.json").read_text())
    assert report["state"] == "REFUSED"
    assert report["source_commit"] == "deadbeef"
    assert "git', 'show', 'abc123:core.v" in report["reason"]
    assert report["reason"].endswith("fatal: bad object abc123")
    assert [p.name for p in (tmp_path / "out").iterdir()] == ["report.json"]


def test_an_execution_error_at_the_source_commit_is_not_refused(tmp_path, monkeypatch):
    """The handler is not widened to do this: an exception it does not name is
    an execution error and leaves main() as it arrived (tools/refusal.py)."""
    monkeypatch.setattr(build.subprocess, "check_output", _raising(TypeError("genuine bug")))
    with pytest.raises(TypeError, match="genuine bug"):
        build.main(["--out", str(tmp_path)])
    assert not (tmp_path / "report.json").exists()
