"""Controls for `tools/refusal_exit_shape_scan.py` (#1807): every shape the
scanner decides is pinned against planted sources where the answer is known
by construction, and the scanner's own preconditions are driven to REFUSE.

THE DEFECT.  A tool prints `REFUSED: ...` because a precondition failed and
leaves with status 1, which `tools/exit_contract.py` reserves for a
measurement that DISAGREED and `tools/run_all.py` renders as a RESULT.  The
label and the status say opposite things.

THE KNOWN ANSWERS, independent of anything this repository models: the Python
interpreter.  `raise SystemExit("REFUSED: x")` exits 1 with the string on
stderr, `raise SystemExit(2)` exits 2 silently, and `print(...); return 1`
under `sys.exit(main())` exits 1.  Those three are run as real subprocesses
below (class `TestInterpreterKnownAnswers`), so the scanner's claim about
which shapes exit 1 is grounded in the interpreter and not in the scanner.

WHAT EACH CONTROL PLANTS.  The shapes are the sites the issue's 6-line text
window found on origin/main @ 03148399 -- the 17 true positives and its 6
false positives -- plus the two the window MISSED (a flag set beside the
print and read 8 lines later; `raise SystemExit("REFUSED ...")`), plus the
UNREAD shapes the tree actually holds.  A misclassification cannot ship
silently: if the scanner is taught to step over a false positive in a way
that also steps over a true one, the pair of controls here goes red.

WRONG BEFORE IT WAS RIGHT, recorded because each wrong result looked like an
answer:
  1. The scanner's own first draft read the enclosing-block chain
     outermost-first, so for `if bad: print("REFUSED ..."); return 1` it read
     the function's trailing `return 0` and found NOTHING.  Its self-control
     REFUSED on the first run, before any count was printed
     (`test_self_control_refuses_when_the_planted_defect_is_not_found` is
     that failure, kept).
  2. Its next draft walked nested `def` bodies from the module too, so every
     site in a function was reported twice, once as `<module>`
     (`test_a_site_is_reported_once_not_once_per_enclosing_def`).
  3. Its first sibling-module resolver read `NAME = <int>` only; this tree
     declares its exit statuses by tuple (`OK, FAIL, REFUSED = 0, 1, 2`) and
     through other modules (`= rp.OK, rp.FAIL, rp.REFUSED`), so 25 correct
     `return REFUSED` sites sat in UNREAD and read like open questions
     (`TestSiblingModuleResolution`).
"""
from __future__ import annotations

import ast
import pathlib
import subprocess
import sys
import textwrap

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import refusal_exit_shape_scan as scan_mod                     # noqa: E402

# fixed, so the unit controls do not depend on the tree's contract
CONTRACT = {"PASS": 0, "FAIL": 1, "REFUSED": 2, "REFUSED_ALTERNATE": 3, "RUN_WITH_RSS_REFUSED": 126}
NO_EVIDENCE = frozenset({2, 3, 126})


def scan(src: str, **kw):
    return scan_mod.scan_source(textwrap.dedent(src), CONTRACT, NO_EVIDENCE, "<planted>", **kw)


def hits(src: str, **kw):
    return scan(src, **kw)["hits"]


def unread(src: str, **kw):
    return scan(src, **kw)["unread"]


# ---------------------------------------------------------- the interpreter
class TestInterpreterKnownAnswers:
    """Which of these shapes exits 1 is the interpreter's answer, run for real."""

    def _py(self, body: str):
        return subprocess.run([sys.executable, "-c", textwrap.dedent(body)],
                              capture_output=True, text=True)

    def test_systemexit_carrying_a_string_exits_1_and_prints_it(self):
        r = self._py('raise SystemExit("REFUSED: x")')
        assert (r.returncode, r.stderr.strip()) == (1, "REFUSED: x")

    def test_systemexit_carrying_an_int_exits_that_int_silently(self):
        r = self._py("raise SystemExit(2)")
        assert (r.returncode, r.stderr) == (2, "")

    def test_print_then_return_1_under_sys_exit_main_exits_1(self):
        r = self._py("""
            def main():
                print("REFUSED: x")
                return 1
            sys.exit(main())
        """)
        assert r.returncode == 1

    def test_true_equals_one_is_why_a_bool_return_must_not_be_read_as_an_exit(self):
        """The trap behind two of the issue's six false positives, as a fact."""
        assert True == 1
        assert isinstance(True, int)


# --------------------------------------------------- the issue's true shape
class TestPrintThenExitOne:
    def test_same_block_is_a_hit(self):
        h = hits("""
            def main(ok):
                if not ok:
                    print("REFUSED: box preconditions failed")
                    return 1
                return 0
        """)
        assert [(x["shape"], x["status"], x["func"]) for x in h] == [("print-exit", 1, "main")]

    def test_the_prints_block_falls_through_to_the_enclosing_return(self):
        h = hits("""
            def main(ok):
                if not ok:
                    print("REFUSED: box preconditions failed")
                return 1
        """)
        assert [x["status"] for x in h] == [1]

    def test_fallthrough_crosses_two_levels(self):
        h = hits("""
            def main(rows):
                for r in rows:
                    if r:
                        print("REFUSED: a row refused")
                return 1
        """)
        assert [x["status"] for x in h] == [1]

    def test_a_compound_statement_with_no_exit_between_is_stepped_over(self):
        h = hits("""
            def main(ok, verbose):
                if not ok:
                    print("REFUSED: x")
                    if verbose:
                        print("details")
                    return 1
        """)
        assert [x["status"] for x in h] == [1]

    @pytest.mark.parametrize("terminator", ["sys.exit(1)", "raise SystemExit(1)"])
    def test_exit_1_by_systemexit_after_the_print_is_a_hit(self, terminator):
        h = hits(f"""
            def main(ok):
                if not ok:
                    print("REFUSED: x")
                    {terminator}
        """)
        assert [x["status"] for x in h] == [1]

    @pytest.mark.parametrize("label", ['"REFUSED: x"', '"  REFUSED: x"', '"\\nREFUSED: x"',
                                        'f"REFUSED: {e}"', '"REFUSED: %s" % e',
                                        '"REFUSED: {}".format(e)', '"REFUSED: " + name'])
    def test_every_string_form_that_leads_with_REFUSED_is_read(self, label):
        h = hits(f"""
            def main(e, name):
                print({label})
                return 1
        """)
        assert [x["status"] for x in h] == [1]

    @pytest.mark.parametrize("label", ['"REFUSE: x"', '"was REFUSED"', '"refused: x"', 'msg',
                                        'f"{e}: REFUSED"'])
    def test_a_label_that_does_not_lead_with_REFUSED_is_not_read(self, label):
        assert hits(f"""
            def main(e, msg):
                print({label})
                return 1
        """) == []

    def test_stderr_and_keyword_arguments_do_not_hide_the_label(self):
        h = hits("""
            def main(ok):
                if not ok:
                    print("REFUSED: x", file=sys.stderr, flush=True)
                    return 1
        """)
        assert [x["status"] for x in h] == [1]


# ------------------------------------------- the corrected shape (negative)
class TestTheCorrectedShapeIsNotAHit:
    """tools/cymbal_low_tail.py: `return REFUSED`, the contract's constant."""

    def test_return_refused_imported_from_the_contract(self):
        src = """
            from exit_contract import REFUSED

            def main(ok):
                if not ok:
                    print("REFUSED: the measurement's own controls do not pass")
                    return REFUSED
                return 0
        """
        assert hits(src) == [] and unread(src) == []

    def test_exit_contract_attribute_form(self):
        assert hits("""
            import exit_contract

            def main(ok):
                if not ok:
                    print("REFUSED: x")
                    return exit_contract.REFUSED
        """) == []

    def test_same_file_module_int(self):
        assert hits("""
            REFUSED = 2

            def main(ok):
                if not ok:
                    print("REFUSED: x")
                    return REFUSED
        """) == []

    @pytest.mark.parametrize("status", [2, 3])
    def test_literal_refusal_statuses(self, status):
        assert hits(f"""
            def main(ok):
                if not ok:
                    print("REFUSED: x")
                    return {status}
        """) == []

    def test_raise_systemexit_with_a_refusal_int(self):
        assert hits("""
            def main(ok):
                if not ok:
                    print("REFUSED: x")
                    raise SystemExit(2)
        """) == []


# ------------------------------------------ the issue's six false positives
class TestTheIssuesSixFalsePositivesAreNotHits:
    """Each is the shape of a site the 6-line window listed on origin/main."""

    def test_check_doc_claims__return_2_then_another_branchs_return_1(self):
        src = """
            def main(scope_err, n):
                if scope_err:
                    print(f"REFUSED  cannot audit for markers outside the scanned set: {scope_err}",
                          file=sys.stderr)
                if n[0]:
                    return 1
                if n[1] or scope_err:
                    return 2
                return 0
        """
        assert hits(src) == []
        assert [u["why"] for u in unread(src)] == [scan_mod.WHY_FLAG]

    def test_check_m1a_selection__except_body_prints_then_returns_2(self):
        src = """
            def main(a, problems):
                try:
                    x = a
                except OSError as exc:
                    print(f"REFUSED: {exc}")
                    return 2
                for p in problems:
                    print("MISMATCH:", p)
                if problems:
                    return 1
                return 0
        """
        assert hits(src) == [] and unread(src) == []

    def test_check_workflows__refused_and_no_bad_returns_2(self):
        src = """
            def main(refused, bad):
                if refused and not bad:
                    print(f"REFUSED: {refused}", file=sys.stderr)
                    return 2
                if bad:
                    return 1
                return 0
        """
        assert hits(src) == [] and unread(src) == []

    def test_preparation_contract__corpus_missing_returns_2(self):
        src = """
            def main(refdir, refused):
                if not refdir:
                    print(f"REFUSED: reference corpus not at {refdir}")
                    return 2
                if refused:
                    return 1
                return 0
        """
        assert hits(src) == [] and unread(src) == []

    @pytest.mark.parametrize("predicate", ["True", "False", "None"])
    def test_marker_controls__a_predicate_returns_a_bool_or_none(self, predicate):
        """`True == 1`, which is how the issue's window counted these two."""
        src = f"""
            def already_satisfied(matched, pattern):
                if matched:
                    print(f"REFUSED: {{', '.join(matched)}} already contains a line matching "
                          f"{{pattern!r}}", file=sys.stderr)
                    return {predicate}
                return False
        """
        assert hits(src) == []
        assert [u["why"] for u in unread(src)] == [scan_mod.WHY_HELPER]

    def test_the_same_predicate_returning_1_IS_a_hit_so_the_control_discriminates(self):
        assert [x["status"] for x in hits("""
            def refuse(matched):
                if matched:
                    print("REFUSED: x")
                    return 1
                return 0
        """)] == [1]


# ------------------------------------------------- the two the window missed
class TestTheTwoShapesTheWindowMissed:
    def test_a_flag_set_beside_the_print_and_read_by_a_later_conditional_exit(self):
        """tools/dsp_dpreg_sim.py:369 on origin/main: `ok = False` inside a
        loop, `if not ok: return 1` after it."""
        h = hits("""
            def main(lines):
                ok = True
                for line in lines:
                    if line:
                        print(f"REFUSED: hash mismatch {line}")
                        ok = False
                if not ok:
                    return 1
                return 0
        """)
        assert [(x["status"], x["line"]) for x in h] == [(1, 6)]

    def test_a_flag_read_through_a_conditional_expression_eight_lines_later(self):
        """tools/probes/dc_t20_gate_qualification.py:294-321 on origin/main:
        six prints, each `ok = False`, then `return 0 if ok else 1`."""
        h = hits("""
            def main(a, b, c):
                ok = True
                if a:
                    print("REFUSED: one")
                    ok = False
                if b:
                    print("REFUSED: two")
                    ok = False
                print("QUALIFIED" if ok else "NOT QUALIFIED")
                if c:
                    print("x")
                return 0 if ok else 1
        """)
        assert [x["line"] for x in h] == [5, 8]

    def test_the_same_conditional_expression_reading_2_for_the_flag_is_not_a_hit(self):
        assert hits("""
            def main(ok):
                if not ok:
                    print("REFUSED: x")
                    ok = False
                return 0 if ok else 2
        """) == []

    def test_a_name_assigned_a_refusal_int_beside_the_print_resolves(self):
        src = """
            def main(exc):
                try:
                    x = exc
                except Refused as e:
                    print(f"REFUSED: {e}")
                    code = 2
                return code
        """
        assert hits(src) == [] and unread(src) == []

    def test_a_tuple_assignment_of_a_refusal_int_resolves(self):
        assert hits("""
            from exit_contract import REFUSED

            def main(why):
                try:
                    x = why
                except Refused as e:
                    print(f"REFUSED  {e}")
                    rec, code = report(e), REFUSED
                return code
        """) == []

    def test_a_conditional_exit_on_a_test_the_path_did_not_set_is_unread_not_a_hit(self):
        src = """
            def main(exc, expect):
                try:
                    x = exc
                except Refused as e:
                    print(f"REFUSED: {e}")
                    code = 2
                if not expect:
                    return code
                return 1
        """
        assert hits(src) == []
        assert [u["why"] for u in unread(src)] == [scan_mod.WHY_FLAG]

    def test_a_conditional_exit_between_the_print_and_a_return_1_is_unread(self):
        src = """
            def main(x):
                print("REFUSED: x")
                if x:
                    return 0
                return 1
        """
        assert hits(src) == [] and [u["why"] for u in unread(src)] == [scan_mod.WHY_FLAG]

    def test_systemexit_carrying_a_refused_string_is_a_hit(self):
        for call in ('SystemExit("REFUSED: x")', 'SystemExit(f"REFUSED: {e}")',
                     'sys.exit("REFUSED: x")', 'exit("REFUSED: x")',
                     'builtins.SystemExit("REFUSED: x")',
                     'SystemExit("REFUSED: " + name)', 'SystemExit("\\nREFUSED: x")'):
            h = hits(f"""
                def main(e, name):
                    raise {call}
            """)
            assert [(x["shape"], x["status"]) for x in h] == [("systemexit-str", 1)], call

    @pytest.mark.parametrize("call", ["SystemExit(2)", "SystemExit(1)", 'SystemExit("x")',
                                      'SystemExit(f"{e}")', "ValueError('REFUSED: x')",
                                      "SystemExit"])
    def test_other_raises_are_that_shapes_business(self, call):
        assert hits(f"""
            def main(e):
                raise {call}
        """) == []

    def test_a_print_falling_through_to_a_string_systemexit_reads_1_by_both_shapes(self):
        """Two statements, two hits, nothing UNREAD: the print's own path
        exits 1 (the interpreter's rule for a string SystemExit, run in
        TestInterpreterKnownAnswers) and the raise is its own shape."""
        src = """
            def main(ok):
                if ok:
                    print("REFUSED: x")
                raise SystemExit(f"REFUSED: {ok}")
        """
        assert [(x["line"], x["shape"], x["status"]) for x in hits(src)] == [
            (4, "print-exit", 1), (5, "systemexit-str", 1)]
        assert unread(src) == []

    def test_sys_exit_of_none_is_0_and_of_a_bool_is_not_read(self):
        assert hits("""
            def main(ok):
                if ok:
                    print("REFUSED: x")
                sys.exit(None)
        """) == [] and unread("""
            def main(ok):
                if ok:
                    print("REFUSED: x")
                sys.exit(None)
        """) == [dict(file="<planted>", func="main", line=4, why=scan_mod.WHY_ZERO)]
        assert [u["why"] for u in unread("""
            def main(ok):
                if ok:
                    print("REFUSED: x")
                sys.exit(True)
        """)] == [scan_mod.WHY_NAME]


# ------------------------------------------------------- what stays UNREAD
class TestUnreadIsReportedNotGuessed:
    def test_a_break_or_continue_redirects_the_flow(self):
        src = """
            def main(items):
                for it in items:
                    try:
                        x = it
                    except Refused as e:
                        print(f"REFUSED: {e}")
                        continue
                return 1
        """
        assert hits(src) == [] and [u["why"] for u in unread(src)] == [scan_mod.WHY_JUMP]

    def test_a_reporting_helpers_bare_return(self):
        src = """
            def print_report(result):
                if result["status"] == "REFUSED":
                    print(f"REFUSED: {result['why']}")
                    return
        """
        assert hits(src) == [] and [u["why"] for u in unread(src)] == [scan_mod.WHY_HELPER]

    def test_a_return_0_after_the_label_is_unread_not_a_hit(self):
        """A helper's value, or a refusal rendered PASS: this scan decides neither."""
        src = """
            def report(ok):
                if not ok:
                    print("REFUSED: x")
                return 0
        """
        assert hits(src) == [] and [u["why"] for u in unread(src)] == [scan_mod.WHY_ZERO]

    def test_an_unresolvable_name_is_unread(self):
        src = """
            def main(rc):
                print("REFUSED: x")
                return rc
        """
        assert hits(src) == [] and [u["why"] for u in unread(src)] == [scan_mod.WHY_NAME]

    def test_a_function_that_ends_without_an_exit(self):
        src = """
            def say(why):
                print(f"REFUSED: {why}")
        """
        assert hits(src) == [] and [u["why"] for u in unread(src)] == [scan_mod.WHY_END]

    def test_a_conditional_expression_on_an_unknown_test_is_unread(self):
        src = """
            def main(ok):
                print("REFUSED: x")
                return 0 if ok else 1
        """
        assert hits(src) == [] and [u["why"] for u in unread(src)] == [scan_mod.WHY_NAME]


# ----------------------------------------------------- structural controls
class TestStructure:
    def test_a_site_is_reported_once_not_once_per_enclosing_def(self):
        h = hits("""
            def outer():
                def inner(ok):
                    if not ok:
                        print("REFUSED: x")
                        return 1
                return inner
        """)
        assert [(x["func"], x["line"]) for x in h] == [("inner", 5)]

    def test_module_level_code_is_scanned_too(self):
        h = hits("""
            import sys
            if not sys.argv[1:]:
                print("REFUSED: x")
                sys.exit(1)
        """)
        assert [(x["func"], x["status"]) for x in h] == [(scan_mod.MODULE_LEVEL, 1)]

    def test_a_try_body_falls_through_its_else_and_finally(self):
        h = hits("""
            def main(ok):
                try:
                    if not ok:
                        print("REFUSED: x")
                finally:
                    cleanup()
                return 1
        """)
        assert [x["status"] for x in h] == [1]

    def test_an_except_handler_that_sets_a_flag_read_after_the_try(self):
        h = hits("""
            def main(ok):
                bad = False
                try:
                    x = ok
                except Refused as e:
                    print(f"REFUSED: {e}")
                    bad = True
                if bad:
                    return 1
                return 0
        """)
        assert [x["status"] for x in h] == [1]

    def test_the_bfs_walk_trap_the_issue_recorded_is_real_and_is_avoided(self):
        """`ast.walk` is breadth-first: on the planted positive it yields the
        `Return` (a child of `If`) BEFORE the `print` (a child of `Expr`), so
        a window measured over that order finds nothing.  Pinned as a fact,
        then the scanner is shown to find the site regardless."""
        tree = ast.parse(textwrap.dedent(scan_mod.CONTROL_POSITIVE))
        order = [type(n).__name__ for n in ast.walk(tree)
                 if isinstance(n, (ast.Return, ast.Call)) and not isinstance(n, ast.Name)]
        assert order.index("Return") < order.index("Call")
        pos = scan_mod.scan_source(scan_mod.CONTROL_POSITIVE, CONTRACT, NO_EVIDENCE)
        assert [h["status"] for h in pos["hits"]] == [1]

    def test_hits_carry_file_func_line_shape_status_and_label(self):
        h = hits("""
            def main(ok):
                if not ok:
                    print("\\nREFUSED: this instrument's own controls do not pass")
                    return 1
        """)[0]
        assert h == dict(file="<planted>", func="main", line=4, shape="print-exit", status=1,
                         label="REFUSED: this instrument's own controls do not pass")


# ------------------------------------------------ sibling-module resolution
class TestSiblingModuleResolution:
    """The tree declares its statuses by tuple and through other modules;
    a `return REFUSED` that resolves to 2 is not a hit, and is not UNREAD."""

    @staticmethod
    def resolve(module, name):
        return {"refusal": {"REFUSED": 2}, "rp": {"OK": 0, "FAIL": 1, "REFUSED": 2}}.get(module, {}).get(name)

    def test_from_import(self):
        src = """
            from refusal import REFUSED

            def main(ok):
                if not ok:
                    print("REFUSED: x")
                    return REFUSED
        """
        assert hits(src, resolve=self.resolve) == [] and unread(src, resolve=self.resolve) == []

    def test_import_alias_attribute(self):
        src = """
            import refusal as r

            def main(ok):
                if not ok:
                    print("REFUSED: x")
                    return r.REFUSED
        """
        assert hits(src, resolve=self.resolve) == []

    def test_module_level_tuple_through_another_module(self):
        src = """
            import rp

            OK, FAIL, REFUSED = rp.OK, rp.FAIL, rp.REFUSED

            def main(ok):
                if not ok:
                    print("REFUSED: x")
                    return REFUSED
        """
        assert hits(src, resolve=self.resolve) == [] and unread(src, resolve=self.resolve) == []

    def test_the_same_tuple_through_a_module_that_declares_1_IS_a_hit(self):
        src = """
            import rp

            OK, FAIL, REFUSED = rp.OK, rp.FAIL, rp.REFUSED

            def main(ok):
                if not ok:
                    print("REFUSED: x")
                    return REFUSED
        """
        assert [x["status"] for x in hits(src, resolve=lambda m, n: 1)] == [1]

    def test_without_a_resolver_the_site_is_unread_never_a_silent_pass(self):
        src = """
            from refusal import REFUSED

            def main(ok):
                if not ok:
                    print("REFUSED: x")
                    return REFUSED
        """
        assert hits(src) == [] and [u["why"] for u in unread(src)] == [scan_mod.WHY_NAME]

    def test_a_cycle_in_the_module_chain_stops(self, tmp_path, monkeypatch):
        """A.py's REFUSED is B's, B's is A's: the resolver answers None for
        both instead of recursing."""
        (tmp_path / "A.py").write_text("import B\nREFUSED = B.REFUSED\n")
        (tmp_path / "B.py").write_text("import A\nREFUSED = A.REFUSED\n")
        monkeypatch.setattr(scan_mod, "ROOT", tmp_path)
        cache, building = {}, set()
        resolve = scan_mod._sibling_resolver("A.py", CONTRACT, cache, building)
        assert resolve("A", "REFUSED") is None
        assert resolve("B", "REFUSED") is None
        assert not building
        assert cache == {("A.py", "REFUSED"): None, ("B.py", "REFUSED"): None} or all(
            v is None for v in cache.values())

    def test_the_resolver_follows_a_chain_of_modules(self, tmp_path, monkeypatch):
        """The tree's real shape: list -> s3 -> local -> `0, 1, 2`."""
        (tmp_path / "refaudio_local.py").write_text("FETCHED, FAIL, REFUSED = 0, 1, 2\n")
        (tmp_path / "refaudio_s3.py").write_text(
            "import refaudio_local as rl\nFETCHED, FAIL, REFUSED = rl.FETCHED, rl.FAIL, rl.REFUSED\n")
        (tmp_path / "refaudio_member_list.py").write_text(
            "import refaudio_s3 as s3\nLISTED, FAIL, REFUSED = s3.FETCHED, s3.FAIL, s3.REFUSED\n")
        monkeypatch.setattr(scan_mod, "ROOT", tmp_path)
        resolve = scan_mod._sibling_resolver("refaudio_member_list.py", CONTRACT, {}, set())
        assert (resolve("refaudio_s3", "REFUSED"), resolve("refaudio_member_list", "REFUSED"),
                resolve("refaudio_s3", "FAIL"), resolve("refaudio_local", "FETCHED")) == (2, 2, 1, 0)
        assert resolve("refaudio_s3", "NOT_THERE") is None
        assert resolve("no_such_module", "REFUSED") is None


# ---------------------------------------------------------- the tree-level
class TestPreconditionsRefuse:
    def test_self_control_refuses_when_the_planted_defect_is_not_found(self, monkeypatch):
        """The scanner's own first-run failure, kept: a scanner that cannot
        see the planted site REFUSES (exit 2) instead of printing a count."""
        monkeypatch.setattr(scan_mod, "scan_source",
                            lambda *a, **k: {"hits": [], "unread": []})
        with pytest.raises(scan_mod.Refused, match="was NOT found"):
            scan_mod.scan()

    def test_main_maps_that_refusal_to_exit_2_with_the_label(self, monkeypatch, capsys):
        monkeypatch.setattr(scan_mod, "scan_source",
                            lambda *a, **k: {"hits": [], "unread": []})
        assert scan_mod.main([]) == 2
        assert "REFUSED: self-control" in capsys.readouterr().err

    def test_self_control_refuses_when_the_corrected_shape_is_reported(self, monkeypatch):
        real = scan_mod.scan_source
        monkeypatch.setattr(scan_mod, "scan_source",
                            lambda src, *a, **k: {"hits": [dict(shape="print-exit", status=1)],
                                                  "unread": []} if "REFUSED" in src else real(src, *a, **k))
        with pytest.raises(scan_mod.Refused, match="corrected shape|would flag"):
            scan_mod.scan()

    def test_a_partial_tree_refuses(self, monkeypatch):
        monkeypatch.setattr(scan_mod.subprocess, "run",
                            lambda *a, **k: subprocess.CompletedProcess(a, 0, "a.py\nb.py\n", ""))
        with pytest.raises(scan_mod.Refused, match="partial tree"):
            scan_mod.tracked_python(())

    def test_a_git_failure_refuses(self, monkeypatch):
        def boom(*a, **k):
            raise OSError("no git")
        monkeypatch.setattr(scan_mod.subprocess, "run", boom)
        with pytest.raises(scan_mod.Refused, match="could not run"):
            scan_mod.tracked_python(())

    def test_a_contract_without_no_evidence_refuses(self, tmp_path, monkeypatch):
        p = tmp_path / "exit_contract.py"
        p.write_text("PASS = 0\nFAIL = 1\n")
        monkeypatch.setattr(scan_mod, "CONTRACT", p)
        monkeypatch.setattr(scan_mod, "ROOT", tmp_path)
        with pytest.raises(scan_mod.Refused, match="declares no NO_EVIDENCE"):
            scan_mod.read_contract()

    def test_an_empty_no_evidence_set_refuses(self, tmp_path, monkeypatch):
        p = tmp_path / "exit_contract.py"
        p.write_text("PASS = 0\nREFUSED = 2\nNO_EVIDENCE = frozenset()\n")
        monkeypatch.setattr(scan_mod, "CONTRACT", p)
        monkeypatch.setattr(scan_mod, "ROOT", tmp_path)
        with pytest.raises(scan_mod.Refused, match="empty"):
            scan_mod.read_contract()


def _res(hits):
    return {"n_files": 300, "roots": (), "no_evidence": [2, 3, 126], "hits": hits, "unread": []}


def _hit(file, func, shape, status=1, line=1):
    return dict(file=file, func=func, shape=shape, status=status, line=line, label="REFUSED: x")


class TestCheckGate:
    PINNED = dict(file="tools/pinned.py", func="main", shape="print-exit", count=2)

    @pytest.fixture
    def pinned(self, monkeypatch):
        entry = dict(self.PINNED, owner="#0", reason="test")
        monkeypatch.setattr(scan_mod, "EXPECTED", [entry])
        return entry

    def test_an_unpinned_hit_is_red(self, pinned):
        ok, lines = scan_mod.check(_res([_hit("tools/new.py", "main", "print-exit")]))
        assert not ok and any("not in EXPECTED" in ln for ln in lines)

    def test_a_pinned_hit_at_its_count_is_green(self, pinned):
        ok, _ = scan_mod.check(_res([_hit(self.PINNED["file"], "main", "print-exit", line=1),
                                     _hit(self.PINNED["file"], "main", "print-exit", line=9)]))
        assert ok

    def test_a_third_hit_in_the_pinned_function_is_red(self, pinned):
        ok, lines = scan_mod.check(_res([_hit(self.PINNED["file"], "main", "print-exit", line=1),
                                         _hit(self.PINNED["file"], "main", "print-exit", line=9),
                                         _hit(self.PINNED["file"], "main", "print-exit", line=20)]))
        assert not ok and any("3 hits, 2 pinned" in ln for ln in lines)

    def test_the_pin_is_keyed_on_the_function_not_the_line(self, pinned):
        """An edit above the site shifts lines and must not red the gate."""
        ok, _ = scan_mod.check(_res([_hit(self.PINNED["file"], "main", "print-exit", line=400),
                                     _hit(self.PINNED["file"], "main", "print-exit", line=401)]))
        assert ok

    def test_a_hit_in_another_function_of_the_pinned_file_is_red(self, pinned):
        ok, _ = scan_mod.check(_res([_hit(self.PINNED["file"], "main", "print-exit", line=1),
                                     _hit(self.PINNED["file"], "main", "print-exit", line=9),
                                     _hit(self.PINNED["file"], "other", "print-exit", line=9)]))
        assert not ok

    def test_a_pinned_site_whose_owner_landed_is_a_note_not_a_failure(self, pinned):
        """One-directional on purpose: a pin that reds on somebody else's fix
        gets trained away.  The stale entry is printed so it gets removed."""
        ok, lines = scan_mod.check(_res([]))
        assert ok and any("no longer present" in ln for ln in lines)

    def test_a_pinned_shape_is_not_a_licence_for_the_other_shape(self, pinned):
        ok, _ = scan_mod.check(_res([_hit(self.PINNED["file"], "main", "systemexit-str")]))
        assert not ok


class TestTheRealTree:
    """The gate `make verify` runs, in-process and through the CLI."""

    def test_every_hit_in_the_tree_is_a_pinned_exception(self):
        ok, lines = scan_mod.check(scan_mod.scan())
        assert ok, "\n".join(lines)

    def test_main_dash_check_exits_zero(self, capsys):
        assert scan_mod.main(["--check"]) == 0
        assert "REFUSED" not in capsys.readouterr().err

    def test_main_dash_check_exits_1_not_2_when_red(self, monkeypatch, capsys):
        monkeypatch.setattr(scan_mod, "EXPECTED", [])
        assert scan_mod.main(["--check"]) == 1
        assert "not in EXPECTED" in capsys.readouterr().out

    def test_the_scan_finds_the_tree_not_a_partial_tree(self):
        res = scan_mod.scan()
        assert res["n_files"] >= scan_mod.MIN_PY_FILES

    def test_the_real_contract_is_read_not_retyped(self):
        contract, no_evidence = scan_mod.read_contract()
        assert no_evidence == frozenset({2, 3, 126})
        assert contract["REFUSED"] == 2 and contract["FAIL"] == 1

    def test_the_files_this_issue_repaired_hold_no_shape_hit(self):
        """tools/dsp_dpreg_extract.py, tools/dsp_dpreg_sim.py and
        model/alias_probe.py refused with 1 on origin/main @ 03148399 (20
        sites between them).  None may carry either shape again."""
        res = scan_mod.scan()
        repaired = ("tools/dsp_dpreg_extract.py", "tools/dsp_dpreg_sim.py", "model/alias_probe.py")
        assert [f"{h['file']}:{h['line']}" for h in res["hits"] if h["file"] in repaired] == []

    def test_render_names_the_status_and_what_run_all_makes_of_it(self):
        out = scan_mod.render(_res([_hit("tools/x.py", "main", "print-exit")]))
        assert "FAIL (a RESULT)" in out and "tools/x.py:1" in out

    def test_roots_narrow_the_scan(self):
        res = scan_mod.scan(["tools/dsp_dpreg_extract.py", "tools/dsp_dpreg_sim.py"])
        assert {h["file"] for h in res["hits"]} <= {"tools/dsp_dpreg_extract.py", "tools/dsp_dpreg_sim.py"}
