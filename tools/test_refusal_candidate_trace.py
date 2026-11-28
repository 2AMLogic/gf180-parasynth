#!/usr/bin/env python3
"""Known-answer controls for tools/refusal_candidate_trace.py (#1576).

The tracer answers one question per call site -- when this callee refuses,
where does the refusal go -- and its own start-red run (2026-11-28) got that
answer WRONG three ways before it got it right, each on a shape this file now
pins:

  * every refusal read `NOT-RAISED`: a frame CPython resumes on the `except`
    line, before the body's first statement, and the handler lookup missed it;
  * a clause that was TESTED and did not match read as a handler: the header
    line fires either way (the R2 stale-control calibration, #1569's defect,
    showed `except Refused` at r2_release.py:395 "handling" a sibling it
    lets through);
  * a frame unwinding through a `with` read as a handler with no clause: it
    resumes on the header at PUSH_EXC_INFO to call `__exit__`.

A fourth, found later while tracing #1909 (#1958), the same shape one
construct over:

  * a frame unwinding out of an INLINED comprehension read as a handler with
    no clause: CPython 3.12 compiles a list, set or dict comprehension into
    its enclosing frame (PEP 709), and when the callee raises inside one the
    frame resumes on the comprehension's own line -- inside the `try` body,
    where there is no `except` -- at SWAP, to drop the half-built container,
    before the enclosing clause is reached.

THE KNOWN ANSWERS are in the fixtures' own exit statuses, read by running each
caller directly (table below, CPython 3.12.3) -- a route that shares nothing
with the tracer but the interpreter:

    caller              refusing input          exit
    escape              callee.Refused, uncaught  1 + traceback
    handled             except refusal.Refused    2, `REFUSED: ...`
    intermediate        except callee.Refused, in a helper   0, `result: None`
    finally             try/finally, uncaught    1, after the finally ran
    reraise             caught, re-raised, caught 2
    with_unwind         a re-raising `with`, uncaught   1
    suppressed          contextlib.suppress       0

That third row is worth a test of its own: a refusal caught by an alias in an
intermediate frame is COVERED and the process still exits 0 with a None where
a result belonged -- the tracer must say HANDLED, and must not say REFUSED.

EVERY BENCH CARRIES ITS INJECTED CONTROLS (docs/verification-rules.md rule
2). The tracer's defects are injectable, and each control must turn the
verdict on its fixture WRONG in the named way: `NO_FOLLOW` (blind to where
the refusal goes), `FINALLY_IS_HANDLER`, `WITH_IS_HANDLER`,
`COMPREHENSION_IS_HANDLER`.

The calibration on REAL code is a differential, not a pin: the R2 host
stale-control, PR #1574's control shape, run through the CLI entry under the
tracer and called directly in-process; the tracer's verdict must equal what
the direct call observes. It holds on the broken revision (escapes) and on
the repaired one (exit 2), so it does not go red when PR #1574 merges.
"""
from __future__ import annotations


import json
import pathlib
import shutil
import subprocess
import sys
import types

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "fpga"))
sys.path.insert(0, str(ROOT / "fpga/release"))

import refusal_candidate_trace as rct  # noqa: E402

CALLEE = '''\
import refusal


class Refused(refusal.Refused, Exception):
    pass


def maybe(x):
    if x:
        raise Refused(f"callee refused: {x}")
    return "ok"


def other():
    return "other"
'''

HEAD = '''\
import sys
import refusal
import callee
'''


def _write(tmp_path: pathlib.Path, name: str, body: str) -> pathlib.Path:
    (tmp_path / name).write_text(body)
    return tmp_path / name


def _tail(main_body: str) -> str:
    return f'''


def main(argv=None):
{main_body}
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
'''


def _line_of(path: pathlib.Path, needle: str) -> str:
    return next(i for i, text in enumerate(path.read_text().splitlines(), 1) if needle in text)


def _tree(tmp_path: pathlib.Path) -> dict[str, pathlib.Path]:
    shutil.copy(ROOT / "tools/refusal.py", tmp_path / "refusal.py")
    _write(tmp_path, "callee.py", CALLEE)
    files = {}
    files["escape"] = _write(tmp_path, "escape.py", HEAD + '''


class Refused(refusal.Refused, RuntimeError):
    pass


def work(x):
    return callee.maybe(x)
''' + _tail('''\
    try:
        x = int(argv[0])
    except Refused as exc:
        print(f"REFUSED: {exc}")
        return 2
    print("result:", work(x))
'''))
    files["handled"] = _write(tmp_path, "handled.py", HEAD + '''


def work(x):
    return callee.maybe(x)
''' + _tail('''\
    try:
        print("result:", work(int(argv[0])))
    except refusal.Refused as exc:
        print(f"REFUSED: {exc}")
        return 2
'''))
    files["tested"] = _write(tmp_path, "tested.py", HEAD + '''


class Refused(refusal.Refused, RuntimeError):
    pass


def work(x):
    return callee.maybe(x)
''' + _tail('''\
    try:
        print("result:", work(int(argv[0])))
    except Refused as exc:              # a sibling of what the callee raises
        print(f"REFUSED: {exc}")
        return 2
'''))
    files["intermediate"] = _write(tmp_path, "intermediate.py", HEAD + '''

Refused = callee.Refused          # an exact alias, in the frame that catches


def lib(x):
    try:
        return callee.maybe(x)
    except Refused as exc:
        print("lib saw it")
        return None


def work(x):
    return lib(x)
''' + _tail('''\
    print("result:", work(int(argv[0])))
'''))
    files["finally"] = _write(tmp_path, "finally.py", HEAD + '''


def work(x):
    try:
        return callee.maybe(x)
    finally:
        print("cleanup ran")
''' + _tail('''\
    print("result:", work(int(argv[0])))
'''))
    files["reraise"] = _write(tmp_path, "reraise.py", HEAD + '''


def work(x):
    try:
        return callee.maybe(x)
    except callee.Refused:
        print("inner handler; re-raising")
        raise
''' + _tail('''\
    try:
        print("result:", work(int(argv[0])))
    except refusal.Refused as exc:
        print(f"REFUSED: {exc}")
        return 2
'''))
    files["two_on_a_line"] = _write(tmp_path, "two.py", HEAD + '''


def work(x):
    return callee.maybe(x), callee.other()
''' + _tail('''\
    print("result:", work(int(argv[0])))
'''))
    return files


WITH = '''\
import contextlib
import sys
import refusal
import callee


@contextlib.contextmanager
def through():
    try:
        yield
    finally:
        print("exit ran")


def work(x):
    with through():                  # re-raises: the frame is still unwinding here
        return callee.maybe(x)
'''

THROUGH = HEAD + '''\


class through:                     # a plain context manager: no contextlib frame in the record
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        print("exit ran")
        return False
'''

#: #1958. The same callee under the same clause, called from a plain loop and
#: from a comprehension. CPython 3.12 inlines a list, set or dict comprehension
#: into the caller's own frame (PEP 709); a generator expression keeps a frame
#: of its own and is the contrast.
_COMPREHENSION = "{v: callee.maybe(v) for v in (1, 2)}"
_COMPREHENSION_CALLER = f'''\
    try:
        d = {_COMPREHENSION}
    except refusal.Refused as e:
        print("REFUSED:", e)
        return 2
'''

INLINED = {
    "inlined_loop": HEAD + _tail('''\
    try:
        d = {}
        for v in (1, 2):
            d[v] = callee.maybe(v)
    except refusal.Refused as e:
        print("REFUSED:", e)
        return 2
'''),
    "inlined_dict": HEAD + _tail(_COMPREHENSION_CALLER),
    "inlined_list": HEAD + _tail(_COMPREHENSION_CALLER.replace(
        _COMPREHENSION, "[callee.maybe(v) for v in (1, 2)]")),
    "inlined_set": HEAD + _tail(_COMPREHENSION_CALLER.replace(
        _COMPREHENSION, "{callee.maybe(v) for v in (1, 2)}")),
    "inlined_genexp": HEAD + _tail(_COMPREHENSION_CALLER.replace(
        _COMPREHENSION, "list(callee.maybe(v) for v in (1, 2))")),
    # the test plan's edge cases: a comprehension inside a comprehension (two
    # cleanups on one line), the cleanup before a `with` header's own
    # resumption, and a handler around a comprehension that re-raises
    "inlined_nested": HEAD + _tail('''\
    try:
        d = {k: {v: callee.maybe(v) for v in (1, 2)} for k in (1,)}
    except refusal.Refused as e:
        print("REFUSED:", e)
        return 2
'''),
    "inlined_in_with": THROUGH + _tail('''\
    try:
        with through():
            d = {v: callee.maybe(v) for v in (1, 2)}
    except refusal.Refused as e:
        print("REFUSED:", e)
        return 2
'''),
    "inlined_reraise": HEAD + '''\


def work(x):
    try:
        return {v: callee.maybe(v) for v in (1, 2)}
    except callee.Refused:
        print("inner handler; re-raising")
        raise
''' + _tail('''\
    try:
        print("result:", work(1))
    except refusal.Refused as e:
        print("REFUSED:", e)
        return 2
'''),
    # a clause that does not match, then one that does
    "inlined_tested_then_matched": HEAD + '''\


class Other(Exception):
    pass
''' + _tail('''\
    try:
        d = {v: callee.maybe(v) for v in (1, 2)}
    except Other as e:
        print("other")
        return 2
    except refusal.Refused as e:
        print("REFUSED:", e)
        return 3
'''),
    # only a clause that does not match: the refusal leaves the frame
    "inlined_declined": HEAD + _tail('''\
    try:
        d = {v: callee.maybe(v) for v in (1, 2)}
    except KeyError as e:
        print("REFUSED:", e)
        return 2
'''),
    # the input that would defeat the repair if it read EVERY resumption
    # inside a comprehension as unwinding: a context manager swallows it
    "inlined_swallow": "import contextlib\n" + HEAD + _tail('''\
    with contextlib.suppress(callee.Refused):
        d = {v: callee.maybe(v) for v in (1, 2)}
    print("carried on after the with")
'''),
}


def _fixture(tmp_path: pathlib.Path) -> dict[str, pathlib.Path]:
    files = _tree(tmp_path)
    files["with_unwind"] = _write(tmp_path, "withunwind.py", WITH + _tail('''\
    print("result:", work(int(argv[0])))
'''))
    files["suppressed"] = _write(tmp_path, "suppressed.py", "import contextlib\n" + HEAD + '''


def work(x):
    with contextlib.suppress(callee.Refused):
        callee.maybe(x)
    print("carried on after the with")
    return "suppressed"
''' + _tail('''\
    print("result:", work(int(argv[0])))
'''))
    return files


def _site(path: pathlib.Path, needle: str = "callee.maybe(x)") -> str:
    """A site key the way the child names it: relative to the traced tree."""
    return f"{path.name}:{_line_of(path, needle)}"


def _trace(tmp_path: pathlib.Path, name: str, arg: str, inject=(), callee="callee.maybe"):
    path = _fixture(tmp_path)[name]
    run = {"entry": str(path), "argv": [arg], "root": str(tmp_path), "cwd": str(tmp_path), "timeout": 60}
    rec = rct.run_child(run, tmp_path / f"work-{name}-{arg}", [callee], [_site(path, "callee.maybe(x)" if callee.endswith("maybe") else "callee.other()")],
                        extra_plan={"inject": list(inject)})
    assert not rec.get("wrap_missing"), rec["wrap_missing"]
    return rec


def _direct(tmp_path: pathlib.Path, name: str, arg: str) -> subprocess.CompletedProcess:
    p = subprocess.run([sys.executable, str(_fixture(tmp_path)[name]), arg], capture_output=True, text=True, timeout=60)
    return p


def _needs_inlined_comprehensions() -> None:
    """The shape #1958 names exists only where CPython inlines a list, set or
    dict comprehension into its enclosing frame (PEP 709, 3.12+): on an older
    interpreter the comprehension is a frame of its own and its caller never
    resumes on the cleanup. Refuse there -- a skip that says so, never a pass
    -- on the interpreter's version and on what it actually built."""
    if sys.version_info < (3, 12):
        pytest.skip(f"CPython {sys.version.split()[0]}: comprehensions are not inlined before 3.12 "
                    "(PEP 709); the shape under test does not exist")
    outer = compile("def f(xs):\n    return {x: x for x in xs}\n", "<inlining probe>", "exec")
    [f] = [c for c in outer.co_consts if isinstance(c, types.CodeType)]
    if any(isinstance(c, types.CodeType) for c in f.co_consts):
        pytest.skip(f"CPython {sys.version.split()[0]} keeps the comprehension in a frame of its own; "
                    "the shape under test does not exist")


def _inlined(tmp_path: pathlib.Path) -> dict[str, pathlib.Path]:
    """#1958's callers, written over the shared `refusal.py` and `callee.py`."""
    _fixture(tmp_path)
    return {name: _write(tmp_path, f"{name}.py", source) for name, source in INLINED.items()}


def _trace_inlined(tmp_path: pathlib.Path, name: str, inject=()) -> tuple[dict, pathlib.Path]:
    """Trace `INLINED[name]` at its one call, `callee.maybe(v)`."""
    path = _inlined(tmp_path)[name]
    run = {"entry": str(path), "argv": [], "root": str(tmp_path), "cwd": str(tmp_path), "timeout": 60}
    rec = rct.run_child(run, tmp_path / f"inlined-{name}", ["callee.maybe"], [_site(path, "callee.maybe(v)")],
                        extra_plan={"inject": list(inject)})
    assert not rec.get("wrap_missing"), rec["wrap_missing"]
    return rec, path


def _ran(path: pathlib.Path) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(path)], capture_output=True, text=True, timeout=60, check=False)


# ---------------------------------------------------------------------------
# the tracer's verdict is the process's own behaviour, on every shape
# ---------------------------------------------------------------------------
def test_escape(tmp_path):
    rec = _trace(tmp_path, "escape", "1")
    truth = _direct(tmp_path, "escape", "1")
    assert (truth.returncode, "Traceback" in truth.stderr) == (1, True)
    assert rec["verdict"] == rct.ESCAPED and rec["exit_status"] == 1 and rec["traceback"]
    assert rec["uncaught"]["class"] == "callee.Refused" and rec["uncaught"]["in_family"]
    assert rec["site_executed"] == [_site(_fixture(tmp_path)["escape"])]
    follow = rec["follows"][0]
    assert [s["fn"] for s in follow["stack"]] == ["work", "main", "<module>"]
    assert follow["handlers"] == [] and follow["left_entry"]


def test_boundary_handler_names_its_clause(tmp_path):
    rec = _trace(tmp_path, "handled", "1")
    truth = _direct(tmp_path, "handled", "1")
    assert (truth.returncode, truth.stdout.strip()) == (2, "REFUSED: callee refused: 1")
    assert rec["verdict"] == rct.HANDLED and rec["exit_status"] == 2 and not rec["traceback"]
    assert rec["uncaught"] is None
    [(handler)] = rec["follows"][0]["handlers"]
    assert handler["clause"] == "refusal.Refused" and handler["fn"] == "main"


def test_a_clause_that_does_not_match_is_tested_not_entered(tmp_path):
    """#1569's shape: `except Refused` over a sibling class. The line fires,
    the frame still leaves, and the record must say so in those words."""
    rec = _trace(tmp_path, "tested", "1")
    assert _direct(tmp_path, "tested", "1").returncode == 1
    assert rec["verdict"] == rct.ESCAPED and rec["traceback"]
    follow = rec["follows"][0]
    assert follow["handlers"] == []
    [tested] = follow["tested"]
    assert tested["clause"] == "Refused" and tested["fn"] == "main"
    assert "main" in [s["fn"] for s in follow["propagated_out"]]


def test_intermediate_alias_handler_is_handled_and_the_process_still_exits_0(tmp_path):
    rec = _trace(tmp_path, "intermediate", "1")
    truth = _direct(tmp_path, "intermediate", "1")
    assert (truth.returncode, truth.stdout.strip()) == (0, "lib saw it\nresult: None")
    assert rec["verdict"] == rct.HANDLED and rec["exit_status"] == 0
    [(handler)] = rec["follows"][0]["handlers"]
    assert handler["clause"] == "Refused" and handler["fn"] == "lib"


def test_a_finally_body_is_not_a_handler(tmp_path):
    rec = _trace(tmp_path, "finally", "1")
    truth = _direct(tmp_path, "finally", "1")
    assert truth.returncode == 1 and truth.stdout.strip() == "cleanup ran"
    assert rec["verdict"] == rct.ESCAPED
    follow = rec["follows"][0]
    assert follow["handlers"] == []
    assert [f["line"] for f in follow["finally"]] == [_line_of(_fixture(tmp_path)["finally"], 'print("cleanup ran")')]


def test_a_with_the_exception_unwinds_through_is_not_a_handler(tmp_path):
    rec = _trace(tmp_path, "with_unwind", "1")
    truth = _direct(tmp_path, "with_unwind", "1")
    assert truth.returncode == 1 and truth.stdout.strip() == "exit ran"
    assert rec["verdict"] == rct.ESCAPED
    follow = rec["follows"][0]
    assert [h for h in follow["handlers"] if h["in_tree"]] == []
    # what the exception met on the way out, outside the traced tree:
    # contextlib's own `except BaseException`, which re-raises
    assert [(h["file"].rsplit("/", 1)[-1], h["clause"]) for h in follow["handlers"]] == [
        ("contextlib.py", "BaseException")]
    assert not any(h["in_tree"] for h in follow["handlers"])
    assert [w["line"] for w in follow["with_unwind"]] == [_line_of(_fixture(tmp_path)["with_unwind"], "with through():")]


def test_a_context_manager_that_swallows_it_is_recorded_without_a_clause(tmp_path):
    rec = _trace(tmp_path, "suppressed", "1")
    truth = _direct(tmp_path, "suppressed", "1")
    assert (truth.returncode, "carried on after the with" in truth.stdout) == (0, True)
    assert rec["verdict"] == rct.HANDLED and rec["exit_status"] == 0
    [(handler)] = rec["follows"][0]["handlers"]
    assert handler["clause"] is None and handler["fn"] == "work"


def test_catch_and_reraise_is_two_handlers_in_order(tmp_path):
    rec = _trace(tmp_path, "reraise", "1")
    assert _direct(tmp_path, "reraise", "1").returncode == 2
    assert rec["verdict"] == rct.HANDLED and rec["exit_status"] == 2
    assert [(h["fn"], h["clause"]) for h in rec["follows"][0]["handlers"]] == [
        ("work", "callee.Refused"), ("main", "refusal.Refused")]


def test_a_working_input_reports_the_call_returned(tmp_path):
    rec = _trace(tmp_path, "escape", "0")
    assert _direct(tmp_path, "escape", "0").returncode == 0
    assert rec["verdict"] == rct.RETURNED and rec["exit_status"] == 0
    [(call)] = rec["calls"]
    assert call["outcome"] == "returned" and call["returned"] == "ok" and call["args"] == [0]


def test_the_child_prints_the_traceback_the_real_cli_prints(tmp_path):
    """No frame of the tracer's in it: the harness is not the thing tested."""
    rec = _trace(tmp_path, "escape", "1")
    assert not rec["harness_frame_in_traceback"]
    assert rec["stderr_tail"][-1] == "callee.Refused: callee refused: 1"


# ---------------------------------------------------------------------------
# an inlined comprehension (PEP 709) is not a handler: #1958's pair, run
# ---------------------------------------------------------------------------
def _clauses(rec: dict) -> list[tuple[str, object]]:
    return [(h["fn"], h["clause"]) for h in rec["follows"][0]["handlers"]]


def _names_its_own_clause(rec: dict, path: pathlib.Path, left: tuple[str, ...] = ()) -> None:
    """One handler -- `main`, the clause as written, on its own `except` line --
    nothing tested, and no frame left but those named in `left`. A function,
    not an inline assert, so the control below turns THIS red instead of
    restating it."""
    follow = rec["follows"][0]
    assert len(follow["handlers"]) == 1, follow["handlers"]
    handler = follow["handlers"][0]
    header = _line_of(path, "except refusal.Refused as e:")
    assert (handler["fn"], handler["clause"]) == ("main", "refusal.Refused"), handler
    assert (handler["line"], handler["handler"]) == (header, header), handler
    assert follow["tested"] == [], follow["tested"]
    assert [s["fn"] for s in follow["propagated_out"]] == list(left), follow["propagated_out"]
    assert not follow["left_entry"]


@pytest.mark.parametrize("name", ["inlined_dict", "inlined_list", "inlined_set"])
def test_a_call_inside_an_inlined_comprehension_names_the_clause_the_loop_names(tmp_path, name):
    """#1958's pair, run: the loop caller and a comprehension caller, same
    callee, same `except refusal.Refused as e: return 2`. Before the repair the
    comprehension caller reported `('main', <the comprehension's line>, None,
    None)` -- a handler with no clause inside the `try` body, where there is no
    `except` -- and nothing for the line that caught it."""
    _needs_inlined_comprehensions()
    loop, loop_path = _trace_inlined(tmp_path, "inlined_loop")
    comp, comp_path = _trace_inlined(tmp_path, name)
    for rec, path in ((loop, loop_path), (comp, comp_path)):
        truth = _ran(path)
        assert (truth.returncode, truth.stdout.strip()) == (2, "REFUSED: callee refused: 1")
        assert (rec["verdict"], rec["exit_status"], rec["traceback"]) == (rct.HANDLED, 2, False)
        _names_its_own_clause(rec, path)
    assert _clauses(comp) == _clauses(loop)


def test_a_generator_expression_keeps_its_own_frame_and_names_the_clause_too(tmp_path):
    """PEP 709 leaves a generator expression alone: the refusal leaves the
    `<genexpr>` frame, which the record lists under `propagated_out`, and
    `main` names its clause as the loop caller does. Nothing here needed the
    repair; the row pins the docstring's note."""
    _needs_inlined_comprehensions()
    rec, path = _trace_inlined(tmp_path, "inlined_genexp")
    truth = _ran(path)
    assert (truth.returncode, truth.stdout.strip()) == (2, "REFUSED: callee refused: 1")
    assert (rec["verdict"], rec["exit_status"], rec["traceback"]) == (rct.HANDLED, 2, False)
    _names_its_own_clause(rec, path, left=("<genexpr>",))
    assert [s["fn"] for s in rec["follows"][0]["stack"]] == ["<genexpr>", "main", "<module>"]


@pytest.mark.parametrize("name, handlers, with_header", [
    pytest.param("inlined_nested", [("main", "refusal.Refused")], None, id="nested"),
    pytest.param("inlined_in_with", [("main", "refusal.Refused")], "with through():", id="in-a-with"),
    pytest.param("inlined_reraise", [("work", "callee.Refused"), ("main", "refusal.Refused")], None,
                 id="reraise"),
])
def test_an_inlined_comprehension_cleanup_leaves_the_rest_of_the_unwind_alone(tmp_path, name, handlers,
                                                                              with_header):
    """The test plan's edge cases, run: the cleanup must neither be recorded
    nor stop the frame being watched, whatever it is nested in or followed by."""
    _needs_inlined_comprehensions()
    rec, path = _trace_inlined(tmp_path, name)
    truth = _ran(path)
    assert truth.returncode == 2 and truth.stdout.strip().splitlines()[-1] == "REFUSED: callee refused: 1"
    assert (rec["verdict"], rec["exit_status"], rec["traceback"]) == (rct.HANDLED, 2, False)
    follow = rec["follows"][0]
    assert [(h["fn"], h["clause"]) for h in follow["handlers"] if h["in_tree"]] == handlers
    assert all(h["clause"] is not None for h in follow["handlers"])
    assert [w["line"] for w in follow["with_unwind"]] == ([_line_of(path, with_header)] if with_header else [])


def test_a_clause_declined_under_a_comprehension_is_tested_and_the_matching_one_entered(tmp_path):
    """A declined clause is still seen once the frame is not dropped at the
    comprehension's line: in `tested`, by clause and header line."""
    _needs_inlined_comprehensions()
    rec, path = _trace_inlined(tmp_path, "inlined_tested_then_matched")
    truth = _ran(path)
    assert (truth.returncode, truth.stdout.strip()) == (3, "REFUSED: callee refused: 1")
    assert (rec["verdict"], rec["exit_status"], rec["traceback"]) == (rct.HANDLED, 3, False)
    follow = rec["follows"][0]
    [tested] = follow["tested"]
    assert (tested["fn"], tested["clause"], tested["line"]) == (
        "main", "Other", _line_of(path, "except Other as e:"))
    assert _clauses(rec) == [("main", "refusal.Refused")]
    assert (follow["handlers"][0]["line"], follow["handlers"][0]["handler"]) == (
        _line_of(path, "except refusal.Refused as e:"),) * 2
    assert follow["propagated_out"] == [] and not follow["left_entry"]


def test_an_unmatched_clause_under_a_comprehension_still_reads_escaped(tmp_path):
    """Nothing matches: the frame leaves, and the record says so with no
    handler at all."""
    _needs_inlined_comprehensions()
    rec, path = _trace_inlined(tmp_path, "inlined_declined")
    truth = _ran(path)
    assert truth.returncode == 1 and "Traceback" in truth.stderr
    assert (rec["verdict"], rec["exit_status"], rec["traceback"]) == (rct.ESCAPED, 1, True)
    follow = rec["follows"][0]
    assert follow["handlers"] == []
    [tested] = follow["tested"]
    assert (tested["fn"], tested["clause"], tested["line"]) == (
        "main", "KeyError", _line_of(path, "except KeyError as e:"))
    assert follow["left_entry"] and "main" in [s["fn"] for s in follow["propagated_out"]]


def test_a_swallow_around_a_comprehension_is_recorded_where_the_frame_carried_on(tmp_path):
    """The repair reads a resumption inside a comprehension as unwinding. The
    input that would defeat it: a context manager that swallows the refusal,
    so the frame does carry on. It must still read as one handler with no
    clause, on the statement the frame carried on at -- which is never the
    comprehension's line, because the statement after a `with` starts a line
    of its own."""
    _needs_inlined_comprehensions()
    rec, path = _trace_inlined(tmp_path, "inlined_swallow")
    truth = _ran(path)
    assert (truth.returncode, "carried on after the with" in truth.stdout) == (0, True)
    assert (rec["verdict"], rec["exit_status"], rec["traceback"]) == (rct.HANDLED, 0, False)
    follow = rec["follows"][0]
    [handler] = follow["handlers"]
    assert (handler["fn"], handler["clause"]) == ("main", None)
    assert handler["line"] == _line_of(path, 'print("carried on after the with")')
    assert [w["line"] for w in follow["with_unwind"]] == [_line_of(path, "with contextlib.suppress(")]


# ---------------------------------------------------------------------------
# the view: two calls on one line, and a run the site was never reached in
# ---------------------------------------------------------------------------
def test_two_calls_on_one_line_are_viewed_by_callee(tmp_path):
    path = _fixture(tmp_path)["two_on_a_line"]
    site = f"{path.name}:{_line_of(path, 'callee.maybe(x), callee.other()')}"

    def traced(arg):
        return rct.run_child({"entry": str(path), "argv": [arg], "root": str(tmp_path), "cwd": str(tmp_path),
                              "timeout": 60}, tmp_path / f"two-{arg}", ["callee.maybe", "callee.other"], None)

    rec = traced("0")
    assert len(rec["calls"]) == 2 and rec["verdict"] == rct.RETURNED
    assert rct.view(rec, site, "callee.maybe")["verdict"] == rct.RETURNED
    assert rct.view(rec, site, "callee.other")["verdict"] == rct.RETURNED
    rec = traced("1")
    assert len(rec["calls"]) == 1                 # maybe() raised: other() never ran
    assert rct.view(rec, site, "callee.maybe")["verdict"] == rct.ESCAPED
    assert rct.view(rec, site, "callee.other")["verdict"] == rct.NOT_REACHED


def test_a_shared_name_cannot_stand_for_two_runs(tmp_path):
    path = _fixture(tmp_path)["escape"]
    spec = {"id": "a", "site": _site(path), "callee": "callee.maybe", "wrap": ["callee.maybe"]}
    makers = [lambda ws, arg=arg: {"entry": str(path), "argv": [arg], "root": str(tmp_path), "cwd": str(tmp_path),
                                   "timeout": 60, "share": "same-name"} for arg in ("0", "1")]
    one = dict(spec, id="a", working=makers[0])
    two = dict(spec, id="b", working=makers[1])
    a, b = rct.trace_all([one, two], tmp_path / "shared", ("working",))
    assert a["runs"]["working"][0]["verdict"] == rct.RETURNED        # made once
    assert b["runs"]["working"][0]["verdict"] == rct.UNRESOLVED      # and not served to another run
    assert "two different runs" in b["runs"]["working"][0]["unresolved"]


# ---------------------------------------------------------------------------
# injected controls: each tracer defect turns its fixture's verdict wrong
# ---------------------------------------------------------------------------
def test_control_no_follow_cannot_read_as_returned(tmp_path):
    rec = _trace(tmp_path, "escape", "1", inject=["NO_FOLLOW"])
    assert rec["verdict"] == rct.UNRESOLVED and rec["uncaught"]["class"] == "callee.Refused"
    assert rec["follows"] == [] and rec["calls"][0]["outcome"] == "raised"


def test_control_finally_read_as_a_handler(tmp_path):
    rec = _trace(tmp_path, "finally", "1", inject=["FINALLY_IS_HANDLER"])
    assert rec["verdict"] == rct.ESCAPED             # the exit status still tells the truth
    assert len(rec["follows"][0]["handlers"]) == 1   # but the handler list now lies


def test_control_with_read_as_a_handler(tmp_path):
    rec = _trace(tmp_path, "with_unwind", "1", inject=["WITH_IS_HANDLER"])
    in_tree = [h for h in rec["follows"][0]["handlers"] if h["in_tree"]]
    assert len(in_tree) == 1 and in_tree[0]["clause"] is None        # the `with` header, called a handler


def test_control_inlined_comprehension_cleanup_read_as_a_handler(tmp_path):
    """`COMPREHENSION_IS_HANDLER` reinstates #1958: the cleanup resumption is
    recorded as a handler with no clause on the comprehension's line, and the
    frame stops being watched, so the clause that took the exception is never
    recorded. The exit status still tells the truth."""
    _needs_inlined_comprehensions()
    rec, path = _trace_inlined(tmp_path, "inlined_dict", inject=["COMPREHENSION_IS_HANDLER"])
    follow = rec["follows"][0]
    assert len(follow["handlers"]) == 1, follow["handlers"]
    handler = follow["handlers"][0]
    assert (handler["fn"], handler["line"], handler["clause"]) == (
        "main", _line_of(path, "d = {v: callee.maybe(v)"), None)     # the comprehension's line, called a handler
    assert rec["verdict"] == rct.HANDLED and rec["exit_status"] == 2
    with pytest.raises(AssertionError):
        _names_its_own_clause(rec, path)          # the paired test's check, red again


# ---------------------------------------------------------------------------
# calibration on real code: the tracer agrees with a direct call, whichever
# way the revision behaves
# ---------------------------------------------------------------------------
REFUSE_MAIN = ("import sys\nimport uart_host as uh\n"
               "def _refused(argv):\n"
               "    print('uart_host: unsupported command', file=sys.stderr)\n"
               "    return 1\n"
               "uh.main = _refused\n")


def _r2_site() -> str:
    """stale_control's call, the second `rm._capture(` in the file."""
    path = ROOT / "fpga/release/r2_release.py"
    lines = [i for i, text in enumerate(path.read_text().splitlines(), 1) if "cap = rm._capture(" in text]
    return f"fpga/release/r2_release.py:{lines[1]}"


def test_r2_stale_control_tracer_agrees_with_a_direct_call(tmp_path, monkeypatch, capsys):
    pytest.importorskip("numpy")
    import r2_release as r2r
    import uart_host as uh
    import refusal

    def refused(argv):
        print("uart_host: unsupported command", file=sys.stderr)
        return 1
    monkeypatch.setattr(uh, "main", refused)
    try:
        rc = r2r.stale_control("host", tmp_path / "direct")
        direct = ("handled", rc)
    except refusal.Refused as exc:
        direct = ("escaped", f"{type(exc).__module__}.{type(exc).__qualname__}")
    capsys.readouterr()
    monkeypatch.undo()

    run = {"entry": str(ROOT / "fpga/release/r2_release.py"),
           "argv": ["stale-control", "host", "--out", str(tmp_path / "traced")], "root": str(ROOT),
           "cwd": str(ROOT), "timeout": 300, "prelude": REFUSE_MAIN,
           "path": [str(ROOT / p) for p in ("fpga", "fpga/release", "model", "tools", "audition", "rtl-sketch")]}
    rec = rct.run_child(run, tmp_path / "work", ["release_manifest._capture"], [_r2_site()])
    assert rec["site_executed"] == [_r2_site()] and rec["wrap_missing"] == []
    [(call)] = rec["calls"]
    assert call["raised"]["class"] == "release_manifest.Refused" and call["raised"]["in_family"]
    assert "uart_host: unsupported command" in call["raised"]["message"]
    if direct[0] == "escaped":
        assert rec["verdict"] == rct.ESCAPED and rec["exit_status"] == 1 and rec["traceback"]
        assert rec["uncaught"]["class"] == direct[1]
    else:
        assert rec["verdict"] == rct.HANDLED and rec["exit_status"] == direct[1] and not rec["traceback"]
        assert rec["follows"][0]["handlers"]
    assert "uart_host: unsupported command" in " ".join(rec["stdout_tail"] + rec["stderr_tail"])


# ---------------------------------------------------------------------------
# the committed evidence is internally consistent
# ---------------------------------------------------------------------------
def _record() -> dict:
    return json.loads((ROOT / "docs/verification/refusal-candidate-trace.json").read_text())


def _disposition(t: dict) -> str:
    refusing = [rec["verdict"] for rec in t["runs"].get("refusing") or []]
    if rct.ESCAPED in refusing:
        return "escaped"
    if rct.HANDLED in refusing:
        return "handled"
    if refusing:
        return "shadowed"
    return "no refusing input"


def test_the_committed_record_names_what_it_traced():
    """The record's headline is counted off its own rows: 37 distinct rows,
    each in the census it carries, every working run reaching its site."""
    record = _record()
    rows = {(c["site"], c["callee"]) for c in record["scanner"]["candidates"]}
    seen, kinds = set(), {}
    for t in record["traces"]:
        assert (t["site"], t["callee"]) in rows, t["id"]
        assert t["id"] not in seen
        seen.add(t["id"])
        working = t["runs"]["working"]
        assert working and all(rec["verdict"] == rct.RETURNED and rec["site_executed"] for rec in working), t["id"]
        for role, recs in t["runs"].items():
            for rec in recs or []:
                assert rec["verdict"] in (rct.ESCAPED, rct.HANDLED, rct.RETURNED, rct.NOT_REACHED, rct.UNRESOLVED)
        kinds[_disposition(t)] = kinds.get(_disposition(t), 0) + 1
    assert len(seen) == 37
    assert kinds == {"no refusing input": 18, "escaped": 12, "shadowed": 4, "handled": 3}
    for t in record["traces"]:                      # an escaped row says how
        if _disposition(t) == "escaped":
            escaped = [rec for rec in t["runs"]["refusing"] if rec["verdict"] == rct.ESCAPED]
            assert all(rec["traceback"] and rec["exit_status"] == 1 and rec["uncaught"]["followed"] for rec in escaped)
            assert all(rec["follows"][-1]["left_entry"] for rec in escaped)


def test_three_working_runs_exit_nonzero_by_design_and_reach_their_site():
    record = _record()
    for id_, status in (("tools/probes/audio_distance_floor.py:705", 2),
                        ("fpga/release/r2_release.py:372", 1),
                        ("tools/verify_m5a_filter2x_i2s.py:50", 2)):
        [t] = [t for t in record["traces"] if t["id"] == id_]
        rec = t["runs"]["working"][0]
        assert rec["verdict"] == rct.RETURNED and rec["site_executed"] == [t["site"]], id_
        assert rec["exit_status"] == status and not rec["traceback"], (id_, rec["exit_status"])
