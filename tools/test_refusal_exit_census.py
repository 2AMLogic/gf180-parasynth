"""CI wiring for `tools/refusal_exit_census.py --check` (issue #804, review).

The census shipped wired to nothing: no test collected it, it was absent from
`make verify`'s gate list and from `make verify-fast` (which is what CI runs),
so its --check sat RED on a PR whose CI was green -- the exact shape of the
"gate wired to nothing" failure the review round documented.  This file is
the wiring: it calls the census in-process on the real tree and through the
CLI exit path `make verify` invokes, and carries controls proving the gate
CAN fail -- a doctored contract set, a site refusing with an undeclared
status, or a pin emptied to accept such a site must each turn check() red.
A gate that cannot fail is not a gate.

Issue #868 re-keyed sites_outside_set from (file, line) pairs -- which reds
on any unrelated edit above the pinned lines -- to
(file, enclosing function, status) counts.  The #868 controls live here too:
Control A (a line shift above the sites stays green) was run RED against the
old pin before the re-key landed; Controls B (a new undeclared site, a third
refusal in the pinned function) prove the re-key opened no hole.

Issue #1790 added the third detector, the label/status pass, for a refusal
carried in prose -- `print("REFUSED: ...")` beside a bare `return 1` outside
any handler -- which the handler pass cannot see.  It is reported, never
pinned; its controls live here, and were run RED against a stub that saw
nothing before the detector landed.
"""
from __future__ import annotations

import ast
import pathlib
import sys
import textwrap

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import refusal_exit_census as census_mod                     # noqa: E402

# The documented exception sites, as (file, enclosing function, line) --
# the shape handler_census produces.  The PIN is keyed on
# (file, function, status, count) since #868, so the line here is
# informational; the function is what the pin reads.
VCA_SITES = [("tools/cymbal_vca_drive.py", "main", 1525),
             ("tools/cymbal_vca_drive.py", "main", 1531)]


def _result(no_evidence, handler_sites=None, constant_sites=None) -> dict:
    """A minimal census()-shaped result for the comparison logic.  Sites are
    (file, function, line) triples as handler_census/constant_census
    produce them (constant sites are module-level by construction)."""
    codes: dict[int, dict] = {}
    for c, sites in (handler_sites or {}).items():
        codes[c] = {"handler_sites": sites,
                    "handler_files": sorted({f for f, _, _ in sites}),
                    "constant_sites": []}
    for c, sites in (constant_sites or {}).items():
        codes.setdefault(c, {"handler_sites": [], "handler_files": [],
                             "constant_sites": sites})
    return {"n_python_files": 300, "no_evidence": list(no_evidence),
            "codes": codes}


def test_check_is_green_on_this_tree():
    """The real gate: the shipped tree must satisfy its own census."""
    ok, lines = census_mod.check()
    assert ok, "\n".join(lines)


def test_main_dash_check_exits_zero():
    """The exact command `make verify` runs, through the CLI exit wiring."""
    assert census_mod.main(["--check"]) == 0


def test_an_edited_contract_set_turns_check_red(monkeypatch):
    """An accidental edit to tools/exit_contract.py's NO_EVIDENCE must fail
    the gate (here: 126 dropped from the set)."""
    monkeypatch.setattr(census_mod, "census", lambda: _result([2, 3]))
    ok, lines = census_mod.check()
    assert not ok, "\n".join(lines)
    assert any("NO-VERDICT set" in ln for ln in lines)


def test_an_undeclared_refusal_status_turns_check_red(monkeypatch):
    """The defect this census exists to catch: a site refusing with a status
    outside the declared set (a future exit 4, say)."""
    monkeypatch.setattr(census_mod, "census",
                        lambda: _result([2, 3, 126],
                                        handler_sites={4: [VCA_SITES[0]]}))
    ok, lines = census_mod.check()
    assert not ok, "\n".join(lines)
    assert any("outside the declared set" in ln for ln in lines)


def test_emptying_the_pin_does_not_accept_an_undeclared_site(monkeypatch):
    """Control on the pin itself: the same undeclared site with
    sites_outside_set emptied must ALSO be red -- the pin constrains; editing
    it to accept the defect is visible, not silent (and the census's own
    sources carry the documented reasons for each pinned exception)."""
    monkeypatch.setattr(census_mod, "census",
                        lambda: _result([2, 3, 126],
                                        handler_sites={4: [VCA_SITES[0]]}))
    monkeypatch.setattr(census_mod, "EXPECTED",
                        {**census_mod.EXPECTED, "sites_outside_set": []})
    ok, _ = census_mod.check()
    assert not ok


def test_the_documented_exceptions_satisfy_the_pin(monkeypatch):
    """Positive control for the one above: with the pin intact, exactly the
    two documented sites is what --check expects."""
    monkeypatch.setattr(census_mod, "census",
                        lambda: _result([2, 3, 126], handler_sites={1: VCA_SITES}))
    ok, _ = census_mod.check()
    assert ok


# ---------------------------------------------------------------- issue #868
# The sites_outside_set pin was keyed on (file, line) pairs, so an unrelated
# edit ABOVE the pinned lines in tools/cymbal_vca_drive.py reds --check with
# the exit contract intact.  These controls pin the behaviour the re-keyed
# pin must have; Control A was run RED against the line-keyed pin first
# (docs/verification-rules.md rule 1).

def test_control_a_line_shift_above_the_sites_stays_green(monkeypatch):
    """Control A (issue #868): an unrelated edit above the pinned sites --
    one inserted comment line shifts both down one -- must leave --check
    green.  On the line-keyed pin this exact census was RED while the exit
    contract was untouched: the defect this issue repairs."""
    shifted = [("tools/cymbal_vca_drive.py", "main", 1526),
               ("tools/cymbal_vca_drive.py", "main", 1532)]
    monkeypatch.setattr(census_mod, "census",
                        lambda: _result([2, 3, 126], handler_sites={1: shifted}))
    ok, lines = census_mod.check()
    assert ok, "\n".join(lines)


def test_control_b_new_undeclared_site_elsewhere_turns_check_red(monkeypatch):
    """Control B (issue #868): a new undeclared exit-4 refusal in another
    function and file must still turn --check red -- re-keying the pin must
    not open a hole the old pin did not have."""
    monkeypatch.setattr(census_mod, "census",
                        lambda: _result([2, 3, 126],
                                        handler_sites={1: VCA_SITES,
                                                       4: [("tools/exit_probe_other.py", "run", 40)]}))
    ok, lines = census_mod.check()
    assert not ok, "\n".join(lines)
    assert any("outside the declared set" in ln for ln in lines)


def test_control_b_third_refusal_in_the_pinned_function_turns_check_red(monkeypatch):
    """Control B (issue #868): a THIRD exit-1 refusal inside the same pinned
    function must turn --check red -- the count is part of the pin."""
    monkeypatch.setattr(census_mod, "census",
                        lambda: _result([2, 3, 126],
                                        handler_sites={1: VCA_SITES + [("tools/cymbal_vca_drive.py", "main", 1600)]}))
    ok, lines = census_mod.check()
    assert not ok, "\n".join(lines)


def test_module_level_handler_keys_as_module_not_crash():
    """Edge case (issue #868): a refusal handler with no enclosing def keys
    as "<module>", a distinct key that cannot collide with a function name."""
    tree = ast.parse(textwrap.dedent("""
        try:
            risky()
        except Refused:
            pass
    """))
    found = census_mod._handlers_in_scope(tree)
    assert [func for func, _ in found] == [census_mod.MODULE_LEVEL]


def test_nested_function_handler_keys_on_the_innermost_def():
    """Edge case (issue #868): a handler inside a nested def keys on the
    innermost function, not the outer one."""
    tree = ast.parse(textwrap.dedent("""
        def outer():
            def inner():
                try:
                    risky()
                except Refused:
                    return 4
            return inner
    """))
    found = census_mod._handlers_in_scope(tree)
    assert [func for func, _ in found] == ["inner"]


# --------------------------------------------------------------- issue #1790
# The handler pass counts `except <refusal>: ... return <status>` sites.  A
# refusal carried in PROSE -- a function that prints a string starting
# REFUSED and also `return 1`s outside any handler -- has no handler to read,
# so the census was green on a tree where tools printed REFUSED and exited 1,
# which run_all.py renders FAIL: the #804 label/status inversion.  The
# label/status pass is the third detector.  It is REPORTED, never pinned: the
# sites belong to the tools that print them (#1769, #1786, #1789), and a pin
# that reds on their merges gets trained away.  These controls were run RED
# against a stub whose label_sites had the right signature and returned
# nothing (docs/verification-rules.md rule 1).

# The injected bug: REFUSED printed, a bare `return 1` beside it, no handler.
# Line 7 is the `return 1`; a site is named by the statement that carries the
# status, never by the print.
INJECTED = """
    REFUSED = 2

    def run(a):
        if not a:
            print("REFUSED: x")
            return 1
        return 0
"""


def _label(src, no_evidence=(2, 3, 126)):
    """label_sites over synthetic source, with the module's own constants and
    the contract's outcome names resolved the way label_census resolves them."""
    tree = ast.parse(textwrap.dedent(src))
    return census_mod.label_sites(
        tree, census_mod._module_int_assigns(tree), census_mod._exit_contract_imports(tree),
        {"PASS": 0, "FAIL": 1, "REFUSED": 2}, frozenset(no_evidence))


def test_control_1790_refused_label_beside_bare_return_1_is_reported():
    """The injected bug turns the pass non-empty: one site, exit 1."""
    assert _label(INJECTED) == [(1, "run", 7)]


def test_control_1790_the_handler_pass_is_blind_to_the_injected_bug():
    """Wrong before it was right, recorded: on the same source the handler
    pass's detector finds no handler at all, which is how --check stayed green
    while tools printed REFUSED and exited 1."""
    assert census_mod._handlers_in_scope(ast.parse(textwrap.dedent(INJECTED))) == []


def test_control_1790_same_function_returning_refused_2_is_silent():
    """The compliant shape: the function prints REFUSED and returns REFUSED
    (2, in NO_EVIDENCE) -- no site."""
    src = INJECTED.replace("return 1", "return REFUSED")
    assert _label(src) == []


def test_label_pass_reports_an_undeclared_status_under_its_own_exit():
    """A `return 4` beside the label is listed under exit 4.  Only the
    handler pass's sites_outside_set pin reds on an undeclared status; this
    pass shows the label was there."""
    src = INJECTED.replace("return 1", "return 4")
    assert _label(src) == [(4, "run", 7)]


def test_label_pass_trailing_success_return_is_not_reached_by_the_label():
    """The `return 0` at the end of the function is not listed: the `return 1`
    above it ended the label.  (Every one of the functions on the current
    tree ends in one.)"""
    assert 0 not in {status for status, _, _ in _label(INJECTED)}


def test_label_pass_lists_a_refused_label_beside_return_0():
    """Rendered PASS, the worst inversion, and inside the issue's
    `<int not in NO_EVIDENCE>`.  Listing it is safe only because the label
    must sit beside the return; under function granularity it would have
    listed every function's success return."""
    src = INJECTED.replace("return 1", "return 0")
    assert _label(src) == [(0, "run", 7)]


# ---- the BESIDE walk.  "The function prints REFUSED somewhere" was the first
# rule: on the tree of 03148399 it listed 50 sites, at least 14 of them a
# main() whose REFUSED paths all `return 2` inside handlers, beside a
# correctly-labelled FAIL return.  The controls below discriminate the two
# rules and were run RED against the function-granularity code.

def test_label_pass_sees_through_a_transparent_statement():
    """tools/dsp_dpreg_extract.py:466-468: a diagnostic line sits between the
    label and the return.  This is the input that defeats the issue's
    `grep -A1`, which is why that grep counted 12 and not 14."""
    src = """
        def run(a):
            if not a:
                print(f"REFUSED: vivado batch failed rc={a}")
                tail_log()
                return 1
            return 0
    """
    assert _label(src) == [(1, "run", 6)]


def test_label_pass_an_earlier_return_ends_the_label():
    """The common compliant main(): REFUSED printed inside a handler that
    returns 2, and a separate FAIL path returning 1.  Not an inversion, with
    or without a FAIL message of its own -- the handler's return ended the
    label.  Function granularity listed both."""
    src = """
        REFUSED = 2

        def run(a):
            try:
                risky()
            except Refused as exc:
                print(f"REFUSED: {exc}")
                return REFUSED
            if not a:
                print("FAIL: controls did not pass")
                return 1
            return 0
    """
    assert _label(src) == []
    silent = src.replace('                print("FAIL: controls did not pass")\n', "")
    assert _label(silent) == []


def test_label_pass_a_differently_labelled_print_relabels():
    """A REFUSED print with no return after it, then a FAIL print, then the
    return: the return's label is FAIL."""
    src = """
        def run(a):
            if a:
                print("REFUSED: x")
            print(f"FAIL: {a}")
            return 1
    """
    assert _label(src) == []


def test_label_pass_a_raise_ends_the_label():
    """A raise after the print ends that path exactly as a return does."""
    src = """
        def run(a):
            if a:
                print("REFUSED: x")
                raise Refused("x")
            return 1
    """
    assert _label(src) == []


def test_label_pass_sees_a_handler_that_prints_and_falls_through():
    """A refusal handler that prints REFUSED and does NOT return falls through
    to a `return 1`: the handler pass counts nothing (no Return inside), so
    the label pass must.  The handler's print is a label like any other."""
    src = """
        def run(a):
            try:
                risky()
            except Refused as exc:
                print(f"REFUSED: {exc}")
            return 1
    """
    assert _label(src) == [(1, "run", 7)]


def test_label_pass_sees_the_newline_prefixed_label():
    """Two of the three sites the issue named print `"\\nREFUSED: ..."`.  A
    startswith() on the raw literal is the input that defeats the check."""
    src = INJECTED.replace('print("REFUSED: x")', 'print("\\nREFUSED: x")')
    assert _label(src) == [(1, "run", 7)]


def test_label_pass_reads_an_f_string_with_a_literal_prefix():
    """Six of the sites print `f"REFUSED: ... {x}"`.  An f-string that opens
    on a formatted value has no label this pass can read."""
    src = INJECTED.replace('print("REFUSED: x")', 'print(f"REFUSED: {a}")')
    assert _label(src) == [(1, "run", 7)]
    blind = INJECTED.replace('print("REFUSED: x")', 'print(f"{a} REFUSED")')
    assert _label(blind) == []


def test_label_pass_ignores_a_label_that_only_contains_refused():
    """`MISSED: ... expected REFUSED` (tools/check_iverilog_generate_case_string.py)
    is a control miss, not a refusal; neither is a word that merely begins
    with the seven letters."""
    src = INJECTED.replace('print("REFUSED: x")', 'print(f"MISSED: expected REFUSED, got {a}")')
    assert _label(src) == []
    src = INJECTED.replace('print("REFUSED: x")', 'print("REFUSED_TYPES = ()")')
    assert _label(src) == []


def test_label_pass_is_silent_without_the_label():
    """A `return 1` in a function that never prints REFUSED is an ordinary
    FAIL exit, correctly rendered."""
    src = INJECTED.replace('print("REFUSED: x")', 'print(f"FAIL: {a}")')
    assert _label(src) == []


def test_label_pass_defers_to_the_handler_pass_inside_a_refusal_handler():
    """Inside a refusal handler the handler pass owns the site: it counts it
    and, for exit 1, sites_outside_set pins it (tools/cymbal_vca_drive.py).
    The label pass lists only what that pass cannot see, so the pinned
    in-handler `return 1` is not listed a second time."""
    pinned = """
        def run(a):
            try:
                risky()
            except SourceUnavailable as exc:
                print(f"REFUSED (--require-source): {exc}")
                return 1
            return 0
    """
    assert _label(pinned) == []
    assert [func for func, _ in
            census_mod._handlers_in_scope(ast.parse(textwrap.dedent(pinned)))] == ["run"]


def test_label_pass_sees_a_non_refusal_handler():
    """`except OSError:` is not in REFUSAL_CLASSES, so the handler pass is
    blind to a REFUSED + `return 1` inside it; the label pass is not."""
    src = """
        def run(a):
            try:
                risky()
            except OSError as exc:
                print(f"REFUSED: {exc}")
                return 1
            return 0
    """
    assert _label(src) == [(1, "run", 7)]


def test_label_pass_nested_function_keys_on_the_innermost_def():
    """Edge case (issue #1790, the #868 test shape): a print and a return in
    a nested def belong to the nested def, not the enclosing one."""
    nested = """
        def outer():
            def inner(a):
                print("REFUSED: x")
                return 1
            return inner
    """
    assert _label(nested) == [(1, "inner", 5)]


def test_label_pass_is_blind_to_a_refusal_printed_in_a_helper():
    """The documented blind spot, pinned so it stays true: the caller returns
    1 but never prints; the callee prints but returns no status."""
    helper = """
        def _refuse(msg):
            print(f"REFUSED: {msg}")

        def run(a):
            if not a:
                _refuse("x")
                return 1
            return 0
    """
    assert _label(helper) == []


def test_render_lists_label_sites_as_file_line():
    """The census output names each label/status site as file:line."""
    res = _result([2, 3, 126], handler_sites={1: []})
    res["codes"][1]["run_all_state"] = "FAIL"
    res["codes"][1]["label_sites"] = [("tools/x.py", "main", 12)]
    assert "exit 1  tools/x.py:12  (in main)" in census_mod.render(res)


def test_check_reports_label_sites_without_pinning_them(monkeypatch):
    """Reported, not pinned: label/status sites leave --check green, and
    their count prints so drift is visible in every run."""
    res = _result([2, 3, 126], handler_sites={1: VCA_SITES})
    res["codes"][1]["label_sites"] = [("tools/x.py", "main", 12), ("tools/y.py", "main", 3)]
    monkeypatch.setattr(census_mod, "census", lambda: res)
    ok, lines = census_mod.check()
    assert ok, "\n".join(lines)
    assert any("2 label/status sites" in ln for ln in lines)


def test_shared_trees_parse_each_file_once_and_change_no_result(tmp_path, monkeypatch):
    """The three passes share one read+parse per file (the census's runtime is
    nearly all parsing, #1827 review).  Control: counting `ast.parse` calls
    fails if a pass re-parses; comparing against the standalone call fails if
    sharing changes a result."""
    src = textwrap.dedent('''
        import sys
        REFUSED = 2
        def main():
            try:
                pass
            except Refused:
                return 2
            print("REFUSED: no evidence")
            return 1
    ''')
    (tmp_path / "a.py").write_text(src)
    (tmp_path / "test_skip.py").write_text(src)
    (tmp_path / "bad.py").write_text("def (:\n")
    monkeypatch.setattr(census_mod, "ROOT", tmp_path)
    files = ["a.py", "test_skip.py", "bad.py"]
    contract = {"REFUSED": 2}

    real_parse, calls = ast.parse, []
    monkeypatch.setattr(census_mod.ast, "parse",
                        lambda text, *a, **k: calls.append(text) or real_parse(text, *a, **k))
    trees = census_mod.parse_sources(files)
    shared = (census_mod.handler_census(files, contract, trees),
              census_mod.constant_census(files, trees),
              census_mod.label_census(files, contract, frozenset({2}), trees))
    assert len(calls) == 2, "a.py and bad.py are parsed once each; test_ files never"
    assert set(trees) == {"a.py"}

    alone = (census_mod.handler_census(files, contract),
             census_mod.constant_census(files),
             census_mod.label_census(files, contract, frozenset({2})))
    assert shared == alone
    assert shared[0] and shared[1], "the fixture must exercise handler and constant passes"
