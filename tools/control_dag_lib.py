"""Shared harness for the injected-defect control tools.

`control_dag_issue_only.py`, `control_dag_verdictless_evidence.py`,
`control_dag_churn.py` and `control_capability_verdicts.py` each keep their
own CONTROLS table -- the per-guard reasoning, which is theirs -- and hand
the mechanics to this module: the subprocess template that injects the
defect, the decision logic that turns one pytest run into held /
did-not-hold, and the `--list` / report / exit-status scaffolding. Until
#1097 that harness existed as three near-verbatim copies; #1099 moved the
fourth, mutation-only copy in (its tables are 3-tuples -- no expected-colour
leg, because every entry expects red).

WHY THE DECISION LOGIC LIVES HERE, ONCE
---------------------------------------
The harness is the part that decides "the suite went RED **and the named
guard is among the failures**". A fix to that sentence -- say, parsing
pytest output differently -- used to have to be made three times, and a
missed copy became a control that quietly stopped being strict: the
false-green failure `docs/verification-rules.md` exists to prevent, pointed
at the controls themselves.

A control tool cannot catch a defect in its own harness. Every control that
genuinely fails its named guard reports identically whether or not the
must_fail check exists, so with the check deleted from `run_control` below
all three tools still exit 0 (measured 2026-11-23 while writing this
module; the wrong-then-right record lives in `tools/test_control_dag_lib.py`,
which was written because the measurement came out that way). That
bootstrapping gap is closed one level down: the test feeds `run_control`
canned pytest output in which the named guard is absent and asserts the
verdict flips. Deleting the must_fail branch turns it red; nothing in the
control tools does.

Nothing on disk is touched by any runner this module formats: the mutated
DAG (or the patched module) exists only inside the control's subprocess and
leaves no state behind.
"""
from __future__ import annotations

import argparse
import pathlib
import subprocess
import sys
import textwrap

ROOT = pathlib.Path(__file__).resolve().parent.parent

#: The suite both node-injecting controls run; churn runs its own.
DEPS_SUITE = "test_compile_dag_deps.py"

#: Subprocess template for node injection: the node is added to a tempfile
#: copy of docs/dag.json and compile_dag.DAG is repointed at it, all before
#: pytest collects.
NODE_RUNNER = """
import json, pathlib, sys, tempfile
ROOT = pathlib.Path({root!r})
sys.path.insert(0, str(ROOT / "tools"))
import compile_dag as cd
doc = json.loads(cd.DAG.read_text())
doc["nodes"]["ZZ"] = {node!r}
tmp = pathlib.Path(tempfile.mkdtemp()) / "dag.json"
tmp.write_text(json.dumps(doc, indent=2))
cd.DAG = tmp
import pytest
sys.exit(pytest.main(["-q", "--no-header", "-p", "no:cacheprovider",
                      str(ROOT / "tools" / {suite!r})]))
"""

#: Subprocess template for in-memory patches: the patch is applied to
#: compile_dag inside the subprocess, before collection.
PATCH_RUNNER = """
import pathlib, sys
ROOT = pathlib.Path({root!r})
sys.path.insert(0, str(ROOT / "tools"))
import compile_dag as cd
{patch}
import pytest
sys.exit(pytest.main(["-q", "--no-header", "-p", "no:cacheprovider",
                      str(ROOT / "tools" / {suite!r})]))
"""

#: Subprocess template for in-memory mutations of an imported module: the
#: extra sys.path roots are inserted, the module is imported under its
#: alias, and the mutation is applied before pytest collects.
MUTATION_RUNNER = """
import pathlib, sys
ROOT = pathlib.Path({root!r})
{path_setup}
import {module} as {alias}
{mutation}
import pytest
sys.exit(pytest.main(["-q", "--no-header", "-p", "no:cacheprovider",
                      str(ROOT / "tools" / {suite!r})]))
"""


def run_raw(code: str) -> tuple[int, list, str]:
    """Run one runner subprocess. Returns (pytest's exit status, the FAILED
    tests, the last line of output)."""
    p = subprocess.run([sys.executable, "-c", code], cwd=str(ROOT),
                       capture_output=True, text=True)
    failed = sorted({ln.split("::")[-1].split(" ")[0]
                     for ln in p.stdout.splitlines() if ln.startswith("FAILED")})
    tail = ((p.stdout.strip() or p.stderr.strip()).splitlines()
            or ["(no output)"])[-1]
    return p.returncode, failed, tail


def run_control(code: str, expect: str, must_fail: str | None) -> tuple[bool, list, str]:
    """Run one injected defect and judge it. Returns (held, failed, why-not).

    This is the sentence the three tools share and the reason this module
    exists (see the docstring): a red suite only counts when the guard the
    control was written for is among the failures, and a green expected
    colour only counts when the suite really stayed green.
    """
    rc, failed, _tail = run_raw(code)
    if expect == "green":
        if rc != 0:
            return False, failed, ("the suite went RED on a shape the guard is "
                                   "meant to PERMIT -- the guard is stricter "
                                   "than its own contract")
        return True, failed, ""
    if rc == 0:
        return False, failed, "the suite stayed GREEN with the defect injected"
    if must_fail not in failed:
        return False, failed, (f"the suite went red but {must_fail} was not among "
                               f"the failures, so this control is satisfied by "
                               f"some OTHER assertion and would survive that "
                               f"one's deletion")
    return True, failed, ""


def node_control(node, expect: str, must_fail: str | None,
                 suite: str = DEPS_SUITE) -> tuple[bool, list, str]:
    """Inject `node` into a tempfile copy of docs/dag.json and judge the run."""
    return run_control(NODE_RUNNER.format(root=str(ROOT), node=node, suite=suite),
                       expect, must_fail)


def patch_control(patch: str, expect: str, must_fail: str | None,
                  suite: str) -> tuple[bool, list, str]:
    """Apply `patch` to compile_dag in memory, inside the subprocess, and judge."""
    return run_control(PATCH_RUNNER.format(root=str(ROOT), patch=patch.strip(),
                                           suite=suite),
                       expect, must_fail)


def patch_suite(patch: str, suite: str) -> tuple[int, list, str]:
    """Run `suite` with `patch` applied (empty = UNMUTATED) and return the raw
    (exit status, FAILED tests, last output line), unjudged."""
    return run_raw(PATCH_RUNNER.format(root=str(ROOT), patch=patch.strip(),
                                       suite=suite))


def mutation_control(mutation: str, must_fail: str, *,
                     module: str = "refprofile", alias: str = "rp",
                     path_roots: tuple[str, ...] = ("tools", "model", "audition"),
                     suite: str = "test_refprofile.py") -> tuple[bool, list, str]:
    """Apply `mutation` to `module` in memory, inside the subprocess, and judge.

    The red-expected twin of `patch_control`: a mutation-only controls table
    has no green leg, so `expect` is fixed to "red" here and never appears
    as a fake leg in the caller's tuples. Defaults reproduce the runner
    `control_capability_verdicts.py` carried before #1099.
    """
    path_setup = "\n".join(
        f'sys.path.insert(0, str(ROOT / "{p}"))' for p in path_roots)
    return run_control(
        MUTATION_RUNNER.format(root=str(ROOT), path_setup=path_setup,
                               module=module, alias=alias,
                               mutation=mutation.strip(), suite=suite),
        "red", must_fail)


def mutation_suite(mutation: str, *,
                   module: str = "refprofile", alias: str = "rp",
                   path_roots: tuple[str, ...] = ("tools", "model", "audition"),
                   suite: str = "test_refprofile.py") -> tuple[int, list, str]:
    """Run `suite` with `mutation` applied (empty = UNMUTATED) and return the
    raw (exit status, FAILED tests, last output line), unjudged: the baseline
    twin of `mutation_control` (#1125)."""
    path_setup = "\n".join(
        f'sys.path.insert(0, str(ROOT / "{p}"))' for p in path_roots)
    return run_raw(MUTATION_RUNNER.format(
        root=str(ROOT), path_setup=path_setup, module=module, alias=alias,
        mutation=mutation.strip(), suite=suite))


def _unpack(entry):
    """Split a controls-table entry into (inject, expect, must_fail, why).

    A 3-tuple (inject, must_fail, why) is a mutation-only table -- every
    entry expects red -- so the colour leg defaults to "red" and `--list`
    renders `caught by` instead of an expects line.
    """
    if len(entry) == 3:
        inject, must_fail, why = entry
        return inject, "red", must_fail, why
    return entry


def main(description, controls, run_one, *, show, ok_message, argv=None,
         baseline=None) -> int:
    """`--list`, the per-control report and the exit status, shared by the tools.

    `controls` is the caller's name -> entry table; a 4-tuple entry is
    (injected, expected colour, the test that must be among the failures
    when red, why) and a 3-tuple entry is (injected, that test, why) -- the
    mutation-only shape, where every control expects red. `show` renders the
    injected thing for `--list` (a JSON node for the node tools, the raw
    patch for churn, the raw mutation for the mutation tool); `ok_message`
    is each tool's own closing line.

    `baseline`, when given, is a zero-argument callable returning the
    UNMUTATED suite's (exit status, FAILED tests, last line). If it is not
    green the tool REFUSES (exit 2) before any control runs: a red under a
    patch would be that test's own, not the patch's (#1125).
    """
    ap = argparse.ArgumentParser(
        description=description, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--list", action="store_true",
                    help="what each control injects, and what must happen")
    a = ap.parse_args(argv)

    if a.list:
        for name, entry in controls.items():
            inject, expect, must_fail, why = _unpack(entry)
            if len(entry) == 3:
                print(f"{name}\n  caught by  {must_fail}")
            else:
                print(f"{name}\n  expects    suite {expect.upper()}"
                      f"{'' if not must_fail else f', failing {must_fail}'}")
            print(f"  why        {why}")
            print(textwrap.indent(show(inject), "  | "))
            print()
        return 0

    if not controls:
        print("REFUSED: the controls table is empty -- no control was run, so "
              "nothing was measured and the ok message was not earned.")
        return 2

    if baseline is not None:
        rc, failed, tail = baseline()
        if rc != 0:
            print(f"REFUSED: the suite exits {rc} before any patch ({tail}) -- "
                  "a red under a patch would be that test's own, not the "
                  "patch's, so no control's red could be attributed to its "
                  "injection. Nothing was measured.")
            for f in failed[:8]:
                print(f"               {f}")
            return 2

    bad = []
    for name in controls:
        held, failed, why = run_one(name)
        expect = _unpack(controls[name])[1]
        label = ("RED " if expect == "red" else "GREEN") if held else "DID NOT HOLD"
        print(f"{label:<14}{name}{'' if held else '  -- ' + why}")
        for f in failed[:8]:
            print(f"               {f}")
        if not held:
            bad.append(name)
    print()
    if bad:
        print(f"FAIL -- {len(bad)} control(s) did not hold: {', '.join(bad)}. "
              f"A run where these do not hold is a broken run, not a quiet one.")
        return 1
    print(ok_message)
    return 0
