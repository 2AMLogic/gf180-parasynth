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


# ---- #2043: a record whose commit is not on origin/main ---------------------

def _committed():
    return json.loads((ROOT / "docs" / "scorecard" / "results" / "D02A.json").read_text())


def _citing(commit):
    """The committed D02A, citing `commit` instead of the one it was
    written at -- the only change."""
    record = _committed()
    record["source_commit"] = commit
    record["provenance"]["worktree"]["commit"] = commit
    return record


def _only_record(monkeypatch, record):
    monkeypatch.setattr(r1s.sc, "load_cases", lambda: [{"case_id": "D02A", "subject": "D02A"}])
    monkeypatch.setattr(r1s.sc, "load_result", lambda case_id: record)


def test_the_sound_rows_still_score_the_committed_record_whose_commit_is_on_main():
    row = next(r for r in r1s.sound_rows() if r["case"] == "D02A")
    assert row["state"] == r1s.sc.PASS


def test_a_sound_row_whose_commit_no_checkout_has_is_refused(monkeypatch):
    """git's null id is in no object store, so this holds in any clone. The
    row is no verdict with no properties -- not a pass, and not a fail."""
    _only_record(monkeypatch, _citing("0" * 40))
    row, = r1s.sound_rows()
    assert row["state"] == r1s.sc.NO_VERDICT and row["props"] == {}
    assert row["why"] == f"REFUSED: {'0' * 12} is not on origin/main (answer: None)"
    assert row["commit"] == "00000000"


def test_a_sound_row_citing_a_moving_name_as_its_commit_is_refused(monkeypatch):
    """`origin/main` is an ancestor of itself in every clone, which is why
    the board may not simply ask git about the string a record holds."""
    _only_record(monkeypatch, _citing("origin/main"))
    row, = r1s.sound_rows()
    assert row["state"] == r1s.sc.NO_VERDICT
    assert row["why"] == "REFUSED: origin/main is not on origin/main (answer: None)"


def _rendered():
    return r1s.render(json.loads(r1s.CANDIDATE.read_text()),
                      json.loads(r1s.SUMMARY.read_text()), release=ev())


def test_the_rendered_view_says_a_row_is_refused_and_how_many(monkeypatch):
    _only_record(monkeypatch, _citing("0" * 40))
    text = _rendered()
    row = next(line for line in text.splitlines() if "| `00000000` |" in line)
    assert " | no verdict | REFUSED: 000000000000 is not on origin/main" in row
    assert "1 no verdict, 0 not run of 1" in text
    assert (f"**1 of these rows is refused:** {r1s.sc.REFUSED_MEANS}.") in text


def test_the_rendered_view_says_nothing_about_refusal_when_nothing_is_refused(monkeypatch):
    _only_record(monkeypatch, _committed())
    text = _rendered()
    assert "refused:**" not in text and "REFUSED: " not in text
