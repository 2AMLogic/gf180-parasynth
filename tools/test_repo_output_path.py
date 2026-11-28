"""Controls for repo_output_path.confine and the five writers that use it (#1584)."""

import os

import pytest

import measure_m5a_filter_headroom as headroom
import measure_m5a_filter_oversample as oversample
import measure_m5a_pulse_duty as pulse_duty
import measure_m5a_signal_path as signal_path
import measure_shark_blamp as shark
import repo_output_path

# (module, option, declared refusal code)
WRITERS = [
    (headroom, "--out", 2), (headroom, "--recompare-report", 2),
    (oversample, "--out", 2), (signal_path, "--out", 2),
    (pulse_duty, "--out", 2), (shark, "--out", 3),
]
IDS = [f"{m.__name__}{o}" for m, o, _ in WRITERS]


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    return root.resolve()


def test_dotdot_through_a_regular_file_is_refused(repo):
    (repo / "take.wav").write_text("x")
    assert repo_output_path.confine("take.wav/../summary.json", repo) is None


def test_dotdot_through_an_existing_directory_is_collapsed(repo):
    (repo / "a").mkdir()
    assert repo_output_path.confine("a/../b/r.json", repo) == repo / "b" / "r.json"


def test_dotdot_through_a_missing_component_is_refused(repo):
    assert repo_output_path.confine("new/../r.json", repo) is None


def test_new_output_directories_are_allowed(repo):
    assert repo_output_path.confine("build/new/r.json", repo) == repo / "build" / "new" / "r.json"


def test_symlink_loop_is_refused_not_raised(repo):
    (repo / "latest.json").symlink_to(repo / "latest.json")
    assert repo_output_path.confine("latest.json", repo) is None
    assert repo_output_path.confine("latest.json", repo, replace_leaf=True) is None


def test_escape_is_refused(repo):
    assert repo_output_path.confine("../x.json", repo) is None
    assert repo_output_path.confine(str(repo.parent / "x.json"), repo) is None


@pytest.mark.parametrize("module,option,code", WRITERS, ids=IDS)
def test_dotdot_through_a_file_refuses_and_preserves_the_existing_report(repo, monkeypatch, capsys, module, option, code):
    monkeypatch.setattr(module, "ROOT", repo)
    (repo / "take.wav").write_text("not a directory")
    sentinel = repo / "summary.json"
    sentinel.write_bytes(b"existing report")
    assert module.main([option, "take.wav/../summary.json"]) == code
    captured = capsys.readouterr()
    assert "REFUSED" in captured.out + captured.err
    assert sentinel.read_bytes() == b"existing report"


@pytest.mark.parametrize("module,option,code", WRITERS, ids=IDS)
def test_symlink_loop_gets_the_declared_refusal_code_before_measurement(repo, monkeypatch, module, option, code):
    monkeypatch.setattr(module, "ROOT", repo)
    (repo / "latest.json").symlink_to(repo / "latest.json")
    assert module.main([option, "latest.json"]) == code
