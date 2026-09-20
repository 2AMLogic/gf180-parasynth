from __future__ import annotations

import numpy as np
import pytest

import measure_m5a_signal_path as probe


def test_q15_trace_conversion_uses_one_full_scale_for_every_stage():
    got = probe._q15_to_full_scale(np.array([-32768, 0, 32767], dtype=np.int16))
    assert got.tolist() == [-1.0, 0.0, pytest.approx(32767 / 32768)]


def test_full_scale_conversion_is_gain_linear():
    q15 = np.array([-16384, 8192, 24576], dtype=np.int32)
    assert probe._q15_to_full_scale(q15) == pytest.approx([-0.5, 0.25, 0.75])
