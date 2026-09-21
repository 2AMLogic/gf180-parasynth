import numpy as np
import pytest
import json
from scipy.io import wavfile
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


@pytest.mark.parametrize("note", [36, 43])
@pytest.mark.parametrize("phase", [0, .25, .5, .75])
def test_bass_release_window_is_qualified_across_carrier_phase(note, phase):
    sr = probe.ref.SR
    t = np.arange(round(1.8 * sr)) / sr
    envelope = np.clip((t - .1) / .03, 0, 1)
    envelope[t >= .7] = np.exp(-(t[t >= .7] - .7) / .1)
    hz = 440 * 2 ** ((note - 69) / 12)
    audio = envelope * np.sin(2 * np.pi * (hz * t + phase))
    measured = probe.ref.am.rms_envelope(audio, ms=probe.ENVELOPE_WINDOW_MS, sr=sr)
    timing = probe.ref.envelope_timing(measured, sr, .1, .7)
    assert timing["valid"] and timing["release_complete_40db"]
    assert abs(timing["release_t20_ms"] - 100 * np.log(10)) < 10


def test_frozen_bass_reference_reproduces_without_a_plugin():
    directory = probe.ROOT / "docs/scorecard/mono-m1a-miniv3"
    manifest = json.loads((directory / "manifest.json").read_text())
    for artifact in [*manifest["renders"], *manifest["controls"].values()]:
        assert probe.ref.sha256(directory / artifact["file"]) == artifact["sha256"]
    sr, audio = wavfile.read(directory / manifest["renders"][0]["file"])
    assert sr == probe.ref.SR
    observed = probe.event_measurements(audio)
    for got, expected in zip(observed, manifest["renders"][0]["events"]):
        assert got["f0_hz"] == pytest.approx(expected["f0_hz"], abs=1e-5)
        assert got["rms_dbfs"] == pytest.approx(expected["rms_dbfs"], abs=1e-5)
        assert got["envelope"]["release_t20_ms"] == expected["envelope"]["release_t20_ms"]
