#!/usr/bin/env python3
"""Injected-defect controls for the refused-simulation publication (#1631).

    python tools/control_publish_selected_refusal.py          every control must fire
    python tools/control_publish_selected_refusal.py --list   what each one injects

    exit 0   every control fired, from a suite that was green with no mutation
    exit 1   a control did not fire
    exit 2   REFUSED: the suite is not green before any mutation (#1125)

WHY THIS FILE EXISTS
--------------------
`fpga/publish_selected.py` records a refused stored simulation as a NO-VERDICT
publication with the refusal as its reason, exit 2. Before #1631 the
`RuntimeError` escaped `publish()` as a traceback at exit 1. The tests in
`fpga/test_publish_selected.py` were red against that, which is evidence once;
from then on they are green, and a green suite does not say which of its
assertions a later edit could delete without noticing. #1595 changes what
`build_selected.simulation_evidence()` checks, which is the neighbourhood of
the edit that would put the traceback back.

So each entry below re-breaks `publish()` a different way, in memory, and this
tool asserts the suite turns RED **and names the test that must be among the
failures**. The mutation is a text substitution applied to the source of
`publish`, re-executed into the imported module inside the subprocess, before
pytest collects. Nothing on disk is touched, so this is safe to run beside
anything else and leaves no state behind.

WHAT THIS CANNOT REACH
----------------------
`test_cli_exits_2_without_a_traceback_when_the_stored_simulation_is_refused`
runs the file from disk (`runpy.run_path`), so it never sees an in-memory
mutation and stays green under every entry here. The exit status of
`python fpga/publish_selected.py` with the fix reverted is a one-time record in
the #1631 pull request, not something this tool re-checks.
"""
from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from control_dag_lib import ROOT, main as lib_main, run_control, run_raw  # noqa: E402

SUITE = "test_publish_selected.py"
REFUSED = "test_refused_stored_simulation_is_a_no_verdict_publication_not_a_traceback"
NOT_MASKED = "test_a_genuine_bug_is_not_masked_as_a_refusal"

RUNNER = """
import inspect, pathlib, sys
ROOT = pathlib.Path({root!r})
sys.path.insert(0, str(ROOT / "fpga"))
import publish_selected as ps
{mutation}
import pytest
sys.exit(pytest.main(["-q", "--no-header", "-p", "no:cacheprovider",
                      str(ROOT / "fpga" / {suite!r})]))
"""

PATCH = """
import inspect
source = inspect.getsource(ps.publish)
patched = source.replace({old!r}, {new!r})
assert patched != source, "the injection site is gone: this control no longer injects anything"
exec(patched, vars(ps))
"""

AT_THE_CALL = "except RuntimeError as exc:"
BUILD_HANDLER = "except (ValueError, FileNotFoundError, KeyError) as exc:"

#: name -> (the text replaced inside `publish`, its replacement), the test that
#: MUST be among the failures, why it is a plausible way for the fix to be undone
CONTROLS = {
    "REFUSAL_ESCAPES_AGAIN": (
        (AT_THE_CALL, "except AssertionError as exc:"),
        f"{REFUSED}[BUILT]",
        "the exact broken behaviour of #1631, and what a change to the "
        "exception `simulation_evidence()` raises produces: the refusal is no "
        "longer caught at the call and leaves `publish()` as a traceback"),
    "REFUSAL_RECORDED_BUT_BUILD_QUALIFIED": (
        ("        if sim_refusal is not None:\n"
         "            raise ValueError(sim_refusal)\n", ""),
        f"{REFUSED}[BUILT]",
        "the catch kept and the consequence dropped: the refusal is captured "
        "and the build checks then qualify the bitstream over it"),
    "REFUSAL_REASON_REPLACED": (
        ("sim_refusal = None, str(exc)", 'sim_refusal = None, "stored simulation refused"'),
        f"{REFUSED}[BUILT]",
        "NO-VERDICT with a reason that no longer says which file differs from "
        "which recorded simulation, so nothing on the summary names the repair"),
    "FAIL_REPORT_PROMOTED_OVER_THE_REFUSAL": (
        ('if report["state"] == "FAIL" and sim_refusal is None:', 'if report["state"] == "FAIL":'),
        f"{REFUSED}[FAIL]",
        "a failed build whose simulation is also refused reported FAIL beside a "
        "reason about the simulation, which reads as the build failing BECAUSE "
        "of it; the build's own failure belongs to the copied report.json"),
    "BINDING_CHECKED_AGAINST_NO_EVIDENCE": (
        ('if sim_refusal is None and simulation != report["simulation_evidence"]:',
         'if simulation != report["simulation_evidence"]:'),
        f"{REFUSED}[BUILT]",
        "the catch added and the comparison guard forgotten: `None` is compared "
        "with the recorded evidence and the mismatch leaves as a ValueError"),
    "BUILD_HANDLER_WIDENED_TO_RUNTIME_ERROR": (
        (BUILD_HANDLER, "except (ValueError, FileNotFoundError, KeyError, RuntimeError) as exc:"),
        f"{NOT_MASKED}[runtime-error-after-the-call]",
        "'also catch RuntimeError' applied to the wrong handler: a bug raised "
        "by the timing reader is filed as a NO-VERDICT publication instead of "
        "surfacing, which is the genuine bug the fix must not swallow"),
    "BARE_EXCEPT_AT_THE_CALL": (
        (AT_THE_CALL, "except Exception as exc:"),
        f"{NOT_MASKED}[other-exception-at-the-call]",
        "'also catch everything' applied at the call: a malformed record's "
        "TypeError is reported as a refusal, with its message, instead of "
        "surfacing as the bug it is"),
}


def run_suite(mutation: str) -> tuple[int, list, str]:
    """Run the suite with `mutation` applied to `publish` (empty = UNMUTATED)
    and return the raw (exit status, FAILED tests, last output line)."""
    return run_raw(RUNNER.format(root=str(ROOT), mutation=mutation, suite=SUITE))


def run_one(name: str) -> tuple[bool, list, str]:
    """Apply one mutation and run the suite. Returns (fired, failed tests, why)."""
    (old, new), must_fail, _why = CONTROLS[name]
    return run_control(RUNNER.format(root=str(ROOT), mutation=PATCH.format(old=old, new=new),
                                     suite=SUITE),
                       "red", must_fail)


def main(argv=None) -> int:
    return lib_main(
        __doc__, CONTROLS, run_one,
        show=lambda inject: f"{inject[0]!r}\n     -> {inject[1]!r}",
        baseline=lambda: run_suite(""),
        ok_message=f"OK -- all {len(CONTROLS)} refused-simulation controls "
                   f"fired, each on the test it was written for.",
        argv=argv)


if __name__ == "__main__":
    sys.exit(main())
