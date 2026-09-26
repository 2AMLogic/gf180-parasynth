"""What the ensemble I2S scorer must refuse, and that its metrics are not inert.

The second half matters more than the first. A scorer that validates its inputs
strictly and then computes a number no defect can move is the shape of wrong
answer this repository keeps producing, so `test_event_timing_is_not_inert` and
`test_bus_balance_sees_a_mix_that_is_not_the_sum` drive the two metrics that
could plausibly be blind and require them to MOVE.
"""
import hashlib
import json
import wave

import numpy as np
import pytest

import score_ensemble_i2s as score
import verify_synth_top as vst

SR = score.SR
CASE_FRAMES = 32_000


# --------------------------------------------------------------------------- #
# fixtures: a report, a WAV and a schedule that bind to each other
# --------------------------------------------------------------------------- #
def _write_wav(path, samples) -> str:
    y = np.clip(np.asarray(samples), -32768, 32767).astype("<i2")
    with wave.open(str(path), "wb") as fh:
        fh.setnchannels(1)
        fh.setsampwidth(2)
        fh.setframerate(SR)
        fh.writeframes(y.tobytes())
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _schedule(part, *, case_id="E1A", stops=None, case_frames=CASE_FRAMES):
    return {"case_id": case_id, "part": part, "patch": "01-bass-classic", "dense": False,
            "bpm": 124.0, "bars": 2, "seconds": 6.0, "case_frames": case_frames,
            "bus_gain": 0.45, "gains": dict(zip(("vol", "dvol", "bvol"),
                                                vst.ensemble_part_gains(part))),
            "scheduled_hits": 42, "voice_notes": 8, "i2s_delay_periods": 1,
            "sample_rate_hz": SR, "worst_latency_frames": 3,
            "worst_latency_stop": "BD", "worst_latency_intended_frame": 2400,
            "stops": stops or {}}


def _report(*, case_id="E1A", part="mix", wav_sha, schedule_sha, periods,
            gains=None, simulator="verilator", latency=3, pass_line=True,
            verified=True, extra=""):
    vol, dvol, bvol = gains if gains is not None else vst.ensemble_part_gains(part)
    lines = [
        f"verify_synth_top: ENSEMBLE {case_id} part {part}: patch 01-bass-classic, sparse "
        f"groove, 124 BPM, 2 bars, 6.000 s ({CASE_FRAMES} frames), no limiter",
        f"verify_synth_top: {case_id} ensemble gains: vol={vol}, dvol={dvol}, bvol={bvol}; "
        f"8 voice notes and 42 drum hits over 8 stop(s): BD, CB, CH, CP, HT, LT, OH, SD",
        f"verify_synth_top: simulator backend {simulator}",
        f"verify_synth_top: {case_id} ensemble part {part}: register schedule from the CS_N "
        f"pin, 42 raises; worst host-to-register latency {latency:+d} frame(s) "
        f"({1000.0 * latency / SR:+.3f} ms) on BD at intended frame 2400",
        f"verify_synth_top: ensemble schedule sha256 {schedule_sha}",
        f"verify_synth_top: decoded I2S WAV sha256 {wav_sha}",
    ]
    if pass_line:
        lines.append(f"verify_synth_top: PASS -- {periods} I2S periods decoded from the wire, "
                     f"every one identical to the model; both channels agree; every slot "
                     f"32 BCLK; the core's own stream matches too")
    if verified:
        lines.append(f"verify_synth_top: {case_id} ensemble part {part} verified from SPI pins "
                     f"through the COMBINED voice and drum paths to the I2S pins")
    if extra:
        lines.append(extra)
    return "\n".join(lines) + "\n"


def _part_files(tmp_path, part, *, samples=None, schedule=None, **report_kw):
    samples = np.zeros(CASE_FRAMES, dtype=np.int64) if samples is None else samples
    paths = score.part_paths(tmp_path, part)
    wav_sha = _write_wav(paths["wav"], samples)
    schedule = schedule or _schedule(part)
    paths["schedule"].write_text(json.dumps(schedule, indent=1, sort_keys=True))
    schedule_sha = hashlib.sha256(paths["schedule"].read_bytes()).hexdigest()
    text = _report(part=part, wav_sha=wav_sha, schedule_sha=schedule_sha,
                   periods=len(samples), **report_kw)
    paths["report"].write_text(text)
    return paths, text, wav_sha, schedule_sha


# --------------------------------------------------------------------------- #
# the stimulus decides the part list, not a restated constant
# --------------------------------------------------------------------------- #
def test_parts_come_from_the_stimulus_the_bench_will_send():
    parts = score.parts_for("E1A")
    assert parts[:4] == list(score.STEM_PARTS)
    # the eight stops of PATTERN_808, read out of the same script that is sent
    assert sorted(parts[4:]) == ["stop:BD", "stop:CB", "stop:CH", "stop:CP",
                                 "stop:HT", "stop:LT", "stop:OH", "stop:SD"]
    _, _, info = vst.ensemble_script("E1A", "mix")
    assert sorted(f"stop:{s}" for s in info["raises"]) == sorted(parts[4:])


def test_every_part_sends_the_same_stream_apart_from_the_three_gain_words():
    """The bus-sum claim rests on this: if the stems were a different write
    stream their writes would land in different frames and the sum would be a
    comparison of three different renders."""
    reference, tail, info = vst.ensemble_script("E1A", "mix")
    for part in ("voice", "drum-mix", "body"):
        sent, other_tail, other = vst.ensemble_script("E1A", part)
        assert other_tail == tail and len(sent) == len(reference)
        gains = {vst.A.A_VOL, vst.A.A_DVOL, vst.A.A_BVOL}
        for a, b in zip(reference, sent):
            if a[2] == vst.SEC_V and a[3] in gains:
                assert a[:4] == b[:4]                      # same slot, different word
            else:
                assert a == b
        assert other["raises"] == info["raises"]


# --------------------------------------------------------------------------- #
# what validate_part must refuse
# --------------------------------------------------------------------------- #
def test_accepts_a_complete_passing_run(tmp_path):
    paths, text, wav_sha, schedule_sha = _part_files(tmp_path, "mix")
    got = score.validate_part("E1A", "mix", text, paths["wav"], paths["schedule"])
    assert got["simulator"] == "verilator"
    assert got["wav_sha256"] == wav_sha and got["schedule_sha256"] == schedule_sha
    assert got["periods"] == CASE_FRAMES and got["case_frames"] == CASE_FRAMES
    assert got["worst_latency_frames"] == 3


def test_refuses_a_mutation_run(tmp_path):
    paths, text, *_ = _part_files(
        tmp_path, "mix",
        extra="verify_synth_top: selected legacy single-rate waveform; compile defines: "
              "INJECT_BUG_DRUM_BUS_STALE")
    with pytest.raises(score.Refused, match="mutation run cannot provide sound evidence"):
        score.validate_part("E1A", "mix", text, paths["wav"], paths["schedule"])


def test_refuses_a_run_that_did_not_pass(tmp_path):
    paths, text, *_ = _part_files(tmp_path, "mix", pass_line=False)
    with pytest.raises(score.Refused, match="not a passing complete ensemble run"):
        score.validate_part("E1A", "mix", text, paths["wav"], paths["schedule"])


def test_refuses_a_run_that_never_claimed_the_combined_path(tmp_path):
    paths, text, *_ = _part_files(tmp_path, "mix", verified=False)
    with pytest.raises(score.Refused, match="not a passing complete ensemble run"):
        score.validate_part("E1A", "mix", text, paths["wav"], paths["schedule"])


def test_refuses_a_report_that_binds_a_different_wav(tmp_path):
    paths, text, *_ = _part_files(tmp_path, "mix")
    _write_wav(paths["wav"], np.ones(CASE_FRAMES, dtype=np.int64))
    with pytest.raises(score.Refused, match="does not bind this decoded WAV"):
        score.validate_part("E1A", "mix", text, paths["wav"], paths["schedule"])


def test_refuses_a_report_that_binds_a_different_schedule(tmp_path):
    paths, text, *_ = _part_files(tmp_path, "mix")
    paths["schedule"].write_text(json.dumps(_schedule("mix", stops={"BD": []}), indent=1))
    with pytest.raises(score.Refused, match="does not bind this schedule"):
        score.validate_part("E1A", "mix", text, paths["wav"], paths["schedule"])


def test_refuses_a_part_whose_bus_gains_are_another_parts(tmp_path):
    paths, text, *_ = _part_files(tmp_path, "voice",
                                  gains=vst.ensemble_part_gains("mix"))
    with pytest.raises(score.Refused, match="bus gains are not this part's"):
        score.validate_part("E1A", "voice", text, paths["wav"], paths["schedule"])


def test_refuses_a_run_shorter_than_the_case(tmp_path):
    short = np.zeros(CASE_FRAMES - 100, dtype=np.int64)
    paths, text, *_ = _part_files(tmp_path, "mix", samples=short)
    with pytest.raises(score.Refused, match="frame\\(s\\) short of"):
        score.validate_part("E1A", "mix", text, paths["wav"], paths["schedule"])


def test_refuses_a_report_that_omits_the_simulator(tmp_path):
    paths, text, *_ = _part_files(tmp_path, "mix")
    with pytest.raises(score.Refused, match="omits the simulator backend"):
        score.validate_part("E1A", "mix", text.replace("simulator backend verilator",
                                                       "simulator backend magic"),
                            paths["wav"], paths["schedule"])


def test_refuses_a_schedule_that_does_not_declare_the_i2s_delay(tmp_path):
    bad = _schedule("mix")
    bad["i2s_delay_periods"] = 0
    paths, text, *_ = _part_files(tmp_path, "mix", schedule=bad)
    with pytest.raises(score.Refused, match="contract 13's D = 1"):
        score.validate_part("E1A", "mix", text, paths["wav"], paths["schedule"])


def test_refuses_a_wav_shorter_than_the_periods_the_report_decoded(tmp_path):
    paths, text, *_ = _part_files(tmp_path, "mix")
    text = text.replace(f"PASS -- {CASE_FRAMES} I2S", f"PASS -- {CASE_FRAMES + 5} I2S")
    with pytest.raises(score.Refused, match="the report decoded"):
        score.validate_part("E1A", "mix", text, paths["wav"], paths["schedule"])


# --------------------------------------------------------------------------- #
# the one frame of I2S delay, at the point of use
# --------------------------------------------------------------------------- #
def test_the_schedule_the_onsets_are_measured_against_is_the_i2s_period():
    rows = [{"intended_frame": 2400, "landed_frame": 2403, "latency_frames": 3,
             "accent": 1.4, "i2s_period": 2404}]
    evidence = {"schedule": _schedule("stop:BD", stops={"BD": rows})}
    assert score.stop_schedule_s(evidence, "BD") == [2404 / SR]


def test_refuses_a_stop_whose_schedule_holds_no_raise():
    evidence = {"schedule": _schedule("stop:BD", stops={"BD": []})}
    with pytest.raises(score.Refused, match="holds no raise"):
        score.stop_schedule_s(evidence, "BD")


# --------------------------------------------------------------------------- #
# the metrics, driven until they move
# --------------------------------------------------------------------------- #
def _burst_train(times_s, n, *, amp=12000.0, tau=0.02, f0=220.0):
    x = np.zeros(n, dtype=np.float64)
    for start in times_s:
        i = int(round(start * SR))
        k = np.arange(max(0, n - i)) / SR
        x[i:] += amp * np.exp(-k / tau) * np.sin(2.0 * np.pi * f0 * k)
    return np.round(x)


def _evidence(hits_s, schedule_s, n=24_000):
    """A whole ensemble's worth of evidence from one stop's burst train: the
    drum bus carries the bursts, the voice bus is silent, and the mix is
    exactly the sum -- so bus balance reads 0 unless something breaks it."""
    drum = _burst_train(hits_s, n)
    voice = np.zeros(n)
    body = np.zeros(n)
    mix = drum + voice + body
    rows = [{"intended_frame": int(t * SR), "landed_frame": int(t * SR),
             "latency_frames": 0, "accent": 1.0, "i2s_period": int(round(t * SR))}
            for t in schedule_s]
    def part(name, samples, stops=None):
        return {"part": name, "simulator": "verilator", "samples": samples,
                "case_frames": n, "periods": n, "wav_sha256": f"wav-{name}",
                "schedule_sha256": f"sched-{name}", "report_sha256": f"rep-{name}",
                "worst_latency_frames": 0,
                "schedule": _schedule(name, stops=stops or {}, case_frames=n)}
    return {"mix": part("mix", mix), "voice": part("voice", voice),
            "drum-mix": part("drum-mix", drum), "body": part("body", body),
            "stop:BD": part("stop:BD", drum, stops={"BD": rows})}


HITS = [0.05, 0.25, 0.45]


def test_event_timing_passes_when_the_onsets_sit_where_the_register_landed():
    metrics, diagnostics = score.measure("E1A", _evidence(HITS, HITS))
    timing = metrics["Event timing"]
    assert timing["valid"] is True and timing["tolerance"] == 10.0
    assert timing["error"] < 10.0, timing
    assert diagnostics["per_stop_event_timing"]["BD"]["scheduled"] == 3
    assert metrics["bus balance"]["error"] == 0.0
    assert metrics["output artifacts"]["error"] == 0.0


def test_event_timing_is_not_inert():
    """START RED: move the register schedule 15 ms away from the onsets and the
    metric must exceed its own 10 ms tolerance. Without this the estimator could
    be wired to a constant and every test above would still pass.

    15 ms, not 20: `worst_event_offset_ms` only pairs onsets inside
    [first scheduled - 20 ms, last + min gap], and past about 19.5 ms here the
    first onset falls outside that window and the estimator REFUSES instead --
    which is the next test, and is the correct behaviour rather than a second
    bug. The two together say the metric moves when it can pair the events and
    withholds a number when it cannot."""
    late = [t + 0.015 for t in HITS]
    metrics, _ = score.measure("E1A", _evidence(HITS, late))
    timing = metrics["Event timing"]
    assert timing["valid"] is True
    assert timing["error"] > 10.0, timing
    assert timing["worst_stop"] == "BD"


def test_a_schedule_too_far_from_the_onsets_withholds_a_number():
    """Past the estimator's pairing window a 20 ms shift is NO distance, not a
    large one: pairing three onsets with three scheduled events it cannot see
    would be a guess wearing a measurement's clothes."""
    metrics, _ = score.measure("E1A", _evidence(HITS, [t + 0.020 for t in HITS]))
    assert metrics["Event timing"]["valid"] is False
    assert "error" not in metrics["Event timing"]


def test_bus_balance_sees_a_mix_that_is_not_the_sum():
    """START RED for the property the drum handshake would break: scale the mix
    by 1 dB and the 0.5 dB bus-sum tolerance must be exceeded."""
    evidence = _evidence(HITS, HITS)
    evidence["mix"]["samples"] = np.round(evidence["mix"]["samples"] * 10 ** (1.0 / 20.0))
    metrics, diagnostics = score.measure("E1A", evidence)
    assert metrics["bus balance"]["error"] > 0.5
    assert diagnostics["stem_sum_minus_mix_samples"] > 0


def test_output_artifacts_sees_a_railed_mix():
    evidence = _evidence(HITS, HITS)
    mix = evidence["mix"]["samples"].copy()
    mix[:64] = 32767.0                                   # 64 of 24000 samples: 0.27 %
    evidence["mix"]["samples"] = mix
    metrics, _ = score.measure("E1A", evidence)
    assert metrics["output artifacts"]["error"] > 0.01


def test_measure_refuses_without_every_stem(tmp_path):
    evidence = _evidence(HITS, HITS)
    del evidence["body"]
    with pytest.raises(score.Refused, match="no evidence for the body part"):
        score.measure("E1A", evidence)


def test_measure_refuses_without_a_per_stop_part():
    evidence = _evidence(HITS, HITS)
    del evidence["stop:BD"]
    with pytest.raises(score.Refused, match="event timing cannot be asked"):
        score.measure("E1A", evidence)


def test_a_stop_whose_onsets_cannot_be_paired_invalidates_rather_than_scores():
    """`worst_event_offset_ms` refuses when the detected count does not match
    the schedule. That must arrive on the board as NO distance, never as 0."""
    evidence = _evidence(HITS, HITS + [1.5])             # a scheduled hit that never sounds
    metrics, _ = score.measure("E1A", evidence)
    assert metrics["Event timing"]["valid"] is False
    assert "error" not in metrics["Event timing"]
