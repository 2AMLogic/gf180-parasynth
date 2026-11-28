"""tools/bd_glide_phase_sweep.py: a frozen record that cannot be read is REFUSED
(#1604, #1623).

`record_knobs()` reads `rec["conditions"]` with `[]`. Before #1623 a record
without it exited 1 by `KeyError` -- FAIL -- where `tools/exit_contract.py`
says an unmet precondition is exit 2, and nothing here catches it. The control
runs the tool's own `main()`; the refusal fires before any cell is computed, so
the sweep itself (thousands of `pitch_trajectory` evaluations) never starts.
"""
import json
import pathlib
import sys

import pytest

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import bd_glide_phase_sweep as bg  # noqa: E402
import bd_pitch_predeclaration as bp  # noqa: E402


def _main(monkeypatch, tmp_path, capsys, record):
    path = tmp_path / "record.json"
    path.write_text(json.dumps(record))
    monkeypatch.setattr(bp, "RECORD", path)
    rc = bg.main([])
    return rc, capsys.readouterr().out


def test_the_frozen_record_still_yields_the_knobs_the_sweep_reads():
    """The guard must be satisfiable against the record that ships."""
    knobs = bg.record_knobs()
    assert knobs and all(0.0 <= k <= 10.0 for k in knobs)


def test_a_record_without_conditions_is_refused(monkeypatch, tmp_path, capsys):
    rec = json.loads(bp.RECORD.read_text())
    del rec["conditions"]
    rc, out = _main(monkeypatch, tmp_path, capsys, rec)
    assert rc == 2 and "REFUSED" in out and "'conditions'" in out
    assert "cells" not in out                       # no sweep was reported


def test_a_record_with_null_conditions_is_refused(monkeypatch, tmp_path, capsys):
    rec = json.loads(bp.RECORD.read_text())
    rec["conditions"] = None
    rc, out = _main(monkeypatch, tmp_path, capsys, rec)
    assert rc == 2 and "REFUSED" in out and "'conditions'" in out


@pytest.mark.parametrize("record, why", [({}, "'conditions'"), ([], "not a JSON object"),
                                         (5, "not a JSON object"), (None, "not a JSON object")])
def test_a_record_that_carries_no_conditions_at_all_is_refused(monkeypatch, tmp_path, capsys,
                                                                record, why):
    rc, out = _main(monkeypatch, tmp_path, capsys, record)
    assert rc == 2 and "REFUSED" in out and why in out
