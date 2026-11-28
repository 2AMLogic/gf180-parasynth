#!/usr/bin/env python3
"""Find a refusal LABEL on a path whose exit status is not a refusal status.

THE DEFECT CLASS (issue #1807, found by #1786's closing search).  A tool
prints `REFUSED: ...` because a precondition was not met -- nothing was
measured -- and then leaves with status 1.  `tools/exit_contract.py` reserves
1 for a measurement that DISAGREED, and `tools/run_all.py` renders 1 as a
RESULT, so the batch runner reports the refusal as evidence against the claim
the tool was pointed at.  The label and the status say opposite things, and a
caller that reads only the status -- a runner, a shell `&&` chain, a `make`
recipe -- gets the status.

Two shapes reach it, and both are scanned here:

  print-exit      a `print(...)` whose text LEADS WITH `REFUSED`, followed by
                  the exit statement that the refusal's own control flow
                  reaches, and that statement resolves to a status outside
                  the contract's NO_EVIDENCE set
  systemexit-str  `raise SystemExit("REFUSED: ...")` / `sys.exit("REFUSED:
                  ...")` -- a SystemExit carrying a STRING prints the string
                  and exits 1 (Python's documented rule; pinned as a known
                  answer by a real subprocess in this tool's test file), so
                  the label and the status disagree in one statement.  The
                  issue's scan looked for the first shape only and found 22
                  sites; this shape adds 25 more that no scan had listed.

A hit is a SHAPE match, never a verdict.  A site is this defect only if its
`REFUSED` is reached by a PRECONDITION failing; if a control MEASURED a
disagreement, 1 is right and the label is the bug.  The trace of the sites
this scan found on origin/main @ 03148399 is docs/refusal-exit-shape-trace.md;
a new hit needs the same trace before it is changed.

HOW THE EXIT IS READ, and why that is not a text window.  The issue's scan
took the next `return 1` within 6 lines and found 23 sites of which 6 were
false positives, every one of a shape this scan is built to step over (each
is a control in tools/test_refusal_exit_shape_scan.py), and it MISSED one
true positive whose `return 0 if ok else 1` sits 8 lines after the first
print (tools/probes/dc_t20_gate_qualification.py on origin/main).  Here the
status is read from control flow, in source order:

  * the first exit statement after the print IN ITS OWN BLOCK decides --
    `print(...)`; `return 2` reads 2, whatever a later branch returns;
  * a block that holds no exit after the print FALLS THROUGH to the enclosing
    block at the statement after the one that contains the print, and so on
    outward: `if bad: print("REFUSED ...")` with the `return 1` one line
    further out is a hit, which a same-block-only rule would miss;
  * every name the refusal's path ASSIGNS is carried along with its value
    (`ok = False`, `code = 2`, `rec, code = ..., REFUSED`), and the first
    conditional exit is EVALUATED against those values: `if not ok: return 1`
    after `ok = False` reads 1; `return 0 if ok else 1` reads 1; `return
    code` after `code = 2` reads 2.  A conditional exit whose test reads
    nothing the path assigned is another branch's exit
    (tools/check_doc_claims.py on origin/main: the refusal's `return 2`
    follows a `return 1` that belongs to `n[STALE]`);
  * a compound statement with no exit inside (`if verbose: print(...)`) is
    stepped over; a `break`/`continue` redirects the flow and ends the read;
  * a name resolves to an int when it is a same-file module int, an
    exit_contract import, or an import from a sibling module that declares
    it as a module-level int (`from refusal import REFUSED` -- 25 of the
    sites the issue's window could not have told from `return 1`);
  * a bool is not an exit status (`True == 1` in Python, which is how the
    issue's scan counted two `return True` predicates), the rule
    tools/refusal_exit_census.py applies to its literals.

WHAT IT CANNOT TELL YOU, stated rather than carried:

  * a label that is not a literal leading with `REFUSED` -- `print(msg)`,
    `sys.stderr.write(...)`, a logging call -- is invisible; the scan reads
    the first argument of `print` only;
  * an exit it cannot READ is reported as UNREAD, never as a hit and never as
    zero: a conditional exit whose test it cannot evaluate, a `return` of a
    bool or None or of a value that resolves to no int, a `return 0` after a
    REFUSED label (a reporting helper's value, or a refusal rendered PASS --
    this scan does not decide which).  UNREAD sites are counted and listed,
    grouped by reason, in every run;
  * whether the `REFUSED` was reached by a precondition or by a measurement.
    That is a trace, by reading and by driving the path, not an AST question.

WHY NOT EXTEND tools/refusal_exit_census.py.  The census counts refusal sites
inside `except` handlers and module-level `REFUSED =` constants.  None of the
sites here is inside a handler, so the census is blind to every one of them
before AND after any fix; the two tools answer different questions and are
reported separately.

    python tools/refusal_exit_shape_scan.py            # the scan, with its self-control
    python tools/refusal_exit_shape_scan.py --check    # gate: every hit must be a pinned exception

PRECONDITIONS, ASSERTED AT THE POINT OF USE, REFUSED (exit 2) WHEN UNMET:

  * `git ls-files` must run from this repository and list at least 300 Python
    files -- a scan over a partial tree under-reports and reads exactly like
    a scan over the whole one;
  * `tools/exit_contract.py` must parse and declare NO_EVIDENCE as a set of
    ints; the statuses are read from it, never retyped here;
  * the scanner must find a planted `print("REFUSED ..."); return 1` and must
    NOT find the planted `return REFUSED` shape, in the same process, before
    it answers about the tree.  WRONG BEFORE IT WAS RIGHT, twice: the issue's
    scan first iterated `ast.walk()`, which is breadth-first, so it visited
    each `Return` before the `print` and reported 0 sites on a tree with two
    known instances; and this scanner's own first draft read the enclosing
    chain outermost-first, so it read the function's trailing `return 0`
    instead of the refusal's `return 1` and found NOTHING -- the self-control
    REFUSED on its first run, before any count was printed.  A scanner that
    cannot find a planted defect REFUSES here rather than printing a count.
"""
from __future__ import annotations

import argparse
import ast
import collections
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
CONTRACT = pathlib.Path("tools") / "exit_contract.py"
MIN_PY_FILES = 300
LABEL = "REFUSED"
MODULE_LEVEL = "<module>"

# sentinels for "the print's block does not exit; look one block out" and
# "an exit is reachable but its status is not statically readable here"
FALLTHROUGH = "fallthrough"
UNREAD = "unread"

WHY_HELPER = "the next exit is a return of a bool or None (a predicate's value, not an exit status)"
WHY_FLAG = "a conditional exit follows whose test this scan cannot evaluate from what the refusal's path assigned"
WHY_NAME = "an exit follows whose status resolves to no int this scan can read"
WHY_ZERO = "a 0 return follows (a reporting helper's value, or a refusal rendered PASS)"
WHY_JUMP = "a break/continue redirects the flow before any exit"
WHY_END = "the function ends with no exit after the print"

# the planted sources the self-control runs through scan_source before the
# tree is read.  POSITIVE is the issue's shape; NEGATIVE is the corrected
# shape (tools/cymbal_low_tail.py: `return REFUSED`, the contract's constant).
CONTROL_POSITIVE = '''
def main(ok):
    if not ok:
        print("REFUSED: box preconditions failed")
        return 1
    return 0
'''
CONTROL_NEGATIVE = '''
from exit_contract import REFUSED

def main(ok):
    if not ok:
        print("REFUSED: the measurement's own controls do not pass")
        return REFUSED
    return 0
'''


class Refused(Exception):
    """A precondition of this scan was not met; it cannot answer."""


# ----------------------------------------------------------------- source

def _label_text(node) -> str | None:
    """The leading text of a print's first argument, for the string forms
    this tree writes, or None when it is not a readable string."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):                       # f"REFUSED: {x}"
        head = node.values[0] if node.values else None
        if isinstance(head, ast.Constant) and isinstance(head.value, str):
            return head.value
        return None
    if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Mod, ast.Add)):
        return _label_text(node.left)                         # "REFUSED: %s" % x, "REFUSED: " + x
    if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
            and node.func.attr == "format"):
        return _label_text(node.func.value)                   # "REFUSED: {}".format(x)
    return None


def _leads_with_label(node) -> bool:
    text = _label_text(node)
    return text is not None and text.lstrip().startswith(LABEL)


class _Env:
    """What the refusal's path assigns, the file's module ints, its imports
    and a resolver for sibling-module ints -- the names a return value may
    be read through."""

    def __init__(self, local, imported, contract, resolve, values=None, aliases=None):
        self.local = local                  # module-level name -> value node
        self.imported = imported            # local name -> (module, original name)
        self.aliases = aliases or {}        # `import X as Y`: Y -> X
        self.contract = contract
        self.resolve = resolve or (lambda module, name: None)
        self.values = dict(values or {})

    def with_values(self, values) -> "_Env":
        return _Env(self.local, self.imported, self.contract, self.resolve, values, self.aliases)


def _int_of(value, env: _Env, depth: int = 0) -> int | None:
    """The int an exit value resolves to, or None.  Literals always (a bool is
    NOT an int here); a name through what the path assigned it, a same-file
    module int, an exit_contract import or a sibling-module int; a
    conditional expression through its evaluated test."""
    if depth > 4:
        return None
    if isinstance(value, ast.Constant) and isinstance(value.value, int) \
            and not isinstance(value.value, bool):
        return value.value
    if isinstance(value, ast.Name):
        if value.id in env.values:
            assigned = env.values[value.id]
            return None if assigned is None else _int_of(assigned, env, depth + 1)
        if value.id in env.local:
            return _int_of(env.local[value.id], env, depth + 1)
        if value.id in env.imported:
            module, name = env.imported[value.id]
            return env.contract.get(name) if module == "exit_contract" else env.resolve(module, name)
        return None
    if isinstance(value, ast.Attribute) and isinstance(value.value, ast.Name):
        module, attr = env.aliases.get(value.value.id, value.value.id), value.attr
        return env.contract.get(attr) if module == "exit_contract" else env.resolve(module, attr)
    if isinstance(value, ast.IfExp):
        truth = _eval(value.test, env)
        if truth is None:
            return None
        return _int_of(value.body if truth else value.orelse, env, depth + 1)
    return None


def _eval(test, env: _Env):
    """True/False for the test forms a refusal's flag is read through --
    `ok`, `not ok`, `a and b`, `a or b`, `code == 2` -- against the values the
    path assigned; None when the test reads anything else."""
    if isinstance(test, ast.Constant) and isinstance(test.value, bool):
        return test.value
    if isinstance(test, ast.Name):
        v = env.values.get(test.id)
        return v.value if isinstance(v, ast.Constant) and isinstance(v.value, bool) else None
    if isinstance(test, ast.UnaryOp) and isinstance(test.op, ast.Not):
        inner = _eval(test.operand, env)
        return None if inner is None else (not inner)
    if isinstance(test, ast.BoolOp) and isinstance(test.op, (ast.And, ast.Or)):
        vals = [_eval(v, env) for v in test.values]
        if any(v is None for v in vals):
            return None
        return all(vals) if isinstance(test.op, ast.And) else any(vals)
    if isinstance(test, ast.Compare) and len(test.ops) == 1 and len(test.comparators) == 1 \
            and isinstance(test.ops[0], (ast.Equal, ast.NotEqual, ast.Is, ast.IsNot)):
        left = _int_of(test.left, env)
        right = _int_of(test.comparators[0], env)
        if left is None or right is None:
            return None
        same = left == right
        return same if isinstance(test.ops[0], (ast.Equal, ast.Is)) else (not same)
    return None


def _assigned_values(st: ast.stmt) -> dict:
    """{name: value node} for what a statement assigns simply: `ok = False`,
    `rec, code = r, REFUSED` (tuple to tuple), `code: int = 2`.  A target
    assigned anything else (`x = f()`, `d[k] = v`) maps to None: read, but
    not as a constant."""
    def put(targets, values, out):
        if len(targets) == len(values):
            for t, v in zip(targets, values):
                if isinstance(t, ast.Name):
                    out[t.id] = v
                else:
                    out[None] = None
    out: dict = {}
    if isinstance(st, ast.Assign) and len(st.targets) == 1:
        t, v = st.targets[0], st.value
        if isinstance(t, ast.Tuple) and isinstance(v, ast.Tuple):
            put(t.elts, v.elts, out)
        elif isinstance(t, ast.Name):
            out[t.id] = v
    elif isinstance(st, ast.AnnAssign) and isinstance(st.target, ast.Name) and st.value is not None:
        out[st.target.id] = st.value
    elif isinstance(st, ast.AugAssign) and isinstance(st.target, ast.Name):
        out[st.target.id] = None
    return {k: v for k, v in out.items() if k is not None}


def _names_read(node: ast.AST) -> set[str]:
    return {n.id for n in ast.walk(node) if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)}


def _exit_call_argv(call: ast.Call) -> list | None:
    """The argument list when `call` is `sys.exit(...)` / `exit(...)`, else None."""
    f = call.func
    if isinstance(f, ast.Name) and f.id == "exit":
        return call.args
    if isinstance(f, ast.Attribute) and f.attr == "exit" and isinstance(f.value, ast.Name) \
            and f.value.id == "sys":
        return call.args
    return None


def _systemexit_argv(call: ast.Call) -> list | None:
    """The argument list when `call` is `SystemExit(...)`, else None."""
    f = call.func
    if isinstance(f, ast.Name) and f.id == "SystemExit":
        return call.args
    if isinstance(f, ast.Attribute) and f.attr == "SystemExit" \
            and isinstance(f.value, ast.Name) and f.value.id == "builtins":
        return call.args
    return None


def _is_exit_stmt(st: ast.stmt) -> bool:
    if isinstance(st, (ast.Return, ast.Raise)):
        return True
    if isinstance(st, ast.Expr) and isinstance(st.value, ast.Call):
        return _exit_call_argv(st.value) is not None
    return False


def _argv_status(argv, env: _Env) -> int | None:
    """The status a SystemExit/exit argv carries, by the interpreter's rule
    (run as a real subprocess in this tool's test file): no argument is 0; an
    int is itself; ANY other object -- a string, a tuple -- is printed and
    exits 1.  None only when an int argument is a name this scan cannot read."""
    if not argv:
        return 0
    if len(argv) == 1:
        value = argv[0]
        if isinstance(value, ast.Constant):
            if value.value is None:                     # sys.exit(None) is exit 0
                return 0
            if isinstance(value.value, bool):           # not an exit status here, as for a return
                return None
            return value.value if isinstance(value.value, int) else 1
        if isinstance(value, (ast.JoinedStr, ast.BinOp)) or (
                isinstance(value, ast.Call) and isinstance(value.func, ast.Attribute)
                and value.func.attr == "format"):
            return 1
        return _int_of(value, env)
    return 1


def _read(status) -> tuple:
    """An exit status as the scan reports it: (status, None) when it is a
    non-zero status outside or inside NO_EVIDENCE alike (the caller decides);
    0 is UNREAD (WHY_ZERO); None -- unreadable -- is UNREAD (WHY_NAME)."""
    if status is None:
        return None, WHY_NAME
    if status == 0:
        return None, WHY_ZERO
    return status, None


def _exit_status(st: ast.stmt, env: _Env):
    """(status, why) an exit statement carries: `raise SystemExit(n)` and
    `sys.exit(n)` carry n; one carrying any other object carries 1 (the
    interpreter's rule, _argv_status).  A `return` of a bool or None is a
    predicate's value, not an exit status."""
    if isinstance(st, ast.Return):
        if st.value is None:
            return None, WHY_HELPER
        if isinstance(st.value, ast.Constant) and (isinstance(st.value.value, bool)
                                                   or st.value.value is None):
            return None, WHY_HELPER
        return _read(_int_of(st.value, env))
    if isinstance(st, ast.Expr) and isinstance(st.value, ast.Call):
        argv = _exit_call_argv(st.value)
        return (None, WHY_NAME) if argv is None else _read(_argv_status(argv, env))
    if isinstance(st, ast.Raise):
        if isinstance(st.exc, ast.Call):
            for argv_of in (_systemexit_argv, _exit_call_argv):
                argv = argv_of(st.exc)
                if argv is not None:
                    return _read(_argv_status(argv, env))
        return None, WHY_NAME            # a bare re-raise or another exception
        return None, WHY_NAME            # a bare re-raise or another exception
    return None, WHY_NAME


def _has_exit(st: ast.stmt) -> bool:
    """Whether any exit statement sits anywhere inside `st`, excluding `st`."""
    if isinstance(st, (ast.Return, ast.Raise)):
        return False
    return any(_is_exit_stmt(n) for n in ast.walk(st))


def _next_exit(stmts: list, start: int, env: _Env):
    """What the straight-line flow from `stmts[start]` runs into, in source
    order: (status, env) when an exit is read; (FALLTHROUGH, env) when the
    block holds none, the env grown by every name assigned along the way; or
    (UNREAD, why).  A compound statement with NO exit inside is stepped over;
    one WITH an exit is conditional, and the branch taken is the one its
    test evaluates to against the env -- unreadable test, UNREAD."""
    for st in stmts[start:]:
        if isinstance(st, (ast.Break, ast.Continue)):
            return UNREAD, WHY_JUMP
        if _is_exit_stmt(st):
            status, why = _exit_status(st, env)
            return (UNREAD, why) if status is None else (status, env)
        env = env.with_values({**env.values, **_assigned_values(st)})
        if _has_exit(st):
            if isinstance(st, ast.If):
                truth = _eval(st.test, env)
                if truth is True:
                    status, info = _next_exit(st.body, 0, env)
                elif truth is False:
                    status, info = _next_exit(st.orelse, 0, env) if st.orelse else (FALLTHROUGH, env)
                else:
                    status, info = UNREAD, WHY_FLAG
                if status is not FALLTHROUGH:
                    return status, info
                continue
            return UNREAD, WHY_FLAG
    return FALLTHROUGH, env


def _reaching_status(chain: list, env: _Env):
    """The status the refusal reaches: the next exit in its own block, else
    -- falling through -- the next exit in each enclosing block from the
    point after the statement that contains the print.  `chain` is that
    context, innermost LAST, as (stmts, index-after) pairs."""
    for stmts, start in reversed(chain):
        status, info = _next_exit(stmts, start, env)
        if status is not FALLTHROUGH:
            return status, info
        env = info
    return UNREAD, WHY_END


def _sub_blocks(st: ast.stmt) -> list[tuple[list, list]]:
    """The statement lists nested directly in `st`, each with the blocks the
    flow enters next when it falls through this one (a `try` body continues
    into its `else` and `finally`; a handler into its `finally`; a loop body
    into the loop's `else`).  Nested `def`/`class` bodies are NOT listed:
    they are visited as their own functions by _functions, and walking them
    from here too would report every one of their sites twice, once as
    `<module>`."""
    def pair(block, extra=()):
        return block, [(b, 0) for b in extra]
    if isinstance(st, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        return []
    if isinstance(st, ast.Try):
        out = [pair(h.body, [st.finalbody]) for h in st.handlers]
        out.append(pair(st.body, [st.orelse, st.finalbody]))
        if st.orelse:
            out.append(pair(st.orelse, [st.finalbody]))
        if st.finalbody:
            out.append(pair(st.finalbody))
        return out
    out = []
    for field in ("body", "orelse"):
        b = getattr(st, field, None)
        if not isinstance(b, list) or not b:
            continue
        loop_else = [st.orelse] if (field == "body" and isinstance(st, (ast.For, ast.AsyncFor, ast.While))
                                    and isinstance(st.orelse, list) and st.orelse) else []
        out.append(pair(b, loop_else))
    return out


def _is_int_literal(node) -> bool:
    return isinstance(node, ast.Constant) and isinstance(node.value, int) \
        and not isinstance(node.value, bool)


def _module_assigns(tree: ast.AST) -> dict[str, ast.expr]:
    """Module-level `NAME = <value>` and `A, B, C = x, y, z` declarations (the
    form most tools here declare their exit statuses in), name -> value node.
    The node is kept, not its int: `OK, FAIL, REFUSED = rp.OK, rp.FAIL,
    rp.REFUSED` chains through another module and is resolved by _int_of
    with the sibling resolver."""
    out: dict[str, ast.expr] = {}
    for st in tree.body:
        if not isinstance(st, ast.Assign):
            continue
        for t in st.targets:
            if isinstance(t, ast.Name):
                out[t.id] = st.value
            elif isinstance(t, ast.Tuple) and isinstance(st.value, ast.Tuple) \
                    and len(t.elts) == len(st.value.elts):
                for te, ve in zip(t.elts, st.value.elts):
                    if isinstance(te, ast.Name):
                        out[te.id] = ve
    return out


def _module_int_assigns(tree: ast.AST) -> dict[str, int]:
    """The module-level declarations that ARE int literals, as ints."""
    return {k: v.value for k, v in _module_assigns(tree).items() if _is_int_literal(v)}


def _from_imports(tree: ast.AST) -> dict[str, tuple[str, str]]:
    """`from X import Y [as Z]` bindings: local name -> (X, Y)."""
    out: dict[str, tuple[str, str]] = {}
    for st in ast.walk(tree):
        if isinstance(st, ast.ImportFrom) and st.module and not st.level:
            for a in st.names:
                out[a.asname or a.name] = (st.module, a.name)
    return out


def _import_aliases(tree: ast.AST) -> dict[str, str]:
    """`import X [as Y]` bindings: the name the module is reached through ->
    the module (`import refaudio_local as rl` -> {rl: refaudio_local})."""
    out: dict[str, str] = {}
    for st in ast.walk(tree):
        if isinstance(st, ast.Import):
            for a in st.names:
                if a.name and "." not in a.name:
                    out[a.asname or a.name] = a.name
    return out


def _functions(tree: ast.AST):
    """(function name, statement list) for every def at any depth, and the
    module body.  The module body is walked WITHOUT descending into defs, so
    each statement list is visited exactly once."""
    yield MODULE_LEVEL, tree.body
    for n in ast.walk(tree):
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
            yield n.name, n.body


def scan_source(source: str, contract: dict[str, int], no_evidence: frozenset[int],
                filename: str = "<string>", resolve=None) -> dict:
    """Scan one module's source.  `resolve(module, name)` answers a
    sibling-module int or None.  Returns `hits` and `unread`, each a list of
    dicts with file, func, line, shape and (for hits) status and label, (for
    unread) why."""
    tree = ast.parse(source)
    env = _Env(_module_assigns(tree), _from_imports(tree), contract, resolve,
               aliases=_import_aliases(tree))
    hits, unread = [], []

    def walk(stmts: list, func: str, chain: list) -> None:
        for i, st in enumerate(stmts):
            line = st.lineno
            # shape 2: SystemExit / sys.exit carrying a REFUSED string
            if isinstance(st, ast.Raise) and isinstance(st.exc, ast.Call):
                for argv_of in (_systemexit_argv, _exit_call_argv):
                    argv = argv_of(st.exc)
                    if argv and _leads_with_label(argv[0]):
                        hits.append(dict(file=filename, func=func, line=line,
                                         shape="systemexit-str", status=1,
                                         label=_label_text(argv[0]).strip()))
            # shape 1: a REFUSED print, then the exit its control flow reaches
            if isinstance(st, ast.Expr) and isinstance(st.value, ast.Call) \
                    and isinstance(st.value.func, ast.Name) and st.value.func.id == "print" \
                    and st.value.args and _leads_with_label(st.value.args[0]):
                status, why = _reaching_status(chain + [(stmts, i + 1)], env)
                if status is UNREAD:
                    unread.append(dict(file=filename, func=func, line=line, why=why))
                elif status not in no_evidence:
                    hits.append(dict(file=filename, func=func, line=line,
                                     shape="print-exit", status=status,
                                     label=_label_text(st.value.args[0]).strip()))
            for block, extra in _sub_blocks(st):
                walk(block, func, chain + extra + [(stmts, i + 1)])

    for func, body in _functions(tree):
        walk(body, func, [])
    return {"hits": hits, "unread": unread}


# ------------------------------------------------------------------- tree

def tracked_python(roots=()) -> list[str]:
    """Every tracked `.py` path, narrowed to the `roots` prefixes when any are
    given.  REFUSES if git cannot answer or the REPOSITORY holds fewer than
    MIN_PY_FILES Python files -- a scan over a partial tree under-reports and
    reads exactly like a scan over the whole one.  The precondition is on the
    repository, not on the selection, so `--roots` can deliberately narrow."""
    try:
        out = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True,
                             text=True, check=True).stdout
    except (OSError, subprocess.CalledProcessError) as exc:
        raise Refused(f"git ls-files could not run in {ROOT}: {exc}") from exc
    files = [f for f in out.split() if f.endswith(".py")]
    if len(files) < MIN_PY_FILES:
        raise Refused(f"git ls-files found {len(files)} Python files in {ROOT}, under the "
                      f"{MIN_PY_FILES} this repository has; a partial tree under-reports the scan")
    if roots:
        files = [f for f in files if any(f.startswith(r) for r in roots)]
    return files


def read_contract() -> tuple[dict[str, int], frozenset[int]]:
    """(named outcomes, NO_EVIDENCE) read from tools/exit_contract.py by AST.
    REFUSES if the declaration is gone, so the scan cannot answer from a
    retyped assumption."""
    try:
        tree = ast.parse((ROOT / CONTRACT).read_text())
    except (OSError, SyntaxError) as exc:
        raise Refused(f"cannot read {CONTRACT}: {exc}") from exc
    ints = _module_int_assigns(tree)
    for st in tree.body:
        if isinstance(st, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id == "NO_EVIDENCE" for t in st.targets):
            v = st.value
            if isinstance(v, ast.Call) and isinstance(v.func, ast.Name) \
                    and v.func.id in ("set", "frozenset"):
                v = v.args[0] if v.args else None
            if isinstance(v, (ast.Set, ast.List, ast.Tuple)):
                out = set()
                for e in v.elts:
                    if isinstance(e, ast.Name) and e.id in ints:
                        out.add(ints[e.id])
                    elif isinstance(e, ast.Constant) and isinstance(e.value, int) \
                            and not isinstance(e.value.value, bool):
                        out.add(e.value.value)
                    else:
                        raise Refused(f"{CONTRACT}: NO_EVIDENCE holds {ast.dump(e)[:60]}, "
                                      f"which this scan cannot resolve to an int")
                if out:
                    return ints, frozenset(out)
            raise Refused(f"{CONTRACT}: NO_EVIDENCE is empty or not a set literal this scan can read")
    raise Refused(f"{CONTRACT} declares no NO_EVIDENCE; a refusal status cannot be told from a result")


def _sibling_resolver(for_file: str, contract: dict, cache: dict, building: set):
    """`resolve(module, name)` for the int a sibling module declares at module
    level under `name`, transitive: `refaudio_member_list.REFUSED` is
    `s3.REFUSED`, which is `rl.REFUSED`, which is 2.  Looked up in the
    importing file's directory and in tools/ and model/ (the sys.path roots
    this repository's scripts add).  A module that is not a file, a name it
    does not declare, or a cycle in the chain resolves to None.  Results are
    cached per (file, name); `building` holds the keys being resolved so an
    A-imports-B-imports-A chain stops instead of recursing."""
    dirs = [ROOT / pathlib.Path(for_file).parent, ROOT / "tools", ROOT / "model", ROOT]

    def resolve(module, name):
        for d in dirs:
            p = d / f"{module}.py"
            if not p.is_file():
                continue
            key = (str(p), name)
            if key in cache:
                return cache[key]
            if key in building:
                return None
            building.add(key)
            try:
                rel = p.relative_to(ROOT).as_posix()
                tree = ast.parse(p.read_text())
                env = _Env(_module_assigns(tree), _from_imports(tree), contract,
                           _sibling_resolver(rel, contract, cache, building),
                           aliases=_import_aliases(tree))
                val = _int_of(ast.Name(id=name, ctx=ast.Load()), env)
            except (OSError, SyntaxError, ValueError):
                val = None
            finally:
                building.discard(key)
            cache[key] = val
            return val
        return None
    return resolve


def self_control(contract: dict, no_evidence: frozenset[int]) -> None:
    """The scanner must find the planted defect and must not find the planted
    correction, in this process, before it answers about the tree.  REFUSES
    otherwise -- a count from a scanner that cannot see a planted site is
    not a count."""
    pos = scan_source(CONTROL_POSITIVE, contract, no_evidence, "<control-positive>")
    neg = scan_source(CONTROL_NEGATIVE, contract, no_evidence, "<control-negative>")
    if not [h for h in pos["hits"] if h["status"] == 1 and h["shape"] == "print-exit"]:
        raise Refused("self-control: the planted print('REFUSED ...'); return 1 was NOT found; "
                      "the scan cannot see the defect it exists for")
    if neg["hits"]:
        raise Refused(f"self-control: the planted `return REFUSED` shape was reported as a "
                      f"hit ({neg['hits']}); the scan would flag the corrected shape")


def scan(roots=()) -> dict:
    """The scan over the tracked tree, after its preconditions and its
    self-control.  `hits` is sorted in source order (file, line)."""
    files = tracked_python(roots)
    contract, no_evidence = read_contract()
    self_control(contract, no_evidence)
    hits, unread, n = [], [], 0
    cache: dict = {}
    for f in files:
        try:
            src = (ROOT / f).read_text()
            res = scan_source(src, contract, no_evidence, f,
                              _sibling_resolver(f, contract, cache, set()))
        except (OSError, SyntaxError, ValueError):
            continue
        n += 1
        hits += res["hits"]
        unread += res["unread"]
    hits.sort(key=lambda h: (h["file"], h["line"]))
    unread.sort(key=lambda h: (h["file"], h["line"]))
    return {"n_files": n, "roots": tuple(roots) or ("(every tracked .py)",),
            "no_evidence": sorted(no_evidence), "hits": hits, "unread": unread}


# ------------------------------------------------------------------- gate

# Every hit must be one of these, or --check is red.  Keyed on
# [file, enclosing function, shape, count] -- never on a line number, so an
# edit above a site cannot red the gate (tools/refusal_exit_census.py #868).
# Each entry names the issue that owns the site: one defect, one owner.  The
# comparison is ONE-DIRECTIONAL on purpose.  A hit with no entry is red.  An
# entry whose site is gone -- its owner landed -- is a NOTE, never red: a pin
# that fails on somebody else's fix gets trained away, and the note is what
# keeps the stale entry visible until it is removed.
EXPECTED = []


def _entry(file, func, shape, count, owner, reason):
    return dict(file=file, func=func, shape=shape, count=count, owner=owner, reason=reason)


# --- the issue's excluded sites: owned by other open issues (#1807's scope note)
EXPECTED += [
    _entry("tools/cymbal_m_origin.py", "main", "print-exit", 1,
           "#1806 (same family as #1786; #1789 names the same line)",
           "'the shared instrument's own controls do not pass': a precondition refusal exiting 1"),
    _entry("tools/cymbal_tone_knob.py", "_report", "print-exit", 1, "#1786",
           "'this instrument's own controls do not pass' behind one bool that also carries a measured disagreement"),
    _entry("tools/cymbal_vca_clip.py", "main", "print-exit", 1, "#1769 (PR #1792)",
           "'this instrument's own controls do not pass': a precondition refusal exiting 1"),
    _entry("tools/cymbal_vca_drive.py", "main", "print-exit", 2,
           "pinned also in tools/refusal_exit_census.py EXPECTED.sites_outside_set (#804)",
           "the --require-source exit 1 is that tool's documented contract (argparse help, "
           "docs/scorecard/cymbal-369/vca-drive/README.md)"),
]

# --- found by this scanner on origin/main @ 03148399, NOT TRACED: #1841 owns
# every one.  Seventeen SystemExit-carrying-a-string sites no scan had listed
# and six prints whose flag is read eight lines later (past the issue's
# window).  The reading of each source in #1841 says where to look; a site is
# reclassified only by driving the path, and its entry here is then removed.
EXPECTED += [
    _entry("model/measure_harness.py", "assert_precondition", "systemexit-str", 2, "#1841",
           "the harness's own known-answer controls, `shape mismatch` and `off by`"),
    _entry("tools/check_decimator_saturation.py", "main", "systemexit-str", 1, "#1841",
           "iverilog and vvp are required"),
    _entry("tools/check_phrase_schedule.py", "applied", "systemexit-str", 2, "#1841",
           "one write log expected; an applied frame differs from its pin-side prediction"),
    _entry("tools/check_phrase_schedule.py", "main", "systemexit-str", 1, "#1841",
           "N/M gates against the schedule"),
    _entry("tools/compile_dag.py", "run_evidence", "systemexit-str", 1, "#1841",
           "a node declares an evidence key run_evidence() cannot execute"),
    _entry("tools/compile_dag.py", "load_results", "systemexit-str", 2, "#1841",
           "the results file is present but unreadable, or not an object"),
    _entry("tools/measure_dag_churn.py", "replay", "systemexit-str", 2, "#1841",
           "a shallow repository; no [dag] commits to replay"),
    _entry("tools/probes/absorbing_aggregate_audit.py", "tracked_python", "systemexit-str", 1, "#1841",
           "no .py files under the root"),
    _entry("tools/probes/hihat/hh_probe.py", "derive_filters", "systemexit-str", 1, "#1841",
           "the formula does not reproduce the two already-stated filters"),
    _entry("tools/probes/trajectory_within_rate_drift.py", "load_old", "systemexit-str", 1, "#1841",
           "the merge-base estimator cannot be read from git"),
    _entry("tools/verify_voice_scoped.py", "parse_expect_first", "systemexit-str", 2, "#1841",
           "--expect-first is not TAP@FRAME[:SAMPLES], or names an unknown tap"),
    _entry("tools/verify_voice_scoped.py", "extremes_only", "systemexit-str", 1, "#1841",
           "the extremes index is out of range"),
    _entry("tools/probes/dc_t20_gate_qualification.py", "main", "print-exit", 6, "#1841",
           "six of the gate's own controls each set `ok = False`; `return 0 if ok else 1` eight lines on"),
]


def _keyed(hits):
    return collections.Counter((h["file"], h["func"], h["shape"]) for h in hits)


def check(res: dict) -> tuple[bool, list[str]]:
    """True when every hit is a pinned exception.  Prints the pinned entries
    whose sites are gone as notes, and the UNREAD count, never as failures."""
    out, ok = [], True
    got = _keyed(res["hits"])
    for entry in EXPECTED:
        k = (entry["file"], entry["func"], entry["shape"])
        n = got.get(k, 0)
        if n > entry["count"]:
            ok = False
            out.append(f"  RED  {k[0]} ({k[1]}) {k[2]}: {n} hits, {entry['count']} pinned "
                       f"({entry['owner']})")
        elif n == 0:
            out.append(f"  note pinned exception no longer present, remove it from EXPECTED: "
                       f"{k[0]} ({k[1]}) {k[2]} -- {entry['owner']}")
        got[k] = 0
    for (f, func, shape), n in sorted(got.items()):
        if n:
            ok = False
            out.append(f"  RED  {f} ({func}) {shape}: {n} hit(s) not in EXPECTED")
    owners = {(e["file"], e["func"], e["shape"]): e["owner"] for e in EXPECTED}
    for h in res["hits"]:
        k = (h["file"], h["func"], h["shape"])
        out.append(f"  hit  {h['file']}:{h['line']} ({h['func']}) {h['shape']} status {h['status']}"
                   f"{'' if k in owners else '  NOT PINNED'}: {h['label'].splitlines()[0][:70]}"
                   + (f"  -- {owners[k]}" if k in owners else ""))
    if not res["hits"]:
        out.append("  no shape hits in the scanned tree")
    out.append(f"  {len(res['unread'])} UNREAD print('REFUSED ...') site(s) -- status conditional or "
               f"not statically readable; listed by the scan, not pinned")
    return ok, out


def render(res: dict) -> str:
    how = {0: "PASS", 1: "FAIL (a RESULT)"}
    lines = [f"{res['n_files']} tracked Python files under {', '.join(res['roots'])} scanned; "
             f"a status outside {res['no_evidence']} is rendered by tools/run_all.py as a RESULT.",
             "", f"{'site':<52} {'shape':<15} exit  run_all reports  label"]
    for h in res["hits"]:
        where = f"{h['file']}:{h['line']}"
        lines.append(f"{where:<52} {h['shape']:<15} {h['status']:>4}  "
                     f"{how.get(h['status'], 'FAIL'):<14}  {h['label'].splitlines()[0][:60]}")
    if not res["hits"]:
        lines.append("  none")
    lines += ["", f"{len(res['unread'])} UNREAD site(s): a REFUSED print whose exit status this scan "
                  "cannot read. Not hits, and not zero -- each needs a trace before it is called either:"]
    by_why = collections.OrderedDict()
    for u in res["unread"]:
        by_why.setdefault(u["why"], []).append(f"{u['file']}:{u['line']}")
    for why, sites in by_why.items():
        lines.append(f"  {why}  [{len(sites)}]")
        lines.append("    " + ", ".join(sites))
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--check", action="store_true",
                    help="gate: every hit must be a pinned exception in EXPECTED")
    ap.add_argument("--roots", nargs="*", default=[],
                    help="tracked-path prefixes to scan (default: every tracked .py)")
    a = ap.parse_args(argv)
    try:
        res = scan(a.roots)
        if a.check:
            ok, lines = check(res)
            print("\n".join(lines))
            return 0 if ok else 1
        print(render(res))
    except Refused as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 2            # tools/exit_contract.py REFUSED; run_all.py reports NO-VERDICT
    return 0


if __name__ == "__main__":
    sys.exit(main())
