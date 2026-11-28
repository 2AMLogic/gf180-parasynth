"""Ground truth and refusal controls for `measure_mono_case`.

THE `_apparatus` HANDLER (#1777), at the end of this file. It wrapped the
rig's construction and the reference render in `except Exception` and raised
whatever it caught as this module's `Refused` -- so a bug in the rig, in the
causality battery or in an estimator was reported as `REFUSED ... qualified
Surge apparatus refused`, exit 2: an execution error filed as a missing
precondition, the #1771 shape. The handler now catches the callee's refusal
vocabulary and nothing wider (see the comment at the site).
RED FIRST against the unfixed handler (2026-11-28, origin/main @ 1c56aaf6):
the five execution-error controls failed with `DID NOT RAISE`. Seen directly
too, outside pytest: `run()` with an injected `KeyError` in the estimator
returned exit 2 and a report reading `REFUSED -- qualified Surge apparatus
refused: 'injected bug in the estimator'`. With the fix: 11 passed.
The positive controls are green before AND after the fix by design, so their
teeth were checked by mutation instead: with the handler narrowed to
`refusal.Refused` alone (the literal reading of "catch the callee's
refusal"), 5 of the 6 go red -- every class `reference_rigs` actually
refuses in; without `AssertionError`, the `RigRefusal`, `InsufficientEvidence`
and `NonFiniteAudio` controls go red.

WHAT THE CONTROLS DO NOT CLAIM. No plugin ran: a stand-in rig raises the
injected exception where the real one would raise its own, so this exercises
the handler's class boundary and not a production path. And the bound the
site states is real: a bug that raises `RuntimeError` or `AssertionError` is
still filed as REFUSED here, because that is how this callee refuses.
"""
from __future__ import annotations

import json
import pathlib
import sys
import types
import wave

import numpy as np
import pytest

import measure_mono_case as m
import voice_fx as vf
import audio_measure as am
import oversampled_osc as os2
import refusal
import rig_qualification as rq


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


# ---------------------------------------------------------------------------
# #1777: `_apparatus`'s handler. The rig's own refusals stay REFUSED; a bug
# does not.
# ---------------------------------------------------------------------------
def _rig_whose_tone_raises(exc):
    class _Rig:
        def __init__(self, *_a, **_k):
            pass

        def osc_tone(self, *_a, **_k):
            raise exc
    return _Rig


def _arm(monkeypatch, exc):
    """dawdreamer importable, Surge's bundle present, and a rig whose tone
    raises `exc`: `_apparatus` then runs its own shipped code up to the rig."""
    monkeypatch.setitem(sys.modules, "dawdreamer", types.ModuleType("dawdreamer"))
    monkeypatch.setattr(m.rr, "PATH_SURGE", pathlib.Path(__file__))
    monkeypatch.setattr(m.rr, "SurgeRig", _rig_whose_tone_raises(exc))


def _apparatus_with(monkeypatch, exc):
    _arm(monkeypatch, exc)
    return m._apparatus()


APPARATUS_REFUSALS = [
    pytest.param(RuntimeError("surge-type2: oscillator 1 reads 'Audio In', wanted 'Classic'"),
                 id="readback-mismatch-RuntimeError"),
    pytest.param(rq.RigRefusal("surge-type2 under dawdreamer: REFUSED -- pins: ..."),
                 id="battery-RigRefusal"),
    pytest.param(am.InsufficientEvidence("spectral_centroid: silent"),
                 id="estimator-InsufficientEvidence"),
    pytest.param(am.NonFiniteAudio("the render held NaN"), id="render-NonFiniteAudio"),
    pytest.param(refusal.Refused("injected"), id="another-modules-Refused"),
]


@pytest.mark.parametrize("exc", APPARATUS_REFUSALS)
def test_apparatus_still_refuses_on_the_rigs_own_refusals(monkeypatch, exc):
    """The positive control. `reference_rigs` refuses in its own vocabulary --
    `RuntimeError` for a pin or readback that does not hold, `RigRefusal` from
    the causality battery, `InsufficientEvidence`/`NonFiniteAudio` from the
    estimators (#1751, #1763) -- and each of those is still this module's
    `Refused`, with the cause chained."""
    with pytest.raises(m.Refused, match=r"^qualified Surge apparatus refused: ") as e:
        _apparatus_with(monkeypatch, exc)
    assert e.value.__cause__ is exc


@pytest.mark.parametrize("bug", [
    TypeError("injected bug"), KeyError("injected bug"), ValueError("injected bug"),
    IndexError("injected bug"), AttributeError("injected bug")],
    ids=["TypeError", "KeyError", "ValueError", "IndexError", "AttributeError"])
def test_apparatus_lets_a_bug_escape_as_an_execution_error(monkeypatch, tmp_path, bug):
    """#1777's injected-bug control, through `run` -- the entry point that
    ships. The handler was `except Exception`, so a `TypeError` in the rig, in
    the battery or in an estimator was reported as `REFUSED ... qualified Surge
    apparatus refused`, exit 2: a bug filed as a missing precondition. It must
    propagate, and no report may be written for it. `RuntimeError` is not among
    the injected types because it IS how this callee refuses (`select_osc`
    raises it on a readback mismatch)."""
    _arm(monkeypatch, bug)
    with pytest.raises(type(bug), match="injected bug") as e:
        m.run(tmp_path / "out")
    assert not isinstance(e.value, m.Refused)
    assert not (tmp_path / "out").exists()


def test_run_still_reports_the_rigs_own_refusal_as_refused(monkeypatch, tmp_path):
    """The positive control through `run`: exit 2 and a report whose status is
    REFUSED, as the module docstring promises for an apparatus that is not
    available."""
    exc = RuntimeError("surge-type2: oscillator 1 reads 'Audio In', wanted 'Classic'")
    _arm(monkeypatch, exc)
    code, rep = m.run(tmp_path / "out")
    assert (code, rep["status"]) == (m.REFUSED, "REFUSED")
    assert rep["reason"] == f"qualified Surge apparatus refused: {exc}"
    assert not list((tmp_path / "out").glob("*.wav"))
