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


def test_r2_differs_from_r1s_freeze_only_in_its_reviewed_set():
    rec = json.loads(r2.RECORD.read_text())
    assert list(rec["rtl"]["differs_from_r1_freeze"]) == ["fpga/boards/arty-a7-100.xdc",
                                                          "rtl-sketch/ladder_dp_n.v",
                                                          "rtl-sketch/polyblep_saw_pair.v",
                                                          "rtl-sketch/voice_dp.v"]
    assert rec["rtl"]["configuration"] == {"OSC2X": 1, "FILTER2X": 1, "PULSE2X": 1}
    assert rec["status"].startswith("CANDIDATE") and "OVERRIDE" in rec["status"]


def test_another_source_moving_refuses(monkeypatch):
    monkeypatch.setattr(r2, "EXPECTED_CHANGES", {})
    with pytest.raises(r2.Refused, match="beyond R2's stated changes"):
        r2.rtl()


def test_r1_records_are_not_written_by_this_module():
    src = (HERE / "r2_candidate.py").read_text()
    assert "r1-candidate.json\").write" not in src and "r1-2025.1.json\").write" not in src
    assert r2.RECORD.name == "r2-candidate.json"



# ---- bound to R2_FROZEN_AT, not the working tree (#658) ---------------------------
import shutil                                              # noqa: E402
import subprocess                                          # noqa: E402


def _moved_tree(tmp_path, rel="rtl-sketch/drum_kit.v", extra=None):
    """A copy of the working tree's frozen files with one byte changed (the
    input that defeats a working-tree binding), and optionally an added file."""
    rec = r2.rtl()
    for r in {**rec["sources"], **rec["roms"], **rec["constraints"]}:
        (tmp_path / r).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(r2.ROOT / r, tmp_path / r)
    with open(tmp_path / rel, "ab") as f:
        f.write(b"\n// post-R2 edit\n")
    for e in extra or []:
        (tmp_path / e).write_text("// added after R2\n")
    return tmp_path


def test_a_moved_working_tree_stays_bound_with_a_drift_note(monkeypatch, tmp_path):
    monkeypatch.setattr(r2, "TREE", _moved_tree(tmp_path, extra=["rtl-sketch/post_r2_new.v"]))
    verdict, detail = r2.check()
    assert verdict == "BOUND", detail
    assert "NOTE" in detail and "rtl-sketch/drum_kit.v" in detail
    assert r2.tree_drift() == ["rtl-sketch/drum_kit.v"]


def test_a_post_r2_edit_does_not_change_the_derivation(monkeypatch, tmp_path):
    before = r2.rtl()
    monkeypatch.setattr(r2, "TREE", _moved_tree(tmp_path))
    assert r2.rtl() == before


def test_the_real_working_tree_is_never_read_for_the_binding(monkeypatch):
    # the frozen path lists must not consult build_arty.sources()/roms()
    import build_arty as ba

    def boom():
        raise AssertionError("working-tree path list consulted")
    monkeypatch.setattr(ba, "sources", boom)
    monkeypatch.setattr(ba, "roms", boom)
    assert r2.check()[0] == "BOUND"


def test_a_tampered_frozen_hash_is_stale(tmp_path):
    rec = json.loads(r2.RECORD.read_text())
    rec["rtl"]["sources"]["rtl-sketch/voice_dp.v"] = "0" * 64
    bad = tmp_path / "r2-candidate.json"
    bad.write_text(json.dumps(rec))
    assert r2.check(bad)[0] == "STALE"


def test_a_different_freeze_commit_is_stale(monkeypatch):
    import r2_release
    monkeypatch.setattr(r2_release, "R2_FROZEN_AT", r2.rtl()["r1_freeze"])
    # R1's own commit lacks R2's stated changes: refused, never BOUND
    verdict, detail = r2.check()
    assert verdict == "REFUSED" and "absent from its freeze" in detail


def test_an_unreadable_freeze_refuses_not_a_traceback(monkeypatch):
    import r2_release
    monkeypatch.setattr(r2_release, "R2_FROZEN_AT", "0" * 40)
    verdict, detail = r2.check()
    assert verdict == "REFUSED" and "shallow clone" in detail


def test_the_freeze_is_an_ancestor_commit_in_this_clone():
    import r2_release
    r = subprocess.run(["git", "-C", str(r2.ROOT), "cat-file", "-t", r2_release.R2_FROZEN_AT],
                       capture_output=True, text=True)
    assert r.stdout.strip() == "commit"


def test_an_explicit_commit_is_read_in_place_of_the_module_freeze(monkeypatch):
    """rtl(commit), _frozen_paths(commit) and _frozen_blob(rel, commit) list and
    hash at the commit they are given; R2_FROZEN_AT is only the default."""
    import r2_release
    freeze = r2_release.R2_FROZEN_AT
    at_freeze, paths_at_freeze = r2.rtl(freeze), r2._frozen_paths(freeze)
    assert at_freeze == r2.rtl() and paths_at_freeze == r2._frozen_paths()
    monkeypatch.setattr(r2_release, "R2_FROZEN_AT", "0" * 40)
    assert r2.rtl(freeze) == at_freeze
    assert r2._frozen_paths(freeze) == paths_at_freeze
    with pytest.raises(r2.Refused, match="shallow clone"):
        r2.rtl()


def test_an_empty_freeze_is_refused_by_name_not_read_from_the_index(monkeypatch):
    """`git show :path` -- what an empty commit string builds -- reads the INDEX,
    the working tree's staged bytes, and returns them without a word. With no
    freeze there is nothing frozen to read: refuse, by name."""
    import r2_release
    monkeypatch.setattr(r2_release, "R2_FROZEN_AT", "")
    for read in (lambda: r2._frozen_blob("rtl-sketch/voice_dp.v", ""),
                 lambda: r2._frozen_blob("rtl-sketch/voice_dp.v"),
                 lambda: r2._frozen_paths(""),
                 lambda: r2._frozen_paths(),
                 lambda: r2.rtl()):
        with pytest.raises(r2.Refused, match="R2_FROZEN_AT is not set"):
            read()
    assert r2.check() == ("REFUSED", "R2_FROZEN_AT is not set: no R2 image has been built")


def test_a_fresh_derivation_is_the_committed_record_byte_for_byte():
    """What --write emits is the committed file, key order included. check()
    compares values; this compares the bytes."""
    fresh = (json.dumps(r2.build(), indent=1) + "\n").encode()
    assert fresh == r2.RECORD.read_bytes()
