"""Pulse-path checks grounded in the Fourier series of a rectangular wave."""
import numpy as np
import pytest

import voice_fx as vf


def test_pulse_2x_preserves_known_duty_dc_and_harmonic_series():
    # Exactly 750 Hz on this phase grid: no pitch estimator or window leakage.
    n, inc = 8192, vf.CYCLE // 64
    osc = vf.OscFx("pulse479", smooth=False)
    pcm, _, _ = vf._render_2x(osc, n, inc, np.zeros(30, dtype=np.int64), 0)
    steady = pcm[64:].astype(float)
    duty = 0.479
    assert abs(np.mean(steady) - (2 * duty - 1) * vf._OS2_SUBSTEP_GAIN_Q15) < 10
    spectrum = np.abs(np.fft.rfft(steady))
    fundamental_bin = len(steady) // 64
    for harmonic in range(2, 13):
        expected = abs(np.sin(np.pi * harmonic * duty) /
                       (harmonic * np.sin(np.pi * duty)))
        observed = spectrum[harmonic * fundamental_bin] / spectrum[fundamental_bin]
        assert abs(20 * np.log10(observed / expected)) < 0.5


def test_pulse_2x_is_optional_and_requires_the_2x_engine():
    assert vf.VoiceFx().oversample_pulse_2x is False
    with pytest.raises(ValueError, match="requires"):
        vf.VoiceFx(oversample_pulse_2x=True)
    voice = vf.VoiceFx(oversample_2x=True, oversample_pulse_2x=True)
    assert voice.oversample_pulse_2x is True


@pytest.mark.parametrize("shape", ["square", "pulse29", "pulse479"])
def test_pulse_2x_chunk_boundaries_and_phase_changes_are_exact(shape):
    n = 1001
    inc = np.linspace(17001, 64003, n, dtype=np.int64)
    whole, split = vf.OscFx(shape), vf.OscFx(shape)
    expected, final_history, final_phase = vf._render_2x(
        whole, n, inc, np.zeros(30, dtype=np.int64), 0)
    history, phase, output = np.zeros(30, dtype=np.int64), 0, []
    start = 0
    for end in (1, 16, 31, 321, n):
        chunk, history, phase = vf._render_2x(
            split, end-start, inc[start:end], history, phase)
        output.append(chunk)
        start = end
    np.testing.assert_array_equal(np.concatenate(output), expected)
    np.testing.assert_array_equal(history, final_history)
    assert phase == final_phase
    assert split.phase == whole.phase
