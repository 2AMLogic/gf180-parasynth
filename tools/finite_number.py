"""The one definition of "is this a finite measurement?" (#718).

Four hand-copied guards had drifted: cymbal_tone_render's accepted `bool`
(`True` read as 1.0), the others did not. A flag, a string and a NaN are not
measurements, so none of them is accepted; int/float and numpy real scalars are.
"""
from __future__ import annotations

import math
import numbers

import numpy as np


def is_finite_number(x) -> bool:
    """True for a real, finite int/float or numpy real scalar. bool (an int
    subclass, also np.bool_), str, None, containers and complex are all False."""
    if isinstance(x, (bool, np.bool_)):
        return False
    if not isinstance(x, (numbers.Real, np.integer, np.floating)):
        return False
    return math.isfinite(x)
