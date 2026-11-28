#!/usr/bin/env python3
"""refusal_candidate_trace -- run a caller FOR REAL and record where a callee's
refusal goes (#1576).

`tools/refusal_sibling_scan.py` says which call sites reach another module's
refusal-raising code with no guard that could catch it. It says so from the
AST, and in its own words: an inventory, not a verdict. A `candidate-escape`
is "unguarded here and no base guard at the boundary", never "escapes at the
exit" -- guards in intermediate library frames are not modelled, an
`except dx.KitRefused` is not a family handler to it, and whether the call can
be reached with refusing input at all is not an AST question.

This tool closes that gap per site, by execution. For each traced site it runs
the entry point in a fresh interpreter -- once with a working input and once
with a known refusing input -- and records what the process did:

  * whether the call at the site EXECUTED: callee, call-site line, a brief of
    its arguments, and whether it returned or raised;
  * what it raised: class, defining module, whether that class is in the
    `refusal.Refused` family, the message, and the live stack from the call
    site out to the entry point;
  * every frame the exception then propagated out of and every handler it
    reached (`file:line` and the `except` clause as written), in order, so a
    catch-and-re-raise reads as two handlers, not one;
  * the result visible to whatever ran the process: exit status, whether
    stderr carries a traceback, the last lines of stdout and stderr.

HOW IT OBSERVES WITHOUT EDITING THE CALLER. The callee is wrapped at import
time by a meta-path finder, so every caller of it -- the entry point included
-- sees the wrapper. When a wrapped call raises a refusal the wrapper tags that
exception INSTANCE and installs a trace function on the frames already on the
stack, which are by construction suspended and will each resume by either
handling the exception or leaving. Nothing is traced before a refusal is
raised, so a working run costs one extra function call per callee invocation.

WHAT IT CANNOT TELL YOU, stated rather than carried:

  * a callee bound by `from x import f` BEFORE the finder runs, or reached
    through an instance attribute, is not intercepted (every site the scan
    reports is a dotted module call);
  * a refusal raised one frame deeper than the wrapped callee is attributed to
    the wrapped call that let it out, which is the call site a repair would
    guard -- the scanner's own blind spot, seen from the other side;
  * a `return` inside a `finally` discards an exception without an `except`
    clause; it reads as "left the frame", which is what it is.

It is a tracer, not a gate: it records `ESCAPED`, `HANDLED`, `NOT-RAISED` or
`UNRESOLVED` per run and never decides whether an escape is a defect.

    python tools/refusal_candidate_trace.py follow PLAN.json SCRIPT.py [args...]
    python tools/refusal_candidate_trace.py run --spec SPEC.py --out RECORD.json
    python tools/refusal_candidate_trace.py report RECORD.json

`follow` is the child half and is not meant to be typed. A spec module defines
`TRACES`, a list of dicts:

    dict(id=..., site="path:line", callee="module.func", wrap=["module.func", ...],
         working=<callable>, refusing=<callable> | None, boundary=[<callable>, ...])

Each callable receives a scratch directory and returns a run dict -- `entry`,
`argv`, `env`, `cwd`, `path` (dirs to prepend to the child's sys.path),
`prelude` (Python source run in the child before the entry point; the control
shape PR #1574 uses, `uh.main = lambda argv: 1`), `expect`, `timeout`,
`sites` (the `path:line` keys a hit on this run is attributed to) -- or a dict
with `unresolved` set when a prerequisite prevents the run.
"""
from __future__ import annotations

import argparse
import ast
import functools
import importlib.util
import json
import os
import pathlib
import signal
import subprocess
import sys
import tempfile
import time
import types

ROOT = pathlib.Path(__file__).resolve().parents[1]
HERE = pathlib.Path(__file__).resolve()
TAG = "_refusal_candidate_trace_token"
#: a refusal by NAME as well as by family: `drums_fx.KitRefused` is a plain
#: RuntimeError, and where it goes is still the question the trace asks
REFUSED_SUFFIX = "Refused"

ESCAPED, HANDLED, NOT_RAISED, UNRESOLVED = "ESCAPED", "HANDLED", "NOT-RAISED", "UNRESOLVED"


# ---------------------------------------------------------------------------
# the child half: wrap the callees, follow the refusal, write the log
# ---------------------------------------------------------------------------
_LOG: dict = {"calls": [], "follows": [], "uncaught": None, "system_exit": None,
              "wrap_missing": [], "python": sys.version.split()[0]}
_POSITIONS: dict = {}      # code object -> its per-instruction (line, end) pairs
_TREES: dict = {}         # source path -> (ast tree, {function first line: name})


def _in_family(exc: BaseException) -> bool:
    return any(c.__module__ == "refusal" and c.__name__ == "Refused"
               for c in type(exc).__mro__)


def _is_refusal(exc: BaseException) -> bool:
    return _in_family(exc) or type(exc).__name__.endswith(REFUSED_SUFFIX)


def _exc(exc: BaseException) -> dict:
    mro = [f"{c.__module__}.{c.__qualname__}" for c in type(exc).__mro__ if c is not object]
    return {"class": f"{type(exc).__module__}.{type(exc).__qualname__}", "mro": mro,
            "in_family": _in_family(exc), "message": str(exc)[:400]}


def _rel(path: str) -> str:
    p = pathlib.Path(path)
    try:
        return str(p.relative_to(ROOT))
    except ValueError:
        return str(p)


def _span(frame) -> tuple[int, int]:
    """(first, last) source line of the instruction the frame is stopped on,
    which for a CALL is the whole call expression, however many lines it spans."""
    code = frame.f_code
    pos = _POSITIONS.get(code)
    if pos is None:
        try:
            pos = [(n or 0, e or n or 0) for n, e, _c, _ec in code.co_positions()]
        except AttributeError:                                   # pragma: no cover
            pos = []
        _POSITIONS[code] = pos
    i = frame.f_lasti // 2
    if not pos or i >= len(pos):
        return frame.f_lineno, frame.f_lineno
    return pos[i]


def _where(frame) -> dict:
    first, last = _span(frame)
    return {"file": _rel(frame.f_code.co_filename), "line": frame.f_lineno,
            "first": first, "last": last, "fn": frame.f_code.co_name}


def _brief(value, depth: int = 0) -> object:
    """Enough of an argument to recognise the call, never the audio."""
    kind = type(value).__name__
    if value is None or isinstance(value, (bool, int, float, str)):
        return value if not isinstance(value, str) else value[:80]
    if hasattr(value, "shape") and hasattr(value, "dtype"):
        try:
            import numpy as np
            a = np.asarray(value)
            finite = bool(np.isfinite(a).all()) if a.dtype.kind == "f" else True
            pk = float(np.abs(a).max()) if a.size and a.dtype.kind in "fiu" else None
            first = float(a.flat[0]) if a.size and a.dtype.kind in "fiu" else None
            return f"{kind}{tuple(a.shape)} {a.dtype} peak={pk} first={first} finite={finite}"
        except Exception:                                        # noqa: BLE001 -- a brief never raises
            return f"{kind}{tuple(value.shape)}"
    if isinstance(value, dict):
        if depth:
            return f"dict({len(value)})"
        return {str(k)[:40]: _brief(v, depth + 1) for k, v in list(value.items())[:12]}
    if isinstance(value, (list, tuple)):
        if depth:
            return f"{kind}({len(value)})"
        return [_brief(v, depth + 1) for v in value[:12]]
    return kind


def _tree(path: str):
    """(ast, {first line: function name}) for one source file, parsed once."""
    t = _TREES.get(path)
    if t is None:
        try:
            src = pathlib.Path(path).read_text()
            tree = ast.parse(src)
        except (OSError, SyntaxError, ValueError):
            tree = None
        t = (tree, {})
        _TREES[path] = t
    return t


def _line_role(frame) -> tuple[str, str | None]:
    """("except", clause) when the frame resumed inside an `except` body of
    its own function, ("finally", None) inside a `finally`, else
    ("other", None): resumption with no `except` clause, which is a context
    manager's `__exit__` returning true or a `return` inside a `finally`."""
    code = frame.f_code
    tree, _ = _tree(code.co_filename)
    if tree is None:
        return "other", None

    def owner(node) -> ast.AST | None:
        best = None
        for n in ast.walk(tree):
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)) \
                    and n.lineno <= node.lineno and n.end_lineno >= node.end_lineno:
                if best is None or n.lineno > best.lineno:
                    best = n
        return best

    def mine(node) -> bool:
        own = owner(node)
        if own is None:
            return code.co_name == "<module>"
        first = own.lineno if not isinstance(own, ast.Lambda) else own.lineno
        return first == code.co_firstlineno

    line = frame.f_lineno
    handler, finally_hit = None, False
    for node in ast.walk(tree):
        if not isinstance(node, ast.Try) or not mine(node):
            continue
        for stmt in node.finalbody:
            if stmt.lineno <= line <= stmt.end_lineno:
                finally_hit = True
        for h in node.handlers:
            body = h.body
            # the header line too: CPython resumes a handling frame ON the
            # `except ...:` line, before the body's first statement
            if h.lineno <= line <= max(s.end_lineno for s in body):
                if handler is None or node.lineno > handler[0]:
                    handler = (node.lineno, h)
    if handler is not None:
        h = handler[1]
        return "except", (ast.unparse(h.type) if h.type is not None else "<bare>")
    return ("finally", None) if finally_hit else ("other", None)


class _Traced:
    def __init__(self, callee: str, fn, inject: frozenset):
        self.callee, self._fn, self._inject = callee, fn, inject
        functools.update_wrapper(self, fn, updated=())

    def __call__(self, *args, **kwargs):
        frame = sys._getframe(1)
        rec = {"callee": self.callee, "caller": _where(frame),
               "args": _brief(args), "kwargs": _brief(kwargs)}
        _LOG["calls"].append(rec)
        try:
            out = self._fn(*args, **kwargs)
        except BaseException as exc:
            rec["outcome"], rec["raised"] = "raised", _exc(exc)
            if _is_refusal(exc) and "NO_FOLLOW" not in self._inject:
                _follow(exc, frame, self._inject)
            raise
        rec["outcome"], rec["returned"] = "returned", _brief(out, 1)
        return out


def _follow(exc: BaseException, caller, inject: frozenset) -> None:
    """Tag `exc` and watch the frames it is about to unwind through."""
    token = object()
    try:
        setattr(exc, TAG, token)
    except Exception:                                            # noqa: BLE001
        return
    stack = []
    f = caller
    while f is not None and f.f_code.co_filename != str(HERE):
        stack.append(f)
        f = f.f_back
    ev = {"raised": _exc(exc), "stack": [_where(fr) for fr in stack],
          "propagated_out": [], "handlers": [], "finally": [], "left_entry": False}
    _LOG["follows"].append(ev)
    receiving: set[int] = {id(caller)} if stack else set()
    top = id(stack[-1]) if stack else None

    def local(fr, event, arg):
        if event == "exception":
            if getattr(arg[1], TAG, None) is token:
                receiving.add(id(fr))                # a handler that re-raised it
        elif event == "line" and id(fr) in receiving:
            if "FINALLY_IS_HANDLER" in inject:
                kind, clause = "except", "<injected: any resumption>"
            else:
                kind, clause = _line_role(fr)
            if kind == "finally":
                ev["finally"].append(_where(fr))
            else:
                ev["handlers"].append({**_where(fr), "clause": clause, "role": kind})
                receiving.discard(id(fr))
        elif event == "return" and id(fr) in receiving:
            receiving.discard(id(fr))
            ev["propagated_out"].append(_where(fr))
            if id(fr) == top:
                ev["left_entry"] = True
        return local

    for fr in stack:
        fr.f_trace = local

    def glob(fr, event, arg):
        # frames ENTERED while the exception is in flight (`__exit__`, a
        # `finally` that calls something) are followed; nothing else is
        return local if receiving else None

    if "NO_FOLLOW" not in inject:
        sys.settrace(glob)
        try:
            import threading
            threading.settrace(glob)
        except Exception:                                        # pragma: no cover
            pass


class _WrapLoader:
    def __init__(self, inner, funcs, inject):
        self._inner, self._funcs, self._inject = inner, funcs, inject

    def create_module(self, spec):
        return self._inner.create_module(spec)

    def exec_module(self, module):
        self._inner.exec_module(module)
        for name in self._funcs:
            orig = getattr(module, name, None)
            if orig is None:
                _LOG["wrap_missing"].append(f"{module.__name__}.{name}")
                continue
            setattr(module, name, _Traced(f"{module.__name__}.{name}", orig, self._inject))

    def __getattr__(self, name):
        return getattr(self._inner, name)


class _WrapFinder:
    """Wrap named functions the moment their module is first executed, so a
    caller's own `import x as y` binds a module that already carries them."""

    def __init__(self, targets: dict[str, list[str]], inject: frozenset):
        self._targets, self._inject = targets, inject

    def find_spec(self, fullname, path=None, target=None):
        if fullname not in self._targets or target is not None:
            return None
        for finder in sys.meta_path:
            if finder is self:
                continue
            hook = getattr(finder, "find_spec", None)
            if hook is None:
                continue
            spec = hook(fullname, path, target)
            if spec is not None:
                if spec.loader is not None:
                    spec.loader = _WrapLoader(spec.loader, self._targets.pop(fullname), self._inject)
                return spec
        return None


def follow(plan_path: str, script: str, args: list[str]) -> int:
    plan = json.loads(pathlib.Path(plan_path).read_text())
    inject = frozenset(plan.get("inject", ()))
    script = str(pathlib.Path(script).resolve())
    sys.meta_path.insert(0, _WrapFinder({k: list(v) for k, v in plan.get("wrap", {}).items()}, inject))
    for d in reversed(plan.get("path", ())):
        if d not in sys.path:
            sys.path.insert(0, d)
    sys.argv = [script, *args]
    rc = 0
    try:
        if plan.get("prelude"):
            exec(compile(plan["prelude"], "<refusal_candidate_trace prelude>", "exec"), {})
        main = types.ModuleType("__main__")
        main.__file__ = script
        main.__package__ = None
        main.__spec__ = None
        sys.modules["__main__"] = main
        code = compile(pathlib.Path(script).read_text(), script, "exec", dont_inherit=True)
        exec(code, main.__dict__)                              # noqa: S102 -- the entry point
    except SystemExit as e:
        c = e.code
        if c is None or isinstance(c, int):
            rc, _LOG["system_exit"] = (c or 0), c
        else:
            print(c, file=sys.stderr)                          # what the interpreter does
            rc, _LOG["system_exit"] = 1, str(c)[:300]
    except BaseException as e:                                 # noqa: BLE001 -- the interpreter's own job
        _LOG["uncaught"] = {**_exc(e), "followed": hasattr(e, TAG)}
        sys.excepthook(type(e), e, e.__traceback__)
        rc = 1
    finally:
        sys.settrace(None)
        pathlib.Path(plan["log"]).write_text(json.dumps(_LOG, indent=1, default=str) + "\n")
    return rc


# ---------------------------------------------------------------------------
# the parent half: run the child, read the log back
# ---------------------------------------------------------------------------
def run_child(run: dict, workdir: pathlib.Path, wrap: list[str], sites: list[str],
              inject: list[str] = (), extra_plan: dict | None = None) -> dict:
    """One traced execution of `run["entry"]`. Never raises on the child's
    behalf: a timeout is recorded as `UNRESOLVED`, not as a verdict."""
    entry = str(pathlib.Path(run["entry"]).resolve())
    log = workdir / f"log-{time.time_ns()}.json"
    plan = {"log": str(log), "wrap": {w.split(".")[0]: [] for w in wrap},
            "path": [str(p) for p in run.get("path", ())],
            "prelude": run.get("prelude"), "inject": list(inject)}
    for w in wrap:
        mod, _, fn = w.partition(".")
        plan["wrap"][mod].append(fn)
    plan.update(extra_plan or {})
    plan_path = workdir / f"plan-{time.time_ns()}.json"
    plan_path.write_text(json.dumps(plan))
    env = dict(os.environ)
    env.update({k: str(v) for k, v in run.get("env", {}).items()})
    env["PYTHONUNBUFFERED"] = "1"
    env.pop("PYTHONDONTWRITEBYTECODE", None)
    cmd = [sys.executable, str(HERE), "follow", str(plan_path), entry, *[str(a) for a in run["argv"]]]
    started = time.time()
    rec = {"entry": _rel(entry), "argv": [str(a) for a in run["argv"]], "role": run.get("role"),
           "timeout_s": run.get("timeout", 900)}
    proc = subprocess.Popen(cmd, cwd=str(run.get("cwd", ROOT)), env=env, text=True,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
    try:
        out, err = proc.communicate(timeout=rec["timeout_s"])
    except subprocess.TimeoutExpired:
        os.killpg(proc.pid, signal.SIGKILL)
        out, err = proc.communicate()
        rec.update(verdict=UNRESOLVED, unresolved=f"timed out after {rec['timeout_s']} s")
    rec["seconds"] = round(time.time() - started, 1)
    rec["exit_status"] = proc.returncode
    rec["traceback"] = "Traceback (most recent call last)" in err
    rec["stdout_tail"] = out.strip().splitlines()[-12:]
    rec["stderr_tail"] = err.strip().splitlines()[-12:]
    try:
        logrec = json.loads(log.read_text()) if log.exists() else None
    except ValueError:
        logrec = None
    if logrec is None:
        rec.setdefault("verdict", UNRESOLVED)
        rec.setdefault("unresolved", "the child wrote no log (it exited before the entry point ran)")
        return rec
    rec["wrap_missing"] = logrec["wrap_missing"]
    rec["system_exit"] = logrec["system_exit"]
    rec["calls"] = [c for c in logrec["calls"] if _hit(c, sites)]
    rec["site_executed"] = sorted({f"{c['caller']['file']}:{c['caller']['line']}" for c in rec["calls"]})
    rec["follows"] = [f for f in logrec["follows"] if _hit_stack(f, sites)]
    rec["all_raised"] = sorted({c["raised"]["class"] for c in logrec["calls"] if c.get("raised")})
    uncaught = logrec["uncaught"]
    rec["uncaught"] = uncaught
    if "verdict" not in rec:
        rec["verdict"] = classify(rec)
    return rec


def _hit(call: dict, sites: list[str]) -> bool:
    """Does this call sit at one of `sites`? Its own line, or inside the
    source span of its call expression when the call is written over several
    lines (the scanner names the first)."""
    c = call["caller"]
    for s in sites:
        path, _, line = s.rpartition(":")
        if path == c["file"] and c["first"] <= int(line) <= c["last"]:
            return True
    return False


def _hit_stack(followed: dict, sites: list[str]) -> bool:
    return bool(followed["stack"]) and _hit({"caller": followed["stack"][0]}, sites)


def classify(rec: dict) -> str:
    """ESCAPED, HANDLED, NOT-RAISED or UNRESOLVED for one run, from what the
    child observed -- never from what was expected."""
    if rec.get("unresolved"):
        return UNRESOLVED
    follows = rec.get("follows", [])
    if not follows:
        # a refusal raised at the site that was not followed is UNRESOLVED:
        # "not raised" would contradict the record's own `calls`
        if any(c.get("raised") for c in rec.get("calls", ())):
            return UNRESOLVED
        return NOT_RAISED
    last = follows[-1]
    if rec.get("uncaught") and rec["uncaught"].get("followed"):
        return ESCAPED
    if last["handlers"] and not last["left_entry"]:
        return HANDLED
    if rec.get("system_exit") is not None and not last["left_entry"]:
        return HANDLED
    return UNRESOLVED


def trace_one(spec: dict, workdir: pathlib.Path, roles: tuple[str, ...] = ("working", "refusing")) -> dict:
    sites = spec.get("sites") or [spec["site"]]
    out = {"id": spec["id"], "site": spec["site"], "callee": spec["callee"],
           "classification": spec.get("classification"), "runs": {}}
    for role in roles:
        make = spec.get(role)
        if make is None:
            out["runs"][role] = None
            continue
        run = make(workdir / role)
        if run.get("unresolved"):
            out["runs"][role] = {"role": role, "verdict": UNRESOLVED, "unresolved": run["unresolved"],
                                 "expect": run.get("expect")}
            continue
        run["role"] = role
        rec = run_child(run, workdir / role, spec["wrap"], sites,
                        inject=run.get("inject", ()))
        rec["expect"] = run.get("expect")
        rec["note"] = run.get("note")
        out["runs"][role] = rec
    return out


def load_spec(path: pathlib.Path):
    mod = importlib.util.module_from_spec(importlib.util.spec_from_file_location(path.stem, path))
    sys.path.insert(0, str(path.parent))
    sys.modules[path.stem] = mod
    importlib.util.spec_from_file_location(path.stem, path).loader.exec_module(mod)
    return mod.TRACES


def report(record: dict) -> str:
    lines = [f"# refusal_candidate_trace @ {record['revision']}"
             f" ({'dirty' if record['tree_dirty'] else 'clean'} tree),"
             f" scanner {record['scanner']['rev']} roots={','.join(record['scanner']['roots'])}"]
    for t in record["traces"]:
        runs = t["runs"]
        w, r = runs.get("working") or {}, runs.get("refusing") or {}
        lines.append(f"{t['id']:52s} {t['callee']:36s} "
                     f"working={w.get('verdict', '-'):<10s} refusing={r.get('verdict', '-'):<10s} "
                     f"exit={r.get('exit_status', '-')} tb={int(bool(r.get('traceback')))}")
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("follow")
    f.add_argument("plan")
    f.add_argument("script")
    f.add_argument("args", nargs="*")
    r = sub.add_parser("run")
    r.add_argument("--spec", type=pathlib.Path, required=True)
    r.add_argument("--out", type=pathlib.Path, required=True)
    r.add_argument("--only", action="append", default=[], help="trace id (repeatable)")
    r.add_argument("--roles", default="working,refusing,boundary")
    r.add_argument("--keep", type=pathlib.Path, default=None, help="scratch dir to reuse (kept)")
    p = sub.add_parser("report")
    p.add_argument("record", type=pathlib.Path)
    a = ap.parse_args(argv)
    if a.cmd == "follow":
        return follow(a.plan, a.script, a.args)
    if a.cmd == "report":
        print(report(json.loads(a.record.read_text())))
        return 0
    traces = load_spec(a.spec)
    if a.only:
        traces = [t for t in traces if t["id"] in set(a.only)]
    sys.path.insert(0, str(ROOT / "tools"))
    import refusal_sibling_scan as rss
    scan = json.loads(json.dumps(_scan_json(rss)))
    out = {"revision": _git("rev-parse", "HEAD"), "tree_dirty": bool(_git("status", "--porcelain").strip()),
           "dirty_paths": [ln[3:] for ln in _git("status", "--porcelain").splitlines()],
           "scanner": scan, "traces": []}
    roles = tuple(r_ for r_ in a.roles.split(",") if r_)
    workroot = a.keep or pathlib.Path(tempfile.mkdtemp(prefix="refusal-candidate-trace-"))
    workroot.mkdir(parents=True, exist_ok=True)
    for spec in traces:
        print(f"== {spec['id']}", flush=True)
        t = trace_one(spec, workroot / spec["id"].replace("/", "_").replace(":", "_"), roles)
        out["traces"].append(t)
        for role, rec in t["runs"].items():
            if rec:
                print(f"   {role:9s} {rec.get('verdict')} exit={rec.get('exit_status')} "
                      f"{rec.get('unresolved', '')}", flush=True)
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text(json.dumps(out, indent=1, default=str) + "\n")
    print(f"wrote {a.out} (scratch kept in {workroot})")
    return 0


def _git(*args: str) -> str:
    return subprocess.run(["git", "-C", str(ROOT), *args], capture_output=True, text=True).stdout


def _scan_json(rss) -> dict:
    files = rss.tracked_files()
    scope = [f for f in files if any(str(f.relative_to(rss.ROOT)).startswith(r_ + "/") for r_ in rss.SCAN_ROOTS)]
    sites = rss.scan(scope, rss.build_index(files))
    behind = subprocess.run(["git", "-C", str(ROOT), "rev-list", "--count", "HEAD..origin/main"],
                            capture_output=True, text=True).stdout.strip() or "?"
    return dict(rev=_git("rev-parse", "--short", "HEAD"), behind_origin_main=behind,
                roots=list(rss.SCAN_ROOTS), files_scanned=len(scope), sites=sites)


if __name__ == "__main__":
    sys.exit(main())
