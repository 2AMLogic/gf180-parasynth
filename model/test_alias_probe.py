"""Window provenance in the oscillator and ladder aliasing report (#1388)."""

import json

import numpy as np
import pytest

import alias_probe as probe
import audio_measure as am


def test_known_tone_windows_in_cli_and_json(tmp_path, monkeypatch, capsys):
    # Keep every section of the report at the same off-bin tone. The oscillator
    # requires an increment divisible by four for exact rate conversion.
    sr = probe.SR
    inc = 4 * round(997 * probe.CYCLE / (4 * sr))
    monkeypatch.setattr(probe, "base_inc", lambda note: inc)
    monkeypatch.setattr(probe, "NOTES", (83,))
    monkeypatch.setitem(probe.MEASURE, "seconds", 4096 / sr)
    assert probe.main(["--shape", "sine", "--out", str(tmp_path)]) == 0

    report = json.loads((tmp_path / "alias_probe.json").read_text())
    settings = report["provenance"]["measure"]
    assert "window" not in settings
    stdout = capsys.readouterr().out
    assert str(settings) in stdout
    assert "'window':" not in stdout
    assert settings["band_split_window"] == "hann"

    # Replay both readings on the same high-rate oscillator tap.
    row = report["chain_a"][0]
    with np.load(tmp_path / "chainA-note83.npz") as raw:
        tone = raw["p3"]
    rate = sr * row["os"]
    f0 = inc * sr / probe.CYCLE
    guard = settings["guard"]
    alias = am.inharmonic_fraction_db(tone, f0, rate, guard=guard, min_headroom_db=None)
    assert row["p3"] == alias.require()
    assert settings["alias_fraction_window"] == alias.detail["window"]

    with np.load(tmp_path / "chainB-sine-note83.npz") as raw:
        ladder = am.inharmonic_fraction_db(
            raw["b4"], f0, sr, guard=guard, min_headroom_db=None
        )
    assert report["chain_b_sine"][0]["b4"] == ladder.require()
    assert settings["alias_fraction_window"] == ladder.detail["window"]

    power = abs(np.fft.rfft(tone * np.hanning(len(tone)))) ** 2
    upper = np.zeros(len(power), dtype=bool)
    for harmonic in np.arange(f0, rate / 2, f0):
        if harmonic >= sr / 2:
            center = round(harmonic * len(tone) / rate)
            upper[center - guard : center + guard + 1] = True
    assert row["split"]["above"] == pytest.approx(
        power[upper].sum() / power.sum(), abs=0
    )
