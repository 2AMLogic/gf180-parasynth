#!/usr/bin/env python3
"""Tests for `tools/refaudio_local.py` and the helpers the three reference-audio
routes share through it (#1602).

    .venv/bin/python -m pytest tools/test_refaudio_local.py -q

`refaudio_fetch.py` (ssh) and `refaudio_s3.py` (S3) take their catalog lookup,
member path check, index load and zip loop from this module, so a defect here is
a defect in every route that puts a verified 808 recording into refaudio/cache/.
Until #1602 this module had no tests beside it.

Every case is one of two shapes: a FALSE GREEN (a file reported FETCHED that is
not the file the index describes, or written where nothing asked for it to be)
or a REFUSED rendered as a traceback -- exit 1, which every caller reads as FAIL,
for a precondition that was simply not met. The tests marked START RED failed
against `origin/main` 1023ef4b for the reason they name.

No licensed audio and no multi-GB download: a throwaway refaudio/ with a
catalog, a TSV index and a two-member zip, with REFAUDIO_LOCAL pointed at it.
"""
from __future__ import annotations

import dataclasses
import hashlib
import io
import json
import os
import pathlib
import sys
import zipfile
import zlib

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import refaudio_local as rl                                      # noqa: E402

ARCHIVE = "pack_a.zip"
KICK, OTHER = "Pack A/WAV/kick.wav", "Pack A/Kontakt/other.wav"
KICK_BYTES, OTHER_BYTES = b"K" * 1234, b"O" * 10
GOOD_INDEX = f"path\tbytes\n{KICK}\t{len(KICK_BYTES)}\n"
# A prefix that is itself benign, selecting a name that climbs four directories:
# out of the pack's cache directory and out of refaudio/cache/ itself.
ESCAPE = "Loops/WAV/../../../../escaped.wav"


def _zip(**members: bytes) -> bytes:
    b = io.BytesIO()
    with zipfile.ZipFile(b, "w") as z:
        for name, payload in members.items():
            z.writestr(name, payload)
    return b.getvalue()


@dataclasses.dataclass
class Lib:
    """A throwaway refaudio/ whose catalog vouches for exactly the bytes in packs/."""
    root: pathlib.Path
    refaudio: pathlib.Path
    packs: pathlib.Path

    @property
    def cache(self) -> pathlib.Path:
        return self.refaudio / "cache"

    def archive(self, data: bytes) -> None:
        (self.packs / ARCHIVE).write_bytes(data)

    def catalog(self, data: bytes, **override) -> None:
        rec = {"archive": ARCHIVE, "archive_bytes": len(data),
               "archive_sha256": hashlib.sha256(data).hexdigest(), **override}
        (self.refaudio / "catalog.json").write_text(json.dumps({"packs": [rec]}), encoding="utf-8")

    def index(self, text: str) -> None:
        (self.refaudio / "index" / "pack_a.tsv").write_text(text, encoding="utf-8")

    def fetched(self) -> list[str]:
        root = self.cache
        return sorted(p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file()) \
            if root.exists() else []

    def escaped(self) -> list[pathlib.Path]:
        """Every `escaped.wav` anywhere under the throwaway tree, whichever depth the
        climbing name reached."""
        return sorted(self.root.rglob("escaped.wav"))


@pytest.fixture
def lib(tmp_path, monkeypatch) -> Lib:
    L = Lib(tmp_path, tmp_path / "refaudio", tmp_path / "packs")
    (L.refaudio / "index").mkdir(parents=True)
    L.packs.mkdir()
    blob = _zip(**{KICK: KICK_BYTES, OTHER: OTHER_BYTES})
    L.archive(blob)
    L.catalog(blob)
    L.index(GOOD_INDEX)
    monkeypatch.setattr(rl, "REFAUDIO", L.refaudio)
    monkeypatch.setenv("REFAUDIO_LOCAL", str(L.packs))
    return L


# --- the controls: this route can pass -----------------------------------------

def test_an_indexed_member_lands_whole_at_the_size_the_index_states(lib):
    assert rl.main([ARCHIVE, KICK]) == rl.FETCHED
    assert (lib.cache / "pack_a" / KICK).read_bytes() == KICK_BYTES
    assert lib.fetched() == ["pack_a/" + KICK]


def test_prefix_takes_every_indexed_member_under_it_and_nothing_else(lib):
    assert rl.main(["--prefix", ARCHIVE, "Pack A/WAV/"]) == rl.FETCHED
    assert lib.fetched() == ["pack_a/" + KICK], "the un-indexed Kontakt member is not under the prefix"


def test_a_size_that_disagrees_with_the_index_is_a_fail_and_leaves_no_file(lib):
    lib.index(f"path\tbytes\n{KICK}\t{len(KICK_BYTES) - 1}\n")
    assert rl.main([ARCHIVE, KICK]) == rl.FAIL
    assert lib.fetched() == []


def test_an_unchecked_size_is_said_out_loud(lib, capsys):
    """A member the index does not list is fetched, but never silently."""
    assert rl.main([ARCHIVE, OTHER]) == rl.FETCHED
    out = capsys.readouterr().out
    assert f"note     {OTHER!r} is not in the index: size will NOT be checked" in out
    assert "size unchecked" in out


def test_a_pack_with_no_index_says_sizes_are_unchecked(lib, capsys):
    (lib.refaudio / "index" / "pack_a.tsv").unlink()
    assert rl.main([ARCHIVE, KICK]) == rl.FETCHED
    assert f"note     no committed index for {ARCHIVE}: sizes will NOT be checked" in capsys.readouterr().out


def test_one_bad_member_fails_the_run_but_keeps_the_good_one(lib, capsys):
    assert rl.main([ARCHIVE, "Pack A/WAV/missing.wav", KICK]) == rl.FAIL
    assert "FAIL     Pack A/WAV/missing.wav\n         no such member in the archive" in capsys.readouterr().out
    assert lib.fetched() == ["pack_a/" + KICK]


# --- REFUSED, each before the thing it guards ----------------------------------

def test_no_refaudio_local_is_refused_and_nothing_is_written(lib, monkeypatch):
    monkeypatch.delenv("REFAUDIO_LOCAL")
    assert rl.main([ARCHIVE, KICK]) == rl.REFUSED
    assert not lib.cache.exists()


def test_an_archive_absent_from_the_catalog_is_refused(lib, capsys):
    assert rl.main(["nope.zip", KICK]) == rl.REFUSED
    assert "not an archive in refaudio/catalog.json" in capsys.readouterr().out


def test_an_archive_of_the_wrong_size_is_refused_before_it_is_hashed(lib, monkeypatch):
    lib.archive(b"K" * 5)

    def never(_):
        raise AssertionError("a wrong-size archive must not be hashed")
    monkeypatch.setattr(rl, "sha256_of", never)
    assert rl.main([ARCHIVE, KICK]) == rl.REFUSED


def test_an_archive_of_the_wrong_hash_is_refused_and_nothing_is_written(lib, monkeypatch):
    monkeypatch.setattr(rl, "sha256_of", lambda p: "0" * 64)
    assert rl.main([ARCHIVE, KICK]) == rl.REFUSED
    assert not lib.cache.exists()


@pytest.mark.parametrize("member", ["../escape.wav", "/abs.wav", "", "a/../../b.wav", "."])
def test_a_member_that_is_not_a_relative_path_inside_the_archive_is_refused(lib, member):
    assert rl.main([ARCHIVE, member]) == rl.REFUSED
    assert not lib.cache.exists()


# --- START RED (#1602): a malformed index is REFUSED, never a traceback --------

def test_an_index_row_with_no_size_is_REFUSED_and_nothing_is_extracted(lib, capsys):
    lib.index(f"path\tbytes\n{KICK}\t\n")
    assert rl.main([ARCHIVE, KICK]) == rl.REFUSED
    out = capsys.readouterr().out
    assert "REFUSED" in out and "missing bytes" in out


def test_an_index_that_cannot_be_opened_is_REFUSED(lib):
    tsv = lib.refaudio / "index" / "pack_a.tsv"
    tsv.unlink()
    tsv.mkdir()                                  # IsADirectoryError, whoever runs the test
    assert rl.main([ARCHIVE, KICK]) == rl.REFUSED


def test_an_index_row_the_csv_reader_rejects_is_REFUSED(lib):
    lib.index(f"path\tbytes\n{'x' * 200000}\t12\n")          # _csv.Error: over the field limit
    assert rl.main([ARCHIVE, KICK]) == rl.REFUSED


# --- START RED (#1602): --prefix reads nothing before the catalog gate ---------

@pytest.mark.parametrize("archive", ["not_in_catalog.zip", "../../outside.zip"])
def test_prefix_names_the_catalog_before_it_reads_any_index(lib, monkeypatch, capsys, archive):
    reads = []
    monkeypatch.setattr(rl, "read_indexed_sizes", lambda tsv: reads.append(tsv) or {})
    assert rl.main(["--prefix", archive, "Pack A/"]) == rl.REFUSED
    assert "not an archive in refaudio/catalog.json" in capsys.readouterr().out
    assert reads == [], "nothing under refaudio/index/ may be read for an archive the catalog does not name"


def test_prefix_cannot_reach_a_tsv_outside_the_index_directory(lib, capsys):
    """The archive name used to be joined onto refaudio/index/ BEFORE the catalog was
    consulted, so `--prefix ../../outside.zip` opened and parsed a file two directories
    above it, and reported THAT file's defect instead of the catalog's."""
    (lib.root / "outside.tsv").write_text("path\tbytes\nx\t\n", encoding="utf-8")
    assert rl.main(["--prefix", "../../outside.zip", "Pack A/"]) == rl.REFUSED
    assert "not an archive in refaudio/catalog.json" in capsys.readouterr().out


def test_prefix_with_no_indexed_member_is_refused_before_the_archive_is_located(lib, monkeypatch, capsys):
    monkeypatch.delenv("REFAUDIO_LOCAL")         # locate() has its own refusal; it must not be reached
    assert rl.main(["--prefix", ARCHIVE, "NoSuchFolder/"]) == rl.REFUSED
    assert "no indexed member under any of ['NoSuchFolder/']" in capsys.readouterr().out


def test_prefix_on_an_unindexed_pack_is_refused(lib, capsys):
    (lib.refaudio / "index" / "pack_a.tsv").unlink()
    assert rl.main(["--prefix", ARCHIVE, "Pack A/"]) == rl.REFUSED
    assert "--prefix needs a committed index" in capsys.readouterr().out


def test_the_index_is_read_once_per_run(lib, monkeypatch):
    reads = []
    real = rl.read_indexed_sizes
    monkeypatch.setattr(rl, "read_indexed_sizes", lambda tsv: reads.append(tsv) or real(tsv))
    assert rl.main(["--prefix", ARCHIVE, "Pack A/WAV/"]) == rl.FETCHED
    assert len(reads) == 1, "the index used to be parsed twice per --prefix run"


# --- START RED (#1602): a bad zip is FAIL, never a traceback -------------------

def test_an_archive_that_is_not_a_zip_is_FAIL(lib, capsys):
    junk = b"not a zip"
    lib.archive(junk)
    lib.catalog(junk)                            # it hashes right: the catalog vouches for junk
    assert rl.main([ARCHIVE, KICK]) == rl.FAIL
    assert "does not open as a zip" in capsys.readouterr().out
    assert not lib.cache.exists()


def test_a_corrupt_member_fails_alone_and_the_others_are_still_fetched(lib, capsys):
    """A real corrupt deflate stream: the member's CRC no longer matches. One bad
    member is one FAIL line; it neither aborts the run nor leaves a partial file."""
    import random
    rnd = random.Random(1602)
    noise = bytes(rnd.getrandbits(8) for _ in range(20000))
    b = io.BytesIO()
    with zipfile.ZipFile(b, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(KICK, KICK_BYTES)
        z.writestr(OTHER, noise)
    raw = bytearray(b.getvalue())
    with zipfile.ZipFile(io.BytesIO(raw)) as z:
        inf = z.getinfo(OTHER)
        start = inf.header_offset + 30 + len(inf.filename) + len(inf.extra)
    for i in range(start + 50, start + 60):
        raw[i] ^= 0xFF
    lib.archive(bytes(raw))
    lib.catalog(bytes(raw))
    lib.index(GOOD_INDEX + f"{OTHER}\t{len(noise)}\n")
    assert rl.main([ARCHIVE, OTHER, KICK]) == rl.FAIL
    out = capsys.readouterr().out
    assert f"FAIL     {OTHER}\n         " in out and "1 fetched, 1 failed" in out
    assert lib.fetched() == ["pack_a/" + KICK]
    assert not list(lib.cache.rglob("*.part"))


def test_a_member_the_decompressor_rejects_fails_alone(lib, monkeypatch, capsys):
    real = zipfile.ZipFile.open

    def open_(self, name, *a, **kw):
        if getattr(name, "filename", name) == OTHER:
            raise zlib.error("Error -3 while decompressing data: invalid stored block lengths")
        return real(self, name, *a, **kw)
    monkeypatch.setattr(zipfile.ZipFile, "open", open_)
    assert rl.main([ARCHIVE, OTHER, KICK]) == rl.FAIL
    assert f"FAIL     {OTHER}" in capsys.readouterr().out
    assert lib.fetched() == ["pack_a/" + KICK]


# --- a member is only ever written inside its own cache directory -------------

def test_extract_writes_nothing_outside_the_pack_cache_directory(lib):
    """`extract` is the last line before a file is opened: whatever asked for a
    member, its destination is inside refaudio/cache/<pack>/, and a name that
    climbs out is refused there too (#1602)."""
    blob = _zip(**{ESCAPE: b"x"})
    lib.archive(blob)
    lib.catalog(blob)
    with zipfile.ZipFile(lib.packs / ARCHIVE) as zf:
        why = rl.extract(zf, ARCHIVE, ESCAPE, None, lib.cache)
    assert why is not None and "outside refaudio/cache/pack_a/" in why
    assert lib.escaped() == [], "on origin/main this returned None and wrote refaudio/escaped.wav"
    assert not lib.cache.exists()


def test_extract_from_archive_refuses_a_member_under_a_prefix_that_climbs_out(lib):
    """START RED (#1602). A benign prefix says nothing about the names under it: this
    one selects `Loops/WAV/../../../../escaped.wav` as well. Every expanded name is
    checked before anything is written, so the run is REFUSED with no file at all --
    not even the member that was fine."""
    blob = _zip(**{"Loops/WAV/a.wav": b"a", ESCAPE: b"x"})
    lib.archive(blob)
    lib.catalog(blob)
    with zipfile.ZipFile(lib.packs / ARCHIVE) as zf:
        with pytest.raises(rl.Refused, match="relative path inside the archive"):
            rl.extract_from_archive(zf, ARCHIVE, None, None, lib.cache, prefixes=["Loops/WAV/"])
    assert lib.escaped() == []
    assert lib.fetched() == [], "not even the member that was fine is written"


def test_extract_from_archive_refuses_a_prefix_with_nothing_under_it(lib):
    with zipfile.ZipFile(lib.packs / ARCHIVE) as zf:
        with pytest.raises(rl.Refused, match="no member under any of"):
            rl.extract_from_archive(zf, ARCHIVE, None, None, lib.cache, prefixes=["NoSuch/"])


def test_extract_from_archive_takes_the_indexed_members_under_a_prefix(lib, capsys):
    with zipfile.ZipFile(lib.packs / ARCHIVE) as zf:
        assert rl.extract_from_archive(zf, ARCHIVE, None, {KICK: len(KICK_BYTES)}, lib.cache,
                                       prefixes=["Pack A/"]) == rl.FETCHED
    assert lib.fetched() == ["pack_a/" + KICK]


# --- the shared helpers, each on its own ---------------------------------------

@pytest.mark.parametrize("bad", ["../escape.wav", "/abs.wav", "", "a/../../b.wav", "."])
def test_validate_members_is_the_one_path_check(bad):
    rl.validate_members(["Pack A/WAV/kick.wav"])
    with pytest.raises(rl.Refused, match="relative path inside the archive"):
        rl.validate_members(["Pack A/WAV/kick.wav", bad])


def test_pack_record_and_load_indexed_sizes_read_the_library_they_are_pointed_at(lib):
    """`refaudio_fetch.py` is redirected to a test library by rebinding ITS
    `REFAUDIO`; the shared helpers must follow the caller, not this module."""
    other = lib.root / "other"
    (other / "index").mkdir(parents=True)
    (other / "catalog.json").write_text(json.dumps({"packs": [{"archive": "z.zip", "k": 1}]}), encoding="utf-8")
    (other / "index" / "z.tsv").write_text("path\tbytes\na\t1\n", encoding="utf-8")
    assert rl.pack_record("z.zip", refaudio=other) == {"archive": "z.zip", "k": 1}
    assert rl.load_indexed_sizes("z.zip", refaudio=other) == {"a": 1}
    assert rl.load_indexed_sizes("absent.zip", refaudio=other) is None
    with pytest.raises(rl.Refused, match="not an archive in refaudio/catalog.json"):
        rl.pack_record("pack_a.zip", refaudio=other)
    with pytest.raises(rl.Refused, match="not an archive in refaudio/catalog.json"):
        rl.pack_record("z.zip")                  # this module's own library has no z.zip


def test_load_indexed_sizes_refuses_what_read_indexed_sizes_raises(lib):
    """`read_indexed_sizes` keeps its #1551 contract (ValueError / OSError) for the
    model tools that catch it; the fetchers get the Refused form."""
    lib.index(f"path\tbytes\n{KICK}\t\n")
    with pytest.raises(rl.Refused, match="missing bytes"):
        rl.load_indexed_sizes(ARCHIVE)
    with pytest.raises(ValueError, match="missing bytes"):
        rl.indexed_sizes(ARCHIVE)
    assert rl.load_indexed_sizes("noindex.zip") is None


def test_resolve_prefix_from_index_is_sorted_and_refuses_an_empty_selection():
    sizes = {"b/2.wav": 2, "b/1.wav": 1, "c/3.wav": 3}
    assert rl.resolve_prefix_from_index("p.zip", ["b/", "c/"], sizes) == ["b/1.wav", "b/2.wav", "c/3.wav"]
    with pytest.raises(rl.Refused, match="needs a committed index"):
        rl.resolve_prefix_from_index("p.zip", ["b/"], None)
    with pytest.raises(rl.Refused, match="no indexed member"):
        rl.resolve_prefix_from_index("p.zip", ["d/"], sizes)


def test_size_mismatch_is_none_unless_the_catalog_states_another_size(lib):
    z = lib.packs / ARCHIVE
    assert rl.size_mismatch(z, ARCHIVE, {"archive_bytes": z.stat().st_size}) is None
    assert rl.size_mismatch(z, ARCHIVE, {}) is None, "no recorded size is nothing to disagree with"
    assert f"{ARCHIVE} is {z.stat().st_size} bytes, catalog.json says {z.stat().st_size - 1}" \
        in rl.size_mismatch(z, ARCHIVE, {"archive_bytes": z.stat().st_size - 1})
