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
    `candidate-escape` with no guard and every origin uncovered;
  * a site written in a NESTED DEF is credited by the tries around the
    helper's CALLS (#1600): `drum_solo_report` guards `_render(...)` with
    `except rc.Refused`, but the `rc.render_drum_solo` call sits in
    `_render`'s def, lexically outside that try, and read `guard=none,
    candidate-escape`. The def that calls a local helper is read ONE level
    up, and only when every reference to the helper in the module is a direct
    call written in the body of the def that defines it -- the refusals are
    `helper_call_sites`' docstring -- and an origin is credited only when
    EVERY call catches it.

WHAT IT CANNOT TELL YOU, and why the per-site list is an inventory, not a
verdict. A dotted call is not necessarily a raising call on the CLI's input
space: `rs_guard_band.both_records` calls `rc.render_drum_solo` (which can
refuse) but no CLI input of that probe reaches its refusal arms. Reachability
is decided by tracing the caller with a known-answer input, as #1160 and
#1162's closing comments do per site. Blind spots, deliberately accepted:

  * only raises written in the CALLED DEF's own subtree are collected; a
    refusal raised one frame deeper (the def calls a helper that raises)
    is invisible unless the helper is the credited def;
  * bindings are per scope, not per statement: within a module or within a
    function the LAST import of a name in SOURCE order wins for that whole
    scope, so a call written above a later rebinding of its head is read
    through the later binding; a local VARIABLE shadowing an import can
    misattribute;
  * not modelled at all: `global`/`nonlocal` rebinding of an imported name,
    an import in a class body, and a name used in a decorator (evaluated in
    the enclosing scope) -- each contrived or a runtime error;
  * handlers are found around the CALL SITE, plus ONE level above: the
    module's CLI boundary -- `main()`, the `__main__` dispatch, and (from
    #1641) a module-level function the DISPATCH calls by bare name, the
    `_cli()` wrapper hh_probe5's repair #1611 first wrote and this census
    read as no guard at all. That is where #1160/#1162's repairs live. The
    wrapper is followed ONE level, on the way in only: a call from the
    wrapper itself, from a def nested in the dispatch, or from an except
    handler or finally clause reaches nothing. A function `main()` calls is
    not followed -- main fans out to siblings, and a sibling's try guards
    its own branch alone. The credit is by PRESENCE, not by path: a handler
    anywhere in a boundary frame covers every uncovered site in the module,
    whether or not the site's own path runs through it (#1656). Guards in
    intermediate library frames are not modelled, with ONE exception: the
    def that CALLS a local helper (#1600, under WHAT THIS SCAN RESOLVES). A
    call written inside a nested def is lexically outside its caller's try,
    and until #1600 that read `guard=none` here. What remains unmodelled of
    that shape is the helper that ESCAPES its def -- returned, stored on an
    object, passed as a callback, decorated, `global` -- a call of it from
    any other scope, a helper at module level or a method (the cross-def
    call graph), and a helper reached without its name (`locals()`, `eval`),
    the same class as the `global`/`nonlocal` rebinding below. Each is kept
    a candidate rather than credited. So `candidate-escape` means
    "unguarded here, at the helper's calls, and no base guard at the
    boundary", never "escapes at the exit". The one site `rtl-sketch/` adds
    to the census is that shape, and it is ACCEPTED rather than repaired
    (#1577): `verify_synth_top.drum_solo_report` calls its nested `_render`
    twice inside a try whose handler is `except rc.Refused`, the callee's
    own class, and reads `covered(exact)` since #1600. The acceptance is
    observed, not only read -- `tools/test_refusal_sibling_scan.py` runs the
    shipped function with a refusing callee (caught, returned in the
    report's `refused` list) and with a sibling class (escapes);
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
list claimed last-import-wins. All three are closed. What #1577 left open
on purpose was the one ACCEPTED census entry named under handlers above;
#1600 closed that one too, and every input that must keep such a site a
candidate is a control in the test file, each run under the interpreter.

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
# a node the walk up from a call does not cross (#1600): a def, a lambda, a
# class body or a generator expression. A def, a lambda and a generator
# expression run their body where it is CALLED, AWAITED or ITERATED; a class
# body binds names of its own. `scope_entry` stops at one, so the tries
# credited to a helper's CALLS are read inside the enclosing def. A list, set
# or dict comprehension is NOT one -- it runs eagerly where it stands
DEFERRED_SCOPES = SCOPE_NODES + (ast.Lambda, ast.GeneratorExp)


# ---------------------------------------------------------------------------
# module index: every tracked production .py, by importable stem
# ---------------------------------------------------------------------------
def tracked_files() -> list[pathlib.Path]:
    out = subprocess.run(["git", "-C", str(ROOT), "ls-files", "*.py"],
                         capture_output=True, text=True, check=True)
    return [ROOT / p for p in out.stdout.split()
            if "/test_" not in p and not p.startswith("test_")]


def _bind(stmt: ast.stmt, imports: dict[str, str],
          from_imports: dict[str, tuple[str, str]]) -> None:
    """Record ONE import statement's names in the given bindings, the way a
    rebinding does: the last import of a name in a scope wins there."""
    if isinstance(stmt, ast.Import):
        for a in stmt.names:
            stem = a.name.split(".")[0]
            nm = a.asname or stem
            # a later plain import rebinds the name away from any earlier
            # from-import of it: the last binding wins across BOTH forms
            from_imports.pop(nm, None)
            imports[nm] = stem
    elif isinstance(stmt, ast.ImportFrom) and stmt.module and stmt.level == 0:
        stem = stmt.module.split(".")[0]
        for a in stmt.names:
            nm = a.asname or a.name
            imports.pop(nm, None)                 # ... and the reverse
            # the ORIGINAL name is kept (`from x import f as g`):
            # a `g()` call resolves to f's def, not to a missing g
            from_imports[nm] = (stem, a.name)


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
        self.has_local_family = False            # a local `class Refused` def (maybe shadowed)
        self.has_family = False                  # a family name bound anywhere: module level or a def
        self.parents: dict[ast.AST, ast.AST] = {}          # child -> parent, for the scope walk
        self.scopes: dict[ast.AST, _Bindings] = {}         # function node -> its own bindings
        self._read_bindings()

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
                _bind(node, self.imports, self.from_imports)
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
            elif isinstance(node, ast.ClassDef) and node.name == FAMILY_NAME:
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
    """The family origins the named def of `mod` raises itself. Given
    `by_stem`, a re-exported class resolves to the module that DEFINES it, as
    `handler_origins` resolves the guard side: both sides must name one class
    by one origin or a re-export reads as two."""
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
            if fam is not None:
                out.add(defining(fam))
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


def scope_entry(mod: ModInfo, node: ast.AST) -> tuple[ast.AST | None, ast.AST]:
    """`(scope, entry)`: the nearest deferred-scope node `node` is written in
    -- a def, a lambda, a class body or a generator expression, None at module
    level -- and the child of it that `node` sits under. `entry in
    scope.body` is what tells a call in a def's BODY from one in its
    decorators, defaults or annotations: those are written inside the def
    node and evaluated where the def statement stands, in the enclosing
    scope (#1600)."""
    prev, cur = node, mod.parents.get(node)
    while cur is not None and not isinstance(cur, DEFERRED_SCOPES):
        prev, cur = cur, mod.parents.get(cur)
    return cur, prev


def _is_generator(mod: ModInfo, h: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    """Whether `h` ITSELF yields: `h()` then only CREATES a generator, and
    the site runs where it is iterated. A yield in a def nested in `h` is
    that def's own."""
    return any(isinstance(n, (ast.Yield, ast.YieldFrom)) and scope_entry(mod, n)[0] is h
               for n in ast.walk(h))


def _is_declared(mod: ModInfo, h: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    """Whether a `global`/`nonlocal` statement names `h`: the def then binds
    a name that frames outside its own def can call it by."""
    return any(isinstance(n, (ast.Global, ast.Nonlocal)) and h.name in n.names
               for n in ast.walk(mod.tree))


def helper_call_sites(mod: ModInfo, site: ast.Call,
                      ) -> tuple[ast.FunctionDef | ast.AsyncFunctionDef, list[ast.Call]] | None:
    """`(def, calls)` when `site` is written in the body of a nested def `h`
    and EVERY reference to `h` in the module is a direct call written in the
    body of the def that defines it. Those calls are the frames a refusal
    from `site` surfaces in, one level up, so the tries around them can catch
    it (#1600): `drum_solo_report` guards its two `_render()` calls with
    `except rc.Refused` while the `rc.render_drum_solo` call sits in
    `_render`'s def. None, and no credit, whenever that is not decidable from
    the AST -- fail closed; each refusal has a control in the test file, run
    under the interpreter:

      * `site` in a lambda, a generator expression, a class body or at module
        level: no nested def holds it. A lambda has no def to follow and a
        generator expression runs where it is iterated; a site in an eager
        list, set or dict comprehension IS in the def's body;
      * `site` in `h`'s decorators, defaults or annotations: evaluated at
        the def statement, outside every try around the calls;
      * `h` defined at module level or in a class body (a method): that is
        the cross-def call graph, which this does not follow -- a
        module-level helper is callable from every frame in the module;
      * `h` decorated (the decorator holds it, and may call it or hand it
        on), a generator (`h()` only creates one; the site runs where it is
        iterated), or declared `global`/`nonlocal` (callable from any frame
        by a name this module need not spell);
      * any reference that is not the callee of a call -- returned, assigned
        to another name, passed as a callback, stored on an object, deleted --
        or a call from any other scope: a sibling def, a lambda, the module.
        The name is read module-wide on purpose, so a def of the same name
        elsewhere, called anywhere, keeps the row a candidate: a false
        candidate is the price, never a false covered;
      * a bare call to an `async def` `h`, which only CREATES the coroutine
        (#1718's rule, one level up): only `await h(...)` runs the site where
        the call is written;
      * no call at all: an uncalled helper runs no site, and "every call is
        guarded" must not hold vacuously of nothing.
    """
    h, entry = scope_entry(mod, site)
    if not isinstance(h, FUNCTION_NODES) or entry not in h.body:
        return None
    outer, _ = scope_entry(mod, h)
    if not isinstance(outer, FUNCTION_NODES):
        return None
    if h.decorator_list or _is_generator(mod, h) or _is_declared(mod, h):
        return None
    calls: list[ast.Call] = []
    for ref in ast.walk(mod.tree):
        if not (isinstance(ref, ast.Name) and ref.id == h.name):
            continue
        scope, entry = scope_entry(mod, ref)
        call = mod.parents.get(ref)
        if not (isinstance(ref.ctx, ast.Load) and isinstance(call, ast.Call)
                and call.func is ref and scope is outer and entry in outer.body):
            return None
        if (isinstance(h, ast.AsyncFunctionDef)
                and not isinstance(mod.parents.get(call), ast.Await)):
            return None
        calls.append(call)
    return (outer, calls) if calls else None


def helper_call_credit(mod: ModInfo, by_stem: dict[str, list[ModInfo]],
                       site: ast.Call, origins: set[str]) -> tuple[set[str], list[str]]:
    """(origins the tries around the helper's CALLS catch for a refusal
    raised at `site`, the shapes of the handlers that catch them). An origin
    is credited only when EVERY call catches it, because the refusal can
    surface at any of them: crediting the union would read a helper with one
    guarded and one unguarded call covered. Only tries inside the enclosing
    def are read -- one level, the def that calls the helper; a try further
    out guards that def's own callers, which this does not follow. A handler
    that catches nothing of `origins` adds no shape here, unlike at a site's
    own try: the row's `guard` is what the caller's credit rests on. Nothing
    at all when `helper_call_sites` refuses."""
    found = helper_call_sites(mod, site)
    if found is None:
        return set(), []
    outer, calls = found
    caught, shapes = set(origins), []
    for call in calls:
        cov = set()
        for tr in enclosing_try_parents(outer, call):
            for h in tr.handlers:
                ho, shape = handler_origins(mod, by_stem, h)
                got = (origins if shape in ("base", "broad")
                       else ho & origins if shape != "other" else set())
                cov |= got
                if got:
                    shapes.append((shape, got))
        caught &= cov
    return caught, sorted({s for s, got in shapes if got & caught})


def _is_main_guard(node: ast.AST) -> bool:
    """`if __name__ == ...`: the `__main__` dispatch block."""
    return (isinstance(node, ast.If) and isinstance(node.test, ast.Compare)
            and getattr(node.test.left, "id", "") == "__name__")


def _entry_calls(guard: ast.If) -> set[str]:
    """The names the `__main__` dispatch calls by bare name on its way into
    the program. Its statements and conditions are read; NOT a def of its own
    (runs only if something calls IT: one level only), an except handler or
    a finally clause (cleanup after the work, not entry into it)."""
    own_frames = SCOPE_NODES + (ast.Lambda,)
    out: set[str] = set()
    todo: list[ast.AST] = [guard.test, *guard.body, *guard.orelse]
    while todo:
        n = todo.pop()
        if isinstance(n, own_frames):
            continue
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name):
            out.add(n.func.id)
        if isinstance(n, ast.Try):
            todo.extend(n.body)
            todo.extend(n.orelse)
        else:
            todo.extend(ast.iter_child_nodes(n))
    return out


def cli_boundary_guards(mod: ModInfo, by_stem: dict[str, list[ModInfo]]) -> list[tuple[set[str], str]]:
    """The refusal handlers at the module's CLI boundary -- try statements
    that sit directly in a `main()` def, `def` or `async def` (#1718), or in the
    `if __name__ == "__main__"` dispatch, where #1160/#1162's repairs live, or
    directly in a module-level function the DISPATCH calls by bare name: the
    `_cli()` wrapper hh_probe5's repair first used and this census read as no
    guard at all (#1641). One level only, and from the dispatch only -- a
    function `main()` calls is a sibling of main's other callees, and its try
    guards its own branch alone (#1656 has the tree that showed it). Returns
    (origins, shape) per
    handler: `except refusal.Refused` covers every origin; an exact/alias
    handler covers only its own origin (measure_partial_balance's `except
    Refused` with `Refused = pc.Refused` is exact, not local-only)."""
    parents: dict[ast.AST, ast.AST] = {}
    for node in ast.walk(mod.tree):
        for ch in ast.iter_child_nodes(node):
            parents[ch] = node
    wrappers: set[str] = set()
    for node in ast.walk(mod.tree):
        if _is_main_guard(node):
            wrappers |= _entry_calls(node)

    def in_boundary(tr: ast.Try) -> bool:
        cur = parents.get(tr)
        if isinstance(cur, FUNCTION_NODES) and cur.name == "main":
            return True
        if (isinstance(cur, FUNCTION_NODES) and cur.name in wrappers
                and parents.get(cur) is mod.tree):
            return True                   # a `_cli()` wrapper, called by name
        while cur is not None:            # the __main__ dispatch block
            if _is_main_guard(cur):
                return True
            cur = parents.get(cur)
        return False

    out: list[tuple[set[str], str]] = []
    for node in ast.walk(mod.tree):
        if isinstance(node, ast.Try) and in_boundary(node):
            out.extend(handler_origins(mod, by_stem, h) for h in node.handlers)
    return out


def refusal_runs_at_call(mod: ModInfo, call: ast.Call, callees: list[ModInfo],
                         fname: str) -> bool:
    """Whether a refusal raised through this call would surface where the
    Call node stands, so that the tries around it -- and the CLI boundary --
    can catch it from there. False for one shape only (#1718): a bare call
    to an `async def` CREATES a coroutine and runs none of the callee's code;
    the refusal is raised wherever the coroutine is awaited, which no walk
    up from the Call node can see. Only `await <call>` runs the callee where
    the call is written. Among same-stem candidates one `async def` decides:
    which module the import binds is not decidable, and the reading that
    cannot report a false covered is the one taken."""
    if isinstance(mod.parents.get(call), ast.Await):
        return True
    return not any(isinstance(c.funcs.get(fname), ast.AsyncFunctionDef) for c in callees)


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
        boundary = cli_boundary_guards(mod, by_stem)
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
                if runs_here:
                    # (#1600) the same credit, for what it can settle here: a
                    # base or broad handler at EVERY call of the helper
                    every = {o for c in cands for o in raise_origins(c, fname or "", by_stem)}
                    # ... but only when EVERY origin any candidate raises is
                    # caught at EVERY call. A shape is a property of one
                    # call: a base handler at one call plus a partial exact
                    # handler at another leaves `caught` non-empty yet short
                    # of `every`, and that shape must not settle the row
                    # (PR #1803 review: a false covered).
                    h_caught, h_shapes = helper_call_credit(mod, by_stem, node, every)
                    if every and h_caught >= every:
                        ashapes += [s for s in h_shapes if s in ("base", "broad")]
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
            if runs_here:
                # (#1600) one frame up: a site written in a nested def is
                # caught by the tries around the helper's CALLS
                up, up_shapes = helper_call_credit(mod, by_stem, node, origins)
                if up - covered:
                    covered |= up
                    shapes += up_shapes
            uncovered = origins - covered
            bguards = boundary if runs_here else []
            bshapes = {shape for _, shape in bguards}
            borigins: set[str] = set()
            for ho, shape in bguards:
                if shape == "base":
                    borigins |= origins
                elif shape != "broad" and shape != "other":
                    borigins |= ho
            if uncovered and "base" in bshapes:
                # a base guard at the CLI boundary catches it on the way out
                classification, uncovered = "covered(cli-base)", []
            elif uncovered and uncovered <= borigins:
                classification, uncovered = "covered(cli-exact)", []
            elif uncovered and "broad" in bshapes:
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
        print(f"{s['site']:58s} {s['callee']:34s} origins={','.join(s['origins']):30s} "
              f"guard={s['guard']:12s} {s['classification']}{unc}{amb}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
