import hashlib
import json
from pathlib import Path
import zipfile

import pytest
import refprofile_restore as restore


def fixture(tmp_path, *, corrupt=False, omit=False):
    clips = {"a": b"independently known clip A", "b": b"independently known clip B"}
    profile = {"clips": {key: {"file": f"cache/{key}.wav", "bytes": len(value),
                               "sha256": hashlib.sha256(value).hexdigest()}
                          for key, value in clips.items()}}
    manifest = tmp_path / "profile.json"
    manifest.write_text(json.dumps(profile))
    archive = tmp_path / "cache.zip"
    with zipfile.ZipFile(archive, "w") as output:
        output.writestr("cache/a.wav", clips["a"])
        if not omit:
            output.writestr("cache/b.wav", b"corrupted" if corrupt else clips["b"])
    return archive, manifest, clips


def test_restore_exact_bytes_and_idempotence(tmp_path):
    archive, manifest, clips = fixture(tmp_path)
    for _ in range(2):
        assert restore.restore(archive, manifest) == 2
        for key, value in clips.items():
            assert (tmp_path / "cache" / f"{key}.wav").read_bytes() == value


@pytest.mark.parametrize("fault", ["corrupt", "omit"])
def test_broken_archive_refuses_before_writing_any_clip(tmp_path, fault):
    archive, manifest, _ = fixture(tmp_path, **{fault: True})
    with pytest.raises(restore.Refused):
        restore.restore(archive, manifest)
    assert not (tmp_path / "cache").exists()


def test_missing_archive_refuses(tmp_path):
    archive, manifest, _ = fixture(tmp_path)
    archive.unlink()
    with pytest.raises(restore.Refused, match="archive"):
        restore.restore(archive, manifest)


def test_manifest_cannot_write_outside_cache(tmp_path):
    archive, manifest, _ = fixture(tmp_path)
    data = json.loads(manifest.read_text())
    data["clips"]["a"]["file"] = "../outside.wav"
    manifest.write_text(json.dumps(data))
    with pytest.raises(restore.Refused, match="path"):
        restore.restore(archive, manifest)
