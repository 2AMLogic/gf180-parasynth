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
