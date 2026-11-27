#!/usr/bin/env python3
"""Cross-reference census for the "verdict recomputed from a rounded score"
defect class (#1334): a field stored through `round()` in one module and read
in an ordered comparison in another.

    python3 tools/rounded_verdict_census.py <source-root>
    git archive --format=tar 65d5c023 tools model | tar -x -C /tmp/pre-fix
    python3 -I tools/rounded_verdict_census.py /tmp/pre-fix | grep -A2 '^best_correlation'

WHY THIS EXISTS.  `measure_harness.descent_test` decides whether a candidate
is "a re-pressing of the reference set" by comparing the FULL-precision
correlation with `MATCH_THRESHOLD`, and rounds the correlation only to display
it.  `tools/measure_conga_body_spread.py` then decided a second time, from the
rounded field:

    r["verdict"] = ("a re-pressing of the Fischer set -- not a second machine"
                    if r["best_correlation"] >= mh.MATCH_THRESHOLD else
                    "no Fischer file matches it")

so 0.94997528 -- below the threshold at full precision, a verdict of "no
match" -- displayed as 0.9500 and read as a match.  The repair's own comment
puts the cost at half a display step (#1334).  PR #1421 replaced the
recomputation with `fischer_verdict(r["verdict"])`, which forwards the
harness's decision and only rewords it.

The search that showed the repair removed this pair was a 36-line script in a
dispatch worker's /tmp, so the claim "no remaining instance of this shape"
rested on an instrument a later reader could not run (#1434).  This is that
instrument.  Its value is the known-positive tree below: a tree on which it
must report the pair, and one on which it must not.

WHAT A PAIR IS.  A candidate for inspection, never a finding.  The join is NAME
EQUALITY between a store site and a comparison site in two different modules.
A shared name cannot establish that the two sites ever see the same value, let
alone that a verdict is wrong.  The census makes the search reproducible; the
reading belongs to whoever runs it.  The exit status therefore says nothing
about the pairs: 0 with them and 0 without.  A zero-candidate gate is out of
scope (#1434).

The report prints one block per field name -- its store sites, then its
comparison sites, each once.  `census()` returns the pairs themselves.

WHAT IT MATCHES.  A STORE site binds a field name to the result of `round(...)`:

  * a keyword argument, `dict(best_correlation=round(best[0], 4))` -- #1334's
    own shape, and the form to check first because the name crosses a call;
  * a dict key, `{"best_correlation": round(best[0], 4)}`;
  * a subscript or attribute target, `r["best_correlation"] = round(x, 4)` and
    `r.best_correlation = round(x, 4)`.

The rounding call is `round(` by bare name or any `<expr>.round(` attribute
call (`np.round`, `numpy.round`, `arr.round`), and it must be the whole stored
expression.  A COMPARISON site is an `ast.Compare` that puts `>=`, `<=`, `>` or
`<` against an operand reading a named slot: a subscript with a string-literal
key (`r["best_correlation"]`), an attribute (`r.best_correlation`), or a
`.get("best_correlation", ...)` call.  A chain counts where its ordered
operator touches the field (`0 <= r["x"] < 1`), on either side.  An attribute
is also how a module constant is read (`mh.MATCH_THRESHOLD`), so a constant
compared against a threshold is counted as a field comparison.  It pairs only
with a same-named rounded store in another module, so it usually inflates the
`ordered-field-compares` total and adds no block.

Scanned: Python under `tools/` and `model/`, recursively, with test modules
(`test_*.py`, `*_test.py` -- the pytest.ini patterns) left out.

SEARCH BOUNDARY -- what a clean run does NOT say.  Each item below is asserted
blind by `tools/test_rounded_verdict_census.py`, so widening the detector turns
that test red until this list is rewritten to match:

  * f-string re-parsing, `float(f"{x:.3f}")`: a rounded value with no `round(`;
  * truncation, `int(x * 1000) / 1000` and `math.floor(x * 1000) / 1000`;
  * `np.clip(x, lo, hi)`: a bound applied silently, which is the same defect
    with no comparison site left to find;
  * a `round` nested inside the stored expression, `abs(round(x, 4))`;
  * a value that passes through a local on either side: `score = round(x, 4)`
    followed by `dict(score=score)`, or `score = r["score"]` followed by
    `if score >= t`.  The census joins a record slot to a record slot;
  * an augmented assignment, `r["x"] += round(y, 4)`;
  * `round` bound to another name;
  * a field compared in the module that stored it.  The census is the
    CROSS-MODULE reference the #1334 defect had;
  * a slot named by anything but a string literal (`r[key]`), and every file
    outside `tools/` and `model/` (`fpga/`, `pnr/`, `rtl-sketch/`, `spec/`).

PRECONDITIONS, ASSERTED AT THE POINT OF USE.  REFUSED (exit 2) WHEN UNMET.
A census is a statement about a whole tree, so a partial one must not read as
a clean one:

  * the root exists and is a directory;
  * it holds BOTH `tools/` and `model/`.  A pair needs a producer and a
    consumer, and half a tree cannot hold one;
  * at least one non-test Python file was scanned.  An empty report looks
    exactly like a clean one;
  * every scanned file parses.  One that does not is a part of the tree that
    was not read: it is named, and nothing is reported.

An unexpected exception is an execution error (exit 1, traceback), never a
refusal.

WRONG BEFORE IT WAS RIGHT, in this tool's own first green run.  Three, each
caught by a test or a measurement rather than by reading:

  * `report()` took `out=sys.stdout` as a default argument, which binds the
    real stdout at import.  The CLI test passed while the in-process test read
    an empty string: the report was right in the process and invisible to a
    caller that had replaced `sys.stdout`.  It now writes to `sys.stdout` at
    the call;
  * the report printed the cross product -- 532 pairs over 1,598 lines on
    65d5c023, 322 of them the field `value` against itself across files.
    Every site was right, and each repeated once per partner.  One block per
    field replaced it;
  * the comparison total drops by 2 across PR #1421 where the /tmp script's
    dropped by 1.  The difference had to be measured, not explained away: the
    removed line reads TWO named slots, `best_correlation` and
    `mh.MATCH_THRESHOLD`.  That is the attribute caveat above.

VALIDATION CASE, and the reason this is worth keeping.  Two revisions that
exist independently of any working tree:

    git archive --format=tar 65d5c023 tools model | tar -x -C /tmp/pre-fix
    python3 -I tools/rounded_verdict_census.py /tmp/pre-fix
    git archive --format=tar 00409889 tools model | tar -x -C /tmp/post-fix
    python3 -I tools/rounded_verdict_census.py /tmp/post-fix

At 65d5c023 the census reports `best_correlation`, stored at
`model/measure_harness.py:363` and compared at
`tools/measure_conga_body_spread.py:503`.  At 00409889 (PR #1421's head) that
pair is gone while the rounded store and the display read
(`r={r['best_correlation']:.3f}`) both remain.  The disappearance of that one
pair is the assertion; how many other candidates either tree holds is not.
`tools/test_rounded_verdict_census.py` runs both trees wherever those
revisions are in the clone.

`-I` because the census READS a tree and imports nothing from it: no module
under the scanned root is executed, so an exported revision needs no
environment.  Totals are reported and never pinned -- they move with every
merge, and a pin on them reds on other people's work.
"""
from __future__ import annotations

import argparse
import ast
import collections
import pathlib
import sys
from typing import NamedTuple

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import exit_contract  # noqa: E402
import refusal  # noqa: E402


class Refused(refusal.Refused, Exception):
    """A precondition failed.  REFUSED is a verdict, distinct from a value."""


SCAN_DIRS = ("tools", "model")
ORDERED_OPS = (ast.GtE, ast.LtE, ast.Gt, ast.Lt)


class Site(NamedTuple):
    field: str
    file: str
    line: int
    form: str
    text: str


class Candidate(NamedTuple):
    stored: Site
    compared: Site


def _key(node: ast.expr | None) -> str | None:
    """The string a subscript or `.get()` names its field by, if it is a literal."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _slot(node: ast.expr) -> tuple[str, str] | None:
    """(field, form) of a subscript or attribute that names a record slot."""
    if isinstance(node, ast.Subscript):
        name = _key(node.slice)
        return None if name is None else (name, "subscript")
    if isinstance(node, ast.Attribute):
        return node.attr, "attribute"
    return None


def _read(node: ast.expr) -> tuple[str, str] | None:
    """(field, form) when `node` reads a named slot of a record."""
    slot = _slot(node)
    if slot is not None:
        return slot
    if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
            and node.func.attr == "get" and node.args):
        name = _key(node.args[0])
        if name is not None:
            return name, "get"
    return None


def _is_round(node: ast.expr | None) -> bool:
    """`round(...)` by bare name, or any `<expr>.round(...)`: np.round, arr.round."""
    if not isinstance(node, ast.Call):
        return False
    func = node.func
    return ((isinstance(func, ast.Name) and func.id == "round")
            or (isinstance(func, ast.Attribute) and func.attr == "round"))


def _stores(tree: ast.AST, rel: str) -> list[Site]:
    sites: list[Site] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            for kw in node.keywords:
                if kw.arg is not None and _is_round(kw.value):
                    sites.append(Site(kw.arg, rel, kw.lineno, "keyword",
                                      f"{kw.arg}={ast.unparse(kw.value)}"))
        elif isinstance(node, ast.Dict):
            for k, v in zip(node.keys, node.values):
                name = _key(k)
                if name is not None and _is_round(v):
                    sites.append(Site(name, rel, k.lineno, "dict-key",
                                      f"{ast.unparse(k)}: {ast.unparse(v)}"))
        elif isinstance(node, (ast.Assign, ast.AnnAssign)) and _is_round(node.value):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                slot = _slot(target)
                if slot is not None:
                    sites.append(Site(slot[0], rel, target.lineno, slot[1],
                                      f"{ast.unparse(target)} = {ast.unparse(node.value)}"))
    return sites


def _compares(tree: ast.AST, rel: str) -> list[Site]:
    sites: list[Site] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Compare):
            continue
        for i, operand in enumerate((node.left, *node.comparators)):
            left = node.ops[i - 1] if i > 0 else None
            right = node.ops[i] if i < len(node.ops) else None
            if not any(isinstance(op, ORDERED_OPS) for op in (left, right)):
                continue
            read = _read(operand)
            if read is not None:
                sites.append(Site(read[0], rel, node.lineno, read[1], ast.unparse(node)))
    return sites


def _parse(path: pathlib.Path, root: pathlib.Path) -> tuple[str, ast.Module]:
    """(path relative to the root, tree).  A file that does not parse is a
    part of the tree that was not read, so it is refused by name."""
    rel = path.relative_to(root).as_posix()
    try:
        return rel, ast.parse(path.read_text(encoding="utf-8"), filename=rel)
    except (OSError, SyntaxError, ValueError) as exc:
        raise Refused(f"could not read {rel}: {exc}. Nothing is reported: a census "
                      "over the files that happened to parse would under-report it") from exc


def _pairs(stores: list[Site], compares: list[Site]) -> list[Candidate]:
    by_field: dict[str, list[Site]] = collections.defaultdict(list)
    for site in compares:
        by_field[site.field].append(site)
    found = {Candidate(store, compared) for store in stores
             for compared in by_field[store.field] if compared.file != store.file}
    return sorted(found, key=lambda c: (c.stored.field, c.compared.file, c.compared.line,
                                        c.compared.form, c.stored.file, c.stored.line))


def _order(site: Site) -> tuple[str, int, str, str]:
    """File, then line: the order a reader meets the sites in."""
    return (site.file, site.line, site.field, site.form)


def census(root: pathlib.Path | str) -> dict:
    """Stores, ordered field comparisons and their cross-module name matches
    under `root`.  REFUSES rather than returning a partial census."""
    root = pathlib.Path(root)
    if not root.exists():
        raise Refused(f"{root} does not exist; no census was taken")
    if not root.is_dir():
        raise Refused(f"{root} is not a directory; no census was taken")
    missing = [f"{d}/" for d in SCAN_DIRS if not (root / d).is_dir()]
    if missing:
        raise Refused(f"{root} has no {' and no '.join(missing)} directory, so the tree is "
                      "partial: a pair needs a producer and a consumer, and half a tree "
                      "cannot hold one")
    files, test_files = [], 0
    for d in SCAN_DIRS:
        for path in sorted((root / d).rglob("*.py")):
            if path.name.startswith("test_") or path.name.endswith("_test.py"):
                test_files += 1
            else:
                files.append(path)
    if not files:
        raise Refused(f"{root}: scanned 0 non-test Python files under "
                      f"{' and '.join(SCAN_DIRS)} ({test_files} test files skipped). An "
                      "empty report would read as a clean census")
    stores: list[Site] = []
    compares: list[Site] = []
    for path in files:
        rel, tree = _parse(path, root)
        stores.extend(_stores(tree, rel))
        compares.extend(_compares(tree, rel))
    return {"root": str(root), "files": len(files), "test_files": test_files,
            "stores": sorted(stores, key=_order), "compares": sorted(compares, key=_order),
            "candidates": _pairs(stores, compares)}


def report(result: dict) -> None:
    """The census as text: the population, one block per field, what a block
    means.  A block lists each store and comparison site once.  The pairs
    behind it are the cross product of the two lists, less whatever shares a
    file; printed one by one, a site repeats as many times as it has partners,
    which for a name like `value` on this tree is hundreds of times.  Written
    to whatever `sys.stdout` is at the call, never to a stream bound at
    import."""
    print(f"root={result['root']}  files={result['files']}  "
          f"test-files-skipped={result['test_files']}  "
          f"rounded-stores={len(result['stores'])}  "
          f"ordered-field-compares={len(result['compares'])}  "
          f"cross-module-pairs={len(result['candidates'])}")
    stored_in: dict[str, list[Site]] = {}
    compared_in: dict[str, list[Site]] = {}
    counts: dict[str, int] = {}
    for c in result["candidates"]:
        for index, site in ((stored_in, c.stored), (compared_in, c.compared)):
            sites = index.setdefault(c.stored.field, [])
            if site not in sites:
                sites.append(site)
        counts[c.stored.field] = counts.get(c.stored.field, 0) + 1
    for field in sorted(counts):
        print(f"{field}  cross-module-pairs={counts[field]}")
        for role, sites in (("stored", stored_in[field]), ("compared", compared_in[field])):
            for site in sorted(sites, key=_order):
                print(f"    {role:<8} [{site.form:<9}] {site.file}:{site.line}  {site.text}")
    print("a block is a NAME shared by a rounded store and an ordered comparison. A pair is one "
          "of each in DIFFERENT modules: a candidate to read, not a finding")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Fields stored through round() in one module and compared with "
                    ">=, <=, > or < in another. Candidates for inspection, not findings.")
    parser.add_argument("root", type=pathlib.Path,
                        help="source tree holding tools/ and model/")
    args = parser.parse_args(argv)
    try:
        result = census(args.root)
    except Refused as exc:
        print(f"REFUSED -- {exc}", file=sys.stderr)
        return exit_contract.REFUSED
    report(result)
    return exit_contract.PASS


if __name__ == "__main__":
    sys.exit(main())
