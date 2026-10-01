#!/usr/bin/env python3
"""A NaN must never become a number: one property, not a case list (#134).

    python3 -m pytest model/test_nan_invariance.py -q

WHY A PROPERTY AND NOT A CASE LIST

Every refusal guard shaped like `if bad_condition: refuse` is DEFEATED by a NaN,
because IEEE says every ordering comparison against NaN is False:

    if float(np.abs(y).max()) <= 1e-9:
        raise Refused("silent")        # NaN <= 1e-9 is False -> "not silent"

So the check that should catch bad data is the check bad data slips through. It
is not an omission, it is an INVERSION. #133 is the worked example: all-NaN audio
passed seven checks in `tools/refprofile.py::load_clip` -- profile membership,
file present, byte count, sha256, sample rate, frame count, not-silent -- and
loaded as a reference. #134 generalised it: at the time there were of the order
of a hundred comparison guards across the measurement code against four
finiteness checks.

A test per guard would not help. The guards are not where the fix belongs and
there are too many of them to keep right, and the ones nobody has written yet are
not testable at all. **What is testable without knowing any estimator's right
answer is the invariance: inject a NaN anywhere in an estimator's input and the
answer must be a refusal, never a number.** That is #103's pattern, and it covers
estimators nobody has written yet, because `ESTIMATORS` below is checked for
COMPLETENESS against the module (`test_the_table_covers_every_signal_ingesting_
function`) -- a new estimator that ingests a signal fails this file until it is
registered.

THE CONTROL, WHICH IS HALF THE FILE

An invariance test that only asserts refusals is satisfied by an estimator that
refuses everything, which is why `test_every_estimator_answers_the_clean_signal`
runs first: each entry's fixture is a signal that estimator DOES answer. Without
it the property is vacuous, and a vacuous green is the failure mode this
repository keeps finding (docs/verification-rules.md rule 1).

START RED, AS MEASURED (2026-10-01)

Reproduce it exactly -- this file is written so that it COLLECTS against a
version of `audio_measure` that has no finiteness check at all (see `REFUSALS`),
which is what makes the red run a measurement rather than an import error:

    git checkout <this commit>~1 -- model/audio_measure.py tools/run_case.py \\
        tools/refprofile.py model/reference_rigs.py tools/test_refprofile.py
    python3 tools/nan_guard_audit.py --check          # exit 1, 6 gaps
    python3 -m pytest model/test_nan_invariance.py -q # 334 failed, 150 passed
    git checkout HEAD -- .                            # put it back

What that red run said:

    6 of 7 audio-entry boundaries asserted nothing       (only #133's load_clip)
    52 of 57 registered estimators returned a NUMBER     for a signal with ONE
                                                         NaN injected in it
    29 of them returned a refusal that still CARRIED a non-finite number in
       its `detail`, which a caller logging diagnostics prints as data

The five that did refuse refused for an unrelated reason -- an empty band, a fit
that would not converge -- never because the input was not audio.

`test_the_silence_guard_shape_is_what_fails_open` keeps the MECHANISM on record
permanently, in IEEE arithmetic rather than prose, so that evidence cannot rot
with the fix.
"""
from __future__ import annotations

import ast
import json
import math
import os
import pathlib
import sys
import types

import numpy as np
import pytest
from scipy.signal import butter, freqz, iirpeak, lfilter

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "model"))
sys.path.insert(0, str(ROOT / "tools"))

import audio_measure as am                                          # noqa: E402
import nan_guard_audit as nga                                       # noqa: E402

SR = 48000


# ---------------------------------------------------------------------------
# fixtures -- deterministic signals each registered estimator DOES answer
# ---------------------------------------------------------------------------
def _t(n: int) -> np.ndarray:
    return np.arange(n) / SR


_RNG = np.random.default_rng(7)
TONE = 0.5 * np.sin(2 * math.pi * 440 * _t(int(0.5 * SR)))
_tt = _t(int(1.2 * SR))
RING = np.exp(-_tt / 0.15) * np.sin(2 * math.pi * 220 * _tt)
RING_TAIL = np.concatenate([RING, np.zeros(int(0.6 * SR))])
NOISE = 0.3 * _RNG.standard_normal(int(0.5 * SR))
NOISE_LONG = 0.3 * _RNG.standard_normal(int(2.0 * SR))
#: an EXACT 200 Hz square: 240 samples per period, 100 periods. Built by tiling
#: rather than from `sign(sin(...))`, because the sign of a sampled sine jitters
#: its edges by up to a sample and `waveform_id` -- correctly -- refuses a record
#: whose per-period residual is 9 % of the cycle. The control caught that.
_SQ_PERIOD = np.where(np.arange(240) < 120, 0.5, -0.5)
SQUARE = np.tile(_SQ_PERIOD, 100)
#: the same square with a weak 317 Hz partner, because a mathematically exact
#: square has NO inharmonic content and `inharmonic_fraction_db` correctly
#: refuses a record whose inharmonic band is 56 dB below its own leakage floor.
#: The control caught that too.
INHARMONIC = SQUARE + 0.05 * np.sin(2 * math.pi * 317 * _t(len(SQUARE)))
#: a naive square at a high f0, so its images above Nyquist are sparse enough
#: for `foldback_alias_db` to attribute them
SQUARE_ALIASED = 0.5 * np.sign(np.sin(2 * math.pi * 2500 * _t(int(0.5 * SR))))
COMB = sum(0.2 * np.sin(2 * math.pi * f * _t(int(0.5 * SR)))
           for f in (3000.0, 4700.0, 6300.0, 8100.0, 9900.0, 11500.0))
_IMP = np.zeros(8192)
_IMP[0] = 1.0
IR_LP = lfilter(*butter(2, 2000 / (SR / 2), btype="low"), _IMP)
IR_BP = lfilter(*butter(2, [1800 / (SR / 2), 2200 / (SR / 2)], btype="band"), _IMP)
ENV = np.exp(-_t(int(0.5 * SR)) / 0.1)
RAMP = np.linspace(0.0, 1.0, 2000)
CYCLE = 0.5 * np.sign(np.sin(2 * math.pi * np.arange(1024) / 1024))
REPEATS = np.tile(RING[:int(0.3 * SR)], 4)
HITS = np.zeros(int(1.0 * SR))
for _i in (2400, 14400, 28800):
    HITS[_i:_i + 4800] += np.exp(-_t(4800) / 0.02) * np.sin(2 * math.pi * 300 * _t(4800))

FREQS = np.geomspace(20.0, 20000.0, 400)
_, _h = freqz(*butter(4, 2000 / (SR / 2), btype="low"), worN=FREQS, fs=SR)
GAIN_DB = 20 * np.log10(np.abs(_h) + 1e-30)
_, _hp = freqz(*iirpeak(2000 / (SR / 2), Q=8), worN=FREQS, fs=SR)
PEAK_DB = 20 * np.log10(np.abs(_hp) + 1e-30)


# ---------------------------------------------------------------------------
# the register
#
# `name -> (fixture, call)`. `call(y)` runs the estimator with `y` in the AUDIO
# argument and every other argument set to something that answers on the clean
# fixture. The fixture is named separately so the control can use the same one.
# ---------------------------------------------------------------------------
ESTIMATORS: dict[str, tuple[np.ndarray, object]] = {
    # level, silence, clipping
    "rms": (TONE, lambda y: am.rms(y)),
    "peak": (TONE, lambda y: am.peak(y)),
    "is_silent": (TONE, lambda y: am.is_silent(y)),
    "sounding_extent": (TONE, lambda y: am.sounding_extent(y)),
    "strip_trailing_silence": (TONE, lambda y: am.strip_trailing_silence(y)),
    "clipped_fraction": (TONE, lambda y: am.clipped_fraction(y, 1.0)),
    "quantisation_floor": (TONE, lambda y: am.quantisation_floor(y, 1e-4)),
    "max_sample_step": (TONE, lambda y: am.max_sample_step(y)),
    "longest_plateau": (SQUARE, lambda y: am.longest_plateau(y)),
    "step_ratio": (SQUARE, lambda y: am.step_ratio(y)),
    # envelope
    "analytic_signal": (TONE, lambda y: am.analytic_signal(y)),
    "analytic_envelope": (TONE, lambda y: am.analytic_envelope(y)),
    "rms_envelope": (TONE, lambda y: am.rms_envelope(y)),
    "moving_average_envelope": (TONE, lambda y: am.moving_average_envelope(y, 5.0)),
    "average_envelope": (TONE, lambda y: am.average_envelope([y, y])),
    "instantaneous_frequency": (TONE, lambda y: am.instantaneous_frequency(y)),
    "envelope_bursts": (ENV, lambda y: am.envelope_bursts(y)),
    "envelope_ripple_db": (ENV, lambda y: am.envelope_ripple_db(y)),
    "segment_shape": (RAMP, lambda y: am.segment_shape(y)),
    # decay
    "decay_tau": (RING, lambda y: am.decay_tau(y)),
    "damped_sinusoid": (RING, lambda y: am.damped_sinusoid(y)),
    "schroeder_t20": (RING_TAIL, lambda y: am.schroeder_t20(y)),
    # events
    "onsets": (HITS, lambda y: am.onsets(y)),
    "repeat_period": (REPEATS, lambda y: am.repeat_period(y)),
    # spectrum
    "spectrum": (TONE, lambda y: am.spectrum(y)),
    "dominant_frequency": (TONE, lambda y: am.dominant_frequency(y, 300.0, 600.0)),
    "line_at": (TONE, lambda y: am.line_at(y, 440.0)),
    "spectral_lines": (COMB, lambda y: am.spectral_lines(y)),
    "line_stability": (COMB, lambda y: am.line_stability(y)),
    "spectral_flatness": (NOISE, lambda y: am.spectral_flatness(y)),
    "spectral_centroid": (TONE, lambda y: am.spectral_centroid(y)),
    "tonality_db": (TONE, lambda y: am.tonality_db(y)),
    "band_energy": (NOISE_LONG, lambda y: am.band_energy(y, [(100, 1000), (1000, 10000)])),
    "psd_slope_db_oct": (NOISE_LONG, lambda y: am.psd_slope_db_oct(y, (200.0, 5000.0))),
    "tone_amplitude": (TONE, lambda y: am.tone_amplitude(y, 440.0)),
    "windowed_tone_amplitude": (TONE, lambda y: am.windowed_tone_amplitude(y, 440.0)),
    "zero_crossing_frequency": (TONE, lambda y: am.zero_crossing_frequency(y)),
    # harmonics
    "harmonic_powers": (SQUARE, lambda y: am.harmonic_powers(y, 200.0, [1, 2, 3])),
    "harmonic_signature": (SQUARE, lambda y: am.harmonic_signature(y)),
    "inharmonic_fraction_db": (INHARMONIC, lambda y: am.inharmonic_fraction_db(y, 200.0)),
    "foldback_alias_db": (SQUARE_ALIASED, lambda y: am.foldback_alias_db(y, 2500.0)),
    "refine_f0": (SQUARE, lambda y: am.refine_f0(y, 200.0)),
    # transfer response, from an impulse response
    "transfer": (IR_LP, lambda y: am.transfer(y)),
    "resonant_peak": (IR_BP, lambda y: am.resonant_peak(y)),
    "bandwidth_q": (IR_BP, lambda y: am.bandwidth_q(y)),
    "corner_3db": (IR_LP, lambda y: am.corner_3db(y, "lowpass")),
    # waveform identity, one cycle at a time
    "cycle_average": (SQUARE, lambda y: am.cycle_average(y, 200.0)),
    "waveform_id": (SQUARE, lambda y: am.waveform_id(y, 200.0)),
    "pulse_edges": (CYCLE, lambda y: am.pulse_edges(y)),
    "duty_cycle": (CYCLE, lambda y: am.duty_cycle(y)),
    "rectangularity": (CYCLE, lambda y: am.rectangularity(y)),
    "midpoint_crossings": (CYCLE, lambda y: am.midpoint_crossings(y)),
    # two signals
    "compare": (TONE, lambda y: am.compare(y, TONE)),
    # measured response curves: the FREQUENCY axis is audio-grade evidence and
    # must be finite. The dB axis is the registered exemption below.
    "slope_db_oct": (FREQS, lambda y: am.slope_db_oct(y, GAIN_DB, (4000.0, 8000.0))),
    "plateau_db": (FREQS, lambda y: am.plateau_db(y, GAIN_DB, (20.0, 200.0))),
    "dc_plateau_db": (FREQS, lambda y: am.dc_plateau_db(y, GAIN_DB, (20.0, 200.0),
                                                        scale_hz=2000.0)),
    "corner_from_curve": (FREQS, lambda y: am.corner_from_curve(y, GAIN_DB)),
    "peak_from_curve": (FREQS, lambda y: am.peak_from_curve(y, PEAK_DB)),
}

#: Estimators that ingest an array through `_as_float` and are deliberately NOT
#: in the property above, with the reason. An empty reason is not allowed: an
#: unexplained exemption is how a guard stops guarding.
EXEMPT: dict[str, str] = {
    "glide_law": (
        "its argument is a frequency TRAJECTORY, not audio, and a trajectory "
        "legitimately carries NaN for frames where there was no pitch to read. "
        "The function documents filtering them and does so on its second line."),
}

#: The complete list of `_as_float(..., finite=False)` opt-outs in
#: `model/audio_measure.py`, as (enclosing function, argument). Pinned here so
#: that the module's one boundary check cannot be quietly switched off for a
#: sixth or seventh caller: `test_the_finite_opt_outs_are_exactly_these` fails
#: on any call site this table does not name.
FINITE_OPT_OUTS: dict[tuple[str, str], str] = {
    ("slope_db_oct", "gain_db"): "a dB axis; 20*log10(0) is -inf by construction",
    ("plateau_db", "gain_db"): "a dB axis; already masked with np.isfinite",
    ("dc_plateau_db", "gain_db"): "a dB axis; already masked with np.isfinite",
    ("corner_from_curve", "gain_db"): "a dB axis; already masked with np.isfinite",
    ("peak_from_curve", "gain_db"): "a dB axis; already masked with np.isfinite",
    ("glide_law", "f_hz"): "a frequency trajectory, not audio (see EXEMPT)",
}


# ---------------------------------------------------------------------------
# what counts as a refusal
# ---------------------------------------------------------------------------
#: `getattr`, not `am.NonFiniteAudio`, for ONE reason: start red
#: (docs/verification-rules.md rule 1). This file must be able to COLLECT
#: against a version of `audio_measure` that has no finiteness check at all, or
#: the red run is an import error instead of a measurement, and an import error
#: does not tell you how many estimators answered a NaN with a number.
REFUSALS = tuple({am.InsufficientEvidence,
                  getattr(am, "NonFiniteAudio", am.InsufficientEvidence)})


def _refusal(call, y) -> str | None:
    """`None` if the call produced an ANSWER; otherwise how it refused.

    A refusal is either a raise or a result carrying `ok=False` -- `Estimate`
    and `WaveformID` both do that, and `WaveformID` is how `waveform_id` passes
    `cycle_average`'s refusal on. A returned array is an ANSWER: an array of NaN
    is the worst possible answer, not a refusal."""
    try:
        r = call(y)
    except REFUSALS as why:
        return f"raised {type(why).__name__}"
    if getattr(r, "ok", True) is False:
        return f"{type(r).__name__}(not ok: {getattr(r, 'reason', '')})"
    return None


def _numbers_in(r) -> list[float]:
    """Every float a result carries, however it is wrapped. Used to prove that a
    refusal is a refusal rather than a NaN wearing one."""
    if isinstance(r, am.Estimate):
        return ([] if r.value is None else [float(r.value)]) + _numbers_in(r.detail)
    if isinstance(r, dict):
        out: list[float] = []
        for v in r.values():
            out += _numbers_in(v)
        return out
    if isinstance(r, (list, tuple)):
        out = []
        for v in r:
            out += _numbers_in(v)
        return out
    if isinstance(r, np.ndarray):
        return [float(v) for v in np.asarray(r, dtype=np.float64).reshape(-1)[:64]]
    if isinstance(r, (bool, np.bool_)):
        return []
    if isinstance(r, (int, float, np.integer, np.floating)):
        return [float(r)]
    return []


# ---------------------------------------------------------------------------
# 1. the control: each fixture is a signal its estimator ANSWERS
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("name", sorted(ESTIMATORS))
def test_every_estimator_answers_the_clean_signal(name):
    """Without this the property below is vacuous: "refuses a NaN" is trivially
    satisfied by an estimator that refuses everything."""
    fixture, call = ESTIMATORS[name]
    why = _refusal(call, fixture)
    assert why is None, (
        f"{name} refused its own clean fixture ({why}), so the NaN property "
        f"below proves nothing about it. Fix the fixture, not the assertion.")


# ---------------------------------------------------------------------------
# 2. the property
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("where", ["first", "middle", "last"])
@pytest.mark.parametrize("name", sorted(ESTIMATORS))
def test_a_nan_anywhere_in_the_input_is_refused_not_measured(name, where):
    """ONE NaN, at one position. This is the realistic case -- a partial write, a
    denormal blow-up, one divide by zero in a plugin -- and the dangerous one,
    because the record still looks like audio everywhere else."""
    fixture, call = ESTIMATORS[name]
    y = np.array(fixture, dtype=np.float64)
    y[{"first": 0, "middle": len(y) // 2, "last": -1}[where]] = np.nan
    why = _refusal(call, y)
    assert why is not None, (
        f"{name} returned a number for a signal with a NaN at the {where} "
        f"sample. A NaN defeats every threshold guard downstream rather than "
        f"tripping one (#134), so a number here is a number nothing checked.")


@pytest.mark.parametrize("fill", [np.nan, np.inf, -np.inf])
@pytest.mark.parametrize("name", sorted(ESTIMATORS))
def test_a_wholly_non_finite_input_is_refused_not_measured(name, fill):
    """#133's shape: the whole record. Inf is here as well as NaN because the
    comparison inversion is the same -- `inf <= floor` is also False -- and
    because an Inf reaching an FFT makes every bin NaN."""
    fixture, call = ESTIMATORS[name]
    y = np.full(len(fixture), fill, dtype=np.float64)
    why = _refusal(call, y)
    assert why is not None, (
        f"{name} returned a number for an all-{fill} record")


@pytest.mark.parametrize("name", sorted(ESTIMATORS))
def test_a_refused_nan_carries_no_number_at_all(name):
    """A refusal that still carries a NaN in `detail` is worse than a raise: a
    caller that logs the diagnostics prints a number that nothing produced."""
    fixture, call = ESTIMATORS[name]
    y = np.array(fixture, dtype=np.float64)
    y[len(y) // 2] = np.nan
    try:
        r = call(y)
    except REFUSALS:
        return                                   # raised: there is no result
    bad = [v for v in _numbers_in(r) if not math.isfinite(v)]
    assert not bad, f"{name} refused but handed back {len(bad)} non-finite value(s)"


# ---------------------------------------------------------------------------
# 3. completeness: the property must cover estimators nobody has written yet
# ---------------------------------------------------------------------------
def _module_tree() -> ast.Module:
    return ast.parse((ROOT / "model" / "audio_measure.py").read_text(encoding="utf-8"))


def _signal_ingesting_functions() -> set[str]:
    """Every public top-level function in `audio_measure` whose body calls
    `_as_float`. That call IS the module's declaration that an argument is
    audio, so this is not a guess about intent."""
    out = set()
    for node in _module_tree().body:
        if not isinstance(node, ast.FunctionDef) or node.name.startswith("_"):
            continue
        for sub in ast.walk(node):
            if isinstance(sub, ast.Call) and getattr(sub.func, "id", "") == "_as_float":
                out.add(node.name)
                break
    return out


def test_the_table_covers_every_signal_ingesting_function():
    """**This is what makes the property cover estimators nobody has written
    yet.** A new estimator that ingests a signal is unregistered, and
    unregistered fails here -- so the author has to either put it in the
    property or say in `EXEMPT` why a NaN in its input is legitimate."""
    registered = set(ESTIMATORS) | set(EXEMPT)
    missing = sorted(_signal_ingesting_functions() - registered)
    assert not missing, (
        f"{len(missing)} estimator(s) ingest a signal and are in neither "
        f"ESTIMATORS nor EXEMPT: {missing}. Register each one, or state in "
        f"EXEMPT why a non-finite value in its input is by design (#134).")


def test_every_exemption_states_a_reason():
    for name, why in EXEMPT.items():
        assert len(why.split()) >= 8, f"{name}'s exemption is not an explanation"


def test_the_finite_opt_outs_are_exactly_these():
    """`_as_float(..., finite=False)` is how the module's one boundary check is
    switched off. Pin the list, or the fix can be undone one call site at a
    time with nothing turning red."""
    found: set[tuple[str, str]] = set()
    for node in _module_tree().body:
        if not isinstance(node, ast.FunctionDef):
            continue
        for sub in ast.walk(node):
            if not (isinstance(sub, ast.Call)
                    and getattr(sub.func, "id", "") == "_as_float"):
                continue
            off = any(k.arg == "finite" and k.value.value is False for k in sub.keywords)
            if off and sub.args:
                found.add((node.name, ast.unparse(sub.args[0])))
    assert found == set(FINITE_OPT_OUTS), (
        f"the finite=False opt-outs have changed.\n"
        f"  added:   {sorted(found - set(FINITE_OPT_OUTS))}\n"
        f"  removed: {sorted(set(FINITE_OPT_OUTS) - found)}\n"
        f"Every one needs a line in FINITE_OPT_OUTS saying why (#134).")


# ---------------------------------------------------------------------------
# 4. the boundaries: finiteness is asserted ONCE, where audio enters
# ---------------------------------------------------------------------------
def test_every_registered_boundary_asserts_finiteness():
    """`tools/nan_guard_audit.py --check`, run by `make verify` through this
    file. The audit's own register of audio-entry boundaries is the scope
    statement #134 §2 asks for, and this is what keeps it true."""
    gaps = nga.boundary_gaps()
    assert not gaps, "audio-entry boundaries with no finiteness check:\n  " + \
                     "\n  ".join(gaps)


def test_the_boundary_register_is_not_empty_and_names_real_functions():
    """A register that silently lost its entries would make the check above
    pass by being vacuous -- the exact shape of #113."""
    assert len(nga.BOUNDARIES) >= 7
    for rel, func, why in nga.BOUNDARIES:
        assert (ROOT / rel).exists(), f"{rel} is not a file"
        assert func in nga.finite_checks(rel), f"{rel}::{func} is not a function here"
        assert len(why.split()) >= 6, f"{rel}::{func} has no stated reason"


# ---------------------------------------------------------------------------
# 5. the mechanism, on permanent record
# ---------------------------------------------------------------------------
def test_the_silence_guard_shape_is_what_fails_open():
    """The inversion itself, written in IEEE arithmetic rather than in prose.

    This is the file's injected-bug control: it asserts that the OLD guard shape
    genuinely lets a NaN through and the new one genuinely stops it. It cannot
    rot with the fix, because it depends on nothing but floating point."""
    nan, floor = float("nan"), 1e-9
    # the shape that was everywhere: refuse when the level is at or below a floor
    assert not (nan <= floor), "a NaN is not 'at or below' anything"
    assert not (nan > floor), "nor is it above anything"
    # ...so `if x <= floor: refuse` passes a NaN, and `if not (x > floor): refuse`
    # refuses it. The two differ ONLY here, which is the entire point of #134 §4.
    assert (nan <= floor) is False                       # fails OPEN
    assert (not (nan > floor)) is True                   # fails CLOSED
    # and the same for Inf, in the same direction
    assert not (float("inf") <= floor)
    assert not (-float("inf") >= -floor)


def test_require_finite_names_the_sample_and_the_count():
    """A refusal that does not say WHERE sends the reader back to the file."""
    y = np.ones(1000)
    y[137] = np.nan
    y[900] = np.inf
    with pytest.raises(am.NonFiniteAudio) as e:
        am.require_finite(y, "the probe")
    s = str(e.value)
    assert "the probe" in s and "137" in s and "2 non-finite" in s
    assert "1 NaN" in s and "1 Inf" in s
    assert am.require_finite(np.ones(8)) is not None       # the other direction
    assert am.nonfinite_report(np.ones(8)) is None
    # an integer array has no NaN to find and must not be rejected for having a
    # dtype `np.isfinite` would refuse to consider
    assert am.nonfinite_report(np.arange(8, dtype=np.int16)) is None


# ---------------------------------------------------------------------------
# 6. the boundaries, exercised rather than read
# ---------------------------------------------------------------------------
def _raw_wav(path: pathlib.Path, y, sr: int = SR, dtype=np.float32):
    from scipy.io import wavfile
    path.parent.mkdir(parents=True, exist_ok=True)
    wavfile.write(str(path), sr, np.asarray(y, dtype=dtype))


@pytest.mark.parametrize("fill", [np.nan, np.inf])
def test_boundary_refprofile_write_clip_refuses_to_freeze_it(tmp_path, fill):
    import refprofile as rp
    y = np.full(2048, fill, dtype=np.float32)
    with pytest.raises(rp.Refused, match="non-finite"):
        rp.write_clip(tmp_path / "cache" / "c.wav", y)
    assert not (tmp_path / "cache" / "c.wav").exists(), "a refused write wrote"


@pytest.mark.parametrize("fill", [np.nan, np.inf])
def test_boundary_refprofile_load_clip_refuses_it(tmp_path, monkeypatch, fill):
    """#133's regression, end to end: the file hashes correctly and matches the
    profile in every other field, so finiteness is the ONLY thing that can
    refuse it."""
    import refprofile as rp
    pdir = tmp_path / "refprofile"
    monkeypatch.setattr(rp, "PROFILE_DIR", pdir)
    monkeypatch.setattr(rp, "CACHE", pdir / "cache")
    monkeypatch.setattr(rp, "PROFILE_JSON", pdir / "profile.json")
    rel = pathlib.Path("cache") / "x.wav"
    dest = pdir / rel
    y = np.full(2048, fill, dtype=np.float32)
    _raw_wav(dest, y)
    (pdir / "profile.json").write_text(json.dumps({
        "schema": rp.SCHEMA, "sr": SR,
        "clips": {"x": {"file": str(rel), "sr": SR, "frames": len(y),
                        "bytes": dest.stat().st_size, "sha256": rp.file_sha256(dest)}},
    }))
    with pytest.raises(rp.Refused, match="non-finite"):
        rp.load_clip("x")


@pytest.mark.parametrize("fill", [np.nan, np.inf])
def test_boundary_run_case_load_reference_refuses_it(tmp_path, fill):
    """The external corpus reader. The WAV is planted as float32 because that is
    what a float render or a corrupted copy looks like; an int16 file cannot hold
    a NaN, which is why this boundary is about the READER and not the format."""
    import run_case as rc
    voice = sorted(rc.REF_MAIN)[0]
    rel, _setting = rc.REF_MAIN[voice]
    refdir = tmp_path / "refs"
    _raw_wav(refdir / rel, np.full(4096, fill, dtype=np.float32))
    with pytest.raises(rc.Refused, match="non-finite"):
        rc.load_reference(voice, refdir)


@pytest.mark.parametrize("fill", [np.nan, np.inf])
def test_boundary_run_case_prepare_refuses_it_and_says_why(fill):
    """Both sides of every comparison pass through `prepare`.

    The second assertion is the one worth having: BEFORE #134 this call already
    refused, but with the WRONG reason -- `np.abs(x) > ONSET_FRAC * nan` is False
    everywhere, so the onset landed at index 0 and a non-finite render was
    reported as "cut into the strike". A misdiagnosis in a refusal costs an hour
    in the wrong apparatus."""
    import run_case as rc
    y = np.array(TONE)
    y[len(y) // 2] = fill
    with pytest.raises(rc.Refused) as e:
        rc.prepare(y, SR, side="the probe")
    s = str(e.value)
    assert "non-finite" in s, f"refused for the wrong reason: {s}"
    assert "cut into the strike" not in s, "the old misdiagnosis is back"
    assert "the probe" in s and str(len(y) // 2) in s


def test_boundary_run_case_prepare_still_accepts_ordinary_audio():
    """The control in the other direction, for the boundary that every metric
    depends on: the new refusal must not change a verdict that worked."""
    import run_case as rc
    y = np.concatenate([np.zeros(int(0.05 * SR)), TONE])
    out = rc.prepare(y, SR, side="the probe")
    assert np.isfinite(out).all() and float(np.abs(out).max()) == pytest.approx(1.0)


@pytest.mark.parametrize("fill", [np.nan, np.inf])
def test_boundary_reference_rigs_plugin_render_refuses_the_hosts_buffer(fill):
    """`reference_rigs._Plugin.render` without dawdreamer, by calling the
    unbound method against a stub host.

    This is the point of the stub: the rigs cannot be CONSTRUCTED on a machine
    with no plugins, so the only way to prove this boundary is reachable at all
    is to drive the method directly. The stub is the thing the real host is --
    an object that hands back a buffer -- and nothing else."""
    import reference_rigs as rr
    n = 4096
    buf = np.full(n, fill, dtype=np.float32)

    stub = types.SimpleNamespace(
        name="stub", note=48, have_input=False, pb=None,
        p=types.SimpleNamespace(clear_midi=lambda: None,
                                add_midi_note=lambda *a, **k: None,
                                get_latency_samples=lambda: 0),
        eng=types.SimpleNamespace(render=lambda s: None,
                                  get_audio=lambda: np.vstack([buf, buf])),
    )
    with pytest.raises(am.NonFiniteAudio, match="non-finite"):
        rr._Plugin.render(stub, None, n / SR)

    # the control: the same stub handing back real audio returns it unchanged
    good = np.asarray(TONE[:n], dtype=np.float32)
    stub.eng.get_audio = lambda: np.vstack([good, good])
    out = rr._Plugin.render(stub, None, n / SR)
    assert np.isfinite(out).all() and len(out) == n


def test_the_audit_tool_classifies_the_two_shapes_correctly(tmp_path):
    """The audit is an instrument, so it has its own ground truth: two guards
    whose only difference is the one #134 is about."""
    src = tmp_path / "probe.py"
    src.write_text(
        "def a(x, t):\n"
        "    if x <= t:\n"
        "        raise Refused('low')\n"
        "def b(x, t):\n"
        "    if not (x > t):\n"
        "        raise Refused('low')\n"
        "def c(x, t):\n"
        "    if x != t:\n"
        "        raise Refused('moved')\n", encoding="utf-8")
    verdicts = {g.func: g.verdict for g in nga.audit_file("probe.py", root=tmp_path)}
    assert verdicts == {"a": "fails-open", "b": "fails-closed", "c": "fails-closed"}


def test_the_audit_tools_check_runs_clean_as_a_subprocess():
    """`--check` is meant to be runnable by a human and by CI, not only
    importable. An unsatisfiable gate is worse than no gate, so the gate is run
    against the current tree here (docs/verification-rules.md)."""
    import subprocess
    r = subprocess.run([sys.executable, str(ROOT / "tools" / "nan_guard_audit.py"),
                        "--check"], capture_output=True, text=True,
                       env={**os.environ, "PYTHONPATH": str(ROOT / "model")})
    assert r.returncode == 0, r.stdout + r.stderr
    assert "boundaries all assert finiteness" in r.stdout
