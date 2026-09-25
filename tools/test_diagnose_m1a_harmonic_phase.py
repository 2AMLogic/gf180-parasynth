"""Known answers for the M1A persistent-versus-phase-dependent diagnosis.

Every estimator the diagnosis reports through is checked here on signals whose
answer is known independently of the model or the reference, and the
classifier is checked on the three synthetic phrases whose answer is known by
construction. The injected-defect tests show each check can turn red."""
import json
import math

import numpy as np
import pytest

import diagnose_m1a_harmonic_phase as d

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
        "persistent partial deficit": True, "interference only": True, "genuine amplitude change": True}
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
