"""Known answers for the drive experiment's pre-declared rules: the volume
compensation, the regression detector and the choice rule. Each check is also
shown to fire on an injected defect."""
import json

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


# ---- a record main() reads with [] and cannot read is REFUSED (exit 2), not a
# KeyError traceback (exit 1) (#1604, #1622). One control per key, through the
# tool's own main(), on doctored copies of the committed records.
def _main(monkeypatch, tmp_path, capsys, **records):
    """main() with each named module path (RECORD, PHASE_REPORT) pointing at a
    scratch copy of its committed record, doctored by the caller."""
    monkeypatch.setattr(m, "OUT", tmp_path / "out")
    for name, record in records.items():
        path = tmp_path / f"{name}.json"
        path.write_text(json.dumps(record))
        monkeypatch.setattr(m, name, path)
    rc = m.main()
    return rc, capsys.readouterr().out


@pytest.mark.parametrize("key", ("reference", "summaries"))
def test_a_phase_cycle_report_without_a_key_is_refused(monkeypatch, tmp_path, capsys, key):
    phase = json.loads(m.PHASE_REPORT.read_text())
    del phase[key]
    rc, printed = _main(monkeypatch, tmp_path, capsys, PHASE_REPORT=phase)
    assert rc == 2 and "REFUSED" in printed and repr(key) in printed
    assert not (tmp_path / "out").exists()


def test_an_m1a_record_without_diagnostics_is_refused(monkeypatch, tmp_path, capsys):
    record = json.loads(m.RECORD.read_text())
    del record["diagnostics"]
    rc, printed = _main(monkeypatch, tmp_path, capsys, RECORD=record)
    assert rc == 2 and "REFUSED" in printed and "'diagnostics'" in printed
    assert not (tmp_path / "out").exists()


@pytest.mark.parametrize("phase, why", [({}, "'reference'"), ([], "not a JSON object"),
                                        (None, "not a JSON object")])
def test_a_phase_cycle_report_that_is_not_an_object_is_refused(monkeypatch, tmp_path, capsys, phase, why):
    rc, printed = _main(monkeypatch, tmp_path, capsys, PHASE_REPORT=phase)
    assert rc == 2 and "REFUSED" in printed and why in printed


def test_a_phase_cycle_report_with_a_null_key_is_refused(monkeypatch, tmp_path, capsys):
    phase = json.loads(m.PHASE_REPORT.read_text())
    phase["summaries"] = None
    rc, printed = _main(monkeypatch, tmp_path, capsys, PHASE_REPORT=phase)
    assert rc == 2 and "REFUSED" in printed and "'summaries'" in printed and "null" in printed
