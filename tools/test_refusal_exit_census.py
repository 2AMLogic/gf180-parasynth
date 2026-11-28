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

Issue #1789 emptied sites_outside_set: the two exit-1 refusal handlers it
held -- both in tools/cymbal_vca_drive.py's main() -- now refuse with the
declared status, so nothing outside the declared set is accepted any more.
The #868 controls are about a pin's KEYING, which outlives its contents, so
they now state the one-exception pin they test against
(`documented_exception_pin`) instead of borrowing whatever the tree holds;
the removed sites are fed to the REAL pin, where they must be RED.
"""
from __future__ import annotations

import ast
import pathlib
import sys
import textwrap

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import refusal_exit_census as census_mod                     # noqa: E402

# The two exit-1 refusal handlers in tools/cymbal_vca_drive.py's main() that
# #804 pinned as documented exceptions and #1789 moved to the declared REFUSED
# status, as (file, enclosing function, line) -- the shape handler_census
# produces.  A pin is keyed on (file, function, status, count) since #868, so
# the line here is informational; the function is what a pin reads.
VCA_SITES = [("tools/cymbal_vca_drive.py", "main", 1525),
             ("tools/cymbal_vca_drive.py", "main", 1531)]
# #804's pin for them, as sites_outside_set held it until #1789: one entry.
DOCUMENTED_EXCEPTION_PIN = [["tools/cymbal_vca_drive.py", "main", 1, 2]]


@pytest.fixture
def documented_exception_pin(monkeypatch):
    """Restore #804's one-exception pin for one test.  The real pin is empty
    since #1789, and the controls on the pin MECHANISM below are about its
    keying, not its contents -- so they name the pin they test against."""
    monkeypatch.setattr(census_mod, "EXPECTED",
                        {**census_mod.EXPECTED,
                         "sites_outside_set": DOCUMENTED_EXCEPTION_PIN})


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


def test_the_exit_one_refusals_1789_removed_turn_check_red(monkeypatch):
    """#1789. The two exit-1 refusal handlers in cymbal_vca_drive.py's main()
    were #804's pinned, documented exceptions; they now refuse with the
    declared status and the pin is empty, so the census they made -- fed back
    in -- must be RED.  GREEN against the old pin before it was emptied
    (docs/verification-rules.md rule 1): this is what says the gate notices a
    refusal rendered as FAIL coming back."""
    monkeypatch.setattr(census_mod, "census",
                        lambda: _result([2, 3, 126], handler_sites={1: VCA_SITES}))
    ok, lines = census_mod.check()
    assert not ok
    assert any("outside the declared set" in ln for ln in lines)


def test_control_a_listed_exception_is_still_admitted(documented_exception_pin,
                                                     monkeypatch):
    """Control for the one above: with #804's one-exception pin restored, the
    same two sites are green -- the red comes from the pin being empty, not
    from a census that rejects every exception.  A pin still admits exactly
    what it lists; an exception is a decision recorded in one place."""
    monkeypatch.setattr(census_mod, "census",
                        lambda: _result([2, 3, 126], handler_sites={1: VCA_SITES}))
    ok, lines = census_mod.check()
    assert ok, "\n".join(lines)


# ---------------------------------------------------------------- issue #868
# The sites_outside_set pin was keyed on (file, line) pairs, so an unrelated
# edit ABOVE the pinned lines in tools/cymbal_vca_drive.py reds --check with
# the exit contract intact.  These controls pin the behaviour the re-keyed
# pin must have; Control A was run RED against the line-keyed pin first
# (docs/verification-rules.md rule 1).  Since #1789 the real pin is empty, so
# these controls state #804's one-exception pin through the fixture.

def test_control_a_line_shift_above_the_sites_stays_green(documented_exception_pin,
                                                         monkeypatch):
    """Control A (issue #868): an unrelated edit above the pinned sites --
    one inserted comment line shifts both down one -- must leave --check
    green.  On the line-keyed pin this exact census was RED while the exit
    contract was untouched: the defect this issue repairs.  The pin under
    test is #804's, supplied by the fixture: this control is about a pin's
    keying, not its contents."""
    shifted = [("tools/cymbal_vca_drive.py", "main", 1526),
               ("tools/cymbal_vca_drive.py", "main", 1532)]
    monkeypatch.setattr(census_mod, "census",
                        lambda: _result([2, 3, 126], handler_sites={1: shifted}))
    ok, lines = census_mod.check()
    assert ok, "\n".join(lines)


def test_control_b_new_undeclared_site_elsewhere_turns_check_red(documented_exception_pin,
                                                               monkeypatch):
    """Control B (issue #868): a new undeclared exit-4 refusal in another
    function and file must still turn --check red -- re-keying the pin must
    not open a hole the old pin did not have.  The two listed sites are
    admitted by the fixture's pin, so it is the exit-4 alone that reds."""
    monkeypatch.setattr(census_mod, "census",
                        lambda: _result([2, 3, 126],
                                        handler_sites={1: VCA_SITES,
                                                       4: [("tools/exit_probe_other.py", "run", 40)]}))
    ok, lines = census_mod.check()
    assert not ok, "\n".join(lines)
    assert any("outside the declared set" in ln for ln in lines)


def test_control_b_third_refusal_in_the_pinned_function_turns_check_red(
        documented_exception_pin, monkeypatch):
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
