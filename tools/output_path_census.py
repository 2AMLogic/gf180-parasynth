#!/usr/bin/env python3
"""Census of command-line output paths: where a non-test tool writes a file
whose location the user named, and whether that file's parent is made first.

THE CLASS (#2040). `tools/f1_rtl_filter_path.py --json PATH` wrote its record
after the comparison had run and printed, and nothing made PATH's parent.
Under a missing one the write raised `FileNotFoundError` out of `main`: exit 1
with a traceback -- FAIL, "the comparison ran and disagreed" -- for a
comparison that had printed BIT-EXACT, and its record was lost.  The fix is in
that file.  This tool counts the rest of the class, which nobody had counted.
A grep for `.write_text(` answers a different question: it finds every write,
and says nothing about which of them a user-named path can send off a cliff.

WHAT A CANDIDATE IS.  A write -- `write_text`, `write_bytes`, `touch`,
`open(..., "w")`, `wavfile.write`, `shutil.copyfile` and the other sinks
named below -- whose target is built on an attribute of an argparse namespace
(`args.out`, `a.json`), through local aliases (`out = Path(args.json)`), or
through a helper that writes to one of its own parameters
(`write_json(args.out, record)`), following that parameter out through up to
four calls (`main` -> `build(dest)` -> `publish(dest)` -> `dest.write_bytes`).
A `mkdir` -- a `Path.mkdir`, an `os.makedirs` or an `os.mkdir` -- is followed
the same way, so a parent made by a helper counts.  For each candidate it
reports whether the directory that must exist for the write does:

    MADE      a `mkdir` of exactly that directory runs first: earlier in the
              write's own function, or in a caller, before the call that
              leads to the write.  The line is printed; read it as handled.
    MADE?     a matching `mkdir` exists, but not in a place this can order
              against the write.  Read it: this tool does not follow control
              flow between functions.
    NOT-MADE  no `mkdir` of that directory anywhere in the file.
    UNKNOWN   the target is derived in a way this cannot follow (a subscript,
              a helper it cannot resolve, a chain deeper than four calls).
              Read it.

and `guarded` when the write, or a call that reaches it, sits in a `try`
whose handler takes `OSError`, `Exception` or everything: the failure is then
decided rather than left to the traceback.  `NOT-MADE` and `UNKNOWN` are the
columns to read by hand.

    python tools/output_path_census.py                     # every candidate
    python tools/output_path_census.py --only NOT-MADE     # one verdict column
    python tools/output_path_census.py --summary           # counts only

WHAT IT CANNOT TELL YOU, and a count taken from it inherits every one:

  * whether a write happens after a measurement or before anything ran.  The
    two are the same defect only in the first case;
  * whether the parent exists for a reason this file cannot see: made by
    `tools/run_all.py`, by a caller in another file, or because the path is
    always inside a directory the tool made by other means;
  * anything about a path that did not come from argparse (`sys.argv[1]`, a
    config file), and the dotted sinks must be written as they are named
    below: `from shutil import copyfile` is not seen.  A helper is followed
    only when it is called as `name(...)` or `self.name(...)` in its own
    file; a write or a `mkdir` through a parameter with no such caller is
    counted in the summary as unresolved, and is neither a candidate nor a
    clearance;
  * a local name is resolved in its own function and the functions it is
    nested in, so a helper's parameter named like a caller's local is not
    confused with it, but a name rebound twice in one function is both.

PRECONDITIONS, ASSERTED AT THE POINT OF USE, REFUSED (exit 2) WHEN UNMET:

  * `git ls-files` must run from this repository and list at least
    MIN_TRACKED_FILES non-test Python files.  A census over a partial tree
    under-reports the class and reads exactly like one over the whole;
  * every tracked non-test file must parse;
  * the detectors must still find the #2040 shape in the five-line sample
    below, see the `parent.mkdir` that repairs it, and see that repair when a
    helper makes it.  A census whose detectors had gone blind would print an
    empty column with a straight face.

This tool reports.  It is not a gate and pins nothing: a new candidate is
somebody's new tool, not a red build (the lesson `tools/refusal_exit_census.py`
records about pinned totals).
"""
from __future__ import annotations

import argparse
import ast
import pathlib
import re
import subprocess
import sys

import refusal

ROOT = pathlib.Path(__file__).resolve().parents[1]
TEST_NAME = re.compile(r"(^|/)(test_[^/]*|[^/]*_test|conftest)\.py$")
MIN_TRACKED_FILES = 300
MAX_HOPS = 4
WRITE_MODES = frozenset({"w", "x", "a", "wb", "xb", "ab", "w+", "x+", "a+",
                         "w+b", "x+b", "a+b"})
METHOD_SINKS = frozenset({"write_text", "write_bytes", "touch"})
FUNCTION_SINKS = {"wavfile.write": 0, "soundfile.write": 0, "np.savetxt": 0,
                  "shutil.copyfile": 1, "shutil.copy": 1, "shutil.move": 1,
                  "os.replace": 1, "os.rename": 1}
NS_PARAMS = frozenset({"args", "ns", "opts", "options", "parsed"})
PATH_CALLS = frozenset({"Path", "os.fspath", "str"})
SAME_PATH = frozenset({"abspath", "realpath", "normpath"})
SAME_PARENT = frozenset({"with_suffix", "with_name", "resolve", "absolute", "expanduser"})
MAKE_DIRS = frozenset({"os.makedirs", "os.mkdir"})
VERDICTS = ("MADE", "MADE?", "NOT-MADE", "UNKNOWN")

DEFECT = """\
import argparse, json, pathlib
ap = argparse.ArgumentParser()
ap.add_argument("--json")
a = ap.parse_args()
pathlib.Path(a.json).write_text(json.dumps({}))
"""
REPAIRED = """\
import argparse, json, pathlib
ap = argparse.ArgumentParser()
ap.add_argument("--json")
a = ap.parse_args()
out = pathlib.Path(a.json)
out.parent.mkdir(parents=True, exist_ok=True)
out.write_text(json.dumps({}))
"""
REPAIRED_IN_HELPER = """\
import argparse, json, pathlib
ap = argparse.ArgumentParser()
ap.add_argument("--json")
a = ap.parse_args()
def ensure(path):
    path.parent.mkdir(parents=True, exist_ok=True)
def record(name):
    pathlib.Path(name).write_text(json.dumps({}))
ensure(pathlib.Path(a.json))
record(a.json)
"""


class Refused(refusal.Refused, RuntimeError):
    """A precondition of the census was not met; nothing was counted."""


def dotted(node) -> str:
    """`shutil.copyfile` for the call `shutil.copyfile(...)`, else ''."""
    parts = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
        return ".".join(reversed(parts))
    return ""


def part(node) -> str | None:
    """A path component as this census names it: "child", "parent" for `..`,
    '' for one that adds nothing, None for one it cannot place (absolute, or
    not text at all)."""
    if isinstance(node, ast.JoinedStr) or not isinstance(node, ast.Constant):
        return "child"
    if not isinstance(node.value, str):
        return None
    text = node.value
    if text.startswith("/") or re.match(r"^[A-Za-z]:", text):
        return None
    if text in {"", "."}:
        return ""
    return "parent" if text == ".." else "child"


def precedes(before: list, after: list) -> bool:
    """Does the event whose scopes are `before` run before the one whose
    scopes are `after`?  True when they share a function and the first
    stands at the earlier line there: the same function, or the same caller
    reaching the helper that does it first.  Anything else is not decided."""
    return any(one is other and first < later
               for one, first in before for other, later in after)


class Candidate:
    """One write to a user-named path, and the mkdirs that bear on it."""

    def __init__(self, root, sink, target, via, guarded, steps, scopes, made):
        self.root = root            # the argparse attribute, as `args.out`
        self.sink = sink
        self.target = target        # the target as written, at the write or at the call into it
        self.via = via              # the chain out to the command line, innermost first
        self.guarded = guarded
        self.steps = steps          # target relative to the root: [], ["child"], ...
        self.scopes = scopes        # [(function, line)]: the write, then each call out to it
        self.made = made            # [(mkdir node, steps, parents=True?, scopes)] on the same root
        self.verdict = ""
        self.need = []
        self.earlier = []

    @property
    def line(self) -> int:
        return self.scopes[0][1]

    def key(self):
        return self.line, self.root, self.sink, self.via

    def row(self) -> str:
        guard = "guarded" if self.guarded else "unguarded"
        via = f" (via {self.via})" if self.via else ""
        where = "".join(f" L{mk.lineno}" for mk, _, _, _ in self.earlier[:2])
        return (f":{self.line:<5d} {self.sink:<15s} {self.target[:36]:<36s}{via:<58s}"
                f" parent={self.verdict:<9s}{where:<9s} {guard}")


class Scan:
    """One file: its writes, its mkdirs, its local aliases and the names its
    argparse namespace goes by."""

    def __init__(self, rel: str, source: str):
        self.rel = rel
        self.source = source
        self.tree = ast.parse(source, filename=rel)
        self.ns = set()             # names the argparse namespace goes by
        self.aliases = {}           # (function, local name) -> [expressions assigned]
        self.parent = {}
        self.mkdir = []             # (function, node, receiver, parents=True?)
        self.writes = []            # (function, node, sink, target)
        self.calls = {}             # callee name -> [Call], for `name()` and `self.name()`
        self.candidates = []
        self.unresolved = []        # (function, node): a parameter no caller in this file supplies

    def enclosing(self, node):
        """The function `node` sits in, or None at module level.  A method
        counts as its function."""
        node = self.parent.get(node)
        while node is not None and not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            node = self.parent.get(node)
        return node

    @staticmethod
    def params_of(fn) -> frozenset:
        if fn is None:
            return frozenset()
        return frozenset(a.arg for a in fn.args.posonlyargs + fn.args.args + fn.args.kwonlyargs)

    @staticmethod
    def named(fn) -> str:
        return "<module>" if fn is None else f"{fn.name}()"

    def lookup(self, fn, name):
        """What `name` was assigned in `fn`, then in the functions it is
        nested in, then at module level."""
        while fn is not None:
            if (fn, name) in self.aliases:
                return self.aliases[(fn, name)]
            fn = self.enclosing(fn)
        return self.aliases.get((None, name), [])

    def alias(self, targets, value):
        fn = self.enclosing(value)
        for target in targets:
            if isinstance(target, ast.Name):
                self.aliases.setdefault((fn, target.id), []).append(value)

    @staticmethod
    def mode(node, method: bool) -> str:
        """The `open` mode as a literal, else 'r' (a variable mode is not
        read).  `open(p, m)` takes it second; `p.open(m)` takes it first."""
        index = 0 if method else 1
        if len(node.args) > index and isinstance(node.args[index], ast.Constant):
            return str(node.args[index].value)
        for kw in node.keywords:
            if kw.arg == "mode" and isinstance(kw.value, ast.Constant):
                return str(kw.value.value)
        return "r"

    def call(self, node):
        fn = self.enclosing(node)
        func = node.func
        name = dotted(func)
        method = isinstance(func, ast.Attribute)
        mode = self.mode(node, method)
        if method:
            if func.attr == "mkdir":
                deep = any(k.arg == "parents" and isinstance(k.value, ast.Constant)
                           and k.value.value is True for k in node.keywords)
                self.mkdir.append((fn, node, func.value, deep))
            elif func.attr in METHOD_SINKS:
                self.writes.append((fn, node, func.attr, func.value))
            elif func.attr == "open" and mode in WRITE_MODES:
                self.writes.append((fn, node, "open", func.value))
            if isinstance(func.value, ast.Name) and func.value.id == "self":
                self.calls.setdefault(func.attr, []).append(node)
        elif isinstance(func, ast.Name):
            self.calls.setdefault(func.id, []).append(node)
            if func.id == "open" and node.args and mode in WRITE_MODES:
                self.writes.append((fn, node, "open", node.args[0]))
        if name in MAKE_DIRS and node.args:
            self.mkdir.append((fn, node, node.args[0], name == "os.makedirs"))
        if name in FUNCTION_SINKS and len(node.args) > FUNCTION_SINKS[name]:
            self.writes.append((fn, node, name, node.args[FUNCTION_SINKS[name]]))

    def collect(self):
        for node in ast.walk(self.tree):
            for child in ast.iter_child_nodes(node):
                self.parent[child] = node
        for node in ast.walk(self.tree):
            if isinstance(node, ast.Assign):
                if (isinstance(node.value, ast.Call)
                        and dotted(node.value.func).endswith(".parse_args")):
                    self.ns.update(t.id for t in node.targets if isinstance(t, ast.Name))
                self.alias(node.targets, node.value)
            elif isinstance(node, ast.AnnAssign) and node.value is not None:
                self.alias([node.target], node.value)
            elif isinstance(node, ast.NamedExpr):
                self.alias([node.target], node.value)
            elif isinstance(node, ast.For):
                self.alias([node.target], node.iter)
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                self.ns.update(a.arg for a in node.args.posonlyargs + node.args.args
                               + node.args.kwonlyargs if a.arg in NS_PARAMS)
            elif isinstance(node, ast.Call):
                self.call(node)

    @staticmethod
    def is_root(e, ns, params) -> bool:
        """An attribute of the namespace (`args.out`), or a bare parameter."""
        attribute = isinstance(e, ast.Attribute) and isinstance(e.value, ast.Name)
        return (attribute and e.value.id in ns) or (isinstance(e, ast.Name) and e.id in params)

    @staticmethod
    def root_name(e) -> str:
        if isinstance(e, ast.Attribute):
            return f"{e.value.id}.{e.attr}"
        return e.id

    def roots(self, e, fn, params=frozenset()):
        """Every namespace attribute, and every parameter in `params`, that the
        path `e` is built on, aliases expanded.  Only the base of a join
        counts: `dst / f.name` is built on `dst`, whatever `f` was read from."""
        found = set()

        def walk(e, depth, component):
            if not isinstance(e, ast.AST) or depth > 24:
                return
            if self.is_root(e, self.ns, params):
                if not component:
                    found.add(self.root_name(e))
            elif isinstance(e, ast.Name) and e.id not in params:
                for value in self.lookup(fn, e.id):
                    walk(value, depth + 1, component)
            elif component:
                return
            elif isinstance(e, ast.BinOp) and isinstance(e.op, ast.Div):
                walk(e.left, depth + 1, False)
                walk(e.right, depth + 1, True)
            elif isinstance(e, ast.Call) and e.args and self.join_parts(e) is not None:
                base, rest = self.join_parts(e)
                walk(base, depth + 1, False)
                for node in rest:
                    walk(node, depth + 1, True)
            elif isinstance(e, ast.Attribute):
                walk(e.value, depth + 1, e.attr != "parent")   # `x.name` is a string read off x
            elif isinstance(e, ast.Call) and isinstance(e.func, ast.Attribute) \
                    and self.call_name(e) in SAME_PARENT | SAME_PATH | {"dirname"}:
                # `x.resolve()` is x, and `os.path.abspath(x)` is x: a method
                # keeps its path before the dot, an os.path function after it
                base = e.func.value if self.call_name(e) in SAME_PARENT else (
                    e.args[0] if e.args else None)
                if base is not None:
                    walk(base, depth + 1, component)
            elif isinstance(e, ast.JoinedStr):
                for value in e.values:
                    if isinstance(value, ast.FormattedValue):
                        walk(value.value, depth + 1, bool(found) or component)
            else:
                for child in ast.iter_child_nodes(e):
                    walk(child, depth + 1, component)

        walk(e, 0, False)
        return found

    @staticmethod
    def call_name(e) -> str:
        """`resolve` for `a.outdir.resolve()`, the last name of the call."""
        return dotted(e.func).split(".")[-1]

    @staticmethod
    def join_parts(e) -> tuple | None:
        """`(base, components)` for `os.path.join(base, ...)` and
        `base.joinpath(...)`, else None."""
        name = dotted(e.func).split(".")[-1]
        if name == "join" and dotted(e.func) == "os.path.join":
            return e.args[0], e.args[1:]
        if name == "joinpath":
            return e.func.value, e.args
        return None

    def steps(self, e, root, fn, params=frozenset(), depth=0):
        """Where `e` sits relative to `root`: [] for the root itself,
        ["child"] for `root / "x"`, ["parent"] for `root.parent`.  None when
        the expression is none of those."""
        if not isinstance(e, ast.AST) or depth > 24:
            return None
        if self.is_root(e, self.ns, params):
            return [] if self.root_name(e) == root else None
        if isinstance(e, ast.Name):
            for value in self.lookup(fn, e.id):
                found = self.steps(value, root, fn, params, depth + 1)
                if found is not None:
                    return found
            return None
        if isinstance(e, ast.Call):
            name = dotted(e.func).split(".")[-1]
            if name in PATH_CALLS and e.args:
                return self.steps(e.args[0], root, fn, params, depth + 1)
            if name in SAME_PARENT and isinstance(e.func, ast.Attribute):
                return self.steps(e.func.value, root, fn, params, depth + 1)
            if name in SAME_PATH and e.args:                 # os.path.abspath(a.json) is a.json
                return self.steps(e.args[0], root, fn, params, depth + 1)
            if name == "dirname" and e.args:                 # os.path.dirname(x): x's parent
                inner = self.steps(e.args[0], root, fn, params, depth + 1)
                return None if inner is None else inner + ["parent"]
            parts = self.join_parts(e) if e.args else None
            if parts is not None:
                return self.joined(*parts, root, fn, params, depth)
            return None
        if isinstance(e, ast.BinOp) and isinstance(e.op, ast.Div):
            return self.joined(e.left, [e.right], root, fn, params, depth)
        if isinstance(e, ast.Attribute) and e.attr == "parent":
            inner = self.steps(e.value, root, fn, params, depth + 1)
            return None if inner is None else inner + ["parent"]
        if isinstance(e, ast.JoinedStr):
            return self.interpolated(e, root, fn, params, depth)
        choices = e.values if isinstance(e, ast.BoolOp) else (
            [e.body, e.orelse] if isinstance(e, ast.IfExp) else [])
        for choice in choices:
            found = self.steps(choice, root, fn, params, depth + 1)
            if found is not None:
                return found
        return None

    def joined(self, base, rest, root, fn, params, depth):
        """`base` joined with the components in `rest`."""
        found = self.steps(base, root, fn, params, depth + 1)
        if found is None or any(isinstance(p, ast.Starred) for p in rest):
            return None
        for node in rest:
            step = part(node)
            if step is None:
                return None
            if step:
                found = found + [step]
        return found

    def interpolated(self, e, root, fn, params, depth):
        """`f"{out}/synth.ys"`: the first interpolated value carries the
        root, each name after a slash is a child."""
        found = None
        for value in e.values:
            if isinstance(value, ast.FormattedValue):
                if found is None:
                    found = self.steps(value.value, root, fn, params, depth + 1)
                    if found is None:
                        return None
                else:
                    found = found + ["child"]
            elif value.value:
                if found is None or not value.value.startswith("/"):
                    return None
                found = found + [part(ast.Constant(s)) for s in value.value[1:].split("/")]
        if found is None or None in found or "" in found:
            return None
        return found

    def guarded(self, *nodes) -> bool:
        """Is any of `nodes` inside the `try` block of a handler that takes
        `OSError`, `Exception` or everything?"""
        for node in nodes:
            child, up = node, self.parent.get(node)
            while up is not None:
                if isinstance(up, ast.Try) and any(child is statement for statement in up.body):
                    for handler in up.handlers:
                        name = dotted(handler.type).split(".")[-1] if handler.type else ""
                        if not name or name in {"OSError", "IOError", "Exception", "BaseException"}:
                            return True
                child, up = up, self.parent.get(up)
        return False

    def argument(self, call, fn, name):
        """The expression `call` passes for `fn`'s parameter `name`, or None
        when it cannot tell (`*args`, or not passed positionally)."""
        positional = [a.arg for a in fn.args.posonlyargs + fn.args.args]
        if name in positional:
            index = positional.index(name)
            if isinstance(call.func, ast.Attribute) and positional[:1] == ["self"]:
                index += 1
            args = call.args[:index + 1]
            if len(args) == index + 1 and not any(isinstance(a, ast.Starred) for a in args):
                return args[index]
            return None
        for kw in call.keywords:
            if kw.arg == name:
                return kw.value
        return None

    def text(self, node) -> str:
        return " ".join(ast.get_source_segment(self.source, node, padded=True).split())

    def resolve(self, fn, e):
        """Every way the path `e`, in `fn`, comes from the command line:
        (root, steps of `e` relative to it -- None when the shape is not
        followed -- [(function, call)] leading out to it, innermost first)."""
        mine = self.params_of(fn)
        roots = self.roots(e, fn, mine)
        for root in sorted(roots - mine):
            yield root, self.steps(e, root, fn, mine), []
        for param in sorted(roots & mine):
            inner = self.steps(e, param, fn, {param})
            for root, steps, calls in self.supplied(fn, param, e, [], 1):
                yield root, None if steps is None or inner is None else steps + inner, calls

    def supplied(self, fn, param, e, calls, hops):
        """Every way `fn`'s parameter `param`, which `e` is built on, is
        supplied from the command line: (root, steps of the parameter
        relative to it, calls)."""
        found = [(c, self.argument(c, fn, param)) for c in self.calls.get(fn.name, [])]
        found = [(c, arg) for c, arg in found if arg is not None]
        if not found:
            self.unresolved.append((fn, e))
            return
        for call, arg in found:
            caller = self.enclosing(call)
            theirs = self.params_of(caller)
            arg_roots = self.roots(arg, caller, theirs)
            reached = calls + [(caller, call)]
            for root in sorted(arg_roots - theirs):
                yield root, self.steps(arg, root, caller, theirs), reached
            outer = sorted(arg_roots & theirs)
            if hops >= MAX_HOPS:
                for _ in outer:
                    yield None, None, reached          # deeper than this follows: UNKNOWN
                continue
            for name in outer:
                given = self.steps(arg, name, caller, theirs)
                for root, steps, deeper in self.supplied(caller, name, arg, reached, hops + 1):
                    total = None if steps is None or given is None else steps + given
                    yield root, total, deeper

    def find(self):
        """Every write to a path the command line named, judged."""
        self.unresolved = []
        made = []                                   # on one root: steps, deep, scopes, node
        for owner, mk, recv, deep in self.mkdir:
            for root, steps, calls in self.resolve(owner, recv):
                made.append((root, steps, deep, [(owner, mk.lineno)] + scopes(calls), mk))
        out = []
        for fn, node, sink, target in self.writes:
            for root, steps, calls in self.resolve(fn, target):
                chain = [(fn, node.lineno)] + scopes(calls)
                via = " <- ".join([f"{self.named(fn)} L{node.lineno}"]
                                  + [f"{self.named(c)} L{call.lineno}" for c, call in calls])
                bearing = [(mk, s, d, sc) for r, s, d, sc, mk in made if r == root]
                out.append(Candidate(root, sink, self.text(target), "" if not calls else via,
                                     self.guarded(node, *[call for _, call in calls]),
                                     steps, chain, bearing))
        unique = {c.key(): c for c in out}
        seen = {(fn, node.lineno): (fn, node) for fn, node in self.unresolved}.values()
        self.unresolved = sorted(seen, key=lambda item: item[1].lineno)
        return [judge(c) for c in sorted(unique.values(), key=Candidate.key)]


def scopes(calls) -> list:
    return [(caller, call.lineno) for caller, call in calls]


def judge(candidate: Candidate) -> Candidate:
    """Set `verdict` on `candidate` (see the module docstring) and return it."""
    if candidate.steps is None:
        candidate.verdict = "UNKNOWN"
        return candidate
    candidate.need = candidate.steps[:-1] if candidate.steps else ["parent"]
    matching = [m for m in candidate.made if m[1] == candidate.need]
    candidate.earlier = [m for m in matching if precedes(m[3], candidate.scopes)]
    candidate.verdict = "MADE" if candidate.earlier else "MADE?" if matching else "NOT-MADE"
    return candidate


def scan(source: str, rel: str = "tools/sample.py") -> Scan:
    """One source text, collected: `.candidates` judged, `.unresolved` listed."""
    scan_ = Scan(rel, source)
    scan_.collect()
    scan_.candidates = scan_.find()
    return scan_


def self_check():
    """The detectors must see the defect and see its repair (a precondition)."""
    verdicts = [(c.verdict, c.guarded) for c in scan(DEFECT).candidates]
    if verdicts != [("NOT-MADE", False)]:
        raise Refused(f"the #2040 shape in the five-line sample reads {verdicts}, "
                      f"not [('NOT-MADE', False)]")
    for sample in (REPAIRED, REPAIRED_IN_HELPER):
        verdicts = [c.verdict for c in scan(sample).candidates]
        if verdicts != ["MADE"]:
            raise Refused(f"the repair of the #2040 shape reads {verdicts}, not ['MADE']")


def tracked_files(root: pathlib.Path) -> list[str]:
    try:
        run = subprocess.run(["git", "-C", str(root), "ls-files", "--", "*.py"],
                             capture_output=True, text=True, timeout=120)
    except (OSError, subprocess.SubprocessError) as exc:
        raise Refused(f"git ls-files did not run: {exc}") from exc
    if run.returncode != 0:
        raise Refused(f"git ls-files exited {run.returncode}: {run.stderr.strip()[:200]}")
    files = sorted(f for f in run.stdout.split("\n") if f and not TEST_NAME.search(f))
    if len(files) < MIN_TRACKED_FILES:
        raise Refused(f"git ls-files lists {len(files)} non-test Python files, fewer than the "
                      f"{MIN_TRACKED_FILES} this repository has: a partial tree under-reports")
    return files


def census(root: pathlib.Path = ROOT):
    """(files scanned, [(relative path, Candidate)], [(relative path, function, line)])."""
    self_check()
    files = tracked_files(root)
    found, unresolved = [], []
    for rel in files:
        try:
            scanned = scan((root / rel).read_text(), rel)
        except (OSError, SyntaxError, ValueError) as exc:
            raise Refused(f"{rel} could not be read or parsed: {exc}") from exc
        found.extend((rel, c) for c in scanned.candidates)
        unresolved.extend((rel, fn, node) for fn, node in scanned.unresolved)
    return files, found, unresolved


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="count writes to command-line output paths, "
                                             "and whether each one's parent is made first")
    ap.add_argument("--only", default="", help="comma-separated verdicts to print (MADE, MADE?, "
                                               "NOT-MADE, UNKNOWN); default prints them all")
    ap.add_argument("--summary", action="store_true", help="print the counts only")
    a = ap.parse_args(argv)
    only = {v.strip() for v in a.only.split(",") if v.strip()}
    if only - set(VERDICTS):
        print(f"output_path_census: REFUSED -- unknown verdict in --only: "
              f"{sorted(only - set(VERDICTS))}; choose from {', '.join(VERDICTS)}")
        return 2
    try:
        files, found, unresolved = census(ROOT)
    except Refused as exc:
        print(f"output_path_census: REFUSED -- {exc}")
        return 2
    if not a.summary:
        current = None
        for rel, c in found:
            if only and c.verdict not in only:
                continue
            if rel != current:
                current = rel
                print(rel)
            print("  " + c.row())
    by = {v: sum(1 for _, c in found if c.verdict == v) for v in VERDICTS}
    unread = [c for _, c in found if c.verdict in {"NOT-MADE", "UNKNOWN"}]
    print(f"output_path_census: {len(files)} tracked non-test Python files, "
          f"{len(found)} writes to a command-line path in "
          f"{len({rel for rel, _ in found})} files")
    for v in VERDICTS:
        print(f"  parent {v:<9s} {by[v]:4d}")
    print(f"  of NOT-MADE and UNKNOWN: {sum(1 for c in unread if c.guarded)} guarded, "
          f"{sum(1 for c in unread if not c.guarded)} unguarded")
    print(f"  unresolved, neither candidates nor clearances: {len(unresolved)} writes or mkdirs "
          f"through a parameter no caller in its own file supplies")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
