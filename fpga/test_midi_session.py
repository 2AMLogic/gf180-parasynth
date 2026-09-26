#!/usr/bin/env python3
"""fpga/test_midi_session.py -- the live MIDI session's parts, as tests (#281).

The whole-session behaviour is fpga/verify_live_midi.py's job (an independent
schedule, eleven properties, three controls). These pin the pieces that
harness does not isolate: the byte parser, the incremental note logic against
the model's own KeyHost, each refusal path, Active Sensing, the stdlib MIDI
input, and that the session's maps are the documented ones.
"""
import os
import random
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "model"))

import pytest                                              # noqa: E402

import live_midi_contract as C                             # noqa: E402
import midi_session as ms                                  # noqa: E402
import uart_device_sim as dev                              # noqa: E402
import verify_live_midi as vlm                             # noqa: E402
import voice_fx as vf                                      # noqa: E402
import synth_top_model as stm                              # noqa: E402


# ---- the parser ---------------------------------------------------------------
def test_parser_expands_running_status_and_survives_any_chunking():
    stream = bytes([0x90, 60, 100, 62, 90, 64, 0, 0xB0, 74, 10, 75, 20])
    want = [bytes([0x90, 60, 100]), bytes([0x90, 62, 90]), bytes([0x90, 64, 0]),
            bytes([0xB0, 74, 10]), bytes([0xB0, 75, 20])]
    for cut in range(1, len(stream)):
        p = ms.MidiParser()
        assert p.feed(stream[:cut]) + p.feed(stream[cut:]) == want


def test_parser_delivers_realtime_inside_a_message_and_keeps_the_message():
    p = ms.MidiParser()
    assert p.feed(bytes([0x90, 60, 0xF8, 100])) == [bytes([0xF8]), bytes([0x90, 60, 100])]


def test_parser_sysex_system_common_and_stray_data_are_delivered_to_be_refused():
    p = ms.MidiParser()
    out = p.feed(bytes([0x05, 0xF0, 1, 2, 3, 0xF7, 0xF2, 1, 2, 0xF6]))
    assert out == [bytes([0x05]), bytes([0xF0, 1, 2, 3, 0xF7]), bytes([0xF2, 1, 2]),
                   bytes([0xF6])]
    # system common cancels running status: a bare data byte after it is stray
    assert p.feed(bytes([0x40])) == [bytes([0x40])]


# ---- the note logic, against the model's reference host ---------------------------
def test_monokeys_steps_to_exactly_what_keyhost_computes_in_batch():
    regs = vf.VoiceFx.patch_regs()
    rng = random.Random(7)
    for trial in range(40):
        events, down = [], set()
        for i in range(30):
            note = rng.choice((48, 50, 52, 55, 57, 60))
            op = "off" if (note in down and rng.random() < 0.6) else "on"
            if rng.random() < 0.1:
                op = "off"                       # releases of keys that are not held
            (down.discard if op == "off" else down.add)(note)
            events.append((i, op, note))
        want = []
        for f, kind, *args in vf.KeyHost().writes(events, regs):
            if kind == "INC":
                want.append((f, (1 if args[2] else 0, 0, stm.A_INC + args[0], args[1])))
            elif kind == "TRACK":
                want.append((f, (0, 0, stm.A_TRACK, args[0])))
            else:
                want.append((f, (0, 0, stm.A_GATE_ON if args[0] else stm.A_GATE_OFF, 0)))
        keys, got = ms.MonoKeys(regs), []
        for f, op, note in events:
            planned = keys.plan(op == "on", note)
            if planned:
                got += [(f, w) for w in planned[0]]
                keys.commit(planned[1])
        assert got == want, f"trial {trial}"


# ---- one short session on the device contract ------------------------------------
def session(patch=None, *, epoch=0):
    clock = dev.SimClock()
    sim = dev.UartDeviceSim(epoch_frame=epoch, clock=clock)
    ser = dev.SimSerial(sim)
    s = ms.MidiSession(ser, clock=clock, patch=patch)
    s.start()
    return s, sim, ser, clock


def play(s, clock, msgs, dt=0.010):
    t = clock.t + 0.01
    for m in msgs:
        s.service(t)
        s.feed(t, bytes(m))
        t += dt
    s.close()


def executed(sim):
    return [(w[2], w[3], w[4]) for w in sim.writes if w[5] == "event"]


@pytest.mark.parametrize("msg,category", [
    ((0xB0, 64, 127), "sustain"), ((0xB9, 64, 127), "sustain"), ((0xE0, 0, 64), "pitch-bend"),
    ((0xC0, 3), "program-change"), ((0xD0, 40), "aftertouch"), ((0xA0, 60, 9), "poly-aftertouch"),
    ((0xB0, 121, 0), "cc-unsupported"), ((0xB0, 1, 64), "mod-unrouted"),
    ((0x92, 60, 100), "channel"), ((0x99, 37, 100), "unmapped-drum"),
    ((0x90, 127, 100), "domain"), ((0x90, 5, 100), "domain"), ((0xF8,), "realtime"),
    ((0xF0, 1, 0xF7), "sysex"), ((0xF3, 1), "system"), ((0x33,), "stray-data"),
])
def test_every_unsupported_message_is_refused_by_name_and_writes_nothing(msg, category):
    s, sim, _ser, clock = session()
    play(s, clock, [msg])
    assert [r.category for r in s.refusals] == [category]
    # only the closing panic reaches the device
    assert executed(sim) == [(0, stm.A_GATE_OFF, 0), (1, 0x00, 0)]


def test_velocity_zero_is_note_off_and_drum_note_offs_do_nothing():
    a, sim_a, _, ca = session()
    play(a, ca, [(0x90, 60, 100), (0x80, 60, 64), (0x89, 36, 0)])
    b, sim_b, _, cb = session()
    play(b, cb, [(0x90, 60, 100), (0x90, 60, 0), (0x99, 36, 0)])
    assert executed(sim_a) == executed(sim_b)
    assert a.stats["drum_noteoffs"] == 1 and not a.refusals and not b.refusals


def test_mod_wheel_is_available_when_the_patch_routes_modulation():
    patch = vf.VoiceFx.patch_regs(filt_mod=True)
    s, sim, _, clock = session(patch)
    play(s, clock, [(0xB0, 1, 127)])
    assert not s.refusals
    assert (0, stm.A_MWHEEL, vf.MMIX_FULL) in executed(sim)


def test_close_always_panics_a_held_note():
    s, sim, _, clock = session()
    play(s, clock, [(0x90, 60, 100)])
    ex = executed(sim)
    assert ex.index((0, stm.A_GATE_ON, 0)) < ex.index((0, stm.A_GATE_OFF, 0))
    assert s.stats["final_status"] == {"evq": 0, "wrq": 0, "drops": 0, "errs": 0, "flags": 0}


def test_active_sensing_silence_is_a_lost_connection_and_panics():
    s, sim, _, clock = session()
    t = clock.t + 0.01
    s.service(t)
    s.feed(t, bytes([0xFE, 0x90, 60, 100]))
    s.service(t + 0.25)
    assert (0, stm.A_GATE_OFF, 0) not in executed(sim)     # 250 ms: still connected
    s.service(t + C.ACTIVE_SENSING_TIMEOUT_S + 0.05)
    assert s.stats["groups"].get("panic") == 1
    s.service(t + 0.5)
    assert (0, stm.A_GATE_OFF, 0) in executed(sim)


def test_a_device_reset_mid_session_is_seen_on_the_wire():
    s, sim, ser, clock = session()
    t = clock.t + 0.01
    s.service(t)
    s.feed(t, bytes([0x90, 60, 100]))
    ser.reset()
    s.service(t + 0.1)
    assert s.stats["boots"] == 1


def test_the_time_map_holds_across_a_counter_wrap():
    s, sim, _, clock = session(epoch=65400)
    t = clock.t + 1.0
    truth = 65400 + int((t - sim._t0) * C.SR)
    assert abs(s.frame_of(t) % 65536 - truth % 65536) <= 1


# ---- the documented maps: the session and the oracle agree ------------------------
def test_session_and_oracle_drum_maps_are_the_documented_one():
    assert ms.DRUM_MAP == vlm.DRUM_MAP
    assert set(ms.DRUM_MAP.values()) <= set(__import__("drums_fx").STOP_NAMES)


def test_raw_midi_input_reads_a_pipe_and_reports_end_of_input():
    r, w = os.pipe()
    src = ms.RawMidiInput(r)
    assert src.read(0.0)[1] == b""
    os.write(w, bytes([0x90, 60, 100]))
    assert src.read(1.0)[1] == bytes([0x90, 60, 100])
    os.close(w)
    assert src.read(1.0)[1] is None
    src.close()


# ---- the harness itself ----------------------------------------------------------
def test_each_control_is_caught_by_its_own_property():
    for name, (_sc, must, _why) in vlm.CONTROLS.items():
        c = vlm.run_control(name)
        assert c["caught"], (name, c["reasons"])
        assert all(c["matrix"][p] == "MOVED" for p in must), (name, c["matrix"])


def test_the_harness_is_red_against_the_stub():
    r = vlm.check(vlm.run_session("coverage", stub=True))
    assert r["verdict"] == "FAIL"
    assert r["props"]["static_image"]["moved"] and r["props"]["voice_gate"]["moved"]


def test_a_wrong_time_map_is_no_verdict_not_a_result():
    run = vlm.run_session("coverage")
    s = run["session"]
    s.anchor = (s.anchor[0] + 3, s.anchor[1])
    assert vlm.check(run)["verdict"] == "NO VERDICT"


def test_release_domain_property_moves_when_the_session_lets_a_note_out(monkeypatch):
    """#255's validator is an external check: with the session's own domain
    refusal disabled, MIDI 127 reaches the device and check_stream rejects it."""
    monkeypatch.setattr(ms.qd, "check_note", lambda note, regs: None)
    run = vlm.run_session("coverage")
    r = vlm.check(run)
    assert r["props"]["release_domain"]["moved"], r["props"]["release_domain"]
    assert "INC_RANGE" in r["props"]["release_domain"]["detail"]
