#!/usr/bin/env python3
"""tools/deadline_recapture_l2.py: a capture record that cannot be read is
REFUSED, not a traceback (#1604, #1623).

Before #1623 `main()` read `rec['facts']['periods']` &c. with `[]` and nothing
caught the `KeyError`, so a capture record without one of those keys exited 1
by traceback -- FAIL, "the comparison ran and disagreed" -- where
`tools/exit_contract.py` says an unmet precondition is exit 2. One control per
key through the tool's own `main()`.

The records and the reader are real: the two capture records are the committed
`docs/deadline/runs/*-l2.json`. The simulator batch (`tools/run_all.py`) is
stubbed; its output directory is a scratch copy of what it writes.
"""
import hashlib
import json
import pathlib
import sys
from types import SimpleNamespace

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import deadline_recapture_l2 as dl  # noqa: E402

SHIPPED = {name: ROOT / "docs" / "deadline" / "runs" / f"{name}.json" for name, _ in dl.CAPTURES}


@pytest.fixture
def recapture(tmp_path, monkeypatch):
    """A scratch build/dl holding the batch's two records and trace files."""
    runs, traces, work = (tmp_path / n for n in ("runs", "traces", "work"))
    for d in (runs, traces, work):       # the committed directories already exist
        d.mkdir()
    for name in SHIPPED:
        (work / name).mkdir()
        for n in dl.ARCHIVED:
            (work / name / n).write_bytes(b"")
    monkeypatch.setattr(dl, "RUNS", str(runs))
    monkeypatch.setattr(dl, "TRACES", str(traces))
    monkeypatch.setattr(dl, "WORK", str(work))
    monkeypatch.setattr(dl.subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=0))
    return work, runs, traces


def _main(recapture, capsys, records):
    work, runs, traces = recapture
    (work / "run_all-l2.json").write_text(json.dumps({"jobs": []}))
    for name, rec in records.items():
        (work / f"{name}.json").write_text(json.dumps(rec))
    rc = dl.main()
    return rc, capsys.readouterr().out, runs, traces


def test_both_committed_records_are_archived_not_refused(recapture, capsys):
    """The guards below must be satisfiable against the records that ship."""
    rc, out, runs, traces = _main(recapture, capsys, {n: json.loads(p.read_text()) for n, p in SHIPPED.items()})
    assert rc == 0 and "REFUSED" not in out
    assert out.count("-> 4 files archived") == len(dl.CAPTURES)
    for name in SHIPPED:
        assert (runs / f"{name}.json").exists() and len(list((traces / name).glob("*.gz"))) == 4


@pytest.mark.parametrize("name", sorted(SHIPPED))
@pytest.mark.parametrize("key", ["status", "facts", "schedule"])
def test_a_capture_record_missing_a_key_is_refused(recapture, capsys, name, key):
    records = {n: json.loads(p.read_text()) for n, p in SHIPPED.items()}
    del records[name][key]
    rc, out, runs, traces = _main(recapture, capsys, records)
    assert rc == 2 and "REFUSED" in out and f"'{key}'" in out and name in out


@pytest.mark.parametrize("key", ["status", "facts", "schedule"])
def test_a_capture_record_with_null_for_a_key_is_refused(recapture, capsys, key):
    records = {n: json.loads(p.read_text()) for n, p in SHIPPED.items()}
    records[dl.CAPTURES[0][0]][key] = None
    rc, out, _, _ = _main(recapture, capsys, records)
    assert rc == 2 and "REFUSED" in out and f"'{key}'" in out


@pytest.mark.parametrize("record, why", [({}, "'status'"), ([], "not a JSON object")])
def test_a_capture_record_that_is_not_an_object_is_refused(recapture, capsys, record, why):
    records = {n: json.loads(p.read_text()) for n, p in SHIPPED.items()}
    records[dl.CAPTURES[0][0]] = record
    rc, out, _, _ = _main(recapture, capsys, records)
    assert rc == 2 and "REFUSED" in out and why in out


def _hashes(*roots):
    return {str(f): hashlib.sha256(f.read_bytes()).hexdigest()
            for r in roots for f in sorted(pathlib.Path(r).rglob("*")) if f.is_file()}


@pytest.mark.parametrize("damaged", range(len(dl.CAPTURES)))
def test_a_damaged_capture_leaves_every_retained_file_untouched(recapture, capsys, damaged):
    """Control for the preflight: seed the destination with prior evidence, damage
    ONE capture (either), and require refusal AND byte-identical retained files.
    Before the preflight a damaged second capture exited 2 after replacing 10 of
    11 retained files, including the refused receipt itself."""
    work, runs, traces = recapture
    records = {n: json.loads(p.read_text()) for n, p in SHIPPED.items()}
    _main(recapture, capsys, records)                      # seed with a good archive
    for name in records:                                   # new captures differ from the seed
        for n in dl.ARCHIVED:
            (work / name / n).write_bytes(b"replacement")
    del records[dl.CAPTURES[damaged][0]]["schedule"]
    before = _hashes(runs, traces)
    assert before
    rc, out, _, _ = _main(recapture, capsys, records)
    assert rc == 2 and "REFUSED" in out
    assert _hashes(runs, traces) == before
