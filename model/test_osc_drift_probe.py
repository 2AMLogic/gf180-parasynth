#!/usr/bin/env python3
"""Controls for `model/osc_drift_probe.py`.

The probe exists to answer "how much do these oscillators drift". Every number
it can produce is worthless unless these pass, and they are written in the
order `docs/verification-rules.md` asks for:

  1. it must NOT fire on a signal that does not drift        (negative control)
  2. it must NOT fire on a statically-detuned mix, which     (issue #138's
     moves and beats while every partial is constant)         retraction)
  3. it must NOT call periodic modulation drift              (vibrato is not
                                                               drift)
  4. it MUST recover a KNOWN injected wander, in cents,      (known answer)
     and must track it monotonically over magnitude
  5. it must say BOUNDED vs RANDOM WALK correctly            (the issue asks)
  6. it must REFUSE, not answer, when its preconditions fail

The known answers here are constructed from an exact phase integral
(`fm_tone`), so the ground truth is independent of everything the probe does.
"""
from __future__ import annotations

import math
import os
import sys

import numpy as np
import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import osc_drift_probe as odp                                        # noqa: E402

SR = 48000
F0 = 130.8127826502993          # MIDI 48; a bass note, where drift is audible


# ---- 1. the estimator does not invent drift --------------------------------
def test_perfectly_stable_tone_is_stable():
    x = odp.fm_tone(SR, 8.0, F0, np.zeros(64), harmonics=6)
    r = odp.drift_report(x, SR, F0, label="stable")
    assert r["verdict"] == odp.STABLE, r
    assert r["drift_rms_cents"] < 3.0 * r["floor_cents"]
    # and the floor itself must be small enough for the measurement to be
    # capable of the magnitudes this issue cares about (~1 cent)
    assert r["floor_cents"] < 0.05, r["floor_cents"]


def test_stable_tone_f0_is_recovered_exactly():
    x = odp.fm_tone(SR, 6.0, F0, np.zeros(64), harmonics=4)
    r = odp.drift_report(x, SR, F0)
    assert abs(r["f0_offset_cents"]) < 0.05, r["f0_offset_cents"]


# ---- 2. THE control of issue #138: static detune beats but does not drift ---
@pytest.mark.parametrize("detune", [(0.0, 7.0, -4.0), (0.0, 12.0, -12.0),
                                    (0.0, 3.0, 5.0)])
def test_static_detune_mix_is_not_drifting(detune):
    """Three PERFECTLY STABLE oscillators at fixed offsets. The mix's zero
    crossings move, its envelope moves, its waveform never repeats on a short
    view -- and nothing in it drifts. A DRIFTING verdict here would mean every
    drift number this probe has ever produced is a beat measurement."""
    x = odp.static_detune_mix(SR, 20.0, F0, detune_cents=detune, harmonics=4)
    r = odp.drift_report(x, SR, F0, label=f"static-detune{detune}")
    assert r["verdict"] != odp.DRIFTING, r
    assert r["verdict"] in (odp.PERIODIC, odp.REFUSED), r
    if r["verdict"] == odp.REFUSED:
        # WINDOW_SPANS_SILENCE is the code all three of these actually take,
        # and it was missing here at first: THREE equal-gain partials beat
        # through a near-total null, so the fundamental's level moves 37.9,
        # 44.4 and 50.1 dB across the window -- past LEVEL_HEADROOM_DB (30),
        # which is tested before the MULTI_PARTIAL ripple. The two-oscillator
        # mix of test_refuses_a_mix_outright moves only 19.1 dB and so does
        # reach MULTI_PARTIAL. Both are the probe declining to put a pitch
        # number on a window it cannot defend, which is what this control
        # demands; the teeth are the two assertions around this one (never
        # DRIFTING, and no magnitude emitted), not the choice of code.
        assert r["refusal"] in ("MULTI_PARTIAL", "SHORT_FOR_WOBBLE",
                               "WINDOW_SPANS_SILENCE"), r
    assert "drift_rms_cents" not in r or r["verdict"] == odp.PERIODIC


@pytest.mark.parametrize("dur", [1.4, 3.0, 8.0, 30.0])
def test_weak_partner_beating_is_periodic_not_drift(dur):
    """A partner 26 dB down ripples the band by under 1 dB, below the
    MULTI_PARTIAL refusal, so it reaches the classifier -- and must be called
    BEATING there, at EVERY window length, because the frequency wobble tracks
    the ripple exactly (r = 1.00 measured) however little of one beat cycle the
    window holds. At 1.4 s this reported DRIFTING at 0.2 cents until the
    envelope and the deviation were detrended the same way."""
    x = odp.static_detune_mix(SR, dur, F0, detune_cents=(0.0, 7.0),
                              gains=(1.0, 0.05), harmonics=4)
    r = odp.drift_report(x, SR, F0, label=f"weak-partner-{dur}s")
    assert r["env_ripple_db"] < odp.MULTI_PARTIAL_DB, r["env_ripple_db"]
    assert r["verdict"] == odp.PERIODIC, r
    assert r.get("periodic_kind") == "beating", r


def test_static_detune_actually_moves_the_naive_reading():
    """The control is only a control if the thing it guards against is real:
    the raw (unclassified) slow deviation on the static-detune mix must be
    LARGE, comparable with the drift magnitudes this issue targets. If it were
    small, passing test #2 would prove nothing."""
    x = odp.static_detune_mix(SR, 20.0, F0, detune_cents=(0.0, 7.0, -4.0),
                              harmonics=4)
    r = odp.drift_report(x, SR, F0)
    naive = r.get("drift_rms_cents")
    if naive is None:                      # refused before measuring: read it
        t, f, _ = odp.het_series(x, SR, F0)   # straight off the trajectory
        naive = float(np.std(odp.cents(f, float(np.median(f)))))
    assert naive > 0.3, ("the static-detune control does not move enough to "
                         f"be a control ({naive} cents)")


# ---- 3. vibrato is not drift ------------------------------------------------
def test_sinusoidal_fm_is_periodic_not_drift():
    t = np.linspace(0, 8.0, 4000)
    dev = 8.0 * np.sin(2 * math.pi * 0.7 * t)          # 0.7 Hz, +-8 cents
    x = odp.fm_tone(SR, 8.0, F0, dev, harmonics=4)
    r = odp.drift_report(x, SR, F0, label="vibrato")
    assert r["verdict"] == odp.PERIODIC, r
    assert r["repeat_r"] > odp.REPEAT_R, r["repeat_r"]


# ---- 4. the known answer: a bounded wander of KNOWN rms --------------------
@pytest.mark.parametrize("rms", [0.5, 1.0, 2.0, 4.0])
def test_known_ou_wander_is_recovered(rms):
    dev = odp.ou_cents(4000, 500.0, rms_cents=rms, tau_s=1.0, seed=7)
    x = odp.fm_tone(SR, 8.0, F0, dev, harmonics=6)
    r = odp.drift_report(x, SR, F0, label=f"ou-{rms}c")
    assert r["verdict"] == odp.DRIFTING, r
    got = r["drift_rms_cents"]
    assert 0.6 * rms < got < 1.4 * rms, (rms, got, r)


def test_recovered_magnitude_is_monotone_in_the_truth():
    got = []
    for rms in (0.25, 0.5, 1.0, 2.0, 4.0):
        dev = odp.ou_cents(4000, 500.0, rms_cents=rms, tau_s=1.0, seed=11)
        x = odp.fm_tone(SR, 8.0, F0, dev, harmonics=6)
        got.append(odp.drift_report(x, SR, F0)["drift_rms_cents"])
    assert all(b > a for a, b in zip(got, got[1:])), got


# ---- 5. bounded vs random walk ---------------------------------------------
def test_bounded_and_unbounded_are_distinguished():
    dev_b = odp.ou_cents(4000, 500.0, rms_cents=3.0, tau_s=0.25, seed=3)
    rng = np.random.default_rng(5)
    dev_w = np.cumsum(rng.standard_normal(4000))
    dev_w = 3.0 * dev_w / np.std(dev_w)
    rb = odp.drift_report(odp.fm_tone(SR, 8.0, F0, dev_b, harmonics=6), SR, F0)
    rw = odp.drift_report(odp.fm_tone(SR, 8.0, F0, dev_w, harmonics=6), SR, F0)
    assert rb["walk_exponent"] < rw["walk_exponent"], (rb["walk_exponent"],
                                                       rw["walk_exponent"])
    assert rw["walk_exponent"] > 0.6, rw["walk_exponent"]


# ---- 6. the refusals -------------------------------------------------------
def test_refuses_a_short_window():
    x = odp.fm_tone(SR, 0.05, F0, np.zeros(8))
    r = odp.drift_report(x, SR, F0)
    assert r["verdict"] == odp.REFUSED and r["refusal"] == "TOO_FEW_PERIODS", r
    assert "drift_rms_cents" not in r


def test_refuses_a_declared_rate_mismatch():
    x = odp.fm_tone(SR, 4.0, F0, np.zeros(8))
    r = odp.drift_report(x, SR, F0, sr_expected=44100)
    assert r["verdict"] == odp.REFUSED and r["refusal"] == "SR_MISMATCH", r


def test_refuses_silence():
    r = odp.drift_report(np.zeros(SR * 4), SR, F0)
    assert r["verdict"] == odp.REFUSED, r
    assert r["refusal"] in ("LOW_LEVEL", "NO_TRACK", "TOO_FEW_PERIODS"), r


def test_refuses_a_mix_outright():
    """The probe measures ONE oscillator. A mix of two comparable partials
    ripples the fundamental's envelope by 18 dB, which one oscillator cannot
    do, and that is a refusal with its own code rather than a number."""
    x = odp.static_detune_mix(SR, 8.0, F0, detune_cents=(0.0, 7.0),
                              gains=(1.0, 0.8), harmonics=4)
    r = odp.drift_report(x, SR, F0)
    assert r["verdict"] == odp.REFUSED and r["refusal"] == "MULTI_PARTIAL", r
    assert "drift_rms_cents" not in r


@pytest.mark.parametrize("slope", [2.0, 5.0, 20.0])
def test_refuses_a_monotone_ramp(slope):
    """A window holding ONE monotone excursion carries no evidence that the
    wander is aperiodic -- it is equally the first quarter of something slow
    and periodic. A mean-removed ramp's own correlation time is dur/4.86
    whatever its slope, under this probe's MIN_TAUS = 5, so it refuses at every
    magnitude rather than reporting one it cannot defend. This is also the
    reason a drift measurement here needs SECONDS of held tone, not one note."""
    x = odp.fm_tone(SR, 8.0, F0, np.linspace(0.0, slope, 4000), harmonics=6)
    r = odp.drift_report(x, SR, F0)
    assert r["verdict"] == odp.REFUSED, r
    assert r["refusal"] == "SHORT_FOR_WOBBLE", r
    assert r["taus_in_window"] < odp.MIN_TAUS, r
    assert "drift_rms_cents" not in r


# ---- the across-note measurement -------------------------------------------
def test_across_note_drift_reads_a_pitch_step():
    a = odp.drift_report(odp.fm_tone(SR, 3.0, F0, np.zeros(8), harmonics=4),
                         SR, F0, label="first")
    b = odp.drift_report(odp.fm_tone(SR, 3.0, F0 * 2 ** (2.0 / 1200),
                                     np.zeros(8), harmonics=4),
                         SR, F0, label="second")
    across = odp.across_note_drift([a, b])
    assert across["verdict"] == odp.DRIFTING, across
    assert abs(across["span_cents"] - 2.0) < 0.2, across


def test_across_note_drift_is_stable_on_two_identical_notes():
    rs = [odp.drift_report(odp.fm_tone(SR, 3.0, F0, np.zeros(8), harmonics=4),
                           SR, F0, label=f"n{i}") for i in range(3)]
    across = odp.across_note_drift(rs)
    assert across["verdict"] == odp.STABLE, across


def test_across_note_refuses_without_two_measured_windows():
    bad = odp.drift_report(np.zeros(SR), SR, F0)
    across = odp.across_note_drift([bad])
    assert across["verdict"] == odp.REFUSED, across


@pytest.mark.parametrize("bad_window", ["mixed", "silent"])
def test_cli_across_note_requires_all_requested_pitches(tmp_path, bad_window):
    """A single capture of two valid notes and an unusable third window, read
    back through the CLI's JSON. `mixed` is a LATE refusal: the probe writes a
    pitch before it decides the window holds two oscillators, and the
    comparison used to spend that pitch -- 2.658 cents of DRIFTING read off
    three constant-frequency tones. `silent` is an EARLY refusal, which used to
    vanish: STABLE over two of the three requested windows (#1405)."""
    import json

    from scipy.io import wavfile

    t = np.arange(3 * SR) / SR
    tone = 0.4 * np.sin(2 * np.pi * F0 * t)
    mix = tone + 0.32 * np.sin(2 * np.pi * F0 * 2 ** (7 / 1200) * t)
    third = mix if bad_window == "mixed" else np.zeros_like(tone)
    capture = tmp_path / "notes.wav"
    report = tmp_path / "report.json"
    wavfile.write(capture, SR, np.concatenate([tone, tone, third]).astype("float32"))
    args = ["--wav", str(capture), "--sr-expected", str(SR), "--f0", str(F0),
            "--window", "0:3", "--window", "3:6", "--json", str(report)]
    assert odp.main(args) == 0
    clean = json.loads(report.read_text())
    assert [r["verdict"] for r in clean["reports"]] == [odp.STABLE, odp.STABLE]
    assert clean["across_note"]["verdict"] == odp.STABLE

    odp.main(args + ["--window", "6:9"])
    observed = json.loads(report.read_text())
    rejected = observed["reports"][2]
    assert rejected["verdict"] == odp.REFUSED
    assert rejected["refusal"] == ("MULTI_PARTIAL" if bad_window == "mixed" else "LOW_LEVEL")
    across = observed["across_note"]
    assert across["verdict"] == odp.REFUSED, across
    assert across["refusal"] == rejected["refusal"], across
    assert across["unusable_window"] == 2, across
    assert across["n_windows"] == 3, across
    assert "span_cents" not in across, across


# ---- the across-note comparison: one unusable window, wherever it sits ------
def _held(dur_s: float = 3.0) -> np.ndarray:
    return odp.fm_tone(SR, dur_s, F0, np.zeros(8), harmonics=4)


def _weak_fundamental() -> np.ndarray:
    t = np.arange(int(8.0 * SR)) / SR
    return 0.02 * np.sin(2 * math.pi * F0 * t) \
        + 0.4 * np.sin(2 * math.pi * 2 * F0 * t)


def _non_finite() -> np.ndarray:
    x = _held()
    x[SR + 1000:SR + 1010] = np.nan
    return x


REFUSED_WINDOWS = [
    # id, how the window is built, the code, and whether `drift_report` still
    # wrote `f0_measured_hz` on it before refusing
    ("silent", lambda: np.zeros(3 * SR), "LOW_LEVEL", False),
    ("short", lambda: _held(0.05), "TOO_FEW_PERIODS", False),
    ("non-finite", _non_finite, "NO_TRACK", False),
    ("mix", lambda: odp.static_detune_mix(SR, 8.0, F0, detune_cents=(0.0, 7.0),
                                          gains=(1.0, 0.8), harmonics=4),
     "MULTI_PARTIAL", True),
    ("monotone-ramp",
     lambda: odp.fm_tone(SR, 8.0, F0, np.linspace(0.0, 5.0, 4000),
                         harmonics=6),
     "SHORT_FOR_WOBBLE", True),
    ("note-then-silence",
     lambda: np.concatenate([_held(4.0), np.zeros(2 * SR)]),
     "WINDOW_SPANS_SILENCE", True),
    ("octave-partner", _weak_fundamental, "ESTIMATOR_DISAGREE", True),
]

_MISSING = object()
MALFORMED = [
    ("pitch-nan", "f0_measured_hz", math.nan),
    ("pitch-inf", "f0_measured_hz", math.inf),
    ("pitch-zero", "f0_measured_hz", 0.0),
    ("pitch-negative", "f0_measured_hz", -F0),
    ("pitch-absent", "f0_measured_hz", _MISSING),
    ("floor-nan", "floor_cents", math.nan),
    ("floor-inf", "floor_cents", math.inf),
    ("floor-negative", "floor_cents", -1.0),
    ("floor-absent", "floor_cents", _MISSING),
    ("verdict-absent", "verdict", _MISSING),
    ("verdict-unknown", "verdict", "OK"),
    ("verdict-empty", "verdict", None),
]


@pytest.fixture(scope="module")
def good():
    return [odp.drift_report(_held(), SR, F0, label=f"n{i}") for i in range(2)]


@pytest.fixture(scope="module")
def refused():
    return {name: odp.drift_report(build(), SR, F0, label=name)
            for name, build, _, _ in REFUSED_WINDOWS}


@pytest.mark.parametrize("at", [0, 1, 2])
@pytest.mark.parametrize("name, code, carries_pitch",
                         [(n, c, p) for n, _, c, p in REFUSED_WINDOWS],
                         ids=[n for n, _, _, _ in REFUSED_WINDOWS])
def test_across_note_refuses_naming_the_unusable_window(good, refused, name,
                                                        code, carries_pitch, at):
    """A refused window at ANY position refuses the comparison with that
    window's own code and index, whether or not it carries a pitch. `armed` is
    the adversarial form: the same refused window given a pitch nine cents off
    the held notes and a floor of zero, so that admitting it would read
    DRIFTING and a missing floor would read as no floor at all."""
    bad = refused[name]
    assert (bad["verdict"], bad["refusal"]) == (odp.REFUSED, code), bad
    assert ("f0_measured_hz" in bad) == carries_pitch, bad
    armed = dict(bad, f0_measured_hz=F0 * 2 ** (9.0 / 1200.0), floor_cents=0.0)
    for window in (bad, armed):
        across = odp.across_note_drift(good[:at] + [window] + good[at:])
        assert across["verdict"] == odp.REFUSED, across
        assert across["refusal"] == code, across
        assert across["unusable_window"] == at, across
        assert (across["n_windows"], across["labels"][at]) == (3, name), across
        assert not ({"span_cents", "cents_vs_first", "floor_cents"}
                    & across.keys()), across


@pytest.mark.parametrize("name, key, value", MALFORMED)
def test_across_note_refuses_a_window_with_no_usable_pitch_or_floor(good, name,
                                                                    key, value):
    """The comparison's own preconditions, on a window that carries a verdict:
    a finite positive pitch and a finite non-negative floor. A NaN span
    compares False against every floor and a missing floor defaulted to zero,
    so each of these used to be compared as STABLE or DRIFTING."""
    assert odp.across_note_drift(good + [good[0]])["verdict"] == odp.STABLE
    bad = dict(good[0], label=name)
    if value is _MISSING:
        bad.pop(key)
    else:
        bad[key] = value
    across = odp.across_note_drift(good + [bad])
    assert across["verdict"] == odp.REFUSED, across
    assert across["refusal"] == "UNUSABLE_WINDOW", across
    assert across["unusable_window"] == 2, across
    assert not ({"span_cents", "cents_vs_first", "floor_cents"}
                & across.keys()), across


def test_across_note_uses_a_window_that_reported_drifting_or_periodic(good):
    """A reported verdict qualifies a window; STABLE is not required. A window
    whose pitch wanders is still one oscillator with a mean f0, and the frozen
    Mini V3 pair `m1a-osc2-midi48-4.0s-apart` is DRIFTING in one of its two
    windows. Its own verdict is not the comparison's to re-derive."""
    wander = odp.ou_cents(4000, 500.0, rms_cents=1.0, tau_s=1.0, seed=7)
    vibrato = 8.0 * np.sin(2 * math.pi * 0.7 * np.linspace(0.0, 8.0, 4000))
    reports = [odp.drift_report(odp.fm_tone(SR, 8.0, F0, dev, harmonics=h),
                                SR, F0, label=label)
               for dev, h, label in ((wander, 6, "drifting"),
                                     (vibrato, 4, "periodic"))]
    assert [r["verdict"] for r in reports] == [odp.DRIFTING, odp.PERIODIC], reports
    across = odp.across_note_drift([good[0]] + reports)
    assert across["n_windows"] == 3, across
    assert across["verdict"] in (odp.STABLE, odp.DRIFTING), across
    assert math.isfinite(across["span_cents"]), across


def test_across_note_accepts_any_iterable_of_windows(good):
    across = odp.across_note_drift(iter(good))
    assert across["verdict"] == odp.STABLE, across
    assert across["n_windows"] == 2, across
