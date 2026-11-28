import subprocess
import sys
import hashlib
import json
import pathlib
import re
import pytest

import build_selected as build

COMMITTED = pathlib.Path(__file__).resolve().parents[1] / "docs/scorecard/results/M5A.json"
VERIFIED = "M5A path verified from SPI pins through the production voice and I2S pins"


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
    text = _committed_transcript()
    stamp = _stamp(text)
    _stored_simulation(monkeypatch, tmp_path, text)
    failing = ["show", f"{stamp}:rtl-sketch/synth_top.v"]
    check_output, calls = _git_that_fails(b"fatal: bad object " + stamp.encode(), failing)
    monkeypatch.setattr(build.subprocess, "check_output", check_output)
    assert build.main(["--out", str(tmp_path / "out")]) == 2
    assert [c for c, _ in calls] == [["rev-parse", "HEAD"], failing]
    assert calls[1][1] == {"cwd": tmp_path, "stderr": subprocess.PIPE}
    report = json.loads((tmp_path / "out" / "report.json").read_text())
    assert report["state"] == "REFUSED"
    assert report["source_commit"] == "deadbeef"
    assert f"git', 'show', '{stamp}:rtl-sketch/synth_top.v" in report["reason"]
    assert report["reason"].endswith("fatal: bad object " + stamp)
    assert [p.name for p in (tmp_path / "out").iterdir()] == ["report.json"]


def test_an_execution_error_at_the_source_commit_is_not_refused(tmp_path, monkeypatch):
    """The handler is not widened to do this: an exception it does not name is
    an execution error and leaves main() as it arrived (tools/refusal.py)."""
    monkeypatch.setattr(build.subprocess, "check_output", _raising(TypeError("genuine bug")))
    with pytest.raises(TypeError, match="genuine bug"):
        build.main(["--out", str(tmp_path)])
    assert not (tmp_path / "report.json").exists()

# ---- simulation_evidence(): one transcript gate, a checked build stamp -------

def _committed_transcript() -> str:
    record = json.loads(COMMITTED.read_text())
    return (COMMITTED.parent.parent / record["provenance"]["artefacts"]["spi_i2s"]
            .removeprefix("docs/scorecard/")).read_text()


def _without(prefix: str) -> str:
    return "".join(line for line in _committed_transcript().splitlines(keepends=True)
                   if not line.startswith(prefix))


def _stored_simulation(monkeypatch, tmp_path, text: str, *, decoded="keep"):
    """The committed M5A record on a scratch ROOT, its transcript replaced by `text`.

    The recorded commit's core answer `git show` with the scratch tree's own
    bytes, so the core-file comparison -- not what these tests are about --
    passes and only the transcript can refuse. Every other tool call is a
    non-zero exit, so `main()` cannot run past its evidence check."""
    record = json.loads(COMMITTED.read_text())
    content = text.encode()
    (tmp_path / "verification.txt").write_bytes(content)
    record["provenance"]["artefacts"]["spi_i2s"] = "verification.txt"
    record["provenance"]["inputs"]["verification:SPI-I2S"] = "sha256:" + hashlib.sha256(content).hexdigest()
    if decoded != "keep":
        if decoded is None:
            record["provenance"]["inputs"].pop("decoded:I2S")
        else:
            record["provenance"]["inputs"]["decoded:I2S"] = decoded
    (tmp_path / "docs/scorecard/results").mkdir(parents=True)
    (tmp_path / "docs/scorecard/results/M5A.json").write_text(json.dumps(record))
    core = tmp_path / "rtl-sketch/synth_top.v"
    core.parent.mkdir()
    core.write_bytes(b"module synth_top; endmodule\n")
    top = tmp_path / "fpga/rtl/ulx3s_top.v"
    top.parent.mkdir(parents=True)
    top.write_bytes(b"module ulx3s_top; endmodule\n")
    monkeypatch.setattr(build, "ROOT", tmp_path)
    monkeypatch.setattr(build, "sources", lambda: [top, core])

    def git_or_refuse(command, *args, **kwargs):
        if command[:2] == ["git", "show"]:
            return core.read_bytes()
        if command[:2] == ["git", "rev-parse"]:
            return "abc1234\n"
        raise subprocess.CalledProcessError(1, command)
    monkeypatch.setattr(build.subprocess, "check_output", git_or_refuse)


def _stamp(text: str) -> str:
    return re.search(r"built from ([0-9a-f]+)", text).group(1)


def test_the_committed_m5a_transcript_is_the_simulation_evidence(monkeypatch, tmp_path):
    """The precondition of every refusal below, on the record the repository
    committed: one clean run, bound to the WAV it digests, verifying the M5A
    path, built from a clean commit."""
    text = _committed_transcript()
    _stored_simulation(monkeypatch, tmp_path, text)
    evidence = build.simulation_evidence()
    assert evidence["commit"] == _stamp(text)
    assert evidence["core_files_and_roms_identical"] is True


@pytest.mark.parametrize("text,why", [
    pytest.param(_committed_transcript().replace(
        "compile defines: VOICE_OSC_2X, VOICE_FILTER_2X",
        "compile defines: VOICE_OSC_2X, INJECT_BUG_SPI_ADDR7"),
        "mutation runs", id="injected-control"),
    pytest.param(_committed_transcript().replace(
        "verify_synth_top: PASS --",
        "verify_synth_top: FAIL -- 68 writes reached the register port, 67 were sent\n"
        "verify_synth_top: PASS --", 1),
        "it contains FAIL --", id="pass-beside-fail"),
    pytest.param(_committed_transcript() + "verify_synth_top: REFUSED -- partial run\n",
                 "it contains REFUSED", id="pass-beside-refused"),
    pytest.param(_without("verify_synth_top: PASS --"), "no PASS -- line", id="no-pass-line"),
    pytest.param(_without("verify_synth_top: simulator backend"),
                 "omits the simulator backend", id="no-simulator"),
    pytest.param(_committed_transcript().replace(VERIFIED, "M5A path verified"), "M5A path", id="no-verified-line"),
    pytest.param(_committed_transcript().replace("built from " + _stamp(_committed_transcript()) + ",",
                                                 "built from " + _stamp(_committed_transcript()) + "-dirty,"),
                 "dirty tree", id="dirty-build-stamp"),
    pytest.param(_without("verify_synth_top: built from"), "which commit", id="no-build-stamp"),
])
def test_a_transcript_that_is_not_one_clean_m5a_pass_is_no_simulation_evidence(
        tmp_path, monkeypatch, text, why):
    """START RED (#1595). `simulation_evidence()` read no verdict, no verified
    line and no simulator from the transcript; it stripped `-dirty` off the
    build stamp, treating a simulation of a dirty tree as one of the clean
    commit, and an absent stamp was an `AttributeError` past the refusal
    handler (exit 1, not 2)."""
    assert text != _committed_transcript()
    _stored_simulation(monkeypatch, tmp_path, text)
    with pytest.raises(RuntimeError, match=why):
        build.simulation_evidence()


def test_the_transcript_must_bind_the_decoded_wav_the_record_names(tmp_path, monkeypatch):
    """START RED. `decoded:I2S` was on the record and never read."""
    _stored_simulation(monkeypatch, tmp_path, _committed_transcript(),
                       decoded="sha256:" + "0" * 64)
    with pytest.raises(RuntimeError, match="does not bind"):
        build.simulation_evidence()


def test_a_record_with_no_decoded_wav_digest_is_no_simulation_evidence(tmp_path, monkeypatch):
    """The scorer always records `decoded:I2S` (`tools/score_m5a_i2s.py`), so a
    record without it did not come from scoring the WAV, and there is nothing
    to bind the transcript to."""
    _stored_simulation(monkeypatch, tmp_path, _committed_transcript(), decoded=None)
    with pytest.raises(RuntimeError, match="no decoded I2S WAV digest"):
        build.simulation_evidence()


@pytest.mark.parametrize("stamp,why", [
    pytest.param("built from " + _stamp(_committed_transcript()) + "-dirty,", "dirty tree",
                 id="dirty-build-stamp"),
    pytest.param("built from ?,", "which commit", id="no-commit"),
    pytest.param("", "which commit", id="no-build-stamp"),
])
def test_an_unusable_build_stamp_refuses_the_build_as_no_evidence(tmp_path, monkeypatch, stamp, why):
    """Through `main()`: the stamp is refused before any tool runs, the report
    says `REFUSED` with the stamp's reason, and the exit status is 2."""
    text = _committed_transcript().replace("built from " + _stamp(_committed_transcript()) + ",", stamp)
    assert text != _committed_transcript()
    _stored_simulation(monkeypatch, tmp_path, text)
    out = tmp_path / "out"
    assert build.main(["--out", str(out)]) == 2
    report = json.loads((out / "report.json").read_text())
    assert report["state"] == "REFUSED"
    assert why in report["reason"]


def test_a_clean_build_stamp_is_cleared_so_the_refusal_above_is_the_stamps(tmp_path, monkeypatch):
    """The control for the test above: on the committed transcript
    `simulation_evidence()` passes, and `main()` is then refused by the next
    tool (`yosys -V`, made to fail here) -- so a stamp refusal can only be the
    stamp."""
    _stored_simulation(monkeypatch, tmp_path, _committed_transcript())
    out = tmp_path / "out"
    assert build.main(["--out", str(out)]) == 2
    report = json.loads((out / "report.json").read_text())
    assert report["state"] == "REFUSED"
    assert "yosys" in report["reason"] and "simulation_evidence" in report


# ---- a scorecard record that cannot be read is REFUSED, not a traceback (#1621)

def _record_is(monkeypatch, tmp_path, record):
    """The scratch tree of the tests above with its M5A.json replaced by `record`:
    the transcript, the core files and every tool are as they are there."""
    _stored_simulation(monkeypatch, tmp_path, _committed_transcript())
    (tmp_path / "docs/scorecard/results/M5A.json").write_text(json.dumps(record))


@pytest.mark.parametrize("record,why", [
    pytest.param({}, "'provenance'", id="empty"),
    pytest.param({"provenance": None}, "'provenance'", id="null-key"),
    pytest.param([], "not a JSON object", id="list"),
    pytest.param(5, "not a JSON object", id="number"),
    pytest.param(None, "not a JSON object", id="null"),
])
def test_a_scorecard_record_lacking_provenance_refuses_the_build(monkeypatch, tmp_path, record, why):
    """Through `main()`: `record["provenance"]` was read before anything checked
    the record, so a partial M5A.json was a `KeyError` traceback past the
    handler (exit 1, not 2). The handler catches RuntimeError, which is this
    tool's refusal on this path."""
    _record_is(monkeypatch, tmp_path, record)
    out = tmp_path / "out"
    assert build.main(["--out", str(out)]) == 2
    report = json.loads((out / "report.json").read_text())
    assert report["state"] == "REFUSED" and why in report["reason"], report["reason"]


def test_a_scorecard_record_missing_exactly_provenance_names_only_it(monkeypatch, tmp_path):
    _record_is(monkeypatch, tmp_path, json.loads(COMMITTED.read_text()))
    path = tmp_path / "docs/scorecard/results/M5A.json"
    record = json.loads(path.read_text())
    record.pop("provenance")
    path.write_text(json.dumps(record))
    out = tmp_path / "out"
    assert build.main(["--out", str(out)]) == 2
    report = json.loads((out / "report.json").read_text())
    assert report["state"] == "REFUSED"
    assert re.findall(r"'[^']+'", report["reason"]) == ["'provenance'"], report["reason"]


def test_the_committed_scorecard_record_carries_provenance():
    # the guard above must be satisfiable against the record that ships
    assert json.loads(COMMITTED.read_text())["provenance"] is not None
