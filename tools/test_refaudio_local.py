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

Also on this module: a member that cannot be landed is one member's FAIL, not
the run's traceback (#1684), and a request in which one member's destination is
another member's temporary path is REFUSED (#1697).

`extract()` promises "the reason it failed". An archive holding a FILE at a
name that is also the directory of another member (`a` and `a/b.wav`) broke
that promise on `origin/main` a3186508: `dest.parent.mkdir(parents=True,
exist_ok=True)` raised, the loop died mid-way, the `N fetched, M failed` tally
never printed, and the members already extracted sat in the cache with no line
saying so.

The names are the input, so every START RED case spells them out: what the
archive holds, in what order it is requested, which member cannot land, what
the filesystem says about it, and what must be on disk afterwards. That last
column is the rule-8 adversary (`docs/verification-rules.md`): a handler that
returns a reason but leaves a `.part` behind, or dies in its own cleanup,
satisfies the check and violates its intent.

#1697: `extract()` writes each member to `<its path>.part` and renames it into
place. Beside a member named `a/b.part` that temporary path IS another
member's finished file: asked `a/b.part` first, on `origin/main` 69ce038d it
was reported FETCHED, then `a/b` truncated it, renamed it away and was
reported FETCHED too -- `2 fetched, 0 failed`, exit 0, one file on disk.
Two of those cases are the rule-8 adversaries of the guard itself:
`a/c.part` is nobody's temporary path and must still be fetched, and `./a/b`
is `a/b` to the filesystem though not to a string compare.

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
import shutil
import subprocess
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



# --- a member whose destination is another member's temporary path (#1697) ------
# Merged from the standalone suite that landed on main with the same file name:
# it carries its own throwaway-library fixture (`local_lib`), kept as it was.

PACK = "stacked_names.zip"
A, AB, ABC = b"a", b"ab", b"abc"
FILE_AND_DIRECTORY = [("a", A), ("a/b.wav", AB)]

# (what the archive holds, what is requested in that order, the member that cannot
#  land, what the filesystem says about it, the files that must be on disk after)
CASES = [
    pytest.param(FILE_AND_DIRECTORY, ["a", "a/b.wav"], "a/b.wav", "FileExistsError", {"a": A},
                 id="mkdir-onto-a-file"),
    pytest.param(FILE_AND_DIRECTORY, ["a/b.wav", "a"], "a", "IsADirectoryError", {"a/b.wav": AB},
                 id="rename-onto-a-directory"),
    pytest.param([("a", A), ("a/b/c.wav", ABC)], ["a", "a/b/c.wav"], "a/b/c.wav", "NotADirectoryError",
                 {"a": A}, id="mkdir-under-a-file"),
    pytest.param([("a/b.wav.part/x.wav", b"x"), ("a/b.wav", AB)],
                 ["a/b.wav.part/x.wav", "a/b.wav"], "a/b.wav", "IsADirectoryError",
                 {"a/b.wav.part/x.wav": b"x"}, id="directory-where-the-part-goes"),
]

B, B_PART = b"b", b"b.part"
PART_PAIR = [("a/b", B), ("a/b.part", B_PART)]



def _stacked_zip(*members: tuple[str, bytes]) -> bytes:
    b = io.BytesIO()
    with zipfile.ZipFile(b, "w") as z:
        for name, payload in members:
            z.writestr(name, payload)
    return b.getvalue()


class LocalLib:
    """A throwaway refaudio/ whose catalog vouches for exactly one zip, held in
    the directory REFAUDIO_LOCAL points at."""

    def __init__(self, root: pathlib.Path):
        self.refaudio, self.packs = root / "refaudio", root / "packs"
        self.refaudio.mkdir()
        self.packs.mkdir()

    def serve(self, *members: tuple[str, bytes]) -> None:
        blob = _stacked_zip(*members)
        (self.packs / PACK).write_bytes(blob)
        (self.refaudio / "catalog.json").write_text(json.dumps({"packs": [
            {"archive": PACK, "archive_bytes": len(blob),
             "archive_sha256": hashlib.sha256(blob).hexdigest()}]}), encoding="utf-8")

    @property
    def cache(self) -> pathlib.Path:
        return self.refaudio / "cache"

    def files(self) -> dict[str, int]:
        """path under refaudio/cache/ -> size, for every file there, `.part` included."""
        return {p.relative_to(self.cache).as_posix(): p.stat().st_size
                for p in self.cache.rglob("*") if p.is_file()} if self.cache.exists() else {}

    def dirs(self) -> set[str]:
        return {p.relative_to(self.cache).as_posix()
                for p in self.cache.rglob("*") if p.is_dir()} if self.cache.exists() else set()


@pytest.fixture
def local_lib(tmp_path, monkeypatch) -> LocalLib:
    L = LocalLib(tmp_path)
    monkeypatch.setattr(rl, "REFAUDIO", L.refaudio)
    monkeypatch.setenv("REFAUDIO_LOCAL", str(L.packs))
    return L


def _path(m: str) -> str:
    return f"{PACK[:-4]}/{m}"


def _dirs_of(landed: dict[str, bytes]) -> set[str]:
    """Every directory the landed members need under refaudio/cache/, and no more."""
    return {str(p) for m in landed for p in pathlib.PurePosixPath(_path(m)).parents if str(p) != "."}


# --- the control: this route can pass -----------------------------------------

def test_members_that_do_not_collide_are_all_fetched(local_lib, capsys):
    """Nothing in its way: the same zip, catalog and route land every member, so
    the FAILs below are the names and nothing else."""
    local_lib.serve(("a/b.wav", AB), ("c.wav", b"c"))
    assert rl.main([PACK, "a/b.wav", "c.wav"]) == rl.FETCHED
    assert capsys.readouterr().out.splitlines()[-1] == "2 fetched, 0 failed"
    assert local_lib.files() == {_path("a/b.wav"): len(AB), _path("c.wav"): 1}
    assert local_lib.dirs() == _dirs_of({"a/b.wav": AB, "c.wav": b"c"})


# --- a member that cannot be landed ---------------------------------------------

@pytest.mark.parametrize(("members", "asked", "stuck", "says", "lands"), CASES)
def test_a_member_that_cannot_land_is_its_own_fail_and_the_run_finishes(
        local_lib, capsys, members, asked, stuck, says, lands):
    """START RED (#1684): against a3186508 each case escaped `main()` as the
    exception named in `says`, so no tally printed. The run must instead end
    with that member's FAIL, the others FETCHED, and only them on disk."""
    local_lib.serve(*members)
    assert rl.main([PACK, *asked]) == rl.FAIL
    lines = capsys.readouterr().out.splitlines()
    i = lines.index(f"FAIL     {stuck}")
    assert says in lines[i + 1], lines
    assert lines[-1] == f"{len(asked) - 1} fetched, 1 failed"
    assert [ln.split("  (")[0] for ln in lines if ln.startswith("FETCHED")] == \
        [f"FETCHED  refaudio/cache/{_path(m)}" for m in asked if m != stuck]
    assert local_lib.files() == {_path(m): len(data) for m, data in lands.items()}
    assert local_lib.dirs() == _dirs_of(lands)


@pytest.mark.skipif(hasattr(os, "geteuid") and os.geteuid() == 0,
                    reason="root writes into a read-only directory, so nothing refuses")
def test_a_read_only_cache_directory_is_the_members_fail_and_creates_nothing(local_lib, capsys):
    """START RED (#1684): against a3186508 this escaped `main()` as
    `PermissionError`. No permission to write is the commonest OSError of all:
    the `.part` is never created, so nothing is left and nothing needs removing."""
    local_lib.serve(("x.wav", b"x"))
    pack = local_lib.cache / PACK[:-4]
    pack.mkdir(parents=True)
    pack.chmod(0o500)
    try:
        assert rl.main([PACK, "x.wav"]) == rl.FAIL
    finally:
        pack.chmod(0o700)
    lines = capsys.readouterr().out.splitlines()
    assert "PermissionError" in lines[lines.index("FAIL     x.wav") + 1]
    assert lines[-1] == "0 fetched, 1 failed"
    assert local_lib.files() == {}


def test_extract_returns_the_reason_and_raises_nothing(local_lib):
    """The contract at the function: a reason naming what the filesystem said, or None."""
    local_lib.serve(*FILE_AND_DIRECTORY)
    with zipfile.ZipFile(local_lib.packs / PACK) as zf:
        assert rl.extract(zf, PACK, "a", None, local_lib.cache) is None
        why = rl.extract(zf, PACK, "a/b.wav", None, local_lib.cache)
    assert why is not None and "FileExistsError" in why
    assert local_lib.files() == {_path("a"): len(A)}


# --- the CLIs as processes: the exit code and the stderr a caller meets --------

CLI_RUNS = [
    pytest.param("refaudio_local.py", [PACK, "a", "a/b.wav"], {}, id="local"),
    pytest.param("refaudio_fetch.py", [PACK, "a", "a/b.wav"],
                 {"REFAUDIO_SSH": "user@storage", "REFAUDIO_ROOT": "/lib"}, id="fetch"),
    pytest.param("refaudio_s3.py", ["--prefix", PACK, "a"], {"REFAUDIO_S3": "s3://bucket/prefix"},
                 id="s3-prefix"),
]


@pytest.mark.parametrize(("tool", "argv", "env"), CLI_RUNS)
def test_the_cli_exits_1_with_the_fail_line_a_tally_and_no_traceback(
        local_lib, tmp_path, monkeypatch, tool, argv, env):
    """`sys.exit(main(sys.argv[1:]))` is what a caller meets. START RED (#1684):
    against a3186508 each of the three routes ended in a traceback on stderr and
    printed no tally. The tools are copied beside a catalog that vouches for the
    zip, and `ssh` and `aws` are stubs first on PATH, so no network is touched."""
    local_lib.serve(*FILE_AND_DIRECTORY)
    cli = tmp_path / "cli"
    (cli / "tools").mkdir(parents=True)
    here = pathlib.Path(__file__).resolve().parent
    for name in ("refusal.py", "refaudio_local.py", "refaudio_fetch.py", "refaudio_s3.py"):
        shutil.copy(here / name, cli / "tools" / name)
    (cli / "refaudio").mkdir()
    shutil.copy(local_lib.refaudio / "catalog.json", cli / "refaudio" / "catalog.json")
    (tmp_path / "ssh").write_text(f"#!{sys.executable}\nimport sys\nsys.stdout.buffer.write(b'R' * 8)\n",
                                  encoding="utf-8")
    (tmp_path / "aws").write_text(
        f"#!{sys.executable}\nimport shutil, sys\nshutil.copy({str(local_lib.packs / PACK)!r}, sys.argv[-2])\n",
        encoding="utf-8")
    for stub in ("ssh", "aws"):
        (tmp_path / stub).chmod(0o755)
    for k in ("REFAUDIO_LOCAL", "REFAUDIO_SSH", "REFAUDIO_ROOT", "REFAUDIO_S3", "REFAUDIO_S3_PROFILE"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("PATH", f"{tmp_path}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("REFAUDIO_LOCAL", str(local_lib.packs))
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    p = subprocess.run([sys.executable, str(cli / "tools" / tool), *argv], cwd=cli,
                       capture_output=True, text=True, timeout=120)
    assert p.returncode == rl.FAIL, p.stderr
    assert p.stderr == "", p.stderr
    lines = p.stdout.splitlines()
    assert "FileExistsError" in lines[lines.index("FAIL     a/b.wav") + 1]
    assert lines[-1] == "1 fetched, 1 failed"
    cache = cli / "refaudio" / "cache"
    assert sorted(q.relative_to(cache).as_posix() for q in cache.rglob("*") if q.is_file()) == [_path("a")]


# --- a member whose destination is another member's temporary path (#1697) ------

@pytest.mark.parametrize("asked", [["a/b.part", "a/b"], ["a/b", "a/b.part"]],
                         ids=["part-first", "part-last"])
def test_a_member_named_like_anothers_part_file_is_refused(local_lib, capsys, asked):
    """START RED (#1697): against 69ce038d `part-first` printed FETCHED for
    `a/b.part`, then `a/b` truncated that file, renamed it away and was
    FETCHED too: `2 fetched, 0 failed`, exit 0, `a/b` the only file on disk.
    `part-last` landed both and exited 0, so the outcome turned on the order.
    Both are REFUSED before anything is written, naming the pair."""
    local_lib.serve(*PART_PAIR)
    assert rl.main([PACK, *asked]) == rl.REFUSED
    lines = capsys.readouterr().out.splitlines()
    assert len(lines) == 1 and lines[0].startswith("REFUSED  "), lines
    assert "'a/b.part'" in lines[0] and "'a/b'" in lines[0], lines[0]
    assert local_lib.files() == {}


def test_one_pair_among_three_refuses_the_whole_request(local_lib, capsys):
    """Nothing is written, not even the member that is not in the pair."""
    local_lib.serve(("x/y", b"xy"), *PART_PAIR)
    assert rl.main([PACK, "x/y", "a/b", "a/b.part"]) == rl.REFUSED
    assert "'a/b.part'" in capsys.readouterr().out
    assert local_lib.files() == {}


def test_prefix_whose_members_are_the_pair_is_refused(local_lib, capsys):
    """`--prefix` takes its members from the committed index, sorted, so it can
    only ever ask the winning order. The set is refused all the same, as a
    typed request for it is."""
    local_lib.serve(*PART_PAIR)
    (local_lib.refaudio / "index").mkdir()
    (local_lib.refaudio / "index" / f"{PACK[:-4]}.tsv").write_text(
        f"path\tbytes\na/b\t{len(B)}\na/b.part\t{len(B_PART)}\n", encoding="utf-8")
    assert rl.main(["--prefix", PACK, "a/"]) == rl.REFUSED
    assert "'a/b.part'" in capsys.readouterr().out
    assert local_lib.files() == {}


@pytest.mark.parametrize("spelling", ["./a/b", "a//b", "a/b/", "a/b/."],
                         ids=["dot-slash", "double-slash", "trailing-slash", "trailing-dot"])
def test_the_same_path_spelled_another_way_is_still_the_pair(local_lib, capsys, spelling):
    """Rule 8's input for a STRING compare: `'./a/b' + '.part'` is not
    `'a/b.part'`, but `extract()` builds the temporary path with `pathlib`,
    which spells it `a/b.part` all the same -- and `zipfile` keeps a member
    under exactly the name it was given, so an archive can hold this. Without
    the guard `a/b.part` is FETCHED and then truncated. Refused as the pair is."""
    local_lib.serve(("a/b.part", B_PART), (spelling, B))
    assert rl.main([PACK, "a/b.part", spelling]) == rl.REFUSED
    out = capsys.readouterr().out
    assert out.startswith("REFUSED  ") and "'a/b.part'" in out and repr(spelling) in out, out
    assert local_lib.files() == {}


# --- and the controls: these must still pass ------------------------------------

def test_members_that_merely_end_in_part_are_fetched(local_lib, capsys):
    """The guard's over-refusal input (rule 8): `a/c.part` ends in `.part` and
    is nobody's temporary path. A guard that refused by suffix alone fails
    this, and `test_refaudio_fetch.py` / `test_refaudio_s3.py` carry it too."""
    local_lib.serve(("a/b", B), ("a/c.part", B_PART))
    assert rl.main([PACK, "a/b", "a/c.part"]) == rl.FETCHED
    assert capsys.readouterr().out.splitlines()[-1] == "2 fetched, 0 failed"
    assert local_lib.files() == {_path("a/b"): len(B), _path("a/c.part"): len(B_PART)}


def test_the_same_member_asked_twice_is_not_the_pair(local_lib, capsys):
    """`["a/b", "a/b"]` holds no `.part` name; it must not read as one."""
    local_lib.serve(*PART_PAIR)
    assert rl.main([PACK, "a/b", "a/b"]) == rl.FETCHED
    assert capsys.readouterr().out.splitlines()[-1] == "2 fetched, 0 failed"
    assert local_lib.files() == {_path("a/b"): len(B)}
