"""fpga/scripts/mode_report.py: a synthesis result that cannot be read is
REFUSED, not a traceback (#1621).

The script is loaded by path (it is not an importable package module) and its
build directory and report root are pointed at tmp_path, so no control here
reads `rtl-sketch/build/` or touches the committed `fpga/reports/mode_sweep.txt`.
Its one external tool, `grep`, reads a netlist that is absent here (captured,
and counted as zero flops), so the controls run without it.
"""
import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("mode_report", ROOT / "fpga/scripts/mode_report.py")
mr = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mr)

RESULT_KEYS = ("total_cells", "total_area_um2")


@pytest.fixture
def scratch(monkeypatch, tmp_path):
    """Write one mode's result.json under a scratch build directory and point
    the script's `B` and `ROOT` at it. `ms_drumdp` is always written complete:
    the script reads it unguarded for its summary line."""
    def write(record, mode="ms_modal_m4"):
        for name in (mode, "ms_drumdp"):
            (tmp_path / "area" / name).mkdir(parents=True, exist_ok=True)
            (tmp_path / "area" / name / "result.json").write_text(
                json.dumps({k: 1 for k in RESULT_KEYS} if name == "ms_drumdp" else record))
        (tmp_path / "fpga" / "reports").mkdir(parents=True, exist_ok=True)
        monkeypatch.setattr(mr, "B", str(tmp_path / "area"))
        monkeypatch.setattr(mr, "ROOT", str(tmp_path))
    return write


def test_a_complete_result_renders_its_row(scratch, capsys):
    """The control for the refusals below: with the keys present the row is
    rendered and the table written, so a refusal can only be a missing key."""
    scratch({k: 1 for k in RESULT_KEYS})
    assert mr.main() == 0
    out = capsys.readouterr().out
    assert "    4   4       1        1.0      0" in out, out
    assert (Path(mr.ROOT) / "fpga/reports/mode_sweep.txt").exists()


@pytest.mark.parametrize("record,why", [
    pytest.param({}, "'total_cells', 'total_area_um2'", id="empty"),
    pytest.param({"total_cells": None, "total_area_um2": 1.0}, "'total_cells'", id="null-cells"),
    pytest.param({"total_cells": 1, "total_area_um2": None}, "'total_area_um2'", id="null-area"),
    pytest.param({"total_area_um2": 1.0}, "'total_cells'", id="no-cells"),
    pytest.param({"total_cells": 1}, "'total_area_um2'", id="no-area"),
    pytest.param([], "not a JSON object", id="list"),
    pytest.param(5, "not a JSON object", id="number"),
    pytest.param(None, "not a JSON object", id="null"),
])
def test_a_result_that_cannot_be_read_refuses_the_whole_table(scratch, capsys, record, why):
    """`d['total_cells']` and `d['total_area_um2']` were read with nothing
    checking the result, so a partial result.json was a `KeyError` traceback
    (exit 1). The refusal is the table's, not the row's: a row silently dropped
    from an area table (as a MISSING result is, by design) is a wrong table."""
    scratch(record)
    rc = mr.main()
    out = capsys.readouterr().out
    assert rc == 2 and "REFUSED" in out and why in out, out
    assert not (Path(mr.ROOT) / "fpga/reports/mode_sweep.txt").exists()
