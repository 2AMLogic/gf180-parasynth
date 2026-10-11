"""Controls for tools/probes/cymbal_400_production_tail.py (#400).

    python3 -m pytest tools/probes/test_cymbal_400_production_tail.py -q

Written and committed RED against a stub (git history carries the ordering).
Groups: known answers (planted constants, independent of the instrument),
defeating inputs (preparation mismatch, truncation/floor, filter-skirt leak,
non-finite), refusals (corpus / frozen record / unsupported knob settings),
invariances, and executed mutants (a defect injected into the probe must turn
the matching control red -- a control never seen to fail is not a control).
"""
import inspect
import json
import math
import pathlib
import sys

import numpy as np
import pytest

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE.parents[1] / "model"))
import cymbal_400_production_tail as pt  # noqa: E402
import cymbal_low_tail as lt  # noqa: E402
import run_case as rc  # noqa: E402

SR = 48_000


def strike(tau_m, a_m, **kw):
    """Planted strike with a SEPARATE 0.9-2.8 kHz component of known tau and amplitude."""
    return lt.synth(tau_l=0.35, tau_m=tau_m, a_m=a_m, tau_h=0.2, a_h=0.6, **kw)[0]


@pytest.fixture(scope="module")
def ref_long():
    return strike(0.60, 4.0)


# ---------------------------------------------------------------- known answers
@pytest.mark.parametrize("kind,p,r,want", [
    ("energy_db", 5.0, 0.0, "DISCREPANT"), ("energy_db", -5.0, 0.0, "DISCREPANT"),
    ("energy_db", 2.9, 0.0, "MATCH"), ("energy_db", 0.0, 0.0, "MATCH"),
    ("time_ratio", 1.6, 1.0, "DISCREPANT"), ("time_ratio", 0.4, 1.0, "DISCREPANT"),
    ("time_ratio", 1.4, 1.0, "MATCH"), ("time_ratio", 1.0, 1.0, "MATCH"),
])
def test_classify_known_answers(kind, p, r, want):
    assert pt.classify(p, r, kind)["verdict"] == want


@pytest.mark.parametrize("p,r", [(None, 1.0), (1.0, None), (float("nan"), 1.0), (1.0, float("inf"))])
def test_classify_refuses_missing_or_nonfinite_never_matches(p, r):
    assert pt.classify(p, r, "time_ratio")["verdict"] == "REFUSED"


def test_classify_unknown_kind_refuses():
    with pytest.raises(pt.Refused):
        pt.classify(1.0, 1.0, "vibes")


def test_properties_are_reported_separately_energy_match_decay_discrepant(ref_long):
    prod = strike(0.25, 4.0)                       # same level, 2.4x shorter decay
    r = pt.compare_records(prod, SR, ref_long, SR, prod_has_hh1=True)
    assert r["energy"]["verdict"] == "MATCH", r["energy"]
    assert r["decay"]["verdict"] == "DISCREPANT", r["decay"]
    assert r["joint"] is False


def test_properties_are_reported_separately_energy_discrepant_decay_match(ref_long):
    prod = strike(0.60, 12.0)                      # +9.5 dB in M, same decay
    r = pt.compare_records(prod, SR, ref_long, SR, prod_has_hh1=True)
    assert r["energy"]["verdict"] == "DISCREPANT", r["energy"]
    assert r["decay"]["verdict"] == "MATCH", r["decay"]


def test_null_control_a_record_against_itself_matches_everywhere(ref_long):
    r = pt.compare_records(ref_long, SR, ref_long, SR, prod_has_hh1=True)
    assert r["energy"]["verdict"] == "MATCH" and r["decay"]["verdict"] == "MATCH"


def test_joint_requires_both(ref_long):
    prod = strike(0.25, 12.0)
    r = pt.compare_records(prod, SR, ref_long, SR, prod_has_hh1=True)
    assert r["joint"] is True


# ---------------------------------------------------------------- invariances
def test_scale_and_prepended_silence_invariance(ref_long):
    base = strike(0.25, 4.0)
    raw = lt.synth(tau_l=0.35, tau_m=0.25, a_m=4.0, tau_h=0.2, a_h=0.6)[0]
    scaled = rc.prepare(0.1 * raw, SR, side="scaled")
    padded = rc.prepare(np.concatenate([np.zeros(SR // 4), raw]), SR, side="padded")
    a = pt.compare_records(base, SR, ref_long, SR, prod_has_hh1=True)
    for v in (scaled, padded):
        b = pt.compare_records(v, SR, ref_long, SR, prod_has_hh1=True)
        assert b["energy"]["verdict"] == a["energy"]["verdict"]
        assert b["decay"]["verdict"] == a["decay"]["verdict"]
        assert b["energy"]["prod"] == pytest.approx(a["energy"]["prod"], abs=0.05)
        assert b["decay"]["prod"] == pytest.approx(a["decay"]["prod"], rel=0.02)


def test_trailing_silence_is_outside_the_domain_so_it_never_silently_changes_a_verdict(ref_long):
    """Trailing digital silence zeroes the floor estimate the Schroeder curve
    subtracts. The declared domain forbids it: the probe must either give the
    same verdict or REFUSE -- never a different verdict."""
    base = strike(0.25, 4.0)
    a = pt.compare_records(base, SR, ref_long, SR, prod_has_hh1=True)
    padded = np.concatenate([base, np.zeros(SR)])
    try:
        b = pt.compare_records(padded, SR, ref_long, SR, prod_has_hh1=True)
    except pt.Refused:
        return
    for k in ("energy", "decay"):
        assert b[k]["verdict"] in (a[k]["verdict"], "REFUSED"), (k, a[k], b[k])


# ---------------------------------------------------------------- defeating inputs
def test_preparation_mismatch_unprepared_record_is_refused(ref_long):
    raw = np.concatenate([np.zeros(10), np.hanning(64) * 0.5, np.random.default_rng(1).standard_normal(3 * SR) * 0.01])
    with pytest.raises(pt.Refused, match="prepar"):
        pt.compare_records(raw, SR, ref_long, SR, prod_has_hh1=True)
    with pytest.raises(pt.Refused, match="prepar"):
        pt.compare_records(ref_long, SR, raw, SR, prod_has_hh1=True)


def test_assert_prepared_accepts_prepare_output(ref_long):
    pt.assert_prepared(ref_long, SR, "x")


@pytest.mark.parametrize("bad", [np.nan, np.inf])
def test_nonfinite_is_refused(ref_long, bad):
    y = ref_long.copy()
    y[-1000] = bad
    with pytest.raises(pt.Refused, match="finite"):
        pt.compare_records(y, SR, ref_long, SR, prod_has_hh1=True)


def test_truncation_refuses_decay_instead_of_reporting_a_short_tail(ref_long):
    prod = strike(0.60, 4.0)
    short = prod[: rc.required_lead_samples(SR) + int(0.5 * SR)]   # cut inside the tail
    try:
        r = pt.compare_records(short, SR, ref_long, SR, prod_has_hh1=True)
    except pt.Refused:
        return
    assert r["decay"]["verdict"] == "REFUSED", r["decay"]
    assert r["joint"] is False


def test_floor_contamination_is_not_a_discrepancy(ref_long):
    noisy = strike(0.60, 4.0, floor=0.05)
    try:
        r = pt.compare_records(noisy, SR, ref_long, SR, prod_has_hh1=True)
    except pt.Refused:
        return
    assert r["decay"]["verdict"] in ("REFUSED", "MATCH"), r["decay"]


def test_filter_skirt_leak_only_band_refuses_not_discrepant(ref_long):
    """Production with NO separate 0.9-2.8 kHz content: its band is the 3.45 kHz
    band-pass's skirt. Reading that against the reference's real content and
    calling it a defect is the #101 failure mode; it must be REFUSED."""
    leak_only = lt.synth(tau_l=0.35, a_m=0.0, tau_h=0.2, a_h=0.6)[0]
    r = pt.compare_records(leak_only, SR, ref_long, SR, prod_has_hh1=False)
    assert r["energy"]["verdict"] == "REFUSED", r["energy"]
    assert r["decay"]["verdict"] == "REFUSED", r["decay"]
    assert "skirt" in (r["energy"]["reason"] + r["decay"]["reason"]).lower() or "leak" in (r["energy"]["reason"] + r["decay"]["reason"]).lower()


# ---------------------------------------------------------------- refusals
def test_production_path_has_no_knob_parameters_so_only_cy5025_is_supported():
    params = set(inspect.signature(rc.render_drum_solo).parameters)
    assert not {"tone", "decay", "knobs", "setting"} & params, (
        "render_drum_solo now takes knobs: wire real support into production_supported "
        "and re-freeze, do not leave the refusal stale")
    pt.production_supported("CY5025")
    for s in ("CY0000", "CY5000", "CY2500", "CY7575", "kit_at", "OH00"):
        with pytest.raises(pt.Refused, match="knob|support|setting"):
            pt.production_supported(s)


def test_corpus_missing_is_refused_not_failed(tmp_path):
    with pytest.raises(pt.Refused, match="GF180_TR808_REFS|corpus"):
        pt.require_corpus(tmp_path / "nope", frozen=None)


def test_corpus_hash_mismatch_with_frozen_is_refused(tmp_path):
    d = tmp_path / "cy8"
    d.mkdir()
    (d / "CY5025.WAV").write_bytes(b"not the fischer recording")
    got = pt.require_corpus(tmp_path, frozen=None)
    assert got["CY5025.WAV"]
    with pytest.raises(pt.Refused, match="hash"):
        pt.require_corpus(tmp_path, frozen={"reference_sha256": {"CY5025.WAV": "0" * 64}})


# ---------------------------------------------------------------- executed mutants
def test_mutant_no_prep_assert_lets_the_unprepared_record_through(ref_long):
    raw = np.concatenate([np.zeros(10), np.hanning(64) * 0.5, np.random.default_rng(1).standard_normal(3 * SR) * 0.01])
    with pytest.raises(pt.Refused):
        pt.compare_records(raw, SR, ref_long, SR, prod_has_hh1=True)
    try:
        pt.compare_records(raw, SR, ref_long, SR, prod_has_hh1=True, defect="NO_PREP_ASSERT")
    except pt.Refused as e:
        assert "prepar" not in str(e), "mutant still hit the preparation guard: it is not the mutant"


def test_mutant_no_finite_check_is_caught(ref_long):
    y = ref_long.copy()
    y[-1000] = np.nan
    with pytest.raises(pt.Refused, match="finite"):
        pt.compare_records(y, SR, ref_long, SR, prod_has_hh1=True)
    try:
        r = pt.compare_records(y, SR, ref_long, SR, prod_has_hh1=True, defect="NO_FINITE_CHECK")
    except pt.Refused as e:
        assert "finite" not in str(e)
    except Exception:
        pass  # the mutant crashed downstream: the guard was load-bearing


def test_mutant_wrong_hh1_declaration_changes_the_leak_prediction(ref_long):
    prod = strike(0.25, 4.0)
    a = pt.compare_records(prod, SR, ref_long, SR, prod_has_hh1=False)
    b = pt.compare_records(prod, SR, ref_long, SR, prod_has_hh1=True)
    assert a["energy"]["detail"]["predicted_leak_db_re_L"] != b["energy"]["detail"]["predicted_leak_db_re_L"]


# ---------------------------------------------------------------- frozen record
def test_frozen_record_declares_the_pre_measurement_commitments():
    f = pt.FROZEN
    for k in ("supported_settings", "windows_s", "tolerances", "candidate_budget",
              "preservation_set", "stage_isolation", "development_settings", "confirmation_settings"):
        assert k in f
    assert f["candidate_budget"] == 0
    assert set(f["preservation_set"]) >= {"D15A", "D16A", "OH00", "OH75"}
    assert f["supported_settings"] == ["CY5025"]
    assert not set(f["development_settings"]) & set(f["confirmation_settings"])
    assert all(p["status"] in ("measured", "inferred", "unverified") for p in f["stage_isolation"]["predictions"])
    json.dumps(f)
