#!/usr/bin/env python3
"""Tests for `tools/refaudio_s3.py`: FETCHED / FAIL / REFUSED, with `aws` faked.

    .venv/bin/python -m pytest tools/test_refaudio_s3.py -q

A fake `aws` writes a small zip, so what is exercised is the real hash check,
extraction and cleanup, with no network and no licensed audio.
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

ARCH = "808_loops_from_mars.zip"
MEMBER = "808 Loops From Mars/WAV/03. Bass Drum/4x4/x.wav"


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


def _fake_aws(monkeypatch, data: bytes, rc: int = 0, want_sha: str | None = None):
    sha = want_sha or hashlib.sha256(data).hexdigest()
    real = s3.rl.pack_record
    monkeypatch.setattr(s3.rl, "pack_record", lambda a: {**real(a), "archive_sha256": sha})
    calls = []

    def run(cmd, **kw):
        calls.append(cmd)
        if rc == 0:
            pathlib.Path(cmd[-2] if cmd[-1] == "--only-show-errors" else cmd[-1]).write_bytes(data)
        return subprocess.CompletedProcess(cmd, rc, b"", b"AccessDenied" if rc else b"")
    monkeypatch.setattr(s3.subprocess, "run", run)
    return calls


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
