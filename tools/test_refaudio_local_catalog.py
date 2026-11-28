"""tools/refaudio_local.py: a catalog that cannot be read is REFUSED (#1604, #1623).

`pack_record()` reads `cat["packs"]` with `[]`. Before #1623 a catalog without
it exited 1 by `KeyError` -- FAIL -- where `tools/exit_contract.py` says an
unmet precondition is exit 2. One control per key through the tool's own
`main()`: the catalog is read before the archive is located, so no archive is
needed.
"""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import refaudio_local as rl  # noqa: E402

ARCHIVE = "pack_a.zip"


@pytest.fixture
def catalog(tmp_path, monkeypatch):
    ra = tmp_path / "refaudio"
    ra.mkdir()
    (ra / "catalog.json").write_text(json.dumps({"packs": [{"archive": ARCHIVE}]}))
    monkeypatch.setattr(rl, "REFAUDIO", ra)
    return ra


def test_the_committed_catalog_still_answers_for_a_pack():
    """The guard must be satisfiable against the catalog that ships."""
    assert rl.pack_record("808-from-mars.zip")["archive"] == "808-from-mars.zip"


def test_a_catalog_without_packs_is_refused(catalog, capsys):
    (catalog / "catalog.json").write_text("{}")
    assert rl.main([ARCHIVE, "Pack/WAV/a.wav"]) == rl.REFUSED
    out = capsys.readouterr().out
    assert "REFUSED" in out and "'packs'" in out


def test_a_catalog_with_null_packs_is_refused(catalog, capsys):
    (catalog / "catalog.json").write_text(json.dumps({"packs": None}))
    assert rl.main([ARCHIVE, "Pack/WAV/a.wav"]) == rl.REFUSED
    out = capsys.readouterr().out
    assert "REFUSED" in out and "'packs'" in out


@pytest.mark.parametrize("cat, why", [([], "not a JSON object"), (None, "not a JSON object")])
def test_a_catalog_that_is_not_an_object_is_refused(catalog, capsys, cat, why):
    (catalog / "catalog.json").write_text(json.dumps(cat))
    assert rl.main([ARCHIVE, "Pack/WAV/a.wav"]) == rl.REFUSED
    out = capsys.readouterr().out
    assert "REFUSED" in out and why in out
