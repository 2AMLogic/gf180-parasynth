"""A known spectral pedestal distinguishes the two D10A/D13A candidate fits."""

import numpy as np
import pytest

import balance_line_shape as probe


def test_candidate_table_identifies_a_known_pedestal():
    # 10 ms and -10 dB are exact grid points for the fitter. The additive
    # power holds the skirt above the floorless curve as the offset increases.
    offsets = np.repeat(np.arange(1, 7) / 0.06, 2)
    lorentzian_power = 1 / (1 + (2 * np.pi * 0.01 * offsets) ** 2)
    drops_db = -10 * np.log10(lorentzian_power + 0.1)

    def residual(fit):
        return fit(1.0, offsets, None, drops_db, 0.06)

    # Positive control on the fixture before grading either reported column.
    reference = probe.PT._lorentzian_fit(offsets, drops_db, pedestal=True)
    assert reference["ok"] and reference["pedestal_db"] == -10
    assert reference["resid_db"] == pytest.approx(0, abs=1e-10)
    assert residual(probe.CANDS["floorless dB"]) > 0.5
    assert residual(probe.CANDS["fit resid dB"]) == pytest.approx(0, abs=1e-10)
