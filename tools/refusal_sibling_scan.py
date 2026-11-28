#!/usr/bin/env python3
"""Census of cross-module `Refused` call sites: which modules call another
module's refusal-raising code, and whether the guard at the call site can
actually catch what the callee raises (#1162).

THE DEFECT CLASS. Every module here keeps its own `class Refused` (or aliases
a sibling's), and from #784 on they all subclass `refusal.Refused` -- but
subclasses of a common base are SIBLINGS, not each other. A handler written
`except Refused` catches only its own module's class, so a caller that reaches
another module's raising code through that guard reports a missing
precondition as an exit-1 traceback: a refusal rendered as a FAIL result, the
inversion `tools/exit_contract.py` exists to prevent. #1160 repaired the
cymbal_bands importer family; #1162 repaired `tools/probes/rs_guard_band.py`
and recorded this census so the next one is found by running a command
instead of re-deriving the class relations by hand.

WHAT THIS SCAN RESOLVES that the #1160 one-off dotted-call grep could not:

  * an attribute call counts only when the rooted name is a real IMPORT
    binding (`import x as ref` / `from x import f`) -- a local VARIABLE named
    `ref`, `rc` or `rp` no longer lights up (the one-off listed
    `tools/corpus_lineage.py` and `model/reference_rigs.py` on exactly that
    false positive);
  * the callee is resolved to a file, and the RAISE ORIGINS of the specific
    function called are read out of its def: `rc.prepare` can raise only
    `preparation_contract.Refused`, `rc.capabilities_for` only
    `refprofile.Refused` -- so a site is a candidate only for the origins its
    callee actually raises, not for every family in the tree;
  * the guard is classified per origin: the base (`except refusal.Refused`)
    covers every origin; an exact/alias handler covers an origin only when its
    own class IS that origin (`Refused = pc.Refused` covers run_case's
    refusals because run_case.Refused is the same object);
  * a try is credited only when the call is in its BODY: a handler covers
    the body it guards, not the same statement's except/else/finally
    clauses, where the raised refusal propagates past it (#1188
    "try-handler credit", fixed);
  * every tracked file is indexed by its own path -- two same-stem files
    (audition/play.py and fpga/play.py in this tree) are both scanned with
    their own trees, and a callee imported through a colliding stem is
    reported as `ambiguous-callee` with repo-relative candidates, never
    guessed -- and only when some candidate actually raises that refusal:
    a collision of quiet modules is not a site (#1188 "stem collision",
    #1214 filter);
  * `from x import f as g` keeps the ORIGINAL name, so a `g()` call
    resolves to f's raise origins instead of vanishing (#1188 "alias
    loss", fixed);
  * a family class BORROWED by from-import is visible: after
    `from x import Refused [as R]`, a `raise R(...)` in the borrower
    resolves to x's origin and an `except R` handler covers it -- shape
    `exact`, because the borrowed class IS x's; only a class defined in
    the module itself reads `local` (#1193 "borrowed family", fixed);
  * an import written INSIDE a function binds its name in that function and
    only there (#1573): `r2_release.stale_control` imports
    `release_manifest as rm` in an `elif` branch and calls `rm._capture`
    under a `with` inside an `except Refused` that is `r1_release.Refused`
    -- a binding read off `tree.body` alone never existed, so that site was
    absent from the census, not misclassified. Resolution is lexical and
    innermost-first, at the call, at the raise and at the handler alike: a
    nested def reads its enclosing def's imports, a nested def's own import
    overrides them, and a function's import never reaches a sibling function
    or the module. The tree needs the scoping, not just the walk:
    `r1_release.py` binds `rc` to record_compare at module level and to
    r1_candidate inside three of its functions;
  * a family class borrowed INSIDE a function is a family name there (#1577):
    after `from x import Refused as R` written in a def, a `raise R(...)` in
    that def or one nested in it resolves to x's origin, and an `except R`
    there covers it -- the module-level borrow's rule, read innermost-first
    at the raise and at the handler alike. So the name shadows the module's
    own `Refused` inside that def and leaves every sibling def reading the
    module's. It never sets `<mod>.Refused`: a function-local name is not a
    module attribute;
  * an `async def` callee is a callee (#1718): `funcs` registered
    `ast.FunctionDef` alone, so a call to an `async def` resolved to no def,
    read as raising nothing and was ABSENT from the census -- not
    misclassified, the same optimistic direction #1573 found for
    function-level imports. It is registered now and its raises are read
    like any def's; a try in an `async def main()` is the CLI boundary like
    one in `main()`. Whether the guards around such a call can catch what
    it raises is decided by where Python raises it, and the call only
    CREATES the coroutine: `await callee.load(p)` raises in the try body it
    is written in, while `coro = callee.load(p)` in a try with `await coro`
    after it raises past the try, though the Call node sits in its body. So
    a call to an `async def` is credited by the tries around it and by the
    CLI boundary only when it is written `await <call>`; a bare call reads
    `candidate-escape` with no guard and every origin uncovered.
  * a SUBCLASS of the module's family, raised under its own name, is a raise
    of that family (#1956): the raise was keyed on the NAME `Refused`, so a
    def whose raises were all of a local `class Unbindable(Refused)` read as
    raising nothing and its callers left the census -- the optimistic
    direction again. #1843's `f1_level_capture.load_capture` raised only
    such subclasses and its one caller, guarded at the CLI boundary, vanished
    (397 -> 396 sites). A class defined at module level with a family name
    for a base, or an earlier such class, is a family subclass to any depth
    and resolves to the origin its root family name has: the module's own,
    or the lender's through a re-export when the family is borrowed. Bases
    are read as of the class statement, as Python reads them, so a family
    name rebound by a LATER import does not reach a class defined before
    it; an import that rebinds the subclass's own name afterwards does --
    the name is then no longer the class -- and a def's own import of that
    name binds it there and only there, as for any name.

WHAT IT CANNOT TELL YOU, and why the per-site list is an inventory, not a
verdict. A dotted call is not necessarily a raising call on the CLI's input
space: `rs_guard_band.both_records` calls `rc.render_drum_solo` (which can
refuse) but no CLI input of that probe reaches its refusal arms. Reachability
is decided by tracing the caller with a known-answer input, as #1160 and
#1162's closing comments do per site. Blind spots, deliberately accepted:

  * only raises written in the CALLED DEF's own subtree are collected; a
    refusal raised one frame deeper (the def calls a helper that raises)
    is invisible unless the helper is the credited def;
  * a family subclass is found by its base NAMES, at module level. Not
    resolved: a dotted base (`class Sub(x.Refused)`), which on this tree is
    the shape of a family kept under another name (`class
    Refusal(refusal.Refused)`, whose raise and handlers the census cannot
    see at all: #2011); a class defined inside a function; a base reached
    through an assignment alias; and a subclass raised under ANOTHER
    module's name (`from x import Unbindable`, `x.Unbindable(...)`), which
    is neither the callee's family name nor a class of the callee's
    (#2015). Each leaves the raise unread;
  * bindings are per scope, not per statement: within a module or within a
    function the LAST import of a name in SOURCE order wins for that whole
    scope, so a call written above a later rebinding of its head is read
    through the later binding; a local VARIABLE shadowing an import can
    misattribute;
  * not modelled at all: `global`/`nonlocal` rebinding of an imported name,
    an import in a class body, and a name used in a decorator (evaluated in
    the enclosing scope) -- each contrived or a runtime error;
  * handlers are found around the CALL SITE, and then ON THE PATH (#1656):
    a site is `covered(cli-*)` only when EVERY path from the module's entry
    -- its top level, the `__main__` dispatch included -- to the site's own
    def runs through that handler's try body. The path follows bare-name
    calls through intermediate defs and a sealed dispatch table's
    subscript, so a guard in a caller one or two frames up counts when
    every caller guards (r2_release's `build()`), and so does one in the
    def that CALLS a nested helper by name inside its try
    (`verify_synth_top.drum_solo_report` -> `_render`), while a try in a
    sibling helper, or in `main()` after its subcommand returned
    (r0_capture's `except BaseException`), covers nothing. Until #1656 the
    credit was by PRESENCE -- any handler directly in `main()` or under the
    dispatch covered every site in the module, and nothing else did -- which
    was wrong in both directions (#1656's table). The `via` field names the
    handlers a row's credit rests on, as `<def>:<try line>`;
  * the path credit can only LOSE credit, never grant it, by not knowing
    the code (within one module; see the cross-module caveat below): it follows bare-name calls resolved by lexical scope and
    sealed dispatch tables, nothing else. Not followed, so never credited:
    a method reached through an instance, a call written in a lambda, a
    call from another module, `getattr`. A lambda or method frame the entry
    cannot reach that CALLS a def the graph reaches by another path counts as
    an unguarded path to it (credit is withheld); `getattr(...)("y")` and a
    cross-module caller make no edge at all, so they lose credit only for a
    callee with no resolvable in-edge -- the credit is per module. `try*` grants nothing. A function whose name is read as a
    VALUE anywhere in the module (`apply(helper, x)`, `pool.map(worker, ..)`)
    has a path no call site shows, so it is credited nowhere even where a
    bare-name call to it is guarded; a LOCAL of the same name reads nothing
    of the def. Control flow inside a def is flattened -- every call
    written there is an edge, whatever `if` hides it -- which adds paths. A
    `main()` that nothing at module level calls is not an entry. So
    `candidate-escape` means "no handler was established on every path",
    never "escapes at the exit".
  * that await rule is conservative where only the await could tell: a bare
    call to an `async def` whose coroutine is awaited INSIDE the try that
    created it reads `candidate-escape`, though Python catches it there,
    and so does one awaited under a guard further up the call chain. Which
    variable holds the coroutine, and where it is awaited, is not in the AST
    at the Call node; the same goes for the driver forms,
    `asyncio.run(callee.load(p))` and `asyncio.gather(callee.load(p))` inside
    a try, which surface the refusal there yet read `candidate-escape`. The
    price is a row to triage, never a false covered.
    An `async def` that yields is read as a coroutine function: its refusal
    surfaces at the `async for` that drives it, which the rule does not
    model;
  * test files are excluded: they are not CLI callers and their
    `pytest.raises` sites are deliberate;
  * only `SCAN_ROOTS` is scanned: `tools`, `model`, `pnr`, `fpga`,
    `rtl-sketch` and `audition` since #1577 -- the last two were outside it
    at #1573, which put the site above in no census; `audition/` has none.
    Every other tracked file is indexed as a CALLEE only.

Four further gaps were filed against this list -- try-handler credit,
stem collision and alias loss in #1188, the borrowed family in #1193 --
and are FIXED as of those issues' planted-shape controls (see WHAT THIS
SCAN RESOLVES above); they are named here so a reader searching the
blind-spot list for them finds their true status instead of an absence. A
fifth was listed here as "function-level imports are attributed to the whole
file" -- they were in fact INVISIBLE, the optimistic direction -- and
`fpga/` was outside the roots: between them the census could not see the
site of the defect #1569 repairs. Both blind spots are closed (#1573), and
it took the `fpga` root and the scope walk together: either alone leaves
that site absent. Three more were stated here as accepted until #1577: a
family class borrowed inside a function bound no family name, silencing
its raises and handlers the way #1193's module-level borrow had;
`rtl-sketch/` and `audition/` sat outside the roots; and inside a function
scope the binding that won was the walk's breadth-first order where this
list claimed last-import-wins. All three are closed. The one census entry
#1577 left open on purpose -- `verify_synth_top.drum_solo_report` calling its
nested `_render` inside a try the call is lexically outside -- stopped being
an escape when the credit became by path (#1656): the call to the helper is
an edge that carries the try. Its runtime evidence is kept where it was
written, in the test file's #1577 section.

NOT A GATE. Totals are reported, never pinned: the exit-status census
(`tools/refusal_exit_census.py`) learned that a totals pin reds on every
unrelated merge that touches a refusal site, and a gate that reds on other
people's work gets trained away. This tool prints how far behind origin/main
the tree it ran against is, because a census quoted from a stale tree is a
claim about a tree the reader cannot see.

    python tools/refusal_sibling_scan.py            # human table
    python tools/refusal_sibling_scan.py --json     # machine-readable
"""
from __future__ import annotations

import argparse
import ast
import itertools
import json
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
SCAN_ROOTS = ("tools", "model", "pnr", "fpga", "rtl-sketch", "audition")   # tools/ includes tools/probes/
FAMILY_NAME = "Refused"
BASE_STEM = "refusal"                   # the module defining the common base class
# a node whose body is a scope of its own: an import inside one binds that
# scope's name, not the enclosing function's (nor the module's)
SCOPE_NODES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
FUNCTION_NODES = (ast.FunctionDef, ast.AsyncFunctionDef)
# a FRAME: the def (or lambda) a call is written in, the unit the per-path
# credit of #1656 is computed for. A class body is no frame of its own: it
# runs whoever executes the `class` statement.
CALLER_NODES = FUNCTION_NODES + (ast.Lambda,)
COMP_NODES = (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)


# ---------------------------------------------------------------------------
# module index: every tracked production .py, by importable stem
# ---------------------------------------------------------------------------
def tracked_files() -> list[pathlib.Path]:
    out = subprocess.run(["git", "-C", str(ROOT), "ls-files", "*.py"],
                         capture_output=True, text=True, check=True)
    return [ROOT / p for p in out.stdout.split()
            if "/test_" not in p and not p.startswith("test_")]


def _bind(stmt: ast.stmt, imports: dict[str, str],
          from_imports: dict[str, tuple[str, str]]) -> list[str]:
    """Record ONE import statement's names in the given bindings, the way a
    rebinding does: the last import of a name in a scope wins there. Returns
    the local names it bound (a relative from-import binds none here)."""
    bound: list[str] = []
    if isinstance(stmt, ast.Import):
        for a in stmt.names:
            stem = a.name.split(".")[0]
            nm = a.asname or stem
            # a later plain import rebinds the name away from any earlier
            # from-import of it: the last binding wins across BOTH forms
            from_imports.pop(nm, None)
            imports[nm] = stem
            bound.append(nm)
    elif isinstance(stmt, ast.ImportFrom) and stmt.module and stmt.level == 0:
        stem = stmt.module.split(".")[0]
        for a in stmt.names:
            nm = a.asname or a.name
            imports.pop(nm, None)                 # ... and the reverse
            # the ORIGINAL name is kept (`from x import f as g`):
            # a `g()` call resolves to f's def, not to a missing g
            from_imports[nm] = (stem, a.name)
            bound.append(nm)
    return bound


def _local_bindings(scope: ast.AST) -> set[str]:
    """Every name `scope` binds as its own local, decided the way Python
    decides a name is local: bound by ANY statement of the body -- a
    parameter, an assignment or an augmented one, a loop or `with` target,
    an import, a nested def or class, an `except ... as`, a walrus, a
    `match` capture, a `del`. `global`/`nonlocal` unbinds it here. A nested
    scope's own names are not ours: only the name of the def itself is, a
    lambda's are nothing, and a comprehension's targets are its own (Python 3
    scopes them) -- only a walrus inside one binds HERE. A lambda or a
    comprehension passed as `scope` binds its parameters / its targets."""
    out: set[str] = set()
    a = getattr(scope, "args", None)
    if a is not None:
        out.update(x.arg for x in (*a.posonlyargs, *a.args, *a.kwonlyargs))
        out.update(x.arg for x in (a.vararg, a.kwarg) if x)
    if isinstance(scope, COMP_NODES):
        for g in scope.generators:
            out.update(t.id for t in ast.walk(g.target) if isinstance(t, ast.Name))
        return out
    lifted: set[str] = set()
    todo: list[ast.AST] = list(scope.body) if isinstance(scope.body, list) else [scope.body]
    while todo:
        n = todo.pop()
        if isinstance(n, COMP_NODES):
            # its targets are its own; a walrus anywhere inside binds here
            inner = list(ast.iter_child_nodes(n))
            while inner:
                m = inner.pop()
                if isinstance(m, ast.NamedExpr) and isinstance(m.target, ast.Name):
                    out.add(m.target.id)
                if not isinstance(m, ast.Lambda):
                    inner.extend(ast.iter_child_nodes(m))
            continue
        if isinstance(n, ast.ExceptHandler) and n.name:
            out.add(n.name)
        elif isinstance(n, (ast.MatchAs, ast.MatchStar)) and n.name:
            out.add(n.name)
        elif isinstance(n, ast.MatchMapping) and n.rest:
            out.add(n.rest)
        if isinstance(n, ast.Import):
            out.update(x.asname or x.name.split(".")[0] for x in n.names)
        elif isinstance(n, ast.ImportFrom):
            out.update(x.asname or x.name for x in n.names)
        elif isinstance(n, SCOPE_NODES):
            out.add(n.name)
            continue
        elif isinstance(n, ast.Lambda):
            continue
        elif isinstance(n, (ast.Global, ast.Nonlocal)):
            lifted.update(n.names)
        elif isinstance(n, ast.Name) and isinstance(n.ctx, (ast.Store, ast.Del)):
            out.add(n.id)
        todo.extend(ast.iter_child_nodes(n))
    return out - lifted


class _Bindings:
    """The import bindings of ONE function scope, in the same representation
    the module's own bindings use."""

    def __init__(self) -> None:
        self.imports: dict[str, str] = {}                  # local name -> module stem
        self.from_imports: dict[str, tuple[str, str]] = {}  # local name -> (stem, original name)


class ModInfo:
    def __init__(self, path: pathlib.Path):
        self.path = path
        self.tree = ast.parse(path.read_text(), filename=str(path))
        self.imports: dict[str, str] = {}                  # local name -> module stem
        self.from_imports: dict[str, tuple[str, str]] = {}  # local name -> (stem, original name)
        self.funcs: dict[str, ast.FunctionDef | ast.AsyncFunctionDef] = {}
        self.refused_origin: str | None = None   # stem whose class our Refused is
        self.refused_names: dict[str, str] = {}  # family name -> stem it last resolved to
        # module-level class -> the lender stems of the family it subclasses
        self.family_subclasses: dict[str, frozenset[str]] = {}
        self.has_local_family = False            # a local `class Refused` def (maybe shadowed)
        self.has_family = False                  # a family name bound anywhere: module level or a def
        self.parents: dict[ast.AST, ast.AST] = {}          # child -> parent, for the scope walk
        self.scopes: dict[ast.AST, _Bindings] = {}         # function node -> its own bindings
        # scope -> the name -> the defs written in ITS OWN body
        self.direct_defs: dict[ast.AST, dict[str, list[ast.AST]]] = {}
        self.local_names: dict[ast.AST, set[str]] = {}     # scope -> every name it binds itself
        # a sealed dispatch table -> the defs it holds: keyed by its Dict
        # node, or by its module-level name
        self.tables: dict[object, list[ast.AST]] = {}
        self.table_values: set[ast.Name] = set()           # table entries that are not unseen paths
        self.names: dict[str, list[ast.Name]] = {}        # every Name node, by id
        self.value_names: set[str] = set()   # names read as a value (not called) somewhere
        self._read_bindings()
        self._read_calls()

    def resolve_head(self, head: str, at: ast.AST | None = None) -> str | None:
        """The module stem a local name is bound to, if an import binds it.
        Given `at`, the node where the name is used, the function scopes
        enclosing it are read first, innermost first (#1573); without `at`,
        module level only."""
        for b in self._enclosing_scopes(at):
            if head in b.imports:
                return b.imports[head]
            ent = b.from_imports.get(head)
            if ent:
                return ent[0]
        if head in self.imports:
            return self.imports[head]
        ent = self.from_imports.get(head)
        return ent[0] if ent else None

    def from_import(self, name: str, at: ast.AST | None = None) -> tuple[str, str] | None:
        """The (stem, original name) a from-import binds `name` to where
        `at` sits, function scopes first as in `resolve_head`."""
        for b in self._enclosing_scopes(at):
            ent = b.from_imports.get(name)
            if ent:
                return ent
        return self.from_imports.get(name)

    def family_name_at(self, name: str, at: ast.AST | None = None) -> str | None:
        """The lender's stem a family NAME is bound to where `at` sits, or
        None when the name is no family there. Function scopes are read
        first, innermost first, as `resolve_head` does: `from x import
        Refused as R` written inside a def makes `R` a family name in that
        def and the defs nested in it only (#1577). Any binding of the name
        in a nearer scope, family or not, shadows a module-level family of
        the same name."""
        for b in self._enclosing_scopes(at):
            if name in b.imports:
                return None
            ent = b.from_imports.get(name)
            if ent:
                return ent[0] if ent[1] == FAMILY_NAME else None
        return self.refused_names.get(name)

    def family_subclasses_at(self, name: str, at: ast.AST | None = None) -> frozenset[str]:
        """The lender stems a module-level class `name` subclasses the family
        through, where `at` sits; empty when `name` is no such class there.
        Function scopes are read first, innermost first, as `family_name_at`
        does: a def's own import of the name binds it there and only there
        (#1956)."""
        for b in self._enclosing_scopes(at):
            if name in b.imports or name in b.from_imports:
                return frozenset()
        return self.family_subclasses.get(name, frozenset())

    def _family_through(self, base: ast.expr) -> set[str]:
        """The lender stems a class BASE puts the class being defined in the
        family of: a family name's lender, or an earlier local subclass's
        own. Called while the module's bindings are those of the class
        statement, which is when Python reads a base (#1956)."""
        txt = ast.unparse(base)
        fam = self.family_name_at(txt, base)
        if fam is not None:
            return {fam}
        return set(self.family_subclasses.get(txt, ()))

    def _enclosing_scopes(self, at: ast.AST | None):
        """The bindings of the function scopes `at` sits in, innermost
        first: a nested def reads its enclosing def's imports, and a
        function's import never reaches a sibling function or the module."""
        cur = self.parents.get(at) if at is not None else None
        while cur is not None:
            b = self.scopes.get(cur)
            if b is not None:
                yield b
            cur = self.parents.get(cur)

    def _scope_owner(self, node: ast.AST) -> ast.AST | None:
        """The nearest scope node `node` is written in, if any: a top-level
        statement has none."""
        cur = self.parents.get(node)
        while cur is not None and not isinstance(cur, SCOPE_NODES):
            cur = self.parents.get(cur)
        return cur

    def _read_bindings(self) -> None:
        for node in self.tree.body:
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                # the name is an import's now, whatever class it named before
                for nm in _bind(node, self.imports, self.from_imports):
                    self.family_subclasses.pop(nm, None)
                if isinstance(node, ast.Import):
                    for a in node.names:
                        self.refused_names.pop(a.asname or a.name.split(".")[0], None)
                if isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                    stem = node.module.split(".")[0]
                    for a in node.names:
                        if a.name == FAMILY_NAME:
                            # a BORROWED family class binds its name (aliased or
                            # not) to the LENDER's stem, last binding wins -- the
                            # same rule the Assign alias below follows (#1193).
                            # Only a binding named `Refused` exposes
                            # `<mod>.Refused`, so only that sets refused_origin:
                            # `import Refused as R` binds just `R` (#1234)
                            nm = a.asname or FAMILY_NAME
                            if nm == FAMILY_NAME:
                                self.refused_origin = stem
                            self.refused_names[nm] = stem
            elif isinstance(node, ast.ClassDef):
                # a local subclass of the family (#1956): read while the
                # bindings are those of THIS statement, as Python reads the
                # bases, so only names bound above and classes defined above
                # are reachable -- which is the fixpoint, in source order
                fams: set[str] = set()
                for base in node.bases:
                    fams |= self._family_through(base)
                self.family_subclasses.pop(node.name, None)
                if fams:
                    self.family_subclasses[node.name] = frozenset(fams)
                if node.name == FAMILY_NAME:
                    self.refused_origin = self.path.stem      # a local class
                    self.refused_names[FAMILY_NAME] = self.path.stem
                    self.has_local_family = True
            elif isinstance(node, ast.Assign):
                for t in node.targets:
                    if (isinstance(t, ast.Name) and t.id == FAMILY_NAME
                            and isinstance(node.value, ast.Attribute)
                            and isinstance(node.value.value, ast.Name)):
                        h = node.value.value.id
                        src = self.resolve_head(h) or h
                        self.refused_origin = src
                        self.refused_names[FAMILY_NAME] = src
        for node in ast.walk(self.tree):
            for ch in ast.iter_child_nodes(node):
                self.parents[ch] = node
            if isinstance(node, FUNCTION_NODES):
                self.funcs.setdefault(node.name, node)
        scoped: dict[ast.AST, list[ast.stmt]] = {}
        for node in ast.walk(self.tree):
            # an import written inside a function binds that function's name,
            # whatever block (elif, with, try) it sits in, and the module's
            # own bindings above are untouched (#1573). One written in a
            # class body is nobody's -- a stated blind spot in the docstring.
            if not isinstance(node, (ast.Import, ast.ImportFrom)):
                continue
            owner = self._scope_owner(node)
            if isinstance(owner, FUNCTION_NODES):
                scoped.setdefault(owner, []).append(node)
        for owner, nodes in scoped.items():
            b = self.scopes.setdefault(owner, _Bindings())
            # bound in SOURCE order, as at module level: `ast.walk` is
            # breadth-first, so taken in walk order a deeper, earlier import
            # was bound after a shallower, later one and won (#1577)
            for node in sorted(nodes, key=lambda n: (n.lineno, n.col_offset)):
                _bind(node, b.imports, b.from_imports)
        self.has_family = bool(self.refused_names) or any(
            ent[1] == FAMILY_NAME for b in self.scopes.values()
            for ent in b.from_imports.values())

    def _read_calls(self) -> None:
        """Index what the per-path credit needs (#1656): the defs written
        DIRECTLY in each scope (a def inside an `if` or a `try` of the body
        still binds the body's name; one inside a nested def or class binds
        that scope's), the dispatch tables, and every name read as a VALUE
        outside a sealed one."""
        scopes, dicts, small = [], [], []
        for n in ast.walk(self.tree):          # one walk, three indexes
            if isinstance(n, SCOPE_NODES):
                scopes.append(n)
            elif isinstance(n, (ast.Lambda, *COMP_NODES)):
                small.append(n)
            elif isinstance(n, ast.Dict):
                dicts.append(n)
            if isinstance(n, ast.Name):
                self.names.setdefault(n.id, []).append(n)
        for scope in [self.tree, *scopes]:
            own: dict[str, list[ast.AST]] = {}
            todo = list(scope.body)
            while todo:
                n = todo.pop()
                if isinstance(n, SCOPE_NODES):
                    own.setdefault(n.name, []).append(n)
                    continue                  # a scope of its own: its defs are not ours
                todo.extend(ast.iter_child_nodes(n))
            self.direct_defs[scope] = own
        self.local_names = {s: _local_bindings(s) for s in (*scopes, *small)}
        self._read_tables(dicts)
        for n in itertools.chain.from_iterable(self.names.values()):
            if (isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)
                    and n not in self.table_values
                    and not (isinstance(self.parents.get(n), ast.Call)
                             and self.parents[n].func is n)
                    and self._names_function(n)):
                self.value_names.add(n.id)

    def _names_function(self, n: ast.Name) -> bool:
        """Whether a value read of `n` reaches a function def: the innermost
        scope that BINDS the name must bind it with one. A local variable of
        the same name binds it first and reads nothing of the function --
        cymbal_tone_knob's `check()` holds a `verdict` from a tuple-unpack
        while `def verdict` sits at module level, and a `main` parameter of
        run_case's is not its `def main`."""
        scope = self._binding_scope(n.id, n)
        if scope is not None:
            return bool(self.direct_defs.get(scope, {}).get(n.id))
        return bool(self.direct_defs[self.tree].get(n.id))

    def _binding_scope(self, name: str, at: ast.AST) -> ast.AST | None:
        """The innermost enclosing scope that binds `name` as its own local,
        or None when only the module can. Every scope counts: a def, a class
        (visible only to code written directly in it), a lambda (its
        parameters) and a comprehension (its targets; the FIRST iterable is
        evaluated outside it). A parameter, a loop variable, an `except as`
        or a function-local import therefore stops the lookup exactly as it
        stops Python's."""
        cur, prev, prev2, innermost = self.parents.get(at), at, None, True
        while cur is not None:
            if isinstance(cur, SCOPE_NODES + (ast.Lambda, *COMP_NODES)):
                outside = (
                    (isinstance(cur, COMP_NODES) and prev is cur.generators[0]
                     and prev2 is prev.iter)               # first iterable
                    # a lambda's or def's DEFAULTS (and a def's annotations and
                    # decorators) are evaluated where the def is written, not in
                    # it: `def use(helper=helper)` reads the OUTER `helper`
                    or (isinstance(cur, (ast.Lambda, *FUNCTION_NODES)) and prev is cur.args)
                    or (isinstance(cur, FUNCTION_NODES)
                        and (prev is cur.returns or prev in cur.decorator_list))
                    or (isinstance(cur, ast.ClassDef)
                        and (prev in cur.bases or prev in cur.keywords
                             or prev in cur.decorator_list)))
                if not outside:
                    if not (isinstance(cur, ast.ClassDef) and not innermost):
                        if name in self.local_names[cur]:
                            return cur
                    innermost = False
            cur, prev, prev2 = self.parents.get(cur), cur, prev
        return None

    def _read_tables(self, dicts: list[ast.Dict]) -> None:
        """A DISPATCH TABLE -- `{"compare": cmd_compare, ...}[cmd]()` -- is a
        call, written through a subscript: the #1162/#1183 repairs in
        rs_guard_band, rs_mode_drive, balance_line_shape and
        measure_partial_balance all dispatch this way, under the try that is
        their repair. Read as callbacks, its entries would each carry an
        unseen path and lose the credit the try gives them. A literal of
        constant keys and function names is followed, then, when it is
        SEALED: subscripted and called at once, or bound to a module-level
        name whose every binding is such a literal and whose every other
        read is an immediate call or a read of its keys. Anything else done
        with it -- stored in a variable, iterated, mutated, passed on --
        leaves its entries the plain value references they are."""
        parents = self.parents

        def immediate(sub: ast.Subscript) -> bool:
            return isinstance(parents.get(sub), ast.Call) and parents[sub].func is sub

        cands: dict[ast.Dict, list[ast.AST]] = {}
        for d in dicts:
            if not d.keys or not all(isinstance(k, ast.Constant) for k in d.keys):
                continue
            callees: list[ast.AST] = []
            for v in d.values:
                if not (isinstance(v, ast.Name) and isinstance(v.ctx, ast.Load)):
                    break
                callees.extend(self._resolve_name(v.id, v))
            else:
                if callees:
                    cands[d] = callees
        bound: dict[str, list[ast.Dict]] = {}
        for d, callees in cands.items():
            up = parents.get(d)
            if isinstance(up, ast.Subscript) and up.value is d and immediate(up):
                self.tables[d] = callees
                self.table_values.update(d.values)
            elif (isinstance(up, ast.Assign) and up.value is d and parents.get(up) is self.tree
                  and len(up.targets) == 1 and isinstance(up.targets[0], ast.Name)):
                bound.setdefault(up.targets[0].id, []).append(d)

        def keys_only(n: ast.Name) -> bool:
            """A read of the table that iterates its KEYS and so cannot reach
            an entry: a membership test, or `sep.join(TABLE)`, which yields
            the keys (`', '.join(COMMANDS)` in a usage message)."""
            up = parents.get(n)
            if (isinstance(up, ast.Compare) and n in up.comparators
                    and any(isinstance(op, (ast.In, ast.NotIn)) for op in up.ops)):
                return True
            return (isinstance(up, ast.Call) and n in up.args
                    and isinstance(up.func, ast.Attribute) and up.func.attr == "join"
                    and isinstance(up.func.value, ast.Constant)
                    and isinstance(up.func.value.value, str))

        for name, ds in bound.items():
            sealed = True
            for n in self.names.get(name, []):
                up = parents.get(n)
                if isinstance(n.ctx, ast.Store):
                    sealed &= (isinstance(up, ast.Assign) and up.value in ds
                               and parents.get(up) is self.tree)
                elif isinstance(up, ast.Subscript) and up.value is n:
                    sealed &= immediate(up)
                else:
                    sealed &= keys_only(n)
            if sealed:
                self.tables[name] = [c for d in ds for c in cands[d]]
                for d in ds:
                    self.table_values.update(d.values)

    def caller_of(self, node: ast.AST) -> ast.AST:
        """The frame `node` is written in: its innermost enclosing def or
        lambda, else the module itself (its top level, the dispatch
        included)."""
        cur, prev = self.parents.get(node), node
        while cur is not None and not (isinstance(cur, CALLER_NODES)
                                       and not self._at_definition(cur, prev)):
            cur, prev = self.parents.get(cur), cur
        return self.tree if cur is None else cur

    @staticmethod
    def _at_definition(fn: ast.AST, child: ast.AST) -> bool:
        """`child` of def/lambda `fn` is evaluated when the def STATEMENT runs,
        in the frame that contains it, not in `fn`'s own body: its defaults
        and annotations (`fn.args`), its return annotation and its decorators
        (#1688 review round 3). `def f(x=callee.load())` raises while the
        enclosing frame -- the module, at top level -- executes the def."""
        return (child is fn.args or child is getattr(fn, "returns", None)
                or child in getattr(fn, "decorator_list", ()))

    def _resolve_name(self, name: str, at: ast.AST) -> list[ast.AST]:
        """The function defs a name is bound to, the way Python binds it: the
        innermost scope defining it wins, then the module. Nothing is guessed
        -- a name no def binds (a from-import, a callback parameter, a class
        instance) reaches no edge, and a class binding the name shadows an
        outer def instead of falling through to it. A class body's names are
        visible only to code written directly in it."""
        scope = self._binding_scope(name, at)
        own = self.direct_defs.get(self.tree if scope is None else scope, {}).get(name, [])
        return [d for d in own if isinstance(d, FUNCTION_NODES)]

    def resolve_call(self, call: ast.Call) -> list[ast.AST]:
        """The function defs `call` reaches: by bare name, or through a
        sealed dispatch table's subscript. Anything else reaches no edge."""
        f = call.func
        if isinstance(f, ast.Name):
            return self._resolve_name(f.id, call)
        if isinstance(f, ast.Subscript) and isinstance(f.value, (ast.Dict, ast.Name)):
            table = f.value if isinstance(f.value, ast.Dict) else f.value.id
            if isinstance(table, str) and self._binding_scope(table, call) is not None:
                return []                 # a parameter or local of that name, not the table
            return list(self.tables.get(table, []))
        return []


def build_index(files: list[pathlib.Path]) -> dict[str, ModInfo]:
    """Index every tracked file by its own path, NOT by importable stem:
    two same-stem files (audition/play.py, fpga/play.py) are distinct
    entries, so neither is silently dropped. Stem-keyed lookup lives in
    `scan`'s by_stem map, where a collision is visible instead of guessed."""
    idx: dict[str, ModInfo] = {}
    for f in files:
        try:
            idx[str(f)] = ModInfo(f)
        except SyntaxError:
            continue                              # recorded scope loss, not a crash
    return idx


def stem_map(idx: dict[str, ModInfo]) -> dict[str, list[ModInfo]]:
    """Importable stem -> every indexed module carrying it (a list: stems
    collide across directories)."""
    by_stem: dict[str, list[ModInfo]] = {}
    for m in idx.values():
        by_stem.setdefault(m.path.stem, []).append(m)
    return by_stem


def origin_of(by_stem: dict[str, list[ModInfo]], stem: str,
              seen: frozenset = frozenset()) -> str | None:
    """Resolve a module's family to the stem that DEFINES the class. A stem
    carried by zero or several indexed modules resolves to None: which class
    `play.Refused` is is not decidable from the AST alone."""
    mods = by_stem.get(stem)
    if not mods or len(mods) != 1 or stem in seen:
        return None
    ro = mods[0].refused_origin
    if ro == stem or ro is None:
        return ro
    return origin_of(by_stem, ro, seen | {stem})


def raise_origins(mod: ModInfo, fname: str,
                  by_stem: dict[str, list[ModInfo]] | None = None) -> set[str]:
    """The family origins the named def of `mod` raises itself: through a
    family name, or through a module-local subclass of the family raised
    under its own name (#1956). Given `by_stem`, a re-exported class resolves
    to the module that DEFINES it, as `handler_origins` resolves the guard
    side: both sides must name one class by one origin or a re-export reads
    as two."""
    def defining(stem: str) -> str:
        return (origin_of(by_stem, stem) if by_stem is not None else None) or stem

    f = mod.funcs.get(fname)
    if f is None:
        return set()
    out: set[str] = set()
    for node in ast.walk(f):
        if isinstance(node, ast.Raise) and node.exc:
            exc = node.exc
            if isinstance(exc, ast.Call):        # `raise Refused("reason")`
                exc = exc.func
            txt = ast.unparse(exc)
            fam = mod.family_name_at(txt, node)
            subs = mod.family_subclasses_at(txt, node) if fam is None else ()
            if fam is not None:
                out.add(defining(fam))
            elif subs:
                # a class DEFINED in this module is the definer of what
                # derives from it, whatever its family name is bound to by now
                out.update(s if s == mod.path.stem else defining(s) for s in subs)
            elif "." in txt:
                head = txt.split(".")[0]
                stem = mod.resolve_head(head, node) or head
                out.add(defining(stem) if txt.endswith(f".{FAMILY_NAME}") else stem)
    return out


# ---------------------------------------------------------------------------
# per-call-site analysis
# ---------------------------------------------------------------------------
def enclosing_try_parents(tree: ast.AST, call: ast.AST) -> list[ast.Try]:
    """The try statements whose handlers can actually catch `call`'s
    exceptions: only tries whose BODY contains the call. A try's handlers
    cover the body they guard -- NOT the same statement's except/else/
    finally clauses, where an exception propagates past them."""
    parents: dict[ast.AST, ast.AST] = {}
    for node in ast.walk(tree):
        for ch in ast.iter_child_nodes(node):
            parents[ch] = node
    out, cur = [], call
    while cur in parents:
        prev, cur = cur, parents[cur]
        if isinstance(cur, ast.Try) and prev in cur.body:
            out.append(cur)
    return out


def handler_origins(mod: ModInfo, by_stem: dict[str, list[ModInfo]],
                    handler: ast.ExceptHandler) -> tuple[set[str], str]:
    """(origins this handler catches, shape) -- shape in
    {base, exact, local, broad, other}."""
    t = handler.type
    if t is None:
        return set(), "broad"
    parts = t.elts if isinstance(t, ast.Tuple) else [t]
    origins: set[str] = set()
    shapes = set()
    for p in parts:
        txt = ast.unparse(p)
        if txt == "Exception" or txt == "BaseException":
            shapes.add("broad")
        elif txt == f"refusal.{FAMILY_NAME}":
            shapes.add("base")
        elif "." in txt:
            head = txt.split(".")[0]
            stem = mod.resolve_head(head, handler) or head
            if txt.endswith(f".{FAMILY_NAME}"):
                o = origin_of(by_stem, stem) or stem
                origins.add(o)
                # `import refusal as rf; except rf.Refused` is the base too
                shapes.add("base" if o == BASE_STEM else "exact")
            else:
                shapes.add("other")
        else:
            fam = mod.family_name_at(txt, handler)
            if fam is None:
                shapes.add("other")
            else:
                o = origin_of(by_stem, fam)
                if o:
                    origins.add(o)
                if (o or fam) == BASE_STEM:
                    # a name bound to the common base, module level or inside
                    # the def (`from refusal import Refused as R`), catches
                    # every subclass: the base guard, not an exact one
                    shapes.add("base")
                    continue
                # shape: `local` only for a class DEFINED in this module; a
                # borrowed or assigned name is the lender's own class, so the
                # handler is exact (#1193)
                shapes.add("local" if (mod.has_local_family
                                       and fam == mod.path.stem)
                           else "exact")
    shape = ("base" if "base" in shapes else "broad" if "broad" in shapes
             else "other" if shapes == {"other"} or "other" in shapes and not origins
             else "+".join(sorted(shapes - {"other"})))
    return origins, shape


def _handlers_within(mod: ModInfo, frame: ast.AST,
                     call: ast.AST) -> tuple[list[ast.ExceptHandler], bool]:
    """The handlers of the tries, written in `frame`'s own body, whose BODY
    holds `call` -- innermost try first, a try's handlers in source order, so
    the first that matches is the one Python runs. The walk stops at `frame`:
    a try further out, around the nested def the call sits in, guards the
    DEFINITION, not this call. It stops at a generator expression too, unless
    the call is in its first iterable (evaluated at once): the rest of its
    body runs when something iterates it, which may be after the try has
    exited, so a try around its CREATION guards nothing (#1688 review). The
    second value is True when the walk stopped at such a generator expression:
    the call runs LATER, under no handler of this frame at all, so the edge is
    deferred (see `path_guards`)."""
    out, cur, prev, prev2 = [], call, None, None
    while cur is not frame:
        prev2, prev, cur = prev, cur, mod.parents[cur]
        if (isinstance(cur, ast.GeneratorExp)
                and not (prev is cur.generators[0] and prev2 is prev.iter)):
            return out, True
        if isinstance(cur, ast.Try) and prev in cur.body:
            out.extend(cur.handlers)
    return out, False


def _is_generator(fn: ast.AST) -> bool:
    """A `yield` in `fn`'s own body (not a nested scope's)."""
    todo = list(fn.body)
    while todo:
        n = todo.pop()
        if isinstance(n, (ast.Yield, ast.YieldFrom)):
            return True
        if not isinstance(n, SCOPE_NODES + (ast.Lambda,)):
            todo.extend(ast.iter_child_nodes(n))
    return False


def path_guards(mod: ModInfo, by_stem: dict[str, list[ModInfo]]):
    """Returns `lookup(frame, origins) -> (kind, via)` (#1656): the WEAKEST
    outcome, over every path from the module's entry to `frame` and every
    origin, of a refusal raised there -- "base", "exact", "broad" or "none"
    -- and `via`, the handlers those outcomes rest on as `<def>:<try line>`.
    A frame nothing reaches is credited nothing.

    The path is a call graph over bare-name calls and sealed dispatch tables
    (`resolve_call`), rooted at the module's top level -- the `__main__`
    dispatch included. An edge carries the handlers of the tries whose body
    holds its call site, innermost first. A refusal walking OUT from a frame
    is decided by the FIRST handler that catches it: an inner `except
    Exception` shadows an outer `except refusal.Refused` (the CLI then
    prints the inner handler's marker, not a refusal), so the outcome is the
    innermost match, not the best one anywhere on the path, and `via` names
    that handler, not another. Each path ends in its own outcome and the
    frame is credited the weakest -- a handler on SOME path is never credited
    for EVERY path. One edge is added that no call site writes: from the
    entry, catching nothing, to each function whose name is read as a value
    somewhere (a callback has a path the graph cannot see). A call
    written in a frame the entry cannot reach (a lambda, a method) is an
    unguarded entry edge to its callee, so it cannot be dropped. A call that
    only CREATES a generator or coroutine is DEFERRED (its body runs when
    something iterates or awaits it, perhaps after the try exited), unless a
    coroutine is awaited on the spot; the same holds for a call in a
    generator expression's body. A deferred edge carries no handler AND does
    not inherit its caller's outcome: the path through it ends in "none", so
    a try around the creation is never credited. The price is a row to
    triage when the generator is in fact driven inside that try. Anything else the graph cannot resolve is
    not an edge: it loses credit only for a callee with no resolvable
    in-edge. The invariant is PER MODULE: a caller in another scanned module
    is not seen (the blind-spot list says so). `try*` (TryStar) is not an
    `ast.Try`, so a `try*` body contributes no handlers: credit lost, never
    granted. A handler whose class the scanner cannot resolve is read as not
    catching, as before."""
    top = mod.tree
    ins: dict[ast.AST, list[tuple[ast.AST, list[ast.ExceptHandler], bool]]] = {}
    outs: dict[ast.AST, list[ast.AST]] = {}

    def edge(caller: ast.AST, callee: ast.AST, held: list[ast.ExceptHandler],
             deferred: bool = False) -> None:
        ins.setdefault(callee, []).append((caller, held, deferred))
        outs.setdefault(caller, []).append(callee)

    for node in ast.walk(top):
        if isinstance(node, ast.Call):
            caller = mod.caller_of(node)
            for callee in mod.resolve_call(node):
                held, deferred = _handlers_within(mod, caller, node)
                # a call that only creates a generator or coroutine runs its
                # body later -- unless it is awaited on the spot (#1688 review)
                gen = _is_generator(callee)
                if (gen or isinstance(callee, ast.AsyncFunctionDef)) and not (
                        not gen and isinstance(mod.parents.get(node), ast.Await)):
                    deferred = True
                # DEFERRED is not "no handler here": the body runs when
                # something iterates or awaits it, under whatever is around
                # THAT, which the graph cannot see. An ordinary call with no
                # handler inherits its caller's outcome; a deferred one must
                # not, or the creator's try is credited for code that runs
                # after the try exited (#1688 review, round 2).
                edge(caller, callee, [] if deferred else held, deferred)
    for own in mod.direct_defs.values():
        for name, defs in own.items():
            if name in mod.value_names:
                for d in defs:
                    if isinstance(d, FUNCTION_NODES):
                        edge(top, d, [])
    reach, todo = {top}, [top]
    while todo:
        for callee in outs.get(todo.pop(), []):
            if callee not in reach:
                reach.add(callee)
                todo.append(callee)
    # A caller frame outside `reach` (a lambda or a method: no edge enters it)
    # still calls its callee, so that callee has a path the graph cannot see.
    # Dropping the edge would let a guarded sibling caller alone carry the
    # intersection; make it an unguarded entry edge instead (#1688 review).
    for callee, edges in list(ins.items()):
        if callee in reach and any(c not in reach for c, _, _ in edges):
            edge(top, callee, [])

    handler_kinds: dict[ast.ExceptHandler, tuple[frozenset[str], set[str]]] = {}

    def match(h: ast.ExceptHandler, origin: str) -> str | None:
        """What Python does with a refusal of `origin` at handler `h`:
        "base" (`except refusal.Refused`), "exact" (the origin's own class, or
        the module's local one), "broad" (`Exception`), or None when `h` does
        not catch it and the next handler out is tried."""
        if h not in handler_kinds:
            origins, shape = handler_origins(mod, by_stem, h)
            handler_kinds[h] = (frozenset(origins), set(shape.split("+")))
        origins, kinds = handler_kinds[h]
        if "base" in kinds:
            return "base"
        if kinds & {"exact", "local"} and origin in origins:
            return "exact"
        return "broad" if "broad" in kinds else None

    def where(h: ast.ExceptHandler) -> str:
        tr = mod.parents[h]
        w = mod.caller_of(tr)
        return f"{w.name if isinstance(w, FUNCTION_NODES) else '<module>'}:{tr.lineno}"

    by_origin: dict[str, dict[ast.AST, set[tuple[str, str | None]]]] = {}

    def outcomes(origin: str) -> dict[ast.AST, set[tuple[str, str | None]]]:
        """Per frame, the set of (kind, handler) a refusal of `origin` raised
        in it ends in, one per path to it: walking OUT from the frame, the
        FIRST handler that catches it decides (an inner `except Exception`
        shadows an outer `except refusal.Refused`), and a path no handler
        catches ends in ("none", None). The least fixed point, so a cycle
        converges and adds nothing."""
        if origin in by_origin:
            return by_origin[origin]
        out: dict[ast.AST, set[tuple[str, str | None]]] = {n: set() for n in reach}
        out[top] = {("none", None)}
        changed = True
        while changed:
            changed = False
            for n in reach:
                if n is top:
                    continue
                new: set[tuple[str, str | None]] = set()
                for c, held, deferred in ins.get(n, []):
                    if c not in reach:
                        continue
                    if deferred:
                        new.add(("none", None))
                        continue
                    first = next(((k, where(h)) for h in held if (k := match(h, origin))), None)
                    new |= {first} if first else out[c]
                if new != out[n]:
                    out[n], changed = new, True
        by_origin[origin] = out
        return out

    rank = {"none": 0, "broad": 1, "exact": 2, "base": 3}

    def lookup(frame: ast.AST, origins: set[str]) -> tuple[str, list[str]]:
        """(the WEAKEST outcome over every path and every origin, the handlers
        those outcomes rest on as `<def>:<try line>`). A frame the entry does
        not reach is credited nothing."""
        if frame not in reach or not origins:
            return "none", []
        ends = [e for o in origins for e in outcomes(o)[frame]]
        weakest = min((k for k, _ in ends), key=rank.__getitem__, default="none")
        wheres = {w for k, w in ends if w}
        return weakest, sorted(wheres, key=lambda w: (int(w.rsplit(":", 1)[1]), w))

    return lookup


def refusal_runs_at_call(mod: ModInfo, call: ast.Call, callees: list[ModInfo],
                         fname: str) -> bool:
    """Whether a refusal raised through this call would surface where the
    Call node stands, so that the tries around it -- and the CLI boundary --
    can catch it from there. False when the call only DEFERS the callee's code
    (#1718, #1688 review round 3):

      * a bare call to an `async def` CREATES a coroutine; only `await <call>`
        runs the callee where the call is written;
      * a bare call to a generator function creates a generator; its body
        runs at the first `next`, perhaps after every try has exited;
      * a call written in a generator expression's body (not its first
        iterable) runs when something iterates the expression.

    The same rules `path_guards` applies to an edge to a LOCAL def, applied at
    the final, cross-module call site. Among same-stem candidates one
    generator or `async def` decides: which module the import binds is not
    decidable, and the reading that cannot report a false covered is taken."""
    if _handlers_within(mod, mod.caller_of(call), call)[1]:
        return False
    defs = [c.funcs.get(fname) for c in callees]
    if any(isinstance(d, FUNCTION_NODES) and _is_generator(d) for d in defs):
        return False
    if isinstance(mod.parents.get(call), ast.Await):
        return True
    return not any(isinstance(d, ast.AsyncFunctionDef) for d in defs)


def _rel_label(path: pathlib.Path) -> str:
    """Repo-relative when under ROOT, absolute otherwise (synthetic test
    trees live outside it). `site` and `candidates` must share this format so
    the --json census is host-independent (#1214)."""
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def scan(files: list[pathlib.Path], idx: dict[str, ModInfo]) -> list[dict]:
    by_stem = stem_map(idx)
    sites: list[dict] = []
    for f in files:
        mod = idx.get(str(f))
        if mod is None:
            continue
        guards = path_guards(mod, by_stem)
        label = _rel_label(f)
        for node in ast.walk(mod.tree):
            callee_stem = fname = None
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and isinstance(node.func.value, ast.Name)):
                head = node.func.value.id
                callee_stem = mod.resolve_head(head, node)
                fname = node.func.attr
            elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                ent = mod.from_import(node.func.id, node)
                if ent:
                    # `from x import f[ as g]`: the def reached is f, whatever
                    # the call site names it
                    callee_stem, fname = ent
            if callee_stem is None or callee_stem == f.stem:
                continue
            cands = by_stem.get(callee_stem, [])
            if not cands:
                continue                      # callee outside the tracked tree
            # (#1718) a bare call to an `async def` is credited by no guard
            # at all: its refusal is raised where the coroutine is awaited
            runs_here = refusal_runs_at_call(mod, node, cands, fname or "")
            if len(cands) > 1:
                # stem collision: which module the import binds is not
                # decidable from the AST, so name the candidates instead of
                # guessing -- unless a base/broad guard settles coverage the
                # same way for every one of them. A collision whose every
                # candidate either has no family or raises no refusal for
                # this callee cannot refuse under ANY resolution, so it is
                # not a site at all -- the multi-candidate mirror of the
                # single-callee filters below (#1214).
                if not any(c.has_family and raise_origins(c, fname or "", by_stem)
                           for c in cands):
                    continue
                ashapes = []
                for tr in (enclosing_try_parents(mod.tree, node) if runs_here else []):
                    for h in tr.handlers:
                        _, shape = handler_origins(mod, by_stem, h)
                        if shape in ("base", "broad"):
                            ashapes.append(shape)
                sites.append(dict(
                    site=f"{label}:{node.lineno}",
                    callee=f"{callee_stem}.{fname}",
                    origins=[],
                    guard=",".join(ashapes) or "none",
                    classification=(f"covered({'+'.join(ashapes)})" if ashapes
                                    else "ambiguous-callee"),
                    uncovered=[],
                    candidates=sorted(_rel_label(c.path) for c in cands),
                ))
                continue
            callee = cands[0]
            if not callee.has_family:
                continue                      # callee has no family at all
            origins = raise_origins(callee, fname or "", by_stem)
            if not origins:
                continue                      # this function raises no refusal
            covered, shapes = set(), []
            for tr in (enclosing_try_parents(mod.tree, node) if runs_here else []):
                for h in tr.handlers:
                    ho, shape = handler_origins(mod, by_stem, h)
                    if shape in ("base", "broad"):
                        covered |= origins
                        shapes.append(shape)
                    elif shape != "other":
                        covered |= ho & origins
                        shapes.append(shape)
            uncovered = origins - covered
            # the CLI credit is BY PATH (#1656): only what every path from
            # the entry to THIS site's own def catches
            kind, via = (guards(mod.caller_of(node), set(uncovered))
                         if runs_here else ("none", []))
            if uncovered and kind == "base":
                # a base guard is the first handler on every path
                classification, uncovered = "covered(cli-base)", []
            elif uncovered and kind == "exact":
                classification, uncovered = "covered(cli-exact)", []
            elif uncovered and kind == "broad":
                classification = "relabelled(cli-total)"
            else:
                classification = ("candidate-escape" if uncovered
                                  else f"covered({'+'.join(shapes) or 'none-needed'})")
            sites.append(dict(
                site=f"{label}:{node.lineno}",
                callee=f"{callee_stem}.{fname}",
                origins=sorted(origins),
                guard=",".join(shapes) or "none",
                classification=classification,
                uncovered=sorted(uncovered),
                **({"via": via} if via and not classification.startswith("candidate") else {}),
            ))
    return sites


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    a = ap.parse_args(argv)
    files = tracked_files()
    scope = [f for f in files if any(str(f.relative_to(ROOT)).startswith(r + "/") for r in SCAN_ROOTS)]
    idx = build_index(files)
    sites = scan(scope, idx)
    rev = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "--short", "HEAD"],
                         capture_output=True, text=True).stdout.strip()
    behind = subprocess.run(["git", "-C", str(ROOT), "rev-list", "--count", f"HEAD..origin/main"],
                            capture_output=True, text=True).stdout.strip() or "?"
    esc = [s for s in sites if s["classification"] == "candidate-escape"]
    if a.json:
        print(json.dumps(dict(rev=rev, behind_origin_main=behind, roots=list(SCAN_ROOTS),
                              files_scanned=len(scope), sites=sites), indent=1))
        return 0
    print(f"# refusal_sibling_scan @ {rev} ({behind} behind origin/main), "
          f"{len(scope)} files, {len(sites)} cross-module refusal-raising call sites, "
          f"{len(esc)} candidate escapes (inventory, NOT a verdict -- see module docstring)")
    for s in sorted(sites, key=lambda s: (s["classification"], s["site"])):
        unc = f" uncovered={','.join(s['uncovered'])}" if s["uncovered"] else ""
        amb = (f" candidates={','.join('/'.join(c.split('/')[-2:]) for c in s['candidates'])}"
               if s.get("candidates") else "")
        via = f" via={','.join(s['via'])}" if s.get("via") else ""
        print(f"{s['site']:58s} {s['callee']:34s} origins={','.join(s['origins']):30s} "
              f"guard={s['guard']:12s} {s['classification']}{unc}{amb}{via}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
