import copy
import json
from scipy.io import wavfile
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


def test_frozen_attack_context_is_reproducible_without_plugins():
    directory = probe.ROOT / "docs/scorecard/mono-attack-context"
    report = json.loads((directory / "report.json").read_text())
    for row in report["renders"]:
        path = directory / row["wav"]
        assert probe.ref.sha256(path) == row["sha256"]
        if row["repeat"] != 0:
            continue
        sr, audio = wavfile.read(path)
        envelope = probe.ref.am.rms_envelope(audio, ms=5, sr=sr)
        event = row["events"][-1]
        timing = probe.ref.envelope_timing(envelope, sr, event["on_s"],
                                           event["on_s"] + event["gate_s"])
        assert timing["valid"] and timing["release_complete_40db"]
        assert timing["attack_10_90_ms"] == pytest.approx(
            row["measurements"][-1]["envelope"]["attack_10_90_ms"], abs=1000/sr)
