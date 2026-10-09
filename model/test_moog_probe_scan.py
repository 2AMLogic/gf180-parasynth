#!/usr/bin/env python3
"""`moog_probe.scan` must REFUSE a damaged capture set, not shrink it (#600).

    .venv/bin/python -m pytest model/test_moog_probe_scan.py -q

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
"""
from __future__ import annotations

import os
import sys

import numpy as np
import pytest
from scipy.io import wavfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import moog_probe as mp                                             # noqa: E402

SR = 48000


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
