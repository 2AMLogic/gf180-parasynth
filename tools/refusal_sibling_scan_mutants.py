#!/usr/bin/env python3
"""Injected-defect matrix for `tools/refusal_sibling_scan.py` (#1656): each
rule of the per-path CLI credit is LOOSENED by a named one-line change, the
scanner's own test file is run against the loosened scanner in a scratch
clone, and the result is printed as controls x mutants -- MOVED where a
control went red, blind where it did not (verification-rules rules 2 and 4).
The reverted scanner is a column too: `--base REV` runs the same tests
against `git show REV:tools/refusal_sibling_scan.py`, so "started red" is
read off a run rather than asserted in a docstring (rule 1).

Three outcomes, and the third is the point of having a harness at all:

  OK          the shipped scanner is green, and every mutant reddens at
              least one control -- a #1656 one or an earlier one, and the
              split is printed, because a rule this change did not touch
              (#1188's) is still allowed to be seen only by its own controls;
  GAP         a mutant reddened NOTHING: no control sees that loosening;
  NO VERDICT  the shipped scanner is not green, a mutant's anchor text was
              not found exactly once (the scanner moved and this file did
              not), or pytest could not run.

Exit status 0 / 1 / 2 for those. A harness that cannot fail would be a
false green in the place where a result belongs.

    python tools/refusal_sibling_scan_mutants.py --base origin/main
"""
from __future__ import annotations

import argparse
import os
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
SCANNER = "tools/refusal_sibling_scan.py"
TESTS = "tools/test_refusal_sibling_scan.py"
SECTION = "# --- #1656 controls"

# (name, what it loosens, substitutions that must each apply exactly once)
MUTANTS = [
    ("SOME-PATH",
     "a handler on SOME path to a def credits it: union over callers, least fixed point",
     [("        return weakest, sorted(wheres",
       "        weakest = max((k for k, _ in ends), key=rank.__getitem__, default=\"none\")\n"
       "        return weakest, sorted(wheres")]),
    ("ANY-TRY-IN-THE-CALLER",
     "every try written anywhere in the calling def guards the call -- a sibling's too",
     [("    out, cur, prev, prev2 = [], call, None, None\n    while cur is not frame:\n"
       "        prev2, prev, cur = prev, cur, mod.parents[cur]\n"
       "        if (isinstance(cur, ast.GeneratorExp)\n"
       "                and not (prev is cur.generators[0] and prev2 is prev.iter)):\n"
       "            return out, True\n"
       "        if isinstance(cur, ast.Try) and prev in cur.body:\n"
       "            out.extend(cur.handlers)\n    return out, False\n",
       "    return [h for t in ast.walk(frame) if isinstance(t, ast.Try)"
       " for h in t.handlers], False\n")]),
    ("EDGE-FROM-ANY-CLAUSE",
     "on the path, a call in a try's except, else or finally clause is guarded by it",
     [("        prev2, prev, cur = prev, cur, mod.parents[cur]\n"
       "        if (isinstance(cur, ast.GeneratorExp)\n"
       "                and not (prev is cur.generators[0] and prev2 is prev.iter)):\n"
       "            return out, True\n"
       "        if isinstance(cur, ast.Try) and prev in cur.body:\n",
       "        prev2, prev, cur = prev, cur, mod.parents[cur]\n"
       "        if (isinstance(cur, ast.GeneratorExp)\n"
       "                and not (prev is cur.generators[0] and prev2 is prev.iter)):\n"
       "            return out, True\n"
       "        if isinstance(cur, ast.Try):\n")]),
    ("ANY-TRY-IN-THE-SITES-DEF",
     "every try written in the site's own def guards the site, wherever it starts",
     [("            covered, shapes = set(), []\n"
       "            for tr in (enclosing_try_parents(mod.tree, node) if runs_here else []):\n",
       "            covered, shapes = set(), []\n"
       "            for tr in (t for t in ast.walk(mod.caller_of(node))\n"
       "                       if isinstance(t, ast.Try)):\n")]),
    ("LOCAL-TRY-WITHOUT-BODY",
     "the call site's own tries guard it from any clause (#1188's defect)",
     [("        prev, cur = cur, parents[cur]\n"
       "        if isinstance(cur, ast.Try) and prev in cur.body:\n",
       "        prev, cur = cur, parents[cur]\n"
       "        if isinstance(cur, ast.Try):\n")]),
    ("EXACT-CATCHES-EVERYTHING",
     "an exact or local handler is read as catching every family",
     [("        if kinds & {\"exact\", \"local\"} and origin in origins:\n",
       "        if kinds & {\"exact\", \"local\"}:\n")]),
    ("BEST-HANDLER-NOT-FIRST",
     "the best handler anywhere on the path decides, not the first that matches",
     [("                    first = next(((k, where(h)) for h in held if (k := match(h, origin))), None)\n",
       "                    ms = [(k, where(h)) for h in held if (k := match(h, origin))]\n"
       "                    first = max(ms, key=lambda m: rank[m[0]]) if ms else None\n"),
      ("    by_origin: dict[str, dict[ast.AST, set[tuple[str, str | None]]]] = {}\n",
       "    rank = {\"none\": 0, \"broad\": 1, \"exact\": 2, \"base\": 3}\n"
       "    by_origin: dict[str, dict[ast.AST, set[tuple[str, str | None]]]] = {}\n")]),
    ("OUTER-HANDLER-ALWAYS-CONTINUES",
     "a handler that catches the refusal still lets the outer handlers claim it",
     [("                    new |= {first} if first else out[c]\n",
       "                    new |= ({first} | out[c]) if first else out[c]\n")]),
    ("DEF-DEFAULT-READ-INSIDE",
     "a def's default is read inside the def, as its own parameter",
     [("                    or (isinstance(cur, (ast.Lambda, *FUNCTION_NODES)) and prev is cur.args)\n",
       "                    or (isinstance(cur, ast.Lambda) and prev is cur.args)\n")]),
    ("GENERATOR-CREATION-GUARDED",
     "a try around creating a generator or coroutine guards its body",
     [("                if (gen or isinstance(callee, ast.AsyncFunctionDef)) and not (\n",
       "                if False and (gen or isinstance(callee, ast.AsyncFunctionDef)) and not (\n")]),
    ("GENEXP-BODY-GUARDED",
     "a try around a generator expression guards calls in its body",
     [("                and not (prev is cur.generators[0] and prev2 is prev.iter)):\n            return out, True\n"
       "        if isinstance(cur, ast.Try) and prev in cur.body:\n",
       "                and False):\n            return out, True\n"
       "        if isinstance(cur, ast.Try) and prev in cur.body:\n")]),
    ("FINAL-CALL-ALWAYS-RUNS",
     "a cross-module call in a generator expression, or to an imported generator, runs where written",
     [("    if _handlers_within(mod, mod.caller_of(call), call)[1]:\n        return False\n"
       "    defs = [c.funcs.get(fname) for c in callees]\n"
       "    if any(isinstance(d, FUNCTION_NODES) and _is_generator(d) for d in defs):\n"
       "        return False\n",
       "    defs = [c.funcs.get(fname) for c in callees]\n")]),
    ("DEFINITION-TIME-IN-THE-DEF",
     "a default, annotation or decorator call is attributed to the def that contains the syntax",
     [("        while cur is not None and not (isinstance(cur, CALLER_NODES)\n"
       "                                       and not self._at_definition(cur, prev)):\n",
       "        while cur is not None and not isinstance(cur, CALLER_NODES):\n")]),
    ("AWAIT-NEVER-EAGER",
     "an awaited coroutine is deferred past its try like an unawaited one",
     [("                        not gen and isinstance(mod.parents.get(node), ast.Await)):\n",
       "                        False):\n")]),
    ("VALUE-RULE-OFF",
     "a function read as a value keeps the credit of its guarded bare-name call",
     [("                    and self._names_function(n)):\n", "                    and False):\n")]),
    ("VALUE-RULE-NAME-WIDE",
     "any read of a function's NAME taints it, local variables included",
     [("                        if name in self.local_names[cur]:\n                            return cur\n",
       "                        if False:\n                            return cur\n")]),
    ("COMPREHENSION-TARGET-LEAKS",
     "a comprehension's loop variable is read as a local of the enclosing def",
     [("        if isinstance(n, COMP_NODES):\n            # its targets",
       "        if False:\n            # its targets")]),
    ("EXCEPT-AS-NOT-BOUND",
     "an `except ... as name` binds nothing, so a same-named def is read as a value",
     [("        if isinstance(n, ast.ExceptHandler) and n.name:\n",
       "        if False:\n")]),
    ("TABLE-IGNORES-SHADOW",
     "a parameter named like a sealed dispatch table still follows the table",
     [("            if isinstance(table, str) and self._binding_scope(table, call) is not None:\n",
       "            if False:\n")]),
    ("SEAL-IGNORES-OTHER-READS",
     "a dispatch table stays sealed whatever else is done with it",
     [("                    sealed &= keys_only(n)\n", "                    sealed = sealed\n")]),
    ("TABLES-NEVER-SEALED",
     "no dispatch table is followed; every entry is an unseen path",
     [("            if sealed:\n                self.tables[name]",
       "            if False:\n                self.tables[name]"),
      ("            if isinstance(up, ast.Subscript) and up.value is d and immediate(up):\n",
       "            if False:\n")]),
    ("LAMBDA-IS-NO-FRAME",
     "a call written in a lambda is read as its enclosing def's",
     [("        while cur is not None and not (isinstance(cur, CALLER_NODES)\n",
       "        while cur is not None and not (isinstance(cur, FUNCTION_NODES)\n")]),
]


class Apparatus(Exception):
    """The pytest run itself is not a trustworthy observation: a verdict read
    from it would be a measurement of the harness, not the scanner."""


def read_run(returncode: int, out: str, tests: str = TESTS
             ) -> tuple[set[str], set[str], set[str], int, str]:
    """(failed, crashed, errored, passed, tail) of one pytest run, from its
    exit status AND its output; raises `Apparatus` when the two disagree or
    the run is not one that observed the tests (#1688 review).

      failed   an ASSERTION failed: a control saw the loosening
      crashed  a test body raised something else: the scanner (or the test)
               broke, which is not the control's own assertion
      errored  a fixture, setup or collection error: no assertion ran

    The status used to be dropped and the verdict read off the parsed sets
    alone, so a fixture error that stopped one control reading the tree
    counted as that control "seeing" the mutant, and printed OK."""
    fail = re.findall(r"^FAILED " + re.escape(tests)
                      + r"::(\w+)(?:\[[^\n]*?\])?(?: - (\S+))?", out, re.M)
    # POSITIVE evidence of an assertion (#1688 review round 3): pytest prints a
    # bare `assert a == b` as its rewritten text (`assert ...`) and an
    # `assert x, "why"` as `AssertionError: why`. Everything else -- any other
    # exception class, however it is named, or no message at all -- is a
    # crash. A negative list of exception-name suffixes counted a custom
    # `class Refused(Exception)` raised by a broken mutant as a catch.
    is_assertion = re.compile(r"^(assert\b|AssertionError\b)")
    failed = {n for n, msg in fail if msg and is_assertion.match(msg)}
    crashed = {n for n, msg in fail if not (msg and is_assertion.match(msg))}
    errored = set(re.findall(r"^ERROR " + re.escape(tests) + r"::(\w+)", out, re.M))
    m = re.search(r"^(\d+) passed", out, re.M)
    passed = int(m.group(1)) if m else 0
    tail = next((l for l in reversed(out.splitlines())
                 if " passed" in l or " failed" in l or " error" in l), "")
    if returncode not in (0, 1):
        raise Apparatus(f"pytest exited {returncode}, not 0 or 1 ({tail.strip() or 'no summary'})")
    if re.search(r"^ERROR (?!" + re.escape(tests) + r"::)", out, re.M):
        raise Apparatus(f"pytest could not collect {tests} ({tail.strip()})")
    if returncode == 0 and (failed or crashed or errored or not passed):
        raise Apparatus(f"pytest exited 0 but the output disagrees ({tail.strip()})")
    if returncode == 1 and not (failed or crashed or errored):
        raise Apparatus(f"pytest exited 1 but no failing test could be read ({tail.strip()})")
    return failed, crashed, errored, passed, tail


def _run(scratch: pathlib.Path, tests: str = TESTS
         ) -> tuple[set[str], set[str], set[str], int, str]:
    r = subprocess.run([sys.executable, "-m", "pytest", tests, "-q", "-p", "no:cacheprovider",
                        "--tb=no", "-rfE"], cwd=scratch, capture_output=True, text=True,
                       timeout=900, env={**os.environ, "COLUMNS": "400"})
    return read_run(r.returncode, r.stdout + r.stderr, tests)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--base", metavar="REV",
                    help="also run the tests against this revision's scanner")
    a = ap.parse_args(argv)
    shipped = (ROOT / SCANNER).read_text()
    tests = (ROOT / TESTS).read_text()
    # the #1656 section runs from its marker to the next section's, or to the end
    section = tests.split(SECTION, 1)[1].split("\n# --- ", 1)[0] if SECTION in tests else ""
    new_controls = set(re.findall(r"^def (test_\w+)", section, re.M))
    if not new_controls:
        print(f"NO VERDICT: no controls found after {SECTION!r} in {TESTS}")
        return 2
    columns: list[tuple[str, str | None]] = [("shipped", shipped)]
    problems: list[str] = []
    if a.base:
        r = subprocess.run(["git", "-C", str(ROOT), "show", f"{a.base}:{SCANNER}"],
                           capture_output=True, text=True)
        if r.returncode:
            print(f"NO VERDICT: cannot read {a.base}:{SCANNER}: {r.stderr.strip()}")
            return 2
        columns.append((f"base {a.base}", r.stdout))
    built: dict[str, str] = {}
    for name, _, subs in MUTANTS:
        src, ok = shipped, True
        for old, new in subs:
            if src.count(old) != 1:
                problems.append(f"{name}: anchor found {src.count(old)}x, expected 1: "
                                f"{old.strip()[:70]!r}")
                ok = False
                break
            src = src.replace(old, new)
        if ok:
            built[name] = src
    results: dict[str, tuple[set[str], set[str], set[str], int, str]] = {}
    for name, src in list(columns) + list(built.items()):
        scratch = pathlib.Path(tempfile.mkdtemp(prefix="rss-mutants-"))
        try:
            subprocess.run(["git", "clone", "--quiet", "--shared", str(ROOT), str(scratch)],
                           check=True, capture_output=True, text=True)
            (scratch / SCANNER).write_text(src)
            (scratch / TESTS).write_text(tests)
            try:
                results[name] = _run(scratch)
            except subprocess.TimeoutExpired:
                print(f"NO VERDICT: {name} timed out")
                return 2
            except Apparatus as e:
                print(f"NO VERDICT: {name}: {e}")
                return 2
            # A mutant is caught by a control's ASSERTION only. A crash or a
            # fixture error is the harness or the loosened scanner breaking,
            # not a control seeing it (the reverted scanner may legitimately
            # crash a test that uses what it lacks, so it is exempt).
            f, c, e, _, tail = results[name]
            if name in dict(built.items()) and (c or e):
                print(f"NO VERDICT: {name}: {len(c)} test(s) crashed and {len(e)} errored "
                      f"instead of failing an assertion ({tail.strip()}): "
                      f"{', '.join(sorted(c | e))}")
                return 2
        except (subprocess.CalledProcessError, OSError) as e:
            print(f"NO VERDICT: {name}: {e}")
            return 2
        finally:
            shutil.rmtree(scratch, ignore_errors=True)
    if problems:
        print("NO VERDICT: a mutant's anchor is not where this file says it is:")
        for p in problems:
            print("   ", p)
        return 2
    failed, crashed, errored, passed, tail = results["shipped"]
    if failed or crashed or errored or not passed:
        print(f"NO VERDICT: the shipped scanner is not green ({tail})")
        return 2

    print(f"# refusal_sibling_scan mutants: {len(new_controls)} #1656 controls x "
          f"{len(built)} mutants" + (f" + base {a.base}" if a.base else "")
          + f"  (shipped: {tail.strip()})")
    print("  MOVED = the control went red under that loosening; blind = it did not.\n")
    # one record per column: header, key in `results`, legend line, is a mutant
    cols = [("base", n, f"the scanner as of {n.removeprefix('base ')}", False)
            for n, _ in columns[1:]]
    cols += [(f"M{i}", m[0], f"loosened: {m[1]}", True) for i, m in enumerate(MUTANTS, 1)]
    width = max(len(n) for n in new_controls)
    print(" " * width + "  " + "  ".join(f"{c[0]:>5s}" for c in cols))
    gap = False
    for t in sorted(new_controls):
        cells = []
        for header, key, _, _ in cols:
            f, c, _, _, _ = results[key]
            cells.append(f"{'MOVED' if t in f or t in c else 'blind':>5s}")
        print(f"{t[:width]:<{width}s}  " + "  ".join(cells))
    print()
    for header, key, means, is_mutant in cols:
        f, c, _, _, tail = results[key]
        f = f | c
        seen = len(f & new_controls)
        earlier = len(f - new_controls)
        print(f"{header} {means}\n    {seen} of {len(new_controls)} #1656 controls MOVED"
              + (f", {earlier} earlier controls" if earlier else "") + f"   [{tail.strip()}]")
        if is_mutant and not f:
            gap = True
            print("    GAP: no control sees it")
    print("\nGAP: a mutant no control sees" if gap
          else "OK: every mutant is seen by at least one control")
    return 1 if gap else 0


if __name__ == "__main__":
    sys.exit(main())
