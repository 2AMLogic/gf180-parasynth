"""Controls for tools/dphase_floor_census.py (#768).

A census that answers when it cannot is worse than one that is absent,
because its output looks exactly like data. Every precondition the tool
claims to assert is shown REFUSING here, on a throwaway corpus -- none of
this needs the real Fischer set, which is the point: the instrument and its
controls are buildable on any host, and only the measurement needs the box.

The one claim that is not a refusal path is the digest DEFINITION. The tool
pins a value copied out of docs/discrimination.md rather than re-measured, so
the pin is only meaningful if this file computes the digest the same way the
shipped code that produced it does. That is
``test_the_digest_definition_matches_the_shipped_one``, and it is the check
that keeps the pin from being an unsatisfiable gate for a reason nobody can
see.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest
from scipy.io import wavfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "model"))
sys.path.insert(0, str(ROOT / "audition"))

import dphase_floor_census as dc  # noqa: E402


def _wav(path: Path, sr: int = 44100, f0: float = 410.0, n: int = 11025) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    t = np.arange(n) / sr
    x = (0.5 * np.sin(2 * np.pi * f0 * t) * 32767).astype("<i2")
    wavfile.write(str(path), sr, x)


def _corpus(root: Path, n: int) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    for i in range(n):
        _wav(root / "bd8" / f"BD{i:04d}.WAV")
    return root


# ---------------------------------------------------------------------------
def test_a_missing_corpus_is_refused_and_names_how_to_get_one(tmp_path):
    with pytest.raises(dc.Refused) as e:
        dc.check_corpus(str(tmp_path / "nope"), dc.EXPECT_CORPUS_SHA256,
                        dc.EXPECT_SPLIT_HASH)
    assert dc.CORPUS_HOWTO in str(e.value), (
        "a refusal that does not say how to satisfy it is a dead end")


def test_a_directory_with_no_wavs_is_refused(tmp_path):
    (tmp_path / "corpus").mkdir()
    with pytest.raises(dc.Refused, match="no .WAV"):
        dc.check_corpus(str(tmp_path / "corpus"), dc.EXPECT_CORPUS_SHA256,
                        dc.EXPECT_SPLIT_HASH)


def test_the_wrong_number_of_wavs_is_refused(tmp_path):
    root = _corpus(tmp_path / "corpus", 3)
    with pytest.raises(dc.Refused, match="3 WAVs"):
        dc.check_corpus(str(root), dc.EXPECT_CORPUS_SHA256, dc.EXPECT_SPLIT_HASH)


def test_the_right_count_with_the_wrong_bytes_is_refused(tmp_path):
    """THE INPUT THAT DEFEATS A COUNT-ONLY CHECK. 116 files of the right
    names and the wrong contents passes every structural test and answers a
    different question. The digest is what catches it, so the count check
    above is not load-bearing on its own and this proves the next leg runs."""
    root = _corpus(tmp_path / "corpus", dc.EXP_N_WAV)
    with pytest.raises(dc.Refused, match="corpus digest"):
        dc.check_corpus(str(root), dc.EXPECT_CORPUS_SHA256, dc.EXPECT_SPLIT_HASH)


def test_the_digest_is_stable_and_independent_of_walk_order(tmp_path):
    root = _corpus(tmp_path / "corpus", 5)
    a, na = dc.corpus_digest(str(root))
    # the same bytes under a second subdirectory name sort differently but
    # each file's RELATIVE PATH is hashed, so renaming must change it
    b, nb = dc.corpus_digest(str(root))
    assert (a, na) == (b, nb) == (a, 5)
    (root / "bd8").rename(root / "sd8")
    c, nc = dc.corpus_digest(str(root))
    assert nc == 5 and c != a, (
        "the digest ignores relative paths, so a reshuffled corpus would "
        "pass the pin")


def test_the_digest_definition_matches_the_shipped_one(tmp_path):
    """The pin is copied from docs/discrimination.md, not re-measured here --
    no host in this pipeline has the corpus. It is therefore only meaningful
    if this file hashes the way the code that WROTE that number hashes.
    `model/condition_boundary.provenance` is that code."""
    import condition_boundary as cb
    root = _corpus(tmp_path / "corpus", 7)
    mine, n = dc.corpus_digest(str(root))
    theirs = cb.provenance(str(root))
    assert (mine, n) == (theirs["corpus_sha256"], theirs["corpus_n_wav"]), (
        "this census hashes a corpus differently from the shipped function "
        "that produced the pinned value, so the pin can never match and the "
        "gate is unsatisfiable")


# ---------------------------------------------------------------------------
def test_a_reference_clip_at_the_wrong_rate_is_refused(tmp_path):
    """Precondition 5, the one the hashes cannot cover. A corpus resampled in
    place would satisfy count, digest and split hash while making "the
    44.1 kHz side" false, so the rate is read from the data."""
    p = tmp_path / "BD0000.WAV"
    _wav(p, sr=48000)
    out = dc._ref_one(("BD", (), str(p), None))
    assert out[0] == "REFUSED" and "48000 Hz, not 44100" in out[1], out

    _wav(p, sr=44100)
    out = dc._ref_one(("BD", (), str(p), None))
    assert out[0] == "ref" and out[3] in dc.ROW_STATES, out


def test_a_side_with_no_refusals_is_printed_as_zero_not_omitted(tmp_path, capsys):
    """#768's stated edge case. An omitted row reads as "not measured"; a
    `0 / 116` reads as measured and zero, and those are different claims."""
    from model.discrimination_features import census_counts
    pre = dict(refdir="/x", corpus_sha256="deadbeef", n_wav=116,
               split_hash="cafe", n_clips=116)
    none_refused = census_counts([dc.ROW_STATES[0]] * 4)
    res = {"ref": {**none_refused, "per_voice": {"BD": none_refused}},
           "render": {**none_refused, "per_voice": {"BD": none_refused}},
           "arm": "ours", "floor_ppm": 20.0, "floor_is_shipped": True}
    dc.report(pre, res, sys.stdout)
    text = capsys.readouterr().out
    assert "ref side: 0 / 4 refused by the floor" in text, text
    assert "render side: 0 / 4 refused by the floor" in text, text
    assert "BD" in text


def test_the_tool_exits_2_rather_than_0_when_it_cannot_answer(tmp_path):
    """REFUSED is a first-class outcome and must not be mistakable for a
    pass by anything reading the exit status."""
    assert dc.main(["--refs", str(tmp_path / "absent")]) == 2
