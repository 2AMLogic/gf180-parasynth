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


# ---- a resumed report that cannot be read is REFUSED (exit 2), not a KeyError
# traceback (exit 1) (#1604, #1622). One control per key main() reads with []
# from the --resume record, through main(['--resume', ...]).
COMMITTED = probe.ROOT / "docs/scorecard/mono-attack-context/report.json"
RESUME_KEYS = ("identity", "provenance", "renders")


def _committed():
    return json.loads(COMMITTED.read_text())


def _resume(monkeypatch, tmp_path, capsys, prior):
    """The plugin/host probes before the read are stubbed: each needs a Mini V3
    install and none is what the control is about. The identity stub answers
    with the resumed report's own identity, so the identity check it feeds
    passes and the control reaches the read; the clean-tree check is stubbed to
    answer what a committed instrument would. The resumed report's recorded
    source digests are rebound to this tree, since the committed ones name a
    capture older than the tools and the tool refuses a drifted source before
    it reads `renders`."""
    if isinstance(prior, dict) and isinstance(prior.get("provenance"), dict):
        files = prior["provenance"].get("files_sha256", {})
        prior["provenance"]["files_sha256"] = {
            name: probe.ref.sha256(probe.ROOT / name) for name in files}
    path = tmp_path / "prior.json"
    path.write_text(json.dumps(prior))
    out = tmp_path / "out"
    monkeypatch.setattr(probe.subprocess, "check_output", lambda *a, **k: "")
    monkeypatch.setattr(probe.ref, "_source_provenance",
                        lambda: {"files_sha256": {}})
    monkeypatch.setattr(probe.ref, "_plugin_metadata",
                        lambda: prior.get("identity") if isinstance(prior, dict)
                        else {})
    monkeypatch.setattr(probe.ref, "_qualify_reference_integrity", lambda: {})
    rc = probe.main(["--resume", str(path), "--out", str(out)])
    return rc, capsys.readouterr().out, out


def test_the_committed_report_carries_the_keys_a_resume_reads():
    """The guard must be satisfiable against the report that ships."""
    assert set(RESUME_KEYS) <= _committed().keys()


@pytest.mark.parametrize("key", RESUME_KEYS)
def test_a_resumed_report_without_a_key_is_refused(monkeypatch, tmp_path, capsys,
                                                   key):
    prior = _committed()
    del prior[key]
    rc, printed, out = _resume(monkeypatch, tmp_path, capsys, prior)
    assert rc == 2 and "REFUSED" in printed and repr(key) in printed
    assert json.loads((out / "refused.json").read_text())["state"] == "REFUSED"


@pytest.mark.parametrize("prior, why", [({}, "'identity'"),
                                        ([], "not a JSON object"),
                                        (None, "not a JSON object")])
def test_a_resumed_report_that_is_not_an_object_is_refused(monkeypatch,
                                                           tmp_path, capsys,
                                                           prior, why):
    rc, printed, out = _resume(monkeypatch, tmp_path, capsys, prior)
    assert rc == 2 and "REFUSED" in printed and why in printed


def test_a_resumed_report_with_a_null_key_is_refused(monkeypatch, tmp_path,
                                                     capsys):
    prior = _committed()
    prior["renders"] = None
    rc, printed, out = _resume(monkeypatch, tmp_path, capsys, prior)
    assert rc == 2 and "REFUSED" in printed
    assert "'renders'" in printed and "null" in printed
