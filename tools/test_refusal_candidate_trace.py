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
"""
from __future__ import annotations


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


def checked(x):
    if x:
        raise AssertionError(f"callee checked: {x}")
    return "checked"
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
    files["unfollowed"] = _write(tmp_path, "unfollowed.py", HEAD + '''


def work(x, times):
    for i in range(times):
        callee.other(), callee.checked(x if i == times - 1 else 0)
''' + _tail('''\
    work(int(argv[0]), int(argv[1]))
    print("done")
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
    spec = {"id": "a", "site": _site(path), "callee": "callee.maybe", "wrap": ["callee.maybe"]}
    makers = [lambda ws, arg=arg: {"entry": str(path), "argv": [arg], "root": str(tmp_path), "cwd": str(tmp_path),
                                   "timeout": 60, "share": "same-name"} for arg in ("0", "1")]
    one = dict(spec, id="a", working=makers[0])
    two = dict(spec, id="b", working=makers[1])
    a, b = rct.trace_all([one, two], tmp_path / "shared", ("working",))
    assert a["runs"]["working"][0]["verdict"] == rct.RETURNED        # made once
    assert b["runs"]["working"][0]["verdict"] == rct.UNRESOLVED      # and not served to another run
    assert "two different runs" in b["runs"]["working"][0]["unresolved"]


def _unfollowed(tmp_path: pathlib.Path, x: int, times: int):
    """Trace `unfollowed.py`: `times` rounds of `callee.other(), callee.checked(...)`
    on one line. `checked` returns in every round but the last, where it
    raises a plain `AssertionError` when `x`; nothing in the tracer follows
    that class."""
    path = _fixture(tmp_path)["unfollowed"]
    site = _site(path, "callee.other(), callee.checked(")
    run = {"entry": str(path), "argv": [str(x), str(times)], "root": str(tmp_path), "cwd": str(tmp_path),
           "timeout": 60}
    rec = rct.run_child(run, tmp_path / f"unfollowed-{x}-{times}", ["callee.checked", "callee.other"], [site])
    assert not rec.get("wrap_missing"), rec["wrap_missing"]
    return rec, site


def test_a_raised_class_the_tracer_does_not_follow_never_reads_returned(tmp_path):
    """#1952. Only refusals are followed, so a call that raises anything else
    has calls and no follows -- and `view()` read that RETURNED, "the call ran
    and came back", for a process that died on it."""
    rec, site = _unfollowed(tmp_path, 1, 1)
    truth = subprocess.run([sys.executable, str(_fixture(tmp_path)["unfollowed"]), "1", "1"],
                           capture_output=True, text=True, timeout=60, check=False)
    assert (truth.returncode, truth.stderr.strip().splitlines()[-1]) == (1, "AssertionError: callee checked: 1")
    assert rec["exit_status"] == 1 and rec["verdict"] == rct.UNRESOLVED
    assert rec["stderr_tail"][-1] == truth.stderr.strip().splitlines()[-1]
    assert rec["uncaught"]["class"] == "builtins.AssertionError" and not rec["uncaught"]["followed"]
    assert rec["follows"] == []
    assert [(c["callee"], c["outcome"]) for c in rec["calls"]] == [("callee.other", "returned"),
                                                                  ("callee.checked", "raised")]
    assert rct.view(rec, site, "callee.checked")["verdict"] == rct.UNRESOLVED


def test_the_raised_call_counts_past_the_kept_calls_and_only_for_its_own_callee(tmp_path):
    """A view keeps its first KEPT_CALLS calls; the verdict is not read off
    those alone, and a callee that came back on the same line is not tarred
    by the one that raised."""
    rec, site = _unfollowed(tmp_path, 1, rct.KEPT_CALLS + 3)
    assert rec["exit_status"] == 1 and rec["verdict"] == rct.UNRESOLVED
    checked = rct.view(rec, site, "callee.checked")
    assert checked["calls_total"] == rct.KEPT_CALLS + 3 and len(checked["calls"]) == rct.KEPT_CALLS
    assert [c["outcome"] for c in checked["calls"]] == ["returned"] * rct.KEPT_CALLS
    assert checked["verdict"] == rct.UNRESOLVED
    other = rct.view(rec, site, "callee.other")
    assert other["calls_total"] == rct.KEPT_CALLS + 3 and other["verdict"] == rct.RETURNED


def test_what_came_back_and_what_was_followed_keep_their_verdicts(tmp_path):
    back, site = _unfollowed(tmp_path, 0, 1)
    assert back["exit_status"] == 0 and back["verdict"] == rct.RETURNED
    assert rct.view(back, site, "callee.checked")["verdict"] == rct.RETURNED
    assert rct.view(back, site, "callee.other")["verdict"] == rct.RETURNED
    files = _fixture(tmp_path)
    assert rct.view(_trace(tmp_path, "escape", "1"), _site(files["escape"]), "callee.maybe")["verdict"] == rct.ESCAPED
    assert rct.view(_trace(tmp_path, "handled", "1"), _site(files["handled"]), "callee.maybe")["verdict"] == rct.HANDLED


def test_an_unresolved_run_stays_unresolved_whatever_its_calls_say():
    rec = {"unresolved": "timed out after 60 s", "verdict": rct.UNRESOLVED, "site_executed": ["escape.py:1"],
           "calls": [{"callee": "callee.maybe", "raised": {"class": "AssertionError"},
                      "caller": {"file": "escape.py", "line": 1, "first": 1, "last": 1}}],
           "follows": []}
    out = rct.view(rec, "escape.py:1", "callee.maybe")
    assert out["verdict"] == rct.UNRESOLVED and out["unresolved"] == "timed out after 60 s"
    assert (out["calls"], out["follows"], out["site_executed"]) == ([], [], [])


# ---------------------------------------------------------------------------
# injected controls: each tracer defect turns its fixture's verdict wrong
# ---------------------------------------------------------------------------
def test_control_no_follow_cannot_read_as_returned(tmp_path):
    rec = _trace(tmp_path, "escape", "1", inject=["NO_FOLLOW"])
    assert rec["verdict"] == rct.UNRESOLVED and rec["uncaught"]["class"] == "callee.Refused"
    assert rec["follows"] == [] and rec["calls"][0]["outcome"] == "raised"
    assert rct.view(rec, _site(_fixture(tmp_path)["escape"]), "callee.maybe")["verdict"] == rct.UNRESOLVED


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
    if rct.UNRESOLVED in refusing:
        return "unresolved"
    if refusing:
        return "shadowed"
    return "no refusing input"


def test_an_unresolved_refusing_run_is_not_counted_as_shadowed():
    """`shadowed` is "an earlier site refused first", a `NOT-REACHED` here. A
    run the tracer could not follow is not that fact, and must not be filed
    under it (#1952)."""
    def trace(*verdicts):
        return {"runs": {"refusing": [{"verdict": v} for v in verdicts]}}
    assert _disposition(trace(rct.NOT_REACHED)) == "shadowed"
    assert _disposition(trace(rct.NOT_REACHED, rct.NOT_REACHED)) == "shadowed"
    assert _disposition(trace(rct.UNRESOLVED)) == "unresolved"
    assert _disposition(trace(rct.UNRESOLVED, rct.NOT_REACHED)) == "unresolved"
    assert _disposition(trace(rct.ESCAPED, rct.UNRESOLVED)) == "escaped"
    assert _disposition(trace(rct.HANDLED, rct.UNRESOLVED)) == "handled"
    assert _disposition({"runs": {"refusing": None}}) == "no refusing input"


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


def test_no_committed_returned_verdict_keeps_a_call_that_raised():
    """#1952's shape read off every run in the record, not assumed from the
    headline: no `RETURNED` keeps a call that raised. Stated limit: a view
    keeps its first `KEPT_CALLS` calls, so a raise after those is invisible
    here. Four working runs keep 12 of more (20, 20, 64, 25); for those the
    record is read for what it can say -- no traceback, nothing uncaught."""
    capped = []
    for t in _record()["traces"]:
        for role, recs in t["runs"].items():
            for rec in recs or []:
                if rec["verdict"] != rct.RETURNED:
                    continue
                assert not [c for c in rec["calls"] if c.get("raised")], (t["id"], role)
                if rec.get("calls_total", 0) > len(rec["calls"]):
                    capped.append((t["id"], rec["calls_total"]))
                    assert not rec["traceback"] and rec["uncaught"] is None, t["id"]
    assert sorted(n for _id, n in capped) == [20, 20, 25, 64]
