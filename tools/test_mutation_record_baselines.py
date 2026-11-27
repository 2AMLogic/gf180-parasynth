"""The two unwired mutation records (#1130): each refuses a red baseline, the
sound one attributes its reds (#1400), and so does the FPGA one (#1401).

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

Issue #1480 is what the sound probe leaves behind. It edits its target in
place, and its first mutant write sat outside the guard that restores the
file: a write that failed part-way left the reporter at its first 64 bytes,
and a run that completed restored a CRLF source as LF, because the original
was saved with `read_text()`. The two restoration cases drive the staged
probe and compare bytes: one with a partial first write injected after the
green baseline, one a completed run on a CRLF target.

Issue #1401 is the same defect one file over, in the FPGA record, which
printed `KILLED` for fourteen collection errors. Its fixtures follow the sound
ones. The headroom CLI test (#1474) supplies the assertion for the
missing-return-code mutant. The FPGA record is imported as a module for
its `M` and `EXPECTED`; before #1401
that import ran the whole mutation loop against the live tree.
"""
import functools
import os
import re
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

#: What a script needs beside its target to run alone. Neither probe decides
#: its own verdicts: both import the holdout probe's (#1400, #1401).
DEPENDENCIES = {
    "fpga": ("tools/probes/holdout_injected_controls.py",
             "tools/probes/pytest_failure_sites.py"),
    "sound": ("tools/probes/holdout_injected_controls.py",
              "tools/probes/pytest_failure_sites.py"),
}


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


FAILING_RECORDED_CHECKS = """
def test_the_moving_average_injection_ripples_at_the_carrier_it_cannot_resolve(apparatus):
    clean_db = broken_db = 0
    assert broken_db > clean_db + 3.0

def test_the_centroid_injection_switches_which_field_the_brightness_property_reads(apparatus):
    power = amplitude = 1
    assert amplitude > 2.0 * power

def test_the_lock_changelog_agrees_with_the_live_table(apparatus):
    problems = ['broken baseline']
    assert problems == []
"""


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

""" + FAILING_RECORDED_CHECKS + """

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


def test_sound_failures_skipped_on_the_baseline_cannot_earn_kills(tmp_path):
    suite = READS_TARGET + """
import pytest

@pytest.fixture
def apparatus():
    if SRC == PRISTINE and not (HERE / 'enable-checks').exists():
        pytest.skip('apparatus unavailable')

def test_housekeeping():
    assert True
""" + FAILING_RECORDED_CHECKS
    script, target = stage_sound(tmp_path, suite)
    before = target.read_bytes()
    result = run(script, tmp_path)
    out = result.stdout + result.stderr
    assert target.read_bytes() == before

    # Enable the very same assertions on the original target. Each is already red.
    (target.parent / "enable-checks").touch()
    pristine = subprocess.run([sys.executable, "-m", "pytest", "-q", sound.TESTS],
                              cwd=tmp_path, capture_output=True, text=True)
    assert pristine.returncode == 1 and "3 failed, 1 passed" in pristine.stdout, pristine.stdout
    assert result.returncode == 2, out
    for mutant in sound.MUTANTS:
        assert _line(out, mutant).startswith("REFUSED"), out
    assert "baseline" in out and "SURVIVED" not in out, out
    _no_kill_credit(out)


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


def _assert_sound_credits(out, expected):
    """Assert each sound mutant has its own `RED (good)` header and its own
    complete group of credited test IDs."""
    for mutant, names in expected.items():
        assert _verdict_line(out, mutant).startswith("RED (good)"), out
        credited = _credited_ids(out, mutant)
        assert sorted(credited) == sorted(names), (
            mutant, "missing", sorted(set(names) - set(credited)),
            "unexpected", sorted(set(credited) - set(names)))


def test_sound_shared_id_credit_is_checked_per_mutant_group():
    """Injected control (#1503): two sound mutants share `test_shared`, and the
    first mutant's credit line is suppressed while both headers, the summary,
    and the second mutant's credit stay intact. Whole-output search passes by
    borrowing the second mutant's line; per-group attribution must fail naming
    the first mutant and `test_shared` while the second keeps its credit."""
    first, second = list(sound.MUTANTS)[:2]
    expected = {
        first: {"test_shared": None},
        second: {"test_shared": None},
    }
    clean = (
        f"{'RED (good)':24s}  {first}: 1 test(s) caught it\n"
        "    test_shared\n"
        f"{'RED (good)':24s}  {second}: 1 test(s) caught it\n"
        "    test_shared\n"
        "\nall 2 mutants killed.\n"
    )
    _assert_sound_credits(clean, expected)

    missing_first = clean.replace("    test_shared\n", "", 1)
    assert missing_first.count("RED (good)") == 2
    assert "all 2 mutants killed." in missing_first
    assert all(f"    {name}" in missing_first.splitlines()
               for names in expected.values() for name in names)
    with pytest.raises(AssertionError) as exc:
        _assert_sound_credits(missing_first, expected)
    msg = str(exc.value)
    assert first in msg and "missing" in msg and "test_shared" in msg
    assert second not in msg
    assert _credited_ids(missing_first, second) == ["test_shared"]

    # Order-independent within a group, but rejects duplicates and group swaps.
    multi_expected = {
        first: {"t_a": None, "t_b": None},
        second: {"t_c": None},
    }
    reordered = (
        f"{'RED (good)':24s}  {first}: 2 test(s) caught it\n"
        "    t_b\n"
        "    t_a\n"
        f"{'RED (good)':24s}  {second}: 1 test(s) caught it\n"
        "    t_c\n"
    )
    _assert_sound_credits(reordered, multi_expected)

    duplicated = reordered.replace("    t_c\n", "    t_c\n    t_c\n")
    with pytest.raises(AssertionError) as dup_exc:
        _assert_sound_credits(duplicated, multi_expected)
    assert second in str(dup_exc.value)

    swapped = (
        f"{'RED (good)':24s}  {first}: 1 test(s) caught it\n"
        "    t_c\n"
        f"{'RED (good)':24s}  {second}: 2 test(s) caught it\n"
        "    t_a\n"
        "    t_b\n"
    )
    with pytest.raises(AssertionError) as swap_exc:
        _assert_sound_credits(swapped, multi_expected)
    swap_msg = str(swap_exc.value)
    assert first in swap_msg and "t_a" in swap_msg and "t_c" in swap_msg


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
    _assert_sound_credits(out, sound.EXPECTED)
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


def test_skipped_centroid_comparison_cannot_establish_survival(tmp_path):
    script, target = stage_real_sound(tmp_path)
    (target.parent / "conftest.py").write_text('''
from pathlib import Path
import pytest

@pytest.fixture(autouse=True)
def apparatus(request):
    centroid = "test_the_centroid_injection_switches_which_field_the_brightness_property_reads"
    if request.node.name == centroid and 'ctx["centroid_weight"] = weight' not in (
            Path(__file__).with_name("sound_report.py").read_text()):
        pytest.skip("centroid comparison unavailable")
''')
    result = run(script, tmp_path)
    out = result.stdout + result.stderr
    assert result.returncode == 2, out
    line = _line(out, "centroid injection sets nothing")
    assert line.startswith("REFUSED") and "test_the_centroid_injection" in line, out
    assert out.count("RED (good)") == 2 and "SURVIVED" not in out, out


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


# ---- issue #1480: the probe leaves the target as it found it ----------------

#: Green on the unmutated target, and leaves a marker naming which run it was:
#: the baseline's, or a mutant's.
RUN_MARKER = (READS_TARGET
              + "def test_green_on_pristine():\n"
              "    pathlib.Path('baseline-executed' if SRC == PRISTINE else 'mutant-executed').touch()\n"
              "    assert 1\n")

#: Runs the staged probe in-process with `Path.write_text` on its target
#: replaced: once the baseline's marker exists, the first write puts 64
#: characters on disk, records what it left, and raises `OSError`. Every later
#: write succeeds. An `OSError` leaving `main()` is reported as exit 4.
PARTIAL_WRITE_LAUNCHER = """
import pathlib
import sys

root, record = map(pathlib.Path, sys.argv[1:3])
target = (root / "model" / "sound_report.py").resolve()
sys.path.insert(0, str(root / "model"))
import probe_injection_discrimination as probe

real_write_text = pathlib.Path.write_text
armed = True


def write_text(self, data, *args, **kwargs):
    global armed
    if armed and self == target and (root / "baseline-executed").exists():
        armed = False
        real_write_text(self, data[:64])
        record.write_bytes(target.read_bytes())
        raise OSError("injected partial write")
    return real_write_text(self, data, *args, **kwargs)


pathlib.Path.write_text = write_text
try:
    code = probe.main()
except OSError as exc:
    print(f"OSError left main(): {exc}")
    raise SystemExit(4)
raise SystemExit(code)
"""


def _with_line_ending(target, line_ending):
    """Stage identical target and pristine bytes in the requested newline format."""
    source = target.read_bytes().replace(b"\r\n", b"\n")
    assert b"\n" in source
    newline = {"LF": b"\n", "CRLF": b"\r\n"}[line_ending]
    staged = source.replace(b"\n", newline)
    target.write_bytes(staged)
    target.with_suffix(".original").write_bytes(staged)
    return staged


@pytest.mark.parametrize("line_ending, expected", [
    ("LF", b"first\nsecond\n"),
    ("CRLF", b"first\r\nsecond\r\n"),
], ids=["LF", "CRLF"])
def test_with_line_ending_stages_both_files_from_a_crlf_checkout(tmp_path, line_ending, expected):
    target = tmp_path / "sound_report.py"
    target.write_bytes(b"first\r\nsecond\r\n")
    pristine = target.with_suffix(".original")
    pristine.write_bytes(target.read_bytes())

    assert _with_line_ending(target, line_ending) == expected
    assert target.read_bytes() == pristine.read_bytes() == expected


@pytest.mark.parametrize("line_ending", ["LF", "CRLF"])
def test_sound_partial_first_mutant_write_restores_the_exact_bytes(tmp_path, line_ending):
    """The issue's reproduction, as it ran on `origin/main` @ `d5bac16a`: the
    first mutant's write sat outside the guard, so a write that put 64
    characters on disk and raised left the reporter at 64 bytes. The error
    must still reach the caller before any mutant suite runs or gets a
    verdict, and the file must be byte for byte what it was at entry."""
    _, target = stage_sound(tmp_path, RUN_MARKER)
    before = _with_line_ending(target, line_ending)
    launcher = tmp_path / "partial_write.py"
    launcher.write_text(PARTIAL_WRITE_LAUNCHER)
    record = tmp_path / "partial-write.bin"
    result = subprocess.run([sys.executable, str(launcher), str(tmp_path), str(record)],
                            cwd=tmp_path, capture_output=True, text=True)
    out = result.stdout + result.stderr

    assert (tmp_path / "baseline-executed").exists()
    assert record.exists(), out                              # the fault fired
    left = record.read_bytes()
    assert len(left) == 64, out                              # and wrote the prefix
    assert left == before.replace(b"\r\n", b"\n")[:64]
    assert result.returncode == 4, out                       # and its error propagated
    assert "OSError left main(): injected partial write" in out, out
    assert not (tmp_path / "mutant-executed").exists()       # before a mutant suite ran
    _no_kill_credit(out)
    assert "SURVIVED" not in out and "REFUSED" not in out     # or got a verdict
    assert target.read_bytes() == before


def test_sound_completed_run_leaves_a_crlf_target_as_it_found_it(tmp_path):
    """The other failure in #1480, on a run that completes: the probe saved
    its original with `read_text()`, which folds CRLF to LF, so restoring it
    rewrote a CRLF reporter as LF. A repair that only moves the write inside
    the guard still fails here. Verdicts and exit are the existing ones."""
    script, target = stage_sound(tmp_path, INSENSITIVE)
    before = _with_line_ending(target, "CRLF")
    result = run(script, tmp_path)
    out = result.stdout + result.stderr
    assert result.returncode == 1, out
    assert out.count("SURVIVED") == len(sound.MUTANTS), out
    _no_kill_credit(out)
    assert target.read_bytes() == before


# ---- issue #1401: what the FPGA record may call a kill ---------------------

@functools.lru_cache(maxsize=None)
def _fpga_record():
    """The FPGA record as a module, for its `M` and `EXPECTED`. Imported
    lazily: before #1401 the script ran its whole mutation loop at import."""
    sys.path.insert(0, str(REPO / "fpga" / "testdata"))
    import mutate_pnr_report
    return mutate_pnr_report


#: The FPGA fixtures read the target `fpga/testdata/mutate_pnr_report.py`
#: edits, in `fpga/scripts/`, and compare it with the pristine copy
#: `stage_fpga()` leaves beside it.
_FPGA_READS = ("import pathlib\n"
               "HERE = pathlib.Path(__file__).resolve().parent\n"
               "SRC = (HERE / 'scripts' / 'pnr_report.py').read_bytes()\n"
               "TEXT = SRC.decode().replace('\\r\\n', '\\n')\n"
               "PRISTINE = (HERE / 'scripts' / 'pnr_report.original').read_bytes()\n")

#: The issue's reproduction, verbatim: collects, and runs its one test, on the
#: unmutated target; uncollectable under any mutant. The marker tells a refusal
#: apart from a baseline that never ran.
FPGA_COLLECTION_ERROR = (_FPGA_READS
                         + "if SRC != PRISTINE:\n"
                           "    raise ImportError('injected collection failure')\n"
                           "def test_fixture():\n"
                           "    pathlib.Path('baseline-executed').touch()\n")

#: Green on the unmutated target; under any mutant every test errors in setup:
#: pytest exits 1 and records no call failure.
FPGA_ERROR_ONLY = (_FPGA_READS
                   + "import pytest\n\n\n"
                     "@pytest.fixture(autouse=True)\n"
                     "def boom():\n"
                     "    if SRC != PRISTINE:\n"
                     "        raise RuntimeError('apparatus control')\n\n\n"
                     "def test_a():\n"
                     "    assert 1\n")

#: Green on the unmutated target; under any mutant one test fails, and its
#: name is in no recorded set.
FPGA_UNRELATED = (_FPGA_READS
                  + "def test_unrelated_to_every_recorded_name():\n"
                    "    assert SRC == PRISTINE\n")

#: Green on the unmutated target; under any mutant it fails at exactly the
#: assertion recorded for the Fmax mutant -- and at an assertion no other
#: mutant has recorded.
_FMAX = "new: render Fmax on unrouted reports"
_MISSING_RC = "J-equivalent: disable rc is None"
FPGA_ATTRIBUTABLE = (_FPGA_READS
                     + "def test_refused_report_does_not_label_an_fmax_as_post_route_result():\n"
                       "    text = 'suppressed' if SRC == PRISTINE else 'post-route RESULT'\n"
                       "    assert \"post-route RESULT\" not in text and \"ESTIMATE:\" not in text\n")


def stage_fpga(tmp, suite_src, *, crlf=False):
    """`stage()` for the FPGA record, plus the pristine copy its fixtures
    compare the mutated target against."""
    script, target = stage(tmp, "fpga", suite_src)
    if crlf:
        target.write_bytes(target.read_text().replace("\n", "\r\n").encode())
    target.with_suffix(".original").write_bytes(target.read_bytes())
    return script, target


def test_fpga_partial_mutant_write_restores_original(tmp_path, monkeypatch):
    script, target = stage_fpga(tmp_path,
                                "from pathlib import Path\n"
                                "def test_baseline():\n"
                                "    Path('baseline-executed').touch()\n")
    original = target.read_bytes()
    write_bytes = Path.write_bytes

    def partial_write(path, data):
        if path == target and data != original:
            write_bytes(path, data[:64])
            raise OSError("injected partial mutant write")
        return write_bytes(path, data)

    record = _fpga_record()
    monkeypatch.setattr(record, "ROOT", tmp_path)
    monkeypatch.setattr(record, "TARGET", target)
    monkeypatch.setattr(sys, "argv", [str(script)])
    monkeypatch.setattr(Path, "write_bytes", partial_write)
    with pytest.raises(OSError, match="injected partial mutant write"):
        record.main()
    assert (tmp_path / "baseline-executed").is_file()
    assert target.read_bytes() == original


def test_fpga_python_option_runs_baseline_and_every_mutant(tmp_path):
    script, target = stage_fpga(tmp_path, """
import sys
def test_interpreter():
    with open('interpreters', 'a') as log:
        print(sys.executable, file=log)
""")
    original = target.read_bytes()
    # A second path to this installation keeps pytest available while making
    # the requested executable distinguishable inside each suite process.
    executable = Path(sys.executable)
    selected = tmp_path / "selected"
    selected.symlink_to(executable.parent.parent, target_is_directory=True)
    requested = selected / executable.parent.name / executable.name
    result = subprocess.run([sys.executable, str(script), "--python", str(requested)],
                            cwd=tmp_path, capture_output=True, text=True)
    assert result.returncode == 1, result.stdout + result.stderr   # insensitive suite
    assert (tmp_path / "interpreters").read_text().splitlines() == (
        [str(requested)] * (1 + len(_fpga_record().M)))
    assert target.read_bytes() == original


def _fpga_uncollectable_under(mutant, body):
    """`body` as the suite, plus an ImportError at collection under `mutant`
    ONLY: the other mutants still run it, so one run holds a refusal and
    whatever `body` makes of the rest."""
    (old, _new), = _fpga_record().M[mutant]
    return (_FPGA_READS
            + f"if {old!r} not in TEXT:\n"
              "    raise ImportError('uncollectable under this mutant only')\n"
            + body)


_VERDICT_PREFIXES = (
    "KILLED",
    "RED (good)",
    "SURVIVED -- NOT COVERED",
    "SURVIVED",
    "REFUSED",
)


def _is_verdict_header(line, name):
    for prefix in _VERDICT_PREFIXES:
        if line.startswith(prefix) and line[len(prefix):].startswith(" "):
            tail = line[len(prefix):].lstrip(" ")
            if prefix == "RED (good)":
                match = re.fullmatch(r"(.+): [0-9]+ test\(s\) caught it", tail)
                return match is not None and match[1] == name
            rem = tail[len(name):]
            return tail.startswith(name) and (not rem or rem.startswith((": ", " -- ")))
    return False


def _verdict_header(out, name):
    """`(lines, index)` for `name`'s unique verdict header, not a summary line."""
    lines = out.splitlines()
    hits = [i for i, ln in enumerate(lines) if _is_verdict_header(ln, name)]
    assert len(hits) == 1, out
    return lines, hits[0]


def _verdict_line(out, name):
    """The record's own verdict line for `name`, not a summary that lists it."""
    lines, index = _verdict_header(out, name)
    return lines[index]


def _credited_ids(out, name):
    """Test IDs credited under `name`'s own verdict header: the indented,
    non-parenthesised lines up to the next unindented line (the next verdict
    header or the summary). A search of the whole output lets a mutant that
    shares IDs with another borrow its credits."""
    lines, start = _verdict_header(out, name)
    ids = []
    for ln in lines[start + 1:]:
        if not ln.startswith(" "):
            break
        item = ln.strip()
        if item and not item.startswith("("):
            ids.append(item)
    return ids


def test_credited_ids_are_read_from_the_mutants_own_group():
    """Control: the first group lost both IDs while the second keeps them."""
    out = ("KILLED   A\n"
           "KILLED   B\n"
           "    t1\n"
           "    t2[x-y]\n"
           "    (note: diagnostic)\n"
           "2 mutant(s) killed\n")
    assert _credited_ids(out, "A") == []
    assert _credited_ids(out, "B") == ["t1", "t2[x-y]"]


@pytest.mark.parametrize("long_name", ["AB", "A -- variant", "A: variant"])
def test_sound_credit_requires_the_complete_mutant_name(long_name):
    longer_group = f"RED (good)   {long_name}: 1 test(s) caught it\n    test_long\n"
    assert _credited_ids(longer_group, long_name) == ["test_long"]
    with pytest.raises(AssertionError):
        _credited_ids(longer_group, "A")

    both = "RED (good)   A: 1 test(s) caught it\n    test_short\n" + longer_group
    _assert_sound_credits(both, {"A": ["test_short"], long_name: ["test_long"]})


def _survivors(out):
    return [ln for ln in out.splitlines() if ln.startswith("SURVIVED")]


def _refusals(out):
    return [ln for ln in out.splitlines() if ln.startswith("REFUSED")]


def _no_fpga_kill_credit(out):
    assert "KILLED" not in out and "killed" not in out, out


def test_fpga_mutant_collection_error_refuses(tmp_path):
    """The issue's reproduction, as it ran on `origin/main` @ `4b4c4935`: the
    baseline test executes, then every mutant is uncollectable, and the record
    printed `KILLED` fourteen times with no test beside it and exited 0."""
    script, target = stage_fpga(tmp_path, FPGA_COLLECTION_ERROR)
    before = target.read_bytes()
    result = run(script, tmp_path)
    out = result.stdout + result.stderr
    assert (tmp_path / "baseline-executed").exists()
    assert target.read_bytes() == before
    assert result.returncode == 2, out
    _no_fpga_kill_credit(out)
    assert "SURVIVED" not in out and "EXPECTED SURVIVOR" not in out
    assert "pytest exited 2" in out            # the status is named
    for mutant in _fpga_record().M:
        assert _verdict_line(out, mutant).startswith("REFUSED"), out


def test_fpga_mutant_error_only_run_refuses(tmp_path):
    script, target = stage_fpga(tmp_path, FPGA_ERROR_ONLY)
    before = target.read_bytes()
    result = run(script, tmp_path)
    out = result.stdout + result.stderr
    assert result.returncode == 2, out
    _no_fpga_kill_credit(out)
    assert "SURVIVED" not in out and "EXPECTED SURVIVOR" not in out
    assert "pytest exited 1" in out
    # The suite that ran is named: this record's, not the holdout probe's whose
    # decision it borrows.
    assert "no call failure was recorded for fpga/test_pnr_report.py" in out
    assert "tools/test_holdout.py" not in out and "model/test_sound_report.py" not in out
    assert target.read_bytes() == before


def test_fpga_mutant_failure_outside_the_recorded_names_refuses(tmp_path):
    """The third condition, not the second: a red is the mutant's only if an
    intended test went red. One that falls entirely elsewhere is refused,
    expected and parsed names both shown. Before #1401 this printed `KILLED`."""
    script, target = stage_fpga(tmp_path, FPGA_UNRELATED)
    before = target.read_bytes()
    result = run(script, tmp_path)
    out = result.stdout + result.stderr
    assert result.returncode == 2, out
    _no_fpga_kill_credit(out)
    assert "SURVIVED" not in out and "EXPECTED SURVIVOR" not in out
    for mutant in _fpga_record().M:
        assert _verdict_line(out, mutant).startswith("REFUSED"), out
    assert "test_unrelated_to_every_recorded_name" in out       # parsed, named
    for mutant, names in _fpga_record().EXPECTED.items():
        for name in names:
            assert name in out, (mutant, name)                  # expected, named
    assert target.read_bytes() == before


def test_fpga_baseline_red_with_zero_exit_stops_before_mutation(tmp_path):
    """Three assertions fail on the unmutated source, which then forces a zero
    exit. Accepting that exit would let the same failures earn every mutant a
    kill (#1130)."""
    suite = _FPGA_READS + """
def test_a():
    assert 0

def test_b():
    assert 0

def test_c():
    assert 0

def test_stop_after_results():
    if SRC == PRISTINE:
        import pytest
        pytest.exit('end of baseline', returncode=0)
    pathlib.Path('mutant-executed').touch()
"""
    script, target = stage_fpga(tmp_path, suite)
    before = target.read_bytes()
    result = run(script, tmp_path)
    out = result.stdout + result.stderr
    assert result.returncode == 2, out
    assert "REFUSED" in out and "baseline" in out and "3 test(s) red" in out, out
    for name in ("test_a", "test_b", "test_c"):
        assert name in out, out
    _no_fpga_kill_credit(out)
    assert not (tmp_path / "mutant-executed").exists()
    assert target.read_bytes() == before


def test_fpga_mutant_search_text_gone_refuses_before_anything_runs(tmp_path):
    """A search text the target no longer contains exactly once: that mutant
    cannot be applied, so the record is measuring a file that moved under it.
    REFUSE (exit 2) naming it, before the baseline, let alone a mutant."""
    script, target = stage_fpga(
        tmp_path, "import pathlib\ndef test_x():\n    pathlib.Path('baseline-executed').touch()\n")
    order = "J-survivor: disable util[0] > r order check"
    old = b"if util and util[0] > r:"                      # its search text in `M`
    moved = target.read_bytes().replace(old, old.replace(b"> ", b">  "))
    assert old not in moved and moved != target.read_bytes()
    target.write_bytes(moved)
    result = run(script, tmp_path)
    out = result.stdout + result.stderr
    assert result.returncode == 2, out
    assert "REFUSED" in out and order in out and "No mutant was run" in out, out
    _no_fpga_kill_credit(out)
    assert "SURVIVED" not in out and "EXPECTED SURVIVOR" not in out
    assert not (tmp_path / "baseline-executed").exists()
    assert target.read_bytes() == moved
    assert _fpga_record().M[order][0][0].encode() == old   # the text is still its own


def test_fpga_intended_assertion_gets_kill_credit_and_the_rest_refuse(tmp_path):
    """Attribution must not refuse everything. The one mutant with the failing
    test recorded is KILLED and names it. The other mutants refuse the same
    red because it falls outside their records. The run exits 2."""
    script, target = stage_fpga(tmp_path, FPGA_ATTRIBUTABLE)
    before = target.read_bytes()
    result = run(script, tmp_path)
    out = result.stdout + result.stderr
    assert result.returncode == 2, out
    assert target.read_bytes() == before
    killed = _verdict_line(out, _FMAX)
    assert killed.startswith("KILLED") and "1 test(s) caught it" in killed, out
    assert ("    test_refused_report_does_not_label_an_fmax_as_post_route_result"
            in out.splitlines()), out
    assert not _survivors(out) and "EXPECTED SURVIVOR" not in out, out
    for mutant in _fpga_record().M:
        if mutant != _FMAX:
            assert _verdict_line(out, mutant).startswith("REFUSED"), out


def test_fpga_survivor_outranks_a_refusal_exits_one(tmp_path):
    """The holdout probe's precedence (#1113), so the three records read
    alike: a mutant that survived is a measured hole in the suite and is not
    hidden behind a refusal that measured nothing. Both are named. (The Fmax
    mutant, whose search text no other mutant removes: the two unlink mutants
    delete the same block, so either makes the other's probe uncollectable.)"""
    script, target = stage_fpga(tmp_path, _fpga_uncollectable_under(_FMAX, INSENSITIVE))
    before = target.read_bytes()
    result = run(script, tmp_path)
    out = result.stdout + result.stderr
    assert result.returncode == 1, out
    assert len(_survivors(out)) == len(_fpga_record().M) - 1, out
    assert _verdict_line(out, _FMAX).startswith("REFUSED"), out
    assert "UNEXPECTED SURVIVORS (13)" in out and "1 refused" in out, out
    _no_fpga_kill_credit(out)
    assert target.read_bytes() == before


def test_fpga_crlf_checkout_exercises_all_mutants_and_restores_bytes(tmp_path):
    """Each mutant runs with CRLF, and the insensitive suite reports them all."""
    script, target = stage_fpga(tmp_path, r"""
import hashlib
from pathlib import Path

def test_distinct_crlf_source():
    source = (Path(__file__).parent / 'scripts/pnr_report.py').read_bytes()
    assert b'\r\n' in source
    assert b'\n' not in source.replace(b'\r\n', b'')
    # A no-op injection would present bytes already seen by the suite.
    Path(hashlib.sha256(source).hexdigest()).touch(exist_ok=False)
""", crlf=True)
    before = target.read_bytes()
    result = run(script, tmp_path)
    out = result.stdout + result.stderr
    assert result.returncode == 1, out
    assert len(_survivors(out)) == len(_fpga_record().M), out
    assert _verdict_line(out, _MISSING_RC).startswith("SURVIVED"), out
    assert _MISSING_RC in _line(out, "UNEXPECTED SURVIVORS")
    _no_fpga_kill_credit(out)
    assert target.read_bytes() == before


def test_fpga_missing_returncode_wrong_assertion_refuses(tmp_path):
    """An unrelated assertion in the recorded CLI case earns no kill credit."""
    (old, _new), = _fpga_record().M[_MISSING_RC]
    suite = (_FPGA_READS
             + "import pytest\n"
               "@pytest.mark.parametrize('case', [0], ids=['missing-returncode'])\n"
               "def test_headroom_report_fit_error_requires_recorded_returncode(case):\n"
               f"    assert {old!r} in TEXT\n")
    script, target = stage_fpga(tmp_path, suite, crlf=True)
    before = target.read_bytes()
    result = run(script, tmp_path)
    out = result.stdout + result.stderr
    assert result.returncode == 1, out           # thirteen others survived; both named
    line = _verdict_line(out, _MISSING_RC)
    assert line.startswith("REFUSED"), out
    assert "no intended assertion failed" in line, out
    case = "test_headroom_report_fit_error_requires_recorded_returncode[missing-returncode]"
    assert f"    (failed, no credit: {case})" in out.splitlines(), out
    assert "UNEXPECTED SURVIVORS (13)" in out and "1 refused" in out, out
    _no_fpga_kill_credit(out)
    assert target.read_bytes() == before


def test_every_fpga_mutant_has_recorded_names_that_the_real_suite_collects():
    """`EXPECTED` is what a kill is attributed to. A mutant with no record
    refuses every run, and so does a recorded ID the suite no longer collects
    or an assertion it no longer contains: an unsatisfiable gate, which is
    worse than none. So the record is checked against the suite it names, by
    collecting that suite rather than running it."""
    fpga = _fpga_record()
    assert set(fpga.EXPECTED) == set(fpga.M)
    collected = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q", "-p", "no:cacheprovider",
         fpga.SUITE], cwd=REPO, capture_output=True, text=True)
    ids = {ln.split("::", 1)[1] for ln in collected.stdout.splitlines()
           if ln.startswith(fpga.SUITE + "::")}
    assert ids, collected.stdout + collected.stderr
    lines = [ln.strip() for ln in (REPO / fpga.SUITE).read_text().splitlines()]
    for mutant, names in fpga.EXPECTED.items():
        assert names, mutant
        for name, site in names.items():
            assert name in ids, (mutant, name)
            # The rule the verdict applies to the failed assertion.
            assert any(ln == site.statement or ln.startswith(site.statement + ",")
                       for ln in lines), (mutant, name, site.statement)


def stage_real_fpga(tmp_path):
    """Copy the suite, the file the record edits, and what the
    fake-executable `make -C fpga` tests need into a private tree. The
    Makefile's ROOT is the parent of fpga/, so rtl-sketch/ comes too: without
    it every rig test fails under every mutant, the baseline included."""
    for rel in ("fpga", "rtl-sketch", "tools/probes"):
        shutil.copytree(REPO / rel, tmp_path / rel,
                        ignore=shutil.ignore_patterns("__pycache__", "build"))
    return tmp_path / "fpga/testdata/mutate_pnr_report.py", tmp_path / "fpga/scripts/pnr_report.py"


@pytest.mark.skipif(shutil.which("make") is None, reason="make not installed")
def test_fpga_real_suite_kills_all_mutants_at_recorded_assertions(tmp_path):
    """The real suite catches every mutant and restores the reporter."""
    fpga = _fpga_record()
    script, target = stage_real_fpga(tmp_path)
    before = target.read_bytes()
    result = run(script, tmp_path)
    out = result.stdout + result.stderr
    assert result.returncode == 0, out
    assert target.read_bytes() == before
    # By line, not by substring: a mutant is named "allow-no-fit accepts REFUSED".
    assert not _refusals(out) and not _survivors(out), out
    assert f"{len(fpga.M)} mutant(s) killed." in out, out
    for mutant, names in fpga.EXPECTED.items():
        assert _verdict_line(out, mutant).startswith("KILLED"), out
        credited = _credited_ids(out, mutant)
        assert sorted(credited) == sorted(names), (
            mutant, "missing", sorted(set(names) - set(credited)),
            "unexpected", sorted(set(credited) - set(names)))
