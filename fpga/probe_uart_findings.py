#!/usr/bin/env python3
"""fpga/probe_uart_findings.py -- the review findings, reproduced one by one.

Run against the unrepaired host, every probe prints OBSERVED (the wrong
behaviour) and the script exits 1. Run against the repaired host, every probe
shows the required behaviour and it exits 0 -- the same script is the
before/after record (docs/uart-host-repair-2026-09-22.md carries both
transcripts).

It uses only the long-lived API (main, plan, Bridge, note_writes, wait_until),
so it runs on both sides of the repair.
"""
from __future__ import annotations

import contextlib
import io
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import uart_device_sim as dev
import uart_host as uh

A_GATE_ON, A_GATE_OFF = 0x20, 0x21


def main_capture(argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        rc = uh.main(argv)
    return rc, out.getvalue(), err.getvalue()


def wait_for(pred, timeout_s=6.0):
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout_s:
        if pred():
            return True
        time.sleep(0.02)
    return False


def probe_1():
    """An isolated note-off must emit the gate-off write for a sounding note."""
    writes = uh.note_writes(45, False)
    ok = writes == [(0, 0, A_GATE_OFF, 0)]
    return ok, f"note_writes(45, False) = {writes!r}"


def probe_2(sim):
    """The note-off subcommand sends and verifies the gate-off."""
    rc, out, err = main_capture(["note-off", "--note", "45", "--port", sim.port])
    wait_for(lambda: any(w[3] == A_GATE_OFF for w in sim.writes))
    offs = [w for w in sim.writes if w[3] == A_GATE_OFF]
    ok = rc == 0 and len(offs) == 1
    return ok, (f"exit {rc}; usage shown: {'usage:' in out}; "
                f"gate-off writes executed: {len(offs)}")


def probe_3(sim):
    """run() must take the device origin BEFORE planning, and no raw
    ValueError from a blind unshifted plan may ever escape."""
    try:
        bridge = uh.Bridge(sim.port)
        rows = bridge.run([("write", 0, 0, 4, 1), ("event", 0, 0, 0, 0x40, 7)])
    except SystemExit as exc:
        return False, f"hard exit {exc.code} mid-run"
    except getattr(uh, "Refused", ()) as exc:
        return True, f"Refused (first-class): {exc}"
    except ValueError as exc:
        return False, f"raw ValueError from the unshifted plan: {exc}"
    lead = getattr(bridge, "lead_frames", None)
    if lead is None:
        return False, "bridge exposes no origin/lead; the plan ran before the device origin"
    ok = rows[0].send_frame == bridge.origin + lead
    return ok, (f"planned after the origin: first send f{rows[0].send_frame} "
                f"= origin {bridge.origin} + lead {lead}")


def probe_4():
    """--hold-frames must reach the scheduled gate-off."""
    outs = {}
    for hold in (1920, 4800):
        try:
            outs[hold] = main_capture(["run", "--fixture", "none", "--note", "45",
                                       "--hold-frames", str(hold), "--dry-run"])
        except KeyError:
            outs[hold] = None
    if any(v is None for v in outs.values()):
        # a host without a phrase-free fixture: fall back to the default
        # fixture for the before/after comparison (the holds still apply)
        for hold in (1920, 4800):
            outs[hold] = main_capture(["run", "--note", "45",
                                       "--hold-frames", str(hold), "--dry-run"])
    (rc1, out1, e1), (rc2, out2, e2) = outs[1920], outs[4800]
    if rc1 != 0 or rc2 != 0:
        return False, (f"dry-run exits {rc1}/{rc2}; stderr1: {e1.strip()[:160]}; "
                       f"stderr2: {e2.strip()[:160]}")
    if out1 == out2:
        return False, "dry-run output identical for hold 1920 and 4800 (hold ignored)"
    gate = [ln for ln in out1.splitlines() if re.search(r"\b21\b", ln)]
    return bool(gate), f"outputs differ; gate-off (0x21) row in plan: {bool(gate)}"


def probe_5():
    """The device origin is applied exactly once."""
    sim = dev.UartDeviceSim(epoch_frame=1000).start()
    try:
        bridge = uh.Bridge(sim.port)
        try:
            rows = bridge.run([("write", 0, 0, 4, 1)], hold_frames=0)
        except TypeError:
            try:
                rows = bridge.run([("write", 0, 0, 4, 1)])
            except SystemExit as exc:
                return False, f"REFUSED (exit {exc.code}) mid-run"
        except SystemExit as exc:
            return False, f"REFUSED (exit {exc.code}) mid-run"
        first = rows[0].send_frame
        ok = first == bridge.origin + bridge.lead_frames
        return ok, (f"first send f{first} vs origin {bridge.origin} + lead "
                    f"{bridge.lead_frames} (a doubled origin would show "
                    f"~2x origin)")
    except getattr(uh, "Refused", ()) as exc:
        return False, f"Refused: {exc}"
    finally:
        sim.stop()


def probe_6(sim):
    """send() preserves a waited schedule."""
    bridge = uh.Bridge(sim.port)
    anchor = bridge.status()
    origin = anchor.frame
    lead = uh.MIN_LEAD_FRAMES + int((bridge.status_round_trip_s or 0) * uh.SR) + 1
    rows = uh.plan([("write", 0, 0, 4, 0xAAAA), ("wait", 4800),
                    ("write", 0, 0, 5, 0xBBBB)],
                   start_frame=lead, anchor_frame=origin)
    bridge.send(rows)
    ok = wait_for(lambda: len([w for w in sim.writes if w[3] in (4, 5)]) >= 2)
    wr = [w for w in sim.writes if w[3] in (4, 5)]
    if not ok or len(wr) < 2:
        return False, f"writes executed: {wr}"
    delta = (wr[1][0] - wr[0][0]) & 0xFFFF
    return abs(delta - 4800) < 1500, f"second write applied {delta} frames after the first"


def probe_7():
    """STATUS parsed promptly; the snapshot must be fresh, not one round trip
    stale (a device that answers after 0.6 s)."""
    sim = dev.UartDeviceSim(reply_delay_s=0.6).start()
    try:
        bridge = uh.Bridge(sim.port)
        t0 = time.monotonic()
        try:
            pkt = bridge.status(attempts=3)
        except SystemExit as exc:
            return False, (f"REFUSED (exit {exc.code}) after {time.monotonic() - t0:.2f}s "
                           f"-- the 64-byte/0.5 s read never sees an 8-byte packet")
        stale = (sim.frame_now() - pkt.frame) & 0xFFFF
        ok = stale < 1600
        return ok, (f"snapshot {stale} frames old (device now {sim.frame_now()}, "
                    f"snapshot {pkt.frame}), took {time.monotonic() - t0:.2f}s")
    finally:
        sim.stop()


def probe_8():
    """wait_until is wrap-safe: 65700 is past the 65536 wrap."""
    sim = dev.UartDeviceSim(epoch_frame=65300).start()
    try:
        bridge = uh.Bridge(sim.port)
        t0 = time.monotonic()
        try:
            bridge.wait_until(65700, timeout_s=2.0)
            return True, f"reached the wrapped target in {time.monotonic() - t0:.2f}s"
        except SystemExit as exc:
            return False, (f"REFUSED (exit {exc.code}) after {time.monotonic() - t0:.2f}s "
                           f"-- integer compare against a wrapping counter")
    finally:
        sim.stop()


def probe_9(sim):
    """The default bar808 fixture is preflighted honestly."""
    try:
        rc, out, err = main_capture(["play", "--port", sim.port])
    except SystemExit as exc:
        rc, err = exc.code, f"hard SystemExit({exc.code}) escaped main()"
    except ValueError as exc:
        # the old CLI dies before sending: plan_shifted is never reached, so
        # the queue overflow it would cause is INVISIBLE to the tool. Show
        # what plan_shifted would have asked the device to absorb:
        peak, first_excess = _bar808_queue_peak()
        return False, (f"raw ValueError before any preflight: {exc}; the "
                       f"shifted schedule it would then send peaks at "
                       f"{peak} queued events against a 64-deep queue "
                       f"(first excess at packet index {first_excess})")
    sent = [r for r in sim.received if r[0] == "event"]
    last_err = err.strip().splitlines()[-1][:220] if err.strip() else ""
    if rc == 2:
        ok = (not sent) and ("queue" in err or "bandwidth" in err) and "event" in err
        return ok, f"REFUSED before sending ({len(sent)} event packets sent): {last_err}"
    if rc == 1:
        return False, (f"exit 1 AFTER sending {len(sent)} event packets; device "
                       f"drops={sim.drops} errs={sim.errs}: {last_err}")
    return False, f"exit {rc}; sent {len(sent)} events, drops {sim.drops}"


def _bar808_queue_peak():
    """Deterministic in-flight peak of the old path's shifted bar808 schedule,
    from the planner's ideal acceptance times."""
    events, _end = uh.phrase_events("bar808")
    cmds = [("write", 0, 0, 4, 0)] * 20 + [("event", d, f, s, a, v)
                                           for d, f, s, a, v in events]
    rows = uh.plan_shifted(cmds)
    ev = [(r.accept_frame, r.due) for r in rows if r.kind == "event"]
    peak, first_excess = 0, None
    for i, (acc, due) in enumerate(ev):
        inflight = sum(1 for a2, d2 in ev[:i + 1] if d2 > acc)
        peak = max(peak, inflight)
        if inflight > 64 and first_excess is None:
            first_excess = i
    return peak, first_excess


def probe_10():
    """The event packet's golden payload bytes."""
    # {F=0, 6'b0, SEC=1, A=0x40, D=4}: SEC sits at bit 40, the LSB of the
    # first reg byte, so the bytes are 01 40 00 00 00 04. The old test's
    # expectation (004000000004) was wrong, masked by `or True`.
    pkt = uh.pkt_event(0x0918, 0, 1, 0x40, 4)
    want = bytes.fromhex("014000000004")
    ok = pkt[3:9] == want
    return ok, f"payload bytes {pkt[3:9].hex()} (want {want.hex()})"


PROBES = [
    ("1 isolated note-off emits the gate-off write", probe_1, False),
    ("2 note-off subcommand sends + verifies", probe_2, True),
    ("3 run() plans from the device origin", probe_3, True),
    ("4 --hold-frames reaches the gate-off", probe_4, False),
    ("5 device origin applied exactly once", probe_5, False),
    ("6 send() preserves a waited schedule", probe_6, True),
    ("7 STATUS fresh against a 0.6 s reply", probe_7, False),
    ("8 wait_until is wrap-safe", probe_8, False),
    ("9 bar808 preflighted honestly", probe_9, True),
    ("10 event packet golden bytes", probe_10, False),
]


def main() -> int:
    only = sys.argv[1:] or None
    results = []
    for name, fn, needs_sim in PROBES:
        if only and not any(o in name for o in only):
            continue
        sim = dev.UartDeviceSim().start() if needs_sim else None
        try:
            ok, observed = fn(sim) if needs_sim else fn()
        finally:
            if sim:
                sim.stop()
        results.append((name, ok, observed))
        print(f"[{'PASS' if ok else 'FAIL'}] {name}")
        print(f"    observed: {observed}")
    bad = [r for r in results if not r[1]]
    print(f"\n{len(results) - len(bad)}/{len(results)} probes show the required behaviour")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
