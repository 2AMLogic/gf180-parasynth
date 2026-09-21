import numpy as np
import pytest
import measure_mono_m1a_reference as probe


def test_bass_measurement_uses_known_pitch_and_release():
    sr = probe.ref.SR
    t = np.arange(round(probe.SECONDS * sr)) / sr
    audio = np.zeros_like(t)
    for e in probe.EVENTS:
        dt = t - e["on_s"]
        env = np.clip(dt / .03, 0, 1)
        off = dt >= e["gate_s"]
        env[off] = np.exp(-(dt[off] - e["gate_s"]) / .10)
        hz = 440 * 2 ** ((e["note"] - 69) / 12)
        audio += .1 * env * np.sin(2 * np.pi * hz * t)
    rows = probe.event_measurements(audio)
    assert len(rows) == 3
    assert max(abs(row["pitch_cents"]) for row in rows) < .5
    assert all(abs(row["envelope"]["release_t20_ms"] - 100 * np.log(10)) < 10 for row in rows)
    with pytest.raises(probe.ref.Refused):
        probe.event_measurements(np.zeros_like(audio))
