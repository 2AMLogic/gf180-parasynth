import hashlib
import json

import numpy as np
import pytest
from scipy.io import wavfile

import measure_m5a_attack_bias as ab
import refusal


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


def _damaged_m5a_record(tmp_path, monkeypatch, damage):
    """An M5A record whose WAV passes both the manifest hash and the tool's
    pinned hash, and fails a later gate (#1570). Without re-pinning, the pin
    would refuse first and the missing gate could never be reached."""
    manifest = json.loads(ab.MANIFEST.read_text())
    samples = int(manifest["audio"]["samples"])
    pcm = (np.full(samples, np.nan, dtype=np.float32) if damage == "nan"
           else np.zeros(samples, dtype=np.int16))
    audio = tmp_path / "damaged.wav"
    wavfile.write(audio, ab.SR, pcm)
    digest = hashlib.sha256(audio.read_bytes()).hexdigest()
    manifest["audio"] = {**manifest["audio"], "file": audio.name, "sha256": digest}
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(manifest))
    monkeypatch.setattr(ab, "EXPECTED_AUDIO_SHA256", digest)
    return path


@pytest.mark.parametrize(("damage", "reason"), [
    ("nan", "finite-sample"),
    ("int16", "not mono float32"),
])
def test_attack_bias_preconditions_refuse_audio_that_passes_both_hashes(
        tmp_path, monkeypatch, damage, reason):
    manifest = _damaged_m5a_record(tmp_path, monkeypatch, damage)
    with pytest.raises(refusal.Refused, match=reason):
        ab._reference_preconditions(manifest)


def test_attack_bias_preconditions_refuse_a_foreign_case_manifest(tmp_path):
    manifest = json.loads(ab.MANIFEST.read_text())
    manifest["case_id"] = "M5B"
    audio = tmp_path / manifest["audio"]["file"]
    audio.symlink_to(ab.MANIFEST.parent / manifest["audio"]["file"])
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(manifest))
    # The genuine audio sits beside the manifest and matches the pin, so only
    # the case gate can refuse here.
    with pytest.raises(refusal.Refused, match="identifies as 'M5B', expected M5A"):
        ab._reference_preconditions(path)


def test_attack_bias_cli_maps_a_shared_refusal_to_exit_two(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(ab, "ROOT", tmp_path)
    manifest = _damaged_m5a_record(tmp_path, monkeypatch, "nan")
    assert ab.main(["--manifest", str(manifest), "--out", "never-written.json"]) == 2
    assert "finite-sample" in capsys.readouterr().out
    assert not (tmp_path / "never-written.json").exists()
