import hashlib
import io
import tarfile
import pytest
from setup_ci_oss_cad import extract_verified


def archive(tmp_path, name):
    path = tmp_path / "fixture.tgz"
    with tarfile.open(path, "w:gz") as bundle:
        info = tarfile.TarInfo(name)
        info.size = 4
        bundle.addfile(info, io.BytesIO(b"test"))
    return path, hashlib.sha256(path.read_bytes()).hexdigest()


def test_tampered_archive_refuses_before_extraction(tmp_path):
    path, digest = archive(tmp_path, "oss-cad-suite/bin/yosys")
    path.write_bytes(path.read_bytes() + b"tampered")
    with pytest.raises(RuntimeError, match="checksum"):
        extract_verified(path, tmp_path / "out", digest)
    assert not (tmp_path / "out").exists()


def test_verified_archive_extracts_and_traversal_refuses(tmp_path):
    path, digest = archive(tmp_path, "oss-cad-suite/bin/yosys")
    extract_verified(path, tmp_path / "out", digest)
    assert (tmp_path / "out/oss-cad-suite/bin/yosys").read_bytes() == b"test"
    path, digest = archive(tmp_path, "../escape")
    with pytest.raises(tarfile.FilterError):
        extract_verified(path, tmp_path / "out", digest)
