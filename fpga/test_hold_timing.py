#!/usr/bin/env python3
"""fpga/test_hold_timing.py -- the held-note hold, asserted on the DEVICE (#306).

The acceptance assertion is the device's: gate-on to gate-off, read from the
scripted device's executed-write log by register identity on its unwrapped
timeline (fpga/hold_timing.py), through the shipped CLI
(`uart_host.main(..., bridge_factory=Harness.factory)`). Never the planner's
numbers, never the host's own timing record.
"""
import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import hold_timing as ht                          # noqa: E402
import uart_host as uh                            # noqa: E402


# ---- the acceptance assertion: start red on the historical host --------------
@pytest.mark.parametrize("key", sorted(ht.HELD_COMMANDS))
def test_the_cli_delivers_the_requested_hold_on_the_device(key):
    """Requested 1920 frames; the device must hold 1920 within the host's
    own per-run bound, and that bound within the declared maximum. On the
    tree before #306 this FAILED on all three pinned commands: the device
    held 3155 frames (error +1235) and the host claimed no bound."""
    m = ht.run_condition(dict(key=key))
    assert m["verdict"] == ht.PASS, (m["reason"], m.get("host_record"))


# ---- conditions NOT used to select the approach -------------------------------
@pytest.mark.parametrize("cond", ht.untouched(),
                         ids=lambda c: "-".join(f"{k}={v}" for k, v in c.items()))
def test_untouched_conditions(cond):
    """Delays both ways and asymmetric, counter wraps during the upload and
    inside the hold, the shortest supported hold, a long hold, holds that must
    be REFUSED, and a supported hold a slow link cannot meet (refused by the
    device's own late ERR, note released)."""
    m = ht.run_condition(cond)
    assert m["ok"], (m["verdict"], m["expected"], m["reason"])


def test_a_refused_deadline_still_releases_the_note():
    """The device log, not the exit code: after a missed-deadline refusal the
    gate-off EXECUTED -- a refusal that leaves a note sounding is a FAIL."""
    hmin = uh.hold_min_frames()
    m = ht.measure(ht.HELD_COMMANDS["held-default"], hold_frames=hmin,
                   reply_delay_s=0.004, tx_delay_s=0.004)
    assert m["rc"] == 2 and "deadline was missed" in m["stderr_tail"], m["stderr_tail"]
    assert m["device"]["off"] is not None


# ---- the controls: each must fail THE HOLD assertion --------------------------
@pytest.mark.parametrize("name", sorted(ht.CONTROLS))
def test_controls_fail_the_hold_assertion_for_their_reason(name):
    r = ht.run_control(name)
    assert r["caught"] is True, r
    assert r["matrix"]["hold"] == "MOVED", r["matrix"]


def test_the_historical_control_reproduces_the_recorded_baseline():
    """HOLD_ACK_DRAIN is the pre-#306 path verbatim: it must reproduce the
    recorded numbers (fpga/reports/hold-timing-306-baseline.json), or the
    control is not the bug it claims to be."""
    m = ht.run_condition(dict(key="held-default"), inject="HOLD_ACK_DRAIN")
    assert (m["planned_hold"], m["actual_hold"], m["hold_error"]) == (3121, 3155, 1235)


def test_the_forged_log_is_invisible_to_everything_but_the_device():
    """The deceptive-log control: the host's record and plan claim 1920 while
    the device applies 2520. Every host-side property is BLIND; only the
    device-read hold moves -- which is why the assertion reads the device."""
    r = ht.run_control("HOLD_FORGED_LOG")
    assert r["host_record_hold"] == 1920 and r["device_hold"] == 2520
    assert [k for k, v in r["matrix"].items() if v == "MOVED"] == ["hold"]


def test_a_mutant_that_does_not_move_the_device_is_no_verdict(monkeypatch):
    """Condition 2: a control whose mutant never activates (here: a name the
    host does not know) must not read as caught."""
    monkeypatch.setitem(ht.CONTROLS, "NOT_A_REAL_BUG", "held-default")
    r = ht.run_control("NOT_A_REAL_BUG")
    assert r["caught"] is not True, r


def test_a_crash_is_no_verdict_not_a_fail(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("apparatus broke")
    monkeypatch.setattr(uh, "gate_bracket", boom)
    m = ht.run_condition(dict(key="held-default"))
    assert m["verdict"] == ht.NO_VERDICT, m["reason"]


# ---- the bracket: the derivation, and the inputs that defeat its guards -------
def test_gate_bracket_contains_the_gate_on_a_gapless_wire():
    """Known answer, independent of the host: Q accepted at t_p, the gate's 8
    bytes and the second Q's 2 bytes behind it at the nominal byte time."""
    b = uh.BITS_PER_BYTE * uh.SR / uh.DEFAULT_BAUD
    for k in range(40):
        t_p = 1000 + k / 40                      # every phase within a frame
        t_g, t_q = t_p + 8 * b, t_p + 10 * b
        pre, post = int(t_p) + 1, int(t_q) + 1   # the frame REGISTER: audio + 1
        br = uh.gate_bracket(pre, post)
        g = int(t_g) + 1                         # live write: accept + 1
        assert br["apply_lo"] <= g <= br["apply_hi"], (k, br, g)
        assert abs(g - br["estimate"]) <= br["bound"] <= uh.HOLD_BOUND_MAX_FRAMES


def test_gate_bracket_widens_with_a_stall_and_never_excludes_the_gate():
    """A stall between the gate and the second query only moves t_q later:
    the bracket widens, still contains the gate, and past the declared
    maximum the host must refuse (checked through the CLI below)."""
    b = uh.BITS_PER_BYTE * uh.SR / uh.DEFAULT_BAUD
    t_p = 500.3
    t_g = t_p + 8 * b
    g = int(t_g) + 1
    for stall in (0, 3, 10, 40):
        t_q = t_g + 2 * b + stall
        br = uh.gate_bracket(int(t_p) + 1, int(t_q) + 1)
        assert br["apply_lo"] <= g <= br["apply_hi"]
    assert uh.gate_bracket(int(t_p) + 1, int(t_g + 2 * b + 40) + 1)["bound"] \
        > uh.HOLD_BOUND_MAX_FRAMES


@pytest.mark.parametrize("pre,post,why", [
    (1000, 1000, "did not advance"),        # a frozen counter: the defeating input
    (1000, 1020, "did not advance"),        # faster than the wire can carry 10 bytes
    (1000, 1000 - 5, "runs backwards"),     # a counter that stepped back
])
def test_gate_bracket_refuses_what_the_protocol_cannot_produce(pre, post, why):
    with pytest.raises(uh.Refused, match=why):
        uh.gate_bracket(pre, post)


def test_gate_bracket_is_wrap_safe():
    a = uh.gate_bracket(65530, (65530 + 42) & 0xFFFF)
    b = uh.gate_bracket(100, 142)
    assert a["bound"] == b["bound"]
    assert a["estimate"] - 65530 == b["estimate"] - 100


def test_a_stalled_burst_is_refused_and_the_note_released():
    """Through the CLI: the burst is split after the first STATUS query and
    the rest of the bytes reach the device's RX 1 ms after the first part has
    finished (a USB split). The bracket widens past the declared maximum; the
    host must REFUSE the hold and release the note, never report it."""
    q = uh.pkt_status()
    gate = uh.pkt_write(0, 0, 0x20, 0)

    def stall(h):
        ser, orig = h.ser, h.ser.write

        def write(data):
            i = data.find(q + gate)                 # the bracket's first query
            if i >= 0:
                head = data[:i + len(q)]
                orig(head)
                done = ser.clock.t + len(head) * ser.sim._byte_time
                ser._in_flight.append((done + 0.001, data[i + len(q):]))
                return len(data)
            return orig(data)
        ser.write = write
    m = ht.measure(ht.HELD_COMMANDS["held-default"], harness_hook=stall)
    v, why = ht.hold_verdict(m, expect_refusal=True)
    assert m["rc"] == 2 and "wider than the declared" in m["stderr_tail"], m["stderr_tail"]
    assert v == ht.REFUSED and m["device"]["off"] is not None, why


# ---- the rolling verifier now enforces the hold it reports --------------------
def test_the_rolling_verifier_enforces_the_requested_hold():
    """verify_rolling_playback's held branch used to record a number it called
    `hold_frames_observed` -- the gate-off's distance from the host-chosen
    performance origin -- and assert nothing about the hold. It now checks
    the device's gate-on -> gate-off against the request; the historical host
    path must turn it red for that reason."""
    import verify_rolling_playback as vrp
    clean = vrp.check(vrp.run_cli("bar808", note=45))
    assert clean["ok"] and clean["hold_delivered"] is not None, clean["reasons"]
    assert abs(clean["hold_delivered"] - 1920) <= clean["hold_bound"]
    bad = vrp.check(vrp.run_cli("bar808", note=45, inject="HOLD_ACK_DRAIN"))
    assert any(r.startswith("held note: the device held") for r in bad["reasons"]), \
        bad["reasons"]


# ---- the RTL replay tooling (the replay itself runs on the build box) ---------
def test_the_held_rtl_capture_replays_the_hosts_actual_bytes(tmp_path):
    """No RTL here: the capture must be exactly the host's transmit log, carry
    the bracket's two STATUS queries, parse in the bench's own reader, and --
    on the bench's wire model, not the sim's -- put the gate-off at the
    requested hold from the gate's predicted apply frame, within the bound."""
    import verify_uart_bridge as vub
    m = ht.measure(ht.HELD_COMMANDS["held-default"])
    cap = ht.write_held_rtl_capture(m["_harness"], tmp_path / "held-default")
    sent = b"".join(d for _t, d in m["_harness"].ser.tx_log)
    lines = (tmp_path / "held-default.cmds").read_text().splitlines()
    replayed = b"".join(bytes.fromhex("".join(ln.split()[2:])) for ln in lines)
    assert replayed == sent
    assert cap["writes"] == 35 and cap["events"] == 1 and cap["status"] >= 3
    items, rows, _origin, _baud = vub.rows_from_capture(str(tmp_path / "held-default"))
    on = next(r for r in rows[0] if r.kind == "write"
              and uh.decode_reg_frame(r.packet[1:7])[2] == 0x20)
    off = next(r for r in rows[0] if r.kind == "event")
    assert abs((off.due - on.apply_frame) - 1920) <= m["host_record"]["hold_bound_frames"] + 1


@pytest.mark.parametrize("rows,verdict", [
    ([["100", "0", "0", "32", "0"], ["2020", "0", "0", "33", "0"]], ht.PASS),
    ([["100", "0", "0", "32", "0"], ["2030", "0", "0", "33", "0"]], ht.FAIL),
    # the defeating inputs of a "first gate-off minus first gate-on" reader:
    ([["100", "0", "0", "32", "0"], ["2020", "0", "0", "33", "0"],
      ["2500", "0", "0", "33", "0"]], ht.REFUSED),           # two gate-offs
    ([["100", "0", "1", "32", "0"], ["2020", "0", "0", "33", "0"]], ht.REFUSED),  # drum page
    ([["100", "0", "0", "32", "0"], ["x", "0", "0", "33", "0"]], ht.REFUSED),     # X
])
def test_rtl_hold_verdict_reads_gates_by_identity(rows, verdict):
    assert ht.rtl_hold_verdict(rows, 1920, 2)["verdict"] == verdict
