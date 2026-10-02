#!/usr/bin/env python3
"""Ground truth for `tools/probes/perturbation_ladder.py` (#518).

    python3 -m pytest tools/probes/test_perturbation_ladder.py -q

Every derived expectation here is checked against a signal of known answer
(the resample dual-effect formula, gain's exact invariance), and the
five-question machinery itself carries INJECTED-BUG controls: a mocked sweep
rigged to drift under an "invariant" perturbation must turn Q4 red, and one
rigged to ignore a known `scales_inverse` relation must turn Q2 red. A check
that only ever sees passing input has never been shown to fail on anything.
"""
from __future__ import annotations

import math
import pathlib
import sys

import numpy as np
import pytest
from scipy.io import wavfile

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "model"))
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "tools" / "probes"))

import audio_measure as am                                           # noqa: E402
import perturbation_ladder as pl                                      # noqa: E402
import run_case as rc                                                 # noqa: E402

SR = 48000


def _damped(f, tau, amp, n, sr=SR, phase=0.0):
    t = np.arange(n) / sr
    return amp * np.exp(-t / tau) * np.sin(2 * math.pi * f * t + phase)


# ---------------------------------------------------------------------------
# 1. REFUSE, not silently skip, when the corpus is absent
# ---------------------------------------------------------------------------
def test_refuses_cleanly_when_corpus_absent(tmp_path):
    missing = tmp_path / "no-such-corpus"
    result = pl.run_ladder(missing)
    assert result["status"] == "REFUSED"
    assert str(missing) in result["why"]
    assert "estimators" not in result


def test_main_exits_nonzero_when_corpus_absent(tmp_path, capsys):
    missing = tmp_path / "no-such-corpus"
    rc_code = pl.main(["--refs", str(missing)])
    out = capsys.readouterr().out
    assert rc_code == 1
    assert "REFUSED" in out


# ---------------------------------------------------------------------------
# 2. perturb_gain -- exact invariance, to machine precision
# ---------------------------------------------------------------------------
def test_perturb_gain_is_exact_amplitude_scale():
    x = _damped(440.0, 0.05, 1.0, 4000)
    y = pl.perturb_gain(x, SR, 6.0)
    assert np.allclose(y, x * (10.0 ** (6.0 / 20.0)), atol=1e-12)


# ---------------------------------------------------------------------------
# 3. perturb_resample -- the dual-effect formula, against a KNOWN signal
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("ratio", [0.80, 0.90, 1.10, 1.25])
def test_perturb_resample_moves_frequency_and_decay_by_the_same_ratio(ratio):
    """#518's second acceptance criterion: resampling by `a` moves frequency
    to `a * f` AND tau to `tau / a`, not a pure pitch shift. Checked here
    against a signal whose f and tau are known by construction -- the same
    standard `model/test_audio_measure.py` holds every estimator to."""
    f0, tau0 = 220.0, 0.08
    n = int(0.40 * SR)
    x = _damped(f0, tau0, 1.0, n)
    y = pl.perturb_resample(x, SR, ratio)

    f_est = am.dominant_frequency(y, f0 * ratio * 0.9, f0 * ratio * 1.1, SR)
    assert f_est.ok, f_est.reason
    assert f_est.value == pytest.approx(f0 * ratio, rel=0.01)

    e = am.decay_tau(y, SR)
    assert e.ok, e.reason
    assert e.value == pytest.approx(tau0 / ratio, rel=0.03)


# ---------------------------------------------------------------------------
# 4. relation_for / expected_delta
# ---------------------------------------------------------------------------
def test_relation_for_decay_tau_resample_is_the_one_scaling_case():
    assert pl.relation_for("decay_tau", "resample_ratio") == "scales_inverse"


@pytest.mark.parametrize("estimator", ["band_pair_db", "tone_ratio_db",
                                       "inharmonic_fraction_db"])
def test_relation_for_other_estimators_resample_is_not_derivable(estimator):
    assert pl.relation_for(estimator, "resample_ratio") == "not_derivable"


def test_relation_for_defaults_to_invariant_for_every_other_pair():
    assert pl.relation_for("decay_tau", "gain_db") == "invariant"
    assert pl.relation_for("band_pair_db", "quantisation_bits") == "invariant"
    assert pl.relation_for("some_future_estimator", "some_future_perturbation") == "invariant"


def test_expected_delta_invariant_is_always_zero():
    assert pl.expected_delta("invariant", base_value=123.4, strength=99.0) == 0.0


def test_expected_delta_scales_inverse_matches_the_formula():
    base_tau = 0.030
    for a in (0.5, 0.9, 1.0, 1.1, 2.0):
        got = pl.expected_delta("scales_inverse", base_value=base_tau, strength=a)
        assert got == pytest.approx(base_tau * (1.0 / a - 1.0))


def test_expected_delta_not_derivable_is_none():
    assert pl.expected_delta("not_derivable", base_value=1.0, strength=2.0) is None


# ---------------------------------------------------------------------------
# 5. five_questions -- Q1
# ---------------------------------------------------------------------------
def _rows_scales_inverse(base_tau=0.030, strengths=(0.8, 0.9, 1.0, 1.1, 1.2),
                         measured_matches_expected=True, measured_override=None):
    rows = []
    for a in strengths:
        exp = base_tau * (1.0 / a - 1.0)
        meas = exp if measured_matches_expected else (measured_override or 0.0)
        rows.append(dict(strength=a, refused=False, why="",
                         measured_value=base_tau + meas,
                         measured_delta=meas, expected_delta=exp,
                         expected_known=True))
    return rows


def test_q1_not_applicable_when_no_sensitive_rung_exists():
    """An invariant sweep's own expected effect is always zero, so there is
    never a rung above resolution to check detection against."""
    rows = [dict(strength=s, refused=False, why="", measured_value=1.0,
                measured_delta=0.0, expected_delta=0.0, expected_known=True)
           for s in (-6.0, -3.0, 3.0, 6.0)]
    q = pl.five_questions("invariant", resolution=0.2, relative=False,
                          base_value=1.0, rows=rows)
    assert q["q1_detects_above_resolution"]["answer"] is None


def test_q1_true_when_a_known_scaling_effect_is_correctly_detected():
    rows = _rows_scales_inverse()
    q = pl.five_questions("scales_inverse", resolution=0.08, relative=True,
                          base_value=0.030, rows=rows)
    assert q["q1_detects_above_resolution"]["answer"] is True


def test_q1_control_false_when_a_detectable_effect_is_missed():
    """INJECTED BUG: the expected effect is well above resolution at every
    off-strength rung, but the (mocked) estimator reports a flat reading
    anyway -- as if it never noticed the apparatus changed at all."""
    rows = _rows_scales_inverse(measured_matches_expected=False, measured_override=0.0)
    q = pl.five_questions("scales_inverse", resolution=0.08, relative=True,
                          base_value=0.030, rows=rows)
    assert q["q1_detects_above_resolution"]["answer"] is False


# ---------------------------------------------------------------------------
# 6. five_questions -- Q2 / Q3
# ---------------------------------------------------------------------------
def test_q2_q3_not_applicable_for_an_invariant_perturbation():
    rows = [dict(strength=s, refused=False, why="", measured_value=1.0,
                measured_delta=0.01, expected_delta=0.0, expected_known=True)
           for s in (-6.0, -3.0, 3.0, 6.0)]
    q = pl.five_questions("invariant", resolution=0.2, relative=False,
                          base_value=1.0, rows=rows)
    assert q["q2_tracks_introduced_magnitude"]["answer"] is None
    assert q["q3_more_defect_more_error"]["answer"] is None


def test_q2_q3_not_applicable_when_expected_effect_is_not_derivable():
    rows = [dict(strength=s, refused=False, why="", measured_value=1.0,
                measured_delta=0.05, expected_delta=None, expected_known=False)
           for s in (0.9, 0.95, 1.05, 1.1)]
    q = pl.five_questions("not_derivable", resolution=0.2, relative=False,
                          base_value=1.0, rows=rows)
    assert q["q2_tracks_introduced_magnitude"]["answer"] is None
    assert q["q3_more_defect_more_error"]["answer"] is None
    assert "not derivable" in q["q2_tracks_introduced_magnitude"]["why"]


def test_q2_q3_true_when_measurement_matches_the_known_scaling_relation():
    rows = _rows_scales_inverse()
    q = pl.five_questions("scales_inverse", resolution=0.08, relative=True,
                          base_value=0.030, rows=rows)
    assert q["q2_tracks_introduced_magnitude"]["answer"] is True
    assert q["q2_tracks_introduced_magnitude"]["correlation"] > 0.99
    assert q["q3_more_defect_more_error"]["answer"] is True


def test_q2_control_false_when_tracking_is_broken():
    """INJECTED BUG: the measured delta is the SAME constant at every
    strength, regardless of how large the known expected effect grows --
    the shape of an estimator that stopped actually reading the signal."""
    rows = _rows_scales_inverse(measured_matches_expected=False, measured_override=0.01)
    q = pl.five_questions("scales_inverse", resolution=0.08, relative=True,
                          base_value=0.030, rows=rows)
    assert q["q2_tracks_introduced_magnitude"]["answer"] is False


# ---------------------------------------------------------------------------
# 7. five_questions -- Q4
# ---------------------------------------------------------------------------
def test_q4_true_when_truly_invariant():
    rows = [dict(strength=s, refused=False, why="", measured_value=1.0 + d,
                measured_delta=d, expected_delta=0.0, expected_known=True)
           for s, d in zip((-6.0, -3.0, 3.0, 6.0), (0.01, -0.01, 0.02, -0.02))]
    q = pl.five_questions("invariant", resolution=0.2, relative=False,
                          base_value=1.0, rows=rows)
    assert q["q4_false_unrelated_move"] == dict(answer=True, violating_strengths=[])


def test_q4_control_flags_an_injected_false_positive():
    """INJECTED BUG: the apparatus-only perturbation is supposed to leave the
    true answer alone (relation == "invariant"), but the (mocked) estimator's
    reading drifts by 5x its own declared resolution at the two largest
    strengths -- the exact shape `tools/measure_conga_body_spread.py`'s
    `lead_1ms_db` documents as a real `sosfiltfilt` edge artefact. Must turn
    red, not silently pass because most rungs look fine."""
    rows = [dict(strength=s, refused=False, why="", measured_value=1.0 + d,
                measured_delta=d, expected_delta=0.0, expected_known=True)
           for s, d in zip((-6.0, -3.0, 3.0, 6.0), (0.01, -0.01, 1.0, -1.2))]
    q = pl.five_questions("invariant", resolution=0.2, relative=False,
                          base_value=1.0, rows=rows)
    assert q["q4_false_unrelated_move"]["answer"] is False
    assert q["q4_false_unrelated_move"]["violating_strengths"] == [3.0, 6.0]


def test_q4_not_applicable_for_a_genuine_scaling_effect():
    rows = _rows_scales_inverse()
    q = pl.five_questions("scales_inverse", resolution=0.08, relative=True,
                          base_value=0.030, rows=rows)
    assert q["q4_false_unrelated_move"]["answer"] is None


def test_q4_relative_resolution_is_honoured_for_decay_tau_like_estimators():
    """`relative=True` estimators (decay_tau) declare resolution as a
    FRACTION of the base reading, not an absolute unit -- the same 0.03
    absolute delta is a false alarm on a 0.01 s base and invisible noise on
    a 10 s one."""
    rows_small_base = [dict(strength=0.0, refused=False, why="",
                            measured_value=0.04, measured_delta=0.03,
                            expected_delta=0.0, expected_known=True)]
    q_small = pl.five_questions("invariant", resolution=0.08, relative=True,
                                base_value=0.01, rows=rows_small_base)
    assert q_small["q4_false_unrelated_move"]["answer"] is False

    rows_large_base = [dict(strength=0.0, refused=False, why="",
                            measured_value=10.03, measured_delta=0.03,
                            expected_delta=0.0, expected_known=True)]
    q_large = pl.five_questions("invariant", resolution=0.08, relative=True,
                                base_value=10.0, rows=rows_large_base)
    assert q_large["q4_false_unrelated_move"]["answer"] is True


# ---------------------------------------------------------------------------
# 8. five_questions -- Q5 (descriptive, never asserts a verdict it cannot back)
# ---------------------------------------------------------------------------
def test_q5_reports_refusals_without_crashing():
    rows = [dict(strength=s, refused=(s in (0.05, 0.02)), why="", measured_value=None,
                measured_delta=None, expected_delta=0.0, expected_known=True)
           for s in (1.0, 0.5, 0.2, 0.1, 0.05, 0.02)]
    q = pl.five_questions("invariant", resolution=0.2, relative=False,
                          base_value=1.0, rows=rows)
    assert q["q5_abstains_when_insufficient"] == dict(
        any_refusal=True, refused_at_strengths=[0.05, 0.02])


def test_q5_reports_no_refusal_when_nothing_refused():
    rows = [dict(strength=s, refused=False, why="", measured_value=1.0,
                measured_delta=0.0, expected_delta=0.0, expected_known=True)
           for s in (1.0, 2.0)]
    q = pl.five_questions("invariant", resolution=0.2, relative=False,
                          base_value=1.0, rows=rows)
    assert q["q5_abstains_when_insufficient"] == dict(
        any_refusal=False, refused_at_strengths=[])


# ---------------------------------------------------------------------------
# 9. sweep_one -- wiring a real perturbation into a real measure()
# ---------------------------------------------------------------------------
def test_sweep_one_reports_the_signed_delta_from_base():
    x = _damped(540.0, 0.05, 1.0, int(0.3 * SR))
    case = pl.EstimatorCase("probe", "X", lambda y, sr: am.Estimate(
        float(np.max(np.abs(y))), True, "", {}, None), 0.01, False, 5000.0)
    base = case.measure(x, SR)
    pert = pl.PERTURBATIONS["gain_db"]
    relation, rows = pl.sweep_one(case, pert, x, SR, base)
    assert relation == "invariant"
    for row, db in zip(rows, pert.strengths):
        expected_peak_delta = base.value * (10.0 ** (db / 20.0) - 1.0)
        assert row["measured_delta"] == pytest.approx(expected_peak_delta, abs=1e-5)
        assert row["expected_delta"] == 0.0


# ---------------------------------------------------------------------------
# 10. run_ladder -- end to end against a synthetic stand-in corpus
# ---------------------------------------------------------------------------
def _write_wav(path: pathlib.Path, x: np.ndarray, sr: int = SR) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    xi = np.clip(x * 32000.0, -32768, 32767).astype(np.int16)
    wavfile.write(str(path), sr, xi)


def _build_fake_corpus(root: pathlib.Path) -> None:
    n = int(0.5 * SR)
    rs = _damped(455.0, 0.040, 1.0, n) + _damped(1786.0, 0.040, 0.6, n, phase=1.0)
    _write_wav(root / "rs8" / "RS.WAV", rs)
    cb = _damped(540.0, 0.15, 1.0, n) + _damped(800.0, 0.10, 0.6, n, phase=0.5)
    _write_wav(root / "cb8" / "CB.WAV", cb)
    bd = _damped(49.4, 0.12, 1.0, n)
    _write_wav(root / "bd8" / "BD5050.WAV", bd)


def test_run_ladder_end_to_end_on_a_synthetic_stand_in_corpus(tmp_path):
    """Not a claim about the real Fischer corpus (absent on this host, and
    out-of-band for this change per #518's own Test Plan) -- this exercises
    every estimator/perturbation/question code path without crashing, which
    the REFUSED-corpus test above cannot reach."""
    _build_fake_corpus(tmp_path)
    result = pl.run_ladder(tmp_path)
    assert result["status"] == "OK"
    assert set(result["estimators"]) == {c.name for c in pl.ESTIMATOR_CASES}
    ran_at_least_one = False
    for name, est in result["estimators"].items():
        assert est["status"] in ("OK", "BASE_REFUSED", "REFUSED")
        if est["status"] != "OK":
            continue
        ran_at_least_one = True
        assert set(est["perturbations"]) == set(pl.PERTURBATIONS)
        for pname, sweep in est["perturbations"].items():
            assert len(sweep["rows"]) == len(pl.PERTURBATIONS[pname].strengths)
            for key in ("q1_detects_above_resolution", "q2_tracks_introduced_magnitude",
                       "q3_more_defect_more_error", "q4_false_unrelated_move",
                       "q5_abstains_when_insufficient"):
                assert key in sweep["questions"]
    assert ran_at_least_one, "every estimator refused on the synthetic stand-in corpus"


def test_run_ladder_the_known_resample_dual_effect_survives_the_full_sweep(tmp_path):
    """decay_tau's resample sweep, run through the FULL pipeline (not just
    `expected_delta` in isolation), must show the measured tau tracking the
    `tau / a` formula closely on a clean synthetic signal."""
    _build_fake_corpus(tmp_path)
    result = pl.run_ladder(tmp_path, estimators=["decay_tau"])
    sweep = result["estimators"]["decay_tau"]["perturbations"]["resample_ratio"]
    assert sweep["relation"] == "scales_inverse"
    q = sweep["questions"]
    assert q["q2_tracks_introduced_magnitude"]["answer"] is True
    assert q["q2_tracks_introduced_magnitude"]["correlation"] > 0.99


def test_run_ladder_restricts_to_the_requested_estimators(tmp_path):
    _build_fake_corpus(tmp_path)
    result = pl.run_ladder(tmp_path, estimators=["tone_ratio_db"])
    assert set(result["estimators"]) == {"tone_ratio_db"}


def test_run_ladder_refuses_per_estimator_when_its_own_file_is_missing(tmp_path):
    """The corpus directory exists, but one voice's file does not -- a
    partial corpus must REFUSE that estimator by name, not crash the sweep
    or silently drop it from the report."""
    (tmp_path / "rs8").mkdir(parents=True)
    # RS.WAV deliberately absent.
    result = pl.run_ladder(tmp_path, estimators=["band_pair_db"])
    assert result["status"] == "OK"
    assert result["estimators"]["band_pair_db"]["status"] == "REFUSED"
    assert "RS.WAV" in result["estimators"]["band_pair_db"]["why"] or \
        "rs8" in result["estimators"]["band_pair_db"]["why"]


# ---------------------------------------------------------------------------
# 11. main() end to end
# ---------------------------------------------------------------------------
def test_main_runs_end_to_end_and_writes_json(tmp_path, capsys):
    _build_fake_corpus(tmp_path)
    out_json = tmp_path / "report.json"
    rc_code = pl.main(["--refs", str(tmp_path), "--estimator", "tone_ratio_db",
                       "--json", str(out_json)])
    captured = capsys.readouterr().out
    assert rc_code == 0
    assert "tone_ratio_db" in captured
    assert out_json.exists()
    import json
    payload = json.loads(out_json.read_text())
    assert payload["status"] == "OK"
    assert set(payload["estimators"]) == {"tone_ratio_db"}
