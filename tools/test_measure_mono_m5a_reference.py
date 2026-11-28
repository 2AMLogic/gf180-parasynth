"""Ground-truth tests for the M5A waveform-level envelope measurement."""
from __future__ import annotations

import json
import pathlib

import numpy as np
import pytest
from scipy.io import wavfile

import tools.measure_mono_m5a_reference as m
from tools.measure_mono_m5a_reference import CASES, envelope_timing


SR = 48_000
REFERENCE = pathlib.Path(__file__).resolve().parents[1] / "docs/scorecard/mono-m5a-miniv3"


def test_m5a_reference_case_keeps_its_frozen_note_map_and_timing():
    case = CASES["M5A"]
    assert [event["note"] for event in case["events"]["saw"]] == [84, 96]
    assert case["segment_seconds"] == 13.5
    assert case["requires_waveform_at_every_note"] is False


def test_m5b_reference_is_a_lower_lead_with_a_complete_phrase_and_release():
    case = CASES["M5B"]
    assert [event["note"] for event in case["events"]["saw"]] == [72, 84]
    assert case["events"]["pulse"] == case["events"]["saw"]
    phrase_end = max(e["on_s"] + e["gate_s"] for e in case["events"]["saw"])
    assert 4.0 <= phrase_end <= 8.0
    assert case["segment_seconds"] > phrase_end
    assert case["segment_seconds"] - phrase_end >= 2.0
    assert abs(case["segment_seconds"] - phrase_end - 2.8) < 1e-9
    assert case["requires_waveform_at_every_note"] is True


def test_envelope_timing_matches_a_piecewise_linear_attack_and_exponential_release():
    on_s, off_s, attack_s, tau_s = 0.1, 0.7, 0.05, 0.1
    t = np.arange(int(2.0 * SR)) / SR
    env = np.zeros_like(t)
    attack = (t >= on_s) & (t < on_s + attack_s)
    env[attack] = (t[attack] - on_s) / attack_s
    sustain = (t >= on_s + attack_s) & (t < off_s)
    env[sustain] = 1.0
    release = t >= off_s
    env[release] = np.exp(-(t[release] - off_s) / tau_s)

    got = envelope_timing(env, SR, on_s, off_s)
    assert got["valid"]
    assert abs(got["attack_10_90_ms"] - 40.0) <= 1.0
    assert abs(got["release_t20_ms"] - tau_s * np.log(10) * 1000) <= 1.0
    assert got["release_complete_40db"]


def test_envelope_timing_refuses_silence_and_incomplete_release():
    silence = envelope_timing(np.zeros(1000), SR, 0.002, 0.010)
    assert silence == {"valid": False, "why": "silent during gate"}

    # A held signal with no post-gate decay cannot be reported as a release.
    held = np.ones(SR)
    got = envelope_timing(held, SR, 0.1, 0.5)
    assert not got["valid"]
    assert got["why"] == "20 dB release threshold not reached"


def _held_then(level):
    """An envelope at `level`, gated to 1.0 from 0.1 s to 0.7 s."""
    env = np.full(int(2.0 * SR), float(level))
    env[:int(0.1 * SR)] = 0.0
    env[int(0.1 * SR):int(0.7 * SR)] = 1.0
    return env


def test_exact_silence_tail_is_a_complete_release_with_no_tail_db():
    got = envelope_timing(_held_then(0.0), SR, 0.1, 0.7)
    assert got["valid"] and got["release_complete_40db"]
    assert got["tail_db"] is None
    assert json.loads(json.dumps(got))["tail_db"] is None


@pytest.mark.parametrize("level, expected_db", [(1e-16, -320.0), (1e-17, -340.0)])
def test_tail_below_the_old_floor_is_its_own_number(level, expected_db):
    got = envelope_timing(_held_then(level), SR, 0.1, 0.7)
    assert got["tail_db"] == pytest.approx(expected_db, abs=1e-6)
    assert got["release_complete_40db"]


def test_tail_at_the_old_floor_keeps_its_number():
    got = envelope_timing(_held_then(1e-15), SR, 0.1, 0.7)
    assert got["tail_db"] == pytest.approx(-300.0, abs=1e-6)


@pytest.mark.parametrize(
    "level, expected", [(0.0, None), (1e-16, -320.0), (1e-15, -300.0)])
def test_clearance_before_the_next_note_is_no_number_only_for_exact_silence(level, expected):
    got = m.pre_next_note_clearance_db(_held_then(level), 1.5, 1.0)
    if expected is None:
        assert got is None
    else:
        assert got == pytest.approx(expected, abs=1e-6)


def _saw_segment():
    manifest = json.loads((REFERENCE / "manifest.json").read_text())
    segment = manifest["timeline"]["segments"][0]
    assert segment["wave"] == "saw"
    sr, audio = wavfile.read(REFERENCE / "m5a-miniv3-raw.wav")
    assert sr == SR
    a, n = segment["offset_samples"], segment["samples"]
    return np.asarray(audio[a:a + n], dtype=np.float64)


def test_reference_release_is_exact_silence_and_is_reported_as_no_number():
    """The committed Mini V3 reference releases to exact digital silence. That
    was published as -283.80 dB, which is 20*log10(1e-15 / held_rms): the
    floor, not a reading."""
    segment = _saw_segment()
    before_next = int(5.7 * SR)
    assert not np.count_nonzero(segment[before_next - int(0.040 * SR):before_next])
    assert not np.count_nonzero(segment[-int(0.050 * SR):])
    first, second = m._event_analysis(segment, "saw", m.M5A_EVENTS, classify_every_note=False)
    for record in (first, second):
        assert record["envelope"]["release_complete_40db"] is True
        assert record["envelope"]["tail_db"] is None
    assert first["envelope"]["pre_next_note_clearance_db"] is None


def test_incomplete_release_still_refuses_with_its_tail_number():
    segment = _saw_segment()
    segment[-int(0.050 * SR):] = 0.01
    with pytest.raises(m.Refused, match=r"release is not complete to -40 dB: tail -[0-9.]+ dB"):
        m._event_analysis(segment, "saw", m.M5A_EVENTS, classify_every_note=False)


def test_summary_lines_print_exact_silence_and_numbers(tmp_path, capsys, monkeypatch):
    def measurement(note, tail_db):
        return {"note": note, "f0_hz": 1046.5888, "f0_cents": 0.143, "gain_rms_dbfs": -19.19,
                "inharmonic_db": -50.02,
                "envelope": {"attack_10_90_ms": 7.3333, "release_t20_ms": 1243.229,
                             "tail_db": tail_db}}
    data = {
        "audio": {"sha256": "0" * 64},
        "timeline": {"segments": [{"wave": "saw",
                                   "measurements": [measurement(84, None), measurement(96, -45.6)]}]},
        "qualification": {"wrong_then_right": {
            "wrong_then_right_rate": "1/4 cutoff measurements",
            "cutoff_calibration": {
                "discarded": {"host_block_size_samples": 512, "measured_hz": 8000.0},
                "accepted": {"host_block_size_samples": 16, "measured_hz": 9000.0}}}},
    }
    monkeypatch.setattr(m, "capture", lambda *args, **kwargs: {
        "manifest": tmp_path / "manifest.json", "audio": tmp_path / "reference.wav", "data": data})
    assert m.main(["--out", str(tmp_path)]) == 0
    lines = capsys.readouterr().out.splitlines()
    silent, = (line for line in lines if "MIDI 84" in line)
    numbered, = (line for line in lines if "MIDI 96" in line)
    assert silent.endswith("R20 1243.2 ms, tail exact silence")
    assert numbered.endswith("R20 1243.2 ms, tail -45.6 dB")
