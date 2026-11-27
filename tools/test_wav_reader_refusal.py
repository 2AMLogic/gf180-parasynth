#!/usr/bin/env python3
"""Known-answer control for the two WAV readers that read the reference
corpus (#817, operator ruling option a, 2026-11-23).

A 1 kHz sine at exactly 0.5 full scale, written as 16-bit PCM and in every
other width a pack could arrive in (24-bit, 32-bit integer, 8-bit, float32).
The signal's answer is known independently of anything this repository
models, so this is a control, not a self-consistency check
(docs/verification-rules.md).

`model/test_discrimination.read_wav` and
`tools/measure_conga_body_spread.read_wav` divided by 32768 without ever
looking at the sample dtype, so a 24-bit file -- which
`scipy.io.wavfile.read` returns as left-justified int32 -- came back
65536x too loud, 96 dB, with nothing raised. This file landed RED on that
code: the 24-bit known answer read peak 32768 from both readers and no
refusal raised. The fix is the ruled one: REFUSE (raise, naming the path
and the dtype) on any width the readers do not handle, and answer 16-bit
exactly as before. Handling 24-bit by scaling is out of scope by the same
ruling.

The other five readers named in #817 are deliberately not exercised here:
they already refuse (`model/drum_fit`, `tools/r0_reference`,
`tools/score_ensemble_i2s`), dispatch on dtype (`model/tom_pitch_probe`) or
peak-normalise in the reader (`model/drum_verify`), and touching their
conditioning is out of scope (#617, #163).
"""
from __future__ import annotations

import pathlib
import struct
import sys
import wave

import numpy as np
import pytest
from scipy.io import wavfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "model"))

import measure_conga_body_spread as conga_probe             # noqa: E402
import test_discrimination as disc                          # noqa: E402

#: Both readers under the same order contract, `model/measure_harness.py`'s
#: `(x, sr)`. Tuple order is out of scope for #817 and unchanged.
READERS = [disc.read_wav, conga_probe.read_wav]

SR, F0, PEAK = 48000, 1000.0, 0.5


def _sine() -> np.ndarray:
    t = np.arange(int(0.25 * SR)) / SR
    return PEAK * np.sin(2 * np.pi * F0 * t)


def _write_16bit(path: pathlib.Path, sig: np.ndarray = _sine()) -> None:
    wavfile.write(str(path), SR, np.round(sig * 32767).astype("<i2"))


def _write_24bit(path: pathlib.Path) -> None:
    """`wave` has no 24-bit mode, so pack little-endian three-byte frames by
    hand -- exactly as the control in #817's body did."""
    q = np.round(_sine() * (2 ** 23 - 1)).astype(np.int32)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(3)
        w.setframerate(SR)
        w.writeframes(b"".join(struct.pack("<i", int(v))[:3] for v in q))


def _write_32bit_int(path: pathlib.Path) -> None:
    q = np.round(_sine() * (2 ** 31 - 1)).astype("<i4")
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(4)
        w.setframerate(SR)
        w.writeframes(q.tobytes())


def _write_8bit(path: pathlib.Path) -> None:
    q = np.round(_sine() * 127 + 128).astype(np.uint8)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(1)
        w.setframerate(SR)
        w.writeframes(q.tobytes())


def _write_float32(path: pathlib.Path) -> None:
    wavfile.write(str(path), SR, _sine().astype(np.float32))


#: label -> (writer, dtype scipy hands the reader). 16-bit is the handled
#: width and has its own known-answer tests; everything here must REFUSE.
UNHANDLED = {
    "24-bit-int32": (_write_24bit, "int32"),
    "32-bit-int": (_write_32bit_int, "int32"),
    "8-bit-uint8": (_write_8bit, "uint8"),
    "float32": (_write_float32, "float32"),
}


def _peak(x: np.ndarray) -> float:
    return float(np.abs(np.asarray(x, dtype=np.float64)).max())


# ---------------------------------------------------------------------------
# The known answer, at the width the readers handle
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("read", READERS, ids=lambda f: f"{f.__module__}.read_wav")
def test_sixteen_bit_known_answer_is_half_full_scale(read, tmp_path):
    p = tmp_path / "known_16bit.wav"
    _write_16bit(p)
    x, sr = read(str(p))
    assert sr == SR
    assert abs(_peak(x) - PEAK) < 1e-3, _peak(x)


@pytest.mark.parametrize("read", READERS, ids=lambda f: f"{f.__module__}.read_wav")
def test_sixteen_bit_samples_are_bit_exact_with_the_old_behaviour(read, tmp_path):
    """16-bit conditioning is unchanged by the refusal: the same samples the
    unconditional `astype(float64) / 32768.0` produced, and stereo is still
    averaged channel-wise."""
    p = tmp_path / "mono.wav"
    _write_16bit(p)
    x, _ = read(str(p))
    _, raw = wavfile.read(str(p))
    assert np.array_equal(x, raw.astype(np.float64) / 32768.0)

    st = tmp_path / "stereo.wav"
    sig = np.round(_sine() * 32767).astype("<i2")
    wavfile.write(str(st), SR, np.column_stack([sig, sig]))
    y, _ = read(str(st))
    assert y.ndim == 1
    assert np.array_equal(y, sig.astype(np.float64) / 32768.0)


# ---------------------------------------------------------------------------
# The known answer at every width the readers do NOT handle: refuse
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("read", READERS, ids=lambda f: f"{f.__module__}.read_wav")
def test_twentyfour_bit_known_answer_refuses_rather_than_answers_96_db_hot(read, tmp_path):
    """THE 96 dB CASE, as ruled (#817 option a): refuse, do not scale. On
    main this reader ANSWERED peak 32768 for this exact file -- the recorded
    red run -- because scipy hands 24-bit PCM back as left-justified int32
    and the unconditional /32768 is 65536x too small a divisor."""
    p = tmp_path / "known_24bit.wav"
    _write_24bit(p)
    with pytest.raises(ValueError, match=r"int32.*16-bit") as why:
        read(str(p))
    assert str(p) in str(why.value), str(why.value)


@pytest.mark.parametrize("read", READERS, ids=lambda f: f"{f.__module__}.read_wav")
@pytest.mark.parametrize("label", sorted(UNHANDLED))
def test_refuses_every_unhandled_width_naming_path_and_dtype(read, label, tmp_path):
    writer, dtype = UNHANDLED[label]
    p = tmp_path / f"{label}.wav"
    writer(p)
    with pytest.raises(ValueError, match=r"16-bit") as why:
        read(str(p))
    assert str(p) in str(why.value), str(why.value)
    assert dtype in str(why.value), str(why.value)


@pytest.mark.parametrize("read", READERS, ids=lambda f: f"{f.__module__}.read_wav")
def test_an_answer_at_an_unhandled_width_cannot_pass_as_a_refusal(read, tmp_path):
    """The defeat-input check for the refusal itself: a reader that quietly
    answers cannot pass, because the exception is asserted, not the value."""
    p = tmp_path / "known_24bit.wav"
    _write_24bit(p)
    try:
        x, _ = read(str(p))
    except ValueError:
        return
    raise AssertionError(f"answered with peak {_peak(x)} instead of refusing")


# ===========================================================================
# #1117: the class sweep. The five readers below divided by 32768 with no
# width check. Each control was run RED first (24-bit answered peak 32768, no
# exception; recorded in the PR) and is green with the guard.
# ===========================================================================
import subprocess                                           # noqa: E402

import cymbal_bands                                         # noqa: E402
import measure_partial_balance as mpb                       # noqa: E402
import refusal                                              # noqa: E402
import run_case as rc                                       # noqa: E402

_SIXTEEN = {"int16"}


def _place(tmp_path: pathlib.Path, rel: str, writer) -> pathlib.Path:
    p = tmp_path / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    writer(p)
    return p


def _refuses(call, path):
    with pytest.raises((ValueError, refusal.Refused, SystemExit), match=r"int32.*16-bit") as why:
        call()
    assert str(path) in str(why.value), str(why.value)


def test_run_case_load_reference_refuses_24bit_and_answers_16bit(tmp_path):
    rel, _ = rc.REF_MAIN["RS"]
    p = _place(tmp_path, rel, _write_24bit)
    _refuses(lambda: rc.load_reference("RS", tmp_path), p)
    p.unlink()
    _place(tmp_path, rel, _write_16bit)
    x, sr, _, _ = rc.load_reference("RS", tmp_path)
    assert sr == SR and abs(_peak(x) - PEAK) < 1e-3, _peak(x)


@pytest.mark.parametrize("label", sorted(UNHANDLED))
def test_run_case_load_reference_refuses_every_unhandled_width(label, tmp_path):
    writer, dtype = UNHANDLED[label]
    rel, _ = rc.REF_MAIN["CB"]
    p = _place(tmp_path, rel, writer)
    with pytest.raises((refusal.Refused, SystemExit), match=r"16-bit") as why:
        rc.load_reference("CB", tmp_path)
    assert dtype in str(why.value) and str(p) in str(why.value), str(why.value)


def test_partial_balance_load_reference_refuses_24bit_and_answers_16bit(tmp_path, monkeypatch):
    rel, _ = rc.REF_MAIN["RS"]
    monkeypatch.setattr(mpb, "REFDIR", tmp_path)
    p = _place(tmp_path, rel, _write_24bit)
    _refuses(lambda: mpb.load_reference("RS"), p)
    p.unlink()
    _place(tmp_path, rel, _write_16bit)
    x, sr, _, _ = mpb.load_reference("RS")
    assert sr == SR and abs(_peak(x) - PEAK) < 1e-3, _peak(x)


@pytest.mark.parametrize("bad, counted", [(np.nan, "1 NaN, 0 Inf"), (np.inf, "0 NaN, 1 Inf")],
                         ids=["nan", "inf"])
def test_partial_balance_load_reference_refuses_non_finite_before_the_width_guard(
        tmp_path, monkeypatch, bad, counted):
    """#1568. The tool's private reader guarded the sample width FIRST, so a
    float32 WAV holding a NaN was refused as `float32 samples, expected 16-bit
    PCM` -- a format mismatch -- and the NaN, the defect that defeats the
    silence test (#133, #134), was never named. run_case's order is finiteness
    first; the tool loads through run_case now and inherits it. On the private
    reader this landed RED with the format refusal."""
    monkeypatch.setattr(mpb, "REFDIR", tmp_path)
    sig = _sine().astype(np.float32)
    sig[10] = bad
    p = _place(tmp_path, rc.REF_MAIN["RS"][0], lambda q: wavfile.write(str(q), SR, sig))
    _, written = wavfile.read(str(p))               # the fixture is what it claims
    assert written.dtype == np.float32 and not np.isfinite(written[10]), written.dtype
    assert np.isfinite(written).sum() == len(written) - 1, "exactly one non-finite sample, at index 10"
    with pytest.raises(refusal.Refused, match=r"non-finite") as why:
        mpb.load_reference("RS")
    assert counted in str(why.value), str(why.value)
    assert "16-bit" not in str(why.value), str(why.value)


def test_cymbal_bands_load_refuses_24bit_and_answers_16bit(tmp_path):
    p = _place(tmp_path, "a.wav", _write_24bit)
    _refuses(lambda: cymbal_bands._load(p), p)
    # Rule 8: _refuses() accepts the base refusal.Refused, so it is satisfied by
    # EITHER class and cannot tell them apart. Pin the module's own class -- the
    # one cymbal_bands already raises at lines 100 and 140, and the name that is
    # greppable at its raise and catch sites (tools/refusal.py) -- so raising the
    # base class goes red.
    #
    # MEASURED during review of this PR, so a later reader is not misled: this
    # does NOT yet buy a REFUSED exit status. cymbal_low_tail:236 and
    # cymbal_tone_knob:251 each define their OWN sibling Refused, and
    # cymbal_bands.main has no refusal handler at all, so a cymbal_bands.Refused
    # is caught by nobody and the process exits 1 (FAIL), not 2. That is a
    # pre-existing, module-wide gap -- it is true of lines 100 and 140 on
    # origin/main too -- and is filed as its own follow-up issue, not this
    # control's business.
    with pytest.raises(cymbal_bands.Refused):
        cymbal_bands._load(p)
    q = _place(tmp_path, "b.wav", _write_16bit)
    x, sr = cymbal_bands._load(q)
    assert sr == SR and abs(_peak(x) - PEAK) < 1e-3, _peak(x)


def test_hh_probe5_scorecard_record_refuses_24bit_reference(tmp_path):
    sys.path.insert(0, str(ROOT / "tools" / "probes" / "hihat"))
    import hh_probe5
    p = _place(tmp_path, "cy8/CY.WAV", _write_24bit)
    _refuses(lambda: hh_probe5.scorecard_record(
        np.zeros(10), "cy8/CY.WAV", "s", "c", tmp_path, {}, {}), p)


def test_ab_808_loud_refuses_24bit_and_answers_16bit(tmp_path):
    """A script, not a library: drive it as the operator does (argv[1] = pack)."""
    _place(tmp_path, "t-a-808.wav", _write_24bit)
    _place(tmp_path, "t-b-ours.wav", _write_16bit)
    run = lambda: subprocess.run([sys.executable, str(ROOT / "tools" / "ab_808_loud.py"),
                                  str(tmp_path)], capture_output=True, text=True)
    r = run()
    assert r.returncode != 0 and "int32" in r.stderr and "16-bit" in r.stderr, r.stderr[-400:]
    assert not (tmp_path / "00-all-AB.wav").exists()
