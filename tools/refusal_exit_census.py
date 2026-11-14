#!/usr/bin/env python3
"""Census of the exit statuses this repository refuses with, and whether
`tools/run_all.py` honours each of them.

HISTORY, because the pin below depends on it.  This tool was written when
`tools/run_all.py` implemented the repo's "refusal is no evidence" contract
with ONE integer, `VERIFIER_NO_EVIDENCE = 2`, and reported every other
non-zero status as `FAIL`.  The repository actually refused with three
statuses (2, 3 and run_with_rss's 126), so 15 of 16 counted refusal sites were
rendered as results -- "an unknown rendered in the place where a result
belongs", the exact defect `run_all.py` was written to prevent.  That defect
is repaired (issue #804): the statuses are now declared, by outcome, in
`tools/exit_contract.py`, and the runner imports `NO_EVIDENCE` from there.

WHAT IT CHECKS NOW.  A repair does not make a census obsolete; it changes what
the census guards:

  * the declared no-evidence set must stay {2, 3, 126} -- an accidental edit to
    the contract turns `--check` red;
  * `tools/run_all.py` must still honour the declaration -- if the import or
    the comparison is removed, this tool REFUSES rather than answering from a
    stale assumption;
  * every refusal site the detectors find must sit INSIDE the declared set,
    except the pinned, documented exceptions.  A new tool refusing with, say,
    4 lands outside it and turns `--check` red until the status is declared
    in `tools/exit_contract.py`.  That is the guard that keeps the next
    refusal family from being silently rendered as FAIL again;
  * per-family totals are pinned, so drift (a new refusal site, a repaired
    one) shows up as a failing pin rather than as a quietly different table.

    python tools/refusal_exit_census.py            # the census
    python tools/refusal_exit_census.py --check    # self-test (pinned answers)

PRECONDITIONS, ASSERTED AT THE POINT OF USE, REFUSED (exit 2) WHEN UNMET:

  * `git ls-files` must run from this repository, and must list at least 300
    Python files.  A census over a partial tree would under-report the
    families and read exactly like a census over the whole one.
  * `tools/exit_contract.py` must parse and must declare `NO_EVIDENCE` as a
    set/frozenset of int literals, and `tools/run_all.py` must import it.
    The "which side does run_all land on" column is read from the contract's
    source, never retyped here; if the declaration or the import is gone,
    this tool cannot answer.

WHAT IT CANNOT TELL YOU.  A handler that returns a refusal status through a
named constant (`return REFUSED`) is invisible to a literal-only return scan
-- `pnr/shuttle/` does exactly that, and after #804 `tools/cymbal_low_tail.py`
imports the contract constant rather than retyping 2.  The handler pass below
therefore resolves two name forms: a module-level int assignment in the same
file, and a name imported from `tools/exit_contract.py`.  Returns it cannot
resolve are not counted (documented blind spot, not a zero).  Module-level
`REFUSED = <int>` declarations are reported by the constant pass separately;
the two passes are reported separately rather than summed, because a sum
would hide which detector found what.  `tools/exit_contract.py` itself is
excluded from both passes: it DECLARES the statuses, it does not refuse with
them.
"""

from __future__ import annotations

import argparse
import ast
import collections
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]

MIN_PY_FILES = 300
CONTRACT = pathlib.Path("tools") / "exit_contract.py"
RUNNER = pathlib.Path("tools") / "run_all.py"

# Exception classes that mean "a precondition is not met, so nothing was
# measured".  Taken from the classes actually raised in this tree; a class
# missing here under-counts, which is why --check pins the totals.
REFUSAL_CLASSES = frozenset({
    "Refused", "SourceUnavailable", "ScanRefused", "KitRefused",
    "MidiPortRefused", "ControlRefused", "StaleBase",
    "Infeasible", "CalibrationError", "REFUSED_TYPES",
})

# WRONG BEFORE IT WAS RIGHT, recorded because the first table this tool printed
# was quotable and wrong.  `Rejected` was in the set above, which made
# `tools/check_f1_rtl_record.py:153` read as a refusal reported as exit 1 --
# the headline defect this census existed to count.  It is not one.  There are
# TWO unrelated `Rejected` classes in this tree and they mean opposite things:
#
#   tools/check_f1_rtl_record.py:48   a VERDICT.  "REJECTED <record> -- ..."
#                                     exits 1 because the record was read and
#                                     found wanting.  Exit 1 is correct there.
#   fpga/release/qualified_domain.py:96  a REFUSAL.  fpga/uart_host.py:1993
#                                     prints "REFUSED -- outside the qualified
#                                     release domain" and exits 2.
#
# A detector keyed on the class NAME cannot separate them, so the name is
# excluded and the uart_host site is not counted.


class Refused(Exception):
    """A precondition failed.  REFUSED is a verdict, distinct from a value."""


def tracked_python() -> list[str]:
    """Every tracked `.py` path.  REFUSES if git cannot answer or the tree is
    too small to be the whole repository."""
    try:
        out = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True,
                             text=True, check=True).stdout
    except (OSError, subprocess.CalledProcessError) as exc:
        raise Refused(f"git ls-files could not run in {ROOT}: {exc}") from exc
    files = [f for f in out.split() if f.endswith(".py")]
    if len(files) < MIN_PY_FILES:
        raise Refused(
            f"git ls-files found {len(files)} Python files, under the {MIN_PY_FILES} "
            f"this repository has; a partial tree under-reports the census")
    return files


def _module_int_assigns(tree: ast.Module) -> dict[str, int]:
    """Module-level `NAME = <int literal>` declarations."""
    out: dict[str, int] = {}
    for st in tree.body:
        if not isinstance(st, ast.Assign):
            continue
        if not (isinstance(st.value, ast.Constant) and isinstance(st.value.value, int)
                and not isinstance(st.value.value, bool)):
            continue
        for t in st.targets:
            if isinstance(t, ast.Name):
                out[t.id] = st.value.value
    return out


def _contract_ints(tree: ast.Module) -> dict[str, int]:
    """The contract's named outcomes, read from its own source."""
    assigns = _module_int_assigns(tree)
    missing = [n for n in ("PASS", "FAIL", "REFUSED") if n not in assigns]
    if missing:
        raise Refused(f"{CONTRACT} declares no module-level int for {missing}; "
                      f"the outcome names cannot be read statically")
    return assigns


def _contract_no_evidence(tree: ast.Module) -> frozenset[int]:
    """`NO_EVIDENCE`, read out of the contract by AST.  REFUSES if the
    declaration is gone, so the census cannot answer from a stale assumption."""
    ints = _contract_ints(tree)
    for st in tree.body:
        if not (isinstance(st, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id == "NO_EVIDENCE" for t in st.targets)):
            continue
        call = st.value
        names = None
        if isinstance(call, ast.Call) and isinstance(call.func, ast.Name) \
                and call.func.id in ("set", "frozenset"):
            names = call.args[0] if call.args else None
        elif isinstance(call, (ast.Set, ast.List, ast.Tuple)):
            names = call
        if isinstance(names, (ast.Set, ast.List, ast.Tuple)):
            out = set()
            for e in names.elts:
                if isinstance(e, ast.Name) and e.id in ints:
                    out.add(ints[e.id])
                elif isinstance(e, ast.Constant) and isinstance(e.value, int) \
                        and not isinstance(e.value, bool):
                    out.add(e.value)
                else:
                    raise Refused(f"{CONTRACT}: NO_EVIDENCE holds something this "
                                  f"tool cannot resolve to an int statically")
            if not out:
                raise Refused(f"{CONTRACT}: NO_EVIDENCE is empty; the runner "
                              f"would report every refusal as FAIL")
            return frozenset(out)
        raise Refused(f"{CONTRACT}: NO_EVIDENCE is not a set/frozenset literal "
                      f"this tool can read; the runner's contract cannot be "
                      f"read statically")
    raise Refused(f"{CONTRACT} declares no NO_EVIDENCE; the runner's "
                  f"no-evidence set cannot be read")


def _runner_honours_contract(runner_tree: ast.Module) -> None:
    """REFUSES unless tools/run_all.py imports NO_EVIDENCE from the contract
    and compares against it.  The census reads the contract, not the runner;
    this check is the one thing tying that read to what the runner does."""
    imported = False
    used = False
    for st in ast.walk(runner_tree):
        if isinstance(st, ast.ImportFrom) and st.module == "exit_contract":
            for a in st.names:
                if (a.name == "NO_EVIDENCE" and not a.asname) or a.asname == "NO_EVIDENCE":
                    imported = True
        if isinstance(st, ast.Compare) and any(
                isinstance(c, ast.Name) and c.id == "NO_EVIDENCE" for c in st.comparators):
            used = True
    if not imported:
        raise Refused(f"{RUNNER} no longer imports NO_EVIDENCE from "
                      f"{CONTRACT}; the census's read of the contract no "
                      f"longer describes the runner, so it cannot answer")
    if not used:
        raise Refused(f"{RUNNER} imports NO_EVIDENCE but never compares "
                      f"against it; the census's read no longer describes "
                      f"the runner, so it cannot answer")


def _exit_contract_imports(tree: ast.Module) -> dict[str, str]:
    """`from exit_contract import X [as Y]` bindings: local name -> contract name."""
    out: dict[str, str] = {}
    for st in ast.walk(tree):
        if isinstance(st, ast.ImportFrom) and st.module == "exit_contract":
            for a in st.names:
                out[a.asname or a.name] = a.name
    return out


def _handled_classes(node: ast.ExceptHandler) -> list[str]:
    t = node.type
    if t is None:
        return []
    elts = t.elts if isinstance(t, ast.Tuple) else [t]
    names = []
    for e in elts:
        if isinstance(e, ast.Name):
            names.append(e.id)
        elif isinstance(e, ast.Attribute):
            names.append(e.attr)
    return names


def _return_status(value, local: dict[str, int], imported: dict[str, str],
                   contract: dict[str, int]) -> int | None:
    """The int a Return's value resolves to, or None.  Literals always;
    names only when they resolve to a same-file int assignment or an
    exit_contract import."""
    if isinstance(value, ast.Constant) and isinstance(value.value, int) \
            and not isinstance(value.value, bool):
        return value.value
    if isinstance(value, ast.Name):
        if value.id in local:
            return local[value.id]
        if value.id in imported and imported[value.id] in contract:
            return contract[imported[value.id]]
    if isinstance(value, ast.Attribute) and isinstance(value.value, ast.Name) \
            and value.value.id == "exit_contract" and value.attr in contract:
        return contract[value.attr]
    return None


def handler_census(files: list[str], contract: dict[str, int]) -> dict[int, list[tuple[str, int]]]:
    """Per exit status, the `except <refusal>: ... return <status>` sites."""
    by: dict[int, list[tuple[str, int]]] = collections.defaultdict(list)
    for f in files:
        if pathlib.Path(f).name.startswith("test_") or f == str(CONTRACT):
            continue
        try:
            tree = ast.parse((ROOT / f).read_text())
        except (OSError, SyntaxError):
            continue
        local = _module_int_assigns(tree)
        imported = _exit_contract_imports(tree)
        for node in ast.walk(tree):
            if not isinstance(node, ast.ExceptHandler):
                continue
            if not (set(_handled_classes(node)) & REFUSAL_CLASSES):
                continue
            for st in ast.walk(node):
                status = _return_status(st.value, local, imported, contract) \
                    if isinstance(st, ast.Return) else None
                if status is not None:
                    by[status].append((f, st.lineno))
                    break
    return dict(by)


def constant_census(files: list[str]) -> dict[int, list[tuple[str, int]]]:
    """Per exit status, the module-level `REFUSED = <int>` declarations.  These
    are invisible to `handler_census` when no handler returns the constant."""
    by: dict[int, list[tuple[str, int]]] = collections.defaultdict(list)
    for f in files:
        if pathlib.Path(f).name.startswith("test_") or f == str(CONTRACT):
            continue
        try:
            tree = ast.parse((ROOT / f).read_text())
        except (OSError, SyntaxError):
            continue
        for st in tree.body:
            if not isinstance(st, ast.Assign):
                continue
            if not any(isinstance(t, ast.Name) and t.id == "REFUSED" for t in st.targets):
                continue
            if isinstance(st.value, ast.Constant) and isinstance(st.value.value, int) \
                    and not isinstance(st.value.value, bool):
                by[st.value.value].append((f, st.lineno))
    return dict(by)


def census() -> dict:
    files = tracked_python()
    try:
        contract_tree = ast.parse((ROOT / CONTRACT).read_text())
        runner_tree = ast.parse((ROOT / RUNNER).read_text())
    except (OSError, SyntaxError) as exc:
        raise Refused(f"cannot read the contract or the runner: {exc}") from exc
    no_evidence = _contract_no_evidence(contract_tree)
    _runner_honours_contract(runner_tree)
    contract_ints = _contract_ints(contract_tree)
    handlers, constants = handler_census(files, contract_ints), constant_census(files)
    codes = sorted(set(handlers) | set(constants))
    return {
        "n_python_files": len(files),
        "no_evidence": sorted(no_evidence),
        "codes": {
            c: {
                "handler_sites": sorted(handlers.get(c, [])),
                "handler_files": sorted({f for f, _ in handlers.get(c, [])}),
                "constant_sites": sorted(constants.get(c, [])),
                # the column that matters: how run_all.py renders this family
                "run_all_state": "NO-VERDICT" if c in no_evidence
                                 else ("PASS" if c == 0 else "FAIL"),
            }
            for c in codes
        },
    }


def render(res: dict) -> str:
    lines = [
        f"{res['n_python_files']} tracked Python files; tools/run_all.py reports "
        f"exits {res['no_evidence']} as NO-VERDICT (declared in {CONTRACT}).",
        "",
        f"{'exit':>4}  {'handler sites':>13}  {'files':>5}  {'REFUSED= consts':>15}  run_all reports",
    ]
    for c, d in res["codes"].items():
        lines.append(f"{c:>4}  {len(d['handler_sites']):>13}  {len(d['handler_files']):>5}  "
                     f"{len(d['constant_sites']):>15}  {d['run_all_state']}")
    misreported = [c for c, d in res["codes"].items()
                   if c != 0 and d["run_all_state"] == "FAIL"]
    lines += ["", "Refusal families run_all.py renders as FAIL (a RESULT, per its own "
                  "docstring), not as NO-VERDICT:"]
    for c in misreported:
        d = res["codes"][c]
        for f, ln in d["handler_sites"]:
            lines.append(f"  exit {c}  {f}:{ln}")
        for f, ln in d["constant_sites"]:
            lines.append(f"  exit {c}  {f}:{ln}  (REFUSED = {c})")
    if not misreported:
        lines.append("  none -- every refusal family is reported NO-VERDICT")
    return "\n".join(lines)


# Known answers, re-pinned after the #804 repair landed (the runner imports
# NO_EVIDENCE from tools/exit_contract.py instead of hardcoding 2).  These are
# pinned so drift shows up as a FAILING self-test rather than as a quietly
# different table that still looks plausible.
#
# Pin history: the first pin (measured on 344ed6e7) recorded the defect this
# tool was written to count -- handler_sites {1: 3, 2: 93, 3: 12} against a
# runner that honoured only 2.  The #804 repair moved cymbal_low_tail.py's
# refusal to the declared status, and the handler pass now also resolves
# `return REFUSED` names (see the docstring), so the totals below are measured
# on the repaired tree, not carried over.
EXPECTED = {
    "no_evidence": [2, 3, 126],
    "handler_files": {1: 1, 2: 84, 3: 10},
    "handler_sites": {1: 2, 2: 95, 3: 12},
    "constant_sites": {3: 4, 126: 1},
}

# The exact refusal sites that sit OUTSIDE the declared set, pinned one by
# one.  Both are in tools/cymbal_vca_drive.py and both are deliberate: the
# --require-source exit 1 is in that tool's argparse help and in
# docs/scorecard/cymbal-369/vca-drive/README.md; #804 scoped them out rather
# than quietly change a documented contract.  A NEW site outside the set --
# a future tool refusing with an undeclared status -- turns --check red here.
EXPECTED["sites_outside_set"] = [
    ["tools/cymbal_vca_drive.py", 1525],
    ["tools/cymbal_vca_drive.py", 1531],
]


def check() -> tuple[bool, list[str]]:
    res = census()
    out, ok = [], True

    def cmp(label, got, want):
        nonlocal ok
        good = got == want
        ok = ok and good
        out.append(f"  {'ok ' if good else 'RED'}  {label}: got {got}, pinned {want}")

    cmp("runner NO-VERDICT set", res["no_evidence"], EXPECTED["no_evidence"])
    for c, want in EXPECTED["handler_sites"].items():
        cmp(f"exit {c} handler sites", len(res["codes"].get(c, {}).get("handler_sites", [])), want)
    for c, want in EXPECTED["handler_files"].items():
        cmp(f"exit {c} handler files", len(res["codes"].get(c, {}).get("handler_files", [])), want)
    for c, want in EXPECTED["constant_sites"].items():
        cmp(f"exit {c} REFUSED= constants", len(res["codes"].get(c, {}).get("constant_sites", [])), want)

    # The control that makes the census mean something: every refusal site
    # outside the declared set must be one of the pinned, documented
    # exceptions -- or a refusal is being rendered as FAIL again, the defect
    # this tool was originally written to count.
    outside = sorted((f, ln) for c in res["codes"] if c != 0 and c not in res["no_evidence"]
                     for (f, ln) in res["codes"][c]["handler_sites"])
    outside += sorted((f, ln) for c in res["codes"] if c != 0 and c not in res["no_evidence"]
                      for (f, ln) in res["codes"][c]["constant_sites"])
    good = outside == [tuple(s) for s in EXPECTED["sites_outside_set"]]
    ok = ok and good
    out.append(f"  {'ok ' if good else 'RED'}  refusal sites outside the declared set are exactly "
               f"the pinned, documented exceptions (got {outside})")
    return ok, out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--check", action="store_true", help="self-test against pinned answers")
    a = ap.parse_args(argv)
    try:
        if a.check:
            ok, lines = check()
            print("\n".join(lines))
            return 0 if ok else 1
        print(render(census()))
    except Refused as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 2            # the dominant family, and the one run_all.py honours
    return 0


if __name__ == "__main__":
    sys.exit(main())
