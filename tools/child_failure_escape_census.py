#!/usr/bin/env python3
"""Census of the paths by which a child-process failure -- `RuntimeError`,
`subprocess.CalledProcessError`, `subprocess.TimeoutExpired` -- reaches a
module's entry point with no handler for its class (#1690).

    python tools/child_failure_escape_census.py                  # one row per path
    python tools/child_failure_escape_census.py --json           # machine-readable
    python tools/child_failure_escape_census.py --check \\
        --known fpga/verify_sd_dac.py=RuntimeError \\
        --known fpga/verify_sd_dac.py=subprocess.TimeoutExpired
                                                                 # exit 1 on a path no --known
                                                                 # names, or on a stale --known

THE DEFECT CLASS. `tools/exit_contract.py`: exit 1 is FAIL, "a result"; exit 2
is REFUSED, "nothing was measured". A simulation that hangs, a `git show` of a
commit the clone does not hold, a loader that never finishes: each is a
precondition that was not met. Where the call sits under no handler for the
class the child raises, the tool prints a traceback and exits 1, and a caller
that reads the status sees a failure where the truth is that nothing was
measured. #1662, #1631, #1676 and #1690 each repaired one tool after reading
it by hand; #1690's search found its four rows with a throwaway walk that was
wrong twice before it was right, and asked for the third version to be
committed so the next search is a command.

WHAT A ROW IS. For every non-test module under the root: a SOURCE is a `raise`
of a followed class (or of a subclass of one, `class Refused(refusal.Refused,
RuntimeError)` included), or a `subprocess` call that can raise one --
`check_output` and `check_call` always, `run` and `call` under a `check` that
is not literally false, any of them under a `timeout` that is not literally
None, and `.wait(timeout=)` / `.communicate(timeout=)` on a name the same
function bound from `subprocess.Popen`. A source or a call to a function that
lets one through is COVERED when a `try` whose body contains it has a handler
for that class -- the class itself, one of its bases (`Exception`, bare,
`subprocess.SubprocessError`), resolved through imports, module-level class
definitions, `tools/refusal.py` and module-level names bound to a class or a
tuple of classes (`BENCH_FAILURES = (...)`, `Refused = pc.Refused`). What is
not covered escapes the function; its callers are then read the same way, to
a fixed point. An ENTRY POINT is a module-level `main()` or the module's own
top-level code (the `if __name__ == "__main__":` block included). Each row
names the entry, the class and every hop as file:line.

WHAT IT CANNOT TELL YOU, and why a row is a path to read, not a verdict:

  * a class method is not resolved: `obj.method()` is not followed, so
    `midi_session.MidiSession.feed` (`raise RuntimeError`, :711) has no row
    however it is called;
  * a raised class the walk cannot place is not followed -- `raise err` for an
    instance, a class built at run time, a class imported from outside the
    root -- and a handler type it cannot read is credited with catching
    NOTHING, so a covered call may be listed; the notice says which handler;
  * a function defined inside another is read where it is DEFINED: the body of
    `pool.map(lambda j: bench(j), jobs)` is read at the `map` call, which is
    where its exception surfaces, and a callback stored and called later from
    another frame is read at its definition instead;
  * a name bound to an analysed function counts as a call wherever it is
    evaluated as a value -- an argument, a list member -- since
    `pool.map(bench, jobs)` has no Call node at all. A module-level alias is
    the other way round: `_need = r1r._need` binds a name and calls nothing,
    and the calls written through the name are read where they are;
  * only the root is scanned as a callee. `fpga/` calls into `tools/` and
    `model/`; what those raise is invisible here;
  * a `main()` that another module calls under a handler is still an entry
    point, because it is also the command's;
  * reachability is not modelled: a path may need an input no caller sends.
    Deciding that is tracing the caller with a known-answer input.

NOT A GATE on its own. Like `tools/refusal_sibling_scan.py` it reports and
does not pin totals; `--check` turns red only on a row no `--known` names, so
what is open is said by the caller, and a `--known` whose row has gone turns
it red too -- a record must occur exactly as recorded. It prints how far
behind origin/main the tree it read is, because a census quoted from a stale
tree is a claim about a tree the reader cannot see.

PRECONDITIONS, ASSERTED AT THE POINT OF USE AND REFUSED (exit 2) WHEN UNMET:

  * the root holds at least `--min-modules` non-test modules (20 by default):
    a walk over a partial tree would read exactly like one over the whole;
  * every module parses, and the refusal base defines `class Refused`;
  * no module uses `contextlib.suppress` or assigns `sys.excepthook` /
    `threading.excepthook`: each swallows or reroutes an exception where no
    `try` statement shows it, so the census would be incomplete without
    saying so. #1690 found none by grep; this asserts it on every run.

WRONG BEFORE RIGHT, because the rate is how a reader calibrates a row. The
throwaway this replaces reported 271 unguarded sites (callees resolved by bare
name, so `subprocess.run` collided with two functions named `run`), then 18
modules (a source already inside its own `try/except` still counted as a
leak, which is how the three `build_arty*.py` tools appeared although each
refuses its Vivado timeout explicitly), then 5. That third answer was itself
smaller than the tree: it matched class NAMES, so a `Refused` that is a
`RuntimeError` (`class Refused(refusal.Refused, RuntimeError)`) was invisible
to it, and on the same tree this walk reads 16 paths where it read 5 -- each
one checked against its file before it was believed. Most are refusals of
the `fpga/release` checkers leaving a CLI through a final `check()` no handler
encloses, or through a handler that names a sibling module's `Refused`: the
family #1576, #1616 and #1681 trace. This version was wrong three times on the
way, each time in its own direction: it credited a `try`'s handlers for its `else` clause,
which Python does not -- an exception raised in `else`, in a handler body or
in `finally` passes those handlers by -- found by running the four-line case
before believing it; it read `sha, _rel, _need = r1r.sha, r1r._rel,
r1r._need` as a call of `r1r._need` and listed `r2_release.py` for it, found
by reading that row's file; and a patch that was meant to add a rule silently
stopped every site from being collected, found by the fixture suite going
red. All three are fixtures now
(`test_an_exception_outside_the_try_body_is_not_covered_by_its_handlers`,
`test_a_module_level_alias_is_read_where_it_is_called_not_where_it_is_bound`).
"""
from __future__ import annotations

import argparse
import ast
import builtins
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "tools") not in sys.path:
    sys.path.insert(0, str(ROOT / "tools"))
import refusal  # noqa: E402

FOLLOWED = ("RuntimeError", "subprocess.CalledProcessError", "subprocess.TimeoutExpired")
#: the known hierarchy of the three, so a handler written on a base is credited
BASES = {"subprocess.CalledProcessError": ("subprocess.SubprocessError",),
         "subprocess.TimeoutExpired": ("subprocess.SubprocessError",),
         "subprocess.SubprocessError": ("Exception",),
         "RuntimeError": ("Exception",),
         "Exception": ("BaseException",),
         "BaseException": ()}
#: subprocess function -> how it raises CalledProcessError
CHILD_CALLS = {"run": "check", "call": "check", "check_call": "always", "check_output": "always"}
POPEN_WAITS = ("wait", "communicate")
FUNC = (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)
TRY = (ast.Try, ast.TryStar)
MODULE_ENTRY = "<module>"
REFUSAL_STEM = "refusal"
DEFAULT_ROOT = ROOT / "fpga"
DEFAULT_REFUSAL = ROOT / "tools/refusal.py"
MIN_MODULES = 20


class Refused(refusal.Refused, Exception):
    """A precondition of the census is not met; an answer would look exactly
    like a census over a whole, readable tree."""


def _is_test(path: Path) -> bool:
    return path.name.startswith("test_") or path.name == "conftest.py"


def _bind(node):
    """Imports bind names: `import a.b as c` binds c to a.b, `import a.b`
    binds a to a, `from m import n as p` binds p to m.n."""
    if isinstance(node, ast.Import):
        return {(name.asname or name.name.split(".")[0]):
                ("module", name.name if name.asname else name.name.split(".")[0])
                for name in node.names}
    if isinstance(node, ast.ImportFrom) and node.module:
        return {(name.asname or name.name): ("name", f"{node.module}.{name.name}")
                for name in node.names if name.name != "*"}
    return {}


def _literal(node, value):
    return isinstance(node, ast.Constant) and node.value is value


def _defaults(node):
    """What evaluates where a def is defined: its decorators and defaults."""
    if isinstance(node, ast.ClassDef):
        return list(node.decorator_list)
    if isinstance(node, ast.Lambda):
        return []
    return [*node.decorator_list, *node.args.defaults, *(k for k in node.args.kw_defaults if k)]


def _module_scope(tree):
    """The module's own statements, not descending into a top-level def or
    class: their bodies run when called, not at import."""
    stack = list(tree.body)
    while stack:
        node = stack.pop()
        yield node
        if isinstance(node, FUNC + (ast.ClassDef,)):
            continue
        stack.extend(ast.iter_child_nodes(node))


class _Mod:
    def __init__(self, path: Path, rel: str):
        self.path, self.rel, self.stem = path, rel, path.stem
        try:
            self.tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except SyntaxError as exc:
            raise Refused(f"{rel} does not parse ({exc.msg} at line {exc.lineno}): a walk "
                          f"over a tree it cannot read would read exactly like one over "
                          f"the whole") from exc
        self.parent = {child: node for node in ast.walk(self.tree)
                       for child in ast.iter_child_nodes(node)}
        self.imports, self.aliases, self.classes, self.functions = {}, {}, {}, {}
        for node in _module_scope(self.tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                self.imports.update(_bind(node))
            elif isinstance(node, ast.ClassDef):
                self.classes[node.name] = node.bases
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                self.functions[node.name] = node
            elif isinstance(node, ast.Assign) and len(node.targets) == 1:
                target, value = node.targets[0], node.value
                pairs = ([(target.id, value)] if isinstance(target, ast.Name) else
                         [(t.id, v) for t, v in zip(target.elts, value.elts)]
                         if isinstance(target, ast.Tuple) and isinstance(value, ast.Tuple)
                         and len(target.elts) == len(value.elts)
                         and all(isinstance(t, ast.Name) for t in target.elts) else [])
                for name, bound in pairs:
                    self.aliases[name] = bound

    def dotted(self, expr, bindings, _seen=()):
        """`subprocess.run`, `sp.run`, `uh.pkt_event`, `run_bench` -> the
        dotted name each stands for under `bindings`, or None for an
        expression that is not a name. A module-level alias to a name
        (`_need = r1r._need`) stands for what it is bound to."""
        parts = []
        while isinstance(expr, ast.Attribute):
            parts.append(expr.attr)
            expr = expr.value
        if not isinstance(expr, ast.Name):
            return None
        tail = ".".join(reversed(parts))
        kind, name = bindings.get(expr.id, (None, None))
        if kind is None and (expr.id in self.functions or expr.id in self.classes):
            kind, name = "name", f"{self.stem}.{expr.id}"
        if kind is None and expr.id in self.aliases and expr.id not in _seen:
            bound = self.dotted(self.aliases[expr.id], bindings, (*_seen, expr.id))
            if bound:
                return bound + (f".{tail}" if tail else "")
        if kind is None:
            return expr.id + (f".{tail}" if tail else "")
        return name + (f".{tail}" if tail else "")


class _Site:
    __slots__ = ("callee", "keys", "kind", "node")

    def __init__(self, node, kind, keys, callee=None):
        self.node, self.kind, self.keys, self.callee = node, kind, keys or set(), callee


class _Unit:
    __slots__ = ("mod", "name", "node", "popen", "scope_imports", "sites", "uid")

    def __init__(self, mod, name, node):
        self.mod, self.name, self.uid, self.node = mod, name, (mod.stem, name), node
        self.sites, self.scope_imports, self.popen = [], {}, set()


def _children(node, scope, visit):
    """Descend. A nested def's decorators and defaults run where it is defined;
    its body is read as part of the function that contains it."""
    for child in ast.iter_child_nodes(node):
        if isinstance(child, FUNC):
            for edge in _defaults(child):
                visit(edge, scope)
            if isinstance(child, ast.Lambda):
                visit(child.body, child)
            else:
                for statement in child.body:
                    visit(statement, child)
        else:
            visit(child, scope)


class _Walk:
    def __init__(self, root: Path, refusal_path: Path, min_modules: int):
        if not root.is_dir():
            raise Refused(f"{root} is not a directory")
        paths = sorted(p for p in root.rglob("*.py") if not _is_test(p))
        if len(paths) < min_modules:
            raise Refused(f"{len(paths)} non-test modules under {root}, fewer than the "
                          f"{min_modules} this census needs: a walk over a partial tree would read "
                          f"exactly like one over the whole")
        if not refusal_path.is_file():
            raise Refused(f"no refusal base at {refusal_path}: every module's Refused is a "
                          f"subclass of its Refused, so the classes cannot be placed without it")
        self.root, self.notices, self.modules = root, [], len(paths)
        self.by_stem = {REFUSAL_STEM: _Mod(refusal_path, str(refusal_path))}
        if "Refused" not in self.by_stem[REFUSAL_STEM].classes:
            raise Refused(f"{refusal_path} does not define class Refused; it is the base of every "
                          f"module's Refused and this census reads it")
        self.mods = [_Mod(p, p.relative_to(root.parent).as_posix()) for p in paths]
        self.by_stem.update({mod.stem: mod for mod in self.mods})
        shared = {m.stem for m in self.mods if sum(x.stem == m.stem for x in self.mods) > 1}
        for stem in sorted(shared - {REFUSAL_STEM}):
            del self.by_stem[stem]
            self.notices.append(f"two modules under {root} share the stem `{stem}`: calls into "
                                f"either are not resolved")
        self._refuse_unseen()
        self.units = []
        for mod in self.mods:
            for name, node in mod.functions.items():
                unit = _Unit(mod, name, node)
                self._collect(unit, _defaults(node), list(node.body))
                self.units.append(unit)
            unit = _Unit(mod, MODULE_ENTRY, mod.tree)
            roots = []
            for node in _module_scope(mod.tree):
                roots.extend(_defaults(node) if isinstance(node, FUNC + (ast.ClassDef,))
                             else [node])
            self._collect(unit, [], roots)
            self.units.append(unit)
        self.by_uid = {unit.uid: unit for unit in self.units}
        self.entries = sum(unit.name == MODULE_ENTRY or unit.name == "main" for unit in self.units)

    # -- what the tree binds ---------------------------------------------------
    def _refuse_unseen(self):
        for mod in [self.by_stem[REFUSAL_STEM], *self.mods]:
            for node in ast.walk(mod.tree):
                unseen = None
                if isinstance(node, (ast.With, ast.AsyncWith)):
                    for item in node.items:
                        call = item.context_expr
                        if isinstance(call, ast.Call) and (
                                mod.dotted(call.func, mod.imports) or ""
                        ).endswith("suppress"):
                            unseen = ast.unparse(call)
                elif isinstance(node, ast.Assign) and any(
                        isinstance(t, ast.Attribute) and t.attr == "excepthook"
                        for t in node.targets):
                    unseen = ast.unparse(node).split(" =")[0]
                elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Attribute) \
                        and node.target.attr == "excepthook":
                    unseen = ast.unparse(node.target)
                if unseen:
                    raise Refused(f"{mod.rel}:{node.lineno} uses `{unseen}`, which this walk "
                                  f"cannot see: a suppression or an excepthook swallows or "
                                  f"reroutes an exception where no try statement shows it, so "
                                  f"the census would be incomplete")

    def _bindings(self, unit, at):
        """The names a node sees: the module's imports, then those of each
        function it is nested in, innermost last (#1573: an import inside a
        def binds there and only there)."""
        found = dict(unit.mod.imports)
        chain, cur = [unit.node], at
        while cur is not None and cur is not unit.node:
            if isinstance(cur, FUNC):
                chain.append(cur)
            cur = unit.mod.parent.get(cur)
        for scope in reversed(chain):
            found.update(unit.scope_imports.get(id(scope), {}))
        return found

    def _collect(self, unit, extra, roots):
        mod = unit.mod

        def visit(node, scope):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                unit.scope_imports.setdefault(id(scope), {}).update(_bind(node))
            if isinstance(node, ast.Raise):
                if node.exc is None:
                    unit.sites.append(_Site(node, "reraise", None))
                else:
                    keys, _ = self._class_keys(mod, node.exc.func if isinstance(node.exc, ast.Call)
                                               else node.exc, self._bindings(unit, node))
                    followed = {k for k in keys if self.followed(k)}
                    if followed:
                        unit.sites.append(_Site(node, "raise", followed))
            elif isinstance(node, ast.Call):
                bindings = self._bindings(unit, node)
                callee = mod.dotted(node.func, bindings)
                kw = {k.arg: k.value for k in node.keywords if k.arg}
                if callee in {f"subprocess.{n}" for n in CHILD_CALLS}:
                    keys = set()
                    if CHILD_CALLS[callee.rsplit(".", 1)[1]] == "always" \
                            or ("check" in kw and not _literal(kw["check"], False)):
                        keys.add("subprocess.CalledProcessError")
                    if "timeout" in kw and not _literal(kw["timeout"], None):
                        keys.add("subprocess.TimeoutExpired")
                    if keys:
                        unit.sites.append(_Site(node, "child", keys))
                elif callee and self.function(callee):
                    unit.sites.append(_Site(node, "call", None, self.function(callee)))
                elif isinstance(node.func, ast.Attribute) and node.func.attr in POPEN_WAITS \
                        and isinstance(node.func.value, ast.Name) \
                        and node.func.value.id in unit.popen \
                        and "timeout" in kw and not _literal(kw["timeout"], None):
                    unit.sites.append(_Site(node, "child", {"subprocess.TimeoutExpired"}))
            elif isinstance(node, ast.Assign) and len(node.targets) == 1 \
                    and isinstance(node.targets[0], ast.Name) and isinstance(node.value, ast.Call) \
                    and mod.dotted(node.value.func, self._bindings(unit, node)) \
                    == "subprocess.Popen":
                unit.popen.add(node.targets[0].id)
            elif isinstance(node, (ast.Name, ast.Attribute)):
                parent = mod.parent.get(node)
                part = (isinstance(parent, ast.Attribute) and parent.value is node) or \
                    (isinstance(parent, ast.Call) and parent.func is node)
                if not part and isinstance(node.ctx, ast.Load) and not self._binds_alias(mod, node):
                    target = mod.dotted(node, self._bindings(unit, node))
                    if target and self.function(target):
                        unit.sites.append(_Site(node, "reference", None, self.function(target)))
            _children(node, scope, visit)

        for node in [*extra, *roots]:
            visit(node, unit.node)

    @staticmethod
    def _binds_alias(mod, node):
        """`_need = r1r._need`, or `a, _need = x.a, x._need`, binds a name and
        calls nothing: the calls that go through the name are read where they
        are written. A name anywhere else in a statement -- an argument, a
        list -- is read as a call there."""
        value = node
        while isinstance(mod.parent.get(value), ast.Tuple):
            value = mod.parent.get(value)
        statement = mod.parent.get(value)
        return isinstance(statement, (ast.Assign, ast.AnnAssign)) and statement.value is value

    # -- classes ------------------------------------------------------------------
    def bases(self, key):
        if key in BASES:
            return BASES[key]
        mod, _, name = key.rpartition(".")
        if mod in self.by_stem and name in self.by_stem[mod].classes:
            module = self.by_stem[mod]
            return tuple(k for base in module.classes[name]
                         for k in self._class_keys(module, base, module.imports)[0])
        return ()

    def ancestors(self, key):
        """Every base of `key`, transitively, as far as the tree lets it be read."""
        seen, stack = set(), list(self.bases(key))
        while stack:
            k = stack.pop()
            if k in seen:
                continue
            seen.add(k)
            stack.extend(self.bases(k))
        return seen

    def followed(self, key):
        for k in (key, *self.ancestors(key)):
            if k in FOLLOWED:
                return k
        return None

    def _known_class(self, key):
        if key in BASES:
            return True
        builtin = getattr(builtins, key, None)
        if isinstance(builtin, type) and issubclass(builtin, BaseException):
            return True
        mod, _, name = key.rpartition(".")
        return mod in self.by_stem and name in self.by_stem[mod].classes

    def _class_keys(self, mod, expr, bindings):
        """(the classes an exception expression names, whether every part of
        it was read). A tuple with an unreadable member keeps its readable
        ones: they do catch what they name."""
        if isinstance(expr, ast.Tuple):
            found, read = set(), True
            for element in expr.elts:
                keys, ok = self._class_keys(mod, element, bindings)
                found |= keys
                read = read and ok
            return found, read
        if isinstance(expr, ast.Name) and expr.id in mod.aliases and expr.id not in bindings:
            return self._class_keys(mod, mod.aliases[expr.id], bindings)
        key = mod.dotted(expr, bindings)
        return ({key}, True) if key and self._known_class(key) else (set(), False)

    def function(self, key):
        mod, _, name = key.rpartition(".")
        if mod in self.by_stem and name in self.by_stem[mod].functions:
            return (mod, name)
        return None

    # -- coverage -----------------------------------------------------------------
    def _tries(self, unit, node):
        """Every try whose BODY holds `node`, innermost first. Not one whose
        handler, else or finally holds it: an exception raised there passes
        those handlers by."""
        mod, child, cur = unit.mod, node, unit.mod.parent.get(node)
        while cur is not None and cur is not unit.node:
            if isinstance(cur, TRY) and any(child is statement for statement in cur.body):
                yield cur
            child, cur = cur, mod.parent.get(cur)

    def _handler_keys(self, unit, handler):
        keys, read = self._class_keys(unit.mod, handler.type, self._bindings(unit, handler))
        if not read and (text := f"{unit.mod.rel}:{handler.lineno}: unresolved handler type "
                                   f"`{ast.unparse(handler.type)}`: outside what this walk can "
                                   f"read, "
                                   f"credited with catching nothing") not in self.notices:
            self.notices.append(text)
        return keys

    def _handler_catches(self, unit, handler, key):
        if handler.type is None:
            return True
        return any(h == key or h in self.ancestors(key) for h in self._handler_keys(unit, handler))

    def _caught(self, unit, node, key):
        return any(self._handler_catches(unit, handler, key)
                   for tri in self._tries(unit, node) for handler in tri.handlers)

    # -- the fixed point ------------------------------------------------------------
    def _keys_of(self, unit, site):
        if site.callee is not None:
            return set(self.escapes.get(site.callee, {}))
        if site.kind == "reraise":
            return self._reraised(unit, site)
        return site.keys

    def _reraised(self, unit, site):
        """A bare `raise` re-raises what the body of ITS OWN try raised and its
        handler caught: under `except ValueError` that is nothing followed."""
        handler = unit.mod.parent.get(site.node)
        while handler is not None and not isinstance(handler, ast.ExceptHandler):
            handler = unit.mod.parent.get(handler)
        if handler is None:
            return {"RuntimeError"}        # `raise` with no active exception
        tri = unit.mod.parent[handler]
        return {key for other in unit.sites if other.kind != "reraise"
                and tri in self._tries(unit, other.node)
                for key in self._keys_of(unit, other) if self._handler_catches(unit, handler, key)}

    def _propagate(self):
        self.escapes = {unit.uid: {} for unit in self.units}
        changed = True
        while changed:
            changed = False
            for unit in self.units:
                escapes = self.escapes[unit.uid]
                for site in unit.sites:
                    for key in self._keys_of(unit, site):
                        if key not in escapes and not self._caught(unit, site.node, key):
                            escapes[key] = site
                            changed = True

    # -- rows -------------------------------------------------------------------------
    def _hop(self, mod, site):
        node = site.node
        if site.kind == "child":
            kw = {k.arg: k.value for k in node.keywords if k.arg in ("check", "timeout")}
            shown = [f"{name}={ast.unparse(kw[name])}"
                     for name in ("check", "timeout") if name in kw]
            what = (f"{ast.unparse(node.func)}(..., {', '.join(shown)})" if shown
                    else f"{ast.unparse(node.func)}(...)")
            what += f"  [{', '.join(sorted(k.rsplit('.', 1)[1] for k in site.keys))}]"
        elif site.kind == "call":
            what = f"{ast.unparse(node)}  ->  {'.'.join(site.callee)}"
        elif site.kind == "reference":
            what = (f"{ast.unparse(node)}  (a reference, read as a call here)  ->  "
                    f"{'.'.join(site.callee)}")
        elif site.kind == "reraise":
            what = "raise  (re-raises what its own try caught)"
        else:
            what = " ".join(ast.unparse(node).split())[:100]
        return {"file": mod.rel, "line": node.lineno, "kind": site.kind, "what": what}

    def _path(self, unit, key, first):
        hops, seen, site = [self._hop(unit.mod, first)], {unit.uid}, first
        while site.callee is not None:
            unit = self.by_uid[site.callee]
            site = self.escapes[unit.uid].get(key)
            if site is None or unit.uid in seen:
                break
            seen.add(unit.uid)
            hops.append(self._hop(unit.mod, site))
        return hops

    def rows(self):
        rows = []
        for unit in self.units:
            if unit.name not in (MODULE_ENTRY, "main"):
                continue
            if unit.name == MODULE_ENTRY:
                own_main, found = (unit.mod.stem, "main"), {}
                for site in unit.sites:
                    if site.callee == own_main:
                        continue        # main() is itself an entry: reported there
                    for key in self._keys_of(unit, site):
                        if key not in found and not self._caught(unit, site.node, key):
                            found[key] = site
            else:
                found = self.escapes[unit.uid]
            for key, site in sorted(found.items()):
                rows.append({"module": unit.mod.rel, "entry": unit.name,
                             "class": self.followed(key), "raised": key,
                             "path": self._path(unit, key, site)})
        return sorted(rows, key=lambda r: (r["module"], r["entry"], r["class"], r["raised"]))


def census(root=DEFAULT_ROOT, *, refusal_path=DEFAULT_REFUSAL, min_modules=MIN_MODULES) -> dict:
    limit = sys.getrecursionlimit()
    sys.setrecursionlimit(max(limit, 20000))
    try:
        walk = _Walk(Path(root), Path(refusal_path), min_modules)
        walk._propagate()
    finally:
        sys.setrecursionlimit(limit)
    return {"root": str(walk.root), "modules": walk.modules, "entries": walk.entries,
            "followed": list(FOLLOWED), "rows": walk.rows(), "notices": list(walk.notices)}


def check(res: dict, known: list) -> tuple:
    """(ok, lines): green only when every row is named by a `PATH=CLASS` and
    every name still matches a row. CLASS is the followed class or the raised
    one."""
    def matches(spec, row):
        path, _, cls = spec.rpartition("=")
        return path == row["module"] and cls in (row["class"], row["raised"])

    lines = [f"not named: {r['module']}  {r['entry']}  {r['class']}  (raised {r['raised']})"
             for r in res["rows"] if not any(matches(spec, r) for spec in known)]
    lines += [f"stale: --known {spec} matches no row; a record must occur exactly as recorded"
              for spec in known if not any(matches(spec, r) for r in res["rows"])]
    return not lines, lines


def _behind():
    try:
        out = subprocess.run(["git", "-C", str(ROOT), "rev-list", "--count", "HEAD..origin/main"],
                             capture_output=True, text=True, timeout=20, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return int(out.stdout.strip()) if out.returncode == 0 and out.stdout.strip().isdigit() else None


def render(res: dict, behind=None) -> str:
    lines = [f"child-failure escape census: {res['modules']} non-test modules under {res['root']}, "
             f"{res['entries']} entry points; "
             + ("origin/main distance unknown" if behind is None
                else f"{behind} commits behind origin/main"),
             "followed: " + ", ".join(res["followed"])
             + " -- reaching a module's main() or its top-level code with no handler for that "
               "class",
             f"{len(res['rows'])} rows"]
    for row in res["rows"]:
        lines.append(f"{row['module']}  {row['entry']}  {row['class']}"
                     + (f"  (raised {row['raised']})" if row["raised"] != row["class"] else ""))
        for hop in row["path"]:
            lines.append(f"    {hop['file']}:{hop['line']}  {hop['what']}  [{hop['kind']}]")
    if res["notices"]:
        lines.append("not read (each credited with catching nothing):")
        lines += [f"    {notice}" for notice in res["notices"]]
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--root", type=Path, default=DEFAULT_ROOT, help="the package to scan")
    ap.add_argument("--refusal", type=Path, default=DEFAULT_REFUSAL,
                    help="the module defining Refused")
    ap.add_argument("--min-modules", type=int, default=MIN_MODULES)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--check", action="store_true", help="exit 1 on a row no --known names")
    ap.add_argument("--known", action="append", default=[], metavar="PATH=CLASS",
                    help="a row that is open and tracked; repeatable")
    a = ap.parse_args(argv)
    try:
        res = census(a.root, refusal_path=a.refusal, min_modules=a.min_modules)
        if a.check:
            ok, lines = check(res, a.known)
            print("\n".join(lines) if lines else f"OK -- every row is named: {len(res['rows'])}")
            return 0 if ok else 1
        print(json.dumps(res, indent=1) if a.json else render(res, behind=_behind()))
        return 0
    except Refused as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
