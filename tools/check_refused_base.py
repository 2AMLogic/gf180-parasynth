#!/usr/bin/env python3
"""check_refused_base.py -- conformance guard for `class Refused`/`Refusal`.

Every module in this repository may define its own `Refused` (the name at the
raise and catch sites is deliberate and greppable), but from #784 on every
definition must RESOLVE to `refusal.Refused` -- directly
(`class Refused(refusal.Refused)`) or transitively through a chain of
sibling-chained definitions. Reusing a sibling's class by alias
(`Refused = other.Refused`) or re-import (`from other import Refused`) is the
correct pattern for a non-defining module and is never flagged: the guard
looks only at `class` definitions.

RESOLUTION IS TRANSITIVE, and deliberately source-based (no imports, no side
effects): a base like `bass.Refused` is followed through the module's own
`import a as b` / `from a import c as d` / `Refused = e.f` bindings into the
defining module, recursively, until it reaches `tools/refusal.py`'s
`Refused` (conforms) or a plain exception base such as `Exception` /
`RuntimeError` (does not). A syntactic check for a literal `refusal.Refused`
base would be unsatisfiable against the intended end state -- 5 definitions
are themselves sibling-chained -- which is why this walks the chain.

MODE: enforcing since #815 re-based the whole set: every definition must
resolve to `refusal.Refused`; any that does not is printed as a loud
`NON-CONFORMING:` line and the run exits 1. There is no baseline and no
allowlist. The one exemption is a bare `class Refusal:`/`Refused:` (no bases)
decorated `@dataclass`: it is a log record, not an exception -- it cannot be
raised, so it cannot be a refusal (the only one in the tree today is
fpga/midi_session.py's `Refusal`, #815). The exemption is keyed on no-bases +
dataclass decorator, NOT on the name, so naming an exception class `Refusal`
does not slip past it (rule 8), and every exempt key is printed for audit.

Exit codes (this repository's convention; 2 = could not run, no evidence):
  0  clean -- every definition resolves to refusal.Refused
  1  at least one NON-CONFORMING definition (re-base it on refusal.Refused,
     preserving its secondary bases; do not catch Exception instead)
  2  git could not run
  3  REFUSED -- a tracked (or given) `*.py` file failed to parse; a partial
     parse can never masquerade as a clean result
"""
from __future__ import annotations

import argparse
import ast
import subprocess
import sys
from pathlib import Path

CANONICAL_REL = "tools/refusal.py"
CANONICAL_CLASS = "Refused"
CLASS_NAMES = ("Refused", "Refusal")


def git(*args: str, cwd: Path) -> str:
    r = subprocess.run(["git", *args], capture_output=True, text=True, cwd=cwd)
    if r.returncode != 0:
        print(f"git {' '.join(args)} failed: {r.stderr.strip()}", file=sys.stderr)
        raise SystemExit(2)
    return r.stdout


# ---- one shared index over the file set -------------------------------------
class Index:
    """Parsed modules, their `Refused`/`Refusal` class definitions, and their
    module-level name bindings (imports, from-imports, aliases)."""

    def __init__(self, files: list[Path], root: Path):
        self.root = root
        self.files = files
        self.classes: dict[tuple[str, str], ast.ClassDef] = {}
        self.bindings: dict[str, dict[str, tuple]] = {}   # file -> name -> binding
        self.modules: dict[str, str] = {}                 # dotted or stem -> file
        self.stem_count: dict[str, int] = {}
        self.parse_errors: list[tuple[str, str]] = []
        self.canonical = str(root / CANONICAL_REL)
        every = sorted({str(f) for f in files} | {self.canonical})
        for f in every:
            stem = Path(f).stem
            self.stem_count[stem] = self.stem_count.get(stem, 0) + 1
        for f in every:
            self._index_file(Path(f))

    def _index_file(self, path: Path) -> None:
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError as exc:
            self.parse_errors.append((str(path), f"{exc.msg} (line {exc.lineno})"))
            return
        try:
            rel = path.relative_to(self.root)
        except ValueError:
            rel = path                          # outside the tree: fixtures
        dotted = None if str(rel).startswith("..") \
            else ".".join(rel.with_suffix("").parts)
        bindings: dict[str, tuple] = {}
        for node in tree.body:              # top level only: a nested `class
            # Refused` is invisible here (none in the tree today; one would
            # have to be added to this walker, and the guard would not see it
            # until then -- the reason nested definitions stay out of style)
            if isinstance(node, ast.Import):
                for a in node.names:
                    bindings[a.asname or a.name.split(".")[0]] = \
                        ("import", a.name if a.asname else a.name.split(".")[0])
            elif isinstance(node, ast.ImportFrom) and node.module:
                for a in node.names:
                    bindings[a.asname or a.name] = ("frommod", node.module, a.name)
            elif isinstance(node, ast.Assign):
                for t in node.targets:
                    if isinstance(t, ast.Name):
                        bindings[t.id] = ("alias", node.value)
            elif isinstance(node, ast.ClassDef) and node.name in CLASS_NAMES:
                self.classes[(str(path), node.name)] = node
        self.bindings[str(path)] = bindings
        if dotted:
            self.modules[dotted] = str(path)
        stem = path.stem
        if self.stem_count.get(stem) == 1:
            self.modules.setdefault(stem, str(path))

    # -- reference resolution -------------------------------------------------
    def _module_file(self, dotted: str) -> str | None:
        return self.modules.get(dotted)

    def _descend(self, current: tuple, attrs: list[str]) -> tuple | None:
        """Walk attribute segments from a ("module", dotted) or
        ("class", (file, name)) position."""
        for attr in attrs:
            if current[0] == "module":
                sub = self._module_file(f"{current[1]}.{attr}")
                if sub:
                    current = ("module", f"{current[1]}.{attr}")
                    continue
                path = self._module_file(current[1])
                if path is None:
                    return None
                current = self._local(path, attr)
            elif current[0] == "class":
                return None                     # attributes of a class: out of scope
            else:
                return None
            if current is None:
                return None
        return current

    def _local(self, path: str, name: str) -> tuple | None:
        """The value `name` binds to at module level in `path`."""
        if (path, name) in self.classes:
            return ("class", (path, name))
        binding = self.bindings.get(path, {}).get(name)
        if binding is None:
            return None
        kind = binding[0]
        if kind == "import":
            return ("module", binding[1])
        if kind == "frommod":
            return self._descend(("module", binding[1]), [binding[2]])
        if kind == "alias":
            return self.resolve(path, binding[1])
        return None

    def resolve(self, path: str, node: ast.expr, _depth: int = 0) -> tuple | None:
        """A base expression -> ("class", (file, name)) | ("module", dotted) | None."""
        if _depth > 32:                          # alias cycle; refuse to hang
            return None
        if isinstance(node, ast.Name):
            return self._local(path, node.id)
        if isinstance(node, ast.Attribute):
            head, attrs = node, []
            while isinstance(head, ast.Attribute):
                attrs.insert(0, head.attr)
                head = head.value
            if not isinstance(head, ast.Name):
                return None
            base = self._local(path, head.id)
            if base is None:
                return None
            if base[0] == "module":
                dotted = ".".join([base[1], *attrs])
                if self._module_file(dotted):
                    return ("module", dotted)
                return self._descend(base, attrs)
            return self._descend(base, attrs) if not attrs else base
        return None


def base_repr(node: ast.expr) -> str:
    parts = []
    while isinstance(node, ast.Attribute):
        parts.insert(0, node.attr)
        node = node.value
    parts.insert(0, node.id if isinstance(node, ast.Name) else "?")
    return ".".join(parts)


def key_for(path: str, name: str, node: ast.ClassDef, root: Path) -> str:
    """The baseline key: repo-relative POSIX, so the committed baseline is
    machine-independent (an absolute key can only ever match the checkout
    that wrote it). Same convention as Index._index_file's `rel`."""
    p = Path(path)
    try:
        p = p.relative_to(root)
    except ValueError:
        pass                               # a fixture outside the tree: as given
    bases = ";".join(sorted(base_repr(b) for b in node.bases))
    return f"{p.as_posix()}:{name}({bases})"


def _dataclass_record(node: ast.ClassDef) -> bool:
    """A bare `class Refusal:`/`class Refused:` (no bases) decorated
    `@dataclass` is a log record, not an exception: it cannot be raised (a
    non-Exception cannot be raised at all), so it cannot be a refusal. The
    exemption is keyed on no-bases + the dataclass decorator, NOT on the
    name -- naming an exception class `Refusal` buys nothing (rule 8) -- and
    a definition that gains any base stops being exempt and is checked
    normally."""
    if node.bases:
        return False
    for dec in node.decorator_list:
        target = dec.func if isinstance(dec, ast.Call) else dec
        if isinstance(target, ast.Name) and target.id == "dataclass":
            return True
        if isinstance(target, ast.Attribute) and target.attr == "dataclass":
            return True
    return False


def nonconforming(files: list[Path], root: Path) -> tuple[list[tuple[str, int, str, str]], int, list[str]]:
    """([(key, lineno, name, bases-string)], total definitions examined,
    exempt keys) for every `class Refused`/`Refusal` that does not resolve,
    transitively, to `refusal.Refused`; exempt keys are the dataclass-record
    carve-outs (never raised, never caught -- see _dataclass_record). Raises
    SystemExit(3) on a parse failure rather than reporting a partial result."""
    ix = Index(files, root)
    if ix.parse_errors:
        for path, msg in ix.parse_errors:
            print(f"REFUSED: {path} does not parse: {msg}", file=sys.stderr)
        raise SystemExit(3)
    out = []
    exempt = []
    for (path, name), node in sorted(ix.classes.items()):
        if path == ix.canonical and name == CANONICAL_CLASS:
            continue                             # the base itself is conforming
        if _dataclass_record(node):
            exempt.append(key_for(path, name, node, root))
            continue
        if _conforms(ix, path, name, frozenset()):
            continue
        bases = ", ".join(base_repr(b) for b in node.bases) or "(none)"
        out.append((key_for(path, name, node, root), node.lineno, name, bases))
    # the canonical anchor class is exempt by definition, never reportable
    total = len(ix.classes) - (1 if (ix.canonical, CANONICAL_CLASS) in ix.classes else 0)
    return out, total, exempt


def _conforms(ix: Index, path: str, name: str, seen: frozenset) -> bool:
    if (path, name) == (ix.canonical, CANONICAL_CLASS):
        return True
    if (path, name) in seen:
        return False                             # a cycle is not a chain to the base
    node = ix.classes.get((path, name))
    if node is None:
        return False
    seen = seen | {(path, name)}
    for base in node.bases:
        r = ix.resolve(path, base)
        if r is None:
            continue
        if r[0] == "class":
            f, n = r[1]
            if (f, n) == (ix.canonical, CANONICAL_CLASS) or _conforms(ix, f, n, seen):
                return True
    return False


def repo_root() -> Path:
    """The repository this guard is part of (anchored here, not at the caller's
    cwd, so pytest and make resolve the same tree)."""
    here = Path(__file__).resolve().parent
    return Path(git("rev-parse", "--show-toplevel", cwd=here).strip())


def _collect(root: Path, paths: list[str] | None) -> list[Path]:
    if paths:
        return [Path(p) for p in paths]
    listed = git("ls-files", "*.py", cwd=root).splitlines()
    return [root / p for p in listed]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--paths", nargs="*", default=None,
                    help="check these files instead of every tracked *.py")
    a = ap.parse_args(argv)

    try:
        root = repo_root()
        found, total, exempt = nonconforming(_collect(root, a.paths), root)
    except SystemExit as exc:                    # git failure (2) or a parse refusal (3)
        return int(exc.code or 0)

    for k, line, name, bases in found:
        print(f"  NON-CONFORMING: {k} <- [{bases}]")
    for k in exempt:
        print(f"  exempt (dataclass record, not an exception): {k}")
    print(f"{len(found)} of {total} definition(s) do not resolve to refusal.Refused "
          f"({len(exempt)} exempt dataclass record(s))")
    if found:
        print(f"\nevery class Refused/Refusal must subclass refusal.Refused "
              f"(directly or through a conforming chain), preserving every "
              f"secondary base; the fix is the re-base, never `except Exception` "
              f"(an unexpected exception is an EXECUTION ERROR, not a refusal).")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
