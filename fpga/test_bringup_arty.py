#!/usr/bin/env python3
"""Validation cases for fpga/bringup_arty.py.

Expectations here come from equal arithmetic and from the nominal 48 kHz audio
frame rate -- both known independently of this module -- not from the module's
own output. Each check that can pass for the wrong reason carries a control
that must go red.
"""
from __future__ import annotations

import os
import pathlib
import sys

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
    (1, "ValueError: event 0: due 48119 (+anchor 16210) is inside the wrap guard",
     "wrap-guard"),
    (0, "uart_host: done; device frame 4203, evq 0, wrq 0, drops 0, errs 0",
     "played"),
    (2, "uart_host: REFUSED -- the device's frame counter did not advance",
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


def test_missing_bitstream_is_refused(tmp_path):
    with pytest.raises(ba.Refused, match="no bitstream"):
        ba.require_bitstream(tmp_path / "absent.bit", "0" * 64)
