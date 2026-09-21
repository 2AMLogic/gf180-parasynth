import pytest

import measure_m5a_attack_bias as ab


def test_five_ms_rms_bias_is_measured_against_known_m5a_attack():
    report = ab.measure_attack_bias(phases=4)
    control = report["known_signal"]

    assert report["method"]["true_attack_10_90_ms"] == pytest.approx(7.333333, abs=0.001)
    medians = [row["median_bias_ms"] for row in control["conditions"]]
    assert min(medians) > 1.0
    assert max(medians) < 1.7


def test_frozen_reference_has_a_patch_matched_attack_spread():
    report = ab.measure_attack_bias(phases=4)
    reference = report["frozen_reference"]

    assert reference["amp_attack_readback"] == 0.05
    assert reference["event_attack_spread_ms"] > 4.0
    assert reference["audio_sha256"] == "a808cd22448ecea1c639f9311578eb72399f69bda373e9036174c2ca208a1f0a"
