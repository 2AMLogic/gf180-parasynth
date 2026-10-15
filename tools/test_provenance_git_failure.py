"""#615: a provenance helper whose `git` returned "" on failure reported
`dirty=False` -- an unanswered git rendered as a clean tree."""
import pathlib
import subprocess
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import provenance                                                     # noqa: E402
import refprofile as rp                                               # noqa: E402


def _repo(path: pathlib.Path) -> None:
    env = ["-c", "user.email=t@t", "-c", "user.name=t"]
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    (path / "f").write_text("x")
    subprocess.run(["git", "-C", str(path), "add", "f"], check=True)
    subprocess.run(["git", "-C", str(path), *env, "commit", "-qm", "c"], check=True)


def test_provenance_worktree_state_is_not_clean_when_git_cannot_answer(tmp_path):
    w = provenance.worktree_state(root=tmp_path)          # not a repository
    assert w["commit"] == "?"
    assert w["dirty"] is True


def test_provenance_worktree_state_still_reports_a_real_clean_repo_clean(tmp_path):
    """The defeating input for the guard above: a guard that said dirty always
    would pass the first test."""
    _repo(tmp_path)
    assert provenance.worktree_state(root=tmp_path)["dirty"] is False
    (tmp_path / "g").write_text("y")
    assert provenance.worktree_state(root=tmp_path)["dirty"] is True


def test_refprofile_worktree_state_is_not_clean_when_git_cannot_answer(tmp_path, monkeypatch):
    monkeypatch.setattr(rp, "ROOT", tmp_path)
    assert rp.worktree_state()["dirty"] is True


def test_refprofile_worktree_state_still_reports_a_real_clean_repo_clean(tmp_path, monkeypatch):
    _repo(tmp_path)
    monkeypatch.setattr(rp, "ROOT", tmp_path)
    assert rp.worktree_state()["dirty"] is False


def test_conga_provenance_is_not_clean_when_git_cannot_answer(tmp_path, monkeypatch):
    import measure_conga_body_spread as conga
    monkeypatch.setattr(conga, "ROOT", tmp_path)
    assert conga.provenance(tmp_path)["dirty"] is True


def test_conga_provenance_still_reports_a_real_clean_repo_clean(tmp_path, monkeypatch):
    import measure_conga_body_spread as conga
    _repo(tmp_path)
    monkeypatch.setattr(conga, "ROOT", tmp_path)
    assert conga.provenance(tmp_path)["dirty"] is False


def _tom_drop_compare(tmp_path, monkeypatch):
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "model"))
    import tom_drop_compare as tdc
    (tmp_path / "model").mkdir(exist_ok=True)
    (tmp_path / "model" / "drums_fx.py").write_text("x = 1\n")
    monkeypatch.setattr(tdc, "REPO", tmp_path)
    return tdc


def test_tom_drop_compare_provenance_is_not_clean_when_git_cannot_answer(tmp_path, monkeypatch):
    tdc = _tom_drop_compare(tmp_path, monkeypatch)
    assert tdc.provenance()["dirty"] is True


def test_tom_drop_compare_provenance_still_reports_a_real_clean_repo_clean(tmp_path, monkeypatch):
    tdc = _tom_drop_compare(tmp_path, monkeypatch)
    _repo(tmp_path)
    subprocess.run(["git", "-C", str(tmp_path), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "-c", "user.email=t@t", "-c", "user.name=t",
                    "commit", "-qm", "m"], check=True)
    assert tdc.provenance()["dirty"] is False
