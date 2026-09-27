"""tools/test_dsp_dpreg_extract.py -- `--box local` (#316) is the ssh path with another transport.

A fake transport stands in for the box: a directory holding R1's shipped DSP
evidence (fpga/reports/arty/r1-player-preview-2025.1/dsp-dpreg-evidence), bound
to the shipped drc.rpt. Every command the tool issues is recorded. Remote and
local runs must issue the same steps with the same scripts and inputs, reach the
same result, and fail at the same step with the same exit status when any step
fails. Only the transport argv may differ (ssh/scp vs bash/cp).
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


def _fake(box_dir, fail_at=None):
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
            out = (PUB / "drc.rpt").read_text()
        return subprocess.CompletedProcess(cmd, rc, out, "")
    return calls, sh


def _run(monkeypatch, tmp_path, box_dir, where, fail_at=None):
    calls, sh = _fake(box_dir, fail_at)
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
