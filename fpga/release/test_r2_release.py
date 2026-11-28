"""The R2 release manifest (fpga/release/r2_release.py): BOUND to the built
image, with R1 BOUND as its rollback; every refusal is for its reason."""
import contextlib
import io
import json
import shutil
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import r2_release as r2r                                   # noqa: E402
import release_manifest as rm                              # noqa: E402
import uart_host as uh                                     # noqa: E402


def test_the_committed_r2_manifest_is_bound():
    verdict, detail = r2r.check()
    assert verdict == "BOUND", detail


def test_r2_manifest_names_its_configuration_override_and_rollback():
    m = json.loads(r2r.MANIFEST.read_text())
    assert m["configuration"] == {"OSC2X": 1, "FILTER2X": 1, "PULSE2X": 1}
    assert m["image"]["image"] == "r2"
    assert "OVERRIDE" in m["status"] and "NOT ACCEPTED" in m["operator_override"]["rule_verdict"]
    assert len(m["operator_override"]["known_limitations"]) == 6
    assert m["rollback"]["name"] == "R1"
    assert sorted(m["image"]["differs_from_r1_freeze"]) == [
        "fpga/boards/arty-a7-100.xdc", "rtl-sketch/ladder_dp_n.v",
        "rtl-sketch/polyblep_saw_pair.v", "rtl-sketch/voice_dp.v"]
    assert all(c.get("equals_r1_bytes") for n, c in m["host"]["commands"].items()
               if n != "live-midi")


@pytest.mark.parametrize("case", ["image", "host"])
def test_stale_control_is_caught_at_exactly_its_fields(tmp_path, case):
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        rc = r2r.stale_control(case, tmp_path)
    assert rc == 1, out.getvalue()
    assert "stale_control: STALE" in out.getvalue()


def test_stale_control_host_reports_refused_when_the_cli_refuses(tmp_path, monkeypatch,
                                                                  capsys):
    """#784's confirmed site, in the R2 copy: release_manifest._capture
    refuses (a uart_host that exits non-zero on `run --fixture demo --image
    release`), and that refusal must reach stale_control's designed REFUSED
    verdict (exit 2), not escape as a traceback past the handler -- a sibling
    RuntimeError subclass does not match `except Refused`. The CLI's own
    reason is asserted too: any OTHER refusal (a missing manifest, a copy
    that is not BOUND) exits 2 with the same verdict line."""
    def refused(argv):
        print("uart_host: unsupported command", file=sys.stderr)
        return 1

    monkeypatch.setattr(uh, "main", refused)
    assert r2r.stale_control("host", tmp_path) == 2
    out = capsys.readouterr().out
    assert "stale_control: REFUSED" in out
    assert "uart_host: unsupported command" in out


def test_a_non_refusal_error_from_the_capture_still_propagates(tmp_path, monkeypatch):
    """The control for the test above: the handler catches refusals by class
    and nothing wider (tools/refusal.py). An implementation error at the same
    call site is an EXECUTION ERROR; it must leave stale_control untouched,
    never become `stale_control: REFUSED`."""
    def broke(argv, outdir):
        raise ValueError("not a refusal: the capture broke")

    monkeypatch.setattr(rm, "_capture", broke)
    with pytest.raises(ValueError, match="the capture broke"):
        r2r.stale_control("host", tmp_path)


def test_an_unbound_rollback_refuses(monkeypatch):
    monkeypatch.setattr(r2r.r1r, "check", lambda *a, **k: ("STALE", "moved"))
    verdict, detail = r2r.check()
    assert verdict == "REFUSED" and "rollback R1 is not BOUND" in detail, detail


def test_a_run_without_the_pulse2x_define_is_not_r2_evidence(tmp_path, monkeypatch):
    src = r2r.EVIDENCE_DIR
    dst = tmp_path / "r2"
    shutil.copytree(src, dst)
    ident = next(dst.rglob("*run_identity.json"))
    rec = json.loads(ident.read_text())
    rec["identity"]["defines"] = [d for d in rec["identity"]["defines"]
                                  if d != r2r.PULSE2X_DEFINE]
    ident.write_text(json.dumps(rec))
    monkeypatch.setattr(r2r, "EVIDENCE_DIR", dst)
    with pytest.raises(r2r.Refused, match="lack VOICE_PULSE_2X"):
        r2r.evidence()


def test_r2_does_not_write_r1_or_r0_records():
    src = (HERE / "r2_release.py").read_text()
    for name in ("r1-2025.1.json", "r1-candidate.json", "baseline-2025.1.json"):
        assert f'{name}").write' not in src


# ---- a committed manifest that cannot be read is REFUSED, not a traceback (#1621)

def _manifest_at(monkeypatch, tmp_path, record):
    """r2_release.MANIFEST pointed at a scratch copy holding `record`."""
    p = tmp_path / "r2-manifest.json"
    p.write_text(json.dumps(record))
    monkeypatch.setattr(r2r, "MANIFEST", p)


@pytest.mark.parametrize("key,case", [("image", "image"), ("host", "host")])
@pytest.mark.parametrize("how", ["missing", "null"])
def test_a_manifest_lacking_a_key_the_stale_control_substitutes_is_refused(
        monkeypatch, tmp_path, capsys, key, case, how):
    """`stale["image"]` / `stale["host"]` were read off the committed manifest
    before anything checked it, so a partial manifest was a `KeyError`
    traceback (exit 1) past the handler (#784's site, in the R2 copy)."""
    record = json.loads(r2r.MANIFEST.read_text())
    if how == "missing":
        record.pop(key)
    else:
        record[key] = None
    _manifest_at(monkeypatch, tmp_path, record)
    rc = r2r.main(["stale-control", case, "--out", str(tmp_path / "out")])
    out = capsys.readouterr().out
    assert rc == 2, out
    assert "stale_control: REFUSED" in out and repr(key) in out, out
    assert not (tmp_path / "out").exists()


@pytest.mark.parametrize("whole", [{}, [], 5, None], ids=["empty", "list", "number", "null"])
def test_a_manifest_that_is_no_object_is_refused_by_the_stale_control(
        monkeypatch, tmp_path, capsys, whole):
    _manifest_at(monkeypatch, tmp_path, whole)
    rc = r2r.main(["stale-control", "image", "--out", str(tmp_path / "out")])
    out = capsys.readouterr().out
    assert rc == 2 and "stale_control: REFUSED" in out, out
    if whole == {}:
        assert "'image'" in out and "'host'" in out, out
    assert not (tmp_path / "out").exists()


def test_the_committed_manifest_carries_the_keys_the_stale_control_substitutes():
    # the guards above must be satisfiable against the record that ships
    m = json.loads(r2r.MANIFEST.read_text())
    assert m["image"] is not None and m["host"] is not None
