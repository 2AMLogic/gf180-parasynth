"""Timing evidence must cover the actual PLL core clock and fit the device,
and a publication whose precondition is not met is REFUSED -- exit 2, the
reason on stderr, nothing written -- never a traceback at exit 1 (#1663); a
refused stored simulation is a NO-VERDICT publication, never a traceback."""
import copy
import hashlib
import json
import math
from pathlib import Path
import runpy
import shutil
import subprocess
import sys
from types import SimpleNamespace
import pytest

import build_selected
import publish_selected
from publish_selected import timing_summary

ROOT = publish_selected.ROOT
WORKFLOW = "https://example.invalid/runs/1"
TOP = "rtl-sketch/synth_top.v"
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


BUNDLE = ROOT / "fpga/reports/selected/linux-85f"
EMPTY = "publication directory must be empty; refuse stale artifacts"
HINT = "; use --historical for the recorded build"


def git(root, *args):
    return subprocess.check_output(["git", "-c", "user.name=t", "-c", "user.email=t@t", *args],
                                   cwd=root, stderr=subprocess.DEVNULL).decode().strip()


def committed(root, spec):
    return subprocess.check_output(["git", "show", spec], cwd=root)


@pytest.fixture
def checkout(tmp_path):
    """This tree's build inputs at `head`, an earlier `prior` commit whose
    synth_top.v differs, and the module under test copied to fpga/.

    The guards compare the checkout against the recorded build, and a test
    cannot drift a file in the shared worktree. The CLI under test therefore
    runs from a byte-identical copy of the module inside a disposable git repo:
    its ROOT is that repo, every guard reads real bytes and real git, and a
    test drifts a file by writing it."""
    root = tmp_path / "repo"
    for path in build_selected.sources() + [ROOT / "fpga/boards/ulx3s.lpf",
                                            *sorted((ROOT / "rtl-sketch").glob("*.hex"))]:
        dest = root / path.relative_to(ROOT)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(path.read_bytes())
    (root / "docs").mkdir()
    (root / "docs" / "sim.txt").write_text("built from the recorded simulation\n")
    module = root / "fpga" / "publish_selected.py"
    shutil.copyfile(publish_selected.__file__, module)
    top = root / TOP
    built = top.read_bytes()
    top.write_bytes(built + b"// the earlier core\n")
    git(root, "init", "-q")
    git(root, "add", "-A", "-f")
    git(root, "commit", "-q", "-m", "prior")
    prior = git(root, "rev-parse", "HEAD")
    top.write_bytes(built)
    git(root, "commit", "-q", "-am", "head")
    return SimpleNamespace(root=root, prior=prior, head=git(root, "rev-parse", "HEAD"), module=module)


def download(co):
    """A build of `co.head` laid out like the CI download: the build directory
    beside oss-cad-pinned/toolchain.json, every recorded hash true, and the
    simulation evidence naming the build commit itself."""
    artifact = co.root / "download"
    build, pinned = artifact / "fpga-selected-85k", artifact / "oss-cad-pinned"
    build.mkdir(parents=True)
    pinned.mkdir()
    sources = {str(p.relative_to(ROOT)): hashlib.sha256(
        committed(co.root, f"{co.head}:{p.relative_to(ROOT)}")).hexdigest()
        for p in build_selected.sources()}
    stages = {}
    for name, filename in (("synthesis", "ecp5.json"), ("place_route", "ecp5.config"),
                           ("bitstream", "ecp5.bit")):
        (build / filename).write_bytes(f"{name} of {co.head}\n".encode())
        stages[name] = {"state": "PASS", "exit_code": 0, "artifact": str(build / filename),
                        "artifact_sha256": hashlib.sha256((build / filename).read_bytes()).hexdigest()}
    (build / "timing.json").write_text(json.dumps(valid_report()))
    evidence = {"commit": co.head, "transcript": "docs/sim.txt",
                "sha256": hashlib.sha256(committed(co.root, f"{co.head}:docs/sim.txt")).hexdigest(),
                "core_files_and_roms_identical": True,
                "scope": "reused complete-phrase SPI-to-I2S core simulation; FPGA wrapper is not covered"}
    (build / "report.json").write_text(json.dumps(
        {"configuration": {"OSC2X": 1, "FILTER2X": 1, "pulse_duty_percent": 47.90},
         "source_commit": co.head, "source_sha256": sources, "device": "85k", "package": "CABGA381",
         "stages": stages, "bitstream_built": True, "hardware_playback_tested": False,
         "state": "BUILT", "simulation_evidence": evidence}))
    (pinned / "toolchain.json").write_text(json.dumps(
        {"sha256": publish_selected.toolchain_pin.SHA256, "release": publish_selected.toolchain_pin.VERSION}))
    return SimpleNamespace(artifact=artifact, build=build, pinned=pinned, evidence=evidence)


def run_cli(monkeypatch, module, artifact, out, *flags):
    """The module's __main__ block, in process: its exit status, or -- for a
    refusal the CLI does not render -- the exception a real invocation prints
    as a traceback."""
    monkeypatch.setattr(sys, "path", list(sys.path))
    monkeypatch.setattr(sys, "argv", ["publish_selected.py", str(artifact), "--workflow-url", WORKFLOW,
                                      "--out", str(out), *flags])
    with pytest.raises(SystemExit) as stopped:
        runpy.run_path(str(module), run_name="__main__")
    return stopped.value.code


def snapshot(out):
    """None while `out` is absent, else every filename -> its bytes."""
    return None if not out.exists() else {p.name: p.read_bytes() for p in out.iterdir()}


def edit_report(build, change):
    path = build / "report.json"
    report = json.loads(path.read_text())
    change(report)
    path.write_text(json.dumps(report))


def drift(path):
    path.write_bytes(path.read_bytes() + b"\n// drifted after the recorded build\n")


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


def occupy(c):
    c.out.mkdir()
    (c.out / "publication.json").write_text("an earlier publication\n")
    (c.out / "ecp5.bit").write_bytes(b"stale bitstream")


def no_build_report(c):
    (c.build / "report.json").unlink()


def other_toolchain(c):
    (c.pinned / "toolchain.json").write_text(json.dumps(
        {"sha256": "0" * 64, "release": publish_selected.toolchain_pin.VERSION}))


def not_the_85f_baseline(c):
    edit_report(c.build, lambda r: r.update(device="25k"))


def build_source_differs(c):
    edit_report(c.build, lambda r: r["source_sha256"].update({TOP: "0" * 64}))


def current_source_differs(c):
    drift(c.root / TOP)


def build_input_differs(c):
    drift(c.root / "fpga/boards/ulx3s.lpf")


def historical_transcript_differs(c):
    edit_report(c.build, lambda r: r["simulation_evidence"].update(sha256="0" * 64))


def historical_core_differs(c):
    edit_report(c.build, lambda r: r["simulation_evidence"].update(commit=c.prior))


def simulation_binding_differs(c):
    c.monkeypatch.setattr(build_selected, "simulation_evidence",
                          lambda: {**c.evidence, "sha256": "0" * 64})


def stage_hash_differs(c):
    edit_report(c.build, lambda r: r["stages"]["synthesis"].update(artifact_sha256="0" * 64))


GUARDS = [
    pytest.param(False, occupy, EMPTY, id="occupied-destination"),
    pytest.param(False, no_build_report, "expected one build report and one pinned toolchain record",
                 id="no-build-report"),
    pytest.param(False, other_toolchain, "toolchain identity does not match the pinned suite",
                 id="other-toolchain"),
    pytest.param(False, not_the_85f_baseline, "artifact is not the selected 85F baseline",
                 id="not-the-85f-baseline"),
    pytest.param(False, build_source_differs, f"build source differs: {TOP}", id="build-source-differs"),
    pytest.param(False, current_source_differs, f"current source differs: {TOP}{HINT}",
                 id="current-source-differs"),
    pytest.param(False, build_input_differs, "build input differs: fpga/boards/ulx3s.lpf",
                 id="build-input-differs"),
    pytest.param(True, historical_transcript_differs, "historical simulation transcript differs",
                 id="historical-transcript-differs"),
    pytest.param(True, historical_core_differs, f"historical core differs from simulation: {TOP}",
                 id="historical-core-differs"),
    pytest.param(False, simulation_binding_differs, "core simulation binding differs",
                 id="simulation-binding-differs"),
    pytest.param(False, stage_hash_differs, "stage artifact hash differs", id="stage-hash-differs"),
]


@pytest.mark.parametrize("historical, unmet, reason", GUARDS)
def test_each_unmet_precondition_is_refused_at_the_cli_and_writes_nothing(
        checkout, tmp_path, monkeypatch, capsys, historical, unmet, reason):
    """One precondition of an otherwise valid artifact is unmet per case, so no
    earlier guard can satisfy a later one. REFUSED is exit 2 with the specific
    mismatch on stderr: no traceback, no summary, and the destination as it was."""
    art = download(checkout)
    out = tmp_path / "publication"
    monkeypatch.setattr(build_selected, "simulation_evidence", lambda: art.evidence)
    unmet(SimpleNamespace(root=checkout.root, build=art.build, pinned=art.pinned, out=out,
                          prior=checkout.prior, evidence=art.evidence, monkeypatch=monkeypatch))
    before = snapshot(out)
    code = run_cli(monkeypatch, checkout.module, art.artifact, out, *(["--historical"] if historical else []))
    captured = capsys.readouterr()
    assert code == 2
    assert captured.err == f"REFUSED: {reason}\n"
    assert captured.out == ""
    assert snapshot(out) == before


def first_drifted_source(bundle):
    """The first source in the bundle's recorded order whose committed bytes
    differ from this checkout: what `current source differs` has to name."""
    report = json.loads((bundle / "report.json").read_text())
    for name in report["source_sha256"]:
        if committed(ROOT, f"{report['source_commit']}:{name}") != (ROOT / name).read_bytes():
            return name
    return None


@pytest.mark.parametrize("destination", ["absent", "empty", "occupied"])
def test_checked_in_bundle_is_refused_by_the_real_command_and_leaves_the_destination(
        tmp_path, destination):
    """The reproduction of #1663: `python fpga/publish_selected.py` on the real
    checked-in bundle, whose recorded build predates this checkout."""
    name = first_drifted_source(BUNDLE)
    assert name is not None, ("the checked-in bundle no longer differs from this checkout; "
                              "this control needs a bundle whose recorded build predates it")
    out = tmp_path / "publication"
    if destination == "empty":
        out.mkdir()
    if destination == "occupied":
        occupy(SimpleNamespace(out=out))
    before = snapshot(out)
    done = subprocess.run([sys.executable, str(ROOT / "fpga/publish_selected.py"), str(BUNDLE),
                           "--workflow-url", WORKFLOW, "--out", str(out)],
                          cwd=ROOT, capture_output=True, text=True)
    reason = EMPTY if destination == "occupied" else f"current source differs: {name}{HINT}"
    assert done.returncode == 2, done.stderr
    assert done.stderr == f"REFUSED: {reason}\n"
    assert done.stdout == ""
    assert snapshot(out) == before


def test_a_plain_value_error_at_a_guarded_site_is_not_rendered_as_a_refusal(
        checkout, tmp_path, monkeypatch):
    """The control for the table above: with one site put back to the plain
    ValueError it raised before #1663, the same CLI call leaves as an exception
    instead of SystemExit(2), so the table's `code == 2` cannot be satisfied by
    a site that stopped raising Refused."""
    art = download(checkout)
    out = tmp_path / "publication"
    occupy(SimpleNamespace(out=out))
    before = snapshot(out)
    site = f'raise Refused("{EMPTY}")'
    source = checkout.module.read_text()
    assert source.count(site) == 1, "the injection site is gone: this control no longer injects anything"
    checkout.module.write_text(source.replace(site, f'raise ValueError("{EMPTY}")'))
    with pytest.raises(ValueError, match=EMPTY) as stopped:
        run_cli(monkeypatch, checkout.module, art.artifact, out)
    assert not isinstance(stopped.value, publish_selected.Refused)
    assert snapshot(out) == before


@pytest.mark.parametrize("historical", [False, True])
def test_a_valid_artifact_still_qualifies(checkout, tmp_path, monkeypatch, capsys, historical):
    art = download(checkout)
    out = tmp_path / "publication"
    consulted = []
    monkeypatch.setattr(build_selected, "simulation_evidence", lambda: consulted.append(1) or art.evidence)
    code = run_cli(monkeypatch, checkout.module, art.artifact, out, *(["--historical"] if historical else []))
    captured = capsys.readouterr()
    assert code == 0
    summary = json.loads((out / "publication.json").read_text())
    assert (summary["state"], summary["bitstream_qualified"]) == ("BUILT-TIMING-PASS", True)
    assert "reason" not in summary
    assert json.loads(captured.out) == summary
    assert captured.err == ""
    assert bool(consulted) is not historical


@pytest.mark.parametrize("state, reason", [
    pytest.param("FAIL", "build did not finish successfully: FAIL", id="failed-build"),
    pytest.param("NO-VERDICT", "timing does not pass: clk_core", id="missed-timing")])
def test_a_build_that_fails_its_own_checks_still_publishes_its_summary_at_exit_2(
        checkout, tmp_path, monkeypatch, capsys, state, reason):
    """The guards refusing is new; a build that passed them and then failed is
    the outcome it must not disturb: a published summary, exit 2, no REFUSED."""
    art = download(checkout)
    if state == "FAIL":
        edit_report(art.build, lambda r: r.update(state="FAIL", bitstream_built=False))
    else:
        timing = valid_report()
        timing["fmax"]["clk_core"]["achieved"] = 12.0
        (art.build / "timing.json").write_text(json.dumps(timing))
    monkeypatch.setattr(build_selected, "simulation_evidence", lambda: art.evidence)
    out = tmp_path / "publication"
    code = run_cli(monkeypatch, checkout.module, art.artifact, out)
    captured = capsys.readouterr()
    summary = json.loads((out / "publication.json").read_text())
    assert code == 2
    assert (summary["state"], summary["bitstream_qualified"], summary["reason"]) == (state, False, reason)
    assert json.loads(captured.out) == summary
    assert captured.err == ""


@pytest.mark.parametrize("error, needle, report_text", [
    pytest.param(json.JSONDecodeError, "Expecting value", "not json", id="invalid-json-in-the-sole-report"),
    pytest.param(ValueError, "genuine bug", None, id="plain-value-error-before-publication")])
def test_an_unrelated_value_error_is_an_execution_error_not_a_refusal(
        checkout, tmp_path, monkeypatch, capsys, error, needle, report_text):
    """The defeating input for a handler widened to `except ValueError`: the
    sole report failing to parse, or a bug before publication, must surface as
    the defect it is with its own message -- never as one of the eleven named
    refusals."""
    art = download(checkout)
    out = tmp_path / "publication"
    if report_text is None:
        monkeypatch.setattr(build_selected, "simulation_evidence", raising(ValueError("genuine bug")))
    else:
        (art.build / "report.json").write_text(report_text)
    with pytest.raises(error, match=needle) as stopped:
        run_cli(monkeypatch, checkout.module, art.artifact, out)
    assert type(stopped.value) is error
    assert not isinstance(stopped.value, publish_selected.Refused)
    captured = capsys.readouterr()
    assert "REFUSED" not in captured.err
    assert captured.out == ""
    assert not out.exists()


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
LPF, SIM_TRANSCRIPT = "fpga/boards/ulx3s.lpf", "docs/sim.txt"
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
