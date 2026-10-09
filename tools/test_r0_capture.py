"""tools/r0_capture.py: known-answer tests for every estimator the verdict
uses, the refusals, and the synthetic defect controls.

The estimators are tested on signals whose answer is known in closed form
(docs/failure-modes.md mechanism 1) -- never calibrated on our own model. The
controls run the reference through a known synthetic analog path; each defect
must FAIL for its own property, the clean session must PASS with its known
delay/gain/clock recovered, and the swap must be reported as unobservable.
"""
from __future__ import annotations

import json
import math
import pathlib
import shutil
import sys

import numpy as np
import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import r0_capture as rc                                           # noqa: E402

SR = 48000


def _bl_noise(n, rng, hi=0.4):
    """Band-limited white noise: exactly representable at any fractional delay."""
    X = np.fft.rfft(rng.normal(size=n))
    f = np.fft.rfftfreq(n)
    X[f > hi] = 0
    return np.fft.irfft(X, n)


def _fdelay(x, d):
    """Exact circular fractional delay by an FFT phase ramp (the known answer)."""
    X = np.fft.rfft(x)
    f = np.fft.rfftfreq(x.size)
    return np.fft.irfft(X * np.exp(-2j * np.pi * f * d), x.size)


# ---- estimators, known answers ---------------------------------------------
def test_interp_is_band_limited_to_minus_80_db():
    n = np.arange(4000)
    f = 0.3                                     # cycles/sample, 14.4 kHz at 48 kHz
    x = np.sin(2 * np.pi * f * n)
    pos = np.linspace(200.0, 3700.0, 997) + 0.37
    got = rc.interp(x, pos)
    want = np.sin(2 * np.pi * f * pos)
    assert np.max(np.abs(got - want)) < 1e-4


def test_warp_then_unwarp_is_identity_inside_the_signal():
    rng = np.random.default_rng(3)
    x = _bl_noise(20000, rng, hi=0.35)
    y = rc.warp_reference(x, 21000, 321.4, 1 + 80e-6)
    back = rc.unwarp_capture(y, 20000, 321.4, 1 + 80e-6)
    s = slice(200, 19800)
    assert rc.rms(back[s] - x[s]) < 1e-3 * rc.rms(x[s])


def test_ncc_lag_recovers_a_known_fractional_delay():
    rng = np.random.default_rng(1)
    x = _bl_noise(16384, rng)
    y = _fdelay(x, 37.3)
    lag, ncc, _ = rc.ncc_lag(x[1000:3048], y, 900, 1200)
    assert abs(lag - 1037.3) < 0.05 and ncc > 0.99


def test_first_strong_lag_takes_the_earliest_repeat():
    rng = np.random.default_rng(2)
    motif = _bl_noise(2000, rng)
    sig = np.zeros(60000)
    for at in (3000, 27000, 51000):          # repeats 0.5 s apart, like bars of a pattern
        sig[at:at + 2000] += motif
    lag, ncc = rc.first_strong_lag(motif, sig, 0, sig.size - motif.size)
    assert abs(lag - 3000) < 0.01 and ncc > 0.99


@pytest.mark.parametrize("f0,dur", [(523.2511, 0.040), (110.0, 0.2), (1046.502, 0.03)])
def test_tone_f0_on_a_sine_and_a_band_limited_saw(f0, dur):
    n = np.arange(int(dur * SR))
    sine = np.sin(2 * np.pi * f0 * n / SR + 0.3)
    saw = sum(np.sin(2 * np.pi * k * f0 * n / SR) / k for k in range(1, int(20000 / f0)))
    for x in (sine, saw):
        got = rc.tone_f0(x, SR, 30, 5000)
        assert abs(1200 * math.log2(got / f0)) < 0.1


def test_clip_runs_counts_runs_at_full_scale_only():
    n = np.arange(4800)
    s = 1.5 * np.sin(2 * np.pi * 100 * n / SR)
    assert rc.clip_runs(np.clip(s, -1, 1), 0.999, 3) == 20      # 10 cycles, 2 flat tops each
    assert rc.clip_runs(0.9 * np.sin(2 * np.pi * 100 * n / SR), 0.999, 3) == 0


def test_rms_dbfs_of_known_noise():
    x = np.random.default_rng(4).normal(0, 10 ** (-60 / 20), 480000)
    assert abs(rc.dbfs(rc.rms(x)) + 60) < 0.05


def test_calibrate_recovers_known_clock_gain_and_delay():
    rng = np.random.default_rng(5)
    ref = np.zeros(3 * SR)
    for k in range(12):                            # bursts, like hits
        at = int((0.1 + 0.24 * k) * SR)
        ref[at:at + 4000] = _bl_noise(4000, rng) * np.exp(-np.arange(4000) / 1500)
    cap = 0.25 * rc.warp_reference(ref, 4 * SR, 1000.4, 1 + 80e-6)
    cap += rng.normal(0, 1e-5, cap.size)
    c = rc.calibrate(ref, (int(0.1 * SR) - 96, int(0.1 * SR) + 1344), cap)
    assert c["ok"]
    assert abs(c["ppm"] - 80) < 1.0
    assert abs(c["gain_db"] - rc.dbfs(0.25)) < 0.05
    assert abs(c["delay"] - 1000.4) < 0.1


def test_calibrate_refuses_a_take_too_short_for_the_clock():
    rng = np.random.default_rng(6)
    ref = np.zeros(SR // 2)
    ref[1000:5000] = _bl_noise(4000, rng)
    cap = rc.warp_reference(ref, SR, 500, 1.0)
    with pytest.raises(rc.Refused, match="too short to estimate the sample clock"):
        rc.calibrate(ref, (900, 2340), cap)


def test_24_bit_capture_reads_back_at_its_level(tmp_path):
    y = np.zeros((4800, 4))
    y[:, 2] = 0.5 * np.sin(2 * np.pi * 1000 * np.arange(4800) / SR)
    rc.write_capture_wav(tmp_path / "t.wav", y)
    sr, z = rc.read_capture(tmp_path / "t.wav")
    assert sr == SR and z.shape == (4800, 4)
    assert np.max(np.abs(z - y)) < 2 ** -22


# ---- sessions: refusals ------------------------------------------------------
@pytest.fixture(scope="module")
def clean(tmp_path_factory):
    return rc.synth_session(tmp_path_factory.mktemp("r0") / "clean")


def _copy(clean, tmp_path):
    d = tmp_path / "s"
    shutil.copytree(clean, d)
    return d


def _edit(d, fn):
    p = d / "session.json"
    s = json.loads(p.read_text())
    fn(s)
    p.write_text(json.dumps(s))


def test_no_session_is_operator_blocked_no_verdict(tmp_path):
    rec = rc.analyse(tmp_path / "nothing")
    assert rec["verdict"] == rc.REFUSED and rec.get("operator_blocked")
    assert "operator-blocked" in rec["reasons"][0]


@pytest.mark.parametrize("mutate,why", [
    (lambda s: s["interface"].pop("gain"), "session.interface.gain missing"),
    (lambda s: s["image"].pop("readback"), "session.image.readback missing"),
    (lambda s: s["board"].update(power=""), "session.board.power missing"),
    (lambda s: s["interface"].update(processing="compressor"), "must be 'none'"),
    (lambda s: s["interface"].update(sample_rate=44100), "is not 48000"),
    (lambda s: s["image"].update(bitstream_sha256="0" * 64), "is not R0's"),
    (lambda s: s["takes"][1].update(command_id="run --note 60"), "not an R0 release command"),
    (lambda s: s["takes"][1].update(host_capture=None), "host_capture missing"),
    (lambda s: s["takes"].pop(0), "no take of ['silence']"),
    (lambda s: [s["takes"].remove(t) for t in list(s["takes"]) if t["id"] in ("demo-2", "held-2")],
     "no repeat take"),
])
def test_incomplete_metadata_or_diagnostic_set_refuses(clean, tmp_path, mutate, why):
    d = _copy(clean, tmp_path)
    _edit(d, mutate)
    rec = rc.analyse(d, allow_synthetic=True)
    assert rec["verdict"] == rc.REFUSED, rec["reasons"]
    assert why in rec["reasons"][0]


def test_missing_wav_refuses(clean, tmp_path):
    d = _copy(clean, tmp_path)
    (d / "takes" / "tone-1.wav").unlink()
    rec = rc.analyse(d, allow_synthetic=True)
    assert rec["verdict"] == rc.REFUSED and "tone-1.wav not found" in rec["reasons"][0]


def test_wrong_sample_rate_file_refuses(clean, tmp_path):
    d = _copy(clean, tmp_path)
    from scipy.io import wavfile
    wavfile.write(str(d / "takes" / "tone-1.wav"), 44100, np.zeros((44100, 4), np.int32))
    rec = rc.analyse(d, allow_synthetic=True)
    assert rec["verdict"] == rc.REFUSED and "44100 Hz, not 48000" in rec["reasons"][0]


def test_host_log_of_another_command_refuses(clean, tmp_path):
    d = _copy(clean, tmp_path)
    shutil.copyfile(rc.REFERENCES / "held-m5a-pulse.plan.json", d / "host" / "tone-1.plan.json")
    rec = rc.analyse(d, allow_synthetic=True)
    assert rec["verdict"] == rc.REFUSED and "is not the released command" in rec["reasons"][0]


def test_truncated_take_refuses(clean, tmp_path):
    d = _copy(clean, tmp_path)
    sr, y = rc.read_capture(d / "takes" / "held-1.wav")
    rc.write_capture_wav(d / "takes" / "held-1.wav", y[: y.shape[0] // 3])
    rec = rc.analyse(d, allow_synthetic=True)
    assert rec["verdict"] == rc.REFUSED and "before its reference does" in rec["reasons"][0]


def test_altered_reference_refuses(clean, tmp_path):
    ref = tmp_path / "ref"
    shutil.copytree(rc.REFERENCES, ref)
    p = ref / "held-default.wav"
    b = bytearray(p.read_bytes())
    b[-2] ^= 1
    p.write_bytes(bytes(b))
    rec = rc.analyse(clean, ref, allow_synthetic=True)
    assert rec["verdict"] == rc.REFUSED and "missing or altered" in rec["reasons"][0]


# ---- the controls -------------------------------------------------------------
def test_clean_synthetic_session_passes_with_its_known_answers(clean):
    rec = rc.analyse(clean, allow_synthetic=True)
    assert rec["verdict"] == rc.PASS, rec["reasons"]
    c = rec["calibration"]
    assert abs(c["ppm"] - rc.CLEAN["ppm"]) < 2.0
    assert abs(c["gain_db"] - rc.dbfs(rc.CLEAN["gain"])) < 0.2
    delays = {t["id"]: t["metrics"].get("delay_samples") for t in rec["takes"]}
    for i, (tid, cmd) in enumerate(rc.SYNTH_TAKES):
        if cmd != "silence":
            assert abs(delays[tid] - rc.CLEAN["delays"][i]) < 0.25, (tid, delays[tid])
    assert rec["identity"]["programming"].startswith("programming transcript only")
    assert rec["properties"]["channel-identity"]["verdict"] == "UNOBSERVABLE"


def test_start_red_the_stub_analyser_catches_nothing(tmp_path):
    res = rc.run_controls(tmp_path, analyser=rc.stub_analyse,
                          defects=["dropout", "clipping", "noise"])
    assert not res["all_caught"]
    assert not any(d["caught"] for d in res["defects"].values())


def _env_spec(tmp_path):
    spec = tmp_path / "env.json"
    spec.write_text(json.dumps({
        "version": "test-env/1", "python": "%d.%d" % sys.version_info[:2], "packages": {},
        "toolchain": {"installer": "none", "bin": "nobin", "record": "none", "tools": {}}}))
    return spec


def test_t_physical_required_child_without_a_capture_is_operator_blocked(tmp_path, monkeypatch):
    """The registered required child alone: no session -> NO VERDICT for the
    operator-blocked reason."""
    import trial
    child = _required_only_trial(tmp_path, monkeypatch, tmp_path / "no-session-here")
    assert child["verdict"] == trial.NO_VERDICT
    assert "operator-blocked" in child["reasons"][0]


def test_t_physical_on_a_synthetic_bundle_is_no_verdict_with_its_control_caught(
        tmp_path, monkeypatch):
    """Judge B1 (#299): `r0_capture.py synth` output placed where the operator's
    bundle goes made T-PHYSICAL PASS, exit 0, with no board. The registered
    trial, end to end through tools/trial.py, must be NO VERDICT on it -- the
    session is synthetic -- and its synthetic-defect control still caught.
    The environment spec is this interpreter's (the trial's own pins are
    checked by the CI job that bootstraps them); nothing else is stubbed."""
    import trial
    bundle = rc.synth_session(tmp_path / "synthetic-bundle")
    monkeypatch.setenv("R0_CAPTURE_BUNDLE", str(bundle))
    run_dir, rec = trial.run_trial("T-PHYSICAL", env_spec=_env_spec(tmp_path),
                                   out_base=tmp_path / "trials")
    assert rec["execution"]["status"] == "complete", rec["execution"]
    assert rec["verdict"] == trial.NO_VERDICT
    child = rec["children"][0]
    assert child["verdict"] == trial.NO_VERDICT
    assert "synthetic" in child["reasons"][0]
    assert rec["controls"][0]["caught"] is True, rec["controls"][0]["reasons"]
    # the control's own record, defect by defect (the suite runs once, here)
    ctl = json.loads((run_dir / "synthetic-defects" / "controls.json").read_text())
    rc.print_matrix(ctl)
    assert ctl["clean"]["ok"], ctl["clean"]
    for name, d in ctl["defects"].items():
        assert d["caught"], (name, d)
        if d["intended"] is not None:
            assert d["intended"] in d["failed"], (name, d)
    assert ctl["defects"]["swap"]["verdict"] == rc.PASS           # honest: unobservable
    ok, problems, _ = trial.check_receipt(run_dir / "receipt.json")
    assert ok, problems


def test_a_held_tone_is_placed_on_its_own_period_not_a_neighbour():
    """The first dev run locked the 523 Hz held note one period (91.7 samples)
    off: neighbouring periods correlate within 0.1 % of the true one. The
    envelope must choose the period, the waveform only the fraction. The
    plain waveform argmax is asserted to fail on the same case, so this test
    is shown able to see the defect (the delay that exposed it, 22222.75)."""
    ref = rc.load_reference(rc.REFERENCES, "held-m5a-saw")
    x, cal = ref["xb"], ref["cal"]
    rng = np.random.default_rng(1)
    for d in (22222.75, 1500.25, 777.6):
        y = rc.synth_take(ref["x"], delay=d, gain=0.3, ppm=35, noise_dbfs=-100, hpf_hz=5,
                          rng=rng)
        A = rc.band(y[:, 2])
        want = d + cal[0] / (1 + 35e-6)
        lag, _, _, _ = rc._coarse(x, cal, A, ref_env=ref["env"])
        assert abs(lag - want) < 0.5, (d, lag - want)
        if d == 22222.75:
            t = x[cal[0]:cal[0] + 24000]
            naive, _, _ = rc.ncc_lag(t, A, 0, A.size - t.size)
            assert abs(naive - want) > 80          # the defect, reproduced



# ---- Judge #299: the analysis the trial runs, on inputs that are not a capture --
def test_a_synthetic_session_is_refused_as_a_physical_capture(clean):
    """Judge B1: the default analysis (the one `analyse` and the trial run)
    must refuse a synthetic session outright."""
    rec = rc.analyse(clean)
    assert rec["verdict"] == rc.REFUSED, rec["reasons"]
    assert "synthetic" in rec["reasons"][0]


GOOD_TRANSCRIPT = (
    "release_manifest: BOUND -- fpga/release/baseline-2025.1.json equals a fresh derivation\n"
    "exit 0\n"
    "a66c9349ef9b5572f3c3453777f38e1b143136755620fe419e730d6f5c84cb95  "
    "fpga/reports/arty/integrated-baseline-2025.1/arty.bit\n"
    "openFPGALoader v1.1.1\n"
    "Load SRAM: [====] 100.00%\nDone\n"
    "exit 0\n")


def _as_real(clean, tmp_path, transcript=GOOD_TRANSCRIPT):
    """The synthetic session with its synthetic markers removed and a
    programming transcript in procedure step 5's format: the audio is still
    synthetic, but every check the real path applies to metadata applies."""
    d = _copy(clean, tmp_path)
    s = json.loads((d / "session.json").read_text())
    s.pop("synthetic")
    s["image"]["programmer"] = "openFPGALoader v1.1.1"
    s["board"].update(model="Arty A7-100T", revision="E", power="USB J10")
    s["dac"].update(model="Adafruit PCM5102 #6250", wiring="fpga/ARTY.md")
    s["interface"].update(model="MOTU M4", recorder="sox -D -t coreaudio M4 -b 24")
    for i, t in enumerate(s["takes"]):
        t["started"] = f"2026-09-26T20:{i:02d}:00Z"
        if t.get("host_capture"):                  # one run per take: distinct logs
            hp = d / f"{t['host_capture']}.plan.json"
            plan = json.loads(hp.read_text())
            plan["origin"] = 1000 * (i + 1)
            hp.write_text(json.dumps(plan))
    (d / "session.json").write_text(json.dumps(s))
    (d / "program.txt").write_text(transcript)
    (d / "detect.txt").write_text("index 0: idcode 0x13631093 xc7a100t\nexit 0\n")
    return d


BAD_TRANSCRIPTS = {
    "manifest not BOUND": GOOD_TRANSCRIPT.replace("BOUND", "STALE", 1),
    "manifest exit": GOOD_TRANSCRIPT.replace("exit 0", "exit 1", 1),
    "wrong image hash": GOOD_TRANSCRIPT.replace("a66c9349", "1a562b42"),
    "programmer exit": GOOD_TRANSCRIPT[::-1].replace("0 tixe", "1 tixe", 1)[::-1],
    "no programmer exit": GOOD_TRANSCRIPT.rsplit("exit 0", 1)[0],
    "non-empty only": "programmed it, looked fine\n",
    "synthetic transcript": "SYNTHETIC: no board was programmed\n" + GOOD_TRANSCRIPT,
}


@pytest.mark.parametrize("name", sorted(BAD_TRANSCRIPTS))
def test_the_programming_transcript_must_show_step_5s_pass_conditions(clean, tmp_path, name):
    """Judge B1: a non-empty program.txt is not programming evidence. The
    manifest BOUND with exit 0, R0's shasum line and the programmer's own
    final exit 0 must all be in it, or the analysis refuses."""
    rec = rc.analyse(_as_real(clean, tmp_path, BAD_TRANSCRIPTS[name]))
    assert rec["verdict"] == rc.REFUSED, (name, rec["reasons"])
    assert "program" in rec["reasons"][0], rec["reasons"][0]


def test_a_good_transcript_on_real_looking_metadata_is_accepted(clean, tmp_path):
    """The gate above is satisfiable: run it against the current state."""
    rec = rc.analyse(_as_real(clean, tmp_path))
    assert rec["verdict"] == rc.PASS, rec["reasons"]


def _live_host_log(key, prefix, epoch=0, inject=None):
    """What the shipped CLI's LIVE path writes with --capture, driven against
    the repository's scripted device (fpga/verify_rolling_playback.Harness):
    the closest thing to hardware this checkout has. The device hold is read
    by REGISTER identity on the device's unwrapped log (fpga/hold_timing.py),
    not as "first event minus last live write" (#306)."""
    import contextlib
    import io
    import hold_timing as ht
    import r0_reference as rr
    import uart_host as uh
    import verify_rolling_playback as vrp
    man = rr.load_manifest()
    argv = rr.cli_argv(man["commands"][key]["command"]) + ["--port", "sim",
                                                           "--capture", str(prefix)]
    h = vrp.Harness(epoch)
    saved = set(uh.INJECT_BUGS)
    uh.INJECT_BUGS.clear()
    if inject:
        uh.INJECT_BUGS.add(inject)
    try:
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            code = uh.main(argv, bridge_factory=h.factory)
    finally:
        uh.INJECT_BUGS.clear()
        uh.INJECT_BUGS.update(saved)
    assert code == 0, (key, code)
    h.ser.run_until(h.clock.t + 0.25)
    device_hold = None
    if key.startswith("held-"):
        g = ht.device_gates(h.sim)
        device_hold = g["off"] - g["on"]
    return json.loads(pathlib.Path(f"{prefix}.plan.json").read_text()), device_hold


@pytest.mark.parametrize("key", ["held-default", "held-m5a-saw", "held-m5a-pulse",
                                 "phrase-m5a", "demo", "bar808-full"])
def test_the_live_cli_host_log_passes_the_identity_gate(key, tmp_path):
    """Judge B2: the gate compared the dry-run plan with the live log row by
    row, and the live CLI sends a held note's gate-off event AFTER its writes
    (the dry-run lists it first), so every held-note take would have been
    refused on the first real session. Every pinned command, through the live
    path, must pass."""
    host, _ = _live_host_log(key, tmp_path / "h")
    ref = json.loads((rc.REFERENCES / f"{key}.plan.json").read_text())
    assert rc.command_identity(ref, host) == []


def test_the_live_held_note_hold_offset_is_measured_not_assumed(tmp_path):
    """HISTORICAL BASELINE (Judge B2), now an injected control: the pre-#306
    live path, `HOLD_ACK_DRAIN`, planned 3121 frames against the dry-run's
    1920 -- 1201 frames, 25 ms -- and the scripted device fired the gate-off
    3155 frames after the gate, 34 off the host's own plan. Kept as measured
    numbers so the control stays the bug it claims to be."""
    ref = json.loads((rc.REFERENCES / "held-default.plan.json").read_text())
    host, device_hold = _live_host_log("held-default", tmp_path / "h",
                                       inject="HOLD_ACK_DRAIN")
    assert rc.planned_hold(ref) == 1920
    assert rc.planned_hold(host) == 3121
    assert device_hold == 3155
    assert abs(device_hold - rc.planned_hold(host)) <= rc.HOLD_UNCERTAINTY_FRAMES
    assert "hold_timing" not in host            # the historical path claimed nothing


@pytest.mark.parametrize("key", ["held-default", "held-m5a-saw", "held-m5a-pulse"])
def test_the_live_held_note_delivers_the_requested_hold(key, tmp_path):
    """#306: the live path brackets the gate on the device's timeline. Its
    log's hold is the record's (due - estimate) = 1920, the DEVICE holds 1920
    within the record's bound, and the planner's live-row prediction -- a
    plan, not an observation -- is no longer what planned_hold reads."""
    host, device_hold = _live_host_log(key, tmp_path / "h")
    rec = host["hold_timing"]
    assert rec["verdict"] == "WITHIN_BOUND" and rc.hold_timing_problems(host) == []
    assert rc.planned_hold(host) == 1920
    assert abs(device_hold - 1920) <= rec["hold_bound_frames"] <= rec["declared_max_bound_frames"]


def test_clean_audio_with_live_cli_host_logs_is_analysed_not_refused(clean, tmp_path):
    """Judge B2, end to end: the clean session with every host log replaced by
    what the live CLI writes. The held takes carry the measured hold offset,
    their release is not compared, and the session is not refused."""
    d = _as_real(clean, tmp_path)
    s = json.loads((d / "session.json").read_text())
    for i, t in enumerate(s["takes"]):
        if t["command_id"] != "silence":
            _live_host_log(t["command_id"], d / t["host_capture"], epoch=5000 * i)
    rec = rc.analyse(d)
    assert rec["verdict"] == rc.PASS, rec["reasons"]
    held = {t["id"]: t for t in rec["takes"]}["held-1"]["metrics"]
    # #306: the live log now plans the requested hold -- offset 0 -- and the
    # release is STILL not compared: the committed reference is the dry-run
    # schedule's, and equal planned holds are not a qualified release
    assert held["hold_offset_frames"] == 0 and held["release_compared"] is False
    assert "NOT EVALUATED" in held["coverage"]["excluded_why"], held["coverage"]
    assert rec["coverage"]["release_compared"]["held-1"] is False


# ---- #306: the release comparison, bound to a live schedule ------------------
_LIVE_STIMULUS = {}


def _live_stimulus(key, tmp_path):
    """(sha256, host hold record) of the bytes the shipped CLI's LIVE path
    sends for `key` against the scripted device, written in the RTL replay
    format exactly as `r0_reference.render_one(..., schedule="live")` writes
    them before replaying them. The RTL replay itself is NOT run here."""
    if key not in _LIVE_STIMULUS:
        import hold_timing as ht
        m = ht.measure(ht.HELD_COMMANDS[key])
        v, why = ht.hold_verdict(m)
        assert v == ht.PASS, why
        prefix = tmp_path / f"{key}-live"
        ht.write_held_rtl_capture(m["_harness"], prefix)
        _LIVE_STIMULUS[key] = (rc.sha256_file(pathlib.Path(f"{prefix}.cmds")),
                               dict(m["host_record"]))
    sha, rec = _LIVE_STIMULUS[key]
    return sha, dict(rec)


def _live_refs(tmp_path, *, requested=1920, tolerance=8, keys=("held-default",)):
    """The committed references with `held-default` carrying a live-schedule
    identity that satisfies the identity invariant: `replayed_stimulus_sha256`
    and `schedule.live_cmds_sha256` are both the hash of the CLI's real live
    bytes, which differ from the pinned dry-run's. SYNTHETIC, and declared so:
    the AUDIO is still the dry-run render (rendering the live schedule through
    the RTL is the build box's job), so production qualification refuses it
    and these tests pass `allow_synthetic=True`. This exercises the binding
    and the comparison, not a rendered schedule."""
    d = tmp_path / "live-refs"
    shutil.copytree(rc.REFERENCES, d)
    for key in keys:
        sha, host_record = _live_stimulus(key, tmp_path)
        p = d / f"{key}.json"
        r = json.loads(p.read_text())
        assert sha != r["cmds_sha256"], "the live bytes are the dry-run's: guard premise"
        host_record["requested_hold_frames"] = requested
        r["replayed_stimulus_sha256"] = sha
        r["schedule"] = {"kind": "live",
                         "synthetic": "dry-run audio under real live-bytes identity (test)",
                         "live_cmds_sha256": sha,
                         "hold_timing": host_record,
                         "release_tolerance_frames": tolerance}
        p.write_text(json.dumps(r))
    return d


def _relabelled_dry_run_refs(tmp_path, *, synthetic=True, live_hash="0" * 64,
                             replayed=None):
    """The Judge's defeating input (PR #563): the committed DRY-RUN reference
    for held-default, copied and relabelled `kind: live`, with an all-zero
    live-bytes hash. Nothing in it came from a live schedule. `replayed`
    additionally forges the record's `replayed_stimulus_sha256`."""
    d = tmp_path / "relabelled-refs"
    shutil.copytree(rc.REFERENCES, d)
    p = d / "held-default.json"
    r = json.loads(p.read_text())
    r["schedule"] = {"kind": "live", "live_cmds_sha256": live_hash,
                     "hold_timing": {"requested_hold_frames": 1920},
                     "release_tolerance_frames": 8}
    if synthetic:
        r["schedule"]["synthetic"] = "relabelled dry-run render (test)"
    if replayed is not None:
        r["replayed_stimulus_sha256"] = replayed
    p.write_text(json.dumps(r))
    return d


def test_a_relabelled_dry_run_reference_qualifies_no_release(clean, tmp_path):
    """NEGATIVE CONTROL (Judge, PR #563): the exact circular fixture -- the
    dry-run reference copied, `kind: live` added, an all-zero hash -- on the
    PRODUCTION path leaves every held release NOT EVALUATED."""
    d = _held_live_session(clean, tmp_path)
    rec = rc.analyse(d, refdir=_relabelled_dry_run_refs(tmp_path))
    assert rec["coverage"]["release_compared"]["held-1"] is False
    assert rec["coverage"]["release_compared"]["held-2"] is False
    held = {t["id"]: t for t in rec["takes"]}["held-1"]["metrics"]
    assert "NOT EVALUATED" in held["coverage"]["excluded_why"], held["coverage"]


def _held_live_session(clean, tmp_path, displace=0, held1_inject=None):
    d = _as_real(clean, tmp_path)
    s = json.loads((d / "session.json").read_text())
    for i, t in enumerate(s["takes"]):
        if t["command_id"] != "silence":
            _live_host_log(t["command_id"], d / t["host_capture"], epoch=5000 * i,
                           inject=held1_inject if t["id"] == "held-1" else None)
    if displace:
        # the held-1 take with its release moved `displace` frames: EARLIER
        # (> 0) cuts the sustain short just before the release, LATER (< 0)
        # repeats the last |displace| frames of sustain; the rest of the note
        # follows unchanged -- the intentionally displaced release
        ref = rc.load_reference(rc.REFERENCES, "held-default")
        x = ref["x"]
        r = int(np.flatnonzero(x)[0]) + int(ref["hold"])
        if displace > 0:
            xd = np.concatenate([x[:r - displace], x[r:], np.zeros(displace)])
        else:
            xd = np.concatenate([x[:r], x[r + displace:r], x[r:]])[:x.size]
        i = next(k for k, t in enumerate(s["takes"]) if t["id"] == "held-1")
        y = rc.synth_take(xd, delay=rc.CLEAN["delays"][i % len(rc.CLEAN["delays"])],
                          gain=rc.CLEAN["gain"], ppm=rc.CLEAN["ppm"],
                          noise_dbfs=rc.CLEAN["noise_dbfs"], hpf_hz=rc.CLEAN["hpf_hz"],
                          rng=np.random.default_rng(99))
        rc.write_capture_wav(d / s["takes"][i]["wav"], y)
    return d


def test_a_live_schedule_reference_qualifies_the_release(clean, tmp_path):
    """The binding satisfied -- a live-schedule reference for the requested
    hold, a host record within its tolerance: the held takes' releases ARE
    compared, and the clean session passes with them in."""
    d = _held_live_session(clean, tmp_path)
    refs = _live_refs(tmp_path)
    rec = rc.analyse(d, refdir=refs, allow_synthetic=True)
    assert rec["verdict"] == rc.PASS, rec["reasons"]
    cov = rec["coverage"]["release_compared"]
    assert cov["held-1"] is True and cov["held-2"] is True
    assert cov["tone-1"] is False               # m5a-saw's reference is still dry-run
    # the same fixture on the PRODUCTION path: it is synthetic (dry-run audio),
    # so no release qualifies
    rec = rc.analyse(d, refdir=refs)
    assert rec["coverage"]["release_compared"]["held-1"] is False
    held = {t["id"]: t for t in rec["takes"]}["held-1"]["metrics"]
    assert "declared synthetic" in held["coverage"]["excluded_why"], held["coverage"]


def _qual_inputs(tmp_path):
    """(reference, host log) for release_qualification, without audio."""
    sha, host_record = _live_stimulus("held-default", tmp_path)
    host, _ = _live_host_log("held-default", tmp_path / "h")
    rec = json.loads((rc.REFERENCES / "held-default.json").read_text())
    rec["replayed_stimulus_sha256"] = sha
    rec["schedule"] = {"kind": "live", "live_cmds_sha256": sha, "hold_timing": host_record,
                       "release_tolerance_frames": 8}
    return rec, host


_DRY_SHA = json.loads((rc.REFERENCES / "held-default.json").read_text())["cmds_sha256"]


@pytest.mark.parametrize("edit,why", [
    # the Judge's input: an all-zero hash, nothing replayed
    (lambda r: (r["schedule"].update(live_cmds_sha256="0" * 64),
                r.pop("replayed_stimulus_sha256")), "not a sha256 digest"),
    # all-zero in BOTH fields: still not a digest of anything
    (lambda r: (r["schedule"].update(live_cmds_sha256="0" * 64),
                r.update(replayed_stimulus_sha256="0" * 64)), "not a sha256 digest"),
    (lambda r: r["schedule"].update(live_cmds_sha256="LIVE"), "not a sha256 digest"),
    (lambda r: r["schedule"].update(live_cmds_sha256="A" * 64), "not a sha256 digest"),
    (lambda r: r["schedule"].pop("live_cmds_sha256"), "not a sha256 digest"),
    # a well-formed hash that is not the record's replay
    (lambda r: r["schedule"].update(live_cmds_sha256="1" * 64), "is not the record's replayed"),
    (lambda r: r.pop("replayed_stimulus_sha256"), "is not the record's replayed"),
    # the equality check's own defeating input: a dry-run render relabelled
    # with BOTH hashes set to the pinned dry-run bytes
    (lambda r: (r["schedule"].update(live_cmds_sha256=_DRY_SHA),
                r.update(replayed_stimulus_sha256=_DRY_SHA)), "pinned dry-run command's"),
    (lambda r: r["schedule"].update(synthetic="x"), "declared synthetic"),
    (lambda r: r.update(synthetic="x"), "declared synthetic"),
])
def test_a_live_label_not_backed_by_its_replay_qualifies_nothing(tmp_path, edit, why):
    rec, host = _qual_inputs(tmp_path)
    assert rc.release_qualification({"record": rec}, host) == (
        True, "the live-schedule reference binds this take's hold record")
    edit(rec)
    ok, msg = rc.release_qualification({"record": rec}, host)
    assert ok is False and "NOT EVALUATED" in msg and why in msg, msg


def test_the_identity_gate_is_blind_to_genuine_hashes_on_dry_run_audio(tmp_path):
    """Rule 8, the gate's DEFEATING INPUT, recorded as BLIND: the identity is
    three hashes in a JSON record; nothing re-derives the wav from the bytes.
    A dry-run render carrying a genuine live run's hashes, with no
    `synthetic` marker, qualifies. Only re-rendering (build box) closes it;
    the unit fixture is exactly this record and is marked synthetic."""
    rec, host = _qual_inputs(tmp_path)
    assert "synthetic" not in rec and "synthetic" not in rec["schedule"]
    assert rc.release_qualification({"record": rec}, host)[0] is True


def test_an_intentionally_displaced_release_is_caught(clean, tmp_path):
    """The control for the comparison the binding enables: held-1's release
    96 frames (2 ms) LATE must FAIL -- and is NOT caught when the release is
    excluded (the dry-run references), which is what the binding buys.
    (Measured while writing this: 240 frames early or late is also caught
    against the dry-run references, but only by held-2's repeat check, i.e.
    only because the two takes differ. A systematic hold error -- the
    historical defect, identical on every take -- would leave that blind.)"""
    d = _held_live_session(clean, tmp_path, displace=-96)
    rec = rc.analyse(d, refdir=_live_refs(tmp_path), allow_synthetic=True)
    assert rec["verdict"] == rc.FAIL, rec["reasons"]
    held = {t["id"]: t for t in rec["takes"]}["held-1"]
    assert "residual" in held["fails"], held["fails"]
    # every reason is the displaced take, directly or as held-2's repeat partner
    assert all(r.startswith(("held-1:", "held-2: repeat")) for r in rec["reasons"]), \
        rec["reasons"]
    # BLIND without the live reference: the release is outside the scored region
    rec0 = rc.analyse(d)
    assert rec0["verdict"] == rc.PASS, rec0["reasons"]


@pytest.mark.parametrize("requested,tolerance", [(1919, 8), (1920, 1)])
def test_a_live_reference_that_does_not_bind_this_hold_compares_no_release(
        clean, tmp_path, requested, tolerance):
    """A live reference for another hold, or one whose release tolerance is
    tighter than the host's bound, qualifies nothing."""
    d = _held_live_session(clean, tmp_path)
    rec = rc.analyse(d, refdir=_live_refs(tmp_path, requested=requested,
                                          tolerance=tolerance), allow_synthetic=True)
    assert rec["coverage"]["release_compared"]["held-1"] is False
    held = {t["id"]: t for t in rec["takes"]}["held-1"]["metrics"]
    assert "NOT EVALUATED" in held["coverage"]["excluded_why"]


def test_a_deceptive_host_log_is_caught_by_the_audio_not_the_log(clean, tmp_path):
    """HOLD_FORGED_LOG: the host's rows, bytes-as-logged and record all claim
    the requested 1920 while the wire carried a due 600 frames later, so the
    board releases 12.5 ms late. Nothing in the log can see that -- it is
    self-consistent by construction -- so the take's AUDIO must, and against
    a live-schedule reference it FAILS. (Against the dry-run references a
    600-frame displacement happens to break the coarse alignment and fails
    as `routing` -- the wrong property, at an ncc of 0.489 against a 0.5
    limit -- so nothing is asserted about that path.)"""
    d = _held_live_session(clean, tmp_path, displace=-600, held1_inject="HOLD_FORGED_LOG")
    s = json.loads((d / "session.json").read_text())
    held = next(t for t in s["takes"] if t["id"] == "held-1")
    host = json.loads((d / f"{held['host_capture']}.plan.json").read_text())
    # BLIND: every log-level check passes, and the log's hold is the claim
    assert rc.host_log_problems(host) == [] and rc.planned_hold(host) == 1920
    rec = rc.analyse(d, refdir=_live_refs(tmp_path), allow_synthetic=True)
    assert rec["verdict"] == rc.FAIL, rec["reasons"]
    assert {t["id"]: t for t in rec["takes"]}["held-1"]["fails"], rec["reasons"]


def test_a_log_whose_row_is_not_its_own_packet_is_refused(tmp_path):
    """The log-level guard (rule 8): an event row whose `due` is not the due
    its own packet bytes carry. Its defeating input is the forged log above,
    which rewrites both consistently -- only the device or the audio sees it."""
    host, _ = _live_host_log("held-default", tmp_path / "h")
    assert rc.host_log_problems(host) == []
    ev = next(r for r in host["rows"] if r["kind"] == "event")
    ev["due"] += 600
    host["hold_timing"]["gate_off_due"] += 600
    host["hold_timing"]["gate_apply_estimate"] += 600
    host["hold_timing"]["gate_apply_bounds"] = [x + 600 for x in
                                                host["hold_timing"]["gate_apply_bounds"]]
    probs = rc.host_log_problems(host)
    assert any("the packet carries due" in p for p in probs), probs


@pytest.mark.parametrize("edit,why", [
    (lambda h: h.update(gate_off_due=h["gate_off_due"] + 1), "event row"),
    (lambda h: h.update(hold_bound_frames=99), "exceeds the declared"),
    (lambda h: h.update(verdict="REFUSED"), "did not establish"),
    (lambda h: h.update(gate_apply_estimate=h["gate_apply_estimate"] - 50,
                        gate_off_due=h["gate_off_due"]), "outside its own bracket"),
])
def test_a_hold_record_that_disagrees_with_its_log_is_refused(tmp_path, edit, why):
    host, _ = _live_host_log("held-default", tmp_path / "h")
    assert rc.hold_timing_problems(host) == []
    edit(host["hold_timing"])
    probs = rc.hold_timing_problems(host)
    assert any(why in p for p in probs), probs


def test_a_live_host_log_of_another_command_is_still_refused(clean, tmp_path):
    """The control for the relaxed ordering: a genuinely foreign command (the
    pulse preset's live log under the saw take) must still be refused."""
    d = _as_real(clean, tmp_path)
    _live_host_log("held-m5a-pulse", d / "host" / "tone-1")
    rec = rc.analyse(d)
    assert rec["verdict"] == rc.REFUSED
    assert "tone-1 is not the released command held-m5a-saw" in rec["reasons"][0]


def test_the_stub_analysers_clean_run_is_not_counted_as_fine(tmp_path):
    """Judge N1: with no calibration record the known answers were skipped and
    the stub's clean run counted as ok."""
    res = rc.run_controls(tmp_path, analyser=rc.stub_analyse, defects=["noise"])
    assert res["clean"]["ok"] is False


def test_a_reference_that_is_not_the_releases_refuses_inside_the_analysis(clean, tmp_path):
    """Judge N2: the reference binding (pinned bytes, image bitstream, image
    sources) is checked in the analysis the trial runs, not only in CI. The
    wav is untouched, so the old wav-hash check alone passed this."""
    ref = tmp_path / "ref"
    shutil.copytree(rc.REFERENCES, ref)
    p = ref / "held-default.json"
    r = json.loads(p.read_text())
    r["image"]["bitstream_sha256"] = "1a562b42" + r["image"]["bitstream_sha256"][8:]
    p.write_text(json.dumps(r))
    rec = rc.analyse(_as_real(clean, tmp_path), ref)
    assert rec["verdict"] == rc.REFUSED
    assert "held-default" in rec["reasons"][0] and "bitstream" in rec["reasons"][0]



# ---- plan089: the relaxed ordering must not hide a real difference ----------
def _live_plan(tmp_path, key="held-default"):
    host, _ = _live_host_log(key, tmp_path / "h")
    ref = json.loads((rc.REFERENCES / f"{key}.plan.json").read_text())
    assert rc.command_identity(ref, host) == []
    return ref, host


def _rows(plan, kind):
    return [r for r in plan["rows"] if r["kind"] == kind]


def _mutate_wrong_value(p):
    _rows(p, "write")[5]["expect"]["data"] ^= 1


def _mutate_missing_write(p):
    p["rows"].remove(_rows(p, "write")[7])


def _mutate_duplicate_write(p):
    w = _rows(p, "write")[7]
    p["rows"].insert(p["rows"].index(w), json.loads(json.dumps(w)))


def _mutate_reordered_writes(p):
    w = _rows(p, "write")
    i, j = p["rows"].index(w[3]), p["rows"].index(w[4])
    p["rows"][i], p["rows"][j] = p["rows"][j], p["rows"][i]


def _mutate_event_before_setup(p):
    _rows(p, "event")[0]["due"] = _rows(p, "write")[-1]["apply_frame"] - 10


@pytest.mark.parametrize("mutate", [_mutate_wrong_value, _mutate_missing_write,
                                    _mutate_duplicate_write, _mutate_reordered_writes,
                                    _mutate_event_before_setup])
def test_the_identity_gate_still_sees_real_differences_in_a_live_log(tmp_path, mutate):
    """Separating writes from events must keep write order, values and
    multiplicity, and the setup -> event relationship on the device timeline."""
    ref, host = _live_plan(tmp_path)
    mutate(host)
    assert rc.command_identity(ref, host) != []


def test_a_changed_event_interval_is_seen(tmp_path):
    ref, host = _live_plan(tmp_path, "phrase-m5a")
    _rows(host, "event")[3]["due"] += 1                 # one frame late
    assert rc.command_identity(ref, host) != []


# ---- Judge #299 re-review (cb29c90): C1 transcript structure, C2 host-log schema --
_SHA = "a66c9349ef9b5572f3c3453777f38e1b143136755620fe419e730d6f5c84cb95"
_HEAD = ("release_manifest: BOUND -- ok\nexit 0\n"
         f"{_SHA}  fpga/reports/arty/integrated-baseline-2025.1/arty.bit\n"
         "openFPGALoader v1.1.1\n")
_PROG_OK = "Load SRAM: [====] 100.00%\nDone\nexit 0\n"
_PROG_FAIL = "Error: JTAG init failed, no device found\nexit 1\n"
STRUCTURE_BAD = {
    "no programmer block at all": ("release_manifest: BOUND\nexit 0\n"
                                   f"{_SHA}  fpga/reports/arty/integrated-baseline-2025.1/"
                                   "arty.bit\nexit 0\n"),
    "programmer exit 1, then a stray exit 0": _HEAD + _PROG_FAIL + "exit 0\n",
    "programmer exit 1, then the manifest re-run": _HEAD + _PROG_FAIL
    + "release_manifest: BOUND\nexit 0\n",
    "manifest check after the programmer": (f"{_SHA}  fpga/reports/arty/integrated-baseline"
                                            "-2025.1/arty.bit\nopenFPGALoader v1.1.1\n"
                                            + _PROG_OK + "release_manifest: BOUND\nexit 0\n"),
}


@pytest.mark.parametrize("name", sorted(STRUCTURE_BAD))
def test_the_programmer_block_must_itself_exit_0(name):
    """Judge C1: a failed programming step followed by ANY `exit 0` was
    accepted, because only the transcript's tail was read."""
    assert rc.transcript_problems(STRUCTURE_BAD[name], _SHA) != [], name


_RETRY = (f"{_SHA}  fpga/reports/arty/integrated-baseline-2025.1/arty.bit\n"
          "openFPGALoader v1.1.1\n" + _PROG_OK)
STRUCTURE_BAD["retry that names no image"] = _HEAD + _PROG_FAIL + "openFPGALoader v1.1.1\n" \
    + _PROG_OK
STRUCTURE_BAD["retry for another image"] = _HEAD + _PROG_FAIL + _RETRY.replace("a66c9349",
                                                                             "1a562b42")
STRUCTURE_BAD["truncated: programmer output with no exit"] = _HEAD + "Load SRAM: [==\n"


@pytest.mark.parametrize("text", [_HEAD + _PROG_OK, _HEAD + _PROG_FAIL + _RETRY],
                         ids=["procedure", "complete retry after a failure"])
def test_the_procedures_transcript_and_a_programmer_retry_are_accepted(text):
    """The structural gate must stay satisfiable, including a legitimate retry."""
    assert rc.transcript_problems(text, _SHA) == []


def _live_session(clean, tmp_path, transcript=GOOD_TRANSCRIPT):
    d = _as_real(clean, tmp_path, transcript)
    s = json.loads((d / "session.json").read_text())
    for i, t in enumerate(s["takes"]):
        if t["command_id"] != "silence":
            _live_host_log(t["command_id"], d / t["host_capture"], epoch=5000 * i)
    return d


def _cli_analyse(d):
    import subprocess
    r = subprocess.run([sys.executable, str(rc.ROOT / "tools/r0_capture.py"), "analyse",
                        "--bundle", str(d)], capture_output=True, text=True, cwd=rc.ROOT)
    return r.returncode, r.stdout + r.stderr


def test_a_failed_programmer_with_a_stray_exit_0_is_no_verdict_end_to_end(clean, tmp_path):
    """Judge C1 end to end: the as-real live-log session whose programmer
    block failed (`Error: JTAG init failed / exit 1`) and then shows a stray
    `exit 0` gave `analyse` PASS, exit 0. It must be NO VERDICT, exit 2."""
    bad = GOOD_TRANSCRIPT.rsplit("exit 0", 1)[0] + "Error: JTAG init failed\nexit 1\nexit 0\n"
    code, out = _cli_analyse(_live_session(clean, tmp_path, bad))
    assert code == 2, out[-600:]
    assert "program" in out


@pytest.mark.parametrize("kind,field", [("write", "apply_frame"), ("event", "due"),
                                        ("write", "expect")])
def test_a_host_log_missing_a_timing_field_is_no_verdict_not_a_crash(clean, tmp_path,
                                                                     kind, field):
    """Judge C2: a live host log whose writes lack apply_frame crashed the CLI
    with KeyError, exit 1 -- this tool's FAIL code, so missing evidence was
    scored as a measured failure. The schema is a precondition: exit 2."""
    d = _live_session(clean, tmp_path)
    p = d / "host" / "held-1.plan.json"
    plan = json.loads(p.read_text())
    for r in plan["rows"]:
        if r["kind"] == kind:
            r.pop(field, None)
    p.write_text(json.dumps(plan))
    code, out = _cli_analyse(d)
    assert code == 2, out[-600:]
    assert "held-1" in out and field in out


def test_an_internal_exception_is_an_execution_error_not_a_fail_nor_stale(
        clean, tmp_path, monkeypatch):
    """plan090: an unexpected exception is an EXECUTION ERROR -- not a measured
    FAIL (exit 1), not relabelled as a refusal -- with its diagnostics kept;
    and an OLD result already in the bundle is replaced by this run's record,
    never reused as the current result."""
    d = _as_real(clean, tmp_path)
    (d / "analysis.json").write_text(json.dumps({"verdict": "PASS", "reasons": []}))

    def boom(*a, **k):
        raise ZeroDivisionError("an estimator blew up")
    monkeypatch.setattr(rc, "calibrate", boom)
    code = rc.main(["analyse", "--bundle", str(d)])
    rec = json.loads((d / "analysis.json").read_text())
    assert code == 3
    assert rec["verdict"] == rc.ERROR and "ZeroDivisionError" in rec["reasons"][0]
    assert any("ZeroDivisionError" in ln for ln in rec["traceback"])


def test_a_retry_keeps_the_failed_attempt_in_the_record(clean, tmp_path):
    """plan090: a genuine retry is accepted, and the failure is not dropped."""
    rec = rc.analyse(_as_real(clean, tmp_path, _HEAD + _PROG_FAIL + _RETRY))
    assert rec["verdict"] == rc.PASS, rec["reasons"]
    att = rec["identity"]["programming_attempts"]
    assert [a["result"] for a in att] == ["FAILED", "success"]
    assert att[0]["exit"] == 1 and att[1]["image_sha256"] == _SHA


def _required_only_trial(tmp_path, monkeypatch, bundle):
    """T-PHYSICAL's registered required child through tools/trial.py, with the
    (12-minute) control stripped so each entry-point check runs in seconds."""
    import trial
    reg = json.loads((rc.ROOT / "docs/trials.json").read_text())
    reg["trials"]["T-PHYSICAL"]["modes"]["capture"]["controls"] = []
    rp = tmp_path / "trials-required-only.json"
    rp.write_text(json.dumps(reg))
    monkeypatch.setenv("R0_CAPTURE_BUNDLE", str(bundle))
    _, rec = trial.run_trial("T-PHYSICAL", registry=rp, env_spec=_env_spec(tmp_path),
                             out_base=tmp_path / "trials")
    return rec["children"][0]


def test_a_missing_timing_field_is_no_verdict_at_both_entry_points(clean, tmp_path,
                                                                   monkeypatch):
    """plan090: the same malformed bundle, direct CLI and trial wrapper; and
    an old PASS in the bundle is replaced by this run's refusal."""
    import trial
    d = _live_session(clean, tmp_path)
    p = d / "host" / "held-1.plan.json"
    plan = json.loads(p.read_text())
    for r in plan["rows"]:
        if r["kind"] == "write":
            r.pop("apply_frame", None)
    p.write_text(json.dumps(plan))
    (d / "analysis.json").write_text(json.dumps({"verdict": "PASS", "reasons": []}))
    code, out = _cli_analyse(d)
    assert code == 2 and "apply_frame" in out, out[-500:]
    assert json.loads((d / "analysis.json").read_text())["verdict"] == rc.REFUSED
    child = _required_only_trial(tmp_path, monkeypatch, d)
    assert child["verdict"] == trial.NO_VERDICT
    assert "apply_frame" in child["reasons"][0]


def test_a_real_sound_defect_is_still_a_measured_fail_at_both_entry_points(tmp_path,
                                                                           monkeypatch):
    """plan090: the repair must not turn every bad outcome into NO VERDICT. A
    real-looking session with a 20 ms dropout is FAIL through the CLI (exit
    1) and through the trial wrapper."""
    import trial
    src = rc.synth_session(tmp_path / "dropout-src", defect="dropout")
    d = _as_real(src, tmp_path)
    code, out = _cli_analyse(d)
    assert code == 1 and "dropout" in out, out[-500:]
    child = _required_only_trial(tmp_path, monkeypatch, d)
    assert child["verdict"] == trial.FAIL
    assert any("dropout" in r for r in child["reasons"])


def test_session_properties_say_where_they_were_not_evaluated(clean, tmp_path):
    """Judge N1: `properties` read `residual: PASS` although no held take
    was scored for it. A PASS must name the takes it does not cover."""
    rec = rc.analyse(_live_session(clean, tmp_path))
    assert rec["verdict"] == rc.PASS, rec["reasons"]
    for p in ("residual", "timing", "gain", "clock", "dropout"):
        v = rec["properties"][p]
        assert v["verdict"] == "PASS", (p, v)
        assert {"held-1", "held-2", "tone-1", "pulse-1"} <= set(v["not_evaluated_on"]), (p, v)
        assert "held-1" not in v["evaluated_on"] and v["evaluated_on"], (p, v)


@pytest.mark.parametrize("verdict", [rc.PASS, rc.FAIL])
def test_an_io_error_at_the_final_record_write_is_an_execution_error(
        tmp_path, monkeypatch, capsys, verdict):
    """#340: if the FINAL atomic write of analysis.json fails (os.replace
    raising OSError), the run is an EXECUTION ERROR -- exit 3, not the
    exception escaping main() as exit 1 (this tool's FAIL code) -- the bundle
    keeps this run's in-progress ERROR record, and no temporary file is left
    behind. Parametrised over PASS and FAIL: neither measured verdict may be
    reported when it never reached disk."""
    d = tmp_path / "bundle"
    d.mkdir()
    monkeypatch.setattr(rc, "analyse", lambda *a, **k: {
        "schema": rc.RECORD_SCHEMA, "verdict": verdict, "reasons": ["stub"]})
    real_replace = rc.os.replace
    calls = []

    def replace(src, dst):
        calls.append(dst)
        if len(calls) >= 2:                          # the FINAL write
            raise OSError(28, "No space left on device")
        return real_replace(src, dst)
    monkeypatch.setattr(rc.os, "replace", replace)
    code = rc.main(["analyse", "--bundle", str(d)])
    out = capsys.readouterr().out
    assert len(calls) == 2, calls
    assert code == 3, out
    rec = json.loads((d / "analysis.json").read_text())
    assert rec["verdict"] == rc.ERROR and "in progress" in rec["reasons"][0]
    assert sorted(p.name for p in d.iterdir()) == ["analysis.json"]
    assert "ERROR" in out and "OSError" in out


def test_an_io_error_at_the_in_progress_write_is_an_execution_error(
        tmp_path, monkeypatch, capsys):
    """#340 companion: the FIRST write failing is also exit 3 with no tmp
    left, and the analysis is not run (there is nowhere to record it)."""
    d = tmp_path / "bundle"
    d.mkdir()
    ran = []
    monkeypatch.setattr(rc, "analyse", lambda *a, **k: ran.append(1))

    def replace(src, dst):
        raise OSError(13, "Permission denied")
    monkeypatch.setattr(rc.os, "replace", replace)
    code = rc.main(["analyse", "--bundle", str(d)])
    out = capsys.readouterr().out
    assert code == 3, out
    assert not ran
    assert list(d.iterdir()) == []


def test_a_live_schedule_render_never_overwrites_the_frozen_references():
    """#306: legacy R0 evidence is frozen. A live-schedule render REFUSES the
    frozen directory before anything is simulated or written."""
    import r0_reference as rr
    before = {p.name: p.read_bytes() for p in rc.REFERENCES.iterdir() if p.is_file()}
    code = rr.main(["render", "--out", str(rc.REFERENCES), "--schedule", "live",
                    "--commands", "held-default"])
    assert code == 2
    assert {p.name: p.read_bytes() for p in rc.REFERENCES.iterdir() if p.is_file()} == before
