#!/usr/bin/env python3
"""fpga/test_coremidi_input.py -- the macOS MIDI input adapter (issue #322).

Hardware-free and deterministic: every session test runs on the device
contract in simulated time (uart_device_sim.SimClock) behind a FAKE CoreMIDI
backend that delivers a script. What they pin:

  * port naming and selection (exact, unique substring, ambiguous, none,
    no ports at all, offline) -- REFUSED, with what exists, never a guess;
  * a missing port refuses before any device is opened;
  * a disconnect mid-session: what arrived before it is played, then the
    session PANICS and records an explicit error, and the CLI exits 1;
  * channel handling: every channel reaches the session byte for byte; the
    session, not the adapter, routes 1 and 10 and refuses the other 14;
  * the MIDIPacketList layout, against CoreMIDI's own encoder on a Mac.

The `mac` tests drive the REAL framework through an in-process virtual source
(no controller needed) and are skipped where CoreMIDI does not exist, so CI
on Linux still passes; on a Mac they are the evidence the ctypes layer works.
"""
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "model"))

import pytest                                              # noqa: E402

import coremidi_input as cmi                               # noqa: E402
import midi_session as ms                                  # noqa: E402
import synth_top_model as stm                              # noqa: E402
import uart_device_sim as dev                              # noqa: E402

LK = [cmi.PortInfo("Launchkey 37 MK3 LKMK3 MIDI Out", 101),
      cmi.PortInfo("Launchkey 37 MK3 LKMK3 DAW Out", 102),
      cmi.PortInfo("M4", 103)]


class FakeBackend:
    """CoreMIDI's interface to PortMidiInput, on a simulated clock. `script`
    is [(t, bytes)] and [(t, "LOST")]; `wait` advances the clock to the next
    scripted instant or the timeout, whichever is first, and delivers it."""

    def __init__(self, clock, ports, script=()):
        self.clock, self.ports = clock, list(ports)
        self.script = sorted(script, key=lambda x: x[0])
        self.on_bytes = self.on_lost = None
        self.disconnected = False

    def sources(self):
        return list(self.ports)

    def connect(self, port, on_bytes, on_lost):
        self.on_bytes, self.on_lost = on_bytes, on_lost
        return port

    def disconnect(self, _handle):
        self.disconnected = True

    def now(self):
        return self.clock.t

    def wait(self, _cv, timeout):
        target = self.clock.t + timeout
        if self.script and self.script[0][0] <= target:
            t = max(self.clock.t, self.script[0][0])
            self.clock.t = t
            while self.script and self.script[0][0] <= t:
                _t, what = self.script.pop(0)
                if what == "LOST":
                    self.on_lost("the source was removed (unplugged or disposed)")
                else:
                    self.on_bytes(t, what)
        else:
            self.clock.t = target


def session():
    clock = dev.SimClock()
    sim = dev.UartDeviceSim(clock=clock)
    ser = dev.SimSerial(sim)
    s = ms.MidiSession(ser, clock=clock, image="tree")
    s.start()
    return s, sim, clock


def executed(sim):
    return [(w[2], w[3], w[4]) for w in sim.writes if w[5] == "event"]


# ---- naming and selection ------------------------------------------------------------
def test_an_exact_name_or_one_unique_substring_selects_the_source():
    assert cmi.select_port(LK, "Launchkey 37 MK3 LKMK3 MIDI Out").uid == 101
    assert cmi.select_port(LK, "lkmk3 midi").uid == 101           # case-insensitive
    assert cmi.select_port(LK, "DAW").uid == 102
    # an exact name wins over it also being a substring of another source
    assert cmi.select_port(LK + [cmi.PortInfo("M4 Mk2", 104)], "M4").uid == 103


@pytest.mark.parametrize("wanted,why", [
    ("Launchkey", "matches 2 MIDI sources"),       # ambiguous: never a silent pick
    ("KeyStep", "no MIDI source matches 'KeyStep'"),
    ("", "no MIDI source name given"),
])
def test_an_ambiguous_or_unknown_name_is_refused_with_what_exists(wanted, why):
    with pytest.raises(cmi.MidiPortRefused) as exc:
        cmi.select_port(LK, wanted)
    assert why in str(exc.value)
    assert "Launchkey 37 MK3 LKMK3 MIDI Out" in str(exc.value)


def test_no_port_at_all_and_an_offline_port_are_refused():
    with pytest.raises(cmi.MidiPortRefused, match="no MIDI sources at all"):
        cmi.select_port([], "Launchkey")
    with pytest.raises(cmi.MidiPortRefused, match="is offline"):
        cmi.select_port([cmi.PortInfo("Launchkey 37 MK3 LKMK3 MIDI Out", 1, offline=True)],
                        "LKMK3 MIDI")


def test_a_missing_port_refuses_before_any_device_is_opened(monkeypatch, capsys):
    monkeypatch.setattr(cmi, "CoreMidiBackend", lambda: FakeBackend(dev.SimClock(), []))

    def no_device(*_a, **_k):
        raise AssertionError("a device was opened for a MIDI input that does not exist")
    monkeypatch.setattr(dev, "UartDeviceSim", no_device)
    rc = ms.main(["--port", "sim", "--midi-in", "coremidi:Launchkey"])
    assert rc == 2
    assert "REFUSED -- no MIDI source matches 'Launchkey'" in capsys.readouterr().err


def test_list_midi_ports_prints_every_source_and_offline(monkeypatch, capsys):
    ports = LK + [cmi.PortInfo("Old Keyboard", 9, offline=True)]
    monkeypatch.setattr(cmi, "CoreMidiBackend", lambda: FakeBackend(dev.SimClock(), ports))
    assert ms.main(["--list-midi-ports"]) == 0
    out = capsys.readouterr().out
    assert "4 CoreMIDI source(s)" in out
    assert "  Launchkey 37 MK3 LKMK3 MIDI Out\n" in out
    assert "  Old Keyboard   (offline)" in out


# ---- the session path: the same feed, the same refusals ------------------------------
def run(script, ports=LK, wanted="LKMK3 MIDI"):
    s, sim, clock = session()
    t0 = clock.t
    src = cmi.PortMidiInput(FakeBackend(clock, ports, [(t0 + t, d) for t, d in script]),
                            wanted)
    fed = []
    real_feed = s.feed
    s.feed = lambda t, data: (fed.append(bytes(data)), real_feed(t, data))[1]
    why = ms.run_live(s, src)
    return s, sim, why, fed


def test_every_channel_reaches_the_session_which_routes_1_and_10_and_refuses_the_rest():
    script, t = [], 0.02
    for ch in range(16):
        on = bytes([0x99, 36, 100]) if ch == 9 else bytes([0x90 | ch, 60, 100])
        script.append((t, on))
        script.append((t + 0.01, bytes([0x80 | ch, on[1], 64])))
        t += 0.03
    script.append((t, "LOST"))
    s, sim, _why, fed = run(script)
    # the adapter filters nothing and alters nothing
    assert fed == [d for _t, d in script if d != "LOST"]
    assert s.stats["groups"]["note-on"] == 1 and s.stats["groups"]["note-off"] == 1
    assert s.stats["groups"]["hit"] == 1 and s.stats["drum_noteoffs"] == 1
    refused = [(r.category, r.raw[0] & 0x0F) for r in s.refusals]
    assert refused == [("channel", ch) for ch in range(16) if ch not in (0, 9)
                       for _ in (0, 1)]                  # its note-on and its note-off
    ex = executed(sim)
    # channel 1's note-off closes the gate before the closing panic's GATE_OFF
    on = ex.index((0, stm.A_GATE_ON, 0))
    assert ex[on:].count((0, stm.A_GATE_OFF, 0)) == 2


def test_a_disconnect_mid_session_plays_what_arrived_then_panics_with_an_explicit_error():
    s, sim, why, fed = run([(0.02, bytes([0x90, 60, 100])),         # a held note
                            (0.05, bytes([0x99, 38, 90])),          # a snare, same instant
                            (0.05, "LOST")])                        # ... as the unplug
    assert fed == [bytes([0x90, 60, 100]), bytes([0x99, 38, 90])]
    assert why.startswith("MIDI input lost") and "unplugged" in why
    assert "unplugged" in s.input_lost
    assert s.stats["groups"] == {"note-on": 1, "hit": 1, "panic": 1}
    ex = executed(sim)
    # the held note's gate closes: the panic reached the device after it
    assert ex.index((0, stm.A_GATE_ON, 0)) < len(ex) - 1 - ex[::-1].index((0, stm.A_GATE_OFF, 0))
    assert s.stats["final_status"]["drops"] == 0 and not s.stats["device_errors"]


def test_a_raw_device_that_errors_mid_session_is_a_lost_input_too():
    s, sim, clock = session()

    class Failing:
        n = 0

        def read(self, timeout):
            self.n += 1
            if self.n == 1:
                clock.sleep(0.02)
                return clock.t, bytes([0x90, 60, 100])
            raise OSError(19, "Operation not supported by device")
    why = ms.run_live(s, Failing())
    assert why.startswith("MIDI input lost") and s.input_lost
    assert s.stats["groups"]["panic"] == 1


def test_the_cli_exits_1_with_an_explicit_error_when_the_input_is_lost(monkeypatch, capsys):
    """The whole CLI, on the real-time simulated board (a pty, as `--port sim`).
    The CLI opens the pty through pyserial, which the m5a-fast CI job does not
    install (it REFUSED there, exit 2, on the first run of this test)."""
    pytest.importorskip("serial", reason="the CLI's serial port needs pyserial")
    class OneNoteThenUnplug:
        port = cmi.PortInfo("fake", 1)
        n = 0

        def read(self, timeout):
            self.n += 1
            if self.n == 1:
                return time.monotonic(), bytes([0x90, 60, 100])
            raise cmi.MidiInputLost("MIDI input 'fake' lost: the source was removed")

        def close(self):
            pass
    monkeypatch.setattr(ms, "open_midi_input",
                        lambda spec: (OneNoteThenUnplug(), None, "fake"))
    rc = ms.main(["--port", "sim", "--midi-in", "coremidi:fake"])
    out, err = capsys.readouterr()
    assert rc == 1
    assert "ERROR -- the MIDI input was lost mid-session" in err
    assert "closed (MIDI input lost (MIDI input 'fake' lost" in out
    assert "'panic': 1" in out


# ---- the packet list layout ------------------------------------------------------------
def _hand_list(packets, align4):
    out = len(packets).to_bytes(4, "little")
    for i, d in enumerate(packets):
        out += (1000 + i).to_bytes(8, "little") + len(d).to_bytes(2, "little") + d
        if align4:
            out += b"\0" * (-len(out) % 4)
    return out


@pytest.mark.parametrize("align4", [True, False])
def test_packet_list_parsing_walks_packets_of_any_length(align4):
    pk = [bytes([0x90, 60, 100]), bytes([0xB0, 74, 10, 0x80]), bytes([0xF8]), bytes([0xFE, 0xFE])]
    got = cmi.parse_packet_list(_hand_list(pk, align4), align4=align4)
    assert got == [(1000 + i, d) for i, d in enumerate(pk)]


# ---- the real framework, hardware-free (macOS only) --------------------------------------
def _backend():
    if sys.platform != "darwin":
        pytest.skip("CoreMIDI exists only on macOS")
    return cmi.CoreMidiBackend()


def test_mac_packet_layout_matches_coremidis_own_encoder():
    b = _backend()
    v = b.create_source(f"parasynth-layout-{os.getpid()}")
    try:
        pk = [bytes([0x90, 60, 100]), bytes([0xB0, 74, 10]), bytes([0xF8]), bytes([0xC0, 5])]
        raw = v.packet_list(pk)
        align4 = cmi.packet_list_align4()
        assert cmi.parse_packet_list(raw, align4=align4) == [(1000 + i, d) for i, d in enumerate(pk)]
        # the control: the other alignment rule misreads CoreMIDI's own list on this CPU
        assert cmi.parse_packet_list(raw, align4=not align4) != \
            [(1000 + i, d) for i, d in enumerate(pk)]
    finally:
        v.dispose()
        b.close()


def test_mac_virtual_source_is_listed_opened_by_name_delivers_and_its_loss_is_seen():
    b = _backend()
    name = f"parasynth-loop-{os.getpid()}"
    v = b.create_source(name)
    try:
        deadline = time.monotonic() + 2.0
        while name not in [p.name for p in b.sources()] and time.monotonic() < deadline:
            time.sleep(0.01)
        src = cmi.PortMidiInput(b, name)
        sent = [bytes([0x90, 60, 100]), bytes([0x99, 36, 127]), bytes([0xBF, 74, 64])]
        for d in sent:
            v.send(d)
        got = b""
        while len(got) < sum(map(len, sent)) and time.monotonic() < deadline + 2.0:
            got += src.read(0.2)[1]
        # channels 1, 10 and 16, unaltered. CoreMIDI may coalesce sends into
        # one packet (it did here, on macOS 26.5): chunking is the session
        # parser's job, so only the byte stream is compared
        assert got == b"".join(sent)
        v.dispose()
        t0 = time.monotonic()
        with pytest.raises(cmi.MidiInputLost, match="removed"):
            while time.monotonic() - t0 < 2.0:
                src.read(0.1)
        src.close()
    finally:
        v.dispose()
        b.close()
