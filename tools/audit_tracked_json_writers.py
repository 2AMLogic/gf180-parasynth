#!/usr/bin/env python3
"""Which tracked JSON records can still be left short by a killed write?

    .venv/bin/python tools/audit_tracked_json_writers.py

The #882 class sweep migrated tracked-JSON record writers to
`run_all.write_json_atomic`; #922 migrated `tools/holdout.py` and
`tools/measure_m1a_volume_mapping.py` and requires the inversion this script
performs: read `git ls-files '*.json'`, find each tracked JSON's writer in the
tracked Python sources, and answer per file whether a kill inside the write
can truncate it (the #842 shape on a record).

Attribution, stated honestly: a site's target expression is read from its
source text -- string literals with `{...}` treated as one path segment --
plus the module's own path constants (`NAME = ROOT / "a" / "b"`), one-line
helper functions (`def f(x): return EXPR`), and imported-module constants
(`alias.OUT`, one level deep). A tracked JSON matches a site when a full-path
pattern matches its repository path, or a filename pattern matches its
basename AND one of the site's resolved directories contains it; a specific
(non-wildcard) basename with no resolvable directory is allowed and flagged
as `basename-only`. A wildcard filename with no resolvable directory
attributes to nothing -- it would match every JSON in the tree and say
nothing.

Classification per tracked JSON, strongest evidence wins: `INLINE` (a
path-attributed inline serialise-and-write -- truncate-then-write, the #842
shape; the remainders, which belong in their own repair issues), `atomic`
(every path-attributed writer replaces through `write_json_atomic`),
`INLINE?` / `atomic?` (basename-only evidence only, or a path-atomic writer
plus a same-named inline writer elsewhere: real writes whose exact tracked
copy is not decidable from text), and `no-writer` (hand-committed, fixture,
or written by tooling outside the tracked sources). Test files and
`run_all.py` (the atomic writer itself) are excluded from scanning. Exit 0
when the audit ran, 2 when git is unreadable -- the audit is a report, not a
gate.
"""
from __future__ import annotations

import pathlib
import re
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent

CALL_WINDOW = 3  # call line + 2 following lines
SKIP = re.compile(r"(^|/)test_[^/]*\.py$|(^|/)tests/|^tools/run_all\.py$"
                  r"|^tools/audit_tracked_json_writers\.py$")
INLINE_TEXT = re.compile(r"\.write_text\(")
JSON_DUMP = re.compile(r"json\.dump\(")
JSON_DUMPS = re.compile(r"json\.dumps\(")
LITERAL = re.compile(r"""(['"])([^'"]*?)\1""")
# `NAME = ROOT / "a" / "b"`, `NAME = OTHER / "lit"`, and the os.path spelling
# `NAME = os.path.join(ROOT, "a", "b")` all reduce to (base, literals).
CONST_DEF = re.compile(r"^([A-Z][A-Z0-9_]*)\s*=\s*(.+?)(\s*(?:#.*)?)$")
IMPORT = re.compile(r"^import (\w+)(?:\s+as (\w+))?\s*(?:#.*)?$")
WILD = re.compile(r"\{[^{}]*\}")
WORD = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def _git(*args: str) -> str:
    out = subprocess.run(["git", "-C", str(ROOT), *args],
                         capture_output=True, text=True)
    if out.returncode != 0:
        sys.exit(f"REFUSED: git {' '.join(args)} failed: {out.stderr.strip()}")
    return out.stdout


def _read(rel: str) -> list[str]:
    return (ROOT / rel).read_text(errors="replace").splitlines()


def _lit_chain(text: str) -> str:
    """The `ROOT / "a" / "b"` right-hand side -> `a/b`."""
    return "/".join(x for _, x in LITERAL.findall(text))


def _raw_constants(lines: list[str]) -> dict[str, tuple[str, str]]:
    """`NAME -> (base, "a/b")` for every uppercase path assignment, in the
    ROOT-slash, chained-constant and os.path.join spellings alike."""
    out = {}
    for line in lines:
        m = CONST_DEF.match(line)
        if not m:
            continue
        name, expr = m.group(1), m.group(2)
        base = re.match(r"\s*(?:os\.path\.join\(\s*)?(ROOT|[A-Z][A-Z0-9_]*)", expr)
        if not base:
            continue
        lits = _lit_chain(expr)
        if lits:
            out[name] = (base.group(1), lits)
    return out


def _resolve_constants(raw: dict[str, tuple[str, str]]) -> dict[str, str]:
    """Chase `NAME = OTHER / "lit"` chains down to ROOT, to a fixpoint."""
    resolved = {n: v for n, (b, v) in raw.items() if b == "ROOT"}
    for _ in range(4):
        grew = False
        for n, (b, v) in raw.items():
            if n in resolved:
                continue
            if b in resolved:
                resolved[n] = f"{resolved[b].rstrip('/')}/{v}"
                grew = True
        if not grew:
            break
    return resolved


def _constant_table(src: str, lines: list[str]) -> dict[str, str]:
    """This module's path constants, plus -- one level deep -- the constants
    of the modules it imports, under the aliases it uses (`volume.OUT`)."""
    table = _resolve_constants(_raw_constants(lines))
    for line in lines:
        m = IMPORT.match(line)
        if not m:
            continue
        mod, alias = m.group(1), m.group(2) or m.group(1)
        base = (ROOT / mod).with_suffix(".py")
        for cand in (base, ROOT / "tools" / base.name,
                     ROOT / "model" / base.name, ROOT / "fpga" / base.name):
            if cand.is_file():
                for name, val in _resolve_constants(
                        _raw_constants(_read(str(cand.relative_to(ROOT))))).items():
                    table[f"{alias}.{name}"] = val
                break
    return table


def _one_line_functions(lines: list[str]) -> dict[str, str]:
    out = {}
    for i, line in enumerate(lines):
        m = re.match(r"^def (\w+)\([^)]*\)[^:]*:\s*$", line)
        if m and i + 1 < len(lines):
            r = re.match(r"^\s*return (.+)$", lines[i + 1])
            if r:
                out[m.group(1)] = r.group(1)
                continue
        m = re.match(r"^def (\w+)\([^)]*\):\s*return (.+)$", line)
        if m:
            out[m.group(1)] = m.group(2)
    return out


def _patterns(expr: str, table: dict[str, str],
              funcs: dict[str, str]) -> tuple[set[str], set[str], set[str]]:
    """(full-path patterns, filename patterns, resolved directories) for one
    target expression. Constants resolve to a directory (no dot) or a full
    file path (ends in .json); one-line helper bodies are inlined."""
    for name, body in funcs.items():
        if re.search(rf"\b{name}\s*\(", expr):
            expr += " ; " + body
    names = {m.group(0) for m in WORD.finditer(expr)} & set(table)
    dirs, files = set(), set()
    for n in names:
        v = table[n]
        (files if v.endswith(".json") else dirs).add(v.rstrip("/"))
    full, bare = set(), set()
    for _, lit in LITERAL.findall(expr):
        if ".json" not in lit:
            continue  # this audit is about JSON records only
        # f-string braces become one-segment wildcards; re.escape must never
        # see them, and the f/r/b prefix is syntax outside the quotes.
        pat = re.escape(WILD.sub("@W@", lit)).replace("@W@", "[^/]+")
        if "/" in lit:  # the literal, not the pattern: [^/]+ contains a slash
            full.add(pat)
        bare.add(pat)
    full |= {f"{d}/{b}" for d in dirs for b in bare} | files
    return full, bare, dirs


def scan(dump: str | None = None) -> None:
    tracked = sorted(l for l in _git("ls-files", "--", "*.json").splitlines() if l)
    sources = sorted(l for l in _git("ls-files", "--", "*.py").splitlines() if l)

    sites: list[dict] = []
    for src in sources:
        if SKIP.search(src):
            continue
        lines = _read(src)
        table = _constant_table(src, lines)
        funcs = _one_line_functions(lines)
        for i, line in enumerate(lines):
            window = "\n".join(lines[i:i + CALL_WINDOW])
            # a migrated module writes through its `write_json` seam, which
            # delegates to `write_json_atomic` -- both call shapes are atomic
            # sites; the `def write_json` body line is skipped.
            is_atomic = ("write_json_atomic(" in line or
                         re.search(r"\bwrite_json\(", line)) and "def " not in line
            if is_atomic:
                kind = "atomic"
            elif (INLINE_TEXT.search(line) and JSON_DUMPS.search(window)) \
                    or JSON_DUMP.search(window):
                kind = "inline"
            else:
                continue
            token_match = re.search(r"\bwrite_json\(|write_json_atomic\(|\.write_text\(",
                                    line)
            token = token_match.group(0) if token_match else ".write_text("
            if token == ".write_text(":
                call = line.split(token)[0]  # target expression precedes the call
            else:                            # seam/atomic target is the argument list
                call = line.split(token, 1)[1] + "\n" + \
                    "\n".join(lines[i + 1:i + CALL_WINDOW])
            if not call.strip():
                call = window
            full, bare, dirs = _patterns(call, table, funcs)
            sites.append({"kind": kind, "src": src, "line": i + 1,
                          "full": full, "bare": bare, "dirs": dirs})

    def hit(site: dict, rel: str) -> str | None:
        base = pathlib.PurePosixPath(rel).name
        parent = pathlib.PurePosixPath(rel).parent.as_posix()
        for pat in site["full"]:
            try:
                if re.fullmatch(pat, rel):
                    return "path"
            except re.error:
                pass
        for pat in site["bare"]:
            if "." not in pat:
                continue
            try:
                if not re.fullmatch(pat, base):
                    continue
            except re.error:
                continue
            if any(parent == d or rel.startswith(d + "/") for d in site["dirs"]):
                return "path"
            if "[^/]+" not in pat and not site["dirs"]:
                return "basename-only"
        return None

    state: dict[str, tuple[str, list[dict]]] = {}
    for rel in tracked:
        inl_path = [s for s in sites if s["kind"] == "inline"
                    and hit(s, rel) == "path"]
        inl_base = [s for s in sites if s["kind"] == "inline"
                    and hit(s, rel) == "basename-only"]
        atom_path = [s for s in sites if s["kind"] == "atomic"
                     and hit(s, rel) == "path"]
        atom_base = [s for s in sites if s["kind"] == "atomic"
                     and hit(s, rel) == "basename-only"]
        if inl_path:
            kind, evidence = "INLINE", inl_path
        elif atom_path and not inl_base:
            kind, evidence = "atomic", atom_path
        elif atom_path and inl_base:
            # the real writer is atomic; a same-named inline writer elsewhere
            # cannot be ruled in or out from the text alone
            kind, evidence = "atomic?", atom_path + inl_base
        elif inl_base:
            kind, evidence = "INLINE?", inl_base
        elif atom_base:
            kind, evidence = "atomic?", atom_base
        else:
            kind, evidence = "no-writer", []
        state[rel] = (kind, evidence)

    inline_recs = [r for r in tracked if state[r][0] == "INLINE"]
    atomic_recs = [r for r in tracked if state[r][0] in ("atomic", "atomic?")]
    ambiguous = [r for r in tracked if state[r][0] in ("INLINE?", "atomic?")]
    unknown = [r for r in tracked if state[r][0] == "no-writer"]
    inline_sites = [s for s in sites if s["kind"] == "inline"]
    orphans = [s for s in inline_sites if not any(hit(s, r) for r in tracked)]

    print(f"tracked JSONs: {len(tracked)}  (atomic {len(atomic_recs)}, "
          f"INLINE {len(inline_recs)}, ambiguous {len(ambiguous)}, "
          f"no-writer {len(unknown)})")
    print(f"write sites in tracked sources: "
          f"{len(sites) - len(inline_sites)} atomic, {len(inline_sites)} inline")
    if inline_recs:
        print("\nREMAINDERS -- tracked JSONs with a path-attributed inline writer:")
        for rel in inline_recs:
            how = ", ".join(sorted({f"{s['src']}:{s['line']}"
                                    for s in state[rel][1]}))
            print(f"  {rel}\n      <- {how}")
    if ambiguous:
        print("\nAMBIGUOUS -- basename-only inline evidence (the write is real; "
              "which tracked copy it produces is not decidable from text):")
        for rel in ambiguous:
            kind = state[rel][0]
            how = ", ".join(sorted({f"{s['src']}:{s['line']} [{s['kind']}]"
                                    for s in state[rel][1]}))
            print(f"  {rel}  ({kind})\n      <- {how}")
    if orphans:
        print("\ninline sites matching no tracked JSON (untracked / build output):")
        for s in orphans:
            print(f"  {s['src']}:{s['line']}")
    print(f"\nno-writer tracked JSONs: {len(unknown)} "
          "(hand-committed, fixture, or external tooling) -- not attributed")
    if dump:
        import json as _json
        pathlib.Path(dump).write_text(_json.dumps(
            {rel: {"state": state[rel][0],
                   "sites": [f"{s['src']}:{s['line']}" for s in state[rel][1]]}
             for rel in tracked}, indent=2, sort_keys=True) + "\n")
        print(f"per-record classification written to {dump}")


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dump", metavar="PATH",
                    help="also write the per-record classification as JSON")
    scan(**vars(ap.parse_args()))
