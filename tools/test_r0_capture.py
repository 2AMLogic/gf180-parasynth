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
    rec = rc.analyse(d)
    assert rec["verdict"] == rc.REFUSED, rec["reasons"]
    assert why in rec["reasons"][0]


def test_missing_wav_refuses(clean, tmp_path):
    d = _copy(clean, tmp_path)
    (d / "takes" / "tone-1.wav").unlink()
    rec = rc.analyse(d)
    assert rec["verdict"] == rc.REFUSED and "tone-1.wav not found" in rec["reasons"][0]


def test_wrong_sample_rate_file_refuses(clean, tmp_path):
    d = _copy(clean, tmp_path)
    from scipy.io import wavfile
    wavfile.write(str(d / "takes" / "tone-1.wav"), 44100, np.zeros((44100, 4), np.int32))
    rec = rc.analyse(d)
    assert rec["verdict"] == rc.REFUSED and "44100 Hz, not 48000" in rec["reasons"][0]


def test_host_log_of_another_command_refuses(clean, tmp_path):
    d = _copy(clean, tmp_path)
    shutil.copyfile(rc.REFERENCES / "held-m5a-pulse.plan.json", d / "host" / "tone-1.plan.json")
    rec = rc.analyse(d)
    assert rec["verdict"] == rc.REFUSED and "is not the released command" in rec["reasons"][0]


def test_truncated_take_refuses(clean, tmp_path):
    d = _copy(clean, tmp_path)
    sr, y = rc.read_capture(d / "takes" / "held-1.wav")
    rc.write_capture_wav(d / "takes" / "held-1.wav", y[: y.shape[0] // 3])
    rec = rc.analyse(d)
    assert rec["verdict"] == rc.REFUSED and "before its reference does" in rec["reasons"][0]


def test_altered_reference_refuses(clean, tmp_path):
    ref = tmp_path / "ref"
    shutil.copytree(rc.REFERENCES, ref)
    p = ref / "held-default.wav"
    b = bytearray(p.read_bytes())
    b[-2] ^= 1
    p.write_bytes(bytes(b))
    rec = rc.analyse(clean, ref)
    assert rec["verdict"] == rc.REFUSED and "missing or altered" in rec["reasons"][0]


# ---- the controls -------------------------------------------------------------
def test_clean_synthetic_session_passes_with_its_known_answers(clean):
    rec = rc.analyse(clean)
    assert rec["verdict"] == rc.PASS, rec["reasons"]
    c = rec["calibration"]
    assert abs(c["ppm"] - rc.CLEAN["ppm"]) < 2.0
    assert abs(c["gain_db"] - rc.dbfs(rc.CLEAN["gain"])) < 0.2
    delays = {t["id"]: t["metrics"].get("delay_samples") for t in rec["takes"]}
    for i, (tid, cmd) in enumerate(rc.SYNTH_TAKES):
        if cmd != "silence":
            assert abs(delays[tid] - rc.CLEAN["delays"][i]) < 0.25, (tid, delays[tid])
    assert rec["identity"]["programming"].startswith("programming transcript only")
    assert "UNOBSERVABLE" in rec["properties"]["channel-identity"]


def test_start_red_the_stub_analyser_catches_nothing(tmp_path):
    res = rc.run_controls(tmp_path, analyser=rc.stub_analyse,
                          defects=["dropout", "clipping", "noise"])
    assert not res["all_caught"]
    assert not any(d["caught"] for d in res["defects"].values())


def test_a_host_planned_hold_offset_is_read_from_the_log_and_stops_the_release_compare(
        clean, tmp_path):
    """On hardware uart_host anchors the gate-off to an OBSERVED frame, so its
    planned hold can differ from the dry-run's; the release then cannot be
    compared with this reference, and the record must say so rather than
    FAIL the take for timing it was never asked to reproduce."""
    d = _copy(clean, tmp_path)
    p = d / "host" / "held-2.plan.json"
    plan = json.loads(p.read_text())
    ev = [r for r in plan["rows"] if r["kind"] == "event"][0]
    ev["due"] += 30
    p.write_text(json.dumps(plan))
    rec = rc.analyse(d)
    t = {x["id"]: x for x in rec["takes"]}["held-2"]
    assert t["metrics"]["hold_offset_frames"] == 30
    assert t["metrics"]["release_compared"] is False
    assert rec["verdict"] == rc.PASS, rec["reasons"]


def test_t_physical_trial_without_a_capture_is_no_verdict_with_its_control_caught(
        tmp_path, monkeypatch):
    """The registered trial, end to end through tools/trial.py: with no capture
    session it is NO VERDICT for the operator-blocked reason, never PASS, and
    its synthetic-defect control is caught. The environment spec is this
    interpreter's (the trial's own pins are checked by the CI job that
    bootstraps them); nothing else is stubbed."""
    import trial
    spec = tmp_path / "env.json"
    spec.write_text(json.dumps({
        "version": "test-env/1", "python": "%d.%d" % sys.version_info[:2], "packages": {},
        "toolchain": {"installer": "none", "bin": "nobin", "record": "none", "tools": {}}}))
    monkeypatch.setenv("R0_CAPTURE_BUNDLE", str(tmp_path / "no-session-here"))
    run_dir, rec = trial.run_trial("T-PHYSICAL", env_spec=spec, out_base=tmp_path / "trials")
    assert rec["execution"]["status"] == "complete", rec["execution"]
    assert rec["verdict"] == trial.NO_VERDICT
    child = rec["children"][0]
    assert child["verdict"] == trial.NO_VERDICT
    assert "operator-blocked" in child["reasons"][0]
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
