import pytest

from verify_m5a_filter2x_i2s import ROOT, _repo_path


def test_verification_artifact_paths_are_repository_relative():
    assert _repo_path("build/m5a-filter2x-i2s").is_relative_to(ROOT)
    assert _repo_path(str(ROOT / "build/out.wav")) == ROOT / "build/out.wav"


def test_verification_refuses_external_artifact_paths(tmp_path):
    with pytest.raises(ValueError, match="inside the repository"):
        _repo_path(str(tmp_path / "outside.wav"))
