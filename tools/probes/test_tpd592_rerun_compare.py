"""Tests for tools/probes/tpd592_rerun_compare.py (#592 record re-run comparator)."""
import json
import pathlib
import sys

import pytest

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


# ---- a record that cannot be read is REFUSED, not a traceback (#1757) ------
@pytest.mark.parametrize("side", ["old", "new"])
@pytest.mark.parametrize("record,kind", [([], "list"), (5, "int"), ("x", "str"), (None, "null"), ({}, "dict")],
                         ids=["list", "int", "str", "null", "dict"])
def test_cli_record_that_cannot_be_read_is_refused(tmp_path, capsys, record, kind, side):
    """Either argument alone. Before the repair a wrong-typed NEW was exit 1 by
    TypeError at `_nudge`'s item assignment, a wrong-typed OLD was exit 1 as
    "1 difference" (FAIL: the comparison ran and disagreed), and `{}` as NEW
    was exit 0, IDENTICAL."""
    good, bad = _write(tmp_path, "good.json", REC), _write(tmp_path, "bad.json", record)
    code = rc.main([bad, good] if side == "old" else [good, bad])
    out = capsys.readouterr().out
    assert code == 2 and "REFUSED" in out and "bad.json" in out, out
    assert "IDENTICAL" not in out
