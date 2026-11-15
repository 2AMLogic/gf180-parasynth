#!/usr/bin/env python3
"""fpga/record_compare.py -- the ONE comparison behind every release gate's
BOUND/STALE verdict, and the one repo-relative path helper those gates print
with (#785).

Before this module there were four hand-copied comparators, and **two of them
disagreed on the same input**. Extracting both and running them side by side
(the control case last, so the table is shown to discriminate):

    case                                             r1/_diff   r2_candidate
    field dropped from derivation vs committed null   BOUND      STALE   *** NO ***
    nested field dropped vs null                      BOUND      STALE   *** NO ***
    int 1 vs bool true                                BOUND      BOUND   agree
    int 1 vs float 1.0                                BOUND      BOUND   agree
    real value change (control)                       STALE      STALE   agree

The cause of the first two rows: the recursive copy walked
`sorted(set(a) | set(b))` and recursed with `a.get(k)` / `b.get(k)`, so **a key
absent on one side was indistinguishable from a key present with value
`null`**. Python's `!=` on whole dicts does not have that hole, which is why
`r2_candidate`'s shallower variant caught what the other three missed.

That hole was live, not theoretical: `fpga/release/r1-candidate.json` ships 3
explicit dict-keyed nulls and `fpga/release/baseline-2025.1.json` ships 5, so a
`build()` that stopped emitting one of those keys left every recursive gate
reporting BOUND on a record that no longer equalled its derivation. That is
`docs/verification-rules.md` rule 8 exactly -- a guard defeated by the
pathology it exists to catch. The controls are
`fpga/release/test_r1_candidate.py` and `fpga/release/test_release_manifest.py`
(`test_control_a_key_dropped_from_the_derivation_is_stale_not_bound`), which
drive `check()` rather than this helper, because a unit test on the comparator
proves the comparator works, not that the gate would have noticed.

Rows 3 and 4 were shared by all four copies, so neither existing variant was
simply "the correct one" to defer to.

---------------------------------------------------------------------------
DECISION: equality here is TYPE-STRICT. `1` differs from `True`; `1` differs
from `1.0`.
---------------------------------------------------------------------------

Python's `==` coerces -- `1 == True == 1.0` is all True -- and that is the
wrong equality for these records, for two reasons that are local to them and
need no wider design argument:

1. **Both sides are JSON round-tripped, and JSON preserves the distinction.**
   Every gate compares `json.loads(path.read_text())` against
   `json.loads(json.dumps(build()))`. `json.dumps` writes `1`, `true` and
   `1.0` as three different tokens and `json.loads` reads them back as `int`,
   `bool` and `float`. So type-strict equality is exactly *serialisation*
   equality, and serialisation equality is the question the gate asks: does
   this committed file equal a fresh derivation?

2. **These records are digest-bearing.** `release_manifest.sha_json` hashes
   `json.dumps(obj, sort_keys=True, default=list)`, and those digests are what
   bind a release to its artifacts. A value whose type changed hashes
   differently. A comparator looser than the record's own digest mechanism
   contradicts it, and would call a record BOUND that its own digests say has
   moved.

So a `build()` that starts emitting a count as `true` where the committed
record holds `1` reads STALE, which is correct: the file it would now write is
a different file.

---------------------------------------------------------------------------
WHAT DEFEATS THIS GUARD (rule 8 -- asked at write time, answered here)
---------------------------------------------------------------------------

Two inputs satisfy `diff(...) == []` while the committed file's bytes are not
what `build()` would now write. Both are deliberate, and both are controlled
in `fpga/test_record_compare.py`:

* **Dict key order.** `{"a": 1, "b": 2}` and `{"b": 2, "a": 1}` compare equal
  here, so a `build()` that merely reordered its keys reads BOUND. That is the
  intended reading: the record's *content* is identical, and the gate asks
  whether the record equals its derivation, not whether the file is
  byte-identical. Byte identity is covered elsewhere and by a stronger
  mechanism -- `r1_release.host_binding()` hashes `r1-candidate.json`'s actual
  bytes into `r1-2025.1.json`, so a byte-level change to a record IS caught, by
  the gate above it.

* **A change inside a list is reported at the list's path, not its index.**
  `diff` compares lists as single units (type-strictly, and recursively, so the
  coercion holes above are closed inside them too) rather than walking indices.
  Nothing is missed; the reported path is coarser. The committed records' nulls
  inside lists -- `presets.*.waves[1..2]` -- therefore never contributed to the
  absent-vs-null hole in the first place, because the hole was in dict
  recursion.

One further stated behaviour, because this repository has been bitten by it
before (#134, ~108 comparison guards against 4 finite checks): a `float('nan')`
anywhere in a record makes every comparison of it unequal to itself, so the
gate reads **STALE forever**. That direction is loud and safe -- a gate that
cannot go BOUND gets fixed -- and no record ships a NaN today; `json.dumps`
would have to write the non-standard `NaN` token for one to exist. It is
recorded here rather than silently special-cased so that the next reader knows
it is a decision and not an oversight.
"""
from __future__ import annotations

from pathlib import Path

__all__ = ["rel", "diff", "equal"]


def rel(p, root) -> str:
    """`p` as a path relative to `root`, or absolute when it lies outside.

    A test's substituted copy (a `tmp_path` record) is outside the tree, and
    these gates print the path in their own REFUSED/STALE/BOUND detail, so
    falling back to the absolute path keeps a check reporting instead of
    crashing on `relative_to`.
    """
    p = Path(p).resolve()
    root = Path(root).resolve()
    return str(p.relative_to(root)) if p.is_relative_to(root) else str(p)


def equal(a, b) -> bool:
    """Type-strict structural equality -- see this module's DECISION block.

    Distinguishes `1` / `True` / `1.0`, and a dict missing a key from one
    holding it as `None`, at every depth including inside lists.
    """
    if type(a) is not type(b):
        return False
    if isinstance(a, dict):
        return set(a) == set(b) and all(equal(a[k], b[k]) for k in a)
    if isinstance(a, (list, tuple)):
        return len(a) == len(b) and all(equal(x, y) for x, y in zip(a, b))
    return a == b


def diff(a, b, path="") -> list:
    """The dotted paths at which committed record `a` differs from derivation
    `b`, deepest-key-first within each level and sorted by key.

    Recurses while BOTH sides are dicts and reports a key present on only one
    side AT THAT KEY -- the absent-vs-`null` repair. Everything else is
    compared with `equal`, so a list or scalar difference is reported at its
    own path.
    """
    if isinstance(a, dict) and isinstance(b, dict):
        out = []
        for k in sorted(set(a) | set(b)):
            here = f"{path}.{k}" if path else k
            if (k in a) != (k in b):
                out.append(here)                 # absent on one side: not a value compare
            else:
                out += diff(a[k], b[k], here)
        return out
    return [] if equal(a, b) else [path]
