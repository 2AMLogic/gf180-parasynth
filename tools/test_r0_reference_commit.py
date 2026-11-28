"""tools/r0_reference.stage: the manifest's `image.source_commit` is checked
for its form before git sees it (#2082, #2104).

The value goes into `git cat-file -e <v>^{commit}` and then `git checkout <v>
-- <files>`. A leading `-` fails at the cat-file today, because that call
refuses on any non-zero status -- the accidental guard #2084 describes -- but
a mutable name (`HEAD`, a branch) is not stopped at all. The manifest writer
(fpga/release/release_manifest.py) emits the image's full 40-hex object ID, so
that is the only form `stage` accepts.

Nothing here adds a worktree: the stub lets `cat-file` through to real git and
stops the run at the next call, where `stage` would begin to change the
checkout.
"""
from __future__ import annotations

import pathlib
import re
import subprocess
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import r0_reference as rr                                          # noqa: E402

FULL_ID = re.compile(r"[0-9a-f]{40}")
IMAGE_ID = "d089c678e661d8ba7cf54841036fea89d864aaf9"


class _Reached(Exception):
    """Raised where `stage` would add a worktree."""


def _manifest(commit):
    return {"image": {"source_commit": commit}}


def _git(monkeypatch, later):
    """`subprocess.run` recorded; real git answers `cat-file`, `later` answers
    everything after it."""
    real = subprocess.run
    calls = []

    def run(argv, **kwargs):
        calls.append(list(argv))
        if argv[1] == "cat-file":
            return real(argv, **kwargs)
        return later(list(argv), real, kwargs)
    monkeypatch.setattr(subprocess, "run", run)
    return calls


def _stop(argv, real, kwargs):
    raise _Reached(argv)


def test_a_full_object_id_reaches_git_cat_file(tmp_path, monkeypatch):
    """Positive control: the guard does not refuse everything. Real git is
    asked about a commit every clone has -- this checkout's HEAD -- and the
    run stops only where `stage` would add a worktree."""
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=rr.ROOT, check=True,
                          capture_output=True, text=True).stdout.strip()
    assert FULL_ID.fullmatch(head)
    calls = _git(monkeypatch, _stop)
    with pytest.raises(_Reached):
        rr.stage(_manifest(head), tmp_path / "stage")
    assert calls[0] == ["git", "cat-file", "-e", f"{head}^{{commit}}"]
    assert [c[1] for c in calls[1:]] == ["worktree"]


@pytest.mark.parametrize("image", sorted(rr.IMAGES))
def test_the_shipped_manifests_pass_the_guard(image):
    """What `main` hands `stage`, for both images: what their writer emits."""
    commit = rr.load_manifest(rr.IMAGES[image]["manifest"])["image"]["source_commit"]
    assert FULL_ID.fullmatch(commit), commit


@pytest.mark.parametrize("commit", [None, "", 0], ids=repr)
def test_an_absent_image_source_commit_keeps_its_own_message(tmp_path, monkeypatch, commit):
    """A manifest naming no commit is refused as before, in its own words: a
    different fault from a malformed one."""
    calls = _git(monkeypatch, _stop)
    with pytest.raises(rr.Refused, match="names no image.source_commit"):
        rr.stage(_manifest(commit), tmp_path / "stage")
    assert calls == []


@pytest.mark.parametrize("preexisting", [False, True], ids=["created", "truncated"])
def test_option_in_image_source_commit_is_refused_before_git(tmp_path, monkeypatch, preexisting):
    """The guard (#2082's input): refused by its form before any git runs, in
    a message of its own, and the named file is neither created nor truncated."""
    target = tmp_path / "target"
    if preexisting:
        target.write_bytes(b"keep me\n")
    calls = _git(monkeypatch, _stop)
    with pytest.raises(rr.Refused, match="image.source_commit is not a full object name") as refused:
        rr.stage(_manifest(f"--output={target}"), tmp_path / "stage")
    assert "names no" not in str(refused.value)
    assert calls == []
    if preexisting:
        assert target.read_bytes() == b"keep me\n"
    else:
        assert not target.exists()


@pytest.mark.parametrize("commit", [
    IMAGE_ID.upper(), IMAGE_ID[:-1], IMAGE_ID + "a", IMAGE_ID + "\n", " " + IMAGE_ID, IMAGE_ID + " ",
    "-" + IMAGE_ID, "--", IMAGE_ID + "^{}",
    212, 3.5, b"d089c678e661d8ba7cf54841036fea89d864aaf9", [IMAGE_ID], {"source_commit": IMAGE_ID},
], ids=repr)
def test_malformed_image_source_commit_is_refused_before_git(tmp_path, monkeypatch, commit):
    """Uppercase hex, 39 and 41 characters, surrounding whitespace, every
    decoration git would otherwise peel, and a non-string."""
    calls = _git(monkeypatch, _stop)
    with pytest.raises(rr.Refused, match="image.source_commit is not a full object name") as refused:
        rr.stage(_manifest(commit), tmp_path / "stage")
    assert "names no" not in str(refused.value)
    assert calls == []


@pytest.mark.parametrize("name", ["HEAD", "HEAD~1", "main", "refs/heads/main", "@", "d089c678"], ids=repr)
def test_a_name_git_would_resolve_is_refused_before_git(tmp_path, monkeypatch, name):
    """Rule 8: what satisfies the unguarded check while violating its intent.
    None of these begins with `-`, so `cat-file -e <name>^{commit}` accepts
    them and `stage` would go on to check out whatever they name today -- the
    tip, not the image's commit. Only the image's own object ID is accepted."""
    calls = _git(monkeypatch, _stop)
    with pytest.raises(rr.Refused, match="image.source_commit is not a full object name"):
        rr.stage(_manifest(name), tmp_path / "stage")
    assert calls == []


def test_control_the_unguarded_check_accepts_a_mutable_name():
    """The premise of the guard, on real git: `cat-file -e` accepts `HEAD`
    (exit 0) and rejects `--output=...` only because `cat-file` has no such
    option -- neither answer is about the image's commit. The refusals above
    therefore do not rest on git having been asked."""
    accepted = subprocess.run(["git", "cat-file", "-e", "HEAD^{commit}"], cwd=rr.ROOT,
                              capture_output=True, text=True)
    assert accepted.returncode == 0, accepted.stderr
    probe = rr.ROOT / "r0-stage-guard-probe"
    assert not probe.exists()
    rejected = subprocess.run(["git", "cat-file", "-e", f"--output={probe}^{{commit}}"], cwd=rr.ROOT,
                              capture_output=True, text=True)
    assert rejected.returncode != 0 and "unknown option" in rejected.stderr, rejected.stderr
    assert not probe.exists()
