import copy
import pytest
import measure_mono_attack_context as probe


def test_context_summary_requires_every_valid_executed_condition():
    rows = []
    for wave in probe.ref.WAVE_SETTINGS:
        for name, events in probe.CONDITIONS.items():
            for repeat in range(3):
                rows.append({"wave": wave, "condition": name, "measurements": [
                    {"note": e["note"], "envelope": {"valid": True,
                     "release_complete_40db": True, "attack_10_90_ms": 10 + repeat}}
                    for e in events]})
    assert probe.summarize(rows)["saw"]["isolated84"]["median_ms"] == 11
    with pytest.raises(probe.ref.Refused, match="missing"):
        probe.summarize(rows[:-1])
    bad = copy.deepcopy(rows)
    bad[0]["measurements"][-1]["envelope"]["valid"] = False
    with pytest.raises(probe.ref.Refused, match="invalid"):
        probe.summarize(bad)
