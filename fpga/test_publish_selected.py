"""Timing evidence must cover the actual PLL core clock and fit the device,
and a refused stored simulation is a NO-VERDICT publication, never a traceback."""
import copy
import hashlib
import json
import math
from pathlib import Path
import re
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


# ----------------------- a recorded commit that git cannot show (#1690) ----
TOP, LPF, SIM_TRANSCRIPT = "rtl-sketch/synth_top.v", "fpga/boards/ulx3s.lpf", "docs/sim.txt"
COMMIT, SIM_COMMIT = "1" * 40, "2" * 40


def _recorded(name):
    """What `git show <commit>:<name>` answers: the bytes in this checkout
    where it has them -- the non-historical guards compare the record with the
    checkout -- and fixed bytes otherwise."""
    path = ROOT / name
    return path.read_bytes() if path.is_file() else f"built {name}\n".encode()


def _artifact(tmp_path, *, with_simulation=False, commit=COMMIT, source=TOP):
    """The least a publication reads before its first `git show`: the build
    report, the selected configuration on the 85k with true source digests,
    beside the pinned toolchain record. `with_simulation` adds what only the
    --historical path reads. `commit` is what the report records as built and
    `source` the one path it records a digest for."""
    build, pinned = tmp_path / "build", tmp_path / "oss-cad-pinned"
    build.mkdir()
    pinned.mkdir()
    report = {"configuration": {"OSC2X": 1, "FILTER2X": 1, "pulse_duty_percent": 47.90},
              "source_commit": commit, "device": "85k", "package": "CABGA381",
              "source_sha256": {source: hashlib.sha256(_recorded(source)).hexdigest()}}
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
    pytest.param(lambda commit, name: name == TOP, id="source-75"),
    pytest.param(lambda commit, name: name == LPF, id="build-input-88"),
]


@pytest.mark.parametrize("fail", _SITES)
def test_a_recorded_commit_git_cannot_show_is_refused(
        tmp_path, monkeypatch, capsys, fail):
    """publish()'s `git show` calls had no handler for the class a failed git
    raises, so a commit the clone does not hold left the CLI as a traceback at
    exit 1. The recorded commit being readable is a precondition, so REFUSED,
    exit 2 (#1690), nothing published. The publisher runs by hand on a
    downloaded artifact (fpga/SELECTED.md), in whatever checkout the operator
    has. The calls capture stderr (bytes: no text=True), or git's reason would
    reach the terminal beside the REFUSED line instead of inside it.

    These two sites run before a publication summary exists, so #2025 keeps
    REFUSED-with-nothing-written for them -- the artifact's integrity is
    unverified until every recorded source has been read at its commit -- and
    makes the line name the commit, which is what the operator has to fetch.
    git itself says the clone does not hold it (`_commit_absent` runs the real
    git here); the patch only fails the show. The two --historical sites,
    where the summary is reachable, are below."""
    artifact = _artifact(tmp_path)
    out = tmp_path / "publication"
    check_output, calls = _git_show(fail)
    monkeypatch.setattr(subprocess, "check_output", check_output)
    assert _cli(monkeypatch, artifact, out) == 2
    assert calls and all(kwargs.get("stderr") == subprocess.PIPE for _, kwargs in calls)
    printed = capsys.readouterr()
    assert printed.out == ""
    assert printed.err.startswith(f"REFUSED: recorded commit {COMMIT} is not in this clone")
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


# ------------- a commit the clone does not hold, at every `git show` (#2025) --
ABSENT_COMMIT, MALFORMED_COMMIT = "3" * 40, "not-a-commit"
real_check_output = subprocess.check_output


def _report_path(artifact):
    return artifact / "fpga-selected-85k" / "report.json"


def _rewrite(artifact, edit):
    """Rewrite the artifact's build report through `edit(report)`."""
    path = _report_path(artifact)
    report = json.loads(path.read_text())
    edit(report)
    path.write_text(json.dumps(report))


def test_the_clone_lacks_probe_reads_gits_own_three_answers():
    """`git cat-file -e -- <name>` decides whether a failed `git show` is a
    precondition the checkout did not meet, and its statuses are three, not
    two: 0 the object is here; 1 a full object name that is not -- a shallow
    or single-branch checkout; 128 not a name git will parse at all. Measured
    on git 2.43 before the handler was written. A handler that read every
    failure as 1 would tell an operator to fetch history for a malformed
    record (#2025's rule-8 input)."""
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    assert publish_selected._commit_absent(head) is False
    assert publish_selected._commit_absent(head[:7]) is False
    assert publish_selected._commit_absent(ABSENT_COMMIT) is True
    for malformed in (MALFORMED_COMMIT, "", "abc", head + "0"):
        assert publish_selected._commit_absent(malformed) is False, repr(malformed)


@pytest.mark.parametrize("report_state", ["BUILT", "FAIL"])
def test_a_historical_simulation_commit_the_clone_lacks_is_a_no_verdict_publication(
        tmp_path, monkeypatch, capsys, report_state):
    """Under --historical the summary is reachable when a `git show` fails, so
    a commit the clone does not hold must not throw the build's reports away
    (#2025): the publication is NO-VERDICT with the commit as its reason, the
    build's report and timing kept beside it, exit 2. A failed build stays
    NO-VERDICT for the same reason the refused stored simulation does (#1631):
    the reason is about the checkout, and a FAIL beside it would read as the
    build failing because of it. The simulation commit is the one that can be
    absent while the source commit is present (#1676); real git decides."""
    artifact, _ = built_artifact(tmp_path, state=report_state)
    _rewrite(artifact, lambda report: report["simulation_evidence"].update(commit=ABSENT_COMMIT))
    out = tmp_path / "publication"
    assert _cli(monkeypatch, artifact, out, "--historical") == 2
    summary = json.loads((out / "publication.json").read_text())
    assert summary["state"] == "NO-VERDICT"
    assert summary["reason"].startswith(f"recorded commit {ABSENT_COMMIT} is not in this clone")
    assert summary["simulation_evidence"] is None
    assert summary["bitstream_qualified"] is False
    assert json.loads((out / "report.json").read_text())["state"] == report_state
    assert all((out / name).is_file() for name in ("timing.json", "toolchain.json"))
    printed = capsys.readouterr()
    assert printed.err == ""
    assert json.loads(printed.out) == summary


@pytest.mark.parametrize("historical, edit", [
    pytest.param(False, lambda r: r["source_sha256"].update({"rtl-sketch/no_such_file.v": "0" * 64}),
                 id="source-path-that-is-not-at-the-commit"),
    pytest.param(True, lambda r: r["simulation_evidence"].update(transcript="docs/no_such_transcript.txt"),
                 id="transcript-path-that-is-not-at-the-commit"),
])
def test_a_git_show_that_fails_for_a_commit_the_clone_holds_surfaces_as_the_defect_it_is(
        tmp_path, historical, edit):
    """Rule 8: the handler tells a missing commit from a defect by asking git
    whether the clone holds the commit, so a show that fails while git says it
    does -- a path the record names that is not at that commit -- leaves
    publish() as the CalledProcessError git raised, not as a refusal, and
    nothing is written. Real git throughout. The core sources under
    --historical cannot get here with real git (they were read at the source
    commit first); the probe below covers them. A commit that is not a name
    no longer reaches git at all: #2082 decides it first, below."""
    artifact, _ = built_artifact(tmp_path)
    _rewrite(artifact, edit)
    out = tmp_path / "publication"
    with pytest.raises(subprocess.CalledProcessError) as failed:
        publish_selected.publish(artifact, out, WORKFLOW, historical=historical)
    assert not isinstance(failed.value, publish_selected.Refused)
    assert not out.exists()


def test_a_malformed_source_commit_is_rejected_before_git_and_not_diagnosed_as_a_missing_one(tmp_path, monkeypatch, capsys):
    """A `source_commit` that is not a name was left for git to reject, and
    #1690's backstop rendered git's 128 as REFUSED at exit 2. #2082 decides it
    before git is asked, so there is nothing of git's left to render: the
    ValueError leaves the CLI as it arrived, the path every malformed-report
    check beside it takes (a real invocation prints a traceback at exit 1).
    What must not come back is the diagnosis: a Refused saying the clone lacks
    it would send an operator to fetch `not-a-commit`. No child is started."""
    artifact = _artifact(tmp_path, commit=MALFORMED_COMMIT)
    out = tmp_path / "publication"
    spawned = _children_spawned(monkeypatch)
    with pytest.raises(ValueError, match="not a full object name") as failed:
        publish_selected.publish(artifact, out, WORKFLOW)
    assert not isinstance(failed.value, publish_selected.Refused)
    _argv(monkeypatch, artifact, out)
    with pytest.raises(ValueError, match="not a full object name") as stopped:
        runpy.run_path(publish_selected.__file__, run_name="__main__")
    assert not isinstance(stopped.value, publish_selected.Refused)
    assert spawned == []
    assert capsys.readouterr().err == ""
    assert not out.exists()


class _NthGitShowFails:
    """check_output replacement: every `git show` answers what real git
    answers for it, except the nth, which fails as git does for an object the
    clone does not hold. n=None fails none and only counts. This is the probe
    the reviewing session drove `publish()` with (issue #2025), with one thing
    added: it left real git to say whether the clone holds the commit, and
    real git holds the fixture's, so a handler that is correct reads each of
    its failures as a defect. The tests that use it say which it is."""
    answers = {}

    def __init__(self, n=None):
        self.n, self.seen = n, 0

    def __call__(self, cmd, *args, **kwargs):
        if not (isinstance(cmd, (list, tuple)) and list(cmd[:2]) == ["git", "show"]):
            return real_check_output(cmd, *args, **kwargs)
        self.seen += 1
        if self.seen == self.n:
            raise subprocess.CalledProcessError(128, list(cmd), stderr=b"fatal: invalid object name\n")
        if tuple(cmd) not in self.answers:
            self.answers[tuple(cmd)] = real_check_output(cmd, *args, **kwargs)
        return self.answers[tuple(cmd)]


def _children(tmp_path, monkeypatch, historical):
    """The built artifact, and how many `git show` children publish() itself
    issues on this path: counted, not assumed. The stored simulation answers
    from the record; its own children are the callee's (#1945), not
    publish()'s, and a red first run counted one of them."""
    artifact, evidence = built_artifact(tmp_path)
    monkeypatch.setattr(build_selected, "simulation_evidence", lambda: evidence)
    counter = _NthGitShowFails()
    monkeypatch.setattr(subprocess, "check_output", counter)
    publish_selected.publish(artifact, tmp_path / "counted", WORKFLOW, historical=historical)
    return artifact, counter.seen


def _probe(artifact, out, historical):
    """(what publish() raised, or None; the summary it returned, or None)."""
    try:
        return None, publish_selected.publish(artifact, out, WORKFLOW, historical=historical)
    except Exception as exc:
        return exc, None


@pytest.mark.parametrize("historical", [False, True])
def test_every_git_show_child_whose_commit_the_clone_lacks_refuses(tmp_path, monkeypatch, historical):
    """Every `git show` publish() issues, one at a time, failing as git does
    when the clone does not hold the commit, with git saying so: never a
    CalledProcessError out of publish() again. Which refusal depends on the
    site, not the path: the sites before a summary exists -- each recorded
    source, then the LPF and each ROM -- are REFUSED with nothing written on
    both paths; the --historical sites after them -- the transcript, then the
    simulation commit's copy of each core source and ROM -- are a NO-VERDICT
    publication that keeps the build's reports. The children are counted, not
    taken from the fixture, and the count is checked against what the tree
    says publish() must read. A fifth `git show` site moves the count."""
    artifact, total = _children(tmp_path, monkeypatch, historical)
    sources, roms = len(build_selected.sources()), len(list((ROOT / "rtl-sketch").glob("*.hex")))
    early = sources + 1 + roms
    assert total == early + (sources + roms if historical else 0)
    head = json.loads(_report_path(artifact).read_text())["source_commit"]
    monkeypatch.setattr(publish_selected, "_commit_absent", lambda commit: True)
    table = []
    for n in range(1, total + 1):
        out = tmp_path / f"publication-{n}"
        monkeypatch.setattr(subprocess, "check_output", _NthGitShowFails(n))
        raised, summary = _probe(artifact, out, historical)
        if n <= early:
            table.append((n, type(raised).__name__, head in str(raised), out.exists()))
        else:
            table.append((n, summary["state"] if summary else type(raised).__name__,
                          head in (summary or {}).get("reason", ""), (out / "report.json").is_file()))
    assert all(row[1:] == ("Refused", True, False) for row in table if row[0] <= early), table
    assert all(row[1:] == ("NO-VERDICT", True, True) for row in table if row[0] > early), table


@pytest.mark.parametrize("historical", [False, True])
def test_a_failed_git_show_for_a_commit_the_clone_holds_surfaces_at_every_child(
        tmp_path, monkeypatch, historical):
    """The same children, failing the same way, with git saying the clone
    does hold the commit: a defect, at every child on both paths. It must
    leave publish() as the CalledProcessError git raised -- not a Refused at
    the early sites, not a NO-VERDICT publication under --historical -- and
    write nothing. This is the control for the handler: one that filed every
    failure as a missing commit turns this red, and so does one that catches
    the defect at the --historical sites."""
    artifact, total = _children(tmp_path, monkeypatch, historical)
    monkeypatch.setattr(publish_selected, "_commit_absent", lambda commit: False)
    table = []
    for n in range(1, total + 1):
        out = tmp_path / f"publication-{n}"
        monkeypatch.setattr(subprocess, "check_output", _NthGitShowFails(n))
        raised, _summary = _probe(artifact, out, historical)
        table.append((n, type(raised).__name__, out.exists()))
    assert all(row[1:] == ("CalledProcessError", False) for row in table), table


# ------ a recorded commit that is not a full object name, before git sees it (#2082) --
#: What is not `git rev-parse HEAD` output, which is all build_selected.py records:
#: what git reads as an option, what it reads as another object (an empty name is
#: the index -- `git show :<path>`, measured), what only looks like a sha, and what
#: is not a string at all.
NOT_A_COMMIT = [
    pytest.param("--help", id="option"),
    pytest.param("abc", id="not-hex"),
    pytest.param("", id="empty"),
    pytest.param("1" * 39, id="39-hex"),
    pytest.param("1" * 41, id="41-hex"),
    pytest.param("A" * 40, id="uppercase"),
    pytest.param("1" * 40 + "\n", id="trailing-newline"),
    pytest.param(" " + "1" * 40, id="leading-space"),
    pytest.param(None, id="none"),
    pytest.param(7, id="number"),
    pytest.param(["1" * 40], id="list"),
]


def _children_spawned(monkeypatch):
    """Every child process publish() starts, as argv, in order, each still run
    for real. Both of the module's calls go through `subprocess.Popen`, so a
    call is counted whichever API made it."""
    spawned = []
    real = subprocess.Popen

    def popen(cmd, *args, **kwargs):
        spawned.append(cmd if isinstance(cmd, str) else [str(part) for part in cmd])
        return real(cmd, *args, **kwargs)
    monkeypatch.setattr(subprocess, "Popen", popen)
    return spawned


def _shown(spawned):
    """The object argument of every `git show` child in `spawned`."""
    return [cmd[2] for cmd in spawned if cmd[:2] == ["git", "show"]]


def _defect(raised):
    """How a name that is not an object name leaves publish(): a ValueError,
    as #2025 filed it, and not the Refused the --historical sites turn into a
    NO-VERDICT publication."""
    return isinstance(raised, ValueError) and not isinstance(raised, publish_selected.Refused)


def test_a_recorded_commit_git_reads_as_an_option_never_reaches_git(tmp_path, monkeypatch):
    """The bug as it shipped, kept as the control (rules 5 and 8): the report's
    `source_commit` went into `git show`'s object position as it came out of
    the downloaded artifact, so a value beginning with `--` was an option. With
    a recorded source path appended it reaches git as `--output=<dir>/probe:x`
    -- `x` is a key of the same report's `source_sha256` -- and git opens that
    file for writing before anything else, so it is created, or truncated where
    it already exists, and git then fails at 128 (measured on git 2.43.0 before
    the guard was written: 15 bytes became 0). That failure went to #1690's
    backstop as REFUSED at exit 2, with no word that a file had been touched.
    Real git throughout -- the recorder only watches, so on the unfixed code it
    is git itself that writes the file. Rejected before any child starts;
    nothing published."""
    probe = tmp_path / "probe"
    artifact = _artifact(tmp_path, commit=f"--output={probe}", source="x")
    out = tmp_path / "publication"
    spawned = _children_spawned(monkeypatch)
    raised, _summary = _probe(artifact, out, False)
    assert not (tmp_path / "probe:x").exists(), spawned
    assert sorted(p.name for p in tmp_path.iterdir()) == ["build", "oss-cad-pinned"], spawned
    assert spawned == []
    assert _defect(raised) and "source_commit" in str(raised) and f"--output={probe}" in str(raised)


@pytest.mark.parametrize("commit", NOT_A_COMMIT)
def test_a_source_commit_that_is_not_a_full_object_name_is_rejected_before_any_git(tmp_path, monkeypatch, commit):
    """The recorded value is `git rev-parse HEAD` output (build_selected.py):
    40 lowercase hex and nothing else, so a string that cannot start with `-`
    cannot be an option and there is no `--` placement to get right. Anything
    else is a defect of the artifact, not a precondition of this checkout,
    which is #2025's classification of a name git cannot parse -- now decided
    without asking git. No child is started, so nothing is written anywhere."""
    artifact = _artifact(tmp_path, commit=commit)
    out = tmp_path / "publication"
    spawned = _children_spawned(monkeypatch)
    raised, _summary = _probe(artifact, out, False)
    assert spawned == [], repr(commit)
    assert _defect(raised) and "source_commit" in str(raised), repr(commit)
    assert not out.exists()


def test_an_empty_source_commit_does_not_publish_the_checkout_against_itself(tmp_path, monkeypatch):
    """The other thing an unvalidated commit lets git read: `git show ":<path>"`
    names the index. Measured on the unfixed code with this complete artifact:
    an empty `source_commit` read every recorded source from the checkout, every
    digest agreed with itself, and the publication went through as
    BUILT-TIMING-PASS with `source_commit` empty on the summary and the
    bitstream qualified -- the check that binds the bitstream to a commit had
    compared the checkout with itself. Real git throughout."""
    artifact, evidence = built_artifact(tmp_path)
    _rewrite(artifact, lambda report: report.update(source_commit=""))
    monkeypatch.setattr(build_selected, "simulation_evidence", lambda: evidence)
    out = tmp_path / "publication"
    raised, summary = _probe(artifact, out, False)
    assert summary is None and _defect(raised), (summary or {}).get("state")
    assert not out.exists()


@pytest.mark.parametrize("commit", NOT_A_COMMIT)
def test_a_historical_simulation_commit_that_is_not_a_full_object_name_is_rejected_before_git(tmp_path, monkeypatch, commit):
    """The same value space at the simulation commit, read only under
    --historical, after the source commit's own reads: every `git show` that
    ran named a full object name, nothing is written, and what leaves
    publish() is the defect, from the site that would have used it. The
    recorder cannot be empty here, and the file the rule-8 input names cannot
    be written from this site -- every path shown at this commit is under
    `rtl-sketch/`, so git is asked for `<value>:rtl-sketch/<name>`, in a
    directory that does not exist (measured) -- so what goes red on the
    unfixed code is that git was handed an option. Not a Refused: the
    --historical handler records a commit the clone merely lacks as NO-VERDICT
    and keeps the build's reports (#2025), and an artifact that names no commit
    is not that."""
    artifact, _ = built_artifact(tmp_path)
    _rewrite(artifact, lambda report: report["simulation_evidence"].update(commit=commit))
    out = tmp_path / "publication"
    spawned = _children_spawned(monkeypatch)
    raised, _summary = _probe(artifact, out, True)
    shown = _shown(spawned)
    assert shown and all(re.fullmatch(r"[0-9a-f]{40}", spec.partition(":")[0]) for spec in shown), (repr(commit), spawned)
    assert _defect(raised) and "simulation_evidence.commit" in str(raised), repr(commit)
    assert not out.exists()
