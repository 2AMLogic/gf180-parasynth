"""`alias_probe.ladder_substeps` refuses an input with no samples (#1469).

The function asserts that its sub-step tap IS the shipping ladder, by counting
the samples where the last sub-step differs from `LadderFx(oversample=os_)`.
On an empty input that count is 0 over 0 samples compared, and the precondition
held without comparing anything.
"""
import pathlib
import sys

import numpy as np
import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import alias_probe as ap                                                # noqa: E402
import audio_measure as am                                              # noqa: E402


def test_empty_input_is_refused_not_held():
    with pytest.raises(am.InsufficientEvidence, match="no sample"):
        ap.ladder_substeps(np.array([], dtype=np.int16), 2)


def test_populated_input_still_holds():
    x = np.array([1000, -1000, 2000, -2000, 500, -500, 3000, -3000], dtype=np.int16)
    sub, ref, g = ap.ladder_substeps(x, 2)
    assert len(sub) == 2 * len(ref) == 16 and len(ref) == 8
