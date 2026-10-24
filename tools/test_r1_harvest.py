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


def put_all(runs, skip=()):
    """A valid-shaped baseline receipt for every expected (trial, mode) in ROWS."""
    for t, mode in h.ROWS:
        if (t, mode) not in skip:
            put(runs, f"{t}/{mode}", receipt(t, mode))


def expected():
    return {f"{t} {mode}" for t, mode in h.ROWS}


def put(runs, name, body):
    d = runs / "trials" / name
    d.mkdir(parents=True)
    (d / "receipt.json").write_text(body if isinstance(body, str) else json.dumps(body))
    return d / "receipt.json"


@pytest.fixture
def fake(monkeypatch):
    """Domain pytest answers PASS; check-receipt answers `valid` unless real=True."""
    state = SimpleNamespace(valid=True, real=False, outputs=[])

    def run(cmd, *a, **k):
        if "pytest" in cmd:
            return subprocess.CompletedProcess(cmd, 0, "1 passed\n", "")
        if "check-receipt" in cmd and not state.real:
            return subprocess.CompletedProcess(cmd, 0 if state.valid else 1, "", "")
        r = _real_run(cmd, *a, **k)
        if "check-receipt" in cmd:
            state.outputs.append((r.stdout or "") + (r.stderr or ""))
        return r
    monkeypatch.setattr(h.subprocess, "run", run)
    return state


def go(tmp_path, capsys):
    rc = h.main(["--runs", str(tmp_path / "runs"), "--to", str(tmp_path / "out")])
    capsys.readouterr()
    summary = json.loads((tmp_path / "out" / "summary.json").read_text())
    return rc, {r["name"]: r for r in summary["runs"]}


def test_valid_receipts_are_receipt_valid_true_and_exit_zero(tmp_path, capsys, fake):
    put_all(tmp_path / "runs")
    rc, rows = go(tmp_path, capsys)
    assert all(rows[n]["receipt_valid"] is True and not rows[n].get("missing")
               for n in expected())
    assert rc == 0


def test_a_receipt_the_real_checker_rejects_is_listed_invalid_and_exits_nonzero(
        tmp_path, capsys, fake):
    fake.real = True                       # the shipped check-receipt, not a stub
    put_all(tmp_path / "runs")             # unhashed, unprovenanced
    rc, rows = go(tmp_path, capsys)
    # invalid BY THE SHIPPED CHECKER: it ran and said REJECTED, it did not crash
    assert fake.outputs and all("REJECTED" in o for o in fake.outputs), fake.outputs
    for n in expected():
        # carried as its own row with its receipt, not dropped and re-added as missing
        assert rows[n].get("receipt") and not rows[n].get("missing"), f"{n} was DROPPED"
        assert rows[n]["receipt_valid"] is False
    assert rc != 0


def test_one_invalid_receipt_among_valid_ones_still_fails_the_run(
        tmp_path, capsys, monkeypatch, fake):
    put_all(tmp_path / "runs")

    def run(cmd, *a, **k):
        if "pytest" in cmd:
            return subprocess.CompletedProcess(cmd, 0, "ok\n", "")
        return subprocess.CompletedProcess(cmd, 1 if "T-LIVE-MIDI/sim" in cmd[-1] else 0, "", "")
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


def _assert_missing(row):
    assert row.get("missing") is True and row["receipt"] is None
    assert row["receipt_valid"] is False and row["verdict"] == "NO VERDICT"


def test_a_partially_missing_trial_is_an_explicit_missing_row_and_fails_the_run(
        tmp_path, capsys, fake):
    """The input that defeated the first version (#628 review): T-LIVE-MIDI rtl
    present, T-LIVE-MIDI sim absent. That used to yield no sim row, exit 0, and
    a playability gate of PASS on half its evidence."""
    put_all(tmp_path / "runs", skip={("T-LIVE-MIDI", "sim")})
    (tmp_path / "runs" / "trials" / "T-LIVE-MIDI" / "empty").mkdir(parents=True)
    rc, rows = go(tmp_path, capsys)
    assert rows["T-LIVE-MIDI rtl"]["receipt_valid"] is True
    _assert_missing(rows["T-LIVE-MIDI sim"])
    assert rc != 0
    # and the scorecard that consumes these rows cannot read the gate as PASS
    import r1_scorecard as r1s
    play = [r for r in rows.values() if r["gate"] == "playability"]
    assert r1s.gate_verdict(play) != "PASS"
    trust = r1s.trust_rows({"runs": list(rows.values())})
    assert r1s.gate_verdict(trust) != "PASS"


def test_a_wholly_missing_trial_is_a_missing_row_per_expected_mode(tmp_path, capsys, fake):
    put_all(tmp_path / "runs", skip={("T-LIVE-MIDI", "sim"), ("T-LIVE-MIDI", "rtl")})
    rc, rows = go(tmp_path, capsys)
    _assert_missing(rows["T-LIVE-MIDI sim"])
    _assert_missing(rows["T-LIVE-MIDI rtl"])
    assert rc != 0


def test_the_domain_test_row_has_receipt_valid_null_and_is_not_counted_valid(
        tmp_path, capsys, fake):
    put_all(tmp_path / "runs")
    rc, rows = go(tmp_path, capsys)
    dom = rows["supported domain + session start (unit)"]
    assert dom["receipt_valid"] is None
    assert {n for n, r in rows.items() if r["receipt_valid"] is True} == expected()


def test_a_failing_domain_test_row_is_fail_but_null_does_not_set_the_exit_code(
        tmp_path, capsys, monkeypatch, fake):
    put_all(tmp_path / "runs")

    def run(cmd, *a, **k):
        if "pytest" in cmd:
            return subprocess.CompletedProcess(cmd, 1, "1 failed\n", "")
        return subprocess.CompletedProcess(cmd, 0, "", "")
    monkeypatch.setattr(h.subprocess, "run", run)
    rc, rows = go(tmp_path, capsys)
    dom = rows["supported domain + session start (unit)"]
    assert dom["verdict"] == "FAIL" and dom["receipt_valid"] is None
    assert rc == 0     # recorded by exit status, carried to the scorecard; not a receipt verdict


def test_non_baseline_candidate_receipts_do_not_stand_in_for_baseline(
        tmp_path, capsys, fake):
    put_all(tmp_path / "runs", skip={("T-DEADLINE", "sim")})
    put(tmp_path / "runs", "T-DEADLINE/late160", receipt(candidate="late160"))
    rc, rows = go(tmp_path, capsys)
    _assert_missing(rows["T-DEADLINE sim"])
    assert rc != 0
