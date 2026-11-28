"""tools/test_dsp_dpreg_extract.py -- `--box local` (#316) is the ssh path with another transport.

A fake transport stands in for the box: a directory holding R1's shipped DSP
evidence (fpga/reports/arty/r1-player-preview-2025.1/dsp-dpreg-evidence), bound
to the shipped drc.rpt. Every command the tool issues is recorded. Remote and
local runs must issue the same steps with the same scripts and inputs, reach
the same result, and fail at the same step with the same exit status when any step
fails. Only the transport argv may differ (ssh/scp vs bash/cp).

EXIT CONTRACT (issue #1807).  Every refusal in this tool is a PRECONDITION
that was not met -- the box could not be reached, a step failed, the pulled
bytes are not the box's, the evidence does not name the checkpoint -- and
tools/exit_contract.py reserves REFUSED (2) for exactly that.  AS MEASURED ON
main @ 03148399 (2026-11-28, red-first run of every control below): all
seventeen refused with 1 -- nine `print("REFUSED ..."); return 1` in main()
and eight `raise SystemExit("REFUSED ...")` in the helpers, which exits 1
because a SystemExit carrying a string prints it and exits 1 -- so
tools/run_all.py rendered every one as a RESULT.  The controls hold the
status to `exit_contract.REFUSED` and never retype the 2.
"""
import ast
import hashlib
import importlib.util
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
import exit_contract                                             # noqa: E402

_spec = importlib.util.spec_from_file_location("dsp_dpreg_extract", HERE / "dsp_dpreg_extract.py")
dx = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(dx)

PUB = ROOT / "fpga/reports/arty/r1-player-preview-2025.1"
DCP = "/home/ubuntu/work/r1-image-build/arty/routed.dcp"
DCP_SHA = "0f81026ecc8a0455da62ec2b7140ea806e00a97a71006d99743340299476ad5f"
REMOTE = "/box/dsp-review"
REFUSED = exit_contract.REFUSED


def _sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def _process_status(exc: BaseException) -> int:
    """The status a process exits with when `exc` propagates out of it:
    Python's rule for SystemExit, which a string argument turns into 1."""
    code = exc.code if isinstance(exc, SystemExit) else 1
    return code if isinstance(code, int) else (0 if code is None else 1)


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
    except SystemExit as exc:                 # what the process would exit with
        rc = _process_status(exc)
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


def test_a_failure_at_every_step_refuses_with_the_refused_status_identically(
        monkeypatch, tmp_path, box):
    """Every failing step is a precondition that was not met, so every one
    refuses with exit_contract.REFUSED on both transports.  Was `!= 0`,
    which a refusal exiting 1 satisfies."""
    _rc, calls, _ev = _run(monkeypatch, tmp_path, box, "vbox")
    n = len(calls)
    assert n >= 10
    for k in range(1, n + 1):
        rc_r, c_r, e_r = _run(monkeypatch, tmp_path, box, "vbox", fail_at=k)
        rc_l, c_l, e_l = _run(monkeypatch, tmp_path, box, "local", fail_at=k)
        assert rc_r == rc_l == REFUSED, (k, rc_r, rc_l)
        assert _transport_free(c_r, e_r) == _transport_free(c_l, e_l), k


def test_a_failed_vivado_run_is_followed_by_one_diagnostic_and_still_refuses(
        monkeypatch, tmp_path, box):
    """The `tail -40` after a failed Vivado run is a diagnostic, not a step:
    its own status is never consulted, and the run has already refused."""
    _rc, calls, _ev = _run(monkeypatch, tmp_path, box, "vbox")
    v = next(i for i, (_cmd, inp) in enumerate(calls, 1) if "vivado -mode batch" in (inp or ""))
    rc, c, _ev = _run(monkeypatch, tmp_path, box, "vbox", fail_at=v)
    assert rc == REFUSED
    assert len(c) == v + 1 and "tail -40" in c[v][0][-1]
    assert not any("vivado -mode batch" in (inp or "") for _cmd, inp in c[v:])


def test_the_post_run_digest_check_still_refuses_in_local_mode(monkeypatch, tmp_path, box):
    """A pulled file that differs from the box manifest is refused locally too."""
    (box / "dsp_cells_dump.txt").write_text("tampered after the manifest\n")
    rc, _calls, _ev = _run(monkeypatch, tmp_path, box, "local")
    assert rc == REFUSED


def test_a_precondition_refusal_reaches_a_real_process_as_exit_refused(tmp_path):
    """Through the real CLI, no fake transport: the box check runs `bash -c`
    against a checkpoint that is not there, and the PROCESS exits REFUSED
    with the label on stderr.  An in-process return value is one witness;
    this is what a runner or a `&&` chain actually sees."""
    r = subprocess.run(
        [sys.executable, str(HERE / "dsp_dpreg_extract.py"), "--box", "local",
         "--dcp", str(tmp_path / "no-such.dcp"), "--dcp-sha256", "0" * 64,
         "--drc-rpt", str(tmp_path / "no-such.rpt"), "--remote-dir", str(tmp_path / "remote"),
         "--evidence", str(tmp_path / "evidence" / "dsp-dpreg-evidence")],
        capture_output=True, text=True, timeout=120)
    assert r.returncode == REFUSED, (r.returncode, r.stdout[-400:], r.stderr[-400:])
    assert "REFUSED: box preconditions failed" in r.stderr
    assert "REFUSED" not in r.stdout            # stdout is the tool's data, not its refusals
    # refused at phase 1, before anything was staged, fetched or written as evidence
    ev = tmp_path / "evidence"
    assert sorted(p.name for p in ev.rglob("*")) == ["dsp-dpreg-evidence"]
    assert not (tmp_path / "remote").exists()


def _refused_by(fn, *args):
    """The process status of calling `fn`, which must raise SystemExit."""
    with pytest.raises(SystemExit) as exc:
        fn(*args)
    return _process_status(exc.value)


def test_the_binding_checks_refuse_with_the_refused_status(tmp_path, box, capsys):
    """verify_dcp_binding's five refusals and write_fetched_drc's one, each
    driven on its own.  All six were `raise SystemExit("REFUSED: ...")`,
    which exits 1."""
    ev = tmp_path / "pulled"
    shutil.copytree(box, ev)
    dcp, sha = DCP, DCP_SHA                # the path the shipped record and dump header name
    rec = ev / "routed_dcp.sha256"
    dump = ev / "dsp_cells_dump.txt"

    assert dx.verify_dcp_binding(ev, dcp, sha) == sha            # the control: untampered, it binds

    rec.unlink()
    assert _refused_by(dx.verify_dcp_binding, ev, dcp, sha) == REFUSED
    assert "no routed_dcp.sha256" in capsys.readouterr().err

    rec.write_text(f"not-a-digest  {dcp}\n")
    assert _refused_by(dx.verify_dcp_binding, ev, dcp, sha) == REFUSED
    assert "malformed routed_dcp.sha256" in capsys.readouterr().err

    rec.write_text(f"{'f' * 64}  {dcp}\n")
    assert _refused_by(dx.verify_dcp_binding, ev, dcp, sha) == REFUSED   # its label: the test below
    capsys.readouterr()

    rec.write_text(f"{sha}  /somewhere/else/routed.dcp\n")
    assert _refused_by(dx.verify_dcp_binding, ev, dcp, sha) == REFUSED
    assert "names /somewhere/else/routed.dcp" in capsys.readouterr().err

    rec.write_text(f"{sha}  {dcp}\n")
    text = dump.read_text().replace(f"DCP {dcp}", "DCP /other/routed.dcp", 1)
    dump.write_text(text)
    assert _refused_by(dx.verify_dcp_binding, ev, dcp, sha) == REFUSED
    assert "the dump opened" in capsys.readouterr().err

    dst = tmp_path / "drc.rpt"
    dst.write_text("a committed, host-scrubbed report\n")
    assert _refused_by(dx.write_fetched_drc, dst, "the box's report") == REFUSED
    assert "exists with different content" in capsys.readouterr().err


def test_the_binding_checks_leave_a_digest_mismatch_labelled(tmp_path, box, capsys):
    ev = tmp_path / "pulled"
    shutil.copytree(box, ev)
    (ev / "routed_dcp.sha256").write_text(f"{'f' * 64}  {DCP}\n")
    assert _refused_by(dx.verify_dcp_binding, ev, DCP, DCP_SHA) == REFUSED
    err = capsys.readouterr().err
    assert err.startswith("REFUSED: routed_dcp.sha256 digest ffff") and "!= asserted" in err


def test_a_tampered_box_digest_record_is_refused_end_to_end(monkeypatch, tmp_path, box):
    """The box record names another checkpoint and the box manifest is
    consistent with it: every transfer check passes, and the binding check
    is the one that refuses."""
    (box / "routed_dcp.sha256").write_text(f"{'f' * 64}  /box/arty/routed.dcp\n")
    (box / "MANIFEST.sha256").write_text("".join(
        f"{_sha((box / n).read_bytes())}  {n}\n" for n in dx.MANIFEST_FILES))
    rc, _calls, _ev = _run(monkeypatch, tmp_path, box, "local")
    assert rc == REFUSED


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
        monkeypatch, tmp_path, box, capsys):
    """That drc.rpt used to be expanded into duplicate cells, a Tcl staged and
    Vivado run, all for the analyser to refuse the dump (#1557)."""
    rc, calls, ev = _run(monkeypatch, tmp_path, box, "local",
                         drc=SAME_INSTANCE_TWICE)
    assert rc == REFUSED
    assert "REFUSED: drc.rpt flags the same instance twice" in capsys.readouterr().err
    issued = [" ".join(cmd) + (inp or "") for cmd, inp in calls]
    assert not any("vivado" in s or "dsp_dpreg_extract.tcl" in s
                   for s in issued)
    assert not (ev / "dsp_dpreg_extract.tcl").exists()


def test_an_unknown_flagged_instance_shape_refuses_before_any_tcl_is_staged(
        monkeypatch, tmp_path, box, capsys):
    """cell_targets' own refusal: a drc.rpt flagging an instance outside the
    two known families means the netlist mapping changed; STOP, never guess.
    One body, so the duplicate-instance refusal is not what fires."""
    one_unknown = (SAME_INSTANCE_TWICE.split("DPREG-4#2")[0]
                   .replace("p0/pair/dec/prod0__0", "somewhere/entirely_unrelated")
                   .replace("| 2      |", "| 1      |"))    # the summary must count the one body left
    assert "DPREG-4#2" not in one_unknown and "entirely_unrelated" in one_unknown
    rc, calls, ev = _run(monkeypatch, tmp_path, box, "local", drc=one_unknown)
    assert rc == REFUSED
    assert "flags an instance shape this derivation does not know" in capsys.readouterr().err
    assert not any("dsp_dpreg_extract.tcl" in " ".join(cmd) for cmd, _ in calls)


def test_no_refusal_in_the_tool_retypes_the_status():
    """By AST, not by text: the status is read from the contract, so the
    module holds no literal `return 1`/`return 2` and no SystemExit carrying
    a string (which would exit 1)."""
    src = (HERE / "dsp_dpreg_extract.py").read_text()
    assert "import exit_contract" in src
    tree = ast.parse(src)
    literal_returns = [n.lineno for n in ast.walk(tree) if isinstance(n, ast.Return)
                       and isinstance(n.value, ast.Constant) and n.value.value in (1, 2)]
    string_exits = [n.lineno for n in ast.walk(tree) if isinstance(n, ast.Raise)
                    and isinstance(n.exc, ast.Call) and n.exc.args
                    and isinstance(n.exc.args[0], (ast.Constant, ast.JoinedStr, ast.BinOp))]
    assert literal_returns == [] and string_exits == []
