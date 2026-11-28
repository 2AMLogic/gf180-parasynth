#!/usr/bin/env python3
"""`compare_mono_attack_context` refuses a frozen report it cannot read.

#1604, #1622. `compare()` reads `frozen["renders"]` and `frozen["summary"]`
with `[]`. Before #1622 a frozen report without either exited 1 by `KeyError`
-- FAIL, "the comparison ran and disagreed" -- where `tools/exit_contract.py`
says an unmet precondition is exit 2. One control per key through the tool's
own `main()`.

`compare()` resolves the frozen report under `ROOT` and hash-checks the
reference WAVs beside it, so each control runs over a scratch root that
symlinks every committed file except `report.json`. `model/` is deliberately
not among them: a run that got past the guard writes its renders there, and a
symlink would carry that write into the committed directory.
"""
from __future__ import annotations

import json
import pathlib
import sys

import pytest

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import compare_mono_attack_context as producer                     # noqa: E402

FROZEN = HERE.parent / "docs/scorecard/mono-attack-context"
KEYS = ("renders", "summary")


def _committed():
    return json.loads((FROZEN / "report.json").read_text())


def _main(monkeypatch, tmp_path, capsys, report):
    """`main()` over a scratch root whose frozen report is `report`."""
    scratch = tmp_path / "docs/scorecard/mono-attack-context"
    scratch.mkdir(parents=True)
    for path in FROZEN.iterdir():
        if path.name not in ("report.json", "model"):
            (scratch / path.name).symlink_to(path)
    (scratch / "report.json").write_text(json.dumps(report))
    monkeypatch.setattr(producer, "ROOT", tmp_path)
    rc = producer.main()
    return rc, capsys.readouterr().out


def test_the_committed_frozen_report_carries_both_keys():
    """The guard must be satisfiable against the report that ships."""
    assert set(KEYS) <= _committed().keys()


@pytest.mark.parametrize("key", KEYS)
def test_a_frozen_report_without_a_key_is_refused(monkeypatch, tmp_path, capsys, key):
    report = _committed()
    del report[key]
    rc, printed = _main(monkeypatch, tmp_path, capsys, report)
    assert rc == 2 and "REFUSED" in printed and repr(key) in printed
    assert not (tmp_path / "docs/scorecard/mono-attack-context/model").exists()   # nothing rendered


def test_a_frozen_report_with_a_null_key_is_refused(monkeypatch, tmp_path, capsys):
    report = _committed()
    report["renders"] = None
    rc, printed = _main(monkeypatch, tmp_path, capsys, report)
    assert rc == 2 and "REFUSED" in printed and "'renders'" in printed and "null" in printed


@pytest.mark.parametrize("report, why", [({}, "'renders'"), ([], "not a JSON object"),
                                         (None, "not a JSON object")])
def test_a_frozen_report_that_is_not_an_object_is_refused(monkeypatch, tmp_path, capsys, report, why):
    rc, printed = _main(monkeypatch, tmp_path, capsys, report)
    assert rc == 2 and "REFUSED" in printed and why in printed
