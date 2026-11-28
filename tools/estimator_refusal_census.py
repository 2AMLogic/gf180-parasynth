#!/usr/bin/env python3
"""Census of the modules that call an estimator able to raise
`audio_measure.InsufficientEvidence` under handlers that cannot catch it
(#1751 class): the #1763 walk, committed, and the rules that walk was blind to
(#1782).

    python tools/estimator_refusal_census.py                   # the table
    python tools/estimator_refusal_census.py --json            # machine-readable
    python tools/estimator_refusal_census.py --rules direct    # the #1763 walk alone

THE DEFECT CLASS. `audio_measure.InsufficientEvidence` subclasses
`AssertionError`, not `refusal.Refused`, so a dispatch written
`except refusal.Refused` -- which catches every Refused sibling since #784 --
lets an estimator's own refusal past it as an exit-1 traceback: a REFUSED
outcome rendered as a FAIL result, the inversion `tools/exit_contract.py`
exists to prevent. #1751 repaired `tools/probes/rs_mode_drive.py`. #1763
listed ten more modules with an AST walk posted in a comment and traced
them; #1782 found that walk blind in two places and asked for it as a
committed instrument with controls. This is it.

A ROW IS A CANDIDATE, NOT A DEFECT, and the criterion is #1763's, unchanged:
the module calls an origin (below), it has an entry point or a handler
naming a `Refused` class, and NO handler anywhere in it stops the family.
Whether any input the module accepts makes the estimator refuse is not an
AST question: #1763 traced its ten and found eight stopped by an earlier
gate. Every row is UNTRACED, and the follow-up issues #1782 filed say so.
At `03148399` (2026-11-28): the #1763 walk's 11 candidates (its ten and the
#1751 site) against 53 under the default rules, 42 of them new -- 23 of
those able to raise `NonFiniteAudio` alone, through `run_case.prepare` or
`run_case.load_reference`.

AN ORIGIN is a `def` a call to which can raise the family:

  * DIRECT -- its own subtree raises the family by name: `raise
    <family>(...)`, through any binding.
  * REQUIRE (rule 1, #1782) -- `Estimate.require` is a method, so no
    module-level def carried its raise. A `.require()` call counts when its
    receiver is an Estimate of the estimator module, typed from the AST:
    a call to a def whose return annotation names that class or whose body
    returns it, the class itself, or a name assigned from either, at any
    enclosing function scope. Typing is POSITIVE: a `.require()` on anything
    else is not a site, however it is spelled -- `rig_qualification`'s
    `Qualification.require` raises a `RigRefusal`, and a Refused handler
    catches that.
  * ONE-HOP (rule 2, #1782) -- a def with no direct raise whose own subtree
    calls a direct origin or an attributed `.require()`, each edge noted
    `guarded` or not. `corner_from_curve` -> `plateau_db`;
    `waveform_id` -> `cycle_average` (guarded) and `harmonic_signature`
    (not); `reference_rigs.SurgeRig.tone_project` -> `Estimate.require`. A
    def whose only raising edges are all guarded is NOT an origin and is
    listed apart (`guarded_only`): the refusal stops inside it.

Each origin carries the family class it raises, and so does each site. That
separates two populations at a glance: rows that raise
`InsufficientEvidence` (an evidence refusal on the tool's own signals) and
rows that can raise only `NonFiniteAudio` -- everything that reaches
`run_case.prepare` or `run_case.load_reference`, which call
`require_finite`. A NaN is not a property of a signal but of a loader, and
`tools/nan_guard_audit.py` keeps the register of the boundaries that assert
it; those rows are still candidates, because the register is not this
census's to read, and they are labelled so nobody traces twenty of them as
if they were twenty different questions.

WHAT A GUARD IS. A handler stops the family when, resolved through the
module's bindings, it names the root class (`InsufficientEvidence` by any
import or alias), an ancestor (`AssertionError`, `Exception`,
`BaseException`), or nothing (`except:`). Three readings the #1763 walk did
not make, each with a control in `tools/test_estimator_refusal_census.py`:

  * `except am.NonFiniteAudio` catches that subclass ALONE; a plain
    `InsufficientEvidence` passes it. #1763's prose and its walk counted it
    as covering.
  * a handler that puts the exception back is no guard: `except Exception:
    log(); raise`, or `raise e` for the exception it bound
    (`fpga/play_song.py`, `tools/run_case.run_filter_case`,
    `tools/r0_capture._write_atomic`). The walk read a handler by NAME. A
    `raise <something else>`, a new exception, stops the family.
  * a bare `raise` therefore names nothing and makes no def a direct
    origin: whether it lets the family out is a question about the try it
    sits under, which the one-hop rule already asks.

Handlers are module-wide, as in #1763: one that stops the family ANYWHERE
makes the module not a candidate, whether or not the raising call's own
path runs through it. `refused_handlers` by spelling -- a handler element
named `Refused` -- is #1763's criterion too.

HOW A CALL IS RESOLVED. Through `tools/refusal_sibling_scan.py`'s module
index and its per-scope import resolution (#1573): `am.f(...)`,
`import audio_measure as AM`, `from audio_measure import f as g` (the
ORIGINAL def), a binding written inside a function (`capture_curve`'s own
`import reference_rigs as rr`), `module.Class.method(...)` and
`module.imported_module.f(...)` (`ref.am.harmonic_signature`, through which
`tools/measure_mono_m1a_reference.py` reaches an estimator and is in no
#1763 row), a bare name to a def of the same module, and `self.method(...)`
to the enclosing class or a base of it named in the same module. A call on
a receiver the AST cannot type -- a local variable, a parameter, a call
result -- is credited by METHOD NAME when any def of that name is an
origin, and flagged `untyped-receiver`: `dev.tone_gain_db(...)`. That is
the reading that cannot report a false covered; the price is a row to
triage.

`--rules direct` IS THE #1763 WALK, exactly, so the known escape can be
shown ABSENT under it: the estimator module's top-level functions with a
named raise of their own, reached through a binding of that module, and
handlers read by name. Its candidates are always a subset of the default
rules', and the report marks every other candidate `NEW`.

WHAT IT IS BLIND TO, printed in every run rather than only said here:

  * a def that calls a ONE-HOP origin is two hops from the refusal and is
    not a candidate: `SurgeRig.tone_gain_db` -> `self.tone_project` ->
    `Estimate.require`. "CHAINS BEYOND ONE HOP" lists every such def and
    every site that calls one (#1782: out of scope, filed). That is also
    why `_as_float` -> `require_finite` makes `_as_float` a one-hop origin
    and no estimator that calls it one;
  * a `.require()` whose receiver is not typed is listed under
    "UNRESOLVED .require()" with the receiver as written: an Estimate
    passed in as a parameter, read off a returned object's field, or
    produced by a closure from another module
    (`tools/measure_repeatability.py:880`, `t20(y, sr).require("T20")` with
    `t20 = rc._t20_ms(0.005)`);
  * `contextlib.suppress` is not read at all, an `except` that names a
    TUPLE ALIAS (`FAILURES = (...)`) is not expanded, and a handler is not
    followed into another function. A handler that re-raises CONDITIONALLY
    (`if type(e).__name__ != "Rejected": raise`) reads as re-raising
    always: a row to triage, never a false covered;
  * bindings are per scope with the last assignment in source order winning,
    the rule of the index this reuses; `global`/`nonlocal` rebinding and an
    import in a class body are not modelled there and not here;
  * a lambda's or a comprehension's body is read unguarded (it runs when
    called), and a call written in a nested def is not credited with its
    caller's `try`;
  * test files (`test_*.py`, `*_test.py`) are scanned and never candidates:
    under pytest an `InsufficientEvidence` fails the test, the conservative
    outcome (#1763). Their unresolved `.require()` sites are not listed;
  * two defs of one module that share a qualname share a label.

NOT A GATE. Totals are reported, never pinned, for the reason
`tools/refusal_sibling_scan.py` records: a pin reds on every unrelated
merge that touches a site and gets trained away. It prints the tree it read
and how far behind `origin/main` that tree is (`tools/scorecard.py`'s
convention), because a census quoted from a stale tree is a claim about a
tree the reader cannot see.

PRECONDITIONS, ASSERTED AT THE POINT OF USE AND REFUSED (exit 2) WHEN UNMET:
every tracked file parses; exactly one module is named `audio_measure.py`
(a colliding stem is not guessed); it defines `InsufficientEvidence`; and
exactly one of its classes has a `require` that raises the family -- if
that ever stops being so (a rebase onto `refusal.Refused` is #1763's
recorded design decision) rule 1 is void, and a census that silently
reported no `.require()` origins would look exactly like one that found
none.

WRONG BEFORE RIGHT, so a reader can calibrate a row. Three times in this
file's making, each in a different direction, and none by reading:

  * the planted callers of `tools/test_estimator_refusal_census.py` were
    run before the census existed. One named `refusal.Refused` in a handler
    without importing `refusal`, so its own handler raised a NameError over
    the refusal it was written to catch; one "guarded inside" shape exits 0,
    not 2. The fixtures were wrong, the expectations right;
  * a bare `raise` under a broad handler was first counted as a DIRECT
    origin. That made `r0_capture._write_atomic`, whose try body cannot
    raise the family, an origin; found by reading the origin table against
    the files. The rule moved to the guard, where it belongs: a handler
    that raises again is no guard;
  * an optimisation of the module walk broke handler detection -- an `elif`
    swallowed every `except ... as e` -- and reported 17 candidates where
    the walk has 11. Found by diffing the optimised output against the
    unoptimised one, line for line, before believing either.
"""

from __future__ import annotations

import argparse
import ast
import json
import pathlib
import subprocess
import sys
from dataclasses import dataclass

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT / "tools") not in sys.path:
    sys.path.insert(0, str(ROOT / "tools"))

import refusal_sibling_scan as rss  # noqa: E402  (the module index and import resolution)

ESTIMATOR_STEM = "audio_measure"
FAMILY_ROOT = "InsufficientEvidence"
REQUIRE = "require"
REFUSED = "Refused"
MAIN = "__main__"
ANCESTORS = frozenset({"AssertionError", "Exception", "BaseException"})
#: what the #1763 walk read as a guard, by NAME alone and wherever bound
WALK_COVERS = ANCESTORS | {FAMILY_ROOT, "NonFiniteAudio"}
RULES = ("direct", "transitive")
FUNC = (ast.FunctionDef, ast.AsyncFunctionDef)
SCOPE = FUNC + (ast.ClassDef,)
NESTED = SCOPE + (ast.Lambda,)
TRY = (ast.Try, ast.TryStar)
MODULE = "<module>"
UNKNOWN = "unknown"


class PreconditionRefused(Exception):
    """The census cannot answer on this tree. Exit 2: an instrument in the
    wrong state refuses rather than report."""


def _rel(path: pathlib.Path) -> str:
    """Repo-relative when under ROOT, absolute otherwise, so `--json` is
    host-independent for the real tree."""
    try:
        return str(pathlib.Path(path).relative_to(ROOT))
    except ValueError:
        return str(path)


def tracked_files() -> list[pathlib.Path]:
    """Every tracked `.py`, test files included: the #1763 walk's own scope."""
    out = subprocess.run(["git", "-C", str(ROOT), "ls-files", "*.py"],
                         capture_output=True, text=True, check=True)
    return [ROOT / p for p in out.stdout.split() if (ROOT / p).exists()]


def is_test_file(path: pathlib.Path) -> bool:
    return path.name.startswith("test_") or path.name.endswith("_test.py")


def _main_guard_in(tests) -> bool:
    return any(MAIN in ast.unparse(t) for t in tests)


def has_main_guard(tree: ast.AST) -> bool:
    """The #1763 walk's spelling of "has an entry point": an `if` whose
    condition mentions `__main__`."""
    return _main_guard_in(n.test for n in ast.walk(tree) if isinstance(n, ast.If))


def _git(*args: str) -> str:
    try:
        r = subprocess.run(["git", "-C", str(ROOT), *args], capture_output=True,
                           text=True, timeout=10)
    except Exception:
        return ""
    return r.stdout.strip() if r.returncode == 0 else ""


def _reraises(h: ast.ExceptHandler) -> bool:
    """Whether a handler puts the caught exception back: a bare `raise`, or
    a `raise` of the name it bound the exception to, in its own body -- not
    in a def nested in it, and not a `raise <something else>`, which is a
    new exception."""
    todo = list(h.body)
    while todo:
        n = todo.pop()
        if isinstance(n, NESTED):
            continue
        if isinstance(n, ast.Raise) and (
                n.exc is None or (isinstance(n.exc, ast.Name) and n.exc.id == h.name)):
            return True
        todo.extend(ast.iter_child_nodes(n))
    return False


@dataclass(frozen=True)
class Def:
    """A `def` wherever it is written: a module's top level, a class body or
    another def."""
    mod: "Mod"
    node: ast.FunctionDef | ast.AsyncFunctionDef
    qualname: str
    cls: str | None

    @property
    def label(self) -> str:
        return f"{self.mod.name}.{self.qualname}"


class Mod:
    """One indexed module and what the census reads of it, gathered in ONE
    walk: parsing this tree costs more than reading it."""

    def __init__(self, census: "Census", info: rss.ModInfo):
        self.census = census
        self.info = info
        self.rel = _rel(info.path)
        self.name = info.path.stem
        self.is_test = is_test_file(info.path)
        self.defs: list[Def] = []
        self.by_node: dict[ast.AST, Def] = {}
        self.top: dict[str, Def] = {}
        self.methods: dict[tuple[str, str], Def] = {}
        self.classes: dict[str, ast.ClassDef] = {}
        self.child: dict[ast.AST, dict[str, Def]] = {}
        self.binds: dict[Def, set[str]] = {}
        self.calls: list[ast.Call] = []
        self.raises: list[ast.Raise] = []
        self.handler_nodes: list[ast.ExceptHandler] = []
        self.handlers: dict[int, dict] = {}
        self.sites: list[dict] = []
        self.walk = False
        parents, funcs, tests, bound = info.parents, [], [], []
        for node in ast.walk(info.tree):
            if isinstance(node, ast.Call):
                self.calls.append(node)
            elif isinstance(node, FUNC):
                funcs.append(node)
            elif isinstance(node, ast.ClassDef):
                self.classes.setdefault(node.name, node)
            elif isinstance(node, ast.Name):
                if isinstance(node.ctx, (ast.Store, ast.Del)):
                    bound.append((node, (node.id,)))
            elif isinstance(node, ast.Raise):
                self.raises.append(node)
            elif isinstance(node, ast.ExceptHandler):
                self.handler_nodes.append(node)
                if node.name:
                    bound.append((node, (node.name,)))
            elif isinstance(node, ast.arg):
                bound.append((node, (node.arg,)))
            elif isinstance(node, ast.Import):
                bound.append((node, tuple(a.asname or a.name.split(".")[0] for a in node.names)))
            elif isinstance(node, ast.ImportFrom):
                bound.append((node, tuple(a.asname or a.name for a in node.names)))
            elif isinstance(node, ast.If):
                tests.append(node.test)
        for node in sorted(funcs, key=lambda n: (n.lineno, n.col_offset)):
            chain: list[ast.AST] = []
            cur = node
            while cur in parents:
                cur = parents[cur]
                if isinstance(cur, SCOPE):
                    chain.append(cur)
            cls = next((c.name for c in reversed(chain) if isinstance(c, ast.ClassDef)), None)
            d = Def(self, node, ".".join([*(c.name for c in reversed(chain)), node.name]), cls)
            self.defs.append(d)
            self.by_node[node] = d
            self.binds[d] = set()
            if not chain:
                self.top.setdefault(node.name, d)
            elif isinstance(chain[-1], ast.ClassDef):
                self.methods.setdefault((chain[-1].name, node.name), d)
            else:
                self.child.setdefault(chain[-1], {}).setdefault(node.name, d)
        for node, names in bound:
            cur = node
            while cur in parents:
                cur = parents[cur]
                d = self.by_node.get(cur)
                if d is not None:
                    self.binds[d].update(names)
                    break
                if isinstance(cur, ast.ClassDef):
                    break
        self.entry = ("__main__" if _main_guard_in(tests)
                      else "main" if "main" in self.top else "none")

    def read_handlers(self) -> None:
        """After the family is known: what every `except` of the module
        catches."""
        for h in self.handler_nodes:
            self.handlers[id(h)] = self.census._handler_info(self, h)

    @property
    def refused_handlers(self) -> list[str]:
        return [f"{h['line']}:{s}" for h in self.handlers.values() for s in h["refused"]]

    @property
    def family_handlers(self) -> list[str]:
        """The handlers the active rule set reads as guards for the family.
        Under the default rules a resolved family class or ancestor that does
        not re-raise; under `direct` anything the #1763 walk read by name."""
        if self.census.rules == "direct":
            stops = [h for h in self.handlers.values() if h["walk_covers"]]
        else:
            stops = [h for h in self.handlers.values() if h["catches"] and not h["reraises"]]
        return [f"{h['line']}:{h['type']}" for h in stops]

    @property
    def reraising_handlers(self) -> list[str]:
        """Family handlers that put the exception back: no guard at all."""
        return [f"{h['line']}:{h['type']}" for h in self.handlers.values()
                if h["catches"] and h["reraises"]]

    @property
    def subclass_only_handlers(self) -> list[str]:
        return [f"{h['line']}:{s}" for h in self.handlers.values() for s in h["subclass_only"]]

    @property
    def printed(self) -> bool:
        """#1763's "printed" row: the module has an entry point or a handler
        naming a `Refused` class."""
        return self.entry != "none" or bool(self.refused_handlers)

    @property
    def candidate(self) -> bool:
        return (self.printed and bool(self.sites) and not self.is_test
                and not self.family_handlers)

    @property
    def walk_candidate(self) -> bool:
        """What the #1763 walk made of this module: a walk-visible call, an
        entry point or a `Refused` handler, and no handler NAMING the family
        or an ancestor -- re-raises and bindings not withstanding."""
        return (self.walk and self.printed and not self.is_test
                and not any(h["walk_covers"] for h in self.handlers.values()))

    def row(self) -> dict:
        return dict(path=self.rel, is_test=self.is_test, entry=self.entry,
                    printed=self.printed, candidate=self.candidate,
                    new=self.candidate and not self.walk_candidate, walk=self.walk,
                    walk_candidate=self.walk_candidate,
                    reraising_handlers=self.reraising_handlers,
                    raises=sorted({c for s in self.sites for c in s["classes"]}),
                    refused_handlers=self.refused_handlers,
                    family_handlers=self.family_handlers,
                    subclass_only_handlers=self.subclass_only_handlers,
                    n_sites=len(self.sites), sites=self.sites)


class _ByLabel:
    """A per-def table, read by the def's label: `module.Class.def`, each
    value passed through `emit`."""

    def __init__(self, items: dict[Def, object], emit=None):
        self._items = dict(items)
        self._emit = emit or (lambda value: value)

    def __getitem__(self, label: str):
        return self._emit(self._items[self._def(label)])

    def __contains__(self, label: str) -> bool:
        return self._def(label) is not None

    def __len__(self) -> int:
        return len(self._items)

    def __iter__(self):
        return iter(d.label for d in self._items)

    def items(self):
        return [(d.label, self._emit(v)) for d, v in self._items.items()]

    def _def(self, label: str) -> Def | None:
        return next((d for d in self._items if d.label == label), None)


class Census:
    """One tree under one rule set. `rows` holds every module, `candidates`
    the shape #1763 defined. `index` is a `refusal_sibling_scan.build_index`
    result to reuse between rule sets: parsing the real tree costs more than
    reading it."""

    def __init__(self, files, rules: str = "transitive", index: dict | None = None):
        if rules not in RULES:
            raise PreconditionRefused(
                f"unknown rules {rules!r}: expected one of {', '.join(RULES)}")
        self.rules = rules
        self.files = [pathlib.Path(f) for f in files]
        self.idx = index if index is not None else rss.build_index(self.files)
        self.unparsed = [f for f in self.files if str(f) not in self.idx]
        if self.unparsed:
            raise PreconditionRefused(
                f"{len(self.unparsed)} file(s) do not parse: "
                + ", ".join(_rel(f) for f in self.unparsed[:5]))
        self.by_stem = rss.stem_map(self.idx)
        self.mods = sorted((Mod(self, i) for i in self.idx.values()), key=lambda m: m.rel)
        self._by_info = {m.info: m for m in self.mods}
        for m in self.mods:
            if len(self.by_stem.get(m.name, ())) != 1:
                m.name = m.rel[:-3] if m.rel.endswith(".py") else m.rel
        found = self.by_stem.get(ESTIMATOR_STEM, [])
        if len(found) != 1:
            raise PreconditionRefused(
                f"expected exactly one module named {ESTIMATOR_STEM}.py; found {len(found)} "
                f"modules named {ESTIMATOR_STEM}.py"
                + (": " + ", ".join(self._by_info[i].rel for i in found) if found else ""))
        self.est = self._by_info[found[0]]
        self._memo: dict = {}
        self._find_family()
        for m in self.mods:
            m.read_handlers()
        self._pass_raises()
        self._method_index: dict[str, list[Def]] = {}
        for m in self.mods:
            if not m.is_test:
                for d in m.methods.values():
                    self._method_index.setdefault(d.node.name, []).append(d)
        self._walk = frozenset(d for d in self.est.defs if d.cls is None and self._raise_items(d))
        self._direct: dict[Def, list[int]] = {}
        self._one_hop: dict[Def, list[dict]] = {}
        self._guarded_only: dict[Def, list[dict]] = {}
        self.unresolved_requires: list[dict] = []
        self.chain_sites: list[dict] = []
        self._chains: dict[Def, list[dict]] = {}
        self._pass_origins()
        self._pass_sites()
        self._pass_chains()
        self.rows = [m.row() for m in self.mods]

    # -- preconditions --------------------------------------------------------

    def _find_family(self) -> None:
        if FAMILY_ROOT not in self.est.classes:
            raise PreconditionRefused(
                f"{self.est.rel} defines no class {FAMILY_ROOT}: the estimator's refusal "
                f"class is not where this census looks")
        members = {FAMILY_ROOT}
        growing = True
        while growing:
            growing = False
            for name, c in self.est.classes.items():
                if name not in members and any(isinstance(b, ast.Name) and b.id in members
                                               for b in c.bases):
                    members.add(name)
                    growing = True
        self.members = frozenset(members)
        raising = sorted(
            name for name, c in self.est.classes.items()
            if any(isinstance(b, FUNC) and b.name == REQUIRE
                   and any(self._raise_node(self.est, n) for n in ast.walk(b)
                           if isinstance(n, ast.Raise))
                   for b in c.body))
        if len(raising) != 1:
            raise PreconditionRefused(
                f"expected exactly one class of {self.est.rel} whose `{REQUIRE}` raises "
                f"{FAMILY_ROOT}; found {len(raising)}"
                + (f" ({', '.join(raising)})" if raising else "")
                + " -- rule 1 has nothing to count (a rebase onto refusal.Refused is "
                  "#1763's recorded design decision)")
        self.estimate_class = raising[0]
        self.require_def, = [d for d in self.est.defs
                             if d.qualname == f"{self.estimate_class}.{REQUIRE}"]

    # -- what a name denotes --------------------------------------------------

    def _assigned(self, m: Mod, at: ast.AST, name: str) -> list[ast.expr]:
        """The expressions assigned to `name` where `at` stands: those of the
        nearest enclosing function that assigns it, else the module's, in
        source order."""
        cur = at
        while cur in m.info.parents:
            cur = m.info.parents[cur]
            if isinstance(cur, FUNC):
                vals = self._stores(m, cur).get(name)
                if vals:
                    return vals
        return self._stores(m, m.info.tree).get(name, [])

    def _stores(self, m: Mod, scope: ast.AST) -> dict[str, list[ast.expr]]:
        """What each name of ONE scope is assigned, in source order: the
        scope's own statements, not a def, class or lambda nested in it."""
        key = ("stores", id(m), id(scope))
        if key not in self._memo:
            out: dict[str, list[ast.expr]] = {}
            todo = list(ast.iter_child_nodes(scope))
            while todo:
                n = todo.pop()
                if isinstance(n, NESTED):
                    continue
                if isinstance(n, ast.Assign):
                    for t in n.targets:
                        if isinstance(t, ast.Name):
                            out.setdefault(t.id, []).append(n.value)
                elif (isinstance(n, ast.AnnAssign) and n.value is not None
                      and isinstance(n.target, ast.Name)):
                    out.setdefault(n.target.id, []).append(n.value)
                elif isinstance(n, ast.NamedExpr) and isinstance(n.target, ast.Name):
                    out.setdefault(n.target.id, []).append(n.value)
                todo.extend(ast.iter_child_nodes(n))
            for vals in out.values():
                vals.sort(key=lambda v: (v.lineno, v.col_offset))
            self._memo[key] = out
        return self._memo[key]

    def _shadowed(self, m: Mod, at: ast.AST, name: str) -> bool:
        """Whether an enclosing function binds `name` itself, so a bare call
        of it is not a call of the module's def of that name."""
        cur = at
        while cur in m.info.parents:
            cur = m.info.parents[cur]
            d = m.by_node.get(cur)
            if d is not None and name in m.binds[d]:
                return True
        return False

    def _class_denoted(self, m: Mod, node: ast.AST, _seen: tuple = ()) -> tuple[Mod, str] | None:
        """The module and name of the class a NAME denotes where `node`
        stands: a from-import, an alias assignment, the module's own class, a
        module attribute, or a module attribute of a module (`ref.am.X`)."""
        text = ast.unparse(node)
        if text in _seen:
            return None
        parts = text.split(".")
        if len(parts) == 1:
            for v in self._assigned(m, node, text):
                r = self._class_denoted(m, v, _seen + (text,))
                if r is not None:
                    return r
            ent = m.info.from_import(text, node)
            if ent is not None:
                t = self._mod(ent[0])
                return (t, ent[1]) if t is not None else None
            return (m, text) if text in m.classes else None
        if len(parts) > 3:
            return None
        t = self._mod(m.info.resolve_head(parts[0], node) or "")
        if t is None:
            return None
        if len(parts) == 2:
            return (t, parts[1])
        inner = self._mod(t.info.imports.get(parts[1], ""))
        return (inner, parts[2]) if inner is not None else None

    def family_member(self, m: Mod, node: ast.AST) -> str | None:
        """The family class a name denotes: the root, one of its subclasses
        (by name), or None."""
        r = self._class_denoted(m, node)
        if r is not None and r[0] is self.est and r[1] in self.members:
            return r[1]
        return None

    def _denotes_estimate_class(self, m: Mod, node: ast.AST) -> bool:
        r = self._class_denoted(m, node)
        return r is not None and r[0] is self.est and r[1] == self.estimate_class

    def _handler_info(self, m: Mod, h: ast.ExceptHandler) -> dict:
        """What one `except` catches. `catches`: how it stops the family once
        resolved through the module's bindings (bare / ancestor / root), and
        None for a subclass alone -- `except NonFiniteAudio` does not catch
        `InsufficientEvidence`. `walk_covers`: what the #1763 walk read, by
        name alone. `reraises`: the handler puts the exception back."""
        out = dict(line=h.lineno, type=ast.unparse(h.type) if h.type is not None else "<bare>",
                   catches=None, walk_covers=False, reraises=_reraises(h),
                   refused=[], subclass_only=[])
        if h.type is None:
            out.update(catches="bare", walk_covers=True)
            return out
        for p in (h.type.elts if isinstance(h.type, ast.Tuple) else [h.type]):
            text = ast.unparse(p)
            if text == REFUSED or text.endswith(f".{REFUSED}"):
                out["refused"].append(text)
            if text.split(".")[-1] in WALK_COVERS:
                out["walk_covers"] = True
            if text in ANCESTORS or (text.startswith("builtins.")
                                    and text.split(".")[-1] in ANCESTORS):
                out["catches"] = out["catches"] or "ancestor"
            member = self.family_member(m, p)
            if member == FAMILY_ROOT:
                out["catches"] = out["catches"] or "root"
            elif member is not None:
                out["subclass_only"].append(text)
        return out

    # -- what a raise lets out -------------------------------------------------

    def _raise_node(self, m: Mod, n: ast.Raise) -> str | None:
        """The family class a `raise` NAMES by any binding, or None. A bare
        `raise` names nothing: whether it lets the family out is a question
        about the handler it sits in, asked in `_handler_info`."""
        exc = n.exc
        if exc is None:
            return None
        return self.family_member(m, exc.func if isinstance(exc, ast.Call) else exc)

    def _pass_raises(self) -> None:
        """Per def, the family classes its own subtree raises, by line."""
        raised: dict[Def, dict[int, str]] = {}
        for m in self.mods:
            for n in m.raises:
                named = self._raise_node(m, n)
                if named is not None:
                    for d in self._chain(m, n):
                        raised.setdefault(d, {})[n.lineno] = named
        self._raised = raised

    def _raise_items(self, d: Def) -> list[int]:
        """The lines on which `d`'s own subtree raises the family."""
        return sorted(self._raised.get(d, ()))

    # -- how a call is resolved -----------------------------------------------

    def _mod(self, stem: str) -> Mod | None:
        found = self.by_stem.get(stem, ())
        return self._by_info[found[0]] if len(found) == 1 else None

    def _in_module(self, m: Mod, cls: str | None, name: str, _seen: tuple = ()) -> Def | None:
        """The def `name` of module `m` -- of class `cls` when given -- or of
        the module `m` re-exports it from."""
        if m.rel in _seen:
            return None
        if cls is not None:
            d = m.methods.get((cls, name))
            if d is not None:
                return d
            c = m.classes.get(cls)
            for base in (c.bases if c is not None else ()):
                if isinstance(base, ast.Name) and base.id in m.classes and base.id != cls:
                    d = self._in_module(m, base.id, name)
                    if d is not None:
                        return d
            return None
        d = m.top.get(name)
        if d is not None:
            return d
        ent = m.info.from_imports.get(name)
        t = self._mod(ent[0]) if ent else None
        return self._in_module(t, None, ent[1], _seen + (m.rel,)) if t is not None else None

    def resolve_call(self, m: Mod, call: ast.Call) -> tuple[Def | None, str]:
        """(def called, how it was found), or (None, why not). `how`:
        import, from-import, local, class, re-export, self."""
        f = call.func
        if isinstance(f, ast.Name):
            ent = m.info.from_import(f.id, call)
            if ent is not None:
                t = self._mod(ent[0])
                d = self._in_module(t, None, ent[1]) if t is not None else None
                return (d, "from-import") if d is not None else (None, "not-indexed")
            if self._shadowed(m, call, f.id):
                return None, "local-name"
            d = self._local(m, call, f.id)
            return (d, "local") if d is not None else (None, "unresolved")
        if not isinstance(f, ast.Attribute):
            return None, "not-a-name"
        v = f.value
        if isinstance(v, ast.Name):
            stem = m.info.resolve_head(v.id, call)
            if stem is not None:
                t = self._mod(stem)
                d = self._in_module(t, None, f.attr) if t is not None else None
                return (d, "import") if d is not None else (None, "not-indexed")
            if v.id in m.classes:
                d = self._in_module(m, v.id, f.attr)
                return (d, "class") if d is not None else (None, "unresolved")
            if v.id == "self":
                cls = self._enclosing_class(m, call)
                d = self._in_module(m, cls.name, f.attr) if cls is not None else None
                if d is not None:
                    return d, "self"
            return None, "untyped-receiver"
        if isinstance(v, ast.Attribute) and isinstance(v.value, ast.Name):
            stem = m.info.resolve_head(v.value.id, call)
            t = self._mod(stem) if stem is not None else None
            if t is None:
                return None, "not-indexed"
            d = self._in_module(t, v.attr, f.attr)
            if d is not None:
                return d, "class"
            inner = self._mod(t.info.imports.get(v.attr, ""))
            d = self._in_module(inner, None, f.attr) if inner is not None else None
            return (d, "re-export") if d is not None else (None, "not-indexed")
        return None, "untyped-receiver"

    def _local(self, m: Mod, at: ast.AST, name: str) -> Def | None:
        """The def `name` of the same module, innermost enclosing function
        first, then the top level."""
        cur = at
        while cur in m.info.parents:
            cur = m.info.parents[cur]
            if isinstance(cur, FUNC):
                d = m.child.get(cur, {}).get(name)
                if d is not None:
                    return d
        return m.top.get(name)

    def _enclosing_class(self, m: Mod, at: ast.AST) -> ast.ClassDef | None:
        cur = at
        while cur in m.info.parents:
            cur = m.info.parents[cur]
            if isinstance(cur, ast.ClassDef):
                return cur
        return None

    def _chain(self, m: Mod, node: ast.AST) -> tuple[Def, ...]:
        """The defs `node` is written in, innermost first."""
        out = []
        cur = node
        while cur in m.info.parents:
            cur = m.info.parents[cur]
            d = m.by_node.get(cur)
            if d is not None:
                out.append(d)
        return tuple(out)

    def _call(self, m: Mod, node: ast.Call) -> dict:
        """Everything the census reads of ONE call, resolved once: the defs
        it is written in, whether it is an attributed `.require()`, and the
        defs it can reach -- one when the AST names it, every def of the
        method's name when the receiver cannot be typed, none otherwise."""
        key = ("call", id(m), id(node))
        if key in self._memo:
            return self._memo[key]
        info = dict(chain=self._chain(m, node), require=False, defs=[], how=None, receiver=None)
        self._memo[key] = info
        f = node.func
        if isinstance(f, ast.Attribute) and f.attr == REQUIRE:
            if self._is_estimate(m, f.value):
                info.update(require=True, defs=[self.require_def], how="estimate")
            else:
                info["receiver"] = ast.unparse(f.value)
            return info
        d, how = self.resolve_call(m, node)
        if d is None and isinstance(f, ast.Attribute) and self._rooted_at_a_value(m, node):
            d, how = self._method_index.get(f.attr), "untyped-receiver"
        if d:
            info.update(defs=d if isinstance(d, list) else [d], how=how)
        return info

    def _rooted_at_a_value(self, m: Mod, call: ast.Call) -> bool:
        """Whether a method call's receiver is rooted at something that can
        be an instance: not a module binding (`os.path.join` is not a method
        call) and not `self`, which `resolve_call` reads precisely."""
        root = call.func.value
        while isinstance(root, ast.Attribute):
            root = root.value
        return not (isinstance(root, ast.Name)
                    and (root.id == "self" or m.info.resolve_head(root.id, call) is not None))

    # -- what is an Estimate ---------------------------------------------------

    def _is_estimate(self, m: Mod, expr: ast.AST) -> bool:
        """Whether `expr` is an Estimate of the estimator module."""
        key = ("estimate", id(m), id(expr))
        if key not in self._memo:
            self._memo[key] = False
            self._memo[key] = self._typed_estimate(m, expr)
        return self._memo[key]

    def _typed_estimate(self, m: Mod, expr: ast.AST) -> bool:
        if isinstance(expr, ast.Call):
            f = expr.func
            if isinstance(f, (ast.Name, ast.Attribute)) and self._denotes_estimate_class(m, f):
                return True
            d, _ = self.resolve_call(m, expr)
            return d is not None and self.returns_estimate(d)
        if isinstance(expr, ast.Name):
            return any(self._is_estimate(m, v) for v in self._assigned(m, expr, expr.id))
        return False

    def returns_estimate(self, d: Def) -> bool:
        """Whether `d` returns the estimator's Estimate: its return
        annotation names the class, or its body returns the class or a call
        to a def that does."""
        key = ("returns", id(d))
        if key not in self._memo:
            self._memo[key] = False
            self._memo[key] = self._returns_estimate(d)
        return self._memo[key]

    def _returns_estimate(self, d: Def) -> bool:
        n = d.node
        if n.returns is not None and self._denotes_estimate_class(d.mod, n.returns):
            return True
        for r in ast.walk(n):
            if isinstance(r, ast.Return) and isinstance(r.value, ast.Call):
                if self._denotes_estimate_class(d.mod, r.value.func):
                    return True
                t, _ = self.resolve_call(d.mod, r.value)
                if t is not None and self.returns_estimate(t):
                    return True
        return False

    # -- origins ---------------------------------------------------------------

    def _is_origin(self, d: Def) -> bool:
        return d in self._direct or d in self._one_hop

    def _origin_targets(self, info: dict) -> list[Def]:
        return [d for d in info["defs"] if self._is_origin(d)]

    def raises(self, d: Def) -> list[str]:
        """The family classes a call to `d` can let out."""
        if d in self._direct:
            return sorted(set(self._raised[d].values()))
        return sorted({c for e in self._one_hop.get(d, ()) if not e["guarded"]
                       for c in self.raises(e["target"])})

    def _guarded(self, m: Mod, at: ast.AST) -> bool:
        """Whether a `try` whose body holds `at`, in the function `at` is
        written in, has a handler that stops the family. One that re-raises
        does not."""
        key = ("guarded", id(m), id(at))
        if key not in self._memo:
            out = False
            cur = at
            while cur in m.info.parents and not out:
                prev, cur = cur, m.info.parents[cur]
                if isinstance(cur, FUNC + (ast.Lambda,)):
                    break
                if isinstance(cur, TRY) and prev in cur.body:
                    out = any((m.handlers.get(id(h)) or {}).get("catches")
                              and not m.handlers[id(h)]["reraises"] for h in cur.handlers)
            self._memo[key] = out
        return self._memo[key]

    def _walk_sees(self, m: Mod, call: ast.Call) -> bool:
        """Whether the #1763 walk credits `call`: a binding of the estimator
        module to one of its own raising top-level functions."""
        f = call.func
        if isinstance(f, ast.Name):
            ent = m.info.from_import(f.id, call)
            return bool(ent) and ent[0] == ESTIMATOR_STEM and self.est.top.get(ent[1]) in self._walk
        if isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name):
            return (m.info.resolve_head(f.value.id, call) == ESTIMATOR_STEM
                    and self.est.top.get(f.attr) in self._walk)
        return False

    def _pass_origins(self) -> None:
        """Direct origins, then one hop: a def with no direct raise whose own
        subtree has an edge to a direct origin or an attributed `.require()`."""
        direct: dict[Def, list[int]] = {}
        defs = self._walk if self.rules == "direct" else (d for m in self.mods for d in m.defs)
        for d in sorted(defs, key=lambda d: (d.mod.rel, d.node.lineno)):
            if self._raise_items(d):
                direct[d] = self._raise_items(d)
        self._direct = direct
        if self.rules == "direct":
            return
        edges_of: dict[Def, list[dict]] = {}
        for m in self.mods:
            for node in m.calls:
                info = self._call(m, node)
                target = self.require_def if info["require"] else next(
                    (t for t in info["defs"] if t in direct), None)
                if target is None:
                    continue
                edge = dict(line=node.lineno, kind="require" if info["require"] else "direct",
                            target=target, guarded=self._guarded(m, node))
                for d in info["chain"]:
                    if d not in direct and edge not in edges_of.setdefault(d, []):
                        edges_of[d].append(edge)
        for d, edges in edges_of.items():
            edges.sort(key=lambda e: (e["line"], e["target"].label))
            bucket = self._one_hop if any(not e["guarded"] for e in edges) else self._guarded_only
            bucket[d] = edges

    @property
    def direct(self) -> _ByLabel:
        return _ByLabel(self._direct)

    @property
    def one_hop(self) -> _ByLabel:
        return _ByLabel(self._one_hop, self._emit)

    @property
    def guarded_only(self) -> _ByLabel:
        return _ByLabel(self._guarded_only, self._emit)

    def _emit(self, edges: list[dict]) -> list[dict]:
        return [dict(line=e["line"], kind=e["kind"], callee=e["target"].label,
                     guarded=e["guarded"], classes=self.raises(e["target"])) for e in edges]

    # -- call sites ------------------------------------------------------------

    @staticmethod
    def _fn_at(chain: tuple[Def, ...]) -> str:
        return ".".join(d.node.name for d in reversed(chain)) if chain else MODULE

    def _pass_sites(self) -> None:
        for m in self.mods:
            m.walk = any(self._walk_sees(m, node) for node in m.calls)
            sites = []
            for node in m.calls:
                info = self._call(m, node)
                if info["receiver"] is not None:
                    if not m.is_test:
                        self.unresolved_requires.append(dict(
                            path=m.rel, line=node.lineno, fn=self._fn_at(info["chain"]),
                            receiver=info["receiver"]))
                    continue
                if self.rules == "direct":
                    if not self._walk_sees(m, node):
                        continue
                    f = node.func
                    d = self.est.top.get(f.attr if isinstance(f, ast.Attribute) else f.id)
                    targets, via, how, through = [d], "direct", info["how"], []
                else:
                    targets = self._origin_targets(info)
                    if not targets:
                        continue
                    via = ("require" if info["require"] else
                           "direct" if all(t in self._direct for t in targets) else "one-hop")
                    how = info["how"]
                    through = sorted({e["target"].label + (" [guarded]" if e["guarded"] else "")
                                      for t in targets for e in self._one_hop.get(t, ())})
                sites.append(dict(line=node.lineno, col=node.col_offset,
                                  fn=self._fn_at(info["chain"]),
                                  callees=[t.label for t in targets], via=via, how=how,
                                  through=through,
                                  classes=sorted({c for t in targets for c in self.raises(t)}),
                                  guarded=self._guarded(m, node)))
            sites.sort(key=lambda s: (s["line"], s["col"], s["callees"], s["through"]))
            m.sites = sites

    def _pass_chains(self) -> None:
        """Every def that calls a one-hop origin (two hops from the
        refusal), and every site that calls one of those."""
        if self.rules == "direct":
            return
        chain_defs: dict[Def, Def] = {}
        for m in self.mods:
            if m.is_test:
                continue
            for node in m.calls:
                info = self._call(m, node)
                for t in info["defs"]:
                    if t not in self._one_hop:
                        continue
                    for d in info["chain"]:
                        rec = dict(caller=d.label, path=m.rel, line=node.lineno)
                        if rec not in self._chains.setdefault(t, []):
                            self._chains[t].append(rec)
                        chain_defs.setdefault(d, t)
        seen: set[tuple] = set()
        for m in self.mods:
            if m.is_test:
                continue
            for node in m.calls:
                info = self._call(m, node)
                for d in info["defs"]:
                    if d in chain_defs and (m.rel, node.lineno, d.label) not in seen:
                        seen.add((m.rel, node.lineno, d.label))
                        self.chain_sites.append(dict(
                            path=m.rel, line=node.lineno, fn=self._fn_at(info["chain"]),
                            callee=d.label, through=chain_defs[d].label))
        self.chain_sites.sort(key=lambda s: (s["path"], s["line"], s["callee"]))

    # -- the report ------------------------------------------------------------

    @property
    def candidates(self) -> list[dict]:
        return [r for r in self.rows if r["candidate"]]

    def counts(self) -> dict:
        rows = self.rows
        return dict(
            origins_direct=len(self._direct),
            origins_one_hop=len(self._one_hop),
            origins_guarded_only=len(self._guarded_only),
            callers_transitive=len([r for r in rows if r["n_sites"]]),
            candidates_transitive=len([r for r in rows if r["candidate"]]),
            candidates_new=len([r for r in rows if r["new"]]),
            callers_direct=len([r for r in rows if r["walk"]]),
            candidates_direct=len([r for r in rows if r["walk_candidate"]]),
            unresolved_requires=len(self.unresolved_requires),
            chain_defs=len({c["caller"] for cs in self._chains.values() for c in cs}),
            chain_sites=len(self.chain_sites),
        )

    def to_json(self, rev: str = UNKNOWN, origin_main: str = UNKNOWN,
                behind: int | None = None) -> dict:
        def by_label(items):
            return sorted(items, key=lambda item: item[0].label)

        return dict(
            tool="estimator_refusal_census", rules=self.rules, rev=rev, origin_main=origin_main,
            behind_origin_main=behind, files=len(self.files), estimator=self.est.rel,
            family=sorted(self.members), estimate_class=self.estimate_class,
            origins=dict(
                direct={d.label: dict(lines=v, raises=self.raises(d))
                        for d, v in by_label(self._direct.items())},
                one_hop={d.label: self._emit(v) for d, v in by_label(self._one_hop.items())},
                guarded_only={d.label: self._emit(v)
                              for d, v in by_label(self._guarded_only.items())}),
            counts=self.counts(),
            rows=self.rows,
            blind=dict(
                unresolved_requires=self.unresolved_requires,
                chain_sites=self.chain_sites,
                chains={t.label: cs for t, cs in by_label(self._chains.items())},
                test_files=[r["path"] for r in self.rows if r["is_test"] and r["n_sites"]]),
        )

    def report(self, rev: str = UNKNOWN, origin_main: str = UNKNOWN,
               behind: int | None = None) -> str:
        c = self.counts()
        rows = self.rows
        out = [f"# estimator_refusal_census @ {rev}; origin/main {origin_main}; "
               f"{UNKNOWN if behind is None else behind} behind origin/main"]
        if behind:
            out.append(f"!! THIS TREE IS {behind} COMMITS BEHIND origin/main. The rows below "
                       f"describe that older tree, not main.")
        out.append("# CANDIDATES, NOT DEFECTS: read off the AST. Whether any input makes the "
                   "estimator refuse is UNTRACED for every row below.")
        out.append(f"rules={self.rules}: origins {c['origins_direct']} direct, "
                   f"{c['origins_one_hop']} one-hop ({c['origins_guarded_only']} more fully "
                   f"guarded, not origins); unresolved .require() {c['unresolved_requires']}; "
                   f"chains beyond one hop: {c['chain_defs']} defs calling a one-hop origin, "
                   f"{c['chain_sites']} sites reaching one")
        out.append(f"rules=direct (the #1763 walk): {c['callers_direct']} callers, "
                   f"{c['candidates_direct']} candidates   rules=transitive: "
                   f"{c['callers_transitive']} callers, {c['candidates_transitive']} candidates "
                   f"({c['candidates_new']} new)")
        out.append(f"CANDIDATES ({c['candidates_transitive']}) -- a Refused-only boundary: an "
                   "InsufficientEvidence would pass every handler this module defines")
        for r in rows:
            if not r["candidate"]:
                continue
            again = ""
            if r["reraising_handlers"]:
                again = f"  re-raises={','.join(r['reraising_handlers'])}"
            out.append(f"  {r['path']}  entry={r['entry']}{'  NEW' if r['new'] else ''}  "
                       f"raises={'|'.join(r['raises'])}  "
                       f"refused={','.join(r['refused_handlers']) or 'none'}{again}")
            for s in r["sites"]:
                through = f"  through {' | '.join(s['through'])}" if s["through"] else ""
                out.append(f"    :{s['line']:<5d} {s['fn']:<30s} {s['via']:<8s} {s['how']:<18s} "
                           f"{', '.join(s['callees'])}{through}")
        reached = [r for r in rows if r["n_sites"] and not r["is_test"]]
        covered = [r for r in reached if r["printed"] and r["family_handlers"]]
        out.append(f"COVERED ({len(covered)}) -- a handler stops the family; the call cannot "
                   "leave as an InsufficientEvidence")
        out.extend(f"  {r['path']}  {', '.join(r['family_handlers'])}" for r in covered)
        quiet = [r for r in reached if not r["printed"]]
        out.append(f"NOT THE SHAPE ({len(quiet)}) -- the call and no entry point or Refused "
                   "handler at all: #1763's criterion is a Refused-only boundary")
        for r in quiet:
            stops = (f"  (stops it: {', '.join(r['family_handlers'])})"
                     if r["family_handlers"] else "")
            out.append(f"  {r['path']}  {r['n_sites']} sites{stops}")
        tests = [r["path"] for r in rows if r["is_test"] and r["n_sites"]]
        out.append(f"TEST FILES ({len(tests)}) -- under pytest an InsufficientEvidence fails the "
                   "test, the conservative outcome; never candidates")
        out.append("  " + ", ".join(tests) if tests else "  none")
        out.append(f"UNRESOLVED .require() ({c['unresolved_requires']}) -- the receiver is not "
                   "typed as an audio_measure.Estimate; a blind spot, listed not guessed")
        out.extend(f"  {u['path']}:{u['line']}  {u['fn']}  {u['receiver']}.require()"
                   for u in self.unresolved_requires)
        out.append(f"CHAINS BEYOND ONE HOP ({c['chain_defs']} defs calling a one-hop origin, "
                   f"{c['chain_sites']} sites) -- two hops from the refusal; not candidates "
                   "(#1782: out of scope). Every def and site in --json")
        reaching: dict[str, list[dict]] = {}
        for site in self.chain_sites:
            reaching.setdefault(site["through"], []).append(site)
        for t in sorted(self._chains, key=lambda d: (-len(reaching.get(d.label, ())), d.label)):
            label = t.label
            callers = {x["caller"] for x in self._chains[t]}
            where = sorted({x["path"] for x in self._chains[t]})
            sites = reaching.get(label, [])
            reach = sorted({s["path"] for s in sites})
            more = f" +{len(reach) - 8} more" if len(reach) > 8 else ""
            reached = f"; reached from {len(sites)} sites in {', '.join(reach[:8])}{more}"
            out.append(f"  {label}  <- {len(callers)} defs in {', '.join(where)}"
                       + (reached if sites else ""))
        return "\n".join(out)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=" ".join(__doc__.strip().split("\n\n")[0].split("\n")),
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    ap.add_argument("--rules", choices=RULES, default="transitive",
                    help="direct: the #1763 walk alone; transitive (default): plus .require() "
                         "and one hop")
    a = ap.parse_args(argv)
    try:
        census = Census(tracked_files(), rules=a.rules)
    except PreconditionRefused as e:
        print(f"REFUSED: {e}", file=sys.stderr)
        return 2
    rev = _git("rev-parse", "--short", "HEAD") or UNKNOWN
    origin_main = _git("rev-parse", "--short", "origin/main") or UNKNOWN
    count = _git("rev-list", "--count", "HEAD..origin/main")
    behind = int(count) if count.isdigit() else None
    if a.json:
        print(json.dumps(census.to_json(rev, origin_main, behind), indent=1))
    else:
        print(census.report(rev, origin_main, behind))
    return 0


if __name__ == "__main__":
    sys.exit(main())
