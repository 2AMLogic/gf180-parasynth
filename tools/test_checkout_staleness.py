"""The stale-checkout banner, and the two tools that now reuse it (issue #155).

WHY THIS FILE EXISTS. `tools/scorecard.py:checkout_staleness()` has existed
since #98 and warned readers of exactly one report that its tree was behind
`origin/main`. Merges here happen server-side through `gh`, so a local checkout
never advances on its own and EVERY read between merges is potentially stale --
which makes the warning a property of the class of status/report tools, not of
the one tool that happened to get it. #155 counted four stale reads in a single
session, including a merged commit diagnosed as *vanished*.

So this pins three things, because the first without the others is the failure
it is guarding against:

  1. the banner fires when `HEAD..origin/main` is non-empty, and says how far
     behind and on what branch;
  2. `tools/r1_scorecard.py` and `tools/compile_dag.py` -- the two tools wired
     up in #155 -- actually SURFACE it, each on the stream that cannot corrupt
     its own output;
  3. it still degrades SILENTLY to `None` when git cannot answer (no remote, a
     detached CI checkout, a tarball) and the new call sites do not crash on
     that. An absent warning is not a guarantee of freshness, and the
     docstring on `checkout_staleness()` says so -- a reused guard that turned
     "cannot answer" into a traceback would be a worse outcome than the
     original gap.

Every git call is faked. A test that needed a genuinely stale checkout would be
a test that cannot run in CI, which per docs/verification-rules.md rule 5 looks
exactly like one that passes.
"""
from __future__ import annotations

import json
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
import compile_dag as cd                                            # noqa: E402
import r1_scorecard as r1                                           # noqa: E402
import scorecard as sc                                              # noqa: E402


class _Result:
    def __init__(self, returncode: int, stdout: str) -> None:
        self.returncode, self.stdout, self.stderr = returncode, stdout, ""


class _FakeGit:
    """Stands in for the `subprocess` MODULE inside `scorecard` only.

    Replacing the name `scorecard.subprocess` rather than `subprocess.run`
    keeps the fake out of every other module's way -- `compile_dag` has its own
    `git()` on the real `subprocess`, and the classification it drives must
    keep working while the banner is being faked underneath it.
    """

    def __init__(self, answers: dict[str, str], *, raise_on: str | None = None) -> None:
        self.answers, self.raise_on, self.calls = answers, raise_on, []

    def run(self, argv, **kw):
        assert argv[0] == "git", argv
        key = " ".join(argv[1:])
        self.calls.append(key)
        if self.raise_on is not None and self.raise_on in key:
            raise OSError("git is not on PATH")
        if key not in self.answers:
            return _Result(128, "")              # git answered, with a failure
        return _Result(0, self.answers[key])


BEHIND_3 = {"rev-list --count HEAD..origin/main": "3",
            "rev-parse --abbrev-ref HEAD": "feature/issue-155"}
CURRENT = {"rev-list --count HEAD..origin/main": "0",
           "rev-parse --abbrev-ref HEAD": "main"}


def _fake(monkeypatch, answers, **kw):
    g = _FakeGit(answers, **kw)
    monkeypatch.setattr(sc, "subprocess", g)
    return g


# --------------------------------------------------------------- the banner


def test_banner_fires_when_the_tree_is_behind_origin_main(monkeypatch):
    _fake(monkeypatch, BEHIND_3)
    msg = sc.checkout_staleness()
    assert msg is not None
    assert "3 COMMITS BEHIND origin/main" in msg
    assert "feature/issue-155" in msg                 # which tree, not just that one


def test_banner_is_silent_on_a_current_tree(monkeypatch):
    _fake(monkeypatch, CURRENT)
    assert sc.checkout_staleness() is None


@pytest.mark.parametrize("answers,kw", [
    ({}, {}),                                         # git answered non-zero: no remote
    ({"rev-list --count HEAD..origin/main": ""}, {}),  # detached HEAD, empty answer
    ({"rev-list --count HEAD..origin/main": "not-a-number"}, {}),
    (BEHIND_3, {"raise_on": "rev-list"}),             # no git at all: a tarball
])
def test_banner_degrades_to_none_when_git_cannot_answer(monkeypatch, answers, kw):
    """`None` means UNKNOWN, not fresh -- it must not raise, and must not lie."""
    _fake(monkeypatch, answers, **kw)
    assert sc.checkout_staleness() is None


# ------------------------------------------------- the tools that reuse it
#
# Each asserts the stream as well as the text. r1_scorecard's stdout is status
# lines, so the banner belongs there; compile_dag's stdout under --print is the
# README block itself, so its banner goes to stderr and nowhere else.


def test_r1_scorecard_surfaces_the_banner_on_stdout(monkeypatch, capsys):
    _fake(monkeypatch, BEHIND_3)
    r1.main(["--check"])                              # --check: reads, never writes
    out = capsys.readouterr()
    assert "3 COMMITS BEHIND origin/main" in out.out


def test_r1_scorecard_is_quiet_on_a_current_tree(monkeypatch, capsys):
    _fake(monkeypatch, CURRENT)
    r1.main(["--check"])
    out = capsys.readouterr()
    assert "COMMITS BEHIND" not in out.out + out.err


def test_r1_scorecard_still_runs_when_git_cannot_answer(monkeypatch, capsys):
    _fake(monkeypatch, {}, raise_on="rev-list")
    rc = r1.main(["--check"])
    assert rc in (0, 1)                               # a verdict, not a traceback
    assert "COMMITS BEHIND" not in capsys.readouterr().out


def test_compile_dag_surfaces_the_banner_on_stderr(monkeypatch, capsys):
    _fake(monkeypatch, BEHIND_3)
    monkeypatch.setattr(sys, "argv", ["compile_dag.py", "--print"])
    cd.main()
    out = capsys.readouterr()
    assert "3 COMMITS BEHIND origin/main" in out.err
    # and NOT in the rendered block: the README's DAG section deliberately
    # carries no local git state, or --check could never pass on main.
    assert "COMMITS BEHIND" not in out.out


def test_compile_dag_is_quiet_on_a_current_tree(monkeypatch, capsys):
    _fake(monkeypatch, CURRENT)
    monkeypatch.setattr(sys, "argv", ["compile_dag.py", "--print"])
    cd.main()
    out = capsys.readouterr()
    assert "COMMITS BEHIND" not in out.out + out.err


def test_compile_dag_still_runs_when_git_cannot_answer(monkeypatch, capsys):
    _fake(monkeypatch, {}, raise_on="rev-list")
    monkeypatch.setattr(sys, "argv", ["compile_dag.py", "--print"])
    assert cd.main() == 0
    out = capsys.readouterr()
    assert "COMMITS BEHIND" not in out.out
    assert "DAG" in out.out or "graph" in out.out.lower()   # it still rendered


# ---------------------------------------------------------------- the rule
#
# The fourth stale read in #155's session was a fact about `main` read from a
# working tree. CLAUDE.md now states the rule; this asserts the statement is
# there, because a rule nothing reads is the gap this issue is about.


def _flat(p: pathlib.Path) -> str:
    """Whitespace-collapsed, so a reflowed paragraph is not a failed assertion."""
    return " ".join(p.read_text().split())


def test_claude_md_states_that_a_fact_about_main_is_read_from_origin_main():
    text = _flat(ROOT / "CLAUDE.md")
    assert "git show origin/main:" in text
    assert "never from the working tree" in text


def test_claude_md_states_the_search_before_closing_rule_and_that_it_is_unenforced():
    text = _flat(ROOT / "CLAUDE.md")
    assert "Closing an issue as fixed includes searching for the same shape" in text
    assert "#134" in text
    # Stated as unenforced ON PURPOSE: a process rule presented as if tooling
    # backed it is the shape this repository keeps mistaking for coverage.
    assert "Nothing enforces this mechanically" in text


def test_verification_rules_states_the_defeating_input_rule():
    text = _flat(ROOT / "docs" / "verification-rules.md")
    assert "## 8. A guard ships with the input that defeats it" in text
    # ... and names its relationship to rule 5, so the two are not read as one.
    assert "not rule 5 restated" in text.split("## 8.")[1]


def test_the_two_tools_wired_up_here_are_the_ones_the_dag_claims(monkeypatch):
    """A cheap guard against the reverse of #155: the import quietly going away.

    `scorecard` being importable from `compile_dag` is the whole mechanism; if
    someone deletes the import to break a cycle, these tests would still pass
    through the module-level `import scorecard as sc` in THIS file.
    """
    assert cd.sc is sc
    assert r1.sc is sc


def test_r1_scorecard_does_not_write_the_banner_into_the_committed_view(monkeypatch):
    """The banner must never reach OUT, or --check becomes unsatisfiable."""
    _fake(monkeypatch, BEHIND_3)
    text = r1.render(json.loads(r1.CANDIDATE.read_text()),
                     json.loads(r1.SUMMARY.read_text()))
    assert "COMMITS BEHIND" not in text
