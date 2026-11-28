"""Validation cases for tools/probes/fold_kernel_rows.py (#2112).

The probe is arithmetic -- the table against its formula, over [0, 1] -- so
its controls are about its verdict and not about a host: the shipped table is
under the bound and the 64-row control is over it; a bound loose enough for
the control is refused as holding nothing; a bound no table can meet fails
on the shipped row; and the shipped count of rows is measured wherever it is
set, so a sweep that cannot see the shipped value cannot pass over it.
"""
from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import fold_kernel_rows as K                                   # noqa: E402

D = K.D


def test_the_shipped_table_is_under_the_bound_and_the_control_over_it(capsys):
    rc = K.main(["--scan", "2001"])
    text = capsys.readouterr().out
    assert rc == 0, text
    assert text.startswith("fold_kernel_rows: OK -- #2112"), text
    assert f"FOLD_KERNEL_ROWS = {D.FOLD_KERNEL_ROWS} is under" in text
    assert "the 64-row control is over it at every width" in text
    assert f"bound KERNEL_ERROR_BOUND = {D.KERNEL_ERROR_BOUND:g}" in text
    rows = {int(ln.split()[0]) for ln in text.splitlines()
             if ln.split() and ln.split()[0].isdigit()}
    assert rows == set(K.ROWS), rows


def test_a_bound_the_coarsest_table_meets_holds_nothing(capsys):
    rc = K.main(["--scan", "2001", "--bound", "1e-3"])
    text = capsys.readouterr().out
    assert rc == 1, (rc, text)
    assert "the 64-row control is under 0.001" in text, text


def test_a_bound_no_table_meets_fails_on_the_shipped_row(capsys):
    rc = K.main(["--scan", "2001", "--bound", "1e-20"])
    text = capsys.readouterr().out
    assert rc == 1, (rc, text)
    assert f"at {D.FOLD_KERNEL_ROWS} rows the table is over 1e-20 at widths [" in text, text


def test_the_shipped_count_of_rows_is_measured_wherever_it_is_set(capsys, monkeypatch):
    """6,000 rows is on no grid here, so a sweep that read the grid and not
    the module would never measure the value that ships."""
    monkeypatch.setattr(D, "FOLD_KERNEL_ROWS", 6000)
    rc = K.main(["--scan", "2001"])
    text = capsys.readouterr().out
    assert rc == 0, (rc, text)
    assert "  6000     16" in text and "FOLD_KERNEL_ROWS = 6000 is under" in text, text
    monkeypatch.setattr(D, "FOLD_KERNEL_ROWS", 300)
    rc = K.main(["--scan", "2001"])
    text = capsys.readouterr().out
    assert rc == 1 and "at 300 rows the table is over 1e-12 at widths [" in text, text
