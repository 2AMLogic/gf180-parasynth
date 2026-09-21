import hashlib
import json

import pytest

import mono_m5a_score as score


def test_corrupted_reference_refuses_before_measurement(tmp_path, monkeypatch):
    audio = tmp_path / "broken.wav"
    audio.write_bytes(b"not the frozen audio")
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"audio": {
        "file": audio.name,
        "sha256": hashlib.sha256(b"different content").hexdigest(),
    }}))
    monkeypatch.setattr(score, "MANIFEST", manifest)
    with pytest.raises(score.Refused, match="hash mismatch"):
        score.measure()


def test_metric_keeps_signed_error_and_declared_units():
    result = score._metric("Pitch", -0.2, 0.1, "cents", 1.0, "frozen")
    assert result["error"] == -0.3
    assert result["tolerance"] == 1.0
    assert result["units"] == "cents"
    assert result["valid"] is True


def test_pitch_deviation_uses_cents_not_semitone_percent():
    assert score._cents_error(2.0, 1.0) == pytest.approx(1200.0)
    assert score._cents_error(440.0 * 2 ** (1 / 12), 440.0) == pytest.approx(100.0)


def test_decoded_i2s_candidate_requires_full_mono_int16_phrase(tmp_path):
    import numpy as np
    from scipy.io import wavfile

    short = tmp_path / "short.wav"
    wavfile.write(short, score.SR, np.zeros(100, dtype=np.int16))
    with pytest.raises(score.Refused, match="short"):
        score._load_i2s_candidate(short, 101)

    stereo = tmp_path / "stereo.wav"
    wavfile.write(stereo, score.SR, np.zeros((128, 2), dtype=np.int16))
    with pytest.raises(score.Refused, match="mono signed-int16"):
        score._load_i2s_candidate(stereo, 100)

    valid = tmp_path / "complete.wav"
    samples = np.arange(128, dtype=np.int16)
    wavfile.write(valid, score.SR, samples)
    loaded, digest = score._load_i2s_candidate(valid, 100)
    assert loaded.shape == (100,)
    assert loaded[12] == pytest.approx(12 / 32768)
    assert digest == hashlib.sha256(valid.read_bytes()).hexdigest()


def test_cleaner_alias_output_is_not_penalized():
    assert score._excess_alias_db(-62.0, -50.0) == 0.0
    assert score._excess_alias_db(-45.0, -50.0) == pytest.approx(5.0)


def test_missing_harmonic_is_scored_against_its_measured_floor():
    error, status = score._harmonic_error(
        {"h8": None, "floor8": -80.0}, {"h8": -25.9, "floor8": -90.0}, 8)
    assert error == pytest.approx(-54.1)
    assert status == "model_below_floor_bound"


def test_two_unmeasurable_harmonics_are_explicitly_uncompared():
    error, status = score._harmonic_error(
        {"h8": None, "floor8": -80.0}, {"h8": None, "floor8": -82.0}, 8)
    assert error is None
    assert status == "both_below_floor"


def test_pulse_segment_selects_pulse_in_the_model():
    common = {"waves": ("saw", "saw", "saw"), "mix": (1.0, 0.0, 0.0)}
    assert score._patch_for_wave(common, "saw")["waves"][0] == "saw"
    assert score._patch_for_wave(common, "pulse")["waves"][0] == "pulse29"
    assert score._patch_for_wave(common, "pulse", "pulse479")["waves"][0] == "pulse479"
    with pytest.raises(score.Refused, match="unsupported"):
        score._patch_for_wave(common, "triangle")


def test_m5a_candidate_uses_the_measured_filter_drive_intervention():
    manifest = json.loads(score.MANIFEST.read_text())
    assert score._voice_patch(manifest)["drive"] == pytest.approx(0.75)


def test_pulse479_is_explicitly_model_only_and_has_no_rtl_encoding():
    import voice_fx as vf

    assert "pulse479" not in vf.WAVE_CODE
    with pytest.raises(score.Refused, match="unsupported M5A model pulse candidate"):
        score._patch_for_wave({"waves": ("saw",) * 3}, "pulse", "pulse50")


def test_stage_vector_reports_absolute_power_and_keeps_stages_distinct():
    import numpy as np

    note, f0, n = 84, score.vf.note_hz(84), 12_000
    tone = score.vf.OscFx("saw").render(n, score.vf.phase_inc(f0)).astype(np.int16)
    trace = {"osc": [tone], "mixed": tone, "ladder": tone.astype(np.int32) * 8}
    stages = score._stage_diagnostics(trace, tone, 0, n, f0)
    assert set(stages) == {"oscillator", "mixer", "ladder", "output"}
    assert stages["ladder"]["total_signal_power_dbfs"] > stages["output"]["total_signal_power_dbfs"] + 15
    for values in stages.values():
        assert np.isfinite(values["alias_band_power_dbfs"])
        assert np.isfinite(values["total_signal_power_dbfs"])


def test_stage_vector_refuses_an_alias_ambiguous_pure_tone():
    import numpy as np

    n = 4_800
    t = np.arange(n) / score.SR
    tone = np.rint(0.1 * 32768 * np.sin(2 * np.pi * 440 * t)).astype(np.int16)
    trace = {"osc": [tone], "mixed": tone,
             "ladder": tone.astype(np.int32) * 8}
    with pytest.raises(score.Refused, match="predicted images collide with real harmonics"):
        score._stage_diagnostics(trace, tone, 0, n, 440.0)


def test_frozen_cutoff_measurement_is_repeatable_at_the_pinned_block_size():
    manifest = json.loads((score.MANIFEST).read_text())
    measurement = manifest["patch"]["cutoff_measurement"]
    assert measurement["block_size_samples"] == manifest["host"]["block_size_samples"] == 16
    assert measurement["repeat_count"] >= 3
    assert measurement["repeat_range_hz"] <= 10.0
    wr = manifest["qualification"]["wrong_then_right"]
    assert wr["overall_wrong_then_right_rate"] == "2/6"
    assert wr["cutoff_calibration"]["discarded"]["host_block_size_samples"] == 512
    assert wr["cutoff_calibration"]["discarded"]["injected_control_caught"] is True
