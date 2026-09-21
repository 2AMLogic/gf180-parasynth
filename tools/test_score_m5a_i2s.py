import pytest

from score_m5a_i2s import validate_integration_report

_HASH = "a" * 64


def _report(detail="27.2 s phrase and complete release", config="selected 2x saw + causal 2x filter candidate"):
    return "\n".join(("verify_synth_top: " + detail,
                      "verify_synth_top: " + config,
                      "verify_synth_top: PASS -- decoded I2S periods match",
                      "verify_synth_top: M5A path verified from SPI pins through the production voice and I2S pins",
                      f"verify_synth_top: decoded I2S WAV sha256 {_HASH}"))


def test_accepts_only_complete_filter_candidate_integration_report():
    validate_integration_report(_report(), _HASH)


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
