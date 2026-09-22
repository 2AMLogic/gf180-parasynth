import numpy as np
import pytest
import json
from scipy.io import wavfile
import measure_mono_m1a_reference as probe


def known_attack_signal(note, phase, span_ms, shape_p=1.0, lp_hz=1056.0,
                        bright=0.0, gate=.6, seconds=1.8):
    """A harmonic-rich bass carrier with an exactly known 10-90% attack span.

    The answer is `span_ms` by construction, independent of both synthesizers.
    `bright` adds a fast-decaying extra harmonic during the attack only, the
    stand-in for a filter-envelope spectral sweep."""
    sr = probe.ref.SR
    t = np.arange(round(seconds * sr)) / sr
    on = .1
    kfrac = 0.9 ** (1 / shape_p) - 0.1 ** (1 / shape_p)
    ramp_s = (span_ms / 1000.) / kfrac
    env = np.clip((t - on) / ramp_s, 0, 1) ** shape_p
    off = on + gate
    rel = t >= off
    env[rel] = np.exp(-(t[rel] - off) / .1)
    hz = 440 * 2 ** ((note - 69) / 12)
    car = probe.saw_bass(t - phase / hz, hz, lp_hz) \
        + .535 * probe.saw_bass(t - phase / hz, 2 * hz, lp_hz)
    if bright:
        car = car + bright * np.sin(2 * np.pi * 20 * hz * t) * (t >= on) \
            * np.exp(-np.maximum(t - on, 0) / .010)
    return env * car, on, off


def test_bass_attack_fit_resolves_known_spans_and_shapes():
    """Ground truth: the answer is known independently of what is measured."""
    cases = [(36, 0., 8., 1.0, 1056., 0.), (43, .5, 8., 1.0, 1056., 0.),
             (36, .25, 1., 1.0, 1056., 0.), (43, .75, 2., 1.0, 1056., 0.),
             (36, .5, 16., 1.0, 1056., 0.), (43, 0., .5, 1.0, 1056., 0.),
             (36, .5, 8., .5, 1056., 0.), (43, 0., 8., 2.0, 1056., 0.),
             (36, 0., 8., 1.0, 400., 0.), (43, .5, 8., 1.0, 3000., 0.),
             (36, .25, 8., 1.0, 1056., .3), (43, .75, 8., 1.0, 1056., .6)]
    errors = []
    for note, phase, span, shape, lp, bright in cases:
        audio, on, off = known_attack_signal(note, phase, span, shape, lp, bright)
        row = probe.attack_fit(audio, probe.ref.SR, 440 * 2 ** ((note - 69) / 12), on, off)
        assert row["valid"], row
        errors.append(abs(row["attack_10_90_ms"] - span))
        assert row["explained_ratio"] >= .95, row
    assert max(errors) < .5, f"known-signal attack error {max(errors):.2f} ms"


def test_bass_attack_fit_refuses_inputs_it_cannot_measure():
    sr = probe.ref.SR
    good, on, off = known_attack_signal(36, 0., 8.)
    with pytest.raises(probe.ref.Refused, match="silent"):
        probe.attack_fit(np.zeros_like(good), sr, 65.4, on, off)
    rng = np.random.default_rng(7)
    with pytest.raises(probe.ref.Refused, match="periodic"):
        probe.attack_fit(rng.standard_normal(len(good)) * .1, sr, 65.4, on, off)
    short = good[:round((on + .04) * sr)]
    with pytest.raises(probe.ref.Refused):
        probe.attack_fit(short, sr, 65.4, on, off)
    with pytest.raises(probe.ref.Refused, match="truncate"):
        probe.attack_fit(good[:round((on + .2) * sr)], sr, 65.4, on, off)


def test_rms_windows_cannot_measure_the_fast_bass_attack():
    """The injected-wrong control stays red: the estimators that made M1A's
    attack no-verdict must FAIL these same known signals, or the control is
    not able to catch the defect it exists for."""
    for window_ms, label in ((probe.ENVELOPE_WINDOW_MS, "40 ms bass window"),
                             (5.0, "5 ms lead window")):
        errors = []
        for note, phase in ((36, 0.), (36, .5), (43, .25), (43, .75)):
            audio, on, off = known_attack_signal(note, phase, 8.)
            env = probe.ref.am.rms_envelope(audio, ms=window_ms, sr=probe.ref.SR)
            timing = probe.ref.envelope_timing(env, probe.ref.SR, on, off)
            assert timing["valid"], label
            errors.append(abs(timing["attack_10_90_ms"] - 8.))
        assert min(errors) > 5.0, f"{label} unexpectedly measured the attack: {errors}"


def test_bass_measurement_uses_known_pitch_and_release():
    sr = probe.ref.SR
    t = np.arange(round(probe.SECONDS * sr)) / sr
    audio = np.zeros_like(t)
    for e in probe.EVENTS:
        dt = t - e["on_s"]
        env = np.clip(dt / .03, 0, 1)
        off = dt >= e["gate_s"]
        env[off] = np.exp(-(dt[off] - e["gate_s"]) / .10)
        hz = 440 * 2 ** ((e["note"] - 69) / 12)
        audio += .1 * env * np.sin(2 * np.pi * hz * t)
    rows = probe.event_measurements(audio)
    assert len(rows) == 3
    assert max(abs(row["pitch_cents"]) for row in rows) < .5
    assert all(abs(row["envelope"]["release_t20_ms"] - 100 * np.log(10)) < 10 for row in rows)
    with pytest.raises(probe.ref.Refused):
        probe.event_measurements(np.zeros_like(audio))


@pytest.mark.parametrize("note", [36, 43])
@pytest.mark.parametrize("phase", [0, .25, .5, .75])
def test_bass_release_window_is_qualified_across_carrier_phase(note, phase):
    sr = probe.ref.SR
    t = np.arange(round(1.8 * sr)) / sr
    envelope = np.clip((t - .1) / .03, 0, 1)
    envelope[t >= .7] = np.exp(-(t[t >= .7] - .7) / .1)
    hz = 440 * 2 ** ((note - 69) / 12)
    audio = envelope * np.sin(2 * np.pi * (hz * t + phase))
    measured = probe.ref.am.rms_envelope(audio, ms=probe.ENVELOPE_WINDOW_MS, sr=sr)
    timing = probe.ref.envelope_timing(measured, sr, .1, .7)
    assert timing["valid"] and timing["release_complete_40db"]
    assert abs(timing["release_t20_ms"] - 100 * np.log(10)) < 10


def test_frozen_bass_reference_reproduces_without_a_plugin():
    directory = probe.ROOT / "docs/scorecard/mono-m1a-miniv3"
    manifest = json.loads((directory / "manifest.json").read_text())
    for artifact in [*manifest["renders"], *manifest["controls"].values()]:
        assert probe.ref.sha256(directory / artifact["file"]) == artifact["sha256"]
    sr, audio = wavfile.read(directory / manifest["renders"][0]["file"])
    assert sr == probe.ref.SR
    observed = probe.event_measurements(audio)
    for got, expected in zip(observed, manifest["renders"][0]["events"]):
        assert got["f0_hz"] == pytest.approx(expected["f0_hz"], abs=1e-5)
        assert got["rms_dbfs"] == pytest.approx(expected["rms_dbfs"], abs=1e-5)
        assert got["envelope"]["release_t20_ms"] == expected["envelope"]["release_t20_ms"]
        assert got["envelope"]["release_valid"] is True
        assert got["envelope"]["attack_valid"] is True
        assert got["envelope"]["attack"]["attack_10_90_ms"] > 0
    # the three frozen repeats are byte-identical, so the fit must be too
    sr2, audio2 = wavfile.read(directory / manifest["renders"][1]["file"])
    observed2 = probe.event_measurements(audio2)
    for a, b in zip(observed, observed2):
        assert a["envelope"]["attack"]["attack_10_90_ms"] == \
            b["envelope"]["attack"]["attack_10_90_ms"]


def test_attack_qualification_carries_ground_truth_and_red_control():
    qualification = probe.qualify_attack_basis()
    assert qualification["valid"] is True
    assert qualification["max_known_error_ms"] < .5
    assert qualification["window_control"]["min_error_ms"] > 5.0
    assert qualification["window_control"]["window_ms"] == probe.ENVELOPE_WINDOW_MS
    assert qualification["known_signal_count"] >= 40
