#!/usr/bin/env python3
"""Tests for `tools/refaudio_local.py`: a member that cannot be landed is one
member's FAIL, not the run's traceback (#1684).

    .venv/bin/python -m pytest tools/test_refaudio_local.py -q

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

No licensed audio: a zip built in `tmp_path` whose members are a few bytes.
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import pathlib
import sys
import zipfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import refaudio_local as rl                                      # noqa: E402

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
