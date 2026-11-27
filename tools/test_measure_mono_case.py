"""Ground truth and refusal controls for measure_mono_case."""
from __future__ import annotations

import json
import wave

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


def _run_with_reference(monkeypatch, tmp_path, ref):
    monkeypatch.setattr(m, "_apparatus", lambda: {"rig": None, "reference": ref})
    return m.run(tmp_path / "out")


def _saw_reference(peak):
    n = int(m.SECONDS * m.SR)
    t = np.arange(n) / m.SR
    f0 = vf.note_hz(m.NOTE)
    x = sum(np.sin(2 * np.pi * f0 * k * t) / k for k in range(1, 13))
    return x / np.max(np.abs(x)) * peak


def _over_range_reference(sign):
    ref = sign * _saw_reference(1.01)
    return np.where(sign * ref < 0, np.clip(ref, -1.0, 1.0), ref)


def _assert_retained_wavs(out, rep):
    names = [rep["audio"][k] for k in ("reference", "dut", "dut_2x")]
    assert sorted(p.name for p in out.glob("*.wav")) == sorted(names)
    for key in ("reference", "dut", "dut_2x"):
        assert m._sha256(out / rep["audio"][key]) == rep["audio"][key + "_sha256"], key


def _assert_refused(out, name, code, rep):
    assert (code, rep["status"]) == (2, "REFUSED")
    assert rep["reason"].startswith(f"{name} signal ")
    assert "full scale" in rep["reason"]
    assert not list(out.glob("*.wav"))
    assert json.loads((out / "report.json").read_text()) == rep


@pytest.mark.parametrize("ref, endpoint", [
    (_saw_reference(0.5), None), (_saw_reference(1.0), 1.0), (-_saw_reference(1.0), -1.0)],
    ids=["half-scale", "exact-plus-one", "exact-minus-one"])
def test_in_range_reference_is_valid_and_exact_full_scale_is_allowed(monkeypatch, tmp_path, ref, endpoint):
    if endpoint is not None:
        assert endpoint in ref and np.max(np.abs(ref)) == 1.0
    code, rep = _run_with_reference(monkeypatch, tmp_path, ref)
    assert (code, rep["status"]) == (0, "VALID")
    _assert_retained_wavs(tmp_path / "out", rep)


@pytest.mark.parametrize("sign", [1.0, -1.0], ids=["above-plus-one", "below-minus-one"])
def test_over_range_reference_is_refused_and_publishes_no_wav(monkeypatch, tmp_path, sign):
    ref = _over_range_reference(sign)
    assert (sign * ref > 1.0).any() and not (sign * ref < -1.0).any()
    code, rep = _run_with_reference(monkeypatch, tmp_path, ref)
    _assert_refused(tmp_path / "out", "reference", code, rep)


@pytest.mark.parametrize("name", ["dut", "dut_2x"])
def test_over_range_dut_signal_is_refused_by_name(monkeypatch, tmp_path, name):
    over = 32768.0 * 1.02 * _saw_reference(1.0)
    if name == "dut":
        monkeypatch.setattr(vf.OscFx, "render", lambda self, n, inc: over)
    else:
        monkeypatch.setattr(os2, "render_saw", lambda n, inc: over)
    code, rep = _run_with_reference(monkeypatch, tmp_path, _saw_reference(0.5))
    _assert_refused(tmp_path / "out", name, code, rep)


@pytest.mark.parametrize("bad", [np.nan, np.inf, -np.inf])
def test_full_scale_guard_refuses_non_finite_input(bad):
    x = _saw_reference(0.5)
    x[7] = bad
    with pytest.raises(m.Refused, match="non-finite"):
        m._assert_full_scale("reference", x)


def test_full_scale_guard_passes_exact_endpoints_and_counts_the_excursion():
    for x in (_saw_reference(0.5), _saw_reference(1.0), -_saw_reference(1.0)):
        m._assert_full_scale("dut_2x", x)
    x = _saw_reference(1.0)
    x[7], x[9] = 1.5, -1.5
    with pytest.raises(m.Refused, match=r"^dut_2x signal exceeds full scale: 2 of \d+ samples"):
        m._assert_full_scale("dut_2x", x)


def _read_words(path):
    with wave.open(str(path), "rb") as w:
        return np.frombuffer(w.readframes(w.getnframes()), dtype="<i2")


@pytest.mark.parametrize("name", ["reference", "dut", "dut_2x"])
def test_without_the_full_scale_guard_an_over_range_run_is_a_control_that_stays_red(monkeypatch, tmp_path, name):
    """The defect as it shipped (#1552), kept where the verdict is issued: with no
    full-scale check the writer hard-clips the signal and the run still reports
    VALID beside a WAV that no longer replays what was scored."""
    monkeypatch.setattr(m, "_assert_full_scale", lambda signal, x: None)
    over = _over_range_reference(1.0)
    ref = over
    if name == "dut":
        monkeypatch.setattr(vf.OscFx, "render", lambda self, n, inc: 32768.0 * over)
        ref = _saw_reference(0.5)
    elif name == "dut_2x":
        monkeypatch.setattr(os2, "render_saw", lambda n, inc: 32768.0 * over)
        ref = _saw_reference(0.5)
    code, rep = _run_with_reference(monkeypatch, tmp_path, ref)
    assert (code, rep["status"]) == (0, "VALID")
    words = _read_words(tmp_path / "out" / rep["audio"][name])
    assert np.count_nonzero(words == 32767) >= np.count_nonzero(over > 1.0)
