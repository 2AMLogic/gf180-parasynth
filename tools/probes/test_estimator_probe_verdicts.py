#!/usr/bin/env python3
"""#1458's two false passes, as regression tests on the probes' verdicts.

    python3 -m pytest tools/probes/test_estimator_probe_verdicts.py -q

`tools/probes/estimator_domains.py` and `tools/probes/estimator_ground_truth.py
check` both read their verdict off "what failed", and before #1458 neither had
a branch for a record in which nothing ran. That is #1451's shape -- an empty
record reads as a pass because nothing in it contradicts one -- and
`tools/run_all.py` reports exit 0 as PASS without scraping output, so the
probe's own exit status is all that stands between an empty run and a green
`make verify`.

Measured on `origin/main` at ce0713ee (the start-red run, Python 3.12.3,
NumPy 2.5.5, SciPy 1.18.1):

- `estimator_ground_truth.py check --estimator typo_1458`, a REAL command line
  with nothing injected: the estimator summary was empty and the command
  printed `All checks passed.` and exited 0. `--estimator` has no `choices`,
  so a misspelt name selects nothing and is accepted without a word.
- `estimator_domains.py`'s `main()` with its six `measure_*` steps replaced by
  no-ops and `report_corpus_absence()` left as shipped: the corpus notice,
  then `All checks passed.`, exit 0. The unmodified probe on the same host
  records 11 checks and ends with the same line, so the verdict could not
  tell a zero-check run from an eleven-check one.

Each row below asserts the exit status AND the terminal diagnostic, through
the entry point: the real CLI for the two ground-truth selections, `main()`
for the domains steps (which no command-line flag can switch off).

WHAT THE COUNT CANNOT SEE, said rather than implied. The guard counts what was
RECORDED. A row recorded `PASS` by a check that gates nothing, or a
`check(True, ...)` nobody can make false, satisfies it and violates its
intent; no counter can tell those from a real outcome, which is why the
ground-truth suite has `controls` (mutants and start-red stubs) as its other
half, and why a domain assertion's non-vacuity stays a reading judgement.

Inputs that would satisfy a row while violating its intent
(docs/verification-rules.md rule 8), each a row of its own:

- a guard on `len(verdicts)` is satisfied by a nonempty list of only SKIP and
  NOT-MEASURED rows, and the auxiliary (axis, inventory, conversion) rows
  passing satisfies any guard that counts checks: bookkeeping is not an
  estimator answering a fixture;
- a guard on "nothing passed" (`n_ok == 0`) calls a measured FAIL an empty
  run: the FAIL-only rows must stay exit 1;
- a tally carried over from an earlier `main()` in the same process lets an
  empty run report the earlier one's verdict, or inherit its failures. The
  domains rows run four verdicts on ONE loaded module for that reason.

Every other row loads its probe fresh from its file, so its tally starts at
zero by construction and no hand-kept reset list can go stale.
"""
from __future__ import annotations

import contextlib
import importlib.util
import pathlib
import re
import subprocess
import sys

import pytest

HERE = pathlib.Path(__file__).resolve().parent
DOMAINS = HERE / "estimator_domains.py"
GROUND_TRUTH = HERE / "estimator_ground_truth.py"

PASS, EXIT_FAIL, NO_VERDICT = 0, 1, 2
#: the named reason each probe prints for a run that recorded nothing
EMPTY_DOMAINS = "NO VERDICT (NO CHECK EXECUTED)"
EMPTY_GROUND_TRUTH = "NO VERDICT (NO ESTIMATOR EXERCISED)"
COUNTED_EMPTY_DOMAINS = "0 of 0 checks passed -- " + EMPTY_DOMAINS
COUNTED_EMPTY_GROUND_TRUTH = "0 estimator outcomes exercised -- " + EMPTY_GROUND_TRUTH


@contextlib.contextmanager
def _loaded(path: pathlib.Path, name: str):
    """The probe loaded fresh from its file, for one test: its module state
    is the probe's own definition, before anything has recorded into it.

    It is registered in `sys.modules` while it executes, which the first
    start-red run of this file got wrong: `estimator_ground_truth.py`'s
    `@dataclass` runs under `from __future__ import annotations`, and Python
    3.12 resolves a dataclass's string annotations through
    `sys.modules[cls.__module__]` -- unregistered, that is an AttributeError
    at import, and 17 rows ERRORED at fixture setup for a reason that was
    this loader's and not the probe's."""
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
        yield module
    finally:
        sys.modules.pop(name, None)


@pytest.fixture
def domains():
    with _loaded(DOMAINS, "estimator_domains_under_test") as probe:
        yield probe


@pytest.fixture
def gt():
    with _loaded(GROUND_TRUTH, "estimator_ground_truth_under_test") as probe:
        yield probe


def _shows(out: str, text: str) -> str:
    """An assertion message that carries the run's output."""
    return f"{text}:\n{out}"


def _tail(out: str, n: int = 1) -> str:
    """The last `n` non-blank lines: the terminal diagnostic."""
    lines = [line for line in out.splitlines() if line.strip()]
    return "\n".join(lines[-n:])


# ---------------------------------------------------------------------------
# tools/probes/estimator_domains.py
# ---------------------------------------------------------------------------
DOMAIN_STEPS = ("measure_band_pair_stationary", "measure_band_pair_decay",
                "measure_band_pair_edge", "measure_tone_ratio_domain",
                "measure_decay_tau_domain", "measure_inharmonic_headroom")


def _domains_main(probe, monkeypatch, capsys, steps=None):
    """`main()`'s exit code and output with the six measurement steps
    replaced -- by no-ops, or, for a name in `steps`, by that replacement.
    `report_corpus_absence()` is left as shipped, so the corpus notice is
    printed exactly as it is under `make verify`."""
    for name in DOMAIN_STEPS:
        monkeypatch.setattr(probe, name, (steps or {}).get(name, lambda: None))
    code = probe.main()
    return code, capsys.readouterr().out


def test_domains_noop_steps_with_the_corpus_notice_is_no_verdict(
        domains, monkeypatch, capsys):
    """The #1458 control: nothing recorded, corpus notice still printed.
    Exit 2 (NO VERDICT), and the last line counts and names it. Red on the
    pre-fix `main()`, which printed `All checks passed.` and returned 0."""
    code, out = _domains_main(domains, monkeypatch, capsys)
    assert code == NO_VERDICT, _shows(
        out, f"an empty run returned {code}, expected 2 (NO VERDICT)")
    assert "7. NOT MEASURED HERE" in out, _shows(
        out, "the corpus notice is information and must still be printed")
    assert _tail(out) == COUNTED_EMPTY_DOMAINS, _shows(
        out, "the terminal diagnostic must count, and name that nothing ran")
    assert "All checks passed." not in out, _shows(
        out, "a run that recorded no check reported a pass")


def test_domains_real_run_counts_its_checks_and_passes():
    """The shipped entry point (`sys.exit(main())`), nothing injected: a
    clean nonempty run is still exit 0, and its last line is the count."""
    p = subprocess.run([sys.executable, str(DOMAINS)], capture_output=True,
                       text=True, timeout=300)
    assert p.returncode == PASS, p.stdout[-2000:] + p.stderr[-2000:]
    m = re.fullmatch(r"(\d+) of (\d+) checks passed\.", _tail(p.stdout))
    assert m, _shows(p.stdout, "the last line must be `N of N checks passed.`")
    assert int(m.group(1)) == int(m.group(2)) >= 1, _shows(
        p.stdout, "every recorded check passed, and at least one was recorded")
    assert EMPTY_DOMAINS not in p.stdout, _shows(
        p.stdout, "a run that recorded checks reported NO VERDICT")


def test_domains_one_passing_check_alone_exits_0_with_its_count(
        domains, monkeypatch, capsys):
    def pass_one():
        domains.check(True, "injected: the only check holds")

    code, out = _domains_main(
        domains, monkeypatch, capsys, {"measure_band_pair_stationary": pass_one})
    assert code == PASS, _shows(out, f"exit {code}")
    assert _tail(out) == "1 of 1 checks passed.", _shows(
        out, "the terminal diagnostic must report this invocation's count")
    assert EMPTY_DOMAINS not in out, _shows(
        out, "a counted check was reported as an empty run")


def test_domains_one_failing_check_alone_keeps_exit_1(domains, monkeypatch,
                                                      capsys):
    """A FAIL and nothing else: `n_ok` is 0 here too, so a guard written on
    "nothing passed" instead of "nothing was recorded" would call this
    measured mismatch an empty run. It stays exit 1, with its diagnostic."""
    def fail_one():
        domains.check(False, "injected: the only check fails")

    code, out = _domains_main(
        domains, monkeypatch, capsys, {"measure_band_pair_stationary": fail_one})
    assert code == EXIT_FAIL, _shows(out, f"exit {code}")
    assert _tail(out, 2) == ("0 of 1 checks passed; 1 check(s) FAILED:\n"
                             "  - injected: the only check fails"), _shows(
        out, "the failure keeps its count and its diagnostic")
    assert EMPTY_DOMAINS not in out, _shows(
        out, "a measured failure was reported as an empty run")


def test_domains_verdict_state_does_not_leak_between_invocations(
        domains, monkeypatch, capsys):
    """One loaded module, one interpreter: a pass, then a failure, then an
    empty run, then two checks recorded outside `main()` at all. Each verdict
    is that invocation's alone."""
    def pass_one():
        domains.check(True, "injected: an earlier pass")

    code, out = _domains_main(
        domains, monkeypatch, capsys, {"measure_band_pair_stationary": pass_one})
    assert (code, _tail(out)) == (PASS, "1 of 1 checks passed."), _shows(
        out, "the first invocation's verdict")

    def fail_one():
        domains.check(False, "injected: an earlier failure")

    code, out = _domains_main(
        domains, monkeypatch, capsys, {"measure_band_pair_stationary": fail_one})
    assert _tail(out, 2) == ("0 of 1 checks passed; 1 check(s) FAILED:\n"
                             "  - injected: an earlier failure"), _shows(
        out, f"the second invocation's verdict (exit {code})")

    code, out = _domains_main(domains, monkeypatch, capsys)
    assert code == NO_VERDICT, _shows(
        out, f"the empty run inherited an earlier verdict (exit {code})")
    assert _tail(out) == COUNTED_EMPTY_DOMAINS, _shows(
        out, "the empty run must count zero, not the earlier checks")
    assert "earlier" not in out, _shows(out, "an earlier failure leaked into it")

    # Recorded through the probe's own `check`, outside any `main()`: what a
    # test or a REPL leaves behind. Their lines are read off here, so what is
    # asserted about the next run's output is that run's alone.
    domains.check(True, "injected: a stray pass outside main()")
    domains.check(False, "injected: a stray failure outside main()")
    stray = capsys.readouterr().out
    assert len(stray.splitlines()) == 2, _shows(stray, "two stray checks")
    code, out = _domains_main(domains, monkeypatch, capsys)
    assert code == NO_VERDICT, _shows(
        out, f"state recorded outside main() leaked (exit {code})")
    assert _tail(out) == COUNTED_EMPTY_DOMAINS, _shows(out, "the fourth verdict")
    assert "stray" not in out, _shows(out, "a stray record leaked into the output")


# ---------------------------------------------------------------------------
# tools/probes/estimator_ground_truth.py check
# ---------------------------------------------------------------------------
def _cli(*args) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(GROUND_TRUTH), *args],
                          capture_output=True, text=True, timeout=300)


def test_ground_truth_cli_typo_selection_is_no_verdict():
    """The #1458 control, as a real command line: nothing injected. Red on
    the pre-fix `cmd_check`, which printed `All checks passed.` and exited 0
    over an empty estimator summary."""
    p = _cli("check", "--estimator", "typo_1458")
    assert p.returncode == NO_VERDICT, _shows(
        p.stdout + p.stderr,
        f"an empty estimator selection returned {p.returncode}, expected 2")
    for heading in ("0. Axis coverage of the fixture catalogue",
                    "1. Inventory: every public callable",
                    "2. Closed-form conversions"):
        assert heading in p.stdout, _shows(
            p.stdout, f"the auxiliary checks must still run and print: {heading}")
    assert _tail(p.stdout) == COUNTED_EMPTY_GROUND_TRUTH, _shows(
        p.stdout,
        "the terminal diagnostic must count, and name what was not exercised")
    assert "All checks passed." not in p.stdout, _shows(
        p.stdout, "a run that exercised no estimator reported a pass")


def test_ground_truth_cli_rms_selection_stays_green():
    """The control's other half: `--estimator rms` exercises one estimator
    over every fixture. Still exit 0, and the count is the table's own PASS
    row -- NOT-MEASURED rows are present in the same record and are not
    added to it."""
    p = _cli("check", "--estimator", "rms")
    assert p.returncode == PASS, _shows(
        p.stdout + p.stderr, f"the rms selection went red: exit {p.returncode}")
    counted = {}
    for k in ("PASS", "NOT-MEASURED"):
        m = re.search(rf"^\s+{re.escape(k)}\s+(\d+)\s*$", p.stdout, re.M)
        assert m, _shows(p.stdout, f"the summary table has no `{k}` row")
        counted[k] = int(m.group(1))
    assert counted["PASS"] >= 1 and counted["NOT-MEASURED"] >= 1, _shows(
        p.stdout, "this control needs both a counted and an uncounted status")
    assert _tail(p.stdout) == (f"{counted['PASS']} estimator outcomes exercised "
                               f"-- All checks passed."), _shows(
        p.stdout, "the count must equal the PASS row, not PASS + NOT-MEASURED")
    assert EMPTY_GROUND_TRUTH not in p.stdout, _shows(
        p.stdout, "a run that exercised an estimator reported NO VERDICT")


#: clean rows of each auxiliary producer's own shape: bookkeeping that passed
AXIS_OK = (("damped_sine", "duration", 5, 3.0, True, "needs >=3 values"),)
INVENTORY_OK = (("rms", "SPECS", "checked against fixtures", True),)
CONVERSIONS_OK = (("db(2) == 6.0206 dB", True, "0.000e+00"),)
#: the same three rows, failing
AXIS_BAD = (("damped_sine", "duration", 1, 0.0, False, "needs >=3 values"),)
INVENTORY_BAD = (("injected_name", "UNDECLARED",
                  "a public callable this suite neither checks nor states a "
                  "reason for", False),)
CONVERSIONS_BAD = (("injected conversion", False, "1.000e+00"),)

AUXILIARY_FAILURES = (
    ("axis", "axis", AXIS_BAD,
     "  - axis not swept: damped_sine/duration: 1 values, spread 0"),
    ("inventory", "inventory", INVENTORY_BAD,
     "  - undeclared estimator: injected_name"),
    ("conversion", "conversions", CONVERSIONS_BAD,
     "  - conversion: injected conversion"),
)

#: (status, exit) for every status the probe can print. The exercised ones are
#: the probe's own GREEN and RED; SKIP and NOT-MEASURED are present in a record
#: without being outcomes. The last row is a misspelling: an outcome the
#: vocabulary does not know must not be able to pass as exercised, so it is
#: an INPUT to the table, not a member of the vocabulary the table is checked
#: against.
MISSPELLED = "NOT_MEASURED"
STATUS_ROWS = (
    ("PASS", PASS), ("REFUSED", PASS), ("IDENTIFIED", PASS),
    ("INSUFFICIENT", PASS),
    ("FAIL", EXIT_FAIL), ("AMBIGUOUS-CONFIDENT", EXIT_FAIL),
    ("SKIP", NO_VERDICT), ("NOT-MEASURED", NO_VERDICT),
    (MISSPELLED, NO_VERDICT),
)


def _record(gt, monkeypatch, *, verdicts=(), axis=AXIS_OK,
            inventory=INVENTORY_OK, conversions=CONVERSIONS_OK, catalogue=()):
    """Replace the record's row producers. The defaults are NONEMPTY clean
    rows, so bookkeeping that passed is present in every run; pass `()` for
    a producer that returned nothing. The catalogue is an empty stand-in
    (nothing downstream of the replaced producers reads it) unless it is
    `None`, which leaves the real one in place."""
    monkeypatch.setattr(gt, "run", lambda kinds=None, estimators=None,
                        fixtures=None: list(verdicts))
    monkeypatch.setattr(gt.ef, "axis_coverage", lambda fixtures: list(axis))
    monkeypatch.setattr(gt, "inventory", lambda: list(inventory))
    monkeypatch.setattr(gt, "conversions", lambda: list(conversions))
    if catalogue is not None:
        monkeypatch.setattr(gt.ef, "catalogue", lambda kinds=None: list(catalogue))


def _ground_truth_main(gt, capsys, *args):
    """`main()`'s exit code and output for `check`, through argparse."""
    code = gt.main(["check", *args])
    return code, capsys.readouterr().out


@pytest.mark.parametrize("status, expected", STATUS_ROWS,
                         ids=[s for s, _ in STATUS_ROWS])
def test_ground_truth_exercised_is_read_off_the_status_vocabulary(
        gt, monkeypatch, capsys, status, expected):
    """One row of one status, beside clean bookkeeping. GREEN and RED are
    outcomes (exit 0 and exit 1); SKIP, NOT-MEASURED and an unknown status
    are not (exit 2). A `len(verdicts)` guard passes all nine."""
    vocabulary = set(gt.GREEN) | set(gt.RED) | {gt.SKIP, gt.NOT_MEASURED}
    assert vocabulary == set(gt._GLYPH), (
        "the probe prints a status this table does not cover: "
        f"{set(gt._GLYPH) ^ vocabulary}")
    declared = {s for s, _ in STATUS_ROWS} - {MISSPELLED}
    assert vocabulary == declared, (
        f"the table and the probe's vocabulary disagree: {vocabulary ^ declared}")
    assert MISSPELLED not in vocabulary, (
        "the misspelling is a status the probe knows; this row no longer "
        "tests an unknown outcome")
    v = gt.Verdict("rms", "damped_sine", "injected label", status, "injected detail")
    _record(gt, monkeypatch, verdicts=[v])
    code, out = _ground_truth_main(gt, capsys, "--estimator", "typo_1458")
    assert code == expected, _shows(out, f"exit {code}, expected {expected}")
    if expected == NO_VERDICT:
        assert _tail(out) == COUNTED_EMPTY_GROUND_TRUTH, _shows(out, "the verdict")
    elif expected == EXIT_FAIL:
        assert _tail(out, 2) == (
            "1 estimator outcome exercised; 1 check(s) FAILED:\n"
            f"  - {status} rms on damped_sine [injected label]: injected detail"), _shows(
            out, "a red outcome keeps its count and its diagnostic")
        assert EMPTY_GROUND_TRUTH not in out, _shows(
            out, "a red outcome was reported as an empty run")
    else:
        assert _tail(out) == "1 estimator outcome exercised -- All checks passed.", \
            _shows(out, "the verdict")
        assert EMPTY_GROUND_TRUTH not in out, _shows(
            out, "an exercised outcome was reported as an empty run")


def test_ground_truth_skip_and_not_measured_rows_are_not_outcomes(
        gt, monkeypatch, capsys):
    """The input that defeats `len(verdicts)`: a NONEMPTY record of only
    SKIP and NOT-MEASURED rows, beside bookkeeping that passed. Exit 2, with
    both rows still printed."""
    verdicts = [gt.Verdict("rms", "damped_sine", "skip row", gt.SKIP, "injected"),
                gt.Verdict("rms", "two_modes", "unmeasured row", gt.NOT_MEASURED,
                           "injected")]
    _record(gt, monkeypatch, verdicts=verdicts)
    code, out = _ground_truth_main(gt, capsys)
    assert code == NO_VERDICT, _shows(
        out, f"{len(verdicts)} unexercised rows returned {code}, expected 2")
    assert "unmeasured row" in out and "  SKIP rms" in out, _shows(
        out, "the rows must still be printed, not dropped")
    assert _tail(out) == COUNTED_EMPTY_GROUND_TRUTH, _shows(out, "the verdict")


def test_ground_truth_entirely_empty_record_is_no_verdict(gt, monkeypatch, capsys):
    """Every row producer returning an empty iterable of its own type: no
    axis rows, no inventory rows, no conversions, no verdicts. The real
    catalogue is built, as it is on the command line."""
    _record(gt, monkeypatch, verdicts=(), axis=(), inventory=(), conversions=(),
            catalogue=None)
    code, out = _ground_truth_main(gt, capsys)
    assert code == NO_VERDICT, _shows(out, f"exit {code}, expected 2")
    assert _tail(out) == COUNTED_EMPTY_GROUND_TRUTH, _shows(out, "the verdict")


@pytest.mark.parametrize("rows", [(), "skip-only"], ids=["no-rows", "skip-rows"])
@pytest.mark.parametrize("which, producer, failing, diagnostic", AUXILIARY_FAILURES,
                         ids=[a[0] for a in AUXILIARY_FAILURES])
def test_ground_truth_auxiliary_failure_outranks_the_empty_selection(
        gt, monkeypatch, capsys, which, producer, failing, diagnostic, rows):
    """An axis, inventory or conversion failure beside a selection that
    exercised nothing: exit 1 with its diagnostic, never exit 2. Failure
    outranks no verdict, and the count still says zero were exercised."""
    verdicts = () if rows == () else (
        gt.Verdict("rms", "damped_sine", "skip row", gt.SKIP, "injected"),)
    _record(gt, monkeypatch, verdicts=verdicts, **{producer: failing})
    code, out = _ground_truth_main(gt, capsys, "--estimator", "typo_1458")
    assert code == EXIT_FAIL, _shows(
        out, f"exit {code}: a failure outranks NO VERDICT")
    assert _tail(out, 2) == ("0 estimator outcomes exercised; 1 check(s) FAILED:\n"
                             f"{diagnostic}"), _shows(
        out, "the auxiliary failure keeps its diagnostic")
    assert EMPTY_GROUND_TRUTH not in out, _shows(
        out, "a failed run reported NO VERDICT, which run_all renders NO-VERDICT"
             " rather than FAIL")


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
