"""Validation cases for tools/probes/tpd592_drift_report.py: what counts as a
numeric drift, what is reported as a changed verdict, and what is never
silently dropped."""
import json
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import tpd592_drift_report as dr  # noqa: E402


def _rec(e=0.0, f=90.0, cls="NO-DROP", ok=True, n_ok=1):
    return {"n": 1, "n_ok": n_ok, "rows": [
        {"case": {"family": "base", "E": 0.0, "f_inf": 90.0}, "class": cls, "ok": ok, "why": "", "E": e, "f_inf": f}]}


def test_a_numeric_drift_is_counted_per_field_with_its_class_and_largest_change():
    num, structural, summary = dr.classify(_rec(-8e-06, 90.000502), _rec(8.2e-05, 90.000483))
    assert [num[k] for k in ("E", "f_inf")] == [[(0, "NO-DROP", 0.0, pytest.approx(9.0e-05))],
                                                [(0, "NO-DROP", 0.0, pytest.approx(1.9e-05))]]
    assert structural == [] and summary == []
    out = dr.report(_rec(-8e-06), _rec(8.2e-05))
    assert "E: 1 rows; class NO-DROP; case E {0}; max |change| 9e-05" in out
    assert "structural differences: none" in out and "summary fields that differ: none" in out


def test_a_changed_verdict_class_or_reason_is_structural_not_a_numeric_leaf():
    a = _rec(ok=True)
    for other, needle in ((_rec(ok=False), "rows[0]/ok: True -> False"),
                          (_rec(cls="DROP"), "rows[0]/class: 'NO-DROP' -> 'DROP'")):
        num, structural, _ = dr.classify(a, other)
        assert not num and needle in structural
    one = _rec(ok=True)       # DEFEATING INPUT for `u == v`: Python's True == 1, so a
    one["rows"][0]["ok"] = 1   # verdict that turned into a number must not read as unchanged
    num, structural, _ = dr.classify(a, one)
    assert not num and structural == ["rows[0]/ok: True -> 1"]


def test_a_changed_identity_a_changed_summary_and_a_lost_row_are_all_reported():
    a, b = _rec(), _rec()
    b["rows"][0]["case"] = dict(a["rows"][0]["case"], E=0.06)
    num, structural, _ = dr.classify(a, b)
    assert not num and len(structural) == 1
    assert structural[0].startswith("rows[0]/case: ") and "E': 0.06" in structural[0]
    b = _rec(n_ok=0)                      # a summary counter moved; no row did
    num, structural, summary = dr.classify(a, b)
    assert not num and structural == [] and summary == ["n_ok"]
    b = _rec()                            # ... and the same conflation on a summary field
    b["n_ok"] = True
    assert dr.classify(a, b)[2] == ["n_ok"]
    b["rows"] = []                        # a lost row is never read as "nothing differs"
    assert dr.classify(a, b)[1] == ["rows: 1 -> 0"]
    assert "structural differences: rows: 1 -> 0" in dr.report(a, b)


def test_cli_reports_both_kinds_and_always_exits_zero(tmp_path, capsys):
    a, b = tmp_path / "a.json", tmp_path / "b.json"
    a.write_text(json.dumps(_rec(ok=True, n_ok=1)))
    b.write_text(json.dumps(_rec(e=1e-05, ok=False, n_ok=0)))
    assert dr.main([str(a), str(b)]) == 0
    out = capsys.readouterr().out
    assert "E: 1 rows" in out and "rows[0]/ok: True -> False" in out
    assert "summary fields that differ: n_ok" in out
    assert dr.main([str(a)]) == 2      # one path is not a comparison
