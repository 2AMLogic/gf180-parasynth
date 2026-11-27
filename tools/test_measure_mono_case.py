"""Ground truth and refusal controls for measure_mono_case."""
from __future__ import annotations

import numpy as np
import pytest

import measure_mono_case as m
import voice_fx as vf
import audio_measure as am
import oversampled_osc as os2


def test_measure_accepts_a_known_saw():
    f0 = 440.0
    t = np.arange(0.7 * m.SR) / m.SR
    x = np.zeros_like(t)
    for k in range(1, int(m.SR / (2 * f0))):
        x += np.sin(2 * np.pi * k * f0 * t) / k
    got = m._measure(x / np.max(np.abs(x)), f0)
    assert abs(got["f0_hz"] - f0) < 0.1
    assert got["inharmonic_db"] < -80


def test_measure_reports_absolute_level_against_a_known_tone():
    f0 = 440.0
    t = np.arange(int(0.7 * m.SR)) / m.SR
    got = m._measure(0.5 * np.sin(2 * np.pi * f0 * t), f0)
    assert abs(got["rms_dbfs"] - (-9.0309)) < 0.01
    assert abs(got["peak_dbfs"] - (-6.0206)) < 0.01


def test_measure_refuses_silence():
    with pytest.raises(m.Refused, match="silent"):
        m._measure(np.zeros(int(0.7 * m.SR)), 440.0)


def test_measure_refuses_nonfinite():
    x = np.ones(int(0.7 * m.SR))
    x[10] = np.nan
    with pytest.raises(m.Refused, match="non-finite"):
        m._measure(x, 440.0)


def test_high_note_oscillator_filter_reduces_alias_metric():
    """The fix must move the independent inharmonic-energy measurement."""
    note = 84
    f0 = vf.note_hz(note)
    n = int(m.SECONDS * m.SR)
    inc = vf.phase_inc(f0)
    raw = vf.OscFx("saw", smooth=False).render(n, inc) / 32768.0
    filtered = vf.OscFx("saw", smooth=True).render(n, inc) / 32768.0
    before = am.inharmonic_fraction_db(raw, f0, m.SR).require("raw")
    after = am.inharmonic_fraction_db(filtered, f0, m.SR).require("filtered")
    assert before - after > 10.0, (before, after)


def test_true_2x_path_is_close_to_the_qualified_external_target():
    note = 84
    f0 = vf.note_hz(note)
    n = int(m.SECONDS * m.SR)
    fixed = os2.render_saw(n, vf.phase_inc(f0)) / 32768.0
    measured = m._measure(fixed, f0)
    # Surge's independently measured target is -47.2265 dB. Keep this gate
    # local and deterministic; the live plugin comparison remains in the
    # measurement command.
    assert measured["inharmonic_db"] < -44.0
    assert abs(measured["f0_cents"]) < 0.02


def _decode(path):
    import wave
    with wave.open(str(path), "rb") as w:
        assert (w.getnchannels(), w.getsampwidth(), w.getframerate()) == (1, 2, m.SR)
        return np.frombuffer(w.readframes(w.getnframes()), dtype="<i2")


def test_component_wavs_replay_the_scored_integer_words_exactly(tmp_path, monkeypatch):
    """Regression for #1547: the writer used to scale by 32767 after the
    measurement divided by 32768, changing ~half the saved words by 1 LSB.
    Drives the real `run()` output path; only the Surge apparatus is stubbed
    (host-specific), with the reference taken from the integer oscillator so
    it passes qualification. Negative control: x32767 fails this assertion."""
    n = int(m.SECONDS * m.SR)
    inc = vf.phase_inc(vf.note_hz(m.NOTE))
    ref_words = np.asarray(vf.OscFx(m.WAVE, smooth=False).render(n, inc))
    monkeypatch.setattr(m, "_apparatus", lambda: {"rig": None, "reference": ref_words / 32768.0})
    _, report = m.run(tmp_path)
    assert report["status"] == "VALID", report
    expect = {
        "dut-polyblep.wav": np.asarray(vf.OscFx(m.WAVE, smooth=True).render(n, inc)),
        "dut-2x-decimated.wav": np.asarray(os2.render_saw(n, inc)),
        "reference-surge.wav": ref_words,
    }
    for name, words in expect.items():
        got = _decode(tmp_path / name)
        assert np.array_equal(got, words.astype(np.int16)), name
    for key, name in (("reference_sha256", "reference-surge.wav"),
                      ("dut_sha256", "dut-polyblep.wav"),
                      ("dut_2x_sha256", "dut-2x-decimated.wav")):
        assert report["audio"][key] == m._sha256(tmp_path / name), key


def test_shared_encoder_round_trips_every_int16_word(tmp_path):
    import manifest as mf
    words = np.arange(-32768, 32768, dtype=np.int64)
    mf._write_wav16(tmp_path / "all.wav", words / 32768.0, m.SR)
    assert np.array_equal(_decode(tmp_path / "all.wav"), words.astype(np.int16))
    assert not hasattr(m, "_write_wav"), "private duplicate encoder must stay removed"
