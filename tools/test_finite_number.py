"""Known answers for tools/finite_number.py (#718): the one definition of "is this
a finite measurement?" that four hand-copied guards had drifted away from."""
from __future__ import annotations

import pathlib
import sys

import numpy as np
import pytest

sys.path[:0] = [str(pathlib.Path(__file__).resolve().parent)]
from finite_number import is_finite_number  # noqa: E402


@pytest.mark.parametrize("x", [0, 1, -3, 0.0, 2.5, np.float32(1), np.float64(-2.5), np.int32(7)])
def test_real_finite_numbers_are_accepted(x):
    assert is_finite_number(x) is True


@pytest.mark.parametrize("x", [True, False, np.bool_(True), "1", "nan", None, [1.0], (1.0,),
                               float("nan"), float("inf"), -float("inf"), np.float32("nan"),
                               np.float64("inf"), 1 + 0j, b"1"])
def test_everything_else_is_rejected(x):
    assert is_finite_number(x) is False
