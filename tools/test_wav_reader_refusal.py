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
def test_twentyfour_bit_known_answer_is_half_full_scale(read, tmp_path):
    """THE 96 dB CASE. A correct float reader returns ~0.5; the pre-fix code
    returned 32768 here, silently."""
    p = tmp_path / "known_24bit.wav"
    _write_24bit(p)
    x, sr = read(str(p))
    assert sr == SR
    assert abs(_peak(x) - PEAK) < 1e-3, _peak(x)


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
