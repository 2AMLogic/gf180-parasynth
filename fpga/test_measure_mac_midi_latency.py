#!/usr/bin/env python3
"""fpga/test_measure_mac_midi_latency.py -- the latency apparatus's known answer (#322).

fpga/measure_mac_midi_latency.py computes receipt -> applied itself, because
T-LIVE-MIDI's check() cannot follow a real clock's re-anchoring. So its
pairing and endpoint arithmetic must first reproduce check()'s -- which pairs
through the independently built oracle -- on SIMULATED time, where both see
the same run. And its one control (a host stall) must move `on_time`.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import measure_mac_midi_latency as mml                     # noqa: E402


def test_the_probe_reproduces_check_latency_on_simulated_time():
    mine, ref = mml.sim_reference(5.0)
    assert ref["verdict"] == "PASS", ref["reasons"]
    theirs = ref["latency"]
    assert mine["endpoint_ms"]["n"] == theirs["n"] > 200
    for k in ("min", "p50", "p95", "p99", "max"):
        assert abs(mine["endpoint_ms"][k] - theirs[k + "_ms"]) < 1e-9, k
    assert mine["props"] == {"target": True, "all_delivered": True, "on_time": True,
                             "load_admitted": True}
    assert mine["accounting_complete"] and mine["lost"] == 0
    assert mine["anchors_unpaired"] == 0
    assert max(map(abs, mine["map_error_frames"].values())) <= 1   # check()'s own tolerance


def test_one_host_stall_moves_on_time_on_simulated_time():
    """The control, deterministic: the same stall the Mac run injects."""
    mine, _ref = mml.sim_reference(5.0, stall_ms=25.0)
    assert not mine["props"]["on_time"]
    assert mine["anchors_off_due"] or mine["device_errors"] or mine["deadline_misses"]


# ---- host overload (timing contract 3): reported, rejected where defined, recovered --
import pytest                                               # noqa: E402

import live_midi_contract as C                              # noqa: E402


@pytest.mark.parametrize("stall_ms", [25.0, 250.0, 1000.0])
def test_a_host_stall_loses_nothing_and_recovers_at_once(stall_ms):
    """Every offered message is accounted for and none is lost. Packets that
    left late were re-dated and counted, so the device reports no error. Notes
    reaching the host more than STALE_MS late are refused as `stale`; nothing
    else is. No message received after the stall ends misses the target."""
    r, _ = mml.sim_reference(8.0, stall_ms=stall_ms)
    assert r["accounting_complete"] and r["lost"] == 0, r["accounting"]
    assert r["deadline_misses"] >= 1 and r["device_errors"] == []
    stale = r["accounting"].get("refused: stale", 0)
    assert (stale > 0) == (stall_ms > C.STALE_MS)
    assert r["stall"]["over_target_after_end"] == 0
    # no note or drum hit sounds more than STALE_MS (plus the lookahead) late
    worst = max(r["endpoint_by_kind_max_ms"].get(k, 0.0) for k in ("note-on", "hit"))
    assert worst <= C.STALE_MS + C.LOOKAHEAD_MS + 1.0, r["endpoint_by_kind_max_ms"]


def test_control_no_redate_a_long_stall_loses_events():
    """Re-dating removed (a late packet sent with its stale due): the same 1 s
    stall LOSES events -- the defect measured before the repair."""
    r, _ = mml.sim_reference(8.0, stall_ms=1000.0, inject={"NO_REDATE"})
    assert r["accounting_complete"] and r["lost"] > 0, r["accounting"]


def test_control_no_stale_notes_sound_long_after_the_key():
    """Stale refusal removed: notes and hits received during a 1 s stall are
    played up to a second late instead of refused."""
    r, _ = mml.sim_reference(8.0, stall_ms=1000.0, inject={"NO_STALE"})
    worst = max(r["endpoint_by_kind_max_ms"].get(k, 0.0) for k in ("note-on", "hit"))
    assert worst > C.STALE_MS + C.LOOKAHEAD_MS + 1.0, r["endpoint_by_kind_max_ms"]


def test_a_stale_note_is_refused_but_its_note_off_and_a_knob_are_delivered():
    import midi_session as ms
    import uart_device_sim as dev
    clock = dev.SimClock()
    sim = dev.UartDeviceSim(clock=clock)
    s = ms.MidiSession(dev.SimSerial(sim), clock=clock, image="tree")
    s.start()
    t_old = clock.t
    clock.sleep(0.2)                                         # the loop stalled 200 ms
    s.feed(t_old, bytes([0x90, 60, 100, 0xB0, 74, 64]))
    assert [r.category for r in s.refusals] == ["stale"]
    assert s.stats["groups"].get("knob") == 1 and "note-on" not in s.stats["groups"]
    s.feed(clock.t, bytes([0x90, 62, 100]))                  # fresh: played
    assert s.stats["groups"].get("note-on") == 1


def test_a_note_handed_over_late_after_a_kick_is_not_dropped():
    """#339: the device DROPS an event whose due is before the last one it
    queued. A kick's timed tail (the BD attack window) is sent with a due
    ~190 frames after its anchor. A note received 1 ms after the kick but
    handed over 11 ms later (host hold; not late, not stale) is scheduled
    from its receipt -- BEFORE that already-sent tail. The host holds it at
    the last sent due and counts it; the device drops nothing. Control
    NO_ORDER_GUARD: the device drops every packet of the note (ERR 3)."""
    import midi_session as ms
    import synth_top_model as stm
    import uart_device_sim as dev

    def run(inject):
        clock = dev.SimClock()
        sim = dev.UartDeviceSim(clock=clock)
        s = ms.MidiSession(dev.SimSerial(sim), clock=clock, image="tree", inject=inject)
        s.start()
        t = clock.t + 0.01
        s.service(t)
        s.feed(t, bytes([0x99, 36, 100]))               # the kick, tail included
        s.service(t + 0.012)
        s.feed(t + 0.001, bytes([0x90, 60, 100]))       # received t+1 ms, handed over t+12 ms
        s.service(clock.t + 0.1)
        s.close()
        on = [w for w in sim.writes if w[5] == "event" and w[3] == stm.A_GATE_ON]
        return s, sim, on
    s, sim, on = run(set())
    assert s.stats["order_guarded"] >= 1 and s.stats["deadline_misses"] == 0
    assert not sim.errors and sim.drops == 0 and len(on) == 1
    s, sim, on = run({"NO_ORDER_GUARD"})
    assert s.stats["order_guarded"] == 0 and not on
    assert [e for e in sim.errors if e[0] == 3]
