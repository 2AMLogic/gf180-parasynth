import pytest

import measure_m5a_saw_cutoff as candidate


def test_invalid_cutoff_refuses_before_render(monkeypatch):
    def unexpected_render(*_args, **_kwargs):
        pytest.fail("invalid cutoff must refuse before rendering")

    monkeypatch.setattr(candidate.m5a, "measure", unexpected_render)
    with pytest.raises(candidate.m5a.Refused, match="cutoff"):
        candidate.measure(30_000)


def test_complete_candidate_uses_selected_filter_factory_and_saw_only_override(monkeypatch):
    calls = []

    def fake_measure(**kwargs):
        calls.append(kwargs)
        factory = kwargs["voice_factory"]
        voice = factory()
        assert voice.rate_converted_ladder
        assert voice.ladder_cfg["oversample"] == 2
        assert voice.preserve_filter_headroom and voice.causal_filter
        cutoff = kwargs.get("saw_cutoff_override")
        units = {"Pitch": "cents", "Harmonic shape": "dB",
                 "Foldback energy": "dB", "Envelope attack": "ms",
                 "Envelope release": "ms", "Gain": "dB", "Clipping": "%"}
        metrics = {name: {"error": 0.0, "tolerance": tolerance[0],
                          "units": units[name]}
                   for name, tolerance in candidate.m5a.TOLERANCES.items()}
        harmonic = {"h2": -1.0 if cutoff is None else -0.5}
        return {"metrics": metrics, "event_diagnostics": [
            {"wave": "saw", "midi": 84,
             "harmonic_error_db_model_minus_reference": harmonic}],
            "model_configuration": {"label": kwargs["model_label"],
                                    "pulse_shape": kwargs["pulse_shape"],
                                    "saw_cutoff_override_hz": cutoff},
            "audio": "build/fake.wav", "reference_sha256": "ref",
            "manifest_sha256": "manifest",
            "cutoff_calibration": {"f0_hz": 14_073}}

    monkeypatch.setattr(candidate.m5a, "measure", fake_measure)
    result = candidate.measure(20_000, -0.45428)

    assert len(calls) == 2
    assert calls[0].get("saw_cutoff_override") is None
    assert calls[1]["saw_cutoff_override"] == 20_000
    assert calls[0]["pulse_shape"] == calls[1]["pulse_shape"] == "pulse479"
    assert calls[0].get("saw_volume_correction_db", 0.0) == 0.0
    assert calls[1]["saw_volume_correction_db"] == pytest.approx(-0.45428)
    assert result["saw_gain_correction_db"]["candidate"] == pytest.approx(-0.45428)
    assert result["comparison"]["case_passes"] is True
    assert result["comparison"]["accepts_incremental_improvement"] is True
