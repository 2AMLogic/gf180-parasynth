"""tools/r1_scorecard.py's image claims come from VALIDATED release evidence (#319)."""
import copy
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import r1_scorecard as r1s                                 # noqa: E402

MANIFEST = json.loads(r1s.RELEASE.read_text())
CLAIMS = ("external I/O qualified", "DSP review complete")


def ev(verdict="BOUND", edit=None):
    m = copy.deepcopy(MANIFEST)
    if edit:
        edit(m)
    return {"verdict": verdict, "detail": "test", **({"manifest": m} if verdict == "BOUND" else {})}


def test_a_bound_manifest_with_both_flags_claims_both():
    image, physical = r1s.image_phrase(ev())
    assert all(c in image for c in CLAIMS) and "BOUND" in image
    assert MANIFEST["image"]["bitstream_sha256"][:12] in physical


@pytest.mark.parametrize("verdict", ["STALE", "REFUSED"])
def test_stale_or_refused_evidence_claims_nothing(verdict):
    image, physical = r1s.image_phrase(ev(verdict))
    assert not any(c in image for c in CLAIMS)
    assert "NOT claimed" in image and verdict in image and verdict in physical


@pytest.mark.parametrize("edit,absent", [
    (lambda m: m["image"].update(external_io_timing_qualified=False), CLAIMS[0]),
    (lambda m: m["image"].pop("external_io_timing_qualified"), CLAIMS[0]),
    (lambda m: m["external_io"].update(state="FAIL"), CLAIMS[0]),
    (lambda m: m["external_io"]["control"].update(caught=False), CLAIMS[0]),
    (lambda m: m["image"]["dsp_feedback_review"].update(complete=False), CLAIMS[1]),
    (lambda m: m["image"].pop("dsp_feedback_review"), CLAIMS[1]),
])
def test_a_false_or_missing_flag_is_not_claimed(edit, absent):
    image, _ = r1s.image_phrase(ev(edit=edit))
    assert absent not in image and absent.replace(" qualified", " NOT qualified").replace(
        " complete", " NOT complete") in image


def test_no_manifest_file_claims_nothing(monkeypatch, tmp_path):
    monkeypatch.setattr(r1s, "RELEASE", tmp_path / "absent.json")
    e = r1s.release_evidence()
    assert e["verdict"] == "REFUSED" and "manifest" not in e
    assert not any(c in r1s.image_phrase(e)[0] for c in CLAIMS)


def test_the_committed_view_is_current_and_claims_from_a_bound_manifest():
    e = r1s.release_evidence()
    assert e["verdict"] == "BOUND", e["detail"]
    assert r1s.main(["--check"]) == 0
