#!/usr/bin/env python3
"""Tests for `tools/refaudio_local.py`: a member that cannot be landed is one
member's FAIL, not the run's traceback (#1684), and a request in which one
member's destination is another member's temporary path is REFUSED (#1697).

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

#1697: `extract()` writes each member to `<its path>.part` and renames it into
place. Beside a member named `a/b.part` that temporary path IS another
member's finished file: asked `a/b.part` first, on `origin/main` 69ce038d it
was reported FETCHED, then `a/b` truncated it, renamed it away and was
reported FETCHED too -- `2 fetched, 0 failed`, exit 0, one file on disk.
Two of those cases are the rule-8 adversaries of the guard itself:
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
import shutil
import subprocess
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
