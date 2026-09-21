import pytest

from score_m5a_i2s import validate_integration_report


def _report(detail="27.2 s phrase and complete release", config="selected 2x saw + causal 2x filter candidate"):
    return "\n".join(("verify_synth_top: " + detail,
                      "verify_synth_top: " + config,
                      "verify_synth_top: PASS -- decoded I2S periods match",
                      "verify_synth_top: M5A path verified from SPI pins through the production voice and I2S pins"))


def test_accepts_only_complete_filter_candidate_integration_report():
    validate_integration_report(_report())


@pytest.mark.parametrize("text", [
    _report(detail="smoke: envelope completion not claimed"),
    _report(config="selected 2x saw candidate"),
    "PASS -- unrelated simulator output",
])
def test_rejects_smoke_or_unrelated_report(text):
    with pytest.raises(ValueError, match="full filter-candidate"):
        validate_integration_report(text)
