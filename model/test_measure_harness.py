#!/usr/bin/env python3
"""Ground truth for `model/measure_harness.py`.

Every function here is exercised against a case with a known-correct answer,
plus at least one INJECTED-BUG control: a deliberately broken measurement or
apparatus that the check must turn red on, in the style of
`tools/test_measure_conga_body_spread.py`'s
`test_check_fails_an_injected_band_direction_error`. A check that only ever
sees passing input has never been shown to fail on anything.

    .venv/bin/python -m pytest model/test_measure_harness.py -q
"""
from __future__ import annotations

import math
import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import measure_harness as mh


# ---------------------------------------------------------------------------
# 1. validate_known_answer
# ---------------------------------------------------------------------------
def _amplitude_ratio_db(x, sr, hz):
    """A trivial, honest measurement: the dB level of a sinusoid at `hz`,
    read by coherent projection. Used only to give `validate_known_answer`
    something real to measure."""
    n = len(x)
    w = np.exp(-2j * math.pi * hz * np.arange(n) / sr)
    amp = 2.0 * np.abs((x * w).sum()) / n
    return (20.0 * math.log10(amp) if amp > 0 else None), ""


def _known_answer_cases(sr=48000):
    cases = []
    for a in (1.0, 0.5, 0.25, 0.1):
        t = np.arange(int(0.5 * sr)) / sr
        x = a * np.sin(2 * math.pi * 440.0 * t)
        cases.append(dict(case=f"440 Hz @ {a:g}", signal=(x, sr),
                          expected=20 * math.log10(a), kwargs=dict(hz=440.0)))
    return cases


def test_validate_known_answer_matches_a_signal_with_a_known_answer():
    rows = mh.validate_known_answer(_known_answer_cases(), _amplitude_ratio_db)
    assert len(rows) == 4
    assert all(r["why"] == "" for r in rows)
    assert max(abs(r["error_db"]) for r in rows) < 0.05


def test_validate_known_answer_control_catches_a_broken_measurement():
    """INJECTED BUG: the measurement reads the WRONG frequency (a swapped
    band, the same class of defect `tools/test_measure_conga_body_spread.py`
    injects for the conga tool). The control must turn red -- a large error --
    not silently agree."""
    def wrong_frequency(x, sr, hz):
        return _amplitude_ratio_db(x, sr, hz * 2.0)  # reads the wrong line

    rows = mh.validate_known_answer(_known_answer_cases(), wrong_frequency)
    # every case except amplitude 1.0 (a harmonic-free sine has ~nothing at
    # 2x its own frequency) must now report a large error
    assert max(abs(r["error_db"]) for r in rows) > 20.0


def test_validate_known_answer_reports_a_refusal_without_crashing():
    def always_refuses(x, sr, hz):
        return None, "no line found"

    rows = mh.validate_known_answer(_known_answer_cases()[:1], always_refuses)
    assert rows[0]["measured_db"] is None
    assert rows[0]["error_db"] is None
    assert rows[0]["why"] == "no line found"


# ---------------------------------------------------------------------------
# 2. floor_for_these_signals
# ---------------------------------------------------------------------------
def test_floor_for_these_signals_reports_the_signed_change_from_base():
    sr = 48000
    x = np.ones(1000, dtype=np.float64)

    def measure(x, sr):
        return float(x.sum()), ""

    def add_ten(x, sr):
        return float(x.sum()) + 10.0

    def refuses(x, sr):
        return None

    signals = [(dict(label="a"), x, sr, measure,
               dict(plus_ten=add_ten, unmeasurable=refuses))]
    rows = mh.floor_for_these_signals(signals)
    assert len(rows) == 1
    row = rows[0]
    assert row["label"] == "a"
    assert row["base_db"] == 1000.0
    assert row["plus_ten"] == 10.0
    assert row["unmeasurable"] is None


def test_floor_for_these_signals_skips_a_signal_whose_base_refuses():
    def refuses(x, sr):
        return None, "silent"

    signals = [(dict(label="dead"), np.zeros(10), 48000, refuses, {})]
    assert mh.floor_for_these_signals(signals) == []


def test_floor_for_these_signals_control_catches_a_perturbation_that_never_perturbs():
    """INJECTED BUG: a perturbation closure that forgets to apply its own
    change and just re-measures the untouched signal -- a copy-paste error
    this repository has made before (CLAUDE.md: "an unlicensed Diva inserting
    clicks for hours" is the same shape of bug -- the apparatus silently in
    the wrong state). It must read exactly 0.0, and a perturbation that
    actually perturbs must not."""
    sr = 48000
    x = np.full(100, 2.0)

    def measure(x, sr):
        return float(x.sum()), ""

    def real_perturbation(x, sr):
        return float((x * 3).sum())      # actually changes the signal

    def broken_perturbation(x, sr):
        return float(x.sum())            # BUG: ignores that it should perturb

    signals = [(dict(), x, sr, measure,
               dict(real=real_perturbation, broken=broken_perturbation))]
    row = mh.floor_for_these_signals(signals)[0]
    assert row["real"] != 0.0
    assert row["broken"] == 0.0


# ---------------------------------------------------------------------------
# 3. windowed_alike
# ---------------------------------------------------------------------------
def test_windowed_alike_computes_worst_as_the_gap_over_tolerance():
    pairs = [dict(label=dict(voice="X"), tol=2.0,
                  as_shipped=(-10.0, -13.0),
                  variants=dict(aligned=(-10.0, -10.5)))]
    rows = mh.windowed_alike(pairs)
    assert len(rows) == 1
    row = rows[0]
    assert row["voice"] == "X"
    assert row["as_shipped"] == dict(ref_db=-10.0, ours_db=-13.0, worst=1.5)
    assert row["aligned"] == dict(ref_db=-10.0, ours_db=-10.5, worst=0.25)


def test_windowed_alike_control_catches_alignment_that_makes_things_worse():
    """INJECTED BUG: an "aligned" geometry that is wired up backwards (it
    widens the gap it was supposed to close). `windowed_alike` must report
    that faithfully -- a WORSE worst-score than as_shipped -- not silently
    report improvement regardless of what the caller measured."""
    pairs = [dict(label={}, tol=1.0,
                  as_shipped=(0.0, 0.2),
                  variants=dict(backwards=(0.0, 5.0)))]
    row = mh.windowed_alike(pairs)[0]
    assert row["backwards"]["worst"] > row["as_shipped"]["worst"]


# ---------------------------------------------------------------------------
# 4. descent_test
# ---------------------------------------------------------------------------
def _prepare_noop(x, sr):
    return x


def _make_candidate_files(tmp_path, names):
    cand = tmp_path / "candidates"
    cand.mkdir()
    for name in names:
        (cand / name).write_bytes(b"")   # content is irrelevant: read_candidate is overridden
    return cand


def _damped_tone(sr, n, rng):
    """A smooth, audio-like signal (unlike i.i.d. noise, tolerant of the
    off-by-one resample length this algorithm's own floating-point ratio
    sweep can produce right at ratio 1.0 -- exactly what a real recording is
    and pure noise is not)."""
    t = np.arange(n) / sr
    return (np.exp(-t / 0.05) * np.sin(2 * math.pi * 220.0 * t)
            + 0.01 * rng.normal(size=n))


def test_descent_test_flags_a_true_re_pressing():
    rng = np.random.default_rng(0)
    sr = 48000
    ref_signal = _damped_tone(sr, 2000, rng)

    class _Ref:
        name = "REF01.WAV"

    def read_ref(path):
        return ref_signal, sr

    def classify(path):
        return dict(voice="X", refs=[_Ref()])

    def read_candidate(path):
        return ref_signal * 0.7, sr   # same recording, just quieter: a re-pressing

    rows = mh.descent_test(_make_candidate_files_dir(rng), classify, read_ref,
                           _prepare_noop, read_candidate=read_candidate,
                           window_s=2000 / sr)
    assert len(rows) == 1
    assert rows[0]["best_correlation"] >= 0.95
    assert "re-pressing" in rows[0]["verdict"]


def test_descent_test_control_does_not_call_an_unrelated_recording_a_match():
    """INJECTED CONTROL: an independent recording (uncorrelated noise, not a
    re-pressing of anything) must NOT be called a re-pressing. A check that
    always says yes has no discriminating power at all."""
    rng = np.random.default_rng(1)
    sr = 48000
    ref_signal = rng.normal(size=4000)
    unrelated = rng.normal(size=4000)          # independent draw, same class

    class _Ref:
        name = "REF01.WAV"

    def read_ref(path):
        return ref_signal, sr

    def classify(path):
        return dict(voice="X", refs=[_Ref()])

    def read_candidate(path):
        return unrelated, sr

    rows = mh.descent_test(_make_candidate_files_dir(rng), classify, read_ref,
                           _prepare_noop, read_candidate=read_candidate,
                           window_s=4000 / sr)
    assert rows[0]["best_correlation"] < 0.95
    assert rows[0]["verdict"] == "no reference file matches it"


def _make_candidate_files_dir(rng):
    import pathlib
    import tempfile
    d = pathlib.Path(tempfile.mkdtemp()) / "candidates"
    d.mkdir()
    (d / "candidate.wav").write_bytes(b"")
    return d


def test_descent_test_reports_unreadable_files_without_crashing_the_sweep(tmp_path):
    cand = _make_candidate_files(tmp_path, ["broken.wav"])

    def classify(path):
        return dict(voice="X", refs=["placeholder"])

    def read_candidate(path):
        raise OSError("truncated file")

    rows = mh.descent_test(cand, classify, lambda p: (np.zeros(4), 48000),
                           _prepare_noop, read_candidate=read_candidate)
    assert rows[0]["status"] == "unreadable"


def test_descent_test_refuses_outright_without_soundfile(tmp_path, monkeypatch):
    cand = _make_candidate_files(tmp_path, ["a.wav"])
    monkeypatch.setitem(sys.modules, "soundfile", None)
    rows = mh.descent_test(cand, lambda p: dict(voice="X", refs=[]),
                           lambda p: (np.zeros(4), 48000), _prepare_noop)
    assert rows == [dict(status="REFUSED", why="soundfile is not installed")]


# ---------------------------------------------------------------------------
# 5. assert_precondition
# ---------------------------------------------------------------------------
def test_assert_precondition_passes_within_tolerance():
    diff = mh.assert_precondition([1.0, 2.0, 3.0], [1.01, 1.99, 3.02], tol=0.05,
                                  what="test")
    assert diff == pytest.approx(0.02, abs=1e-9)


def test_assert_precondition_control_refuses_past_tolerance():
    """INJECTED BUG: the 'measured' value has drifted well outside tolerance
    -- the exact shape of the emulator-vs-fixed-point-render checks the
    hi-hat probes reimplemented ad hoc. Must REFUSE (raise), not report."""
    with pytest.raises(SystemExit, match="REFUSED"):
        mh.assert_precondition([1.0, 2.0], [1.0, 2.5], tol=0.1, what="emulator fidelity")


def test_assert_precondition_refuses_a_shape_mismatch():
    with pytest.raises(SystemExit, match="shape mismatch"):
        mh.assert_precondition([1.0, 2.0, 3.0], [1.0, 2.0], tol=1.0, what="test")


def test_assert_precondition_refuses_a_non_finite_difference():
    with pytest.raises(SystemExit, match="REFUSED"):
        mh.assert_precondition([float("inf")], [1.0], tol=100.0, what="test")
