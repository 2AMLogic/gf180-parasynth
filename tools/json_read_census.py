#!/usr/bin/env python3
"""Census of the read a missing file turns into a traceback: every
`json.loads(<x>.read_text() | read_bytes())` site in the tracked tree, and
whether a `try` around it names what the read can raise (#2055).

THE BLIND SPOT. `tools/refusal_sibling_scan.py` lists a call site only when
two callers of one callee DISAGREE about what they catch. A callee whose
callers all handle too little is invisible to it, and that is #1938's class: a
`read_text()` on a missing, unreadable or truncated file raises `OSError`
past a handler that names only `JSONDecodeError`, and the reader exits 1 with
a traceback where `tools/exit_contract.py` says an unmet precondition is
REFUSED (2). #1939's closing search counted that class with a scratch script
and reported the count from prose; this is the script, with its controls in
`tools/test_json_read_census.py`.

WHAT IS COUNTED. One line, the narrowest form, on purpose -- the scratch
census's definition, kept so its numbers stay comparable:

  * SITE: a call written `json.loads(<read>)` where `json` is the literal
    name and `<read>` is the FIRST positional argument, itself a call whose
    attribute is `read_text` or `read_bytes`. Whatever the receiver (`p`,
    `open(f)`, `a / "b"`) and whatever arguments the read takes.
  * GUARDED: an enclosing `try` -- in the scope the call is written in, and
    with the call in its BODY, not its `except`, `else` or `finally` clause --
    that has a handler naming `OSError`, `Exception` or `BaseException`,
    alone or in a tuple, as a name or an attribute. A bare `except:` guards
    too: it catches what the read raises. `except*` is a try. An inner try
    that names too little does not hide an outer one that names enough.
  * BARE: not guarded. An inventory row, never a verdict.
  * FILE CLASSES, by path alone. A test file's basename matches pytest's two
    default patterns, `test_*.py` or `*_test.py` (the extra names the root
    `pytest.ini` lists are executable probes, not tests here); a probe lives
    under `tools/probes/`; everything else is other. For the partition a file
    is test, else probe, else other. The issue's three "bare" figures are not
    a partition -- 173 + 56 + 502 exceeds its 690 -- so they are printed as
    it printed them, with the overlap (tests under `tools/probes/`) beside
    them.

WHAT IT CANNOT TELL YOU:

  * anything outside the narrowest form: `json.loads(open(f).read())`,
    `json.load(open(f))`, a read stored in a variable before `json.loads`
    sees it, an aliased module (`import json as j`), `from json import
    loads`, or the keyword form `json.loads(s=...)`. The blind spot this
    counts is therefore SMALLER than the one that exists;
  * whether a bare row can raise at all on the CLI's input space, or whether
    every caller of its function handles it -- the limit the sibling scan
    states, and the reason #2055 traces its one new row by running it;
  * what a guard DOES. A handler naming `Exception` is credited whatever it
    does next, including `raise`.

INPUTS THAT SATISFY THIS CENSUS WHILE VIOLATING ITS INTENT (rule 8):

  * a local variable named `json` is counted as the module;
  * credit is LEXICAL to the scope the call is written in. A call in a nested
    `def` is not credited to the definer's `try`, wherever the definer calls
    it; a call in a default value or a decorator, which runs when the `def`
    does, is not credited to it either;
  * an untracked file is invisible (CI sees commits);
  * `OSError` under another name (`IOError`, `EnvironmentError`, `os.error`)
    is not read as `OSError`.

Run::

    .venv/bin/python tools/json_read_census.py --rev origin/main           # the report
    .venv/bin/python tools/json_read_census.py --rev origin/main --list    # + every site

The tree is read from the commit, blob by blob through `git cat-file --batch`.
The working tree is never read, so a sparse or a stale checkout cannot move a
number. Exit 0: report produced. Exit 2 REFUSED: the rev does not resolve,
git fails, a `.py` entry is not a regular file or does not parse, or no site
matched at all -- the scratch census's 0. Anything else is an execution error
and leaves with its traceback.
"""
from __future__ import annotations

import argparse
import ast
import os
import subprocess
import sys
from dataclasses import dataclass, field

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import refusal                                            # noqa: E402
from exit_contract import PASS, REFUSED                   # noqa: E402

READ_ATTRS = ("read_text", "read_bytes")
GUARD_NAMES = frozenset({"OSError", "Exception", "BaseException"})
PROBE_PREFIX = "tools/probes/"
PATTERN = "json.loads(<x>.read_text()|read_bytes())"
GUARD_LINE = "guarded by a try naming OSError/Exception/BaseException"
REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
REGULAR_MODES = (b"100644", b"100755")
_SCOPES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)
_TRIES = (ast.Try, ast.TryStar)
CATEGORIES = ("other", "probe", "test")


class Refused(refusal.Refused):
    pass


@dataclass(frozen=True)
class Site:
    path: str
    line: int
    read: str
    guard: str | None        # the guarding handler's spelling; None = bare


@dataclass
class Census:
    files: int
    sites: list = field(default_factory=list)


@dataclass(frozen=True)
class Summary:
    files: int
    sites: int
    guarded: int
    bare: int
    bare_other: int              # bare, in a file that is neither test nor under tools/probes
    bare_under_probes: int       # bare, any file under tools/probes (its tests included)
    bare_tests: int              # bare, any test file (those under tools/probes included)
    bare_tests_under_probes: int  # the overlap of the two lines above
    part: dict                   # category -> (guarded, bare); test, else probe, else other


def is_test_file(path: str) -> bool:
    name = path.rsplit("/", 1)[-1]
    return name.startswith("test_") or name.endswith("_test.py")


def category(path: str) -> str:
    if is_test_file(path):
        return "test"
    return "probe" if path.startswith(PROBE_PREFIX) else "other"


def _is_site(call: ast.Call) -> str | None:
    """The read's name when `call` is `json.loads(<read>.read_text()|read_bytes())`, else None."""
    f = call.func
    if not (isinstance(f, ast.Attribute) and f.attr == "loads"
            and isinstance(f.value, ast.Name) and f.value.id == "json" and call.args):
        return None
    read = call.args[0]
    if isinstance(read, ast.Call) and isinstance(read.func, ast.Attribute) and read.func.attr in READ_ATTRS:
        return read.func.attr
    return None


def _names_guard(handler: ast.ExceptHandler) -> str | None:
    """The spelling of the handler's type when it guards, else None."""
    t = handler.type
    if t is None:
        return "except:"
    for node in t.elts if isinstance(t, ast.Tuple) else (t,):
        name = node.id if isinstance(node, ast.Name) else getattr(node, "attr", None)
        if name in GUARD_NAMES:
            return ast.unparse(t)
    return None


def _guard_of(call: ast.Call, parents: dict) -> str | None:
    """Lexical credit: walk up to the scope barrier and return the nearest try that guards."""
    node = call
    while True:
        parent = parents.get(id(node))
        if parent is None or isinstance(parent, _SCOPES):
            return None
        if isinstance(parent, _TRIES) and any(node is stmt for stmt in parent.body):
            for handler in parent.handlers:
                spelling = _names_guard(handler)
                if spelling is not None:
                    return spelling
        node = parent


def find_sites(path: str, source: str | bytes) -> list:
    """Every site in one module, in source order. A syntax error propagates."""
    tree = ast.parse(source, filename=path)
    parents = {id(child): parent for parent in ast.walk(tree) for child in ast.iter_child_nodes(parent)}
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            read = _is_site(node)
            if read is not None:
                out.append(Site(path, node.lineno, read, _guard_of(node, parents)))
    out.sort(key=lambda s: s.line)
    return out


def census(sources: dict) -> Census:
    """Every site in a {path: source} mapping. One unparsable file refuses the whole count."""
    c = Census(files=len(sources))
    for path in sorted(sources):
        try:
            c.sites.extend(find_sites(path, sources[path]))
        except SyntaxError as e:
            raise Refused(f"{path} does not parse (line {e.lineno}: {e.msg}) under Python "
                          f"{sys.version.split()[0]} -- the count would be short by every site "
                          "in that file") from e
    return c


def summarize(c: Census) -> Summary:
    part = {k: [0, 0] for k in CATEGORIES}
    n = dict.fromkeys(("sites", "bare", "bare_other", "bare_under_probes", "bare_tests",
                       "bare_tests_under_probes"), 0)
    for s in c.sites:
        n["sites"] += 1
        bare = s.guard is None
        part[category(s.path)][int(bare)] += 1
        if bare:
            test, probe = is_test_file(s.path), s.path.startswith(PROBE_PREFIX)
            n["bare"] += 1
            n["bare_other"] += int(not test and not probe)
            n["bare_under_probes"] += int(probe)
            n["bare_tests"] += int(test)
            n["bare_tests_under_probes"] += int(test and probe)
    return Summary(files=c.files, guarded=n["sites"] - n["bare"], **n,
                   part={k: tuple(v) for k, v in part.items()})


def _git(root: str, args: list, stdin: bytes | None = None) -> bytes:
    try:
        p = subprocess.run(["git", "-C", root, *args], input=stdin, capture_output=True)
    except OSError as e:
        raise Refused(f"git {args[0]} could not run under {root}: {e}") from e
    if p.returncode != 0:
        lines = p.stderr.decode("utf-8", "replace").strip().splitlines()
        raise Refused(f"git {args[0]} under {root} exited {p.returncode}: {lines[-1] if lines else ''}")
    return p.stdout


def read_tree(rev: str, root: str) -> tuple:
    """Every tracked `.py` blob of `rev` as {path: bytes}, and the commit's full SHA."""
    try:
        commit = _git(root, ["rev-parse", "--verify", rev + "^{commit}"]).decode().strip()
    except Refused as e:
        raise Refused(f"{rev!r} does not name a commit under {root} ({e})") from e
    entries = []
    for record in _git(root, ["ls-tree", "-r", "-z", commit]).split(b"\0"):
        if not record:
            continue
        meta, raw = record.split(b"\t", 1)
        mode, _kind, sha = meta.split()
        path = raw.decode("utf-8", "surrogateescape")
        if path.endswith(".py"):
            if mode not in REGULAR_MODES:
                raise Refused(f"{path} in {rev} is not a regular file (mode {mode.decode()}): it is "
                              "not scanned, so the count would be short")
            entries.append((sha, path))
    if not entries:
        raise Refused(f"no tracked .py file in {rev} under {root}")
    batch = _git(root, ["cat-file", "--batch"], b"".join(sha + b"\n" for sha, _ in entries))
    sources, at = {}, 0
    for sha, path in entries:
        end = batch.index(b"\n", at)
        size = int(batch[at:end].split()[2])
        sources[path] = batch[end + 1:end + 1 + size]
        at = end + 2 + size
    return sources, commit


def render(rev: str, commit: str, s: Summary, sites=None) -> str:
    L = [f"tree: {rev} @ {commit} -- {s.files} tracked .py files, all scanned",
         f"{PATTERN} sites: {s.sites}; {GUARD_LINE}: {s.guarded}; bare: {s.bare}",
         f"  bare, non-test non-probe: {s.bare_other}   bare, under tools/probes: {s.bare_under_probes}"
         f"   bare, test files: {s.bare_tests}   (bare tests under tools/probes, in both of the"
         f" previous two lines: {s.bare_tests_under_probes})",
         "  by category, a file being test, else probe (tools/probes/), else other -- guarded/bare: "
         + "   ".join(f"{k} {g}/{b}" for k, (g, b) in s.part.items())]
    for site in sites or []:
        state = "bare" if site.guard is None else f"guarded by {site.guard}"
        L.append(f"{site.path}:{site.line}  {site.read}  {state}  [{category(site.path)}]")
    return "\n".join(L) + "\n"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=f"Census {PATTERN} sites and their guards in a commit.")
    ap.add_argument("--rev", default="HEAD", help="commit or ref to read; the working tree is never read")
    ap.add_argument("--root", default=REPO_ROOT, help="repository the rev resolves in")
    ap.add_argument("--list", action="store_true", help="also print every site with its answer")
    a = ap.parse_args(argv)
    try:
        sources, commit = read_tree(a.rev, a.root)
        c = census(sources)
        s = summarize(c)
        if not s.sites:
            named = ", ".join(sorted(sources)[:5]) + (" ..." if s.files > 5 else "")
            raise Refused(f"no site of the form {PATTERN} among {s.files} tracked .py file(s) ({named})"
                          " -- the pattern or the tree is wrong; tools/manifest.py load_json_refused is"
                          " a known match")
    except Refused as e:
        print(f"REFUSED: {e}")
        return REFUSED
    print(render(a.rev, commit, s, c.sites if a.list else None), end="")
    return PASS


if __name__ == "__main__":
    sys.exit(main())
