#!/usr/bin/env python3
"""Where does an unguarded `Estimate.value` enter an ABSORBING aggregate? (#801)

    python3 tools/probes/absorbing_aggregate_audit.py           # the triage list
    python3 tools/probes/absorbing_aggregate_audit.py --self-test
    python3 tools/probes/absorbing_aggregate_audit.py --defeat   # what it cannot see

THE SHAPE, AND WHY IT IS ITS OWN AXIS

`max()` returns its FIRST argument when the comparison is False, so
`max(0.068, float("nan")) -> 0.068`. An aggregate over estimates therefore
ABSORBS refusals: the result is not `nan`, it is the answer over however many
members happened to answer, and it still looks exactly like a number. #801's
site reduced twelve known period cases that way, and an estimator refusing
nine of them reported a SMALLER error (0.034 % against 0.068 %) than the truth
-- a figure that is quoted as a discrimination floor, so too small there means
a pitch difference can be called significant when it sits inside the reader's
real error.

This is a third axis next to the two audits already in the repository, and
deliberately not a fourth copy of either:

    tools/nan_guard_audit.py       (#134) guards INSIDE an estimator
    tools/vacuous_guard_audit.py   (#620) models an EMPTY population
    this file                      (#801) a SHRUNKEN one: the reduction drops
                                          the members it could not produce

`vacuous_guard_audit`'s own docstring names the shrunken case as a blind spot,
which is why it stayed clean across #801's repair (verified: its `--list`
output is byte-identical before and after).

A TRIAGE LIST, NOT A GATE. Deciding whether a flagged aggregate can actually
receive a refusal is the human half; `--defeat` prints what the scan cannot
see. It is not wired into `make verify` -- under the milestone priority a new
standing gate would have to name a sound requirement first, and this one's
population is currently one false positive.

WRONG-THEN-RIGHT (1 of 1, and it is the reason this file is committed)

Its first run reported **"0 sites"** against the `origin/main` baseline -- a
clean tree, apparently. The baseline was a `git archive` export with no
`.git`, so `git ls-files` returned nothing and the scan ran over ZERO files.
An empty population rendered as a clean result: the exact class the audit next
door was written for, in the instrument written to find it. It now falls back
to a glob and REFUSES rather than reporting when it has no files, per CLAUDE.md
("assert your apparatus's preconditions at the point of use, and REFUSE rather
than report when they fail"). The `--self-test` exists for the same reason: a
scan that finds nothing is indistinguishable from a scan that cannot find
anything, unless something makes it find a known case.
"""
from __future__ import annotations

import argparse
import ast
import pathlib
import subprocess
import sys

#: Callables whose result SURVIVES a `nan` member instead of becoming `nan`.
#: `max`/`min` by Python's comparison semantics, `sorted` by the same, and the
#: `nan*` family by contract. `sum`/`mean` are NOT absorbing -- they propagate
#: `nan` -- but they are listed because they absorb a `None` into a
#: `TypeError`, which is NO VERDICT rather than a result, and because a caller
#: that then compares the sum is back in the silent-False case.
ABSORBING = {"max", "min", "sorted", "nanmax", "nanmin", "nansum", "nanmean",
             "sum", "mean", "fsum", "median", "average"}
#: Reading any of these on a name counts as guarding it anywhere in the same
#: function. `require` raises, so it is a guard by construction.
GUARD_ATTRS = {"ok", "reason", "outside_domain", "require"}


def _guarded_names(fn: ast.AST) -> set:
    """Names on which some `.ok`/`.reason`/`.require` is read in this function.

    Deliberately generous: a guard anywhere in the function counts, even on
    another branch. An audit that cried wolf on correctly-guarded code would
    be ignored, and `--defeat` records the resulting blind spot rather than
    pretending it is not there."""
    out = set()
    for n in ast.walk(fn):
        if (isinstance(n, ast.Attribute) and n.attr in GUARD_ATTRS
                and isinstance(n.value, ast.Name)):
            out.add(n.value.id)
    # ALIAS GUARDS. `for side, m in (("A", ma), ("B", mb)): if not m.ok: ...`
    # guards `ma` and `mb` through the loop variable, so neither name carries
    # a `.ok` read of its own. Without this the scan's only hit in the whole
    # repository was that idiom at `permitted_differences.py:845`, correctly
    # guarded -- a triage list of one entry, all of it noise. Found by this
    # file's own `--self-test`, which is what the self-test is for.
    #
    # Narrow on purpose: it fires only when a name BOUND BY THE LOOP is itself
    # guarded. A loop whose variable is never guarded marks nothing, which
    # SELF_TEST pins as its own case.
    for loop in [n for n in ast.walk(fn) if isinstance(n, (ast.For, ast.AsyncFor))]:
        targets = {t.id for t in ast.walk(loop.target) if isinstance(t, ast.Name)}
        if not (targets & out):
            continue
        out |= {t.id for t in ast.walk(loop.iter) if isinstance(t, ast.Name)}
    return out


def scan_source(src: str, rel: str = "<source>") -> list:
    """[(rel, line, function, aggregate, name)] for this source text."""
    rows = []
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return rows
    for fn in [n for n in ast.walk(tree)
               if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]:
        guarded = _guarded_names(fn)
        for call in [n for n in ast.walk(fn) if isinstance(n, ast.Call)]:
            name = (call.func.attr if isinstance(call.func, ast.Attribute)
                    else getattr(call.func, "id", None))
            if name not in ABSORBING:
                continue
            for arg in call.args:
                for n in ast.walk(arg):
                    if (isinstance(n, ast.Attribute) and n.attr == "value"
                            and isinstance(n.value, ast.Name)
                            and n.value.id not in guarded):
                        rows.append((rel, n.lineno, fn.name, name, n.value.id))
    # A nested function is walked both on its own and inside its parent, so the
    # same physical site is reported twice. Deduplicate on the site, not the
    # enclosing function.
    return sorted({(r, l, a, v): (r, l, f, a, v)
                   for r, l, f, a, v in rows}.values(), key=lambda t: (t[0], t[1]))


def tracked_python(root: pathlib.Path) -> tuple:
    """(relative paths, how they were found). REFUSES on an empty population."""
    files = subprocess.run(["git", "ls-files", "*.py"], cwd=root,
                           capture_output=True, text=True).stdout.split()
    how = "git ls-files"
    if not files:
        # See the module docstring's wrong-then-right note: `git ls-files` in a
        # tree with no index returns nothing, and a scan over nothing reported
        # a clean result.
        files = sorted(str(p.relative_to(root)) for p in root.rglob("*.py"))
        how = "rglob (no git index)"
    files = [f for f in files if not f.startswith(".loom/")]
    if not files:
        raise SystemExit(f"REFUSED: no .py files under {root}; a scan over an "
                         f"empty population is not a clean result")
    return files, how


def scan_tree(root: pathlib.Path) -> tuple:
    files, how = tracked_python(root)
    rows = []
    for rel in files:
        try:
            rows += scan_source((root / rel).read_text(encoding="utf-8"), rel)
        except (OSError, UnicodeDecodeError):
            continue
    return rows, files, how


#: (label, source, must be flagged). The audit's own validation cases: a scan
#: that reports nothing is indistinguishable from a scan that cannot report
#: anything. The first case is #801's site as it stood on origin/main
#: @ 344ed6e7, pasted rather than paraphrased.
SELF_TEST = (
    ("#801's site, pre-repair", '''
def period_error_on_known_cases():
    worst = 0.0
    for f0 in (60.0, 90.0):
        e = pm.dominant_period_ms(_tone(f0), SR, (40.0, f0 * 2.0))
        worst = max(worst, abs(e.value / (1000.0 / f0) - 1.0) * 100.0)
    return worst
''', True),
    ("#801's site, repaired (guard present)", '''
def period_error_report(estimates):
    errs = [abs(e.value / want - 1.0) for _f, want, e in estimates if e.ok]
    return max(errs) if len(errs) == len(estimates) else float("nan")
''', False),
    ("guarded via an alias, as permitted_differences does", '''
def fn(ma, mb):
    for side, m in (("A", ma), ("B", mb)):
        if not m.ok or m.value is None:
            return None
    return min(abs(ma.value), abs(mb.value))
''', False),
    ("a non-absorbing call is not flagged", '''
def fn(e):
    return abs(e.value) + 1.0
''', False),
    # The alias-guard extension must not swallow the defect it is next to: the
    # same loop shape with NOTHING guarded still has to be flagged, or
    # wrapping an unguarded read in a `for` would silence the audit.
    ("the same loop shape with no guard at all is still flagged", '''
def fn(ma, mb):
    for side, m in (("A", ma), ("B", mb)):
        log(side, m)
    return min(abs(ma.value), abs(mb.value))
''', True),
)

DEFEAT = """What satisfies this scan while violating its intent
(verification-rules.md rule 8 -- a guard ships with the input that defeats it):

 1. THE TWO HALVES IN DIFFERENT FUNCTIONS. The load-bearing one.
        rows.append(e.value if e.ok else float("nan"))   # one function
        worst = max(rows)                                # another
    The read and the aggregate must be in ONE function to be seen, so this
    arrangement -- which is the same defect -- is invisible. #801's own site
    was only visible because it was written as a single loop.
 2. A GUARD ON ANOTHER BRANCH. `.ok` read anywhere in the function marks the
    name guarded, so `if e.ok: log(e)` ... `max(e.value, x)` reads clean.
    Deliberate: the alternative cried wolf on `permitted_differences.py:845`.
 2b. AN ALIAS GUARD IT CANNOT FOLLOW. The loop-variable case IS followed (see
    `_guarded_names`), but only that one: `m = ma if cond else mb; if m.ok:`
    does not mark `ma`, and a guard read through a dict or an attribute
    (`sides["A"].ok`) marks nothing. Narrowing this rule is how a real site
    gets hidden, so it fires only when a name bound by the loop is itself
    guarded, and `--self-test` pins the unguarded loop as still-flagged.
 3. AN AGGREGATE NOT IN `ABSORBING`. A hand-rolled `worst = v if v > worst
    else worst`, `heapq.nlargest`, `np.fmax`, a `functools.reduce`.
 4. AN ALIAS. `v = e.value` then `max(v, ...)` -- the aggregate's argument is
    a plain name by then, so there is no `.value` read to see.
 5. A WRAPPER. `max(pick(e), ...)` where `pick` does the unguarded read.
 6. MODULE-LEVEL CODE. Only function bodies are walked. Measured at zero
    occurrences in this repository today, which is a result, not a guarantee.

What it CANNOT be defeated by, and this is the point of having it: the defect
it was written for. `--self-test` runs #801's pre-repair site and requires it
flagged, so a refactor that blinds the scan fails rather than reporting clean.
"""


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("root", nargs="?", default=None,
                    help="tree to scan (default: this repository)")
    ap.add_argument("--self-test", action="store_true",
                    help="run the audit against its own validation cases")
    ap.add_argument("--defeat", action="store_true",
                    help="print what satisfies this scan while violating it")
    args = ap.parse_args(argv)

    if args.defeat:
        print(DEFEAT)
        return 0

    if args.self_test:
        bad = 0
        for label, src, want in SELF_TEST:
            got = bool(scan_source(src, label))
            print(f"{'OK  ' if got == want else 'FAIL'}  {label}: "
                  f"flagged={got}, required={want}")
            bad += got != want
        print("self-test:", "PASS" if not bad else f"FAIL ({bad})")
        return 1 if bad else 0

    root = pathlib.Path(args.root or
                        pathlib.Path(__file__).resolve().parent.parent.parent).resolve()
    rows, files, how = scan_tree(root)
    print(f"scanned {len(files)} .py files under {root} via {how}")
    print(f"{len(rows)} site(s) where an unguarded `.value` enters an "
          f"absorbing aggregate\n")
    for rel, line, fname, agg, var in rows:
        print(f"  {rel}:{line}  in {fname}()  -> {agg}(... {var}.value ...)")
    if not rows:
        print("  (none) -- and `--self-test` is what makes that believable")
    print("\nA triage list, not a gate. `--defeat` prints what it cannot see.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
