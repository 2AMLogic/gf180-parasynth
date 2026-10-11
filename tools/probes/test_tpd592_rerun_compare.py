"""Tests for tools/probes/tpd592_rerun_compare.py (#592 record re-run comparator)."""
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import tpd592_rerun_compare as rc  # noqa: E402

REC = {"rows": {"LT50": {"E": 0.027205, "tau": 0.054678, "class": "MEASURED"}},
       "n": 3, "provenance": {"commit": "aaa"}}


def _write(tmp_path, name, obj):
    p = tmp_path / name
    p.write_text(json.dumps(obj))
    return str(p)


def test_identical_except_provenance_exits_0(tmp_path):
    other = json.loads(json.dumps(REC))
    other["provenance"] = {"commit": "bbb"}
    assert rc.main([_write(tmp_path, "a.json", REC), _write(tmp_path, "b.json", other)]) == 0


def test_a_last_digit_change_is_seen(tmp_path):
    other = json.loads(json.dumps(REC))
    other["rows"]["LT50"]["tau"] = 0.054679
    assert rc.main([_write(tmp_path, "a.json", REC), _write(tmp_path, "b.json", other)]) == 1


def test_a_class_change_and_a_missing_key_are_seen():
    other = json.loads(json.dumps(REC))
    other["rows"]["LT50"]["class"] = "REFUSED"
    del other["n"]
    d = rc.diff(REC, other)
    assert any("class" in x for x in d) and any("/n" in x for x in d)


def test_int_vs_float_is_a_difference():
    assert rc.diff({"x": 1}, {"x": 1.0})


def test_nested_provenance_is_compared():
    # only the top-level provenance block is excluded
    a = {"rows": {"provenance": 1}}
    assert rc.diff(a, {"rows": {"provenance": 2}})


def test_refuses_when_the_comparator_is_blind(tmp_path, monkeypatch):
    monkeypatch.setattr(rc, "diff", lambda a, b, path="", out=None: [])
    assert rc.main([_write(tmp_path, "a.json", REC), _write(tmp_path, "b.json", REC)]) == 2


def test_nudge_changes_exactly_one_thing():
    assert len(rc.diff(REC, rc._nudge(REC))) == 1
    assert len(rc.diff({"provenance": {"x": 1.0}}, rc._nudge({"provenance": {"x": 1.0}}))) == 1
