"""The one definition of "is this a finite measurement?" (#718).

Four hand-copied guards had drifted: cymbal_tone_render's accepted `bool`
(`True` read as 1.0), the others did not. A flag, a string and a NaN are not
measurements, so none of them is accepted; int/float and numpy real scalars are.
"""
from __future__ import annotations

import math
import numbers
from collections.abc import Mapping

import numpy as np


def is_finite_number(x) -> bool:
    """True for a real, finite int/float or numpy real scalar. bool (an int
    subclass, also np.bool_), str, None, containers and complex are all False."""
    if isinstance(x, (bool, np.bool_)):
        return False
    if not isinstance(x, (numbers.Real, np.integer, np.floating)):
        return False
    return math.isfinite(x)


def non_finite(fields: Mapping[str, object]) -> list[str]:
    """`"<field> = <value>"` for every value that is neither a finite number nor
    None: what a reader names when it refuses (#1994).

    A reader that takes a number out of a JSON record or a plugin host and
    compares it must ask this BEFORE the comparison. IEEE makes every ordering
    comparison against NaN False, so `abs(got - want) > tol` does not trip: it
    passes, and the NaN reads as "matches" (#134). `json.loads` accepts a bare
    `NaN` and `json.dumps` writes one, so the value does reach the reader.

    Every offending field is listed, +-inf with the NaNs, and so is every other
    value that is not a finite number (a flag, a string, an array) or an int too
    large for a float. `None` is passed over: it is "no reading", and each reader
    already decides what that means where it reads it.
    """
    out = []
    for name, value in fields.items():
        if value is None:
            continue
        try:
            finite = is_finite_number(value)
        except OverflowError:
            finite = False
        if not finite:
            out.append(f"{name} = {value!r}")
    return out
