"""What the ensemble I2S scorer must refuse, and that its metrics are not inert.

The second half matters more than the first. A scorer that validates its inputs
strictly and then computes a number no defect can move is the shape of wrong
answer this repository keeps producing, so `test_event_timing_is_not_inert` and
`test_bus_balance_sees_a_mix_that_is_not_the_sum` drive the two metrics that
could plausibly be blind and require them to MOVE.
"""
import hashlib
import json
import pathlib
import re
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


@pytest.mark.parametrize("periods", [0, 1])
def test_refuses_a_bound_schedule_selecting_no_audio(tmp_path, periods, monkeypatch, capsys):
    paths, report, *_ = _part_files(tmp_path, "mix", samples=np.zeros(periods),
                                    schedule=_schedule("mix", case_frames=0))
    with pytest.raises(score.Refused, match="no case frames"):
        score.validate_part("E1A", "mix", report, paths["wav"], paths["schedule"])
    # The stand-in leaves the supplied report on disk, just as the RTL run would.
    monkeypatch.setattr(score, "ROOT", tmp_path)
    monkeypatch.setattr(score, "run_part",
                        lambda case, part, outdir, **kw: {"part": part, "status": 0})
    assert score.main(["--outdir", str(tmp_path), "--jobs", "1"]) == 2
    assert "REFUSED -- mix: no case frames" in capsys.readouterr().out


def test_refuses_a_mutation_run(tmp_path):
    paths, text, *_ = _part_files(
        tmp_path, "mix",
        extra="verify_synth_top: selected legacy single-rate waveform; compile defines: "
              "INJECT_BUG_DRUM_BUS_STALE")
    with pytest.raises(score.Refused, match="mutation runs cannot provide sound evidence"):
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


@pytest.mark.parametrize("extra,offending", [
    ("verify_synth_top: REFUSED: the ensemble part did not reach: partial run", "REFUSED"),
    (f"verify_synth_top: FAIL -- of {CASE_FRAMES} decoded I2S periods: 12 differ "
     "from the model", "FAIL --"),
    ("score_ensemble_i2s: NO-VERDICT -- mix timed out after 5400s", "NO-VERDICT"),
])
def test_refuses_a_pass_next_to_a_forbidden_outcome(tmp_path, extra, offending):
    """The strictest of the three contracts, and the one the shared gate was
    written from (#1556): delegating to it must not have loosened it, and the
    refusal still names the outcome it found, under the part's own prefix."""
    paths, text, *_ = _part_files(tmp_path, "mix", extra=extra)
    with pytest.raises(score.Refused, match="contains " + offending) as caught:
        score.validate_part("E1A", "mix", text, paths["wav"], paths["schedule"])
    assert str(caught.value).startswith("mix: ")


@pytest.mark.parametrize("name,part", [
    ("E1A-control-DRUM_BUS_STALE.txt", "mix"),
    ("E1A-control-DRUM_BUS_STALE-stop-BD.txt", "stop:BD"),
])
def test_a_committed_control_report_is_refused_as_a_mutation_before_anything_else(name, part):
    """Real board output. A caught control is also not a passing run, so which
    reason it gets depends on what is asked first, and it has to be the
    mutation: an operator told their injected run was merely incomplete has
    been told something true and useless (#1556)."""
    report = pathlib.Path(score.ROOT) / "docs/scorecard/ensemble-e1a/rtl" / name
    absent = pathlib.Path("absent")
    with pytest.raises(score.Refused,
                       match=f"^{re.escape(part)}: mutation runs cannot provide sound evidence$"):
        score.validate_part("E1A", part, report.read_text(), absent, absent)


@pytest.mark.parametrize("part", score.parts_for("E1A"))
def test_every_committed_part_report_clears_the_transcript_gate(part):
    """Real board output. The decoded WAVs are not committed, so the binding
    cannot be replayed here; everything asked of the REPORT can be: a
    committed part report is either accepted or stopped by the missing sidecar,
    never by the mutation check, the gate, the identity lines or the gains."""
    paths = score.part_paths(score.ROOT / "docs/scorecard/ensemble-e1a/rtl", part)
    try:
        score.validate_part("E1A", part, paths["report"].read_text(), paths["wav"],
                            paths["schedule"])
    except score.Refused as exc:
        assert "the decoded WAV or the schedule is missing" in str(exc), f"{part}: {exc}"


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


# --------------------------------------------------------------------------- #
# cached negative controls: reuse only while the bound files are the exact
# bytes the report digested
# --------------------------------------------------------------------------- #
_CONTROL_EXTRA = (f"compile defines: INJECT_BUG_{score.CONTROL_INJECT}\n"
                  f"FAIL -- of {CASE_FRAMES} decoded I2S periods: {CASE_FRAMES} differ "
                  f"from the model\n"
                  f"negative control {score.CONTROL_INJECT} CAUGHT")


class _RerunRequested(Exception):
    """Raised by a stubbed `run_part`: the scorer asked for a fresh RTL run."""


def _intercept_rerun(*args, **kwargs):
    raise _RerunRequested


def _control_bundle(outdir, part, *, samples=None, schedule=None):
    return _part_files(outdir, part, samples=samples, schedule=schedule,
                       pass_line=False, verified=False, extra=_CONTROL_EXTRA)


@pytest.mark.parametrize("changed", ["wav", "schedule"])
def test_control_reuse_reruns_when_a_bound_file_changes(tmp_path, monkeypatch, changed):
    folder = tmp_path / f"control-{score.CONTROL_INJECT}"
    folder.mkdir()
    clean = np.ones(CASE_FRAMES, dtype=np.int64)
    mutated = -clean
    paths, _, _, _ = _control_bundle(folder, "mix", samples=mutated)
    evidence = {"mix": {"samples": clean}}

    def read_control():
        return score.control_report("E1A", evidence, {}, tmp_path,
                                    simulator="verilator", timeout_s=1, reuse=True)

    monkeypatch.setattr(score, "run_part", _intercept_rerun)
    baseline = read_control()
    assert baseline["caught"]
    assert baseline["audio_vs_clean_mix"]["samples_differing"] == CASE_FRAMES

    if changed == "wav":
        _write_wav(paths["wav"], clean)
    else:
        paths["schedule"].write_text(json.dumps(_schedule("voice")))
    try:
        after = read_control()
    except _RerunRequested:
        return
    pytest.fail(f"reused a different {changed}: caught={after['caught']}, "
                f"audio samples differing={after['audio_vs_clean_mix']['samples_differing']}")


@pytest.mark.parametrize("digest_line", ["decoded I2S WAV sha256 ",
                                         "ensemble schedule sha256 "])
def test_control_reuse_reruns_when_the_report_omits_a_digest(
        tmp_path, monkeypatch, digest_line):
    folder = tmp_path / f"control-{score.CONTROL_INJECT}"
    folder.mkdir()
    mutated = -np.ones(CASE_FRAMES, dtype=np.int64)
    paths, text, _, _ = _control_bundle(folder, "mix", samples=mutated)
    stripped = "\n".join(line for line in text.splitlines()
                         if digest_line not in line) + "\n"
    assert stripped != text
    paths["report"].write_text(stripped)

    monkeypatch.setattr(score, "run_part", _intercept_rerun)
    with pytest.raises(_RerunRequested):
        score.control_report("E1A", {"mix": {"samples": np.ones(CASE_FRAMES)}}, {},
                             tmp_path, simulator="verilator", timeout_s=1, reuse=True)


def test_per_stop_control_reuse_reruns_when_a_bound_file_changes(tmp_path, monkeypatch):
    rows = [{"intended_frame": 2400, "landed_frame": 2403, "latency_frames": 3,
             "accent": 1.4, "i2s_period": 2404}]
    mutated = -np.ones(CASE_FRAMES, dtype=np.int64)
    paths, _, _, _ = _control_bundle(
        tmp_path, "stop:BD", samples=mutated,
        schedule=_schedule("stop:BD", stops={"BD": rows}))

    monkeypatch.setattr(score, "run_part", _intercept_rerun)
    baseline = score._injected_event_timing("E1A", "BD", tmp_path,
                                            simulator="verilator", timeout_s=1, reuse=True)
    assert baseline["measured"] and baseline["exit_status"] == 0

    _write_wav(paths["wav"], np.ones(CASE_FRAMES, dtype=np.int64))
    with pytest.raises(_RerunRequested):
        score._injected_event_timing("E1A", "BD", tmp_path,
                                     simulator="verilator", timeout_s=1, reuse=True)


@pytest.mark.parametrize("name", ["E1A-control-DRUM_BUS_STALE.txt",
                                  "E1A-control-DRUM_BUS_STALE-stop-BD.txt"])
def test_a_caught_control_report_from_the_board_binds_both_sidecars(name):
    """Requiring the digests for reuse is only safe if the verifier emits them
    on a caught run. Asserted against the committed E1A control reports --
    real board output, not a fixture this file built."""
    text = (pathlib.Path(score.ROOT) / "docs/scorecard/ensemble-e1a/rtl" / name).read_text()
    assert f"negative control {score.CONTROL_INJECT} CAUGHT" in text
    assert re.search(r"^verify_synth_top: decoded I2S WAV sha256 [0-9a-f]{64}$", text, re.M)
    assert re.search(r"^verify_synth_top: ensemble schedule sha256 [0-9a-f]{64}$", text, re.M)


# --------------------------------------------------------------------------- #
# one capture in a REUSED control report, not two (#1580)
# --------------------------------------------------------------------------- #
_COMMITTED = pathlib.Path(score.ROOT) / "docs/scorecard/ensemble-e1a/rtl"
#: What one run of a caught control prints: a backend declaration, no PASS
#: verdict (the bench was red) and one WAV digest.
SINGLE_CAUGHT = {"simulator backend declaration": 1, "PASS verdict": 0,
                 "decoded I2S WAV digest": 1}
#: The two callers of `_reusable_control_run`, and the folder and part each
#: keeps its control run in: the mix comparison, and one stop's timing.
_CALLERS = {"mix": (f"control-{score.CONTROL_INJECT}", "mix"),
            "stop:BD": (f"control-{score.CONTROL_INJECT}-timing", "stop:BD")}
_BD_ROWS = [{"intended_frame": 2400, "landed_frame": 2403, "latency_frames": 3,
             "accent": 1.4, "i2s_period": 2404}]


def _control_schedule(part):
    return _schedule(part, stops={"BD": _BD_ROWS}) if part.startswith("stop:") else None


def _caught_control(folder, part):
    """A caught control run of `part` under `folder`, bound to its own sidecars."""
    folder.mkdir(parents=True, exist_ok=True)
    return _control_bundle(folder, part, samples=-np.ones(CASE_FRAMES, dtype=np.int64),
                           schedule=_control_schedule(part))


def _reuse_control(caller, tmp_path):
    """Each caller's `--reuse` path over whatever sits in its control folder."""
    folder, part = _CALLERS[caller]
    if caller == "mix":
        return score.control_report("E1A", {"mix": {"samples": np.ones(CASE_FRAMES)}}, {},
                                    tmp_path, simulator="verilator", timeout_s=1, reuse=True)
    return score._injected_event_timing("E1A", "BD", tmp_path / folder,
                                         simulator="verilator", timeout_s=1, reuse=True)


def _caught_then_escaped(tmp_path, caller, *, drop_backend=False):
    """`caller`'s control folder holding ONE report of two runs of its part:
    run 1 caught (the bench red), then run 2 with the same mutation compiled in
    whose wire matched (`PASS --`, `NEGATIVE CONTROL NOT CAUGHT`), and run 2's
    WAV and schedule beside it. `run_part` overwrites a report, so this is a
    concatenated capture offered under `--reuse`. Returns the report."""
    folder, part = _CALLERS[caller]
    _, caught, *_ = _caught_control(tmp_path / "run1", part)
    escaped_samples = np.zeros(CASE_FRAMES, dtype=np.int64)
    escaped_samples[10] = 100
    (tmp_path / "run2").mkdir()
    escaped_paths, escaped, *_ = _part_files(
        tmp_path / "run2", part, samples=escaped_samples, schedule=_control_schedule(part),
        extra=(f"verify_synth_top: selected legacy single-rate waveform; compile defines: "
               f"INJECT_BUG_{score.CONTROL_INJECT}\n"
               "verify_synth_top: NEGATIVE CONTROL NOT CAUGHT (status 0)"))
    assert f"negative control {score.CONTROL_INJECT} CAUGHT" in caught
    assert f"negative control {score.CONTROL_INJECT} CAUGHT" not in escaped
    assert "NEGATIVE CONTROL NOT CAUGHT" in escaped
    if drop_backend:
        stripped = "\n".join(line for line in escaped.splitlines()
                             if "simulator backend" not in line) + "\n"
        assert stripped != escaped
        escaped = stripped
    (tmp_path / folder).mkdir()
    paths = score.part_paths(tmp_path / folder, part)
    paths["report"].write_text(caught + escaped)
    paths["wav"].write_bytes(escaped_paths["wav"].read_bytes())
    paths["schedule"].write_bytes(escaped_paths["schedule"].read_bytes())
    return caught + escaped


@pytest.mark.parametrize("drop_backend", [False, True],
                         ids=["two backend declarations", "one backend, two digests"])
@pytest.mark.parametrize("caller", sorted(_CALLERS))
def test_control_reuse_reruns_a_report_whose_latest_run_escaped(
        tmp_path, monkeypatch, caller, drop_backend):
    """START RED (#1580). The reuse predicate reads a control report by
    substring and never asked the shared gate -- a control report is never a
    clean PASS. Offered a caught run FOLLOWED BY an escaped one, with the
    escaped run's sidecars beside it, both callers reused it: the mix path
    reported the control caught with the FIRST run's `FAIL -- of` line, and
    the timing path measured the escaped run's WAV as the injected render."""
    text = _caught_then_escaped(tmp_path, caller, drop_backend=drop_backend)
    counts = score.i2s_transcript.record_counts(text)
    assert counts["decoded I2S WAV digest"] == 2
    assert counts["simulator backend declaration"] == (1 if drop_backend else 2)

    monkeypatch.setattr(score, "run_part", _intercept_rerun)
    try:
        reused = _reuse_control(caller, tmp_path)
    except _RerunRequested:
        return
    pytest.fail("reused a control report whose latest run escaped: " + str(
        {k: reused.get(k) for k in ("caught", "measured", "valid", "exit_status",
                                    "periods_differing_from_the_model")}))


@pytest.mark.parametrize("caller", sorted(_CALLERS))
def test_a_single_caught_control_is_still_reused(tmp_path, monkeypatch, caller):
    """The positive control for the refusal above: one caught run bound to its
    own sidecars is reused, so the count rule must take a report with NO PASS
    verdict -- the bench was red -- and one of each other record."""
    folder, part = _CALLERS[caller]
    paths, text, *_ = _caught_control(tmp_path / folder, part)
    assert score.i2s_transcript.record_counts(text) == SINGLE_CAUGHT
    assert score._reusable_control_run(paths, "E1A", part)

    monkeypatch.setattr(score, "run_part", _intercept_rerun)
    reused = _reuse_control(caller, tmp_path)
    assert reused["exit_status"] == 0
    if caller == "mix":
        assert reused["caught"]
        assert reused["periods_differing_from_the_model"] == CASE_FRAMES
    else:
        assert reused["measured"]


@pytest.mark.parametrize("gone", ["wav", "schedule"])
def test_control_reuse_reruns_when_a_bound_file_is_missing(tmp_path, monkeypatch, gone):
    paths, *_ = _caught_control(tmp_path / _CALLERS["mix"][0], "mix")
    paths[gone].unlink()

    monkeypatch.setattr(score, "run_part", _intercept_rerun)
    with pytest.raises(_RerunRequested):
        _reuse_control("mix", tmp_path)


@pytest.mark.parametrize("foreign", ["case", "part", "inject"])
def test_control_reuse_reruns_a_control_that_is_not_this_one(tmp_path, monkeypatch, foreign):
    """The other half of "the correct case, part and injection": a caught,
    bound control of another case, of another part, or one without the
    mutation is not this control, whatever it has cached. Each is placed
    where the mix caller looks, sidecars and all."""
    folder, part = _CALLERS["mix"]
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    extra = _CONTROL_EXTRA
    if foreign == "inject":
        extra = "\n".join(line for line in extra.splitlines() if "INJECT_BUG_" not in line) + "\n"
        assert "INJECT_BUG_" not in extra
    paths, text, *_ = _part_files(
        scratch, "voice" if foreign == "part" else part,
        case_id="E1B" if foreign == "case" else "E1A",
        samples=-np.ones(CASE_FRAMES, dtype=np.int64),
        pass_line=False, verified=False, extra=extra)
    assert f"negative control {score.CONTROL_INJECT} CAUGHT" in text
    (tmp_path / folder).mkdir()
    placed = score.part_paths(tmp_path / folder, part)
    for kind in ("report", "wav", "schedule"):
        placed[kind].write_bytes(paths[kind].read_bytes())
    assert not score._reusable_control_run(placed, "E1A", part)

    monkeypatch.setattr(score, "run_part", _intercept_rerun)
    with pytest.raises(_RerunRequested):
        _reuse_control("mix", tmp_path)


@pytest.mark.parametrize("caller, name, schedule", [
    ("mix", "E1A-control-DRUM_BUS_STALE.txt", "mix-schedule.json"),
    ("stop:BD", "E1A-control-DRUM_BUS_STALE-stop-BD.txt", "stop-BD-schedule.json")])
def test_a_committed_control_report_stays_reusable(tmp_path, monkeypatch, caller, name, schedule):
    """A count rule has to stay satisfiable by what the bench really prints.
    Each committed E1A control report is board output and holds ONE run's
    records. Reused here in full: its committed schedule -- the very bytes the
    run digested -- beside it, a fixture WAV standing in for the untracked
    one, and the one WAV digest line rebound to that fixture. Every other line
    is the board's own, and neither caller may ask for a run."""
    committed = (_COMMITTED / name).read_text()
    assert score.i2s_transcript.record_counts(committed) == SINGLE_CAUGHT

    folder, part = _CALLERS[caller]
    (tmp_path / folder).mkdir()
    paths = score.part_paths(tmp_path / folder, part)
    sidecar = (_COMMITTED / schedule).read_bytes()
    assert f"ensemble schedule sha256 {hashlib.sha256(sidecar).hexdigest()}" in committed
    paths["schedule"].write_bytes(sidecar)
    wav_sha = _write_wav(paths["wav"], -np.ones(CASE_FRAMES, dtype=np.int64))
    rebound, n_wav = re.subn(r"(verify_synth_top: decoded I2S WAV sha256 )[0-9a-f]{64}",
                             rf"\g<1>{wav_sha}", committed)
    assert n_wav == 1
    paths["report"].write_text(rebound)

    monkeypatch.setattr(score, "run_part", _intercept_rerun)
    reused = _reuse_control(caller, tmp_path)
    assert reused["exit_status"] == 0
    assert reused["caught"] if caller == "mix" else reused["measured"]


@pytest.mark.parametrize("caller, name, schedule", [
    ("mix", "E1A-control-DRUM_BUS_STALE.txt", "mix-schedule.json"),
    ("stop:BD", "E1A-control-DRUM_BUS_STALE-stop-BD.txt", "stop-BD-schedule.json")])
def test_control_reuse_reruns_when_a_committed_control_omits_the_backend(
        tmp_path, monkeypatch, caller, name, schedule):
    """START RED (#1659). Delete the `simulator backend` line from a committed
    E1A control transcript while keeping both sidecar digests bound. The count
    ceiling from #1580 refuses > 1 but places no floor, so zero backend
    declarations used to pass `_reusable_control_run` on both callers even
    though `validate_clean_pass` refuses an unnamed apparatus."""
    committed = (_COMMITTED / name).read_text()
    assert score.i2s_transcript.record_counts(committed) == SINGLE_CAUGHT
    stripped = "\n".join(line for line in committed.splitlines()
                         if "simulator backend" not in line) + "\n"
    assert stripped != committed
    assert score.i2s_transcript.record_counts(stripped) == {
        **SINGLE_CAUGHT, "simulator backend declaration": 0}

    folder, part = _CALLERS[caller]
    (tmp_path / folder).mkdir()
    paths = score.part_paths(tmp_path / folder, part)
    sidecar = (_COMMITTED / schedule).read_bytes()
    assert f"ensemble schedule sha256 {hashlib.sha256(sidecar).hexdigest()}" in stripped
    paths["schedule"].write_bytes(sidecar)
    wav_sha = _write_wav(paths["wav"], -np.ones(CASE_FRAMES, dtype=np.int64))
    rebound, n_wav = re.subn(r"(verify_synth_top: decoded I2S WAV sha256 )[0-9a-f]{64}",
                             rf"\g<1>{wav_sha}", stripped)
    assert n_wav == 1
    paths["report"].write_text(rebound)
    assert score._binding_problem(part, rebound, paths["wav"], paths["schedule"])[2] is None

    assert not score._reusable_control_run(paths, "E1A", part)
    monkeypatch.setattr(score, "run_part", _intercept_rerun)
    with pytest.raises(_RerunRequested):
        _reuse_control(caller, tmp_path)


# --------------------------------------------------------------------------- #
# one capture in the report, not two (#1562)
# --------------------------------------------------------------------------- #
TWO_RUNS = ("mix: ambiguous transcript: it holds 2 simulator backend declarations, "
            "2 PASS verdicts, 2 decoded I2S WAV digests, and one run prints one of each")
WITHOUT_A_BACKEND = ("mix: ambiguous transcript: it holds 2 PASS verdicts, "
                     "2 decoded I2S WAV digests, and one run prints one of each")


def _second_run(outdir, part, **report_kw):
    """A second clean run of `part`, its three files under `outdir/b`: the same
    schedule, and a WAV the first run did not decode."""
    (outdir / "b").mkdir(exist_ok=True)
    second = np.zeros(CASE_FRAMES, dtype=np.int64)
    second[10] = 100
    return _part_files(outdir / "b", part, samples=second, **report_kw)


def test_two_clean_runs_in_one_report_are_refused_rather_than_paired(tmp_path):
    """START RED (#1562). Offered BOTH of the second run's sidecars -- so both
    of its digests are in the report -- the report used to yield the FIRST
    run's latency."""
    a_paths, a, a_wav_sha, _ = _part_files(tmp_path, "mix")
    b_paths, b, b_wav_sha, _ = _second_run(tmp_path, "mix", latency=4)
    assert a_wav_sha != b_wav_sha
    assert score.validate_part("E1A", "mix", a, a_paths["wav"],
                               a_paths["schedule"])["worst_latency_frames"] == 3
    assert score.validate_part("E1A", "mix", b, b_paths["wav"],
                               b_paths["schedule"])["worst_latency_frames"] == 4
    other = b.replace("simulator backend verilator", "simulator backend iverilog")
    stripped = b.replace("verify_synth_top: simulator backend verilator\n", "")
    assert stripped != b
    for second, reason in ((b, TWO_RUNS), (other, TWO_RUNS), (stripped, WITHOUT_A_BACKEND)):
        with pytest.raises(score.Refused) as caught:
            score.validate_part("E1A", "mix", a + second, b_paths["wav"], b_paths["schedule"])
        assert str(caught.value) == reason


def test_main_refuses_a_two_run_report_before_measuring_anything(tmp_path, monkeypatch, capsys):
    """START RED (#1562), on the path that ships. Every part's report holds two
    clean runs; each used to validate as the first run's and reach `measure`.
    The stand-in leaves the supplied reports on disk, just as the RTL runs
    would."""
    for part in score.parts_for("E1A"):
        paths, a, *_ = _part_files(tmp_path, part)
        _, b, *_ = _second_run(tmp_path, part, latency=4)
        paths["report"].write_text(a + b)

    def measured(*_):
        raise AssertionError("the scorer measured audio described by a report "
                             "it had to refuse")
    monkeypatch.setattr(score, "measure", measured)
    monkeypatch.setattr(score, "ROOT", tmp_path)
    monkeypatch.setattr(score, "run_part",
                        lambda case, part, outdir, **kw: {"part": part, "status": 0})
    assert score.main(["--outdir", str(tmp_path), "--jobs", "1"]) == 2
    assert "REFUSED -- " + TWO_RUNS in capsys.readouterr().out
    assert not (tmp_path / "docs/scorecard/results/E1A.json").exists()


def test_measurement_sources_hash_the_transcript_gate():
    """Mirror of #1208: the module this scorer calls to validate transcripts
    must be in its own hashed sources, derived from the import rather than a
    literal so a future extraction cannot silently fall outside
    `measurement_source_sha256`."""
    helper = "tools/" + pathlib.Path(score.i2s_transcript.__file__).name
    assert helper in score.MEASUREMENT_SOURCES
    root = pathlib.Path(score.ROOT)
    missing = [rel for rel in score.MEASUREMENT_SOURCES if not (root / rel).is_file()]
    assert missing == []
