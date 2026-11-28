"""Validation cases for `tools/child_failure_escape_census.py` (#1690).

The census is an AST walk, and the throwaway it replaces was wrong twice
before it was right -- both times in the direction of a LARGER answer (271
sites, then 18 modules), and both times caught by holding its output against
a known fact about a real file, never by reading the script. So this file
holds it against known facts of both kinds:

  * small trees whose answer follows from Python's own semantics, each naming
    the one rule it pins;
  * real files in this tree whose answer was established by reading them:
    the three `build_arty*.py` tools each refuse their Vivado timeout
    explicitly, so the walk must not list them (the second wrong answer
    did), and the four rows #1690 repaired must stay absent.
"""
from __future__ import annotations

import json
import pathlib
import shutil
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import child_failure_escape_census as census_mod                     # noqa: E402

Refused = census_mod.Refused
M0, MAIN, MODULE = "fpga/m0.py", "main", "<module>"
TE, CPE, RE = "subprocess.TimeoutExpired", "subprocess.CalledProcessError", "RuntimeError"
BASE = "class Refused(Exception):\n    pass\n"
HEAD = "import subprocess\n"


def tree(tmp_path, *sources: str, refusal: str = BASE) -> pathlib.Path:
    """A scan root holding `sources` as m0.py, m1.py, ... and a refusal base
    file beside it."""
    shutil.rmtree(tmp_path / "fpga", ignore_errors=True)
    (tmp_path / "fpga").mkdir()
    for i, source in enumerate(sources):
        (tmp_path / "fpga" / f"m{i}.py").write_text(source)
    (tmp_path / "refusal.py").write_text(refusal)
    return tmp_path


def census(tmp_path, *sources: str, refusal: str = BASE, min_modules: int = 1) -> dict:
    root = tree(tmp_path, *sources, refusal=refusal)
    return census_mod.census(root / "fpga", refusal_path=root / "refusal.py",
                             min_modules=min_modules)


def rows(tmp_path, *sources: str, refusal: str = BASE) -> dict:
    """{(module, entry, raised class): [(file, line, kind), ...]} -- the rows
    with each path's hops reduced to what a test can pin."""
    out = {}
    for row in census(tmp_path, *sources, refusal=refusal)["rows"]:
        out[(row["module"], row["entry"], row["raised"])] = [
            (hop["file"], hop["line"], hop["kind"]) for hop in row["path"]]
    return out


def line_of(source: str, needle: str) -> int:
    """The line of the LAST place `needle` stands: `bench()` is in
    `def bench():` before it is a call."""
    return source[:source.rindex(needle)].count("\n") + 1


# ------------------------------------------------------------ the row ----
def test_a_child_past_its_deadline_reaching_main_is_listed_with_its_path(tmp_path):
    """The shape of every #1690 row: the source a frame down, no handler on
    the way up, and the path naming each hop."""
    src = HEAD + '''

def bench():
    subprocess.run(["vvp", "tb.vvp"], check=True, timeout=1800)


def main():
    bench()
'''
    got = rows(tmp_path, src)
    assert set(got) == {(M0, MAIN, TE), (M0, MAIN, CPE)}
    for key in got:
        assert got[key] == [(M0, line_of(src, "bench()"), "call"),
                            (M0, line_of(src, "subprocess.run"), "child")]
    res = census(tmp_path, src)
    assert res["modules"] == 1 and res["notices"] == []
    what = res["rows"][0]["path"][-1]["what"]
    assert "subprocess.run" in what and "1800" in what


def test_a_handler_at_the_call_removes_exactly_its_class(tmp_path):
    fixed = HEAD + '''

def bench():
    subprocess.run(["vvp", "tb.vvp"], check=True, timeout=1800)


def main():
    try:
        bench()
    except subprocess.TimeoutExpired as exc:
        print(exc)
        return 2
'''
    assert set(rows(tmp_path, fixed)) == {(M0, MAIN, CPE)}
    assert rows(tmp_path, fixed.replace("check=True, ", "")) == {}


def test_a_source_inside_its_own_try_is_not_a_leak(tmp_path):
    """The second wrong answer listed the three build_arty tools on exactly
    this shape: the timeout refused where it is raised."""
    assert rows(tmp_path, HEAD + '''

def main():
    p = subprocess.Popen(["vivado"])
    try:
        rc = p.wait(timeout=7200)
    except subprocess.TimeoutExpired:
        p.kill()
        return 2
''') == {}


def test_an_exception_outside_the_try_body_is_not_covered_by_its_handlers(tmp_path):
    """Python's rule, pinned because it was got wrong here first: only the
    body is under the handlers. An exception raised in else, in a handler
    body or in finally propagates past them."""
    src = HEAD + '''

def main():
    try:
        pass
    except subprocess.TimeoutExpired:
        pass
    else:
        subprocess.run(["vvp"], timeout=5)
'''
    assert set(rows(tmp_path, src)) == {(M0, MAIN, TE)}
    in_handler = HEAD + '''

def main():
    try:
        pass
    except subprocess.TimeoutExpired:
        subprocess.run(["vvp"], timeout=5)
'''
    in_finally = HEAD + '''

def main():
    try:
        pass
    finally:
        subprocess.run(["vvp"], timeout=5)
'''
    for variant in (src, in_handler, in_finally):
        assert set(rows(tmp_path, variant)) == {(M0, MAIN, TE)}


def test_a_handler_naming_a_base_of_the_raised_class_catches_it(tmp_path):
    template = HEAD + '''

def main():
    try:
        subprocess.run(["vvp"], timeout=5)
    except {}:
        return 2
'''
    for handler in ("subprocess.TimeoutExpired", "subprocess.SubprocessError", "Exception",
                    "BaseException", "(OSError, subprocess.TimeoutExpired)", ""):
        assert rows(tmp_path, template.format(handler)) == {}, handler
    for handler in ("OSError", "RuntimeError", "subprocess.CalledProcessError", "ValueError"):
        assert set(rows(tmp_path, template.format(handler))) == {(M0, MAIN, TE)}, handler


# ------------------------------------------------ the sources it reads ----
@pytest.mark.parametrize("body, keys", [
    ('subprocess.check_output(["git", "show", "x"])', {CPE}),
    ('subprocess.check_output(["git", "show", "x"], timeout=5)', {CPE, TE}),
    ('subprocess.check_call(["x"])', {CPE}),
    ('subprocess.run(["x"], check=True)', {CPE}),
    ('subprocess.run(["x"], check=False, timeout=None)', set()),
    ('subprocess.run(["x"], timeout=5)', {TE}),
    ('subprocess.call(["x"], check=True, timeout=5)', {CPE, TE}),
    ('subprocess.Popen(["x"])', set()),
    ('p = subprocess.Popen(["x"])\n    p.communicate(timeout=5)', {TE}),
    ('p = subprocess.Popen(["x"])\n    p.wait(timeout=5)', {TE}),
    ('p = subprocess.Popen(["x"])\n    p.wait()', set()),
    ('p = subprocess.Popen(["x"])\n    p.wait(timeout=None)', set()),
], ids=["check_output", "check_output+timeout", "check_call", "run-check", "run-unchecked",
        "run-timeout", "call-check-timeout", "popen", "communicate", "wait", "wait-no-timeout",
        "wait-none"])
def test_which_calls_can_raise_a_followed_class(tmp_path, body, keys):
    got = rows(tmp_path, HEAD + "\n\ndef main():\n    " + body + "\n")
    assert {key for (mod, entry, key) in got if (mod, entry) == (M0, MAIN)} == keys, body
    assert all(mod == M0 for (mod, entry, key) in got), body


@pytest.mark.parametrize("alias", ["import subprocess as sp\n", "from subprocess import run\n",
                                   "from subprocess import run as sp_run\n"])
def test_subprocess_under_another_name_is_still_subprocess(tmp_path, alias):
    call = "sp.run" if alias == "import subprocess as sp\n" else ("sp_run" if "as sp_run" in alias
                                                                  else "run")
    got = rows(tmp_path, HEAD + alias + "\n\ndef main():\n    " + call + '(["x"], timeout=5)\n')
    assert set(got) == {(M0, MAIN, TE)}, alias


def test_a_receiver_not_bound_from_popen_is_not_read_as_a_child(tmp_path):
    """`thread.join(timeout=)` and `serial.Serial(timeout=)` return; they do
    not raise TimeoutExpired. Only a name bound from subprocess.Popen in the
    same function makes `.wait`/`.communicate` a followed child."""
    assert rows(tmp_path, HEAD + '''

def main():
    p = make_port()
    p.wait(timeout=5)
''') == {}
    assert rows(tmp_path, HEAD + '''

def other():
    p = subprocess.Popen(["x"])


def main():
    p.wait(timeout=5)
''') == {}


def test_an_unknown_check_value_counts_as_checking(tmp_path):
    assert set(rows(tmp_path, HEAD + '''

def main():
    subprocess.run(["x"], check=flag)
''')) == {(M0, MAIN, CPE)}


def test_a_raise_of_a_followed_class_is_a_source_two_frames_down(tmp_path):
    src = HEAD + '''

def evidence():
    raise RuntimeError("stored transcript hash differs")


def main():
    evidence()
'''
    got = rows(tmp_path, src)
    assert got == {(M0, MAIN, RE): [(M0, line_of(src, "evidence()"), "call"),
                                    (M0, line_of(src, "raise RuntimeError"), "raise")]}


def test_a_raise_of_any_other_class_is_not_followed(tmp_path):
    assert rows(tmp_path, HEAD + '''

def main():
    raise ValueError("publication directory must be empty")
''') == {}


# ------------------------------------------------- classes and aliases ----
RUNTIME_REFUSED = HEAD + '''import refusal


class Refused(refusal.Refused, RuntimeError):
    pass
'''
RAISER = '''

def sim():
    raise Refused('vvp failed')
'''
CALLS_SIM = '''

def main():
    sim()
'''
GUARDS_SIM = '''

def main():
    try:
        sim()
    except {handler}:
        return 2
'''


def test_a_refused_that_is_a_runtime_error_is_followed_and_caught_by_name(tmp_path):
    """verify_late_events' own class: `class Refused(refusal.Refused, RuntimeError)`."""
    assert set(rows(tmp_path, RUNTIME_REFUSED + RAISER + CALLS_SIM)) == {(M0, MAIN, "m0.Refused")}
    for handler in ("Refused", "RuntimeError", "refusal.Refused", "Exception"):
        guarded = RUNTIME_REFUSED + RAISER + GUARDS_SIM.format(handler=handler)
        assert rows(tmp_path, guarded) == {}, handler
    for handler in ("subprocess.TimeoutExpired", "OSError"):
        guarded = RUNTIME_REFUSED + RAISER + GUARDS_SIM.format(handler=handler)
        assert set(rows(tmp_path, guarded)) == {(M0, MAIN, "m0.Refused")}, handler


def test_a_refused_that_is_not_a_runtime_error_subclass_is_not_followed(tmp_path):
    """bringup_arty's own class: `class Refused(refusal.Refused, Exception)`."""
    plain = HEAD + '''import refusal


class Refused(refusal.Refused, Exception):
    pass
'''
    assert rows(tmp_path, plain + RAISER + CALLS_SIM) == {}


def test_a_sibling_refused_does_not_catch_another_modules_runtime_error_refused(tmp_path):
    """The class relation `refusal_sibling_scan.py` exists for, seen from
    here: a handler written on module a's class does not catch module b's."""
    caller = HEAD + '''import refusal
import m1


class Refused(refusal.Refused, RuntimeError):
    pass


def main():
    try:
        m1.sim()
    except Refused:
        return 2
'''
    assert set(rows(tmp_path, caller, RUNTIME_REFUSED + RAISER)) == {(M0, MAIN, "m1.Refused")}
    same_class = caller.replace("except Refused:", "except m1.Refused:")
    assert rows(tmp_path, same_class, RUNTIME_REFUSED + RAISER) == {}


def test_a_module_level_tuple_or_alias_of_classes_resolves_as_handler_and_as_raised_class(tmp_path):
    """verify_sd_dac's `BENCH_FAILURES = (RuntimeError, CalledProcessError,
    TimeoutExpired)` after #1662, and the `Refused = pc.Refused` aliases the
    tree uses."""
    fixed = HEAD + '''
BENCH_FAILURES = (RuntimeError, subprocess.CalledProcessError, subprocess.TimeoutExpired)


def bench():
    raise RuntimeError('bench failed')


def main():
    try:
        bench()
    except BENCH_FAILURES as exc:
        return 2
'''
    assert rows(tmp_path, fixed) == {}
    narrowed = fixed.replace("(RuntimeError, subprocess.CalledProcessError, ", "(")
    assert set(rows(tmp_path, narrowed)) == {(M0, MAIN, RE)}
    aliased = HEAD + '''import refusal
import pc


Refused = pc.Refused


def main():
    try:
        raise Refused('x')
    except Refused:
        return 2
'''
    own_class = HEAD + "class Refused(RuntimeError):\n    pass\n"
    assert rows(tmp_path, aliased, own_class) == {}
    escaped = HEAD + '''import refusal


Refused = refusal.Refused


def main():
    raise Refused('x')
'''
    base = own_class[len(HEAD):]
    assert set(rows(tmp_path, escaped, refusal=base)) == {(M0, MAIN, "refusal.Refused")}


def test_an_alias_that_is_not_a_class_or_a_tuple_is_unresolved_and_noticed(tmp_path):
    src = HEAD + '''
THINGS = get_things()


def main():
    try:
        subprocess.run(['x'], timeout=5)
    except THINGS:
        return 2
'''
    got = rows(tmp_path, src)
    assert set(got) == {(M0, MAIN, TE)}
    notices = census(tmp_path, src)["notices"]
    assert len(notices) == 1 and "unresolved" in notices[0] and "THINGS" in notices[0], notices


def test_an_unresolved_handler_type_is_read_as_catching_nothing(tmp_path):
    """The conservative direction for a leak finder: a handler the walk cannot
    read is credited with nothing, so a covered call may be listed and said so
    in the notices -- never the other way round."""
    src = HEAD + '''

def main():
    try:
        subprocess.run(['x'], timeout=5)
    except exc:
        return 2
'''
    assert set(rows(tmp_path, src)) == {(M0, MAIN, TE)}
    assert any("unresolved" in n for n in census(tmp_path, src)["notices"])


def test_a_bare_raise_re_raises_what_its_own_try_body_raised(tmp_path):
    """Five bare `raise` sites stand in fpga/. A re-raise inherits the classes
    of the body it handles -- so one under `except ValueError` raises nothing
    followed -- and is covered by a handler OUTSIDE its own try, not by the
    try it belongs to."""
    inherited = HEAD + '''

def main():
    try:
        subprocess.run(["vvp"], timeout=5)
    except Exception:
        raise
'''
    assert set(rows(tmp_path, inherited)) == {(M0, MAIN, TE)}
    not_followed = HEAD + '''

def main():
    try:
        raise ValueError("no")
    except ValueError:
        if True:
            raise
'''
    assert rows(tmp_path, not_followed) == {}
    outer = HEAD + '''

def main():
    try:
        try:
            p = subprocess.Popen(["vivado"])
            p.wait(timeout=7200)
        except subprocess.TimeoutExpired:
            p.kill()
            raise
    except (OSError, subprocess.TimeoutExpired):
        return 2
'''
    assert rows(tmp_path, outer) == {}
    narrowed = outer.replace("(OSError, subprocess.TimeoutExpired)", "OSError")
    assert set(rows(tmp_path, narrowed)) == {(M0, MAIN, TE)}


# -------------------------------------------------- how far it follows ----
def test_a_callback_counts_where_it_is_passed(tmp_path):
    """`pool.map(lambda j: bench(j), jobs)`: the lambda's body is read at the
    map call, which is where its exception surfaces."""
    callback = HEAD + '''

def bench(j):
    subprocess.run(["vvp", j], check=True, timeout=1800)


def main():
    with pool() as p:
        rows = list(p.map(lambda j: bench(j), jobs))
'''
    got = rows(tmp_path, callback)
    assert set(got) == {(M0, MAIN, TE), (M0, MAIN, CPE)}
    assert got[(M0, MAIN, TE)] == [(M0, line_of(callback, "p.map("), "call"),
                                   (M0, line_of(callback, 'subprocess.run(["vvp", j]'), "child")]
    unguarded_main = '''def main():
    with pool() as p:
        rows = list(p.map(lambda j: bench(j), jobs))
'''
    guarded_main = '''def main():
    try:
        with pool() as p:
            rows = list(p.map(lambda j: bench(j), jobs))
    except (RuntimeError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return 2
'''
    assert unguarded_main in callback
    assert rows(tmp_path, callback.replace(unguarded_main, guarded_main)) == {}


def test_a_function_passed_by_name_counts_as_a_call(tmp_path):
    """`pool.map(bench, jobs)`: no Call node anywhere, and still where bench's
    exception surfaces."""
    src = HEAD + '''

def bench(j):
    subprocess.run(["vvp", j], check=True, timeout=1800)


def main():
    with pool() as p:
        rows = list(p.map(bench, jobs))
'''
    got = rows(tmp_path, src)
    assert set(got) == {(M0, MAIN, TE), (M0, MAIN, CPE)}
    assert got[(M0, MAIN, TE)][0] == (M0, line_of(src, "p.map(bench, jobs)"), "reference")
    assert got[(M0, MAIN, CPE)][-1][1] == line_of(src, 'subprocess.run(["vvp", j]')


def test_a_module_level_alias_is_read_where_it_is_called_not_where_it_is_bound(tmp_path):
    """r2_release's shape: `sha, _rel, _need, _json = r1r.sha, r1r._rel,
    r1r._need, r1r._json`. Binding a name calls nothing; the first version of
    this walk listed the binding itself as a path from the module."""
    callee = HEAD + '''import refusal


class Refused(refusal.Refused, RuntimeError):
    pass


def _need(p):
    raise Refused(f'missing input: {p}')
'''
    caller = HEAD + '''import m1 as r1r

sha, _rel, _need, _json = r1r.sha, r1r._rel, r1r._need, r1r._json


def main():
    _need('record.json')
'''
    got = rows(tmp_path, caller, callee)
    assert got == {(M0, MAIN, "m1.Refused"): [
        (M0, line_of(caller, "_need('record.json')"), "call"),
        ("fpga/m1.py", line_of(callee, "raise Refused"), "raise")]}
    guarded = caller.replace("def main():\n    _need('record.json')\n", '''def main():
    try:
        _need('record.json')
    except r1r.Refused:
        return 2
''')
    assert guarded != caller and rows(tmp_path, guarded, callee) == {}
    never_called = caller.replace("    _need('record.json')\n", "    return 0\n")
    assert never_called != caller and rows(tmp_path, never_called, callee) == {}


def test_the_main_block_is_an_entry_point(tmp_path):
    """publish_selected's shape: no main() function, the call under
    `if __name__ == "__main__":`."""
    block = HEAD + '''

def publish():
    subprocess.check_output(["git", "show", "abc:x"])


if __name__ == "__main__":
    publish()
'''
    assert set(rows(tmp_path, block)) == {(M0, MODULE, CPE)}


def test_a_module_level_call_is_an_entry_point_too(tmp_path):
    assert set(rows(tmp_path, HEAD + "\nsubprocess.run(['x'], timeout=5)\n")) == {(M0, MODULE, TE)}


def test_the_module_entry_does_not_repeat_what_main_already_reports(tmp_path):
    both = HEAD + '''

def main():
    subprocess.run(["x"], timeout=5)


if __name__ == "__main__":
    main()
'''
    assert set(rows(tmp_path, both)) == {(M0, MAIN, TE)}


def test_an_import_inside_a_function_binds_only_there(tmp_path):
    """#1573's shape: `import m2 as target` inside a def, and a sibling def
    that binds the same name to another module."""
    src = HEAD + '''

def a():
    import m1 as target
    target.sim()


def main():
    import m2 as target
    target.sim()
'''
    m1 = HEAD + "\n\ndef sim():\n    raise RuntimeError('m1')\n"
    m2 = HEAD + "\n\ndef sim():\n    subprocess.run(['x'], timeout=5)\n"
    assert set(rows(tmp_path, src, m1, m2)) == {(M0, MAIN, TE)}


# --------------------------------------------------------- what it cannot ----
def test_a_class_method_is_not_followed(tmp_path):
    """The stated limit: `MidiSession.feed` raises RuntimeError at
    midi_session.py:711 and has no row, because `obj.method()` is not resolved."""
    assert rows(tmp_path, HEAD + '''

class Session:
    def feed(self, packet):
        raise RuntimeError("bad packet")


def main():
    s = Session()
    s.feed(packet)
''') == {}


def test_a_raise_through_an_unresolvable_name_is_not_followed(tmp_path):
    assert rows(tmp_path, HEAD + '''

def main():
    raise err
''') == {}


def test_a_module_that_does_not_parse_is_refused(tmp_path):
    with pytest.raises(Refused, match="parse"):
        census(tmp_path, "def main(:\n", "x = 1\n")


def test_a_refusal_base_without_the_class_is_refused(tmp_path):
    with pytest.raises(Refused, match="Refused"):
        census(tmp_path, "x = 1\n", refusal="X = 1\n")


def test_too_few_modules_is_refused(tmp_path):
    """A walk over a partial tree would read exactly like one over the whole."""
    with pytest.raises(Refused, match="fewer"):
        census(tmp_path, "x = 1\n", min_modules=2)


def test_a_missing_root_is_refused(tmp_path):
    with pytest.raises(Refused, match="directory"):
        census_mod.census(tmp_path / "absent", refusal_path=tmp_path / "refusal.py", min_modules=1)


# --------------------------------------------- the idioms it cannot see ----
@pytest.mark.parametrize("idiom", [
    ("import contextlib\n\nwith contextlib.suppress(RuntimeError):\n"
     "    subprocess.run(['x'], timeout=5)\n"),
    ("from contextlib import suppress\n\nwith suppress(RuntimeError):\n"
     "    subprocess.run(['x'], timeout=5)\n"),
    "import sys\n\nsys.excepthook = lambda *a: None\n",
    "import threading\n\nthreading.excepthook = lambda *a: None\n",
], ids=["contextlib.suppress", "from-import-suppress", "sys.excepthook", "threading.excepthook"])
def test_an_idiom_the_walk_cannot_see_is_refused_rather_than_mis_censused(tmp_path, idiom):
    with pytest.raises(Refused, match="cannot see"):
        census(tmp_path, idiom)


# ---------------------------------------------------------------- --check ----
ONE_ROW = HEAD + "\n\ndef main():\n    subprocess.run(['x'], timeout=5)\n"


def test_check_is_green_only_when_every_row_is_named_and_every_name_is_live(tmp_path):
    res = census(tmp_path, ONE_ROW)
    ok, lines = census_mod.check(res, known=[])
    assert not ok and any("not named" in line for line in lines)
    ok, lines = census_mod.check(res, known=[f"{M0}={TE}"])
    assert ok, lines
    ok, lines = census_mod.check(res, known=[f"{M0}={TE}", f"{M0}={CPE}"])
    assert not ok and any("stale" in line for line in lines)
    assert not census_mod.check(res, known=["fpga/other.py=" + TE])[0]
    assert not census_mod.check(res, known=[f"{M0}={RE}"])[0]


def test_the_cli_exits_0_1_and_2(tmp_path, capsys):
    root = tree(tmp_path, ONE_ROW)
    base = ["--root", str(root / "fpga"), "--refusal", str(root / "refusal.py"),
            "--min-modules", "1"]
    assert census_mod.main([*base]) == 0
    assert f"{M0}  {MAIN}  {TE}" in capsys.readouterr().out
    assert census_mod.main([*base, "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["rows"][0]["raised"] == TE
    assert census_mod.main([*base, "--check"]) == 1
    assert census_mod.main([*base, "--check", "--known", f"{M0}={TE}"]) == 0
    assert census_mod.main([*base, "--check", "--known", f"{M0}={RE}"]) == 1
    absent = ["--root", str(tmp_path / "absent"), "--refusal", str(root / "refusal.py")]
    assert census_mod.main(absent) == 2
    assert "REFUSED" in capsys.readouterr().err


def test_the_rendered_census_says_which_tree_it_read(tmp_path):
    text = census_mod.render(census(tmp_path, ONE_ROW), behind=3)
    assert "child-failure escape census" in text and "3 commits behind origin/main" in text
    assert f"{M0}  {MAIN}  {TE}" in text


# ------------------------------------------------------------ this tree ----
_THIS_TREE = {}


def this_tree() -> dict:
    """The walk over this repository's fpga/, once per session. It raises the
    day a module uses an idiom it cannot see, so every test below also holds
    #1690's grep finding: none does."""
    if "res" not in _THIS_TREE:
        _THIS_TREE["res"] = census_mod.census()
    return _THIS_TREE["res"]


def test_this_tree_holds_at_least_the_modules_the_issue_counted():
    res = this_tree()
    assert res["modules"] >= 56, res["modules"]
    assert res["entries"] >= 40, res["entries"]


def test_the_four_rows_1690_repaired_stay_absent():
    """The gate for the fix, keyed on (module, entry, class) so it does not
    move when unrelated code shifts lines."""
    present = {(r["module"], r["entry"], r["class"]) for r in this_tree()["rows"]}
    for gone in (("fpga/verify_late_events.py", "main", TE),
                 ("fpga/bringup_arty.py", "main", TE),
                 ("fpga/build_selected.py", "main", CPE),
                 ("fpga/publish_selected.py", MODULE, CPE)):
        assert gone not in present, gone


def test_the_three_build_arty_tools_are_not_listed_for_a_vivado_timeout():
    """Known by reading them: each refuses its Vivado timeout where it is
    raised. The walk's second wrong answer listed exactly these three."""
    present = {(r["module"], r["entry"], r["class"]) for r in this_tree()["rows"]}
    for tool in ("build_arty", "build_arty_pads", "build_arty_sd"):
        assert (f"fpga/{tool}.py", "main", TE) not in present, tool
        assert (f"fpga/{tool}.py", "main", CPE) not in present, tool
