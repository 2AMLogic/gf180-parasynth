"""The stored-audio verifier must detect numerical and provenance mutations."""
import ast
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
    assert result["render_source_commit"] == json.loads(REPORT.read_text())["source_commit"]
    assert "historical" in result["evidence_basis"]


@pytest.mark.parametrize("mutation", ["timing", "history", "missing-row", "audio-hash", "invalid", "source-hash"])
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
    elif mutation == "source-hash":
        record["source_sha256"]["model/voice_fx.py"] = "0" * 64
    mutated = tmp_path / "report.json"
    mutated.write_text(json.dumps(record))
    with pytest.raises(AssertionError):
        verifier.verify(mutated)


def _serve_audio_measure(monkeypatch, edit):
    """Give the verifier `edit(current source)` as model/audio_measure.py."""
    real = Path.read_text
    target = verifier.producer.ROOT / verifier.AUDIO_MEASURE
    monkeypatch.setattr(Path, "read_text",
                        lambda self, *a, **k: edit(real(self, *a, **k)) if self == target else real(self, *a, **k))


def _mutate(old, new):
    def edit(src):
        assert old in src, "mutation target moved; the control would be vacuous"
        return src.replace(old, new, 1)
    return edit


def test_changed_analysis_basis_refuses(monkeypatch):
    """Injected defects in the executed basis must refuse: the envelope itself and its helper."""
    _serve_audio_measure(monkeypatch, _mutate("return e * math.sqrt(2.0)", "return e * 1.4"))
    with pytest.raises(AssertionError, match="analysis basis changed"):
        verifier.verify(REPORT)


def test_changed_basis_helper_refuses(monkeypatch):
    _serve_audio_measure(monkeypatch, _mutate("what makes this module's NaN boundary ONE line",
                                              "what makes this module's NaN boundary one line"))
    with pytest.raises(AssertionError, match="analysis basis changed"):
        verifier.verify(REPORT)


def test_unrelated_estimator_edit_does_not_refuse(monkeypatch):
    """The narrowing's point: an edit outside the executed basis is reported, not refused."""
    _serve_audio_measure(monkeypatch, lambda src: src + "\n\ndef _unrelated_probe():\n    return 1\n")
    result = verifier.verify(REPORT)
    assert verifier.AUDIO_MEASURE in result["current_source_differences"]


def test_rms_envelope_dependencies_are_pinned():
    """The pinned basis is closed: nothing in it reaches a module-level name outside it."""
    tree = ast.parse((verifier.producer.ROOT / verifier.AUDIO_MEASURE).read_text())
    defs = {n.name: n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.ClassDef))}
    module_names = set(defs) | {t.id for n in tree.body if isinstance(n, ast.Assign)
                                for t in n.targets if isinstance(t, ast.Name)}
    for name in verifier.AUDIO_MEASURE_BASIS:
        if name in defs:
            used = {n.id for n in ast.walk(defs[name]) if isinstance(n, ast.Name)} & module_names
            assert used <= set(verifier.AUDIO_MEASURE_BASIS) | {name}, (name, used - set(verifier.AUDIO_MEASURE_BASIS))
