#!/usr/bin/env python3
"""json_get_census.py -- where a record from `json.load`/`json.loads` is read
with `.get()` before anything establishes it is an object (#1863).

    python3 tools/json_get_census.py                 every tracked .py file
    python3 tools/json_get_census.py tools/foo.py    just these

This is the class search CLAUDE.md asks for when an issue is closed as
fixed: the fix touched five files, and the question is what the same shape
looks like everywhere else. It is a CENSUS, not a gate -- nothing runs it in
`make verify` or CI, and its output is a count with file names for a closing
comment. A permanent detector belongs to #1618's scanner and is a separate
triage item; this exists so the count quoted for #1863 has its code beside it.

THE SHAPE. Inside one function body, in statement order:

    rec = json.loads(...)            a name bound from json.load / json.loads
    ...                              no require_keys(rec, ...) and no
                                     isinstance(rec, dict) in between
    rec.get(...)                     a .get() on that name

plus the chained form `json.loads(...).get(...)`, which binds no name and so
is invisible to a name-following walk -- it is how `r0_reference.py` hid one
site from the sweep that filed #1863.

WHAT IT CANNOT SEE, stated rather than left to be discovered:

  * object-ness established in a CALLER. `identity_label_problems(rec, ...)`
    is handed a record the caller parsed; a `.get` there is reported against
    the callee, and whether the caller guards it is not followed;
  * object-ness established by any other means -- `try/except AttributeError`,
    a preceding `rec["k"]` that would have raised, `dict(rec)`;
  * a name rebound between the load and the read (`rec = rec[0]`).

So every hit is a CANDIDATE. Reachability from an entry point and the
presence of a guard upstream are settled by reading each one, not by this
script. For #1863 that reading is in the PR body: all 20 hits in its five
files were reachable and unguarded, and the 21st -- the chained form in
`rolling_bound` -- was found only because the function was read.

Exit status is 0 whenever the walk completes: a census reports, it does not
refuse, and a count of zero is a result rather than a pass.
"""
from __future__ import annotations

import ast
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
LOADERS = ("load", "loads")
DEFAULT_DIRS = ("tools", "model", "fpga", "rtl-sketch", "audition", "pnr", "spec")


def _is_json_load(node: ast.AST) -> bool:
    """`json.load(x)` / `json.loads(x)`, written as attribute access on the
    name `json`. An aliased import (`import json as j`) is not followed."""
    return (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
            and node.func.attr in LOADERS
            and isinstance(node.func.value, ast.Name) and node.func.value.id == "json")


def _guard_names(node: ast.AST) -> set[str]:
    """Names a statement establishes as objects: `require_keys(rec, ...)` --
    bare, or reached through a module (`mf.require_keys`) -- and
    `isinstance(rec, dict)`."""
    names: set[str] = set()
    for call in _own_nodes(node):
        if not isinstance(call, ast.Call) or not call.args:
            continue
        arg = call.args[0]
        if not isinstance(arg, ast.Name):
            continue
        func = call.func
        name = func.attr if isinstance(func, ast.Attribute) else (
            func.id if isinstance(func, ast.Name) else None)
        if name in ("require_keys", "_require_object"):
            names.add(arg.id)
        elif (name == "isinstance" and len(call.args) == 2
              and isinstance(call.args[1], ast.Name) and call.args[1].id == "dict"):
            names.add(arg.id)
    return names


def _own_nodes(node: ast.AST):
    """`node` and its descendants, stopping at a nested statement.

    An `if` whose body is `raise Refused(f"{rec.get('k')}")` reaches that
    `.get` through the `if`; walking the `raise` again when `_statements`
    yields it would count the same read twice.
    """
    yield node
    for child in ast.iter_child_nodes(node):
        if isinstance(child, ast.stmt):
            continue
        yield from _own_nodes(child)


def _function_hits(fn: ast.FunctionDef) -> list[tuple[int, str]]:
    """(line, what) for every unguarded `.get` in `fn`, in statement order.

    Statements are visited in source order through every nested block, so a
    guard in an `if` before the read counts, and a read inside a loop the
    guard precedes counts as guarded. A load inside a `with` whose body is
    the read is the ordinary shape (`with open(p) as fh: plan = json.load(fh)`).
    """
    hits: list[tuple[int, str]] = []
    bound: dict[str, int] = {}          # name -> line it was bound from json.load
    guarded: set[str] = set()
    for stmt in _statements(fn):
        for call in _own_nodes(stmt):
            if isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute) \
                    and call.func.attr == "get":
                receiver = call.func.value
                if _is_json_load(receiver):
                    hits.append((call.lineno, "json.loads(...).get(...)"))
                elif isinstance(receiver, ast.Name) and receiver.id in bound \
                        and receiver.id not in guarded:
                    hits.append((call.lineno, f"{receiver.id}.get(...)"))
        guarded |= _guard_names(stmt)
        if isinstance(stmt, (ast.Assign, ast.AnnAssign, ast.NamedExpr)):
            targets = stmt.targets if isinstance(stmt, ast.Assign) else [stmt.target]
            value = stmt.value
            if value is not None and _is_json_load(value):
                for t in targets:
                    if isinstance(t, ast.Name):
                        bound[t.id] = stmt.lineno
    return hits


def _statements(node: ast.AST):
    """Every statement under `node`, in source order, including nested ones."""
    for child in ast.iter_child_nodes(node):
        if isinstance(child, ast.stmt):
            yield child
        yield from _statements(child)


def file_hits(path: pathlib.Path) -> list[tuple[int, str, str]]:
    """(line, function, what) for every hit in `path`."""
    tree = ast.parse(path.read_text(), filename=str(path))
    out = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            out.extend((line, node.name, what) for line, what in _function_hits(node))
    return sorted(out)


def tracked_files(root: pathlib.Path, dirs) -> list[pathlib.Path]:
    rel = subprocess.run(["git", "ls-files", "--", *dirs], cwd=root,
                         capture_output=True, text=True, check=True).stdout.split("\n")
    return [root / r for r in rel if r.endswith(".py")]


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    paths = [pathlib.Path(a) for a in argv] if argv else tracked_files(ROOT, DEFAULT_DIRS)
    total = 0
    for path in paths:
        hits = file_hits(path)
        if hits:
            total += len(hits)
            rel = path.relative_to(ROOT) if path.is_relative_to(ROOT) else path
            print(f"{rel}: {len(hits)}")
            for line, fn, what in hits:
                print(f"  :{line:<5} {fn}()  {what}")
    print(f"\n{total} candidate site(s) in {sum(1 for p in paths if file_hits(p))} "
          f"of {len(paths)} file(s) -- each is a candidate, not a confirmed refusal")
    return 0


if __name__ == "__main__":
    sys.exit(main())
