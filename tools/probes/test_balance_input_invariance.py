"""Controls for the comparison in tools/probes/balance_input_invariance.py (#803).

The probe is verdict-bearing: docs/scorecard/cymbal-369/balance/README.md cites
its `0 register addresses moved` / `cy_render_inputs_identical` verdicts four
times (#412, #410, #417/#418, #420) as the reason a shipped record was not
re-rendered. Before #803 the per-image verdict was derived from a hand-rolled
`a.get(k) != b.get(k)` walk, so a key present on exactly one side with `None`
on the other read as *identical* -- and an absent side was reported as a `null`
value even where the verdict was right. These controls drive `compare()`
itself, with both collection subprocesses replaced, so the shipped comparison
is what is under test, not a copy of it.

Red-first record: every test here was run against the pre-#803 probe and
failed there (absent-vs-null read `identical=True`; absent sides reported
`None`; no precondition refused non-int values) before the fix landed.
"""
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import balance_input_invariance as bii  # noqa: E402


def _payload(images):
    return {"images": images,
            "address_names": {"101": "E_RSG+1"},
            "address_owners": {"101": ["CY"]},
            "cy_env_peak_addrs": []}


def _compare(monkeypatch, old_images, new_images):
    """compare() with both --emit subprocesses replaced by canned payloads."""
    old, new = _payload({"kit_808": old_images}), _payload({"kit_808": new_images})
    monkeypatch.setattr(bii, "_emit_for",
                        lambda path: old if path is not None else new)
    return bii.compare("HEAD")


# (a) the hole: a key absent on one side, held as null on the other, used to
# read `identical=True` -- the wrong verdict, silently.
def test_absent_vs_held_null_is_moved_not_identical(monkeypatch):
    res = _compare(monkeypatch, {"100": 5}, {"100": 5, "101": None})
    assert res["images"]["kit_808"]["identical"] is False
    assert res["images"]["kit_808"]["n_moved"] == 1
    d = res["moved_addresses"]["101"]
    assert d["old"] is bii.ABSENT
    assert d["new"] is None


# (b) the case the old expression got right on the verdict (None != 7) but
# still reported the absent side as a null VALUE. It must not regress.
def test_absent_vs_value_is_moved_and_absent_is_not_reported_as_null(monkeypatch):
    res = _compare(monkeypatch, {"100": 5}, {"100": 5, "101": 7})
    assert res["images"]["kit_808"]["identical"] is False
    d = res["moved_addresses"]["101"]
    assert d["old"] is bii.ABSENT
    assert d["new"] == 7


def test_key_only_on_the_old_side_reports_absent_on_new(monkeypatch):
    res = _compare(monkeypatch, {"100": 5, "101": 7}, {"100": 5})
    assert res["images"]["kit_808"]["identical"] is False
    d = res["moved_addresses"]["101"]
    assert d["old"] == 7
    assert d["new"] is bii.ABSENT


def test_identical_images_stay_identical(monkeypatch):
    res = _compare(monkeypatch, {"100": 5, "101": 7}, {"100": 5, "101": 7})
    assert res["images"]["kit_808"] == {"identical": True, "n_moved": 0}
    assert res["moved_addresses"] == {}


def test_both_sides_empty_is_identical(monkeypatch):
    res = _compare(monkeypatch, {}, {})
    assert res["images"]["kit_808"] == {"identical": True, "n_moved": 0}
    assert res["moved_addresses"] == {}


def test_the_comparison_the_probe_delegates_to_is_type_strict():
    # record_compare's DECISION: 1 / True / 1.0 are three different records.
    assert bii.record_compare.diff({"100": 1}, {"100": True})
    assert bii.record_compare.diff({"100": 1}, {"100": 1.0})
    assert bii.record_compare.diff({"100": 1}, {"100": 1, "101": None})
    assert not bii.record_compare.diff({"100": 1}, {"100": 1})


# The precondition whose absence was the reason the hole above was only
# latent: the measured domain is int register words, and the probe must REFUSE
# rather than answer when a value leaves it.
def test_a_bool_value_refuses_rather_than_answers(monkeypatch):
    with pytest.raises(bii.Refused):
        _compare(monkeypatch, {"100": 1}, {"100": True})


def test_a_float_value_refuses_rather_than_answers(monkeypatch):
    with pytest.raises(bii.Refused):
        _compare(monkeypatch, {"100": 1}, {"100": 1.0})


def test_a_string_value_refuses_rather_than_answers(monkeypatch):
    with pytest.raises(bii.Refused):
        _compare(monkeypatch, {"100": 1}, {"100": "5"})


def test_refusal_names_the_tree_and_the_value(monkeypatch):
    with pytest.raises(bii.Refused, match="baseline HEAD.*1\\.0"):
        _compare(monkeypatch, {"100": 1.0}, {"100": 1})
