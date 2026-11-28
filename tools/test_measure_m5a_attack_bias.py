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


def test_attack_bias_reports_the_digest_of_the_manifest_it_parsed(tmp_path, monkeypatch):
    # #1586 review: the report's manifest hash must not be a second read of the path.
    import hashlib
    manifest = json.loads(ab.MANIFEST.read_text())
    (tmp_path / manifest["audio"]["file"]).symlink_to(ab.MANIFEST.parent / manifest["audio"]["file"])
    path = tmp_path / "manifest.json"
    path.write_bytes(b"\xef\xbb\xbf" + ab.MANIFEST.read_bytes())
    parsed = ab._reference_preconditions(path)
    assert parsed[3] == hashlib.sha256(path.read_bytes()).hexdigest()


# ---- #1985: an execution error is not a refusal ----------------------------
# main()'s handler was typed (OSError, ValueError, KeyError, TypeError,
# refusal.Refused) over the WHOLE of measure_attack_bias, so a KeyError or a
# TypeError from a bug in the measurement printed REFUSED and exited 2, which
# tools/run_all.py renders as NO-VERDICT, "nothing was measured".
# tools/refusal.py: an unexpected exception is an EXECUTION ERROR, never a
# refusal. mono_m5a_score.load_reference -- the loader this tool binds through
# -- had already decided that a malformed manifest (bad JSON, a missing key)
# is an execution error "deliberately not swallowed"; this handler swallowed
# it back. RED FIRST against the shipped handler (2026-11-28): the three
# precondition controls and the meter control failed with DID NOT RAISE, and
# the outside-the-repository control failed because the shipped tool ran the
# whole meter on a manifest outside the repository before Path.relative_to
# refused it by accident. With the tuple narrowed and nothing else, those
# five passed and the two controls that pin a legitimate refusal -- the phase
# count and the location -- went red: both conversions below exist because
# of that run. The positive controls' teeth were checked by mutation: with
# the handler on this module's LOCAL Refused the cross-module cell goes red
# (and so does test_attack_bias_cli_maps_a_shared_refusal_to_exit_two, whose
# refusal is mono_m5a_score's); without OSError the unreadable-input cell does.

def _raise(exc, message):
    def f(*_args, **_kwargs):
        raise exc(message)
    return f


@pytest.mark.parametrize("exc", [KeyError, TypeError, ValueError])
def test_a_bug_in_the_preconditions_is_an_execution_error(monkeypatch, tmp_path, capsys, exc):
    monkeypatch.setattr(ab, "_reference_preconditions", _raise(exc, "injected: a bug"))
    with pytest.raises(exc, match="injected"):
        ab.main(["--out", str(tmp_path / "never-written.json")])
    assert "REFUSED" not in capsys.readouterr().out
    assert not (tmp_path / "never-written.json").exists()


def test_a_bug_in_the_meter_is_an_execution_error(monkeypatch, tmp_path):
    monkeypatch.setattr(ab, "_measure_known_condition", _raise(TypeError, "injected: a bug"))
    with pytest.raises(TypeError, match="injected"):
        ab.main(["--out", str(tmp_path / "never-written.json")])
    assert not (tmp_path / "never-written.json").exists()


@pytest.mark.parametrize("exc", [refusal.Refused, OSError],
                         ids=["cross-module Refused", "unreadable input"])
def test_the_two_classes_the_handler_keeps_still_refuse(monkeypatch, tmp_path, capsys, exc):
    # The paired positive controls. refusal.Refused is the BASE class: a
    # cross-module refusal that is neither this module's Refused nor
    # mono_m5a_score's, so a handler narrowed to a LOCAL class lets it out.
    # OSError stays for the one reason written at the handler: the manifest
    # named on the command line is read by path inside the guarded call.
    monkeypatch.setattr(ab, "_reference_preconditions", _raise(exc, "injected: a precondition"))
    assert ab.main(["--out", str(tmp_path / "never-written.json")]) == 2
    assert "measure_m5a_attack_bias: REFUSED -- injected: a precondition" in capsys.readouterr().out
    assert not (tmp_path / "never-written.json").exists()


def test_a_phase_count_out_of_range_is_refused(capsys, tmp_path):
    # REFUSED before #1985 too, but only because the handler caught the
    # ValueError this check raised. The check now refuses in its own Refused
    # vocabulary; red with the tuple narrowed and the raise left a ValueError
    # (run that way, 2026-11-28).
    assert ab.main(["--phases", "0", "--out", str(tmp_path / "never-written.json")]) == 2
    assert "measure_m5a_attack_bias: REFUSED -- phase count must be in 1..32" in capsys.readouterr().out


def test_a_manifest_outside_the_repository_is_refused_before_anything_runs(
        tmp_path, monkeypatch, capsys):
    # Was REFUSED only by accident: Path.relative_to raised ValueError after
    # the whole measurement had run, and the tuple caught it. Now a stated
    # precondition, checked before anything runs; red with the tuple narrowed
    # and no check (run that way, 2026-11-28).
    foreign = tmp_path / "manifest.json"
    foreign.write_text(ab.MANIFEST.read_text())
    audio = json.loads(foreign.read_text())["audio"]["file"]
    (tmp_path / audio).symlink_to(ab.MANIFEST.parent / audio)
    monkeypatch.setattr(ab, "_measure_known_condition",
                        lambda *a, **k: pytest.fail("the meter ran on a manifest outside the repository"))
    assert ab.main(["--manifest", str(foreign), "--out", str(tmp_path / "never-written.json")]) == 2
    out = capsys.readouterr().out
    assert "measure_m5a_attack_bias: REFUSED -- " in out and "inside the repository" in out, out
    assert not (tmp_path / "never-written.json").exists()
