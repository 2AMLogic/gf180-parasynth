"""The wrong-then-right record behind cymbal_low_tail's repaired FORWARD_INTEGRAL
(#1869) must stay TRUE.

`tools/cymbal_low_tail.py`'s docstring says the issue's first suggested repair
dies on 0/0, that the time-mirrored reading is red only through None, and that
the unrepaired defect raised. Those are claims about code that is no longer
shipped, so nothing else in the suite would notice them going stale. This file
does, through `cymbal_low_tail_forward_trace` -- and it carries the inputs that
must defeat the probe's own two guards.
"""
import pathlib
import sys

import pytest

HERE = pathlib.Path(__file__).resolve().parent
for _p in (HERE, HERE.parent, HERE.parents[1] / "model"):
    sys.path.insert(0, str(_p))
import cymbal_low_tail as lt  # noqa: E402
import cymbal_low_tail_forward_trace as tr  # noqa: E402


@pytest.fixture(scope="module")
def outcomes():
    return tr.outcomes()


def test_each_reading_still_does_what_the_record_says(outcomes):
    assert outcomes["unrepaired"][:2] == ("raised", "Refused"), outcomes
    assert outcomes["A"][:2] == ("raised", "ZeroDivisionError"), outcomes
    assert outcomes["B"][:2] == ("answered", (False, None)), outcomes
    assert outcomes["shipped"][0] == "answered", outcomes
    assert outcomes["shipped"][1][0] is False and outcomes["shipped"][1][1] is not None, outcomes


def test_the_probes_mirror_is_the_instrument():
    assert tr.mirror_is_exact()


def test_the_drift_check_sees_the_instrument_moving():
    """The input that defeats the drift guard: move a constant the instrument
    reads, and the check must go red rather than follow it."""
    was = lt.TRUNC_MARGIN_DB
    lt.TRUNC_MARGIN_DB = 14.0
    try:
        assert not tr.mirror_is_exact()
    finally:
        lt.TRUNC_MARGIN_DB = was
    assert tr.mirror_is_exact()


def test_the_record_check_tells_the_readings_apart():
    """The input that defeats the record guard: a check that accepted "raised
    Something" for any reading could not tell the crash from the refusal."""
    assert tr._matches_record(("raised", "Refused", ""), "unrepaired")
    assert not tr._matches_record(("raised", "ZeroDivisionError", ""), "unrepaired")
    assert tr._matches_record(("raised", "ZeroDivisionError", ""), "A")
    assert not tr._matches_record(("answered", (False, None), ()), "A")
    assert tr._matches_record(("answered", (False, None), ()), "B")
    assert not tr._matches_record(("answered", (False, 0.05), ()), "B")
    assert tr._matches_record(("answered", (False, 0.0517), ()), "shipped")
    assert not tr._matches_record(("answered", (False, None), ()), "shipped")


def test_the_unrepaired_reading_falls_back_to_the_mirror_and_says_so():
    """Without BASE in the clone (a shallow one) the probe must still answer,
    from its mirror, and must name the source rather than imply it read git."""
    base = tr.BASE
    tr.BASE = "0000000"
    try:
        got = tr.outcomes()
    finally:
        tr.BASE = base
    assert "mirror" in got["unrepaired_from"]
    assert got["unrepaired"][:2] == ("raised", "Refused"), got


def test_the_probe_exits_zero_on_the_current_tree(capsys):
    assert tr.main() == 0
    assert "mirror exact = True; readings matching the record = 4/4" in capsys.readouterr().out
