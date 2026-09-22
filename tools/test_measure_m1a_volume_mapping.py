"""Qualify the octave-detune measurement with independently defined signals."""
import math
import numpy as np
import pytest
from scipy.io import wavfile
import measure_m1a_volume_mapping as probe


@pytest.mark.parametrize("offset", [0., -3.49, 3.49])
def test_isolated_octave_mapping_known_answer(tmp_path, monkeypatch, offset):
    t = np.arange(round(probe.bass.reference.SECONDS * probe.SR)) / probe.SR
    controls = {}
    for name, octave, detune in (("osc1_open", 0, 0), ("osc2_open", 12, offset)):
        audio = np.zeros_like(t)
        for event in probe.bass.reference.EVENTS:
            dt = t - event["on_s"]
            gate = (dt >= 0) & (dt < event["gate_s"])
            hz = 440 * 2 ** ((event["note"] + octave + detune / 100 - 69) / 12)
            audio += gate * .05 * sum(np.sin(2 * math.pi * k * hz * t + .17 * k) / k for k in range(1, 13))
        filename = name + ".wav"
        wavfile.write(tmp_path / filename, probe.SR, audio.astype(np.float32))
        controls[name] = {"file": filename}
    monkeypatch.setattr(probe.bass, "MANIFEST", tmp_path / "manifest.json")
    result = probe.oscillator_mapping({"controls": controls})
    assert result["octave_offset_cents"] == pytest.approx([offset] * 3, abs=.03)
