#!/usr/bin/env python3
"""Inventory tracked JSON records and the Python sites that write them (#922).

    python tools/audit_tracked_json_writers.py [--dump state.json]

A confirmed site needs an exact repository path in the destination of a JSON
write. Resolve module constants relative to __file__, including imported
constants, pathlib joins and os.path.join. For json.dump, follow a direct
open(..., "w") or its with-bound handle. Atomic sites use the project's
write_json_atomic helper or a local write_json seam that forwards its path.

INLINE takes precedence over atomic. Question marks denote candidate paths
(a literal suffix in an unknown directory, or a dynamic name in a known
directory). An exact atomic writer with possible inline evidence is atomic?.
no-writer means this scan found no matching site. It can miss runtime path
construction, indirect handles, pre-serialized payloads and other languages.
It does not run the source it inspects. Tests and the atomic primitive are
excluded. The inventory is a report, not a gate on the number of remainders.
"""
from __future__ import annotations

import argparse
import ast
from collections import Counter, defaultdict
from dataclasses import dataclass
import json
import pathlib
import posixpath
import re
import subprocess

ROOT = pathlib.Path(__file__).resolve().parent.parent
SKIP = re.compile(r"(^|/)test_[^/]*\.py$|(^|/)tests/|^tools/run_all\.py$"
                  r"|^tools/audit_tracked_json_writers\.py$")
UNKNOWN = "\0"  # impossible in a real path


def tracked(suffix):
    result = subprocess.run(["git", "-C", ROOT, "ls-files", "-z", "--", "*" + suffix],
                            check=True, stdout=subprocess.PIPE)
    return [path.decode() for path in result.stdout.split(b"\0") if path]


def argument(call, position, keyword):
    if position < len(call.args):
        return call.args[position]
    return next((kw.value for kw in call.keywords if kw.arg == keyword), None)


class Module:
    """The top-level definitions used to resolve a destination without imports."""

    def __init__(self, src):
        self.src = src
        self.tree = ast.parse((ROOT / src).read_bytes(), filename=src)
        self.constants = {}
        self.imports = {}
        self.helpers = {}
        for stmt in self.tree.body:
            if isinstance(stmt, ast.Import):
                for alias in stmt.names:
                    self.imports[alias.asname or alias.name] = alias.name
            if isinstance(stmt, ast.FunctionDef) and len(stmt.body) == 1:
                if isinstance(stmt.body[0], ast.Return):
                    self.helpers[stmt.name] = stmt
            if isinstance(stmt, ast.Assign):
                for target in stmt.targets:
                    self.bind(target, stmt.value)
            if isinstance(stmt, ast.AnnAssign):
                self.bind(stmt.target, stmt.value)

    def bind(self, target, value):
        if isinstance(target, ast.Name) and target.id.isupper():
            self.constants[target.id] = value
        elif isinstance(target, ast.Tuple) and isinstance(value, ast.Tuple):
            for name, expr in zip(target.elts, value.elts):
                self.bind(name, expr)


class Paths:
    def __init__(self, modules):
        self.by_name = defaultdict(list)
        for module in modules:
            self.by_name[pathlib.Path(module.src).stem].append(module)

    def imported(self, module, alias):
        candidates = self.by_name.get(module.imports.get(alias), ())
        local = [m for m in candidates if posixpath.dirname(m.src) == posixpath.dirname(module.src)]
        matches = local or candidates
        return matches[0] if len(matches) == 1 else None

    def resolve(self, node, module, shadowed=frozenset(), seen=frozenset()):
        """Return a path expression with unknown pieces marked, never evaluated."""
        if node is None or node in seen:
            return UNKNOWN
        seen = seen | {node}

        def path(child):
            return self.resolve(child, module, shadowed, seen)

        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return node.value
        if isinstance(node, ast.JoinedStr):
            return "".join(v.value if isinstance(v, ast.Constant) else UNKNOWN
                           for v in node.values)
        if isinstance(node, ast.Name) and node.id not in shadowed:
            if node.id == "__file__":
                return str(ROOT / module.src)
            return self.resolve(module.constants.get(node.id), module, seen=seen)
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
            return posixpath.join(path(node.left), path(node.right))
        if isinstance(node, ast.Attribute):
            if node.attr == "parent":
                base = path(node.value)
                return posixpath.dirname(base) if UNKNOWN not in base else UNKNOWN
            if isinstance(node.value, ast.Name) and node.value.id not in shadowed:
                other = self.imported(module, node.value.id)
                if other:
                    return self.resolve(other.constants.get(node.attr), other, seen=seen)
        if (isinstance(node, ast.Subscript) and isinstance(node.value, ast.Attribute)
                and node.value.attr == "parents" and isinstance(node.slice, ast.Constant)
                and isinstance(node.slice.value, int) and node.slice.value >= 0):
            base = path(node.value.value)
            parents = pathlib.PurePosixPath(base).parents
            if UNKNOWN not in base and node.slice.value < len(parents):
                return str(parents[node.slice.value])
        if isinstance(node, ast.Call):
            fn = ast.unparse(node.func)
            if fn == "os.path.join" and node.args:
                return posixpath.join(*(path(a) for a in node.args))
            if fn in ("Path", "pathlib.Path", "str", "os.path.abspath") and len(node.args) == 1:
                return path(node.args[0])
            if fn == "os.path.dirname" and len(node.args) == 1:
                base = path(node.args[0])
                return posixpath.dirname(base) if UNKNOWN not in base else UNKNOWN
            if isinstance(node.func, ast.Attribute) and node.func.attr == "resolve" and not node.args:
                return path(node.func.value)
            if fn in module.helpers and fn not in shadowed:
                helper = module.helpers[fn]
                params = {n.arg for n in ast.walk(helper.args) if isinstance(n, ast.arg)}
                return self.resolve(helper.body[0].value, module, params, seen)
        return UNKNOWN


def matcher(path):
    """Return (exact, regex) for anchored paths or meaningful uncertain suffixes."""
    if ".." in path.split("/") and UNKNOWN in path:
        return False, None
    path = posixpath.normpath(path)
    if path.startswith("/"):
        try:
            rel = pathlib.PurePosixPath(path).relative_to(ROOT).as_posix()
        except ValueError:
            return False, None
        return UNKNOWN not in rel, re.compile(re.escape(rel).replace(UNKNOWN, "[^/]*"))
    # Without a known root only a literal trailing name supplies evidence.
    parts = []
    for part in reversed(path.split("/")):
        if UNKNOWN in part:
            break
        parts.append(part)
    if not parts or not parts[0].endswith(".json"):
        return False, None
    suffix = "/".join(reversed(parts))
    return False, re.compile(r"(?:.*/)?" + re.escape(suffix))


def opened_path(node):
    """Recognize truncating open modes. Append, read and exclusive create differ."""
    if not isinstance(node, ast.Call):
        return None
    if isinstance(node.func, ast.Name) and node.func.id == "open":
        target, mode = argument(node, 0, "file"), argument(node, 1, "mode")
    elif isinstance(node.func, ast.Attribute) and node.func.attr == "open":
        target, mode = node.func.value, argument(node, 0, "mode")
    else:
        return None
    if isinstance(mode, ast.Constant) and isinstance(mode.value, str) and "w" in mode.value:
        return target
    return None


@dataclass
class Site:
    kind: str
    location: str
    exact: bool
    pattern: re.Pattern | None


class WriterSites(ast.NodeVisitor):
    def __init__(self, module, paths):
        self.module = module
        self.paths = paths
        self.handles = {}
        self.shadowed = set()
        self.sites = []
        self.atomic_calls = {alias + ".write_json_atomic" for alias, name in module.imports.items()
                             if name == "run_all"}
        for stmt in module.tree.body:
            if isinstance(stmt, ast.FunctionDef) and stmt.name == "write_json" and stmt.args.args:
                param = stmt.args.args[0].arg
                body = stmt.body[1:] if ast.get_docstring(stmt) is not None else stmt.body
                if len(body) == 1 and isinstance(body[0], (ast.Expr, ast.Return)):
                    call = body[0].value
                    if isinstance(call, ast.Call) and ast.unparse(call.func) in self.atomic_calls:
                        dest = argument(call, 0, "path")
                        if dest and ast.unparse(dest) in (param, f"str({param})"):
                            self.atomic_calls.add("write_json")

    def visit_FunctionDef(self, node):
        # A local binding can hide a module path constant or an outer handle.
        handles, shadowed = self.handles, self.shadowed
        self.handles = {}
        self.shadowed = shadowed | {n.arg for n in ast.walk(node) if isinstance(n, ast.arg)}
        self.shadowed.update(n.id for n in ast.walk(node)
                             if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store))
        self.generic_visit(node)
        self.handles, self.shadowed = handles, shadowed

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_With(self, node):
        previous = self.handles
        self.handles = previous.copy()
        rebound = {n.id for stmt in node.body for n in ast.walk(stmt)
                   if isinstance(n, ast.Name) and isinstance(n.ctx, (ast.Store, ast.Del))}
        for item in node.items:
            if isinstance(item.optional_vars, ast.Name):
                name = item.optional_vars.id
                self.handles[name] = None if name in rebound else opened_path(item.context_expr)
        self.generic_visit(node)
        self.handles = previous

    def visit_Call(self, node):
        func = ast.unparse(node.func)
        dest = None
        kind = None
        if isinstance(node.func, ast.Attribute) and node.func.attr == "write_text":
            data = argument(node, 0, "data")
            if data and any(isinstance(n, ast.Call) and ast.unparse(n.func) == "json.dumps"
                            for n in ast.walk(data)):
                kind, dest = "inline", node.func.value
        elif func == "json.dump":
            kind = "inline"
            stream = argument(node, 1, "fp")
            if isinstance(stream, ast.Name):
                dest = self.handles.get(stream.id)
            else:
                dest = opened_path(stream)
        elif func in self.atomic_calls and func.split(".")[0] not in self.shadowed:
            kind, dest = "atomic", argument(node, 0, "path")
        if kind:
            exact, pattern = matcher(self.paths.resolve(dest, self.module, self.shadowed))
            self.sites.append(Site(kind, f"{self.module.src}:{node.lineno}", exact, pattern))
        self.generic_visit(node)


def classify(hits):
    inline = [s for s in hits if s.kind == "inline"]
    exact_inline = [s for s in inline if s.exact]
    exact_atomic = [s for s in hits if s.kind == "atomic" and s.exact]
    if exact_inline:
        return "INLINE", exact_inline
    if exact_atomic:
        return ("atomic?", exact_atomic + inline) if inline else ("atomic", exact_atomic)
    if inline:
        return "INLINE?", inline
    if hits:
        return "atomic?", hits
    return "no-writer", []


def scan(dump=None):
    records = tracked(".json")
    modules = [Module(src) for src in tracked(".py") if not SKIP.search(src)]
    paths = Paths(modules)
    sites = []
    for module in modules:
        visitor = WriterSites(module, paths)
        visitor.visit(module.tree)
        sites.extend(visitor.sites)

    state = {}
    matched = set()
    for rel in records:
        hits = [s for s in sites if s.pattern and s.pattern.fullmatch(rel)]
        matched.update(s.location for s in hits)
        kind, evidence = classify(hits)
        state[rel] = {"state": kind, "sites": sorted({s.location for s in evidence})}

    counts = Counter(row["state"] for row in state.values())
    ambiguous = counts["atomic?"] + counts["INLINE?"]
    total = counts["atomic"] + counts["INLINE"] + ambiguous + counts["no-writer"]
    assert total == len(records), "classification does not partition the tracked inventory"
    print(f"tracked JSONs: {total}  (atomic {counts['atomic']}, INLINE {counts['INLINE']}, "
          f"ambiguous {ambiguous}, no-writer {counts['no-writer']})")
    kinds = Counter(s.kind for s in sites)
    print(f"write sites in tracked sources: {kinds['atomic']} atomic, {kinds['inline']} inline")
    for title, states in [("REMAINDERS", {"INLINE"}), ("AMBIGUOUS", {"INLINE?", "atomic?"})]:
        print(f"\n{title}")
        for rel, row in state.items():
            if row["state"] in states:
                print(f"  {rel}  ({row['state']})\n      <- {', '.join(row['sites'])}")
    print("\ninline sites without a matched tracked destination:")
    for site in sites:
        if site.kind == "inline" and site.location not in matched:
            print(f"  {site.location}")
    if dump:
        pathlib.Path(dump).write_text(json.dumps(state, indent=2, sort_keys=True) + "\n")
        print(f"per-record classification written to {dump}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dump", metavar="PATH", help="also write the per-record classification as JSON")
    try:
        scan(**vars(ap.parse_args()))
    except (OSError, SyntaxError, subprocess.CalledProcessError) as exc:
        ap.exit(2, f"REFUSED: cannot complete the inventory: {exc}\n")
