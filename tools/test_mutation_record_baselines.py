"""The two unwired mutation records (#1130): each refuses a red baseline, and
the sound one attributes its reds (#1400).

Each script is copied with its mutation target into an isolated tree whose
suite is a caller-written fixture, so the scripts never mutate the live tree.
Rule-8 input for the baseline: `def test_x(): assert 0` must give exit 2, not
KILLED/RED.

Issue #1400 is what happens AFTER a green baseline. A red is not a kill unless
it is the intended tests' red (docs/verification-rules.md 5, third condition),
and the sound probe read ANY nonzero pytest status as `RED (good)`: a suite
that raised `ImportError` at collection under every mutant printed three kills
and exited 0, no mutant having reached an assertion. The sound fixtures below
are GREEN on the unmutated target and do one specific thing under a mutant --
the only state in which the question can be asked -- and each must be REFUSED
(exit 2, the NO-VERDICT channel) with no kill credit: a collection error (the
shape the issue reproduced), an ERROR-only run, and a red outside the recorded
names (#1136). A silent snare fails an apparatus check in the expected test.
The healthy case runs the real sound suite against each production mutant,
and a mix follows the holdout probe's precedence (#1113): a survivor is a
finding about the tree and outranks a refusal, so it exits 1.
"""
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]

sys.path.insert(0, str(REPO / "model"))
import probe_injection_discrimination as sound  # noqa: E402

# (script, mutation target, suite path, passing-suite exit code, verdict marker)
CASES = {
    "fpga": ("fpga/testdata/mutate_pnr_report.py", "fpga/scripts/pnr_report.py",
             "fpga/test_pnr_report.py", "KILLED"),
    "sound": ("model/probe_injection_discrimination.py", "model/sound_report.py",
              "model/test_sound_report.py", "RED (good)"),
}

#: What a script needs beside its target to run alone. The sound probe does
#: not decide its own verdicts: it imports the holdout probe's (#1400).
DEPENDENCIES = {"sound": ("tools/probes/holdout_injected_controls.py",
                          "tools/probes/pytest_failure_sites.py")}


def stage(tmp, key, suite_src):
    script, target, suite, _ = CASES[key]
    for rel in (script, target, *DEPENDENCIES.get(key, ())):
        d = tmp / rel
        d.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(REPO / rel, d)
    (tmp / suite).write_text(suite_src)
    return tmp / script, tmp / target


def run(script, tmp, env=None):
    return subprocess.run([sys.executable, str(script)], cwd=tmp, env=env,
                          capture_output=True, text=True)


@pytest.mark.parametrize("key", CASES)
def test_red_baseline_refuses(tmp_path, key):
    script, target = stage(tmp_path, key, "def test_x():\n    assert 0\n")
    before = target.read_bytes()
    r = run(script, tmp_path)
    out = r.stdout + r.stderr
    assert r.returncode == 2, out
    assert "REFUSED" in out and "baseline" in out
    assert CASES[key][3] not in out and "survived" not in out.lower()
    assert target.read_bytes() == before


@pytest.mark.parametrize("key", CASES)
def test_green_baseline_reaches_mutants(tmp_path, key):
    script, target = stage(tmp_path, key, "def test_x():\n    assert 1\n")
    before = target.read_bytes()
    r = run(script, tmp_path)
    out = r.stdout + r.stderr
    # Insensitive suite: every mutant survives -> exit 1, verdicts printed.
    assert r.returncode == 1, out
    assert "baseline" not in out and "REFUSED:" not in out and "probe REFUSED" not in out
    assert "SURVIVED" in out
    assert target.read_bytes() == before


# ---- issue #1400: what the sound probe may call a kill ---------------------

#: Every sound fixture can tell a mutant when it sees one. The probe mutates
#: `sound_report.py` IN PLACE, so the suite reads that file and compares it
#: with the pristine copy `stage_sound()` leaves beside it.
READS_TARGET = (
    "import pathlib\n"
    "HERE = pathlib.Path(__file__).parent\n"
    "SRC = (HERE / 'sound_report.py').read_text()\n"
    "PRISTINE = (HERE / 'sound_report.original').read_text()\n")

#: Collects, and runs its one test, on the unmutated target; uncollectable
#: under any mutant. The test leaves a marker, so a refusal can be told from a
#: baseline that never ran.
COLLECTION_ERROR = (READS_TARGET
                    + "if SRC != PRISTINE:\n"
                      "    raise ImportError('injected collection failure')\n"
                      "def test_fixture():\n"
                      "    pathlib.Path('baseline-executed').touch()\n")

#: Green on the unmutated target; under any mutant every test errors in setup:
#: pytest exits 1 and writes no FAILED line to parse.
ERROR_ONLY = (READS_TARGET
              + "import pytest\n\n\n"
                "@pytest.fixture(autouse=True)\n"
                "def boom():\n"
                "    if SRC != PRISTINE:\n"
                "        raise RuntimeError('apparatus control')\n\n\n"
                "def test_a():\n"
                "    assert 1\n")

#: Green on the unmutated target; under any mutant one test fails, and its
#: name is in no recorded set: a red the injection cannot be credited with.
UNRELATED = (READS_TARGET
             + "def test_unrelated_to_every_recorded_name():\n"
               "    assert SRC == PRISTINE\n")

#: Green whatever is injected: the clean survivor.
INSENSITIVE = "def test_insensitive():\n    assert 1\n"


def stage_sound(tmp, suite_src):
    """`stage()` for the sound probe, plus the pristine copy its fixtures
    compare the mutated target against."""
    script, target = stage(tmp, "sound", suite_src)
    target.with_suffix(".original").write_bytes(target.read_bytes())
    return script, target


def _uncollectable_under(mutant, body):
    """`body` as the suite, plus an ImportError at collection under `mutant`
    ONLY: the other mutants still run it, so one probe run holds a refusal
    and whatever `body` makes of the rest."""
    old, _new = sound.MUTANTS[mutant]
    return (READS_TARGET
            + f"if {old!r} not in SRC:\n"
              "    raise ImportError('uncollectable under this mutant only')\n"
            + body)


def _line(out, text):
    """The one output line mentioning `text`."""
    hits = [ln for ln in out.splitlines() if text in ln]
    assert len(hits) == 1, out
    return hits[0]


def _no_kill_credit(out):
    assert "RED (good)" not in out and "killed" not in out, out


def test_sound_baseline_with_failures_and_zero_exit_stops_before_mutation(tmp_path):
    # These three assertions fail even on the unmutated source. Accepting its
    # forced zero exit would let the same failures earn every mutant a kill.
    suite = READS_TARGET + """
import pytest

def test_the_moving_average_injection_ripples_at_the_carrier_it_cannot_resolve():
    clean_db = broken_db = 0
    assert broken_db > clean_db + 3.0

def test_the_centroid_injection_switches_which_field_the_brightness_property_reads():
    power = amplitude = 1
    assert amplitude > 2.0 * power

def test_the_lock_changelog_agrees_with_the_live_table():
    problems = ['broken baseline']
    assert problems == []

def test_stop_after_results():
    if SRC == PRISTINE:
        pytest.exit('end of baseline', returncode=0)
    pathlib.Path('mutant-executed').touch()
"""
    script, target = stage_sound(tmp_path, suite)
    before = target.read_bytes()
    result = run(script, tmp_path)
    out = result.stdout + result.stderr
    assert result.returncode == 2, out
    assert "REFUSED" in out and "baseline" in out and "3 failed" in out, out
    _no_kill_credit(out)
    assert not (tmp_path / "mutant-executed").exists()
    assert target.read_bytes() == before


def test_sound_baseline_with_setup_errors_and_zero_exit_stops_before_mutation(tmp_path):
    """Reproduce the review control: setup errors on the unmutated baseline
    followed by `pytest.exit(..., returncode=0)` must REFUSE before any mutant
    runs, never pass the baseline and award all three kills."""
    suite = READS_TARGET + """
import pytest

@pytest.fixture
def apparatus():
    if SRC == PRISTINE:
        raise RuntimeError('apparatus unavailable')

def test_the_moving_average_injection_ripples_at_the_carrier_it_cannot_resolve(apparatus):
    clean_db = broken_db = 0
    assert broken_db > clean_db + 3.0

def test_the_centroid_injection_switches_which_field_the_brightness_property_reads(apparatus):
    power = amplitude = 1
    assert amplitude > 2.0 * power

def test_the_lock_changelog_agrees_with_the_live_table(apparatus):
    problems = ['broken baseline']
    assert problems == []

def test_stop_after_results():
    if SRC == PRISTINE:
        pytest.exit('end of baseline', returncode=0)
    pathlib.Path('mutant-executed').touch()
"""
    script, target = stage_sound(tmp_path, suite)
    before = target.read_bytes()
    result = run(script, tmp_path)
    out = result.stdout + result.stderr
    assert result.returncode == 2, out
    assert "REFUSED" in out and "baseline" in out and "phase error" in out, out
    assert "setup:" in out and "3 errors" in out, out
    _no_kill_credit(out)
    assert not (tmp_path / "mutant-executed").exists()
    assert target.read_bytes() == before


def test_sound_collect_only_refuses_instead_of_reporting_survivors(tmp_path):
    """PYTEST_ADDOPTS=--collect-only collects the unmodified sound suite without
    executing any test; the probe must REFUSE (exit 2) before mutating, never
    report survivors (exit 1)."""
    script, target = stage_real_sound(tmp_path)
    before = target.read_bytes()
    env = dict(os.environ, PYTEST_ADDOPTS="--collect-only")
    result = run(script, tmp_path, env=env)
    out = result.stdout + result.stderr
    assert result.returncode == 2, out
    assert "REFUSED" in out and "baseline" in out and "executed no tests" in out, out
    assert "SURVIVED" not in out
    _no_kill_credit(out)
    assert target.read_bytes() == before


def test_sound_mutant_collection_error_refuses(tmp_path):
    """The issue's reproduction, as it ran on `origin/main` @ `4b4c4935`: the
    baseline test executes, then every mutant is uncollectable, and the probe
    printed `RED (good)` beside `1 error` three times and exited 0."""
    script, target = stage_sound(tmp_path, COLLECTION_ERROR)
    before = target.read_bytes()
    result = run(script, tmp_path)
    out = result.stdout + result.stderr
    assert (tmp_path / "baseline-executed").exists()
    assert target.read_bytes() == before
    assert result.returncode == 2, out
    _no_kill_credit(out)
    assert "SURVIVED" not in out
    assert "pytest exited 2" in out            # the status is named


def test_sound_mutant_error_only_run_refuses(tmp_path):
    script, target = stage_sound(tmp_path, ERROR_ONLY)
    before = target.read_bytes()
    result = run(script, tmp_path)
    out = result.stdout + result.stderr
    assert result.returncode == 2, out
    _no_kill_credit(out)
    assert "SURVIVED" not in out
    assert "pytest exited 1" in out
    # The suite that ran is named: this probe's, not the holdout probe's whose
    # decision it borrows.
    assert "no call failure was recorded for model/test_sound_report.py" in out
    assert "tools/test_holdout.py" not in out
    assert target.read_bytes() == before


def test_sound_mutant_failure_outside_the_recorded_names_refuses(tmp_path):
    """The third condition, not the second: a red is the injection's only if
    an intended test went red. One that falls entirely elsewhere -- a flaky
    test, a defect that arose after the baseline -- is refused, expected and
    parsed names both shown. Before #1400 this printed `RED (good)`."""
    script, target = stage_sound(tmp_path, UNRELATED)
    before = target.read_bytes()
    result = run(script, tmp_path)
    out = result.stdout + result.stderr
    assert result.returncode == 2, out
    _no_kill_credit(out)
    assert "SURVIVED" not in out
    for mutant in sound.MUTANTS:
        assert _line(out, mutant).startswith("REFUSED"), out
    assert "test_unrelated_to_every_recorded_name" in out       # parsed, named
    for names in sound.EXPECTED.values():
        for name in names:
            assert name in out                                  # expected, named
    assert target.read_bytes() == before


def stage_real_sound(tmp_path):
    """Copy the suite and the file the sound probe edits into a private tree."""
    script = tmp_path / "model/probe_injection_discrimination.py"
    target = tmp_path / "model/sound_report.py"
    shutil.copytree(REPO / "model", tmp_path / "model",
                    ignore=shutil.ignore_patterns("__pycache__"))
    (tmp_path / "audition").symlink_to(REPO / "audition", target_is_directory=True)
    (tmp_path / "tools").symlink_to(REPO / "tools", target_is_directory=True)
    return script, target


def test_sound_intended_assertions_still_get_kill_credit(tmp_path):
    """The other half: attribution must not refuse everything. With the real
    mutant sites and the real recorded names, each mutant is killed, the tests
    that caught it are named, and the probe exits 0."""
    script, target = stage_real_sound(tmp_path)
    before = target.read_bytes()
    result = run(script, tmp_path)
    out = result.stdout + result.stderr
    assert result.returncode == 0, out
    assert f"all {len(sound.MUTANTS)} mutants killed." in out
    assert out.count("RED (good)") == len(sound.MUTANTS)
    assert "REFUSED" not in out and "SURVIVED" not in out
    for names in sound.EXPECTED.values():
        for name in names:
            assert f"    {name}" in out.splitlines(), out
    assert target.read_bytes() == before


def test_silent_snare_under_centroid_mutant_gets_no_kill_credit(tmp_path):
    """Reproduce the review control through the real probe and sound tests."""
    script, target = stage_real_sound(tmp_path)
    (tmp_path / "model" / "conftest.py").write_text(
        "from pathlib import Path\n"
        "import pytest\n\n"
        "@pytest.fixture(autouse=True)\n"
        "def silent_snare(monkeypatch):\n"
        "    import sound_report as sr\n"
        "    if 'ctx[\"centroid_weight\"] = weight' not in Path(sr.__file__).read_text():\n"
        "        monkeypatch.setattr(sr, 'm_sd_brightness', lambda ctx: None)\n")
    before = target.read_bytes()
    result = run(script, tmp_path)
    out = result.stdout + result.stderr
    assert target.read_bytes() == before
    assert result.returncode == 2, out
    assert _line(out, "centroid injection sets nothing").startswith("REFUSED"), out
    assert "the snare rendered silent" in out
    assert _line(out, "envelope patch is a pass-through").startswith("RED (good)"), out
    assert _line(out, "brightness lock removed").startswith("RED (good)"), out
    assert "mutants killed" not in out


def test_sound_survivor_outranks_a_refusal_exits_one(tmp_path):
    """The holdout probe's precedence (#1113), so the two probes read alike: a
    mutant that survived is a measured hole in the suite and is not hidden
    behind a refusal that measured nothing. Both are named."""
    script, target = stage_sound(
        tmp_path, _uncollectable_under("centroid injection sets nothing", INSENSITIVE))
    before = target.read_bytes()
    result = run(script, tmp_path)
    out = result.stdout + result.stderr
    assert result.returncode == 1, out
    assert out.count("SURVIVED") == len(sound.MUTANTS) - 1, out
    assert _line(out, "centroid injection sets nothing").startswith("REFUSED"), out
    assert "1 refused" in out
    _no_kill_credit(out)
    assert target.read_bytes() == before


@pytest.mark.parametrize("cause", ["collection_error", "failed_then_exit_zero"])
def test_sound_fired_alongside_a_refusal_exits_two(tmp_path, cause):
    """Nothing survived and not everything was measured: exit 2, not 0, and
    the two genuine kills are still printed as kills."""
    script, target = stage_real_sound(tmp_path)
    target.with_suffix(".original").write_bytes(target.read_bytes())
    suite = tmp_path / sound.TESTS
    if cause == "collection_error":
        extra = _uncollectable_under("centroid injection sets nothing", "")
    else:
        # The real snare assertion has failed before pytest is told to exit 0.
        extra = """
def test_stop_after_centroid_results():
    import pytest
    from pathlib import Path
    if 'ctx["centroid_weight"] = weight' not in Path(sr.__file__).read_text():
        pytest.exit('end of measurement', returncode=0)
"""
    suite.write_text(suite.read_text() + "\n" + extra)
    before = target.read_bytes()
    result = run(script, tmp_path)
    out = result.stdout + result.stderr
    assert result.returncode == 2, out
    assert out.count("RED (good)") == len(sound.MUTANTS) - 1, out
    refused = _line(out, "centroid injection sets nothing")
    assert refused.startswith("REFUSED"), out
    if cause == "failed_then_exit_zero":
        assert "pytest exited 0" in refused, out
        assert "test_the_centroid_injection_switches_which_field_the_brightness_property_reads" in refused
    assert "SURVIVED" not in out and "mutants killed" not in out
    assert target.read_bytes() == before


def test_sound_mutant_setup_error_and_zero_exit_refuses(tmp_path):
    """After a clean baseline, a mutant that hits a setup error and exits 0
    with zero call failures must REFUSE rather than report SURVIVED."""
    suite = READS_TARGET + """
import pytest

@pytest.fixture
def apparatus():
    if SRC != PRISTINE:
        raise RuntimeError('apparatus unavailable')

def test_a(apparatus):
    assert 1

def test_stop():
    if SRC != PRISTINE:
        pytest.exit('end of mutant', returncode=0)
"""
    script, target = stage_sound(tmp_path, suite)
    before = target.read_bytes()
    result = run(script, tmp_path)
    out = result.stdout + result.stderr
    assert result.returncode == 2, out
    assert "SURVIVED" not in out
    _no_kill_credit(out)
    for mutant in sound.MUTANTS:
        line = _line(out, mutant)
        assert line.startswith("REFUSED") and "phase error" in line and "setup: test_a" in line, out
    assert target.read_bytes() == before


def test_every_sound_mutant_has_recorded_names_that_exist_in_the_real_suite():
    """`EXPECTED` is what a kill is attributed to. A mutant with no record
    refuses every run (#1136), and so does a recorded name the suite no longer
    defines: an unsatisfiable gate, which is worse than none. So the record is
    checked against the suite it names, without running that suite."""
    suite = (REPO / sound.TESTS).read_text()
    assert set(sound.EXPECTED) == set(sound.MUTANTS)
    for mutant, names in sound.EXPECTED.items():
        assert names, mutant
        for name in names:
            assert f"def {name}(" in suite, (mutant, name)
