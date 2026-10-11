"""Known answers, refusals and injected controls for tools/probes/tom_distortion.py (#593).

Everything here is closed-form or synthetic and runs anywhere. Tests that need
the Fischer corpus read GF180_TR808_FISCHER and are SKIPPED with that reason
when it is absent -- a skip, never a pass.
"""
from __future__ import annotations

import json
import math
import os
import pathlib
import sys

import numpy as np
import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import tom_distortion as td  # noqa: E402

REFS = os.environ.get("GF180_TR808_FISCHER")
needs_refs = pytest.mark.skipif(not REFS, reason="GF180_TR808_FISCHER (the Fischer corpus) not set")


@pytest.fixture(autouse=True)
def _clean_inject():
    td.INJECT.clear()
    yield
    td.INJECT.clear()


# ---- start red -------------------------------------------------------------
def test_start_red_stub_estimator_fails_the_harness():
    """Rule 1: the right ports and no behaviour must FAIL the known answers, for
    every reason (missed harmonics, no refusals)."""
    r = td.harness_verdict(td.stub_levels, td.ka_grid()[:40])
    assert not r["pass"]
    assert r["missed_resolved"] > 0
    assert len(r["refusal_cases_bad"]) == len(td.refusal_cases(td.stub_levels)[1])


# ---- the estimator against closed-form truth -------------------------------
def test_estimator_recovers_independent_harmonics():
    r = td.harness_verdict(td.estimate, td.ka_grid()[::4])
    assert r["pass"], r
    assert r["max_abs_err_db"] <= td.TOL_DB


def test_refuses_what_it_cannot_answer():
    ok, bad = td.refusal_cases(td.estimate)
    assert not bad, bad
    assert {"nan", "inf", "silent", "short_window", "f0_too_high_for_h4",
            "glide_outside_domain", "unsupported_rate", "empty"} <= set(ok)


def test_unresolved_fundamental_is_refused():
    rng = np.random.default_rng(1)
    with pytest.raises(td.Refused):
        td.estimate(rng.standard_normal(24000) * 0.1, 48000)


def test_dc_offset_does_not_make_harmonics():
    y = td.synth_mixture(110.0, 0.15, ())
    for dc in (0.5, 5.0):
        assert not any(td.estimate(y + dc, 48000)["resolved"])


def test_adjacent_inharmonic_partial_cannot_pose_as_a_harmonic():
    """A partial at 1.5 f0 or 2.5 f0 (30 dB down) sits beside H2/H3: it must not
    be reported as a resolved H2/H3 (the off-harmonic floor rises to meet it)."""
    n = np.arange(24000) / 48000
    y = td.synth_mixture(110.0, 0.15, ())
    for ratio in (1.5, 2.5):
        z = y + 10 ** (-30 / 20) * np.exp(-n / 0.15) * np.sin(2 * np.pi * 110.0 * ratio * n)
        assert not any(td.estimate(z, 48000)["resolved"])


def test_window_leakage_does_not_masquerade_as_h2():
    """Fast decay + glide + noise on a PURE sinusoid: the apparent H2 is leakage."""
    for tau in (0.06, 0.15):
        for g in (0.0, 60.0):
            y = td.synth_mixture(110.0, tau, (), glide_cents=g, glide_tau=0.1, noise_db=-50.0)
            assert not any(td.estimate(y, 48000)["resolved"])


def test_rectangular_window_leaks_more_than_the_shipped_one():
    """Why the window is Blackman-Harris: the same pure decaying sinusoid reads a
    HIGHER apparent H2 floor through a rectangular window."""
    y = td.synth_mixture(110.0, 0.06, (-45.0, -50.0, -55.0))
    bh = td.harmonic_levels(y, 48000)["floor_db"][0]
    rect = td.harmonic_levels(y, 48000, _rect=True)["floor_db"][0]
    assert rect > bh + 10.0


def test_glide_outside_domain_is_refused_not_misread():
    y = td.synth_mixture(110.0, 0.15, (-35.0, -38.0, -41.0), glide_cents=480.0, glide_tau=0.1)
    with pytest.raises(td.Refused, match="cents"):
        td.estimate(y, 48000)


def test_analytic_snr_matches_a_noise_only_measurement():
    """analytic_snr_db is closed form; check it against a direct measurement."""
    c = dict(f0=110.0, tau=0.15, h_db=(-40.0,), sr=48000, glide_cents=0.0, glide_tau=0.03, noise_db=-50.0)
    snr = td.analytic_snr_db(c, 0)
    assert 5.0 < snr < 40.0
    c2 = dict(c, noise_db=-60.0)
    assert td.analytic_snr_db(c2, 0) == pytest.approx(snr + 10.0, abs=1e-9)


# ---- the candidate path (apply_quadratic) against a closed form ------------
def test_quadratic_term_closed_form_on_a_steady_tone():
    """y + c y^2 with y = A sin(wt): H2/H1 = c*A/2 exactly. Read from the raw DFT
    of an integer number of cycles, with no estimator involved."""
    sr, f0, n = 48000, 100.0, 48000
    y = 0.4 * np.sin(2 * np.pi * f0 * np.arange(n) / sr)
    z = td.apply_quadratic(y, -37.0)
    S = np.abs(np.fft.rfft(z))
    assert 20 * math.log10(S[200] / S[100]) == pytest.approx(-37.0, abs=0.01)


def test_quadratic_term_reads_as_a_polynomial_of_the_carrier():
    r = td.harmonic_trajectory(td.apply_quadratic(td.synth_mixture(110.0, 0.1, ()), -37.0), 48000)
    assert r["class"] == "polynomial-of-carrier"


# ---- mechanism -------------------------------------------------------------
def test_mechanism_classes_on_known_answers():
    assert td.prop_M1_mechanism_classes()


def test_mechanism_indistinguishable_when_prediction_is_small():
    y = td.synth_mixture(110.0, 0.9, (-35.0, -40.0, -45.0), seconds=0.6)
    assert td.harmonic_trajectory(y, 48000)["class"] == "insufficient"


def test_mechanism_verdict_separates_effect_from_mechanism():
    poly = [{"class": "polynomial-of-carrier"}] * 5
    assert td.mechanism_verdict(poly, True)["verdict"] == "SUPPORTED"
    assert td.mechanism_verdict(poly, None)["verdict"] == "INSUFFICIENT"
    assert td.mechanism_verdict(poly, False)["verdict"] == "REFUTED"
    assert td.mechanism_verdict([{"class": "constant-ratio"}] * 5, True)["verdict"] == "REFUTED"
    assert td.mechanism_verdict(poly[:2], True)["verdict"] == "INSUFFICIENT"
    assert td.mechanism_verdict([{"class": "insufficient"}] * 8, True)["verdict"] == "INSUFFICIENT"


# ---- the preregistration ---------------------------------------------------
def test_prereg_loads_and_does_not_relabel_old_rows():
    p = td.load_prereg()
    assert p["issue"] == 593
    assert len(p["prior_seen"]["keys"]) == 30
    assert set(p["development"]["keys"]) <= set(p["prior_seen"]["keys"])
    assert p["confirmation"]["status"] == "REFUSED" and p["confirmation"]["keys"] == []
    assert p["candidates"] == {}
    assert not set(p["confirmation"]["keys"]) & set(p["prior_seen"]["keys"])


def test_prereg_of_558_is_refused(tmp_path):
    old = ROOT_558 = pathlib.Path(__file__).resolve().parents[2] / "docs/scorecard/tom-conga-558/prereg.json"
    with pytest.raises(td.Refused):
        td.load_prereg(old)
    with pytest.raises(td.Refused):
        td.load_prereg(tmp_path / "missing.json")
    assert ROOT_558.exists()


def test_actual_prereg_with_no_candidate_gives_no_candidate_never_confirmed():
    p = td.load_prereg()
    v = td.judge(td.synthetic_record(prereg=p), p)          # population from the prereg itself
    assert v["verdict"] == "NO CANDIDATE"


def test_actual_prereg_refuses_rows_outside_its_frozen_population():
    p = td.load_prereg()                                    # registration: confirmation REFUSED
    rec = td.synthetic_record(prereg=p)
    rec["rows"]["EXT00"] = dict(rec["rows"]["LT25"], split="confirmation")
    assert td.judge(rec, p)["verdict"] == "REFUSED"


def test_confirmation_rows_with_prior_seen_hashes_are_refused():
    p = td.synthetic_prereg()
    rec = td.synthetic_record(prereg=p)
    seen = sorted(p["prior_seen"]["sha256_16"].values())
    for i, k in enumerate(p["confirmation"]["keys"]):
        rec["rows"][k]["sha16"] = seen[i]
    assert td.judge(rec, p)["verdict"] == "REFUSED"


def test_confirmation_registration_using_a_prior_seen_hash_is_refused():
    p = td.synthetic_prereg()
    k0 = p["confirmation"]["keys"][0]
    p["confirmation"]["sha256_16"][k0] = sorted(p["prior_seen"]["sha256_16"].values())[0]
    v = td.judge(td.synthetic_record(prereg=p), p)          # matches the cheating registration
    assert v["verdict"] == "REFUSED"


# ---- the frozen judge ------------------------------------------------------
def _j(**kw):
    n = kw.pop("n_conf", 6)
    return td.judge(td.synthetic_record(n_conf=n, **kw), td.synthetic_prereg(n_conf=n))


def test_clean_candidate_is_confirmed_so_the_judge_is_satisfiable():
    v = _j()
    assert v["verdict"] == "CONFIRMED", v
    assert v["selection"]["Q"]["null_excluded"]["modulation"] > 0     # HT: null, excluded, counted


def test_attractive_target_gain_that_breaks_decay_is_rejected():
    v = _j(decay_bad=True)
    assert v["verdict"] == "NOT CONFIRMED"
    assert any("decay" in x for vs in v["selection"]["Q"]["violations"].values() for x in vs)


def test_attractive_target_gain_that_breaks_peak_is_rejected():
    v = _j(peak_bad=True)
    assert v["verdict"] == "NOT CONFIRMED"


def test_dc_added_by_a_quadratic_term_is_a_violation():
    rec = td.synthetic_record()
    for r in rec["rows"].values():
        r["variants"]["Q"]["dc_fs"] = 0.01
    assert td.judge(rec, td.synthetic_prereg())["verdict"] == "NOT CONFIRMED"


def test_null_preserved_feature_on_one_side_is_a_violation_not_a_skip():
    rec = td.synthetic_record()
    rec["rows"]["LT25"]["variants"]["Q"]["ratio"]["attack"] = None
    assert "LT25" in td.judge(rec, td.synthetic_prereg())["selection"]["Q"]["violations"]


# ---- non-finite / missing measurements (each must refuse, never score) ------
def test_nonfinite_measurements_are_refused_not_scored():
    nan, inf = float("nan"), float("inf")
    mangles = {
        "nan ratio, candidate side": lambda r:
            r["rows"]["MT25"]["variants"]["Q"]["ratio"].update(attack=nan),
        "nan ratio, shipped side": lambda r:
            r["rows"]["MT25"]["variants"]["shipped"]["ratio"].update(decay=nan),
        "inf peak, shipped side": lambda r:
            r["rows"]["MT25"]["variants"]["shipped"].update(peak_fs=inf),
        "nan peak, candidate side": lambda r:
            r["rows"]["MT25"]["variants"]["Q"].update(peak_fs=nan),
        "zero peak, candidate side": lambda r:
            r["rows"]["MT25"]["variants"]["Q"].update(peak_fs=0.0),
        "nan dc, shipped side": lambda r:
            r["rows"]["MT25"]["variants"]["shipped"].update(dc_fs=nan),
        "inf dc, candidate side": lambda r:
            r["rows"]["MT25"]["variants"]["Q"].update(dc_fs=-inf),
        "nan in a resolved harmonic": lambda r:
            r["rows"]["MT25"]["variants"]["shipped"]["harm"]["db"].__setitem__(0, nan),
        "nan in the target": lambda r:
            r["rows"]["MT25"]["target_harm"]["db"].__setitem__(1, nan),
    }
    for name, mangle in mangles.items():
        rec = td.synthetic_record()
        mangle(rec)
        with pytest.raises(td.Refused, match="not finite|positive finite"):
            td.judge(rec, td.synthetic_prereg())


def test_deleted_feature_field_is_refused_not_treated_as_null():
    rec = td.synthetic_record()
    for vn in ("shipped", "Q"):
        rec["rows"]["MT25"]["variants"][vn]["ratio"].pop("pitch")
    with pytest.raises(td.Refused, match="not a measured null"):
        td.judge(rec, td.synthetic_prereg())


def test_null_on_both_sides_of_a_preserved_feature_is_a_violation_not_a_skip():
    rec = td.synthetic_record()
    for vn in ("shipped", "Q"):
        rec["rows"]["MT25"]["variants"][vn]["ratio"]["attack"] = None
    v = td.judge(rec, td.synthetic_prereg())
    assert v["verdict"] == "NOT CONFIRMED"
    assert any("null-on-both-sides" in x
               for vs in v["selection"]["Q"]["violations"].values() for x in vs)


def test_null_target_is_refused_not_scored_as_zero_error():
    assert _j(null_target=True)["verdict"] == "REFUSED"


def test_unresolved_shipped_harmonic_earns_no_credit_from_its_floor():
    """Review defeating input: shipped unresolved at 5.9 dB above its floor
    (legal under the 6 dB rule); the true improvement is 0.355 < the 0.5 bar,
    the old floor-substitution reported 0.778 and CONFIRMED."""
    assert td.prop_J9_unresolved_shipped_floor_earns_no_credit()
    td.INJECT = {"floor-as-bound"}
    try:
        assert not td.prop_J9_unresolved_shipped_floor_earns_no_credit()
    finally:
        td.INJECT = set()


@pytest.mark.parametrize("mech", ["INSUFFICIENT", "REFUTED", "bogus", None])
def test_unsupported_mechanism_cannot_select_a_candidate(mech):
    v = _j(mechanism=mech)
    assert v["verdict"] == "REFUSED" and "mechanism" in v["why"]
    assert "selection" in v and not v["selection"]


def test_missing_mechanism_record_cannot_select_a_candidate():
    assert td.prop_J8_unsupported_mechanism_cannot_select()
    td.INJECT = {"mechanism-gate-bypass"}
    try:
        assert not td.prop_J8_unsupported_mechanism_cannot_select()
    finally:
        td.INJECT = set()


def test_empty_registration_stays_no_candidate_whatever_the_mechanism():
    p = td.synthetic_prereg()
    p["candidates"] = {}
    assert td.judge(td.synthetic_record(prereg=p, mechanism="INSUFFICIENT"), p)["verdict"] == "NO CANDIDATE"


def test_floor_above_the_target_makes_the_row_unmeasurable():
    rec = td.synthetic_record()
    for r in rec["rows"].values():
        h = r["variants"]["shipped"]["harm"]
        h.update(resolved=[False] * 3, db=[-20.0] * 3, floor_db=[-20.0] * 3)
    assert td.judge(rec, td.synthetic_prereg())["verdict"] == "REFUSED"


def test_unresolved_candidate_harmonic_gets_no_credit():
    rec = td.synthetic_record()
    for r in rec["rows"].values():
        r["variants"]["Q"]["harm"]["resolved"] = [False] * 3
    assert td.judge(rec, td.synthetic_prereg())["verdict"] == "NOT CONFIRMED"


def test_missing_variant_is_refused_and_a_frozen_split_cannot_be_relabeled():
    rec = td.synthetic_record()
    del rec["rows"]["LT25"]["variants"]["Q"]
    with pytest.raises(td.Refused):
        td.judge(rec, td.synthetic_prereg())
    rec = td.synthetic_record()
    rec["rows"]["LT25"]["split"] = "untouched"
    v = td.judge(rec, td.synthetic_prereg())
    assert v["verdict"] == "REFUSED" and "contradicts the frozen assignment" in v["why"]
    with pytest.raises(td.Refused):
        td.judge({"rows": {}}, td.synthetic_prereg())


def test_contaminated_confirmation_is_refused_with_its_defeating_input():
    v = _j(contaminated=True)
    assert v["verdict"] == "REFUSED"
    # one defeating input per leg of the guard
    rec = td.synthetic_record()
    rec["rows"]["EXT00"]["sha16"] = None                        # no corpus hash
    assert td.judge(rec, td.synthetic_prereg())["verdict"] == "REFUSED"
    rec = td.synthetic_record()
    rec["rows"]["LT25"]["sha16"] = "devfile"
    rec["rows"]["LT25c"] = dict(rec["rows"]["LT25"], split="confirmation", sha16="devfile")  # dev FILE relabelled
    assert td.judge(rec, td.synthetic_prereg())["verdict"] == "REFUSED"


def test_too_few_independent_confirmation_rows_is_refused_not_confirmed():
    v = _j(n_conf=2)
    assert v["verdict"] == "REFUSED" and "independent confirmation" in v["why"]
    v = _j(n_conf=0)
    assert v["verdict"] == "REFUSED"


# ---- the frozen population (defeating inputs, one per leg of the guard) -----
def test_frozen_population_defeating_inputs_are_rejected():
    p = td.synthetic_prereg()
    mangles = {
        "omitted preservation-only row": lambda r: r["rows"].pop("LC25"),
        "omitted development row": lambda r: r["rows"].pop("MT75"),
        "unregistered confirmation row": lambda r: r["rows"].update(
            EXT99=dict(r["rows"]["EXT00"])),
        "unregistered hash on a registered key": lambda r: r["rows"]["EXT00"].update(
            sha16="unregistered00"),
        "a split the submitter may not choose": lambda r: r["rows"]["EXT00"].update(
            split="development"),
    }
    for name, mangle in mangles.items():
        rec = td.synthetic_record(prereg=p)
        mangle(rec)
        v = td.judge(rec, p)
        assert v["verdict"] == "REFUSED", (name, v.get("why"))


def test_one_recording_under_two_confirmation_keys_is_refused():
    p = td.synthetic_prereg()
    k0, k1 = p["confirmation"]["keys"][:2]
    p["confirmation"]["sha256_16"][k1] = p["confirmation"]["sha256_16"][k0]
    v = td.judge(td.synthetic_record(prereg=p), p)
    assert v["verdict"] == "REFUSED" and "not independent" in v["why"]


def test_unusable_confirmation_registration_is_refused():
    p = td.synthetic_prereg()
    del p["confirmation"]["sha256_16"]["EXT03"]             # registration incomplete
    v = td.judge(td.synthetic_record(prereg=p), p)
    assert v["verdict"] == "REFUSED" and "unusable" in v["why"]


def test_confirmation_failure_is_not_confirmed_and_selection_is_not_reopened():
    rec = td.synthetic_record()
    for k, r in rec["rows"].items():
        if r["split"] == "confirmation":
            r["variants"]["Q"]["ratio"]["decay"] = 9.0
    v = td.judge(rec, td.synthetic_prereg())
    assert v["verdict"] == "NOT CONFIRMED" and v["chosen"] == "Q"


# ---- injected defects x properties -----------------------------------------
@pytest.fixture(scope="module")
def matrix():
    td.INJECT.clear()
    return td.controls_matrix()


def test_every_clean_property_passes_so_the_matrix_is_not_vacuous(matrix):
    assert matrix["status"] != "REFUSED", matrix["clean"]
    assert all(v == "PASS" for v in matrix["clean"].values())


def test_each_injected_defect_moves_its_intended_property(matrix):
    assert matrix["intended_not_moved"] == [], matrix["intended_not_moved"]
    for d, names in td.INTENDED.items():
        for n in names:
            assert matrix["cells"][d][n] == "MOVED", (d, n)


def test_no_control_errored(matrix):
    nv = [(d, n) for d, row in matrix["cells"].items() for n, v in row.items() if v == "NO VERDICT"]
    assert nv == []


def test_an_erroring_property_is_no_verdict_not_a_caught_mutant():
    def boom():
        raise RuntimeError("apparatus missing")
    assert td._outcome(boom).startswith("NO VERDICT")
    assert td._outcome(lambda: False) == "FAIL"


def test_matrix_refuses_when_the_clean_baseline_is_red(monkeypatch):
    monkeypatch.setitem(td.PROPERTIES, "J0_good_candidate_confirms", lambda: False)
    assert td.controls_matrix()["status"] == "REFUSED"


def test_inject_is_cleared_after_the_matrix(matrix):
    assert not td.INJECT


# ---- corpus-dependent (build box) ------------------------------------------
@needs_refs
def test_baseline_reproduces_render_drum_solo_bit_exactly():
    out = td.baseline(pathlib.Path(REFS), td.load_prereg())
    assert all(v["render_equals_render_drum_solo"] for v in out.values())


def test_measuring_commands_refuse_without_a_corpus(tmp_path, capsys):
    assert td.main(["baseline", "--refs", str(tmp_path / "nope"), "--out", str(tmp_path / "o.json")]) == 2
    assert "REFUSED" in capsys.readouterr().out
    assert not (tmp_path / "o.json").exists()


def test_cli_judge_on_a_record(tmp_path, capsys):
    p = tmp_path / "r.json"
    p.write_text(json.dumps(td.synthetic_record(prereg=td.load_prereg()), default=str))
    assert td.main(["judge", str(p), "--out", str(tmp_path / "j.json")]) == 0
    assert json.loads((tmp_path / "j.json").read_text())["verdict"] == "NO CANDIDATE"


# ---- CLI exit codes: a failure or a refusal is never exit 0 -----------------
def test_cli_knownanswer_with_a_failing_harness_exits_1(tmp_path, monkeypatch):
    monkeypatch.setattr(td, "estimate", td.stub_levels)     # the start-red estimator
    assert td.main(["knownanswer", "--out", str(tmp_path / "k.json")]) == 1
    assert json.loads((tmp_path / "k.json").read_text())["pass"] is False


def test_cli_missed_intended_control_exits_1(tmp_path, monkeypatch):
    monkeypatch.setattr(td, "PROPERTIES", {"J0_good_candidate_confirms": td.prop_J0_good_candidate_confirms})
    monkeypatch.setattr(td, "INTENDED", {"stub-estimator": ("J0_good_candidate_confirms",)})
    assert td.main(["controls", "--out", str(tmp_path / "c.json")]) == 1    # BLIND: not moved
    out = json.loads((tmp_path / "c.json").read_text())
    assert out["status"] == "FAIL" and out["intended_not_moved"]


def test_cli_red_controls_baseline_refuses_with_exit_2(tmp_path, monkeypatch):
    monkeypatch.setitem(td.PROPERTIES, "J0_good_candidate_confirms", lambda: False)
    assert td.main(["controls", "--out", str(tmp_path / "c.json")]) == 2
    assert json.loads((tmp_path / "c.json").read_text())["status"] == "REFUSED"


def test_cli_judge_exit_codes_follow_the_verdict(tmp_path, monkeypatch):
    prereg_path = tmp_path / "prereg.json"                  # the real load_prereg call path
    prereg_path.write_text(json.dumps(td.synthetic_prereg()))
    monkeypatch.setattr(td, "load_prereg", lambda *a, **k: json.loads(prereg_path.read_text()))
    p = tmp_path / "r.json"
    p.write_text(json.dumps(td.synthetic_record(null_target=True), default=str))
    assert td.main(["judge", str(p), "--out", str(tmp_path / "j.json")]) == 2
    assert json.loads((tmp_path / "j.json").read_text())["verdict"] == "REFUSED"
    p.write_text(json.dumps(td.synthetic_record(), default=str))
    assert td.main(["judge", str(p), "--out", str(tmp_path / "j.json")]) == 0
    assert json.loads((tmp_path / "j.json").read_text())["verdict"] == "CONFIRMED"
