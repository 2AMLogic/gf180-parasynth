"""The R2 candidate record (DRAFT): bound to this tree, never touching R1's."""
import json
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import r2_candidate as r2                                  # noqa: E402


def test_the_committed_r2_record_is_bound():
    verdict, detail = r2.check()
    assert verdict == "BOUND", detail


def test_r2_differs_from_r1s_freeze_only_in_its_two_stated_changes():
    rec = json.loads(r2.RECORD.read_text())
    assert list(rec["rtl"]["differs_from_r1_freeze"]) == ["fpga/boards/arty-a7-100.xdc",
                                                          "rtl-sketch/polyblep_saw_pair.v",
                                                          "rtl-sketch/voice_dp.v"]
    assert rec["rtl"]["configuration"] == {"OSC2X": 1, "FILTER2X": 1, "PULSE2X": 1}
    assert rec["status"].startswith("DRAFT")


def test_another_source_moving_refuses(monkeypatch):
    monkeypatch.setattr(r2, "EXPECTED_CHANGES", {})
    with pytest.raises(r2.Refused, match="beyond R2's stated changes"):
        r2.rtl()


def test_r1_records_are_not_written_by_this_module():
    src = (HERE / "r2_candidate.py").read_text()
    assert "r1-candidate.json\").write" not in src and "r1-2025.1.json\").write" not in src
    assert r2.RECORD.name == "r2-candidate.json"
