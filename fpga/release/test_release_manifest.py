"""The release manifest is bound to the tree, and each way the selected
artifacts can disagree turns it REFUSED or STALE for its own reason."""
import hashlib
import json
import os
import shutil
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
for _p in (HERE, HERE.parent):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import release_manifest as rm            # noqa: E402


def _sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


@pytest.fixture(scope="module")
def fresh():
    return json.loads(json.dumps(rm.build()))


def test_committed_manifest_is_bound_to_this_tree():
    verdict, detail = rm.check()
    assert verdict == "BOUND", detail


def test_selected_artifacts_agree(fresh):
    img = fresh["image"]
    assert img["configuration"] == {"OSC2X": 1, "FILTER2X": 1, "PULSE2X": 0}
    assert img["bitstream_sha256"].startswith("a66c9349")
    assert img["routed_dcp_sha256"].startswith("6c3c22c5")
    assert img["tree_source_drift"] == {}
    assert img["dsp_feedback_review"]["complete"] is True
    for fx, b in fresh["evidence"]["rolling_playback"].items():
        assert b["cli_bytes_equal_recorded_replay"] and b["replay_rtl_sources_match_image"], fx
    for p, h in fresh["evidence"]["held_note"].items():
        assert h["verdict"] == "PASS" and h["i2s_peak_lsb"] >= 1024, p
        assert h["replay_capture_sha256"] == fresh["commands"][h["command"]]["cmds_sha256"]
    assert fresh["evidence"]["glide_boundary"]["verdicts"]["wrapper:control-247"]["met"]


def test_every_supported_command_passes_the_validator(fresh):
    for name, c in fresh["commands"].items():
        assert c["validator"] and "release domain OK" in c["validator"], name


def test_exclusions_and_pending_evidence_are_explicit(fresh):
    whats = " ".join(e["what"] for e in fresh["exclusions"])
    for needle in ("PULSE2X=1", "#247", "surge-type2-clean-v1"):
        assert needle in whats
    assert fresh["runtime_qualification"]["deadline"]["status"].startswith("PENDING")
    assert fresh["physical_capture"]["status"] == "NONE"


# ---- controls: each disagreement refuses for its own reason ----------------------------
def test_edited_manifest_is_stale(tmp_path):
    m = json.loads(rm.MANIFEST.read_text())
    m["image"]["bitstream_sha256"] = "0" * 64
    p = tmp_path / "m.json"
    p.write_text(json.dumps(m))
    verdict, detail = rm.check(p)
    assert verdict == "STALE" and "image.bitstream_sha256" in detail


def _copy_pub(tmp_path):
    d = tmp_path / "pub"
    shutil.copytree(rm.PUB_DIR, d)
    return d


def test_bitstream_that_is_not_the_published_one_refuses(tmp_path, monkeypatch):
    d = _copy_pub(tmp_path)
    b = bytearray((d / "arty.bit").read_bytes())
    b[-1] ^= 1
    (d / "arty.bit").write_bytes(bytes(b))
    monkeypatch.setattr(rm, "PUB_DIR", d)
    with pytest.raises(rm.Refused, match="arty.bit hashes to"):
        rm.image_identity()


def test_dsp_evidence_bound_to_another_checkpoint_refuses(tmp_path, monkeypatch):
    d = _copy_pub(tmp_path)
    pub = json.loads((d / "publication.json").read_text())
    pub["dsp_disposition"]["routed_dcp_sha256"] = "f" * 64
    (d / "publication.json").write_text(json.dumps(pub))
    monkeypatch.setattr(rm, "PUB_DIR", d)
    with pytest.raises(rm.Refused, match="routed.dcp digest disagrees"):
        rm.image_identity()


def test_tree_rtl_that_differs_from_the_image_refuses(tmp_path, monkeypatch):
    d = _copy_pub(tmp_path)
    pub = json.loads((d / "publication.json").read_text())
    rep = json.loads((d / "report.json").read_text())
    for r in (pub, rep):
        r["source_sha256"]["rtl-sketch/voice_dp.v"] = "e" * 64
    (d / "report.json").write_text(json.dumps(rep))
    pub["published_sha256"]["report.json"] = _sha(d / "report.json")
    (d / "publication.json").write_text(json.dumps(pub))
    monkeypatch.setattr(rm, "PUB_DIR", d)
    assert "rtl-sketch/voice_dp.v" in rm.image_identity()["tree_source_drift"]
    verdict, detail = rm.check()
    assert verdict == "REFUSED" and "voice_dp.v" in detail


def test_recorded_replay_that_is_not_the_clis_bytes_refuses(tmp_path, monkeypatch):
    d = tmp_path / "rolling"
    shutil.copytree(rm.ROLLING_DIR, d)
    p = d / "demo" / "rtl-replay" / "demo.cmds"
    lines = p.read_text().splitlines()
    lines[5] = lines[5][:-1] + ("0" if lines[5][-1] != "0" else "1")
    p.write_text("\n".join(lines) + "\n")
    monkeypatch.setattr(rm, "ROLLING_DIR", d)
    assert rm.rolling_binding("demo")["cli_bytes_equal_recorded_replay"] is False


def test_held_note_evidence_of_other_bytes_refuses(tmp_path, monkeypatch):
    d = tmp_path / "evidence"
    shutil.copytree(rm.EVIDENCE_DIR, d)
    cap = d / "held-note" / "default" / "capture.cmds"
    # the pre-fix (silent) bytes' replay is not evidence for the shipped command
    shutil.copy(rm.EVIDENCE_DIR / "held-note" / "default-legacy" / "capture.cmds", cap)
    monkeypatch.setattr(rm, "EVIDENCE_DIR", d)
    verdict, detail = rm.check()
    assert verdict == "REFUSED" and "not what the CLI emits now" in detail
