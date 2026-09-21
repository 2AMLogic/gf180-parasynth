"""The stored-audio verifier must detect numerical and provenance mutations."""
import copy
import json
from pathlib import Path

import pytest

import verify_attack_context_model as verifier

REPORT = verifier.producer.ROOT / "docs/scorecard/mono-attack-context/model/report.json"


def test_stored_audio_reproduces_without_rendering(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("stored audio verification attempted a voice render")
    monkeypatch.setattr(verifier.producer.lead.vf, "render_mono_fx", forbidden)
    result = verifier.verify(REPORT)
    assert result["exact_timing_rows"] == 12
    assert result["history_contrasts"] == 6


@pytest.mark.parametrize("mutation", ["timing", "history", "missing-row", "audio-hash", "invalid"])
def test_changed_evidence_refuses(tmp_path, mutation):
    record = copy.deepcopy(json.loads(REPORT.read_text()))
    for row in record["rows"]:
        audio = Path(row["audio"]).name
        (tmp_path / audio).symlink_to(REPORT.parent / audio)
    if mutation == "timing":
        record["rows"][0]["model_timing"]["attack_10_90_ms"] += 1 / 48
    elif mutation == "history":
        record["history_effect_ms"][0]["model_attack_ms"] += 1
    elif mutation == "missing-row":
        record["rows"].pop()
    elif mutation == "audio-hash":
        record["rows"][0]["sha256"] = "0" * 64
    elif mutation == "invalid":
        record["rows"][0]["model_timing"]["valid"] = False
    mutated = tmp_path / "report.json"
    mutated.write_text(json.dumps(record))
    with pytest.raises(AssertionError):
        verifier.verify(mutated)
