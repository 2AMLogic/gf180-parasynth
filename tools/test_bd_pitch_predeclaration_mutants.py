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
exit 5, which the script's baseline gate REFUSES).

The last test here is the exception to "pure static": it runs the script end
to end under an interpreter whose `pytest` exits non-zero.  That path cannot
be checked statically -- the defect it covers was the script answering `all 12
mutants red`, exit 0, with no test executed -- and a guard with no input that
defeats it is rule 8's failure.  It stays fast (both cases 0.8 s measured)
because the refusal comes before the 12 mutant runs; the scratch copytree is
the bulk of what remains.
"""
from __future__ import annotations

import ast
import os
import pathlib
import subprocess
import sys
import tempfile

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import bd_pitch_predeclaration_mutants as m  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPT = pathlib.Path(m.__file__).resolve()
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


# pytest's own exit codes for the two ways a run can be non-zero without any
# guard having been removed: 1 is "the module is missing" (the #757 host) and,
# equally, "the unmutated baseline already fails"; 5 is "-k selected no test".
@pytest.mark.parametrize("stub_exit", [1, 5])
def test_script_refuses_when_the_baseline_cannot_be_trusted(stub_exit):
    """Rule 8, the input that defeats the RED verdict.

    `red = r.returncode == 1` counted ANY exit 1 as a reddened control, so on a
    worker whose system python3 has no pytest the script printed `all 12
    mutants red` and exited 0 having run nothing -- the state CLAUDE.md asks to
    be REFUSED rather than reported.  A stub `pytest` module on PYTHONPATH
    reproduces that exactly, and the baseline gate must refuse BEFORE any
    per-mutant verdict line is printed.
    """
    with tempfile.TemporaryDirectory(prefix="bpp-stub-pytest-") as tmp:
        # shadows the real pytest for the script's child process only
        (pathlib.Path(tmp) / "pytest.py").write_text(f"import sys\nsys.exit({stub_exit})\n")
        env = dict(os.environ, PYTHONPATH=tmp, PYTHONDONTWRITEBYTECODE="1")
        r = subprocess.run([sys.executable, str(SCRIPT)],
                           capture_output=True, text=True, env=env)

    assert r.returncode == 2, (
        f"stub pytest exiting {stub_exit} should REFUSE (exit 2), got exit "
        f"{r.returncode}:\n{r.stdout}{r.stderr}")
    lines = [ln for ln in r.stdout.splitlines() if ln.strip()]
    assert lines and lines[0].startswith("REFUSED:"), (
        f"the refusal must come before any verdict, got:\n{r.stdout}")
    assert "mutants red" not in r.stdout, (
        f"no mutant verdict may be printed when nothing ran:\n{r.stdout}")
