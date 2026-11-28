"""Known answers for the M1A phase-cycle analysis.

The synthetic takes have a psi-dependent odd-partial gain known by
construction, so the decision rule's every branch and the darkening estimate
have an answer independent of both the Mini V3 and the model. Injected
analysis defects must turn the control red."""
import json
import sys

import numpy as np
import pytest

import analyse_m1a_phase_cycle as a


@pytest.fixture(scope="module")
def control():
    return a.synthetic_control()


def test_known_answer_control_passes(control):
    assert all(v["ok"] for v in control.values()), control


@pytest.mark.parametrize("defect", ["psi_offset_180", "ignore_take_agreement"])
def test_injected_defects_turn_the_control_red(defect):
    report = a.synthetic_control(defect)
    assert not all(v["ok"] for v in report.values())


def test_bins_centre_on_zero_and_wrap():
    assert a.bin_of(0.) == 0 and a.bin_of(9.9) == 0 and a.bin_of(-9.9) == 0
    assert a.bin_of(180.) == a.bin_of(-179.) == 9
    assert a.bin_centre(a.bin_of(-40.)) == pytest.approx(-40.)


def test_locked_oscillators_refuse_for_coverage():
    # a static-phase render cannot be compared: psi never leaves one bin
    mix, o1, o2, _ = a.synth_take(-10., 0., 0.)
    m = a.measure(mix, o1, o2, .1, 12.)
    with pytest.raises(a.Refused, match="coverage"):
        a.summarise(m["windows"])


def test_psi_that_is_not_one_number_refuses():
    mix, o1, o2, _ = a.synth_take(0., 0., -.2634)
    rng = np.random.default_rng(3)
    t = np.arange(len(o2)) / a.SR
    # oscillator 2's partials with unrelated phases: theta_j != j*psi
    scrambled = sum(.05 * .535 / j * np.sin(2 * np.pi * j * (2 * a.F36 - .2634) * t + rng.uniform(0, 2 * np.pi))
                    for j in range(1, a.JMAX + 1)) * (o2 != 0)
    with pytest.raises(a.Refused, match="single relative phase"):
        a.measure(mix, o1, scrambled, .1, 12.)


def _summary(dark, rng_db, darkest=0.):
    parts = {h: {"dark_db": dark, "range_db": rng_db, "darkest_bin_deg": darkest,
                 "binned_dbfs": [0.] * a.NBINS} for h in a.ODD}
    return {"partials": parts}


def test_decision_boundaries_are_the_preregistered_ones():
    model = _summary(-10., 11.)
    assert a.decide(_summary(0., .9), _summary(0., .9), _summary(0., .9), model)["verdict"] == "SUPPORTS-DRIVE"
    assert a.decide(_summary(0., 1.1), _summary(0., 1.1), _summary(0., 1.1), model)["verdict"] == "INCONCLUSIVE"
    assert a.decide(_summary(-5., 6.), _summary(-5., 6.), _summary(-5., 6.), model)["verdict"] == "REDIRECT-TO-PHASE"
    assert a.decide(_summary(-5., 6., 60.), _summary(-5., 6., 60.), _summary(-5., 6., 60.),
                    model)["verdict"] == "INCONCLUSIVE"            # darkest away from 0
    assert a.decide(_summary(-4.9, 6.), _summary(-4.9, 6.), _summary(-4.9, 6.), model)["verdict"] == "INCONCLUSIVE"
    weak_model = _summary(-2.9, 4.)
    assert a.decide(_summary(0., .5), _summary(0., .5), _summary(0., .5), weak_model)["step"] == 2
    disagree = _summary(-1.2, 1.)
    assert a.decide(_summary(0., .5), disagree, _summary(0., .5), model)["step"] == 1


# ---- a capture record that cannot be read is REFUSED, not a traceback (#1604)
def _analyse(monkeypatch, tmp_path, capsys, record):
    """`main()` over a capture record written under a scratch CAPTURE dir.

    The known-answer control is answered, not exercised: it has its own tests
    above and costs ~20 s a run. The record and the reader are real."""
    (tmp_path / "capture.json").write_text(json.dumps(record))
    monkeypatch.setattr(a, "CAPTURE", tmp_path)
    monkeypatch.setattr(a, "synthetic_control", lambda defect=None: {"stub": {"ok": True}})
    monkeypatch.setattr(sys, "argv", ["analyse_m1a_phase_cycle.py"])    # main() reads argv for --control
    code = a.main()
    return code, capsys.readouterr().out


def test_the_committed_capture_carries_the_key_the_analysis_reads():
    # the guard below must be satisfiable against the record that ships: the
    # key is there, and it is the mapping `load_capture` dereferences it as
    assert isinstance(json.loads((a.CAPTURE / "capture.json").read_text())["takes"], dict)


def test_a_capture_record_without_takes_is_refused(monkeypatch, tmp_path, capsys):
    # #1604's own input: `KeyError: 'takes'`, exit 1, before the repair
    code, out = _analyse(monkeypatch, tmp_path, capsys, {"state": "CAPTURED"})
    assert code == 2 and "REFUSED" in out and "'takes'" in out
    # and nothing was reported as data
    assert not (tmp_path / "report.json").exists()
    assert json.loads((tmp_path / "analysis-refused.json").read_text())["state"] == "REFUSED"


def test_a_capture_record_with_null_takes_is_refused(monkeypatch, tmp_path, capsys):
    code, out = _analyse(monkeypatch, tmp_path, capsys, {"state": "CAPTURED", "takes": None})
    assert code == 2 and "'takes'" in out


@pytest.mark.parametrize("takes", [5, "x", [], True])
def test_a_capture_record_with_a_wrong_typed_takes_is_refused(monkeypatch, tmp_path, capsys, takes):
    # #1679: present and not null, so the guard passes it, and
    # `record["takes"].items()` one line later is an AttributeError -- exit 1
    # by traceback, the outcome #1604 removed, reached by a one-character edit
    code, out = _analyse(monkeypatch, tmp_path, capsys, {"state": "CAPTURED", "takes": takes})
    assert code == 2 and "REFUSED" in out
    assert "'takes'" in out and "dict" in out
    assert not (tmp_path / "report.json").exists()
    assert json.loads((tmp_path / "analysis-refused.json").read_text())["state"] == "REFUSED"


@pytest.mark.parametrize("record", [[], 5, None])
def test_a_capture_record_that_is_not_an_object_is_refused(monkeypatch, tmp_path, capsys, record):
    code, out = _analyse(monkeypatch, tmp_path, capsys, record)
    assert code == 2 and "REFUSED" in out


def test_an_uncaptured_record_keeps_its_own_refusal(monkeypatch, tmp_path, capsys):
    # the refusal that predates #1604: a record that is not CAPTURED says so,
    # whatever else it carries
    code, out = _analyse(monkeypatch, tmp_path, capsys, {"state": "ABANDONED", "takes": {}})
    assert code == 2 and "not CAPTURED" in out
