#!/usr/bin/env python3
"""Did a change to `stage_controls` move the healthy control block? (#2141)

    python3 tools/probes/ladder_controls_before_after.py --before origin/main --quick
    python3 tools/probes/ladder_controls_before_after.py --before origin/main
    python3 tools/probes/ladder_controls_before_after.py --self-test

WHAT IT ANSWERS. `stage_controls` in `tools/compare_ladder_candidates.py`
assembles the START RED and INJECTED DEFECTS blocks out of rows its six
stages return. A change to that assembly must leave every field a healthy run
already printed exactly as it was (#2141, acceptance criterion 3). The tool
takes about four minutes on the quick grid and twenty on the full one, so
running it twice and diffing two JSON files is one way to learn that. This is
the other, and it needs ONE pass of the stages:

  1. the working tree's `stage_controls` runs once on real input over the
     real cores, with every stage call recorded -- the stage's name, its
     arguments and the row it returned, in order;
  2. the `--before` revision's `stage_controls`, read with `git show`, is
     answered from that recording, and REFUSES if it does not ask for the
     same stages with the same arguments in the same order;
  3. the two `controls` dicts are compared leaf by leaf as JSON text, so `1`
     against `1.0` is a change and so is anything that vanished.

VERDICTS, from `tools/exit_contract.py`. `IDENTICAL` (0): no existing leaf
changed or vanished; the leaves the change ADDS are listed. `CHANGED` (1).
`REFUSED` (2): a precondition failed, and nothing is compared in its place.

WHAT IT CANNOT SEE, AND SO REFUSES TO ANSWER FOR. Both assemblies are fed
the working tree's rows. That answers "did the assembly move the block?"
exactly, and nothing wider: a change INSIDE a stage, or inside what a stage
measures with, moves the rows under both assemblies and would read
`IDENTICAL` here. That is this probe's defeating input (verification-rules
rule 8), so it is a precondition instead of a footnote. Unless
`--assembly-only` is given, it REFUSES when

  * anything under `model/` or `audition/` differs between `--before` and the
    working tree, or
  * any top-level definition of the tool other than `stage_controls` has
    different source in the two trees, or was removed.

`--assembly-only` drops those and narrows the claim to the first sentence of
this section.

--self-test is the probe's own control, on a one-point grid and one pass of
the stages. The recording replayed into an unmodified copy of the assembly
reads IDENTICAL, which is what lets any other reading be believed; the same
recording into a copy whose move is nudged by 0.01, and into one whose move
always refuses, each read CHANGED; a truncated recording is REFUSED; and a
tree whose `stage_linear`, or whose `model/`, differs is REFUSED before any
stage runs. Exit 0 iff every one of those lands.
"""
from __future__ import annotations

import argparse
import ast
import json
import os
import subprocess
import sys
import types

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path[:0] = [os.path.join(ROOT, p) for p in ("tools", "model", "audition")]

from exit_contract import FAIL, PASS, REFUSED                              # noqa: E402

TOOL = os.path.join("tools", "compare_ladder_candidates.py")
#: the stages `stage_controls` assembles from, and the trees they measure with
STAGES = ("stage_linear", "stage_resonance", "stage_bass", "stage_drive",
          "stage_movement", "stage_cleanliness")
MEASURES_IN = ("model", "audition")
#: the one definition a change compared here is allowed to live in
ASSEMBLY = "stage_controls"


class ProbeRefused(Exception):
    """A precondition failed. There is no verdict to put in its place."""


def _git(*args: str) -> str:
    p = subprocess.run(["git", "-C", ROOT, *args], capture_output=True, text=True)
    if p.returncode != 0:
        raise ProbeRefused(f"git {' '.join(args)} failed: {p.stderr.strip()}")
    return p.stdout


def _load(source: str, name: str):
    """A module object running `source`, compiled as the tool at its real path
    so the `sys.path` setup at the top of the file lands where it does for the
    file itself."""
    mod = types.ModuleType(name)
    mod.__file__ = os.path.join(ROOT, TOOL)
    exec(compile(source, mod.__file__, "exec"), mod.__dict__)
    return mod


def _defs(source: str) -> tuple:
    """A module's top level in two parts: definitions by name (a function, a
    class, an assigned constant) with their source text, and the source of
    every other statement -- the imports, the `sys.path` calls -- in order."""
    named, plain = {}, []
    for node in ast.parse(source).body:
        text = ast.get_source_segment(source, node)
        if isinstance(node, (ast.FunctionDef, ast.ClassDef)):
            named[node.name] = text
        elif isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name):
            named[node.targets[0].id] = text
        else:
            plain.append(text)
    return named, plain


def _preconditions(before: str, after: str, drift: list) -> list:
    """Why the two trees' stage ROWS may differ; empty when they may not.

    Both assemblies are fed the working tree's rows. A change inside a stage,
    or inside what a stage measures with, moves the rows under both and would
    read IDENTICAL, so it is refused here rather than footnoted there."""
    (b, b_plain), (a, a_plain) = _defs(before), _defs(after)
    why = [f"{p} differs between --before and the working tree" for p in drift]
    if b_plain != a_plain:
        why.append("the tool's imports or path setup differ between the two trees")
    why += [f"{n} differs between the two trees" for n in sorted(b.keys() & a.keys())
            if n != ASSEMBLY and b[n] != a[n]]
    why += [f"{n} was removed" for n in sorted(b.keys() - a.keys())]
    return why


def _grid(mod, quick: bool) -> tuple:
    """The grids `compare()` hands to `stage_controls`."""
    return (mod.LINEAR_CUTS_QUICK if quick else mod.LINEAR_CUTS, mod.LINEAR_RES,
            mod.RING_CUTS_QUICK if quick else mod.RING_CUTS, mod.RING_RES_QUICK if quick else mod.RING_RES)


def _record(mod, grid: tuple) -> tuple:
    """Run `mod`'s `stage_controls` once and keep every stage call it makes:
    the stage's name, its arguments and the row it returned, in order."""
    tape = []
    for name in STAGES:
        def wrapped(*a, _name=name, _stage=getattr(mod, name), **kw):
            row = _stage(*a, **kw)
            tape.append((_name, a, kw, row))
            return row
        setattr(mod, name, wrapped)
    return mod.stage_controls(*grid), tape


def _replay(mod, grid: tuple, tape: list) -> dict:
    """Answer `mod`'s `stage_controls` from `tape`, refusing unless it asks
    for the same stages, with the same arguments, in the same order."""
    calls = iter(tape)

    def stub(name):
        def answer(*a, **kw):
            call = next(calls, None)
            if call is None:
                raise ProbeRefused("stage_controls made more stage calls than the recording holds")
            want, want_a, want_kw, row = call
            if (want, want_a, want_kw) != (name, a, kw):
                raise ProbeRefused(
                    "the two stage_controls do not call the same stages the same way: "
                    f"this one asked {name}({a!r}, {kw!r}); the recording holds "
                    f"{want}({want_a!r}, {want_kw!r})")
            return row
        return answer

    for name in STAGES:
        setattr(mod, name, stub(name))
    out = mod.stage_controls(*grid)
    if next(calls, None) is not None:
        raise ProbeRefused("stage_controls made fewer stage calls than the recording holds")
    return out


def _leaves(x, path: str = ""):
    """`path -> JSON text` over every leaf of a report fragment, so `1` against
    `1.0` is a change and so is anything that vanished."""
    if isinstance(x, dict):
        for k in sorted(x):
            yield from _leaves(x[k], f"{path}.{k}" if path else str(k))
    elif isinstance(x, list):
        for i, v in enumerate(x):
            yield from _leaves(v, f"{path}[{i}]")
    else:
        yield path, json.dumps(x, sort_keys=True, default=str)


def _compare(before: dict, after: dict) -> dict:
    b, a = dict(_leaves(before)), dict(_leaves(after))
    both = b.keys() & a.keys()
    return dict(
        identical=sum(b[k] == a[k] for k in both),
        changed=sorted(f"{k}: {b[k]} -> {a[k]}" for k in both if b[k] != a[k]),
        added=sorted(f"{k}: {a[k]}" for k in a.keys() - b.keys()),
        removed=sorted(f"{k}: {b[k]}" for k in b.keys() - a.keys()))


def _show(tag: str, diff: dict) -> int:
    print(f"  {tag}: {diff['identical']} identical, {len(diff['changed'])} changed, "
          f"{len(diff['added'])} added, {len(diff['removed'])} removed")
    for kind in ("changed", "removed", "added"):
        for line in diff[kind]:
            print(f"      {kind:8s} {line}")
    return PASS if not (diff["changed"] or diff["removed"]) else FAIL


def _before_after(rev: str, quick: bool, assembly_only: bool) -> int:
    try:
        sha = _git("rev-parse", "--verify", f"{rev}^{{commit}}").strip()
        before_src = _git("show", f"{sha}:{TOOL}")
        with open(os.path.join(ROOT, TOOL)) as fh:
            after_src = fh.read()
        edited = _git("status", "--porcelain", "--", TOOL).strip()
        drift = sorted(set(
            _git("diff", "--name-only", sha, "--", *MEASURES_IN).split()
            + _git("ls-files", "--others", "--exclude-standard", "--", *MEASURES_IN).split()))
        why = [] if assembly_only else _preconditions(before_src, after_src, drift)
    except (ProbeRefused, OSError) as exc:
        print(f"REFUSED: {exc}")
        return REFUSED
    print(f"before: {rev} @ {sha[:8]}   after: the working tree{', edited' if edited else ''}")
    if why:
        print("REFUSED. Both assemblies would be fed the working tree's stage rows, and "
              "those may not be the rows --before produced:")
        for line in why:
            print(f"      {line}")
        print("  nothing was compared. --assembly-only compares the assembly alone.")
        return REFUSED
    if assembly_only:
        print("  claim narrowed by --assembly-only: the assembly over the working tree's rows, nothing wider")
    try:
        new = _load(after_src, "ladder_controls_after")
        grid = _grid(new, quick)
        after, tape = _record(new, grid)
        before = _replay(_load(before_src, "ladder_controls_before"), grid, tape)
    except ProbeRefused as exc:
        print(f"REFUSED: {exc}")
        return REFUSED
    print(f"grid: {'quick' if quick else 'full'}   stage calls recorded and replayed: {len(tape)}")
    code = _show("controls", _compare(before, after))
    print(f"VERDICT: {'IDENTICAL in every existing field' if code == PASS else 'CHANGED'}")
    return code


def _self_test() -> int:
    with open(os.path.join(ROOT, TOOL)) as fh:
        src = fh.read()
    grid = ((400.0,), (0.9,), (400.0,), (1.05,))
    after, tape = _record(_load(src, "ladder_controls_self_after"), grid)
    nudged = _load(src, "ladder_controls_self_nudged")
    nudged._moved = lambda d, b, key, n, rows: round(abs(d[key] - b[key]), n) + 0.01
    deaf = _load(src, "ladder_controls_self_deaf")
    deaf._moved = lambda *a, **kw: "REFUSED"
    moved_stage = src.replace('default=float("nan")', "default=0.0", 1)

    def refusal(fn):
        try:
            fn()
        except ProbeRefused as exc:
            return str(exc)
        return None

    same = _compare(after, _replay(_load(src, "ladder_controls_self_same"), grid, tape))
    cases = [
        ("an unmodified copy of the assembly reads IDENTICAL",
         not (same["changed"] or same["added"] or same["removed"]), same),
        ("a move nudged by 0.01 reads CHANGED",
         bool(_compare(after, _replay(nudged, grid, tape))["changed"]), None),
        ("a move that always refuses reads CHANGED",
         bool(_compare(after, _replay(deaf, grid, tape))["changed"]), None),
        ("a truncated recording is REFUSED",
         refusal(lambda: _replay(_load(src, "ladder_controls_self_short"), grid, tape[:-1])) is not None,
         None),
        ("a stage whose source differs is REFUSED before any stage runs",
         moved_stage != src and bool(_preconditions(src, moved_stage, [])), None),
        ("a model/ that differs is REFUSED before any stage runs",
         bool(_preconditions(src, src, ["model/ladder_candidates.py"])), None),
        ("the tree against itself raises no precondition",
         _preconditions(src, src, []) == [], None),
    ]
    bad = 0
    for name, ok, detail in cases:
        print(f"  {'ok  ' if ok else 'FAIL'} {name}" + (f"  {detail}" if not ok and detail else ""))
        bad += not ok
    print(f"SELF-TEST: {'every case landed' if not bad else str(bad) + ' case(s) did not'}")
    return PASS if not bad else FAIL


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--before", default="origin/main", metavar="REV",
                    help="the revision whose stage_controls the working tree's is held against")
    ap.add_argument("--quick", action="store_true", help="the tool's quick grid")
    ap.add_argument("--assembly-only", action="store_true",
                    help="compare the assembly over the working tree's rows even where a stage, "
                         "or what it measures with, differs")
    ap.add_argument("--self-test", action="store_true",
                    help="the probe's own controls, on a one-point grid")
    a = ap.parse_args(argv)
    return _self_test() if a.self_test else _before_after(a.before, a.quick, a.assembly_only)


if __name__ == "__main__":
    sys.exit(main())
