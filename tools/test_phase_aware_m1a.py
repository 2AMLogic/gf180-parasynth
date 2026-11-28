"""Known answers for the #337 phase-aware M1A rules. Each guard is shown to fire
on the input it exists to stop, and the inputs that legitimately pass it are
checked too. No render: the renders are the instrument's job."""
import copy
import json
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


# ---- invalid measurements REFUSE (review of #583: NaN defeated every guard) ----
NAN = float("nan")


def test_select_refuses_nan_candidate():
    """Defeating input: a NaN candidate used to be chosen with no violations."""
    with pytest.raises(m.Refused):
        m.select({.75: _row(3.8), .25: _row(NAN)})
    for key in ("brightness_db", "reference_brightness_db"):
        with pytest.raises(m.Refused):
            m.select({.75: _row(3.8), .25: {**_row(1.6), key: NAN}})
    with pytest.raises(m.Refused):
        m.select({.75: _row(NAN), .25: _row(1.6)})            # NaN baseline
    with pytest.raises(m.Refused):
        m.select({.75: _row(3.8), .25: _row(math.inf)})


def _stats(e_conf, e_43, bright, n=8, cells=23):
    return {"e_conf_db": e_conf, "e_43_db": e_43, "brightness_db": bright,
            "n_included": n, "cells_within_tol": cells, "max_abs_db": 1.}


def test_confirm_refuses_nan_candidate():
    """Defeating input: NaN E_conf, E_43 and brightness returned CONFIRMED."""
    base = _stats(3., 3., -9., cells=8)
    assert m.confirm(base, _stats(.5, .3, -9., cells=23), -9.)[0] == "CONFIRMED"
    with pytest.raises(m.Refused):
        m.confirm(base, _stats(NAN, .3, NAN, cells=23), -9.)
    for bad in (_stats(NAN, .3, -9.), _stats(.5, NAN, -9.), _stats(.5, .3, NAN)):
        with pytest.raises(m.Refused):
            m.confirm(base, bad, -9.)
        with pytest.raises(m.Refused):
            m.confirm(bad, _stats(.5, .3, -9.), -9.)
    with pytest.raises(m.Refused):
        m.confirm(base, _stats(.5, .3, -9.), NAN)


def test_preservation_refuses_non_finite_graded_error():
    prop = lambda e: {"valid": True, "value": e, "reference": 0., "error": e, "tolerance": 3.}
    ok = {"Gain": prop(-1.)}
    with pytest.raises(m.Refused):
        m.preservation_violations(ok, {"Gain": prop(NAN)})
    with pytest.raises(m.Refused):
        m.preservation_violations({"Gain": prop(NAN)}, ok)


def _record(**over):
    p = {"Gain": {"valid": True, "value": -26.02076}, "Envelope attack": {"valid": False, "value": None}}
    return {**p, **over}


def test_baseline_record_check_refuses():
    m.check_baseline_record(_record(), _record())                              # reproduces
    with pytest.raises(m.Refused):                                             # NaN recorded value
        m.check_baseline_record(_record(), _record(Gain={"valid": True, "value": NAN}))
    with pytest.raises(m.Refused):                                             # NaN measured value
        m.check_baseline_record(_record(Gain={"valid": True, "value": NAN}), _record())
    with pytest.raises(m.Refused):                                             # perturbed baseline
        m.check_baseline_record(_record(Gain={"valid": True, "value": -26.0}), _record())
    with pytest.raises(m.Refused):                                             # validity flipped
        m.check_baseline_record(_record(**{"Envelope attack": {"valid": True, "value": 1.}}), _record())
    with pytest.raises(m.Refused):                                             # non-bool flag
        m.check_baseline_record(_record(), _record(Gain={"valid": "yes", "value": -26.02076}))
    with pytest.raises(m.Refused):                                             # property missing
        m.check_baseline_record({"Gain": _record()["Gain"]}, _record())


def test_matched_render_refuses_psi_miss(monkeypatch):
    """The 5 degree psi-match precondition fires on a 6 degree miss and not on 4."""
    import numpy as np
    monkeypatch.setattr(m, "matched_patch", lambda drive, vol: {"mix": (0, 1.)})
    monkeypatch.setattr(m.bass, "load_reference", lambda: (None, np.zeros(4)))
    monkeypatch.setattr(m.d, "EVENTS", [0])
    monkeypatch.setattr(m.d, "phase_for", lambda patch, e, psi: 0)
    monkeypatch.setattr(m.d, "render", lambda *a, **k: np.zeros(4, dtype=np.int16))
    monkeypatch.setattr(m.d, "wrap", lambda x: x)
    monkeypatch.setattr(m.d, "pcm_sha", lambda x: "sha")
    monkeypatch.setattr(m.d, "partials", lambda *a, **k: {})
    for miss, refused in ((6., True), (NAN, True), (4., False)):
        monkeypatch.setattr(m.d, "relative_phase", lambda a, b, e, miss=miss: {"psi_deg": 10. + miss})
        if refused:
            with pytest.raises(m.Refused):
                m.matched_render(.25, 1., [{"psi_deg": 10.}])
        else:
            assert m.matched_render(.25, 1., [{"psi_deg": 10.}])[0]["psi_error_deg"] == pytest.approx(4.)


def test_main_exit_2_on_refusal_and_report_untouched(monkeypatch, tmp_path, capsys):
    """The documented exit-2 contract, through main(): a failed control REFUSES
    before any render and writes no report."""
    bad = {"x": {"expected": 1., "got": 0., "ok": False}}
    monkeypatch.setattr(m, "synthetic_control", lambda: bad)
    monkeypatch.setattr(m, "OUT", tmp_path / "out")
    monkeypatch.setattr(m.sys, "argv", ["phase_aware_m1a.py"])
    assert m.main() == 2
    assert "REFUSED" in capsys.readouterr().out
    assert not (tmp_path / "out").exists()


def test_other_nan_guards_refuse():
    """The same class elsewhere: NaN psi error, NaN confirmation cell, NaN conditioning."""
    with pytest.raises(m.Refused):
        m.confirmation_stats([{h: NAN for h in m.PARTIALS}] * 3, [{h: True for h in m.PARTIALS}] * 3)
    with pytest.raises(m.Refused):
        m.included("h4", {"h4": NAN})
    assert m.included("h4", {"h4": -math.inf}) is False       # exact cancellation is a real exclusion


# ---- a record main() reads with [] and cannot read is REFUSED (exit 2), not a
# KeyError traceback (exit 1) (#1604, #1622). One control per key, through the
# tool's own main(), on doctored copies of the committed records.
def _main(monkeypatch, tmp_path, capsys, **records):
    """main() with each named module path (RECORD, PHASE_REPORT, ...) pointing
    at a scratch copy of its committed record, doctored by the caller."""
    monkeypatch.setattr(m, "OUT", tmp_path / "out")
    monkeypatch.setattr(m.sys, "argv", ["phase_aware_m1a.py"])
    for name, record in records.items():
        path = tmp_path / f"{name}.json"
        path.write_text(json.dumps(record))
        monkeypatch.setattr(m, name, path)
    rc = m.main()
    return rc, capsys.readouterr().out


PHASE_KEYS = ("capture_sha256", "summaries", "reference")


@pytest.mark.parametrize("key", PHASE_KEYS)
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


@pytest.mark.parametrize("phase, why", [({}, "'capture_sha256'"), ([], "not a JSON object"),
                                        (None, "not a JSON object")])
def test_a_phase_cycle_report_that_is_not_an_object_is_refused(monkeypatch, tmp_path, capsys, phase, why):
    rc, printed = _main(monkeypatch, tmp_path, capsys, PHASE_REPORT=phase)
    assert rc == 2 and "REFUSED" in printed and why in printed


def test_a_phase_cycle_report_with_a_null_key_is_refused(monkeypatch, tmp_path, capsys):
    phase = json.loads(m.PHASE_REPORT.read_text())
    phase["summaries"] = None
    rc, printed = _main(monkeypatch, tmp_path, capsys, PHASE_REPORT=phase)
    assert rc == 2 and "REFUSED" in printed and "'summaries'" in printed and "null" in printed


def test_a_diagnosis_report_without_renders_is_refused(monkeypatch, tmp_path, capsys):
    """The read sits behind selection, about a minute of rendering upstream.
    That stretch is replaced, as test_matched_render_refuses_psi_miss replaces
    its renders, so the control reaches the read in under a second."""
    import numpy as np
    phase = json.loads(m.PHASE_REPORT.read_text())
    model = phase["summaries"]["model"]["partials"]
    record = json.loads(m.RECORD.read_text())
    properties = {name: dict(prop) for name, prop in record["diagnostics"]["properties"].items()}
    row = {"max_error_db": 1., "worst_partial": "h3", "mean_error_db": 1., "odd_range_mean_db": 1.,
           "_mp": {"partials": {h: {"range_db": model[h]["range_db"]} for h in m.ap.ODD}},
           "_measured": {"properties": properties}}
    monkeypatch.setattr(m.dx, "render_phrase", lambda patch, n: np.zeros(4, dtype=np.int16))
    monkeypatch.setattr(m.dx, "pcm_sha", lambda pcm: "sha")
    monkeypatch.setattr(m.bass, "compare_audio", lambda pcm, reference: {
        "properties": properties, "events": [{"rms_dbfs": {"model": 0.}}]})
    monkeypatch.setattr(m, "development_row", lambda *a, **k: copy.deepcopy(row))
    monkeypatch.setattr(m, "select", lambda rows: (m.CANDIDATES[0], {}))
    monkeypatch.setattr(m, "reference_confirmation", lambda: (None, None, []))
    monkeypatch.setattr(m, "matched_render", lambda drive, vol, ref_rows: [{"pcm_sha256": "sha"}])
    diag = json.loads(m.DIAG_REPORT.read_text())
    del diag["renders"]
    rc, printed = _main(monkeypatch, tmp_path, capsys, DIAG_REPORT=diag)
    assert rc == 2 and "REFUSED" in printed and "'renders'" in printed
    assert not (tmp_path / "out").exists()
