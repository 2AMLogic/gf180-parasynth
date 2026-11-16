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
"""
from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import refusal_exit_census as census_mod                     # noqa: E402

# The documented exception sites (EXPECTED["sites_outside_set"], exit 1).
VCA_SITES = [("tools/cymbal_vca_drive.py", 1525),
             ("tools/cymbal_vca_drive.py", 1531)]


def _result(no_evidence, handler_sites=None, constant_sites=None) -> dict:
    """A minimal census()-shaped result for the comparison logic.  Sites are
    (file, line) pairs as handler_census/constant_census produce them."""
    codes: dict[int, dict] = {}
    for c, sites in (handler_sites or {}).items():
        codes[c] = {"handler_sites": sites,
                    "handler_files": sorted({f for f, _ in sites}),
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
