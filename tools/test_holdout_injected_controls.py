"""tools/probes/holdout_injected_controls.py: `main()` must keep REFUSED
(exit 2) distinguishable from CONTROL DID NOT FIRE (exit 1) in the process
exit status, because that is the channel `tools/run_all.py` classifies on
(rc in {2, 3, 126} -> NO-VERDICT, anything else non-zero -> FAIL).

Issue #1113. Before the fix, `main()` folded a refusal from `run()` into the
same `bad` counter as a genuine non-fire and the process could only ever exit
0 or 1, so `make controls` recorded both outcomes as FAIL and the Makefile's
"Exit 2 (REFUSED)" comment named a code the probe never emitted.

The five cases below pin the precedence the issue states: a control that
genuinely did not fire (a defect in the seal) outranks a refusal (the
apparatus cannot run), so a mix exits 1, not 2. The mixed case is the input
that defeats a naive "any refusal wins" implementation, per
docs/verification-rules.md rule 8 (a guard ships with the input that defeats
it). Started red: on the unfixed `main()` the refusal-only and refusal+red
cases returned 1, and the summary line said "did not fire" for refusals.
"""
from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent / "probes"))
import holdout_injected_controls as hic  # noqa: E402


def _drive(monkeypatch, results):
    """Run main() with `run()` patched to return `(code, failed)` per control,
    in INJECTS order (GATE_OFF, CLEAN_OFF, STALE_OFF)."""
    order = iter(results)
    monkeypatch.setattr(hic, "run", lambda _name: next(order))
    return hic.main()


def test_every_control_fired_exits_zero(monkeypatch):
    assert _drive(monkeypatch, [(1, ["test_a_seal_gate"]),
                                (1, ["test_b_seal_clean"]),
                                (1, ["test_c_seal_stale"])]) == 0


def test_a_refusal_alone_exits_two_and_says_so(monkeypatch, capsys):
    rc = _drive(monkeypatch, [(2, []), (2, []), (2, [])])
    out = capsys.readouterr().out
    assert rc == 2
    assert "refused" in out
    assert "did not fire" not in out


def test_a_control_that_did_not_fire_alone_exits_one(monkeypatch):
    assert _drive(monkeypatch, [(0, []), (1, ["test_a"]), (1, ["test_b"])]) == 1


def test_a_genuine_non_fire_outranks_a_refusal_exits_one(monkeypatch, capsys):
    # The mixed case: exit 1, not 2 -- a seal defect is worse than the
    # apparatus being unable to run, and both are named in the summary.
    rc = _drive(monkeypatch, [(0, []), (2, []), (1, ["test_a"])])
    out = capsys.readouterr().out
    assert rc == 1
    assert "did not fire" in out and "refused" in out


def test_a_refusal_alongside_only_red_controls_exits_two(monkeypatch):
    # The other boundary of the mix: nothing failed to fire, one refused.
    assert _drive(monkeypatch, [(2, []), (1, ["test_a"]), (1, ["test_b"])]) == 2
