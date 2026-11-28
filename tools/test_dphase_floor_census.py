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

import errno
import json
import os
import pathlib
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


def test_the_floor_refusal_set_only_grows_with_the_floor():
    """The premise under #768's criterion 2, asserted instead of assumed.

    The census measures 0 floor refusals at the shipped 20 ppm. Criterion 2
    asks what the `--dphase-floor-ppm 0` arm does, and the cheap answer is a
    DERIVATION rather than a second heavy A/B: the floor leg is monotone in
    the floor, so 0 refusals at 20 ppm forces 0 refusals at every floor below
    it, hence an identical `jit.dphase_ar1.seg0` column, an identical feature
    matrix and an unchanged `jit.stability` attribution.

    That derivation is only as good as its premise, and a valid derivation
    from an unverified premise is indistinguishable from a verified one (this
    repository's third root cause). So the premise is the thing under test
    here, and it is tested WITHOUT the Fischer corpus, so CI re-checks it on
    every commit rather than only on a host that happens to hold the corpus.

    The arithmetic being pinned: `jitter_features` sets

        refused = not (len(d) >= 4 and d.std() > _EPS and pjit > floor)

    and `dphase_row_state` calls the floor leg when `pjit <= floor`. For
    floor' < floor, `pjit <= floor'` implies both `pjit <= floor` and
    `not (pjit > floor')`, so ROW_FLOOR at floor' implies ROW_FLOOR at floor.
    The refused set is monotone non-decreasing in the floor; it can never
    acquire a row by LOWERING the floor.

    Non-vacuity is asserted too, because a ladder on which nothing ever
    refuses would satisfy every implication above and prove nothing -- the
    same two-identical-arms defect the census itself exists to avoid."""
    from model.discrimination_features import (ROW_FLOOR, _square,
                                               dphase_row_state)

    sr = 44100
    ladder = [0.0, 1.0, 20.0, 1e3, 1e9]
    clips = [("stable", _square(300.0, sr)),
             ("tiny_jitter", _square(300.0, sr, jitter=1e-6, seed=1)),
             ("small_jitter", _square(300.0, sr, jitter=1e-4, seed=2)),
             ("drifting", _square(300.0, sr, jitter=3e-3, seed=3))]

    seen_floor = 0
    for name, x in clips:
        flags = [dphase_row_state(x, sr, f)[0] == ROW_FLOOR for f in ladder]
        seen_floor += sum(flags)
        # monotone: once it refuses at some floor it must refuse at every
        # larger floor. Equivalently, no True may be followed by a False.
        for i in range(len(ladder) - 1):
            if flags[i]:
                assert flags[i + 1], (
                    f"{name}: the floor refused at {ladder[i]:g} ppm but "
                    f"REPORTED at the larger {ladder[i + 1]:g} ppm. The floor "
                    "leg is not monotone, so 'no refusals at 20 ppm' no "
                    "longer implies none at 0 and #768's criterion 2 needs "
                    "the two-arm run after all")

    assert seen_floor > 0, (
        "no clip on this ladder was floor-refused at any floor, so every "
        "implication above held vacuously and this control proves nothing "
        "about monotonicity")
    assert seen_floor < len(clips) * len(ladder), (
        "every clip was floor-refused at every floor, which is also vacuous: "
        "a constant predicate is trivially monotone")


# ---------------------------------------------------------------------------
# the CLI's --json and -o/--out destinations (#2098, a slice of the #2095 class)
#
# THE DEFECT, red-first on origin/main @ 8eb04e5b (2026-11-28): after the
# whole corpus had been checked and censused, `main` wrote `a.json` with
# `write_text` and opened `a.out` with `open("w")`, and nothing made either
# parent directory. Under a missing one either raised FileNotFoundError out
# of `main`: exit 1 with a traceback, and the census -- minutes of rendering
# -- was lost with its record.
#
# THE KNOWN ANSWER, from tools/exit_contract.py and not from the module under
# test: a destination that cannot be written is REFUSED (2), the status this
# script already returns through `Refused`; refused before the census runs
# where the path says so itself; refused after it otherwise, with the report
# then on stdout, because `-o` only redirects it.
#
# WHAT RUNS FOR REAL: `main` and `report`. Doubled, counted: `check_corpus`
# and `census`, which in production need the Fischer set and a render pool.
# ---------------------------------------------------------------------------
@pytest.fixture
def counted(monkeypatch):
    """The real CLI with its corpus check and census doubled and counted."""
    from model.discrimination_features import census_counts
    calls = {"check_corpus": 0, "census": 0}
    none_refused = census_counts([dc.ROW_STATES[0]] * 4)

    def check_corpus(*a, **k):
        calls["check_corpus"] += 1
        return dict(refdir="/x", corpus_sha256="deadbeef", n_wav=116,
                    split_hash="cafe", n_clips=116)

    def census(*a, **k):
        calls["census"] += 1
        return {"ref": {**none_refused, "per_voice": {"BD": none_refused}},
                "render": {**none_refused, "per_voice": {"BD": none_refused}},
                "arm": "ours", "floor_ppm": 20.0, "floor_is_shipped": True}

    monkeypatch.setattr(dc, "check_corpus", check_corpus)
    monkeypatch.setattr(dc, "census", census)
    return calls


def _full_disk(target, name):
    """`Path.<name>` (`write_text` or `open`) that fails for `target` when
    writing, and only then."""
    real = getattr(pathlib.Path, name)

    def write(self, *args, **kwargs):
        if self == target and (name == "write_text" or "w" in args[0]):
            raise OSError(errno.ENOSPC, "No space left on device")
        return real(self, *args, **kwargs)

    return write


NOTHING_RUN = {"check_corpus": 0, "census": 0}
DESTINATIONS = [("--json", "census.json", "record"), ("-o", "census.txt", "report")]
CASES = ["a-directory", "parent-is-a-file", "parent-has-a-nul-byte"]


def _unwritable(tmp_path, filename, kind):
    """Three destinations that cannot hold the file, each with the reason it
    must be refused by (the #2040 wording), in the order of CASES."""
    return [
        (str(tmp_path), f"names an existing directory; name the {kind} file"),
        (str(tmp_path / "a-file" / filename),
         f"no {kind} can be written there: [Errno 17] File exists"),
        (f"nul\x00dir/{filename}", f"no {kind} can be written there: embedded null byte"),
    ]


@pytest.mark.parametrize("flag, filename, kind", DESTINATIONS, ids=["--json", "--out"])
@pytest.mark.parametrize("case", CASES)
def test_an_unwritable_destination_is_refused_before_the_census_runs(counted, tmp_path, capsys,
                                                                     flag, filename, kind, case):
    (tmp_path / "a-file").write_text("not a directory\n")
    target, reason = _unwritable(tmp_path, filename, kind)[CASES.index(case)]
    rc = dc.main([flag, target])
    out, err = capsys.readouterr()
    assert rc == 2, (rc, out, err)
    assert "REFUSED" in err and reason in err, err
    assert counted == NOTHING_RUN, "the census ran for a destination that could not be written"
    assert "floor" not in out, out


def test_a_json_record_under_a_missing_directory_is_created_and_written(counted, tmp_path, capsys):
    target = tmp_path / "no-such-dir" / "census.json"
    rc = dc.main(["--json", str(target)])
    out, _ = capsys.readouterr()
    assert rc == 0, out
    assert counted["census"] == 1
    assert json.loads(target.read_text())["corpus"]["n_wav"] == 116
    assert "ref side: 0 / 4 refused by the floor" in out        # no -o: the report is on stdout


def test_a_report_under_a_missing_directory_is_created_and_written(counted, tmp_path, capsys):
    target = tmp_path / "no-such-dir" / "census.txt"
    rc = dc.main(["-o", str(target)])
    out, _ = capsys.readouterr()
    assert rc == 0, out
    assert "ref side: 0 / 4 refused by the floor" in target.read_text()
    assert f"wrote {target}" in out and "ref side" not in out, out


@pytest.mark.parametrize("flag, name, patch", [("--json", "census.json", "write_text"),
                                               ("-o", "census.txt", "open")],
                         ids=["--json", "--out"])
def test_a_write_failure_after_the_census_is_refused_with_the_report_on_stdout(counted, tmp_path,
                                                                              capsys, monkeypatch,
                                                                              flag, name, patch):
    target = tmp_path / name
    monkeypatch.setattr(pathlib.Path, patch, _full_disk(target, patch))
    rc = dc.main([flag, str(target)])
    out, err = capsys.readouterr()
    assert rc == 2, (rc, out, err)
    assert "ref side: 0 / 4 refused by the floor" in out, out      # the report survives either way
    assert "REFUSED" in err and "not written" in err, err
    assert "No space left on device" in err and "Traceback" not in err, err
    assert "wrote" not in out and not target.exists(), out


def test_an_unwritten_json_record_is_refused_though_the_report_file_was_written(counted, tmp_path,
                                                                               capsys, monkeypatch):
    """Two destinations, one of them lost: still REFUSED, and the one that
    was written says so."""
    (tmp_path / "keep.txt").write_text("x")
    target = tmp_path / "census.json"
    monkeypatch.setattr(pathlib.Path, "write_text", _full_disk(target, "write_text"))
    report = tmp_path / "census.txt"
    rc = dc.main(["--json", str(target), "-o", str(report)])
    out, err = capsys.readouterr()
    assert rc == 2, (rc, out, err)
    assert f"wrote {report}" in out and "ref side" not in out, out
    assert "REFUSED" in err and "--json" in err and "not written" in err, err
    assert "ref side" in report.read_text() and not target.exists()


@pytest.mark.skipif(hasattr(os, "geteuid") and os.geteuid() == 0,
                    reason="root writes into a read-only directory")
@pytest.mark.parametrize("flag, name", [("--json", "census.json"), ("-o", "census.txt")],
                         ids=["--json", "--out"])
def test_a_read_only_parent_is_refused_and_leaves_nothing_behind(counted, tmp_path, capsys,
                                                                 flag, name):
    """Rule 8: the input that defeats the pre-flight. The parent exists, so
    `mkdir(exist_ok=True)` accepts it; only the guard around the write can
    refuse it."""
    parent = tmp_path / "read-only"
    parent.mkdir()
    parent.chmod(0o555)
    try:
        rc = dc.main([flag, str(parent / name)])
        written = list(parent.iterdir())
    finally:
        parent.chmod(0o700)
    out, err = capsys.readouterr()
    assert rc == 2, (rc, out, err)
    assert "REFUSED" in err and "not written" in err, err
    assert "ref side: 0 / 4 refused by the floor" in out, out
    assert written == []
