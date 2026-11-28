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
    name binds it there and only there, as for any name;
  * a family subclass raised under ANOTHER module's name is a raise of the
    LENDER's family (#2015): `from a import Unbindable` then `raise
    Unbindable(...)`, or `raise a.Unbindable(...)` through its module, in a
    borrower that binds no family NAME of its own. Neither #1956's reading
    (the callee's own class) nor a family name, the bare form read as
    raising nothing and the dotted one as the root stem -- and `scan`
    dropped the borrower at its "callee has no family at all" pre-filter
    before either was read. The subclass is the lender's, so the lender's
    own subclass map decides and the raise takes the origin it has in the
    lender, to the definer through a re-export; a same-named class the
    lender does not derive from the family is none. `handler_origins` reads
    `except a.Unbindable` and a from-imported `except Unbindable` to that
    same origin as shape `exact`, so the two sides name one class by one
    origin. The name is read where it stands, innermost scope first, and a
    def or class statement binds its name as an import does: a borrowed
    name rebound by a later `class Unbindable(Exception)` -- at module
    level, or in the def that imported it -- is no longer the subclass.
    The pre-filter is widened only as far as this reading, and the old
    dotted credit (any dotted raise as its root's stem) is held to callees
    WITH a family, the only kind it was ever visible on: a borrower's
    `raise self.Error(...)` invents no origin beside the subclass.

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
    through an assignment alias; and a name rebound by an assignment. Each
    leaves the raise unread -- the last for a BORROWED subclass too, since
    #2015 reads a def or class statement as a binding and an assignment
    still not;
  * a borrowed subclass (#2015) whose LENDER's stem is carried by two
    indexed modules is not resolved: which `play.Unbindable` the borrower
    raises is not decidable from the AST, and no row names a lender's
    candidates -- only a CALLEE's are reported. The raise stays unread, the
    optimistic direction, as a collision of quiet callees is not a site;
  * a handler naming a SUBCLASS is `exact` for the lender's whole family,
    not for the subclass alone (#2015): the raise side gives a class and
    its subclass one origin, so under `except a.Unbindable` a def raising
    the subclass for one input and `a.Refused` for another reads
    `covered(exact)` and the interpreter lets the plain refusal out. It
    takes both raises in one def and a caller guarding the subclass alone;
    an origin per class would tell them apart, and that is #1956's output
    to change;
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
    intermediate library frames are not modelled, and neither is a guard in
    the def that CALLS a local helper -- a call written inside a nested def
    is lexically outside its caller's try -- so `candidate-escape` means
    "unguarded here and no base guard at the boundary", never "escapes at the
    exit". That last shape is the one site `rtl-sketch/` adds to the census,
    and it is ACCEPTED rather than repaired (#1577):
    `verify_synth_top.drum_solo_report` calls its nested `_render` inside a
    try whose handler is `except rc.Refused`, the callee's own class. The
    acceptance is observed, not read -- `tools/test_refusal_sibling_scan.py`
    runs the shipped function with a refusing callee (caught, returned in the
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
on purpose is the one ACCEPTED census entry named under handlers above.
The subclass raised under another module's name, listed here as unread
until #2015, is read now -- the lender's map, on the raise and the handler
side alike -- with the three limits just named above it.

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


class _Bindings:
    """The bindings of ONE function scope, in the same representation the
    module's own bindings use, plus the names a def or class statement
    defines there (#2015)."""

    def __init__(self) -> None:
        self.imports: dict[str, str] = {}                  # local name -> module stem
        self.from_imports: dict[str, tuple[str, str]] = {}  # local name -> (stem, original name)
        self.own: set[str] = set()                         # names a def or class binds in the scope


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

    def borrowed_subclass_at(self, txt: str, at: ast.AST | None = None) -> tuple[str, str] | None:
        """The (lender stem, class name) a raise's or a handler's class
        expression `txt` names through ANOTHER module, where `at` sits:
        `a.Unbindable` dotted, its root an import binding as a call's must
        be, or a from-imported `Unbindable`. Function scopes are read first,
        innermost first, as every name here is, and a nearer scope's binding
        of the name -- an import of its own, or a def or class it defines --
        shadows the module's, whatever it binds (#1577, #2015). None when
        the expression is neither: a family NAME and a class of this
        module's are read before this, and a bare local name lights up
        nothing."""
        if "." in txt:
            stem = self.resolve_head(txt.split(".")[0], at)
            return (stem, txt.rsplit(".", 1)[-1]) if stem is not None else None
        for b in self._enclosing_scopes(at):
            if txt in b.imports or txt in b.own:
                return None
            ent = b.from_imports.get(txt)
            if ent:
                return ent
        if txt in self.imports:
            return None
        return self.from_imports.get(txt)

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

    def _unbind_import(self, name: str) -> None:
        """A def or class statement binds `name` from its statement on, as an
        import does: an EARLIER import of that name no longer says what it
        is, so `raise X(...)` after `from a import X; class X(Exception)` is
        no borrowed subclass (#2015). The reverse order is #1956's rule, the
        import unbinding the class."""
        self.imports.pop(name, None)
        self.from_imports.pop(name, None)

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
                self._unbind_import(node.name)      # a class binds its name
            elif isinstance(node, FUNCTION_NODES):
                self._unbind_import(node.name)      # ... as a def's does
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
            # A def or class DEFINED in the function binds its name there
            # the same way (#2015).
            if not isinstance(node, (ast.Import, ast.ImportFrom, SCOPE_NODES)):
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
                if isinstance(node, (ast.Import, ast.ImportFrom)):
                    for nm in _bind(node, b.imports, b.from_imports):
                        b.own.discard(nm)         # the import rebinds the name
                else:
                    b.imports.pop(node.name, None)
                    b.from_imports.pop(node.name, None)
                    b.own.add(node.name)
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


def _lender_subclass_origins(mod: ModInfo, txt: str, at: ast.AST | None,
                             by_stem: dict[str, list[ModInfo]] | None) -> set[str]:
    """The family origins a raise or a handler names through ANOTHER module's
    family subclass (#2015): `from a import Unbindable` then `Unbindable`,
    or `a.Unbindable` through its module. The subclass is the LENDER's, so
    the lender's own subclass map decides -- a same-named class the lender
    does not derive from the family is none, whatever it is -- and the
    origins are those a raise of that class written IN the lender has
    (`raise_origins`), so a handler and the raise it catches name one class
    by one origin. Empty when the expression is neither shape, when no index
    is given, or when the stem is carried by zero or several indexed
    modules: which class `play.Unbindable` is is not decidable from the AST,
    and it is not guessed."""
    if by_stem is None:
        return set()
    ent = mod.borrowed_subclass_at(txt, at)
    if ent is None:
        return set()
    mods = by_stem.get(ent[0])
    if not mods or len(mods) != 1:
        return set()
    lender = mods[0]
    subs = lender.family_subclasses.get(ent[1])
    if not subs:
        return set()
    # the lender's own reading of its own class (#1956): a class DEFINED in
    # a module is the definer of what derives from it, and a borrowed
    # base's lender resolves to the module that defines it
    return {s if s == lender.path.stem else (origin_of(by_stem, s) or s) for s in subs}


def _imports_subclass_lender(mod: ModInfo, by_stem: dict[str, list[ModInfo]]) -> bool:
    """Whether some module `mod` imports -- at module level or in a def, by
    either form -- is indexed once and defines a family subclass, so that a
    raise in `mod` can name the LENDER's subclass (#2015). `scan`'s
    pre-filter asks for a family NAME, which such a borrower binds none of,
    and dropped it before its raise was read. Wider than the raise on
    purpose, and safe to be: which raises there are is `raise_origins`'
    reading, which holds the old dotted credit back from a callee without a
    family."""
    stems = set(mod.imports.values()) | {ent[0] for ent in mod.from_imports.values()}
    for b in mod.scopes.values():
        stems |= set(b.imports.values()) | {ent[0] for ent in b.from_imports.values()}
    return any(len(by_stem.get(s, ())) == 1 and by_stem[s][0].family_subclasses for s in stems)


def raise_origins(mod: ModInfo, fname: str,
                  by_stem: dict[str, list[ModInfo]] | None = None) -> set[str]:
    """The family origins the named def of `mod` raises itself: through a
    family name, through a module-local subclass of the family raised
    under its own name (#1956), or through ANOTHER module's subclass, by
    from-import or dotted (#2015). Given `by_stem`, a re-exported class
    resolves to the module that DEFINES it and a lender's subclass to the
    origin it has in the lender, as `handler_origins` resolves the guard
    side: both sides must name one class by one origin or a re-export reads
    as two. Without it no cross-module name resolves."""
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
            lent = (_lender_subclass_origins(mod, txt, node, by_stem)
                    if fam is None and not subs else set())
            if fam is not None:
                out.add(defining(fam))
            elif subs:
                # a class DEFINED in this module is the definer of what
                # derives from it, whatever its family name is bound to by now
                out.update(s if s == mod.path.stem else defining(s) for s in subs)
            elif lent:
                out |= lent
            elif "." in txt and mod.has_family:
                # the dotted credit as it was, for a callee WITH a family --
                # the only kind `scan`'s pre-filter ever let read it. A
                # module that binds no family name is in the census for a
                # LENDER's subclass alone, and its other dotted raises
                # (`self.Error`) must not invent an origin beside it (#2015)
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
    {base, exact, local, broad, other}. A LENDER's subclass named dotted or
    by from-import is read to the origin its raise has (#2015)."""
    t = handler.type
    if t is None:
        return set(), "broad"
    parts = t.elts if isinstance(t, ast.Tuple) else [t]
    origins: set[str] = set()
    shapes = set()

    def subclass_or_other(txt: str) -> None:
        """No family name and not `.Refused`: a LENDER's subclass, read to
        the origin its raise has (#2015) -- `exact`, since the class the
        handler names is the one raised -- or no refusal handler at all."""
        lent = _lender_subclass_origins(mod, txt, handler, by_stem)
        if lent:
            origins.update(lent)
            shapes.add("exact")
        else:
            shapes.add("other")

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
                subclass_or_other(txt)
        else:
            fam = mod.family_name_at(txt, handler)
            if fam is None:
                subclass_or_other(txt)
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
                # candidate has no family and borrows no subclass, or raises
                # no refusal for this callee, cannot refuse under ANY
                # resolution, so it is not a site at all -- the
                # multi-candidate mirror of the single-callee filters below
                # (#1214).
                if not any((c.has_family or _imports_subclass_lender(c, by_stem))
                           and raise_origins(c, fname or "", by_stem) for c in cands):
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
            if not (callee.has_family or _imports_subclass_lender(callee, by_stem)):
                continue                      # no family name, and no borrowed subclass
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
