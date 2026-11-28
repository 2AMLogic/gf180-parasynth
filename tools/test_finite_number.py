"""Known answers for tools/finite_number.py (#718): the one definition of "is this
a finite measurement?" that four hand-copied guards had drifted away from."""
from __future__ import annotations

import pathlib
import sys

import numpy as np
import pytest

sys.path[:0] = [str(pathlib.Path(__file__).resolve().parent)]
from finite_number import is_finite_number, non_finite  # noqa: E402


@pytest.mark.parametrize("x", [0, 1, -3, 0.0, 2.5, np.float32(1), np.float64(-2.5), np.int32(7)])
def test_real_finite_numbers_are_accepted(x):
    assert is_finite_number(x) is True


@pytest.mark.parametrize("x", [True, False, np.bool_(True), "1", "nan", None, [1.0], (1.0,),
                               float("nan"), float("inf"), -float("inf"), np.float32("nan"),
                               np.float64("inf"), 1 + 0j, b"1"])
def test_everything_else_is_rejected(x):
    assert is_finite_number(x) is False


def test_non_finite_passes_every_finite_number_and_names_nothing():
    """#1994: `non_finite` is the form a record reader calls before it
    compares. These are its known answers; the nine repaired readers' own
    controls exercise it where it is used."""
    assert non_finite({"a": 0, "b": -2.5, "c": np.float32(1), "d": np.int64(7), "e": 1e300}) == []


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), -float("inf"), np.float32("nan"),
                                 np.float64("inf")])
def test_non_finite_names_a_non_finite_number_by_its_field(bad):
    assert [s.split(" = ")[0] for s in non_finite({"the field": bad, "fine": 1.0})] == ["the field"]


def test_non_finite_lists_every_offending_field_in_order():
    assert non_finite({"ok": 0.0, "x": float("nan"), "also ok": 3, "y": float("inf")}) == [
        "x = nan", "y = inf"]


@pytest.mark.parametrize("bad", [True, np.bool_(False), "1", [1.0], np.array([np.nan]), 1 + 0j,
                                 10 ** 400])
def test_non_finite_names_a_value_that_is_not_a_finite_number_at_all(bad):
    assert [s.split(" = ")[0] for s in non_finite({"f": bad})] == ["f"], bad


def test_non_finite_passes_over_none_because_the_reader_decides_what_absent_means():
    assert non_finite({"absent": None, "present": 0.0, "nan": float("nan")}) == ["nan = nan"]
