#!/usr/bin/env python3
"""Measure the precision of Loom's Hermit "stateless ceremony" detector.

WHY THIS FILE EXISTS. The detector shipped in Loom's
`defaults/roles/hermit-patterns.md` (installed here as
`.claude/commands/loom/hermit-patterns.md`, section "9. Stateless Ceremony")
flags classes that have "no instance state" as candidates for conversion to
module-level functions. Run unmodified over this repository at 344ed6e7 it
flagged 237 classes and NONE of them was a true positive. Those numbers are
cited in rjwalters/loom#13884; this is the code that produced them.

It is a RECORD OF A TOOL THAT LOOKED RIGHT AND WAS WRONG. The detector reads
as rigorous -- it is AST-based, it carries three documented exclusions, and a
previous false-positive report (rjwalters/loom#3198) was closed by adding
them. The form of rigour was there; the precision was zero. Keeping the
measurement means the next person can re-run it against a patched detector
instead of re-deriving the argument.

TWO ROOT CAUSES, both reproduced by `classify`:

  1. The state test matches only `ast.Assign` whose target is directly an
     `ast.Attribute`, so it misses `self.a, self.b = ...` (tuple target),
     `self.x: T = ...` (AnnAssign) and `self.x += ...` (AugAssign). Classes
     with ordinary instance state are reported as having none -- the
     dangerous direction, because acting on the proposal produces wrong code.

  2. There is no type-based exclusion, so exception subclasses, dataclasses,
     NamedTuples, polymorphic subclasses and property-backed classes are all
     flagged for legitimately having no `self.x = ...`.

Usage:
    python tools/hermit_detector_precision.py                 # summary table
    python tools/hermit_detector_precision.py --survivors     # manual-review bucket
    python tools/hermit_detector_precision.py --self-check    # validation cases
"""

from __future__ import annotations

import argparse
import ast
import collections
import os
import re
import sys

SKIP_BASE = re.compile(r"NamedTuple|TypedDict|Enum|Protocol|Exception|Error|ABC|Refused|Warning")
SKIP_DIRS = {"node_modules", "build", "__pycache__"}


# --------------------------------------------------------------------------
# The canonical detector, transcribed verbatim in behaviour from
# hermit-patterns.md L469-523. Do not "improve" this function: its whole job
# is to reproduce what the shipped tool does.
# --------------------------------------------------------------------------
def canonical_flags(node: ast.ClassDef) -> bool:
    """True if the SHIPPED detector would flag `node` as stateless ceremony."""
    has_self_assign = any(
        isinstance(n, ast.Assign)
        and any(
            isinstance(t, ast.Attribute) and isinstance(t.value, ast.Name) and t.value.id == "self"
            for t in n.targets
        )
        for n in ast.walk(node)
    )
    if has_self_assign:
        return False
    # Exclusion 1: internal method dispatch
    if any(
        isinstance(n, ast.Call)
        and isinstance(getattr(n, "func", None), ast.Attribute)
        and isinstance(getattr(n.func, "value", None), ast.Name)
        and n.func.value.id == "self"
        for n in ast.walk(node)
    ):
        return False
    # Exclusion 2: 10+ methods == namespace. NOTE: ast.walk also counts nested
    # defs, so this over-excludes. Latent: 0 cases in this repo (see --self-check).
    if sum(1 for n in ast.walk(node) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))) >= 10:
        return False
    # Exclusion 3: dispatch table built from self.method references
    for n in ast.walk(node):
        if isinstance(n, (ast.Dict, ast.List, ast.Set)):
            for v in ast.walk(n):
                if (
                    isinstance(v, ast.Attribute)
                    and isinstance(getattr(v, "value", None), ast.Name)
                    and v.value.id == "self"
                ):
                    return False
    return True


# --------------------------------------------------------------------------
# Why each flag is wrong
# --------------------------------------------------------------------------
def _self_targets(n: ast.AST):
    """Every assignment target in `n`, including the forms the detector misses."""
    if isinstance(n, ast.Assign):
        for t in n.targets:
            yield from (t.elts if isinstance(t, (ast.Tuple, ast.List)) else [t])
    elif isinstance(n, (ast.AnnAssign, ast.AugAssign)):
        yield n.target


def real_self_assign(node: ast.ClassDef) -> str | None:
    """The assignment FORM by which `node` sets instance state, or None."""
    for n in ast.walk(node):
        if not isinstance(n, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
            continue
        for t in _self_targets(n):
            if (
                isinstance(t, ast.Attribute)
                and isinstance(getattr(t, "value", None), ast.Name)
                and t.value.id == "self"
            ):
                if isinstance(n, ast.AnnAssign):
                    return "annotated self-assign (self.x: T = ...)"
                if isinstance(n, ast.AugAssign):
                    return "augmented self-assign (self.x += ...)"
                # plain Assign the detector already catches unless target is a tuple
                direct = any(
                    isinstance(t2, ast.Attribute)
                    and isinstance(getattr(t2, "value", None), ast.Name)
                    and t2.value.id == "self"
                    for t2 in n.targets
                )
                if not direct:
                    return "tuple-target self-assign (self.a, self.b = ...)"
    return None


def classify(node: ast.ClassDef) -> str:
    """Why the canonical detector's flag on `node` is wrong, or 'manual-review'."""
    decos = [ast.unparse(d) for d in node.decorator_list]
    bases = [ast.unparse(b) for b in node.bases]
    if any("dataclass" in d for d in decos):
        return "dataclass (generated __init__ IS state)"
    if any("NamedTuple" in b or "TypedDict" in b for b in bases):
        return "NamedTuple/TypedDict (value type)"
    if any(re.search(r"Exception|Error|Refused|Warning", b) for b in bases):
        return "exception subclass"
    if any(isinstance(n, ast.AnnAssign) for n in node.body):
        return "class-level annotated fields"
    missed = real_self_assign(node)
    if missed:
        return f"{missed} -- MISSED STATE"
    if any(
        any("setter" in ast.unparse(d) for d in m.decorator_list)
        for m in node.body
        if isinstance(m, (ast.FunctionDef, ast.AsyncFunctionDef))
    ):
        return "property setter (state via @x.setter)"
    if bases:
        return "subclass (polymorphism)"
    return "manual-review"


def scan(root: str):
    """Yield (path, class node, category) for every class the detector flags."""
    for dirpath, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if not d.startswith(".") and d not in SKIP_DIRS]
        for f in sorted(files):
            if not f.endswith(".py"):
                continue
            path = os.path.join(dirpath, f)
            try:
                tree = ast.parse(open(path, encoding="utf-8").read())
            except (SyntaxError, UnicodeDecodeError, OSError):
                continue
            for node in ast.walk(tree):
                if isinstance(node, ast.ClassDef) and canonical_flags(node):
                    yield path, node, classify(node)


# --------------------------------------------------------------------------
# Validation cases. A measurement tool that is not itself checked is a claim.
# --------------------------------------------------------------------------
SELF_CHECK = [
    # (source, detector-flags-it?, expected category substring)
    ("class A:\n    def __init__(self):\n        self.x = 1\n", False, ""),
    ("class A:\n    def __init__(self):\n        self.a, self.b = 1, 2\n", True, "tuple-target"),
    ("class A:\n    def __init__(self):\n        self.x: int = 0\n", True, "annotated self-assign"),
    ("class A:\n    def __init__(self):\n        self.n += 1\n", True, "augmented self-assign"),
    ("import dataclasses\n@dataclasses.dataclass\nclass A:\n    x: int\n", True, "dataclass"),
    ("class A(Exception):\n    pass\n", True, "exception subclass"),
    ("import typing\nclass A(typing.NamedTuple):\n    x: int\n", True, "NamedTuple"),
    ("class A:\n    @property\n    def t(self):\n        return 1\n    @t.setter\n    def t(self, v):\n        pass\n", True, "property setter"),
    ("class A:\n    def f(self):\n        return self.g()\n    def g(self):\n        return 1\n", False, ""),
    # a genuine stateless ceremony: no state, no bases, plain methods
    ("class A:\n    def f(self, x):\n        return x + 1\n    def g(self, x):\n        return x * 2\n", True, "manual-review"),
]


def self_check() -> int:
    failures = 0
    for src, want_flag, want_cat in SELF_CHECK:
        node = next(n for n in ast.walk(ast.parse(src)) if isinstance(n, ast.ClassDef))
        got_flag = canonical_flags(node)
        if got_flag != want_flag:
            print(f"FAIL flag: want {want_flag} got {got_flag} for\n{src}")
            failures += 1
            continue
        if want_flag:
            got_cat = classify(node)
            if want_cat not in got_cat:
                print(f"FAIL cat: want {want_cat!r} in {got_cat!r} for\n{src}")
                failures += 1
    # the latent ast.walk inflation, asserted rather than assumed
    inflated = (
        "class A:\n"
        + "".join(f"    def m{i}(self):\n        def helper():\n            return 1\n        return helper()\n" for i in range(5))
    )
    node = next(n for n in ast.walk(ast.parse(inflated)) if isinstance(n, ast.ClassDef))
    if canonical_flags(node):
        print("NOTE: 5 methods with nested defs was NOT over-excluded (walk count < 10)")
    else:
        print("CONFIRMED: ast.walk inflation over-excludes a 5-method class (counts 10)")
    print("self-check: PASS" if not failures else f"self-check: {failures} FAILURE(S)")
    return 1 if failures else 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--root", default=".", help="tree to scan (default: cwd)")
    ap.add_argument("--survivors", action="store_true", help="list the manual-review bucket")
    ap.add_argument("--self-check", action="store_true", help="run validation cases only")
    a = ap.parse_args(argv)

    if a.self_check:
        return self_check()

    rows = list(scan(a.root))
    cats = collections.Counter(c for _, _, c in rows)
    total = sum(cats.values())
    if not total:
        print("detector flags 0 classes")
        return 0

    print(f"Canonical Hermit stateless-ceremony detector flags {total} classes in {a.root!r}.\n")
    print(f"{'count':>6}  {'pct':>6}  why the flag is wrong")
    for cat, n in cats.most_common():
        print(f"{n:>6}  {100 * n / total:>5.1f}%  {cat}")

    missed = sum(n for c, n in cats.items() if "MISSED STATE" in c)
    print(f"\n{missed} of {total} HAVE instance state the detector failed to see.")
    print(f"{cats['manual-review']} of {total} survive every exclusion and need human review.")

    if a.survivors:
        print("\nmanual-review bucket:")
        for path, node, cat in rows:
            if cat != "manual-review":
                continue
            base = os.path.basename(path)
            kind = "test" if base.startswith("test_") or "stub" in path else "NON-TEST"
            methods = [m.name for m in node.body if isinstance(m, (ast.FunctionDef, ast.AsyncFunctionDef))]
            print(f"  [{kind:8s}] {path}:{node.lineno} class {node.name} methods={methods}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
