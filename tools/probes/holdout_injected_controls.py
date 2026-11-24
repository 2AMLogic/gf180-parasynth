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
`run()` maps it -- 1 with named failures is FIRED, 0 is NO_FIRE, and every
other status (2 a collection error, 4 the path gone, 5 nothing collected, or
1 with no parseable FAILED line) is REFUSED naming the status. A deleted
`tools/test_holdout.py` used to exit 4 in all three copies and print
"every control fired" -- a false green over the exact drift this probe exists
to surface.

And a red is not the injection's red unless the UNMUTATED tree is green
(issue #1124). `run()` decides FIRED from `rc == 1 and failed`, so a
`tools/test_holdout.py` with any always-failing test read as three healthy
controls: this probe printed "every control fired" and exited 0 having
measured nothing. `main()` now runs the unmutated copy once, first, and
REFUSES (exit 2) unless it is green, before any control runs -- no control's
red can mean anything on a suite that is already red. The refusal names the
status and the tests that are red before any injection, and prints pytest's
tail.

And a red is not THIS injection's red unless the tests that went red include
the ones recorded above (issue #1136). `EXPECTED` holds each control's recorded
names as data, and `_verdict()` REFUSES a red that is disjoint from them: the
unmutated tree was green, yet the control's red fell entirely elsewhere (a
flaky or order-dependent test, a defect that arose after the baseline copy), so
"fired" would be a claim about some other defect. Overlap is enough -- a
superset is still FIRED and the unexpected extras are printed as information,
so a newly added test does not make the probe noisy; only a renamed-away or
empty expected set refuses. Exact-set pinning was rejected as coupling the
probe to every test rename.
"""
from __future__ import annotations

import pathlib
import re
import shutil
import subprocess
import sys
import tempfile
from typing import NamedTuple

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


#: The tests recorded above as catching each control (issue #1136), as data so
#: that something compares them. Keep in step with the docstring.
EXPECTED = {
    "GATE_OFF": frozenset({
        "test_a_sealed_holdout_whose_reference_is_not_frozen_is_a_stated_no_verdict",
        "test_an_unsealed_holdout_case_is_refused_and_carries_no_distance",
        "test_an_unsealed_seal_directory_makes_f1d_refuse_for_the_seal_and_not_the_clip",
        "test_the_runner_records_a_read_with_the_seal_hash_and_the_model_state",
    }),
    "CLEAN_OFF": frozenset({"test_a_seal_with_uncommitted_modifications_is_refused"}),
    "STALE_OFF": frozenset({"test_a_seal_edited_after_it_was_read_is_reported_stale"}),
}


def _copy(parent: pathlib.Path) -> pathlib.Path:
    """A throwaway copy of the tree for ONE run -- the baseline's and every
    control's, so the baseline cannot pass by running in an easier tree."""
    work = parent / "tree"
    shutil.copytree(ROOT, work, symlinks=True,
                    ignore=shutil.ignore_patterns("build", ".venv", "__pycache__"))
    return work


def _suite(work: pathlib.Path) -> tuple[int, list[str], str]:
    """`pytest tools/test_holdout.py` inside `work`: its process status, the
    sorted names on its `FAILED tools/test_holdout.py::` lines, its stdout."""
    out = subprocess.run([sys.executable, "-m", "pytest", "tools/test_holdout.py",
                          "-q", "--no-header", "-p", "no:cacheprovider"],
                         cwd=work, capture_output=True, text=True)
    failed = sorted({ln.split("::")[1].split()[0]
                     for ln in out.stdout.splitlines()
                     if ln.startswith("FAILED tools/test_holdout.py::")})
    return out.returncode, failed, out.stdout


def _verdict(rc: int, failed: list[str],
             expected: frozenset[str] = frozenset()) -> Result:
    """pytest's status in this probe's outcome space (#1118): 1 with named
    failures is FIRED, 0 is NO_FIRE, and every other status -- 2 a collection
    error, 4 the path gone, 5 nothing collected -- or 1 with no parseable
    FAILED line is REFUSED naming the status. A red disjoint from `expected` --
    or with no `expected` to compare against -- is REFUSED too (#1136): it is
    not attributable to the injection."""
    if rc == 0:
        return Result(NO_FIRE, failed, "pytest stayed green")
    if rc == 1 and failed:
        if not expected:
            return Result(REFUSED, failed,
                          "no expected test names are recorded for this control, "
                          "so its red cannot be attributed to the injection")
        if not expected & set(failed):
            return Result(REFUSED, failed,
                          f"red but none of the expected tests caught it: expected "
                          f"{sorted(expected)}, parsed {failed}; the red is not "
                          f"this injection's")
        return Result(FIRED, failed, "pytest exited 1")
    if rc == 1:
        return Result(REFUSED, [],
                      "pytest exited 1 but no FAILED tools/test_holdout.py::"
                      " line was parsed; nothing was measured")
    return Result(REFUSED, [], f"pytest exited {rc}; nothing was measured")


def run(name: str) -> Result:
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
        rc, failed, _stdout = _suite(work)
        return _verdict(rc, failed, EXPECTED.get(name, frozenset()))


def baseline() -> Result:
    """The UNMUTATED tree, run once before any control (issue #1124): NO_FIRE
    when it is green, because only then can a control's red be the injection's.
    Anything else is REFUSED -- on a suite that is red on its own, every
    control's "fired" measures nothing."""
    with tempfile.TemporaryDirectory() as tmp:
        rc, failed, stdout = _suite(_copy(pathlib.Path(tmp)))
    if rc == 0:
        return Result(NO_FIRE, [], "pytest stayed green on the UNMUTATED tree")
    if failed:
        what = (f"pytest exited {rc} on the UNMUTATED tree, {len(failed)} test(s) red "
                f"before any injection ({', '.join(failed)})")
    elif rc == 1:
        what = ("pytest exited 1 on the UNMUTATED tree with no FAILED "
                "tools/test_holdout.py:: line parsed")
    else:
        what = f"pytest exited {rc} on the UNMUTATED tree"
    return Result(REFUSED, failed, f"{what} -- no control's red can mean anything",
                  stdout[-TAIL:])


def main() -> int:
    base = baseline()
    if base.outcome == REFUSED:
        print(f"BASELINE: REFUSED -- {base.reason}")
        print(base.tail.rstrip())
        return 2
    print(f"BASELINE: {base.reason}")
    bad = 0      # a control genuinely did not fire: a defect in the seal
    refused = 0  # nothing was measured: the apparatus cannot run
    for name in INJECTS:
        res = run(name)
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
        extra = sorted(set(res.failed) - EXPECTED[name])
        if extra:
            print(f"    (not in the recorded set, information only: "
                  f"{', '.join(extra)})")
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
    print("every control fired")
    return 0


if __name__ == "__main__":
    sys.exit(main())
