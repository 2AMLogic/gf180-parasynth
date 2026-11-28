"""Known answers for the drive experiment's pre-declared rules: the volume
compensation, the regression detector and the choice rule. Each check is also
shown to fire on an injected defect."""
import copy

import pytest

import measure_m1a_drive as m


def test_compensation_is_the_mean_gain_window_difference():
    assert m.compensation_db([-26., -26.2, -26.], [-29.5, -29.8, -29.6]) == pytest.approx(3.5667, abs=1e-4)
    assert m.compensation_db([-26.] * 3, [-26.] * 3) == 0.


def _measured(pitch_err=0., gain_err=-1., h5=(.5, -.3, 2.), valid_attack=False):
    prop = lambda e, tol: {"valid": True, "value": e, "reference": 0., "error": e, "tolerance": tol}
    events = [{"note": n, "on_s": t, "harmonic_error_db_model_minus_reference": {"h5": v, "h8": 19.8}}
              for n, t, v in zip((36, 43, 36), (.1, 2.1, 4.1), h5)]
    return {"properties": {"Pitch": prop(pitch_err, 1.), "Gain": prop(gain_err, 3.),
                           "Envelope release": prop(8., 20.), "Clipping": prop(0., .01),
                           "Harmonic shape": prop(19.8, 1.),
                           "Envelope attack": {"valid": valid_attack}},
            "events": events}


def test_no_regressions_against_itself():
    assert m.regressions(_measured(), _measured()) == []


def test_every_regression_kind_is_caught():
    base = _measured()
    assert any("Gain" in r for r in m.regressions(base, _measured(gain_err=-3.5)))
    assert any("Pitch" in r for r in m.regressions(base, _measured(pitch_err=1.2)))
    got = m.regressions(base, _measured(h5=(1.4, -.3, 2.)))
    assert got and "h5" in got[0] and "36 @0.1" in got[0]
    assert any("validity" in r for r in m.regressions(base, _measured(valid_attack=True)))
    # a failing cell that stays failing is not a regression; one that improves is fine
    assert m.regressions(base, _measured(h5=(.5, -.3, 5.))) == []


def _row(regs, shape, odd):
    return {"regressions": regs, "harmonic_shape_db": shape, "odd_range_mean_db": odd}


def test_choice_rule():
    base = _row([], 19.82, 10.)
    assert m.choose({.75: base, .5: _row([], 19.0, 8.), .25: _row([], 18.0, 6.)}, base) == .25
    assert m.choose({.75: base, .5: _row([], 19.0, 8.), .25: _row(["x"], 18.0, 6.)}, base) == .5
    assert m.choose({.75: base, .5: _row([], 19.5, 8.)}, base) is None          # < 0.5 dB better
    assert m.choose({.75: base, .5: _row([], 18.0, 10.5)}, base) is None        # odd range not reduced
    assert m.choose({.75: base}, base) is None                                   # baseline never chosen


NON_FINITE = [float("nan"), float("inf"), -float("inf")]


def _record_of(measured):
    """An M1A record whose graded properties are exactly `measured`'s."""
    return {"diagnostics": {"properties": copy.deepcopy(measured["properties"])}}


def test_the_baseline_reproduces_a_record_holding_its_own_values():
    """The control for the refusals below (#1994). They exist because every
    ordering comparison against NaN is False (#134): `abs(value - recorded) >
    tol` did not trip on a recorded NaN, so "the baseline reproduces the
    record" was satisfied by a value that is not a number. Each control
    replaces the RECORD operand; the operand this render computes is not what
    they assert."""
    measured = _measured()
    assert m.reproduces_record(measured, _record_of(measured)) is None


def test_a_recorded_property_the_render_does_not_reproduce_is_refused():
    measured, record = _measured(), _record_of(_measured())
    record["diagnostics"]["properties"]["Gain"]["value"] += 1e-5
    with pytest.raises(m.Refused, match="baseline Gain does not reproduce"):
        m.reproduces_record(measured, record)


@pytest.mark.parametrize("bad", NON_FINITE, ids=["nan", "inf", "-inf"])
def test_a_recorded_property_that_is_not_a_finite_number_is_refused_naming_it(bad):
    """NaN in only the record operand: refused, naming the property."""
    measured, record = _measured(), _record_of(_measured())
    record["diagnostics"]["properties"]["Gain"]["value"] = bad
    with pytest.raises(m.Refused) as info:
        m.reproduces_record(measured, record)
    assert "diagnostics.properties[Gain].value" in str(info.value) and repr(bad) in str(info.value)


def _curves(range_db):
    return {h: {"range_db": range_db} for h in m.ODD}


def test_the_baseline_moving_phase_curves_reproduce_219_and_a_moved_one_is_refused():
    assert m.reproduces_model_curves(_curves(10.0), _curves(10.0)) is None
    with pytest.raises(m.Refused, match="does not reproduce #219's model curves"):
        m.reproduces_model_curves(_curves(10.0), _curves(10.0 + 1e-8))


@pytest.mark.parametrize("bad", NON_FINITE, ids=["nan", "inf", "-inf"])
def test_a_recorded_219_curve_range_that_is_not_a_finite_number_is_refused_naming_the_partial(bad):
    """NaN in only the record operand: refused, naming the partial."""
    recorded = _curves(10.0)
    recorded[m.ODD[2]]["range_db"] = bad
    with pytest.raises(m.Refused) as info:
        m.reproduces_model_curves(_curves(10.0), recorded)
    assert f"summaries.model.partials.{m.ODD[2]}.range_db" in str(info.value), str(info.value)
    assert repr(bad) in str(info.value)
