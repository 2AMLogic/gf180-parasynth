"""verify_live_midi's frame unwrap refuses what it cannot measure (#479).

Same defect class as #474 (verify_rolling_playback): `_unwrap` reads a 16-bit
step >= 0x8000 as backward, so a true forward gap that long is silently
mis-unwrapped. Two sites use it: the executed-write log (reference:
`sim.write_frames`) and the receipt log (reference: `sim.received_frames`).
"""
from __future__ import annotations

import pytest

import uart_device_sim as dev
import verify_live_midi as vlm
import verify_rolling_playback as vrp


def _log16(frames):
    return [f & 0xFFFF for f in frames]


def test_one_shared_unwrap_not_a_copy():
    assert vlm._unwrap is vrp._unwrap
    assert vlm.UnwrapRefused is vrp.UnwrapRefused


@pytest.mark.parametrize("what", ["executed writes", "receipts"])
def test_a_schedule_with_a_gap_at_the_bound_refuses(what):
    truth = [70000, 70000 + 0x8000]          # true forward gap == bound
    with pytest.raises(vlm.UnwrapRefused):
        vlm.unwrap_checked(_log16(truth), 70000, [70000] + truth, truth, what)


def test_gap_just_under_the_bound_answers_across_a_wrap():
    truth = [65000, 65000 + 0x7FFF, 65000 + 2 * 0x7FFF]
    out = vlm.unwrap_checked(_log16(truth), 64000, [64000] + truth, truth, "x")
    assert out == truth


def test_an_aliased_step_is_caught_by_the_independent_log():
    # the EXPECTED schedule looks fine (small gaps) but the device actually
    # delivered the second write 0x9000 late: the 16-bit log aliases it
    truth = [1000, 1100 + 0x9000, 1200 + 0x9000]
    expected = [900, 1000, 1100, 1200]
    with pytest.raises(vlm.UnwrapRefused, match="different"):
        vlm.unwrap_checked(_log16(truth), 900, expected, truth, "x")


def test_a_length_mismatch_refuses():
    with pytest.raises(vlm.UnwrapRefused):
        vlm.unwrap_checked([1, 2], 0, [0, 1, 2], [1], "x")


def test_sim_keeps_an_unwrapped_receipt_log_parallel_to_received():
    run = vlm.run_session("coverage")
    s = run["sim"]
    assert len(s.received_frames) == len(s.received)
    pairs = [(v[0], a) for (k, v), a in zip(s.received, s.received_frames)
             if k in ("write", "event")]
    assert pairs and all(f == a & 0xFFFF for f, a in pairs)


@pytest.mark.parametrize("epoch", [0, 0xFF00, 0xFFFF - 5000])
def test_a_real_session_across_a_wrap_still_answers(epoch):
    run = vlm.run_session("coverage", epoch=epoch)
    r = vlm.check(run)
    assert not any("REFUSED" in p for p in r["preconditions"]), r["preconditions"]
    assert r["verdict"] == "PASS", r["reasons"]


def _session():
    return vlm.run_session("coverage")


def test_check_reports_no_verdict_when_the_write_log_is_aliased():
    run = _session()
    sim = run["sim"]
    ev = [i for i, w in enumerate(sim.writes) if w[5] == "event"]
    sim.write_frames[ev[len(ev) // 2]] += 0x9000      # injected aliased step
    r = vlm.check(run)
    assert r["verdict"] == "NO VERDICT"
    assert any("REFUSED" in p and "executed writes" in p for p in r["preconditions"])


def test_check_reports_no_verdict_when_the_receipt_log_is_aliased():
    run = _session()
    sim = run["sim"]
    ev = [i for i, (k, _) in enumerate(sim.received) if k == "event"]
    sim.received_frames[ev[len(ev) // 2]] += 0x9000
    r = vlm.check(run)
    assert r["verdict"] == "NO VERDICT"
    assert any("REFUSED" in p and "receipts" in p for p in r["preconditions"])
