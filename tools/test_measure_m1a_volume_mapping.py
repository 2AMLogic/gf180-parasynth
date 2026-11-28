"""Qualify the octave-detune measurement with independently defined signals."""
import json
import math
import os
import shutil
import tempfile
from pathlib import Path
import numpy as np
import pytest
from scipy.io import wavfile
import measure_m1a_volume_mapping as probe
import diagnose_m1a_oscillator_mapping as diagnosis


@pytest.mark.parametrize("offset", [0., -3.49, 3.49])
def test_isolated_octave_mapping_known_answer(tmp_path, monkeypatch, offset):
    t = np.arange(round(probe.bass.reference.SECONDS * probe.SR)) / probe.SR
    controls = {}
    for name, octave, detune in (("osc1_open", 0, 0), ("osc2_open", 12, offset)):
        audio = np.zeros_like(t)
        for event in probe.bass.reference.EVENTS:
            dt = t - event["on_s"]
            gate = (dt >= 0) & (dt < event["gate_s"])
            hz = 440 * 2 ** ((event["note"] + octave + detune / 100 - 69) / 12)
            audio += gate * .05 * sum(np.sin(2 * math.pi * k * hz * t + .17 * k) / k for k in range(1, 13))
        filename = name + ".wav"
        wavfile.write(tmp_path / filename, probe.SR, audio.astype(np.float32))
        controls[name] = {"file": filename}
    monkeypatch.setattr(probe.bass, "MANIFEST", tmp_path / "manifest.json")
    result = probe.oscillator_mapping({"controls": controls})
    assert result["octave_offset_cents"] == pytest.approx([offset] * 3, abs=.03)


@pytest.mark.parametrize("phase", [0., math.pi])
def test_interference_power_known_constructive_and_destructive_signals(phase):
    t = np.arange(probe.SR // 4) / probe.SR
    first = np.sin(2 * np.pi * 800 * t)
    second = .8 * np.sin(2 * np.pi * 800 * t + phase)
    measured = diagnosis.interference(first, second, 800)
    expected = 1 + .8**2 + 2 * .8 * math.cos(phase)
    assert measured["sum_power"] == pytest.approx(expected, abs=1e-10)
    assert measured["coherent_vs_incoherent_db"] == pytest.approx(10 * math.log10(expected / (1 + .8**2)), abs=1e-9)
    assert measured["linearity_residual"] < 1e-12


def test_report_embeds_the_v3_attack_qualification_not_the_retired_v2_one(monkeypatch):
    """_run() used to close its report with reference.qualify_envelope_basis(),
    running and embedding the retired v2 fit (#1423). The three renders are
    replaced by the committed baseline audio and the three compare_audio calls
    by the committed candidate measurement, so the run reaches the
    qualification without a render; which qualification the report embeds is
    the property under test."""
    def retired(*args, **kwargs):
        raise AssertionError("the volume-mapping report ran the retired v2 attack qualification")
    monkeypatch.setattr(probe.bass.reference, "qualify_attack_basis", retired)
    monkeypatch.setattr(probe.bass.reference, "attack_fit", retired)
    sr, pcm = wavfile.read(probe.OUT / "baseline.wav")
    assert sr == probe.SR
    measured = json.loads((probe.OUT / "volume-minus4db.json").read_text())["measurements"]
    # _run() records its audio relative to the repository root, so the report
    # directory must be inside it; build/ is gitignored
    (probe.ROOT / "build").mkdir(exist_ok=True)
    out = Path(tempfile.mkdtemp(prefix="test-volume-mapping-", dir=probe.ROOT / "build"))
    monkeypatch.setattr(probe, "OUT", out)
    monkeypatch.setattr(probe.bass.lead.vf, "render_mono_fx", lambda *args: pcm)
    monkeypatch.setattr(probe.bass, "compare_audio", lambda ours, reference: measured)
    try:
        assert probe._run() == 0
        report = json.loads((out / "report.json").read_text())
    finally:
        shutil.rmtree(out)
    attack = report["envelope_qualification"]["attack"]
    assert attack["estimator"] == "attack_fit_v3"
    assert attack["cases"] == 1898
    assert attack["domain"] == probe.bass.qualified_attack_domain()


def test_a_missing_isolated_control_wav_is_refused_before_oscillator_mapping_reads_it(tmp_path, monkeypatch, capsys):
    """Pin (#1862 section D): `oscillator_mapping` reads the isolated controls
    with no guard of its own; the guard is `load_reference()` one line earlier,
    which hashes every control under `except (OSError, KeyError, ValueError)`.
    The order is the guard, so the order is what this test pins."""
    manifest = json.loads(probe.bass.MANIFEST.read_text())
    for item in manifest["renders"]:
        os.symlink(probe.bass.MANIFEST.parent / item["file"], tmp_path / item["file"])
    (tmp_path / "manifest.json").write_text(json.dumps(manifest))     # names controls that are absent
    real = probe.bass.load_reference
    monkeypatch.setattr(probe.bass, "load_reference", lambda path=None: real(tmp_path / "manifest.json"))

    def unreached(*args, **kwargs):
        raise AssertionError("oscillator_mapping read a control WAV load_reference never verified")
    monkeypatch.setattr(probe, "oscillator_mapping", unreached)
    assert probe.main() == 2
    assert "REFUSED:" in capsys.readouterr().out
