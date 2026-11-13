"""The mutation controls in `bd_pitch_predeclaration_mutants` still aim at code.

Issue #755.  The script itself REFUSES (exit 2) when a mutant's `old` text is
not found exactly once -- correctly, but only when something runs it, and for
about four weeks nothing did: commit b6590cb7 moved the local `_finite` helper
into the shared guard (tools/finite_number.py, #718), `bool-accepted-as-number`
matched nothing, and the precheck returned before any of the 12 mutants ran.
The repair is two-tiered, and this file is the FAST tier:

  * `make controls` runs the script (12 focused pytest runs, ~5 s) -- the tier
    that proves the controls actually go RED;
  * this file re-derives the same precheck statically, in milliseconds, so it
    runs under `pytest tools/` -- `make verify` and the CI python job.  The PR
    that moves a guard is then red on the PR that moves it, not four weeks on.

What satisfies these checks while violating their intent (rule 8): a mutant
whose `old` is retargeted at some text that happens to occur exactly once but
removes no guard (a comment, a docstring line) passes here and would be
reported NOT RED by the script -- so this file is a staleness check, never
evidence that a mutant is a control.  Only the script's own run is that.
A `-k` expression that selects no test is likewise invisible here (pytest would
exit 5, which the script reports as NOT RED).
"""
from __future__ import annotations

import ast
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import bd_pitch_predeclaration_mutants as m  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[1]
NAMES = sorted(m.MUTANTS)


@pytest.mark.parametrize("name", NAMES)
def test_mutant_targets_still_match_exactly_once(name):
    """The precheck at bd_pitch_predeclaration_mutants.py's own rule-8 gate,
    read off the real tree instead of a scratch copy."""
    rel, old, _new, _k = m.MUTANTS[name]
    src = (ROOT / rel).read_text()
    assert src.count(old) == 1, (
        f"mutant {name}: target text found {src.count(old)} times in {rel} -- "
        f"the mutant no longer removes a guard, so `make controls` REFUSES")


@pytest.mark.parametrize("name", NAMES)
def test_mutant_changes_the_source_and_leaves_it_parseable(name):
    """A mutant that edits nothing cannot redden anything, and one that breaks
    the syntax reddens its control for the wrong reason (a collection error,
    not the removed guard)."""
    rel, old, new, _k = m.MUTANTS[name]
    assert old != new, f"mutant {name}: `new` is `old`, so it removes no guard"
    mutated = (ROOT / rel).read_text().replace(old, new)
    ast.parse(mutated, filename=f"<{name} applied to {rel}>")


def test_every_mutated_file_is_restored_between_mutants():
    """`main()` restores `FILES` before each mutant; a mutated file missing from
    it would stay mutated for every mutant after it, so the set is derived from
    MUTANTS rather than listed twice.  This asserts the derivation holds."""
    assert set(m.FILES) == {rel for rel, *_ in m.MUTANTS.values()}
    for rel in m.FILES:
        assert (ROOT / rel).is_file(), f"{rel} is mutated but does not exist"


def test_the_focused_test_file_exists():
    """Every mutant runs `pytest TEST -k <expr>`; a missing TEST would exit 4,
    reported as NOT RED for a reason that is not a missing guard."""
    assert (ROOT / m.TEST).is_file()
