"""Known answers for the M1A persistent-versus-phase-dependent diagnosis.

Every estimator the diagnosis reports through is checked here on signals whose
answer is known independently of the model or the reference, and the
classifier is checked on the three synthetic phrases whose answer is known by
construction. The injected-defect tests show each check can turn red."""
import io
import json
import math
import os
import shutil
import struct
import sys

import numpy as np
import pytest
from scipy.io import wavfile

import diagnose_m1a_harmonic_phase as d
import refusal

SR = d.SR
EVENT = d.EVENTS[0]


def _tone(hz, amp, phase_at_centre_deg, event=EVENT, n=None):
    n = n or round(d.bass.reference.SECONDS * SR)
    t = np.arange(n) / SR
    return amp * np.cos(2 * np.pi * hz * (t - d.centre_s(event)) + math.radians(phase_at_centre_deg))


@pytest.mark.parametrize("phase", [-170., -45., 0., 30., 120.])
@pytest.mark.parametrize("hz", [130.8, 523.2, 784.9])
def test_projection_known_amplitude_and_centre_phase(hz, phase):
    start, stop = d.window(EVENT)
    value = d.project(_tone(hz, .031, phase), hz, start, stop)
    assert abs(value) == pytest.approx(.031, rel=1e-4)
    assert d.wrap(math.degrees(np.angle(value)) - phase) == pytest.approx(0, abs=.05)


def test_projection_of_a_slowly_rotating_partial_reads_its_centre_phase():
    # the reference's oscillator 2 is 1.05 Hz off the shared projection
    # frequency at h8: it rotates ~95 degrees across the window
    start, stop = d.window(EVENT)
    hz = 523.24
    value = d.project(_tone(hz - 1.054, .02, 40.), hz, start, stop)
    assert d.wrap(math.degrees(np.angle(value)) - 40.) == pytest.approx(0, abs=.5)


def test_projection_refuses_too_few_periods():
    with pytest.raises(d.Refused):
        d.project(np.ones(1000), 20., 0, 1000)


@pytest.mark.parametrize("psi", [-179., -95., 0., 42.6, 156.3])
def test_fit_psi_recovers_a_single_relative_phase(psi):
    theta = [d.wrap(j * psi) for j in range(1, 7)]
    got, residual = d.fit_psi(theta)
    assert d.wrap(got - psi) == pytest.approx(0, abs=1e-6)
    assert residual < 1e-6


def test_fit_psi_reports_a_relative_phase_that_is_not_one_number():
    theta = [d.wrap(j * 30.) for j in range(1, 7)]
    theta[3] += 40.
    assert d.fit_psi(theta)[1] > d.PHASE_FIT_TOL


@pytest.mark.parametrize("psi0", [-120., 0., 156.3])
def test_relative_phase_and_free_running_prediction_on_known_oscillators(psi0):
    drift = {36: -.2634, 43: -.3946}
    osc1, osc2 = d.synth_phrase(psi0, drift)
    rows = [d.relative_phase(osc1, osc2, e) for e in d.EVENTS]
    measured = [r["psi_deg"] for r in rows]
    predicted = d.free_running_prediction(measured[0], {e["note"]: r["drift_hz"]
                                                       for e, r in zip(d.EVENTS, rows)})
    assert measured[0] == pytest.approx(psi0, abs=.2)
    assert max(abs(d.wrap(a - b)) for a, b in zip(measured, predicted)) < .5
    # and the reset hypothesis is visibly wrong for the same signals
    reset = d.reset_prediction(measured)
    assert abs(d.wrap(measured[2] - reset[2])) > 90.


def test_locked_oscillators_are_classified_as_reset_consistent():
    osc1, osc2 = d.synth_phrase(0., {36: -.2634, 43: -.3946}, locked=True)
    rows = [d.relative_phase(osc1, osc2, e) for e in d.EVENTS]
    assert max(abs(r["psi_deg"]) for r in rows) < .2


def test_the_synthetic_control_classifies_every_known_case():
    report = d.synthetic_control()
    assert {name: case["ok"] for name, case in report.items()} == {
        "persistent partial deficit": True, "interference only": True, "genuine amplitude change": True,
        "changing fundamental": True}
    # a changing fundamental moves the ratio, not the partial's absolute level
    assert report["changing fundamental"]["scored_h5"][1] == pytest.approx(-3., abs=.05)
    assert report["changing fundamental"]["lives_in_got"] == {"event 2 h5": "fundamental"}
    # interference and the amplitude change look alike in the mixed signal ...
    assert abs(report["interference only"]["scored_h8"][1]) > 10
    assert abs(report["genuine amplitude change"]["scored_h8"][1]) > 10
    # ... and only the phase-matched residual / contributor energy tells them apart
    assert abs(report["interference only"]["matched_residual_h8"][1]) < d.TOL
    assert abs(report["genuine amplitude change"]["matched_residual_h8"][1]) > d.TOL


def test_classifier_rules():
    assert d.classify([.4, -.6], [.1, .1], 0.) == "within tolerance"
    assert d.classify([3., 20.], [.2, -.3], 0.) == "phase-dependent"
    assert d.classify([-6., -5.5], [-6., -5.6], 0.) == "persistent"
    assert d.classify([-6., 8.], [-6., -5.6], 0.) == "mixed"
    assert d.classify([0., -12.], [0., -12.], 12.) == "amplitude change"
    assert d.classify([3., 20.], None, 0.) == "unresolved"
    # a genuine change with NO contributor evidence is still not called phase
    assert d.classify([0., -12.], [0., -12.], 0.) == "unresolved"


def test_injected_defect_classifier_that_ignores_the_matched_residual_fails_the_control(monkeypatch):
    real = d.classify
    monkeypatch.setattr(d, "classify", lambda s, m, e, **kw: real(s, [0.] * len(s), 0.))
    report = d.synthetic_control()
    assert not report["persistent partial deficit"]["ok"]
    assert not report["genuine amplitude change"]["ok"]


def test_injected_defect_sign_flipped_phase_breaks_free_running_prediction(monkeypatch):
    real = d.project
    monkeypatch.setattr(d, "project", lambda *a: real(*a).conjugate())
    osc1, osc2 = d.synth_phrase(156.3, {36: -.2634, 43: -.3946})
    rows = [d.relative_phase(osc1, osc2, e) for e in d.EVENTS]
    predicted = d.free_running_prediction(rows[0]["psi_deg"], {e["note"]: r["drift_hz"]
                                                              for e, r in zip(d.EVENTS, rows)})
    assert max(abs(d.wrap(r["psi_deg"] - p)) for r, p in zip(rows, predicted)) > 45.


def test_model_psi_integer_arithmetic_for_the_selected_patch():
    patch = d._patch()
    # exact octave in the registers, but the 2x path advances (inc//2)*2:
    # oscillator 1's odd increment loses one LSB, a +2 LSB/sample drift
    incs = d.lead.vf.VoiceFx.note_incs(36, patch["detune"])
    assert incs[1] == 2 * incs[0] and incs[0] % 2 == 1
    rate_hz = 2 * SR / d.CYCLE
    expected = 360 * rate_hz * (d.centre_s(d.EVENTS[2]) - d.EVENTS[0]["on_s"])
    assert d.model_psi(patch, 0, d.centre_s(d.EVENTS[2])) == pytest.approx(expected, abs=1e-6)
    assert d.model_psi(patch, d.CYCLE // 4, d.centre_s(d.EVENTS[0])) == pytest.approx(
        90 + 360 * rate_hz * (d.centre_s(d.EVENTS[0]) - .1), abs=1e-6)


def test_committed_report_reproduces_from_frozen_audio_without_rendering():
    """The frozen-audio half of report.json: reference phase rows, free-running
    and reset errors, and the reference partial table."""
    report = json.loads((d.OUT / "report.json").read_text())
    _, ref, controls = d.reference_audio()
    rows = [d.relative_phase(controls["osc1_open"], controls["osc2_open"], e) for e in d.EVENTS]
    assert [r["psi_deg"] for r in rows] == pytest.approx(report["reference_phase"]["psi_deg"], abs=1e-9)
    for cell in report["table"]:
        i = [e["on_s"] for e in d.EVENTS].index(cell["on_s"])
        mine = d.partials(ref, d.EVENTS[i])
        assert mine["absolute_dbfs"][f"h{cell['k']}"] == pytest.approx(cell["abs_reference"], abs=1e-9)
    assert max(abs(e) for e in report["reference_phase"]["free_running_error_deg"]) < 2.
    assert abs(report["reference_phase"]["reset_error_deg"][2]) > 90.


def test_lives_in_separates_fundamental_from_partial():
    base = {f"h{k}": -.9 for k in range(1, 13)}          # a common output-gain offset only
    assert set(d.lives_in(base).values()) == {"within tolerance"}
    assert d.lives_in({**base, "h7": -6.9})["h7"] == "partial"
    assert d.lives_in({**{f"h{k}": 0. for k in range(1, 13)}, "h1": 3.})["h5"] == "fundamental"
    assert d.lives_in({**base, "h1": 2.1, "h5": -4.})["h5"] == "both"


def test_injected_defect_ratio_only_attribution_fails_the_changing_fundamental_control(monkeypatch):
    monkeypatch.setattr(d, "lives_in", lambda errors, tol=d.TOL: {
        k: ("within tolerance" if abs(v - errors["h1"]) <= tol else "partial")
        for k, v in errors.items() if k != "h1"})
    assert not d.synthetic_control()["changing fundamental"]["ok"]


# ---- the committed audio the reproduction preconditions read (#1862) ----
def _bad_wavs():
    """One file per way a header can fail its reader: ValueError, struct.error
    (a header cut inside a chunk), UnboundLocalError (a size field that lies)."""
    buf = io.BytesIO()
    wavfile.write(buf, SR, np.zeros(4800, dtype=np.int16))
    good = buf.getvalue()
    fmt, data = good[20:36], good[44:]
    chunk = lambda cid, payload: cid + struct.pack("<I", len(payload)) + payload
    return {"non-RIFF": b"not a WAV", "empty": b"", "cut-inside-chunk": good[:20],
            "RIFF-magic-only": b"RIFF",
            "size-field-lies": b"RIFF" + struct.pack("<I", 4 + 8 + len(fmt)) + b"WAVE"
                              + chunk(b"fmt ", fmt) + chunk(b"data", data)}


BAD_WAVS = {"missing": None, **_bad_wavs()}
N_REF = len(d.bass.load_reference()[1])


def _main_without_rendering(monkeypatch):
    """main() with every render answering the same int16 silence, so the
    committed-audio reads and the refusal boundary are what is exercised."""
    monkeypatch.setattr(d, "render", lambda patch, osc2_phase=0, mix=None: np.zeros(N_REF, dtype=np.int16))
    monkeypatch.setattr(sys, "argv", ["diagnose_m1a_harmonic_phase.py"])


@pytest.mark.parametrize("name", sorted(BAD_WAVS))
def test_unreadable_committed_model_wav_is_refused_naming_it(monkeypatch, tmp_path, capsys, name):
    _main_without_rendering(monkeypatch)
    model = tmp_path / "m1a-model.wav"
    if BAD_WAVS[name] is not None:
        model.write_bytes(BAD_WAVS[name])
    monkeypatch.setattr(d, "MODEL_WAV", model)
    before = (d.OUT / "report.json").read_bytes()
    assert d.main() == 2
    out = capsys.readouterr().out
    assert "REFUSED:" in out and "m1a-model.wav" in out
    assert (d.OUT / "report.json").read_bytes() == before


@pytest.mark.parametrize("name", sorted(BAD_WAVS))
def test_unreadable_detune_diagnostic_wav_is_refused_naming_it(monkeypatch, tmp_path, capsys, name):
    """Past the first precondition: the committed model WAV reproduces the
    (stubbed) selected render, so the run reaches the detune-diagnostic read."""
    _main_without_rendering(monkeypatch)
    model = tmp_path / "m1a-model.wav"
    wavfile.write(model, SR, np.zeros(N_REF, dtype=np.int16))
    monkeypatch.setattr(d, "MODEL_WAV", model)
    detune = tmp_path / "volume-mapping"
    detune.mkdir()
    shutil.copy(d.DETUNE_DIR / "detune-diagnostic.json", detune / "detune-diagnostic.json")
    if BAD_WAVS[name] is not None:
        (detune / "detune-diagnostic.wav").write_bytes(BAD_WAVS[name])
    monkeypatch.setattr(d, "DETUNE_DIR", detune)
    before = (d.OUT / "report.json").read_bytes()
    assert d.main() == 2
    out = capsys.readouterr().out
    assert "REFUSED:" in out and "detune-diagnostic.wav" in out
    assert (d.OUT / "report.json").read_bytes() == before


def test_a_missing_isolated_control_wav_is_refused_before_reference_audio_reads_it(tmp_path, monkeypatch):
    """Pin (#1862 section D): `load_reference()` hashes every control WAV under
    `except (OSError, KeyError, ValueError)`, so the read at `reference_audio()`
    is never reached with a control that is not there. The refusal is
    `bass.Refused`, the PARENT of this module's class -- the cross-module shape
    #1265 fixed at the CLI boundary, and why the pin asserts the base class."""
    manifest = json.loads(d.bass.MANIFEST.read_text())
    for item in manifest["renders"]:
        os.symlink(d.bass.MANIFEST.parent / item["file"], tmp_path / item["file"])
    (tmp_path / "manifest.json").write_text(json.dumps(manifest))     # names controls that are absent
    real = d.bass.load_reference
    monkeypatch.setattr(d.bass, "load_reference", lambda path=None: real(tmp_path / "manifest.json"))
    with pytest.raises(refusal.Refused, match="unavailable"):
        d.reference_audio()
