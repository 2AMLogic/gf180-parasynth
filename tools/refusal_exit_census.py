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
  * per-family totals are REPORTED, not pinned.  They were pinned until this
    tool's own review caught the pin RED on a tree whose contract still held:
    a merge of origin/main had added one exit-2 refusal site
    (tools/probes/repeat_period_multiple.py) and "got 96, pinned 95" failed
    while no_evidence, sites_outside_set and the runner read-back -- the
    three checks that ENCODE the contract -- all stayed green.  A totals pin
    reds on every unrelated merge that touches a refusal site, and a gate
    that reds on other people's work gets trained away, which is the failure
    mode this tool exists to prevent.  The guard that still fires: a site
    refusing with an UNDECLARED status lands in sites_outside_set and turns
    --check red.  Drift in the totals is still visible -- they print in every
    --check run -- just never as a failing pin;
  * a third, LABEL/STATUS pass (issue #1790), REPORTED and never pinned.  The
    handler pass reads handlers; a refusal carried in prose -- `print` of
    REFUSED, then a bare `return 1` outside any refusal handler -- has no
    handler to read, so the census stayed green while tools/run_all.py
    rendered those exits FAIL: the #804 inversion, in a shape the tool
    written to catch its neighbours could not see.  The pass lists, as
    file:line, every `return <status>` outside the declared set whose label
    -- the last print before it, with no return or raise between -- opens on
    REFUSED.  It is not pinned for the reason the totals are not: the sites
    belong to the tools that print them, and a pin that reds on the merges
    that repair or add one gets trained away.

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
the passes are reported separately rather than summed, because a sum would
hide which detector found what.  `tools/exit_contract.py` itself is excluded
from every pass: it DECLARES the statuses, it does not refuse with them.

The label/status pass reads a label BESIDE a return: the last print before
it in flat source order, with no return or raise between.  Every shape below
reads as SILENT to it -- a documented blind spot, not a zero:

  * a label held in a variable or concatenated (`msg = "REFUSED"; print(msg)`,
    `print("REFUSED: " + why)`).  Only a str literal, or an f-string whose
    leading part is a literal, is read -- after leading whitespace, because
    two of the sites print `"\nREFUSED: ..."` -- and only as the whole word
    REFUSED: `REFUSED_TYPES` is not a label;
  * a label that does not OPEN on REFUSED: a tool-name prefix
    (`midi_session: REFUSED -- ...`, fpga/midi_session.py) or a sibling
    spelling (`NO VERDICT: ...`, `REJECTED -- ...`).  tools/dsp_dpreg_sim.py
    prints `NO VERDICT` and returns 1; this pass does not say so;
  * a refusal printed in a helper: the function that returns 1 never prints,
    the callee prints but returns no status;
  * `sys.exit(<status>)` beside the label -- the pass reads `return` only.
    The `print REFUSED; sys.exit(2)` sites in this tree exit 2 by
    inspection, not by this tool;
  * a `return` whose status is neither an int literal nor a name the handler
    pass could resolve (`return 0 if ok else 1`, `return rc`);
  * a label that is beside the return in control flow but not in source
    order.  The walk ignores branching: a REFUSED print, a conditional
    `return 0`, then a bare `return 1` is silent (the `return 0` ended the
    label on paper while control flows around it), and a loop that prints
    REFUSED and then FAIL before a flag-driven `return 1` is silent (FAIL
    relabels).  A helper that prints its own labels (`_print(findings)`) is
    transparent, so a return below it inherits the last literal print()
    above -- which an intervening return clears, usually;
  * a return inside a handler for a refusal class, where the pass defers to
    the handler pass entirely -- including a handler's SECOND return, which
    that pass does not count either.  The handler's print is still a label,
    so a handler that prints and falls through to a `return 1` IS seen; a
    handler for any other class (`except OSError:`) is not deferred at all.
"""

from __future__ import annotations

import argparse
import ast
import collections
import pathlib
import re
import subprocess
import sys
import refusal

ROOT = pathlib.Path(__file__).resolve().parents[1]

MIN_PY_FILES = 300
CONTRACT = pathlib.Path("tools") / "exit_contract.py"
RUNNER = pathlib.Path("tools") / "run_all.py"
# The key for a refusal site with no enclosing def (module-level handler or
# constant), distinct from any function name so it can never collide (#868).
MODULE_LEVEL = "<module>"

# Exception classes that mean "a precondition is not met, so nothing was
# measured".  Taken from the classes actually raised in this tree; a class
# missing here under-counts.  The per-family totals are printed in every
# --check run precisely so such an under-count shows up as a changed table,
# but no pin fires on a total; see EXPECTED below.
# "REFUSED_TYPES" was removed in #815: the hand-maintained tuple is gone and
# its two `except` sites catch `refusal.Refused`, which "Refused" (the attr
# of a `refusal.Refused` handler) already counts.
REFUSAL_CLASSES = frozenset({
    "Refused", "SourceUnavailable", "ScanRefused", "KitRefused",
    "MidiPortRefused", "ControlRefused", "StaleBase",
    "Infeasible", "CalibrationError",
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


class Refused(refusal.Refused, Exception):
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


def _handlers_in_scope(tree: ast.Module) -> list[tuple[str, ast.ExceptHandler]]:
    """(innermost enclosing function name, handler) for every except clause.
    The function name is the pin key for sites_outside_set (issue #868): it
    survives any edit that shifts lines, which a line number does not.  A
    handler with no enclosing def keys as MODULE_LEVEL, not a crash."""
    out: list[tuple[str, ast.ExceptHandler]] = []

    def visit(node, func: str) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                visit(child, child.name)
            elif isinstance(child, ast.ExceptHandler):
                out.append((func, child))
                visit(child, func)
            else:
                visit(child, func)

    visit(tree, MODULE_LEVEL)
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


def parse_sources(files: list[str]) -> dict[str, ast.Module]:
    """Parse each scanned file ONCE.  Parsing is essentially the whole runtime
    of the census (~3 s per pass over the tracked tree), so `census()` parses
    here and hands the trees to all three passes.  Unreadable or unparseable
    files are skipped, exactly as each pass skipped them before."""
    out: dict[str, ast.Module] = {}
    for f in files:
        if pathlib.Path(f).name.startswith("test_") or f == str(CONTRACT):
            continue
        try:
            out[f] = ast.parse((ROOT / f).read_text())
        except (OSError, SyntaxError):
            continue
    return out


def _scanned_trees(files: list[str], trees: dict[str, ast.Module] | None):
    """(file, tree) per scanned file: from `trees` when the caller already
    parsed them, else parsed here.  Same exclusions either way."""
    if trees is None:
        trees = parse_sources(files)
    for f in files:
        if f in trees:
            yield f, trees[f]


def handler_census(files: list[str], contract: dict[str, int],
                   trees: dict[str, ast.Module] | None = None) -> dict[int, list[tuple[str, str, int]]]:
    """Per exit status, the `except <refusal>: ... return <status>` sites,
    each as (file, enclosing function, line)."""
    by: dict[int, list[tuple[str, str, int]]] = collections.defaultdict(list)
    for f, tree in _scanned_trees(files, trees):
        local = _module_int_assigns(tree)
        imported = _exit_contract_imports(tree)
        for func, node in _handlers_in_scope(tree):
            if not (set(_handled_classes(node)) & REFUSAL_CLASSES):
                continue
            for st in ast.walk(node):
                status = _return_status(st.value, local, imported, contract) \
                    if isinstance(st, ast.Return) else None
                if status is not None:
                    by[status].append((f, func, st.lineno))
                    break
    return dict(by)


def constant_census(files: list[str],
                    trees: dict[str, ast.Module] | None = None) -> dict[int, list[tuple[str, str, int]]]:
    """Per exit status, the module-level `REFUSED = <int>` declarations.  These
    are invisible to `handler_census` when no handler returns the constant.
    Module-level by construction, so every site keys as MODULE_LEVEL."""
    by: dict[int, list[tuple[str, str, int]]] = collections.defaultdict(list)
    for f, tree in _scanned_trees(files, trees):
        for st in tree.body:
            if not isinstance(st, ast.Assign):
                continue
            if not any(isinstance(t, ast.Name) and t.id == "REFUSED" for t in st.targets):
                continue
            if isinstance(st.value, ast.Constant) and isinstance(st.value.value, int) \
                    and not isinstance(st.value.value, bool):
                by[st.value.value].append((f, MODULE_LEVEL, st.lineno))
    return dict(by)


# The refusal label the label/status pass reads, as a whole word.  `REFUSED:`,
# `REFUSED (` and a bare `REFUSED` match; `REFUSED_TYPES` and a message that
# merely CONTAINS the word (`MISSED: ... expected REFUSED`,
# tools/check_iverilog_generate_case_string.py) do not.
REFUSED_LABEL = re.compile(r"REFUSED\b")


def _label_prefix(node: ast.AST) -> str | None:
    """The literal a print() argument opens with: a str constant, or an
    f-string's leading str constant.  None when neither, i.e. the label is
    not something this pass can read."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr) and node.values \
            and isinstance(node.values[0], ast.Constant) \
            and isinstance(node.values[0].value, str):
        return node.values[0].value
    return None


def _prints_refused(node: ast.AST) -> bool:
    """True for `print(<label>, ...)` whose label opens on the word REFUSED
    once leading whitespace is dropped -- two of the sites print
    `"\\nREFUSED: ..."`, and a startswith() on the raw literal misses them."""
    if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
            and node.func.id == "print" and node.args):
        return False
    prefix = _label_prefix(node.args[0])
    return prefix is not None and REFUSED_LABEL.match(prefix.lstrip()) is not None


def label_sites(tree: ast.Module, local: dict[str, int], imported: dict[str, str],
                contract: dict[str, int], no_evidence: frozenset[int]) -> list[tuple[int, str, int]]:
    """(exit status, innermost enclosing function, line) for every
    `return <status>` OUTSIDE any refusal handler whose label is a print of
    REFUSED, where status is outside NO_EVIDENCE.  Source order.

    The label must sit BESIDE the return, and "beside" is a walk, not a
    function: back through the function's statements from the return, the
    first print, return or raise met must be a print of REFUSED.  A statement
    that does none of those is transparent (a diagnostic line, `ok = False`);
    an earlier return or raise ends the path the print belongs to; a
    differently-labelled print relabels.

    WRONG BEFORE IT WAS RIGHT: the first rule was function granularity -- "the
    function prints REFUSED somewhere" -- and on 03148399 it listed 50 sites,
    at least 14 of them a main() whose REFUSED paths all `return 2` inside
    handlers, beside a correctly-labelled FAIL return.  It read as a list of
    inversions and was not one.  This walk lists 16 on that tree, each
    inspected: the 12 the issue named, two more in the same files that a
    `grep -A1` cannot see (a diagnostic line or a flag between the print and
    the return), and two in files the issue's grep never opened.

    Scoping follows _handlers_in_scope: a print and a return in a nested def
    belong to the nested def.  Inside a handler for a refusal class the
    handler pass owns the site, so a return there is never listed -- but the
    handler's print is still a label, and a handler that prints and falls
    through to a `return 1` is listed (no Return inside it, so the handler
    pass counts nothing).  A handler for any other class (`except OSError:`)
    is not skipped at all: the handler pass is blind to it too."""
    events: dict[ast.AST, list[tuple[int, str, object, bool]]] = collections.defaultdict(list)

    def visit(node: ast.AST, func, in_refusal_handler: bool) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                visit(child, child, False)
            elif isinstance(child, ast.ExceptHandler):
                is_refusal = bool(set(_handled_classes(child)) & REFUSAL_CLASSES)
                visit(child, func, in_refusal_handler or is_refusal)
            else:
                if func is not None:
                    if isinstance(child, ast.Call) and isinstance(child.func, ast.Name) \
                            and child.func.id == "print":
                        events[func].append((child.lineno, "print", _prints_refused(child), False))
                    elif isinstance(child, ast.Return):
                        events[func].append((child.lineno, "return", child, in_refusal_handler))
                    elif isinstance(child, ast.Raise):
                        events[func].append((child.lineno, "raise", None, False))
                visit(child, func, in_refusal_handler)

    visit(tree, None, False)
    out = []
    for func, ev in events.items():
        labelled = False        # the last label met was a print of REFUSED
        for _, kind, payload, in_handler in sorted(ev, key=lambda e: e[0]):
            if kind == "print":
                labelled = payload
            elif kind == "raise":
                labelled = False
            else:
                status = _return_status(payload.value, local, imported, contract)
                if labelled and not in_handler and status is not None \
                        and status not in no_evidence:
                    out.append((status, func.name, payload.lineno))
                labelled = False
    return sorted(out, key=lambda s: (s[2], s[1]))


def label_census(files: list[str], contract: dict[str, int],
                 no_evidence: frozenset[int],
                 trees: dict[str, ast.Module] | None = None) -> dict[int, list[tuple[str, str, int]]]:
    """Per exit status, the label/status sites, each as (file, enclosing
    function, line).  REPORTED, never pinned (issue #1790); same file
    exclusions as the other two passes."""
    by: dict[int, list[tuple[str, str, int]]] = collections.defaultdict(list)
    for f, tree in _scanned_trees(files, trees):
        local = _module_int_assigns(tree)
        imported = _exit_contract_imports(tree)
        for status, func, ln in label_sites(tree, local, imported, contract, no_evidence):
            by[status].append((f, func, ln))
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
    trees = parse_sources(files)  # one read+parse per file, shared by all three passes
    handlers = handler_census(files, contract_ints, trees)
    constants = constant_census(files, trees)
    labels = label_census(files, contract_ints, no_evidence, trees)
    codes = sorted(set(handlers) | set(constants) | set(labels))
    return {
        "n_python_files": len(files),
        "no_evidence": sorted(no_evidence),
        "codes": {
            c: {
                "handler_sites": sorted(handlers.get(c, [])),
                "handler_files": sorted({f for f, _, _ in handlers.get(c, [])}),
                "constant_sites": sorted(constants.get(c, [])),
                "label_sites": sorted(labels.get(c, [])),
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
        for f, func, ln in d["handler_sites"]:
            lines.append(f"  exit {c}  {f}:{ln}  (in {func})")
        for f, func, ln in d["constant_sites"]:
            lines.append(f"  exit {c}  {f}:{ln}  (REFUSED = {c})")
    if not misreported:
        lines.append("  none -- every refusal family is reported NO-VERDICT")
    # The third detector gets its own section, never a sum with the lines
    # above: which detector found what stays readable (see the docstring).
    label = [(c, f, func, ln) for c, d in res["codes"].items()
             for (f, func, ln) in d.get("label_sites", [])]
    lines += ["", "Label/status pass, REPORTED and never pinned (issue #1790): a print of "
                  "REFUSED is the last label before the return, with no return or raise "
                  "between, outside any refusal handler where the handler pass cannot "
                  "see it; run_all.py renders that status as a result (PASS for 0, FAIL "
                  "otherwise) instead of NO-VERDICT:"]
    for c, f, func, ln in label:
        lines.append(f"  exit {c}  {f}:{ln}  (in {func})")
    if label:
        lines.append(f"  {len(label)} sites in {len({f for _, f, _, _ in label})} files "
                     f"-- reported, not pinned")
    else:
        lines.append("  none -- no print of REFUSED sits beside a return outside a handler")
    return "\n".join(lines)


# The two pins that ENCODE THE CONTRACT, re-measured after the #804 repair
# landed (the runner imports NO_EVIDENCE from tools/exit_contract.py instead
# of hardcoding 2):
#
#   no_evidence        the declaration itself; an accidental edit to
#                      tools/exit_contract.py turns --check red.
#   sites_outside_set  every refusal site using a status OUTSIDE the declared
#                      set must be one of these pinned, documented exceptions;
#                      a new undeclared status is the defect this tool exists
#                      to catch.
#
# What is deliberately NOT pinned: the per-family totals.  WRONG BEFORE IT
# WAS RIGHT, in the opposite direction this time: they were pinned until
# review caught the pins red ("exit 2 handler sites: got 96, pinned 95") on a
# tree whose contract still held -- the merge of origin/main had added one
# refusal site, tools/probes/repeat_period_multiple.py:829, and nothing about
# the runner or the contract had changed.  (The very first pin, measured on
# 344ed6e7, recorded the real defect: handler_sites {1: 3, 2: 93, 3: 12}
# against a runner that honoured only 2.)  A pin that reds on every unrelated
# merge that touches a refusal site is a gate that gets trained away; check()
# still PRINTS the measured totals, so drift is visible as a changed table,
# never silently different.
EXPECTED = {
    "no_evidence": [2, 3, 126],
}

# The refusal sites that sit OUTSIDE the declared set, pinned one by one --
# keyed on [file, enclosing function, exit status, count], not on line
# numbers.  WRONG BEFORE IT WAS RIGHT: the first pin keyed each site as a
# (file, line) pair compared by exact equality, so an unrelated edit anywhere
# above the sites in tools/cymbal_vca_drive.py -- a comment, an added argparse
# flag, a reflow -- turned --check red while the exit contract itself stayed
# intact (issue #868; Control A in its test file ran red on that pin before
# this re-key landed).  The same shape #825's review round removed the
# per-family totals pins for: a pin that reds on other people's unrelated
# work gets trained away.  The function name is resolved by AST
# (_handlers_in_scope), the same machinery that reads the contract; a site
# with no enclosing def keys as "<module>".  A NEW site outside the set still
# turns --check red -- a future tool refusing with an undeclared status
# changes the key set, a third refusal in this function changes the count.
# Known residual, accepted: swapping one `return 1` for another inside the
# same function is not caught; the exit-code contract does not care which
# statement refuses, only which statuses exist outside the declared set.
# Both pinned sites are in tools/cymbal_vca_drive.py's main() and both are
# deliberate: the --require-source exit 1 is in that tool's argparse help and
# in docs/scorecard/cymbal-369/vca-drive/README.md; #804 scoped them out
# rather than quietly change a documented contract.
EXPECTED["sites_outside_set"] = [
    ["tools/cymbal_vca_drive.py", "main", 1, 2],
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
    for c in sorted(res["codes"]):
        d = res["codes"][c]
        consts = (f", {len(d['constant_sites'])} REFUSED= constants"
                  if d["constant_sites"] else "")
        labels = (f", {len(d['label_sites'])} label/status sites"
                  if d.get("label_sites") else "")
        out.append(f"  note exit {c}: {len(d['handler_sites'])} handler sites in "
                   f"{len(d['handler_files'])} files{consts}{labels} -- reported, not pinned")

    # The control that makes the census mean something: every refusal site
    # outside the declared set must be one of the pinned, documented
    # exceptions -- or a refusal is being rendered as FAIL again, the defect
    # this tool was originally written to count.  Compared as
    # (file, enclosing function, status) counts, not (file, line) pairs,
    # so an edit that only shifts lines cannot red the gate (#868).
    outside = sorted((f, func, c) for c in res["codes"] if c != 0 and c not in res["no_evidence"]
                     for (f, func, ln) in res["codes"][c]["handler_sites"])
    outside += sorted((f, func, c) for c in res["codes"] if c != 0 and c not in res["no_evidence"]
                      for (f, func, ln) in res["codes"][c]["constant_sites"])
    counted = sorted([*site, n] for site, n in collections.Counter(outside).items())
    good = counted == [list(s) for s in EXPECTED["sites_outside_set"]]
    ok = ok and good
    out.append(f"  {'ok ' if good else 'RED'}  refusal sites outside the declared set are exactly "
               f"the pinned, documented exceptions (file, enclosing function, "
               f"status, count) (got {counted})")
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
