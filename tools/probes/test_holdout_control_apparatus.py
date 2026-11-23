"""Controls for the apparatus of tools/probes/holdout_injected_controls.py
(issue #1118).

The probe measures whether injected defects turn `tools/test_holdout.py` red,
so the probe itself is the apparatus, and `run()` used to return pytest's own
process exit status straight into `main()`'s outcome space. With
`tools/test_holdout.py` deleted, pytest exits 4 in every injected copy, every
control landed in the "fired" branch, and the probe printed "every control
fired" and exited 0 -- a false green over exactly the drift the control exists
to surface. Each case below builds a throwaway tree (the real `tools/
run_case.py` and `tools/holdout.py`, so the injections genuinely apply, plus a
caller-controlled `tools/test_holdout.py`) and drives the real `run()`/`main()`
against it by repointing the probe's ROOT.

Measured pytest statuses pinned here (9.2.0): 4 (path does not exist), 2 (a
collection/import error -- which `main()` used to misreport as "the injection
point is gone"), 5 (no tests collected), and 1 with no parseable
`FAILED tools/test_holdout.py::` line (an ERROR-only run), which used to
report "red, 0 test(s) caught it" and pass. `test_a_working_apparatus...` is
the rule-8 pairing: a probe that refused everything would satisfy every
refusal assertion above, so a healthy firing is pinned too.
"""
from __future__ import annotations

import pathlib
import shutil
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import holdout_injected_controls as hic  # noqa: E402

#: The real tree, captured before any test repoints `hic.ROOT`.
REAL_ROOT = hic.ROOT


def _tree(tmp_path: pathlib.Path, test_holdout: str | None,
          conftest: str | None = None) -> pathlib.Path:
    """A minimal tree the probe can inject into: the real injection targets
    (so every INJECTS pattern matches exactly once) and a controlled
    `tools/test_holdout.py`. `None` leaves that file out entirely."""
    root = tmp_path / "apparatus"
    (root / "tools").mkdir(parents=True)
    for rel in ("tools/run_case.py", "tools/holdout.py"):
        shutil.copyfile(REAL_ROOT / rel, root / rel)
    if test_holdout is not None:
        (root / "tools/test_holdout.py").write_text(test_holdout)
    if conftest is not None:
        (root / "conftest.py").write_text(conftest)
    return root


def _probe(monkeypatch, capsys, root: pathlib.Path) -> tuple[int, str]:
    monkeypatch.setattr(hic, "ROOT", root)
    rc = hic.main()
    return rc, capsys.readouterr().out


def test_a_deleted_test_holdout_py_is_a_refusal_not_a_pass(
        monkeypatch, tmp_path, capsys):
    root = _tree(tmp_path, test_holdout=None)
    rc, out = _probe(monkeypatch, capsys, root)
    assert rc != 0, out
    assert "every control fired" not in out
    assert out.count(": REFUSED --") == len(hic.INJECTS)
    assert "pytest exited" in out  # the status is named, not just counted


def test_an_import_error_in_test_holdout_py_refuses_without_blaming_the_injection_point(
        monkeypatch, tmp_path, capsys):
    root = _tree(tmp_path, test_holdout="import gf180_no_such_module_control\n")
    rc, out = _probe(monkeypatch, capsys, root)
    assert rc != 0, out
    assert "every control fired" not in out
    # pytest exits 2 on a collection error; the old main() folded that into
    # "the injection point is gone", a specific claim that was false here.
    assert "the injection point is gone" not in out
    assert "pytest exited" in out


def test_a_test_holdout_py_that_collects_no_tests_is_a_refusal(
        monkeypatch, tmp_path, capsys):
    root = _tree(tmp_path, test_holdout="# no tests defined\n")
    rc, out = _probe(monkeypatch, capsys, root)
    assert rc != 0, out
    assert "every control fired" not in out
    assert "0 test(s) caught it" not in out
    assert "pytest exited" in out


def test_an_error_only_red_run_is_refused_rather_than_caught_by_nothing(
        monkeypatch, tmp_path, capsys):
    # pytest exits 1 when tests ERROR, and an ERROR line does not parse as a
    # FAILED tools/test_holdout.py:: name -- the "red, 0 test(s) caught it"
    # shape. Nothing was measured, so it must refuse, not report a firing.
    conftest = ("import pytest\n\n\n"
                "@pytest.fixture(autouse=True)\n"
                "def boom():\n"
                "    raise RuntimeError('apparatus control')\n")
    root = _tree(tmp_path, test_holdout="def test_a():\n    assert 1\n",
                 conftest=conftest)
    rc, out = _probe(monkeypatch, capsys, root)
    assert rc != 0, out
    assert "0 test(s) caught it" not in out
    assert "every control fired" not in out
    assert "REFUSED" in out


def test_a_missing_injection_point_keeps_its_own_distinct_refusal(
        monkeypatch, tmp_path, capsys):
    # The pre-#1118 refusal reason must survive as its own message: scrub the
    # CLEAN_OFF pattern out of the copied holdout.py, so that one control
    # cannot inject while the other two still fire.
    root = _tree(tmp_path, test_holdout="def test_a():\n    assert 0\n")
    target = root / "tools/holdout.py"
    text = target.read_text()
    needle = '    if _git("diff", "--name-only", "HEAD", "--", rel).strip():'
    assert needle in text  # the pattern this control injects against
    target.write_text(text.replace(needle, "    if True:"))
    rc, out = _probe(monkeypatch, capsys, root)
    assert rc != 0, out
    assert "the injection point is gone from tools/holdout.py" in out
    assert "every control fired" not in out


def test_a_working_apparatus_still_fires_and_names_what_caught_it(
        monkeypatch, tmp_path, capsys):
    root = _tree(tmp_path, test_holdout="def test_a():\n    assert 0\n")
    rc, out = _probe(monkeypatch, capsys, root)
    assert rc == 0, out
    assert "every control fired" in out
    assert "red, 1 test(s) caught it" in out
    assert "test_a" in out
