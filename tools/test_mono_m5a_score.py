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
    with pytest.raises(score.Refused, match="unsupported"):
        score._patch_for_wave(common, "triangle")


def test_m5a_candidate_uses_the_measured_filter_drive_intervention():
    manifest = json.loads(score.MANIFEST.read_text())
    assert score._voice_patch(manifest)["drive"] == pytest.approx(0.75)


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
