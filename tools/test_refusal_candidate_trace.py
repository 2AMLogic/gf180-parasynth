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
the refusal goes), `FINALLY_IS_HANDLER`, `WITH_IS_HANDLER`.

The calibration on REAL code is a differential, not a pin: the R2 host
stale-control, PR #1574's control shape, run through the CLI entry under the
tracer and called directly in-process; the tracer's verdict must equal what
the direct call observes. It holds on the broken revision (escapes) and on
the repaired one (exit 2), so it does not go red when PR #1574 merges.

THE SITE ANCHOR (#1772). A site was a `path:line` and nothing checked that
the line still held the call. Measured by the resolver this change adds, at
`origin/main` `02394528`: 12 of the plan's 37 pins named a line that was not
the call (five of them in the issue's hand-written list), and every record
would have read as a correct one -- a site whose call had moved reads
NOT-REACHED, exactly like a site the run never reached. A spec now names the
call -- enclosing function, callee, and the statement's target or the call's
first argument -- and `resolve_sites` REFUSES a site it cannot resolve before
any child starts. The controls start red against a line-only spec; they carry
the input that defeats a text anchor (rule 8) and the one that defeats this
guard (a same-named call on another object). The resolver was wrong twice
before it was right, both times caught here rather than by reading: a target
parses in Store context and its text in Load, so every `target` match failed
(the gate on the real plan, 30 of 37 REFUSED); and an `n` was checked only
when two calls matched, so with one left `n=2` resolved (the ordinal control).
"""
from __future__ import annotations


import ast
import json
import pathlib
import shutil
import subprocess
import sys

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
    spec = {"id": "a", "site": _site(path), "callee": "callee.maybe", "in": "work", "arg": "x",
            "wrap": ["callee.maybe"]}
    makers = [lambda ws, arg=arg: {"entry": str(path), "argv": [arg], "root": str(tmp_path), "cwd": str(tmp_path),
                                   "timeout": 60, "share": "same-name"} for arg in ("0", "1")]
    one = dict(spec, id="a", working=makers[0])
    two = dict(spec, id="b", working=makers[1])
    a, b = rct.trace_all([one, two], tmp_path / "shared", ("working",), root=tmp_path)
    assert a["runs"]["working"][0]["verdict"] == rct.RETURNED        # made once
    assert b["runs"]["working"][0]["verdict"] == rct.UNRESOLVED      # and not served to another run
    assert "two different runs" in b["runs"]["working"][0]["unresolved"]


# ---------------------------------------------------------------------------
# the site anchor (#1772): a line says where a call was, not which call it is
# ---------------------------------------------------------------------------
ADF = "tools/probes/audio_distance_floor.py"
BASELINE = "a = norm(rc.prepare(a, sr))"
COMPARISON = "b = norm(rc.prepare(b, s2))"

ANCHORED = '''\
import callee


def norm(x):
    return x


def e4b(a, b, sr, s2):
    a = norm(callee.maybe(a, sr))
    b = norm(callee.maybe(b, s2))
    return a, b


def later(z):
    z = norm(callee.maybe(z, 3))
    return z
'''

ONE_LINE = '''\
import callee


def target_kit_and_presets():
    return callee.maybe("r1"), callee.other("r1")
'''

NESTED = '''\
import callee


def work(x):
    x = callee.maybe(x)
    print(x)

    def work(x):
        return callee.maybe(x)

    return work(x)


class Holder:
    def __init__(self):
        self.value = callee.maybe(1)
'''

MULTI_LINE = '''\
import callee


def stale_control(d):
    cap = callee.maybe(
        ["run", "--fixture", "demo"],
        d,
    )
    return cap
'''

TWICE = '''\
import callee


def twice(x):
    x = callee.maybe(x)
    x = callee.maybe(x)
    return x
'''


def _line_in(text: str, needle: str) -> int:
    return next(i for i, line in enumerate(text.splitlines(), 1) if needle in line)


def _grow(root: pathlib.Path, rel: str, text: str) -> pathlib.Path:
    """A tree under `root` holding one file at `rel`: what a spec resolves against."""
    (root / rel).parent.mkdir(parents=True, exist_ok=True)
    (root / rel).write_text(text)
    return root


def _site_spec(text: str, needle: str, **anchor) -> dict:
    site = f"caller.py:{_line_in(text, needle)}"
    return {"id": site, "site": site, "callee": "callee.maybe", "wrap": ["callee.maybe"], **anchor}


def _only_reason(refused: dict) -> str:
    assert len(refused) == 1, refused
    return next(iter(refused.values()))


def test_control_a_shifted_line_refuses_a_line_only_pin_and_the_anchor_follows_the_call(tmp_path):
    """Start red. One blank line above the function moves both calls. A spec
    that names only the old line is REFUSED -- on the moved tree, because the
    line is no longer the call; on the unmoved one, because a line is not an
    anchor and never resolves one. The spec that names the call resolves on
    both, each to where the call is."""
    moved = ANCHORED.replace("def e4b(", "\ndef e4b(")
    here = _grow(tmp_path / "here", "caller.py", ANCHORED)
    there = _grow(tmp_path / "moved", "caller.py", moved)
    pin = _site_spec(ANCHORED, "callee.maybe(a, sr)")
    anchored = {**pin, "in": "e4b", "target": "a"}
    pinned = pin["site"].rpartition(":")[2]

    why = _only_reason(rct.resolve_sites([dict(pin)], there))
    assert f"line-only pin: line {pinned} of caller.py is not a call to maybe" in why
    why = _only_reason(rct.resolve_sites([dict(pin)], here))
    assert "line-only pin" in why and "not an anchor" in why
    for root, text in ((here, ANCHORED), (there, moved)):
        spec = dict(anchored)
        assert rct.resolve_sites([spec], root) == {}
        assert spec["site"] == f"caller.py:{_line_in(text, 'callee.maybe(a, sr)')}"


@pytest.mark.parametrize("decoy", ("    a = sr  # norm(callee.maybe(a, sr))",
                                   '    a = "norm(callee.maybe(a, sr))"'))
def test_control_a_callee_in_a_comment_or_a_string_is_not_a_call(tmp_path, decoy):
    """Rule 8, asked of a TEXT anchor: the callee's name on the pinned line, as
    a comment and as a string. A search for the text finds it; the ast does
    not, and the site is REFUSED with and without an anchor."""
    original = "    a = norm(callee.maybe(a, sr))"
    path = _grow(tmp_path, "caller.py", ANCHORED.replace(original, decoy)) / "caller.py"
    pin = _site_spec(ANCHORED, original.strip())
    assert _line_of(path, "callee.maybe(a, sr)") == int(pin["site"].rpartition(":")[2])
    assert "callee.maybe" in path.read_text().splitlines()[int(pin["site"].rpartition(":")[2]) - 1]
    why = _only_reason(rct.resolve_sites([dict(pin)], tmp_path))
    assert "line-only pin" in why and "is not a call to maybe" in why
    why = _only_reason(rct.resolve_sites([{**pin, "in": "e4b", "target": "a"}], tmp_path))
    assert "no call to maybe" in why and "target" in why


def test_control_a_different_callee_on_the_pinned_line_is_refused(tmp_path):
    """The pinned line is a call, in a statement that assigns to `a`, but it is
    another callee's. The call the ID names is somewhere else, and a site that
    reads NOT-REACHED because its line moved looks exactly like a site the run
    never reached."""
    _grow(tmp_path, "caller.py", ANCHORED.replace("callee.maybe(a, sr)", "callee.other(a, sr)"))
    pin = _site_spec(ANCHORED, "callee.maybe(a, sr)")
    why = _only_reason(rct.resolve_sites([dict(pin)], tmp_path))
    assert "is not a call to maybe" in why
    why = _only_reason(rct.resolve_sites([{**pin, "in": "e4b", "target": "a"}], tmp_path))
    assert "no call to maybe" in why and "target" in why


_PLAN: dict = {}


def _plan() -> list[dict]:
    if "traces" not in _PLAN:
        pytest.importorskip("numpy")
        pytest.importorskip("scipy")
        _PLAN["traces"] = rct.load_spec(ROOT / "tools/refusal_candidate_traces.py").TRACES
    return _PLAN["traces"]


def _by_id(id_: str) -> dict:
    return dict(next(t for t in _plan() if t["id"] == id_))


def test_control_a_deleted_baseline_call_is_unresolved_and_starts_no_child(tmp_path):
    """Champion's finding on #1772: function + callee + ordinal binds the
    `:441` ID to the comparison call once the baseline is deleted. Here the
    baseline line alone is removed from a copy of the real probe: `:441` is
    REFUSED before anything is launched, and `:446` still resolves and runs.
    A second copy with one blank line above both resolves both, one line down."""
    lines = (ROOT / ADF).read_text().splitlines(keepends=True)
    [i] = [j for j, line in enumerate(lines) if line.strip() == BASELINE]
    assert sum(line.strip() == COMPARISON for line in lines) == 1
    deleted = _grow(tmp_path / "deleted", ADF, "".join(l for j, l in enumerate(lines) if j != i))
    started: list[str] = []
    script = _write(tmp_path, "child.py", "raise SystemExit(0)\n")

    def marker(id_):
        def make(ws):
            started.append(id_)
            return {"entry": str(script), "argv": [], "root": str(tmp_path), "cwd": str(tmp_path), "timeout": 60}
        return make

    first, second = rct.trace_all([{**_by_id(f"{ADF}:441"), "working": marker("441")},
                                   {**_by_id(f"{ADF}:446"), "working": marker("446")}],
                                  tmp_path / "work", ("working",), root=deleted)
    assert started == ["446"]
    [rec] = first["runs"]["working"]
    assert rec["verdict"] == rct.UNRESOLVED and rec.get("exit_status") is None
    for named in (first["id"], ADF, "line 441", "run_case.prepare"):
        assert named in rec["unresolved"]
    assert second["site"] == f"{ADF}:{_line_in((deleted / ADF).read_text(), COMPARISON)}"
    assert second["runs"]["working"][0]["exit_status"] == 0        # a child ran for :446

    shifted = _grow(tmp_path / "shifted", ADF, "".join("\n" + l if j == i else l for j, l in enumerate(lines)))
    specs = [_by_id(f"{ADF}:441"), _by_id(f"{ADF}:446")]
    assert rct.resolve_sites(specs, shifted) == {}
    text = (shifted / ADF).read_text()
    assert [s["site"] for s in specs] == [f"{ADF}:{_line_in(text, BASELINE)}", f"{ADF}:{_line_in(text, COMPARISON)}"]


def test_two_calls_on_one_line_are_anchored_by_callee(tmp_path):
    """`verify_live_midi.py:107`'s shape: two calls in one `return`, neither
    assigned to anything. The callee is the discriminator, as it is in view()."""
    _grow(tmp_path, "caller.py", ONE_LINE)
    want = f"caller.py:{_line_in(ONE_LINE, 'return callee.maybe')}"
    for callee in ("callee.maybe", "callee.other"):
        spec = {"id": "x", "site": "caller.py:1", "callee": callee, "in": "target_kit_and_presets"}
        assert rct.resolve_sites([spec], tmp_path) == {}
        assert spec["site"] == want


def test_an_anchor_names_one_call_or_refuses_to_choose(tmp_path):
    """The issue's edge cases. A nested function of the same name: both are
    `in="work"`, so the site is REFUSED until a target or the qualified scope
    says which. A method: the scope is matched by its tail, so `__init__`
    resolves and the class may qualify it. A call over several lines resolves
    to its first, the line the scanner names."""
    _grow(tmp_path, "caller.py", NESTED)
    outer, inner, method = (_line_in(NESTED, s) for s in
                            ("x = callee.maybe(x)", "return callee.maybe(x)", "self.value = callee.maybe(1)"))

    def resolved(**anchor) -> str | None:
        spec = {"id": "x", "site": "caller.py:1", "callee": "callee.maybe", **anchor}
        return None if rct.resolve_sites([spec], tmp_path) else spec["site"]

    def refused(**anchor) -> str:
        return _only_reason(rct.resolve_sites([{"id": "x", "site": "caller.py:1", "callee": "callee.maybe",
                                                **anchor}], tmp_path))

    why = refused(**{"in": "work"})
    assert "2 calls to maybe" in why and "not unique" in why
    assert resolved(**{"in": "work", "target": "x"}) == f"caller.py:{outer}"
    assert resolved(**{"in": "work.work"}) == f"caller.py:{inner}"
    why = refused(**{"in": "work.work", "target": "x"})
    assert "no call to maybe" in why and "target" in why
    assert resolved(**{"in": "__init__", "target": "self.value"}) == f"caller.py:{method}"
    assert resolved(**{"in": "Holder.__init__", "target": "self.value"}) == f"caller.py:{method}"
    assert "no call to maybe in nope()" in refused(**{"in": "nope"})

    _grow(tmp_path, "caller.py", MULTI_LINE)
    first = _line_in(MULTI_LINE, "cap = callee.maybe(")
    assert resolved(**{"in": "stale_control", "target": "cap"}) == f"caller.py:{first}"


def test_an_ordinal_is_a_tie_breaker_and_refuses_when_it_points_past_the_calls(tmp_path):
    """Two calls no discriminator separates -- the shape of a duplicated
    statement. Without `n` the site is REFUSED; `n` picks among them; an `n`
    past the calls is REFUSED, and so is `n=2` once the first call is deleted:
    the ordinal does not slide onto the call that is left."""
    twice = _grow(tmp_path / "twice", "caller.py", TWICE)
    first = _line_in(TWICE, "x = callee.maybe(x)")
    base = {"id": "x", "site": "caller.py:1", "callee": "callee.maybe", "in": "twice", "target": "x"}
    why = _only_reason(rct.resolve_sites([dict(base)], twice))
    assert "2 calls to maybe" in why and "not unique" in why
    for n, line in ((1, first), (2, first + 1)):
        spec = {**base, "n": n}
        assert rct.resolve_sites([spec], twice) == {} and spec["site"] == f"caller.py:{line}"
    for n in (0, 3):
        why = _only_reason(rct.resolve_sites([{**base, "n": n}], twice))
        assert f"n={n} but only 2 call" in why

    gone = TWICE.replace("    x = callee.maybe(x)\n", "", 1)
    why = _only_reason(rct.resolve_sites([{**base, "n": 2}], _grow(tmp_path / "gone", "caller.py", gone)))
    assert "n=2 but only 1 call" in why


def test_control_the_anchor_cannot_see_which_object_a_tail_name_hangs_on(tmp_path):
    """Rule 8, asked of this guard: `impostor.maybe(x)` where `callee.maybe(x)`
    stood satisfies every part of the anchor -- function, tail name, target,
    first argument -- and is another function. The resolver is blind to it and
    resolves; what says so is the run, whose wrapper intercepts one module's
    function: the site reads NOT-REACHED."""
    _fixture(tmp_path)
    _write(tmp_path, "impostor.py", "def maybe(x):\n    return 'impostor'\n")
    path = _write(tmp_path, "fooled.py", HEAD + "import impostor" + '''


def work(x):
    x = impostor.maybe(x)
    return x
''' + _tail('''\
    print("result:", work(int(argv[0])))
'''))
    spec = {"id": "fooled.py:1", "site": "fooled.py:1", "callee": "callee.maybe", "in": "work", "target": "x"}
    assert rct.resolve_sites([spec], tmp_path) == {}
    assert spec["site"] == f"fooled.py:{_line_of(path, 'impostor.maybe(x)')}"
    rec = rct.run_child({"entry": str(path), "argv": ["0"], "root": str(tmp_path), "cwd": str(tmp_path),
                         "timeout": 60}, tmp_path / "work-fooled", ["callee.maybe"], [spec["site"]])
    assert rec["verdict"] == rct.NOT_REACHED and rec["exit_status"] == 0 and rec["calls"] == []


def test_the_plan_covers_every_id_in_the_record():
    """#1772: the migration missed nothing. The IDs the plan produces are the
    record's -- the loop-made ones and both at `verify_live_midi.py:107` --
    and each keeps the `path:line` it was traced under: an ID is a record key,
    not where the call is now."""
    record = _record()
    ids = {t["id"] for t in _plan()}
    assert ids == {t["id"] for t in record["traces"]}
    assert len(ids) == 37
    assert {"fpga/verify_live_midi.py:107", "fpga/verify_live_midi.py:107#2"} <= ids


def test_every_site_in_the_plan_is_anchored_and_resolves_on_this_tree():
    """The gate, run against the tree it is committed on: no spec pins by line
    alone, and every anchor resolves where its call is now. It names no line,
    so an edit that moves a call leaves it green and one that changes the call
    turns it red naming the spec. The line each resolves to is checked against
    the file by the ast alone, not through the resolver's own matching."""
    specs = [dict(t) for t in _plan()]
    assert all("in" in t for t in specs), [t["id"] for t in specs if "in" not in t]
    assert rct.resolve_sites(specs, ROOT) == {}
    trees = {p: ast.parse((ROOT / p).read_text()) for p in {t["site"].rpartition(":")[0] for t in specs}}
    for t in specs:
        path, _, line = t["site"].rpartition(":")
        tails = {n.func.id if isinstance(n.func, ast.Name) else n.func.attr
                 for n in ast.walk(trees[path]) if isinstance(n, ast.Call) and n.lineno == int(line)}
        assert t["callee"].rpartition(".")[2] in tails, (t["id"], t["site"])


def _git(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(ROOT), *args], capture_output=True, text=True)


def test_every_anchor_names_the_call_its_id_identified_at_the_recorded_revision():
    """Each anchor is checked against the call its ID named when the row was
    traced, not merely against a call of the callee: in the sources of the
    record's own revision, every anchor resolves to its ID's line. The same
    sources with one blank line on top resolve one line down, so this cannot
    pass by leaving the sites alone. Five of the six groups were traced at an
    earlier session commit than the record's revision; the check holds for
    them because no traced file differs between the two."""
    record = _record()
    paths = sorted({t["id"].rpartition(":")[0] for t in record["traces"]})
    revs = sorted({v.strip() for v in record.get("sessions", {}).values()} | {record["revision"]})
    blobs = {_git("rev-parse", f"{rev}:{path}").stdout.strip() for rev in revs for path in paths}
    at = {path: _git("show", f"{record['revision']}:{path}").stdout for path in paths}
    if "" in blobs or len(blobs) != len(paths):
        pytest.skip(f"the recorded revisions ({', '.join(r[:8] for r in revs)}) are not all in this clone")
    assert len(blobs) == len(paths), "a traced file differs between the session commits"

    specs = [dict(t) for t in _plan()]
    assert rct.resolve_sites(specs, sources=at) == {}
    assert [t["site"] for t in specs] == [t["id"].split("#")[0] for t in specs]
    specs = [dict(t) for t in _plan()]
    assert rct.resolve_sites(specs, sources={p: "\n" + s for p, s in at.items()}) == {}
    assert [t["site"] for t in specs] == [f"{p}:{int(n) + 1}" for t in specs
                                          for p, _, n in [t["id"].split("#")[0].rpartition(":")]]


def _repo(root: pathlib.Path) -> None:
    git = ["git", "-C", str(root), "-c", "user.name=t", "-c", "user.email=t@t", "-c", "commit.gpgsign=false"]
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    subprocess.run([*git, "add", "."], check=True)
    subprocess.run([*git, "commit", "-q", "-m", "tree"], check=True)


_SPEC = '''\
import pathlib
STARTED = []
HERE = pathlib.Path(__file__).resolve().parent


def working(ws):
    STARTED.append("working")
    return {"entry": str(HERE / "caller.py"), "argv": [], "root": str(HERE), "cwd": str(HERE), "timeout": 60}


TRACES = [{"id": "caller.py:%(line)d", "site": "caller.py:%(line)d", "callee": "callee.maybe",
           "in": "e4b", "target": "a",
           "wrap": ["callee.maybe"], "working": working}]
'''


def test_a_modified_traced_file_is_refused_before_any_run(tmp_path, capsys, monkeypatch):
    """The precondition of one resolution per site: a tampered run executes a
    clone of HEAD and the working run the working tree, and both are served the
    one line. So a traced file modified in the tree is REFUSED, and no child
    starts: through the CLI, on a clean tree the run is launched and on a
    modified one it is not."""
    pytest.importorskip("numpy")
    line = _line_in(ANCHORED, "callee.maybe(a, sr)")
    repo = _grow(tmp_path, "caller.py", ANCHORED)
    _write(tmp_path, "callee.py", "def maybe(x):\n    return x\n")
    _write(tmp_path, "plan_tmp_repo.py", _SPEC % {"line": line})
    _repo(repo)
    monkeypatch.setattr(rct, "ROOT", repo)
    monkeypatch.setattr(rct, "_scan_json", lambda rss: {})
    out = tmp_path / "record.json"
    argv = ["run", "--spec", str(repo / "plan_tmp_repo.py"), "--out", str(out), "--roles", "working",
            "--keep", str(tmp_path / "scratch")]
    assert rct.main(argv) == 0
    [row] = json.loads(out.read_text())["traces"]
    clean = sys.modules["plan_tmp_repo"]
    assert clean.STARTED == ["working"] and row["runs"]["working"][0]["exit_status"] == 0
    capsys.readouterr()

    (repo / "caller.py").write_text("\n" + ANCHORED)
    assert rct.main(argv) == 0
    [row] = json.loads(out.read_text())["traces"]
    assert clean.STARTED == ["working"] and sys.modules["plan_tmp_repo"].STARTED == []
    [rec] = row["runs"]["working"]
    assert rec["verdict"] == rct.UNRESOLVED and rec.get("exit_status") is None
    assert f"pinned line {line}" in rec["unresolved"]
    assert "caller.py is modified" in rec["unresolved"]
    assert "caller.py is modified" in capsys.readouterr().out


def test_resolve_reports_each_site_and_exits_nonzero_when_one_is_refused(tmp_path, capsys, monkeypatch):
    """The current-state report: every ID with the line it was traced under and
    the line its anchor resolves to now, or why it does not. It starts nothing,
    and its exit status is the gate's."""
    (tmp_path / "caller.py").write_text(ANCHORED)
    line = _line_in(ANCHORED, "callee.maybe(a, sr)")
    spec = tmp_path / "plan_tmp_resolve.py"
    spec.write_text("TRACES = [\n"
                    "    {'id': 'caller.py:%d', 'site': 'caller.py:%d', 'callee': 'callee.maybe',\n"
                    "     'in': 'e4b', 'target': 'a'},\n"
                    "    {'id': 'caller.py:1', 'site': 'caller.py:1', 'callee': 'callee.maybe'},\n"
                    "]\n" % (line, line))
    monkeypatch.setattr(rct, "ROOT", tmp_path)
    assert rct.main(["resolve", "--spec", str(spec)]) == 1
    [header, *rows] = capsys.readouterr().out.splitlines()
    assert header.endswith("2 sites, 1 REFUSED")
    [kept, refused] = (next(r for r in rows if r.startswith(f"caller.py:{n} ")) for n in (line, 1))
    assert kept.split()[-1] == f"caller.py:{line}" and "REFUSED" not in kept
    assert "REFUSED" in refused and "line-only pin" in refused


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
