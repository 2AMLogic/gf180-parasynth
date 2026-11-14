#!/usr/bin/env python3
"""Which callers read an `Estimate.value` without checking `.ok`, and what does
a refusal actually DO there? (the class-wide search behind #801)

    python3 tools/probes/estimate_consumer_audit.py             # the triage table
    python3 tools/probes/estimate_consumer_audit.py --list      # every unguarded site
    python3 tools/probes/estimate_consumer_audit.py --self-test # the validation cases
    python3 tools/probes/estimate_consumer_audit.py --defeat    # the input that defeats it

WHY THIS EXISTS

#801 found one site -- `tools/measure_promoted_bands.py`'s second, duplicated
period grid -- where an estimator's refusal is absorbed by `max()` and reported
as a plausible worst-case percentage. `model/audio_measure.py:365` states the
contract it violates: **"`value` is meaningless unless `ok`"**.

CLAUDE.md requires that closing a defect includes searching for its *class*, not
its one call site, and recording what the search found. This is that search,
written as a tool so the answer can be re-derived instead of believed.

It is a THIRD axis, not a duplicate of the two audits already here:

    tools/nan_guard_audit.py       (#134)  guards INSIDE an estimator: does a
                                          NaN walk through its comparisons?
    tools/vacuous_guard_audit.py   (#620)  assertions over an EMPTY population
    this probe                     (#801)  CONSUMERS of an `Estimate`: is `.ok`
                                          checked before `.value` is read?

#801's own body names the gap: `vacuous_guard_audit` models "a population
emptied after the assertion", not a population **silently shrunken** from
twelve cases to three.

THE PREMISE THIS ASSERTS RATHER THAN ASSUMES

The severity of an unguarded read depends entirely on what a refusal carries in
`value`, and that is checkable: every refusal path in `model/audio_measure.py`
constructs `Estimate(None, False, ...)`, so `value` on a real refusal is `None`,
never `nan`. That makes an unguarded read **loud** -- `None` in arithmetic is a
`TypeError`, which is NO VERDICT, not a false green.

`refusal_encoding_gaps()` asserts it, and the audit REFUSES rather than reports
if it ever stops being true, because the whole classification below would then
be wrong in the unsafe direction. `REFUSED` is a first-class outcome here
(CLAUDE.md); a tool that answers when it cannot is worse than one that is absent.

So the table separates two consequences, and the distinction is the finding:

    unguarded-loud       a real refusal raises TypeError here. Bad, but it
                         cannot be mistaken for data.
    unguarded-absorbed   a real refusal ALSO raises here -- but the read flows
                         into `max`/`min`/`mean`, so an estimator STUBBED or
                         MUTATED to return `(nan, ok=False)` is dropped
                         silently and the aggregate reports a better-than-true
                         number. This is #801's case C (0.034 % from three of
                         twelve cases), and it is why that site is invisible to
                         a control set built out of nan-returning stubs.

WHAT THIS CANNOT SEE (read before quoting a count)

  * **The guard test is textual and per-name.** A site counts as guarded when
    `<name>.ok` or `<name>.require(` appears anywhere in the enclosing function
    for the same name. A function that checks `a.ok` and then reads `b.value`
    is caught (different name); a function that checks `a.ok` on one branch and
    reads `a.value` on another is NOT. See `--defeat`.
  * **Producers are only those it can derive** from the files in `SOURCES`. An
    `Estimate` handed through a wrapper, a dict, a tuple or a `**kwargs` loses
    its provenance and the consumer is not classified at all.
  * **It does not decide intent.** Several unguarded sites here are deliberate:
    `tools/test_measure_repeatability.py` names its variable `unguarded`
    precisely to record what the guard prevents. Intent is the human half.
  * **Only code inside a function body is walked.** A module-level `.value`
    read is invisible. Measured rather than assumed when this was written:
    there are **0** such reads in the repository, so the blind spot is empty
    today -- but it is not guarded against, and a new one would not be seen.
"""
from __future__ import annotations

import argparse
import ast
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent.parent

#: Where `Estimate` producers are defined. The producer set is DERIVED from
#: these, not listed, so an estimator added tomorrow is covered without an edit.
SOURCES = ("model/audio_measure.py", "model/promoted_measures.py")

#: Aggregates that drop a non-finite member instead of propagating it, so a
#: `nan`-valued refusal shrinks the population without shrinking the answer.
ABSORBING = frozenset({"max", "min", "mean", "median", "average", "sorted", "fsum"})

#: A consumer counts as guarded when one of these is applied to the same name.
GUARD_ATTRS = ("ok", "require", "outside_domain")

#: Below this many derived producers, the derivation has broken (a moved file, a
#: renamed dataclass) and every "unguarded" count would be a false zero.
MIN_PRODUCERS = 20


class Refused(Exception):
    """A precondition of the audit does not hold, so it has no answer to give."""


class _Kind:
    GUARDED = "guarded"
    LOUD = "unguarded-loud"
    ABSORBED = "unguarded-absorbed"


def _call_name(call: ast.Call) -> str | None:
    fn = call.func
    if isinstance(fn, ast.Attribute):
        return fn.attr
    if isinstance(fn, ast.Name):
        return fn.id
    return None


def _parse(rel: str, root: pathlib.Path) -> ast.Module:
    path = root / rel
    if not path.exists():
        raise Refused(f"{rel} does not exist under {root}")
    return ast.parse(path.read_text(encoding="utf-8"))


def producers(root: pathlib.Path = ROOT) -> set[str]:
    """Every function that returns an `Estimate`, derived from `SOURCES`.

    A function qualifies by its return annotation OR by constructing an
    `Estimate`/`_fail` in a `return`. `_fail` and `refuse` are dropped: they are
    the refusal constructors, not estimators, and their callers are the
    estimators already counted."""
    found: set[str] = set()
    for rel in SOURCES:
        for node in ast.walk(_parse(rel, root)):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            ann = ast.unparse(node.returns) if node.returns else ""
            body = ast.unparse(node)
            if "Estimate" in ann or re.search(r"return (Estimate\(|_fail\()", body):
                found.add(node.name)
    return found - {"_fail", "refuse", "check", "__repr__", "require"}


def refusal_encoding_gaps(root: pathlib.Path = ROOT) -> list[str]:
    """Every `Estimate(...)` construction that refuses with a value that is not
    `None` -- i.e. every way a refusal could arrive as a `nan` and be absorbed
    silently instead of raising.

    This is the audit's precondition. An empty list is what licenses the word
    `loud` in the table below."""
    gaps: list[str] = []
    for rel in SOURCES:
        tree = _parse(rel, root)
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call) and _call_name(node) == "Estimate"):
                continue
            # ok is the 2nd positional argument, or the `ok=` keyword.
            ok_node = node.args[1] if len(node.args) >= 2 else None
            for kw in node.keywords:
                if kw.arg == "ok":
                    ok_node = kw.value
            if ok_node is None:
                continue                      # ok defaults to True: not a refusal
            if not (isinstance(ok_node, ast.Constant) and ok_node.value is False):
                continue                      # not a literal refusal
            val = node.args[0] if node.args else None
            for kw in node.keywords:
                if kw.arg == "value":
                    val = kw.value
            if not (isinstance(val, ast.Constant) and val.value is None):
                shown = ast.unparse(val) if val is not None else "<missing>"
                gaps.append(f"{rel}:{node.lineno} refuses with value={shown}, not None")
    return gaps


class Site:
    """One read of `.value` off an `Estimate` whose producer could be named."""

    def __init__(self, rel, lineno, func, name, producer, kind, via):
        self.rel, self.lineno, self.func = rel, lineno, func
        self.name, self.producer, self.kind, self.via = name, producer, kind, via

    def __str__(self) -> str:
        via = f" into {self.via}()" if self.via else ""
        return (f"{self.kind:<20} {self.rel}:{self.lineno}  {self.func}()  "
                f"{self.name} <- {self.producer}{via}")


def _absorbing_ancestor(fnode: ast.AST, target: ast.AST) -> str | None:
    """The name of the nearest `max`/`min`/`mean`-style call that `target` feeds,
    or None. Walks parents, so `max(... for ...)` and `max(list_of_values)` built
    one `.append` at a time are BOTH missed when the append is in another
    function -- stated, not hidden."""
    parents: dict[int, tuple[ast.AST, str | None]] = {}
    for node in ast.walk(fnode):
        for child in ast.iter_child_nodes(node):
            nm = _call_name(node) if isinstance(node, ast.Call) else None
            parents[id(child)] = (node, nm)
    cur = target
    while id(cur) in parents:
        cur, nm = parents[id(cur)]
        if nm in ABSORBING:
            return nm
    return None


def audit(root: pathlib.Path = ROOT) -> list[Site]:
    """Classify every derivable `Estimate.value` read in the repository.

    REFUSES if the producer derivation collapsed or if a refusal can carry a
    non-`None` value, because either one silently inverts the result."""
    prod = producers(root)
    if len(prod) < MIN_PRODUCERS:
        raise Refused(
            f"derived only {len(prod)} Estimate producers from {SOURCES} "
            f"(expected >= {MIN_PRODUCERS}). The derivation is broken, so every "
            f"'unguarded' count this would print is a false zero.")
    gaps = refusal_encoding_gaps(root)
    if gaps:
        raise Refused(
            "a refusal can carry a non-None value, so an unguarded read is no "
            "longer guaranteed loud and this audit's 'loud' column would be "
            "wrong in the unsafe direction:\n  " + "\n  ".join(gaps))

    import subprocess
    files = [f for f in subprocess.run(
        ["git", "-C", str(root), "ls-files", "*.py"],
        capture_output=True, text=True, check=True).stdout.split()
        if not f.startswith((".loom/", ".claude/", ".agents/"))]

    sites: list[Site] = []
    for rel in files:
        path = root / rel
        try:
            src = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        if ".value" not in src:
            continue
        try:
            tree = ast.parse(src)
        except SyntaxError:
            continue
        for fnode in ast.walk(tree):
            if not isinstance(fnode, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            ftext = ast.unparse(fnode)
            bound: dict[str, str] = {}
            for node in ast.walk(fnode):
                if (isinstance(node, ast.Assign) and isinstance(node.value, ast.Call)
                        and _call_name(node.value) in prod):
                    for t in node.targets:
                        if isinstance(t, ast.Name):
                            bound[t.id] = _call_name(node.value)
            seen: set[tuple[int, str]] = set()
            for node in ast.walk(fnode):
                if not (isinstance(node, ast.Attribute) and node.attr == "value"):
                    continue
                base = node.value
                if isinstance(base, ast.Name) and base.id in bound:
                    name, producer = base.id, bound[base.id]
                    guarded = any(
                        re.search(rf"\b{re.escape(name)}\.{a}\b", ftext)
                        for a in GUARD_ATTRS)
                elif isinstance(base, ast.Call) and _call_name(base) in prod:
                    name, producer = f"{_call_name(base)}(...)", _call_name(base)
                    guarded = False      # nothing to hang a guard on: read inline
                else:
                    continue
                key = (node.lineno, name)
                if key in seen:
                    continue
                seen.add(key)
                via = _absorbing_ancestor(fnode, node)
                if guarded:
                    kind = _Kind.GUARDED
                elif via:
                    kind = _Kind.ABSORBED
                else:
                    kind = _Kind.LOUD
                sites.append(Site(rel, node.lineno, fnode.name, name, producer,
                                  kind, via))
    return sites


#: Sites on `origin/main` whose classification is known by hand, so a refactor
#: of this probe that silently stops finding things fails instead of passing.
#: `(rel, lineno, kind)` -- #801's site is the reason the probe exists and is
#: the one case that MUST stay found.
VALIDATION = (
    ("tools/measure_promoted_bands.py", 616, _Kind.ABSORBED,
     "#801's site: `max()` over a second, duplicated 12-case period grid"),
    ("tools/test_measure_repeatability.py", 132, _Kind.LOUD,
     "deliberate: the variable is NAMED `unguarded`, to record what the "
     "length guard prevents"),
    ("tools/measure_promoted_bands.py", 142, _Kind.GUARDED,
     "the FIRST copy of the same grid, which does test `.ok` -- the contrast "
     "that makes #801 a duplication finding and not a style note"),
)


def self_test(root: pathlib.Path = ROOT) -> int:
    """Run the validation cases. Exit 1 on any miss."""
    try:
        sites = audit(root)
    except Refused as exc:
        print(f"REFUSED  {exc}")
        return 2
    index = {(s.rel, s.lineno): s for s in sites}
    bad = 0
    for rel, lineno, want, why in VALIDATION:
        got = index.get((rel, lineno))
        if got is None:
            print(f"MISS     {rel}:{lineno} not found at all (expected {want})")
            print(f"         {why}")
            bad += 1
        elif got.kind != want:
            print(f"WRONG    {rel}:{lineno} classified {got.kind}, expected {want}")
            print(f"         {why}")
            bad += 1
        else:
            print(f"OK       {rel}:{lineno} {want}")
    print()
    if bad:
        print(f"{bad} of {len(VALIDATION)} validation cases failed. The line "
              f"numbers are pinned to origin/main and drift when those files "
              f"change -- re-derive them before trusting a count from this probe.")
        return 1
    print(f"{len(VALIDATION)}/{len(VALIDATION)} validation cases hold.")
    return 0


DEFEAT = '''\
WHAT SATISFIES THIS CHECK WHILE VIOLATING ITS INTENT
(`docs/verification-rules.md` rule 8 -- a guard ships with the input that defeats it)

1. A per-name guard on the WRONG BRANCH. This is reported `guarded` and is not:

       e = am.dominant_period_ms(x, sr, band)
       if e.ok:
           record(e.value)              # fine
       else:
           record(e.value * 1e3)        # TypeError, and this probe says guarded

   `e.ok` is in the function text for the name `e`, which is all the test asks.

2. `.ok` INSIDE A STRING. Also reported `guarded`:

       assert abs(e.value) < 3.0, "e.ok was asserted upstream"

3. A LAUNDERED Estimate. Not reported at all, because the producer cannot be
   named -- the commonest real escape:

       def worst(estimates):            # no producer call in this function
           return max(x.value for x in estimates)

4. A POPULATION SHRUNK ACROSS FUNCTIONS. `unguarded-absorbed` needs the
   `.value` read and the `max()` in ONE function. Append in a loop, aggregate in
   the caller, and #801's exact shape is invisible here:

       rows.append(e.value if e.ok else float("nan"))   # one function
       ...
       worst = max(rows)                                # another

   Case 4 is the one that matters: it is #801's defect with the two halves moved
   apart, and it is why this probe is a triage list, not a gate.
'''


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--list", action="store_true",
                    help="print every unguarded site, not just the counts")
    ap.add_argument("--self-test", action="store_true",
                    help="run the pinned validation cases; exit 1 on a miss")
    ap.add_argument("--defeat", action="store_true",
                    help="print the inputs that satisfy this check while "
                         "violating its intent")
    args = ap.parse_args(argv)

    if args.defeat:
        print(DEFEAT)
        return 0
    if args.self_test:
        return self_test()

    try:
        sites = audit()
    except Refused as exc:
        print(f"REFUSED  {exc}")
        return 2

    counts = {_Kind.GUARDED: 0, _Kind.LOUD: 0, _Kind.ABSORBED: 0}
    for s in sites:
        counts[s.kind] += 1
    total = len(sites)
    print(f"{total} derivable `Estimate.value` reads across the repository")
    print(f"  {counts[_Kind.GUARDED]:>4}  guarded             `.ok`/`.require` on the same name")
    print(f"  {counts[_Kind.LOUD]:>4}  unguarded-loud      a real refusal raises TypeError (NO VERDICT)")
    print(f"  {counts[_Kind.ABSORBED]:>4}  unguarded-absorbed feeds max/min/mean: a nan-returning STUB")
    print(f"        {'':>14}      is dropped silently -- #801's class")
    print()
    print(f"Refusal encoding: every refusal in {', '.join(SOURCES)} carries "
          f"value=None (asserted, not assumed).")
    print("Intent is NOT decided here: several unguarded sites are deliberate "
          "controls. Run --defeat before quoting a count.")
    if args.list:
        print()
        for s in sites:
            if s.kind != _Kind.GUARDED:
                print(s)
    return 0


if __name__ == "__main__":
    sys.exit(main())
