"""Known answers for the #337 phase-aware M1A rules. Each guard is shown to fire
on the input it exists to stop, and the inputs that legitimately pass it are
checked too. No render: the renders are the instrument's job."""
import math

import pytest

import phase_aware_m1a as m


def test_synthetic_control_passes():
    report = m.synthetic_control()
    assert all(v["ok"] for v in report.values()), {k: v for k, v in report.items() if not v["ok"]}


def test_phase_aware_error_is_blind_to_power_means_but_not_to_phase():
    """The defeating input for a phase-BLIND comparison: the same power means,
    with the curves misaligned. The phase-aware metric must see it."""
    ref = m._synthetic_summary()
    shifted = m._synthetic_summary(psi_shift_deg=90.)
    errors = m.phase_aware_errors(shifted, ref)
    assert errors["h2"] > 3. and errors["h3"] == pytest.approx(0., abs=1e-12)


def test_persistent_offset_on_even_contributor_is_seen():
    ref = m._synthetic_summary()
    errors = m.phase_aware_errors(m._synthetic_summary(osc2_db={"h6": 3.}), ref)
    assert errors["h6"] > .5 and errors["h4"] == pytest.approx(0., abs=1e-12)


def test_ratio_curves_refuse_non_finite():
    s = m._synthetic_summary()
    s["partials"]["h5"]["binned_dbfs"][3] = float("nan")
    with pytest.raises(m.Refused):
        m.ratio_curves(s)


def test_conditioning_and_inclusion():
    assert m.conditioning_db(1 + 0j, 1 + 0j) == pytest.approx(0.)
    assert m.conditioning_db(1 + 0j, -1 + 0j) == -math.inf
    assert m.included("h5", {}) is True                      # odd: always
    assert m.included("h4", {"h4": -5.9}) is True
    assert m.included("h4", {"h4": -6.1}) is False
    with pytest.raises(m.Refused):
        m.conditioning_db(0j, 0j)


def test_brightness_guard():
    # darker than BOTH baseline and reference by > 0.5 dB: rejected
    assert not m.brightness_ok(-10., -9., -9.)
    # darker than baseline but toward a darker reference: allowed
    assert m.brightness_ok(-10., -9., -10.2)
    # brighter: allowed
    assert m.brightness_ok(-8., -9., -9.)
    with pytest.raises(m.Refused):
        m.brightness_db({"h1": 0.})


def _row(max_err, violations=(), bright=-9., ref_bright=-9.):
    return {"max_error_db": max_err, "violations": list(violations),
            "brightness_db": bright, "reference_brightness_db": ref_bright}


def test_select_chooses_lowest_eligible():
    rows = {.75: _row(3.8), .5: _row(1.8), .25: _row(1.6)}
    choice, reasons = m.select(rows)
    assert choice == .25 and reasons == {.5: [], .25: []}


def test_select_rejects_each_violation():
    rows = {.75: _row(3.8), .5: _row(3.5), .25: _row(1.6, violations=["Gain: pass -> fail (-1.0 -> -9.7)"])}
    choice, reasons = m.select(rows)
    assert choice is None
    assert "not >= 0.5 dB" in reasons[.5][0] and "preservation" in reasons[.25][0]
    rows = {.75: _row(3.8), .25: _row(1.6, bright=-11.)}      # wins by going dark
    choice, reasons = m.select(rows)
    assert choice is None and "darker" in reasons[.25][0]


def test_preservation_violations_quieter_candidate():
    prop = lambda e, tol: {"valid": True, "value": e, "reference": 0., "error": e, "tolerance": tol}
    base = {"Gain": prop(-1., 3.), "Pitch": prop(0., 1.), "Envelope attack": {"valid": False}}
    quiet = {"Gain": prop(-9.7, 3.), "Pitch": prop(0., 1.), "Envelope attack": {"valid": False}}
    assert m.preservation_violations(base, base) == []
    assert m.preservation_violations(base, quiet) == ["Gain: pass -> fail (-1.000 -> -9.700)"]
    valid = {**base, "Envelope attack": prop(1., 5.)}
    assert "validity" in m.preservation_violations(base, valid)[0]


def _errors(v43=1., other=1., h4=0.):
    return [{h: (v43 if i == m.MIDI43_EVENT else other) if h != "h4" else h4 for h in m.PARTIALS}
            for i in range(3)]


def test_confirmation_stats_exclude_notches():
    include = [{h: h != "h8" for h in m.PARTIALS} for _ in range(3)]
    errors = _errors()
    for e in errors:
        e["h8"] = 20.                       # a notch cell: excluded, so it cannot dominate
    s = m.confirmation_stats(errors, include)
    assert s["n_included"] == 30 and s["max_abs_db"] < 20.


def test_confirm_each_rule_fires():
    inc = [{h: True for h in m.PARTIALS} for _ in range(3)]
    base = {**m.confirmation_stats(_errors(3., 3.), inc), "brightness_db": -9.}
    good = {**m.confirmation_stats(_errors(.5, .5), inc), "brightness_db": -9.}
    assert m.confirm(base, good, -9.) == ("CONFIRMED", [])
    small = {**m.confirmation_stats(_errors(2.8, 2.8), inc), "brightness_db": -9.}
    assert "E_conf" in m.confirm(base, small, -9.)[1][0]
    worse43 = {**m.confirmation_stats(_errors(3.5, .1), inc), "brightness_db": -9.}
    assert any("E_43" in f for f in m.confirm(base, worse43, -9.)[1])
    dark = {**good, "brightness_db": -12.}
    assert any("darker" in f for f in m.confirm(base, dark, -9.)[1])
    # better RMS everywhere, but three cells leave the 1 dB band: rule 3 alone fires
    base_tol = {**m.confirmation_stats(_errors(1., 1., h4=1.), inc), "brightness_db": -9.}
    lose = {**m.confirmation_stats(_errors(0., 0., h4=1.5), inc), "brightness_db": -9.}
    assert m.confirm(base_tol, lose, -9.)[1] == ["cells within 1.0 dB fell 33 -> 30"]


def test_injected_phase_blind_metric_turns_the_control_red(monkeypatch):
    """Injected defect: the phase-BLIND comparison (difference of psi power
    means), i.e. the quantity a fit to unequal phases cannot separate from
    timbre. The known-answer control must go red on the psi-relabel case."""
    import numpy as np

    def blind(model_summary, reference_summary):
        def pm(s, h):
            return 10 * np.log10(np.mean(10 ** (np.asarray(s["partials"][h]["binned_dbfs"]) / 10)))
        return {h: abs((pm(model_summary, h) - pm(model_summary, "h1"))
                       - (pm(reference_summary, h) - pm(reference_summary, "h1"))) for h in m.PARTIALS}
    monkeypatch.setattr(m, "phase_aware_errors", blind)
    report = m.synthetic_control()
    assert not report["psi relabelled 60 deg -> h2 error > 3 dB"]["ok"]


def test_injected_zero_metric_turns_the_control_red(monkeypatch):
    """Start red: a stub metric with the right signature and no behaviour."""
    monkeypatch.setattr(m, "phase_aware_errors", lambda a, b: {h: 0. for h in m.PARTIALS})
    report = m.synthetic_control()
    assert sum(not v["ok"] for v in report.values()) >= 3
