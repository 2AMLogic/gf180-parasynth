#!/usr/bin/env python3
"""Validation cases for fpga/bringup_arty.py.

Expectations here come from equal arithmetic and from the nominal 48 kHz audio
frame rate -- both known independently of this module -- not from the module's
own output. Each check that can pass for the wrong reason carries a control
that must go red.
"""
from __future__ import annotations

import os
import hashlib
import pathlib
import subprocess
import sys
import types

import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import bringup_arty as ba                                      # noqa: E402


# ---------------------------------------------------------------- unwrap ----
def test_unwrap_counts_plain_forward_progress():
    s = [(0.0, 100), (0.1, 5000), (0.2, 9900)]
    assert ba.unwrap_forward(s) == 9800


def test_unwrap_crosses_the_16_bit_wrap():
    """65000 -> 4800 is +5336 forward, not -60200 backwards."""
    s = [(0.0, 65000), (0.1, (65000 + 5336) & 0xFFFF)]
    assert ba.unwrap_forward(s) == 5336
    assert s[1][1] == 4800                      # the wrap really happened


def test_unwrap_sums_several_wraps():
    frames, f = [(0.0, 0)], 0
    for i in range(1, 41):                      # 40 x 5000 = 200000, ~3 wraps
        f = (f + 5000) & 0xFFFF
        frames.append((i * 0.1, f))
    assert ba.unwrap_forward(frames) == 200000


def test_unwrap_REFUSES_an_ambiguous_interval():
    """The control for the measurement: at or past the horizon the sign is not
    recoverable, so the answer must be withheld, not guessed. This is exactly
    what uart_host guesses before blaming the device's clock (#459)."""
    s = [(0.0, 0), (1.0, 0x8000)]
    with pytest.raises(ba.Refused, match="not.*recoverable|horizon"):
        ba.unwrap_forward(s)


def test_unwrap_just_inside_the_horizon_is_still_answered():
    s = [(0.0, 0), (0.6, 0x7FFF)]
    assert ba.unwrap_forward(s) == 0x7FFF


def test_unwrap_needs_two_samples():
    with pytest.raises(ba.Refused):
        ba.unwrap_forward([(0.0, 10)])


# ------------------------------------------------------------------ rate ----
def test_frame_rate_reads_a_known_48_khz():
    """4800 frames per 0.1 s is 48 kHz by construction."""
    s = [(i * 0.1, (i * 4800) & 0xFFFF) for i in range(25)]
    hz, jitter = ba.frame_rate_hz(s)
    assert hz == pytest.approx(48000.0, rel=1e-9)
    assert jitter == pytest.approx(0.0, abs=1e-6)


def test_frame_rate_reads_a_known_wrong_rate_as_wrong():
    """Control: a counter at half speed must not read as 48 kHz."""
    hz, _ = ba.frame_rate_hz([(i * 0.1, (i * 2400) & 0xFFFF) for i in range(25)])
    assert hz == pytest.approx(24000.0, rel=1e-9)


def test_frame_rate_survives_realistic_timing_jitter():
    """THE GATE'S SATISFIABILITY, AS A TEST.

    main() refuses outside +-1% of 48 kHz. The measured method jitter on real
    hardware was 1.0 ms, so inject twice that and require the fit to stay well
    inside the gate. The first version of this file gated at +-2% on a 0.39 s
    two-point estimate and refused a clock later measured at 47,977.8 Hz --
    within 0.05%. This is the check that would have caught that, and it is the
    reason a gate gets run against the real thing before it is committed."""
    rng = np.random.default_rng(20260928)
    s = [(i * 0.08 + rng.normal(0, 0.002), (i * 3840) & 0xFFFF) for i in range(25)]  # 1.92 s
    hz, jitter = ba.frame_rate_hz(s)
    assert hz == pytest.approx(48000.0, rel=0.01)
    assert jitter < 5.0


def test_frame_rate_refuses_a_span_too_short_to_gate_on():
    """The regression for the wrong answer this file was born from: 6 samples
    over 0.39 s. The method cannot support a 1% verdict there, so it must
    withhold one rather than report 46,960 Hz for a 47,977.8 Hz clock."""
    s = [(i * 0.078, (i * 3744) & 0xFFFF) for i in range(6)]
    assert s[-1][0] - s[0][0] < 0.4
    with pytest.raises(ba.Refused, match="too short"):
        ba.frame_rate_hz(s)


def test_frame_rate_accepts_the_span_the_hardware_run_used():
    """1.90 s over 25 samples is what the real measurement used."""
    s = [(i * 0.079, (i * 3792) & 0xFFFF) for i in range(25)]
    assert s[-1][0] - s[0][0] == pytest.approx(1.896, abs=0.01)
    hz, _ = ba.frame_rate_hz(s)
    assert hz == pytest.approx(48000.0, rel=1e-6)


def test_frame_rate_refuses_too_few_samples_for_a_fit():
    with pytest.raises(ba.Refused, match="at least 5"):
        ba.frame_rate_hz([(0.0, 0), (0.1, 4800)])


def test_frame_rate_refuses_samples_with_no_elapsed_time():
    with pytest.raises(ba.Refused, match="not ordered"):
        ba.frame_rate_hz([(1.0, i * 10) for i in range(6)])


def test_unwrap_cumulative_is_monotonic_and_agrees_with_the_total():
    s = [(i * 0.1, (i * 4800) & 0xFFFF) for i in range(10)]
    cum = ba.unwrap_cumulative(s)
    assert len(cum) == len(s)
    assert cum == sorted(cum)
    assert cum[-1] == ba.unwrap_forward(s)


# -------------------------------------------------------------- classify ----
@pytest.mark.parametrize("rc,out,want", [
    # the historical #458 traceback: a raw ValueError, exit 1, no REFUSED --
    # an error, never something to retry or decode advice from (#1075)
    (1, "ValueError: event 0: due 48119 (+anchor 16210) is inside the wrap guard",
     "error"),
    (0, "uart_host: done; device frame 4203, evq 0, wrq 0, drops 0, errs 0",
     "played"),
    (2, "uart_host: REFUSED -- the device's frame counter did not advance",
     "refused"),
    # today's #458 verdict: exit 2, REFUSED, and the old guard's words inside
    # the reason -- the REFUSED verdict must outrank the substring, and the
    # reason carries the operator's instruction ("use a shorter hold")
    (2, "uart_host: REFUSED -- hold_frames 48000 cannot be delivered as one "
        "scheduled note-off: event 0: due 48459 (+anchor 51) is inside the "
        "wrap guard. The device reasons about dues within +-32768 frames; "
        "use a shorter hold",
     "refused"),
    (1, "Traceback (most recent call last): OSError", "error"),
])
def test_classify(rc, out, want):
    assert ba.classify(rc, out) == want


def test_classify_does_not_call_a_nonzero_exit_played():
    assert ba.classify(1, "uart_host: done; ...") != "played"


# ----------------------------------------------------- board enumeration ----
IOREG_TWO_FTDI = '''
    | |     "USB Serial Number" = "000000000001"
    | |     "USB Product Name" = "Dual RS232-HS"
    | |     "USB Vendor Name" = "FTDI"
    | |     "idVendor" = 1027
    |       "USB Product Name" = "Digilent USB Device"
    |       "USB Vendor Name" = "Digilent"
    |       "idVendor" = 1027
    |       "USB Serial Number" = "210319C088B7"
'''


def test_only_the_digilent_serial_is_picked_up():
    """The session this file documents had two FT2232H devices on the bus. The
    unrelated one must not be offered as a board."""
    assert ba.parse_digilent_serials(IOREG_TWO_FTDI) == ["210319C088B7"]


def test_no_digilent_device_yields_nothing():
    assert ba.parse_digilent_serials(
        '"USB Vendor Name" = "FTDI"\n"USB Serial Number" = "000000000001"') == []


def test_two_digilent_devices_refuse_rather_than_pick_one(monkeypatch):
    monkeypatch.setattr(ba, "parse_digilent_serials", lambda _: ["AAA1", "BBB2"])
    monkeypatch.setattr(ba.subprocess, "run",
                        lambda *a, **k: type("P", (), {"stdout": ""})())
    with pytest.raises(ba.Refused, match="2 Digilent devices"):
        ba.require_one_board(None)


def test_an_explicit_serial_skips_enumeration():
    assert ba.require_one_board("210319C088B7") == "210319C088B7"


# --------------------------------------------------------- bitstream hash ----
def test_bitstream_hash_mismatch_is_refused(tmp_path):
    """The injected-bug control for image identity: citing evidence bound to
    one bitstream while flashing another is the mistake this refuses."""
    p = tmp_path / "arty.bit"
    p.write_bytes(b"not the published image")
    with pytest.raises(ba.Refused, match="sha256"):
        ba.require_bitstream(p, "0" * 64)


def test_bitstream_hash_match_returns_the_hash(tmp_path):
    import hashlib
    p = tmp_path / "arty.bit"
    p.write_bytes(b"payload")
    want = hashlib.sha256(b"payload").hexdigest()
    assert ba.require_bitstream(p, want) == want


# ----------------------------------------------- play_note regression (#1075) --
# The wrapper is driven through its subprocess boundary: play_note's actual
# CLI arguments run the REAL uart_host.main against a fresh simulated UART
# bridge (fpga/uart_device_sim on simulated time -- no hardware, no wall
# clock). The starting frame advances by 7,919 modulo 65,536 between calls,
# so a retry really does get a fresh anchor: the historical retry's own
# premise, granted before it is proven futile.
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _sub in ("model", "fpga"):
    if os.path.isdir(os.path.join(_ROOT, _sub)):
        sys.path.insert(0, os.path.join(_ROOT, _sub))

import contextlib                                        # noqa: E402
import io                                                # noqa: E402

import synth_top_model as stm                            # noqa: E402
import uart_device_sim as dev                            # noqa: E402
import uart_host as uh                                   # noqa: E402

_REPO = pathlib.Path(_ROOT)
_ANCHOR_STRIDE = 7919          # coprime to 65,536: every retry a fresh anchor


class _HostViaSim:
    """subprocess.run replacement for play_note: the child host CLI runs
    in-process on a fresh simulated bridge each call, capturing what the
    child would have printed and its exit status."""

    def __init__(self):
        self.calls = []      # one record per host invocation

    def __call__(self, cmd, **_kwargs):
        assert cmd[1] == "fpga/uart_host.py", \
            f"interception missed the host CLI: {cmd}"
        argv = cmd[2:]
        epoch = (_ANCHOR_STRIDE * len(self.calls)) & 0xFFFF
        clock = dev.SimClock()
        sim = dev.UartDeviceSim(clock=clock, epoch_frame=epoch)
        ser = dev.SimSerial(sim)
        bridge = uh.Bridge.on_serial(ser, clock=clock)
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = uh.main(argv, bridge_factory=lambda p, b: bridge)
        self.calls.append({"argv": argv, "rc": rc, "out": out.getvalue(),
                           "err": err.getvalue(), "sim": sim, "ser": ser})
        return type("Completed", (), {"returncode": rc,
                                      "stdout": out.getvalue(),
                                      "stderr": err.getvalue()})()


def test_play_note_delivers_a_reachable_hold_with_one_host_call(monkeypatch):
    host = _HostViaSim()
    monkeypatch.setattr(ba.subprocess, "run", host)
    assert ba.play_note(_REPO, "sim", "r1", 45, 24000) is True
    assert len(host.calls) == 1, \
        f"a reachable hold needs one host invocation, got {len(host.calls)}"
    sim = host.calls[0]["sim"]
    # delivery is asserted on the device's own write log, never on what the
    # host claims: the live gate-on, then the scheduled gate-off after the hold
    on = [i for i, w in enumerate(sim.writes)
          if w[5] == "live" and w[3] == stm.A_GATE_ON]
    off = [i for i, w in enumerate(sim.writes)
           if w[5] == "event" and w[3] == stm.A_GATE_OFF]
    assert on and off, "the gate-on and the scheduled gate-off must both land"
    assert on[0] < off[0], "the gate-off must follow the gate-on"
    span = sim.write_frames[off[0]] - sim.write_frames[on[0]]
    assert span >= 24000, \
        f"the gate-off landed {span} frames after the gate-on, " \
        f"before the 24,000-frame hold elapsed"


def _refused_hold_scenario(monkeypatch):
    """The #1075 refused-hold scenario, shared by the regression and its
    injected-defect control: one play_note for the 48,000-frame hold, then
    the wrapper's contract asserted -- one host call, child exit 2, a False
    return, nothing left queued on the device, and the child's full REFUSED
    line returned for the operator-output check."""
    host = _HostViaSim()
    monkeypatch.setattr(ba.subprocess, "run", host)
    ok = ba.play_note(_REPO, "sim", "r1", 45, 48000)
    assert ok is False, "an unreachable hold must be reported undelivered"
    assert len(host.calls) == 1, \
        f"the host refuses an unreachable hold on the first attempt; the " \
        f"wrapper invoked it {len(host.calls)} times"
    call = host.calls[0]
    assert call["rc"] == 2, "exit 2 belongs to the child host"
    # drain the attempted bridge before trusting an empty write log: a
    # note-on queued just before the refusal would still be in flight
    call["ser"].run_until(call["ser"].clock.t + 0.05)
    assert call["sim"].writes == [], \
        "a refused hold must leave no write queued, sent or sounding"
    refused = [l for l in call["err"].splitlines()
               if "uart_host: REFUSED" in l]
    assert len(refused) == 1, "exactly one REFUSED line from the child"
    assert "use a shorter hold" in refused[0], \
        "the child's refusal carries the operator's instruction"
    return refused[0]


def test_play_note_refuses_an_unreachable_hold_on_the_first_call(
        monkeypatch, capsys):
    full_line = _refused_hold_scenario(monkeypatch)
    operator = capsys.readouterr().out
    assert full_line in operator, \
        "the operator must see the child's complete REFUSED line, " \
        "not a truncation or a generic hint"


def test_control_the_restored_retry_loop_turns_the_regression_red(monkeypatch):
    """The injected-defect control (#1075): put the historical wrapper back --
    the match-first classifier AND its ten-try retry loop -- and require the
    SAME scenario to go red on its one-invocation assertion with ten calls.
    A catch that is an import error, a timeout or an unrelated assertion
    does not count, so the failing assertion and the count are both pinned."""
    # verbatim history (indentation aside) -- the defect this control injects
    def historical_classify(rc, out):
        if "inside the wrap guard" in out:
            return "wrap-guard"
        if rc == 0 and "done;" in out:
            return "played"
        if "REFUSED" in out:
            return "refused"
        return "error"

    def historical_play_note(repo, port, image, note, hold, tries=10):
        for t in range(1, tries + 1):
            p = ba.subprocess.run(
                [sys.executable, "fpga/uart_host.py", "--port", port,
                 "--image", image, "run", "--note", str(note),
                 "--hold-frames", str(hold)],
                capture_output=True, text=True, timeout=180, cwd=repo)
            out = p.stdout + p.stderr
            verdict = historical_classify(p.returncode, out)
            if verdict == "played":
                print(f"  note {note}: played (try {t})")
                return True
            if verdict == "wrap-guard":
                continue
            why = [l for l in out.splitlines() if "REFUSED" in l]
            print(f"  note {note}: {verdict}  {why[0][:120] if why else ''}")
            return False
        print(f"  note {note}: never cleared the wrap guard in {tries} tries (#458)")
        return False

    monkeypatch.setattr(ba, "play_note", historical_play_note)
    with pytest.raises(AssertionError) as ei:
        _refused_hold_scenario(monkeypatch)
    assert "invoked it 10 times" in str(ei.value), \
        f"the control must fail on the one-invocation assertion with ten " \
        f"calls, not something unrelated: {ei.value}"


def test_missing_bitstream_is_refused(tmp_path):
    with pytest.raises(ba.Refused, match="no bitstream"):
        ba.require_bitstream(tmp_path / "absent.bit", "0" * 64)


# ------------------------------------- a child that never finishes (#1690) ----
# Three of the bench's children run with a deadline: openFPGALoader (600 s),
# uart_host status (60 s) and uart_host run (180 s). One that had not finished
# by it left main() as a TimeoutExpired traceback. main() is driven for real
# with the board supplied: subprocess.run is replaced by a board whose one child
# named by the scenario hangs, the wall clock advances in exact 40 ms ticks (25
# status samples 1.92 s apart, the span the real measurement used) so the frame
# counter reads 48,000 frames per second by construction, and the port exists.
class _Clock:
    """The wall clock in exact 40 ms ticks, so the frame counter the board
    reports is a whole number of frames per tick: 48 kHz by construction."""

    def __init__(self):
        self.ticks = 0

    def time(self):
        self.ticks += 1
        return 1_800_000_000.0 + self.ticks * 0.04

    def sleep(self, seconds):
        self.ticks += round(seconds / 0.04)


class _Board:
    """subprocess.run replacement. The probes answer; a child the scenario says
    hangs never finishes, which is TimeoutExpired with nothing captured; a child
    it says fails exits 1. Every call is recorded with its deadline."""

    def __init__(self, clock, *, hangs=lambda cmd: False, fails=lambda cmd: False):
        self.clock, self.hangs, self.fails, self.calls = clock, hangs, fails, []

    def __call__(self, cmd, **kwargs):
        self.calls.append((list(cmd), kwargs.get("timeout")))
        if self.hangs(cmd):
            raise subprocess.TimeoutExpired(cmd, kwargs.get("timeout"))
        out = ""
        if cmd[0] == "which":
            out = "/opt/homebrew/bin/openFPGALoader\n"
        elif cmd[0] == "openFPGALoader":
            out = "done 1\n"
        elif "status" in cmd:
            out = f"uart_host: STATUS frame {self.clock.ticks * 1920 & 0xFFFF}\n"
        elif self.fails(cmd):
            return subprocess.CompletedProcess(cmd, 1, stdout="",
                                               stderr="uart_host: no reply from the device\n")
        else:
            raise AssertionError(f"unexpected child {cmd}")
        return subprocess.CompletedProcess(cmd, 0, stdout=out, stderr="")


SERIAL = "210319C088B7"


def _bringup(tmp_path, monkeypatch, board, *argv):
    """bringup_arty.main() for real, with the board supplied. Returns its exit
    status, so a refusal is a 2 and the historical escape is the exception."""
    bit = tmp_path / "top.bit"
    bit.write_bytes(b"the image whose evidence is cited")
    monkeypatch.setattr(ba.subprocess, "run", board)
    monkeypatch.setattr(ba, "time", board.clock)
    monkeypatch.setattr(ba, "os", types.SimpleNamespace(
        path=types.SimpleNamespace(exists=lambda _port: True)))
    monkeypatch.setitem(sys.modules, "serial", types.ModuleType("serial"))
    return ba.main(["--bit", str(bit), "--sha256", hashlib.sha256(bit.read_bytes()).hexdigest(),
                    "--serial", SERIAL, *argv])


PORT = f"/dev/cu.usbserial-{SERIAL}1"
_STATUS_CHILD = f"'fpga/uart_host.py', '--port', '{PORT}', 'status'"
_NOTE_CHILD = (f"'fpga/uart_host.py', '--port', '{PORT}', '--image', 'r1', "
               "'run', '--note', '45'")

_HANGS = [
    pytest.param(lambda cmd: cmd[0] == "openFPGALoader", [], "board: Digilent " + SERIAL,
                 "Command '['openFPGALoader', ", 600, 2, id="flash"),
    pytest.param(lambda cmd: "status" in cmd, ["--no-flash"], "port: " + PORT,
                 _STATUS_CHILD, 60, 2, id="status"),
    pytest.param(lambda cmd: "run" in cmd, ["--no-flash"], "frame counter: 48,000.0 Hz",
                 _NOTE_CHILD, 180, 27, id="note"),
]


@pytest.mark.parametrize("hangs, argv, reached, command, deadline, calls", _HANGS)
def test_a_child_that_never_finishes_is_refused(
        tmp_path, monkeypatch, capsys, hangs, argv, reached, command, deadline, calls):
    """The bench did not complete, so nothing was measured -- whether the board
    configured, the link answered or a note was delivered is unknown, not 'no'.
    REFUSED at exit 2 naming the child and its deadline; no count of notes
    delivered, and no child after the hung one (the note case: the probe, 25
    status reads and ONE run -- the three notes behind the hung one are not
    attempted)."""
    board = _Board(_Clock(), hangs=hangs)
    assert _bringup(tmp_path, monkeypatch, board, *argv) == 2
    out = capsys.readouterr().out
    assert reached in out
    refused = [line for line in out.splitlines() if line.startswith("REFUSED -- ")]
    assert len(refused) == 1
    assert command in refused[0] and f"timed out after {deadline} seconds" in refused[0]
    assert "notes delivered" not in out
    assert len(board.calls) == calls
    assert board.calls[-1][1] == deadline


def test_a_note_the_host_fails_to_deliver_is_a_result_not_a_refusal(tmp_path, monkeypatch, capsys):
    """The distinction the row rests on. A child that answers 'not delivered'
    was measured: FAIL at exit 1 with the count. A child that never answers was
    not: the count is withheld and the exit is 2."""
    board = _Board(_Clock(), fails=lambda cmd: "run" in cmd)
    assert _bringup(tmp_path, monkeypatch, board, "--no-flash", "--notes", "45") == 1
    out = capsys.readouterr().out
    assert "--- 0/1 notes delivered ---" in out
    assert "REFUSED" not in out


@pytest.mark.parametrize("hangs, argv, reached, command, deadline, calls", _HANGS)
def test_control_the_unguarded_child_turns_the_hang_test_red(
        tmp_path, monkeypatch, hangs, argv, reached, command, deadline, calls):
    """The defect as it shipped: the child called with no handler. The same
    scenario must then leave main() as the TimeoutExpired it always did -- the
    historical traceback -- and the failure must be that child's timeout, not
    another assertion."""
    def unguarded(child, *, timeout, cwd=None):
        return subprocess.run(child, capture_output=True, text=True, timeout=timeout, cwd=cwd)
    monkeypatch.setattr(ba, "run_child", unguarded)
    board = _Board(_Clock(), hangs=hangs)
    with pytest.raises(subprocess.TimeoutExpired, match=f"timed out after {deadline} seconds"):
        _bringup(tmp_path, monkeypatch, board, *argv)
