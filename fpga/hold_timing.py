#!/usr/bin/env python3
"""fpga/hold_timing.py -- the held-note hold, measured on the DEVICE (#306).

    .venv/bin/python fpga/hold_timing.py measure --key held-default
    .venv/bin/python fpga/hold_timing.py table --set selection     # the 3 pinned held commands
    .venv/bin/python fpga/hold_timing.py table --set untouched     # conditions not used to select
    .venv/bin/python fpga/hold_timing.py controls                  # the mutants must fail

WHAT IS UNDER TEST. The shipped CLI, `uart_host.main([...])`, driving the
scripted device (fpga/uart_device_sim.py on simulated time, through
fpga/verify_rolling_playback.Harness). Nothing in the host is replaced but the
serial port and the clock.

THE ORACLE IS THE DEVICE, NOT THE PLANNER. The hold is read from the sim's
own record of EXECUTED register writes on its unwrapped timeline
(`UartDeviceSim.write_frames`, one entry per executed write): the one voice
GATE_ON write and the one voice GATE_OFF write after it, identified by
register (section + address), never by position ("the last live write") and
never from the host's plan, log or timing record. Anything else -- no gate,
two gates, a gate-off before its gate-on, a device reset -- is AMBIGUOUS and
the measurement REFUSES rather than reporting a hold.

What the host CLAIMED (its plan rows, its `hold_timing` record) is reported
beside the device's answer so the two can be compared, and that comparison
is exactly what the forged-log control below exists to defeat.

THE VERDICT for one run (`hold_verdict`):
  PASS      the CLI exited 0 and |device hold - requested| <= the host's own
            per-run bound, which is <= uart_host.HOLD_BOUND_MAX_FRAMES
  FAIL      the CLI claimed success (exit 0) and the device held something
            else, or the host's record does not contain the device's truth
  REFUSED   the CLI refused (exit 2) -- a correct outcome only where the
            condition says it must refuse; the device log must then show the
            note RELEASED (a refusal that leaves a note sounding is a FAIL)
  NO VERDICT the run crashed or the device log is ambiguous

The apparatus knobs are the device's: `epoch` (counter start, for the wrap),
`reply_delay_s` (device -> host latency) and `tx_delay_s` (host -> device
latency). The scripted device has NO host-clock noise: a real FTDI link adds
latency (which the knobs model) and USB packetisation (which they do not --
see docs/capture-r0.md, "Held notes: the release is NOT compared").
"""
from __future__ import annotations

import argparse
import contextlib
import io
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for _p in ("fpga", "model", "audition", "tools", "fpga/release"):
    if str(ROOT / _p) not in sys.path:
        sys.path.insert(0, str(ROOT / _p))

import uart_host as uh                            # noqa: E402
import verify_rolling_playback as vrp             # noqa: E402

PASS, FAIL, REFUSED, NO_VERDICT = "PASS", "FAIL", "REFUSED", "NO VERDICT"
SEC_VOICE = 0
TAIL_S = 0.25          # simulated time after the CLI returns: the gate-off fires
REQUESTED_DEFAULT = 1920

# The pinned held-note commands (fpga/release/baseline-2025.1.json), as argv.
HELD_COMMANDS = {
    "held-default": ["run", "--note", "45", "--fixture", "none"],
    "held-m5a-saw": ["run", "--preset", "m5a-saw", "--note", "72", "--fixture", "none"],
    "held-m5a-pulse": ["run", "--preset", "m5a-pulse", "--note", "72", "--fixture", "none"],
}


class Ambiguous(Exception):
    """The device log cannot name one gate-on and one gate-off."""


def _gate_addrs():
    import synth_top_model as stm
    return stm.A_GATE_ON, stm.A_GATE_OFF


def device_gates(sim) -> dict:
    """(gate-on frame, gate-off frame) from the device's EXECUTED writes on
    its unwrapped timeline, by register identity. REFUSES (Ambiguous)
    whenever the log does not name exactly one of each, in order."""
    on_addr, off_addr = _gate_addrs()
    if sim.resets:
        raise Ambiguous(f"the device reset {sim.resets} time(s): its timeline restarted")
    if len(sim.write_frames) != len(sim.writes):
        raise Ambiguous("the device's unwrapped log does not cover every executed write")
    ons = [(f, w) for w, f in zip(sim.writes, sim.write_frames)
           if w[2] == SEC_VOICE and w[3] == on_addr]
    offs = [(f, w) for w, f in zip(sim.writes, sim.write_frames)
            if w[2] == SEC_VOICE and w[3] == off_addr]
    if len(ons) != 1:
        raise Ambiguous(f"{len(ons)} voice GATE_ON writes executed, not one")
    if not offs:
        return {"on": ons[0][0], "off": None, "on_src": ons[0][1][5], "off_src": None}
    if len(offs) != 1:
        raise Ambiguous(f"{len(offs)} voice GATE_OFF writes executed, not one")
    if offs[0][0] <= ons[0][0]:
        raise Ambiguous(f"the GATE_OFF (f{offs[0][0]}) executed at or before the "
                        f"GATE_ON (f{ons[0][0]})")
    return {"on": ons[0][0], "off": offs[0][0], "on_src": ons[0][1][5],
            "off_src": offs[0][1][5]}


def _to_device(frame, ref_abs):
    """A host frame (16-bit-derived, possibly unmasked past 65535) placed on
    the device's unwrapped timeline next to `ref_abs` (signed 16-bit step)."""
    if frame is None or ref_abs is None:
        return None
    d = (int(frame) - ref_abs) & 0xFFFF
    return ref_abs + (d if d < 0x8000 else d - 0x10000)


def measure(argv: list, *, epoch: int = 0, reply_delay_s: float = 0.0,
            tx_delay_s: float = 0.0, hold_frames: int | None = None,
            inject: str | None = None, workdir: Path | None = None,
            harness_hook=None) -> dict:
    """Run the CLI once on the scripted device and return what the DEVICE did
    beside what the HOST claimed. Never raises for a CLI outcome.
    `harness_hook(h)` may alter the apparatus (tests: a stalled wire)."""
    h = vrp.Harness(epoch, reply_delay_s=reply_delay_s, tx_delay_s=tx_delay_s)
    if harness_hook is not None:
        harness_hook(h)
    requested = REQUESTED_DEFAULT if hold_frames is None else int(hold_frames)
    tmp = None
    if workdir is None:
        tmp = tempfile.TemporaryDirectory()
        workdir = Path(tmp.name)
    prefix = Path(workdir) / "hold"
    full = list(argv) + ["--port", "sim", "--capture", str(prefix)]
    if hold_frames is not None:
        full += ["--hold-frames", str(hold_frames)]
    timeline = []                                  # apparatus: host-side events

    def factory(port, baud):
        br = h.factory(port, baud)
        take, status = br._take, br.status

        def _take(kinds, deadline):
            before = (br.acks_seen, len(br.device_errors))
            pkt = take(kinds, deadline)
            if pkt is not None and pkt.kind == "status":
                timeline.append({"what": "status reply read", "frame16": pkt.frame,
                                 "device_frame": h.sim._abs_frame_at(h.clock.t)})
            if (br.acks_seen, len(br.device_errors)) != before:
                timeline.append({"what": "acks/errs read", "acks": br.acks_seen,
                                 "errs": len(br.device_errors),
                                 "device_frame": h.sim._abs_frame_at(h.clock.t)})
            return pkt

        def _status(*a, **k):
            sent = h.sim._abs_frame_at(h.clock.t)
            pkt = status(*a, **k)
            timeline.append({"what": "status()", "sent_device_frame": sent,
                             "frame16": pkt.frame,
                             "rtt_frames": round((br.status_round_trip_s or 0) * uh.SR, 2)})
            return pkt
        br._take, br.status = _take, _status
        return br

    saved = set(uh.INJECT_BUGS)
    uh.INJECT_BUGS.clear()
    if inject:
        uh.INJECT_BUGS.add(inject)
    out, err = io.StringIO(), io.StringIO()
    crash = None
    try:
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                rc = uh.main(full, bridge_factory=factory)
            except SystemExit as exc:
                rc = exc.code if isinstance(exc.code, int) else 2
    except Exception as exc:                      # noqa: BLE001 -- NO VERDICT, kept
        rc, crash = None, f"{type(exc).__name__}: {exc}"
    finally:
        uh.INJECT_BUGS.clear()
        uh.INJECT_BUGS.update(saved)
    h.ser.run_until(h.clock.t + TAIL_S)
    rec = {"argv": list(argv), "epoch": epoch, "reply_delay_s": reply_delay_s,
           "tx_delay_s": tx_delay_s, "inject": inject, "requested_hold": requested,
           "rc": rc, "crash": crash,
           "stderr_tail": (err.getvalue().strip().splitlines() or [""])[-1],
           "device_errors": [list(e) for e in h.sim.errors],
           "host_timeline": timeline}
    # what the HOST claimed: its capture (plan rows + timing record)
    plan_path = Path(f"{prefix}.plan.json")
    host = json.loads(plan_path.read_text()) if plan_path.is_file() else None
    rec["host_record"] = (host or {}).get("hold_timing")
    on_addr, off_addr = _gate_addrs()
    planned_on = planned_off = None
    if host:
        base = int(host.get("base_send_frame", 0))
        for r in host["rows"]:
            e = r.get("expect") or {}
            if r["kind"] == "write" and e.get("addr") == on_addr and e.get("sec") == SEC_VOICE:
                planned_on = r["apply_frame"] + base
            if r["kind"] == "event" and e.get("addr") == off_addr and e.get("sec") == SEC_VOICE:
                planned_off = r["due"] + base
    # what the DEVICE did
    try:
        dg = device_gates(h.sim)
        rec["device"] = dg
    except Ambiguous as exc:
        rec["device"] = None
        rec["ambiguous"] = str(exc)
        dg = None
    if dg:
        on = dg["on"]
        rec["actual_hold"] = (dg["off"] - on) if dg["off"] is not None else None
        rec["hold_error"] = (rec["actual_hold"] - requested
                             if rec["actual_hold"] is not None else None)
        rec["planned_on"] = _to_device(planned_on, on)
        rec["planned_off"] = _to_device(planned_off, on)
        rec["planned_hold"] = (rec["planned_off"] - rec["planned_on"]
                               if rec["planned_on"] is not None and rec["planned_off"] is not None
                               else None)
        rec["gate_on_error"] = (rec["planned_on"] - on) if rec["planned_on"] is not None else None
        rec["gate_off_error"] = ((rec["planned_off"] - dg["off"])
                                 if rec["planned_off"] is not None and dg["off"] is not None
                                 else None)
        hr = rec["host_record"]
        if hr and hr.get("gate_apply_bounds"):
            b = [_to_device(int(x) + base, on) for x in hr["gate_apply_bounds"]]
            rec["host_bounds_on_device"] = b
            rec["host_bound_contains_gate"] = b[0] <= on <= b[1]
    if tmp is not None:
        tmp.cleanup()
    rec["_harness"] = h                            # not JSON: the RTL capture reads it
    return rec


PROPERTIES = ("exit", "released", "hold", "bound-declared", "bracket-contains-gate")


def properties(m: dict) -> dict:
    """Each named property the verdict checks, True/False, from one
    measurement; None where it cannot be evaluated (no device log)."""
    dev = m.get("device")
    hr = m.get("host_record") or {}
    bound = hr.get("hold_bound_frames")
    p = {k: None for k in PROPERTIES}
    p["exit"] = m.get("rc") == 0
    if dev is None:
        return p
    p["released"] = dev["off"] is not None
    if m.get("hold_error") is not None:
        # against the host's own bound, or the declared maximum when the host
        # claimed none: a missing record must not make the hold unjudgeable
        lim = bound if bound is not None else uh.HOLD_BOUND_MAX_FRAMES
        p["hold"] = abs(m["hold_error"]) <= min(lim, uh.HOLD_BOUND_MAX_FRAMES)
    p["bound-declared"] = bound is not None and bound <= uh.HOLD_BOUND_MAX_FRAMES
    p["bracket-contains-gate"] = bool(m.get("host_bound_contains_gate"))
    return p


def hold_verdict(m: dict, *, expect_refusal: bool = False) -> tuple:
    """(verdict, reason) for one measurement against the DEVICE's hold."""
    if m.get("crash"):
        return NO_VERDICT, f"the CLI crashed: {m['crash']}"
    if m.get("device") is None and m.get("ambiguous"):
        if m.get("rc") == 2 and "0 voice GATE_ON" in m["ambiguous"]:
            # refused before the note was ever started: nothing to release
            return REFUSED, f"refused before any gate: {m['stderr_tail']}"
        return NO_VERDICT, f"device log ambiguous: {m['ambiguous']}"
    p = properties(m)
    if m["rc"] == 2:
        if not p["released"]:
            return FAIL, "the CLI refused and left the note SOUNDING (no gate-off executed)"
        return REFUSED, m["stderr_tail"]
    if m["rc"] != 0:
        return FAIL, f"CLI exit {m['rc']}: {m['stderr_tail']}"
    if expect_refusal:
        return FAIL, (f"the CLI claimed success (exit 0) where it must refuse; the "
                      f"device held {m['actual_hold']} for {m['requested_hold']}")
    if not p["released"]:
        return FAIL, "exit 0 and no gate-off executed: the note never ends"
    bound = (m.get("host_record") or {}).get("hold_bound_frames")
    if not p["hold"]:
        lim = bound if bound is not None else uh.HOLD_BOUND_MAX_FRAMES
        return FAIL, (f"device held {m['actual_hold']} frames for {m['requested_hold']} "
                      f"(error {m['hold_error']:+d}) outside "
                      + (f"the host's bound +-{lim}" if bound is not None else
                         f"the declared +-{lim} (the host claimed no bound)"))
    if not p["bound-declared"]:
        return FAIL, (f"exit 0 without a per-run bound within the declared "
                      f"{uh.HOLD_BOUND_MAX_FRAMES} (host record: {bound})")
    if not p["bracket-contains-gate"]:
        return FAIL, ("the host's gate bracket does not contain the device's gate frame "
                      f"{m['device']['on']}: {m.get('host_bounds_on_device')}")
    return PASS, (f"device held {m['actual_hold']} for {m['requested_hold']} "
                  f"(error {m['hold_error']:+d}, bound +-{bound})")


# ---- the condition sets ------------------------------------------------------
# SELECTION: the three pinned held commands on a zero-latency link at epoch 0
# -- the baseline the issue names, and the only conditions looked at while the
# bracket was designed.
SELECTION = [dict(key=k) for k in HELD_COMMANDS]

# UNTOUCHED: never run while choosing the approach. Delays both ways and
# asymmetric, counter-wrap boundaries straddling the gate and the hold, the
# shortest supported hold, a long hold, and holds that must refuse.
def untouched() -> list:
    hmin = uh.hold_min_frames()
    return [
        dict(key="held-default", reply_delay_s=0.002, tx_delay_s=0.002),
        dict(key="held-m5a-saw", reply_delay_s=0.005, tx_delay_s=0.0),
        dict(key="held-m5a-pulse", reply_delay_s=0.0, tx_delay_s=0.007),
        dict(key="held-default", reply_delay_s=0.016, tx_delay_s=0.001),
        dict(key="held-default", epoch=65536 - 1200),       # wrap during the upload
        dict(key="held-m5a-saw", epoch=65536 - 2400),       # wrap inside the hold
        dict(key="held-m5a-pulse", epoch=65536 - 40, reply_delay_s=0.003,
             tx_delay_s=0.003),
        dict(key="held-default", hold_frames=hmin),          # shortest feasible
        dict(key="held-m5a-saw", hold_frames=hmin + 1, epoch=65536 - 1240),
        dict(key="held-default", hold_frames=24_000),        # long, inside the window
        dict(key="held-default", hold_frames=hmin - 1, expect="REFUSED"),
        dict(key="held-default", hold_frames=4, expect="REFUSED"),
        dict(key="held-default", hold_frames=40_000, expect="REFUSED"),
        # a hold the protocol supports but a slow link cannot meet: the
        # device's own late ERR must turn it into a refusal, note released
        dict(key="held-default", hold_frames=hmin, reply_delay_s=0.004,
             tx_delay_s=0.004, expect="REFUSED"),
    ]


def run_condition(c: dict, *, inject: str | None = None) -> dict:
    m = measure(HELD_COMMANDS[c["key"]], epoch=c.get("epoch", 0),
                reply_delay_s=c.get("reply_delay_s", 0.0),
                tx_delay_s=c.get("tx_delay_s", 0.0),
                hold_frames=c.get("hold_frames"), inject=inject)
    want = c.get("expect", PASS)
    v, why = hold_verdict(m, expect_refusal=(want == REFUSED))
    m["condition"] = c
    m["verdict"], m["reason"], m["expected"] = v, why, want
    m["ok"] = v == want
    return m


# ---- the controls --------------------------------------------------------------
# Each mutant must (1) leave the clean run passing, (2) actually execute -- the
# CLI exits 0, the device log is unambiguous -- and (3) fail THE HOLD
# ASSERTION, not anything else. Anything else is NO VERDICT.
CONTROLS = {
    # the historical host path, verbatim: ACK drain in 20 ms read windows,
    # STATUS minus its round trip, PLAN_SLACK added to the gate-off
    "HOLD_ACK_DRAIN": "held-default",
    # the deceptive log: the host's plan rows and timing record name the
    # correct due while the packet on the wire carries a later one
    "HOLD_FORGED_LOG": "held-m5a-saw",
}


def run_control(name: str) -> dict:
    clean = run_condition(dict(key=CONTROLS[name]))
    mut = run_condition(dict(key=CONTROLS[name]), inject=name)
    res = {"control": name, "clean": clean["verdict"], "mutant": mut["verdict"],
           "mutant_reason": mut["reason"], "mutant_rc": mut["rc"],
           "device_hold": mut.get("actual_hold"), "planned_hold": mut.get("planned_hold"),
           "host_record_hold": ((mut.get("host_record") or {}).get("planned_hold_frames"))}
    pc, pm = properties(clean), properties(mut)
    # rule 4: which of the verdict's properties SAW the defect
    res["matrix"] = {k: ("MOVED" if pc[k] != pm[k] else "BLIND") for k in PROPERTIES}
    if clean["verdict"] != PASS:
        res["caught"], res["why"] = None, f"NO VERDICT: the clean run is {clean['verdict']}"
    elif mut["rc"] != 0 or mut.get("device") is None:
        res["caught"], res["why"] = None, (f"NO VERDICT: the mutant did not execute "
                                           f"(rc {mut['rc']}, {mut['reason']})")
    elif mut["verdict"] == FAIL and properties(mut)["hold"] is False:
        res["caught"], res["why"] = True, "the hold assertion failed, as intended"
    else:
        res["caught"], res["why"] = False, f"mutant {mut['verdict']}: {mut['reason']}"
    if res["caught"] is not None and mut.get("actual_hold") == clean.get("actual_hold"):
        # condition 2: a hold mutant that leaves the device's hold untouched
        # did not activate (or activated somewhere the device cannot see)
        res["caught"], res["why"] = None, "NO VERDICT: the mutant did not move the device hold"
    return res


def _row(m: dict) -> str:
    c = m["condition"]
    hr = m.get("host_record") or {}
    return (f"{c['key']:<15} ep {c.get('epoch', 0):>5} rx {1e3 * c.get('reply_delay_s', 0):>4.1f}ms "
            f"tx {1e3 * c.get('tx_delay_s', 0):>4.1f}ms req {m['requested_hold']:>6} "
            f"dev {str(m.get('actual_hold')):>6} err {str(m.get('hold_error')):>5} "
            f"bound {str(hr.get('hold_bound_frames')):>4} rc {m['rc']} "
            f"{m['verdict']:<8} (want {m['expected']})")


# ---- the UART RTL replay (build box) -------------------------------------------
# The scripted device is a Python model of the contract. The RTL is the device.
# `rtl` replays the host's ACTUAL bytes -- the SimSerial transmit log, the
# bracket's STATUS queries included, at the frames the host wrote them --
# through fpga/verify_uart_bridge.py's wrapper bench (UART pins in, I2S out),
# and reports three verdicts SEPARATELY:
#   model    the bench's own comparison: every write executed on its predicted
#            frame (events exactly at their due), the I2S wire bit-exact with
#            the integer model driven by the same schedule, no X, no overrun;
#   hold     the RTL's own write log, by register identity: the one voice
#            GATE_ON to the one voice GATE_OFF, against the request and the
#            host's per-run bound -- the bracket's derivation tested against
#            the real receiver, not against the Python sim it was written with;
#   release  the gate-off's identity (section, address, data) and that nothing
#            the queue reported went wrong (drops, late, resync).
# A heavy run (the whole wrapper, ~15 min for a held note plus 1 s of tail).
# It runs on the build box, never on a dispatch worker.

def write_held_rtl_capture(h, prefix: Path) -> dict:
    """<prefix>.cmds/.plan.json in verify_uart_bridge's replay format from the
    host's TRANSMIT LOG. The wire model is verify_rolling_playback's (true
    baud, the receiver's stop-bit acceptance). Expectations come from the
    packets themselves: the bench checks the device EXECUTES what the host
    sent, on the frames the contract predicts; the HOLD is judged separately
    against the request (`rtl_hold_verdict`), so a host that sent a wrong due
    is not excused by an expectation built from it."""
    if h.sim.epoch != 0 or h.sim.resets:
        raise ValueError("an RTL capture needs an epoch-0 run with no reset")
    bc = 10 * uh.CLK_HZ / uh.DEFAULT_BAUD
    div = (uh.CLK_HZ + uh.DEFAULT_BAUD // 2) // uh.DEFAULT_BAUD
    rows, wire_free = [], 0
    for t, data in h.ser.tx_log:
        send = int(t * uh.SR) + 1
        for pkt in vrp._packets(data):
            start = max(send * uh.CYC_PER_FRAME + 1, wire_free)
            wire_free = start + len(pkt) * bc
            push = start + (len(pkt) - 1) * bc + 4 + 9 * div + div // 2
            accept = int(push // uh.CYC_PER_FRAME)
            row = {"index": len(rows), "packet": pkt.hex(), "send_frame": send,
                   "accept_frame": accept}
            if pkt[0] == uh.OP_WRITE:
                f, s_, a, d = uh.decode_reg_frame(pkt[1:7])
                row.update(kind="write", due=-1, apply_frame=accept + 1,
                           expect={"flag": f, "sec": s_, "addr": a, "data": d})
            elif pkt[0] == uh.OP_EVENT:
                f, s_, a, d = uh.decode_reg_frame(pkt[3:9])
                due16 = pkt[1] | (pkt[2] << 8)
                k = (due16 - accept) & 0xFFFF
                due = accept + (k if k < 0x8000 else k - 0x10000)
                row.update(kind="event", due=due, apply_frame=due,
                           expect={"flag": f, "sec": s_, "addr": a, "data": d})
            elif pkt[0] == uh.OP_STATUS:
                row.update(kind="status", due=-1, apply_frame=-1, expect=None)
            else:
                raise ValueError(f"unexpected opcode 0x{pkt[0]:02x} in the transmit log")
            rows.append(row)
    prefix = Path(prefix)
    prefix.parent.mkdir(parents=True, exist_ok=True)
    with open(f"{prefix}.cmds", "w") as fh:
        for r in rows:
            fh.write(f"S {r['send_frame']} {bytes.fromhex(r['packet']).hex(' ')}\n")
    rec = {"origin": 0, "baud": uh.DEFAULT_BAUD, "base_send_frame": 0,
           "source": "SimSerial transmit log (held note, #306)", "rows": rows}
    Path(f"{prefix}.plan.json").write_text(json.dumps(rec, indent=1) + "\n")
    return {"rows": len(rows), "writes": sum(r["kind"] == "write" for r in rows),
            "events": sum(r["kind"] == "event" for r in rows),
            "status": sum(r["kind"] == "status" for r in rows)}


def rtl_hold_verdict(write_rows: list, requested: int, bound: int) -> dict:
    """The hold from the RTL bench's executed-write log (rows of
    `frame flag sec addr data ...`), by register identity. Ambiguity -- not
    exactly one voice GATE_ON and one voice GATE_OFF after it, or an X in a
    gate row -- is REFUSED, never read as a hold."""
    on_addr, off_addr = _gate_addrs()
    ons, offs = [], []
    for r in write_rows:
        try:
            frame, sec, addr = int(r[0]), int(r[2]), int(r[3])
        except (ValueError, IndexError):
            # an X (or a short row) could be a gate: nothing can be concluded
            return {"verdict": REFUSED, "reason": f"unreadable write row {r}: the "
                    "gate writes cannot be identified"}
        if sec == SEC_VOICE and addr == on_addr:
            ons.append(frame)
        elif sec == SEC_VOICE and addr == off_addr:
            offs.append(frame)
    if len(ons) != 1 or len(offs) != 1 or offs[0] <= ons[0]:
        return {"verdict": REFUSED, "reason": f"the RTL executed {len(ons)} gate-on and "
                f"{len(offs)} gate-off writes: the hold is ambiguous",
                "gate_on": ons, "gate_off": offs}
    hold = offs[0] - ons[0]
    ok = abs(hold - requested) <= bound
    return {"verdict": PASS if ok else FAIL, "hold": hold, "requested": requested,
            "bound": bound, "gate_on": ons[0], "gate_off": offs[0],
            "reason": f"the RTL held {hold} frames for {requested} (bound +-{bound})"}


def rtl_replay(key: str, outdir: Path, *, tail_s: float = 1.0) -> dict:
    """The build-box run. Returns the three verdicts; never raises for them."""
    import verify_uart_bridge as vub
    m = measure(HELD_COMMANDS[key])
    v, why = hold_verdict(m)
    if v != PASS:
        return {"state": REFUSED, "reason": f"the scripted-device run is not clean: {why}"}
    outdir = Path(outdir).resolve()
    cap = write_held_rtl_capture(m["_harness"], outdir / key)
    run = vub.simulate_replay(str(outdir / key), ROOT / "build" / "hold-rtl" / key,
                              tail_frames=int(tail_s * uh.SR), timeout_s=4 * 3600)
    if run is None:
        return {"state": REFUSED, "reason": "the RTL replay did not run", "capture": cap}
    ok, comp, detail = vub.analyze(run)
    hold = rtl_hold_verdict(vub._rows(run["files"]["wrs"]), m["requested_hold"],
                            m["host_record"]["hold_bound_frames"])
    errs = {k: comp.get(k) for k in ("writes_bad", "frame_pred_bad", "frame_no_pred",
                                     "overrun", "wire_mismatch") if k in comp}
    off_rows = [r for r in json.loads(Path(f"{outdir / key}.plan.json").read_text())["rows"]
                if r["kind"] == "event"]
    release_identity = {"verdict": PASS if (len(off_rows) == 1 and off_rows[0]["expect"]
                                   == {"flag": 0, "sec": 0, "addr": _gate_addrs()[1],
                                       "data": 0}) else FAIL,
               "gate_off_row": off_rows}
    return {"state": "COMPLETE", "capture": cap,
            "model": {"verdict": PASS if ok else FAIL, "comparison": comp,
                      "detail": list(detail)[:10]},
            "hold": hold, "release_identity": release_identity,
            "queue_and_errors": errs,
            "host_record": m["host_record"],
            "live_cmds_sha256": __import__("hashlib").sha256(
                Path(f"{outdir / key}.cmds").read_bytes()).hexdigest(),
            "run_identity": vub.rtl_run_report(run)}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    me = sub.add_parser("measure")
    me.add_argument("--key", choices=sorted(HELD_COMMANDS), default="held-default")
    me.add_argument("--epoch", type=int, default=0)
    me.add_argument("--reply-delay-ms", type=float, default=0.0)
    me.add_argument("--tx-delay-ms", type=float, default=0.0)
    me.add_argument("--hold-frames", type=int, default=None)
    me.add_argument("--inject", default=None)
    tb = sub.add_parser("table")
    tb.add_argument("--set", choices=("selection", "untouched"), default="selection")
    tb.add_argument("--inject", default=None)
    tb.add_argument("--json", type=Path, default=None)
    sub.add_parser("controls")
    rt = sub.add_parser("rtl", help="BUILD BOX ONLY: replay the host's bytes "
                                    "through the UART RTL (~15 min per command)")
    rt.add_argument("--key", choices=sorted(HELD_COMMANDS), nargs="+",
                    default=sorted(HELD_COMMANDS))
    rt.add_argument("--outdir", type=Path, required=True)
    rt.add_argument("--tail-s", type=float, default=1.0)
    a = ap.parse_args(argv)
    if a.cmd == "rtl":
        worst = 0
        for key in a.key:
            r = rtl_replay(key, a.outdir, tail_s=a.tail_s)
            (Path(a.outdir) / f"{key}.hold-rtl.json").write_text(
                json.dumps(r, indent=1, default=str) + "\n")
            if r["state"] != "COMPLETE":
                print(f"{key}: {r['state']} -- {r['reason']}")
                worst = max(worst, 2)
                continue
            print(f"{key}: model {r['model']['verdict']}, hold {r['hold']['verdict']} "
                  f"({r['hold']['reason']}), release identity "
                  f"{r['release_identity']['verdict']}")
            if (r["model"]["verdict"], r["hold"]["verdict"],
                    r["release_identity"]["verdict"]) != (PASS, PASS, PASS):
                worst = max(worst, 1)
        return worst
    if a.cmd == "measure":
        m = measure(HELD_COMMANDS[a.key], epoch=a.epoch,
                    reply_delay_s=a.reply_delay_ms / 1e3, tx_delay_s=a.tx_delay_ms / 1e3,
                    hold_frames=a.hold_frames, inject=a.inject)
        v, why = hold_verdict(m)
        m["verdict"], m["reason"] = v, why
        m.pop("_harness", None)
        print(json.dumps(m, indent=1))
        return {PASS: 0, FAIL: 1, REFUSED: 2}.get(v, 3)
    if a.cmd == "table":
        conds = SELECTION if a.set == "selection" else untouched()
        rows = [run_condition(c, inject=a.inject) for c in conds]
        for m in rows:
            print(_row(m))
        if a.json:
            for m in rows:
                m.pop("_harness", None)
            a.json.write_text(json.dumps(rows, indent=1, default=str) + "\n")
        if any(m["verdict"] == NO_VERDICT for m in rows):
            return 3
        return 0 if all(m["ok"] for m in rows) else 1
    res = [run_control(n) for n in CONTROLS]
    for r in res:
        print(json.dumps(r))
    if any(r["caught"] is None for r in res):
        return 3
    return 0 if all(r["caught"] for r in res) else 1


if __name__ == "__main__":
    raise SystemExit(main())
