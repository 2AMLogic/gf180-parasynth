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

Issue #1463 adds a second exception.  The script decided each verdict from
pytest's exit status alone, so a focused run that exited 1 for ANY reason --
an unrelated test, another assertion in the right test, a setup error, a run
that left no report -- was counted RED (#1400's shape, found in #1401's class
search).  Two CLI tests stage a tree the script takes for the
repository and give each of those pathologies its own mutant, so one run
(~4 s) holds all of them beside the summary that must rank them: every
red refused exits 2; a clean survivor is printed SURVIVED and exits 1 over
any refusal.  They execute the STAGED script -- never this checkout's, whose
ROOT is the real tree and would run the real suite instead of the fixture.
A third runs the genuine suite with a diagnostic-only failure injected into
the negative-spread case. Only the invocation with readings can earn its RED.
"""
from __future__ import annotations

import ast
import os
import pathlib
import shutil
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


# ---- issue #1463: what a red may be credited to, through the CLI ------------
#
# `red = r.returncode == 1` credited any focused exit 1.  The fixtures below
# stage a tree the script takes for the repository -- itself, the three sources
# it mutates with a pristine `.clean` copy beside each, the shared verdict, and
# a suite at TEST with one test per `-k` selection -- and give each pathology
# its own mutant, so one run holds all of them and the summary is checked with
# them present.  The default test below is red under its mutants at an
# assertion no mutant records; a pathology replaces it for one selection.

#: The fixture suite's preamble: it can tell a mutant when it sees one.
READS_SOURCES = (
    "import pathlib\n"
    "import pytest\n"
    "HERE = pathlib.Path('tools')\n"
    "SOURCES = ('bd_pitch_predeclaration.py', 'bd_glide_phase_sweep.py', 'finite_number.py')\n"
    "def _mutated():\n"
    "    return [s for s in SOURCES\n"
    "            if (HERE / s).read_bytes() != (HERE / s).with_suffix('.clean').read_bytes()]\n"
    "\n\n")

_UNATTRIBUTED = {k: f"def test_{k}():\n"
                    "    assert not _mutated(), 'a different assertion in the right test'\n"
                 for k in m.SELECTIONS}

#: The recorded statement, passing; a different assertion in the same test
#: goes red under the mutant.
_WRONG_ASSERTION = '''def test_satisfiable_at_equality():
    at = {"satisfiable": True}
    assert at["satisfiable"] is True, "pass is improvement >= minimum: equality satisfies it"
    assert not _mutated(), "a different assertion in the recorded test"
'''

#: Red under the mutant, in a test no mutant records.
_UNRELATED = '''def test_derivation_unrelated_to_every_recorded_name():
    assert not _mutated(), "an unrelated test went red"
'''

#: Green whatever is injected, except that its setup breaks under a mutant.
_SETUP_ERROR = '''@pytest.fixture
def apparatus():
    if _mutated():
        raise RuntimeError("the apparatus broke under injection")

def test_lost_tone_holdout_refuses(apparatus):
    assert True
'''

#: Green whatever is injected: the clean survivor.
_SURVIVOR = '''def test_control_answering_knob0_reading_is_excluded():
    got, rd, D00 = {"measured": []}, {}, ()
    assert sorted(got["measured"]) == sorted(set(rd) - set(D00)), got["measured"]
'''

#: Prepended to the staged reporter: under the one mutant that edits the sweep
#: file the process dies without writing its report.
_NO_REPORT = (
    "import pathlib\n"
    "import sys\n"
    "if (pathlib.Path('tools/bd_glide_phase_sweep.py').read_bytes()\n"
    "        != pathlib.Path('tools/bd_glide_phase_sweep.clean').read_bytes()):\n"
    "    sys.exit(1)\n")


def _stage(tmp, no_report=False, **pathologies):
    assert set(pathologies) <= set(m.SELECTIONS), (
        f"no mutant is selected by {sorted(set(pathologies) - set(m.SELECTIONS))}")
    suite = READS_SOURCES + "\n\n".join({**_UNATTRIBUTED, **pathologies}[k] for k in m.SELECTIONS)
    (tmp / m.TEST).parent.mkdir(parents=True)
    (tmp / m.TEST).write_text(suite)
    for rel in (pathlib.Path("tools", SCRIPT.name), *map(pathlib.Path, m.FILES),
                pathlib.Path("tools/probes/holdout_injected_controls.py"),
                pathlib.Path("tools/probes/pytest_failure_sites.py")):
        shutil.copy(SCRIPT if rel.name == SCRIPT.name else ROOT / rel, _mkdir(tmp, rel))
        if no_report and rel.name == "pytest_failure_sites.py":
            staged = tmp / rel
            head, sep, tail = staged.read_text().partition("from __future__ import annotations\n")
            staged.write_text(head + sep + _NO_REPORT + tail)
    for rel in m.FILES:
        shutil.copy(ROOT / rel, _mkdir(tmp, pathlib.Path(rel).with_suffix(".clean")))


def _mkdir(tmp, rel):
    (tmp / rel).parent.mkdir(parents=True, exist_ok=True)
    return tmp / rel


def _run(tree):
    """Run the CLI from the tree containing the suite we want to exercise."""
    r = subprocess.run([sys.executable, str(tree / "tools" / SCRIPT.name)],
                       cwd=tree, capture_output=True, text=True)
    return r.returncode, r.stdout + r.stderr


def _verdict(out, name):
    """The text of `name`'s one line after the name: the verdict and reason."""
    hits = [ln for ln in out.splitlines() if ln.startswith(name)]
    assert len(hits) == 1, out
    return hits[0][len(name):].strip()


def _staged_tree_untouched(tmp, before):
    assert all((tmp / rel).read_bytes() == src for rel, src in before.items())


def test_cli_refuses_every_red_it_cannot_attribute(tmp_path):
    """The issue's reproduction through the CLI: on `origin/main` a fixture
    whose every test compared the sources with their pristine copies was
    credited `all 12 mutants red`, exit 0, having exercised no guard.  Each
    pathology gets its own mutant here -- a different assertion in the
    recorded test, a red outside the recorded names, a setup error, and a run
    that left no report -- and none may earn credit on its own."""
    _stage(tmp_path, no_report=True, equality=_WRONG_ASSERTION, derivation=_UNRELATED,
           tone_holdout=_SETUP_ERROR)
    before = {rel: (tmp_path / rel).read_bytes() for rel in m.FILES}
    code, out = _run(tmp_path)
    assert code == 2, out
    for name in NAMES:
        assert _verdict(out, name).startswith("REFUSED"), out
    assert "SURVIVED" not in out and "mutants red" not in out
    line = _verdict(out, "strict-at-equality")
    assert "no intended assertion failed" in line and "test_satisfiable_at_equality" in line, out
    line = _verdict(out, "min-count-derivation-unchecked")
    assert "no intended assertion failed" in line, out
    assert "test_derivation_unrelated_to_every_recorded_name" in line, out
    assert "test_control_min_measured_lowered_below_its_derivation_is_caught" in line, out
    assert "no call failure was recorded" in _verdict(out, "tone-holdout-removed"), out
    assert "setup:" in out
    assert "without a failure report" in _verdict(out, "coarse-5hz-pitch-grid"), out
    _staged_tree_untouched(tmp_path, before)


def test_cli_reports_a_clean_survivor_apart_from_refusals(tmp_path):
    """The distinct case: a test that executes, completes and stays green
    under the mutant is a survivor, printed as such and exiting 1 -- also
    when other mutants refused, because a survivor is a finding about the
    controls and a refusal is a finding about the apparatus."""
    _stage(tmp_path, knob0=_SURVIVOR)
    before = {rel: (tmp_path / rel).read_bytes() for rel in m.FILES}
    code, out = _run(tmp_path)
    assert code == 1, out
    assert _verdict(out, "unqualified-knob-admitted").startswith("SURVIVED"), out
    for name in NAMES:
        if name != "unqualified-knob-admitted":
            assert _verdict(out, name).startswith("REFUSED"), out
    assert "mutants red" not in out
    _staged_tree_untouched(tmp_path, before)


def test_cli_refuses_negative_spread_when_only_the_diagnostic_fails(tmp_path, monkeypatch):
    """Both invocations refuse. Only the diagnostic loses its refusal message."""
    guard = m.MUTANTS["negative-spread"][1]
    (tmp_path / "diagnostic_only_failure.py").write_text(f'''
from pathlib import Path
import pytest

@pytest.fixture(autouse=True)
def diagnostic_only_failure(request, monkeypatch):
    if request.node.name != "test_cli_refuses_invalid_baseline_numbers[-5-100]":
        return
    if {guard!r} in Path(request.module.bp.__file__).read_text():
        return

    def refuse(tmp_path, baseline, readings, capsys):
        return 2, "diagnostic output lost" if readings is None else "REFUSED"

    monkeypatch.setattr(request.module, "_cli", refuse)
''')
    monkeypatch.setenv("PYTHONPATH", str(tmp_path), prepend=os.pathsep)
    monkeypatch.setenv("PYTEST_PLUGINS", "diagnostic_only_failure", prepend=",")
    code, out = _run(ROOT)
    assert code == 2, out
    line = _verdict(out, "negative-spread")
    assert line.startswith("REFUSED -- no intended assertion failed"), out
    assert "diagnostic output lost" in line, out
    for name in NAMES:
        if name != "negative-spread":
            assert _verdict(out, name).startswith("RED --"), out
    assert "mutants red" not in out, out


@pytest.mark.parametrize("name", NAMES)
def test_expected_sites_identify_one_statement_in_the_real_test(name):
    """The fast tier for EXPECTED: every recorded test function exists in TEST
    and its recorded statement matches exactly one line in it, by the same rule
    the reporter matches statements with.  Only `make controls` proves the
    statement is the one that fails; this is the staleness check."""
    src = (ROOT / m.TEST).read_text()
    functions = {n.name: n for n in ast.parse(src).body if isinstance(n, ast.FunctionDef)}
    lines = src.splitlines()
    assert m.EXPECTED.get(name), f"mutant {name} records no site, so its red cannot be credited"
    for test, site in m.EXPECTED[name].items():
        fn = functions[test.partition("[")[0]]
        body = [ln.strip() for ln in lines[fn.lineno - 1:fn.end_lineno]]
        matches = [ln for ln in body if ln == site.statement or ln.startswith(site.statement + ",")]
        assert len(matches) == 1, (
            f"{name}: {test} has {len(matches)} matches for {site.statement!r}; expected one")


_OUTCOME = {"red": "fired", "survived": "no-fire", "refused": "refused"}


@pytest.mark.parametrize("outcomes, code, all_red", [
    (["red"] * 12, 0, True),
    (["red"] * 11 + ["survived"], 1, False),
    (["red"] * 11 + ["refused"], 2, False),
    (["survived"] + ["refused"] * 11, 1, False),
    (["survived", "refused"] + ["red"] * 10, 1, False),
    (["refused"] * 12, 2, False),
    (["survived"] * 12, 1, False),
])
def test_only_twelve_attributed_reds_print_the_all_red_summary(capsys, outcomes, code, all_red):
    """The aggregate convention the sound and holdout probes settled on: 0
    only when every mutant is red at a recorded assertion, 1 when any
    survived (a mix included), otherwise 2 when any refused."""
    results = {name: m.hic.Result(_OUTCOME[o], [next(iter(m.EXPECTED[name]))] if o == "red" else [],
                                  "why")
               for name, o in zip(NAMES, outcomes)}
    assert m.report(results) == code
    out = capsys.readouterr().out
    assert ("all 12 mutants red" in out) is all_red, out
    assert out.count(" RED -- ") == outcomes.count("red")
    assert out.count(" SURVIVED -- ") == outcomes.count("survived")
    assert out.count(" REFUSED -- ") == outcomes.count("refused")
    # The evidence a reader checks: every credited test and its recorded site.
    for name, o in zip(NAMES, outcomes):
        assert (f"    {next(iter(m.EXPECTED[name]))}" in out) is (o == "red"), out
