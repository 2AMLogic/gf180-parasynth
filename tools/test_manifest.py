"""Tests for `tools/manifest.py` -- the render/analyse/accept manifest scheme
(issue #68).

Two things this suite exists to prove, beyond "the code runs":

1. **Incomplete records are refused, not silently written** -- a measurement
   missing a required field, an acceptance bound with no rationale, and a
   retained WAV whose content no longer matches its recorded hash all raise
   `manifest.Refused` rather than producing a plausible-looking result.
2. **The title question is actually answerable.**
   `test_retroactive_reanalysis_separates_the_sound_fix_from_the_measurement_fix`
   is a synthetic stand-in for this project's own snare 8.8 -> 7.6 -> 3.4
   incident (issue #68's own acceptance criterion 4 names this as an
   acceptable substitute for the real case): it renders once, re-analyses
   the SAME retained WAV with a corrected estimator (an injected-bug control
   reinstating the historical "fixed short window, extrapolated linearly"
   T20 law -- docs/verification-rules.md rule 5), then renders again with the
   underlying model actually fixed, and asserts `diagnose()` correctly labels
   the first step "measurement changed" and the second "sound changed".
"""
from __future__ import annotations

import json
import math

import numpy as np
import pytest

import manifest as mf


# =============================================================================
# render / load_render
# =============================================================================
def _tone(seconds=0.05, sr=48000, hz=440.0):
    n = int(sr * seconds)
    t = np.arange(n) / sr
    return (0.5 * np.sin(2 * math.pi * hz * t)).astype(np.float64), sr


def test_render_writes_a_manifest_and_a_content_hashed_wav(tmp_path):
    runs = tmp_path / "runs"
    rec = mf.render("T1", {"hz": 440.0}, lambda: _tone(), runs_dir=runs)
    assert rec["stage"] == "render" and rec["case_id"] == "T1"
    wav_path = runs / rec["render_id"] / rec["wav_path"]
    assert wav_path.exists()
    assert rec["wav_sha256"] == mf.provenance.file_sha(wav_path)
    manifest_path = runs / rec["render_id"] / "manifest.json"
    assert json.loads(manifest_path.read_text())["render_id"] == rec["render_id"]


def test_two_renders_with_different_config_never_collide(tmp_path):
    runs = tmp_path / "runs"
    a = mf.render("T1", {"hz": 440.0}, lambda: _tone(hz=440.0), runs_dir=runs)
    b = mf.render("T1", {"hz": 220.0}, lambda: _tone(hz=220.0), runs_dir=runs)
    assert a["render_id"] != b["render_id"]


def test_load_render_refuses_a_wav_that_no_longer_matches_its_recorded_hash(tmp_path):
    """The precondition `analyse()` depends on, asserted at the point of use
    (CLAUDE.md: "assert your apparatus's preconditions ... and REFUSE rather
    than report when they fail")."""
    runs = tmp_path / "runs"
    rec = mf.render("T1", {"hz": 440.0}, lambda: _tone(), runs_dir=runs)
    wav_path = runs / rec["render_id"] / rec["wav_path"]
    # Corrupt the retained WAV after render() wrote it.
    wav_path.write_bytes(wav_path.read_bytes() + b"\x00\x00")
    with pytest.raises(mf.Refused, match="does not match the hash"):
        mf.load_render(rec["render_id"], runs_dir=runs)


def test_load_render_refuses_a_render_id_with_no_manifest(tmp_path):
    with pytest.raises(mf.Refused, match="no render manifest"):
        mf.load_render("does-not-exist", runs_dir=tmp_path / "runs")


# =============================================================================
# measurement / validate_measurement
# =============================================================================
def _good_measurement(**over):
    kw = dict(name="decay", units="ms", value=12.3, analyser="demo",
              analyser_version="v1", method="dB-slope fit",
              interval_s=(0.0, 0.1), channel="mono", resample_hz=None,
              filter="none", normalisation="none", fft={"used": False},
              fit_quality={"r2": 0.98}, reference_identity="synthetic")
    kw.update(over)
    return mf.measurement(**kw)


def test_a_measurement_missing_a_required_field_is_refused_not_silently_written():
    rec = _good_measurement()
    del rec["interval_s"]           # simulate a hand-built / older-schema record
    with pytest.raises(mf.Refused, match="interval_s"):
        mf.validate_measurement(rec)


def test_analyse_writes_nothing_when_one_measurement_is_incomplete(tmp_path):
    runs, jobs = tmp_path / "runs", tmp_path / "jobs"
    render_rec = mf.render("T1", {"hz": 440.0}, lambda: _tone(), runs_dir=runs)
    bad = _good_measurement(name="broken")
    del bad["fit_quality"]
    with pytest.raises(mf.Refused):
        mf.analyse(render_rec, [_good_measurement(), bad], analyser="demo",
                   analyser_version="v1", config={}, jobs_dir=jobs)
    assert not any(jobs.glob("**/analysis.json"))


def test_analyse_round_trips_through_json(tmp_path):
    runs, jobs = tmp_path / "runs", tmp_path / "jobs"
    render_rec = mf.render("T1", {"hz": 440.0}, lambda: _tone(), runs_dir=runs)
    rec = mf.analyse(render_rec, [_good_measurement()], analyser="demo",
                      analyser_version="v1", config={"window_ms": 4}, jobs_dir=jobs)
    on_disk = json.loads((jobs / rec["job_id"] / "analysis.json").read_text())
    assert on_disk["measurements"]["decay"]["value"] == pytest.approx(12.3)
    assert on_disk["render_id"] == render_rec["render_id"]


# =============================================================================
# accept
# =============================================================================
def _analysis_with(value, jobs_dir, render_rec, analyser="demo", version="v1"):
    return mf.analyse(render_rec, [_good_measurement(value=value)], analyser=analyser,
                       analyser_version=version, config={}, jobs_dir=jobs_dir)


def test_accept_refuses_a_bound_with_no_rationale(tmp_path):
    runs, jobs = tmp_path / "runs", tmp_path / "jobs"
    render_rec = mf.render("T1", {"hz": 440.0}, lambda: _tone(), runs_dir=runs)
    analysis = _analysis_with(12.3, jobs, render_rec)
    with pytest.raises(mf.Refused, match="no rationale"):
        mf.accept(analysis, {"decay": {"lo": 0.0, "hi": 20.0}}, jobs_dir=jobs)


def test_accept_pass_and_fail(tmp_path):
    runs, jobs = tmp_path / "runs", tmp_path / "jobs"
    render_rec = mf.render("T1", {"hz": 440.0}, lambda: _tone(), runs_dir=runs)
    analysis = _analysis_with(12.3, jobs, render_rec)
    ok = mf.accept(analysis, {"decay": {"lo": 0.0, "hi": 20.0, "rationale": "spec"}},
                    jobs_dir=jobs)
    assert ok["verdicts"]["decay"]["state"] == "pass"
    bad = mf.accept(analysis, {"decay": {"lo": 20.0, "hi": 30.0, "rationale": "spec"}},
                     jobs_dir=jobs, history_path=jobs / "_h2.json")
    assert bad["verdicts"]["decay"]["state"] == "fail"


def test_accept_records_why_a_bound_changed(tmp_path):
    """The gap this issue names in `model/sound_report.py`'s `LOCK`/`LOCKS`:
    today a lock value is just overwritten. Here, changing a bound without a
    reason is impossible (rationale is mandatory on every call), and the
    change itself -- old bound, new bound, both reasons -- is written to the
    record when it happens."""
    runs, jobs = tmp_path / "runs", tmp_path / "jobs"
    render_rec = mf.render("T1", {"hz": 440.0}, lambda: _tone(), runs_dir=runs)
    analysis = _analysis_with(12.3, jobs, render_rec)
    hist = jobs / "_bounds_history.json"
    first = mf.accept(analysis, {"decay": {"lo": 0.0, "hi": 20.0, "rationale": "initial spec"}},
                       jobs_dir=jobs, history_path=hist)
    assert first["bound_changes"] == []
    second = mf.accept(analysis, {"decay": {"lo": 5.0, "hi": 25.0,
                                             "rationale": "re-measured reference, +5ms"}},
                        jobs_dir=jobs, history_path=hist)
    assert len(second["bound_changes"]) == 1
    change = second["bound_changes"][0]
    assert change["previous"] == {"lo": 0.0, "hi": 20.0, "rationale": "initial spec"}
    assert change["new"]["rationale"] == "re-measured reference, +5ms"
    # An unchanged bound on a THIRD call logs no change.
    third = mf.accept(analysis, {"decay": {"lo": 5.0, "hi": 25.0,
                                            "rationale": "re-measured reference, +5ms"}},
                       jobs_dir=jobs, history_path=hist)
    assert third["bound_changes"] == []


# =============================================================================
# diagnose: "did the sound change, or only the measurement?"
# =============================================================================
def test_diagnose_same_render_different_analyser_is_measurement_changed(tmp_path):
    runs, jobs = tmp_path / "runs", tmp_path / "jobs"
    render_rec = mf.render("T1", {"hz": 440.0}, lambda: _tone(), runs_dir=runs)
    a = _analysis_with(10.0, jobs, render_rec, version="v1")
    b = _analysis_with(8.0, jobs, render_rec, version="v2")
    d = mf.diagnose(a, b, "decay")
    assert d["verdict"] == "measurement changed"
    assert d["delta"] == pytest.approx(-2.0)


def test_diagnose_different_render_same_analyser_is_sound_changed(tmp_path):
    runs, jobs = tmp_path / "runs", tmp_path / "jobs"
    a_render = mf.render("T1", {"hz": 440.0}, lambda: _tone(hz=440.0), runs_dir=runs)
    b_render = mf.render("T1", {"hz": 220.0}, lambda: _tone(hz=220.0), runs_dir=runs)
    a = _analysis_with(10.0, jobs, a_render)
    b = _analysis_with(7.0, jobs, b_render)
    d = mf.diagnose(a, b, "decay")
    assert d["verdict"] == "sound changed"


def test_diagnose_refuses_when_both_render_and_analyser_differ(tmp_path):
    """The comparison this function cannot honestly answer -- it must say so,
    not guess."""
    runs, jobs = tmp_path / "runs", tmp_path / "jobs"
    a_render = mf.render("T1", {"hz": 440.0}, lambda: _tone(hz=440.0), runs_dir=runs)
    b_render = mf.render("T1", {"hz": 220.0}, lambda: _tone(hz=220.0), runs_dir=runs)
    a = _analysis_with(10.0, jobs, a_render, version="v1")
    b = _analysis_with(7.0, jobs, b_render, version="v2")
    with pytest.raises(mf.Refused, match="cannot separate"):
        mf.diagnose(a, b, "decay")


# =============================================================================
# End-to-end: a synthetic stand-in for the snare 8.8 -> 7.6 -> 3.4 case
# =============================================================================
TRUE_TAU_MS = 40.0          # the spec's decay time constant
TARGET_T20_MS = TRUE_TAU_MS * math.log(10)   # ~92.1 ms: T20 for that tau


def _synthetic_decay(tau_ms, sr=48000, dur_s=0.25, seed=0):
    """A stand-in drum hit: white noise under an exponential envelope with a
    known, exact time constant -- so the "true" T20 is knowable independently
    of any estimator, the way an external reference recording would be."""
    n = int(sr * dur_s)
    t = np.arange(n) / sr
    rng = np.random.default_rng(seed)
    x = (rng.standard_normal(n) * np.exp(-t / (tau_ms / 1000.0))).astype(np.float64)
    return x, sr


def _rms_envelope(x, sr, win_ms=2.0):
    win = max(1, int(sr * win_ms / 1000.0))
    n_frames = len(x) // win
    frames = x[: n_frames * win].reshape(n_frames, win)
    rms = np.sqrt(np.mean(frames ** 2, axis=1))
    t_ms = (np.arange(n_frames) * win + win / 2) / sr * 1000.0
    return rms, t_ms


def _t20_law_buggy(x, sr):
    """The historical bug this control reinstates (docs/verification-rules.md
    rule 5: "a bug is not closed until it is an injection"): measure the
    envelope's fall over a FIXED, short absolute window (the first 20 ms)
    and extrapolate that rate LINEARLY out to -20 dB. Over a window this
    short relative to the actual decay this systematically overestimates --
    the same shape of error as `bd-ma-envelope`'s wrong-law family, and (per
    this suite's docstring) a synthetic stand-in for the wrong TONE law
    behind the real snare 8.8 -> 7.6 -> 3.4 incident."""
    rms, _ = _rms_envelope(x, sr)
    win_frames = 10                       # 10 * 2 ms = 20 ms
    if len(rms) < 2 * win_frames:
        return None, None
    early, late = rms[:win_frames].mean(), rms[win_frames:2 * win_frames].mean()
    if late <= 0 or early <= late:
        return None, None
    db_per_ms = 20.0 * math.log10(early / late) / 20.0
    if db_per_ms <= 0:
        return None, None
    return 20.0 / db_per_ms, None       # no fit-quality diagnostics at all


def _t20_law_corrected(x, sr):
    """The corrected law: fit the dB envelope against time over the whole
    decay down to -20 dB (not a fixed 20 ms prefix) and read T20 off the
    fitted slope, reporting the fit's own R^2 as its fit quality."""
    rms, t_ms = _rms_envelope(x, sr)
    peak_i = int(np.argmax(rms))
    peak = rms[peak_i]
    if peak <= 0:
        return None, None
    seg, seg_t = rms[peak_i:], t_ms[peak_i:] - t_ms[peak_i]
    db = 20.0 * np.log10(np.maximum(seg, 1e-9) / peak)
    mask = db > -20.0
    if mask.sum() < 8:
        return None, None
    slope, intercept = np.polyfit(seg_t[mask], db[mask], 1)
    if slope >= 0:
        return None, None
    resid = db[mask] - (slope * seg_t[mask] + intercept)
    ss_res, ss_tot = float(np.sum(resid ** 2)), float(np.sum((db[mask] - db[mask].mean()) ** 2))
    r2 = 1.0 - ss_res / ss_tot if ss_tot > 0 else None
    return -20.0 / slope, r2


def _make_measurement(t20_ms, fit_quality, law_name, law_version):
    why = None if t20_ms is not None else "the estimator did not converge"
    return mf.measurement(
        "T20", "ms", t20_ms, analyser=law_name, analyser_version=law_version,
        method="RMS envelope, dB-domain slope fit" if fit_quality else
               "RMS envelope, fixed 20ms window extrapolated linearly",
        interval_s=(0.0, 0.25), channel="mono", resample_hz=None, filter="none",
        normalisation="none", fft={"used": False},
        fit_quality=({"r2": fit_quality} if fit_quality is not None
                      else {"r2": None, "note": "no fit was performed; a two-point "
                                                 "extrapolation has no residual to report"}),
        reference_identity=f"synthetic ground truth: exponential decay, "
                            f"tau={TRUE_TAU_MS}ms, seed=0", why=why)


def test_retroactive_reanalysis_separates_the_sound_fix_from_the_measurement_fix(tmp_path):
    """A synthetic stand-in for the snare 8.8 -> 7.6 -> 3.4 case (issue #68
    acceptance criterion 4 names this substitute as acceptable).

    Story: the model's decay was actually wrong (55ms instead of the 40ms
    spec) AND the T20 estimator in use at the time had the historical
    fixed-window-extrapolation bug. Two separate fixes landed:

      1. `analyse()` the SAME retained WAV again with the corrected law.
         Nothing was re-rendered -- `diagnose()` must report
         "measurement changed", and the reported distance shrinks
         (some of the original number WAS the wrong law).
      2. Actually fix the model (tau 55ms -> 40ms) and render again, then
         re-analyse with the (now-trusted) corrected law. `diagnose()` must
         report "sound changed", and `accept()`'s verdict flips from FAIL to
         PASS -- the law fix alone was not enough; the real fix was required.
    """
    runs, jobs = tmp_path / "runs", tmp_path / "jobs"
    hist = jobs / "_bounds_history.json"
    criteria = {"T20": {"lo": TARGET_T20_MS - 10.0, "hi": TARGET_T20_MS + 10.0,
                         "rationale": "synthetic spec: tau=40ms exponential decay"}}

    # -- render once, with the model's decay WRONG (55ms instead of 40ms) ---
    render_a = mf.render("SD-SYNTH", {"tau_ms": 55.0, "seed": 0},
                          lambda: _synthetic_decay(55.0), runs_dir=runs)

    # -- leg 1: re-analyse the SAME retained WAV, two different T20 laws ----
    a_manifest, a_x, a_sr = mf.load_render(render_a["render_id"], runs_dir=runs)
    buggy_t20, _ = _t20_law_buggy(a_x, a_sr)
    fixed_t20_a, r2_a = _t20_law_corrected(a_x, a_sr)
    assert buggy_t20 is not None and fixed_t20_a is not None

    analysis_a_buggy = mf.analyse(
        a_manifest, [_make_measurement(buggy_t20, None, "t20-fixed-window", "v1")],
        analyser="t20-fixed-window", analyser_version="v1", config={}, jobs_dir=jobs)
    analysis_a_fixed = mf.analyse(
        a_manifest, [_make_measurement(fixed_t20_a, r2_a, "t20-db-slope", "v2")],
        analyser="t20-db-slope", analyser_version="v2", config={}, jobs_dir=jobs)

    diag_measurement_only = mf.diagnose(analysis_a_buggy, analysis_a_fixed, "T20")
    assert diag_measurement_only["verdict"] == "measurement changed"

    distance_buggy = abs(buggy_t20 - TARGET_T20_MS)
    distance_law_fixed = abs(fixed_t20_a - TARGET_T20_MS)
    # The law fix alone shrinks the reported distance -- some of the
    # original number really was window leakage from the wrong law.
    assert distance_law_fixed < distance_buggy

    verdict_before_sound_fix = mf.accept(analysis_a_fixed, criteria, jobs_dir=jobs,
                                          history_path=hist)
    assert verdict_before_sound_fix["verdicts"]["T20"]["state"] == "fail", (
        "the law fix alone must NOT be enough to pass -- the model's decay is "
        "still genuinely wrong at this point in the story")

    # -- leg 2: the model itself is fixed (55ms -> 40ms); render again ------
    render_b = mf.render("SD-SYNTH", {"tau_ms": 40.0, "seed": 0},
                          lambda: _synthetic_decay(40.0), runs_dir=runs)
    assert render_b["render_id"] != render_a["render_id"]
    b_manifest, b_x, b_sr = mf.load_render(render_b["render_id"], runs_dir=runs)
    fixed_t20_b, r2_b = _t20_law_corrected(b_x, b_sr)
    assert fixed_t20_b is not None

    analysis_b_fixed = mf.analyse(
        b_manifest, [_make_measurement(fixed_t20_b, r2_b, "t20-db-slope", "v2")],
        analyser="t20-db-slope", analyser_version="v2", config={}, jobs_dir=jobs)

    diag_sound_change = mf.diagnose(analysis_a_fixed, analysis_b_fixed, "T20")
    assert diag_sound_change["verdict"] == "sound changed"

    verdict_after_sound_fix = mf.accept(analysis_b_fixed, criteria, jobs_dir=jobs,
                                         history_path=hist)
    assert verdict_after_sound_fix["verdicts"]["T20"]["state"] == "pass"

    # The raw WAVs and numerical traces are retained -- issue #68 acceptance
    # criterion 5 -- so this whole reanalysis was possible from disk alone.
    assert (runs / render_a["render_id"] / "raw.wav").exists()
    assert (runs / render_b["render_id"] / "raw.wav").exists()
