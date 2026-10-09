#!/usr/bin/env python3
"""Which verdicts would still pass if the population they check were EMPTY? (#620)

    python3 tools/vacuous_guard_audit.py              # the audit, as a table
    python3 tools/vacuous_guard_audit.py --list       # every flagged construct
    python3 tools/vacuous_guard_audit.py --check      # the standing check; exit 1 on a gap

WHY THIS EXISTS

`all([])` is True. A `for` loop over zero items raises nothing. `a != b` holds
when both sides were emptied the same way. So a verdict whose only refusal path
lives inside a loop, or inside `all(...)`, PASSES when the population it was
meant to check is empty -- and "empty" is exactly what a deleted child, a
missing file or a dropped section looks like. Found by hand, one site at a time:
#118 (schroeder_t20's truncation guard), #283 (trial receipt with zero required
children), #431 (preconditions that gated on a file existing and never read it),
#582 (`release_manifest.image_identity`, still open). This is the standing check
for the class, modelled on `tools/nan_guard_audit.py` (#134): a triage list and
a deliberately narrow gate.

TWO HALVES, AND ONLY ONE OF THEM IS A GATE.

**The audit (default output) is a TRIAGE LIST, not a defect count.** It lists
each construct inside a verdict-shaped function whose truth survives an empty
input and for which no earlier statement of the same function asserts that
population (`if not X`, `len(X) ...`, `assert X`). Deciding whether the
population CAN be empty is the human half; the tool does not pretend to. Hits
over a constant or integer domain (a literal tuple, `range(3)`, an ALL_CAPS
module constant, `len(...)`) are marked `const?` / `int?` and are advisory:
a literal cannot be emptied by an input.

A population assertion that mentions a DIFFERENT thing does not count. The test
must name the iterated expression itself (`pub["published_sha256"]`), so
`if pub["configuration"] != CONFIG` earlier in the function does not excuse a
later loop over `pub["published_sha256"]`.

What the audit cannot see (stated so a clean row is not read as proof): a
verdict accumulated into a list that is later returned as `not problems`; an
emptiness test made by a helper; a population emptied after the assertion.

**The boundary check (`--check`) is the gate, and it is deliberately narrow.**
`BOUNDARIES` names the verdict functions already FIXED and the population
assertion each one carries, and `--check` asserts each is still there. That
catches the regression that matters: the assertion refactored away while the
suite stays green. It does not find new vacuous verdicts -- the audit does,
for a human to read.

`--check` is asserted by `tools/test_vacuous_guard_audit.py`, which `make
verify` runs inside its broad pytest job, the same way
`model/test_nan_invariance.py` carries `nan_guard_audit --check`.
"""
from __future__ import annotations

import argparse
import ast
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent

#: Non-test code scanned. `fpga/` is included beyond the issue's `tools/` and
#: `model/` because #582 (`fpga/release/release_manifest.py`), the audit's first
#: external grounding, lives there.
AUDIT_DIRS = ("tools", "model", "fpga")

#: Fixed verdict functions and the population assertion each must still carry.
#: `(file, qualified name, substrings of ONE assertion site, issue, why)`.
#: A site is the test of an `if`/`assert`/conditional expression in the
#: function's own body, or the text of a `raise` inside an `except` handler. All
#: substrings must appear in the same site. Substrings are deliberately the
#: terms of the guard, not its message, so rewording a message is not a gap but
#: deleting or rewriting the condition is.
BOUNDARIES = (
    ("model/audio_measure.py", "schroeder_t20", ("after_s", "min_tail_t20"), "#118",
     "the truncation guard is a LENGTH after the fit range; without it any "
     "finite record's backward integral looks fully decayed"),
    ("tools/trial.py", "composite", ("not required",), "#283",
     "all([]) is True: with no required child, a failed product question "
     "would be a PASS"),
    ("tools/cymbal_band_balance.py", "filter_record", ("missing",), "#431",
     "the filter-figure record must carry every curve it is read from"),
    ("tools/cymbal_band_balance.py", "filter_record", ("len(hz)", "5"), "#431",
     "a curve with too few points answers nothing about a peak or pass band"),
    ("tools/cymbal_band_balance.py", "vca_drive_record", ("vca_term_db",), "#431",
     "vca-drive.json is read and schema-checked, not merely required to exist"),
    ("tools/cymbal_band_balance.py", "preconditions", ("content_checked",), "#431",
     "filter-figure and vca-drive are answered by reading the artifact, "
     "not by path.exists()"),
)

#: What a refusal looks like in this repository.
REFUSAL_NAMES = ("Refused", "KitRefused", "InsufficientEvidence", "NonFiniteAudio",
                 "_fail", "level_refusal", "RegistryError", "SystemExit")
#: A function whose name says it renders a verdict.
VERDICT_NAME = re.compile(r"(check|verify|validate|audit|gate|verdict|precondition|"
                          r"problems|composite|identity|qualif|accept|admit|"
                          r"^is_|^_ok|_ok$|refus)", re.I)
_WRAPPERS = ("sorted", "list", "tuple", "set", "reversed", "enumerate", "iter")
_VIEWS = ("items", "values", "keys")


# ---------------------------------------------------------------------------
# AST helpers
# ---------------------------------------------------------------------------
def _call_name(call: ast.Call) -> str:
    fn = call.func
    return fn.attr if isinstance(fn, ast.Attribute) else getattr(fn, "id", "")


def _is_refusal_stmt(stmt: ast.AST) -> bool:
    if isinstance(stmt, ast.Raise):
        return True
    if isinstance(stmt, ast.Assert):
        return True
    if isinstance(stmt, ast.Return) and isinstance(stmt.value, ast.Call):
        return _call_name(stmt.value) in REFUSAL_NAMES
    if isinstance(stmt, ast.Return) and isinstance(stmt.value, ast.Tuple) and stmt.value.elts:
        first = stmt.value.elts[0]
        return isinstance(first, ast.Name) and first.id in ("REFUSED", "NO_VERDICT", "FAIL")
    if isinstance(stmt, ast.Return) and isinstance(stmt.value, ast.Constant):
        return stmt.value.value is False
    return False


def _contains_refusal(nodes) -> bool:
    for n in nodes:
        for sub in ast.walk(n):
            if _is_refusal_stmt(sub):
                return True
    return False


def _iter_base(node: ast.expr) -> ast.expr:
    """Strip `sorted(...)`, `.items()` and friends: the population itself."""
    while True:
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr in _VIEWS and not node.args):
            node = node.func.value
        elif (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
              and node.func.id in _WRAPPERS and node.args):
            node = node.args[0]
        else:
            return node


def _domain(base: ast.expr) -> str:
    """'' for a population an input can empty, else the advisory mark."""
    if isinstance(base, (ast.Tuple, ast.List, ast.Set, ast.Dict, ast.Constant)):
        return "const?"
    if isinstance(base, ast.Call):
        n = _call_name(base)
        if n == "range":
            return "int?"
        if n in ("zip", "product", "combinations", "permutations") and all(
                _domain(_iter_base(a)) for a in base.args):
            return "const?"
    if isinstance(base, ast.Name) and base.id.isupper():
        return "const?"
    if isinstance(base, ast.Attribute) and base.attr.isupper():
        return "const?"
    return ""


def _asserts_population(test: ast.expr, base_text: str) -> bool:
    """Does this test decide whether the population `base_text` is empty?"""
    t = ast.unparse(test)
    if base_text not in t:
        return False
    esc = re.escape(base_text)
    return bool(
        re.search(rf"(?<![\w.]){'not ' + esc}(?![\w\[.(])", t) or
        re.search(rf"len\(\s*{esc}\s*\)", t) or
        re.search(rf"{esc}\.(size|__len__)\b", t) or
        re.fullmatch(esc, t) or
        re.search(rf"(?<![\w.]){esc}\s*(==|!=)\s*(\{{\}}|\[\]|\(\)|0|None|set\(\)|\"\")", t) or
        re.search(rf"\bbool\(\s*{esc}\s*\)", t) or
        re.search(rf"(?:^|\band\b|\bor\b)\s*{esc}\s*(?:\band\b|\bor\b|$)", t)
    )


class _Func:
    def __init__(self, qual: str, node):
        self.qual, self.node = qual, node


def _functions(tree: ast.Module):
    def walk(node, prefix=""):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                name = f"{prefix}{child.name}"
                yield _Func(name, child)
                yield from walk(child, f"{name}.")
            elif isinstance(child, ast.ClassDef):
                yield from walk(child, f"{prefix}{child.name}.")
            else:
                yield from walk(child, prefix)
    yield from walk(tree)


def _own_nodes(fn):
    """Every node in the function's own body, not descending into nested defs."""
    stack = list(fn.body)
    while stack:
        n = stack.pop()
        yield n
        for c in ast.iter_child_nodes(n):
            if not isinstance(c, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda,
                                  ast.ClassDef)):
                stack.append(c)


def is_verdict_shaped(f: _Func) -> bool:
    if VERDICT_NAME.search(f.node.name):
        return True
    return any(_is_refusal_stmt(n) or (isinstance(n, ast.Raise)) for n in _own_nodes(f.node))


# ---------------------------------------------------------------------------
# The audit
# ---------------------------------------------------------------------------
class Hit:
    def __init__(self, path, lineno, func, kind, text, mark):
        self.path, self.lineno, self.func, self.kind, self.text, self.mark = (
            path, lineno, func, kind, text, mark)

    def __str__(self):
        m = f" [{self.mark}]" if self.mark else ""
        return f"{self.path}:{self.lineno} {self.func}: {self.kind} {self.text}{m}"


def _population_tests(fn, before: int) -> list[ast.expr]:
    """Tests of every `if`/`assert`/conditional in the function's own body that
    sit on a line before `before`."""
    out = []
    for n in _own_nodes(fn):
        if isinstance(n, (ast.If, ast.Assert, ast.IfExp, ast.While)) and n.lineno < before:
            out.append(n.test)
        if isinstance(n, ast.BoolOp) and n.lineno < before:
            out.extend(n.values)
    return out


def audit_source(src: str, rel: str) -> list[Hit]:
    tree = ast.parse(src)
    hits: list[Hit] = []
    for f in _functions(tree):
        if not is_verdict_shaped(f):
            continue
        own = list(_own_nodes(f.node))
        for n in own:
            # (a) `all(<gen>)` / `not any(<gen>)` over a population
            pops: list[tuple[int, str, ast.expr]] = []
            if isinstance(n, ast.Call) and _call_name(n) == "all" and n.args:
                a = n.args[0]
                if isinstance(a, (ast.GeneratorExp, ast.ListComp, ast.SetComp)):
                    pops.append((n.lineno, "all()", _iter_base(a.generators[0].iter)))
            if (isinstance(n, ast.UnaryOp) and isinstance(n.op, ast.Not)
                    and isinstance(n.operand, ast.Call) and _call_name(n.operand) == "any"
                    and n.operand.args
                    and isinstance(n.operand.args[0], (ast.GeneratorExp, ast.ListComp))):
                gen = n.operand.args[0]
                pops.append((n.lineno, "not any()", _iter_base(gen.generators[0].iter)))
            # (b) a `for` loop whose body is the only thing that can refuse
            if isinstance(n, ast.For) and _contains_refusal(n.body):
                pops.append((n.lineno, "for-loop refuses", _iter_base(n.iter)))
            for lineno, kind, base in pops:
                base_text = ast.unparse(base)
                if any(_asserts_population(t, base_text)
                       for t in _population_tests(f.node, lineno)):
                    continue
                hits.append(Hit(rel, lineno, f.qual, kind, f"over {base_text}", _domain(base)))
    return hits


def source_files(root: pathlib.Path = ROOT):
    for d in AUDIT_DIRS:
        for p in sorted((root / d).rglob("*.py")):
            rel = p.relative_to(root).as_posix()
            name = p.name
            if (name.startswith("test_") or name.endswith("_test.py") or "/probes/" in rel
                    or "/.venv/" in rel or "/venv/" in rel or "/site-packages/" in rel):
                continue
            yield rel, p


def audit_tree(root: pathlib.Path = ROOT) -> list[Hit]:
    hits = []
    for rel, p in source_files(root):
        try:
            hits.extend(audit_source(p.read_text(encoding="utf-8"), rel))
        except (SyntaxError, UnicodeDecodeError) as exc:
            print(f"SKIPPED  {rel}: {type(exc).__name__}", file=sys.stderr)
    return hits


# ---------------------------------------------------------------------------
# The gate
# ---------------------------------------------------------------------------
def assertion_sites(src: str) -> dict[str, list[str]]:
    """qualified function name -> texts of its population-assertion sites."""
    tree = ast.parse(src)
    out: dict[str, list[str]] = {}
    for f in _functions(tree):
        sites = out.setdefault(f.qual, [])
        for n in _own_nodes(f.node):
            if isinstance(n, (ast.If, ast.Assert, ast.IfExp)):
                sites.append(ast.unparse(n.test))
            if isinstance(n, ast.ExceptHandler):
                for r in n.body:
                    for sub in ast.walk(r):
                        if isinstance(sub, ast.Raise):
                            sites.append(ast.unparse(sub))
    return out


def boundary_gaps(root: pathlib.Path = ROOT) -> list[str]:
    gaps = []
    cache: dict[str, dict[str, list[str]]] = {}
    for rel, func, needles, issue, _why in BOUNDARIES:
        if rel not in cache:
            p = root / rel
            if not p.is_file():
                cache[rel] = {}
            else:
                cache[rel] = assertion_sites(p.read_text(encoding="utf-8"))
        sites = cache[rel].get(func)
        if sites is None:
            gaps.append(f"{rel}::{func} ({issue}) is not a function in that file")
        elif not any(all(nd in s for nd in needles) for s in sites):
            gaps.append(f"{rel}::{func} ({issue}) has lost its population assertion "
                        f"(no guard mentioning {', '.join(map(repr, needles))})")
    return gaps


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--list", action="store_true", help="print every flagged construct")
    ap.add_argument("--check", action="store_true",
                    help="assert every fixed verdict still carries its population assertion")
    ap.add_argument("--root", type=pathlib.Path, default=ROOT, help=argparse.SUPPRESS)
    args = ap.parse_args(argv)

    if args.check:
        gaps = boundary_gaps(args.root)
        for g in gaps:
            print(f"GAP      {g}")
        if gaps:
            print(f"\n{len(gaps)} of {len(BOUNDARIES)} registered population assertions "
                  f"are gone. The verdict passes on an empty population (#620).")
            return 1
        print(f"OK       {len(BOUNDARIES)} registered population assertions all present")
        return 0

    hits = audit_tree(args.root)
    by_mark: dict[str, int] = {}
    for h in hits:
        by_mark[h.mark or "open"] = by_mark.get(h.mark or "open", 0) + 1
    files = {h.path for h in hits}
    print(f"{len(hits)} constructs in {len(files)} files survive an empty population "
          f"with no preceding population assertion "
          f"({by_mark.get('open', 0)} open, {by_mark.get('int?', 0)} int?, "
          f"{by_mark.get('const?', 0)} const?).")
    print("A hit is a defect only where the population CAN be empty; the advisory "
          "marks are the tool's guess that it cannot. See --list.")
    if args.list:
        print()
        for h in sorted(hits, key=lambda h: (h.mark != "", h.path, h.lineno)):
            print(h)
    return 0


if __name__ == "__main__":
    sys.exit(main())
