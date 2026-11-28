#!/usr/bin/env python3
"""fpga/test_late_events.py -- the late-event policy and the device model (#329).

The RTL half (iverilog, tb_uart_late.v) runs on the box through
`fpga/verify_late_events.py`; these are the parts that need no simulator."""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import json                                              # noqa: E402
import subprocess                                          # noqa: E402

import pytest                                              # noqa: E402

import late_event_policy as pol                            # noqa: E402
import verify_late_events as vle                           # noqa: E402


def test_policy_classification_at_the_window_edges():
    assert not pol.is_late(100, 101) and not pol.is_late(100, 100 + 32767)
    assert pol.is_late(100, 100) and pol.is_late(100, 100 + 32768) and pol.is_late(100, 99)
    assert not pol.is_late(65535, 0) and pol.is_late(0, 65535)          # across the wrap


def test_policy_late_is_next_frame_and_queue_order_holds():
    r = pol.expected([(100, 100), (100, 90), (100, 200), (101, 150)])
    assert [(o.verdict, o.executes) for o in r] == [
        ("late", 101), ("drop-order", None), ("ack", 200), ("drop-order", None)]
    # a late event behind a queued event due later is dropped (P3, #339)
    r = pol.expected([(100, 101), (100, 100)])
    assert [o.verdict for o in r] == ["ack", "drop-order"]


def test_policy_p3_judges_the_sent_due_against_the_tail():
    """#339: a late event whose sent due is before a draining tail's due is
    dropped (ERR 3), not re-dated behind the tail; one after it is accepted."""
    burst = [(10 + i, 100) for i in range(10)]                 # 10 due in frame 100
    r = pol.expected(burst + [(102, 95), (102, 101)])
    assert (r[10].verdict, r[10].error, r[10].executes) == ("drop-order", 3, None)
    assert (r[11].verdict, r[11].executes) == ("late", 105)


def test_policy_overflow_drops_and_recovers():
    ev = [(10 + i, 1000) for i in range(66)] + [(2000, 2005)]
    r = pol.expected(ev)
    assert [o.verdict for o in r[64:66]] == ["drop-full", "drop-full"]
    assert r[63].executes == 1031 and r[66].verdict == "ack" and r[66].executes == 2005


def test_the_repaired_model_meets_the_policy_on_every_scenario(tmp_path):
    assert vle.main(["--model-only", "--json", str(tmp_path / "v.json")]) == 0


@pytest.mark.parametrize("names", [[], ["boundry"]], ids=["empty", "unknown"])
def test_invalid_scenario_selection_preserves_previous_result(tmp_path, names):
    record = tmp_path / "v.json"
    cli = [sys.executable, str(vle.ROOT / "fpga/verify_late_events.py"),
           "--model-only", "--workdir", str(tmp_path), "--json", str(record), "--scenario"]
    subprocess.run([*cli, "boundary"], check=True)
    previous = record.read_bytes()
    result = subprocess.run([*cli, *names], capture_output=True, text=True)
    assert result.returncode == 2, (result.stdout, result.stderr)
    assert "--scenario" in result.stderr
    assert record.read_bytes() == previous


def test_the_model_as_shipped_before_329_is_caught(tmp_path):
    """The control: the exact pre-#329 model, from git, executes accepted
    events in the wrong frame (or a revolution late, beyond the run)."""
    have = subprocess.run(["git", "-C", str(vle.ROOT), "cat-file", "-e",
                           vle.PRE_329_MODEL_COMMIT + "^{commit}"], capture_output=True)
    if have.returncode:
        pytest.skip("the pre-#329 model commit is not in this clone (shallow checkout)")
    assert vle.main(["--model-only", "--control", "model-revolution",
                     "--json", str(tmp_path / "c.json")]) == 0


# -------------------------------- a simulation that never finishes (#1690) ----
class _NoSimulator:
    """subprocess.run replacement for the RTL half: git and iverilog answer, and
    the child named by `hangs` never finishes, which is TimeoutExpired with
    nothing captured. Every call is recorded with its deadline."""

    def __init__(self, hangs):
        self.hangs, self.calls = hangs, []

    def __call__(self, cmd, **kwargs):
        self.calls.append((list(cmd), kwargs.get("timeout")))
        if self.hangs(cmd):
            raise subprocess.TimeoutExpired(cmd, kwargs.get("timeout"))
        out = "deadbeef\n" if cmd[0] == "git" else ""
        return subprocess.CompletedProcess(cmd, 0, stdout=out, stderr="")


def test_a_hung_vvp_is_refused_and_replaces_the_earlier_record(tmp_path, monkeypatch, capsys):
    """The vvp run has a 3600 s deadline. A simulation that failed was already
    NO VERDICT; one that hung left main() as a TimeoutExpired traceback, so the
    record on disk stayed the earlier run's. A hang measured no more than a
    failure did: NO VERDICT (REFUSED, exit 2) and the record replaced."""
    record = tmp_path / "v.json"
    assert vle.main(["--model-only", "--json", str(record)]) == 0
    assert json.loads(record.read_text())["verdict"] == "PASS"
    child = _NoSimulator(hangs=lambda cmd: cmd[0] == "vvp")
    monkeypatch.setattr(vle.subprocess, "run", child)
    assert vle.main(["--json", str(record), "--scenario", "boundary"]) == 2
    assert [deadline for cmd, deadline in child.calls if cmd[0] == "vvp"] == [3600]
    after = json.loads(record.read_text())
    assert after["verdict"] == "NO VERDICT"
    assert after["rtl"]["boundary"]["verdict"] == "NO VERDICT"
    why = after["rtl"]["boundary"]["why"]
    assert "vvp" in why and "3600" in why
    out = capsys.readouterr().out
    assert f"late-events RTL[boundary]: NO VERDICT -- {why}" in out
    assert out.rstrip().endswith("late-events: NO VERDICT")


def test_a_timeout_outside_the_vvp_call_is_not_refused(tmp_path, monkeypatch):
    """The handler sits on the vvp call, which is the row. A TimeoutExpired from
    anywhere else -- iverilog, which runs with no deadline -- must leave main()
    as it arrived, so the hang test is green because of that handler and not a
    wider catch."""
    child = _NoSimulator(hangs=lambda cmd: cmd[0] == "iverilog")
    monkeypatch.setattr(vle.subprocess, "run", child)
    with pytest.raises(subprocess.TimeoutExpired):
        vle.main(["--json", str(tmp_path / "v.json"), "--scenario", "boundary"])
    assert not (tmp_path / "v.json").exists()
