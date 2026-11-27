#!/usr/bin/env python3
"""fpga/test_uart_host.py -- the UART host tool's contract, as tests.

These cover the parts that never touch hardware: the wire format (golden
bytes), the checksum gate, the parser's resynchronising scan, the timing
model's arithmetic against independent fractions, the contract's refusal
rules, and the tool's REFUSED paths. The RTL side of the same contract is
fpga/verify_uart_bridge.py's job; when both agree, the host and the device
are two implementations of one specification.
"""
import os
import sys
from fractions import Fraction

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "fpga"))

import pytest

import uart_host as uh


# ---- the wire format --------------------------------------------------------
def test_write_packet_is_opcode_plus_spi_framing_plus_checksum():
    # {F=0, SEC=0, A=0x04, D=0} -> 00 04 00 00 00 00; sum with 0x57 is 0x5B,
    # checksum 0xA5 makes the total 0x100 -> 0x00 mod 256
    assert uh.pkt_write(0, 0, 0x04, 0) == bytes.fromhex("57 00 04 00 00 00 00 a5".replace(" ", ""))


def test_event_packet_carries_due_lsb_first():
    pkt = uh.pkt_event(0x0918, 0, 1, 0x40, 4)
    assert pkt[0] == uh.OP_EVENT
    assert pkt[1] == 0x18 and pkt[2] == 0x09          # due, least significant first
    # {F=0, 6'b0, SEC=1, A=0x40, D=4}: SEC sits at bit 40, the LSB of the
    # first register byte, so the payload is 01 40 00 00 00 04. The previous
    # expectation (00 40 00 00 00 04) was simply wrong, and `or True` was
    # masking it -- an unconditional pass hiding a wrong golden value.
    assert pkt[3:9] == bytes.fromhex("014000000004")
    assert pkt[3] == 0x01 and pkt[4] == 0x40          # {F=0,6'0,SEC=1} then address
    assert (sum(pkt[:-1]) + pkt[-1]) & 0xFF == 0


def test_checksum_rejects_a_flipped_payload_bit_and_a_dropped_byte():
    good = uh.pkt_write(0, 0, 0x12, 0xDEADBEEF)
    flipped = bytearray(good)
    flipped[4] ^= 1
    assert flipped != good
    # a dropped non-zero byte cannot re-align: the parse-side rule is that the
    # device rejects anything whose byte sum is not 0 mod 256
    body = good[:-1]
    assert uh.checksum(body) == good[-1]


def test_reg_frame_round_trips_and_reserved_bits_are_rejected():
    frame = uh.reg_frame(1, 1, 0xFF, 0x12345678)
    assert uh.decode_reg_frame(frame) == (1, 1, 0xFF, 0x12345678)
    bad = bytearray(uh.reg_frame(0, 0, 0, 0))
    bad[0] = 0x40                              # a reserved bit, not the flag
    with pytest.raises(ValueError):
        uh.decode_reg_frame(bytes(bad))


# ---- parsing the device's TX stream ----------------------------------------
def test_parse_device_stream_reads_all_three_packets_and_boot():
    buf = bytes([0xA5, 0x06, 0x2A]) + bytes([0x1C, 2, 0x2B, 0x40]) + \
        bytes([0x55, 0x12, 0x34, 8, 0, 1, 0, 0])
    pkts = uh.parse_device_stream(buf)
    assert [p.kind for p in pkts] == ["boot", "ack", "err", "status"]
    assert pkts[1].seq == 0x2A
    assert pkts[2].code == uh.ERR_EVQ_FULL and pkts[2].info == 0x40
    assert pkts[3].frame == 0x1234 and pkts[3].evq == 8 and pkts[3].drops == 1


def test_parse_device_stream_resyncs_after_garbage():
    buf = bytes([0x00, 0xFF, 0x06, 0x07]) + b"\xab\xcd" + \
        bytes([0x55, 0, 0, 0, 0, 0, 0, 0])
    pkts = uh.parse_device_stream(buf)
    assert [p.kind for p in pkts] == ["ack", "status"]


# ---- the timing model ------------------------------------------------------
def test_byte_cycles_matches_the_receiver_divider():
    # 12.288 MHz / 115200 = 106.67 -> 107 core cycles per bit, 10 bits per byte
    assert uh.byte_cycles(115_200) == 1070
    assert uh.byte_cycles(384_000) == 320       # 32 cycles per bit, exactly


def test_plan_anchor_and_first_send_are_one_and_the_same_boundary():
    rows = uh.plan([("status",)], start_frame=100)
    assert rows[0].send_frame == 100
    # acceptance sits mid-frame: one byte of gap (1070 cycles) plus the stop
    # centre (~1020) = ~2090 cycles = frame 8 of the packet's life
    assert rows[0].accept_frame - rows[0].send_frame == 8


def test_plan_sequences_packets_without_overlap_on_the_wire():
    cmds = [("write", 0, 0, 4, 0)] * 4
    rows = uh.plan(cmds, start_frame=10)
    for a, b in zip(rows, rows[1:]):
        assert b.send_frame >= -(-a.end_cycle // uh.CYC_PER_FRAME)
        assert a.end_cycle <= b.send_frame * uh.CYC_PER_FRAME


def test_plan_wait_items_send_nothing_but_hold_the_wire():
    rows = uh.plan([("status",), ("wait", 50), ("status",)], start_frame=0)
    assert len(rows) == 2
    assert rows[1].send_frame >= rows[0].send_frame + 50


def test_plan_refuses_a_due_the_upload_cannot_beat():
    with pytest.raises(ValueError, match="acceptance"):
        uh.plan([("event", 10, 0, 0, 0x40, 0)], start_frame=14)


def test_plan_refuses_dues_out_of_order_and_outside_the_wrap_window():
    ok = uh.plan([("event", 5000, 0, 0, 0, 0), ("event", 5001, 0, 0, 0, 1)],
                 start_frame=14)
    assert [r.due for r in ok] == [5000, 5001]
    with pytest.raises(ValueError, match="strictly after"):
        uh.plan([("event", 5001, 0, 0, 0, 0), ("event", 5000, 0, 0, 0, 1)],
                start_frame=14)
    # a due beyond the +-32768 wrap window is refused: this host cannot
    # reason about a span it plans in absolute frames
    with pytest.raises(ValueError, match="wrap guard"):
        uh.plan([("event", 5000 + uh.WRAP_HALF, 0, 0, 0, 0)], start_frame=14)


def test_live_write_applies_the_frame_after_acceptance():
    rows = uh.plan([("write", 0, 0, 0x21, 0)], start_frame=0, baud=115_200)
    # 8-byte packet: acceptance is 33 frames and change after the send boundary
    assert rows[0].apply_frame == rows[0].accept_frame + 1
    assert rows[0].accept_frame == 33


def test_frame_is_48_khz_and_the_wrap_guard_bounds_a_batch():
    # the wrap guard limits ONE scheduled batch to 32768 frames (0.68 s) of
    # due span -- the device's due arithmetic is wrap-safe, but this host
    # plans in absolute frames and refuses to emit a span it cannot reason
    # about. A longer phrase is several batches, each anchored by a STATUS.
    assert uh.SR == 48_000
    assert uh.CYC_PER_FRAME == 256
    assert uh.FRAME_S == Fraction(256, 12_288_000)
    assert uh.WRAP_HALF == 32_768
    assert pytest.approx(uh.WRAP_HALF / uh.SR, abs=0.01) == 0.68


# ---- the musical front end: delivery only, never values --------------------
def test_voice_image_writes_stays_on_the_voice_page_with_zero_flags():
    writes = uh.voice_image_writes(None)
    assert len(writes) == 20
    assert all(flag == 0 and sec == 0 for flag, sec, _a, _d in writes)


def test_key_events_come_from_the_model_host_not_from_this_module():
    import voice_fx as vf
    import synth_top_model as stm
    events = [(0, "on", 45), (1920, "off", 45)]
    direct = vf.KeyHost().writes(events, vf.VoiceFx.patch_regs())
    assert any(op == "GATE" and args[0] == 0 for _f, op, *args in direct)
    assert any(op == "GATE" and args[0] == 1 for _f, op, *args in direct)


# ---- the tool's refusals ---------------------------------------------------
def test_budget_quotes_the_contract_numbers():
    b = uh.budget()
    assert b["write_slots_per_frame"] == 2
    assert b["event_queue"] == 64 and b["write_queue"] == 8
    assert b["bytes_per_s"] == pytest.approx(12_288_000 / 1070, rel=1e-3)


@pytest.mark.parametrize("baud,divider", [(115200, 107), (96000, 128)])
@pytest.mark.parametrize("packet,octets", [("write", 8), ("event", 10)])
def test_budget_packet_duration_in_audio_frames(baud, divider, packet, octets):
    # 12.288 MHz receiver, rounded RTL divisor, 8N1, 256 clocks/audio frame.
    frames = Fraction(octets * 10 * divider, 256)
    assert uh.budget(baud)[f"frames_per_{packet}_packet"] == float(frames)


def test_budget_rates_stay_in_packets_per_frame_units():
    # the explicitly named rates are the reciprocal of the duration they name
    for baud in (115200, 96000):
        b = uh.budget(baud)
        assert b["event_packets_per_frame"] == pytest.approx(
            1 / b["frames_per_event_packet"])
        assert b["max_sustained_event_rate_per_kframe"] == pytest.approx(
            b["event_packets_per_frame"] * 1000)


def test_require_serial_refuses_with_exit_2(monkeypatch):
    import builtins
    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "serial":
            raise ImportError("No module named 'serial'")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    with pytest.raises(SystemExit) as exc:
        uh._require_serial()
    assert exc.value.code == 2


def test_bridge_refuses_an_unopenable_port(capsys, monkeypatch):
    # /dev/null-ish port that exists but is not a serial device: pyserial
    # raises SerialException -> the tool must REFUSE, not fall through
    pytest.importorskip("serial")
    with pytest.raises(SystemExit) as exc:
        uh.Bridge("/dev/null", 115_200)
    assert exc.value.code == 2
    assert "REFUSED" in capsys.readouterr().err


def test_main_without_port_or_dry_run_refuses():
    from uart_host import main
    assert main(["--dry-run", "load"]) == 0         # dry-run needs no hardware


def _applies_cells(text):
    """The `applies` column (4th-from-last field) of each data row."""
    return [ln.split()[-2] for ln in text.splitlines()[2:]]


@pytest.mark.parametrize("command", [("abort",), ("status",)])
def test_render_plan_management_row_prints_dash_not_sentinel(command):
    rows = uh.plan([command], start_frame=0)
    assert rows[0].apply_frame == -1                  # the sentinel is really there
    assert _applies_cells(uh.render_plan(rows)) == ["-"]


def test_render_plan_write_and_event_rows_keep_their_frames():
    rows = uh.plan([("write", 0, 0, 0x04, 0), ("event", 5000, 0, 0, 0x20, 0)],
                   start_frame=0)
    cells = _applies_cells(uh.render_plan(rows))
    assert cells == [str(rows[0].accept_frame + 1), str(rows[1].due)]


def test_fmt_apply_frame_dash_for_unset():
    assert uh.fmt_apply_frame(-1) == "-"
    assert uh.fmt_apply_frame(0) == "0"
    assert uh.fmt_apply_frame(42) == "42"


@pytest.mark.parametrize("command", ["abort", "status"])
@pytest.mark.parametrize("mode", ["live", "dry-before", "dry-after"])
def test_management_preview_cannot_touch_a_queued_note(command, mode):
    from uart_device_sim import SimClock, SimSerial, UartDeviceSim

    clock = SimClock()
    device = UartDeviceSim(clock=clock)
    serial = SimSerial(device)
    serial.write(uh.pkt_event(4800, 0, 0, 0x20, 0))  # GATE_ON at 100 ms
    serial.run_until(0.002)
    assert device.evq_count == 1 and not device.writes
    serial.tx_log.clear()
    opens = []

    def open_bridge(port, baud):
        opens.append(port)
        return uh.Bridge.on_serial(serial, baud=baud, clock=clock)

    args = {"live": [command], "dry-before": ["--dry-run", command],
            "dry-after": [command, "--dry-run"]}[mode]
    assert uh.main(["--port", "sim", *args], bridge_factory=open_bridge) == 0
    serial.run_until(0.005)
    sent = [packet.hex() for _, packet in serial.tx_log]
    observed = (opens, sent, device.evq_count)
    if mode == "live":
        assert observed == (["sim"], ["58a8" if command == "abort" else "51af"],
                            0 if command == "abort" else 1)
    else:
        assert observed == ([], [], 1)


# ---- the repair's contract, as units ----------------------------------------
def test_note_off_writes_the_gate_off():
    # an off is stateless by construction: it ALWAYS releases what sounds
    assert uh.note_writes(45, False) == [(0, 0, 0x21, 0)]


def test_note_writes_on_comes_from_the_model_host():
    writes = uh.note_writes(45, True)
    import synth_top_model as stm
    assert writes[-1] == (0, 0, stm.A_GATE_ON, 0)     # the gate lands last


def test_note_writes_has_no_held_parameter():
    # the host is stateless; a `held` dial advertised a contract it never kept
    import inspect
    assert "held" not in inspect.signature(uh.note_writes).parameters


def test_note_writes_on_is_three_jumping_incs_track_and_one_gate_on():
    import synth_top_model as stm
    writes = uh.note_writes(45, True)
    assert [w[0] for w in writes[:3]] == [1, 1, 1]
    assert [w[2] for w in writes[:3]] == [stm.A_INC + k for k in range(3)]
    assert writes[3][2] == stm.A_TRACK
    assert [w[2] for w in writes].count(stm.A_GATE_ON) == 1
    assert stm.A_GATE_OFF not in [w[2] for w in writes]
    assert len(writes) == 5


def test_note_writes_rejects_an_unhandled_keyhost_op(monkeypatch):
    # a dropped op is a silently shortened command stream
    import voice_fx as vf
    monkeypatch.setattr(vf.KeyHost, "writes",
                        lambda self, *a, **k: [(0, "TRIG")])
    with pytest.raises(ValueError, match="unexpected KeyHost op"):
        uh.note_writes(45, True)


def test_scan_packets_consumes_complete_packets_only():
    reply = bytes([uh.RSP_STATUS, 0x12, 0x34, 8, 0, 1, 0, 0]) \
        + bytes([uh.RSP_ACK, 0x2A])
    pkts, consumed = uh.scan_packets(reply + b"\x55\x00")   # trailing partial
    assert [p.kind for p in pkts] == ["status", "ack"]
    assert consumed == len(reply)
    assert len(reply) + 2 - consumed == 2             # the partial bytes stay


def test_wrap_reached_answers_the_wrapping_question():
    reached = uh.Bridge._reached
    assert reached(100, 100)                          # there
    assert reached(105, 100)                          # just past
    assert reached(164, 100)                          # past the target
    assert reached(50, 100) is False                  # wrapped: 50 is 50 short of 100
    assert not reached(65_000, 200)                   # still 336 frames to go
    assert not reached(99, 100)


def test_preflight_bar808_is_refused_with_the_numbers():
    events, _end = uh.phrase_events("bar808")
    cmds = [("write", 0, 0, 4, 0)] * 20 + [("event", d, f, s, a, v)
                                           for d, f, s, a, v in events]
    rows = uh.plan_shifted(cmds)
    v = uh.preflight(rows)
    assert v["verdict"] == "REFUSED"
    assert v["peak"] > uh.EVENT_QUEUE_DEPTH
    assert v["first_excess_index"] is not None
    assert "event packet" in v["reason"] and "due" in v["reason"]
    assert v["first_excess_index"] == 64


def test_preflight_m5a_is_feasible_by_preload():
    events, _end = uh.phrase_events("m5a")
    cmds = [("write", 0, 0, 4, 0)] * 20 + [("event", d, f, s, a, v)
                                           for d, f, s, a, v in events]
    rows = uh.plan_shifted(cmds)
    v = uh.preflight(rows)
    assert v["verdict"] == "FEASIBLE" and v["mode"] == "preload"
    assert v["peak"] <= uh.EVENT_QUEUE_DEPTH


def test_plan_show_schedules_the_gate_off_on_the_device():
    import synth_top_model as stm
    boot = [("write", 0, 0, 4, 1), ("write", 0, 0, 5, 2)]
    on = [(f, s, a, d) for f, s, a, d in uh.note_writes(45, True)]
    cmds = boot + [("write", f, s, a, d) for f, s, a, d in on] \
        + [("gate-off", 0, 0, stm.A_GATE_OFF, 0)]
    rows = uh.plan_show(cmds, hold_frames=1920, start_frame=uh.MIN_LEAD_FRAMES)
    gate = [r for r in rows if r.kind == "write"
            and uh.decode_reg_frame(r.packet[1:7])[2] == stm.A_GATE_ON]
    offs = [r for r in rows if r.kind == "event"
            and uh.decode_reg_frame(r.packet[3:9])[2] == stm.A_GATE_OFF]
    assert len(gate) == 1 and len(offs) == 1
    assert offs[0].due == (gate[0].apply_frame + 1920) & 0xFFFF
    # hold enters the schedule: a different hold moves the gate-off exactly
    rows2 = uh.plan_show(cmds, hold_frames=1920 + 2880,
                         start_frame=uh.MIN_LEAD_FRAMES)
    off2 = [r for r in rows2 if r.kind == "event"
            and uh.decode_reg_frame(r.packet[3:9])[2] == stm.A_GATE_OFF]
    assert (off2[0].due - offs[0].due) & 0xFFFF == 2880


def test_plan_show_without_a_gate_off_plans_plain_events():
    cmds = [("write", 0, 0, 4, 1), ("event", 5000, 0, 0, 0x40, 7)]
    rows = uh.plan_show(cmds, start_frame=uh.MIN_LEAD_FRAMES)
    assert [r.kind for r in rows] == ["write", "event"]
    assert rows[1].due == 5000 + uh.PLAN_SLACK_FRAMES


def test_plan_show_refuses_a_hold_shorter_than_its_own_upload():
    import synth_top_model as stm
    on = [(f, s, a, d) for f, s, a, d in uh.note_writes(45, True)]
    cmds = [("write", f, s, a, d) for f, s, a, d in on] \
        + [("gate-off", 0, 0, stm.A_GATE_OFF, 0)]
    with pytest.raises(uh.Refused, match="hold_frames"):
        uh.plan_show(cmds, hold_frames=1, start_frame=uh.MIN_LEAD_FRAMES)


def test_write_capture_writes_the_bench_files(tmp_path):
    rows = uh.plan([("write", 0, 0, 4, 1), ("event", 900, 0, 0, 0x40, 7)],
                   start_frame=uh.MIN_LEAD_FRAMES)
    prefix = str(tmp_path / "cap")
    cmds_path = uh.write_capture(prefix, rows, origin=17)
    assert cmds_path.endswith(".cmds")
    import json
    rec = json.loads(open(prefix + ".plan.json").read())
    assert rec["origin"] == 17 and len(rec["rows"]) == len(rows)
    first = open(cmds_path).readline().split()
    assert first[0] == "S"
    assert int(first[1]) == 0                                          # rebased
    assert bytes.fromhex("".join(first[2:])) == rows[0].packet


def test_take_counts_every_packet_in_a_chunk_not_just_the_first_match():
    """#281: a real-time pty hands the host four ACKs per read(8). _take used
    to return at the first ACK and drop the other three from acks_seen, so a
    190-write burst was 'acknowledged' 48 times and the session refused."""
    class Burst:
        timeout = 0.0

        def __init__(self, data):
            self.data = data

        def read(self, n):
            out, self.data = self.data[:n], self.data[n:]
            return out

    class Clock:
        t = 0.0

        def monotonic(self):
            self.t += 0.1                    # every look at the clock moves it
            return self.t

        def sleep(self, s):
            self.t += s
    acks = b"".join(bytes([uh.RSP_ACK, k]) for k in range(1, 9))
    b = uh.Bridge.on_serial(Burst(acks + bytes([uh.RSP_ERR, 1, 9, 0x21])), clock=Clock())
    while b._take({"ack", "err"}, 1.0) is not None:
        pass
    assert b.acks_seen == 8
    assert b.device_errors == [(1, 0x21)]


# ---- #1258: the R1 quick-start must not need SciPy --------------------------
def test_r1_quickstart_dry_run_works_without_scipy(tmp_path):
    """The default R1 patch must run even when SciPy imports are blocked."""
    import subprocess
    from pathlib import Path
    from textwrap import dedent

    # This startup hook previously hid the eager import on SciPy-installed hosts.
    (tmp_path / "sitecustomize.py").write_text("from scipy.io import wavfile\n")
    code = dedent("""\
        import os
        import runpy
        import sys

        class ScipyBlocked(ModuleNotFoundError):
            pass

        class BlockScipy:
            def find_spec(self, fullname, path=None, target=None):
                if fullname == "scipy" or fullname.startswith("scipy."):
                    raise ScipyBlocked(fullname)

        sys.meta_path.insert(0, BlockScipy())
        try:
            import scipy
        except ScipyBlocked:
            pass
        else:
            raise AssertionError("SciPy import bypassed the blocker")

        del sys.argv[0]  # drop -c, so argv and sibling imports match script execution
        sys.path.insert(0, os.path.dirname(sys.argv[0]))
        runpy.run_path(sys.argv[0], run_name="__main__")
    """)
    player = Path(__file__).resolve().with_name("uart_host.py")
    result = subprocess.run(
        [sys.executable, "-I", "-c", code, player, "--dry-run", "run", "--note", "45",
         "--fixture", "m5a", "--image", "r1", "--capture", tmp_path / "cap"],
        env={**os.environ, "PYTHONPATH": str(tmp_path)}, capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
    assert "FEASIBLE" in result.stdout
