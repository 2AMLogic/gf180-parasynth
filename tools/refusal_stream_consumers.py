#!/usr/bin/env python3
"""Where a REFUSED reason goes, and who could miss it (issue #1988).

#1988 asked whether any in-repo consumer captures one stream of a tool that
prints `REFUSED:` to stdout on one path and to stderr on another, and so
loses a reason while the exit status stays correct.  This read-only census
answered it.  A measured refutation was a complete outcome, so it pins
nothing, changes no tool and is wired to no gate; it is kept because every
figure in the issue's closing comment comes from it, and because the next
person who asks the same question should not have to re-derive the reading
below.

WHAT IT FOUND on origin/main @ dd0449e0, 2026-11-28 (volatile: re-run it).

  * 532 refusal lines in 242 non-test Python files; 13 files print them on
    BOTH streams, and one more does under the looser rule.
  * WRONG BEFORE IT WAS RIGHT -- the premise.  #1988's table said eleven
    files split, measured with

        grep -cE 'print\\(f?"REFUSED' <file>
        grep -cE 'print\\(f?"REFUSED.*file=sys\\.stderr' <file>

    and re-verified with the same two commands.  The second only sees
    `file=sys.stderr` when it shares the `print(`'s line, so a wrapped print
    counts as stdout.  Read by AST, nine of the eleven are stderr-only
    (tools/refaudio_member_list.py is 4 prints, 4 stderr -- not 4/1) and two
    split: tools/crash_rate.py, whose stdout line is :354, not the three the
    table named, and tools/measure_promoted_bands.py.  `--sites` prints the
    two readings side by side.
  * The other eleven split files use forms the grep cannot match at all: a
    `raise SystemExit("REFUSED ...")` (stderr, by Python), a
    `sys.stderr.write`, a REFUSED name inside an f-string, and a per-row
    `voice  REFUSED: why` cell inside a report printed on stdout.
  * No consumer loses a reason.  0 EXPOSED rows.  14 PINNED rows, each
    traced by hand to a consumer that asserts the exit status before it
    reads one stream.  21 HAND-TRACE rows, each traced by hand: five are
    real readers (all MERGED or status-only), the rest name the tool as
    data.  The trace is in the issue's closing comment.

PASS ONE, `sites`.  Every non-test Python file is read by AST.  A refusal
line is a `print`, a `sys.std*.write` or a `SystemExit("REFUSED ...")`
whose text begins with REFUSED, optionally after a short tag
(`check_doc_claims: REFUSED -- ...`, `[rss] REFUSED: ...`); a line handed to
a same-file helper that prints it counts, with the helper's stream.  A file
with refusal lines on both streams is SPLIT.  A line that merely CONTAINS
the word is kept apart (`loose_only`), and any file that would split only
under that looser rule is named, so the cost of the stricter rule is a
count and not an assumption.  A `file=` this cannot read is reported as
`unknown`, never guessed.

PASS TWO, `consumers`.  For every split file, and for the files #1988 named
whatever pass one says about them, each in-repo reader gets a row with the
stream it captures:

    MERGED      both streams in one buffer (`stderr=STDOUT`, `2>&1`,
                `p.stdout + p.stderr`, tools/run_all.py)
    SEPARATE    both captured and kept apart (`capture_output=True`, pytest's
                capsys); which of them the consumer READS is recorded
    STDOUT-ONLY / STDERR-ONLY
                one stream captured; the other is inherited or discarded
    LOG         neither captured: a Makefile recipe or a CI `run:` step, where
                both streams reach the invoker's log
    NOT-READ    an in-process `main()` whose streams no caller reads
    DISCARDED   a stream sent to DEVNULL and the other not captured

and a verdict:

    NO-LOSS     nothing the consumer uses can be missed
    PINNED      SEPARATE, the tool is split, and the consumer reads one
                stream: a refusal on the other is captured but unread, and a
                text assertion on it goes RED rather than silent when a path
                changes stream.  Listed so each can be traced to the path
                the consumer actually exercises.
    EXPOSED     ONE stream captured, text read, and the tool has a refusal
                line on the stream not captured: that reason cannot reach
                this consumer at all.  This is the shape #1988 asked about.
    HAND-TRACE  the census cannot see the launch: a helper that is given the
                tool but launches nothing (what it returns is executed
                elsewhere), a command string built in one function and
                launched from another, or a generic launch that takes its
                command as an argument.  Listed, never dropped.

A launch whose first argument is `git` (or another DATA_PROGRAM) and that
names the tool reads its source, not its output: a `data` row.  An import
that calls no function reaching a refusal line is an `import` row.  A line
that names the tool in a comment or docstring is a `mention` row.

WHAT IT CANNOT SEE.  A message assembled before the print
(`print("\\n".join(lines))`) is not a site; a refusal passed to a helper in
ANOTHER file is not one either.  A launch whose command names the tool only
through a value computed in another module leaves a HAND-TRACE row when the
file also names it in a command-shaped string, and nothing otherwise.

PRECONDITIONS, ASSERTED AT THE POINT OF USE, REFUSED (exit 2) WHEN UNMET:
`git ls-files` must run in this repository and list at least 300 Python
files; `tools/run_all.py` must still launch with `stderr=subprocess.STDOUT`
(the MERGED of every runner row is read from that file, never assumed); and
every launch must fall into one of the capture classes above.

    python tools/refusal_stream_consumers.py            # the census
    python tools/refusal_stream_consumers.py --sites    # sites, and #1988's grep
    python tools/refusal_stream_consumers.py --json
"""
from __future__ import annotations

import argparse
import ast
import collections
import json
import pathlib
import re
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]

MIN_PY_FILES = 300
RUNNER = "tools/run_all.py"
MAKEFILE = "Makefile"
READER_SUFFIXES = (".py", ".yml", ".yaml", ".sh", ".txt", ".mk", ".toml", "")

ISSUE_1988_FILES = (
    "tools/refaudio_member_list.py",
    "tools/crash_rate.py",
    "tools/klt_error_stream_probe.py",
    "tools/measure_promoted_bands.py",
    "tools/performance_suite.py",
    "tools/werner_fig4.py",
    "tools/werner_fig9.py",
    "tools/check_attested_hashes.py",
    "tools/check_doc_claims.py",
    "tools/cymbal_m_origin.py",
    "tools/measure_shark_blamp.py",
)

REFUSAL_LINE = re.compile(
    r"^\s*(?:REFUSED\b"
    r"|\S{1,40}(?:\s*:\s*|\s+|(?<=\x00))REFUSED\b)")
LOOSE = re.compile(r"\bREFUSED\b")
UNKNOWN = "\x00"
LAUNCHERS = ("run", "Popen", "check_output", "call", "check_call")
RUNNERS = ("run_one", "run_all")
PIPES = {"PIPE": "PIPE", "STDOUT": "STDOUT", "DEVNULL": "DEVNULL"}
STREAMS = ("stdout", "stderr")
FUNC = (ast.FunctionDef, ast.AsyncFunctionDef)

Site = collections.namedtuple("Site", "file line func kind stream loose_only")
Row = collections.namedtuple("Row", "tool file line kind capture reads verdict note")


class Refused(Exception):
    """A precondition of the census failed; it cannot answer."""


def tracked(root: pathlib.Path) -> list[str]:
    """Every tracked path.  REFUSES if git cannot answer or the tree is too
    small to be the whole repository."""
    try:
        out = subprocess.run(["git", "ls-files"], cwd=root, capture_output=True,
                             text=True, check=True).stdout
    except (OSError, subprocess.CalledProcessError) as exc:
        raise Refused(f"git ls-files could not run in {root}: {exc}") from exc
    return out.split()


def _read(root: pathlib.Path, rel: str) -> str:
    try:
        return (root / rel).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise Refused(f"cannot read {rel}: {exc}") from exc


_TREES: dict = {}


def _parse(root: pathlib.Path, rel: str) -> ast.Module:
    """A file's AST, parsed once per (file, modification): the site pass and
    every target's consumer pass all ask for the same files."""
    try:
        st = (root / rel).stat()
    except OSError as exc:
        raise Refused(f"cannot read {rel}: {exc}") from exc
    key = (root, rel, st.st_mtime_ns, st.st_size)
    if key not in _TREES:
        try:
            _TREES[key] = ast.parse(_read(root, rel))
        except SyntaxError as exc:
            raise Refused(f"{rel} does not parse: {exc}") from exc
    return _TREES[key]


def _index(tree: ast.Module, full: bool = True):
    """func_of: node -> enclosing function (None at module level); owner:
    node -> the nearest statement containing it; parent: node -> parent.
    With full=False only func_of is filled, for passes that need nothing else."""
    func_of: dict = {}
    owner: dict = {}
    parent: dict = {}
    stack = [(tree, None, None)]
    while stack:
        node, func, stmt = stack.pop()
        func_of[node] = func
        if full and stmt is not None:
            owner[node] = stmt
        if isinstance(node, FUNC):
            func = node
        if full and isinstance(node, ast.stmt) and not isinstance(node, (*FUNC, ast.ClassDef)):
            stmt = node
        for child in ast.iter_child_nodes(node):
            if full:
                parent[child] = node
            stack.append((child, func, stmt))
    return func_of, owner, parent


def _module_env(tree: ast.Module) -> dict[str, list]:
    """name -> [(line, value node)] for every module-level `NAME = <expr>`."""
    env: dict[str, list] = {}
    for st in tree.body:
        targets = st.targets if isinstance(st, ast.Assign) else (
            [st.target] if isinstance(st, ast.AnnAssign) and st.value is not None else [])
        for t in targets:
            if isinstance(t, ast.Name):
                env.setdefault(t.id, []).append((st.lineno, st.value))
    return env


def _scope_env(func, module_env: dict) -> dict:
    """module_env plus every simple binding inside `func`: assigns, loop and
    `with` targets, comprehension targets.  A parameter binds to None, so it
    never resolves to a same-named module constant."""
    env = {k: list(v) for k, v in module_env.items()}
    if func is None:
        return env
    a = func.args
    for p in [*a.posonlyargs, *a.args, *a.kwonlyargs, *([a.vararg] if a.vararg else []),
              *([a.kwarg] if a.kwarg else [])]:
        env[p.arg] = [(func.lineno, None)]
    for n in ast.walk(func):
        binds = []
        if isinstance(n, ast.Assign):
            binds = [(t, n.value, n.lineno) for t in n.targets if isinstance(t, ast.Name)]
        elif isinstance(n, ast.AnnAssign) and n.value is not None and isinstance(n.target, ast.Name):
            binds = [(n.target, n.value, n.lineno)]
        elif isinstance(n, (ast.For, ast.AsyncFor)):
            binds = [(t, n.iter, n.lineno) for t in ast.walk(n.target) if isinstance(t, ast.Name)]
        elif isinstance(n, ast.With):
            binds = [(v, item.context_expr, n.lineno) for item in n.items
                     if item.optional_vars is not None
                     for v in ast.walk(item.optional_vars) if isinstance(v, ast.Name)]
        elif isinstance(n, ast.comprehension):
            binds = [(t, n.iter, getattr(n.iter, "lineno", 0))
                     for t in ast.walk(n.target) if isinstance(t, ast.Name)]
        for t, value, line in binds:
            env.setdefault(t.id, []).append((line, value))
    return env


def _binding(name: str, env: dict, line: int):
    """The value node `name` holds at `line`, or None when nothing readable."""
    seen = [v for ln, v in env.get(name, []) if ln <= line]
    return seen[-1] if seen else None


def _lit(node, env: dict, line: int, depth: int = 0):
    """The literal text of a message, UNKNOWN for each part no reader can
    see, or None when none of it can be read."""
    if depth > 8:
        return UNKNOWN
    if isinstance(node, ast.Constant):
        return node.value if isinstance(node.value, str) else UNKNOWN
    if isinstance(node, ast.JoinedStr):
        return "".join(_lit(v, env, line, depth + 1) or UNKNOWN for v in node.values)
    if isinstance(node, ast.FormattedValue):
        return _lit(node.value, env, line, depth + 1)
    if isinstance(node, ast.Name):
        value = _binding(node.id, env, line)
        return None if value is None else _lit(value, env, line, depth + 1)
    if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Add, ast.Mod)):
        left = _lit(node.left, env, line, depth + 1)
        right = UNKNOWN if isinstance(node.op, ast.Mod) else _lit(node.right, env, line, depth + 1)
        if left is None and right is None:
            return None
        return (UNKNOWN if left is None else left) + (UNKNOWN if right is None else right)
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
            and node.func.attr == "format":
        return _lit(node.func.value, env, line, depth + 1)
    if isinstance(node, ast.IfExp):
        body, other = _lit(node.body, env, line, depth + 1), _lit(node.orelse, env, line, depth + 1)
        if body is None or other is None:
            return None
        return body if LOOSE.search(body) else other
    return None


def _grep_recipe(text: str) -> tuple[int, int]:
    """#1988's own recipe, line-based, so its table can be set beside the
    AST reading of the same file."""
    lines = text.splitlines()
    prints = sum(1 for ln in lines if re.search(r'print\(f?"REFUSED', ln))
    errs = sum(1 for ln in lines if re.search(r'print\(f?"REFUSED.*file=sys\.stderr', ln))
    return prints, errs


def _print_stream(call: ast.Call, env: dict) -> str:
    """stdout unless `file=` says otherwise; `unknown` when `file=` is not
    `sys.stdout` / `sys.stderr` or a name bound to one of them."""
    for k in call.keywords:
        if k.arg != "file":
            continue
        v = k.value
        while isinstance(v, ast.Name):
            v = _binding(v.id, env, call.lineno)
        if isinstance(v, ast.Attribute) and isinstance(v.value, ast.Name) \
                and v.value.id == "sys" and v.attr in STREAMS:
            return v.attr
        return "unknown"
    return "stdout"


def _output_call(call: ast.Call, env: dict) -> tuple[str, str] | None:
    """(kind, stream) for a call that writes its first argument to a stream;
    None for anything else."""
    f = call.func
    if isinstance(f, ast.Name) and f.id == "print":
        return "print", _print_stream(call, env)
    if isinstance(f, ast.Attribute) and f.attr == "write" and isinstance(f.value, ast.Attribute) \
            and isinstance(f.value.value, ast.Name) and f.value.value.id == "sys" \
            and f.value.attr in STREAMS:
        return "write", f.value.attr
    if (isinstance(f, ast.Name) and f.id in ("SystemExit", "exit")) or (
            isinstance(f, ast.Attribute) and f.attr == "exit"
            and isinstance(f.value, ast.Name) and f.value.id == "sys"):
        return "exit", "stderr"
    return None


def _via_call(call: ast.Call, funcs: dict, env: dict, env_of, rule) -> tuple[str, str] | None:
    """(kind, stream) for a refusal line handed to a function defined in the
    same file that prints it: the stream of the print inside the helper that
    carries the argument, or `unknown` when no single stream can be read."""
    f = call.func
    name = f.id if isinstance(f, ast.Name) else (f.attr if isinstance(f, ast.Attribute) else None)
    if name is None or name.startswith("__") or name not in funcs:
        return None
    helper = funcs[name]
    bound = _bind(helper, call)
    carried = set()
    for p, v in bound.items():
        text = _lit(v, env, call.lineno) if isinstance(v, ast.expr) else None
        if text is not None and rule.search(text):
            carried.add(p)
    if not carried:
        return None
    streams: set = set()
    for inner in ast.walk(helper):
        if not isinstance(inner, ast.Call) or not inner.args:
            continue
        shape = _output_call(inner, env_of(helper))
        if shape and shape[0] in ("print", "write") and any(
                isinstance(n, ast.Name) and n.id in carried and isinstance(n.ctx, ast.Load)
                for n in ast.walk(inner)):
            streams.add(shape[1])
    return "call", (streams.pop() if len(streams) == 1 else "unknown")


def refusal_sites(root: pathlib.Path, py_files: list[str]) -> list[Site]:
    """Every refusal line in every tracked non-test Python file, by stream:
    printed directly, or handed to a same-file helper that prints it.  A site
    whose line merely CONTAINS the word -- prose, a summary count, a row
    labelled with more than a short tag -- is kept with loose_only set, so
    the cost of the stricter rule is a count rather than an assumption."""
    sites: list[Site] = []
    for rel in py_files:
        if not rel.endswith(".py") or pathlib.Path(rel).name.startswith("test_"):
            continue
        tree = _parse(root, rel)
        module_env = _module_env(tree)
        func_of, _, _ = _index(tree, full=False)
        funcs = {n.name: n for n in ast.walk(tree) if isinstance(n, FUNC)}
        envs: dict = {}

        def env_of(func, envs=envs, module_env=module_env):
            if func not in envs:
                envs[func] = _scope_env(func, module_env)
            return envs[func]

        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not node.args:
                continue
            func = func_of[node]
            shape = _output_call(node, env_of(func)) or _via_call(
                node, funcs, env_of(func), env_of, LOOSE)
            if shape is None:
                continue
            text = _lit(node.args[0], env_of(func), node.lineno)
            if text is None or not LOOSE.search(text):
                continue
            sites.append(Site(rel, node.lineno, func.name if func else "<module>", *shape,
                              not REFUSAL_LINE.match(text)))
    return sorted(sites)


def reaching_functions(tree: ast.Module, site_funcs: set) -> set:
    """site_funcs plus every function that calls one of them, transitively:
    an in-process call to any of these can print a refusal."""
    funcs = {n.name: n for n in ast.walk(tree) if isinstance(n, FUNC)}
    reaches = set(site_funcs)
    while True:
        more = {name for name, fn in funcs.items() if name not in reaches and any(
            isinstance(c, ast.Call) and (
                (isinstance(c.func, ast.Name) and c.func.id in reaches)
                or (isinstance(c.func, ast.Attribute) and c.func.attr in reaches))
            for c in ast.walk(fn))}
        if not more:
            return reaches
        reaches |= more


def _path_re(stem: str) -> re.Pattern:
    return re.compile(rf"(?<![A-Za-z0-9_])(?:{re.escape(stem)}\.py(?![A-Za-z0-9_])"
                      rf"|tools\.{re.escape(stem)}(?![A-Za-z0-9_]))")


def _names_tool(value: str, stem: str) -> bool:
    return _path_re(stem).search(value) is not None


def _mentions(node, env: dict, line: int, stem: str, seen: frozenset = frozenset()) -> bool:
    """Does this expression hold the tool's path: a string literal naming
    it, or a name bound (at this line) to one, through literals and calls?"""
    if isinstance(node, ast.Constant):
        return isinstance(node.value, str) and _names_tool(node.value, stem)
    if isinstance(node, ast.JoinedStr):
        literal = "".join(v.value for v in node.values if isinstance(v, ast.Constant))
        return _names_tool(literal, stem) or any(
            _mentions(v, env, line, stem, seen) for v in node.values)
    if isinstance(node, ast.FormattedValue):
        return _mentions(node.value, env, line, stem, seen)
    if isinstance(node, ast.Name):
        if node.id in seen:
            return False
        value = _binding(node.id, env, line)
        return value is not None and _mentions(value, env, line, stem, seen | {node.id})
    if isinstance(node, ast.BinOp):
        return _mentions(node.left, env, line, stem, seen) or _mentions(node.right, env, line, stem, seen)
    if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
        return any(_mentions(e, env, line, stem, seen) for e in node.elts)
    if isinstance(node, ast.Starred):
        return _mentions(node.value, env, line, stem, seen)
    if isinstance(node, ast.Call):
        return any(_mentions(a, env, line, stem, seen) for a in node.args) or any(
            _mentions(k.value, env, line, stem, seen) for k in node.keywords)
    return False


def _mentions_args(call: ast.Call, env: dict, stem: str) -> bool:
    return any(_mentions(a, env, call.lineno, stem) for a in list(call.args)
               + [k.value for k in call.keywords])


def _imports(tree: ast.Module, stem: str) -> tuple[set, dict, set, set]:
    """(module aliases of the tool, its from-imported names, `subprocess`
    names imported bare, run_all module aliases and bare runner names)."""
    mods, froms, subs, runners = set(), {}, set(), set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            for a in n.names:
                if a.name in (stem, f"tools.{stem}"):
                    mods.add(a.asname or a.name)
                if a.name == "run_all":
                    runners.update({a.asname or a.name, *RUNNERS})
        elif isinstance(n, ast.ImportFrom) and n.module:
            if n.module in (stem, f"tools.{stem}"):
                for a in n.names:
                    froms[a.asname or a.name] = a.name
            elif n.module == "subprocess":
                subs.update(a.name for a in n.names)
            elif n.module == "run_all":
                runners.update(a.asname or a.name for a in n.names)
    return mods, froms, subs, runners


def _launch(call: ast.Call, subs: set, runners: set) -> str | None:
    """`subprocess.<fn>` or `run_all.<fn>` when this call starts a process."""
    f = call.func
    if isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name):
        if f.value.id == "subprocess" and f.attr in LAUNCHERS:
            return f"subprocess.{f.attr}"
        if f.value.id in runners and f.attr in RUNNERS:
            return f"run_all.{f.attr}"
    if isinstance(f, ast.Name):
        if f.id in LAUNCHERS and f.id in subs:
            return f"subprocess.{f.id}"
        if f.id in RUNNERS and f.id in runners:
            return f"run_all.{f.id}"
    return None


def _pipe(node, subs: set) -> str | None:
    """PIPE, STDOUT, DEVNULL, None (not set: inherited) or '?'."""
    if node is None:
        return None
    if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) \
            and node.value.id == "subprocess":
        return PIPES.get(node.attr, "?")
    if isinstance(node, ast.Name) and node.id in PIPES and node.id in subs:
        return node.id
    return "?"


def _capture(call: ast.Call, subs: set, launch: str, runner_capture: str, where: str) -> str:
    """Which streams this launch captures."""
    if launch.startswith("run_all."):
        return runner_capture
    kw = {k.arg: k.value for k in call.keywords if k.arg}
    out, err = _pipe(kw.get("stdout"), subs), _pipe(kw.get("stderr"), subs)
    if "STDOUT" in (out, err):
        return "MERGED"
    co = kw.get("capture_output")
    if isinstance(co, ast.Constant) and co.value is True:
        return "SEPARATE"
    if launch == "subprocess.check_output":
        return "STDOUT-ONLY"
    if (out, err) == ("PIPE", "PIPE"):
        return "SEPARATE"
    if (out, err) == ("PIPE", None):
        return "STDOUT-ONLY"
    if (out, err) == (None, "PIPE"):
        return "STDERR-ONLY"
    if (out, err) == ("PIPE", "DEVNULL"):
        return "STDOUT-ONLY"
    if (out, err) == ("DEVNULL", "PIPE"):
        return "STDERR-ONLY"
    if out is None and err is None:
        return "LOG"
    if "DEVNULL" in (out, err) and "PIPE" not in (out, err):
        return "DISCARDED"
    raise Refused(f"{where}:{call.lineno} captures stdout={out}, stderr={err}; "
                  f"this census cannot classify that launch")


def _attr(n, name: str, attrs) -> bool:
    return isinstance(n, ast.Attribute) and isinstance(n.value, ast.Name) \
        and n.value.id == name and n.attr in attrs


def _reads(call: ast.Call, launch: str, stmt, func, parent: dict) -> frozenset:
    """Which captured streams the consumer reads: the result's `.stdout` /
    `.stderr`, a Popen `communicate()` unpack, a `check_output` string, or
    the result object escaping to a caller -- then both, conservatively."""
    if func is None:
        return frozenset()
    reads: set = set()
    p = parent.get(call)
    if isinstance(p, ast.Attribute) and p.attr in STREAMS:
        reads.add(p.attr)
    if not isinstance(stmt, ast.Assign) or not isinstance(stmt.targets[0], (ast.Name, ast.Tuple)):
        return frozenset(reads)
    targets = stmt.targets[0]
    names = [t.id for t in (targets.elts if isinstance(targets, ast.Tuple) else [targets])
             if isinstance(t, ast.Name)]
    later = [n for n in ast.walk(func) if getattr(n, "lineno", 0) > stmt.lineno]
    methods = ("stdout", "stderr", "communicate", "wait", "returncode", "poll", "kill",
               "send_signal", "terminate")

    def loads(name: str) -> list:
        return [n for n in later if isinstance(n, ast.Name) and n.id == name
                and isinstance(n.ctx, ast.Load) and not _attr(parent.get(n), name, methods)]

    if launch == "subprocess.check_output":
        return frozenset(reads | ({"stdout"} if any(loads(n) for n in names) else set()))
    for name in names:
        reads.update(n.attr for n in later if _attr(n, name, STREAMS))
        for n in later:
            if not (isinstance(n, ast.Call) and _attr(n.func, name, ("communicate",))):
                continue
            own = parent.get(n)
            if not (isinstance(own, ast.Assign) and isinstance(own.targets[0], ast.Tuple)
                    and len(own.targets[0].elts) == 2
                    and all(isinstance(t, ast.Name) for t in own.targets[0].elts)):
                continue
            first, second = (t.id for t in own.targets[0].elts)
            reads.update(s for s, nm in (("stdout", first), ("stderr", second))
                         if nm != "_" and any(x.id == nm and x.lineno > own.lineno for x in later
                                              if isinstance(x, ast.Name) and isinstance(x.ctx, ast.Load)))
        if loads(name):
            reads.update(STREAMS)
    return frozenset(reads)


def _capsys(func, parent: dict, owner: dict) -> tuple[str, frozenset]:
    """(SEPARATE when a capsys-style `readouterr()` sits in this function,
    and which of the two streams it then reads)."""
    if func is None:
        return "NOT-READ", frozenset()
    calls = [n for n in ast.walk(func) if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
             and n.func.attr == "readouterr"]
    if not calls:
        return "NOT-READ", frozenset()
    reads: set = set()
    later = [n for n in ast.walk(func) if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)]
    for c in calls:
        p = parent.get(c)
        if isinstance(p, ast.Attribute) and p.attr in ("out", "err"):
            reads.add({"out": "stdout", "err": "stderr"}[p.attr])
        own = owner.get(c)
        if not (isinstance(own, ast.Assign) and isinstance(own.targets[0], (ast.Name, ast.Tuple))):
            continue
        targets = own.targets[0]
        if isinstance(targets, ast.Name):
            reads.update({"out": "stdout", "err": "stderr"}[n.attr] for n in ast.walk(func)
                         if _attr(n, targets.id, ("out", "err")))
        elif len(targets.elts) == 2 and all(isinstance(t, ast.Name) for t in targets.elts):
            first, second = (t.id for t in targets.elts)
            reads.update(s for s, nm in (("stdout", first), ("stderr", second))
                         if any(x.id == nm and x.lineno > own.lineno for x in later))
    return "SEPARATE", frozenset(reads)


def _verdict(kind: str, capture: str, reads: frozenset, tool_streams: set, split: bool) -> str:
    if kind == "hand-trace":
        return "HAND-TRACE"
    if kind in ("make", "ci", "data") or capture in ("MERGED", "LOG", "NOT-READ", "DISCARDED") \
            or not reads:
        return "NO-LOSS"
    if capture == "SEPARATE":
        return "PINNED" if split and (tool_streams - reads) else "NO-LOSS"
    other = "stderr" if capture == "STDOUT-ONLY" else "stdout"
    return "EXPOSED" if other in tool_streams else "NO-LOSS"


DATA_PROGRAMS = frozenset({
    "git", "grep", "rg", "ls", "find", "cat", "sha256sum", "sha1sum", "diff", "cmp", "sed",
    "awk", "wc", "test", "[",
})


def _data_program(call: ast.Call) -> str | None:
    """The program a launch runs when its first argument is a literal from
    DATA_PROGRAMS: the tool's path is then an argument to THAT, read as data.
    Anything else -- an interpreter path, a variable, an unknown program -- is
    treated as a launch of the tool, so a wrong guess here is a visible row
    rather than a missing one."""
    first = call.args[0] if call.args else None
    if isinstance(first, (ast.List, ast.Tuple)) and first.elts:
        first = first.elts[0]
    if isinstance(first, ast.Constant) and isinstance(first.value, str):
        words = first.value.split()
        return words[0] if words and words[0] in DATA_PROGRAMS else None
    return None


def _command_like(literal: str, stem: str) -> bool:
    """A string that is the tool's path, or that begins like a command line
    for it: an interpreter or `{py}` slot, then the path.  A path named inside
    prose, a pathspec or an assertion's needle is not."""
    first = (rf"(?:[\w./-]+/)?{re.escape(stem)}\.py(?![A-Za-z0-9_])"
             rf"|tools\.{re.escape(stem)}(?![A-Za-z0-9_])")
    if re.fullmatch(first, literal.strip()):
        return True
    interpreter = r"(?:\{[^}]*\}|\$\([^)]*\)|[\w./+-]*(?:python3?|py|uv))"
    return re.match(rf"^\s*(?:{interpreter}\s+)?(?:{first})", literal) is not None


def _generic_launch(call: ast.Call, func, env: dict) -> bool:
    """A launch whose command holds a parameter nothing in this file binds:
    the tool, if it runs one, arrives as a runtime argument."""
    if func is None:
        return False
    params = {a.arg for a in func.args.args}
    names = (n for a in call.args for n in ast.walk(a))
    return any(isinstance(n, ast.Name) and n.id in params and isinstance(n.ctx, ast.Load)
               and _binding(n.id, env, call.lineno) is None for n in names)


Src = collections.namedtuple("Src", "text tree func_of owner parent funcs mods froms subs runners "
                                  "launches module_env envs")
_SRC: dict = {}


def _source(root: pathlib.Path, rel: str, stem: str) -> Src | None:
    """One reader file, parsed once per census however many targets ask for
    it; None when it does not name the tool's stem at all."""
    key = (root, rel, stem)
    if key not in _SRC:
        text = _read(root, rel)
        if not re.search(rf"(?<![A-Za-z0-9_]){re.escape(stem)}(?![A-Za-z0-9_])", text):
            _SRC[key] = None
        else:
            tree = _parse(root, rel)
            func_of, owner, parent = _index(tree)
            mods, froms, subs, runners = _imports(tree, stem)
            launches = {n: k for n in ast.walk(tree) if isinstance(n, ast.Call)
                        for k in [_launch(n, subs, runners)] if k}
            _SRC[key] = Src(text, tree, func_of, owner, parent,
                            {n.name: n for n in ast.walk(tree) if isinstance(n, FUNC)},
                            mods, froms, subs, runners, launches, _module_env(tree), {})
    return _SRC[key]


def env_of(src: Src, func) -> dict:
    """`src`'s module constants plus the bindings of `func`."""
    if func not in src.envs:
        src.envs[func] = _scope_env(func, src.module_env)
    return src.envs[func]


def python_rows(root: pathlib.Path, rel: str, tool: str, site_funcs: set, runner_capture: str) -> list[Row]:
    """Every way one Python file reads one tool's output."""
    stem = pathlib.Path(tool).stem
    src = _source(root, rel, stem)
    if src is None:
        return []
    text, tree = src.text, src.tree
    func_of, owner, parent = src.func_of, src.owner, src.parent
    funcs, launches = src.funcs, src.launches
    mods, froms, subs, runners = src.mods, src.froms, src.subs, src.runners
    rows: list[Row] = []
    covered: list[tuple[int, int]] = []
    called: set = set()
    launch_rows: dict = {}
    hand: dict = {}

    def cover(node):
        covered.append((node.lineno, node.end_lineno or node.lineno))

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        env = env_of(src, func_of[node])
        launch = launches.get(node)
        if launch and _mentions(node, env, node.lineno, stem):
            launch_rows.setdefault(node, []).append(None)
            cover(node)
            continue
        f = node.func
        if launch is None and isinstance(f, ast.Name) and f.id in funcs:
            helper = funcs[f.id]
            inner_hits = _via_helper(node, helper, env, env_of(src, helper), stem, subs, runners)
            if inner_hits:
                for inner in inner_hits:
                    launch_rows.setdefault(inner, []).append(node.lineno)
                cover(node)
                continue
            if _mentions_args(call=node, env=env, stem=stem) and launches:
                hand.setdefault(helper.name, []).append(node.lineno)
        target = None
        if isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name) and f.value.id in mods:
            target = f.attr
        elif isinstance(f, ast.Name) and f.id in froms:
            target = froms[f.id]
        if target is None:
            continue
        called.add(target)
        if target != "main" and target not in site_funcs:
            continue
        capture, reads = _capsys(func_of[node], parent, owner)
        note = f"in-process {target}()"
        if capture == "NOT-READ" and func_of[node] is not None:
            callers = _capsys_callers(func_of[node], tree, parent, owner)
            if callers:
                capture, reads = "SEPARATE", frozenset().union(*(r for _, _, r in callers))
                note += f"; captured in caller {', '.join(f'{n}():{ln}' for n, ln, _ in callers)}"
        rows.append(Row(tool, rel, node.lineno, "in-process", capture, reads, "", note))

    for inner, sites in launch_rows.items():
        stmt = owner[inner]
        data = _data_program(inner)
        if data is not None:
            rows.append(Row(tool, rel, stmt.lineno, "data", "", frozenset(), "",
                            f"`{data}` is given {stem}.py as an argument at line {stmt.lineno}: "
                            f"the path is read, the tool is not run"))
            continue
        callers = sorted(s for s in sites if s is not None)
        note = launches[inner]
        if callers:
            note += f"; {func_of[inner].name}() call sites: {callers}"
        rows.append(Row(tool, rel, stmt.lineno, "subprocess",
                        _capture(inner, subs, launches[inner], runner_capture, rel),
                        _reads(inner, launches[inner], stmt, func_of[inner], parent), "", note))

    for name, sites in sorted(hand.items()):
        rows.append(Row(tool, rel, min(sites), "hand-trace", "", frozenset(), "",
                        f"{name}() is given {stem}.py at line(s) {sorted(sites)} but launches "
                        f"nothing; what it returns is executed elsewhere"))

    code_lines = sorted({n.lineno for n in ast.walk(tree)
                         if isinstance(n, ast.Constant) and isinstance(n.value, str)
                         and not isinstance(parent.get(n), ast.Expr) and _command_like(n.value, stem)}
                        | {n.lineno for n in ast.walk(tree) if isinstance(n, ast.JoinedStr)
                           and _command_like("".join(v.value for v in n.values
                                                     if isinstance(v, ast.Constant)), stem)})
    traced = any(r.kind in ("subprocess", "data", "in-process") for r in rows)
    untraced = [ln for ln in code_lines if not any(a <= ln <= b for a, b in covered)]
    generic = sorted(n.lineno for n in launches
                     if _generic_launch(n, func_of[n], env_of(src, func_of[n])))
    if untraced and launches and not traced and not hand:
        rows.append(Row(tool, rel, untraced[0], "hand-trace", "", frozenset(), "",
                        f"names {stem}.py in code at line(s) {untraced[:8]} and launches at "
                        f"{generic[:8] or sorted(n.lineno for n in launches)[:8]}; the path from "
                        f"one to the other is a value this census does not follow"))
    elif generic and not traced:
        mention = [i + 1 for i, ln in enumerate(text.splitlines()) if _path_re(stem).search(ln)]
        if mention:
            rows.append(Row(tool, rel, generic[0], "hand-trace", "", frozenset(), "",
                            f"generic launch(s) at line(s) {generic[:6]} take their command as an "
                            f"argument; a comment or docstring names the tool at {mention[:6]}"))

    if (mods or froms) and not any(r.kind == "in-process" for r in rows):
        rows.append(Row(tool, rel, 0, "import", "", frozenset(), "",
                        f"imports {stem}; calls {sorted(called) or 'nothing'} -- "
                        f"none reaches a function that prints a refusal"))
    mention_lines = [i + 1 for i, ln in enumerate(text.splitlines())
                     if _path_re(stem).search(ln)
                     and not any(a <= i + 1 <= b for a, b in covered)]
    if mention_lines:
        rows.append(Row(tool, rel, mention_lines[0], "mention", "", frozenset(), "",
                        f"{len(mention_lines)} line(s) name {stem}.py outside any launch or "
                        f"call: {mention_lines[:8]}"))
    return rows


def _bind(helper, call) -> dict:
    """parameter name -> the argument node bound at this call: positional,
    then keyword, then the first `*x` spread into a `*args` parameter."""
    a = helper.args
    params = [p.arg for p in [*a.posonlyargs, *a.args, *a.kwonlyargs]]
    bound = {p: v for p, v in zip(params, call.args, strict=False) if isinstance(v, ast.expr)}
    spread = [x.value for x in call.args if isinstance(x, ast.Starred)]
    if a.vararg:
        bound[a.vararg.arg] = spread[0] if spread else ast.List(
            elts=[v for v in call.args[len(params):] if isinstance(v, ast.expr)], ctx=ast.Load())
    bound.update({k.arg: k.value for k in call.keywords if k.arg})
    return bound


def _via_helper(call, helper, env, helper_env, stem, subs, runners) -> list:
    """The launches inside a same-file helper that run the tool: the helper's
    parameter is bound to the tool's path at this call, or the launch
    hardcodes it."""
    bound = _bind(helper, call)
    hits: list = []
    for inner in ast.walk(helper):
        if not isinstance(inner, ast.Call) or inner is call or _launch(inner, subs, runners) is None:
            continue
        used = {n.id for n in ast.walk(inner)
                if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load) and n.id in bound}
        given = any(bound[p] is not None and _mentions(bound[p], env, call.lineno, stem) for p in used)
        if given or _mentions(inner, helper_env, inner.lineno, stem):
            hits.append(inner)
    return hits


def _capsys_callers(helper, tree, parent, owner) -> list:
    """Functions in this file that call `helper` and read a capsys: where an
    in-process launch's streams are read when the launching function does not."""
    out = []
    funcs = [n for n in ast.walk(tree) if isinstance(n, FUNC)]
    for fn in funcs:
        if fn is helper:
            continue
        if not any(isinstance(c, ast.Call) and isinstance(c.func, ast.Name)
                   and c.func.id == helper.name for c in ast.walk(fn)):
            continue
        capture, reads = _capsys(fn, parent, owner)
        if capture == "SEPARATE":
            out.append((fn.name, fn.lineno, reads))
    return out


def _redirect_class(command: str) -> str:
    return "MERGED" if re.search(r"2>&1|&>", command) else "LOG"


def _make_rows(root: pathlib.Path, rel: str, tool: str, runner_capture: str) -> list[Row]:
    """Recipe lines that run the tool, directly or as one job of a runner."""
    stem = pathlib.Path(tool).stem
    lines = _read(root, rel).splitlines()
    variables: dict[str, str] = {}
    logical: list[tuple[int, str]] = []
    i = 0
    while i < len(lines):
        start, buf = i + 1, lines[i]
        while buf.rstrip().endswith("\\") and i + 1 < len(lines):
            i += 1
            buf = buf.rstrip()[:-1] + " " + lines[i]
        m = re.match(r"^([A-Za-z_][A-Za-z0-9_]*)\s*[:?+]?=\s*(.*)$", buf)
        if m:
            variables[m.group(1)] = m.group(2).strip()
        logical.append((start, buf))
        i += 1
    runner_re = re.compile(r"tools/(?:run_all|run_controls)\.py")
    target, rows = None, []
    for start, buf in logical:
        m = re.match(r"^([A-Za-z0-9_./%-]+(?:\s+[A-Za-z0-9_./%-]+)*):", buf)
        if m and not buf.startswith("\t") and not re.match(r"^[A-Za-z_]\w*\s*[:?+]?=", buf):
            target = m.group(1).split()[0]
        if not buf.startswith("\t"):
            continue
        expanded = buf
        for name, value in variables.items():
            if runner_re.search(value):
                expanded = expanded.replace(f"$({name})", value)
        recipe = expanded[1:].strip().lstrip("@-+").strip()
        if runner_re.search(recipe):
            for job in (j for j in re.findall(r'"([^"]+)"', recipe) if _path_re(stem).search(j)):
                rows.append(Row(tool, rel, start, "make", runner_capture, frozenset(), "",
                                f"target {target}: one job of a runner -- `{job.strip()}`"))
        elif _path_re(stem).search(recipe):
            rows.append(Row(tool, rel, start, "make", _redirect_class(recipe), frozenset(), "",
                            f"target {target}: `{recipe[:90]}`"))
    return rows


def _run_blocks(text: str):
    """(line, command) for every `run:` scalar in a workflow file."""
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        m = re.match(r"^(\s*)(?:-\s+)?run:\s*(.*)$", lines[i])
        if not m:
            i += 1
            continue
        indent, rest, start = len(m.group(1)), m.group(2), i + 1
        if rest in ("|", ">", "|-", ">-", "|+", ">+"):
            block = []
            i += 1
            while i < len(lines) and (not lines[i].strip()
                                      or len(lines[i]) - len(lines[i].lstrip()) > indent):
                block.append(lines[i])
                i += 1
            yield start, "\n".join(block)
        else:
            yield start, rest
            i += 1


def _ci_rows(root: pathlib.Path, rel: str, tool: str, make_rows: list[Row]) -> list[Row]:
    """`run:` steps that run the tool, or `make <target>` for a target whose
    recipe does."""
    stem = pathlib.Path(tool).stem
    rows: list[Row] = []
    for start, command in _run_blocks(_read(root, rel)):
        for offset, ln in enumerate(command.splitlines()):
            if _path_re(stem).search(ln):
                rows.append(Row(tool, rel, start + offset, "ci", _redirect_class(ln), frozenset(),
                                "", f"`{ln.strip()[:100]}`"))
                continue
            m = re.search(r"\bmake\s+(?:-[A-Za-z]\s+\S+\s+)*([A-Za-z0-9_-]+)", ln)
            if m and any(r.file == MAKEFILE and r.note.startswith(f"target {m.group(1)}:")
                         for r in make_rows):
                rows.append(Row(tool, rel, start + offset, "ci", "", frozenset(), "",
                                f"`{m.group(0)}`: the Makefile's {m.group(1)} rows apply"))
    return rows


def runner_capture(root: pathlib.Path, files: list[str]) -> str:
    """The streams tools/run_all.py captures, read from its own source."""
    if RUNNER not in files:
        raise Refused(f"{RUNNER} is not in the census; every runner row depends on it")
    for node in ast.walk(_parse(root, RUNNER)):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
                and node.func.attr == "Popen" and isinstance(node.func.value, ast.Name) \
                and node.func.value.id == "subprocess":
            err = {k.arg: k.value for k in node.keywords if k.arg}.get("stderr")
            if isinstance(err, ast.Attribute) and err.attr == "STDOUT":
                return "MERGED"
            raise Refused(f"{RUNNER}:{node.lineno} launches without stderr=subprocess.STDOUT; "
                          f"the runner no longer merges the streams")
    raise Refused(f"{RUNNER} launches nothing; the runner rows cannot be read")


def census(root: pathlib.Path | None = None, files: list[str] | None = None) -> dict:
    """The whole census: sites by stream, then a row for every in-repo
    reader of each split file -- strict or loose-only -- and of the files
    #1988 named."""
    root = ROOT if root is None else root
    _SRC.clear()
    if files is None:
        files = tracked(root)
        n_py = sum(1 for f in files if f.endswith(".py"))
        if n_py < MIN_PY_FILES:
            raise Refused(f"git ls-files found {n_py} Python files, under the {MIN_PY_FILES} this "
                          f"repository has; a partial tree under-reports the census")
    py = [f for f in files if f.endswith(".py")]
    found = refusal_sites(root, py)
    by_file = _by_file([s for s in found if not s.loose_only])
    loose = _by_file(found)
    both = set(STREAMS)
    split = sorted(f for f in by_file if {s.stream for s in by_file[f]} == both)
    loose_split = sorted(f for f in loose
                         if {s.stream for s in loose[f]} == both and f not in split)
    targets = sorted(set(split) | set(loose_split) | {f for f in ISSUE_1988_FILES if f in files})
    capture = runner_capture(root, files)
    readers = [f for f in files if pathlib.Path(f).suffix in READER_SUFFIXES
               and not f.startswith("docs/")]
    make_rows = {t: _make_rows(root, MAKEFILE, t, capture) for t in targets}
    rows: list[Row] = []
    for tool in targets:
        site_funcs = reaching_functions(_parse(root, tool), {s.func for s in loose[tool]})
        streams = {s.stream for s in loose[tool]}
        for rel in readers:
            if rel == tool:
                continue
            try:
                if rel.endswith(".py"):
                    got = python_rows(root, rel, tool, site_funcs, capture)
                elif rel == MAKEFILE:
                    got = make_rows[tool]
                elif rel.startswith(".github/workflows/"):
                    got = _ci_rows(root, rel, tool, make_rows[tool])
                else:
                    continue
            except Refused as exc:
                raise Refused(f"{tool} <- {rel}: {exc}") from exc
            rows.extend(r._replace(verdict=_verdict(r.kind, r.capture, r.reads, streams, tool in split
                                                    or tool in loose_split)) for r in got)
    return {
        "root": str(root),
        "n_python_files": len(py),
        "runner_capture": capture,
        "sites": dict(by_file),
        "loose_only": {f: sorted(s.line for s in loose[f] if s.loose_only)
                       for f in loose if any(s.loose_only for s in loose[f])},
        "split": split,
        "loose_only_split": loose_split,
        "n_loose_only_sites": sum(1 for s in found if s.loose_only),
        "rows": sorted(rows, key=lambda r: (r.tool, r.file, r.line, r.kind)),
    }


def _by_file(sites: list[Site]) -> dict[str, list[Site]]:
    by_file: dict[str, list[Site]] = collections.defaultdict(list)
    for s in sites:
        by_file[s.file].append(s)
    return dict(by_file)


def render_sites(res: dict) -> str:
    n_sites = sum(len(v) for v in res["sites"].values())
    n_files = sum(1 for v in res["sites"].values() if v)
    out = [f"REFUSED LINES BY STREAM: {n_sites} in {n_files} non-test Python files, read by AST. "
           f"A line counts when it begins with REFUSED, optionally after a short tag.",
           f"{res['n_loose_only_sites']} further line(s) in the tree merely CONTAIN the word; "
           f"{len(res['loose_only_split'])} file(s) would split only under that looser rule"
           + (f": {res['loose_only_split']}" if res["loose_only_split"] else "") + ".", ""]
    for f in sorted(res["sites"]):
        ss = res["sites"][f]
        out.append(f"  {'SPLIT' if f in res['split'] else '     '} {f}: {_site_summary(ss)}")
        for s in ss:
            out.append(f"            :{s.line:<5} {s.kind:<6} -> {s.stream:<6} in {s.func}()")
    return "\n".join(out)


def _site_summary(sites: list[Site]) -> str:
    if not sites:
        return "no refusal line"
    by: dict = collections.Counter(s.stream for s in sites)
    return ", ".join(f"{stream} x{by[stream]} ({', '.join(sorted({s.func for s in sites if s.stream == stream}))})"
                     for stream in sorted(by, key=lambda s: (s != "stdout", s != "stderr")))


def _state(res: dict, tool: str) -> str:
    if tool in res["split"]:
        return "SPLIT"
    if tool in res["loose_only_split"]:
        return "SPLIT under the looser rule only"
    streams = {s.stream for s in res["sites"].get(tool, [])}
    if "unknown" in streams:
        return "stream unreadable"
    return "single-stream" if streams else "no refusal line"


def render(res: dict) -> str:
    out = [f"{res['n_python_files']} tracked Python files; {RUNNER} captures "
           f"{res['runner_capture']}.", ""]
    for tool in sorted({r.tool for r in res["rows"]} | set(res["split"]) | set(res["loose_only_split"])):
        ss = res["sites"].get(tool, [])
        out.append(f"{tool}  [{_state(res, tool)}]  {_site_summary(ss)}")
        if tool in res["loose_only_split"]:
            out.append(f"    (+ line(s) {res['loose_only'][tool]} merely contain the word, on stdout)")
        tool_rows = [r for r in res["rows"] if r.tool == tool]
        for r in tool_rows:
            reads = "+".join(sorted(r.reads))
            cap = f"{r.capture}{' reads ' + reads if r.capture == 'SEPARATE' and reads else ''}" \
                if r.capture else ""
            line = f":{r.line}" if r.line else ""
            out.append(f"    {r.verdict:<10} {r.kind:<11}{cap:<34} {r.file}{line}  {r.note}")
        kinds = collections.Counter(r.kind for r in tool_rows)
        launched = sum(kinds[k] for k in ("subprocess", "in-process", "make", "ci"))
        out.append(f"    -- {launched} launch(es), {kinds['import']} import-only, "
                   f"{kinds['mention']} mention(s), {kinds['hand-trace']} hand-trace"
                   + ("" if tool_rows else "; no in-repo file names this tool"))
        out.append("")
    counts: dict = collections.Counter(r.verdict for r in res["rows"])
    out.append(f"{len(res['split'])} files print refusal lines on BOTH streams: "
               f"{', '.join(res['split'])}")
    out.append("rows by verdict: " + (", ".join(f"{k} {counts[k]}" for k in sorted(counts))
                                      or "none"))
    for want in ("EXPOSED", "PINNED", "HAND-TRACE"):
        for r in res["rows"]:
            if r.verdict == want:
                out.append(f"  {want:<10} {r.tool} <- {r.file}:{r.line}  {r.kind} {r.capture} "
                           f"reads {'+'.join(sorted(r.reads)) or '-'}  {r.note}")
    return "\n".join(out)


def render_issue_table(res: dict) -> str:
    """#1988's eleven rows: its grep recipe beside the AST reading."""
    root = pathlib.Path(res["root"])
    out = ["#1988's TABLE: ITS GREP RECIPE BESIDE THE AST READING", "",
           "  file                                    grep prints/stderr  AST stdout/stderr  verdict"]
    for f in ISSUE_1988_FILES:
        if f not in res["sites"] and not any(r.tool == f for r in res["rows"]):
            continue
        prints, errs = _grep_recipe(_read(root, f))
        n = collections.Counter(s.stream for s in res["sites"].get(f, []))
        out.append(f"  {f:<40} {prints:>2}/{errs:<14} {n['stdout']:>2}/{n['stderr']:<13} "
                   f"{'SPLIT' if f in res['split'] else 'single-stream'}")
    return "\n".join(out)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0],
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--sites", action="store_true", help="refusal lines, and #1988's grep recipe")
    ap.add_argument("--json", action="store_true", help="machine-readable")
    a = ap.parse_args(argv)
    try:
        res = census()
        if a.json:
            print(json.dumps({
                **res,
                "sites": {k: [s._asdict() for s in v] for k, v in res["sites"].items()},
                "rows": [r._asdict() | {"reads": sorted(r.reads)} for r in res["rows"]],
            }, indent=1))
        elif a.sites:
            print(render_sites(res))
            print()
            print(render_issue_table(res))
        else:
            print(render(res))
    except Refused as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
