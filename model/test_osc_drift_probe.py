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

import json
import math
import os
import sys

import numpy as np
import pytest
from scipy.io import wavfile

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


# ---- 7. a corrupt sample, and an export a strict parser accepts ------------
NONFINITE = [np.nan, np.inf, -np.inf]


def _reject_constant(name):
    raise ValueError(f"non-standard JSON token {name}")


def _read_strict(path):
    """Parse the file as a strict parser does. RFC 8259 has no NaN and no
    Infinity; `json.loads` accepts all three unless told otherwise, which is
    how the bare NaN in #1406 went unnoticed."""
    with open(path) as fh:
        return json.load(fh, parse_constant=_reject_constant)


def _steady_tone(dur_s):
    return odp.fm_tone(SR, dur_s, F0, np.zeros(8), harmonics=4)


def _as_filed(x):
    """The samples `read_wav` hands `drift_report` for a float32 file of `x`."""
    return np.asarray(x, dtype=np.float32).astype(np.float64)


def _float32_wav(tmp_path, name, x):
    path = tmp_path / f"{name}.wav"
    wavfile.write(str(path), SR, np.asarray(x, dtype=np.float32))
    return str(path)


def _cli_json_strict(wav, dest, *more):
    argv = ["--wav", wav, "--f0", repr(F0), "--json", str(dest)] + list(more)
    return odp.main(argv), _read_strict(dest)


def _no_label(report):
    return {k: v for k, v in report.items() if k != "label"}


def _outcome(report):
    return report["verdict"], report.get("refusal")


@pytest.mark.parametrize("bad", NONFINITE)
def test_a_nonfinite_sample_refuses_before_any_level_or_pitch(bad):
    """`rms <= 0` is False for a NaN rms (IEEE, #134), so one NaN used to walk
    past the LOW_LEVEL guard. The verdict was still REFUSED -- NO_TRACK, from
    the band-pass the NaN poisoned -- but the report carried `rms` and `peak`
    as NaN. A corrupt recording refuses under its own code, first."""
    x = _steady_tone(3.0)
    x[SR + 1000] = bad
    r = odp.drift_report(x, SR, F0)
    assert _outcome(r) == (odp.REFUSED, "NONFINITE_INPUT"), r
    for k in ("rms", "peak", "f0_measured_hz", "f0_offset_cents", "floor_cents",
              "drift_rms_cents", "walk_exponent"):
        assert k not in r, (k, r)
    assert "at sample 49000" in r["refusal_detail"], r


def test_digital_silence_is_low_level_not_nonfinite():
    """The distinction #337 has to read off a report: a silent oscillator has
    a level, zero; a corrupt recording has none."""
    r = odp.drift_report(np.zeros(SR * 3), SR, F0)
    assert _outcome(r) == (odp.REFUSED, "LOW_LEVEL"), r
    assert (r["rms"], r["peak"]) == (0.0, 0.0), r


@pytest.mark.parametrize("bad", NONFINITE)
def test_a_corrupt_sample_refuses_wherever_it_sits(bad):
    """The issue placed one non-finite sample at each of 145 positions and got
    435 NO_TRACK refusals. The code must now come from the input, not from
    wherever the poisoned band-pass happened to fail -- at any position, in an
    all-corrupt window, and in one too short to hold a period."""
    x = _steady_tone(3.0)
    for i in (0, 1, SR // 2, SR + 1000, 2 * SR, len(x) - 2, len(x) - 1):
        y = x.copy()
        y[i] = bad
        r = odp.drift_report(y, SR, F0)
        assert _outcome(r) == (odp.REFUSED, "NONFINITE_INPUT"), (i, r)
        assert "rms" not in r, (i, r)
    for n in (SR * 3, SR // 2, 64):
        r = odp.drift_report(np.full(n, bad), SR, F0)
        assert _outcome(r) == (odp.REFUSED, "NONFINITE_INPUT"), (n, r)


def test_a_corrupt_sample_outside_the_window_leaves_a_clean_one_clean():
    """The guard reads the SELECTED window: a NaN at 1.02 s must not refuse
    the held tone that follows it."""
    x = _steady_tone(3.0)
    x[SR + 1000] = np.nan
    r = odp.drift_report(x[int(1.5 * SR):], SR, F0)
    assert r["verdict"] == odp.STABLE, r


def test_cli_refuses_a_corrupt_wav_with_a_strict_json_file(tmp_path):
    """The issue's own reproduction: the same tone as a float32 WAV with one
    NaN at sample 49000. At 4b4c4935 the CLI exited 0 with NO_TRACK and a file
    holding a bare NaN in both `rms` and `peak`."""
    x = _steady_tone(3.0)
    x[SR + 1000] = np.nan
    rc, doc = _cli_json_strict(_float32_wav(tmp_path, "corrupt", x),
                               tmp_path / "corrupt.json")
    assert rc == 0
    r = doc["reports"][0]
    assert _outcome(r) == (odp.REFUSED, "NONFINITE_INPUT"), r
    assert "rms" not in r and "peak" not in r, r


@pytest.mark.parametrize("name, dur_s", [("clean", 3.0), ("short", 0.5)])
def test_cli_exports_a_clean_tone_complete_and_strict(tmp_path, name, dur_s):
    """Both clean controls keep their STABLE verdict and every available
    measurement. On the short one `walk_exponent` is UNAVAILABLE (fewer than
    three usable lags) and leaves as null; strict encoding alone refused this
    accepted measurement."""
    x = _steady_tone(dur_s)
    rc, doc = _cli_json_strict(_float32_wav(tmp_path, name, x),
                               tmp_path / f"{name}.json")
    assert rc == 0
    got = _no_label(doc["reports"][0])
    mem = _no_label(odp.drift_report(_as_filed(x), SR, F0))
    assert got["verdict"] == mem["verdict"] == odp.STABLE, got
    assert got.keys() == mem.keys()
    for k in got:
        if k == "walk_exponent" and math.isnan(mem[k]):
            assert got[k] is None, k
        else:
            assert got[k] == mem[k], (k, got[k], mem[k])
    assert [k for k, v in got.items() if v is None] == (
        ["walk_exponent"] if dur_s == 0.5 else []), got


def test_cli_checks_the_selected_window_not_the_whole_file(tmp_path):
    """`--window` selects what the guard reads: one corrupt file refuses the
    window that holds the NaN and measures the one that does not."""
    x = _steady_tone(3.0)
    x[SR + 1000] = np.nan
    wav = _float32_wav(tmp_path, "windows", x)
    rc, doc = _cli_json_strict(wav, tmp_path / "windows.json",
                               "--window", "0:1.1", "--window", "1.5:3.0")
    assert rc == 0
    a, b = doc["reports"]
    assert _outcome(a) == (odp.REFUSED, "NONFINITE_INPUT"), a
    assert b["verdict"] == odp.STABLE, b


INJECTED = [(f, b) for f in ("rms", "drift_rms_cents", "f0_measured_hz")
            for b in NONFINITE]
INJECTED += [("walk_exponent", np.inf), ("walk_exponent", -np.inf)]


@pytest.mark.parametrize("field, bad", INJECTED)
def test_an_unexpected_non_finite_number_fails_the_export_before_touching_the_file(
        tmp_path, monkeypatch, field, bad):
    """The writer on its own: finite input, a real report, ONE number
    replaced, through the CLI's own call. It raises naming the field, leaves
    an existing destination exactly as it was, and does not create one. An
    infinity under a known diagnostic is unexpected too; only NaN there means
    unavailable."""
    broken = dict(odp.drift_report(_steady_tone(3.0), SR, F0))
    assert math.isfinite(broken[field])
    broken[field] = bad
    monkeypatch.setattr(odp, "drift_report", lambda *a, **k: dict(broken))
    wav = _float32_wav(tmp_path, "held", _steady_tone(0.5))
    pattern = rf"reports\[0\]\.{field}\b"

    dest = tmp_path / "existing.json"
    dest.write_text("an earlier report")
    with pytest.raises(ValueError, match=pattern):
        odp.main(["--wav", wav, "--f0", repr(F0), "--json", str(dest)])
    assert dest.read_text() == "an earlier report"

    fresh = tmp_path / "new" / "report.json"
    with pytest.raises(ValueError, match=pattern):
        odp.main(["--wav", wav, "--f0", repr(F0), "--json", str(fresh)])
    assert not fresh.exists() and not fresh.parent.exists()


def test_the_export_checks_the_across_note_block_too(tmp_path, monkeypatch):
    """The whole payload is held to the standard, not only the window
    reports."""
    monkeypatch.setattr(odp, "across_note_drift",
                        lambda reports: {"verdict": odp.STABLE,
                                         "span_cents": float("nan")})
    dest = tmp_path / "dest.json"
    dest.write_text("an earlier report")
    with pytest.raises(ValueError, match=r"across_note\.span_cents"):
        _cli_json_strict(_float32_wav(tmp_path, "held", _steady_tone(0.5)), dest)
    assert dest.read_text() == "an earlier report"


def test_every_unavailable_diagnostic_leaves_as_null(tmp_path, monkeypatch):
    """The allowlist, exercised at the real assignment sites: each helper is
    made to answer "unavailable" on a real tone, so every key `drift_report`
    can hold NaN under goes through the CLI and must come back null with the
    rest of the report unchanged. `env_corr` is the one key this cannot reach
    -- it needs a deviation with exactly zero scatter -- and is listed by
    reading the code."""
    nan = float("nan")
    monkeypatch.setattr(odp, "_walk_exponent", lambda d, rate: nan)
    monkeypatch.setattr(odp, "_dominant_wobble", lambda d, rate: (nan, nan))
    monkeypatch.setattr(odp, "_acorr_time", lambda d, rate: nan)
    ou = odp.ou_cents(4000, 500.0, rms_cents=3.0, tau_s=1.0, seed=7)
    tones = (("stable", _steady_tone(3.0)),
             ("wander", odp.fm_tone(SR, 8.0, F0, ou, harmonics=6)))
    wavs = [_float32_wav(tmp_path, n, x) for n, x in tones]
    dest = tmp_path / "all.json"
    argv = [arg for wav in wavs for arg in ("--wav", wav)]
    rc = odp.main(argv + ["--f0", repr(F0), "--json", str(dest)])
    assert rc == 0
    doc = _read_strict(dest)
    assert [r["verdict"] for r in doc["reports"]] == [odp.STABLE, odp.REFUSED]
    for got, (_, x) in zip(doc["reports"], tones):
        mem = _no_label(odp.drift_report(_as_filed(x), SR, F0))
        want = {k: (None if k in odp.OPTIONAL_DIAGNOSTICS
                    and isinstance(v, float) and math.isnan(v) else v)
                for k, v in mem.items()}
        assert _no_label(got) == want, got["verdict"]
    nulled = {k for r in doc["reports"] for k, v in r.items() if v is None}
    assert nulled == odp.OPTIONAL_DIAGNOSTICS - {"env_corr"}, nulled


def test_the_strict_encoder_alone_still_refuses_a_nan(tmp_path, monkeypatch):
    """`allow_nan=False` is the floor under the known-diagnostic conversion:
    with that conversion switched off, the short tone's NaN `walk_exponent`
    must still fail the export rather than reach the file."""
    monkeypatch.setattr(odp, "_for_export", lambda payload: payload)
    dest = tmp_path / "dest.json"
    dest.write_text("an earlier report")
    with pytest.raises(ValueError):
        _cli_json_strict(_float32_wav(tmp_path, "short", _steady_tone(0.5)),
                         dest)
    assert dest.read_text() == "an earlier report"


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
