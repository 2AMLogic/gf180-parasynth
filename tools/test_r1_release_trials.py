#!/usr/bin/env python3
"""tools/r1_release_trials.py: a receipt that cannot be read is REFUSED, not a
traceback (#1604, #1623).

Before #1623 `check()` read `rec["verdict"]` &c. with `[]` and nothing caught
the `KeyError`: a partial receipt exited 1 by traceback -- FAIL, "the comparison
ran and disagreed" -- where `tools/exit_contract.py` says an unmet precondition
is exit 2. One control per key through the tool's own `main()`.

The receipt and the reader are real. The simulator (T-PLAY-DIGITAL simulates
for ~45 minutes), the judged tree's `check-receipt` and `git` are stubbed; the
stand-in leaves the receipt on disk, just as `tools/trial.py run` would.
"""
import json
import pathlib
import sys
import tarfile
from types import SimpleNamespace

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import r1_release_trials as rt  # noqa: E402

KEYS = ("verdict", "execution", "verdict_reasons", "controls", "identities")


def receipt():
    """A complete baseline receipt, as `tools/trial.py run` writes it."""
    return {"trial": {"id": "T-PLAY-DIGITAL", "mode": "sim", "candidate": "baseline"},
            "identities": {"source": {"head": "0123456789abcdef"},
                           "integration": {"origin_main": "0123456789abcdef"}},
            "verdict": "PASS", "verdict_reasons": [], "execution": {"status": "complete"},
            "children": [], "controls": []}


@pytest.fixture
def build_box(tmp_path, monkeypatch):
    """A clean `--tree`, a venv, and a trial runner that leaves a receipt and
    names it in its log."""
    tree, venv, out = tmp_path / "tree", tmp_path / "venv", tmp_path / "out"
    tree.mkdir()
    (venv / "bin").mkdir(parents=True)
    state = SimpleNamespace(receipt=None)

    class Run:
        def __init__(self, cmd):
            self.cmd = cmd

        def wait(self):
            return 0

    def popen(cmd, cwd=None, stdout=None, stderr=None, **kw):
        stdout.write(f"receipt: {state.receipt}\n")
        return Run(cmd)

    def run(cmd, *a, **k):
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(rt.subprocess, "Popen", popen)
    monkeypatch.setattr(rt.subprocess, "run", run)
    return tree, venv, out, state


def _main(build_box, capsys, tmp_path, record):
    tree, venv, out, state = build_box
    state.receipt = tmp_path / "receipt.json"
    state.receipt.write_text(json.dumps(record))
    rc = rt.main(["landed", "--tree", str(tree), "--venv", str(venv), "--out", str(out)])
    return rc, capsys.readouterr().out, out


def test_a_complete_receipt_is_checked_not_refused(build_box, capsys, tmp_path):
    """The guards below must be satisfiable against a whole receipt: every job
    is read (this stand-in PASSes where the landed tree is expected to FAIL, so
    the run is UNEXPECTED, exit 1 -- a verdict, not a refusal)."""
    rc, out, out_dir = _main(build_box, capsys, tmp_path, receipt())
    assert rc == 1 and "UNEXPECTED" in out and "REFUSED" not in out
    summary = json.loads((out_dir / "summary.json").read_text())
    assert set(summary["runs"]) == set(rt.LANDED)


def test_the_shipped_receipts_carry_every_key_the_checker_reads():
    with tarfile.open(ROOT / "fpga/reports/r1-candidate/receipts.tgz") as tf:
        shipped = [json.load(tf.extractfile(m)) for m in tf.getmembers()
                   if m.name.endswith("receipt.json")]
    assert shipped
    for rec in shipped:
        for key in KEYS:
            assert rec.get(key) is not None, key


@pytest.mark.parametrize("key", KEYS)
def test_a_receipt_missing_a_key_the_checker_reads_is_refused(build_box, capsys, tmp_path, key):
    rec = receipt()
    del rec[key]
    rc, out, out_dir = _main(build_box, capsys, tmp_path, rec)
    assert rc == 2 and "REFUSED" in out and f"'{key}'" in out
    assert not (out_dir / "summary.json").exists()    # no summary over a partial receipt


@pytest.mark.parametrize("key", KEYS)
def test_a_receipt_with_null_for_a_key_the_checker_reads_is_refused(build_box, capsys, tmp_path, key):
    rec = receipt()
    rec[key] = None
    rc, out, _ = _main(build_box, capsys, tmp_path, rec)
    assert rc == 2 and "REFUSED" in out and f"'{key}'" in out


@pytest.mark.parametrize("record, why", [({}, "'verdict'"), ([], "not a JSON object"), (None, "not a JSON object")])
def test_a_receipt_that_is_not_an_object_is_refused(build_box, capsys, tmp_path, record, why):
    rc, out, _ = _main(build_box, capsys, tmp_path, record)
    assert rc == 2 and "REFUSED" in out and why in out
