#!/usr/bin/env python3
"""Which refusal guards does a NaN walk straight through, and which boundaries
stop it? (#134)

    python3 tools/nan_guard_audit.py              # the audit, as a table
    python3 tools/nan_guard_audit.py --list       # every fails-open guard
    python3 tools/nan_guard_audit.py --check      # the standing check; exit 1 on a gap

WHY THIS EXISTS

IEEE comparisons against NaN are always False, so the guard

    if float(np.abs(y).max()) <= 1e-9:
        raise Refused("silent")           # NaN <= 1e-9 is False -> "not silent"

does not merely miss a NaN, it is INVERTED by one: the check written to catch
bad values is the check bad values are most invisible to. #133 is the worked
example -- all-NaN audio passed seven checks in `refprofile.load_clip`
(profile membership, file present, byte count, sha256, sample rate, frame count,
not-silent) and loaded as a reference.

TWO HALVES, AND ONLY ONE OF THEM IS A GATE.

**The audit (default output) is a TRIAGE LIST, not a defect count.** It
classifies each refusal guard by what happens when its ordering comparisons are
forced False, which is what a NaN does to them:

    fails-open      the function CONTINUES -- a NaN reaching here is invisible
    fails-closed    the function REFUSES   -- `if not (x > t)` and friends
    indeterminate   the boolean structure does not decide it either way

A fails-open guard is only a defect if a NaN can actually reach it, and most
cannot: a guard on `len(x)`, `sel.sum()` or an `int(...)` is in the integer
domain where no NaN exists. Those are marked `int?` (a HEURISTIC on the operand
text, advisory only) so a reader can filter them out. Deciding reachability is
the human half of the audit and the tool does not pretend to do it.

**The boundary check (`--check`) is the gate, and it is deliberately narrow.**
Checking finiteness once per audio-entry boundary is tractable; rewriting a
hundred comparison guards is not, and would not help -- a value that reached
them is already a number. So `BOUNDARIES` below names each function where audio
ENTERS this repository from outside its own arithmetic, and `--check` asserts
that each one still contains a finiteness assertion in its own body. That
catches the regression this class of bug is really made of: a guard that is
quietly deleted or refactored away while the suite stays green (#113).

`--check` is asserted by `model/test_nan_invariance.py`, so `make verify` runs
it; the invariance property in that file is the dynamic half that no amount of
AST reading can replace.
"""
from __future__ import annotations

import argparse
import ast
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent

#: The four files #134 names, in its own order.
AUDIT_FILES = (
    "model/audio_measure.py",
    "tools/run_case.py",
    "tools/refprofile.py",
    "model/reference_rigs.py",
)

#: Every function where audio ENTERS from outside this repository's own
#: arithmetic, and must therefore be proven to be numbers before anything
#: compares it with anything. `(file, qualified name, why it is a boundary)`.
#:
#: A function is on this list because audio arrives in it that nothing here
#: computed -- a WAV off the disk, a plugin host's buffer, a caller's array.
#: It is NOT on this list merely because it touches audio: `prepare()` is here
#: because every metric's input passes through it, `window()` is not, because
#: `prepare()` already vouched for what it slices.
BOUNDARIES = (
    ("model/audio_measure.py", "_as_float",
     "every estimator in the module ingests its signal through this one "
     "function; `finite=False` is the explicit, commented opt-out for the "
     "response-curve callers whose dB axis legitimately holds -inf/NaN"),
    ("tools/refprofile.py", "load_clip",
     "the frozen reference profile's reader -- #133's seven-check miss"),
    ("tools/refprofile.py", "write_clip",
     "the FREEZE side of the same cache: a hash vouches for the bytes, so "
     "non-finite audio must never become the bytes"),
    ("tools/refprofile.py", "render",
     "the decision to accept a plugin's clip INTO the profile, where the "
     "level floor it sits beside is the guard a NaN is invisible to"),
    ("tools/run_case.py", "load_reference",
     "the external reference corpus reader (a WAV this repository did not write)"),
    ("tools/run_case.py", "prepare",
     "both sides of every drum comparison pass through here before any metric "
     "sees them; it is the render path's boundary as well as the reference's"),
    ("model/reference_rigs.py", "_Plugin.render",
     "the plugin host's output buffer -- a plugin in a wrong state (unlicensed, "
     "un-set-up, denormal-blowup) is exactly where non-finite audio comes from"),
)

#: What a refusal looks like in this repository.
REFUSAL_CALLS = ("_fail", "Refused", "KitRefused", "InsufficientEvidence",
                 "NonFiniteAudio", "level_refusal")

#: Forced to False by a NaN on either side.
FALSE_ON_NAN = (ast.Lt, ast.LtE, ast.Gt, ast.GtE, ast.Eq, ast.Is)
#: Forced to True by a NaN on either side.
TRUE_ON_NAN = (ast.NotEq, ast.IsNot)

#: Operand text that means "this cannot be a NaN": a count, a length, an index.
#: A HEURISTIC, for filtering the triage list only. Nothing is decided by it.
INT_DOMAIN_HINTS = ("len(", ".size", ".sum()", "int(", ".shape", "count_nonzero",
                    "n_", "_n", "len ", "nbins", "idx", "index")


class _Verdict:
    OPEN = "fails-open"
    CLOSED = "fails-closed"
    UNKNOWN = "indeterminate"


def _nan_value(node: ast.expr):
    """Three-valued evaluation of `node` with every ordering comparison forced
    to the value a NaN gives it. Returns True, False or None (unknown).

    This is the whole analysis, and it is small on purpose: the question "does
    this guard refuse when its comparisons are NaN-False" is a question about
    boolean structure, and boolean structure is exactly what an AST has."""
    if isinstance(node, ast.Compare):
        if any(isinstance(op, TRUE_ON_NAN) for op in node.ops):
            return True
        if all(isinstance(op, FALSE_ON_NAN) for op in node.ops):
            return False
        return None
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
        v = _nan_value(node.operand)
        return None if v is None else (not v)
    if isinstance(node, ast.BoolOp):
        vals = [_nan_value(v) for v in node.values]
        if isinstance(node.op, ast.And):
            if any(v is False for v in vals):
                return False
            return True if all(v is True for v in vals) else None
        if any(v is True for v in vals):
            return True
        return False if all(v is False for v in vals) else None
    return None


def _refuses(body: list[ast.stmt]) -> bool:
    """Does this `if` body refuse, rather than fall through?"""
    for stmt in body:
        if isinstance(stmt, ast.Raise):
            return True
        if isinstance(stmt, ast.Return) and isinstance(stmt.value, ast.Call):
            fn = stmt.value.func
            name = fn.attr if isinstance(fn, ast.Attribute) else getattr(fn, "id", "")
            if name in REFUSAL_CALLS:
                return True
        # `return REFUSED, [...]` -- refprofile's verify() shape
        if isinstance(stmt, ast.Return) and isinstance(stmt.value, ast.Tuple):
            first = stmt.value.elts[0] if stmt.value.elts else None
            if isinstance(first, ast.Name) and first.id == "REFUSED":
                return True
    return False


class _Guard:
    def __init__(self, path: str, lineno: int, verdict: str, text: str, func: str):
        self.path, self.lineno, self.verdict, self.text, self.func = (
            path, lineno, verdict, text, func)

    @property
    def int_domain(self) -> bool:
        return any(h in self.text for h in INT_DOMAIN_HINTS)

    def __str__(self) -> str:
        mark = " [int?]" if self.int_domain else ""
        return f"{self.path}:{self.lineno} {self.func}: if {self.text}{mark}"


def _enclosing(tree: ast.Module) -> dict[int, str]:
    """line number -> qualified function name, for every line in a function."""
    out: dict[int, str] = {}

    def walk(node, prefix=""):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                name = f"{prefix}{child.name}"
                for ln in range(child.lineno, (child.end_lineno or child.lineno) + 1):
                    out.setdefault(ln, name)
                walk(child, prefix=f"{name}.")
            elif isinstance(child, ast.ClassDef):
                walk(child, prefix=f"{prefix}{child.name}.")
            else:
                walk(child, prefix=prefix)

    walk(tree)
    return out


def audit_file(rel: str, root: pathlib.Path = ROOT) -> list[_Guard]:
    """Every refusal guard in one file, classified."""
    src = (root / rel).read_text(encoding="utf-8")
    tree = ast.parse(src)
    where = _enclosing(tree)
    guards: list[_Guard] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.If) or not _refuses(node.body):
            continue
        if not any(isinstance(n, ast.Compare) for n in ast.walk(node.test)):
            continue
        v = _nan_value(node.test)
        verdict = (_Verdict.CLOSED if v is True
                   else _Verdict.OPEN if v is False else _Verdict.UNKNOWN)
        guards.append(_Guard(rel, node.test.lineno, verdict,
                             ast.unparse(node.test), where.get(node.lineno, "<module>")))
    return guards


def finite_checks(rel: str, root: pathlib.Path = ROOT) -> dict[str, int]:
    """qualified function name -> how many finiteness assertions its own body
    makes. A call to a helper whose NAME says finite counts, because that is
    how the boundary check is meant to be written."""
    src = (root / rel).read_text(encoding="utf-8")
    tree = ast.parse(src)
    where = _enclosing(tree)
    out: dict[str, int] = {}
    for node in ast.walk(tree):
        text = ""
        if isinstance(node, ast.Call):
            text = ast.unparse(node.func)
        if not text:
            continue
        if ("isfinite" in text or "require_finite" in text
                or "nonfinite_report" in text or "isnan" in text or "isinf" in text):
            fn = where.get(node.lineno, "<module>")
            out[fn] = out.get(fn, 0) + 1
    return out


def boundary_gaps(root: pathlib.Path = ROOT) -> list[str]:
    """Which registered boundaries have NO finiteness assertion of their own.

    This is `--check`'s entire content, and the reason it is narrow: it does not
    guess where audio enters, it asserts that the places someone WROTE DOWN as
    entry points still check. Adding a reader without registering it here is a
    gap this cannot see -- which is why the dynamic property test exists too."""
    gaps = []
    by_file: dict[str, dict[str, int]] = {}
    for rel, func, _why in BOUNDARIES:
        if rel not in by_file:
            by_file[rel] = finite_checks(rel, root)
        if not by_file[rel].get(func):
            gaps.append(f"{rel}::{func} has no finiteness assertion in its own body")
    return gaps


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--list", action="store_true",
                    help="print every fails-open guard, not just the counts")
    ap.add_argument("--check", action="store_true",
                    help="assert every registered boundary still checks finiteness")
    args = ap.parse_args(argv)

    if args.check:
        gaps = boundary_gaps()
        for g in gaps:
            print(f"GAP      {g}")
        if gaps:
            print(f"\n{len(gaps)} of {len(BOUNDARIES)} audio-entry boundaries do not "
                  f"assert finiteness. A NaN entering there is invisible to every "
                  f"threshold guard downstream (#134).")
            return 1
        print(f"OK       {len(BOUNDARIES)} audio-entry boundaries all assert finiteness")
        return 0

    rows = []
    total = {"fails-open": 0, "fails-closed": 0, "indeterminate": 0}
    for rel in AUDIT_FILES:
        guards = audit_file(rel)
        counts = {k: 0 for k in total}
        reachable = 0
        for g in guards:
            counts[g.verdict] += 1
            total[g.verdict] += 1
            if g.verdict == _Verdict.OPEN and not g.int_domain:
                reachable += 1
        fin = sum(finite_checks(rel).values())
        rows.append((rel, counts, reachable, fin))

    w = max(len(r[0]) for r in rows)
    print(f"{'file':<{w}}  open  closed  indet  open&float?  finite-checks")
    for rel, counts, reachable, fin in rows:
        print(f"{rel:<{w}}  {counts['fails-open']:>4}  {counts['fails-closed']:>6}  "
              f"{counts['indeterminate']:>5}  {reachable:>11}  {fin:>13}")
    print(f"\n{total['fails-open']} fails-open, {total['fails-closed']} fails-closed, "
          f"{total['indeterminate']} indeterminate refusal guards.")
    print("'open&float?' drops the guards whose operands look like counts or "
          "lengths (heuristic; see --list).")
    print("A fails-open guard is a defect only where a NaN can REACH it, which is "
          "what the boundary checks are for: python3 tools/nan_guard_audit.py --check")

    if args.list:
        print()
        for rel in AUDIT_FILES:
            for g in audit_file(rel):
                if g.verdict == _Verdict.OPEN:
                    print(g)
    return 0


if __name__ == "__main__":
    sys.exit(main())
