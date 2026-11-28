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


# --- START RED (#1602): an index that cannot be read is REFUSED, before ssh ----
# The three outcomes are kept apart on purpose: a malformed index is a precondition
# not met, so it is exit 2 and no network. It used to escape `main()` as a
# ValueError / OSError traceback -- exit 1, which every caller reads as FAIL.

@pytest.mark.parametrize("tsv", [
    pytest.param(f"path\tbytes\n{MEMBER}\t\n", id="row-with-no-bytes"),
    pytest.param(f"path\tbytes\n{'x' * 200000}\t12\n", id="row-over-the-csv-field-limit"),
])
def test_a_malformed_index_is_REFUSED_and_ssh_is_never_tried(lib, tmp_path, capsys, tsv):
    (lib / "index" / "pack_a.tsv").write_text(tsv, encoding="utf-8")
    assert rf.main([ARCHIVE, MEMBER]) == rf.REFUSED
    assert "REFUSED" in capsys.readouterr().out
    assert not (tmp_path / "ssh.log").exists()
    assert cached(lib) == []


def test_an_index_that_cannot_be_opened_is_REFUSED_and_ssh_is_never_tried(lib, tmp_path):
    tsv = lib / "index" / "pack_a.tsv"
    tsv.unlink()
    tsv.mkdir()                                  # IsADirectoryError, whoever runs the test
    assert rf.main([ARCHIVE, MEMBER]) == rf.REFUSED
    assert not (tmp_path / "ssh.log").exists()
    assert cached(lib) == []


def test_the_member_path_check_comes_from_the_shared_helper(lib, monkeypatch):
    """One path check for the three routes (`rl.validate_members`), not a copy in each."""
    seen = []
    monkeypatch.setattr(rf.rl, "validate_members", lambda ms: seen.append(ms))
    monkeypatch.setenv("STUB_BYTES", str(SIZE))
    assert rf.main([ARCHIVE, MEMBER]) == rf.FETCHED
    assert seen == [[MEMBER]]
