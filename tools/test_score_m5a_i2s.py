import re

import pytest

import score_m5a_i2s as scorer
from score_m5a_i2s import validate_integration_report

_HASH = "a" * 64


def _report(detail="27.2 s phrase and complete release", config="selected 2x saw + causal 2x filter candidate"):
    return "\n".join(("verify_synth_top: " + detail,
                      "verify_synth_top: " + config,
                      "verify_synth_top: M5A controls: pulse=pulse29; effective pulse=pulse479 (47.90% duty); saw cutoff=20000 Hz; saw volume correction=-0.45428 dB",
                      "verify_synth_top: selected 2x path; compile defines: VOICE_OSC_2X, VOICE_FILTER_2X",
                      "verify_synth_top: PASS -- decoded I2S periods match",
                      "verify_synth_top: M5A path verified from SPI pins through the production voice and I2S pins",
                      "verify_synth_top: simulator backend verilator",
                      f"verify_synth_top: decoded I2S WAV sha256 {_HASH}"))


def test_accepts_only_complete_filter_candidate_integration_report():
    controls = validate_integration_report(_report(), _HASH, pulse_shape="pulse29",
                                           saw_cutoff_hz=20_000,
                                           saw_volume_correction_db=-0.45428)
    assert controls["pulse_control_label"] == "pulse29"
    assert controls["pulse_effective_waveform"] == "pulse479"
    assert controls["pulse_effective_duty_percent"] == pytest.approx(47.9, abs=0.01)


@pytest.mark.parametrize("text", [
    _report(detail="smoke: envelope completion not claimed"),
    _report(config="selected 2x saw candidate"),
    "PASS -- unrelated simulator output",
])
def test_rejects_smoke_or_unrelated_report(text):
    with pytest.raises(ValueError, match="full filter-candidate"):
        validate_integration_report(text, _HASH)


def test_refuses_report_bound_to_a_different_wav():
    with pytest.raises(ValueError, match="does not bind"):
        validate_integration_report(_report(), "b" * 64)


def test_refuses_report_with_different_sound_controls():
    with pytest.raises(ValueError, match="cutoff"):
        validate_integration_report(_report(), _HASH, saw_cutoff_hz=14_073)


def test_refuses_report_without_filter2x_duty_mapping():
    text = _report().replace("VOICE_FILTER_2X", "VOICE_FILTER_1X")
    with pytest.raises(ValueError, match="VOICE_FILTER_2X"):
        validate_integration_report(text, _HASH)


def test_refuses_inconsistent_control_and_effective_pulse_metadata():
    text = _report().replace("pulse479 (47.90% duty)", "pulse29 (29.00% duty)")
    with pytest.raises(ValueError, match="effective pulse"):
        validate_integration_report(text, _HASH)


def test_filter2x_rtl_duty_encoding_matches_reported_effective_waveform():
    source = (scorer.ROOT / "rtl-sketch/voice_dp.v").read_text()
    match = re.search(
        r"`ifdef VOICE_FILTER_2X\s+localparam \[23:0\] DUTY_WIDE = 24'd(\d+)",
        source)
    assert match, "selected RTL must declare its VOICE_FILTER_2X pulse-width encoding"
    assert int(match.group(1)) == scorer.vf.DUTY["pulse479"]
    assert 100 * int(match.group(1)) / scorer.vf.CYCLE == pytest.approx(47.9, abs=0.001)
