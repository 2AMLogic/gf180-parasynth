"""The fetch tool hands audio to measurements, so it gets verified.

Every case here is a way `refaudio_fetch.py` could report a recording as present
when it is not -- a FALSE GREEN -- or blur REFUSED into FAIL. A zero-byte .wav
is the specific hazard: `unzip -p` of a member that does not exist prints
nothing and a naive tool saves that nothing under the right name.

No network and no storage: `ssh` is replaced on PATH by a stub whose behaviour
the test sets, so what is tested is this tool's handling of each remote outcome.
"""
from __future__ import annotations
import json, os, pathlib, stat, sys, textwrap
import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import refaudio_fetch as rf                                      # noqa: E402

ARCHIVE = "pack_a.zip"
MEMBER = "Pack A/WAV/Kick [01] #1!.wav"          # spaces, brackets, '#', '!' -- all real
SIZE = 1234


@pytest.fixture
def lib(tmp_path, monkeypatch):
    """A throwaway refaudio/ with one indexed pack, and a stub ssh first on PATH."""
    ra = tmp_path / "refaudio"
    (ra / "index").mkdir(parents=True)
    (ra / "catalog.json").write_text(json.dumps({"packs": [{"archive": ARCHIVE}, {"archive": "noindex.zip"}]}))
    (ra / "index" / "pack_a.tsv").write_text(f"path\tbytes\tsample_rate\tbits\tchannels\tseconds\n{MEMBER}\t{SIZE}\t44100\t24\t1\t0.1\n")
    monkeypatch.setattr(rf, "REFAUDIO", ra)

    bindir = tmp_path / "bin"
    bindir.mkdir()
    stub = bindir / "ssh"
    stub.write_text(textwrap.dedent(f"""\
        #!{sys.executable}
        import os, sys
        open(os.environ["STUB_LOG"], "a").write(sys.argv[-1] + "\\n")
        n, code = int(os.environ.get("STUB_BYTES", "0")), int(os.environ.get("STUB_EXIT", "0"))
        bad = os.environ.get("STUB_FAIL_MATCH")
        if bad and bad in sys.argv[-1]:
            sys.exit(11)                      # what unzip returns for "no such member"
        sys.stdout.buffer.write(b"R" * n)
        sys.stderr.write(os.environ.get("STUB_ERR", ""))
        sys.exit(code)
        """))
    stub.chmod(stub.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setenv("PATH", f"{bindir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("STUB_LOG", str(tmp_path / "ssh.log"))
    monkeypatch.setenv("REFAUDIO_SSH", "user@storage")
    monkeypatch.setenv("REFAUDIO_ROOT", "/lib")
    return ra


def cached(ra: pathlib.Path) -> list[pathlib.Path]:
    return [p for p in (ra / "cache").rglob("*") if p.is_file()] if (ra / "cache").exists() else []


# --- REFUSED is its own outcome, and attempts nothing ------------------------

def test_no_route_is_refused_not_failed(lib, monkeypatch, tmp_path, capsys):
    monkeypatch.delenv("REFAUDIO_SSH")
    assert rf.main([ARCHIVE, MEMBER]) == rf.REFUSED
    assert "REFUSED" in capsys.readouterr().out
    assert not (tmp_path / "ssh.log").exists(), "REFUSED must not have tried the network"
    assert cached(lib) == []


@pytest.mark.parametrize("archive,member", [
    ("not_in_catalog.zip", MEMBER),
    ("../../etc/passwd", MEMBER),
    (ARCHIVE, "../escape.wav"),
    (ARCHIVE, "/abs/path.wav"),
])
def test_bad_request_is_refused(lib, tmp_path, archive, member):
    assert rf.main([archive, member]) == rf.REFUSED
    assert not (tmp_path / "ssh.log").exists()
    assert cached(lib) == []


# --- a member whose destination is another member's temporary path (#1697) ------
#
# `fetch_one()` writes `<member>.part` and renames it onto `<member>`, so a
# member named `a/b.part` beside `a/b` made the second one's temporary file
# the first one's finished file. Asked `a/b.part` first, that member was
# FETCHED, then truncated, renamed away, and the second was FETCHED too: exit
# 0, `2 fetched, 0 failed`, one file on disk. A false green, so REFUSED.

def _landed(lib: pathlib.Path) -> list[str]:
    return sorted(p.relative_to(lib / "cache" / "noindex").as_posix() for p in cached(lib))


@pytest.mark.parametrize("asked", [["a/b.part", "a/b"], ["a/b", "a/b.part"]],
                         ids=["part-first", "part-last"])
def test_a_member_named_like_anothers_part_file_is_refused(lib, tmp_path, monkeypatch, capsys, asked):
    """START RED (#1697): against 69ce038d both orders exited 0; the first left
    only `a/b` on disk. Refused before any ssh, in either order, naming the pair."""
    monkeypatch.setenv("STUB_BYTES", "8")
    assert rf.main(["noindex.zip", *asked]) == rf.REFUSED
    lines = capsys.readouterr().out.splitlines()
    assert len(lines) == 1 and lines[0].startswith("REFUSED  "), lines
    assert "'a/b.part'" in lines[0] and "'a/b'" in lines[0], lines[0]
    assert not (tmp_path / "ssh.log").exists(), "REFUSED must not have tried the network"
    assert cached(lib) == []


def test_members_that_merely_end_in_part_are_fetched(lib, monkeypatch, capsys):
    """The guard's over-refusal input (rule 8): `a/c.part` is nobody's
    temporary path. Refusing by suffix alone would fail this."""
    monkeypatch.setenv("STUB_BYTES", "8")
    assert rf.main(["noindex.zip", "a/b", "a/c.part"]) == rf.FETCHED
    assert capsys.readouterr().out.splitlines()[-1] == "2 fetched, 0 failed"
    assert _landed(lib) == ["a/b", "a/c.part"]


# --- the false-green cases ---------------------------------------------------

def test_remote_exit_zero_with_no_bytes_is_a_fail_and_leaves_no_file(lib, monkeypatch):
    """The zero-byte .wav. Exit status alone would call this a success.

    Run against the pack with NO index, deliberately. On an indexed member the
    size check also rejects an empty file, so the first version of this test
    passed with the zero-byte guard deleted -- the injected-defect control
    caught it. Most packs have no committed index, so this guard is the only
    one they get.
    """
    monkeypatch.setenv("STUB_BYTES", "0")
    assert rf.main(["noindex.zip", "x/y.wav"]) == rf.FAIL
    assert cached(lib) == []


def test_remote_nonzero_exit_is_a_fail_even_with_bytes(lib, monkeypatch):
    monkeypatch.setenv("STUB_BYTES", str(SIZE))
    monkeypatch.setenv("STUB_EXIT", "11")
    assert rf.main([ARCHIVE, MEMBER]) == rf.FAIL
    assert cached(lib) == [], "a partial or suspect file must not survive"


def test_size_disagreeing_with_the_index_is_a_fail(lib, monkeypatch):
    """The file on disk must be the file the index describes."""
    monkeypatch.setenv("STUB_BYTES", str(SIZE - 1))
    assert rf.main([ARCHIVE, MEMBER]) == rf.FAIL
    assert cached(lib) == []


def test_one_bad_member_fails_the_run_but_keeps_the_good_one(lib, monkeypatch):
    """A run is only FETCHED if every member is. One good file must not launder a bad one."""
    monkeypatch.setenv("STUB_BYTES", str(SIZE))
    monkeypatch.setenv("STUB_FAIL_MATCH", "missing")
    assert rf.main([ARCHIVE, MEMBER, "Pack A/WAV/missing.wav"]) == rf.FAIL
    assert [f.name for f in cached(lib)] == ["Kick [01] #1!.wav"]


def test_member_outside_the_index_is_fetched_but_says_size_is_unchecked(lib, monkeypatch, capsys):
    monkeypatch.setenv("STUB_BYTES", str(SIZE))
    assert rf.main([ARCHIVE, "Pack A/Kontakt/other.wav"]) == rf.FETCHED
    assert "size will NOT be checked" in capsys.readouterr().out, "an unchecked size must be said out loud"


# --- and the control: it can pass --------------------------------------------

def test_good_fetch_lands_whole_with_the_right_size(lib, monkeypatch, tmp_path):
    monkeypatch.setenv("STUB_BYTES", str(SIZE))
    assert rf.main([ARCHIVE, MEMBER]) == rf.FETCHED
    (f,) = cached(lib)
    assert f.stat().st_size == SIZE and f.name == "Kick [01] #1!.wav"
    assert not list((lib / "cache").rglob("*.part"))
    # unzip would treat [01] as a character class; the remote command must escape it
    assert "\\[01]" in (tmp_path / "ssh.log").read_text()


def test_pack_without_an_index_says_size_is_unchecked(lib, monkeypatch, capsys):
    monkeypatch.setenv("STUB_BYTES", "10")
    assert rf.main(["noindex.zip", "x/y.wav"]) == rf.FETCHED
    assert "sizes will NOT be checked" in capsys.readouterr().out


# --- a member that cannot be landed (#1684) ------------------------------------
#
# Not a false green: the process exited 1. The shape was wrong. `fetch_one()`
# promises "the reason it failed", and an `OSError` from its `mkdir`, its
# `part.open` or its `replace` escaped `main()` instead, so the tally never
# printed.

@pytest.mark.parametrize(("asked", "stuck", "says"), [
    (["a", "a/b.wav"], "a/b.wav", "FileExistsError"),
    (["a/b.wav", "a"], "a", "IsADirectoryError"),
], ids=["mkdir-onto-a-file", "rename-onto-a-directory"])
def test_a_member_that_cannot_land_is_its_own_fail_and_the_run_finishes(
        lib, monkeypatch, capsys, asked, stuck, says):
    """START RED (#1684): against a3186508 both orders escaped `main()` as the
    exception in `says`. The stub answers every member, so what stands in the
    way is the name alone: `a` on disk is a file when `a/b.wav` wants it as a
    directory, and a directory when `a` wants it as a file."""
    monkeypatch.setenv("STUB_BYTES", "8")
    assert rf.main(["noindex.zip", *asked]) == rf.FAIL
    lines = capsys.readouterr().out.splitlines()
    i = lines.index(f"FAIL     {stuck}")
    assert says in lines[i + 1], lines
    assert lines[-1] == "1 fetched, 1 failed"
    good = [m for m in asked if m != stuck]
    assert [ln.split("  (")[0] for ln in lines if ln.startswith("FETCHED")] == \
        [f"FETCHED  refaudio/cache/noindex/{m}" for m in good]
    assert sorted(p.relative_to(lib / "cache" / "noindex").as_posix() for p in cached(lib)) == sorted(good)
    assert {p.stat().st_size for p in cached(lib)} == {8}


@pytest.mark.skipif(hasattr(os, "geteuid") and os.geteuid() == 0,
                    reason="root writes into a read-only directory, so nothing refuses")
def test_a_read_only_cache_directory_is_the_members_fail_at_part_open(lib, tmp_path, monkeypatch, capsys):
    """START RED (#1684): against a3186508 this escaped `main()` as
    `PermissionError`. `mkdir` succeeds because the directory is there; it is
    `part.open` that is refused, before any ssh, and nothing is created."""
    monkeypatch.setenv("STUB_BYTES", "8")
    pack = lib / "cache" / "noindex"
    pack.mkdir(parents=True)
    pack.chmod(0o500)
    try:
        assert rf.main(["noindex.zip", "x.wav"]) == rf.FAIL
    finally:
        pack.chmod(0o700)
    lines = capsys.readouterr().out.splitlines()
    assert "PermissionError" in lines[lines.index("FAIL     x.wav") + 1]
    assert lines[-1] == "0 fetched, 1 failed"
    assert cached(lib) == []
    assert not (tmp_path / "ssh.log").exists()


# --- the index the sizes are checked against ----------------------------------

@pytest.mark.parametrize(("row", "field"), [
    ("Pack A/kick.wav", "bytes"),
    ("Pack A/kick.wav\t", "bytes"),
    ("\t12", "path"),
])
def test_an_incomplete_index_row_is_an_error_naming_the_field_not_a_size(lib, row, field):
    """A row with no `path` or no `bytes` is not an index entry. This tool used
    to carry its own reader of `refaudio/index/*.tsv` and read the empty path
    as `{'': 12}`: with that row in the index `main()` still printed FETCHED
    and exited 0. The other two rows died inside `int()` with a message that
    named no field (#1551). The sizes are now read by `tools/refaudio_local.py`'s
    one reader, which names the line and the field."""
    assert rf.indexed_sizes(ARCHIVE) == {MEMBER: SIZE}, "the complete index must still read"
    (lib / "index" / "pack_a.tsv").write_text(f"path\tbytes\n{row}\n", encoding="utf-8")
    with pytest.raises(ValueError, match=f"missing {field}"):
        rf.indexed_sizes(ARCHIVE)


# ---- a catalog missing the key known_archives() reads with [] is REFUSED
# (exit 2), not a traceback (#1604, #1623), through the tool's own main().
def test_the_committed_catalog_lists_packs():
    """The guard must be satisfiable against the catalog that ships."""
    assert "808-from-mars.zip" in rf.known_archives()


def test_a_catalog_without_packs_is_refused(lib, capsys):
    (lib / "catalog.json").write_text("{}")
    assert rf.main([ARCHIVE, MEMBER]) == rf.REFUSED
    out = capsys.readouterr().out
    assert "REFUSED" in out and "'packs'" in out


def test_a_catalog_with_null_packs_is_refused(lib, capsys):
    (lib / "catalog.json").write_text(json.dumps({"packs": None}))
    assert rf.main([ARCHIVE, MEMBER]) == rf.REFUSED
    out = capsys.readouterr().out
    assert "REFUSED" in out and "'packs'" in out


@pytest.mark.parametrize("catalog, why", [([], "not a JSON object"), (None, "not a JSON object")])
def test_a_catalog_that_is_not_an_object_is_refused(lib, capsys, catalog, why):
    (lib / "catalog.json").write_text(json.dumps(catalog))
    assert rf.main([ARCHIVE, MEMBER]) == rf.REFUSED
    out = capsys.readouterr().out
    assert "REFUSED" in out and why in out
