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
import wave

import numpy as np
import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import refusal                                                   # noqa: E402
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


def _copy_reference_corpus(tmp_path):
    ref = tmp_path / "ref"
    shutil.copytree(rc.REFERENCES, ref)
    return ref


def test_a_24bit_reference_with_a_matching_hash_refuses_not_errors(clean, tmp_path):
    """#784 confirmed site 2: `load_reference` reaches rr.read_wav_int16 (the
    24-bit refusal in r0_reference) only when the WAV bytes MATCH the recorded
    wav_sha256 -- without the matching hash the altered-reference guard above
    refuses first, correctly, and the defect is never exercised. A reference
    side refusing is a REFUSED verdict, not an execution error."""
    import wave
    ref = _copy_reference_corpus(tmp_path)
    p = ref / "held-default.wav"
    with wave.open(str(p), "rb") as w:
        sr, ch, n = w.getframerate(), w.getnchannels(), w.getnframes()
        x = np.frombuffer(w.readframes(n), dtype="<i2").astype("<i4")
    with wave.open(str(p), "wb") as w:
        w.setnchannels(ch)
        w.setsampwidth(3)
        w.setframerate(sr)
        w.writeframes(x.tobytes())
    rec_path = ref / "held-default.json"
    record = json.loads(rec_path.read_text())
    record["wav_sha256"] = rc.sha256_file(p)
    rec_path.write_text(json.dumps(record))
    rec = rc.analyse(clean, ref, allow_synthetic=True)
    assert rec["verdict"] == rc.REFUSED, rec["reasons"]
    assert "16-bit" in rec["reasons"][0]


# ---- the reference is the take its record describes ---------------------------
_REFERENCE_KEYS = ("held-default", "held-m5a-saw", "held-m5a-pulse", "phrase-m5a",
                   "demo", "bar808-full")


def _manifest():
    return rc.rr.load_manifest(rc.rr.IMAGES["r0"]["manifest"])


def _committed_frames(key="held-default"):
    with wave.open(str(rc.REFERENCES / f"{key}.wav"), "rb") as w:
        return w.getnframes()


def _rewrite_record(ref, key, fn):
    p = ref / f"{key}.json"
    rec = json.loads(p.read_text())
    fn(rec)
    p.write_text(json.dumps(rec))


def _reference_of_length(tmp_path, n_frames, *, key="held-default", channels=1):
    """A copy of the corpus whose `key` WAV holds exactly `n_frames` frames:
    the committed take's prefix, zero-padded past its end, written as a
    well-formed WAV (header and data agree, so nothing but the length shows
    the edit) with its record's wav_sha256 rewritten to match."""
    ref = _copy_reference_corpus(tmp_path)
    p = ref / f"{key}.wav"
    with wave.open(str(p), "rb") as w:
        sr, n = w.getframerate(), w.getnframes()
        mono = np.frombuffer(w.readframes(n), dtype="<i2")
    x = np.zeros(n_frames, "<i2")
    x[: min(n, n_frames)] = mono[: n_frames]
    with wave.open(str(p), "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(np.repeat(x[:, None], channels, axis=1).tobytes())
    _rewrite_record(ref, key, lambda rec: rec.update(wav_sha256=rc.sha256_file(p)))
    return ref


@pytest.mark.parametrize("frac", [0.99, 0.95, 0.40])
def test_a_cut_reference_with_a_rewritten_hash_refuses(tmp_path, frac):
    """99 / 95 / 40 % of held-default.wav, re-hashed: both readers must refuse,
    naming the file and both lengths, and name the same problem."""
    n = _committed_frames()
    kept = int(n * frac)
    assert kept < n
    ref = _reference_of_length(tmp_path, kept)
    with pytest.raises(rc.Refused) as e:
        rc.load_reference(ref, "held-default")
    msg = str(e.value)
    assert "held-default.wav" in msg and f"{kept}" in msg and f"{n}" in msg, msg
    why = rc.rr.reference_problems(ref, "held-default", _manifest())
    assert why == [f"held-default: {msg}"], why


@pytest.mark.parametrize("frac", [0.99, 0.95, 0.40])
def test_a_cut_reference_refuses_the_verdict_through_the_path_that_ships(clean, tmp_path,
                                                                          frac):
    """The same cut through `analyse`, the entry the trial runs: a REFUSED
    verdict naming the take -- not a verdict computed on its truncated prefix,
    and not a refusal for some other reason the cut happens to cause."""
    n = _committed_frames()
    kept = int(n * frac)
    ref = _reference_of_length(tmp_path, kept)
    rec = rc.analyse(clean, ref, allow_synthetic=True)
    assert rec["verdict"] == rc.REFUSED, (rec["verdict"], rec["reasons"])
    assert f"{kept}" in rec["reasons"][0] and f"{n}" in rec["reasons"][0], rec["reasons"]


def test_the_hash_still_refuses_a_take_of_the_right_length(tmp_path):
    """Control: the length check must not displace the hash check. One bit
    flipped in the last sample -- the length untouched, the hash not rewritten
    -- still refuses on the hash, in both readers."""
    ref = _copy_reference_corpus(tmp_path)
    p = ref / "held-default.wav"
    b = bytearray(p.read_bytes())
    b[-2] ^= 1
    p.write_bytes(bytes(b))
    with pytest.raises(rc.Refused, match="missing or altered"):
        rc.load_reference(ref, "held-default")
    assert rc.rr.reference_problems(ref, "held-default", _manifest()) == \
        ["held-default: wav missing or altered"]


@pytest.mark.parametrize("key", _REFERENCE_KEYS)
def test_every_committed_reference_is_the_take_its_record_describes(key):
    """Control: the guard passes the six references it was written for -- the
    committed take is `periods` frames long and its calibration window lies
    inside it -- and both readers say so."""
    ref = rc.load_reference(rc.REFERENCES, key)
    rec = ref["record"]
    assert ref["x"].size == rec["periods"]
    assert 0 <= ref["cal"][0] < ref["cal"][1] <= rec["periods"]
    assert rc.rr.reference_problems(rc.REFERENCES, key, _manifest()) == []


@pytest.mark.parametrize("damage", [
    lambda rec: rec.pop("periods"),
    lambda rec: rec.update(periods=None),
    lambda rec: rec.update(periods="51384"),
    lambda rec: rec.update(periods=51384.0),
], ids=["field-deleted", "none", "string", "float-of-the-right-value"])
def test_a_record_that_declares_no_frame_count_refuses_rather_than_skips(tmp_path, damage):
    """The guard's own defeating input, asked at write time (verification rule
    8): delete the field it compares against. The WAV is the committed one,
    byte for byte. A missing count must REFUSE -- a `rec.get` that falls
    through would hand the guard nothing to compare, and it would pass."""
    ref = _copy_reference_corpus(tmp_path)
    _rewrite_record(ref, "held-default", damage)
    with pytest.raises(rc.Refused, match=r"held-default\.wav.*not a frame count"):
        rc.load_reference(ref, "held-default")
    why = rc.rr.reference_problems(ref, "held-default", _manifest())
    assert len(why) == 1 and "not a frame count" in why[0], why


@pytest.mark.parametrize("mutate", [
    lambda cal: cal.update(stop=10 ** 9),
    lambda cal: cal.pop("stop"),
    lambda cal: cal.update(stop=cal["start"]),
    lambda cal: cal.update(start=-1),
], ids=["stop-far-past-the-take", "stop-missing", "empty", "starts-before-the-take"])
def test_a_calibration_window_outside_the_take_refuses(tmp_path, mutate):
    """The window the record indexes the take by must lie inside it. On main a
    stop past the end was sliced away silently (numpy clips) and a missing stop
    was a KeyError: an execution error, so no verdict at all."""
    ref = _copy_reference_corpus(tmp_path)

    def damage(rec):
        cal = dict(rec["calibration"])
        mutate(cal)
        rec["calibration"] = cal
    _rewrite_record(ref, "held-default", damage)
    with pytest.raises(rc.Refused, match="calibration window"):
        rc.load_reference(ref, "held-default")
    why = rc.rr.reference_problems(ref, "held-default", _manifest())
    assert len(why) == 1 and "calibration window" in why[0], why


def test_an_empty_take_refuses_even_with_its_record_made_to_agree(tmp_path):
    """Edge: `periods` rewritten to 0 as well, so the lengths agree -- and no
    calibration window can lie inside an empty take."""
    ref = _reference_of_length(tmp_path, 0)
    _rewrite_record(ref, "held-default", lambda rec: rec.update(periods=0))
    with pytest.raises(rc.Refused, match="calibration window"):
        rc.load_reference(ref, "held-default")
    assert rc.rr.reference_problems(ref, "held-default", _manifest())


def test_length_is_counted_in_frames_not_samples(tmp_path):
    """Edge: a two-channel take of half the frames carries the same number of
    samples. `load_reference` indexes channel 0, so what it reads is frames,
    and the guard must count what it reads."""
    n = _committed_frames()
    ref = _reference_of_length(tmp_path, n // 2, channels=2)
    with pytest.raises(rc.Refused) as e:
        rc.load_reference(ref, "held-default")
    assert f"{n // 2}" in str(e.value) and f"{n}" in str(e.value), str(e.value)
    assert rc.rr.reference_problems(ref, "held-default", _manifest())


def test_a_two_channel_take_of_the_whole_length_still_loads(tmp_path):
    """Its twin: the same samples over the full `periods` frames IS the take
    the record describes. A guard that counted samples would refuse it."""
    n = _committed_frames()
    ref = _reference_of_length(tmp_path, n, channels=2)
    loaded = rc.load_reference(ref, "held-default")
    assert loaded["x"].size == n
    assert rc.rr.reference_problems(ref, "held-default", _manifest()) == []


def test_a_longer_take_refuses_too(tmp_path):
    """The other direction: padded past its end and re-hashed, the record
    untouched."""
    n = _committed_frames()
    ref = _reference_of_length(tmp_path, n + 1000)
    with pytest.raises(rc.Refused) as e:
        rc.load_reference(ref, "held-default")
    assert f"{n + 1000}" in str(e.value) and f"{n}" in str(e.value), str(e.value)


def test_a_take_truncated_on_disk_refuses_though_its_header_still_promises_the_whole(tmp_path):
    """The accidental shape of the same cut: a half-copied file whose header
    still declares the committed length. The guard must count the frames it
    can read, not the frames the header promises."""
    n = _committed_frames()
    frames_lost = 1000
    ref = _copy_reference_corpus(tmp_path)
    p = ref / "held-default.wav"
    b = bytearray(p.read_bytes())
    del b[-2 * frames_lost:]
    p.write_bytes(bytes(b))
    _rewrite_record(ref, "held-default", lambda rec: rec.update(wav_sha256=rc.sha256_file(p)))
    with wave.open(str(p), "rb") as w:
        assert w.getnframes() == n, "the header must still promise the whole take"
    with pytest.raises(rc.Refused,
                       match=f"{n - frames_lost} frames, its record describes {n} periods"):
        rc.load_reference(ref, "held-default")
    assert rc.rr.reference_problems(ref, "held-default", _manifest())


def test_the_ci_check_sees_a_non_16bit_take_the_trial_refuses(tmp_path):
    """Agreement beyond length: `reference_problems` reads the WAV the trial
    reads, so a 24-bit take -- refused by `load_reference` since #784 -- is the
    CI check's problem too, not a pass on its hash alone. Caught by the common
    base: this refusal is raised by rr.read_wav_int16, a sibling of rc.Refused."""
    ref = _copy_reference_corpus(tmp_path)
    p = ref / "held-default.wav"
    with wave.open(str(p), "rb") as w:
        sr, n = w.getframerate(), w.getnframes()
        x = np.frombuffer(w.readframes(n), dtype="<i2").astype("<i4")
    with wave.open(str(p), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(3)
        w.setframerate(sr)
        w.writeframes(x.tobytes())
    _rewrite_record(ref, "held-default", lambda rec: rec.update(wav_sha256=rc.sha256_file(p)))
    with pytest.raises(refusal.Refused, match="16-bit"):
        rc.load_reference(ref, "held-default")
    why = rc.rr.reference_problems(ref, "held-default", _manifest())
    assert len(why) == 1 and "16-bit" in why[0], why


def test_an_unreadable_release_manifest_refuses_not_errors(tmp_path):
    """#784 site 2, second reachable path: analyse -> resolve_image ->
    rr.load_manifest refusing (unreadable manifest) yields REFUSED, not
    ERROR."""
    bad = tmp_path / "manifest.json"
    bad.write_text("{ this is not json")
    rec = rc.analyse(tmp_path / "bundle-unused", manifest_path=bad)
    assert rec["verdict"] == rc.REFUSED, rec["reasons"]
    assert "unreadable" in rec["reasons"][0]


def test_an_internal_valueerror_is_still_an_execution_error(clean, tmp_path, monkeypatch):
    """#784: the repair must not widen the deliberately-total handler -- a
    genuine implementation error inside the same try is still an EXECUTION
    ERROR (plan090), proving the sibling-class handler catches only refusals."""
    d = _copy(clean, tmp_path)

    def boom(*a, **k):
        raise ValueError("an implementation error")
    monkeypatch.setattr(rc, "check_session", boom)
    rec = rc.analyse(d, allow_synthetic=True)
    assert rec["verdict"] == rc.ERROR and "ValueError" in rec["reasons"][0]


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


def _live_host_log(key, prefix, epoch=0):
    """What the shipped CLI's LIVE path writes with --capture, driven against
    the repository's scripted device (fpga/verify_rolling_playback.Harness):
    the closest thing to hardware this checkout has."""
    import contextlib
    import io
    import r0_reference as rr
    import uart_host as uh
    import verify_rolling_playback as vrp
    man = rr.load_manifest()
    argv = rr.cli_argv(man["commands"][key]["command"]) + ["--port", "sim",
                                                           "--capture", str(prefix)]
    h = vrp.Harness(epoch)
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        code = uh.main(argv, bridge_factory=h.factory)
    assert code == 0, (key, code)
    live = [w for w in h.sim.writes if w[5] == "live"]
    ev = [w for w in h.sim.writes if w[5] == "event"]
    device_hold = ((ev[0][0] - live[-1][0]) & 0xFFFF) if (live and ev) else None
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
    """Judge B2: the live planned hold is 3121 frames against the dry-run's
    1920 -- 1201 frames, 25 ms, not "a few". On the scripted device the gate-
    off actually fires 3155 frames after the gate, so the host log's planned
    hold is itself 34 frames off what the device did. Recorded here as
    measured numbers, so a change in the CLI's anchoring shows up."""
    ref = json.loads((rc.REFERENCES / "held-default.plan.json").read_text())
    host, device_hold = _live_host_log("held-default", tmp_path / "h")
    assert rc.planned_hold(ref) == 1920
    assert rc.planned_hold(host) == 3121
    assert device_hold == 3155
    assert abs(device_hold - rc.planned_hold(host)) <= rc.HOLD_UNCERTAINTY_FRAMES


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
    assert held["hold_offset_frames"] == 1201 and held["release_compared"] is False


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
