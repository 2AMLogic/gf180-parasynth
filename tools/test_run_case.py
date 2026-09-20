#!/usr/bin/env python3
"""Ground truth for the estimators `tools/run_case.py` adds, and proof that the
runner reports the two states that are easy to get wrong.

    .venv/bin/python -m pytest tools/test_run_case.py -q

TWO JOBS, KEPT APART.

**Ground truth.** Four estimators are not in `model/audio_measure.py` and had
to be written. Each is exercised here against a signal whose answer is known in
closed form, before it is used on anything -- six estimator bugs were found in
this repository the day `model/test_audio_measure.py` was written, and every one
of them looked like a defect in the design until the estimator was checked.

**A runner's only failure mode that matters is a false green.** So the states
this runner must get right are the unhappy ones, and they are tested through
`tools/scorecard.py`'s own `evaluate` -- what the BOARD says, not what the
runner thinks it said:

  * a reference that is not there is `no verdict` with a stated reason, and
    carries no `error` key that could read as a zero distance;
  * a reference shifted by twice the tolerance is `fail`, not a near miss;
  * a voice the kit does not implement is `no verdict`, not a silent absence;
  * a required measurement that is missing invalidates the case rather than
    being dropped from the maximum.
"""
from __future__ import annotations

import json
import math
import pathlib
import sys

import numpy as np
import pytest

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT / "model"))

import audio_measure as am                                          # noqa: E402
import run_case as rc                                               # noqa: E402
import scorecard as sb                                              # noqa: E402

SR = 48000
REFS = pathlib.Path(rc.REFS_DEFAULT)
have_refs = pytest.mark.skipif(
    not (REFS / "bd8" / "BD5050.WAV").exists(),
    reason=f"the Fischer corpus is not at {REFS}; clone sounds-tr808-fischer")


def sine(hz, seconds, amp=1.0, sr=SR):
    """An integer number of periods, so a spectrum of it has no leakage and
    the closed-form answers below are exact."""
    n = int(round(seconds * hz)) * int(round(sr / hz))
    return amp * np.sin(2 * math.pi * hz * np.arange(n) / sr)


def test_changed_control_accepts_distance_change_when_both_states_fail():
    """A control must not be satisfied merely because both runs are red.

    This is the #167 shape: F1A fails cleanly after the corner repair, while
    REF_CORNER_2X makes its measured error much larger.  The distance is the
    evidence that the injected defect fired.
    """
    clean = ("fail", 1.68, "clean F1A")
    injected = ("fail", 6.45, "injected F1A")
    assert rc.control_changed(clean, injected)


def test_changed_control_rejects_an_injection_removed_from_a_clean_failure():
    clean = ("fail", 1.68, "clean F1A")
    same = ("fail", 1.68, "injection removed")
    assert not rc.control_changed(clean, same)


def test_changed_control_rejects_two_refusals():
    clean = ("no verdict", None, "missing frozen reference")
    injected = ("no verdict", None, "missing frozen reference")
    assert not rc.control_changed(clean, injected)


def test_ref_corner_2x_control_moves_a_known_reference_corner(monkeypatch):
    """The octave mutation is proven without the optional Surge audio cache."""
    import reference_rigs as rr
    freqs = np.geomspace(40.0, 12000.0, 32)
    clip_id = "synthetic/known-4pole"
    meta = {"freqs_hz": freqs.tolist(),
            "parts": [[i * 16, 16, float(f)] for i, f in enumerate(freqs)],
            "amp": 0.1, "sha256": "known-ground-truth"}
    profile = {"clips": {clip_id: {"sha256": "known-ground-truth"}}}
    monkeypatch.setattr(rc.rp, "load_profile", lambda: profile)
    monkeypatch.setattr(rc.rp, "load_clip", lambda _cid, _profile: (np.zeros(32), SR, meta))
    monkeypatch.setattr(rr.SurgeRig, "tone_project",
                        staticmethod(lambda _y, parts, _amp, _cid:
                                     ideal_4pole_db([p[2] for p in parts])))

    clean_f, clean_g, _ = rc.load_filter_reference(clip_id)
    shifted_f, shifted_g, _ = rc.load_filter_reference(clip_id, "REF_CORNER_2X")
    clean_corner = rc.filt_corner(IDEAL_FP)(clean_f, clean_g)
    shifted_corner = rc.filt_corner(IDEAL_FP)(shifted_f, shifted_g)
    assert clean_corner.ok and shifted_corner.ok
    assert np.array_equal(shifted_f, clean_f / 2.0)
    assert np.array_equal(shifted_g, clean_g)
    assert abs(shifted_corner.value / clean_corner.value - 0.5) < 0.01
    assert rc.control_changed(("fail", clean_corner.value, "synthetic clean"),
                              ("fail", shifted_corner.value, "synthetic octave fault"))


def test_control_refuses_a_case_that_is_not_implemented():
    outcome = rc.control_outcome(
        "changed", "REF_CORNER_2X", ["M5A"], {"M5A": "not-run"}, {}, {})
    assert outcome[0] == "refused"
    assert "not implemented" in outcome[1]


def test_control_refuses_when_clean_baseline_has_no_verdict():
    clean = {"F1A": ("no verdict", None, "missing reference", "")}
    injected = {"F1A": ("no verdict", None, "missing reference", "")}
    outcome = rc.control_outcome(
        "no verdict", "REF_PROFILE_MISSING", ["F1A"], {"F1A": "filter"},
        clean, injected)
    assert outcome[0] == "refused"
    assert "clean baseline" in outcome[1]


def test_no_verdict_control_requires_its_injected_cause():
    clean = {"F1A": ("pass", 0.5, "", "")}
    unrelated = {"F1A": ("no verdict", None, "", "simulator missing")}
    outcome = rc.control_outcome(
        "no verdict", "REF_PROFILE_MISSING", ["F1A"], {"F1A": "filter"},
        clean, unrelated)
    assert outcome[0] == "fail"
    assert "does not identify" in outcome[1]


def test_no_verdict_control_passes_only_for_measured_clean_and_matching_mutation():
    clean = {"D09A": ("pass", 0.61, "", "")}
    injected = {"D09A": ("no verdict", None, "", "REFUSED: no-such-file.wav")}
    outcome = rc.control_outcome(
        "no verdict", "REF_MISSING", ["D09A"], {"D09A": "drum"},
        clean, injected)
    assert outcome[0] == "pass"


def test_changed_control_requires_comparable_valid_results_for_each_case():
    clean = {"F1A": ("fail", 1.68, "", "")}
    injected = {"F1A": ("fail", 6.45, "", "")}
    outcome = rc.control_outcome(
        "changed", "REF_CORNER_2X", ["F1A"], {"F1A": "filter"},
        clean, injected)
    assert outcome[0] == "pass"
    missing = {"F1A": ("no verdict", None, "", "cache absent")}
    outcome = rc.control_outcome(
        "changed", "REF_CORNER_2X", ["F1A"], {"F1A": "filter"},
        clean, missing)
    assert outcome[0] == "refused"

    removed = rc.control_outcome(
        "changed", "REF_CORNER_2X", ["F1A"], {"F1A": "filter"},
        clean, clean)
    assert removed[0] == "fail"
    assert "indistinguishable" in removed[1]


# ===========================================================================
# Ground truth: band_energy and band_ratio_db
# ===========================================================================
def test_band_ratio_db_of_two_sines_is_their_amplitude_ratio():
    """Two sines either side of the split: the ratio is 20 log10(a_hi/a_lo),
    exactly, and the estimator must not invent a correction."""
    lo = sine(100.0, 0.5, amp=1.0)
    hi = sine(2000.0, 0.5, amp=0.5)
    n = min(len(lo), len(hi))
    e = rc.band_ratio_db(lo[:n] + hi[:n], SR, 700.0)
    assert e.ok
    # 0.3 dB, not 0.0: this splits with a 4th-order Butterworth rather than
    # partitioning FFT bins, and a filter's skirts are not a brick wall. The
    # filter is the point -- a windowed FFT reports a decaying voice's TAIL
    # spectrum and disagrees by a factor of four on a real cymbal.
    assert e.value == pytest.approx(20 * math.log10(0.5), abs=0.3)


def test_band_ratio_db_refuses_a_split_outside_its_band():
    """An absence is not a balance."""
    e = rc.band_ratio_db(sine(100.0, 0.5), SR, 700.0, lo=20.0, hi=600.0)
    assert not e.ok and e.value is None


def test_band_pair_db_of_two_sines_is_their_amplitude_ratio():
    a = sine(1800.0, 0.4, amp=0.5)
    b = sine(450.0, 0.4, amp=1.0)
    n = min(len(a), len(b))
    e = rc.band_pair_db(a[:n] + b[:n], SR, (1500, 2100), (380, 560))
    assert e.ok
    assert e.value == pytest.approx(20 * math.log10(0.5), abs=0.3)


# ===========================================================================
# Ground truth: pitch_drop_hz
# ===========================================================================
def test_pitch_drop_hz_on_a_known_exponential_glide():
    """A tone whose frequency falls from 150 Hz to 90 Hz with a 30 ms time
    constant, under a slow decay. The windows are 4-18 ms and 60-150 ms, so
    the closed-form drop is f(11 ms) - f(105 ms) and the estimator must find
    it. A windowed FFT cannot: 14 ms holds under two periods of a 90 Hz tone,
    which is why this is a phase derivative."""
    f1, f2, tau = 150.0, 90.0, 0.030
    n = int(0.4 * SR)
    t = np.arange(n) / SR
    f = f2 + (f1 - f2) * np.exp(-t / tau)
    ph = 2 * math.pi * np.cumsum(f) / SR
    x = np.exp(-t / 0.25) * np.sin(ph)
    want = (f2 + (f1 - f2) * math.exp(-0.011 / tau)) - (f2 + (f1 - f2) * math.exp(-0.105 / tau))
    e = rc.pitch_drop_hz(x, SR, (20.0, 400.0))
    assert e.ok
    assert e.value == pytest.approx(want, abs=3.0)


def test_pitch_drop_hz_is_zero_for_a_steady_tone():
    n = int(0.4 * SR)
    t = np.arange(n) / SR
    x = np.exp(-t / 0.25) * np.sin(2 * math.pi * 120.0 * t)
    e = rc.pitch_drop_hz(x, SR, (20.0, 400.0))
    # 2 Hz is this estimator's floor, set by the band-pass transient in the
    # early window. A reading inside it means "no measurable sweep" -- which
    # is what the real TR-808 toms read, and why our 50 Hz drop is a finding
    # and not an estimator artefact.
    assert e.ok and abs(e.value) < 2.0


def test_pitch_drop_hz_refuses_a_window_that_is_already_silent():
    n = int(0.4 * SR)
    t = np.arange(n) / SR
    x = np.exp(-t / 0.004) * np.sin(2 * math.pi * 120.0 * t)   # gone by 60 ms
    assert not rc.pitch_drop_hz(x, SR, (20.0, 400.0)).ok


# ===========================================================================
# prepare(): the two artefacts it was written wrong twice to avoid
# ===========================================================================
def _burst_in_silence(seconds=2.2, burst_ms=60.0, dc=0.0, lead_ms=10.0):
    """A 4 ms-tau burst inside 2.2 s of digital silence.

    `burst_ms` was 15, which is 3.75 tau: the exponential was HARD-CUT at
    -32 dB and the 2.2 s of zeros that followed were what satisfied the decay
    guard's length criterion. That is #139's defect standing in this file's own
    fixture, and it is why the number below is 60 -- fifteen tau, so the decay
    is over before the silence starts and the record contains it."""
    n = int(seconds * SR)
    x = np.full(n, dc)
    a = int(lead_ms * 1e-3 * SR)
    k = int(burst_ms * 1e-3 * SR)
    t = np.arange(k) / SR
    x[a:a + k] += np.exp(-t / 0.004) * np.sin(2 * math.pi * 1800.0 * t)
    return x


def test_prepare_does_not_leave_a_floor_that_never_decays():
    """Subtracting the mean of a buffer that is mostly silence leaves a
    CONSTANT across the silence, and a constant never decays. That put 0.19 %
    of the rimshot's energy into a floor and `schroeder_t20` read a 4.5-second
    T20 for a 15 ms sound. The prepared signal must carry no more energy in
    its last second than the raw one does."""
    x = _burst_in_silence(dc=2e-4)
    y = rc.prepare(x, SR)
    tail = float((y[-SR:] ** 2).sum() / (y ** 2).sum())
    assert tail < 1e-6, tail
    e = am.schroeder_t20(y, SR)
    assert e.ok and e.value * 1e3 < 60.0, e
    assert e.value == pytest.approx(am.t20_from_tau(0.004), rel=0.02), \
        f"the prepared burst must read its own closed-form T20: {e}"
    # The control, because "< 60 ms" is also satisfied by a refusal and by a
    # wrong number: the SAME burst with a floor left in must NOT read the
    # closed-form answer, or prepare is not what is being measured here.
    for dc in (1e-3, 2e-3):
        bad = am.schroeder_t20(_burst_in_silence(dc=dc), SR)
        assert not bad.ok or abs(bad.value / e.value - 1) > 0.15, \
            f"a {dc:.0e} floor left in read the right answer anyway: {bad}"


def test_prepare_does_not_put_a_precursor_ahead_of_the_strike():
    """A zero-phase high-pass is not causal: a 20 Hz first-order one puts a
    precursor tens of ms AHEAD of a sharp strike, and that read a 2 ms attack
    as 11 ms. The prepared attack must still be the one that is there."""
    y = rc.prepare(_burst_in_silence(dc=2e-4), SR)
    e = rc.attack_ms(y, SR, window_ms=2.0)
    assert e.ok and e.value < 4.0, e


def test_prepare_removes_a_converter_offset():
    y = rc.prepare(_burst_in_silence(dc=5e-3), SR)
    assert abs(float(y[-SR:].mean())) < 1e-6


# ===========================================================================
# Ground truth: attack_ms
# ===========================================================================
def test_attack_ms_finds_a_known_linear_rise():
    """A 1 kHz carrier under an envelope that rises linearly over exactly
    20 ms and then decays. The short-time RMS window smears the peak by up to
    about one window, so the bound asserted is the window, not zero -- which
    is the honest statement of what this estimator can resolve."""
    rise_ms, win_ms = 20.0, 2.0
    n = int(0.30 * SR)
    t = np.arange(n) / SR
    k = int(rise_ms * 1e-3 * SR)
    env = np.concatenate([np.linspace(0, 1, k), np.exp(-(t[k:] - t[k]) / 0.05)])
    x = env * np.sin(2 * math.pi * 1000.0 * t)
    e = rc.attack_ms(x, SR, window_ms=win_ms)
    assert e.ok
    assert abs(e.value - rise_ms) <= win_ms


def test_attack_ms_cannot_resolve_an_attack_shorter_than_its_window():
    """A signal with no rise in it at all -- an exponential from sample zero.
    The estimator does not refuse; it reports about one window, because a
    short-time RMS cannot see anything faster than its own window. That is the
    resolution floor, and it is the reason every comparison this is used in
    takes BOTH sides with the same window, and the reason
    docs/drum-verification.md compares attack ratios rather than absolute
    attack times measured with different windows."""
    n = int(0.2 * SR)
    t = np.arange(n) / SR
    x = np.exp(-t / 0.02) * np.sin(2 * math.pi * 1000.0 * t)
    for win_ms in (2.0, 4.0):
        e = rc.attack_ms(x, SR, window_ms=win_ms)
        assert e.ok
        assert e.value <= win_ms


def test_attack_ms_refuses_silence():
    """An estimator that always produces a plausible number is how a 700 ms
    attack got reported on seven of eight voices."""
    assert not rc.attack_ms(np.zeros(SR // 10), SR).ok


# ===========================================================================
# Ground truth: tone_ratio_db
# ===========================================================================
def test_tone_ratio_db_of_two_known_sines():
    a, b = sine(800.0, 0.2, amp=1.0), sine(540.0, 0.2, amp=0.5)
    n = min(len(a), len(b))
    e = rc.tone_ratio_db(a[:n] + b[:n], SR, 800.0, 540.0)
    assert e.ok
    assert e.value == pytest.approx(20 * math.log10(1.0 / 0.5), abs=0.2)


def test_tone_ratio_db_refuses_a_record_too_short_to_project():
    """Three cycles is a guess with a plausible value, so the projection
    refuses it rather than returning one."""
    e = rc.tone_ratio_db(sine(800.0, 0.003), SR, 800.0, 540.0)
    assert not e.ok


# ===========================================================================
# Ground truth: worst_event_offset_ms
# ===========================================================================
def _clicks(times, sr=SR, seconds=2.0, tau=0.010):
    n = int(seconds * sr)
    t = np.arange(n) / sr
    x = np.zeros(n)
    for s in times:
        i = int(s * sr)
        env = np.exp(-(t[i:] - t[i]) / tau)
        x[i:] += env * np.sin(2 * math.pi * 900.0 * (t[i:] - t[i]))
    return x


def test_worst_event_offset_ms_on_bursts_at_known_times():
    """Bursts placed at times we wrote down: the worst offset is inside the
    10 ms `audio_measure.onsets` states for itself."""
    times = [0.10, 0.60, 1.10, 1.60]
    e = rc.worst_event_offset_ms(_clicks(times), SR, times)
    assert e.ok
    assert e.value <= 10.0


def test_worst_event_offset_ms_sees_a_moved_event():
    """One burst 40 ms late, and the estimator must report about 40 ms -- not
    an average over the events that were on time."""
    sched = [0.10, 0.60, 1.10, 1.60]
    e = rc.worst_event_offset_ms(_clicks([0.10, 0.60, 1.14, 1.60]), SR, sched)
    assert e.ok
    assert e.value == pytest.approx(40.0, abs=10.0)


def test_worst_event_offset_ms_survives_hits_that_ring_into_each_other():
    """Our bass drum's T20 is 348 ms and the groove puts its hits 363 ms
    apart, so every strike lands on the last one's ring and the analytic
    envelope beats. With the detector's minimum gap left at its 20 ms default
    that reads 12 onsets where 10 were written; taken from the schedule it
    reads 10."""
    times = [0.10, 0.46, 0.82, 1.30, 1.66]
    x = _clicks(times, seconds=2.4, tau=0.15)          # rings past the next hit
    e = rc.worst_event_offset_ms(x, SR, times)
    assert e.ok, e
    assert e.value <= 10.0
    assert e.detail["min_gap_s"] == pytest.approx(0.18, abs=0.01)


def test_worst_event_offset_ms_never_goes_below_the_estimators_own_gap():
    """The schedule can only make the detector STRICTER than its 20 ms
    default, never looser -- otherwise a dense stimulus could talk it into
    accepting ripple as an event."""
    e = rc.worst_event_offset_ms(_clicks([0.10, 0.12]), SR, [0.10, 0.12])
    assert (e.detail or {}).get("min_gap_s", 0.02) >= 0.02


def test_worst_event_offset_ms_refuses_when_the_counts_disagree():
    """A missing event makes the pairing a guess, so there is no number."""
    e = rc.worst_event_offset_ms(_clicks([0.10, 0.60]), SR, [0.10, 0.60, 1.10])
    assert not e.ok and e.value is None


def test_coincident_hits_are_one_event():
    """Two stops struck in the same frame produce one onset. Counting them as
    two would make a correct render look like a miss."""
    e = rc.worst_event_offset_ms(_clicks([0.10, 0.60]), SR, [0.10, 0.1001, 0.60])
    assert e.ok


# ===========================================================================
# The runner's unhappy states, judged by the board's own evaluate()
# ===========================================================================
def _case(cid, required, family="Drums"):
    return {"case_id": cid, "family": family, "split": "Development",
            "subject": "test", "reference_target": "test", "batch": "First 32",
            "required_measurements": "; ".join(required)}


def _cases_row(cid):
    with open(rc.CASES_CSV) as fh:
        import csv
        for row in csv.DictReader(fh):
            if row["case_id"] == cid:
                return row
    raise AssertionError(cid)


def _prov():
    """The minimum provenance the board now insists on."""
    return {"worktree": {"commit": "abc1234", "dirty": False,
                         "uncommitted_sha256": "0" * 16},
            "command": "tools/run_case.py X1", "inputs": {"model/drums_fx.py": "sha256:x"},
            "engine": "fixed-model", "outcome_code": 0,
            "outcome_code_meaning": rc.OUTCOME_MEANING}


def test_a_result_without_provenance_gets_no_verdict():
    """A number nobody can re-derive is not evidence. With fourteen worktrees
    live at once, a result that cannot say what it ran against cannot be told
    apart from a stale one."""
    case = _case("X0", ["a"])
    good = {"value": 1.0, "reference": 1.0, "error": 0.0, "tolerance": 1.0,
            "units": "Hz", "valid": True}
    res = {"engine": "fixed-model", "metrics": {"a": good}}
    r = sb.evaluate(case, res)
    assert r["state"] == sb.NO_VERDICT and "no provenance" in r["why"]
    # ... and the same result WITH provenance passes, so the gate is the
    # provenance and not something else about the fixture.
    res["provenance"] = _prov()
    assert sb.evaluate(case, res)["state"] == sb.PASS


def test_a_clean_commit_is_not_enough_when_the_tree_is_dirty():
    """The uncommitted tree is hashed as well as the commit, so two runs at the
    same SHA with different working trees are distinguishable on the record."""
    w = rc.worktree_state()
    assert set(w) >= {"commit", "dirty", "uncommitted_sha256"}
    assert len(w["uncommitted_sha256"]) == 16


def test_the_outcome_code_convention_is_the_repositorys():
    """0 match, 1 mismatch (a result), 2 did not run (no evidence). A case that
    produced no evidence and one that was measured and scored badly need
    opposite responses, so they must never share a code."""
    assert rc.OUTCOME_CODE["pass"] == 0
    assert rc.OUTCOME_CODE["fail"] == 1
    assert rc.OUTCOME_CODE["no verdict"] == 2 and rc.OUTCOME_CODE["not run"] == 2


def test_every_result_this_runner_writes_carries_provenance():
    row = _cases_row("D16A")
    res = rc.run_case(row, REFS, keep_audio=False)
    prov = res["provenance"]
    assert prov["engine"] in sb.ENGINES
    assert prov["worktree"]["commit"] and prov["command"]
    assert "model/drums_fx.py" in prov["inputs"]
    assert prov["outcome_code_meaning"] == rc.OUTCOME_MEANING


def test_an_invalid_metric_carries_no_error_key_at_all():
    """Zero reads on the board as a perfect match. An invalid measurement has
    NO distance, so there must be no `error` to read."""
    m = rc.invalid_metric("Hz", "the reference is not there", 5.0)
    assert m["valid"] is False and "error" not in m and "value" not in m


def test_a_missing_required_measurement_invalidates_the_case():
    """Not dropped from the maximum -- otherwise the cheapest route to a better
    score is to stop measuring the inconvenient thing."""
    case = _case("X1", ["a", "b"])
    res = {"engine": "fixed-model", "provenance": _prov(),
           "metrics": {"a": {"value": 1.0, "reference": 1.0, "error": 0.0,
                             "tolerance": 1.0, "units": "Hz", "valid": True}}}
    r = sb.evaluate(case, res)
    assert r["state"] == sb.NO_VERDICT and "missing required: b" in r["why"]


def test_a_result_without_an_engine_gets_no_verdict():
    case = _case("X2", ["a"])
    res = {"provenance": _prov(),
           "metrics": {"a": {"value": 1.0, "reference": 1.0, "error": 0.0,
                             "tolerance": 1.0, "units": "Hz", "valid": True}}}
    assert sb.evaluate(case, res)["state"] == sb.NO_VERDICT


def test_every_engine_this_runner_writes_is_one_the_board_knows():
    assert rc.ENGINE in sb.ENGINES


def test_a_sound_the_kit_does_not_have_is_refused_not_guessed():
    """Every sound in cases.csv is now in the kit, so this is exercised
    directly: the renderer must refuse a name it does not know rather than
    striking some other circuit and calling it that sound."""
    with pytest.raises(rc.Refused) as e:
        rc.render_drum_solo("NOPE")
    assert "sixteen sounds" in str(e.value)


def test_a_case_with_no_measurement_plan_is_a_stated_no_verdict(monkeypatch):
    """A refusal has to name its reason. It counts as accounted for; a silence
    does not."""
    row = _cases_row("D14A")
    monkeypatch.setitem(rc.DRUM_PLAN, "CY", None)
    monkeypatch.delitem(rc.DRUM_PLAN, "CY")
    res = rc.run_case(row, REFS, keep_audio=False)
    r = sb.evaluate(row, res)
    assert r["state"] == sb.NO_VERDICT
    assert "REFUSED" in res["note"] and "CY" in res["note"]
    for m in res["metrics"].values():
        assert m["valid"] is False and "error" not in m


@have_refs
def test_a_missing_reference_is_a_no_verdict_with_a_reason():
    """The control: point the reference at a file that is not there. The board
    must say no verdict and the reason must name the missing recording.

    On D09A, not D01A, and the second assertion is why: D01A is a no-verdict
    on its own now (#118's length guard refuses the bass drum reference's
    decay), so this control would have gone on passing with the injection
    removed -- a control that fires without its defect is a false green. The
    uninjected run of D09A is asserted to be a PASS, so the no-verdict here
    can only be the injection."""
    row = _cases_row("D09A")
    res = rc.run_case(row, REFS, inject="REF_MISSING", keep_audio=False)
    r = sb.evaluate(row, res)
    assert r["state"] == sb.NO_VERDICT
    assert "missing" in res["note"].lower()
    assert all("error" not in m for m in res["metrics"].values())
    clean = sb.evaluate(row, rc.run_case(row, REFS, keep_audio=False))
    assert clean["state"] == sb.PASS, \
        "the control must be the injection, not a case that had no verdict anyway"


@have_refs
def test_a_reference_shifted_by_twice_the_tolerance_fails():
    """The other control: the reference pitch moved 20 %, which is twice the
    frequency tolerance. A runner that reported this as a pass would be
    reporting a false green, which is the only failure mode that matters.

    **This control ran on D01A until #118's length guard landed**, which
    refuses the bass drum reference's decay -- 1.57 T20s of record past the
    -25 dB point against a requirement of 2 -- so D01A is now a no-verdict
    whatever is injected into it and no injection can turn it red. A control
    that cannot fire is not a control. Nor is one that fails when CLEAN: D06A
    was the first replacement and the re-run then made it a genuine fail, its
    body spectrum having been flattered by the very #101 artefact this branch
    removed. D09A (claves) passes clean at 0.61, has a direct `Pitch` metric at
    the 10 % frequency tolerance, and fails injected at 2.39."""
    row = _cases_row("D09A")
    res = rc.run_case(row, REFS, inject="REF_F0_20PCT", keep_audio=False)
    r = sb.evaluate(row, res)
    assert r["state"] == sb.FAIL, res["metrics"]
    assert r["worst"] > 1.0


@have_refs
def test_the_same_case_without_the_injection_does_not_fail_on_pitch():
    """A control only means something if the uninjected run differs. Without
    the shift, the pitch metric is inside its own tolerance -- so the failure
    above is the injection and not the case."""
    row = _cases_row("D09A")
    res = rc.run_case(row, REFS, keep_audio=False)
    m = res["metrics"]["Pitch"]
    assert m["valid"] and abs(m["error"]) <= m["tolerance"]


@have_refs
def test_the_injection_refuses_to_write_onto_the_board():
    """A control's output is not evidence about the instrument and must never
    be able to be mistaken for it."""
    assert rc.main(["--inject", "REF_F0_20PCT", "D01A"]) == 2


def test_every_first_32_case_has_a_stated_plan():
    """Honestly accounted for means every case says what happens to it: a
    measurement, a stated refusal, or a stated reason for not running. A case
    with no entry anywhere is the one that quietly disappears."""
    import csv
    with open(rc.CASES_CSV) as fh:
        rows = [r for r in csv.DictReader(fh) if r["batch"] == "First 32"]
    assert len(rows) == 32
    unplanned = [r["case_id"] for r in rows if rc.plan_for(r["case_id"]) == "unplanned"]
    assert not unplanned, f"no stated plan for {unplanned}"


def test_a_t20_fitted_across_a_knee_is_refused():
    """A voice that decays and then sits on a floor gives an energy curve that
    is monotone but not straight, and a line fitted across the knee is not a
    decay time. Both sides of every comparison get the same bound."""
    n = int(1.0 * SR)
    t = np.arange(n) / SR
    x = np.exp(-t / 0.004) * np.sin(2 * math.pi * 1800.0 * t) + 3e-3 * np.sin(2 * math.pi * 900.0 * t)
    straight = np.exp(-t / 0.004) * np.sin(2 * math.pi * 1800.0 * t)
    assert not rc._t20_ms(0.0)(x, SR).ok
    assert rc._t20_ms(0.0)(straight, SR).ok


def test_every_sound_in_the_kit_has_a_plan_and_a_reference():
    """Sixteen sounds on eleven circuits. A case whose sound exists but has no
    plan would come back as a refusal that reads like a capability gap, which
    is the exact failure base_check exists to stop."""
    import drums_fx as dx
    for cid, sound in rc.DRUM_CASE_VOICE.items():
        assert sound in dx.SOUND_NAMES, (cid, sound)
        assert sound in rc.DRUM_PLAN, (cid, sound)
        assert sound in rc.REF_MAIN, (cid, sound)


def test_the_base_check_refuses_a_stale_dependency(monkeypatch):
    """The premise of the batch, asserted before any of it runs. Driven with a
    fake `git` so it tests the rule and not today's `origin/main`: a drums_fx
    on origin/main with more circuits than the one here must refuse."""
    def fake_git(*args):
        if args[:2] == ("rev-parse", "--verify"):
            return "deadbeefcafe0000\n"
        if args[0] == "rev-list":
            return "2\n"
        if args[0] == "show" and args[1].endswith("model/drums_fx.py"):
            return "N_STOPS, N_ENV, N_PATH, N_MODES, N_NUMS, N_OSC = 11, 18, 23, 16, 11, 6\n"
        if args[0] == "show":
            return "something else entirely\n"
        return ""
    monkeypatch.setattr(rc, "_git", fake_git)
    monkeypatch.setattr(rc, "DEPENDENCIES", ("model/drums_fx.py",))
    import drums_fx as dx
    monkeypatch.setattr(dx, "N_STOPS", 8)
    with pytest.raises(rc.StaleBase) as e:
        rc.base_check()
    assert "8 drum circuits" in str(e.value) and "11" in str(e.value)
    # --allow-stale runs anyway and says so on the record, rather than
    # silently producing numbers nobody can tell apart from current ones.
    st = rc.base_check(allow_stale=True)
    assert st["allow_stale"] and st["problems"]


def test_the_base_check_does_not_refuse_on_an_unrelated_commit(monkeypatch):
    """main moves several times an hour here. A gate that fires on commits
    that cannot change a measurement trains everyone to bypass it, and an
    ignored gate is worse than no gate."""
    def fake_git(*args):
        if args[:2] == ("rev-parse", "--verify"):
            return "deadbeefcafe0000\n"
        if args[0] == "rev-list" and args[1] == "--count" and "HEAD.." in args[2]:
            return "3\n"
        if args[0] == "rev-list":
            return "0\n"
        if args[0] == "show" and args[1].endswith("model/drums_fx.py"):
            import drums_fx as dx
            return (ROOT / "model" / "drums_fx.py").read_text()
        return ""
    monkeypatch.setattr(rc, "_git", fake_git)
    monkeypatch.setattr(rc, "DEPENDENCIES", ("model/drums_fx.py",))
    st = rc.base_check()
    assert st["problems"] == [] and st["behind_commits"] == 3
    assert "behind" in st["note"]


def test_the_real_tree_this_batch_ran_on_was_checked():
    """Not a rule -- the fact. If this fires, the results in the tree were
    measured against inputs that are not origin/main's."""
    st = rc.base_check(allow_stale=True)
    assert st.get("checked") is not False
    assert not st["problems"], st["problems"]


def test_tolerances_are_frozen_in_one_place_and_named_by_every_metric():
    """A per-case tolerance is a tolerance fitted to an error. Every rule here
    names one of the frozen classes."""
    for voice, plan in rc.DRUM_PLAN.items():
        for name, units, est, rule in plan:
            _, basis = rule(10.0, {"ref_f0": 100.0})
            assert basis.split(" (")[0] in rc.TOLERANCE_POLICY, (voice, name, basis)


def test_written_results_are_the_shape_the_board_reads(tmp_path):
    """Round trip: what the runner writes is what scorecard.py loads, and the
    verdict does not change in the post."""
    row = _cases_row("D16A")
    res = rc.run_case(row, REFS, keep_audio=False)
    before = sb.evaluate(row, res)["state"]
    p = tmp_path / "D16A.json"
    p.write_text(json.dumps(res, indent=2) + "\n")
    back = json.loads(p.read_text())
    assert back["engine"] in sb.ENGINES
    assert sb.evaluate(row, back)["state"] == before
    assert before in (sb.PASS, sb.FAIL, sb.NO_VERDICT)


# ===========================================================================
# Ground truth for the three FILTER estimators, on closed-form curves.
#
# An ideal analogue 4-pole low-pass has |H| = 1/(1+(f/fp)^2)^2, so its response
# in dB is -40*log10(1+(f/fp)^2) and its -3 dB corner is at exactly
# fp*sqrt(10^(3/40) - 1) = 0.4342*fp. Every answer below is that closed form.
# None of these touches the frozen cache, so they run on a host that has never
# seen a plugin.
# ===========================================================================
IDEAL_FP = 250.0
IDEAL_CORNER = IDEAL_FP * math.sqrt(10 ** (3.0 / 40.0) - 1.0)


def ideal_4pole_db(freqs, fp=IDEAL_FP):
    f = np.asarray(freqs, dtype=np.float64)
    return -40.0 * np.log10(1.0 + (f / fp) ** 2)


def probe_freqs():
    """The profile's own grid, so these tests exercise the same sampling the
    measurement does rather than a denser one that hides a sampling error."""
    import reference_compare as rcmp
    return np.asarray(rcmp.FREQS, dtype=np.float64)


def test_filt_corner_is_plateau_relative_and_grid_interpolated():
    """The absolute number this estimator reports is NOT the textbook -3 dB
    corner, and pretending otherwise is how a biased number reaches a board.
    This test exists to pin that bias where someone reading the board can find
    it, not to wish it away.

    **It used to read 124.96 Hz against a closed-form 108.54 -- 15 % high**,
    and that whole 15 % was the passband reference: the median of a band the
    filter itself is 2.6 dB down at by its top edge. With the reference
    extrapolated to DC (#150) the same curve reads 108.37, and what is left is
    0.16 % of log-grid interpolation on points 20.2 % apart. The bias is now
    smaller than the grid step by two orders of magnitude, and it is still a
    bias."""
    f = probe_freqs()
    e = rc.filt_corner(IDEAL_FP)(f, ideal_4pole_db(f))
    assert e.ok, e.reason
    assert e.value == pytest.approx(108.37, rel=0.005)
    assert abs(e.value / IDEAL_CORNER - 1) < 0.01, \
        f"{e.value:.2f} Hz against a closed-form {IDEAL_CORNER:.2f}"
    # The old reference is still computed and still reported, so the size of
    # the repair stays visible next to the number it repaired.
    assert e.detail["band_median_db"] == pytest.approx(-0.904, abs=0.01)
    assert e.detail["plateau_db"] == pytest.approx(-0.030, abs=0.01)


def test_filt_corner_grid_dependence_is_the_reconciliation_of_150s_two_readings():
    """#150 records two readings of the same control -- 0.500 / 0.445 / 0.434
    and 0.460 / 0.439 / 0.432 -- and says the size of the correction depends on
    which is right. **Both are right, and the difference is the grid**, which
    is part of the instrument and was not stated with either number.

    A grid whose lowest frequency is 20 Hz puts the plateau band lower relative
    to the cutoff, so its median catches less of the filter's own droop and the
    bias is smaller. `geomspace(20, 18000, 32)` reproduces the second reading
    to three decimal places. The board's grid is `reference_compare.FREQS`,
    `geomspace(40, 12000, 32)`, which is the first -- so the first is the one a
    correction had to be sized against.

    This test pins the OLD estimator's readings on both grids, because they are
    what the two audits saw and a reconciliation nobody can re-run is an
    assertion."""
    def old_ratio(F, fc):
        """`filt_corner` exactly as it stood: -3 dB below the band MEDIAN."""
        g = ideal_4pole_db(F, fc)
        return am.corner_from_curve(F, g, ref_band=rc._ref_band(F, fc)).value / fc

    board = probe_freqs()
    assert list(board[[0, -1]]) == [40.0, 12000.0] and len(board) == 32
    got = [old_ratio(board, fc) for fc in (250.0, 1000.0, 4000.0)]
    assert got == pytest.approx([0.4998, 0.4446, 0.4342], abs=0.0005), got

    other = np.geomspace(20.0, 18000.0, 32)
    got2 = [old_ratio(other, fc) for fc in (250.0, 1000.0, 4000.0)]
    assert got2 == pytest.approx([0.4597, 0.4390, 0.4317], abs=0.0005), got2

    # And the point: the repair is grid-independent, so the reconciliation
    # stops mattering once it is in.
    for F in (board, other):
        new = [rc.filt_corner(fc)(F, ideal_4pole_db(F, fc)).value / fc
               for fc in (250.0, 1000.0, 4000.0)]
        assert max(new) / min(new) - 1 < CORNER_RATIO_SPREAD_MAX, (F[0], new)
        assert new == pytest.approx([IDEAL_CORNER / IDEAL_FP] * 3, rel=0.015), (F[0], new)


def test_filt_corner_recovers_a_known_ratio_between_two_corners():
    """The property F1A actually rests on: the bias above is COMMON MODE, so a
    RATIO of two corners measured the same way is right even though neither
    absolute value is the textbook one. Two ideal 4-poles an exact 25 % apart
    must read 25 % apart."""
    f = probe_freqs()
    a = rc.filt_corner(IDEAL_FP)(f, ideal_4pole_db(f, IDEAL_FP))
    b = rc.filt_corner(IDEAL_FP)(f, ideal_4pole_db(f, IDEAL_FP * 1.25))
    assert a.ok and b.ok
    assert b.value / a.value == pytest.approx(1.25, rel=0.05)


# ---------------------------------------------------------------------------
# #150 -- the test whose ABSENCE let a frequency-dependent bias through.
#
# `test_filt_corner_recovers_a_known_ratio_between_two_corners` pins a ratio
# between two corners AT THE SAME cut_hz. That is common mode within one
# comparison and says nothing about whether the bias is the same at 250 Hz and
# at 4 kHz -- which is exactly what a claim about a TREND across the range
# needs. An ideal 4-pole's corner/cutoff ratio is 0.4342 by construction,
# independent of the cutoff, so the estimator's ratio must be constant too.
# ---------------------------------------------------------------------------
#: The commanded cutoffs the Filters family states (`refprofile.CUT_HZ` and
#: `CUT_REGIONS_HZ`), plus four in between so the trend is sampled rather than
#: sampled at its endpoints.
CORNER_SWEEP_HZ = (250.0, 400.0, 630.0, 1000.0, 1600.0, 2500.0, 4000.0)

#: How far the estimator's corner/cutoff ratio may move across that sweep, on
#: a response whose true ratio is constant. This is the instrument's own
#: frequency-dependent systematic and everything read off a trend across
#: cutoffs is limited by it.
CORNER_RATIO_SPREAD_MAX = 0.015


@pytest.mark.parametrize("poles", [2, 4, 6])
def test_filt_corner_ratio_is_constant_across_the_range(poles):
    """An all-pole low-pass `|H| = (1+(f/fc)^2)^(-n/2)` has its -3 dB point at
    `fc*sqrt(10^(3/(10n)) - 1)` -- **a constant multiple of fc, whatever fc
    is.** So the estimator's reported ratio must be constant across the range
    too, to within its own stated systematic.

    It was not. On the profile's own grid it read 0.4998 / 0.4446 / 0.4342 at
    250 / 1000 / 4000 Hz: **15 % of apparent droop contributed by the
    instrument**, concentrated at the bottom of the range, where a claim about
    the filter's cutoff mapping was being read.

    Run over three pole counts because the repair must not be a curve fit to
    the 4-pole case."""
    f = probe_freqs()
    true_ratio = math.sqrt(10 ** (3.0 / (10.0 * poles)) - 1.0)
    ratios = {}
    for fc in CORNER_SWEEP_HZ:
        g = -(10.0 * poles) * np.log10(1.0 + (f / fc) ** 2)
        e = rc.filt_corner(fc)(f, g)
        assert e.ok, (fc, e.reason)
        ratios[fc] = e.value / fc
    spread = max(ratios.values()) / min(ratios.values()) - 1.0
    assert spread < CORNER_RATIO_SPREAD_MAX, (
        f"{poles}-pole: corner/cutoff ratio moves {100*spread:.2f} % across "
        f"{CORNER_SWEEP_HZ[0]:.0f}-{CORNER_SWEEP_HZ[-1]:.0f} Hz on a response "
        f"whose true ratio is {true_ratio:.4f} everywhere: "
        + " ".join(f"{k:.0f}Hz={v:.4f}" for k, v in ratios.items()))


def test_filt_corner_reads_a_constant_tuning_error_as_constant():
    """The consequence, stated the way the board reads it. A synthesiser whose
    cutoff is a **constant 16 % low** at every setting must read as 16 % low at
    every setting. Through the uncalibrated estimator it read -11.9 / -15.3 /
    -15.7 % at 250 / 1000 / 4000 Hz -- a 3.8-point trend manufactured out of a
    constant error, in the same direction as the trend #146 attributed to the
    filter."""
    f = probe_freqs()
    err = 0.16
    read = {}
    for fc in (250.0, 1000.0, 4000.0):
        ref = rc.filt_corner(fc)(f, ideal_4pole_db(f, fc))
        got = rc.filt_corner(fc)(f, ideal_4pole_db(f, fc * (1.0 - err)))
        assert ref.ok and got.ok
        read[fc] = got.value / ref.value - 1.0
    for fc, v in read.items():
        assert abs(v + err) < 0.010, \
            f"a constant -16 % read as {100*v:.2f} % at {fc:.0f} Hz: " + str(read)
    spread = max(read.values()) - min(read.values())
    assert spread < 0.010, f"a constant error read with a {100*spread:.2f}-point trend: {read}"


def test_filt_corner_refuses_a_curve_with_no_corner_in_it():
    """A flat response has no -3 dB point. An estimator that returned its last
    frequency instead would put 12 kHz on the board as a cutoff."""
    f = probe_freqs()
    e = rc.filt_corner(IDEAL_FP)(f, np.zeros(len(f)))
    assert not e.ok


def test_filt_rolloff_of_an_ideal_4pole():
    """An ideal 4-pole fitted between 2.2 and 7 times its measured corner is
    not at its asymptotic -24 dB/oct; it is at about -18.7, and that is the
    number a real 4-pole has to be read against."""
    f = probe_freqs()
    e = rc.filt_rolloff(IDEAL_FP)(f, ideal_4pole_db(f))
    assert e.ok, e.reason
    # With the corrected DC plateau reference the fit band starts at the
    # corrected 108.4 Hz corner, not the old 124.9 Hz moving-median corner.
    assert e.value == pytest.approx(-17.06, abs=0.15)
    assert e.detail["fit_residual_db"] < 1.5


def test_filt_rolloff_is_nearly_scale_invariant():
    """The band is 2.2-7 times each device's OWN corner, so two filters of the
    same shape should read the same slope wherever their corners sit. They do
    not quite, because the corner is interpolated on a 20.2 %-spaced grid, and
    this pins how much: over a 2:1 range of corners the slope must not move by
    more than 1.5 dB/oct, the tolerance itself. Measured today it moves 1.2."""
    f = probe_freqs()
    vals = []
    for fp in (250.0, 312.5, 500.0):
        e = rc.filt_rolloff(IDEAL_FP)(f, ideal_4pole_db(f, fp))
        assert e.ok, (fp, e.reason)
        vals.append(e.value)
    spread = max(vals) - min(vals)
    assert spread < 1.5, vals
    # And the part that matters for a comparison of two nearby corners: 25 %
    # apart must cost less than a fifth of the tolerance.
    a = rc.filt_rolloff(IDEAL_FP)(f, ideal_4pole_db(f, 250.0))
    b = rc.filt_rolloff(IDEAL_FP)(f, ideal_4pole_db(f, 312.5))
    assert abs(a.value - b.value) < 0.45, (a.value, b.value)


def test_filt_rolloff_sees_a_pole_that_is_not_there():
    """The sign the metric has power: a THREE-pole low-pass with the same
    corner must read several dB/oct shallower. A dropped pole is the injected
    defect `reference_compare` already carries for our own ladder, and it must
    not come out looking like a 4-pole."""
    f = probe_freqs()
    fp3 = IDEAL_FP * 1.246          # about the same -3 dB corner with three poles
    g3 = -30.0 * np.log10(1.0 + (f / fp3) ** 2)
    four = rc.filt_rolloff(IDEAL_FP)(f, ideal_4pole_db(f))
    three = rc.filt_rolloff(IDEAL_FP)(f, g3)
    assert three.ok, three.reason
    assert three.value - four.value > 2.5, (three.value, four.value)


def test_filt_rolloff_refuses_when_the_band_is_not_a_straight_line():
    """`slope_db_oct` refuses a fit whose RMS residual exceeds 1.5 dB, and this
    metric must pass that refusal through rather than quote a slope anyway.
    That refusal is what found our own ladder's quantisation floor: at -60 dBFS
    it fired on every resonant row."""
    f = probe_freqs()
    # Put the knee inside the corrected fit band.  At -30 dB the old band
    # happened to stop before the knee; -20 dB makes the refusal independent of
    # which corner reference positions that band.
    g = np.maximum(ideal_4pole_db(f), -20.0)      # a knee mid-band, not a slope
    e = rc.filt_rolloff(IDEAL_FP)(f, g)
    assert not e.ok
    assert "straight line" in e.reason


def test_filt_lowband_gain_reads_a_known_offset():
    """A gain against the SAME instrument wide open. Offset the whole curve by
    a known -6.0 dB and the metric must read -6.0 and nothing else."""
    f = probe_freqs()
    g = ideal_4pole_db(f)
    e = rc.filt_lowband_gain(IDEAL_FP, 0.0)(f, g - 6.0)
    assert e.ok, e.reason
    # The passband band is 40-62.5 Hz, where an ideal 4-pole with a 250 Hz fp
    # is already a little below 0 dB; that part is the filter, not the offset.
    assert e.value == pytest.approx(-6.0 + float(np.median(
        g[(f >= f[0]) & (f <= max(f[0] * 2.5, IDEAL_FP * 0.25))])), abs=1e-6)


def test_filt_lowband_gain_cancels_a_level_difference_between_instruments():
    """The property the whole metric exists for: add 20 dB of make-up gain to
    BOTH of one instrument's curves and nothing changes. A raw plateau in dBFS
    would move by 20 dB and be reported as a filter difference."""
    f = probe_freqs()
    g = ideal_4pole_db(f)
    a = rc.filt_lowband_gain(IDEAL_FP, 0.0)(f, g)
    b = rc.filt_lowband_gain(IDEAL_FP, 20.0)(f, g + 20.0)
    assert a.ok and b.ok
    assert a.value == pytest.approx(b.value, abs=1e-9)


# ===========================================================================
# The filter case as the BOARD sees it, with no frozen cache on this host.
# ===========================================================================
def test_a_filter_case_without_the_frozen_cache_is_a_stated_no_verdict(tmp_path, monkeypatch):
    """The normal state of most hosts in this fleet. It must be `no verdict`
    with the reason on the record and NO `error` key -- not a zero, and not a
    silent re-render that would quietly redefine what the reference was."""
    import refprofile as rp
    monkeypatch.setattr(rp, "PROFILE_JSON", tmp_path / "gone.json")
    case = next(c for c in rc.load_cases() if c["case_id"] == "F1A")
    res = rc.run_case(case, pathlib.Path("/nonexistent"), keep_audio=False)
    state, _worst, _why = rc.verdict_of(case, res)
    assert state == sb.NO_VERDICT
    assert "REFUSED" in res["note"]
    for m in res["metrics"].values():
        assert m["valid"] is False
        assert "error" not in m


def test_the_filter_plan_never_maps_a_case_to_a_clip_the_profile_lacks():
    """A plan naming a clip that is not in the committed profile would be a
    no-verdict on every host forever, which reads like a capability gap."""
    import refprofile as rp
    real = ROOT / "refprofile" / "profile.json"
    if not real.exists():
        pytest.skip("no committed profile in this tree")
    prof = json.loads(real.read_text())
    for cid, spec in rc.FILTER_CASES.items():
        for key in ("ref_clip", "ref_open_clip"):
            assert spec[key] in prof["clips"], (cid, key, spec[key])


def test_no_case_is_both_planned_and_deliberately_not_run():
    """`plan_for` checks NOT_RUN first, so an id in both tables would be
    silently skipped -- the case would read as deliberately not attempted while
    a working plan for it sat right there."""
    planned = set(rc.DRUM_CASE_VOICE) | set(rc.ENSEMBLE_CASES) | set(rc.FILTER_CASES) | {"M5A"}
    assert not (planned & set(rc.NOT_RUN)), planned & set(rc.NOT_RUN)


def test_m5a_is_a_mono_plan_with_a_frozen_reference():
    """The first qualified Mono case must no longer be reported as not run."""
    case = next(c for c in rc.load_cases() if c["case_id"] == "M5A")
    assert rc.plan_for("M5A") == "mono"
    assert "Mini V3 3.12" in case["reference_target"]
    assert "Envelope release" in case["required_measurements"]


def test_every_not_run_reason_says_something():
    """"not run" and "we forgot" must not be the same entry. A reason under a
    sentence is the second one wearing the first one's label."""
    for cid, why in rc.NOT_RUN.items():
        assert len(why) > 80, (cid, why)


# ===========================================================================
# INVARIANCE (#103). Not expected-value tests: each of these asserts something
# that must hold WHATEVER the right answer is, which is exactly what an
# expected-value test cannot do -- and what would have caught #101 without
# anyone knowing the right answer in advance.
#
#   "#101 found a 6 dB measurement error caused by PREPENDING DIGITAL SILENCE,
#    an operation that cannot possibly change what the machine did. Nothing in
#    the suite could have caught it, because every audio test here compares a
#    number to an expected number. None asserts a PROPERTY."
# ===========================================================================
def _strike(seconds=0.60, lead_ms=10.0, f=220.0, tau=0.040, sr=SR, gain=1.0):
    """A drum-shaped signal: a click on a decaying body, after a lead of true
    digital silence. Long enough that `schroeder_t20`'s length guard is
    satisfied, so the decay metric is a measurement here and not a refusal."""
    n = int(seconds * sr)
    a = int(lead_ms * 1e-3 * sr)
    t = np.arange(n - a) / sr
    x = np.zeros(n)
    x[a:] = (np.exp(-t / tau) * np.sin(2 * math.pi * f * t)
             + 0.05 * np.exp(-t / 0.002) * np.sin(2 * math.pi * 3000.0 * t))
    return gain * x


def _measure_plan(voice, x, sr=SR):
    """Every metric in one voice's plan, through the real path: prepare, then
    the plan's own estimators. What a drum case actually computes."""
    y = rc.prepare(x, sr, side="a synthetic strike")
    out = {}
    for name, _units, est, _tol in rc.DRUM_PLAN[voice]:
        e = est(y, sr)
        out[name] = e.value if e.ok else None
    return out


@pytest.mark.parametrize("pad_ms", [0.5, 5.0, 50.0, 500.0])
def test_every_drum_metric_is_unchanged_by_prepended_silence(pad_ms):
    """**The one that would have caught #101.** Prepending digital silence
    cannot change what the machine did, so it must not change any number.

    Before the lead was guaranteed, `prepare`'s `max(0, onset - 1 ms)` clamp
    made this false by up to 10 dB on the band split: a record whose onset was
    inside the clamp got a 0.16 ms lead and one with silence in front of it got
    1.00 ms, and `sosfiltfilt`'s odd extension reads those two boundaries
    completely differently."""
    base = _strike()
    padded = np.concatenate([np.zeros(int(pad_ms * 1e-3 * SR)), base])
    for voice in ("LC", "SD", "CH"):
        a, b = _measure_plan(voice, base), _measure_plan(voice, padded)
        for k in a:
            assert (a[k] is None) == (b[k] is None), f"{voice} {k}: refusal changed"
            if a[k] is None:
                continue
            scale = max(abs(a[k]), 1.0)
            assert abs(a[k] - b[k]) / scale < 1e-3, \
                f"{voice} {k}: {pad_ms} ms of silence moved it {a[k]:.4f} -> {b[k]:.4f}"


@pytest.mark.parametrize("pad_ms", [5.0, 200.0])
def test_every_drum_metric_is_unchanged_by_appended_silence(pad_ms):
    """The other end. These signals already end in near-silence, so this was
    never the failure -- but a window that ran off the end of the array would
    make it one, and nothing asserted it."""
    base = _strike()
    padded = np.concatenate([base, np.zeros(int(pad_ms * 1e-3 * SR))])
    for voice in ("LC", "SD", "CH"):
        a, b = _measure_plan(voice, base), _measure_plan(voice, padded)
        for k in a:
            if a[k] is None or b[k] is None:
                assert (a[k] is None) == (b[k] is None), f"{voice} {k}: refusal changed"
                continue
            scale = max(abs(a[k]), 1.0)
            assert abs(a[k] - b[k]) / scale < 1e-3, \
                f"{voice} {k}: {pad_ms} ms appended moved it {a[k]:.4f} -> {b[k]:.4f}"


@pytest.mark.parametrize("gain", [1e-3, 0.5, 4.0])
def test_every_ratio_metric_is_unchanged_by_scaling(gain):
    """#103: "scale by a constant -> unchanged, for every ratio metric. A
    level-sensitive ratio is a bug." Every metric in these plans is a dB
    ratio, a time or a frequency, and not one of them may depend on the gain
    the take was recorded at -- which is also the premise of `prepare`'s
    peak normalisation."""
    base = _strike()
    for voice in ("LC", "SD", "CH", "CB", "RS"):
        a, b = _measure_plan(voice, base), _measure_plan(voice, base * gain)
        for k in a:
            if a[k] is None or b[k] is None:
                assert (a[k] is None) == (b[k] is None), f"{voice} {k}: refusal changed"
                continue
            scale = max(abs(a[k]), 1.0)
            assert abs(a[k] - b[k]) / scale < 1e-6, \
                f"{voice} {k}: gain {gain} moved it {a[k]:.6f} -> {b[k]:.6f}"


def test_a_shifted_onset_does_not_move_an_onset_relative_measure():
    """#103: "shift the whole signal by N samples -> unchanged, for any
    onset-relative measure." Every window in section 6 is measured from the
    onset, so moving the strike inside its buffer must be free."""
    a = _measure_plan("LC", _strike(lead_ms=10.0))
    b = _measure_plan("LC", _strike(lead_ms=137.0))
    for k in a:
        assert (a[k] is None) == (b[k] is None), k
        if a[k] is not None:
            assert abs(a[k] - b[k]) / max(abs(a[k]), 1.0) < 1e-3, \
                f"{k}: moving the strike moved it {a[k]:.4f} -> {b[k]:.4f}"


# ===========================================================================
# The lead itself: the thing #101 turned out to be.
# ===========================================================================
def test_the_pad_is_the_bandpass_figure_and_not_the_lowpass_one():
    """#101 and #103 both quote "~12-15 samples, 0.25-0.3 ms at 48 kHz". That
    is a 4th-order LOW-pass: two sections, `padlen` 15. The filter this code
    actually builds is a 4th-order BAND-pass, which is 8th order overall --
    four sections, `padlen` 27. Every lead budget derived from 0.3 ms was half
    what it should have been, and this asserts the number is computed from the
    filter rather than quoted from an issue."""
    lp = rc._sosfiltfilt_padlen(
        __import__("scipy.signal", fromlist=["butter"]).butter(
            4, 400 / (SR / 2), btype="lowpass", output="sos"))
    assert lp == 15, lp
    assert rc.BANDPASS_PADLEN == 27, rc.BANDPASS_PADLEN
    for sr in (44100, 48000):
        assert rc.required_lead_samples(sr) >= 20 * rc.BANDPASS_PADLEN
        assert rc.required_lead_samples(sr) >= 0.010 * sr


@pytest.mark.parametrize("lead_ms", [0.0, 0.16, 1.0, 10.0, 200.0])
def test_prepare_gives_every_record_the_same_lead_whatever_it_arrived_with(lead_ms):
    """The asymmetry itself. A reference that begins at the strike and a render
    that begins with 10 ms of silence must come out of `prepare` with the SAME
    amount of true silence in front of the onset -- that is the whole fix, and
    before it the two sides differed by a factor of six."""
    need = rc.required_lead_samples(SR) + int(round(rc.TRIM_MS * 1e-3 * SR))
    y = rc.prepare(_strike(lead_ms=lead_ms), SR)
    i = rc._onset_index(y)
    assert i == need, f"lead {lead_ms} ms: onset landed at {i}, wanted {need}"
    assert float(np.abs(y[:rc.required_lead_samples(SR)]).max()) < 1e-9, \
        "the lead must be TRUE silence, not merely quiet"


def test_prepare_refuses_a_record_cut_into_the_strike():
    """REFUSE rather than clamp. A record that begins at full amplitude has no
    pre-onset region, and prepending silence to it would manufacture exactly
    the edge the lead exists to avoid -- so this is the one case where the lead
    cannot be supplied and the apparatus has to say so.

    The clamp is what produced #101: it turned a missing precondition into a
    number that looked like every other number."""
    cut = _strike(lead_ms=0.0)[int(0.004 * SR):]        # editor-trimmed into the attack
    with pytest.raises(rc.Refused) as got:
        rc.prepare(cut, SR, side="a clipped reference")
    assert "cut into the strike" in str(got.value)
    assert "a clipped reference" in str(got.value)


def test_the_lead_is_on_the_record_of_every_drum_result():
    """#103 and section 8 row 12: the windowing convention has to be IN the
    result. A number that cannot be re-derived can only be re-trusted."""
    r = rc.lead_report(_strike(lead_ms=0.16), SR)
    assert r["lead_samples"] == rc.required_lead_samples(SR)
    assert r["bandpass_padlen_samples"] == rc.BANDPASS_PADLEN
    assert r["lead_in_padlens"] >= 20.0
    assert r["lead_manufactured_samples"] > 0          # this record could not supply it
    assert rc.lead_report(_strike(lead_ms=200.0), SR)["lead_manufactured_samples"] == 0


# ===========================================================================
# #108: probe measured lines, not nominal ones.
# ===========================================================================
def _two_partials(hz_lo, hz_hi, amp_lo, amp_hi, seconds=0.100, sr=SR, extra=None):
    n = int(seconds * sr)
    t = np.arange(n) / sr
    x = amp_hi * np.sin(2 * math.pi * hz_hi * t) + amp_lo * np.sin(2 * math.pi * hz_lo * t)
    if extra:
        hz, amp = extra
        x = x + amp * np.sin(2 * math.pi * hz * t)
    return x


def test_tone_ratio_db_finds_a_detuned_line_the_nominal_probe_misses():
    """The cowbell's real lines are 558.35 and 823.70 Hz, not the 540 and 800
    the chart gives. Two partials at the REAL frequencies with a known 6 dB
    ratio: probing the nominal frequencies gets it wrong, finding the lines
    gets it right.

    The second assertion is the point -- it records how wrong the nominal probe
    is on this signal, so reinstating it turns this test red."""
    x = _two_partials(558.35, 823.70, amp_lo=0.5, amp_hi=1.0)
    got = rc.tone_ratio_db(x, SR, 800.0, 540.0)
    assert got.ok, got.reason
    assert got.value == pytest.approx(20 * math.log10(1.0 / 0.5), abs=0.2), got.value
    assert abs(got.detail["num_hz"] - 823.70) < 1.0 and abs(got.detail["den_hz"] - 558.35) < 1.0
    nominal = 20 * math.log10(am.tone_amplitude(x, 800.0, SR).require()
                              / am.tone_amplitude(x, 540.0, SR).require())
    assert abs(nominal - got.value) > 1.0, \
        f"the nominal probe read {nominal:.2f} dB against a true {got.value:.2f}"


def test_tone_ratio_db_refuses_rather_than_falling_back_to_nominal():
    """"There is no partial here" and "the partial is exactly where the chart
    says" are opposite findings and must not share a return value."""
    x = _two_partials(558.35, 823.70, 0.5, 1.0)
    assert not rc.tone_ratio_db(x, SR, 3000.0, 540.0).ok


def test_difference_tone_db_probes_the_measured_difference():
    """The difference tone is at f_hi - f_lo of the REAL partials -- 265.35 Hz
    here, not the 260 the nominal pair implies. Plant one 40 dB under the upper
    partial and it must be found at its own level."""
    x = _two_partials(558.35, 823.70, 0.5, 1.0, extra=(823.70 - 558.35, 0.01))
    got = rc.difference_tone_db(x, SR, 800.0, 540.0)
    assert got.ok, got.reason
    assert got.detail["diff_hz"] == pytest.approx(265.35, abs=1.0)
    assert got.value == pytest.approx(20 * math.log10(0.01 / 1.0), abs=0.5), got.value


def test_difference_tone_db_refuses_a_reading_at_its_own_leakage_floor():
    """Two partials 60 dB above the thing being looked for leak into its bin.
    With NO difference tone present the projection still returns a number, and
    reporting that number is the "25 dB of separation that was window leakage"
    failure. The floor is measured from the two partials alone and a reading
    inside 6 dB of it is refused -- the margin `harmonic_signature` already
    uses for the same decision."""
    x = _two_partials(558.35, 823.70, 0.5, 1.0)          # nothing at the difference
    got = rc.difference_tone_db(x, SR, 800.0, 540.0)
    assert not got.ok, f"reported {got.value:.1f} dB of a difference tone that is not there"
    assert "only the window" in got.reason
    assert got.detail["headroom_db"] < rc.FLOOR_MARGIN_DB
