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

TWO RESUMPTIONS THAT ARE NOT HANDLERS, each measured on CPython 3.12.3 and
3.13.18: a frame unwinding through a `with` resumes on the header at
PUSH_EXC_INFO to call `__exit__`; a frame unwinding out of a list, set or dict
comprehension inlined into it (PEP 709) resumes on the comprehension's own
line at SWAP to drop the half-built container, before any enclosing `except`
is reached. Both leave the frame unwinding and are not recorded as handlers
(#1958).

WHAT IT CANNOT TELL YOU, stated rather than carried:

  * a callee bound by `from x import f` BEFORE the finder runs, or reached
    through an instance attribute, is not intercepted (every site the scan
    reports is a dotted module call);
  * a refusal raised one frame deeper than the wrapped callee is attributed to
    the wrapped call that let it out, which is the call site a repair would
    guard -- the scanner's own blind spot, seen from the other side;
  * a `return` inside a `finally` discards an exception without an `except`
    clause; it reads as "left the frame", which is what it is;
  * an inlined comprehension whose line lies inside a `finally` body is listed
    with that `finally`, on the comprehension's line: the frame did resume
    there, to run the comprehension's cleanup, not at the body's first
    statement. No handler is claimed.

It is a tracer, not a gate: per run and per site it records `ESCAPED` (the
refusal left the entry point), `HANDLED` (a clause took it), `RETURNED` (the
call ran and came back), `NOT-REACHED` (this run never got to the site) or
`UNRESOLVED` (a prerequisite was missing), and never decides whether an
escape is a defect.

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
import opcode
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

ESCAPED, HANDLED, RETURNED, NOT_REACHED, UNRESOLVED = (
    "ESCAPED", "HANDLED", "RETURNED", "NOT-REACHED", "UNRESOLVED")


# ---------------------------------------------------------------------------
# the child half: wrap the callees, follow the refusal, write the log
# ---------------------------------------------------------------------------
_LOG: dict = {"calls": [], "follows": [], "uncaught": None, "system_exit": None,
              "wrap_missing": [], "python": sys.version.split()[0]}
_POSITIONS: dict = {}      # code object -> its per-instruction (line, end) pairs
_TREES: dict = {}         # source path -> its ast tree, parsed once


def _in_family(exc: BaseException) -> bool:
    return any(c.__module__ == "refusal" and c.__name__ == "Refused"
               for c in type(exc).__mro__)


def _is_refusal(exc: BaseException) -> bool:
    return _in_family(exc) or type(exc).__name__.endswith(REFUSED_SUFFIX)


def _exc(exc: BaseException) -> dict:
    mro = [f"{c.__module__}.{c.__qualname__}" for c in type(exc).__mro__ if c is not object]
    return {"class": f"{type(exc).__module__}.{type(exc).__qualname__}", "mro": mro,
            "in_family": _in_family(exc), "message": str(exc)[:400]}


_ROOT = ROOT             # the TRACED tree's root: paths are named relative to it


def _rel(path: str) -> str:
    """Repo-relative inside the traced tree (site keys are), absolute outside
    it -- a sandbox clone names its own root, never this file's checkout."""
    p = pathlib.Path(path)
    try:
        return str(p.relative_to(_ROOT))
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
    """One source file's ast, parsed once (None if it cannot be read)."""
    tree = _TREES.get(path)
    if tree is None:
        try:
            tree = ast.parse(pathlib.Path(path).read_text())
        except (OSError, SyntaxError, ValueError):
            tree = False                        # a cacheable "cannot be read"
        _TREES[path] = tree
    return tree or None


def _line_role(frame) -> dict:
    """What the line the frame just resumed on IS, in its own function.
    {"role": "except", "clause": ..., "handler": (try line, header line, body
    first line, body last line)} inside an `except` clause COUNTING ITS
    HEADER -- CPython resumes an unwinding frame on the `except ...:` line to
    test the match, matched or not; {"role": "finally"} inside a `finally`;
    {"role": "other"} anywhere else, which while unwinding is a `with` header
    or a resumption with no clause at all (a context manager's `__exit__`
    returned true, a `return` written inside a `finally`). The "other" dict
    carries "comprehension": True when the line lies inside a list, set or
    dict comprehension of this function -- the constructs CPython 3.12 inlines
    into it (PEP 709), which own no clause and whose one resumption while
    unwinding is the cleanup that drops the half-built container. A generator
    expression keeps a frame of its own and never sets it."""
    code = frame.f_code
    tree = _tree(code.co_filename)
    other: dict = {"role": "other"}
    if tree is None:
        return other

    def first_line(fn) -> int:
        # a code object starts on its first DECORATOR line; its ast node on
        # `def` (measured on CPython 3.12: a generator whose `finally` ran
        # while a refusal unwound through its `with` read as "no clause")
        return min([fn.lineno] + [d.lineno for d in getattr(fn, "decorator_list", [])])

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
        return first_line(own) == code.co_firstlineno

    line = frame.f_lineno

    def own_comprehension(node) -> bool:
        # an inlined list, set or dict comprehension of this function spanning
        # the line. The span only says the resumption is INSIDE one: a frame
        # that carried on after a swallow lands on the next statement, which
        # lies in a span whenever that statement holds a comprehension. What
        # separates the cleanup from a carry-on is the opcode (`local()`)
        return isinstance(node, (ast.ListComp, ast.SetComp, ast.DictComp)) \
            and node.lineno <= line <= node.end_lineno and mine(node)

    handler, finally_hit = None, False
    for node in ast.walk(tree):
        if not isinstance(node, ast.Try) or not mine(node):
            continue
        for stmt in node.finalbody:
            if stmt.lineno <= line <= stmt.end_lineno:
                finally_hit = True
        for h in node.handlers:
            if h.lineno <= line <= max(st.end_lineno for st in h.body):
                if handler is None or node.lineno > handler[0]:
                    handler = (node.lineno, h)
    if handler is not None:
        h = handler[1]
        return {"role": "except",
                "clause": ast.unparse(h.type) if h.type is not None else "<bare>",
                "handler": (handler[0], h.lineno, h.body[0].lineno,
                            max(st.end_lineno for st in h.body))}
    if finally_hit:
        return {"role": "finally"}
    if any(own_comprehension(n) for n in ast.walk(tree)):
        return {"role": "other", "comprehension": True}
    return other


def _opcode_at(frame) -> str:
    """The instruction the frame is stopped on, by name. Read from the raw
    bytecode: `f_lasti` is a byte offset, and is not an index into
    `dis.get_instructions`, which hides inline cache entries."""
    return opcode.opname[frame.f_code.co_code[frame.f_lasti]]


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
                _follow(exc, frame, self._inject, self.callee)
            raise
        rec["outcome"], rec["returned"] = "returned", _brief(out, 1)
        return out


def _follow(exc: BaseException, caller, inject: frozenset, callee: str) -> None:
    """Tag `exc` and watch the frames it is about to unwind through. Each of
    them is suspended on a call into the callee, so its next event says what
    it did: a clause it tested and did not match (`tested`), one it entered
    (`handlers`), a `finally` or `with` it ran on the way out, an inlined
    comprehension's cleanup, or leaving."""
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
    ev = {"callee": callee, "raised": _exc(exc), "stack": [_where(fr) for fr in stack],
          "propagated_out": [], "handlers": [], "tested": [], "finally": [],
          "with_unwind": [], "left_entry": False}
    _LOG["follows"].append(ev)
    receiving: set[int] = {id(caller)} if stack else set()
    top = id(stack[-1]) if stack else None
    pending: dict[int, dict] = {}       # frame -> the clause it is testing

    def entered(frame, clause: dict) -> None:
        ev["handlers"].append({**clause, "role": "except", "in_tree": not clause["file"].startswith("/")})
        receiving.discard(id(frame))
        pending.pop(id(frame), None)

    def tested(fid: int) -> None:
        clause = pending.pop(fid, None)
        if clause is not None:
            ev["tested"].append({**clause, "role": "except", "in_tree": not clause["file"].startswith("/")})

    def local(fr, event, arg):
        fid = id(fr)
        if fr.f_code.co_filename == str(HERE):      # the tracer's own frames
            return None
        if event == "exception":
            if getattr(arg[1], TAG, None) is token:
                clause = pending.get(fid)
                if clause is not None:               # `except X: raise`, one line
                    entered(fr, clause)
                receiving.add(fid)                   # a handler that re-raised it
        elif event == "line" and fid in receiving:
            if "FINALLY_IS_HANDLER" in inject:
                tested(fid)
                entered(fr, {**_where(fr), "clause": "<injected: any resumption>"})
            elif _line_role(fr)["role"] == "other" and _opcode_at(fr) == "PUSH_EXC_INFO" \
                    and "WITH_IS_HANDLER" not in inject:
                # a `with` header, resumed to call `__exit__` (measured on
                # CPython 3.12: PUSH_EXC_INFO on the header line): the frame
                # is still unwinding and will either leave or carry on
                tested(fid)
                ev["with_unwind"].append(_where(fr))
            else:
                role = _line_role(fr)
                if role["role"] == "finally":
                    tested(fid)
                    ev["finally"].append(_where(fr))
                elif role["role"] == "except":
                    _try, header, first, last = role["handler"]
                    clause = pending.get(fid)
                    in_body = first > header and first <= fr.f_lineno <= last
                    if clause is not None and clause["handler"] == header:
                        if in_body:
                            entered(fr, clause)
                    elif in_body:
                        entered(fr, {**_where(fr), "clause": role["clause"],
                                     "handler": header})
                    else:
                        tested(fid)
                        pending[fid] = {**_where(fr), "clause": role["clause"],
                                        "handler": header, "one_line": header == first}
                elif role.get("comprehension") and _opcode_at(fr) == "SWAP" \
                        and "COMPREHENSION_IS_HANDLER" not in inject:
                    # an inlined comprehension's own cleanup (measured on
                    # CPython 3.12.3: the frame resumes on the comprehension's
                    # line AT `SWAP`, to drop the half-built container, then
                    # re-raises): the frame is still unwinding, and the clause
                    # that takes the exception is still to come. The line alone
                    # does not say so -- a frame that carried on after a swallow
                    # resumes on the statement AFTER the `with`, and that
                    # statement is inside a comprehension's span when it holds
                    # one (a carry-on of `e = [n for n in xs]` starts on
                    # LOAD_CONST, not SWAP). Like the `with` branch above this
                    # leans on one opcode; an interpreter that begins the
                    # cleanup differently reads the cleanup as a carry-on
                    # (the pre-repair reading), which the paired test turns red
                    tested(fid)
                else:
                    # the frame carried on with no clause of its own: either
                    # a one-line `except X: ...` it just entered, or a
                    # context manager's `__exit__` that swallowed the exception
                    clause = pending.get(fid)
                    if clause is not None and clause["one_line"]:
                        entered(fr, clause)
                    else:
                        tested(fid)
                        entered(fr, {**_where(fr), "clause": None})
            return local
        elif event == "return" and fid in receiving:
            clause = pending.get(fid)
            if clause is not None and clause["one_line"] and arg is not None:
                entered(fr, clause)                  # `except X: return <value>`, one line
            else:
                # a one-line `except X: return` that returns None is the one
                # shape this cannot tell from a non-match: stated, not carried
                tested(fid)
            receiving.discard(fid)
            ev["propagated_out"].append(_where(fr))
            if fid == top:
                ev["left_entry"] = True
        return local

    for fr in stack:
        fr.f_trace = local

    def glob(fr, event, arg):
        # frames ENTERED while the exception is in flight (`__exit__`, a
        # `finally` that calls something) are followed; nothing else is
        return local if receiving else None

    sys.settrace(glob)
    try:
        import threading
        threading.settrace(glob)
    except Exception:                                            # pragma: no cover
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


def _without_harness_frames(exc: BaseException):
    """The exception's traceback with this file's frames unlinked, so the
    child prints the traceback the real CLI prints, not one that names the
    tracer. `__traceback__` is rewritten in place: the hook reads it there."""
    keep = last = None
    tb = exc.__traceback__
    while tb is not None:
        if tb.tb_frame.f_code.co_filename != str(HERE):
            if last is None:
                keep = tb
            else:
                last.tb_next = tb
            last = tb
        tb = tb.tb_next
    if last is not None:
        last.tb_next = None
    exc.__traceback__ = keep
    return keep


def follow(plan_path: str, script: str, args: list[str]) -> int:
    plan = json.loads(pathlib.Path(plan_path).read_text())
    inject = frozenset(plan.get("inject", ()))
    script = str(pathlib.Path(script).resolve())
    global _ROOT
    _ROOT = pathlib.Path(plan["root"]).resolve()
    sys.meta_path.insert(0, _WrapFinder({k: list(v) for k, v in plan.get("wrap", {}).items()}, inject))
    for d in reversed(plan.get("path", ())):
        if d not in sys.path:
            sys.path.insert(0, d)
    # what `python SCRIPT.py` gives an entry point: its own directory first
    sys.path.insert(0, str(pathlib.Path(script).parent))
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
        sys.excepthook(type(e), e, _without_harness_frames(e))
        rc = 1
    finally:
        sys.settrace(None)
        pathlib.Path(plan["log"]).write_text(json.dumps(_LOG, indent=1, default=str) + "\n")
    return rc


# ---------------------------------------------------------------------------
# the parent half: run the child, read the log back
# ---------------------------------------------------------------------------
NOT_REACHED = "NOT-REACHED"


def run_child(run: dict, workdir: pathlib.Path, wrap: list[str], sites: list[str] | None = None,
              extra_plan: dict | None = None) -> dict:
    """One traced execution of `run["entry"]`. Never raises on the child's
    behalf: a timeout, or a child that wrote no log, is recorded as
    `UNRESOLVED`, not as a verdict. `sites` None keeps every intercepted
    call (a run several sites share is recorded once and viewed per site)."""
    entry = str(pathlib.Path(run["entry"]).resolve())
    root = pathlib.Path(run.get("root", ROOT)).resolve()
    workdir.mkdir(parents=True, exist_ok=True)
    stamp = time.time_ns()
    log, plan_path = workdir / f"log-{stamp}.json", workdir / f"plan-{stamp}.json"
    plan = {"log": str(log), "wrap": {w.split(".")[0]: [] for w in wrap},
            "path": [str(p) for p in run.get("path", ())],
            "prelude": run.get("prelude"), "inject": list(run.get("inject", ())),
            "root": str(root)}
    for w in wrap:
        mod, _, fn = w.partition(".")
        plan["wrap"][mod].append(fn)
    plan.update(extra_plan or {})
    plan_path.write_text(json.dumps(plan))
    env = dict(os.environ)
    env.update({k: str(v) for k, v in run.get("env", {}).items()})
    env["PYTHONUNBUFFERED"] = "1"
    cmd = [sys.executable, str(HERE), "follow", str(plan_path), entry, *[str(a) for a in run["argv"]]]

    def rel(q) -> str:
        q = pathlib.Path(q)
        return str(q.relative_to(root)) if q.is_relative_to(root) else str(q)

    rec = {"entry": rel(entry), "argv": [str(a) for a in run["argv"]], "role": run.get("role"),
           "timeout_s": run.get("timeout", 900), "tree": str(root),
           "expect": run.get("expect"), "note": run.get("note")}
    started = time.time()
    proc = subprocess.Popen(cmd, cwd=str(run.get("cwd", root)), env=env, text=True,
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
    rec["harness_frame_in_traceback"] = any(str(HERE) in line for line in rec["stderr_tail"])
    try:
        child = json.loads(log.read_text()) if log.exists() else None
    except ValueError:
        child = None
    if child is None:
        rec.setdefault("verdict", UNRESOLVED)
        rec.setdefault("unresolved", "the child wrote no log (it exited before the entry point ran)")
        rec["calls"], rec["follows"], rec["site_executed"] = [], [], []
        return rec
    rec["wrap_missing"] = child["wrap_missing"]
    rec["system_exit"] = child["system_exit"]
    rec["uncaught"] = child["uncaught"]
    rec["calls"] = child["calls"] if sites is None else [c for c in child["calls"] if _hit(c, sites)]
    rec["follows"] = child["follows"] if sites is None else [f for f in child["follows"] if _hit_stack(f, sites)]
    rec["all_raised"] = sorted({c["raised"]["class"] for c in child["calls"] if c.get("raised")})
    rec["site_executed"] = sorted({f"{c['caller']['file']}:{c['caller']['line']}" for c in rec["calls"]})
    rec.setdefault("verdict", classify(rec))
    return rec


def _hit(call: dict, sites: list[str]) -> bool:
    """Does this call sit at one of `sites`? On its own line, or inside the
    source span of its call expression when the call is written over several
    lines (the scanner names the first)."""
    c = call["caller"]
    for s in sites:
        path, _, line = s.rpartition(":")
        if path == c["file"] and c["first"] <= int(line) <= c["last"]:
            return True
    return False


def _hit_stack(followed: dict, sites: list[str]) -> bool:
    """A refusal is a site's when the call that let it out was made there."""
    return bool(followed["stack"]) and _hit({"caller": followed["stack"][0]}, sites)


def _mine(call: dict, site: str, callee: str) -> bool:
    return call["callee"] == callee and _hit(call, [site])


def classify(rec: dict) -> str:
    """ESCAPED, HANDLED, RETURNED, NOT-REACHED or UNRESOLVED for one run, from
    what the child observed -- never from what was expected. A refusal raised
    at the site that was not followed is UNRESOLVED: "returned" would
    contradict the record's own `calls`."""
    if rec.get("unresolved"):
        return UNRESOLVED
    calls = rec.get("calls", ())
    follows = rec.get("follows", [])
    if not calls:
        return NOT_REACHED
    if not follows:
        if any(c.get("raised") for c in calls):
            return UNRESOLVED
        return RETURNED
    last = follows[-1]
    if rec.get("uncaught") and rec["uncaught"].get("followed"):
        return ESCAPED
    if last["handlers"] and not last["left_entry"]:
        return HANDLED
    if rec.get("system_exit") is not None and not last["left_entry"]:
        return HANDLED
    return UNRESOLVED


def view(rec: dict, site: str, callee: str) -> dict:
    """One site's view of a run: only that callee's calls made at `site` and
    the refusals they let out (two calls share a line at
    `fpga/verify_live_midi.py:107`), the process-level facts unchanged."""
    out = {k: v for k, v in rec.items() if k not in ("calls", "follows", "site_executed", "verdict")}
    if rec.get("unresolved"):
        return {**out, "verdict": UNRESOLVED, "calls": [], "follows": [], "site_executed": []}
    out["calls"] = [c for c in rec["calls"] if _mine(c, site, callee)]
    out["follows"] = [f for f in rec["follows"] if _mine({"callee": f.get("callee"), "caller": f["stack"][0]},
                                                       site, callee)]
    out["site_executed"] = [site] if out["calls"] else []
    cap_calls(out)
    if out["follows"]:
        out["verdict"] = classify({**rec, "follows": out["follows"]})
    elif out["calls"]:
        out["verdict"] = RETURNED            # the call ran and came back
    else:
        out["verdict"] = NOT_REACHED         # this run never got to the site
    return out


#: how many of a run's calls at one site a record keeps; a loop that calls the
#: callee 64 times is one fact, `calls_total`, not 64 briefs
KEPT_CALLS = 12


def cap_calls(rec: dict) -> None:
    """Keep the first KEPT_CALLS calls a site's view lists; `calls_total` keeps
    the count. In place, so a record merged from an earlier run can be trimmed
    to the same shape."""
    calls = rec.get("calls") or []
    if "calls_total" not in rec:
        rec["calls_total"] = len(calls)
    rec["calls"] = calls[:KEPT_CALLS]


def trace_all(traces: list[dict], workroot: pathlib.Path, roles: tuple[str, ...],
              cache: dict | None = None, wrap: list[str] | None = None) -> list[dict]:
    """Run every trace. A shared run -- `share="name"` in the dict a callable
    returns, or `spec["share"][role]` -- is executed once and viewed by every
    site that names it: rows may share a fixture when the record shows each
    site's own call executed. `cache` carries shared runs across calls."""
    cache = cache if cache is not None else {}
    # every callee ANY trace names: a shared run is made once, and the site
    # that views it second must find its own callee's calls in the record
    all_wrap = wrap or sorted({w for t in traces for w in t["wrap"]})
    # a shared name -> the run it was first used for, for the whole session: a
    # name that later stands for a different run is a spec error, and reusing
    # the first run's record under it would be a silent wrong answer
    owners: dict = cache.setdefault("_owners", {})
    out = []
    for spec in traces:
        sites = spec.get("sites") or [spec["site"]]
        t = {"id": spec["id"], "site": spec["site"], "callee": spec["callee"],
             "classification": spec.get("classification"), "runs": {}}
        for role in roles:
            make = spec.get(role)
            if make is None:
                t["runs"][role] = None
                continue
            makers = make if isinstance(make, (list, tuple)) else [make]
            base = workroot / _slug(spec.get("share", {}).get(role) or spec["id"])
            recs = []
            for i, mk in enumerate(makers):
                ws = base if len(makers) == 1 else base / str(i)
                ws.mkdir(parents=True, exist_ok=True)
                made = mk(ws)
                for j, run in enumerate(made if isinstance(made, list) else [made]):
                    name = (run.get("share") or spec.get("share", {}).get(role)) if isinstance(run, dict) else None
                    if name and not run.get("unresolved"):
                        what = (run["entry"], tuple(run["argv"]), run.get("root"))
                        if owners.setdefault(name, what) != what:
                            run = {"unresolved": f"shared run {name!r} names two different runs: "
                                                 f"{owners[name]} and {what}"}
                    recs.append(_one(run, spec, sites, role, all_wrap, ws / f"child{j}", cache, owners))
            t["runs"][role] = recs
        for k in ("invariant", "shadowed_by"):
            if spec.get(k):
                t[k] = spec[k]
        out.append(t)
    return out


def _one(run: dict, spec: dict, sites: list[str], role: str, wrap: list[str],
         child_dir: pathlib.Path, cache: dict, owners: dict) -> dict:
    if run.get("unresolved"):
        return {"role": role, "verdict": UNRESOLVED, "unresolved": run["unresolved"],
                "expect": run.get("expect"), "note": run.get("note")}
    share = run.get("share") or spec.get("share", {}).get(role)
    if share and share in cache:
        what = (run["entry"], tuple(run["argv"]), run.get("root"))
        if owners.setdefault(share, what) != what:
            return {"role": role, "verdict": UNRESOLVED,
                    "unresolved": f"shared run {share!r} names two different runs: "
                                  f"{owners[share][0]} and {what[0]}"}
        rec = cache[share]
    else:
        run["role"] = role
        rec = run_child(run, child_dir, wrap, None if share else sites)
        if share:
            owners[share] = (run["entry"], tuple(run["argv"]), run.get("root"))
            cache[share] = rec
    rec = view(rec, spec["site"], spec["callee"])
    rec["role"], rec["share"] = role, share
    return rec


def _slug(text: str) -> str:
    return "".join(ch if ch.isalnum() or ch in "-." else "_" for ch in text)


def load_spec(path: pathlib.Path):
    """Import a spec module by file (its name is not importable elsewhere)."""
    spec = importlib.util.spec_from_file_location(path.stem, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[path.stem] = mod
    spec.loader.exec_module(mod)
    return mod


def report(record: dict) -> str:
    lines = [f"# refusal_candidate_trace @ {record['revision']}"
             f" ({'dirty' if record['tree_dirty'] else 'clean'} tree),"
             f" scanner {record['scanner']['rev']} roots={','.join(record['scanner']['roots'])}"]
    for t in record["traces"]:
        runs = t["runs"]
        cells = []
        for role, recs in runs.items():
            for rec in recs or []:
                if role == "working":
                    cells.append(f"exit={rec.get('exit_status')} site={'hit' if rec.get('site_executed') else 'NOT-REACHED'}")
                elif role == "refusing":
                    cells.append(f"{rec.get('verdict')} exit={rec.get('exit_status')} "
                                 f"tb={int(bool(rec.get('traceback')))}")
                else:
                    cells.append(rec.get("verdict", "?"))
        lines.append(f"{t['id']:50s} {t['callee']:34s} " + " | ".join(cells))
    return "\n".join(lines)


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == "follow":
        # not argparse: everything after the script belongs to the ENTRY
        # POINT, its own options included
        if len(argv) < 3:
            print("usage: refusal_candidate_trace.py follow PLAN.json SCRIPT.py [args...]", file=sys.stderr)
            return 2
        return follow(argv[1], argv[2], argv[3:])
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--spec", type=pathlib.Path, required=True)
    r.add_argument("--out", type=pathlib.Path, required=True)
    r.add_argument("--only", action="append", default=[], help="trace id (repeatable)")
    r.add_argument("--roles", default="working,refusing,boundary")
    r.add_argument("--keep", type=pathlib.Path, default=None, help="scratch dir to reuse (kept)")
    r.add_argument("--into", type=pathlib.Path, default=None,
                   help="an existing record: replace only the traces named by --only")
    p = sub.add_parser("report")
    p.add_argument("record", type=pathlib.Path)
    a = ap.parse_args(argv)
    if a.cmd == "report":
        print(report(json.loads(a.record.read_text())))
        return 0
    module = load_spec(a.spec)
    traces = module.TRACES
    if a.only:
        traces = [t for t in traces if t["id"] in set(a.only)]
    sys.path.insert(0, str(ROOT / "tools"))
    import refusal_sibling_scan as rss
    out = {"revision": _git("rev-parse", "HEAD"), "tree_dirty": bool(_git("status", "--porcelain").strip()),
           "dirty_paths": [ln[3:] for ln in _git("status", "--porcelain").splitlines()],
           "scanner": _scan_json(rss), "traces": []}
    if a.into and a.into.exists():
        prior = json.loads(a.into.read_text())
        keep = [t for t in prior["traces"] if t["id"] not in {s["id"] for s in traces}]
        out = {**prior, "traces": keep}
    roles = tuple(r_ for r_ in a.roles.split(",") if r_)
    workroot = a.keep or pathlib.Path(tempfile.mkdtemp(prefix="refusal-candidate-trace-"))
    workroot.mkdir(parents=True, exist_ok=True)
    if hasattr(module, "set_scratch"):
        module.set_scratch(workroot)
    cache: dict = {}
    all_wrap = sorted({w for t in module.TRACES for w in t["wrap"]})
    for spec in traces:
        print(f"== {spec['id']}", flush=True)
        for t in trace_all([spec], workroot, roles, cache, all_wrap):
            out["traces"].append(t)
            for role, recs in t["runs"].items():
                for rec in recs or []:
                    print(f"   {role:9s} {rec.get('verdict')} exit={rec.get('exit_status')} "
                          f"{rec.get('unresolved', '')}", flush=True)
    out["traces"].sort(key=lambda t: t["id"])
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text(json.dumps(out, indent=1, default=str) + "\n")
    print(f"wrote {a.out} (scratch kept in {workroot})")
    return 0


def _git(*args: str) -> str:
    return subprocess.run(["git", "-C", str(ROOT), *args], capture_output=True, text=True).stdout


def _scan_json(rss) -> dict:
    """The inventory the traces are read against, saved with them: revision,
    roots, and every candidate row (the test plan's first item)."""
    files = rss.tracked_files()
    scope = [f for f in files if any(str(f.relative_to(rss.ROOT)).startswith(r_ + "/") for r_ in rss.SCAN_ROOTS)]
    sites = rss.scan(scope, rss.build_index(files))
    behind = subprocess.run(["git", "-C", str(ROOT), "rev-list", "--count", "HEAD..origin/main"],
                            capture_output=True, text=True).stdout.strip() or "?"
    return dict(rev=_git("rev-parse", "--short", "HEAD"), behind_origin_main=behind,
                roots=list(rss.SCAN_ROOTS), files_scanned=len(scope),
                candidates=[s for s in sites if s["classification"] == "candidate-escape"],
                classes={c: sum(1 for s in sites if s["classification"] == c)
                         for c in sorted({s["classification"] for s in sites})})


if __name__ == "__main__":
    sys.exit(main())
