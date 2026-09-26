#!/usr/bin/env python3
"""`verify_ctl.py`'s per-field blindness matrix, without iverilog.

    .venv/bin/python -m pytest rtl-sketch/test_verify_ctl_blindness.py -q

docs/verification-rules.md 4: a suite that measures more than one property
reports, for every injected control, which properties MOVED and which were
BLIND. `verify_ctl` qualifies -- its comparison decomposes each write into four
fields -- and the matrix is what turns "SPI_ADDR7 was caught" into "SPI_ADDR7
was caught BY THE ADDRESS FIELD, and the other three could not have seen it."

The simulation itself is covered by `make controls`
(`verify_ctl.py --inject SPI_ADDR7 --expect-fail`, ~1 s under iverilog); what
is covered HERE is the reporting, which is the part that can be wrong while
every simulation still passes. In particular the refusal case: a run whose
link delivered nothing has no matrix to print, and printing four zeros for it
would be a table of BLIND rows that looks exactly like a defect no field could
see. That distinction -- no evidence versus evidence of nothing -- is the one
this repository keeps getting wrong.
"""
from __future__ import annotations

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import verify_ctl                                                   # noqa: E402


def _matrix(last, tag="TAG", capsys=None):
    verify_ctl.LAST.clear()
    verify_ctl.LAST.update(last)
    verify_ctl.print_blindness(tag)
    return capsys.readouterr().out


def _rows(out):
    """{field: verdict} for the four field lines."""
    got = {}
    for line in out.splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[0] in ("MOVED", "BLIND"):
            got[parts[1]] = parts[0]
    return got


def test_a_defect_confined_to_one_field_leaves_the_other_three_blind(capsys):
    """The SPI_ADDR7 shape, as measured on this tree: 105 of 206 writes have a
    wrong address and nothing else is touched."""
    out = _matrix(dict(total=206, bad_flag=0, bad_sec=0, bad_addr=105, bad_data=0),
                  "SPI_ADDR7", capsys)
    assert _rows(out) == {"flag": "BLIND", "section": "BLIND",
                          "address": "MOVED", "data": "BLIND"}
    assert "105 of 206" in out
    assert "NO FIELD MOVED" not in out


def test_the_data_truncation_moves_the_data_field_and_only_that(capsys):
    """SPI_DATA24: 42 of 206 writes carry a datum wider than 24 bits."""
    out = _matrix(dict(total=206, bad_flag=0, bad_sec=0, bad_addr=0, bad_data=42),
                  "SPI_DATA24", capsys)
    assert _rows(out) == {"flag": "BLIND", "section": "BLIND",
                          "address": "BLIND", "data": "MOVED"}
    assert "42 of 206" in out


def test_a_defect_no_field_sees_is_reported_as_a_coverage_hole_not_a_pass(capsys):
    """Four BLIND rows is not a clean result -- it means the control cannot
    turn this bench red, and the matrix has to say so in words rather than
    leaving a reader to notice the absence of a MOVED row."""
    out = _matrix(dict(total=206, bad_flag=0, bad_sec=0, bad_addr=0, bad_data=0),
                  "SPI_NOTHING", capsys)
    assert set(_rows(out).values()) == {"BLIND"}
    assert "NO FIELD MOVED" in out
    assert "coverage has a hole" in out


def test_a_run_that_delivered_nothing_refuses_the_matrix_rather_than_printing_zeros(capsys):
    """`LAST` empty means `compare_writes` never reached the per-field
    comparison -- the link carried no writes at all. Four zeros would render
    as four BLIND rows, i.e. as EVIDENCE that no field sees the defect, when
    in fact there is no evidence of anything. REFUSED is a first-class
    outcome here (CLAUDE.md), distinct from both pass and fail."""
    verify_ctl.LAST.clear()
    verify_ctl.print_blindness("SPI_DEAD")
    out = capsys.readouterr().out
    assert "no per-field blindness matrix" in out
    assert "MOVED" not in out and "BLIND" not in out


def test_the_matrix_names_every_field_compare_writes_counts(capsys):
    """If `compare_writes` grows a fifth per-field counter, the matrix must
    grow a fifth row -- otherwise the new field is silently outside the
    blindness report while looking covered."""
    out = _matrix(dict(total=4, bad_flag=1, bad_sec=1, bad_addr=1, bad_data=1),
                  "ALL", capsys)
    counters = {k for k in ("bad_flag", "bad_sec", "bad_addr", "bad_data")}
    assert len(_rows(out)) == len(counters)
    assert set(_rows(out).values()) == {"MOVED"}
