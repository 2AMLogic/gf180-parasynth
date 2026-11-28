#!/usr/bin/env python3
"""Start red: three injected defects, each of which MUST turn
`tools/test_holdout.py` red, and the list of tests that caught each one.

    python3 tools/probes/holdout_injected_controls.py

A suite that passes the first time it is run has told you nothing about what it
would have caught (CLAUDE.md: start red, carry injected-bug controls). The
sealing mechanism has three assertions that a reader has to take on trust
otherwise, so each one is deleted here in turn, in a copy of the tree, and the
suite is re-run against it:

    GATE_OFF       tools/run_case.py stops asking holdout.assert_readable
    CLEAN_OFF      holdout.seal_git_state stops checking `git diff HEAD`
    STALE_OFF      holdout.check stops comparing the ledger's seal hash

Recorded result (2026-09-28, 21 tests):

    GATE_OFF    4 caught it: test_a_sealed_holdout_whose_reference_is_not_frozen
                _is_a_stated_no_verdict, test_an_unsealed_holdout_case_is_refused
                _and_carries_no_distance, test_an_unsealed_seal_directory_makes
                _f1d_refuse_for_the_seal_and_not_the_clip, test_the_runner
                _records_a_read_with_the_seal_hash_and_the_model_state
    CLEAN_OFF   1 caught it: test_a_seal_with_uncommitted_modifications_is_refused
    STALE_OFF   1 caught it: test_a_seal_edited_after_it_was_read_is_reported_stale

`test_an_injected_control_never_consumes_a_sealed_reading` deliberately does NOT
catch GATE_OFF, and that is right: with the gate gone nothing is recorded at all,
so its assertion (an empty ledger) still holds. A control whose absence is
explained is worth more than one counted as coverage it does not provide.

Each injection is deleted in a COPY of the worktree, never in place: a control
that can leave the tree broken behind it is a control nobody runs twice.

pytest's own process exit status is NOT this probe's outcome (issue #1118):
`run()` maps it -- 1 with an attributed failure is FIRED and 0 after a clean,
executed, completed suite run with no phase errors is NO_FIRE. A contradictory
status, non-execution (e.g. `--collect-only`), phase errors, or a run that
could establish neither outcome is REFUSED naming the status. A deleted
`tools/test_holdout.py` used to exit 4 in all three copies and print
"every control fired" -- a false green over the exact drift this probe exists
to surface.

And a red is not the injection's red unless the UNMUTATED tree is green
(issue #1124). `run()` once decided FIRED from `rc == 1 and failed`, so a
`tools/test_holdout.py` with any always-failing test read as three healthy
controls: this probe printed "every control fired" and exited 0 having
measured nothing. `main()` now runs the unmutated copy once, first, and
REFUSES (exit 2) unless it is green, before any control runs -- no control's
red can mean anything on a suite that is already red. The refusal names the
status and the tests that are red before any injection, and prints pytest's
tail.

A suite with no control declared has measured nothing either (#1466): an
empty `INJECTS` printed "every control fired" and exited 0, because `bad` and
`refused` were both zero over a loop that never ran. The verdict line now
counts the controls it fired, and zero declared is NO VERDICT (exit 2) before
the baseline copy is made.

Both this probe and the sound probe require a recorded test to fail at its
intended assertion (#1136, #1400). A setup error, another assertion in the
same test, or an exception inside the code under test cannot earn credit.
One matching failure from a test that passed on the baseline is sufficient.
A survivor requires the collected checks for that control to pass in both runs.
Other failures are reported separately.
"""
from __future__ import annotations

import json
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile
from typing import NamedTuple, Sequence

ROOT = pathlib.Path(__file__).resolve().parent.parent.parent

#: The probe's own outcome space, deliberately disjoint from any subprocess's
#: exit status: an integer returned here is what let pytest's statuses be read
#: as verdicts (#1118).
FIRED = "fired"          # pytest went red and the failures are named
NO_FIRE = "no-fire"      # pytest stayed green with the assertion deleted
REFUSED = "refused"      # nothing was measured; `reason` says why

#: How much of pytest's output a red baseline prints -- the window
#: tools/r1_harvest_controls.py shows for its clean run. The tail is where the
#: short summary and the `N failed` line are, which is what names the red.
TAIL = 2000


class Result(NamedTuple):
    outcome: str
    failed: list[str]
    reason: str
    tail: str = ""        # pytest's own last lines, where they must be seen
    other: tuple[str, ...] = ()  # failed, but did not earn credit


class Site(NamedTuple):
    statement: str
    exception: str = "AssertionError"


class Failure(NamedTuple):
    statement: str
    exception: str
    reason: str

    def matches(self, site: Site) -> bool:
        # An assert can carry a diagnostic after the condition.
        at_site = (self.statement == site.statement
                   or self.statement.startswith(site.statement + ","))
        return at_site and site.exception in (self.exception, self.reason)


class SuiteReport(NamedTuple):
    failures: dict[str, Failure]
    outcomes: dict[str, str]  # suite-relative pytest IDs, including parameter IDs
    errors: tuple[str, ...] = ()
    executed: int = 0
    completed: int = 0
    deselected: int = 0

    @property
    def passed(self) -> set[str]:
        return {test for test, outcome in self.outcomes.items() if outcome == "passed"}


class Baseline(NamedTuple):
    report: SuiteReport | None  # present only for a clean, completed run
    reason: str
    tail: str = ""


INJECTS = {
    "GATE_OFF": ("tools/run_case.py",
                 r"    if is_holdout\(case\):",
                 "    if False and is_holdout(case):"),
    "CLEAN_OFF": ("tools/holdout.py",
                  r'    if _git\("diff", "--name-only", "HEAD", "--", rel\)\.strip\(\):',
                  '    if False:'),
    "STALE_OFF": ("tools/holdout.py",
                  r'        if e\.get\("generation"\) == seal\["generation"\] and \\',
                  '        if False and \\'),
}


# Pin the statement as well as the test. The GATE_OFF KeyErrors specifically
# identify the missing holdout block, even where the assertion reads two keys.
EXPECTED = {
    "GATE_OFF": {
        "test_a_sealed_holdout_whose_reference_is_not_frozen_is_a_stated_no_verdict":
            Site('assert res["holdout"]["state"] == ho.SEALED', "KeyError: 'holdout'"),
        "test_an_unsealed_holdout_case_is_refused_and_carries_no_distance":
            Site('assert res["holdout"]["state"] == "unsealed"', "KeyError: 'holdout'"),
        "test_an_unsealed_seal_directory_makes_f1d_refuse_for_the_seal_and_not_the_clip":
            Site('assert "not committed" in res["note"]'),
        "test_the_runner_records_a_read_with_the_seal_hash_and_the_model_state":
            Site('assert res["holdout"]["ledger_read"] == 1', "KeyError: 'holdout'"),
    },
    "CLEAN_OFF": {
        "test_a_seal_with_uncommitted_modifications_is_refused":
            Site("with pytest.raises(ho.Refused) as e:", "Failed"),
    },
    "STALE_OFF": {
        "test_a_seal_edited_after_it_was_read_is_reported_stale": Site("assert code == ho.FAIL"),
    },
}
SUITE = "tools/test_holdout.py"


def _copy(parent: pathlib.Path) -> pathlib.Path:
    """A throwaway copy of the tree for ONE run -- the baseline's and every
    control's, so the baseline cannot pass by running in an easier tree."""
    work = parent / "tree"
    shutil.copytree(ROOT, work, symlinks=True,
                    ignore=shutil.ignore_patterns("build", ".venv", "__pycache__"))
    return work


def run_suite(work: pathlib.Path, suite: str, targets: Sequence[str] = (), *,
              python: str = sys.executable,
              pytest_options: Sequence[str] = ()) -> tuple[int, SuiteReport | None, str]:
    """Run one pytest suite and record its outcomes, phase errors, and failure sites.

    `suite` is the identity every recorded node ID is labelled against. With
    `targets` empty the whole suite is collected; otherwise exactly those node
    IDs are, so a focused run deselects nothing (`_zero_exit_problem` refuses
    any deselection) and `suite` still decides what counts as in-suite. A caller
    can request collection with `pytest_options` and read the selected IDs from
    the returned report."""
    runner = pathlib.Path(__file__).with_name("pytest_failure_sites.py")
    with tempfile.TemporaryDirectory() as tmp:
        report = pathlib.Path(tmp) / "report.json"
        out = subprocess.run([python, str(runner), suite, str(report),
                              *(targets or [suite]), *pytest_options],
                             cwd=work, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        try:
            data = json.loads(report.read_text())
            failures = {name: Failure(*site) for name, site in data["failures"].items()}
            suite_report = SuiteReport(
                failures=failures,
                errors=tuple(data["errors"]),
                outcomes=data["outcomes"],
                executed=int(data["executed"]),
                completed=int(data["completed"]),
                deselected=int(data["deselected"]),
            )
        except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError):
            return out.returncode, None, out.stdout
    return out.returncode, suite_report, out.stdout


def _zero_exit_problem(report: SuiteReport, suite: str) -> str | None:
    """Return why an exit-0 report cannot establish a clean run, or None."""
    if report.failures:
        names = sorted(report.failures)
        return f"recorded failures in {suite}: " + ", ".join(names)
    if report.errors:
        return f"phase error(s) in {suite}: " + ", ".join(report.errors)
    collected = len(report.outcomes)
    if not collected:
        return f"collected no tests for {suite}"
    if report.deselected > 0:
        return f"{report.deselected} test(s) deselected in {suite}"
    if report.executed == 0:
        return f"executed no tests for {suite} ({collected} collected)"
    if not report.passed:
        return f"no test passed in {suite}"
    if report.completed < collected:
        return (f"only {report.completed}/{collected} collected test(s) "
                f"completed in {suite}")
    return None


def verdict(rc: int, report: SuiteReport | None,
            expected: dict[str, Site], *, suite: str, baseline: SuiteReport) -> Result:
    """Credit only recorded assertions that actually failed under the mutant."""
    if report is None:
        return Result(REFUSED, [], f"pytest exited {rc} without a failure report for {suite}")
    if rc == 0:
        problem = _zero_exit_problem(report, suite)
        if problem is not None:
            return Result(REFUSED, sorted(report.failures), f"pytest exited 0: {problem}")
        paired = baseline.passed & report.passed
        # An insensitive suite can collect none of the recorded tests. If it
        # does collect one in either run, skipping or losing it can hide a kill.
        recorded = expected.keys() & (baseline.outcomes.keys() | report.outcomes.keys())
        missing = sorted(recorded - paired)
        if missing:
            details = ", ".join(f"{name} (baseline {baseline.outcomes.get(name, 'absent')}, "
                                f"mutant {report.outcomes.get(name, 'absent')})" for name in missing)
            return Result(REFUSED, [], f"recorded checks did not pass in both runs: {details}")
        if not paired:
            return Result(REFUSED, [], f"no test in {suite} passed in both runs")
        return Result(NO_FIRE, [], "pytest stayed green")
    if rc == 1 and report.failures:
        names = sorted(report.failures)
        if not expected:
            return Result(REFUSED, names, "no expected assertions are recorded for this control, "
                          "so its red cannot be attributed to the injection")
        at_site = {name for name in expected.keys() & report.failures.keys()
                   if report.failures[name].matches(expected[name])}
        caught = sorted(at_site & baseline.passed)
        if caught:
            return Result(FIRED, caught, "pytest exited 1",
                          other=tuple(sorted(report.failures.keys() - set(caught))))
        if at_site:
            return Result(REFUSED, names, "intended failure(s) in tests that did not pass "
                          "on the clean baseline: " + ", ".join(sorted(at_site)))
        details = ", ".join(f"{name} ({report.failures[name].reason})" for name in names)
        return Result(REFUSED, names, f"no intended assertion failed: expected {sorted(expected)}, "
                      f"observed {details}")
    if rc == 1:
        suffix = f" ({', '.join(report.errors)})" if report.errors else ""
        return Result(REFUSED, [],
                      f"pytest exited 1 but no call failure was recorded for {suite}{suffix}")
    return Result(REFUSED, [], f"pytest exited {rc}; nothing was measured")


def run(name: str, baseline: SuiteReport) -> Result:
    rel, pattern, replacement = INJECTS[name]
    with tempfile.TemporaryDirectory() as tmp:
        work = _copy(pathlib.Path(tmp))
        target = work / rel
        text = target.read_text()
        # A lambda, not a template: a replacement containing a backslash is not
        # a regex escape here and must not be parsed as one.
        new, n = re.subn(pattern, lambda _m: replacement, text, count=1)
        if n != 1:
            return Result(REFUSED, [],
                          f"the injection point is gone from {rel}; this "
                          f"control cannot fire and must be repaired, not "
                          f"skipped")
        target.write_text(new)
        rc, report, _stdout = run_suite(work, SUITE)
        return verdict(rc, report, EXPECTED.get(name, {}), suite=SUITE, baseline=baseline)


def run_baseline(work: pathlib.Path, suite: str, targets: Sequence[str] = (), *,
                 python: str = sys.executable) -> Baseline:
    """Run the unmutated suite. Both probes require exit 0 and a clean, executed run."""
    rc, report, stdout = run_suite(work, suite, targets, python=python)
    if report is None:
        what = f"pytest exited {rc} on the UNMUTATED tree without a failure report"
    elif report.failures:
        names = sorted(report.failures)
        what = (f"pytest exited {rc} on the UNMUTATED tree, {len(names)} test(s) red "
                f"before any injection ({', '.join(names)})")
    elif rc == 0:
        problem = _zero_exit_problem(report, suite)
        if problem is None:
            return Baseline(report, "pytest stayed green on the UNMUTATED tree")
        what = f"pytest exited 0 on the UNMUTATED tree: {problem}"
    elif report.errors:
        what = (f"pytest exited {rc} on the UNMUTATED tree with phase error(s) in {suite}: "
                + ", ".join(report.errors))
    elif rc == 1:
        what = f"pytest exited 1 on the UNMUTATED tree with no call failure recorded for {suite}"
    else:
        what = f"pytest exited {rc} on the UNMUTATED tree"
    return Baseline(None, f"{what} -- no control's red can mean anything", stdout[-TAIL:])


def baseline() -> Baseline:
    with tempfile.TemporaryDirectory() as tmp:
        return run_baseline(_copy(pathlib.Path(tmp)), SUITE)


def main() -> int:
    # #1466: with no control declared there is nothing for the baseline to
    # precondition, and the verdict below would print "every control fired"
    # over zero controls. Refused before the tree is copied.
    if not INJECTS:
        print("NO VERDICT -- no control is declared (INJECTS is empty): "
              "a pass over zero controls has shown nothing")
        return 2
    base = baseline()
    if base.report is None:
        print(f"BASELINE: REFUSED -- {base.reason}")
        print(base.tail.rstrip())
        return 2
    print(f"BASELINE: {base.reason}")
    bad = 0      # a control genuinely did not fire: a defect in the seal
    refused = 0  # nothing was measured: the apparatus cannot run
    for name in INJECTS:
        res = run(name, base.report)
        if res.outcome == REFUSED:
            print(f"{name}: REFUSED -- {res.reason}")
            refused += 1
            continue
        if res.outcome == NO_FIRE:
            print(f"{name}: CONTROL DID NOT FIRE -- the suite stayed green with the "
                  f"assertion deleted")
            bad += 1
            continue
        print(f"{name}: red, {len(res.failed)} test(s) caught it")
        for f in res.failed:
            print(f"    {f}")
        if res.other:
            print(f"    (other failures, information only: {', '.join(res.other)})")
    # A seal defect (bad) outranks a refusal: 1 when any control genuinely did
    # not fire (a mix included), else 2 when any refused, else 0. Exit 2 is the
    # runner's NO-VERDICT channel (tools/run_all.py, tools/exit_contract.py) --
    # the refusal reasons above name what could not be measured, and a refused
    # baseline took the same exit above, before any control ran.
    if bad:
        print(f"{bad} control(s) did not fire"
              + (f", {refused} refused" if refused else ""))
        return 1
    if refused:
        print(f"{refused} control(s) refused -- the reasons are above; "
              f"nothing was measured")
        return 2
    print(f"every control fired ({len(INJECTS)})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
