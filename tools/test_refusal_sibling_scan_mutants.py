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


def test_a_parametrized_assertion_failure_is_still_an_assertion(tmp_path):
    """`FAILED t.py::test_a[case] - assert ...`: the id sits between the name
    and the message. Read as no message it counted as a crash, and the matrix
    refused every mutant that reddened a parametrized control."""
    body = ("import pytest\n\n@pytest.mark.parametrize('x', ['else', 'a:b'])\n"
            "def test_a(x):\n    assert x == 'nope'\n")
    failed, crashed, errored, _, _ = _run(tmp_path, body)
    assert (failed, crashed, errored) == ({"test_a"}, set(), set())


def test_a_custom_exception_is_a_crash_not_a_catch(tmp_path):
    """#1688 review round 3: a mutant that raised its own `Refused(Exception)`
    printed OK because only names ending in Error/Exception counted as crashes.
    Only an assertion is a catch; any other class, whatever its name, is not."""
    body = ("class Refused(Exception):\n    pass\n\n"
            "def test_a():\n    raise Refused('scanner crashed')\n")
    failed, crashed, errored, _, _ = _run(tmp_path, body)
    assert (failed, crashed, errored) == (set(), {"test_a"}, set())


def test_an_assertion_with_a_message_is_still_a_catch(tmp_path):
    failed, crashed, _, _, _ = _run(tmp_path, "def test_a():\n    assert 1 == 2, 'why'\n")
    assert (failed, crashed) == ({"test_a"}, set())


def test_main_no_verdict_when_a_custom_exception_crashes_a_mutant(one_mutant, monkeypatch, capsys):
    """Through the entry point: the crash is NO VERDICT, never `1 MOVED` + OK."""
    ctl = _first_control()
    out = ("FAILED " + mut.TESTS + f"::{ctl} - Refused: scanner crashed\n1 failed, 4 passed\n")
    failed, crashed, errored, passed, tail = mut.read_run(1, out)
    assert (failed, crashed) == (set(), {ctl})
    _fake(monkeypatch, GREEN, (failed, crashed, errored, passed, tail))
    assert mut.main([]) == 2
    assert "NO VERDICT" in capsys.readouterr().out


def test_every_shipped_mutant_anchor_applies_exactly_once():
    """#1688 review round 3: four anchors had drifted and the documented full
    run exited NO VERDICT. This is the cheap half of that check, so drift
    fails here instead of in a ten-minute matrix."""
    shipped = (mut.ROOT / mut.SCANNER).read_text()
    bad = [(name, old.strip()[:60]) for name, _, subs in mut.MUTANTS
           for old, _ in subs if shipped.count(old) != 1]
    assert bad == []
