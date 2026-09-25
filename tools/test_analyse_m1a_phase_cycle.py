"""Known answers for the M1A phase-cycle analysis.

The synthetic takes have a psi-dependent odd-partial gain known by
construction, so the decision rule's every branch and the darkening estimate
have an answer independent of both the Mini V3 and the model. Injected
analysis defects must turn the control red."""
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
