#!/usr/bin/env python3
"""The tom reader is held to its inventory (#1427).

`model/tom_drop_measure.py` reads nine groups of eleven takes out of the 808
From Mars cache, fifteen with `--congas`. It used to take whatever `*.wav` a
group's folder held, so:

  * a partly fetched cache shortened a row's `n` and moved its median while
    the header still said "11 tunings";
  * an empty folder printed "-- no files in the cache" and the report went on;
  * a WAV of an unexpected name was measured as a tuning.

The expected names now come from the tracked `refaudio/index/808-from-mars.tsv`,
for the groups the command requests, and a requested group that does not hold
every one of them is REFUSED, by name, before anything is measured.

The cache is built here, so its answer is known; no audio from the licensed
pack is used. Each take is a 50 ms sine at 300 Hz + 20 Hz per tuning position,
so a row's settled f0 says which file it came from. The estimator behind the
reader (`tom_pitch_probe.measure`, 1.4 s a file) is replaced by a stand-in that
reads that one frequency back. Everything between the CLI and the file is real:
the index, the inventory, the membership check, the report and its JSON.

    .venv/bin/python -m pytest model/test_tom_drop_measure.py -q
"""
from __future__ import annotations

import json
import os
import pathlib
import shutil
import sys
import uuid

import numpy as np
import pytest
from scipy.io import wavfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import tom_drop_measure as tm                                   # first: must import on its own
import refaudio_local as rl                                     # noqa: E402  (tools/, which tm puts on sys.path)

#: The accents the command asks of each voice.
ACCENTS = {"LT": "ABC", "MT": "ABC", "HT": "ABC", "LC": "AB", "MC": "AB", "HC": "AB"}
LOW_TOM_A = "03. Low Tom/Clean/Digital/A"
HI_TOM_C = "05. Hi Tom/Clean/Digital/C"
TAKE = "Tom Low A 808 05.wav"


# ----------------------------------------------------------------- fixtures
@pytest.fixture
def root():
    """A cache root inside the repository, as the real one is, because
    `run_voice` records each take's path relative to REPO. Removed with the test."""
    r = tm.REPO / "refaudio" / "cache" / f"_pytest-tom-drop-{uuid.uuid4().hex}"
    r.mkdir(parents=True)
    yield r
    shutil.rmtree(r, ignore_errors=True)


def _hz(name: str) -> float:
    return 300.0 + 20.0 * int(pathlib.PurePosixPath(name).stem[-2:])


def _write(path: pathlib.Path) -> None:
    sr, t = 44100, np.arange(2205) / 44100
    wavfile.write(str(path), sr, (0.5 * np.sin(2 * np.pi * _hz(path.name) * t)).astype(np.float32))


def _group(root: pathlib.Path, group: str) -> pathlib.Path:
    """The cache folder of `group`, which is `<voice folder>/Clean/Digital/<accent>`."""
    return root / tm.STEM / group


def _cache(root: pathlib.Path, groups=tm.VOICES) -> pathlib.Path:
    """A cache holding every take the index lists for each of `groups`."""
    for vid, folder, _stem in groups:
        for acc in ACCENTS[vid]:
            d = _group(root, f"{folder}/Clean/Digital/{acc}")
            d.mkdir(parents=True, exist_ok=True)
            for name in tm.indexed_members(folder, acc):
                _write(d / name)
    return root


def _fast_measure(x, sr, *, label="", **_):
    """`tom_pitch_probe.measure`'s row shape, with the settled f0 read back as
    the peak bin. Exact for the fixture's sines, which sit on bins."""
    spec = np.abs(np.fft.rfft(x))
    return {"label": label, "sr": sr, "verdict": "OK", "why": "",
            "f_settled_hz": float(np.argmax(spec) * sr / len(x)),
            "floor_hz": 0.5, "ratio_first_period": 1.10, "ratio_second_period": 1.05,
            "ratio_at_onset": 1.20, "ratio_at_onset_nofirst": 1.15, "fit_tau_ms": 60.0,
            "fit_shape": "exponential", "excess_sigma": 8.0, "neighbour_db": -30.0,
            "peak_dbfs": -6.0, "floor_dbfs": -90.0}


def _refusing(why: str):
    """The same stand-in, declining one take: an estimator refusal, which the
    report must count and keep rather than treat as an inventory refusal."""
    def measure(x, sr, *, label="", **_):
        r = _fast_measure(x, sr, label=label)
        if label == TAKE:
            r.update(verdict="REFUSED", why=why)
        return r
    return measure


# ------------------------------------------------------------ the inventory
def test_the_index_names_the_groups_the_command_asks_for():
    """Eleven tunings at A/B/C for each tom and at A/B for each conga, which is
    all the pack holds for them, read from the tracked index and sorted."""
    for _vid, folder, _stem in tm.VOICES + tm.CONGAS:
        for acc in ACCENTS[_vid]:
            names = tm.indexed_members(folder, acc)
            assert len(names) == 11 and names == sorted(names), (folder, acc)
    assert tm.indexed_members("03. Low Tom", "A")[4] == TAKE
    assert tm.indexed_members("08. Hi Conga", "B")[0] == "Conga Hi B 808 01.wav"
    with pytest.raises(tm.Refused, match="no WAV"):
        tm.indexed_members("06. Low Conga", "C")


def test_an_unavailable_index_is_refused(root, monkeypatch):
    _cache(root, tm.VOICES)
    monkeypatch.setattr(tm, "CACHE", root)
    monkeypatch.setattr(rl, "REFAUDIO", root / "no-index")
    with pytest.raises(tm.Refused, match="808-from-mars.tsv"):
        tm.files_for("03. Low Tom", "Tom Low", "A")


def test_an_index_with_nothing_for_the_group_is_refused(root, monkeypatch):
    _cache(root, tm.VOICES)
    monkeypatch.setattr(tm, "CACHE", root)
    (root / "index").mkdir()
    (root / "index" / "808-from-mars.tsv").write_text("path\tbytes\n", encoding="utf-8")
    monkeypatch.setattr(rl, "REFAUDIO", root)
    with pytest.raises(tm.Refused, match="no WAV"):
        tm.files_for("03. Low Tom", "Tom Low", "A")


# ---------------------------------------------------------------- the reader
def test_a_complete_group_reads_every_indexed_take_in_name_order(root, monkeypatch):
    _cache(root, tm.VOICES[:1])
    monkeypatch.setattr(tm, "CACHE", root)
    got = tm.files_for("03. Low Tom", "Tom Low", "A")
    assert [p.name for p in got] == tm.indexed_members("03. Low Tom", "A")
    assert len(got) == 11 and got == sorted(got)


def test_one_absent_take_is_named_with_its_group(root, monkeypatch):
    """The old reader returned the other ten, and the row said n=10."""
    _cache(root, tm.VOICES)
    monkeypatch.setattr(tm, "CACHE", root)
    (_group(root, LOW_TOM_A) / TAKE).unlink()
    with pytest.raises(tm.Refused) as e:
        tm.files_for("03. Low Tom", "Tom Low", "A")
    msg = str(e.value)
    assert LOW_TOM_A in msg and TAKE in msg


def test_an_equal_count_substitute_does_not_stand_in_for_a_take(root, monkeypatch):
    """The folder holds 11 WAVs again, so a count, and the old glob, both read
    a complete group with one wrong take in it."""
    _cache(root, tm.VOICES)
    monkeypatch.setattr(tm, "CACHE", root)
    d = _group(root, LOW_TOM_A)
    (d / TAKE).rename(d / "Tom Low A 808 99.wav")
    assert len(list(d.glob("*.wav"))) == 11
    with pytest.raises(tm.Refused) as e:
        tm.files_for("03. Low Tom", "Tom Low", "A")
    assert TAKE in str(e.value) and "808 99" not in str(e.value)


def test_an_unexpected_wav_never_contributes(root, monkeypatch):
    """A complete group plus a twelfth WAV: the report is about the eleven the
    index lists, and the stranger is not one of them."""
    _cache(root, tm.VOICES)
    monkeypatch.setattr(tm, "CACHE", root)
    d = _group(root, "04. Mid Tom/Clean/Digital/B")
    _write(d / "Tom Mid B 808 12.wav")
    assert [p.name for p in tm.files_for("04. Mid Tom", "Tom Mid", "B")] == \
        tm.indexed_members("04. Mid Tom", "B")


def test_an_empty_group_is_refused_with_every_expected_take(root, monkeypatch):
    """The old reader returned [] here, the row printed "-- no files in the
    cache", and the report went on to the next accent."""
    _cache(root, tm.VOICES)
    monkeypatch.setattr(tm, "CACHE", root)
    for p in _group(root, HI_TOM_C).iterdir():
        p.unlink()
    with pytest.raises(tm.Refused) as e:
        tm.files_for("05. Hi Tom", "Tom Hi", "C")
    msg = str(e.value)
    assert HI_TOM_C in msg
    assert all(n in msg for n in tm.indexed_members("05. Hi Tom", "C"))


def test_a_directory_at_a_wav_path_is_not_a_take(root, monkeypatch):
    _cache(root, tm.VOICES)
    monkeypatch.setattr(tm, "CACHE", root)
    gone = _group(root, LOW_TOM_A) / TAKE
    gone.unlink()
    gone.mkdir()
    with pytest.raises(tm.Refused, match=TAKE):
        tm.files_for("03. Low Tom", "Tom Low", "A")


# -------------------------------------------------------------------- the CLI
def test_the_cli_refuses_before_anything_is_measured_and_leaves_the_json_intact(
        root, tmp_path, monkeypatch, capsys):
    """Through `main`, with and without `--controls`: `REFUSED`, the group and
    the take, exit 2, no report, and the previous `--json` result untouched."""
    _cache(root, tm.VOICES)
    monkeypatch.setattr(tm, "CACHE", root)
    (_group(root, HI_TOM_C) / "Tom Hi C 808 11.wav").unlink()

    def past_the_inventory(*a, **k):
        raise AssertionError("the report measured a take from an incomplete cache")
    monkeypatch.setattr(tm.P, "measure", past_the_inventory)
    out = tmp_path / "result.json"
    previous = '{"sentinel": "the previous run"}'
    out.write_text(previous, encoding="utf-8")
    for argv in ([], ["--controls"]):
        assert tm.main([*argv, "--json", str(out)]) == 2
        text = capsys.readouterr().out
        assert "REFUSED" in text and HI_TOM_C in text and "Tom Hi C 808 11.wav" in text
        assert "=== LT" not in text and "THE ANSWER" not in text
        assert out.read_text(encoding="utf-8") == previous


def test_the_cli_refuses_an_unreadable_index_and_leaves_the_json_intact(
        root, tmp_path, monkeypatch, capsys):
    """A permission error reading the index is an unavailable inventory: a
    named REFUSED and exit 2, not a PermissionError traceback (#1427)."""
    _cache(root, tm.VOICES)
    monkeypatch.setattr(tm, "CACHE", root)

    def unreadable(archive):
        raise PermissionError(13, "Permission denied", str(tm.INDEX))
    monkeypatch.setattr(rl, "indexed_sizes", unreadable)
    out = tmp_path / "result.json"
    previous = '{"sentinel": "the previous run"}'
    out.write_text(previous, encoding="utf-8")
    assert tm.main(["--json", str(out)]) == 2
    text = capsys.readouterr().out
    assert "REFUSED" in text and "808-from-mars.tsv" in text and "Permission denied" in text
    assert out.read_text(encoding="utf-8") == previous


def test_the_cli_refuses_an_inaccessible_tom_cache(tmp_path, monkeypatch, capsys):
    cache = tmp_path / "cache"
    cache.mkdir()
    monkeypatch.setattr(tm, "CACHE", cache)
    out = tmp_path / "result.json"
    previous = b'{"previous": true}\n'
    out.write_bytes(previous)
    cache.chmod(0)
    try:
        if os.access(cache, os.X_OK):
            pytest.skip("requires a user subject to directory search permissions")
        assert tm.main(["--json", str(out)]) == 2
    finally:
        cache.chmod(0o700)
    text = capsys.readouterr().out
    assert "REFUSED" in text and "Permission denied" in text and str(cache) in text
    assert out.read_bytes() == previous


@pytest.mark.parametrize(("row", "field"), [
    ("folder/take.wav", "bytes"),
    ("folder/take.wav\t", "bytes"),
    ("\t12", "path"),
])
def test_the_cli_refuses_an_incomplete_tsv_row(tmp_path, monkeypatch, capsys, row, field):
    index = tmp_path / "index" / tm.INDEX.name
    index.parent.mkdir()
    index.write_text(f"path\tbytes\n{row}\n", encoding="utf-8")
    monkeypatch.setattr(rl, "REFAUDIO", tmp_path)
    monkeypatch.setattr(tm, "INDEX", index)
    out = tmp_path / "result.json"
    previous = b'{"previous": true}\n'
    out.write_bytes(previous)
    assert tm.main(["--json", str(out)]) == 2
    text = capsys.readouterr().out
    assert "REFUSED" in text and str(index) in text and field in text
    assert out.read_bytes() == previous


def test_the_cli_names_every_absent_take_in_every_requested_group(root, monkeypatch, capsys):
    _cache(root, tm.VOICES)
    monkeypatch.setattr(tm, "CACHE", root)
    (_group(root, LOW_TOM_A) / TAKE).unlink()
    (_group(root, "05. Hi Tom/Clean/Digital/B") / "Tom Hi B 808 01.wav").unlink()
    assert tm.main([]) == 2
    text = capsys.readouterr().out
    assert LOW_TOM_A in text and TAKE in text
    assert "05. Hi Tom/Clean/Digital/B" in text and "Tom Hi B 808 01.wav" in text


def test_the_cli_measures_the_complete_inventory(root, tmp_path, monkeypatch, capsys):
    """All 99 takes, each read from its own file: every row's settled f0 is the
    frequency written into the take of that name, and no other."""
    _cache(root, tm.VOICES)
    monkeypatch.setattr(tm, "CACHE", root)
    monkeypatch.setattr(tm.P, "measure", _fast_measure)
    out = tmp_path / "result.json"
    assert tm.main(["--json", str(out)]) == 0
    assert "REFUSED" not in capsys.readouterr().out
    res = json.loads(out.read_text(encoding="utf-8"))
    assert sorted(res["voices"]) == ["HT", "LT", "MT"]
    for vid, _folder, _stem in tm.VOICES:
        for acc in "ABC":
            cell = res["voices"][vid][acc]
            assert cell["n_ok"] == 11 and cell["n_refused"] == 0, (vid, acc)
            labels = [r["label"] for r in cell["rows"]]
            assert labels == sorted(labels) == tm.indexed_members(_folder, acc)
            for row in cell["rows"]:
                assert row["f_settled_hz"] == pytest.approx(_hz(row["label"])), row


def test_a_per_take_estimator_refusal_is_counted_and_is_not_an_inventory_refusal(
        root, tmp_path, monkeypatch):
    """Complete inventory, one take the estimator declines: still measured, and
    the refusal is kept on its row as before."""
    why = "the estimator declined this take"
    _cache(root, tm.VOICES)
    monkeypatch.setattr(tm, "CACHE", root)
    monkeypatch.setattr(tm.P, "measure", _refusing(why))
    out = tmp_path / "result.json"
    assert tm.main(["--json", str(out)]) == 0
    cell = json.loads(out.read_text(encoding="utf-8"))["voices"]["LT"]["A"]
    assert cell["n_ok"] == 10 and cell["n_refused"] == 1
    assert cell["refusals"] == [{"file": TAKE, "why": why}]


def test_absent_congas_are_required_only_by_the_congas_flag(root, tmp_path, monkeypatch, capsys):
    """The default run asks for the toms alone, so a cache without one conga is
    complete for it. `--congas` asks for six more groups and refuses on the
    first take of the first one that is not there."""
    _cache(root, tm.VOICES)
    monkeypatch.setattr(tm, "CACHE", root)
    monkeypatch.setattr(tm.P, "measure", _fast_measure)
    assert tm.main(["--json", str(tmp_path / "toms.json")]) == 0
    assert tm.main(["--congas", "--json", str(tmp_path / "all.json")]) == 2
    text = capsys.readouterr().out
    assert "06. Low Conga/Clean/Digital/A" in text and "Conga Low A 808 01.wav" in text
    assert "08. Hi Conga/Clean/Digital/B" in text and "Conga Hi B 808 11.wav" in text
    assert not (tmp_path / "all.json").exists()
    _cache(root, tm.CONGAS)
    assert tm.main(["--congas", "--json", str(tmp_path / "all.json")]) == 0
    res = json.loads((tmp_path / "all.json").read_text(encoding="utf-8"))
    assert sorted(res["voices"]) == ["HC", "HT", "LC", "LT", "MC", "MT"]
    assert res["voices"]["HC"]["B"]["n_ok"] == 11 and "C" not in res["voices"]["HC"]


def test_provenance_still_reads_the_committed_catalog():
    """The guard below must be satisfiable against the catalog that ships."""
    prov = tm.provenance()
    assert prov["archive"] == "808-from-mars.zip" and len(prov["archive_sha256"]) == 64


@pytest.mark.parametrize("catalog, why", [({}, "'packs'"), ({"packs": None}, "'packs'"),
                                          ([], "not a JSON object"), (None, "not a JSON object")])
def test_the_cli_refuses_a_catalog_that_cannot_be_read(tmp_path, monkeypatch, capsys, catalog, why):
    """`provenance()` read `cat["packs"]` with [] and `main()` had no handler
    for its refusal: exit 1 by traceback, FAIL, where tools/exit_contract.py
    says an unmet precondition is exit 2 (#1604, #1623). Through the CLI, with
    the inventory answered (it has its own tests above)."""
    monkeypatch.setattr(tm, "require_inventory", lambda groups: None)
    monkeypatch.setattr(tm.P, "measure",
                        lambda *a, **k: pytest.fail("a take was measured over a partial catalog"))
    (tmp_path / "refaudio").mkdir()
    (tmp_path / "refaudio" / "catalog.json").write_text(json.dumps(catalog), encoding="utf-8")
    monkeypatch.setattr(tm, "REPO", tmp_path)
    out = tmp_path / "result.json"
    assert tm.main(["--json", str(out)]) == 2
    text = capsys.readouterr().out
    assert "REFUSED" in text and why in text
    assert not out.exists()
