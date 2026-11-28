#!/usr/bin/env python3
"""`moog_probe` must REFUSE what it cannot measure, not report a number.

    .venv/bin/python -m pytest model/test_moog_probe_scan.py -q

#600 -- `scan()` must REFUSE a damaged capture set, not shrink it.
`scan()` used to wrap `wavfile.read` in `except Exception: continue` and drop
files shorter than half a second without a word. A damaged set therefore
yielded fewer rows and no error, and every count printed after it ("N of M
recordings pass the admission test") was over a denominator nobody chose. A
count is not evidence of correctness (docs/verification-rules.md).

The guard and the input that defeats it (verification-rules 8): a file that is
MISSING from disk cannot be seen by a scan of what IS on disk, so skip
counting alone cannot catch it. `--expect N` is the stated answer -- the
caller names how many recordings the set has, and fewer is a refusal. The last
test here is that defeating input.

#1736 -- `harmonics()` and `peak_shape()` must REFUSE silence, and must not
floor the spectrum. Both floored the spectrum at `1e-12` BEFORE dividing by a
reference bin, so an all-zero clip read as a flat 0 dB harmonic series at a
fabricated f0 of 26 Hz, a clip 180 dB under the floor lost its real harmonic
ratios to it, and a harmonic above Nyquist read -317 dB instead of "none".
Same shape as #1731: a projected-gain floor followed by reference-bin
subtraction. The sub-floor test is the input that defeats a reinstated floor;
the two-tone answer (-20 dB for a 0.1 amplitude ratio) is independent of our
model.
"""
from __future__ import annotations

import os
import sys
import warnings

import numpy as np
import pytest
from scipy.io import wavfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import moog_probe as mp                                             # noqa: E402
import refusal  # noqa: E402

SR = 48000
T = np.arange(SR) / SR
TONE_440 = np.sin(2 * np.pi * 440.0 * T)
TWO_TONE = TONE_440 + 0.1 * np.sin(2 * np.pi * 880.0 * T)


def _tone(path, seconds=1.0, hz=220.0):
    t = np.arange(int(seconds * SR)) / SR
    wavfile.write(str(path), SR, (0.5 * np.sin(2 * np.pi * hz * t) * 32767).astype(np.int16))


def test_a_clean_set_scans_every_file(tmp_path):
    for i in range(3):
        _tone(tmp_path / f"t{i}.wav", hz=200.0 + 50 * i)
    rows = mp.scan(str(tmp_path))
    assert [r[0] for r in rows] == ["t0.wav", "t1.wav", "t2.wav"]


def test_an_unreadable_wav_refuses_rather_than_shrinking_the_set(tmp_path):
    _tone(tmp_path / "good.wav")
    (tmp_path / "damaged.wav").write_bytes(b"RIFF\x00\x00not a wave file")
    with pytest.raises(mp.ScanRefused) as e:
        mp.scan(str(tmp_path))
    assert "damaged.wav" in str(e.value)
    assert [s[0] for s in e.value.skipped] == ["damaged.wav"]


def test_a_truncated_wav_refuses(tmp_path):
    """A capture cut short reads fine and used to be dropped as 'too short'."""
    _tone(tmp_path / "good.wav")
    _tone(tmp_path / "short.wav", seconds=0.1)
    with pytest.raises(mp.ScanRefused) as e:
        mp.scan(str(tmp_path))
    assert "short.wav" in str(e.value)


def test_an_empty_set_refuses(tmp_path):
    with pytest.raises(mp.ScanRefused):
        mp.scan(str(tmp_path))


def test_a_missing_file_is_caught_only_by_the_expected_count(tmp_path):
    """The input that defeats skip-counting: the damaged file is not there at
    all. Without `expect` the scan is clean; with it, it refuses."""
    _tone(tmp_path / "a.wav")
    _tone(tmp_path / "b.wav")
    assert len(mp.scan(str(tmp_path))) == 2
    with pytest.raises(mp.ScanRefused):
        mp.scan(str(tmp_path), expect=3)


def test_main_exits_nonzero_and_prints_refused(tmp_path, capsys):
    _tone(tmp_path / "good.wav")
    (tmp_path / "damaged.wav").write_bytes(b"garbage")
    rc = mp.main(["--set", str(tmp_path)])
    assert rc != 0
    assert "REFUSED" in capsys.readouterr().out


def test_main_refuses_an_absent_set_when_expect_is_given(tmp_path, capsys):
    """The defeating input for --expect: the whole directory is missing, which
    used to return 0 before scan() ran. Zero of three captures is a refusal."""
    rc = mp.main(["--set", str(tmp_path / "missing"), "--expect", "3"])
    assert rc == 2
    assert "REFUSED" in capsys.readouterr().out


def test_main_still_skips_an_absent_set_without_expect(tmp_path, capsys):
    """Default policy: no --expect means no claim a set exists, so skipping is
    the intended, stated behaviour (positive leg of the control above)."""
    rc = mp.main(["--set", str(tmp_path / "missing")])
    assert rc == 0
    assert "skipping the recordings" in capsys.readouterr().out


# ---- #1736: harmonics() and peak_shape() ------------------------------------

def _impulse():
    x = np.zeros(SR)
    x[0] = 1.0
    return x


def _with_sample(x, value):
    x = np.array(x, dtype=float)
    x[len(x) // 2] = value                                   # inside the steady middle
    return x


SILENT_OR_BROKEN = [
    pytest.param(np.zeros(SR), id="exact-silence"),
    pytest.param(_impulse(), id="impulse-decayed-to-exact-zero"),
    pytest.param(np.zeros(0), id="empty"),
    pytest.param(_with_sample(TONE_440, np.nan), id="nan-in-steady-middle"),
    pytest.param(_with_sample(TONE_440, np.inf), id="inf-in-steady-middle"),
]


def test_harmonics_two_tone_against_an_answer_independent_of_our_model():
    """Positive control. A 0.1 amplitude ratio IS -20 dB, and the fundamental
    IS 0 dB at 440 Hz; neither comes from our ladder."""
    h = mp.harmonics(TWO_TONE, SR)
    assert h["f0"] == pytest.approx(440.0, abs=1e-9)
    assert h["h1"] == 0.0
    assert h["h2"] == pytest.approx(-20.0, abs=1e-6)


def test_harmonics_does_not_depend_on_the_clip_level():
    """The input that defeats a `max(..., 1e-12)` floor: the same two-tone
    180 dB lower must read the same -20 dB. Read h2 = 0.0 before #1736."""
    with warnings.catch_warnings():
        warnings.simplefilter("error", RuntimeWarning)
        h = mp.harmonics(1e-18 * TWO_TONE, SR)
    assert h["f0"] == pytest.approx(440.0, abs=1e-9)
    assert h["h2"] == pytest.approx(-20.0, abs=1e-6)


def test_harmonics_above_nyquist_harmonic_is_none_not_a_floored_number():
    """7 x 3500 and 8 x 3500 Hz are past the 24 kHz Nyquist, so their bands are
    empty: there is no harmonic to report. Read -317.32 dB before #1736."""
    h = mp.harmonics(np.sin(2 * np.pi * 3500.0 * T), SR)
    assert h["f0"] == pytest.approx(3500.0, abs=1e-9)
    assert h["h1"] == 0.0
    assert h["h6"] is not None
    assert h["h7"] is None and h["h8"] is None


@pytest.mark.parametrize("clip", SILENT_OR_BROKEN)
def test_harmonics_refuses_a_clip_it_cannot_measure(clip):
    """Silence read as h1..h8 = 0.0 dB at f0 = 26 Hz, an empty clip raised
    ValueError, and a NaN one returned nan -- all before #1736."""
    with pytest.raises(mp.Refused) as e:
        mp.harmonics(clip, SR)
    assert isinstance(e.value, refusal.Refused)
    assert str(e.value)


@pytest.mark.parametrize("sr", [0, -48000, float("nan"), float("inf")])
def test_harmonics_refuses_a_sample_rate_that_is_not_a_positive_number(sr):
    with pytest.raises(mp.Refused):
        mp.harmonics(TWO_TONE, sr)


def test_harmonics_refuses_our_ladder_when_it_is_not_self_oscillating():
    """The real instance, unmocked: at resonance 0.5 the integer ladder's
    steady middle is exactly zero, and that used to fingerprint as 0 dB."""
    x = mp.our_ladder_selfosc(516.0, 0.5)
    mid = x[len(x) // 4: 3 * len(x) // 4]
    assert np.abs(mid).max() == 0.0                      # the precondition, asserted
    with pytest.raises(mp.Refused):
        mp.harmonics(x, SR)


def test_peak_shape_does_not_depend_on_the_clip_level():
    """Before #1736 the floored spectrum peaked at the first bin, 62 Hz, for
    any clip under 1e-12, and reported nan for both of its numbers."""
    with warnings.catch_warnings():
        warnings.simplefilter("error", RuntimeWarning)
        ref = mp.peak_shape(TONE_440, SR)
        low = mp.peak_shape(1e-18 * TONE_440, SR)
    assert ref["f_peak"] == pytest.approx(440.0, abs=1e-9)
    assert low["f_peak"] == ref["f_peak"]
    assert low["peak_over_passband_db"] == pytest.approx(ref["peak_over_passband_db"], abs=1e-6)
    assert low["Q"] == pytest.approx(ref["Q"], abs=1e-6)
    assert np.isfinite(ref["Q"]) and np.isfinite(ref["peak_over_passband_db"])


@pytest.mark.parametrize("clip", SILENT_OR_BROKEN)
def test_peak_shape_refuses_a_clip_it_cannot_measure(clip):
    """Exact silence read f_peak = 62.0 Hz with nan height and Q before."""
    with pytest.raises(mp.Refused) as e:
        mp.peak_shape(clip, SR)
    assert isinstance(e.value, refusal.Refused)


@pytest.mark.parametrize("sr", [0, -48000, float("nan"), float("inf")])
def test_peak_shape_refuses_a_sample_rate_that_is_not_a_positive_number(sr):
    with pytest.raises(mp.Refused):
        mp.peak_shape(TONE_440, sr)


def test_refusals_share_the_repository_base_class():
    """A handler written against `refusal.Refused` catches this module's."""
    assert issubclass(mp.Refused, refusal.Refused)
    assert issubclass(mp.ScanRefused, mp.Refused)
    e = mp.ScanRefused("msg", [("a.wav", "why")])
    assert e.skipped == [("a.wav", "why")] and str(e) == "msg"


def test_scan_refuses_a_silent_wav_and_still_inspects_the_rest(tmp_path):
    """An all-zero capture used to be admitted to `rows` as a 0 dB fingerprint.
    The damaged file sorts AFTER it, so its presence in `skipped` shows the
    scan went on past the silent one."""
    _tone(tmp_path / "good.wav")
    wavfile.write(str(tmp_path / "a_silent.wav"), SR, np.zeros(SR, dtype=np.int16))
    (tmp_path / "z_damaged.wav").write_bytes(b"garbage")
    with pytest.raises(mp.ScanRefused) as e:
        mp.scan(str(tmp_path))
    assert isinstance(e.value, refusal.Refused)
    assert "a_silent.wav" in str(e.value)
    assert [s[0] for s in e.value.skipped] == ["a_silent.wav", "z_damaged.wav"]


def test_main_refuses_a_ladder_that_is_not_self_oscillating(tmp_path, capsys):
    """`--res 0.5`: the probe that cannot run. Before #1736 main() printed a
    flat 0 dB series for it and exited 0."""
    rc = mp.main(["--res", "0.5", "--set", str(tmp_path / "missing")])
    out = capsys.readouterr().out
    assert rc == 2
    assert "REFUSED" in out
    assert "516 Hz" in out


def test_main_refuses_when_a_peak_shape_probe_refuses(tmp_path, capsys, monkeypatch):
    """main()'s per-file characterisation: a refused peak shape is a refused
    run, not a line left quietly out of the report."""

    def _refuse(x, sr):
        raise mp.Refused("no spectral energy")

    _tone(tmp_path / "SYNTH-SimpleThinSquareFilterSlope.wav")
    monkeypatch.setattr(mp, "peak_shape", _refuse)
    rc = mp.main(["--set", str(tmp_path)])
    out = capsys.readouterr().out
    assert rc == 2
    assert "REFUSED" in out
    assert "SYNTH-SimpleThinSquareFilterSlope.wav" in out


def test_main_prints_an_exactly_zero_harmonic_without_crashing(tmp_path, capsys, monkeypatch):
    """`None` is a legitimate harmonic level (#1736): it prints as -inf, sorts
    as the purest, passes (a) as -inf - h3, and cannot pass (b)."""
    rows = [
        ("even_free.wav", SR, 1.0,
         {"h1": 0.0, "h2": None, "h3": -40.0, "h4": -90.0, "h5": None, "f0": 220.0}),
        ("no_h3.wav", SR, 1.0,
         {"h1": 0.0, "h2": -30.0, "h3": None, "h4": -90.0, "h5": -60.0, "f0": 330.0}),
    ]
    monkeypatch.setattr(mp, "scan", lambda setdir, expect=None: [tuple(r) for r in rows])
    rc = mp.main(["--set", str(tmp_path)])
    out = capsys.readouterr().out
    assert rc == 0
    assert "h2   -inf" in out and "h3  -40.0" in out
    assert "recordings passing the admission test: 1 of 2" in out


# ---- #1745 review findings --------------------------------------------------

def test_scan_refuses_unsigned_pcm_silence_on_disk(tmp_path):
    """uint8 silence is 128, not 0. Read as DC it scanned as f0 26 Hz."""
    wavfile.write(str(tmp_path / "u8.wav"), SR, np.full(SR, 128, dtype=np.uint8))
    with pytest.raises(mp.ScanRefused) as e:
        mp.scan(str(tmp_path))
    assert "u8.wav" in str(e.value)


def test_scan_refuses_a_nonfinite_float_wav_without_a_warning(tmp_path):
    x = np.sin(2 * np.pi * 220.0 * T).astype(np.float32)
    x[SR // 2] = np.inf
    wavfile.write(str(tmp_path / "inf.wav"), SR, x)
    _tone(tmp_path / "z_good.wav")
    with warnings.catch_warnings():
        warnings.simplefilter("error", RuntimeWarning)
        with pytest.raises(mp.ScanRefused) as e:
            mp.scan(str(tmp_path))
    assert [s[0] for s in e.value.skipped] == ["inf.wav"]


def test_unsigned_pcm_tone_still_measures(tmp_path):
    """Control: centring must not break real uint8 audio."""
    x = (128 + 100 * np.sin(2 * np.pi * 440.0 * T)).astype(np.uint8)
    wavfile.write(str(tmp_path / "t.wav"), SR, x)
    assert mp.scan(str(tmp_path))[0][3]["f0"] == pytest.approx(440.0, abs=2.0)


def test_harmonic_whose_centre_is_past_nyquist_is_none_even_with_a_neighbour():
    """h7 of 3440 Hz is 24080 Hz > 24000; a 23800 Hz line is in its +-1.5% band."""
    x = np.sin(2 * np.pi * 3440.0 * T) + 0.1 * np.sin(2 * np.pi * 23800.0 * T)
    h = mp.harmonics(x, SR)
    assert h["f0"] == pytest.approx(3440.0, abs=1.0)
    assert h["h7"] is None


@pytest.mark.parametrize("scale", [1e-166, 1e160, 1e-300])
def test_harmonics_two_tone_survives_extreme_but_finite_scales(scale):
    with warnings.catch_warnings():
        warnings.simplefilter("error", RuntimeWarning)
        h = mp.harmonics(scale * TWO_TONE, SR)
    assert h["h2"] == pytest.approx(-20.0, abs=1e-6)


def test_main_reports_unavailable_comparison_not_a_conclusion(tmp_path, capsys):
    """1600 Hz at 8 kHz: h3 = 4800 Hz is past Nyquist. No -inf, no nan, no verdict."""
    sr = 8000
    t = np.arange(sr) / sr
    wavfile.write(str(tmp_path / "hi.wav"), sr, (0.5 * np.sin(2 * np.pi * 1600.0 * t) * 32767).astype(np.int16))
    rc = mp.main(["--set", str(tmp_path)])
    out = capsys.readouterr().out
    assert rc == 0
    assert "nan" not in out and "-inf" not in out
    assert "UNAVAILABLE" in out and "n/a" in out
    assert "NO recording in this set" not in out and "NO measurable recording" not in out


def test_main_refuses_a_slope_probe_input_with_no_peak_band(tmp_path, capsys):
    sr = 100
    t = np.arange(sr) / sr
    wavfile.write(str(tmp_path / "WEIRD-ResonanceZone.wav"), sr, (np.sin(2 * np.pi * 30.0 * t) * 20000).astype(np.int16))
    rc = mp.main(["--set", str(tmp_path)])           # must refuse, not raise from argmax
    assert rc == 2
    assert "REFUSED" in capsys.readouterr().out
