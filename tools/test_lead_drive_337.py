import math
import pytest
import lead_drive_337 as L

BASE = dict(n_cells=20, max_abs=7.5, rms=4.5, mean_signed=-4.0, alias_max=2.0,
            level_mean=-18.0, pitch_abs=0.10)


def cand(**kw):
    return {**BASE, **kw}


GOOD = cand(max_abs=4.5, rms=3.0, mean_signed=-2.5)


def test_start_red_baseline_against_itself_is_ineligible():
    # the stub "candidate = baseline" must NOT pass (rule 2 and others)
    assert L.rule_failures(BASE, dict(BASE))


def test_good_candidate_passes_and_is_chosen():
    assert L.rule_failures(BASE, GOOD) == []
    assert L.choose(BASE, {0.5: (GOOD, True)}) == 0.5


def test_quieter_candidate_cannot_win():
    f = L.rule_failures(BASE, cand(max_abs=4.5, rms=3.0, mean_signed=-2.5, level_mean=-23.0))
    assert any(x.startswith("5") for x in f)


def test_darker_candidate_cannot_win():
    f = L.rule_failures(BASE, cand(max_abs=4.5, rms=3.0, mean_signed=-4.6))
    assert any(x.startswith("4") for x in f)
    # brighter than baseline but darker than the reference is fine (reference = 0)
    assert not any(x.startswith("4") for x in L.rule_failures(BASE, cand(mean_signed=-3.0)))


def test_darkness_guard_when_baseline_is_bright():
    bright = dict(BASE, mean_signed=+1.0)
    # going from +1 to -0.6 passes only if >= min(+1, 0) - 0.5 = -0.5 -> fails
    assert any(x.startswith("4") for x in L.rule_failures(bright, cand(mean_signed=-0.6, max_abs=4, rms=3)))


def test_alias_pitch_and_unreachable_rules():
    assert any(x.startswith("6") for x in L.rule_failures(BASE, cand(max_abs=4, rms=3, alias_max=3.0)))
    assert any(x.startswith("7") for x in L.rule_failures(BASE, cand(max_abs=4, rms=3, pitch_abs=0.2)))
    assert any(x.startswith("1") for x in L.rule_failures(BASE, GOOD, reachable=False))


def test_rms_regression_blocks_a_max_only_win():
    assert any(x.startswith("3") for x in L.rule_failures(BASE, cand(max_abs=4.5, rms=5.0)))


def test_choose_lowest_among_eligible_and_none():
    better = cand(max_abs=3.0, rms=2.0, mean_signed=-1.5)
    assert L.choose(BASE, {0.5: (GOOD, True), 0.25: (better, True)}) == 0.25
    assert L.choose(BASE, {0.5: (GOOD, True), 0.25: (better, False)}) == 0.5
    assert L.choose(BASE, {0.5: (dict(BASE), True)}) is None


def test_compensation_known_answer():
    assert L.compensation_db([-18, -20], [-24, -26]) == pytest.approx(6.0)


def test_vol_effective_and_reach_boundary():
    assert L.vol_effective("pulse", 0.0) == pytest.approx(0.45)
    assert L.vol_effective("saw", 0.0) == pytest.approx(0.45 * 10 ** (-0.45428 / 20))
    assert L.vol_effective("pulse", 6.9) > 0.99
    assert L.vol_effective("pulse", 7.0) > 1.0


def test_summarise_refuses_nan_and_thin_data():
    ev = [dict(errors={"h2": 1.0, "h3": float("nan"), "h4": 1.0, "h5": 1.0},
               gain_model=-18, alias=0, pitch=0)]
    with pytest.raises(L.Refused):
        L.summarise(ev)
    with pytest.raises(L.Refused):
        L.summarise([dict(errors={"h2": 1.0}, gain_model=-18, alias=0, pitch=0)])


def test_summarise_known_answer():
    ev = [dict(errors={"h2": -3.0, "h3": -4.0, "h4": 4.0, "h5": 3.0},
               gain_model=-10, alias=1.5, pitch=-0.2)]
    s = L.summarise(ev)
    assert s["max_abs"] == 4.0 and s["mean_signed"] == 0.0
    assert s["rms"] == pytest.approx(math.sqrt(12.5)) and s["pitch_abs"] == 0.2


# ---------------------------------------------- apparatus-precondition controls
KEYS = ("Harmonic shape", "Foldback energy", "Gain", "Pitch")
RECS = {c: {k: {"value": v} for k, v in zip(KEYS, (7.0, 2.0, -18.0, 0.1))}
        for c in ("M5A", "M5B")}


def fake(sha, **over):
    m = {k: {"value": RECS["M5A"][k]["value"]} for k in KEYS}
    for k, v in over.items():
        m[k.replace("_", " ")] = {"value": v}
    return {"metrics": m, "_sha": sha}


def pre(a=None, b=None, probe=None):
    return L.establish_preconditions(a or fake("A"), b or fake("B"),
                                     probe or fake("P"), load_record=lambda c: RECS[c])


def test_preconditions_positive_leg_passes():
    r = pre()
    assert r["baseline_matches_records"] and r["drive_changes_audio"]
    assert r["baseline_sha"] == "A" and r["drive0.5_sha"] == "P"
    # a deviation inside RECORD_TOL is accepted (the guard is not trivially strict)
    assert pre(a=fake("A", Gain=-18.0 + L.RECORD_TOL / 2))


@pytest.mark.parametrize("which", ["a", "b"])
@pytest.mark.parametrize("key", ["Harmonic_shape", "Foldback_energy", "Gain", "Pitch"])
def test_baseline_mismatch_is_refused_for_every_metric_and_case(which, key):
    bad = fake("A" if which == "a" else "B", **{key: RECS["M5A"][key.replace("_", " ")]["value"] + 0.1})
    with pytest.raises(L.Refused, match="baseline M5[AB] .* != record"):
        pre(**{which: bad})


def test_baseline_mismatch_just_over_tolerance_is_refused():
    with pytest.raises(L.Refused, match="baseline"):
        pre(a=fake("A", Gain=-18.0 + 2 * L.RECORD_TOL))


def test_baseline_nan_is_refused_not_passed():
    # NaN > tol is False: a naive comparison guard would silently accept it
    with pytest.raises(L.Refused):
        pre(a=fake("A", Gain=float("nan")))


def test_unchanged_candidate_audio_is_refused():
    with pytest.raises(L.Refused, match="did not change the audio"):
        pre(probe=fake("A"))


def test_unchanged_audio_refused_even_when_metrics_differ():
    # same bytes, different metrics dict: the SHA is the evidence, not metrics
    with pytest.raises(L.Refused, match="did not change the audio"):
        pre(probe=fake("A", Gain=-30.0))

