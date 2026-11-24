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
    refusals because run_case.Refused is the same object).

WHAT IT CANNOT TELL YOU, and why the per-site list is an inventory, not a
verdict. A dotted call is not necessarily a raising call on the CLI's input
space: `rs_guard_band.both_records` calls `rc.render_drum_solo` (which can
refuse) but no CLI input of that probe reaches its refusal arms. Reachability
is decided by tracing the caller with a known-answer input, as #1160 and
#1162's closing comments do per site. Blind spots, deliberately accepted:

  * only raises written in the CALLED DEF's own subtree are collected; a
    refusal raised one frame deeper (the def calls a helper that raises)
    is invisible unless the helper is the credited def;
  * function-level imports are attributed to the whole file; a local name
    shadowing an import can misattribute;
  * handlers are found around the CALL SITE only -- a guard higher up the
    call chain (typically the CLI dispatch, which is where #1162's escape
    lived) is not modelled. `unguarded` therefore means "unguarded here",
    never "escapes at the exit".
  * test files are excluded: they are not CLI callers and their
    `pytest.raises` sites are deliberate.

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
SCAN_ROOTS = ("tools", "model", "pnr")          # tools/ includes tools/probes/
FAMILY_NAME = "Refused"


# ---------------------------------------------------------------------------
# module index: every tracked production .py, by importable stem
# ---------------------------------------------------------------------------
def tracked_files() -> list[pathlib.Path]:
    out = subprocess.run(["git", "-C", str(ROOT), "ls-files", "*.py"],
                         capture_output=True, text=True, check=True)
    return [ROOT / p for p in out.stdout.split()
            if "/test_" not in p and not p.startswith("test_")]


class ModInfo:
    def __init__(self, path: pathlib.Path):
        self.path = path
        self.tree = ast.parse(path.read_text(), filename=str(path))
        self.imports: dict[str, str] = {}        # local name -> module stem
        self.from_imports: dict[str, str] = {}   # local name -> module stem
        self.funcs: dict[str, ast.FunctionDef] = {}
        self.refused_origin: str | None = None   # stem whose class our Refused is
        self.refused_names: set[str] = set()     # names bound to our Refused
        self._read_bindings()

    def _read_bindings(self) -> None:
        for node in self.tree.body:
            if isinstance(node, ast.Import):
                for a in node.names:
                    stem = a.name.split(".")[0]
                    self.imports[a.asname or stem] = stem
            elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                stem = node.module.split(".")[0]
                for a in node.names:
                    self.from_imports[a.asname or a.name] = stem
            elif isinstance(node, ast.ClassDef) and node.name == FAMILY_NAME:
                self.refused_origin = self.path.stem      # a local class
                self.refused_names.add(FAMILY_NAME)
            elif isinstance(node, ast.Assign):
                for t in node.targets:
                    if (isinstance(t, ast.Name) and t.id == FAMILY_NAME
                            and isinstance(node.value, ast.Attribute)
                            and isinstance(node.value.value, ast.Name)):
                        src = self.from_imports.get(node.value.value.id,
                                                    node.value.value.id)
                        self.refused_origin = src
                        self.refused_names.add(FAMILY_NAME)
        for node in ast.walk(self.tree):
            if isinstance(node, ast.FunctionDef):
                self.funcs.setdefault(node.name, node)


def build_index(files: list[pathlib.Path]) -> dict[str, ModInfo]:
    idx: dict[str, ModInfo] = {}
    for f in files:
        try:
            idx.setdefault(f.stem, ModInfo(f))
        except SyntaxError:
            continue                              # recorded scope loss, not a crash
    return idx


def origin_of(idx: dict[str, ModInfo], stem: str, seen: frozenset = frozenset()) -> str | None:
    """Resolve a module's family to the stem that DEFINES the class."""
    m = idx.get(stem)
    if m is None or stem in seen:
        return stem if m and m.refused_origin == stem else m.refused_origin if m else None
    if m.refused_origin == stem or m.refused_origin is None:
        return m.refused_origin
    return origin_of(idx, m.refused_origin, seen | {stem})


def raise_origins(mod: ModInfo, fname: str) -> set[str]:
    """The family origins the named def of `mod` raises itself."""
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
            if txt == FAMILY_NAME and mod.refused_origin:
                out.add(mod.refused_origin)
            elif "." in txt:
                head = txt.split(".")[0]
                stem = mod.imports.get(head) or mod.from_imports.get(head) or head
                out.add(stem)
    return out


# ---------------------------------------------------------------------------
# per-call-site analysis
# ---------------------------------------------------------------------------
def enclosing_try_parents(tree: ast.AST, call: ast.AST) -> list[ast.Try]:
    parents: dict[ast.AST, ast.AST] = {}
    for node in ast.walk(tree):
        for ch in ast.iter_child_nodes(node):
            parents[ch] = node
    out, cur = [], call
    while cur in parents:
        cur = parents[cur]
        if isinstance(cur, ast.Try):
            out.append(cur)
    return out


def handler_origins(mod: ModInfo, idx: dict[str, ModInfo],
                    handler: ast.ExceptHandler) -> tuple[set[str], str]:
    """(origins this handler catches, shape) -- shape in
    {base, exact, local, broad, other}."""
    t = handler.type
    if t is None:
        return set(), "broad"
    parts = t.elts if isinstance(t, ast.Tuple) else [t]
    origins: set[str] = set()
    shapes = set()
    for p in parts:
        txt = ast.unparse(p)
        if txt == "Exception" or txt == "BaseException":
            shapes.add("broad")
        elif txt == f"refusal.{FAMILY_NAME}":
            shapes.add("base")
        elif "." in txt:
            head = txt.split(".")[0]
            stem = mod.imports.get(head) or mod.from_imports.get(head) or head
            if txt.endswith(f".{FAMILY_NAME}"):
                o = origin_of(idx, stem) or stem
                origins.add(o)
                shapes.add("exact")
            else:
                shapes.add("other")
        elif txt in mod.refused_names:
            o = origin_of(idx, mod.refused_origin or mod.path.stem)
            if o:
                origins.add(o)
            shapes.add("local")
        else:
            shapes.add("other")
    shape = ("base" if "base" in shapes else "broad" if "broad" in shapes
             else "other" if shapes == {"other"} or "other" in shapes and not origins
             else "+".join(sorted(shapes - {"other"})))
    return origins, shape


def scan(files: list[pathlib.Path], idx: dict[str, ModInfo]) -> list[dict]:
    sites: list[dict] = []
    for f in files:
        mod = idx.get(f.stem)
        if mod is None:
            continue
        for node in ast.walk(mod.tree):
            callee_stem = fname = None
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and isinstance(node.func.value, ast.Name)):
                head = node.func.value.id
                callee_stem = mod.imports.get(head) or mod.from_imports.get(head)
                fname = node.func.attr
            elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                callee_stem = mod.from_imports.get(node.func.id)
                fname = node.func.id
            if callee_stem is None or callee_stem == f.stem:
                continue
            callee = idx.get(callee_stem)
            if callee is None or not callee.refused_names:
                continue                      # callee has no family at all
            origins = raise_origins(callee, fname or "")
            if not origins:
                continue                      # this function raises no refusal
            covered, shapes = set(), []
            for tr in enclosing_try_parents(mod.tree, node):
                for h in tr.handlers:
                    ho, shape = handler_origins(mod, idx, h)
                    if shape in ("base", "broad"):
                        covered |= origins
                        shapes.append(shape)
                    elif shape != "other":
                        covered |= ho & origins
                        shapes.append(shape)
            uncovered = origins - covered
            try:
                label = str(f.relative_to(ROOT))
            except ValueError:
                label = str(f)                    # synthetic trees in tests
            sites.append(dict(
                site=f"{label}:{node.lineno}",
                callee=f"{callee_stem}.{fname}",
                origins=sorted(origins),
                guard=",".join(shapes) or "none",
                classification="candidate-escape" if uncovered else f"covered({'+'.join(shapes) or 'none-needed'})",
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
        print(f"{s['site']:58s} {s['callee']:34s} origins={','.join(s['origins']):30s} "
              f"guard={s['guard']:12s} {s['classification']}{unc}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
