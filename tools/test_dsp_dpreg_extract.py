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
import re
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


@pytest.mark.parametrize("manifest_body", [
    "",                                                    # zero-byte manifest (#1469)
    "not-a-valid-sha256-line\n",                           # malformed line
    "0" * 64 + "  unexpected.txt\n",
    "ONLY_ROUTED",                                         # Rule 8: non-empty, omits dsp_cells_dump.txt
])
def test_empty_or_partial_box_manifest_refuses_even_when_dcp_header_matches(
        monkeypatch, tmp_path, box, capsys, manifest_body):
    """#1469: phase 4 derived OK from `bad == []` over `MANIFEST.sha256` lines.
    With an empty or partial manifest and a tampered dump (preserving line 2's
    `DCP ...`), zero dump hashes were checked and `main` exited 0 ('extraction OK')."""
    orig_lines = (box / "dsp_cells_dump.txt").read_text().splitlines()
    (box / "dsp_cells_dump.txt").write_text(
        "\n".join(orig_lines[:2] + ["TAMPERED BODY"]) + "\n")
    if manifest_body == "ONLY_ROUTED":
        h = _sha((box / "routed_dcp.sha256").read_bytes())
        (box / "MANIFEST.sha256").write_text(f"{h}  routed_dcp.sha256\n")
    else:
        (box / "MANIFEST.sha256").write_text(manifest_body)
    rc, _calls, _ev = _run(monkeypatch, tmp_path, box, "local")
    out = capsys.readouterr().out
    assert rc == 2
    assert "REFUSED" in out and "extraction OK" not in out


def test_header_only_dump_with_a_complete_manifest_refuses(monkeypatch, tmp_path, box, capsys):
    """#1469 review: keep the first two lines (incl. the correct DCP line) of
    dsp_cells_dump.txt and regenerate every digest. The manifest is complete and
    the digests match, yet no cell section was returned: it printed `cells
    dumped : 0` then `extraction OK` and exited 0."""
    lines = (box / "dsp_cells_dump.txt").read_text().splitlines()
    (box / "dsp_cells_dump.txt").write_text("\n".join(lines[:2]) + "\n")
    (box / "MANIFEST.sha256").write_text("".join(
        f"{_sha((box / n).read_bytes())}  {n}\n" for n in dx.MANIFEST_FILES))
    rc, _calls, _ev = _run(monkeypatch, tmp_path, box, "local")
    out = capsys.readouterr().out
    assert rc == 2
    assert "REFUSED" in out and "cell sections absent" in out and "extraction OK" not in out


@pytest.mark.parametrize(("defect", "reason"), [
    ("empty-sections", "no OPMODE pins"),
    ("last-sibling-without-site", "missing mandatory properties"),
    ("unindexed-opmode", "OPMODE pin"),
    ("invalid-pin-name", "pin name"),
    ("swapped-end-markers", "END CELL"),
])
def test_digest_correct_sections_require_parsed_cell_evidence(
        monkeypatch, tmp_path, box, capsys, defect, reason):
    path = box / "dsp_cells_dump.txt"
    dump = path.read_text()
    if defect == "empty-sections":
        lines = dump.splitlines()
        markers = [line for line in lines if line.startswith(("==== CELL ", "==== END CELL "))]
        assert len(markers) == 38
        dump = "\n".join(lines[:2] + markers) + "\n"
    elif defect == "last-sibling-without-site":
        head, section = dump.rsplit("==== CELL ", 1)
        section, n = re.subn(r"^SITE = .+\n", "", section, flags=re.M)
        assert n == 1
        dump = head + "==== CELL " + section
    elif defect == "swapped-end-markers":
        names = re.findall(r"^==== END CELL (\S+) ====$", dump, re.M)
        first, second = names[:2]
        swapped = {first: second, second: first}
        dump = re.sub(r"^==== END CELL (\S+) ====$",
                      lambda m: f"==== END CELL {swapped.get(m[1], m[1])} ====",
                      dump, flags=re.M)
    else:
        assert "PIN OPMODE[0] dir=" in dump
        pin = "OPMODE" if defect == "unindexed-opmode" else "[0]"
        dump = dump.replace("PIN OPMODE[0] dir=", f"PIN {pin} dir=", 1)
    path.write_text(dump)
    (box / "MANIFEST.sha256").write_text("".join(
        f"{_sha((box / n).read_bytes())}  {n}\n" for n in dx.MANIFEST_FILES))
    rc, _calls, _ev = _run(monkeypatch, tmp_path, box, "local")
    out = capsys.readouterr().out
    assert rc == 2
    assert "REFUSED" in out and reason in out and "extraction OK" not in out


def test_dump_cell_problems_names_the_missing_or_unexpected_section():
    dump = "==== CELL a ====\n==== END CELL a ====\n==== CELL b ====\n==== END CELL b ====\n"
    assert "cell sections absent" in dx.dump_cell_problems(dump, ["a", "b", "c"])[0]
    assert "unrequested cell sections" in dx.dump_cell_problems(dump, ["a"])[0]
    assert "no target cells" in dx.dump_cell_problems(dump, [])[0]
    assert "missing END CELL" in dx.dump_cell_problems(dump.removesuffix("==== END CELL b ====\n"),
                                                       ["a", "b"])[0]


def test_phase3_manifest_command_does_not_mask_sha256sum_failure(monkeypatch, tmp_path, box):
    """Phase 3 ended `&& grep -c '^ERROR' vivado_extract.log || true` unparenthesised,
    so a failed `sha256sum` after `> MANIFEST.sha256` truncated the manifest to
    zero bytes still exited 0 under `bash -c`."""
    _rc, calls, _ev = _run(monkeypatch, tmp_path, box, "local")
    phase3_cmd = next(c[0][-1] for c in calls if "MANIFEST.sha256" in c[0][-1] and c[0][0] == "bash")
    work = tmp_path / "phase3-shell"
    work.mkdir()
    # Missing MANIFEST_FILES makes sha256sum fail after `> MANIFEST.sha256` truncates it:
    rewritten = phase3_cmd.replace(f"cd {REMOTE}", f"cd {work}")
    r = subprocess.run(["bash", "-c", rewritten], capture_output=True, text=True)
    assert r.returncode != 0, f"sha256sum failure was masked (rc=0): {rewritten}"
