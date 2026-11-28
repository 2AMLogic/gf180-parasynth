"""Controls for the runner in `refusal_sibling_scan_mutants.py` (#1688 review).

The matrix printed `OK: every mutant is seen by at least one control` and
returned 0 for a mutant that did not move a single assertion: it replaced
`top = mod.tree` with a `raise`, one control's FIXTURE failed in setup, and
the parsed error set counted as that control "seeing" the mutant. The runner
dropped pytest's exit status. `test_a_fixture_error_is_not_a_catch` is that
negative control, kept so the runner cannot go back to it.
"""
from __future__ import annotations

import pathlib
import subprocess
import sys

import pytest

import refusal_sibling_scan_mutants as mut

PASSING = "def test_a():\n    assert True\n"
ASSERTING = "def test_a():\n    assert 1 == 2\n"
CRASHING = "def test_a():\n    raise RuntimeError('boom')\n"
FIXTURE_ERROR = (
    "import pytest\n\n@pytest.fixture\ndef census():\n"
    "    raise RuntimeError('broken census fixture')\n\n"
    "def test_a(census):\n    assert census\n"
)
BROKEN_COLLECTION = "def test_a(:\n"


def _run(tmp_path: pathlib.Path, body: str):
    (tmp_path / "test_x.py").write_text(body)
    return mut._run(tmp_path, "test_x.py")


def test_a_passing_run_reads_as_green(tmp_path):
    failed, crashed, errored, passed, _ = _run(tmp_path, PASSING)
    assert (failed, crashed, errored, passed) == (set(), set(), set(), 1)


def test_an_assertion_failure_is_a_catch(tmp_path):
    failed, crashed, errored, _, _ = _run(tmp_path, ASSERTING)
    assert (failed, crashed, errored) == ({"test_a"}, set(), set())


def test_a_crash_in_the_test_body_is_not_an_assertion(tmp_path):
    failed, crashed, errored, _, _ = _run(tmp_path, CRASHING)
    assert (failed, crashed, errored) == (set(), {"test_a"}, set())


def test_a_fixture_error_is_not_a_catch(tmp_path):
    """The reviewer's diagnostic mutant: the fixture raises in setup, before
    any assertion ran. It is neither `failed` nor green."""
    failed, crashed, errored, _, _ = _run(tmp_path, FIXTURE_ERROR)
    assert failed == set() and errored == {"test_a"}


def test_a_collection_error_is_an_apparatus_error(tmp_path):
    with pytest.raises(mut.Apparatus):
        _run(tmp_path, BROKEN_COLLECTION)


@pytest.mark.parametrize("rc,out", [
    (5, "no tests ran"),                                   # nothing collected
    (2, "Interrupted"),
    (0, "1 failed"),                                       # status and text disagree
    (1, "1 passed in 0.1s"),                               # exit 1, nothing readable
])
def test_status_and_output_must_agree(rc, out):
    with pytest.raises(mut.Apparatus):
        mut.read_run(rc, out)


# --- the matrix verdict, end to end, with a faked pytest ----------------------
@pytest.fixture
def one_mutant(monkeypatch):
    """The real `main`, its real git clones, ONE trivial mutant, and a fake
    pytest keyed on whether the scratch scanner carries the mutant."""
    text = (mut.ROOT / mut.SCANNER).read_text()
    anchor = "ROOT = pathlib.Path(__file__).resolve().parents[1]\n"
    assert text.count(anchor) == 1
    monkeypatch.setattr(mut, "MUTANTS", [("TRIVIAL", "a comment",
                                          [(anchor, anchor + "# MUTANT\n")])])
    return mut


def _fake(monkeypatch, shipped, mutant):
    def run(scratch, tests=mut.TESTS):
        is_mutant = "# MUTANT" in (scratch / mut.SCANNER).read_text()
        return mutant if is_mutant else shipped
    monkeypatch.setattr(mut, "_run", run)


GREEN = (set(), set(), set(), 5, "5 passed")


def test_main_ok_when_an_assertion_catches_the_mutant(one_mutant, monkeypatch, capsys):
    ctl = _first_control()
    _fake(monkeypatch, GREEN, ({ctl}, set(), set(), 4, "1 failed, 4 passed"))
    assert mut.main([]) == 0, capsys.readouterr().out


def test_main_gap_when_nothing_moves(one_mutant, monkeypatch, capsys):
    _fake(monkeypatch, GREEN, GREEN)
    assert mut.main([]) == 1, capsys.readouterr().out


def test_main_no_verdict_when_a_fixture_errors_in_a_mutant(one_mutant, monkeypatch, capsys):
    """The reviewed defect: this used to print OK and return 0."""
    ctl = _first_control()
    _fake(monkeypatch, GREEN, (set(), set(), {ctl}, 4, "1 error, 4 passed"))
    assert mut.main([]) == 2
    assert "NO VERDICT" in capsys.readouterr().out


def test_main_no_verdict_when_a_mutant_crashes_a_test(one_mutant, monkeypatch, capsys):
    ctl = _first_control()
    _fake(monkeypatch, GREEN, (set(), {ctl}, set(), 4, "1 failed, 4 passed"))
    assert mut.main([]) == 2


def test_main_no_verdict_when_the_shipped_scanner_is_not_green(one_mutant, monkeypatch, capsys):
    ctl = _first_control()
    red = ({ctl}, set(), set(), 4, "1 failed")
    _fake(monkeypatch, red, red)
    assert mut.main([]) == 2


def test_main_no_verdict_on_an_apparatus_error(one_mutant, monkeypatch, capsys):
    def run(scratch, tests=mut.TESTS):
        raise mut.Apparatus("pytest exited 5")
    monkeypatch.setattr(mut, "_run", run)
    assert mut.main([]) == 2


def _first_control() -> str:
    tests = (mut.ROOT / mut.TESTS).read_text()
    section = tests.split(mut.SECTION, 1)[1].split("\n# --- ", 1)[0]
    import re
    return re.findall(r"^def (test_\w+)", section, re.M)[0]
