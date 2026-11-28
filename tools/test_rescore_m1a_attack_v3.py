#!/usr/bin/env python3
"""`rescore_m1a_attack_v3` refuses a sweep point -- or the qualification it is
scored against -- that it cannot read.

#1604, #1622. `main()` reads `point["audio"]` and `point["sha256"]` with `[]`,
and reaches `mono_m1a_score._qualified_attack_report()`, which reads
`suite["sources_sha256"]`, `report["v3"]` and `report["bound_ms"]` the same
way. Before #1622 any of them missing exited 1 by `KeyError`. One control per
key through the tool's own `main()`.

No clean checkout can rescore: every committed point names a WAV under
`build/`, so a complete run REFUSES on the first point's missing audio. The
controls here cover everything before that, over scratch copies of the
committed point and qualification records, so none of them is ever written.
"""
from __future__ import annotations

import json
import pathlib
import shutil
import sys

import pytest

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import rescore_m1a_attack_v3 as rescore                             # noqa: E402

SWEEP = rescore.sweep.OUT
QUALIFICATION = rescore.bass.QUALIFICATION
FIRST_POINT = rescore.sweep.point_name(rescore.SETTINGS_MS[0])
POINT_KEYS = ("audio", "sha256")
QUALIFICATION_KEYS = (("suite-v3.json", "sources_sha256"), ("qualification.json", "v3"),
                      ("qualification.json", "bound_ms"))


def _points(tmp_path, monkeypatch, **replaced):
    scratch = tmp_path / "attack-sweep"
    shutil.copytree(SWEEP, scratch)
    for filename, record in replaced.items():
        (scratch / filename).write_text(json.dumps(record))
    monkeypatch.setattr(rescore.sweep, "OUT", scratch)


def _qualification(tmp_path, monkeypatch, **replaced):
    scratch = tmp_path / "attack-qualification"
    shutil.copytree(QUALIFICATION, scratch)
    for filename, record in replaced.items():
        (scratch / filename).write_text(json.dumps(record))
    monkeypatch.setattr(rescore.bass, "QUALIFICATION", scratch)


def _main(capsys):
    rc = rescore.main()
    return rc, capsys.readouterr().out


def test_the_committed_records_carry_every_key():
    """The guard must be satisfiable against the records that ship."""
    for ms in rescore.SETTINGS_MS:
        point = json.loads((SWEEP / f"{rescore.sweep.point_name(ms)}.json").read_text())
        assert set(POINT_KEYS) <= point.keys()
    for filename, key in QUALIFICATION_KEYS:
        assert key in json.loads((QUALIFICATION / filename).read_text())


@pytest.mark.parametrize("key", POINT_KEYS)
def test_a_sweep_point_without_a_key_is_refused(tmp_path, monkeypatch, capsys, key):
    point = json.loads((SWEEP / f"{FIRST_POINT}.json").read_text())
    del point[key]
    _points(tmp_path, monkeypatch, **{f"{FIRST_POINT}.json": point})
    # `sha256` is read only while the point's audio is where the point says
    # it is, so the scratch root holds a file at that path.
    audio = tmp_path / "root" / point.get("audio", "build/m1a-attack-sweep/x.wav")
    audio.parent.mkdir(parents=True)
    audio.write_bytes(b"not the sweep audio")
    monkeypatch.setattr(rescore, "ROOT", tmp_path / "root")
    _qualification(tmp_path, monkeypatch)
    before = (rescore.bass.QUALIFICATION / "rescore.json").read_bytes()
    rc, printed = _main(capsys)
    assert rc == 2 and "REFUSED" in printed and repr(key) in printed
    assert (rescore.bass.QUALIFICATION / "rescore.json").read_bytes() == before


@pytest.mark.parametrize("filename, key", QUALIFICATION_KEYS)
def test_a_qualification_record_without_a_key_is_refused(tmp_path, monkeypatch, capsys, filename, key):
    record = json.loads((QUALIFICATION / filename).read_text())
    del record[key]
    _points(tmp_path, monkeypatch)
    _qualification(tmp_path, monkeypatch, **{filename: record})
    before = (rescore.bass.QUALIFICATION / "rescore.json").read_bytes()
    rc, printed = _main(capsys)
    assert rc == 2 and "REFUSED" in printed and repr(key) in printed
    assert (rescore.bass.QUALIFICATION / "rescore.json").read_bytes() == before


@pytest.mark.parametrize("point, why", [({}, "'audio'"), ([], "not a JSON object"),
                                        (None, "not a JSON object")])
def test_a_sweep_point_that_is_not_an_object_is_refused(tmp_path, monkeypatch, capsys, point, why):
    _points(tmp_path, monkeypatch, **{f"{FIRST_POINT}.json": point})
    _qualification(tmp_path, monkeypatch)
    rc, printed = _main(capsys)
    assert rc == 2 and "REFUSED" in printed and why in printed


def test_a_sweep_point_with_a_null_key_is_refused(tmp_path, monkeypatch, capsys):
    point = json.loads((SWEEP / f"{FIRST_POINT}.json").read_text())
    point["sha256"] = None
    _points(tmp_path, monkeypatch, **{f"{FIRST_POINT}.json": point})
    _qualification(tmp_path, monkeypatch)
    rc, printed = _main(capsys)
    assert rc == 2 and "REFUSED" in printed and "'sha256'" in printed and "null" in printed


@pytest.mark.parametrize("filename, record, why", [
    ("suite-v3.json", {}, "'sources_sha256'"), ("suite-v3.json", [], "not a JSON object"),
    ("qualification.json", None, "not a JSON object")])
def test_a_qualification_record_that_is_not_an_object_is_refused(
        tmp_path, monkeypatch, capsys, filename, record, why):
    _points(tmp_path, monkeypatch)
    _qualification(tmp_path, monkeypatch, **{filename: record})
    rc, printed = _main(capsys)
    assert rc == 2 and "REFUSED" in printed and why in printed
