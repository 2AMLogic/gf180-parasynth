"""Controls for tools/check_collection_union.py (issue #1947).

The guard exists because `pytest tools/` on N workers, or split across
shards, only saves wall time if every test still runs, and the one existing
check that looks like it covers this -- tools/test_fpga_uncovered_workflow.py
-- pins the `fpga-uncovered` job, not the `tools` job. A test that falls out
of a worker leaves every check on main green.

So the load-bearing control is the one the issue names: omit ONE test from
the run's junit and the check must go red naming that test. Everything real
here comes from real pytest: each fixture project is written to a tmp dir and
run by `sys.executable -m pytest`, so the junit, the collection text and the
summary line are pytest's and not a hand-synthesised idea of them -- including
the hostile ids (a param holding `::`, spaces, `[]`) and every outcome class.
The only thing a hand builds is the defect.

    python -m pytest tools/test_check_collection_union.py -q
"""
from __future__ import annotations
import copy
import json
import pathlib
import re
import subprocess
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import check_collection_union as cu  # noqa: E402

ALPHA = '''\
import pytest


def test_plain_pass():
    pass


@pytest.mark.skip(reason="decorator skip")
def test_skip_decorator():
    pass


def test_skip_in_call():
    pytest.skip("call-phase skip")


@pytest.fixture
def skip_in_fixture():
    pytest.skip("setup-phase skip")


def test_skip_via_fixture(skip_in_fixture):
    pass


@pytest.mark.xfail(reason="expected to fail")
def test_xfail():
    assert False


@pytest.mark.xfail(reason="expected to fail")
def test_xpass():
    pass


@pytest.mark.parametrize("node", ["tools/test_x.py::test_y[a::b]", "two words", "x[y]", ""])
def test_param(node):
    pass


class TestGroup:
    def test_in_class(self):
        pass

    @pytest.mark.parametrize("n", [1])
    def test_in_class_param(self, n):
        pass
'''

ALPHA_FAILING = '''\

def test_fail():
    assert False


@pytest.mark.xfail(reason="strict", strict=True)
def test_xpass_strict():
    pass


@pytest.fixture
def boom():
    raise RuntimeError("fixture error")


def test_error_in_setup(boom):
    pass


@pytest.fixture
def leaks():
    yield
    raise RuntimeError("teardown error")


def test_teardown_error(leaks):
    pass
'''

NESTED = "def test_nested_ok():\n    pass\n"

#: (node id, outcome class) for the clean project, by pytest's own summary
#: line: "8 passed, 3 skipped, 1 xfailed, 1 xpassed".
CLEAN = {
    "pkg/sub/test_nested.py::test_nested_ok": "PASSED",
    "pkg/test_alpha.py::test_plain_pass": "PASSED",
    "pkg/test_alpha.py::test_skip_decorator": "SKIPPED",
    "pkg/test_alpha.py::test_skip_in_call": "SKIPPED",
    "pkg/test_alpha.py::test_skip_via_fixture": "SKIPPED",
    "pkg/test_alpha.py::test_xfail": "XFAILED",
    "pkg/test_alpha.py::test_xpass": "XPASSED",
    "pkg/test_alpha.py::test_param[tools/test_x.py::test_y[a::b]]": "PASSED",
    "pkg/test_alpha.py::test_param[two words]": "PASSED",
    "pkg/test_alpha.py::test_param[x[y]]": "PASSED",
    "pkg/test_alpha.py::test_param[]": "PASSED",
    "pkg/test_alpha.py::TestGroup::test_in_class": "PASSED",
    "pkg/test_alpha.py::TestGroup::test_in_class_param[1]": "PASSED",
}
#: (node id, outcome class) as JUNIT ALONE reads it. The non-strict xpass is
#: a bare <testcase>, so it reads PASSED here -- the tool's stated blind spot,
#: pinned as behaviour below; only the log's XPASS line can split it out.
FAILING = dict(CLEAN, **{
    "pkg/test_alpha.py::test_fail": "FAILED",
    "pkg/test_alpha.py::test_xpass_strict": "FAILED",
    "pkg/test_alpha.py::test_error_in_setup": "ERROR",
    "pkg/test_alpha.py::test_teardown_error": "PASSED",
    "pkg/test_alpha.py::test_xpass": "PASSED",
})


def write_project(root: pathlib.Path, failing: bool = False) -> None:
    (root / "pytest.ini").write_text("[pytest]\n")
    (root / "pkg").mkdir()
    (root / "pkg" / "__init__.py").write_text("")
    (root / "pkg" / "sub").mkdir()
    (root / "pkg" / "sub" / "__init__.py").write_text("")
    src = ALPHA + (ALPHA_FAILING if failing else "")
    (root / "pkg" / "test_alpha.py").write_text(src)
    (root / "pkg" / "sub" / "test_nested.py").write_text(NESTED)


def run_pytest(root: pathlib.Path, *args: str):
    r = subprocess.run([sys.executable, "-m", "pytest", "pkg", "-q",
                        "-p", "no:cacheprovider", *args],
                       cwd=root, capture_output=True, text=True)
    return r.returncode, r.stdout, r.stderr


def one_run(root: pathlib.Path, tag: str) -> dict:
    """A real pytest run plus a real by-directory collection of `root`."""
    junit = root / f"junit-{tag}.xml"
    rc, log, err = run_pytest(root, f"--junitxml={junit}", "-rX")
    assert not err, err
    rc_c, collect, err_c = run_pytest(root, "--collect-only")
    assert rc_c == 0 and not err_c, collect + err_c
    return dict(root=root, junit=junit, log=log, collect_text=collect, rc=rc)


@pytest.fixture(scope="module")
def clean(tmp_path_factory) -> dict:
    root = tmp_path_factory.mktemp("clean")
    write_project(root)
    run = one_run(root, "a")
    assert run["rc"] == 0, run["log"]
    return run


@pytest.fixture(scope="module")
def clean_baseline(clean) -> dict:
    """A SECOND real run of the same project: the parity check's baseline."""
    return one_run(clean["root"], "b")


@pytest.fixture(scope="module")
def failing(tmp_path_factory) -> dict:
    root = tmp_path_factory.mktemp("failing")
    write_project(root, failing=True)
    run = one_run(root, "f")
    assert run["rc"] == 1, run["log"]
    return run


def cases_of(run: dict) -> list[cu.Case]:
    return cu.load_junit(run["junit"])


def without(cases: list[cu.Case], nodeid: str) -> list[cu.Case]:
    """The injected defect the issue names: one test never ran."""
    key = cu.junit_key(nodeid)
    return [c for c in cases if (c.classname, c.name) != key]


def with_outcome(cases: list[cu.Case], nodeid: str, outcome: str) -> list[cu.Case]:
    out = copy.deepcopy(cases)
    key = cu.junit_key(nodeid)
    hit = [c for c in out if (c.classname, c.name) == key]
    assert len(hit) == 1, (nodeid, hit)
    hit[0].outcome = outcome
    hit[0].teardown_error = False
    return out


def adjust_summary(log: str, old: str, new: str) -> str:
    """A log that agrees with an edited junit: the summary line is rewritten
    to match, the way a REAL run that behaved differently would have printed
    it. This is what separates 'the junit was edited' from 'the test really
    changed outcome'."""
    lines = log.splitlines()
    idx = [i for i, l in enumerate(lines) if " passed" in l and " in " in l]
    assert len(idx) == 1, lines[-3:]
    assert old in lines[idx[0]], lines[idx[0]]
    lines[idx[0]] = lines[idx[0]].replace(old, new)
    return "\n".join(lines) + "\n"


# ---- the clean run, and the key the whole comparison rests on ---------------
def test_the_clean_run_passes(clean):
    r = cu.check(collect_text=clean["collect_text"], cases=cases_of(clean), log=clean["log"])
    assert r.verdict == cu.PASS, r.render()
    assert (r.expected, r.missing, r.extra, r.duplicate) == (13, [], [], [])
    assert r.counts == {"PASSED": 8, "XPASSED": 1, "SKIPPED": 3, "XFAILED": 1}, r.counts


def test_every_node_id_is_keyed_exactly_as_pytest_keyed_its_junit_row(clean):
    """The external referent for `junit_key`: pytest itself. Each collected id
    must land on exactly one real junit row, hostile ids included."""
    rows = {(c.classname, c.name) for c in cases_of(clean)}
    for nodeid in cu.parse_collect_output(clean["collect_text"]):
        assert cu.junit_key(nodeid) in rows, (nodeid, cu.junit_key(nodeid))
    assert cu.junit_key("pkg/test_alpha.py::test_param[tools/test_x.py::test_y[a::b]]") == (
        "pkg.test_alpha", "test_param[tools/test_x.py::test_y[a::b]]")
    assert cu.junit_key("pkg/test_alpha.py::TestGroup::test_in_class_param[1]") == (
        "pkg.test_alpha.TestGroup", "test_in_class_param[1]")
    assert cu.junit_key("pkg/test_alpha.py::test_param[]") == ("pkg.test_alpha", "test_param[]")
    with pytest.raises(cu.Refusal):
        cu.junit_key("pkg/test_alpha.py")


def test_every_outcome_class_reads_from_a_real_junit(failing):
    got = {}
    for nodeid, outcome in FAILING.items():
        hit = [c for c in cases_of(failing)
               if (c.classname, c.name) == cu.junit_key(nodeid)]
        assert len(hit) == 1, nodeid
        got[nodeid] = hit[0].outcome
        if nodeid.endswith("test_teardown_error"):
            assert hit[0].teardown_error is True
    assert got == FAILING, {k: (FAILING[k], got[k]) for k in got if got[k] != FAILING[k]}
    # ...and WITH the log the same row is XPASSED, so the parity class is right
    # wherever a log exists (it does on the hosted measurement).
    with_log = cu.check(collect_text=failing["collect_text"], cases=cases_of(failing),
                        log=failing["log"])
    assert with_log.counts["XPASSED"] == 1 and with_log.counts["PASSED"] == 9, with_log.counts


def test_the_parser_agrees_with_pytest_s_own_count_on_every_class(failing):
    """17 tests: 2 failed, 9 passed, 3 skipped, 1 xfailed, 1 xpassed, 2 errors
    (one a teardown error, which pytest counts as a pass AND an error). If any
    class were misread the summary cross-check would refuse here. The 9 was
    first written as 10 -- the xpass counted twice, by hand; pytest's own line
    is the oracle, not the hand."""
    r = cu.check(collect_text=failing["collect_text"], cases=cases_of(failing),
                 log=failing["log"])
    assert r.verdict == cu.PASS, r.render()
    assert r.summary == {"passed": 9, "failed": 2, "error": 2, "skipped": 3,
                         "xfailed": 1, "xpassed": 1}, r.summary
    assert r.counts == {"PASSED": 9, "XPASSED": 1, "SKIPPED": 3, "XFAILED": 1,
                        "FAILED": 2, "ERROR": 1}, r.counts


# ---- THE control: omit one test ---------------------------------------------
def test_control_omitting_one_test_turns_the_check_red_and_names_it(clean):
    for victim in ("pkg/sub/test_nested.py::test_nested_ok",
                   "pkg/test_alpha.py::test_param[tools/test_x.py::test_y[a::b]]",
                   "pkg/test_alpha.py::TestGroup::test_in_class_param[1]"):
        r = cu.check(collect_text=clean["collect_text"],
                     cases=without(cases_of(clean), victim), log=clean["log"])
        assert r.verdict == cu.FAIL, (victim, r.render())
        assert r.missing == [victim], (victim, r.missing)
        assert r.extra == [] and r.duplicate == [], (victim, r.render())
        assert victim in r.render(), "the report must name the omitted test"


def test_control_the_omitted_test_is_whatever_the_collection_says(clean):
    """Not a hand-picked victim: drop the k-th collected id for every k."""
    ids = cu.parse_collect_output(clean["collect_text"])
    for victim in ids:
        r = cu.check(collect_text=clean["collect_text"],
                     cases=without(cases_of(clean), victim))
        assert r.verdict == cu.FAIL and r.missing == [victim], (victim, r.render())


# ---- the other properties ---------------------------------------------------
def test_control_a_testcase_nobody_collected_is_flagged(clean):
    ghost = cu.Case("pkg.test_alpha", "test_never_collected", "PASSED", source="ghost")
    r = cu.check(collect_text=clean["collect_text"],
                 cases=cases_of(clean) + [ghost], log=clean["log"])
    assert r.verdict == cu.FAIL and r.missing == [] and r.duplicate == []
    assert len(r.extra) == 1 and "test_never_collected" in r.extra[0], r.render()


def test_control_a_collection_error_row_is_an_extra_not_a_pass(clean):
    """pytest writes a `classname="pytest" name="internal"` row for a file that
    failed to import. It is not a test and must not balance a missing one."""
    r = cu.check(collect_text=clean["collect_text"],
                 cases=without(cases_of(clean), "pkg/sub/test_nested.py::test_nested_ok")
                 + [cu.Case("pytest", "internal", "ERROR", source="x")])
    assert r.verdict == cu.FAIL
    assert r.missing == ["pkg/sub/test_nested.py::test_nested_ok"]
    assert len(r.extra) == 1


def test_control_a_test_that_ran_in_two_shards_is_flagged(clean):
    """The two-shard split's own failure: both shards took the same test."""
    cases = cases_of(clean)
    twice = cases[:4]
    names = {cu.junit_key(n) for n in cu.parse_collect_output(clean["collect_text"])}
    expected = sorted(n for n in cu.parse_collect_output(clean["collect_text"])
                      if cu.junit_key(n) in {(c.classname, c.name) for c in twice})
    r = cu.check(collect_text=clean["collect_text"], cases=cases + twice, log=clean["log"])
    assert r.verdict == cu.FAIL and r.missing == [] and r.extra == []
    assert sorted(r.duplicate) == expected, r.render()
    assert names  # the keys above were drawn from real ids


def test_control_an_outcome_that_changed_is_flagged_against_the_baseline(clean, clean_baseline):
    victim = "pkg/test_alpha.py::test_plain_pass"
    cand = with_outcome(cases_of(clean), victim, "SKIPPED")
    log = adjust_summary(clean["log"], "8 passed, 3 skipped", "7 passed, 4 skipped")
    r = cu.check(collect_text=clean["collect_text"], cases=cand, log=log,
                 baseline_cases=cases_of(clean_baseline), baseline_log=clean_baseline["log"])
    assert r.verdict == cu.FAIL, r.render()
    assert [(d.nodeid, d.baseline, d.candidate) for d in r.outcome_diffs] == [
        (victim, "PASSED", "SKIPPED")], r.render()


def test_control_a_baseline_that_is_itself_incomplete_is_refused(clean):
    hole = without(cases_of(clean), "pkg/test_alpha.py::test_xfail")
    r = cu.check(collect_text=clean["collect_text"], cases=cases_of(clean), log=clean["log"],
                 baseline_cases=hole, baseline_log=clean["log"])
    assert r.verdict == cu.REFUSED and r.property == "baseline", r.render()
    assert "test_xfail" in r.reason


def test_the_same_run_against_a_second_real_run_has_no_outcome_differences(
        clean, clean_baseline):
    r = cu.check(collect_text=clean["collect_text"], cases=cases_of(clean), log=clean["log"],
                 baseline_cases=cases_of(clean_baseline), baseline_log=clean_baseline["log"])
    assert r.verdict == cu.PASS and r.outcome_diffs == [], r.render()


# ---- refusals: no verdict, never a pass --------------------------------------
def test_control_a_junit_edited_against_its_own_log_is_refused(clean):
    """Same edit as the outcome control, but the log is left alone: the junit
    now disagrees with pytest's own count, which is not a verdict about the
    tests, it is a corrupt record."""
    cand = with_outcome(cases_of(clean), "pkg/test_alpha.py::test_plain_pass", "SKIPPED")
    r = cu.check(collect_text=clean["collect_text"], cases=cand, log=clean["log"])
    assert r.verdict == cu.REFUSED and r.property == "summary", r.render()
    assert "passed" in r.reason and "8" in r.reason and "7" in r.reason, r.reason


def test_control_collection_defects_are_refused(clean):
    text = clean["collect_text"]
    ids = cu.parse_collect_output(text)
    good = dict(cases=cases_of(clean), log=clean["log"])

    truncated = text.replace(ids[3] + "\n", "")          # one id line lost
    r = cu.check(collect_text=truncated, **good)
    assert r.verdict == cu.REFUSED and r.property == "collection", r.render()
    assert "13" in r.reason and "12" in r.reason

    miscounted = text.replace("13 tests collected", "12 tests collected")
    assert cu.check(collect_text=miscounted, **good).verdict == cu.REFUSED

    erroring = text.replace(ids[0] + "\n", "ERROR collecting pkg/sub/test_nested.py\n")
    assert cu.check(collect_text=erroring, **good).verdict == cu.REFUSED

    assert cu.check(collect_text="", **good).verdict == cu.REFUSED
    assert cu.check(collect_text="13 tests collected in 0.01s\n", **good).verdict == cu.REFUSED


def test_control_an_empty_or_unparseable_junit_is_refused(tmp_path, clean):
    (tmp_path / "empty.xml").write_text(
        '<?xml version="1.0"?><testsuites><testsuite tests="0"/></testsuites>')
    (tmp_path / "notxml.xml").write_text("this is not xml")
    (tmp_path / "notpytest.xml").write_text(
        '<?xml version="1.0"?><testsuites><testsuite><testcase foo="bar"/></testsuite></testsuites>')
    for name in ("empty.xml", "notxml.xml", "notpytest.xml"):
        with pytest.raises(cu.Refusal):
            cu.load_junit(tmp_path / name)
    r = cu.check(collect_text=clean["collect_text"], cases=[], log=clean["log"])
    assert r.verdict == cu.REFUSED and r.property == "junit", r.render()


def test_control_an_ambiguous_key_is_refused_not_guessed():
    """`a.b/test_x.py` and `a/b/test_x.py` key alike. Matching by construction
    would silently bind both to one junit row; the tool must refuse instead."""
    with pytest.raises(cu.Refusal) as e:
        cu.build(["a.b/test_x.py::test_y", "a/b/test_x.py::test_y"], [])
    assert "ambiguous" in str(e.value)


def test_collect_ignores_pytest_addopts_so_an_inherited_k_cannot_shrink_it(clean, monkeypatch):
    ids = cu.parse_collect_output(clean["collect_text"])
    monkeypatch.setenv("PYTEST_ADDOPTS", "-k test_plain_pass")     # would leave 1
    assert cu.collect(clean["root"], ("pkg",)) == ids


def test_collect_refuses_when_collection_itself_fails(tmp_path):
    write_project(tmp_path)
    (tmp_path / "pkg" / "test_broken.py").write_text("def test_x(:\n")
    with pytest.raises(cu.Refusal) as e:
        cu.collect(tmp_path, ("pkg",))
    assert "collection" in str(e.value)


# ---- the control matrix, and its own guard -----------------------------------
PROPERTIES = ("missing", "extra", "duplicate", "outcome", "summary", "collection",
              "junit", "key")


def test_control_matrix_caught_on_its_intended_property(clean, clean_baseline):
    rows = cu.control(clean["collect_text"], cases_of(clean), clean["log"],
                      baseline_cases=cases_of(clean_baseline),
                      baseline_log=clean_baseline["log"])
    by_name = {r.name: r for r in rows}
    assert by_name["clean"].verdict == cu.PASS
    intended = {"omit-one": "missing", "extra-one": "extra", "ran-twice": "duplicate",
                "outcome-changed": "outcome", "junit-edited": "summary",
                "collection-truncated": "collection", "junit-empty": "junit",
                "key-ambiguous": "key", "baseline-incomplete": "baseline"}
    for name, prop in intended.items():
        assert by_name[name].caught, by_name[name]
        assert by_name[name].moved == [prop], (name, by_name[name])   # only that one
    text = cu.render_matrix(rows, PROPERTIES)
    for line in text.splitlines():
        if line.startswith("omit-one"):
            assert "MOVED" in line.split()[1] and "BLIND" in line.split()[2]
    assert "NOT EVALUATED" not in text


def test_control_matrix_goes_red_when_a_guard_is_disabled(clean, monkeypatch):
    """Rule 5 condition 3: the control must fail through the intended property.
    Delete the missing-test comparison and the omit-one control must be
    reported NOT caught -- not quietly still caught by something else. No log
    is passed here on purpose: with one, the summary cross-check would also
    notice, and this test is about the property that is supposed to."""
    monkeypatch.setattr(cu, "missing_tests", lambda expected, seen: [])
    rows = cu.control(clean["collect_text"], cases_of(clean))
    by_name = {r.name: r for r in rows}
    assert not by_name["omit-one"].caught, by_name["omit-one"]
    assert by_name["omit-one"].verdict == cu.PASS, by_name["omit-one"]
    assert cu.control_exit(rows) == 1
    text = cu.render_matrix(rows, PROPERTIES)
    assert text.count("NOT CAUGHT") == 1, text
    assert text.count("NOT EVALUATED") == 3, text      # outcome-changed, junit-edited, baseline-incomplete
    assert cu.control_exit(cu.control(clean["collect_text"], cases_of(clean), clean["log"])) == 1


def test_control_matrix_refuses_to_claim_a_property_it_could_not_exercise(clean):
    rows = cu.control(clean["collect_text"], cases_of(clean), clean["log"])
    by_name = {r.name: r for r in rows}
    assert not by_name["outcome-changed"].caught
    assert by_name["outcome-changed"].verdict == "NOT EVALUATED"
    assert "baseline" in by_name["outcome-changed"].reason
    assert cu.control_exit(rows) == 1


# ---- the command line --------------------------------------------------------
def test_cli_exit_codes(clean, tmp_path):
    expected = tmp_path / "collect.txt"
    expected.write_text(clean["collect_text"])
    log = tmp_path / "pytest.log"
    log.write_text(clean["log"])
    base = ["--expected", str(expected), "--junit", str(clean["junit"]),
            "--pytest-log", str(log)]
    assert cu.main(base) == 0
    assert cu.main(base + ["--json", str(tmp_path / "r.json")]) == 0
    rec = json.loads((tmp_path / "r.json").read_text())
    assert rec["verdict"] == "PASS" and rec["expected"] == 13 and rec["missing"] == []

    hole = tmp_path / "hole.xml"
    xml = clean["junit"].read_text()
    victim_key = cu.junit_key("pkg/test_alpha.py::test_param[two words]")
    one = re.sub(r'<testcase classname="%s" name="%s"[^>]*?(/>|>.*?</testcase>)'
                 % (re.escape(victim_key[0]), re.escape(victim_key[1])), "", xml, count=1)
    assert one != xml, "the regex did not find the victim's testcase"
    hole.write_text(one)
    assert cu.main(["--expected", str(expected), "--junit", str(hole),
                    "--pytest-log", str(log)]) == 1
    assert cu.main(["--expected", str(expected), "--junit", str(tmp_path / "absent.xml")]) == 2


def test_cli_control_subcommand(clean, clean_baseline, tmp_path, capsys):
    expected = tmp_path / "collect.txt"
    expected.write_text(clean["collect_text"])
    logs = {}
    for tag, run in (("a", clean), ("b", clean_baseline)):
        logs[tag] = tmp_path / f"log-{tag}.txt"
        logs[tag].write_text(run["log"])
    argv = ["control", "--expected", str(expected),
            "--junit", str(clean["junit"]), "--pytest-log", str(logs["a"]),
            "--baseline-junit", str(clean_baseline["junit"]),
            "--baseline-log", str(logs["b"])]
    assert cu.main(argv) == 0
    out = capsys.readouterr().out
    assert "MOVED" in out and "BLIND" in out and "NOT CAUGHT" not in out


def test_cli_refuses_unknown_inputs(clean, tmp_path):
    assert cu.main([]) == 2
    assert cu.main(["--junit", str(clean["junit"])]) == 2          # no expected side
    assert cu.main(["--expected", str(tmp_path / "nope.txt"),
                    "--junit", str(clean["junit"])]) == 2


# ---- a missing precondition is REFUSED (2), never a traceback with FAIL's exit
# code (#2152). main() guarded input loading only: control() sat outside every
# guard, and so did one xpass_ids() call in check(), so a bad --pytest-log or
# --expected reached a shell as "Traceback ... rc=1" -- the code that means a
# control was not caught.
def test_check_refuses_an_xpass_line_naming_no_collected_test(clean, tmp_path, capsys):
    """A log whose ``XPASS`` line names an id the collection never listed
    cannot be bound to this run. It used to escape check() as an exception;
    the baseline_log guard already refuses the same input, and both must
    read the same way."""
    bad = clean["log"] + "XPASS pkg/test_alpha.py::test_never_collected - expected to fail\n"
    r = cu.check(collect_text=clean["collect_text"], cases=cases_of(clean), log=bad)
    assert r.verdict == cu.REFUSED and r.property == "summary", r.render()
    assert r.expected == 13 and "test_never_collected" in r.reason, r.reason
    base = cu.check(collect_text=clean["collect_text"], cases=cases_of(clean),
                    log=clean["log"], baseline_cases=cases_of(clean), baseline_log=bad)
    assert base.verdict == cu.REFUSED and base.property == "summary", base.render()

    expected = tmp_path / "collect.txt"
    expected.write_text(clean["collect_text"])
    log = tmp_path / "bad-xpass.log"
    log.write_text(bad)
    assert cu.main(["--expected", str(expected), "--junit", str(clean["junit"]),
                    "--pytest-log", str(log)]) == 2
    assert capsys.readouterr().out.startswith("REFUSED [summary]:")


def test_control_refuses_a_log_with_no_summary_line(clean, clean_baseline, tmp_path, capsys):
    """The issue's repro: every input real except --pytest-log, a one-line
    file with no summary line. _rewrite_summary raises Refusal inside
    control(), which was one call past main()'s guard."""
    expected = tmp_path / "collect.txt"
    expected.write_text(clean["collect_text"])
    bad, base_log = tmp_path / "bad.log", tmp_path / "base.log"
    bad.write_text("this log has no summary line\n")
    base_log.write_text(clean_baseline["log"])
    argv = ["control", "--expected", str(expected), "--junit", str(clean["junit"]),
            "--pytest-log", str(bad), "--baseline-junit", str(clean_baseline["junit"]),
            "--baseline-log", str(base_log)]
    assert cu.main(argv) == 2
    out = capsys.readouterr().out
    assert out.startswith("REFUSED [summary]:"), out
    assert "Traceback" not in out and "NOT CAUGHT" not in out


def test_control_refuses_a_broken_collection_or_an_ambiguous_key(clean, tmp_path, capsys):
    """control() parses --expected and builds the key table itself, outside
    check(); each refusal used to escape main() as a traceback."""
    expected = tmp_path / "collect.txt"
    argv = ["control", "--expected", str(expected), "--junit", str(clean["junit"])]
    ids = cu.parse_collect_output(clean["collect_text"])
    expected.write_text(clean["collect_text"].replace(ids[3] + "\n", ""))   # one id line lost
    assert cu.main(argv) == 2
    assert capsys.readouterr().out.startswith("REFUSED [collection]:")

    expected.write_text("a.b/test_x.py::test_y\na/b/test_x.py::test_y\n"
                        "2 tests collected in 0.01s\n")
    assert cu.main(argv) == 2
    assert capsys.readouterr().out.startswith("REFUSED [key]:")


def test_control_still_exits_1_when_a_control_is_genuinely_not_caught(
        clean, tmp_path, capsys, monkeypatch):
    """The guard must not turn an uncaught control into REFUSED: with the
    missing-test comparison disabled (as in
    test_control_matrix_goes_red_when_a_guard_is_disabled) the matrix prints
    NOT CAUGHT and the exit code stays 1."""
    monkeypatch.setattr(cu, "missing_tests", lambda expected, seen: [])
    expected = tmp_path / "collect.txt"
    expected.write_text(clean["collect_text"])
    assert cu.main(["control", "--expected", str(expected),
                    "--junit", str(clean["junit"])]) == 1
    assert capsys.readouterr().out.count("NOT CAUGHT") == 1


def test_the_shipped_cli_control_exits_2_without_a_traceback(clean, clean_baseline, tmp_path):
    """What a shell sees. The tests above drive main(); this drives
    ``python tools/check_collection_union.py`` and reads the exit status and
    stderr, which is where the issue's traceback was."""
    expected, bad, base_log = (tmp_path / n for n in ("collect.txt", "bad.log", "base.log"))
    expected.write_text(clean["collect_text"])
    bad.write_text("this log has no summary line\n")
    base_log.write_text(clean_baseline["log"])
    r = subprocess.run(
        [sys.executable, str(pathlib.Path(cu.__file__)), "control",
         "--expected", str(expected), "--junit", str(clean["junit"]),
         "--pytest-log", str(bad), "--baseline-junit", str(clean_baseline["junit"]),
         "--baseline-log", str(base_log)],
        capture_output=True, text=True)
    assert r.returncode == 2, r.stdout + r.stderr
    assert "Traceback" not in r.stderr, r.stderr
    assert "REFUSED [summary]:" in r.stdout, r.stdout
