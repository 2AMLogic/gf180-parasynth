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
     [("            new = EVERYTHING\n", "            new = NOTHING\n"),
      ("                    new = meet(new, join(guarantee[c], cgt))\n",
       "                    new = join(new, join(guarantee[c], cgt))\n")]),
    ("ANY-TRY-IN-THE-CALLER",
     "every try written anywhere in the calling def guards the call -- a sibling's too",
     [("    out, cur = [], call\n    while cur is not frame:\n"
       "        prev, cur = cur, mod.parents[cur]\n"
       "        if isinstance(cur, ast.Try) and prev in cur.body:\n"
       "            out.extend(cur.handlers)\n    return out\n",
       "    return [h for t in ast.walk(frame) if isinstance(t, ast.Try)"
       " for h in t.handlers]\n")]),
    ("EDGE-FROM-ANY-CLAUSE",
     "on the path, a call in a try's except, else or finally clause is guarded by it",
     [("        prev, cur = cur, mod.parents[cur]\n"
       "        if isinstance(cur, ast.Try) and prev in cur.body:\n",
       "        prev, cur = cur, mod.parents[cur]\n"
       "        if isinstance(cur, ast.Try):\n")]),
    ("ANY-TRY-IN-THE-SITES-DEF",
     "every try written in the site's own def guards the site, wherever it starts",
     [("            covered, shapes = set(), []\n"
       "            for tr in enclosing_try_parents(mod.tree, node):\n",
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
     [("                frozenset(origins) if kinds & {\"exact\", \"local\"} else frozenset(),\n",
       "                every if kinds & {\"exact\", \"local\"} else frozenset(),\n")]),
    ("BASE-CARRIES-NO-ORIGINS",
     "a base handler is carried as catching no origin (the first #1656 lattice)",
     [("            return (True, every, True)\n", "            return (True, frozenset(), True)\n")]),
    ("VALUE-RULE-OFF",
     "a function read as a value keeps the credit of its guarded bare-name call",
     [("                    and self._names_function(n)):\n", "                    and False):\n")]),
    ("VALUE-RULE-NAME-WIDE",
     "any read of a function's NAME taints it, local variables included",
     [("                    if n.id in self.local_names[cur]:\n", "                    if False:\n")]),
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
     [("        while cur is not None and not isinstance(cur, CALLER_NODES):\n",
       "        while cur is not None and not isinstance(cur, FUNCTION_NODES):\n")]),
]


def _run(scratch: pathlib.Path) -> tuple[set[str], set[str], int, str]:
    """(failed, errored, passed, tail) of one pytest run in `scratch`."""
    r = subprocess.run([sys.executable, "-m", "pytest", TESTS, "-q", "-p", "no:cacheprovider",
                        "--tb=no", "-rfE"], cwd=scratch, capture_output=True, text=True,
                       timeout=900)
    out = r.stdout + r.stderr
    failed = set(re.findall(r"^FAILED " + re.escape(TESTS) + r"::(\w+)", out, re.M))
    errored = set(re.findall(r"^ERROR " + re.escape(TESTS) + r"::(\w+)", out, re.M))
    m = re.search(r"^(\d+) passed", out, re.M)
    tail = next((l for l in out.splitlines()
                 if " passed" in l or " failed" in l or " error" in l), "")
    return failed, errored, int(m.group(1)) if m else 0, tail


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
    results: dict[str, tuple[set[str], set[str], int, str]] = {}
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
    failed, errored, passed, tail = results["shipped"]
    if failed or errored:
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
            f, e, _, _ = results[key]
            cells.append(f"{'MOVED' if t in f or t in e else 'blind':>5s}")
        print(f"{t[:width]:<{width}s}  " + "  ".join(cells))
    print()
    for header, key, means, is_mutant in cols:
        f, e, _, tail = results[key]
        seen = len((f | e) & new_controls)
        earlier = len((f | e) - new_controls)
        print(f"{header} {means}\n    {seen} of {len(new_controls)} #1656 controls MOVED"
              + (f", {earlier} earlier controls" if earlier else "") + f"   [{tail.strip()}]")
        if is_mutant and not (f | e):
            gap = True
            print("    GAP: no control sees it")
    print("\nGAP: a mutant no control sees" if gap
          else "OK: every mutant is seen by at least one control")
    return 1 if gap else 0


if __name__ == "__main__":
    sys.exit(main())
