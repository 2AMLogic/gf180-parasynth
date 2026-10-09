"""tools/r1_harvest.py: the receipt_valid verdict the release reader trusts (#609).

Synthetic receipt trees, no build box. Two things are faked and one is not:
the domain-test pytest run is replaced (it is the slow part and is not what is
under test), `check-receipt` is replaced ONLY where a receipt must be valid
(building a hash-bound valid receipt is the trial tool's job); an invalid
receipt always goes through the REAL `tools/trial.py check-receipt`, so the
"invalid" input is invalid by the shipped checker, not by our stub.

`R1_HARVEST_MODULE` points the suite at a mutant copy of the harvester;
tools/r1_harvest_controls.py uses that to prove each test goes red.
"""
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))


def _load():
    p = os.environ.get("R1_HARVEST_MODULE")
    if not p:
        import r1_harvest
        return r1_harvest
    spec = importlib.util.spec_from_file_location("r1_harvest_under_test", p)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


h = _load()
_real_run = subprocess.run


def receipt(trial="T-DEADLINE", mode="sim", candidate="baseline"):
    return {"trial": {"id": trial, "mode": mode, "candidate": candidate},
            "identities": {"source": {"head": "0123456789abcdef"}},
            "verdict": "PASS", "verdict_reasons": [], "execution": {"status": "ran"},
            "children": [], "controls": []}


def put(runs, name, body):
    d = runs / "trials" / name
    d.mkdir(parents=True)
    (d / "receipt.json").write_text(body if isinstance(body, str) else json.dumps(body))
    return d / "receipt.json"


@pytest.fixture
def fake(monkeypatch):
    """Domain pytest answers PASS; check-receipt answers `valid` unless real=True."""
    state = SimpleNamespace(valid=True, real=False)

    def run(cmd, *a, **k):
        if "pytest" in cmd:
            return subprocess.CompletedProcess(cmd, 0, "1 passed\n", "")
        if "check-receipt" in cmd and not state.real:
            return subprocess.CompletedProcess(cmd, 0 if state.valid else 1, "", "")
        return _real_run(cmd, *a, **k)
    monkeypatch.setattr(h.subprocess, "run", run)
    return state


def go(tmp_path, capsys):
    rc = h.main(["--runs", str(tmp_path / "runs"), "--to", str(tmp_path / "out")])
    capsys.readouterr()
    summary = json.loads((tmp_path / "out" / "summary.json").read_text())
    return rc, {r["name"]: r for r in summary["runs"]}


def test_valid_receipt_is_receipt_valid_true_and_exits_zero(tmp_path, capsys, fake):
    put(tmp_path / "runs", "T-DEADLINE/a", receipt())
    rc, rows = go(tmp_path, capsys)
    assert rows["T-DEADLINE sim"]["receipt_valid"] is True
    assert rc == 0


def test_a_receipt_the_real_checker_rejects_is_listed_invalid_and_exits_nonzero(
        tmp_path, capsys, fake):
    fake.real = True                       # the shipped check-receipt, not a stub
    put(tmp_path / "runs", "T-DEADLINE/a", receipt())   # unhashed, unprovenanced
    rc, rows = go(tmp_path, capsys)
    assert "T-DEADLINE sim" in rows, "an invalid receipt was DROPPED"
    assert rows["T-DEADLINE sim"]["receipt_valid"] is False
    assert rc != 0


def test_one_invalid_receipt_among_valid_ones_still_fails_the_run(
        tmp_path, capsys, monkeypatch, fake):
    put(tmp_path / "runs", "T-DEADLINE/a", receipt())
    put(tmp_path / "runs", "T-LIVE-MIDI/a", receipt("T-LIVE-MIDI", "sim"))

    def run(cmd, *a, **k):
        if "pytest" in cmd:
            return subprocess.CompletedProcess(cmd, 0, "ok\n", "")
        return subprocess.CompletedProcess(cmd, 1 if "T-LIVE-MIDI" in cmd[-1] else 0, "", "")
    monkeypatch.setattr(h.subprocess, "run", run)
    rc, rows = go(tmp_path, capsys)
    assert rows["T-DEADLINE sim"]["receipt_valid"] is True
    assert rows["T-LIVE-MIDI sim"]["receipt_valid"] is False
    assert rc != 0


def test_a_malformed_receipt_is_refused_not_skipped(tmp_path, capsys, fake):
    put(tmp_path / "runs", "T-DEADLINE/a", receipt())
    put(tmp_path / "runs", "T-LIVE-MIDI/a", "{ not json")
    with pytest.raises(json.JSONDecodeError):
        h.main(["--runs", str(tmp_path / "runs"), "--to", str(tmp_path / "out")])
    # and no summary was written that omits the broken run
    assert not (tmp_path / "out" / "summary.json").exists()


def test_a_receipt_missing_required_fields_is_refused_not_skipped(tmp_path, capsys, fake):
    put(tmp_path / "runs", "T-LIVE-MIDI/a", {"trial": {"id": "T-LIVE-MIDI"}})
    with pytest.raises(KeyError):
        h.main(["--runs", str(tmp_path / "runs"), "--to", str(tmp_path / "out")])


def test_a_run_dir_with_no_receipt_is_a_missing_row_never_a_valid_one(tmp_path, capsys, fake):
    put(tmp_path / "runs", "T-DEADLINE/a", receipt())
    (tmp_path / "runs" / "trials" / "T-LIVE-MIDI" / "empty").mkdir(parents=True)
    rc, rows = go(tmp_path, capsys)
    assert not any(n.startswith("T-LIVE-MIDI") for n in rows)   # scorecard reports NO VERDICT
    assert rc == 0                                              # by design: absence is not invalid


def test_the_domain_test_row_has_receipt_valid_null_and_is_not_counted_valid(
        tmp_path, capsys, fake):
    put(tmp_path / "runs", "T-DEADLINE/a", receipt())
    rc, rows = go(tmp_path, capsys)
    dom = rows["supported domain + session start (unit)"]
    assert dom["receipt_valid"] is None
    assert [r for r in rows.values() if r["receipt_valid"] is True] == [rows["T-DEADLINE sim"]]


def test_a_failing_domain_test_row_is_fail_but_null_does_not_set_the_exit_code(
        tmp_path, capsys, monkeypatch, fake):
    put(tmp_path / "runs", "T-DEADLINE/a", receipt())

    def run(cmd, *a, **k):
        if "pytest" in cmd:
            return subprocess.CompletedProcess(cmd, 1, "1 failed\n", "")
        return subprocess.CompletedProcess(cmd, 0, "", "")
    monkeypatch.setattr(h.subprocess, "run", run)
    rc, rows = go(tmp_path, capsys)
    dom = rows["supported domain + session start (unit)"]
    assert dom["verdict"] == "FAIL" and dom["receipt_valid"] is None
    assert rc == 0     # recorded by exit status, carried to the scorecard; not a receipt verdict


def test_non_baseline_candidate_receipts_are_not_rows(tmp_path, capsys, fake):
    put(tmp_path / "runs", "T-DEADLINE/a", receipt(candidate="late160"))
    rc, rows = go(tmp_path, capsys)
    assert "T-DEADLINE sim" not in rows
