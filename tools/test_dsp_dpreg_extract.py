"""tools/test_dsp_dpreg_extract.py -- `--box local` (#316) is the ssh path with another transport.

A fake transport stands in for the box: a directory holding R1's shipped DSP
evidence (fpga/reports/arty/r1-player-preview-2025.1/dsp-dpreg-evidence), bound
to the shipped drc.rpt. Every command the tool issues is recorded. Remote and
local runs must issue the same steps with the same scripts and inputs, reach the
same result, and fail at the same step with the same exit status when any step
fails. Only the transport argv may differ (ssh/scp vs bash/cp).

The fake launches nothing, so it cannot see what a host without ssh or scp
does (#1741). Those cases run the real `sh()` with PATH holding only what each
case names.
"""
import hashlib
import importlib.util
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
_spec = importlib.util.spec_from_file_location("dsp_dpreg_extract", HERE / "dsp_dpreg_extract.py")
dx = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(dx)

PUB = ROOT / "fpga/reports/arty/r1-player-preview-2025.1"
DCP = "/home/ubuntu/work/r1-image-build/arty/routed.dcp"
DCP_SHA = "0f81026ecc8a0455da62ec2b7140ea806e00a97a71006d99743340299476ad5f"
REMOTE = "/box/dsp-review"


def _sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


@pytest.fixture
def box(tmp_path):
    """The box's session directory after a successful Vivado run: R1's evidence,
    with drc_rpt.sha256 naming the shipped drc.rpt's bytes (the ones `cat`
    returns here) and the manifest recomputed."""
    d = tmp_path / "box"
    shutil.copytree(PUB / "dsp-dpreg-evidence", d)
    drc = (PUB / "drc.rpt").read_bytes()
    (d / "drc_rpt.sha256").write_text(f"{_sha(drc)}  /box/arty/drc.rpt\n")
    (d / "MANIFEST.sha256").write_text("".join(
        f"{_sha((d / n).read_bytes())}  {n}\n" for n in dx.MANIFEST_FILES))
    return d


def _fake(box_dir, fail_at=None, drc=None):
    calls = []

    def sh(cmd, **kw):
        calls.append((list(cmd), kw.get("input")))
        n = len(calls)
        rc = 1 if n == fail_at else 0
        out = ""
        if rc == 0 and cmd[:2] in (["cp", str(cmd[1])], ["scp", str(cmd[1])]):
            src, dst = cmd[1], cmd[2]
            if src.startswith(("vbox:", REMOTE)):              # a pull from the box
                name = Path(src.split(":", 1)[-1]).name
                shutil.copyfile(box_dir / name, dst)
        if rc == 0 and cmd[-1].startswith("cat "):
            out = (PUB / "drc.rpt").read_text() if drc is None else drc
        return subprocess.CompletedProcess(cmd, rc, out, "")
    return calls, sh


def _run(monkeypatch, tmp_path, box_dir, where, fail_at=None, drc=None):
    calls, sh = _fake(box_dir, fail_at, drc)
    monkeypatch.setattr(dx, "sh", sh)
    ev = tmp_path / f"ev-{where}-{fail_at}" / "dsp-dpreg-evidence"
    try:
        rc = dx.main(["--box", where, "--dcp", DCP, "--dcp-sha256", DCP_SHA,
                      "--drc-rpt", "/box/arty/drc.rpt", "--remote-dir", REMOTE,
                      "--evidence", str(ev)])
    except SystemExit as exc:
        rc = f"SystemExit: {exc}"
    return rc, calls, ev


def _transport_free(calls, ev):
    """What the step DOES, with the transport removed (and this run's local
    evidence directory named EV, since each test run writes its own)."""
    local = str(ev)
    norm = lambda x: x.split(":", 1)[-1].replace(local, "EV")          # noqa: E731
    out = []
    for cmd, inp in calls:
        if cmd[0] in ("ssh", "bash"):
            out.append(("run", cmd[-1], inp))
        else:
            out.append(("copy", norm(cmd[1]), norm(cmd[2]), inp))
    return out


def test_local_and_remote_issue_the_same_steps_and_produce_the_same_evidence(
        monkeypatch, tmp_path, box):
    rc_r, calls_r, ev_r = _run(monkeypatch, tmp_path, box, "vbox")
    rc_l, calls_l, ev_l = _run(monkeypatch, tmp_path, box, "local")
    assert rc_r == rc_l == 0
    assert _transport_free(calls_r, ev_r) == _transport_free(calls_l, ev_l)
    assert {c[0][0] for c in calls_r} == {"ssh", "scp"}
    assert {c[0][0] for c in calls_l} == {"bash", "cp"}
    for name in dx.MANIFEST_FILES + ("MANIFEST.sha256", "dsp_dpreg_extract.tcl"):
        assert (ev_r / name).read_bytes() == (ev_l / name).read_bytes(), name


def test_local_run_commands_are_the_scripts_under_bash():
    assert dx.on_box("local", "bash -s") == ["bash", "-c", "bash -s"]
    assert dx.on_box("vbox", "bash -s") == ["ssh", "vbox", "bash -s"]
    assert dx.to_box("local", Path("/a/t.tcl"), "/r/t.tcl") == ["cp", "/a/t.tcl", "/r/t.tcl"]
    assert dx.from_box("vbox", "/r/x", Path("/e/x")) == ["scp", "vbox:/r/x", "/e/x"]


def test_a_failure_at_every_step_propagates_identically(monkeypatch, tmp_path, box):
    _rc, calls, _ev = _run(monkeypatch, tmp_path, box, "vbox")
    n = len(calls)
    assert n >= 10
    for k in range(1, n + 1):
        rc_r, c_r, e_r = _run(monkeypatch, tmp_path, box, "vbox", fail_at=k)
        rc_l, c_l, e_l = _run(monkeypatch, tmp_path, box, "local", fail_at=k)
        # every failing step refuses (exit 1), except the diagnostic `tail`
        # after a failed Vivado run, whose own status is not consulted
        tail = "tail -40" in c_r[k - 1][0][-1]
        assert rc_r == rc_l and (rc_r != 0 or tail), (k, rc_r, rc_l)
        assert _transport_free(c_r, e_r) == _transport_free(c_l, e_l), k


def test_the_post_run_digest_check_still_refuses_in_local_mode(monkeypatch, tmp_path, box):
    """A pulled file that differs from the box manifest is refused locally too."""
    (box / "dsp_cells_dump.txt").write_text("tampered after the manifest\n")
    rc, _calls, _ev = _run(monkeypatch, tmp_path, box, "local")
    assert rc == 1


SAME_INSTANCE_TWICE = """\
| DPREG-4 | Warning  | DSP48E1_PregDynOpmodeZmuxP: | 2      |
DPREG-4#1 Warning
DSP48E1_PregDynOpmodeZmuxP:
The DSP48E1 cell u_synth/u_voice/osc2_path/p0/pair/dec/prod0__0 with the given dynamic OPMODE[6:0] connections may lead
Related violations: <none>

DPREG-4#2 Warning
DSP48E1_PregDynOpmodeZmuxP:
The DSP48E1 cell u_synth/u_voice/osc2_path/p0/pair/dec/prod0__0 with the given dynamic OPMODE[6:0] connections may lead
Related violations: <none>
"""


def test_a_drc_flagging_one_instance_twice_refuses_before_any_tcl_is_staged(
        monkeypatch, tmp_path, box):
    """That drc.rpt used to be expanded into duplicate cells, a Tcl staged and
    Vivado run, all for the analyser to refuse the dump (#1557)."""
    rc, calls, ev = _run(monkeypatch, tmp_path, box, "local",
                         drc=SAME_INSTANCE_TWICE)
    assert rc == "SystemExit: REFUSED: drc.rpt flags the same instance twice"
    issued = [" ".join(cmd) + (inp or "") for cmd, inp in calls]
    assert not any("vivado" in s or "dsp_dpreg_extract.tcl" in s
                   for s in issued)
    assert not (ev / "dsp_dpreg_extract.tcl").exists()


REAL_SH = dx.sh
LOCAL_TOOLS = ("bash", "cp", "sha256sum", "awk", "mkdir", "tee", "grep",
               "cat", "rm", "tail")


@pytest.fixture
def real_box(tmp_path):
    """A box this host runs the scripts of for real: a checkpoint to hash
    (only ever hashed; nothing here reaches Vivado), its true digest, the
    shipped drc.rpt and an empty session directory."""
    root = tmp_path / "box"
    root.mkdir()
    dcp = root / "routed.dcp"
    dcp.write_bytes(b"hashed by the staging and recheck scripts, never opened\n")
    drc = root / "drc.rpt"
    shutil.copyfile(PUB / "drc.rpt", drc)
    return {"dcp": str(dcp), "sha": _sha(dcp.read_bytes()), "drc": str(drc),
            "remote": root / "dsp-review"}


def _only_on_path(monkeypatch, tmp_path, *tools):
    """A PATH holding symlinks to exactly `tools`, and nothing else."""
    bindir = tmp_path / "bin"
    bindir.mkdir()
    for t in tools:
        if not shutil.which(t):
            pytest.skip(f"this host has no {t}: the real local route cannot be run here")
        (bindir / t).symlink_to(shutil.which(t))
    monkeypatch.setenv("PATH", str(bindir))
    return bindir


def _stub(bindir, name, log, body=""):
    """An executable `name` that appends its last argument to `log`, then
    runs `body`."""
    p = bindir / name
    p.write_text(f"""\
#!{sys.executable}
import sys
open({str(log)!r}, "a").write(sys.argv[-1] + "\\n")
{body}
""")
    p.chmod(0o755)
    return p


def _run_real(monkeypatch, capfd, box, ev, where):
    monkeypatch.setattr(dx, "sh", REAL_SH)
    rc = dx.main(["--box", where, "--dcp", box["dcp"], "--dcp-sha256", box["sha"],
                  "--drc-rpt", box["drc"], "--remote-dir", str(box["remote"]),
                  "--evidence", str(ev)])
    out, err = capfd.readouterr()
    return rc, out, err


def _issued(out):
    return [l for l in out.splitlines() if l.startswith("+ ")]


@pytest.mark.parametrize("broken, reason",
                         [("absent", "No such file or directory"),
                          ("not-executable", "Permission denied")])
@pytest.mark.parametrize("where, missing, issued",
                         [("example.invalid", "ssh", "+ ssh example.invalid bash -s"),
                          (dx.LOCAL, "bash", "+ bash -c bash -s")],
                         ids=["remote", "local"])
def test_a_transport_executable_that_cannot_run_is_refused_not_a_traceback(
        monkeypatch, tmp_path, capfd, broken, reason, where, missing, issued):
    """START RED (#1741): against 3309dd59 every case raised out of main()
    (FileNotFoundError, or PermissionError for a file with no execute bit).
    PATH is otherwise empty, so the executable the route names first is the
    one that cannot run."""
    bindir = _only_on_path(monkeypatch, tmp_path)
    if broken == "not-executable":
        (bindir / missing).write_text("#!/bin/sh\necho must not run\n")
    monkeypatch.setattr(dx, "sh", REAL_SH)
    rc = dx.main(["--box", where, "--dcp", "/example/routed.dcp",
                  "--dcp-sha256", "0" * 64, "--drc-rpt", "/example/drc.rpt",
                  "--evidence", str(tmp_path / "ev")])
    out, err = capfd.readouterr()
    assert rc == 1
    assert _issued(out) == [issued]
    assert out.splitlines()[-2:] == [
        f"REFUSED: cannot run {missing}: {reason}",
        "REFUSED: box preconditions failed"]
    assert err == ""


@pytest.mark.parametrize("where, copy_tool", [("vbox", "scp"), (dx.LOCAL, "cp")])
def test_a_missing_copy_tool_is_refused_after_the_shell_ran(
        monkeypatch, tmp_path, capfd, real_box, where, copy_tool):
    """START RED (#1741): the shell runs -- a stub ssh, or the real bash over
    `real_box` -- so the run stages the box, fetches drc.rpt and meets the
    copy tool at the Tcl push."""
    if where == dx.LOCAL:
        _only_on_path(monkeypatch, tmp_path, *(t for t in LOCAL_TOOLS if t != copy_tool))
    else:
        log = tmp_path / "ssh.log"
        bindir = _only_on_path(monkeypatch, tmp_path)
        _stub(bindir, "ssh", log,
              'if sys.argv[-1].startswith("cat "):\n'
              f'    sys.stdout.write(open({real_box["drc"]!r}).read())\n')
    ev = tmp_path / "ev"
    rc, out, err = _run_real(monkeypatch, capfd, real_box, ev, where)
    assert rc == 1
    assert len(_issued(out)) == 3 and _issued(out)[-1].startswith(f"+ {copy_tool} ")
    assert out.splitlines()[-2:] == [
        f"REFUSED: cannot run {copy_tool}: No such file or directory",
        "REFUSED: could not stage Tcl"]
    assert err == ""
    assert not (real_box["remote"] / "dsp_dpreg_extract.tcl").exists()
    if where != dx.LOCAL:
        assert log.read_text().splitlines() == ["bash -s", f"cat {real_box['drc']}"]


def test_the_local_route_is_unchanged_without_the_remote_tools_on_path(
        monkeypatch, tmp_path, capfd, real_box):
    """`--box local` names only bash and cp: with ssh and scp absent the real
    `sh()` carries the run to Vivado, which refuses here as on any host
    without it, exactly as it does with stub ssh/scp installed -- and those
    are never invoked. Green on 3309dd59 by design: it pins what the guard
    must not change."""
    bindir = _only_on_path(monkeypatch, tmp_path, *LOCAL_TOOLS)
    rc, out, err = _run_real(monkeypatch, capfd, real_box, tmp_path / "ev-1", dx.LOCAL)
    log = tmp_path / "remote-tools.log"
    for name in ("ssh", "scp"):
        _stub(bindir, name, log, "sys.exit(99)")
    rc2, out2, err2 = _run_real(monkeypatch, capfd, real_box, tmp_path / "ev-2", dx.LOCAL)

    def norm(s):
        return s.replace(str(tmp_path / "ev-1"), "EV").replace(str(tmp_path / "ev-2"), "EV")

    assert (rc2, norm(out2), norm(err2)) == (rc, norm(out), norm(err))
    assert rc == 1 and "REFUSED: vivado batch failed" in out
    assert "cannot run" not in out
    assert {l.split()[1] for l in _issued(out)} == {"bash", "cp"}
    assert (real_box["remote"] / "dsp_dpreg_extract.tcl").is_file()
    assert (real_box["remote"] / "routed_dcp.sha256").read_text().split()[0] == real_box["sha"]
    assert not log.exists()


@pytest.mark.parametrize("kw", [{}, {"capture_output": True}, {"input": "set -eu\n"}],
                         ids=["plain", "capture_output", "input"])
@pytest.mark.parametrize("argv", [dx.on_box("vbox", "bash -s"),
                                  dx.to_box("vbox", "t.tcl", "/r/t.tcl"),
                                  dx.from_box("vbox", "/r/x", "x")],
                         ids=["on_box", "to_box", "from_box"])
def test_sh_answers_a_failed_command_for_a_missing_executable(
        monkeypatch, tmp_path, capfd, argv, kw):
    """Every argv the remote route builds, in every shape main() calls it.
    The pull is a `from_box` scp that main() only reaches once an earlier scp
    succeeded, so it is exercised here. START RED (#1741): 3309dd59 raises."""
    _only_on_path(monkeypatch, tmp_path)
    r = dx.sh(argv, **kw)
    out, err = capfd.readouterr()
    assert r.args == argv and r.returncode == 127
    assert r.stdout == r.stderr == ""
    assert out.splitlines() == [
        "+ " + " ".join(argv),
        f"REFUSED: cannot run {argv[0]}: No such file or directory"]
    assert err == ""
