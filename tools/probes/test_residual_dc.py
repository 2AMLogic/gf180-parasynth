#!/usr/bin/env python3
"""Apparatus controls for tools/probes/residual_dc.py (#152).

    python3 -m pytest tools/probes/test_residual_dc.py -q

COMMITTED RED FIRST (docs/residual-dc/red-start.txt): the estimator was a stub
returning NaN, every fixture executed, and the intended assertions failed.
Only then was the estimator written.

Every fixture's answer is fixed by CONSTRUCTION (a constant added to a
zero-mean ring; a 10 ms half-sine pulse; a Hann-windowed integer-cycle tone),
not by running the estimator on the drum model. Nothing here renders the
model, so these tests are cheap and are the gate the model records depend on.
"""
from __future__ import annotations

import json
import math
import pathlib
import sys

import numpy as np
import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import residual_dc as R                                   # noqa: E402

SR = 48000
FC = R.FC


@pytest.mark.parametrize("name,prop", R.PROPERTIES, ids=[n for n, _ in R.PROPERTIES])
def test_property_holds_on_the_real_estimator(name, prop):
    assert prop(R.REAL), name


def test_every_injected_defect_turns_something_red():
    rows = R.controls_matrix()
    clean = rows.pop("(clean)")
    assert all(v == "ok" for v in clean.values()), clean
    for defect, row in rows.items():
        assert "MOVED" in row.values(), f"{defect} is invisible to every property: {row}"


def test_each_property_catches_at_least_one_defect():
    """A property no defect moves is a property that tests nothing."""
    rows = R.controls_matrix()
    rows.pop("(clean)")
    dead = [n for n, _ in R.PROPERTIES if all(r[n] != "MOVED" for r in rows.values())]
    # EXEMPT, stated: polarity and zero-mean are guarded by construction
    # (|X0|^2 is sign-blind; a zero-mean burst has nothing for a defect to
    # erase); burst->SKIRT is a direction none of the five defects attacks
    # (a 10 ms pulse has ~no mean to subtract) -- it is held by the closed-form
    # tests above instead. Anything else dead is a test that tests nothing.
    assert set(dead) <= {"polarity", "zero-mean!=OFFSET", "burst->SKIRT"}, dead


def test_the_mean_subtracting_conditioner_is_the_named_wrong_one():
    """The exact conditioner the issue names. It erases the offset it classifies."""
    est = R.MeanSubtract()
    assert R.classify(R.fx_offset(), SR, FC, est)[0] != "OFFSET"
    assert R.classify(R.fx_offset(), SR, FC, R.REAL)[0] == "OFFSET"


def test_known_answers_on_the_synthetic_statistics():
    off = R.REAL.stats(R.fx_offset(), SR, FC)
    assert off["beta"] > 0.99 and off["flat"] > 0.9 * (2 * off["n20"] - 1), off
    sk = R.REAL.stats(R.fx_skirt(), SR, FC)
    assert abs(sk["flat"] - 1.0) < 0.1, sk                    # flat: S = 1
    assert abs(sk["beta"] - FC / 20.0) < 0.05, sk             # flat -> beta = fc/20


def test_spectral_flatness_is_the_closed_form_for_a_pulse():
    """Independent of the estimator's own code: the DFT of a 10 ms half-sine is
    |X(f)| ~ cos(pi f T/2)/(1-(fT)^2) -> flat to 20 Hz within 1 %."""
    d = 0.010
    ratio = math.cos(math.pi * 20.0 * d / 2.0) / (1 - (20.0 * d) ** 2)
    assert 0.98 < ratio < 1.0
    assert abs(R.REAL.stats(R.fx_skirt(), SR, FC)["flat"] - 1.0) < 2 * (1 - ratio) + 0.05


def test_added_hf_is_absolute_and_lf_removal_is_not_hf():
    base = R.fx_offset()
    add = R.add_hf(base)
    rem = R.remove_offset(base, 0.08 * R.FS)
    k, d_hf, d_sh = R.share_change_kind(base, add, SR)
    assert k == "HF_ADDED" and d_hf > 10.0, (k, d_hf)
    k, d_hf, d_sh = R.share_change_kind(base, rem, SR)
    assert k == "LF_REMOVAL" and abs(d_hf) < R.HF_FLAT_DB and d_sh > 0, (k, d_hf, d_sh)


# ---- guards, each with the input that defeats it -----------------------------------
def test_refuses_non_finite():
    x = R.fx_offset().astype(float)
    x[100] = np.nan
    assert R.classify(x, SR, FC)[0] == "REFUSED"


def test_refuses_silence_and_near_silence():
    assert R.classify(np.zeros(SR * 3), SR, FC)[0] == "REFUSED"
    quiet = np.zeros(SR * 3)
    quiet[:10] = 3.0                                           # 3 LSB peak
    assert R.classify(quiet, SR, FC)[0] == "REFUSED"


def test_refuses_a_clip_shorter_than_the_declared_window():
    x = R.fx_offset()[: int(1.0 * SR)]
    label, d = R.classify(x, SR, FC)
    assert label == "REFUSED" and "declared" in d["reason"], d


def test_refuses_when_the_voice_is_still_sounding_at_the_window_end():
    """Defeating input: a ring that never decays. Its beta/S are then functions
    of where the window was cut, not of the signal."""
    t = np.arange(int(2.4 * SR)) / SR
    x = R._q(0.3 * np.sin(2 * np.pi * 300.0 * t) + 0.08)
    label, d = R.classify(x, SR, FC)
    assert label == "REFUSED" and "still sounding" in d["reason"], (label, d)


def test_refuses_sub20_energy_that_does_not_clear_the_quantisation_floor():
    """Defeating input: a zero-mean 33 LSB burst. It clears the silence guard
    (peak >= 16 LSB) and has nothing in 0-20 Hz; a classifier that answered
    would be reading rounding residue. (A constant offset cannot defeat this
    guard: even 1 LSB held for 2 s is 41 dB over the white floor, because all
    of it lands in one bin. Tried; stated rather than constructed.)"""
    label, d = R.classify(R.fx_zero_mean(amp=0.001), SR, FC)
    assert label == "REFUSED" and "quantisation floor" in d["reason"], (label, d)


def test_refuses_a_band_it_cannot_resolve():
    """Defeating input: a corner below the 4-bin resolution (0.5 Hz bins)."""
    label, d = R.classify(R.fx_offset(), SR, 1.0)
    assert label == "REFUSED" and "band resolution" in d["reason"], (label, d)


def test_a_quantisation_floor_fixture_really_sits_at_the_floor():
    """The floor formula against a measured requantisation of white noise
    (an independent check: dither is generated, not derived)."""
    rng = np.random.default_rng(7)
    x = rng.uniform(-0.5, 0.5, SR * 8)                          # +-0.5 LSB uniform
    got = R.band_db(x, SR, 0.0, 20.0)
    assert abs(got - R.quantisation_floor_db(SR)) < 1.5, got


def test_window_means_are_onset_relative_and_never_zero_for_a_missing_window():
    x = R.fx_offset()
    y = np.concatenate([np.zeros(12345), x])
    a, b = R.window_means(x, SR), R.window_means(y, SR)
    assert np.allclose(a, b, equal_nan=True)
    short = R.window_means(x[:int(0.1 * SR)], SR)
    assert math.isnan(short[-1]) and math.isnan(short[-2])


def test_mixed_is_reported_not_forced():
    """A burst sitting on a short plateau: beta says skirt, S says offset."""
    x = R.fx_skirt().astype(float)
    x[:int(0.2 * SR)] += 0.05 * R.FS                            # a 200 ms standing step
    label, d = R.classify(x, SR, FC)
    assert label in ("MIXED", "OFFSET", "REFUSED"), (label, d)
    assert label != "SKIRT", d


# ---- the reference gate: REFUSED is an outcome, and each guard has its defeater ----
def _corpus(tmp, coupling="dc", corrupt=False, drop_sr=False, drop_file=False):
    import hashlib
    f = tmp / "BD.wav"
    f.write_bytes(b"RIFFfake")
    h = hashlib.sha256(b"RIFFfake").hexdigest()
    if corrupt:
        h = "0" * 64
    m = {"files": [{"name": "BD.wav", "sha256": h}], "sample_rate": 48000}
    if coupling:
        m["capture_coupling"] = coupling
    if drop_sr:
        del m["sample_rate"]
    if drop_file:
        f.unlink()
    (tmp / "manifest.json").write_text(json.dumps(m))
    return str(tmp)


def test_reference_gate_refuses_when_there_is_no_corpus(tmp_path):
    g = R.reference_gate(str(tmp_path / "nope"), environ={})
    assert g["status"] == "REFUSED" and g["dc"] == "REFUSED"


def test_reference_gate_refuses_without_a_manifest(tmp_path):
    assert R.reference_gate(str(tmp_path), environ={})["status"] == "REFUSED"


@pytest.mark.parametrize("kw", [dict(corrupt=True), dict(drop_sr=True), dict(drop_file=True),
                                dict(coupling=None)], ids=["bad-hash", "no-rate", "missing-file", "no-coupling"])
def test_reference_gate_refuses_each_defeating_manifest(tmp_path, kw):
    g = R.reference_gate(_corpus(tmp_path, **kw), environ={})
    assert g["status"] == "REFUSED" and g["reasons"], g


def test_an_ac_coupled_capture_is_available_but_never_proves_absence_of_dc(tmp_path):
    g = R.reference_gate(_corpus(tmp_path, coupling="ac"), environ={})
    assert g["status"] == "AVAILABLE" and g["dc"] == "REFUSED", g


def test_only_a_declared_dc_coupled_verified_capture_permits_a_dc_reading(tmp_path):
    g = R.reference_gate(_corpus(tmp_path, coupling="dc"), environ={})
    assert g["status"] == "AVAILABLE" and g["dc"] == "PERMITTED", g


# ---- the ending logic, on constructed rows (no render) ------------------------------
def _row(label, mean_frac=0.0, reason=None):
    return dict(label=label, mean_frac=mean_frac, detail=dict(reason=reason))


def test_verdict_never_claims_a_sound_defect_without_a_reference():
    v = R.subject_verdict({"dev": _row("OFFSET", .1), "confirm": _row("OFFSET", .1)})
    assert "capability REFUSED" in v and "STANDING OFFSET" in v


def test_verdict_is_no_verdict_when_a_condition_refuses_or_the_conditions_disagree():
    assert R.subject_verdict({"dev": _row("REFUSED", reason="x"), "confirm": _row("OFFSET", .1)}).startswith("NO VERDICT")
    v = R.subject_verdict({"dev": _row("OFFSET", .1), "confirm": _row("SKIRT")})
    assert v.startswith("NO VERDICT") and "NOT revisited" in v


def test_a_small_offset_is_not_a_defect_by_the_declared_magnitude():
    v = R.subject_verdict({"dev": _row("OFFSET", .001), "confirm": _row("OFFSET", .001)})
    assert "no defect established" in v


def test_skirt_in_both_conditions_establishes_no_offset():
    v = R.subject_verdict({"dev": _row("SKIRT"), "confirm": _row("SKIRT")})
    assert v.startswith("NO STANDING OFFSET")


def test_hypotheses_refuse_on_a_refused_class_and_do_not_fit_a_bad_prediction():
    base = dict(label="OFFSET", beta=.97, S=40.0, reason=None, steady_db=11.7, measured_db=5.0)
    ok = R.hypotheses({"CH": dict(base, rest_db=5.4)})[0][1]
    assert ok.startswith("CONSISTENT")
    bad = R.hypotheses({"CH": dict(base, rest_db=9.0)})[0][1]
    assert bad.startswith("NOT SUPPORTED")
    ref = R.hypotheses({"CH": dict(base, label="REFUSED", reason="r", rest_db=5.0)})[0][1]
    assert ref.startswith("NO VERDICT")
    na = R.hypotheses({"CH": dict(base, measured_db=11.0, rest_db=11.0)})[0][1]
    assert na.startswith("NOT APPLICABLE")


def test_the_toggle_in_the_render_is_the_register_the_model_ships():
    """Check that the thing tested is the thing that ships: the production
    register at reset is 0, and the enable writes go through DrumsFx.write."""
    import drums_fx as dx
    assert dx.DrumsFx().couple_en == 0
    d = dx.DrumsFx()
    d.write(dx.A_COUPLE, 1)
    assert d.couple_en == 1


# ---- the integer-resolution intervention, on ground truth ---------------------------
def _sub_lsb_mean_signal(mean=0.27, amp=40.0, f0=11700.0, sr=SR, seconds=2.4):
    n = np.arange(int(seconds * sr))
    return np.floor(amp * np.sin(2 * np.pi * f0 * n / sr + 0.3) + mean + 0.5).astype(np.int64)


def test_the_scale_probe_separates_an_integer_resolution_limit_from_a_linear_one():
    """GROUND TRUTH: a 0.27 LSB mean under an 11.7 kHz ring. A float LTI
    blocker attenuates it by the same amount at any scale; the integer one
    cannot see a sub-LSB mean until acc crosses 2^K."""
    import drums_fx as dx
    from scipy.signal import lfilter
    x = _sub_lsb_mean_signal()
    assert abs(float(x.mean()) - 0.27) < 0.02                   # the construction holds
    a = 1.0 - 2.0 ** -dx.COUPLE_K
    lin = []
    for sc in R.SCALES:
        y = lfilter([1.0, -1.0], [1.0, -a], x * sc)
        lin.append(R.band_db((x * sc)[:96000].astype(float), SR, 0, 20) - R.band_db(y[:96000], SR, 0, 20))
    assert max(lin) - min(lin) < 0.05, lin                      # LTI: scale-free
    integ = [att for _, att in R.integer_scale_probe(x, 0, SR, dx.COUPLE_K)]
    assert integ[-1] - integ[0] > 3.0, integ                    # integer: scale-DEPENDENT
    assert integ[-1] > lin[0] - 1.0, (integ, lin)               # and approaches the LTI answer


def test_the_scale_probe_is_flat_where_the_mean_is_resolvable():
    """Defeater of the 'always scale-dependent' reading: a 20 LSB mean is far
    above the integer estimator's resolution; scale must then not matter much."""
    import drums_fx as dx
    x = _sub_lsb_mean_signal(mean=20.0)
    att = [a for _, a in R.integer_scale_probe(x, 0, SR, dx.COUPLE_K)]
    assert max(att) - min(att) < 1.5, att


def _ch(production, floatout, x64, exact=True, bound=11.7, own=None):
    """`own` = each variant's steady-state bound on ITS OWN baseline. Default:
    the variant did not move its baseline, so its bound is the production one."""
    rows = [("production", production), ("float-out", floatout), ("buses x8", x64), ("buses x64", x64)]
    own = dict({n: bound for n, _ in rows}, **(own or {}))
    return dict(label="OFFSET", beta=.97, S=40.0, reason=None, steady_db=bound, measured_db=production,
                rest_db=bound, bv=dict(exact=exact, rows=rows, bounds=own))


def _h2(row):
    return [v for h, v in R.hypotheses({"CH": row}) if h.startswith("H_CH2")][0]


def test_h_ch2_distinguishes_its_outcomes():
    assert _h2(_ch(5.0, 11.5, 6.0)).startswith("SUPPORTED by intervention (output truncation)")
    assert _h2(_ch(5.0, 6.0, 11.9)).startswith("SUPPORTED by intervention (bus-level")
    assert _h2(_ch(5.0, 6.0, 6.0)).startswith("NOT SUPPORTED")
    assert _h2(_ch(5.0, 11.5, 11.5, exact=False)).startswith("NO VERDICT")


def test_h_ch2_defeater_a_variant_that_moves_its_own_baseline_is_not_support():
    """RULE 8, the input that defeated the first rule (#614 review). The dev
    record had float-out 21.22 dB against a production bound of 11.66 dB, and a
    one-sided `>= bound - tol` read that as SUPPORTED. But removing the output
    floor changes the variant's BASELINE too, so its attenuation is only
    comparable to the steady bound computed on its own baseline.

    (a) the variant clears the production bound but falls short of its own:
        the quantiser was not the whole story -> must NOT read SUPPORTED.
    (b) the variant overshoots its own bound by far more than the tolerance:
        a filter does not beat its own steady-state bound, so the variant is not
        like-for-like -> must NOT read SUPPORTED either."""
    short_of_own = _ch(5.0, 21.2, 21.2, own={"float-out": 30.0, "buses x8": 30.0, "buses x64": 30.0})
    assert not _h2(short_of_own).startswith("SUPPORTED"), _h2(short_of_own)
    overshoot = _ch(5.0, 21.2, 21.2)                     # own bounds == 11.7
    assert not _h2(overshoot).startswith("SUPPORTED"), _h2(overshoot)
    assert _h2(overshoot).startswith("NO VERDICT"), _h2(overshoot)


def test_the_bus_surrogate_is_bit_exact_against_the_shipped_coupling():
    """Check that the thing tested is the thing that ships: a short render with
    the CH, buses re-blocked by DcBlockFx at the declared widths, must equal the
    A_COUPLE=1 buses exactly."""
    import drums_fx as dx
    c = dict(seconds=0.30, gain=0.45, vel=1.0)
    R.render_bus("CH", 0, c["seconds"], c["gain"], c["vel"])
    R.render_bus("CH", 1, c["seconds"], c["gain"], c["vel"])
    got = R.bus_variants("CH", c, 0, dx.SR)
    assert got["exact"] is True
    # and the guard has a defeater: a surrogate at the wrong corner is not exact
    d0, b0, g = R._BUSES[("CH", 0, c["seconds"], c["gain"], c["vel"])]
    _, b1, _ = R._BUSES[("CH", 1, c["seconds"], c["gain"], c["vel"])]
    assert not d0.any() and b0.any()          # the CH lives on the BODY bus; dmix is trivially exact
    wrong = dx.DcBlockFx(dx.COUPLE_K - 1, in_bits=dx.BODY_BITS)
    assert not np.array_equal(np.array([wrong.step(int(v)) for v in b0], np.int64), b1)
