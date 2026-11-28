#!/usr/bin/env python3
"""tools/deadline_reanalyse.py: a run record that cannot be read is REFUSED, not
a traceback (#1604, #1623).

Before #1623 a run record without `schedule`, or a re-analysis record without
`status`, exited 1 by `KeyError` -- FAIL, "the comparison ran and disagreed" --
where `tools/exit_contract.py` says an unmet precondition is exit 2. One
control per key through the tool's own `main()`: each deletes ONE key from the
committed record and requires the refusal to name it.

The records and the reader are real: the run record is the committed
`docs/deadline/runs/prod-stress-saw.json` and the re-analysis the committed
`docs/deadline/reanalysis/prod-stress-saw.json` it was identified by. The
simulator batch (`tools/run_all.py` over ~30 min of RTL) and `git` are stubbed.
"""
import json
import pathlib
import sys
from types import SimpleNamespace

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import deadline_reanalyse as dr  # noqa: E402

SHIPPED_RUN = ROOT / "docs" / "deadline" / "runs" / "prod-stress-saw.json"
SHIPPED_NEW = ROOT / "docs" / "deadline" / "reanalysis" / "prod-stress-saw.json"
NAME = "stress-saw-run"


def _run_record():
    return json.loads(SHIPPED_RUN.read_text())


def _new_record():
    return json.loads(SHIPPED_NEW.read_text())


@pytest.fixture
def box(tmp_path, monkeypatch):
    """A scratch runs/, capture directory, re-analysis output and trace archive."""
    runs, out, capt, traces = (tmp_path / n for n in ("runs", "out", "capt", "traces"))
    for d in (runs, out, capt):
        d.mkdir()
    (capt / dr.tag_of(_run_record())).mkdir()
    # the trace files the committed re-analysis says it read (archived, not re-read)
    for n in _new_record()["reanalysis"]["capture_sha256"]:
        (capt / dr.tag_of(_run_record()) / n).write_bytes(b"")
    (runs / "HEAD.txt").write_text("abc1234\n")
    for name, val in (("RUNS", runs), ("OUT", out), ("CAPT", capt), ("TRACES", traces)):
        monkeypatch.setattr(dr, name, str(val))
    monkeypatch.setattr(dr.subprocess, "run",
                        lambda *a, **k: SimpleNamespace(returncode=0, stdout="", stderr=""))
    return runs, out


def _main(box, capsys, run_record, new_record):
    runs, out = box
    (runs / f"{NAME}.json").write_text(json.dumps(run_record))
    # what `verify_deadline.py --analyse-capture --json` writes for this capture
    (out / f"{NAME}.json").write_text(json.dumps(new_record))
    rc = dr.main()
    return rc, capsys.readouterr().out


def test_the_committed_run_and_reanalysis_records_are_identified(box, capsys):
    """The guards below must be satisfiable against the records that ship."""
    rc, out = _main(box, capsys, _run_record(), _new_record())
    assert rc == 0
    summary = json.loads((box[1] / "summary.json").read_text())
    assert [c["record"] for c in summary["captures"]] == [NAME]
    assert summary["captures"][0]["identified"] is True
    assert f"{NAME} PASS identified" in out
    assert summary["cost_model_totals"]["traces"] == 1


# the run record, read at its first load (and again as `orig` after the batch)
@pytest.mark.parametrize("key", ["scenario", "configuration", "schedule", "status"])
def test_a_run_record_missing_a_key_is_refused(box, capsys, key):
    rec = _run_record()
    del rec[key]
    rc, out = _main(box, capsys, rec, _new_record())
    assert rc == 2 and "REFUSED" in out and f"'{key}'" in out
    assert not (box[1] / "summary.json").exists()      # nothing was reported as data


# the re-analysis record the batch writes
@pytest.mark.parametrize("key", ["schedule", "status"])
def test_a_reanalysis_record_missing_a_key_is_refused(box, capsys, key):
    new = _new_record()
    del new[key]
    rc, out = _main(box, capsys, _run_record(), new)
    assert rc == 2 and "REFUSED" in out and f"'{key}'" in out
    assert not (box[1] / "summary.json").exists()


@pytest.mark.parametrize("key", ["schedule", "status"])
def test_a_run_record_with_null_for_a_key_is_refused(box, capsys, key):
    rec = _run_record()
    rec[key] = None
    rc, out = _main(box, capsys, rec, _new_record())
    assert rc == 2 and "REFUSED" in out and f"'{key}'" in out


def test_an_empty_run_record_is_refused(box, capsys):
    rc, out = _main(box, capsys, {}, _new_record())
    assert rc == 2 and "REFUSED" in out and "'schedule'" in out


def test_a_run_record_that_is_not_an_object_is_refused(box, capsys):
    # a list carries no keys at all; it must not be read as one missing key
    rc, out = _main(box, capsys, [], _new_record())
    assert rc == 2 and "REFUSED" in out and "not a JSON object" in out
