#!/usr/bin/env python3
"""Tests for `tools/refaudio_s3.py`: FETCHED / FAIL / REFUSED, with `aws` faked.

    .venv/bin/python -m pytest tools/test_refaudio_s3.py -q

A fake `aws` writes a small zip, so what is exercised is the real hash check,
extraction and cleanup, with no network and no licensed audio.

`tools/refaudio_member_list.py` (#1427) shares that download and hash check
and then lists a folder instead of extracting it, so its tests live here on
the same fixtures: what it lists, what it refuses, and the round trip from the
file it writes to the reader that consumes it.
"""
from __future__ import annotations

import hashlib
import io
import pathlib
import subprocess
import sys
import zipfile

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import refaudio_s3 as s3                                           # noqa: E402
import refaudio_member_list as ml                                 # noqa: E402

ARCH = "808_loops_from_mars.zip"
INDEXED = "808-from-mars.zip"                     # the real catalog indexes this one
MEMBER = "808 Loops From Mars/WAV/03. Bass Drum/4x4/x.wav"
FOLDER = "808 Loops From Mars/WAV/03. Bass Drum/4x4"
# A benign prefix's member whose name climbs four directories: out of the pack's
# cache directory, out of refaudio/cache/, and into the cache's parent.
ESCAPE = "808 Loops From Mars/WAV/../../../../escaped.wav"


def _zip(payload: bytes = b"RIFFfake") -> bytes:
    b = io.BytesIO()
    with zipfile.ZipFile(b, "w") as z:
        z.writestr(MEMBER, payload)
        z.writestr("__MACOSX/" + MEMBER, b"junk")
    return b.getvalue()


@pytest.fixture
def env(monkeypatch, tmp_path):
    monkeypatch.setenv("REFAUDIO_S3", "s3://bucket/prefix")
    monkeypatch.setattr(s3, "CACHE", tmp_path / "cache")
    monkeypatch.setattr(s3, "ARCHIVES", tmp_path / "cache" / "_archives")
    monkeypatch.setattr(s3.shutil, "which", lambda n: "/usr/bin/aws")
    return tmp_path


def _serve_aws(monkeypatch, data: bytes, rc: int = 0) -> list:
    """The fake `aws` on its own: every `s3 cp` writes `data`. Returns the calls."""
    calls = []

    def run(cmd, **kw):
        calls.append(cmd)
        if rc == 0:
            pathlib.Path(cmd[-2] if cmd[-1] == "--only-show-errors" else cmd[-1]).write_bytes(data)
        return subprocess.CompletedProcess(cmd, rc, b"", b"AccessDenied" if rc else b"")
    monkeypatch.setattr(s3.subprocess, "run", run)
    return calls


def _fake_aws(monkeypatch, data: bytes, rc: int = 0, want_sha: str | None = None, **override):
    """`_serve_aws`, with the real catalog's record for the archive re-vouched for
    `data`: its hash AND its size, because `download()` checks `archive_bytes`
    before hashing, as the local route always did (#1602). `override` edits the
    record after that, to state something the download must disagree with."""
    sha = want_sha or hashlib.sha256(data).hexdigest()
    real = s3.rl.pack_record
    monkeypatch.setattr(s3.rl, "pack_record", lambda a, **kw: {
        **real(a, **kw), "archive_sha256": sha, "archive_bytes": len(data), **override})
    return _serve_aws(monkeypatch, data, rc)


def _escaped(tmp_path) -> list[pathlib.Path]:
    return sorted(tmp_path.rglob("escaped.wav"))


def _landed() -> list[pathlib.Path]:
    """Every file under the cache: what a run actually wrote for the user. The
    empty `_archives/` the download step creates is not a file."""
    return sorted(p for p in s3.CACHE.rglob("*") if p.is_file()) if s3.CACHE.exists() else []


def test_no_env_is_refused(monkeypatch, capsys):
    monkeypatch.delenv("REFAUDIO_S3", raising=False)
    assert s3.main([ARCH, MEMBER]) == s3.REFUSED
    assert "REFAUDIO_S3 is not set" in capsys.readouterr().out


def test_non_s3_url_is_refused(monkeypatch):
    monkeypatch.setenv("REFAUDIO_S3", "https://example.com/x")
    assert s3.main([ARCH, MEMBER]) == s3.REFUSED


def test_unknown_archive_and_path_traversal_are_refused(env):
    assert s3.main(["nope.zip", MEMBER]) == s3.REFUSED
    assert s3.main([ARCH, "../etc/passwd"]) == s3.REFUSED
    assert s3.main([ARCH, "/abs.wav"]) == s3.REFUSED


# --- #1697: a member whose destination is another member's temporary path -------
#
# This route extracts through `refaudio_local.extract()`, so it has the same
# `.part` collision: a member named `a/b.part` beside `a/b` is FETCHED, then
# truncated and renamed away, and the run says `2 fetched, 0 failed`. Typed
# members are checked before the download. Under `--prefix` the typed arguments
# are folder prefixes, so only the expanded set is the member list, and that is
# checked as soon as it is known.

B, B_PART = b"b", b"b.part"


def _named_zip(*members: tuple[str, bytes]) -> bytes:
    b = io.BytesIO()
    with zipfile.ZipFile(b, "w") as z:
        for name, payload in members:
            z.writestr(name, payload)
    return b.getvalue()


@pytest.mark.parametrize("asked", [["a/b.part", "a/b"], ["a/b", "a/b.part"]],
                         ids=["part-first", "part-last"])
def test_a_member_named_like_anothers_part_file_is_refused(env, monkeypatch, capsys, asked):
    """START RED (#1697): against 69ce038d both orders exited 0; the first left
    only `a/b` on disk. Refused before the archive is fetched, in either order."""
    calls = _fake_aws(monkeypatch, _named_zip(("a/b", B), ("a/b.part", B_PART)))
    assert s3.main([ARCH, *asked]) == s3.REFUSED
    lines = capsys.readouterr().out.splitlines()
    assert len(lines) == 1 and lines[0].startswith("REFUSED  "), lines
    assert "'a/b.part'" in lines[0] and "'a/b'" in lines[0], lines[0]
    assert calls == [], "REFUSED must not have fetched the archive"
    assert not s3.CACHE.exists()


def test_prefix_whose_members_are_the_pair_is_refused(env, monkeypatch, capsys):
    """`--prefix` spells its members from the archive itself, sorted, so it can
    only ask the winning order. The set is refused all the same, as it is on the
    local route, nothing is extracted and the archive is not left behind."""
    assert s3.rl.indexed_sizes(ARCH) is None, \
        "ARCH gained a committed index: this case would now read it, not the zip"
    _fake_aws(monkeypatch, _named_zip(("a/b", B), ("a/b.part", B_PART)))
    assert s3.main(["--prefix", ARCH, "a/"]) == s3.REFUSED
    lines = capsys.readouterr().out.splitlines()
    assert len(lines) == 1 and lines[0].startswith("REFUSED  "), lines
    assert "'a/b.part'" in lines[0] and "'a/b'" in lines[0], lines[0]
    assert not (s3.CACHE / ARCH[:-4]).exists()
    assert not (s3.ARCHIVES / ARCH).exists()


def test_members_that_merely_end_in_part_are_fetched(env, monkeypatch):
    """The guard's over-refusal input (rule 8): `a/c.part` is nobody's
    temporary path. Refusing by suffix alone would fail this."""
    _fake_aws(monkeypatch, _named_zip(("a/b", B), ("a/c.part", B_PART)))
    assert s3.main([ARCH, "a/b", "a/c.part"]) == s3.FETCHED
    got = s3.CACHE / ARCH[:-4]
    assert (got / "a/b").read_bytes() == B and (got / "a/c.part").read_bytes() == B_PART


def test_missing_aws_cli_is_refused(env, monkeypatch):
    monkeypatch.setattr(s3.shutil, "which", lambda n: None)
    assert s3.main([ARCH, MEMBER]) == s3.REFUSED


def test_fetched_and_archive_deleted(env, monkeypatch):
    _fake_aws(monkeypatch, _zip())
    assert s3.main([ARCH, MEMBER]) == s3.FETCHED
    got = s3.CACHE / ARCH[:-4] / MEMBER
    assert got.read_bytes() == b"RIFFfake"
    assert not (s3.ARCHIVES / ARCH).exists()


def test_keep_archive(env, monkeypatch):
    _fake_aws(monkeypatch, _zip())
    assert s3.main(["--keep-archive", ARCH, MEMBER]) == s3.FETCHED
    assert (s3.ARCHIVES / ARCH).exists()


def test_hash_mismatch_is_refused_and_nothing_extracted(env, monkeypatch, capsys):
    """START RED: a zip that is not the catalogued one must never be used."""
    _fake_aws(monkeypatch, _zip(), want_sha="0" * 64)
    assert s3.main([ARCH, MEMBER]) == s3.REFUSED
    assert "not the archive the catalogue describes" in capsys.readouterr().out
    assert not (s3.ARCHIVES / ARCH).exists()
    assert not (s3.CACHE / ARCH[:-4]).exists()


def test_aws_failure_is_FAIL_not_refused(env, monkeypatch, capsys):
    _fake_aws(monkeypatch, b"", rc=1)
    assert s3.main([ARCH, MEMBER]) == s3.FAIL
    assert "AccessDenied" in capsys.readouterr().out


def test_missing_member_is_FAIL(env, monkeypatch):
    _fake_aws(monkeypatch, _zip())
    assert s3.main([ARCH, "808 Loops From Mars/absent.wav"]) == s3.FAIL


# --- #1684: a member that cannot be landed -------------------------------------
#
# `--prefix` on an unindexed pack is the reachable route: the member names come
# from the archive's own directory, so no committed index screens them.

FILE_MEMBER = "808 Loops From Mars/WAV/a"
NESTED_MEMBER = FILE_MEMBER + "/b.wav"


def _stacked_zip() -> bytes:
    """A FILE member whose name is also the directory of another member."""
    b = io.BytesIO()
    with zipfile.ZipFile(b, "w") as z:
        z.writestr(FILE_MEMBER, b"a")
        z.writestr(NESTED_MEMBER, b"ab")
    return b.getvalue()


@pytest.mark.parametrize(("argv", "stuck", "says"), [
    (["--prefix", ARCH, "808 Loops From Mars/WAV/"], NESTED_MEMBER, "FileExistsError"),
    ([ARCH, NESTED_MEMBER, FILE_MEMBER], FILE_MEMBER, "IsADirectoryError"),
], ids=["prefix-mkdir-onto-a-file", "typed-rename-onto-a-directory"])
def test_a_member_that_cannot_land_is_its_own_fail_and_the_run_finishes(
        env, monkeypatch, capsys, argv, stuck, says):
    """START RED (#1684): against a3186508 both orders escaped `main()` as the
    exception in `says`, through this route's own loop, so the tally never
    printed."""
    monkeypatch.setattr(s3.rl, "indexed_sizes", lambda archive: None)      # unindexed: names from the archive
    _fake_aws(monkeypatch, _stacked_zip())
    assert s3.main(argv) == s3.FAIL
    lines = capsys.readouterr().out.splitlines()
    i = lines.index(f"FAIL     {stuck}")
    assert says in lines[i + 1], lines
    assert lines[-1] == "1 fetched, 1 failed"
    good = NESTED_MEMBER if stuck == FILE_MEMBER else FILE_MEMBER
    assert [ln for ln in lines if ln.startswith("FETCHED")] == \
        [f"FETCHED  refaudio/cache/{ARCH[:-4]}/{good}  (archive SHA-256 verified)"]
    pack = s3.CACHE / ARCH[:-4]
    assert {p.relative_to(pack).as_posix(): p.read_bytes()
            for p in s3.CACHE.rglob("*") if p.is_file()} == {good: b"ab" if good == NESTED_MEMBER else b"a"}
    assert not list(s3.CACHE.rglob("*.part"))
    assert not (s3.ARCHIVES / ARCH).exists()


def test_prefix_excludes_macosx_and_clean_removes(env, monkeypatch):
    _fake_aws(monkeypatch, _zip())
    assert s3.main(["--prefix", ARCH, "808 Loops From Mars/WAV/"]) == s3.FETCHED
    assert not (s3.CACHE / ARCH[:-4] / "__MACOSX").exists()
    assert s3.main(["--clean"]) == s3.FETCHED
    assert not s3.CACHE.exists()


def test_profile_is_passed(env, monkeypatch):
    monkeypatch.setenv("REFAUDIO_S3_PROFILE", "batch-runner-submit")
    calls = _fake_aws(monkeypatch, _zip())
    s3.main([ARCH, MEMBER])
    assert calls[0][:3] == ["aws", "--profile", "batch-runner-submit"]


# ---------------------------------------------------------------------------
# START RED (#1602). Each of these was a live defect against origin/main
# 1023ef4b: the S3 route carried its own copies of the catalog, path, index and
# zip-loop logic, and the copies had drifted from the local and ssh routes.
# INDEXED is a pack the REAL catalog indexes (refaudio/index/808-from-mars.tsv),
# so its committed index is what refuses or checks, exactly as a user's would.
# ---------------------------------------------------------------------------

def test_a_member_under_a_prefix_that_climbs_out_of_the_cache_is_REFUSED(env, monkeypatch, capsys):
    """The path check ran on the typed prefix alone, never on the names it expanded
    to. This archive was FETCHED with exit 0 and the file written OUTSIDE
    refaudio/cache/. Every expanded name is now checked before anything is written,
    so nothing lands at all -- not even the member that was fine."""
    b = io.BytesIO()
    with zipfile.ZipFile(b, "w") as z:
        z.writestr(MEMBER, b"RIFFfake")
        z.writestr(ESCAPE, b"x")
    _fake_aws(monkeypatch, b.getvalue())
    assert s3.main(["--prefix", ARCH, "808 Loops From Mars/WAV/"]) == s3.REFUSED
    out = capsys.readouterr().out
    assert "REFUSED" in out and "relative path inside the archive" in out and ESCAPE in out
    assert _escaped(env) == []
    assert _landed() == [] and not (s3.CACHE / ARCH[:-4]).exists()


def test_an_indexed_prefix_nothing_is_under_is_refused_without_a_download(env, monkeypatch, capsys):
    """The committed index already says the folder is empty; the multi-GB download
    used to happen anyway, and the refusal came only after the hash."""
    calls = _fake_aws(monkeypatch, _zip())
    assert s3.main(["--prefix", INDEXED, "NoSuchFolder/"]) == s3.REFUSED
    assert calls == [], "REFUSED must come before the download"
    assert "no indexed member" in capsys.readouterr().out
    assert not s3.ARCHIVES.exists()


def test_a_malformed_index_is_REFUSED_without_a_download(env, monkeypatch, capsys):
    """A row with no size used to escape as a ValueError traceback -- exit 1, read
    as FAIL -- after the whole archive had been downloaded and hashed."""
    calls = _fake_aws(monkeypatch, _zip())

    def malformed(tsv):
        raise ValueError("line 2: missing bytes")
    monkeypatch.setattr(s3.rl, "read_indexed_sizes", malformed)
    assert s3.main([INDEXED, MEMBER]) == s3.REFUSED
    out = capsys.readouterr().out
    assert "REFUSED" in out and "missing bytes" in out
    assert calls == []


def test_a_member_absent_from_a_committed_index_says_its_size_is_unchecked(env, monkeypatch, capsys):
    """The local and ssh routes say this out loud; this route said nothing."""
    _fake_aws(monkeypatch, _zip())
    assert s3.main([INDEXED, MEMBER]) == s3.FETCHED
    out = capsys.readouterr().out
    assert f"note     {MEMBER!r} is not in the index: size will NOT be checked" in out
    assert "archive SHA-256 verified" in out


def test_an_indexed_member_is_checked_against_the_index(env, monkeypatch, capsys):
    """The control for the size check on an indexed pack: it was already here, and
    the consolidation must not lose it. (Passes on origin/main too.)"""
    member, size = next(iter(s3.rl.indexed_sizes(INDEXED).items()))
    b = io.BytesIO()
    with zipfile.ZipFile(b, "w") as z:
        z.writestr(member, b"x" * (size + 1))
    _fake_aws(monkeypatch, b.getvalue())
    assert s3.main([INDEXED, member]) == s3.FAIL
    assert f"size {size + 1} != {size} in the index" in capsys.readouterr().out
    assert not (s3.CACHE / INDEXED[:-4] / member).exists()


def test_an_archive_whose_size_disagrees_with_the_catalog_is_REFUSED(env, monkeypatch, capsys):
    """`archive_bytes` was ignored on this route; the local route refuses on it
    before hashing."""
    calls = _fake_aws(monkeypatch, _zip(), archive_bytes=1_000_000_000)
    assert s3.main([ARCH, MEMBER]) == s3.REFUSED
    assert f"REFUSED  {ARCH} is {len(_zip())} bytes, catalog.json says 1000000000" in capsys.readouterr().out
    assert len(calls) == 1, "a download's size can only be checked once it is here"
    assert not (s3.ARCHIVES / ARCH).exists()
    assert _landed() == []


def test_a_cached_archive_that_is_not_the_catalogued_size_is_downloaded_again(env, monkeypatch):
    """The control for the cached-copy branch: a cached file is trusted only when
    it is the size AND the hash the catalog records. (Passes on origin/main too:
    the hash caught it; the size is the cheap half that comes first.)"""
    calls = _fake_aws(monkeypatch, _zip())
    s3.ARCHIVES.mkdir(parents=True)
    (s3.ARCHIVES / ARCH).write_bytes(b"stale")
    assert s3.main(["--keep-archive", ARCH, MEMBER]) == s3.FETCHED
    assert len(calls) == 1
    assert (s3.ARCHIVES / ARCH).read_bytes() == _zip()


def test_an_archive_that_hashes_right_but_is_not_a_zip_is_FAIL(env, monkeypatch, capsys):
    """BadZipFile used to escape `main()` as a traceback."""
    calls = _fake_aws(monkeypatch, b"not a zip")
    assert s3.main([ARCH, MEMBER]) == s3.FAIL
    assert "does not open as a zip" in capsys.readouterr().out
    assert len(calls) == 1 and not (s3.ARCHIVES / ARCH).exists()
    assert _landed() == []


def test_a_prefix_that_is_not_a_relative_path_is_refused_before_the_download(env, monkeypatch):
    for bad in ["../etc/", "/abs/", ""]:
        calls = _fake_aws(monkeypatch, _zip())
        assert s3.main(["--prefix", ARCH, bad]) == s3.REFUSED
        assert calls == []


# ---------------------------------------------------------------------------
# tools/refaudio_member_list.py: the folder's immediate WAVs, from a
# hash-verified archive, as the inventory file the loop reader consumes.
# ---------------------------------------------------------------------------
def _folder_zip() -> bytes:
    b = io.BytesIO()
    with zipfile.ZipFile(b, "w") as z:
        z.writestr(FOLDER + "/BD 4x4 Zeta 808 Loops 128.wav", b"b")
        z.writestr(FOLDER + "/BD 4x4 Alpha 808 Loops 120.wav", b"a")
        z.writestr(FOLDER + "/Read Me.txt", b"t")
        z.writestr(FOLDER + "/Processed/BD 4x4 nested 808 Loops 120.wav", b"n")
        z.writestr(FOLDER + " Tops/BD 4x4 sibling 808 Loops 120.wav", b"s")
        z.writestr("__MACOSX/" + FOLDER + "/BD 4x4 Alpha 808 Loops 120.wav", b"junk")
    return b.getvalue()


def _names(text: str) -> list[str]:
    """The member lines of an inventory: everything that is not a `#` header."""
    return [ln for ln in text.splitlines() if ln and not ln.startswith("#")]


def test_member_list_lists_the_folders_immediate_wavs_and_nothing_else(env, monkeypatch, capsys):
    """Sorted, and only the folder's own WAVs: not a sub-folder's, not the
    sibling folder that begins with the same name, not another extension, not
    a resource fork. No audio is extracted and the archive is not kept."""
    blob = _folder_zip()
    _fake_aws(monkeypatch, blob)
    assert ml.main([ARCH, FOLDER]) == ml.LISTED
    out = capsys.readouterr().out
    assert _names(out) == ["BD 4x4 Alpha 808 Loops 120.wav", "BD 4x4 Zeta 808 Loops 128.wav"]
    head = [ln for ln in out.splitlines() if ln.startswith("#")]
    assert f"# archive_sha256 {hashlib.sha256(blob).hexdigest()}" in head
    assert any("tools/refaudio_member_list.py " in ln and FOLDER in ln for ln in head), head
    assert any("tools/refaudio_s3.py --prefix " in ln and f"{FOLDER}/" in ln
               and "REFAUDIO_S3=s3://bucket/prefix" in ln for ln in head), head
    assert not (s3.ARCHIVES / ARCH).exists()
    assert not (s3.CACHE / ARCH[:-4]).exists()


def test_member_list_is_deterministic_so_a_regeneration_diffs_clean(env, monkeypatch, capsys):
    _fake_aws(monkeypatch, _folder_zip())
    ml.main([ARCH, FOLDER])
    first = capsys.readouterr().out
    ml.main([ARCH, FOLDER])
    assert capsys.readouterr().out == first


def test_member_list_write_keeps_stdout_quiet_and_the_commands_in_the_file(
        env, monkeypatch, capsys):
    _fake_aws(monkeypatch, _folder_zip())
    out = env / "inventory.txt"
    assert ml.main([ARCH, FOLDER, "--write", str(out), "--keep-archive"]) == ml.LISTED
    assert capsys.readouterr().out == ""
    text = out.read_text(encoding="utf-8")
    assert len(_names(text)) == 2
    assert any(f"--write {out}" in ln for ln in text.splitlines() if ln.startswith("#"))
    assert (s3.ARCHIVES / ARCH).exists()


def test_the_loop_reader_reads_what_the_lister_wrote(env, monkeypatch):
    import measure_repeatability as mr
    _fake_aws(monkeypatch, _folder_zip())
    out = env / "inventory.txt"
    assert ml.main([ARCH, FOLDER, "--write", str(out)]) == ml.LISTED
    assert mr.expected_loops(out) == ["BD 4x4 Alpha 808 Loops 120.wav",
                                      "BD 4x4 Zeta 808 Loops 128.wav"]


@pytest.mark.parametrize("write", [False, True], ids=["stdout", "write"])
def test_member_list_cli_missing_route_leaves_inventory_intact(monkeypatch, tmp_path, write):
    monkeypatch.delenv("REFAUDIO_S3", raising=False)
    cmd = [sys.executable, ml.__file__, ARCH, FOLDER]
    if write:
        inventory = tmp_path / "inventory.txt"
        previous = b"# Previous inventory\r\noriginal.wav\r\n"
        inventory.write_bytes(previous)
        cmd.extend(["--write", str(inventory)])

    result = subprocess.run(cmd, capture_output=True, text=True)
    assert result.returncode == 2
    assert result.stdout == ""
    assert "REFUSED" in result.stderr and "REFAUDIO_S3 is not set" in result.stderr
    if write:
        assert inventory.read_bytes() == previous


def test_member_list_refuses_a_folder_with_no_wav(env, monkeypatch, capsys):
    """An empty inventory must never be written: the reader would refuse it,
    and a reader that did not would read whatever was on disk."""
    _fake_aws(monkeypatch, _folder_zip())
    out = env / "inventory.txt"
    assert ml.main([ARCH, "808 Loops From Mars/WAV/03. Bass Drum", "--write", str(out)]) == ml.REFUSED
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "holds no WAV" in captured.err
    assert not out.exists()


def test_member_list_refuses_a_hash_mismatch_before_reading_a_name(env, monkeypatch, capsys):
    _fake_aws(monkeypatch, _folder_zip(), want_sha="0" * 64)
    out = env / "inventory.txt"
    assert ml.main([ARCH, FOLDER, "--write", str(out)]) == ml.REFUSED
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "not the archive the catalogue describes" in captured.err
    assert not out.exists()
    assert not (s3.ARCHIVES / ARCH).exists()


def test_member_list_refuses_without_a_route_or_for_an_unknown_archive(monkeypatch, env):
    monkeypatch.delenv("REFAUDIO_S3", raising=False)
    assert ml.main([ARCH, FOLDER]) == ml.REFUSED
    monkeypatch.setenv("REFAUDIO_S3", "s3://bucket/prefix")
    assert ml.main(["nope.zip", FOLDER]) == ml.REFUSED


def test_member_list_transfer_failure_is_FAIL_not_refused(env, monkeypatch, capsys):
    _fake_aws(monkeypatch, b"", rc=1)
    assert ml.main([ARCH, FOLDER]) == ml.FAIL
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "FAIL" in captured.err and "AccessDenied" in captured.err


def test_member_list_reports_an_invalid_zip_on_stderr(env, monkeypatch, capsys):
    _fake_aws(monkeypatch, b"not a zip")
    assert ml.main([ARCH, FOLDER]) == ml.FAIL
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "FAIL" in captured.err and "does not open as a zip" in captured.err


def test_member_list_refuses_a_name_that_cannot_be_listed_one_per_line(env, monkeypatch, capsys):
    """The file's format is one member a line; a name holding a line break
    would list as two members, neither of which exists."""
    b = io.BytesIO()
    with zipfile.ZipFile(b, "w") as z:
        z.writestr(FOLDER + "/BD 4x4\ninjected 808 Loops 120.wav", b"x")
    _fake_aws(monkeypatch, b.getvalue())
    out = env / "inventory.txt"
    assert ml.main([ARCH, FOLDER, "--write", str(out)]) == ml.REFUSED
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "line break" in captured.err
    assert not out.exists()


def _zip_of(*members: str) -> bytes:
    b = io.BytesIO()
    with zipfile.ZipFile(b, "w") as z:
        for m in members:
            z.writestr(FOLDER + "/" + m, b"x")
    return b.getvalue()


def test_every_name_the_lister_accepts_is_read_back_unchanged(env, monkeypatch):
    """Round trip over awkward-but-representable names: spaces, a '#' that is
    not first, a leading space, a tab, non-ASCII. Writer and reader must agree."""
    import measure_repeatability as mr
    names = ["a #1.wav", " lead.wav", "tab\there.wav", "caf\u00e9.wav", "normal.wav"]
    _fake_aws(monkeypatch, _zip_of(*names))
    out = env / "inventory.txt"
    assert ml.main([ARCH, FOLDER, "--write", str(out)]) == ml.LISTED
    assert mr.expected_loops(out) == sorted(names)


def test_duplicate_zip_members_leave_the_previous_inventory_readable(env, monkeypatch, capsys):
    import measure_repeatability as mr
    out = env / "inventory.txt"
    with monkeypatch.context() as good:
        _fake_aws(good, _zip_of("original.wav"))
        assert ml.main([ARCH, FOLDER, "--write", str(out)]) == ml.LISTED
    previous = out.read_bytes()
    with pytest.warns(UserWarning, match="Duplicate name"):
        blob = _zip_of("same.wav", "other.wav", "same.wav")
    _fake_aws(monkeypatch, blob)
    assert ml.main([ARCH, FOLDER, "--write", str(out)]) == ml.REFUSED
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "REFUSED" in captured.err and "same.wav" in captured.err and FOLDER in captured.err
    assert out.read_bytes() == previous
    assert mr.expected_loops(out) == ["original.wav"]


@pytest.mark.parametrize("bad", ["#2.wav", "a\x85b.wav", "a\u2028b.wav", "a\x0bb.wav",
                                 "a\x1cb.wav", "a\rb.wav"])
def test_a_name_the_reader_would_drop_or_split_is_refused_before_writing(
        env, monkeypatch, capsys, bad):
    """The reader treats a leading '#' as a comment and splits on every
    `str.splitlines` boundary, not just CR/LF: such a name would be dropped or
    become two members while the lister reported success."""
    _fake_aws(monkeypatch, _zip_of("normal.wav", bad))
    out = env / "inventory.txt"
    assert ml.main([ARCH, FOLDER, "--write", str(out)]) == ml.REFUSED
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "REFUSED" in captured.err
    assert not out.exists()
