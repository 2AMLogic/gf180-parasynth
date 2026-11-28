#!/usr/bin/env python3
"""Tests for `tools/refaudio_local.py`: a request in which one member's
destination is another member's temporary path is REFUSED (#1697).

    .venv/bin/python -m pytest tools/test_refaudio_local.py -q

`extract()` writes each member to `<its path>.part` and renames it into
place. Beside a member named `a/b.part` that temporary path IS another
member's finished file: asked `a/b.part` first, on `origin/main` 69ce038d it
was reported FETCHED, then `a/b` truncated it, renamed it away and was
reported FETCHED too -- `2 fetched, 0 failed`, exit 0, one file on disk.

The names are the input, so every START RED case spells them out. Two of them
are the rule-8 adversaries of the guard itself (`docs/verification-rules.md`):
`a/c.part` is nobody's temporary path and must still be fetched, and `./a/b`
is `a/b` to the filesystem though not to a string compare.

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


@pytest.fixture
def local_lib(tmp_path, monkeypatch) -> LocalLib:
    L = LocalLib(tmp_path)
    monkeypatch.setattr(rl, "REFAUDIO", L.refaudio)
    monkeypatch.setenv("REFAUDIO_LOCAL", str(L.packs))
    return L


def _path(m: str) -> str:
    return f"{PACK[:-4]}/{m}"


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
