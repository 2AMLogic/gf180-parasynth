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

import inspect
import json
import math
import os
import pathlib
import sys

import numpy as np
import pytest

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT / "model"))

import audio_measure as am                                          # noqa: E402
import partial_trajectory as pt                                     # noqa: E402
import run_case as rc                                               # noqa: E402
import scorecard as sb                                              # noqa: E402

SR = 48000
# The SAME location the runner uses (${GF180_TR808_REFS}, else /tmp/tr808-ref).
# Hard-coding the default here once made these tests skip against an empty
# /tmp/tr808-ref while the runner was measuring a corpus set by the variable.
REFS = rc.configured_refs()


@pytest.fixture
def fischer_refs():
    """Optional locally, REQUIRED where ${GF180_REQUIRE_TR808_REFS}=1: a
    required reference-integration job must REFUSE on a missing corpus rather
    than go green through skips."""
    probe = REFS / "bd8" / "BD5050.WAV"
    if probe.exists():
        return REFS
    msg = (f"the Fischer corpus is not at {REFS} (no {probe.relative_to(REFS)}); "
           f"clone tidalcycles/sounds-tr808-fischer there or set {rc.REFS_ENV}")
    if os.environ.get(rc.REFS_REQUIRED_ENV) == "1":
        pytest.fail(f"REFUSED: {msg}. {rc.REFS_REQUIRED_ENV}=1 makes this a required "
                    f"gate, and a required gate does not pass by skipping.", pytrace=False)
    pytest.skip(f"OPTIONAL local run, skipped: {msg}")


have_refs = pytest.mark.usefixtures("fischer_refs")


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


def test_ref_corner_2x_moves_only_the_reference_axis_not_the_dut_grid(monkeypatch):
    """plan075 4: the DUT's stimulus grid is invariant under the reference-side
    octave mutation. Before the repair our side was rendered on the halved
    axis, which left F1C's rolloff band with 3 points (NO-VERDICT)."""
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
    clean_f, _, clean_meta = rc.load_filter_reference(clip_id)
    shifted_f, _, shifted_meta = rc.load_filter_reference(clip_id, "REF_CORNER_2X")
    assert not np.array_equal(shifted_f, clean_f)                 # the reference moved
    assert np.array_equal(rc.dut_probe_grid(shifted_meta), freqs)  # the DUT did not
    assert np.array_equal(rc.dut_probe_grid(clean_meta), clean_f)


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


# ---------------------------------------------------------------------------
# #115 -- `band_pair_db`'s validated domain, as data, and its refusal outside
# the decay-rate and detuning axes it declares
# ---------------------------------------------------------------------------
def _band_pair_two_tone_lead(f1, f2, tau1, tau2, a1, a2, seconds, *, lead_ms=10.0, sr=SR):
    """Two damped partials, with a TRUE PRE-ONSET LEAD.

    `band_energy` pads with `sosfiltfilt`'s odd extension through the first
    sample, so a segment that begins at full amplitude manufactures an edge
    worth several dB in a sparsely-occupied band -- the same precondition
    `tools/probes/estimator_domains.py`'s `_two_tone` states and guarantees.
    Without it this test would measure that edge and call it the estimator."""
    n = int(seconds * sr)
    t = np.arange(n) / sr
    x = (a1 * np.exp(-t / tau1) * np.sin(2 * math.pi * f1 * t + 0.3)
         + a2 * np.exp(-t / tau2) * np.sin(2 * math.pi * f2 * t + 1.9))
    lead = np.zeros(int(lead_ms * 1e-3 * sr))
    return np.concatenate([lead, x])


def test_band_pair_db_domain_is_inspectable_without_synthesizing_a_signal():
    """The domain's bounds are DATA a test can assert against directly --
    the whole point of #115. `BAND_PAIR_EDGE_MARGIN` is the same +-10 % the
    TR-808's own component tolerance uses elsewhere (`LINE_SEARCH_FRAC`)."""
    detuning = rc.BAND_PAIR_DOMAIN.axis(am.AXIS_DETUNING)
    assert detuning.lo == rc.BAND_PAIR_EDGE_MARGIN == rc.LINE_SEARCH_FRAC == pytest.approx(0.10)
    decay = rc.BAND_PAIR_DOMAIN.axis(am.AXIS_DECAY_RATE)
    assert decay.hi == rc.BAND_PAIR_MAX_DECAY_BIAS_DB == pytest.approx(0.5)
    assert detuning.basis and decay.basis


def test_band_pair_db_refuses_partials_that_decay_at_different_rates():
    """The A^2*tau disease #109 named: a 4:1 tau mismatch between the two
    bands' partials puts far more than `BAND_PAIR_MAX_DECAY_BIAS_DB` of bias
    into a fixed-window ratio, and it must be refused rather than reported as
    a balance."""
    x = _band_pair_two_tone_lead(1800.0, 460.0, 0.006, 0.0015, 1.0, 1.0, 0.30)
    e = rc.band_pair_db(x, SR, (1500, 2100), (380, 560))
    assert not e.ok
    assert e.outside_domain
    assert e.detail["axis"] == am.AXIS_DECAY_RATE
    assert abs(e.detail["decay_bias_db"]) > rc.BAND_PAIR_MAX_DECAY_BIAS_DB
    assert e.domain is rc.BAND_PAIR_DOMAIN
    # Explicitly asking for the out-of-domain case reports it instead:
    forced = rc.band_pair_db(x, SR, (1500, 2100), (380, 560), min_decay_bias_db=None)
    assert forced.ok


def test_band_pair_db_a_matched_decay_pair_is_inside_the_domain():
    """Guard against an overly aggressive refusal check: partials decaying at
    the SAME rate are exactly the case `BAND_PAIR_MAX_DECAY_BIAS_DB` was set
    to admit, and must still report."""
    x = _band_pair_two_tone_lead(1800.0, 460.0, 0.006, 0.006, 1.0, 1.0, 0.30)
    e = rc.band_pair_db(x, SR, (1500, 2100), (380, 560))
    assert e.ok, e.reason
    assert abs(e.detail["decay_bias_db"]) <= rc.BAND_PAIR_MAX_DECAY_BIAS_DB


def test_band_pair_db_refuses_a_partial_sitting_on_a_band_edge():
    """A 4th-order Butterworth is -3 dB at its own edge, so a partial that has
    drifted close to the band edge reads low rather than absent -- a plausible
    but biased number. `edge_margin` refuses it instead, by naming which band
    the offending line sits in."""
    a = sine(2080.0, 0.4, amp=0.5)   # 0.0096 of margin from band_a's 2100 Hz edge
    b = sine(460.0, 0.4, amp=1.0)    # comfortably inside band_b
    n = min(len(a), len(b))
    e = rc.band_pair_db(a[:n] + b[:n], SR, (1500, 2100), (380, 560))
    assert not e.ok
    assert e.outside_domain
    assert e.detail["axis"] == am.AXIS_DETUNING
    assert e.detail["band"] == "a"
    # Explicitly asking for the out-of-domain case reports it instead:
    forced = rc.band_pair_db(a[:n] + b[:n], SR, (1500, 2100), (380, 560), edge_margin=None)
    assert forced.ok


def test_band_pair_db_edge_margin_boundary_does_not_flap():
    axis = rc.BAND_PAIR_DOMAIN.axis(am.AXIS_DETUNING)
    assert axis.violation(rc.BAND_PAIR_EDGE_MARGIN) is None
    assert axis.violation(rc.BAND_PAIR_EDGE_MARGIN - 1e-9) is not None


# ===========================================================================
# Ground truth: balance_trajectory_db (#109)
#
# The disease this replaces: `band_pair_db`/`tone_ratio_db` integrated over a
# SINGLE fixed window, which cannot tell a balance that is falling fast (the
# rimshot's two bridged-T modes decay at very different rates) from one that
# is flat (equal-amplitude partials at the same tau) -- both can average to
# the same one number. Every case here uses a signal whose balance AT ANY
# INSTANT is known in closed form (`ah_true`), independent of the estimator,
# so this is a validated instrument and not one calibrated on itself.
# ===========================================================================
# DERIVED from the operating point that SHIPS, not a second copy of it (#380):
# the guard set is the thing these tests are about, and a hand-copied literal
# here let RS_BALANCE_OP's guards change without a single test noticing.
RS_TEST_OP = {k: v for k, v in rc.RS_BALANCE_OP.items()
              if k not in ("f_lo_range", "f_hi_range")}
RS_TEST_RANGES = (rc.RS_BALANCE_OP["f_lo_range"], rc.RS_BALANCE_OP["f_hi_range"])
# Also derived rather than copied: the floor margin is a DEFAULT of the estimator
# rather than a member of the OP dict, so it has to be read off the signature.
FLOOR_MARGIN_DB = inspect.signature(
    rc.balance_trajectory_db).parameters["floor_margin_db"].default


def _damped(f, tau, amp, n, sr, phase=0.0):
    t = np.arange(n) / sr
    return amp * np.exp(-t / tau) * np.sin(2 * math.pi * f * t + phase)


def _two_partial(f_lo, tau_lo, a_lo, f_hi, tau_hi, a_hi, seconds, sr):
    n = int(seconds * sr)
    return (_damped(f_lo, tau_lo, a_lo, n, sr, 0.3)
            + _damped(f_hi, tau_hi, a_hi, n, sr, 1.9))


def _true_balance_db(t_s, tau_lo, a_lo, tau_hi, a_hi):
    return 20.0 * math.log10((a_hi * math.exp(-t_s / tau_hi))
                             / (a_lo * math.exp(-t_s / tau_lo)))


def test_balance_trajectory_db_domain_is_inspectable_without_synthesizing_a_signal():
    """#115: the second of the three ad-hoc validation measurements the issue
    names (2.4 dB) relocated into `BALANCE_TRAJECTORY_DOMAIN` -- it used to be
    a comment in this file's `RS_BALANCE_OP` block, reachable only by reading
    the comment."""
    snr = rc.BALANCE_TRAJECTORY_DOMAIN.axis(am.AXIS_SNR)
    assert snr.lo == FLOOR_MARGIN_DB
    assert "2.4 dB" in rc.BALANCE_TRAJECTORY_DOMAIN.worst_error
    assert am.DOMAINS["balance_trajectory_db"] is rc.BALANCE_TRAJECTORY_DOMAIN


def test_balance_trajectory_db_reports_two_points_not_one():
    """A rimshot-shaped signal: the high partial starts louder AND decays
    about 4x faster than the low one, so the true balance falls by tens of dB
    within a few ms -- the shape a single integrated window averages away
    (#109's own worked example). `t1`/`balance1` is the metric SCORED;
    `t2`/`balance2`/`slope_db_per_ms` are the SAME record's later instant,
    carried as evidence that this is a trajectory and not a second window."""
    tau_lo, a_lo, tau_hi, a_hi = 0.006, 1.0, 0.0015, 3.0
    x = _two_partial(460.0, tau_lo, a_lo, 1800.0, tau_hi, a_hi, 0.060, SR)
    e = rc.balance_trajectory_db(x, SR, (380, 620), (1450, 2150), **RS_TEST_OP)
    assert e.ok, e.reason
    assert "t1_ms" in e.detail and "t2_ms" in e.detail, \
        "a trajectory needs at least two named instants, not one integrated number"
    t1_s, t2_s = e.detail["t1_ms"] / 1e3, e.detail["t2_ms"] / 1e3
    assert t2_s > t1_s
    truth1 = _true_balance_db(t1_s, tau_lo, a_lo, tau_hi, a_hi)
    truth2 = _true_balance_db(t2_s, tau_lo, a_lo, tau_hi, a_hi)
    assert e.value == pytest.approx(truth1, abs=3.0)
    assert e.detail["balance2_db"] == pytest.approx(truth2, abs=3.0)
    # The whole point: the two instants disagree by far more than the 3 dB
    # tolerance a single window is scored against -- a one-number metric
    # necessarily picks a value between them and is wrong about both ends.
    assert abs(e.detail["balance2_db"] - e.value) > 10.0
    assert e.detail["slope_db_per_ms"] < -1.0            # falling, and fast


def test_balance_trajectory_db_a_flat_balance_reads_flat():
    """Equal decay rates: the true balance is CONSTANT over time. This is the
    cowbell's opposite failure mode from the rimshot's -- a flat trajectory
    must read as flat, not merely as *some* single number."""
    tau, a_lo, a_hi = 0.020, 1.0, 2.0
    x = _two_partial(460.0, tau, a_lo, 1800.0, tau, a_hi, 0.060, SR)
    e = rc.balance_trajectory_db(x, SR, (380, 620), (1450, 2150), **RS_TEST_OP)
    assert e.ok, e.reason
    truth = 20.0 * math.log10(a_hi / a_lo)
    assert e.value == pytest.approx(truth, abs=1.0)
    assert e.detail["balance2_db"] == pytest.approx(truth, abs=1.0)
    assert abs(e.detail["slope_db_per_ms"]) < 0.05
    # And the two signals are told apart, which a single window could not do:
    diverging = _two_partial(460.0, 0.006, 1.0, 1800.0, 0.0015, 3.0, 0.060, SR)
    e_div = rc.balance_trajectory_db(diverging, SR, (380, 620), (1450, 2150), **RS_TEST_OP)
    assert e_div.ok
    assert abs(e_div.detail["slope_db_per_ms"]) > 20 * abs(e.detail["slope_db_per_ms"])


def test_balance_trajectory_db_excludes_a_point_below_the_floor():
    """#92's own pattern: a point measured to be inside the record's floor is
    excluded, not integrated. A tone AT one of the guard frequencies (neither
    partial) for the first 20 ms raises the floor there and nowhere else; the
    earliest reported instant must move past it.

    THE FIXTURE NOW ASSERTS ITS OWN PREMISE, and the burst amplitude is 0.6
    rather than the 5.0 it was written with (#389). "Raises the floor there and
    nowhere else" was a claim this fixture made in prose and violated in fact: a
    5x tone at 1000 Hz throws a 1/df skirt across the whole record, and at the
    +-100 Hz offsets around the LOW partial that `line_is_resolved` reads, the
    burst alone measured 13 to 21 dB BELOW that partial -- comparable to, and at
    the outer offsets louder than, the partial's own skirt, which it scrambled by
    up to 7 dB. #389's line-shape precondition saw that and refused the record,
    correctly: the low partial's neighbourhood in the 5x fixture genuinely does
    not look like an isolated mode, so there was nothing there to take a verdict
    from.

    So the premise is now assertions rather than a sentence, and the amplitude
    was SWEPT rather than guessed -- `balance_line_shape.py fixture` is the
    sweep, so this table is a command and not a transcription:

        A      t1     spill onto 460 Hz   shape residual lo / hi
        0.4   3.0 ms        -36.5 dB        1.09 dB   0.31 dB
        0.6  21.0 ms        -33.0 dB        1.42 dB   0.32 dB
        1.0  21.5 ms        -28.6 dB        2.07 dB   0.39 dB
        5.0  REFUSED        -14.6 dB        3.72 dB   1.56 dB

    0.4 is too quiet to raise the floor past the estimator's own 6 dB gate at
    all, so the fixture stops testing anything; 1.0 leaves the shape residual
    within 6 % of the gate's 2.2 dB tolerance, which is a flake waiting to
    happen. 0.6 is 2.9 dB inside the floor gate and 1.55x inside the shape gate.
    5.0 bought nothing this test asserts and cost the premise it claims.

    WHERE THE PREMISE BAR COMES FROM, which is the review finding on PR #405 and
    the reason this docstring changed. The first version of the assertion
    compared the burst's spill onto each partial against -`FLOOR_MARGIN_DB`
    (-6 dB) -- and EVERY row of that table clears -6 dB, including the 5.0 the
    gate refuses. It was a precondition that read stronger than it was: it could
    not fail on the drift it was written to catch, and what actually failed at
    5.0 was `contaminated.ok`. The floor margin is the wrong scale because the
    gate that notices this contamination is the LINE SHAPE, and the sweep above
    puts its sensitivity about 20 dB below the floor margin: the largest spill
    whose residual still fits inside 2.2 dB is near -28 dB (1.0 reads 2.07 at
    -28.6 dB; interpolating the two rows either side puts the crossing at
    -27.8 dB). `spill_bar_db = -30.0` is 2 dB inside that measured edge and
    3.0 dB clear of the shipping fixture's -33.0, the 5.0 case is carried below
    as the control that the bar is not vacuous, and "nowhere else" is asserted at
    the HIGH partial as a residual rather than as a spill -- because 1000 and
    1800 Hz are both whole numbers of cycles over the burst's own 20 ms, so a
    projection of the burst at 1800 Hz is exactly orthogonal to it and reads
    -316 dB whatever the amplitude. A spill assertion there would be vacuous by
    construction; the residual is not (0.319 dB clean, 0.323 with the burst,
    1.56 at 5.0)."""
    tau, a_lo, a_hi = 0.020, 1.0, 1.0
    x = _two_partial(460.0, tau, a_lo, 1800.0, tau, a_hi, 0.060, SR)
    clean = rc.balance_trajectory_db(x, SR, *RS_TEST_RANGES, **RS_TEST_OP)
    assert clean.ok
    t = np.arange(len(x)) / SR
    burst_end_s = 0.020
    guard_hz = RS_TEST_OP["guards"][0]
    burst = 0.6 * np.sin(2 * math.pi * guard_hz * t) * (t < burst_end_s)
    dirty = x + burst

    # THE PREMISE, ASSERTED, at the scale the SHAPE gate works on rather than at
    # the floor margin -- see the docstring: a -6 dB bar here passes on the 5.0
    # fixture it exists to reject, so it could not have caught the drift it was
    # written for. -30 dB is 2 dB inside the measured edge of the shape gate's
    # own sensitivity (`balance_line_shape.py fixture`).
    spill_bar_db = -30.0
    m = int(0.020 * SR)
    partial_lo = pt.project(x[:m], 460.0, SR)
    spill_lo_db = 20.0 * math.log10(pt.project(burst[:m], 460.0, SR) / partial_lo)
    assert spill_lo_db < spill_bar_db, (
        f"the burst spills onto the low partial at 460 Hz at {spill_lo_db:.1f} dB "
        f"re that partial, past the {spill_bar_db:.0f} dB this fixture is allowed: "
        f"it is then contaminating the partial, not only the guard")
    # AND THE CONTROL THAT THE BAR IS NOT VACUOUS (verification-rules.md rule 2):
    # the 5.0 burst this fixture was written with must FAIL it. The -6 dB bar this
    # replaces passed at 5.0 -- an assertion that holds whatever the fixture does
    # is documentation, not a precondition.
    old_burst = 5.0 * np.sin(2 * math.pi * guard_hz * t) * (t < burst_end_s)
    old_spill_db = 20.0 * math.log10(pt.project(old_burst[:m], 460.0, SR) / partial_lo)
    assert old_spill_db > spill_bar_db, (
        f"the 5.0 burst spills onto the low partial at {old_spill_db:.1f} dB and the "
        f"bar is {spill_bar_db:.0f} dB: the bar no longer rejects the fixture this "
        f"test spent its first life with, so it is not guarding anything")
    assert old_spill_db < -FLOOR_MARGIN_DB, (
        "and this is why the bar is NOT the floor margin, pinned as code rather "
        "than left in prose: the 5.0 fixture clears -6 dB comfortably, so an "
        "assertion set from the floor margin cannot fail on it (review of #405)")
    at_guard = pt.project(burst[:m], guard_hz, SR)
    assert at_guard > pt.project(x[:m], guard_hz, SR) * 10.0, \
        "the burst must be what sets the floor at the guard, or this tests nothing"

    contaminated = rc.balance_trajectory_db(dirty, SR, *RS_TEST_RANGES, **RS_TEST_OP)
    assert contaminated.ok, contaminated.reason
    assert contaminated.detail["t1_ms"] > clean.detail["t1_ms"]
    assert contaminated.detail["t1_ms"] >= burst_end_s * 1e3 - RS_TEST_OP["win_ms"] / 2.0
    # "THERE, AND NOWHERE ELSE" AS A NUMBER: the burst moves the guard's floor by
    # >20 dB (asserted above) and the LOW line's shape residual by ~0.95 dB, and
    # leaves the HIGH line's alone to within 0.005 dB. Read off the accepted
    # verdict's own detail, which is why those fields exist on this path.
    assert contaminated.detail["hi_resid_db"] == pytest.approx(
        clean.detail["hi_resid_db"], abs=0.05), (
        "the burst was supposed to leave the high partial's line shape alone; it "
        f"moved from {clean.detail['hi_resid_db']:.3f} to "
        f"{contaminated.detail['hi_resid_db']:.3f} dB")
    assert contaminated.detail["lo_resid_db"] > clean.detail["lo_resid_db"] + 0.5, (
        "the burst must actually reach the low partial's neighbourhood, or the "
        "spill bar above is satisfied by a fixture that does nothing")
    assert (contaminated.detail["lo_resid_db"]
            < contaminated.detail["line_max_resid_db"])


def test_rs_guards_sit_clear_of_the_strikes_own_broadband_splash():
    """#380: the guard must read the RECORD's floor, not the STRIKE's own splash.

    The control is a struck sound carrying ONLY the low partial -- an onset step
    into one damped sinusoid -- so it has exactly ZERO steady energy at any guard
    frequency and whatever `floor_at` returns there is leakage and nothing else.
    That leakage is not a Hann sidelobe (the same signal STARTED at t=0, with no
    onset step, reads 18 dB lower at 900 Hz): it is the step's broadband splash,
    which falls monotonically with distance from the partial right across the
    900-1200 Hz gap. So the LOWEST guard in the gap always reads the loudest,
    and because `floor_at` takes the MAX over guards, that one member sets the
    floor for the whole set.

    That is what made D10A unmeasurable. #109 chose the gap to avoid exactly
    this failure mode ("a guard below the low partial picked up broadband
    attack-transient leakage") -- the finding here is that at 900 Hz it is still
    inside it, 2.4 dB above where the rest of the gap reads."""
    n = int(0.060 * SR)
    on = int(0.010 * SR)                     # a STRIKE, not a signal already ringing
    x = np.zeros(n)
    x[on:] = _damped(452.0, 0.006, 1.0, n - on, SR, 0.3)
    kw = dict(win_ms=RS_TEST_OP["win_ms"], hop_ms=RS_TEST_OP["hop_ms"],
              t_end=RS_TEST_OP["t_end"])
    _, a_lo = pt.trajectory(x, SR, 452.0, **kw)
    peak = float(a_lo.max())
    at_900 = float(pt.floor_at(x, SR, 452.0, (900.0,), **kw).max())
    shipping = float(pt.floor_at(x, SR, 452.0, RS_TEST_OP["guards"], **kw).max())
    at_900_db = 20.0 * math.log10(at_900 / peak)
    shipping_db = 20.0 * math.log10(shipping / peak)
    assert 900.0 not in RS_TEST_OP["guards"], (
        "a 900 Hz guard reads the strike's splash rather than the record's "
        "floor; #380 moved RS's lower guard off it")
    assert shipping_db <= at_900_db - 2.0, (
        f"the shipping guards {RS_TEST_OP['guards']} read a leakage-only floor "
        f"of {shipping_db:.1f} dB re the low partial, not the >=2 dB below the "
        f"900 Hz reading ({at_900_db:.1f} dB) that #380 measured. A guard this "
        "close to the partial reads the strike, not the floor.")
    # And the splash is the mechanism, not the window's stationary sidelobes:
    # the same partial with no onset step reads far lower at the same frequency.
    ringing = _damped(452.0, 0.006, 1.0, n, SR, 0.3)
    _, a_ring = pt.trajectory(ringing, SR, 452.0, **kw)
    ring_900_db = 20.0 * math.log10(
        float(pt.floor_at(ringing, SR, 452.0, (900.0,), **kw).max())
        / float(a_ring.max()))
    assert ring_900_db < at_900_db - 10.0, (
        "removing the onset step was expected to drop the 900 Hz reading by "
        f">10 dB (splash, not sidelobe); it moved from {at_900_db:.1f} to "
        f"{ring_900_db:.1f} dB")


def test_rs_guards_still_refuse_mid_band_contamination_a_900hz_guard_caught():
    """#380's other half: the retune must not have bought D10A's verdict by
    going blind. A real mid-band component injected into a clean two-partial
    signal at the levels the OLD (900, 1100) guards refused must still be
    refused by the shipping set -- for injection frequencies right across the
    gap, including 900 Hz itself, which no longer has a guard on it.

    Without this, 'the metric reports again' and 'the metric stopped noticing'
    look identical from the scorecard."""
    caught_at_db = {900.0: -15.0, 1000.0: -18.0, 1100.0: -18.0, 1200.0: -15.0}
    tau_lo, tau_hi, a_lo = 0.006, 0.004, 0.5
    a_hi = a_lo * 10.0 ** (-12.0 / 20.0)          # our own render's balance
    n = int(0.060 * SR)
    base = (_damped(452.0, tau_lo, a_lo, n, SR, 0.3)
            + _damped(1795.0, tau_hi, a_hi, n, SR, 1.9))
    clean = rc.balance_trajectory_db(base, SR, *RS_TEST_RANGES, **RS_TEST_OP)
    assert clean.ok, f"the uncontaminated control must report: {clean.reason}"
    for hz, level_db in caught_at_db.items():
        dirty = base + _damped(hz, tau_lo, a_lo * 10.0 ** (level_db / 20.0),
                               n, SR, 0.7)
        old = rc.balance_trajectory_db(
            dirty, SR, *RS_TEST_RANGES,
            **{**RS_TEST_OP, "guards": (900.0, 1100.0)})
        new = rc.balance_trajectory_db(dirty, SR, *RS_TEST_RANGES, **RS_TEST_OP)
        assert not old.ok, (
            f"the control is not a control: (900, 1100) was supposed to refuse "
            f"{level_db:.0f} dB of contamination at {hz:.0f} Hz and did not")
        assert not new.ok, (
            f"the shipping guards {RS_TEST_OP['guards']} REPORTED "
            f"{new.value:+.2f} dB on a record contaminated at {hz:.0f} Hz by "
            f"{level_db:.0f} dB re the low partial, which (900, 1100) refused "
            "-- the retune traded contamination sensitivity for a verdict")


def test_balance_trajectory_db_refuses_white_noise_at_both_operating_points():
    """#389: a record made of NOISE has no partials, so it has no balance --
    and the estimator must say so rather than report one.

    THE BUG THIS PINS, as it shipped (verification-rules.md rule 5 -- the
    control is the exact broken behaviour, not an imagined one). The 6 dB
    joint-headroom gate compared two quantities measured in different ways:
    the partials came from `find_partial`, the STRONGEST line over ~4800 and
    ~14000 grid points -- on noise an upward-biased pick -- while the floor
    came from `floor_at` at FIXED guard frequencies, an unbiased read of the
    same noise. The headroom between them was therefore a selection artifact,
    and on noise it cleared 6 dB at some instant out of the 240 the trajectory
    visits. Every seed below REPORTED a balance at `RS_BALANCE_OP` before this
    test existed -- seed 0 at +4.14 dB, seed 3 at -16.23 dB, seed 7 at
    +5.74 dB -- and seeds 2, 5, 6 and 7 did at `CB_BALANCE_OP`.

    BOTH shipping operating points, because the gate is shared: D10A scores
    `RS_BALANCE_OP` and D13A scores `CB_BALANCE_OP`, and a fix that only knows
    about the rimshot's window is not a fix to the gate.

    The seeds that clear the floor gate must be refused by the NEW precondition
    by name -- otherwise this test could go green on the old code's own floor
    refusals (5 of 8 RS seeds and 4 of 8 CB seeds already refused that way) and
    prove nothing about the hole it is here to close."""
    for label, op, seconds, min_by_line_shape in (
            ("RS_BALANCE_OP", rc.RS_BALANCE_OP, 0.25, 8),
            ("CB_BALANCE_OP", rc.CB_BALANCE_OP, 0.75, 4)):
        kw = {k: v for k, v in op.items() if k not in ("f_lo_range", "f_hi_range")}
        by_line_shape = 0
        for seed in range(8):
            x = 0.01 * np.random.default_rng(seed).standard_normal(int(seconds * SR))
            e = rc.balance_trajectory_db(x, SR, op["f_lo_range"], op["f_hi_range"], **kw)
            assert not e.ok and e.value is None, (
                f"{label} seed {seed}: white noise has no partials, so it has no "
                f"balance -- REPORTED {e.value} dB at t1 "
                f"{e.detail.get('t1_ms')} ms instead of refusing")
            assert ("is not a resolved line" in e.reason
                    or "clears the record's own floor" in e.reason), (
                f"{label} seed {seed}: a refusal must name the precondition that "
                f"failed; got {e.reason!r}")
            by_line_shape += "is not a resolved line" in e.reason
        assert by_line_shape >= min_by_line_shape, (
            f"{label}: only {by_line_shape} of 8 noise seeds were refused by the "
            f"line-shape precondition; the rest went out through the floor gate, "
            f"which already refused them before #389. This test is then green for "
            f"the wrong reason.")


def test_balance_trajectory_db_would_report_noise_again_with_the_gate_DISABLED():
    """The injected-bug control for the test above (verification-rules.md rule 2).

    A green refusal test proves nothing until something has been shown to turn
    it red, and "white noise is refused" has a trivial wrong reason to be green:
    the floor gate might be doing all the work, or the estimator might be
    refusing for a reason that has nothing to do with #389. So RELAX the line
    shape tolerance to infinity -- the one line of the fix, and nothing else --
    and the old behaviour must come back."""
    op = rc.RS_BALANCE_OP
    kw = {k: v for k, v in op.items() if k not in ("f_lo_range", "f_hi_range")}
    real = pt.line_is_resolved
    reported = 0
    try:
        pt.line_is_resolved = lambda *a, **k: real(*a, **{**k, "max_resid_db": math.inf})
        for seed in range(8):
            x = 0.01 * np.random.default_rng(seed).standard_normal(int(0.25 * SR))
            e = rc.balance_trajectory_db(x, SR, op["f_lo_range"], op["f_hi_range"], **kw)
            reported += e.ok
    finally:
        pt.line_is_resolved = real
    assert reported > 0, (
        "with the line-shape tolerance relaxed to infinity, NO noise seed was "
        "reported -- so the refusals the test above observes are not this gate's "
        "doing and that test is green for some other reason")


def test_line_is_resolved_passes_a_damped_mode_at_every_tau():
    """The gate must accept a damped sinusoid across the whole range of decays a
    drum voice presents, from a hat-fast 0.5 ms to two thirds of the window.

    This is the external grounding, and it is the case a shape gate is most
    likely to get wrong: the statistic's closed form is tau-free, so every row
    here has the same right answer for a reason that comes from the DTFT of a
    damped sinusoid and not from anything measured in this repository. An earlier
    draft of this gate (#389, `balance_line_shape.py` wrong-then-right item 3)
    refused the rows below 2 ms, which is where the rimshot's HIGH mode lives."""
    T = RS_TEST_OP["t_end"]
    for tau_ms in (0.5, 1.0, 2.0, 4.0, 8.0, 16.0, 32.0):
        for f in (452.0, 1795.0):
            x = _damped(f, tau_ms * 1e-3, 1.0, int(0.25 * SR), SR, 0.3)
            r = pt.line_is_resolved(x, SR, f, seconds=T)
            assert r["ok"], (f"tau {tau_ms} ms at {f} Hz is a damped mode and the "
                            f"gate refused it: {r['reason']}")
            assert r["tau_ms"] == pytest.approx(tau_ms, rel=0.15), (
                f"the shape fit recovered tau {r['tau_ms']:.2f} ms from a mode "
                f"built with {tau_ms} ms -- the gate passed, but not because it "
                f"measured the right thing")


def test_line_is_resolved_refuses_white_noise():
    """The other side of the same gate, at the unit rather than the estimator.

    8 seeds at both operating points' windows, because a precondition that
    holds on most records is not a precondition. The reason must name the
    statistic and its tolerance, so a reader of a REFUSED verdict can tell
    whether the record was nearly a partial or nowhere near one.

    WHY 8 AND NOT THE 16 THIS WAS WRITTEN WITH, and why the record is no longer
    3x the window (review of PR #405 -- this test cost 81 s of a 600 s CI budget
    and took `make verify-fast`'s first pytest bundle over it):

      - The cost is `find_partial`, and it is grid x samples: 4800 grid points
        (240 Hz at df=0.05) against `seconds` of record, twice per seed. At the
        cowbell's 600 ms window that is 138 M complex projections per seed, and
        it is ~90 % of this test's wall clock. Halving the seeds halves it;
        measured 81.2 s -> 40.6 s on an 8-vCPU worker.
      - The 3x record length was NOT where the time went, which is worth
        recording because it was the first hypothesis. `find_partial` and
        `line_offsets` both slice `x[: int(seconds * sr)]`, so the last two
        thirds of the record were never read: dropping the multiplier leaves the
        analysed samples BIT-IDENTICAL (asserted in this test) and saves only the
        RNG draw, about 1 ms.
      - Seeds 1000..1007 are the same eight records the 16-seed version tested
        first, at the same two windows, so this is a strict subset of what was
        green and not a re-roll onto easier draws. The estimator-level test keeps
        its 8 seeds per operating point, which is where #389's acceptance
        criterion put the >=8 requirement.

    8 seeds x 2 windows is still 16 independent noise records, every one of which
    must be refused; the gate's null distribution is characterised over 96
    partials in `balance_line_shape.py margin`, not here."""
    for seconds in (RS_TEST_OP["t_end"], rc.CB_BALANCE_OP["t_end"]):
        n_read = int(seconds * SR)
        for seed in range(8):
            x = 0.01 * np.random.default_rng(1000 + seed).standard_normal(n_read)
            # the slice both functions below actually read, so "shortening the
            # record changed nothing" is asserted rather than reasoned about
            assert len(x) == n_read
            assert np.array_equal(x, 0.01 * np.random.default_rng(
                1000 + seed).standard_normal(int(3.0 * seconds * SR))[:n_read])
            f = pt.find_partial(x, SR, *RS_TEST_RANGES[0], seconds=seconds)
            r = pt.line_is_resolved(x, SR, f, seconds=seconds)
            assert not r["ok"], (
                f"seed {seed} over {seconds*1e3:.0f} ms: the strongest line white "
                f"noise happens to have at {f:.1f} Hz is not a partial, and the "
                f"gate accepted it (residual {r['resid_db']:.2f} dB, fitted tau "
                f"{r['tau_ms']:.1f} ms)")
            assert "does not have the shape of one" in r["reason"]
            assert "RMS" in r["reason"]


def test_line_is_resolved_refuses_a_line_that_never_decays():
    """A steady tone is a line, and a very clean one, but it is not a STRUCK
    MODE -- so the second condition, that the fitted tau decays inside the
    window, is what has to refuse it. The reason must say so by name rather than
    blaming the shape, because those are different findings about the record."""
    n = int(0.25 * SR)
    x = np.sin(2 * math.pi * 452.0 * np.arange(n) / SR + 0.3)
    r = pt.line_is_resolved(x, SR, 452.0, seconds=RS_TEST_OP["t_end"])
    assert not r["ok"]
    assert "does not decay inside" in r["reason"], r["reason"]
    assert r["tau_ms"] > RS_TEST_OP["t_end"] * 1e3


def test_line_is_resolved_is_blind_to_the_lines_height():
    """The whole reason this gate is on the SHAPE: `find_partial`'s upward bias
    lives entirely in the line's height, so a statistic that moved with the
    height would carry the bias it exists to defeat.

    Scaling a record by 60 dB either way changes every projection and must
    change neither the residual nor the fitted tau -- and must not change the
    verdict on noise either, which is the direction that matters."""
    x = _damped(452.0, 0.006, 1.0, int(0.25 * SR), SR, 0.3)
    base = pt.line_is_resolved(x, SR, 452.0, seconds=RS_TEST_OP["t_end"])
    noise = 0.01 * np.random.default_rng(7).standard_normal(int(0.25 * SR))
    f_n = pt.find_partial(noise, SR, *RS_TEST_RANGES[0], seconds=RS_TEST_OP["t_end"])
    base_n = pt.line_is_resolved(noise, SR, f_n, seconds=RS_TEST_OP["t_end"])
    assert base["ok"] and not base_n["ok"]
    for g in (1e-3, 1e3):
        s = pt.line_is_resolved(g * x, SR, 452.0, seconds=RS_TEST_OP["t_end"])
        assert s["ok"]
        assert s["resid_db"] == pytest.approx(base["resid_db"], abs=1e-9), (
            f"scaling by {g} moved the residual from {base['resid_db']:.6f} to "
            f"{s['resid_db']:.6f} dB: the statistic is reading the height")
        assert s["tau_ms"] == pytest.approx(base["tau_ms"], rel=1e-9)
        sn = pt.line_is_resolved(g * noise, SR, f_n, seconds=RS_TEST_OP["t_end"])
        assert not sn["ok"]
        assert sn["resid_db"] == pytest.approx(base_n["resid_db"], abs=1e-9)


def test_balance_trajectory_db_refuses_when_no_instant_clears_the_floor():
    """Digital silence: no instant anywhere is even 6 dB above its own floor,
    on either partial. REFUSED, not a zero or a coincidental number, and the
    reason names the best margin actually found rather than just 'no'."""
    x = np.zeros(int(0.060 * SR))
    e = rc.balance_trajectory_db(x, SR, (380, 620), (1450, 2150), **RS_TEST_OP)
    assert not e.ok and e.value is None
    assert "clears the record's own floor" in e.reason
    assert "best joint headroom" in e.reason


def test_balance_trajectory_is_what_drum_plan_actually_scores():
    """Check the thing tested is the thing that ships: DRUM_PLAN["RS"]'s own
    "Partial balance" entry, not a hand-called copy of the estimator, must
    itself return the trajectory shape."""
    # DRUM_PLAN stores (name, units, est, tol_rule) tuples, not a mapping.
    est = next(e for (name, _u, e, _t) in rc.DRUM_PLAN["RS"] if name == "Partial balance")
    x = _two_partial(460.0, 0.006, 1.0, 1800.0, 0.0015, 3.0, 0.060, SR)
    e = est(x, SR)
    assert e.ok, e.reason
    assert {"t1_ms", "t2_ms", "balance1_db", "balance2_db", "slope_db_per_ms"} <= e.detail.keys()


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


# ---------------------------------------------------------------------------
# #115 -- `tone_ratio_db`'s validated domain, as data, and the one axis of it
# that was declared but never checked: partial separation
# ---------------------------------------------------------------------------
def test_tone_ratio_db_domain_is_inspectable_without_synthesizing_a_signal():
    """`min_separation_bins` (4) is HALF of `windowed_tone_amplitude`'s own
    docstring claim of 8 -- measured, not assumed (#115); a test can compare
    the two directly from the declaration, with no signal in sight."""
    detuning = rc.TONE_RATIO_DOMAIN.axis(am.AXIS_DETUNING)
    assert (detuning.lo, detuning.hi) == (-rc.LINE_SEARCH_FRAC, rc.LINE_SEARCH_FRAC)
    separation = rc.TONE_RATIO_DOMAIN.axis(am.AXIS_PARTIAL_SEPARATION)
    assert separation.lo == rc.TONE_RATIO_MIN_SEPARATION_BINS == 4.0
    assert am.DOMAINS["tone_ratio_db"] is rc.TONE_RATIO_DOMAIN


def test_tone_ratio_db_refuses_two_lines_inside_one_main_lobe():
    """Two NOMINAL frequencies close enough that both searches land on the
    SAME real line -- one tone at 780 Hz is within +-10 % of both 800 and
    810 Hz -- used to return 0.0 dB with nothing marking it as unresolved. It
    is now refused: zero bins of separation is the extreme case of the
    partial-separation axis, not a balance of two things."""
    x = sine(780.0, 0.2, amp=1.0)
    e = rc.tone_ratio_db(x, SR, 800.0, 810.0)
    assert not e.ok
    assert e.outside_domain
    assert e.detail["axis"] == am.AXIS_PARTIAL_SEPARATION
    assert e.detail["num_hz"] == e.detail["den_hz"]
    assert e.domain is rc.TONE_RATIO_DOMAIN
    # Explicitly asking for the out-of-domain case reports it instead, at 0 dB
    # -- the two searches found the same line, so the "ratio" is of a line
    # against itself:
    forced = rc.tone_ratio_db(x, SR, 800.0, 810.0, min_separation_bins=None)
    assert forced.ok
    assert forced.value == pytest.approx(0.0, abs=1e-6)


def test_tone_ratio_db_partial_separation_boundary_does_not_flap():
    axis = rc.TONE_RATIO_DOMAIN.axis(am.AXIS_PARTIAL_SEPARATION)
    assert axis.violation(rc.TONE_RATIO_MIN_SEPARATION_BINS) is None
    assert axis.violation(rc.TONE_RATIO_MIN_SEPARATION_BINS - 1e-9) is not None


def test_tone_ratio_db_two_resolvable_lines_are_inside_the_domain():
    """Guard against an overly aggressive refusal check: the two known-sines
    case above already covers this, at a separation of hundreds of bins; this
    is the same check named explicitly against the domain."""
    a, b = sine(800.0, 0.2, amp=1.0), sine(540.0, 0.2, amp=0.5)
    n = min(len(a), len(b))
    e = rc.tone_ratio_db(a[:n] + b[:n], SR, 800.0, 540.0)
    assert e.ok, e.reason
    assert e.detail["separation_bins"] >= rc.TONE_RATIO_MIN_SEPARATION_BINS


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


def test_base_check_allows_branch_repair_when_main_only_changed_docs(monkeypatch):
    """Divergence alone cannot turn a deliberate model repair into stale code."""
    def fake_git(*args):
        if args[0] in ("rev-parse", "merge-base"):
            return "deadbeefcafe0000\n"
        if args[0] == "rev-list":
            return "2\n"
        if args[0] == "diff":
            return ""
        if args[0] == "show":
            return "unchanged upstream voice, different from our candidate\n"
        return ""
    monkeypatch.setattr(rc, "_git", fake_git)
    monkeypatch.setattr(rc, "DEPENDENCIES", ("model/voice_fx.py",))
    state = rc.base_check()
    assert not state["problems"]
    assert not state["stale_dependencies"]
    assert "model/voice_fx.py" in state["ahead_dependencies"]


def test_base_check_refuses_diverged_dependency_changed_upstream(monkeypatch):
    def fake_git(*args):
        if args[0] in ("rev-parse", "merge-base"):
            return "deadbeefcafe0000\n"
        if args[0] == "rev-list":
            return "2\n"
        if args[0] == "diff":
            return "model/voice_fx.py\n"
        if args[0] == "show":
            return ("old voice at common ancestor\n" if args[1].startswith("deadbeef") else
                    "a new voice fix on main missing from the candidate\n")
        return ""
    monkeypatch.setattr(rc, "_git", fake_git)
    monkeypatch.setattr(rc, "DEPENDENCIES", ("model/voice_fx.py",))
    with pytest.raises(rc.StaleBase, match="model/voice_fx.py"):
        rc.base_check()


# ===========================================================================
# #129: prose commentary (verdict/why/notes) must not be a hashed input
# ===========================================================================
def test_profile_notes_is_not_a_hashed_input():
    """`refprofile/profile-notes.json` holds the prose split out of
    `refprofile/profile.json` -- verdict/why/readback-caption text -- and must
    never join `DEPENDENCIES` or `MODEL_INPUTS`. Both tuples name only the
    evidence-only file; that absence is the entire fix, so a future edit that
    adds the notes sibling to either tuple would silently reopen #129."""
    assert "refprofile/profile.json" in rc.DEPENDENCIES
    assert "refprofile/profile.json" in rc.MODEL_INPUTS
    assert "refprofile/profile-notes.json" not in rc.DEPENDENCIES
    assert "refprofile/profile-notes.json" not in rc.MODEL_INPUTS


def test_correcting_a_verdict_no_longer_touches_profile_json_so_base_check_passes(
        monkeypatch):
    """Reproduces the incident #129 opens with, after the fix: editing a
    verdict string used to change `profile.json`'s bytes and trip this refusal
    for every measurement checked against its hash. A verdict now lives in
    `profile-notes.json`, which `base_check` never reads, so origin/main's copy
    of `profile.json` and this checkout's are byte-identical even though the
    verdict was just corrected -- and the batch is not refused."""
    real_text = (ROOT / "refprofile" / "profile.json").read_text()

    def fake_git(*args):
        if args[:2] == ("rev-parse", "--verify"):
            return "deadbeefcafe0000\n"
        if args[0] == "rev-list":
            return "0\n"
        if args[0] == "show" and args[1].endswith("refprofile/profile.json"):
            return real_text
        return ""
    monkeypatch.setattr(rc, "_git", fake_git)
    monkeypatch.setattr(rc, "DEPENDENCIES", ("refprofile/profile.json",))
    st = rc.base_check()
    assert st["problems"] == []
    assert "refprofile/profile.json" not in st["stale_dependencies"]


def test_a_changed_hash_in_profile_json_still_refuses_the_batch(monkeypatch):
    """The gate must still catch what matters: `profile.json` is evidence, and
    evidence differing from `origin/main` -- a clip's sha256, a commanded
    parameter, a rig readback -- has to refuse exactly as before the split."""
    def fake_git(*args):
        if args[0] in ("rev-parse", "merge-base"):
            return "deadbeefcafe0000\n"
        if args[0] == "rev-list":
            return "2\n"
        if args[0] == "show":
            return ("the reference at the common ancestor\n" if args[1].startswith("deadbeef")
                    else "a clip hash changed on origin/main, missing from here\n")
        return ""
    monkeypatch.setattr(rc, "_git", fake_git)
    monkeypatch.setattr(rc, "DEPENDENCIES", ("refprofile/profile.json",))
    with pytest.raises(rc.StaleBase, match="refprofile/profile.json"):
        rc.base_check()


def test_the_committed_profile_json_carries_no_prose_run_case_depends_on():
    """The evidence file `run_case.py` actually hashes must not have regrown a
    prose key the #129 split removed -- checked here too, and not only in
    `tools/test_refprofile.py`, because this is the file this module's own
    gate reads."""
    import refprofile as rp
    real = ROOT / "refprofile" / "profile.json"
    if not real.exists():
        pytest.skip("no committed profile in this tree")
    prof = json.loads(real.read_text())
    for name, r in prof.get("rigs", {}).items():
        for k in rp.RIG_PROSE_KEYS:
            assert k not in r, (name, k)
    for cid, c in prof.get("clips", {}).items():
        for k in rp.CLIP_PROSE_KEYS:
            assert k not in c, (cid, k)


def test_run_case_py_is_a_model_input_but_not_a_dependency():
    """The other half of #129: `tools/run_case.py`'s own `NOT_RUN` reason
    strings sit in a file that IS a `MODEL_INPUT` (so correcting one changes
    the hash every future provenance record carries -- a true statement about
    a source file that changed) but is deliberately NOT a `DEPENDENCY` (so that
    same edit never trips `base_check`'s StaleBase refusal, unlike
    `refprofile/profile.json`, whose content-only prose WAS gated by #129
    before the split). Documented as a rationale, not a TODO: this is the
    'accept the difference' branch of #129's third acceptance criterion, and
    it holds only as long as `tools/run_case.py` stays out of `DEPENDENCIES`."""
    assert "tools/run_case.py" in rc.MODEL_INPUTS
    assert "tools/run_case.py" not in rc.DEPENDENCIES


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
    planned = set(rc.DRUM_CASE_VOICE) | set(rc.ENSEMBLE_CASES) | set(rc.FILTER_CASES) | {"M5A", "M5B"}
    assert not (planned & set(rc.NOT_RUN)), planned & set(rc.NOT_RUN)


def test_m5a_is_a_mono_plan_with_a_frozen_reference():
    """The first qualified Mono case must no longer be reported as not run."""
    case = next(c for c in rc.load_cases() if c["case_id"] == "M5A")
    assert rc.plan_for("M5A") == "mono"
    assert "Mini V3 3.12" in case["reference_target"]
    assert "Envelope release" in case["required_measurements"]


def test_m5b_is_a_mono_plan_with_its_frozen_lower_note_reference():
    case = next(c for c in rc.load_cases() if c["case_id"] == "M5B")
    assert rc.plan_for("M5B") == "mono"
    assert "Mini V3 3.12" in case["reference_target"]
    assert all(name in case["required_measurements"] for name in (
        "Pitch", "Harmonic shape", "Foldback energy", "Envelope attack",
        "Envelope release", "Gain", "Clipping"))


def test_mono_reference_summary_skips_unclassified_pulse_notes():
    manifest = {"timeline": {"segments": [{"wave": "pulse", "measurements": [
        {"waveform": "pulse:47.9%"}, {"waveform": None}]}]}}
    assert rc.mono_reference_pulse_mapping(manifest) == "pulse:47.9%"


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


def _pytest_one(test_id: str, env_over: dict) -> "subprocess.CompletedProcess":
    import subprocess
    env = {k: v for k, v in os.environ.items()
           if k not in (rc.REFS_ENV, rc.REFS_REQUIRED_ENV)}
    env.update(env_over)
    return subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
                           "-rs", f"{__file__}::{test_id}"],
                          cwd=ROOT, env=env, capture_output=True, text=True, timeout=300)


def test_a_required_reference_gate_refuses_a_missing_corpus(tmp_path):
    """The gate itself, both ways, against a folder that is certainly empty:
    required -> non-zero with REFUSED; optional -> exit 0, marked OPTIONAL."""
    tid = "test_the_injection_refuses_to_write_onto_the_board"
    empty = {rc.REFS_ENV: str(tmp_path)}
    req = _pytest_one(tid, {**empty, rc.REFS_REQUIRED_ENV: "1"})
    assert req.returncode != 0 and "REFUSED" in req.stdout, req.stdout[-2000:]
    opt = _pytest_one(tid, empty)
    assert opt.returncode == 0 and "OPTIONAL local run" in opt.stdout, opt.stdout[-2000:]


def test_the_tests_read_the_same_corpus_location_as_the_runner(monkeypatch, tmp_path):
    monkeypatch.setenv(rc.REFS_ENV, str(tmp_path))
    assert rc.configured_refs() == tmp_path
    monkeypatch.delenv(rc.REFS_ENV)
    assert rc.configured_refs() == pathlib.Path(rc.REFS_DEFAULT)


# ===========================================================================
# the runner as a SCRIPT -- the thing that ships, not the thing pytest imports
# ===========================================================================
# Every test above reaches `run_case.py` through `import run_case as rc`, so its
# module body executes exactly once under exactly one name. CI does not: it runs
# `python tools/run_case.py <case>`, where the body executes as `__main__` AND
# again as `run_case` via the pre-existing self-import at
# `tools/probes/f1_selected_path.py:68`. #115 put three `register_domain` calls
# in that body, and the registry's unconditional duplicate-raise turned every
# F1 case into `no verdict: ValueError: band_pair_db already declares a domain`
# -- with this file's 356 tests still green, because they never split the two
# module identities. These two tests close that gap: the first is the cheap
# mechanism, the second is the actual entry point.
def _run_script(args: list[str], timeout: int = 600) -> "subprocess.CompletedProcess":
    import subprocess
    return subprocess.run([sys.executable, *args], cwd=ROOT,
                          capture_output=True, text=True, timeout=timeout)


def test_run_case_module_body_survives_executing_twice_in_one_process():
    """The mechanism, in ~2 s: load `run_case.py` a second time under a second
    module name, which is what `__main__` + `import run_case` amounts to. Both
    copies must declare the same domains without raising, and the registry must
    hold ONE entry per estimator."""
    prog = (
        "import importlib.util, pathlib, sys\n"
        "root = pathlib.Path.cwd()\n"
        "sys.path.insert(0, str(root / 'tools'))\n"
        "sys.path.insert(0, str(root / 'model'))\n"
        "import audio_measure as am\n"
        "import run_case as first\n"                      # body execution #1
        "before = dict(am.DOMAINS)\n"
        "path = root / 'tools' / 'run_case.py'\n"
        "spec = importlib.util.spec_from_file_location('run_case_second_copy', path)\n"
        "second = importlib.util.module_from_spec(spec)\n"
        "spec.loader.exec_module(second)\n"               # body execution #2
        "assert set(am.DOMAINS) == set(before), (set(am.DOMAINS) ^ set(before))\n"
        "for name, d in before.items():\n"
        "    assert am.DOMAINS[name] is d, name\n"
        "for n in ('band_pair_db', 'balance_trajectory_db', 'tone_ratio_db'):\n"
        "    assert n in am.DOMAINS, n\n"
        "    assert getattr(second, 'BAND_PAIR_DOMAIN', None) is not None\n"
        "print('TWICE OK')\n")
    got = _run_script(["-c", prog], timeout=300)
    assert got.returncode == 0, (got.stdout + got.stderr)[-3000:]
    assert "TWICE OK" in got.stdout, got.stdout[-2000:]


def test_run_case_script_reaches_a_verdict_on_a_filter_case(tmp_path):
    """The entry point CI actually drives, for real, on the case family that
    broke: `python tools/run_case.py F1A --results <dir>` must reach a MEASURED
    verdict. Deliberately not asserting `pass` -- that is the scorecard's call
    and may legitimately change. What must never come back is `no verdict`
    caused by an exception in the runner's own import path.

    ASSERT THE PRECONDITION, do not assume it. F1A measures against the frozen
    Surge clip `surge-type2/lp-cut250-res0.00`, and a checkout has no
    `refprofile/cache/` -- `reference-controls` restores it as a setup step but
    `m5a-fast`, which runs this file, does not. Without the restore this test
    reads `no verdict` for a reason that has nothing to do with the runner's
    import path, and the first version of it did exactly that: green here on a
    warm worktree, red in CI on a cold one. `refprofile_restore.py` is 0.23 s,
    idempotent, hash-verified against `refprofile/profile.json`, and writes only
    into the gitignored cache, so the fix is to RUN it rather than to skip --
    and to REFUSE loudly if it cannot."""
    restore = _run_script(["tools/refprofile_restore.py"], timeout=300)
    assert restore.returncode == 0, (
        "REFUSED: cannot restore the frozen reference audio F1A measures "
        f"against, so this test cannot tell a runner crash from a missing "
        f"clip:\n{(restore.stdout + restore.stderr)[-2000:]}")

    got = _run_script(["tools/run_case.py", "F1A", "--results", str(tmp_path)])
    out = got.stdout + got.stderr
    assert "already declares a domain" not in out, out[-3000:]
    assert "Traceback" not in out, out[-3000:]
    payload = json.loads((tmp_path / "F1A.json").read_text())
    whys = [m.get("why", "") or "" for m in payload.get("metrics", {}).values()
            if isinstance(m, dict)]
    assert not any("already declares a domain" in w for w in whys), whys
    assert not any("not in the cache" in w for w in whys), (
        f"the restore above reported success yet the clip is still missing: {whys}")
    assert got.returncode in (0, 1), f"exit {got.returncode}\n{out[-3000:]}"
    assert "no verdict" not in got.stdout, (
        "F1A reached no measured verdict as a SCRIPT while the imported-module "
        f"tests above are green -- the #115 regression's exact shape:\n{out[-3000:]}")
