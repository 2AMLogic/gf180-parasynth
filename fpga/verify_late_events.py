#!/usr/bin/env python3
"""fpga/verify_late_events.py -- late events through the REAL uart_bridge.v, and the model (#329).

    .venv/bin/python fpga/verify_late_events.py              # RTL + model (iverilog; the box)
    .venv/bin/python fpga/verify_late_events.py --model-only # the model alone (seconds)

WHAT IS UNDER TEST. rtl-sketch/uart_bridge.v, cycle-exact in
rtl-sketch/tb_uart_late.v (the bridge with synth_top.v's frame counter and
write grant), and fpga/uart_device_sim.py (the device model every host test
runs against). Each is driven with the same event packets, accepted at the
same (audio frame, cycle) instants, and each outcome is compared with the
LATE-EVENT POLICY (fpga/late_event_policy.py). That policy was committed
before any result, and neither implementation is consulted to build it.

For every event the comparison is:
  * verdict: ACK / ERR 3 and executed (late) / ERR 3 and not executed
    (order) / ERR 2 (full). The verdict is read from the TX bytes (RTL) or
    the reply outbox (model);
  * the audio frame it executed in, read from the write port.
STATUS replies are checked for the drop counter and the overflow flag
(P4/P5).

SCENARIOS (one simulation each). Acceptances sit mid-frame unless a test
names the frame boundary:
  boundary        P1/P2/P7: due A+1 accepted in the LAST cycle of audio frame A
                  (on time) and in the FIRST cycle of A+1 (late, d == 0);
                  due == A; due 100 behind; due 32768 ahead (late); ordinary
                  events after each; a late event behind a queued future one
                  (order); the future one still executes on time
  boundary-wrap   the same list, placed so that it crosses 65535 -> 0
  backlog         P2/P3/P6: 20 events due in one frame drain two a frame. A
                  late event arrives mid-drain with a raw due at or after the
                  tail's, and another with a raw due BEFORE the tail's (its
                  effective due is not)
  overflow        P4/P5: 64 queued, 3 more dropped with ERR 2 and counted;
                  STATUS; drain; recovery with on-time and late events;
                  STATUS again

CONTROLS (rule 2). Each implementation must be able to fail this bench:
  --control model-revolution   the model as shipped before #329 (a late event
                               queued at its acceptance frame and scheduled
                               for the frame's midpoint, one revolution late
                               once that is behind the cursor): must FAIL on
                               execution frames
  (RTL) the tb's replica of synth_top's frame/grant is asserted against
        synth_top.v's text; a drifted replica is REFUSED, not compared.

Exit 0 PASS, 1 FAIL (a disagreement with the policy, printed per event),
2 REFUSED / NO VERDICT (apparatus precondition).
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for _p in ("fpga", "rtl-sketch", "model"):
    if str(ROOT / _p) not in sys.path:
        sys.path.insert(0, str(ROOT / _p))

import late_event_policy as pol                           # noqa: E402
import uart_host as uh                                    # noqa: E402

BENCH = ROOT / "rtl-sketch/tb_uart_late.v"
BRIDGE = ROOT / "rtl-sketch/uart_bridge.v"
SYNTH_TOP = ROOT / "rtl-sketch/synth_top.v"
CYC = 256
BAUD = uh.DEFAULT_BAUD
DIV = (12_288_000 + BAUD // 2) // BAUD
EVENT_CYCLES = 10 * 10 * DIV                  # one event packet on the wire
STATUS_CYCLES = 2 * 10 * DIV
GAP = 3 * 10 * DIV                            # idle between packets
SR = 48_000
MID = 128                                     # a mid-frame cycle, clear of the slots


class Refused(RuntimeError):
    pass


# ---- the timeline: audio frame and cyc of an absolute bench cycle -------------------
# (synth_top: cyc increments every cycle from 0 at reset release; the frame
# register increments on the edge where cyc == 0, so it reads frame0 at cycle 0
# and frame0 + ceil(k/256) at cycle k; the AUDIO frame is register - 1.) These
# are used only to AIM acceptances; every frame used in a comparison is the
# one the bench printed at that cycle.
def audio_at(frame0: int, k: int) -> int:
    return frame0 + -(-k // CYC) - 1


def cycle_for(frame0: int, audio: int, cyc: int) -> int:
    """The bench cycle at which the audio frame is `audio` and cyc is `cyc`
    (cyc 1..255 then 0: cyc 0 is the LAST cycle of an audio frame)."""
    m = audio - frame0                       # audio frame frame0 + m spans k in [256m+1, 256(m+1)]
    k = CYC * m + (cyc if cyc else CYC)
    assert audio_at(frame0, k) == audio and k % CYC == cyc, (audio, cyc, k)
    return k


# ---- scenarios: [(kind, accept_audio_frame, cyc, due16 | None, tag)] ---------------
def _data(i: int) -> int:
    return 0x5A000000 | i


def sc_boundary(frame0: int) -> list:
    """A list of packets, each aimed at an acceptance instant, 60 frames apart."""
    a = frame0 + 60
    steps = []

    def ev(off, cyc, due_off, tag, *, abs_due=None):
        A = a + off
        due = (abs_due if abs_due is not None else A + due_off) % 65536
        steps.append(("event", A, cyc, due, tag))
    ev(0, 0, +1, "due A+1, accepted in the LAST cycle of A (on time)")
    ev(60, 1, 0, "due A, accepted in the FIRST cycle of A (late, d == 0)")
    ev(62, MID, +3, "ordinary after a late event")
    ev(120, MID, 0, "due == A mid-frame (late)")
    ev(180, MID, -100, "due 100 behind (late)")
    ev(182, MID, +2, "ordinary after a late event")
    ev(240, MID, +32768, "due 32768 ahead (late: outside the window)")
    ev(300, MID, +32767 - 32767 + 400, "a future event, due A+400")
    ev(360, MID, -3, "late behind a queued future event (order)")
    ev(420, MID, +300, "ordinary, after the future one")
    return steps


def sc_backlog(frame0: int) -> list:
    a = frame0 + 60
    steps = []
    D = a + 20 * 45 + 60                      # every burst event queued before D
    for i in range(20):
        steps.append(("event", a + 45 * i, MID, D % 65536, f"burst {i} due D"))
    steps.append(("event", D + 3, MID, (D + 1) % 65536,
                  "mid-drain late, raw due D+1 >= the tail's D"))
    steps.append(("event", D + 50, MID, (D + 60) % 65536, "ordinary after the backlog"))
    # a second burst, then a late event whose RAW due is BEFORE the tail's
    D2 = D + 50 + 10 * 45 + 60
    for i in range(10):
        steps.append(("event", D + 95 + 45 * i, MID, D2 % 65536, f"burst2 {i} due D2"))
    steps.append(("event", D2 + 2, MID, (D2 - 5) % 65536,
                  "mid-drain late, raw due D2-5 BEFORE the tail's D2 (effective D2+3)"))
    steps.append(("event", D2 + 60, MID, (D2 + 70) % 65536, "ordinary after it"))
    return steps


def sc_overflow(frame0: int) -> list:
    a = frame0 + 60
    steps = []
    D = a + 67 * 45 + 100
    for i in range(67):
        steps.append(("event", a + 45 * i, MID, D % 65536,
                      f"fill {i}" + (" (the queue is full: dropped)" if i >= 64 else "")))
    steps.append(("status", a + 67 * 45, MID, None, "STATUS: drops 3, overflow flag"))
    steps.append(("event", D + 40, MID, (D + 45) % 65536, "recovery: on time"))
    steps.append(("event", D + 90, MID, (D + 80) % 65536, "recovery: late"))
    steps.append(("event", D + 140, MID, (D + 150) % 65536, "recovery: on time"))
    steps.append(("status", D + 190, MID, None, "STATUS: flags cleared, drops still 3"))
    return steps


SCENARIOS = {
    "boundary": (sc_boundary, 1000),
    "boundary-wrap": (sc_boundary, 65536 - 200),
    "backlog": (sc_backlog, 3000),
    "overflow": (sc_overflow, 20000),
}


def packets(steps: list) -> list:
    out = []
    for i, (kind, _A, _c, due, _tag) in enumerate(steps):
        if kind == "event":
            out.append(uh.pkt_event(due, 0, 0, 0x40 + (i % 8), _data(i)))
        else:
            out.append(uh.pkt_status())
    return out


# ---- responses on the wire -------------------------------------------------------
def parse_replies(bs: list) -> list:
    """TX bytes -> [("boot",), ("ack", seq), ("err", code, seq, info), ("status", {...})]"""
    out, i = [], 0
    while i < len(bs):
        b = bs[i]
        if b == 0xA5:
            out.append(("boot",)); i += 1
        elif b == 0x06:
            out.append(("ack", bs[i + 1])); i += 2
        elif b == 0x1C:
            out.append(("err", bs[i + 1], bs[i + 2], bs[i + 3])); i += 4
        elif b == 0x55:
            f = bs[i + 1:i + 8]
            out.append(("status", {"frame": (f[0] << 8) | f[1], "evq": f[2], "wrq": f[3],
                                   "drops": f[4], "errs": f[5], "flags": f[6]})); i += 8
        else:
            raise Refused(f"unparseable reply byte 0x{b:02x} at {i}")
    return out


# ---- the RTL -------------------------------------------------------------------------
REPLICA = [r"wire tick = \(cyc == 8'd0\);",
           r"cyc <= cyc \+ 8'd1;",
           r"if \(tick\) frame <= frame \+ 16'd1;",
           r"localparam integer UART_WIN_LO = GO_CYCLE - 2;",
           r"wire uart_grant = \(cyc >= UART_WIN_LO\) && \(cyc < GO_CYCLE\);",
           r"parameter GO_CYCLE = 8,"]


def check_replica() -> None:
    top, tb = SYNTH_TOP.read_text(), BENCH.read_text()
    for rx in REPLICA:
        if not re.search(rx, top):
            raise Refused(f"synth_top.v no longer holds `{rx}`: the bench's replica of the "
                          "frame counter and UART grant is not the design's")
        if rx.startswith("parameter"):
            continue
        if not re.search(rx, tb):
            raise Refused(f"tb_uart_late.v does not copy `{rx}`")


def run_rtl(name: str, workdir: Path) -> dict:
    fn, frame0 = SCENARIOS[name]
    steps = fn(frame0)
    pk = packets(steps)
    check_replica()
    # calibrate the acceptance latency: one packet, measured
    lat = _calibrate(workdir)
    lines, starts = [], []
    prev_end = 0
    for (kind, A, cyc, _due, _tag), p in zip(steps, pk):
        start = cycle_for(frame0, A, cyc) - (lat if kind == "event" else 0)
        if start < prev_end + GAP // 3:
            raise Refused(f"{name}: packets overlap at step {len(starts)} (start {start}, "
                          f"previous ends {prev_end})")
        starts.append(start)
        prev_end = start + (EVENT_CYCLES if kind == "event" else STATUS_CYCLES)
        lines.append(f"S {start} " + " ".join(f"{b:02x}" for b in p))
    total = prev_end + CYC * 700
    log = _sim(workdir / name, lines, frame0, total)
    acc = [(int(f[1]), int(f[2]), int(f[3])) for f in log if f[0] == "ACC"]
    wr = [(int(f[1]), int(f[2]), int(f[6])) for f in log if f[0] == "W"]
    tx = [int(f[2]) for f in log if f[0] == "T"]
    if any(f[0] == "OVERLAP" for f in log):
        raise Refused(f"{name}: the bench reports an overlapping packet")
    ev_idx = [i for i, s in enumerate(steps) if s[0] == "event"]
    if len(acc) != len(ev_idx):
        raise Refused(f"{name}: {len(acc)} acceptances for {len(ev_idx)} event packets")
    accepted = {}
    for i, (cycle, freg, cyc) in zip(ev_idx, acc):
        A = frame0 + ((freg - 1 - frame0) % 65536)      # unwrapped audio frame
        aimed = steps[i][1:3]
        if (A, cyc) != aimed:
            raise Refused(f"{name}: step {i} was aimed at audio frame {aimed[0]} cyc "
                          f"{aimed[1]} and accepted at {A} cyc {cyc}")
        accepted[i] = A
    execd = {}
    for cycle, freg, data in wr:
        if data >> 24 == 0x5A:
            execd.setdefault(data & 0xFFFFFF, []).append(frame0 + ((freg - 1 - frame0) % 65536))
    return {"steps": steps, "accepted": accepted, "executed": execd,
            "replies": parse_replies(tx), "latency_cycles": lat}


def _sim(d: Path, lines: list, frame0: int, total: int) -> list:
    d.mkdir(parents=True, exist_ok=True)
    (d / "cmds.txt").write_text("\n".join(lines) + "\n")
    exe = d / "tb.vvp"
    r = subprocess.run(["iverilog", "-g2012", "-o", str(exe), str(BENCH), str(BRIDGE)],
                       capture_output=True, text=True)
    if r.returncode:
        raise Refused(f"iverilog failed: {r.stdout}{r.stderr}")
    r = subprocess.run(["vvp", "-n", str(exe), f"+cmds={d / 'cmds.txt'}", f"+log={d / 'log.txt'}",
                        f"+frame0={frame0}", f"+cycles={total}"], capture_output=True,
                       text=True, timeout=3600)
    if r.returncode or "done at cycle" not in r.stdout:
        raise Refused(f"vvp failed: {r.stdout[-400:]}{r.stderr[-400:]}")
    return [ln.split() for ln in (d / "log.txt").read_text().splitlines()]


_LAT = {}


def _calibrate(workdir: Path) -> int:
    if "lat" not in _LAT:
        start = 2000
        log = _sim(workdir / "calibrate", [f"S {start} " + " ".join(
            f"{b:02x}" for b in uh.pkt_event(500, 0, 0, 0x40, 1))], 0, start + 20000)
        acc = [int(f[1]) for f in log if f[0] == "ACC"]
        if len(acc) != 1:
            raise Refused(f"calibration saw {len(acc)} acceptances")
        _LAT["lat"] = acc[0] - start
    return _LAT["lat"]


# ---- the model -----------------------------------------------------------------------
def run_model(name: str, *, control: str | None = None) -> dict:
    """The same packets, accepted by uart_device_sim at the same (audio frame,
    cyc) instants the RTL run was aimed at (and, on the box, confirmed)."""
    import uart_device_sim as dev
    fn, frame0 = SCENARIOS[name]
    steps = fn(frame0)
    pk = packets(steps)
    clock = dev.SimClock()
    sim = dev.UartDeviceSim(epoch_frame=frame0 % 65536, clock=clock)
    ser = dev.SimSerial(sim, boot=True)             # t0 = 0; model frame = audio frame
    if control == "model-revolution":
        _revert_model_fix(sim)
    accepted = {}

    def t_of(A, cyc):
        frac = (((cyc - 1) % CYC) + 0.5) / CYC      # position inside the audio frame
        return (A - frame0 + frac) / SR
    for i, ((kind, A, cyc, _due, _tag), p) in enumerate(zip(steps, pk)):
        t = t_of(A, cyc)
        sim._advance(t)
        clock.t = t
        sim._accept(p[0], p, t)
        if kind == "event":
            accepted[i] = A
    t_end = t_of(steps[-1][1] + 700, MID)
    sim._advance(t_end)
    clock.t = t_end
    execd = {}
    for frame16, _flag, _sec, _addr, data, src in sim.writes:
        if src == "event" and data >> 24 == 0x5A:
            execd.setdefault(data & 0xFFFFFF, []).append(frame0 + ((frame16 - frame0) % 65536))
    bs = b"".join(b for _t, b in sim.outbox)
    return {"steps": steps, "accepted": accepted, "executed": execd,
            "replies": parse_replies(list(bs)), "latency_cycles": None}


def _revert_model_fix(sim) -> None:
    """The CONTROL: reinstate the pre-#329 model behaviour on this instance,
    exactly as it shipped: a late event queued with due = its acceptance frame,
    and the fire time taken at the frame midpoint, pushed a revolution
    forward whenever that midpoint is behind the cursor."""
    import types
    import uart_device_sim as dev
    sim._late_due_offset = 0
    sim._fire_past_heads_now = False
    if not hasattr(dev.UartDeviceSim, "_late_due_offset"):
        raise Refused("the model has no #329 switch to revert (is the fix present?)")
    _ = types


# ---- the comparison --------------------------------------------------------------------
def compare(run: dict) -> dict:
    steps = run["steps"]
    ev = [(i, s) for i, s in enumerate(steps) if s[0] == "event"]
    want = pol.expected([(run["accepted"][i], s[3]) for i, s in ev])
    replies = [r for r in run["replies"] if r[0] != "boot"]
    if len(replies) != len(steps):
        return {"verdict": "NO VERDICT", "why": f"{len(replies)} replies for {len(steps)} packets",
                "rows": []}
    rows, bad = [], 0
    for (i, s), w in zip(ev, want):
        r = replies[i]
        got_exec = run["executed"].get(i, [])
        if r[0] == "ack":
            got = "ack"
        elif r[0] == "err" and r[1] == 3:
            got = "late" if got_exec else "drop-order"
        elif r[0] == "err" and r[1] == 2:
            got = "drop-full"
        else:
            got = f"reply {r}"
        ok = got == w.verdict and (got_exec == ([w.executes] if w.executes is not None else []))
        bad += not ok
        rows.append({"step": i, "tag": s[4], "accept": run["accepted"][i],
                     "due16": s[3], "want": w.verdict, "got": got,
                     "want_exec": w.executes, "got_exec": got_exec, "ok": ok,
                     "beyond_effective_due": (got_exec[0] - w.effective_due)
                     if (got_exec and w.effective_due is not None) else None})
    status = [(i, replies[i][1]) for i, s in enumerate(steps) if s[0] == "status"]
    for i, st in status:
        rows.append({"step": i, "tag": steps[i][4], "status": st})
    worst = max((r["beyond_effective_due"] for r in rows
                 if r.get("beyond_effective_due") is not None), default=0)
    return {"verdict": "PASS" if not bad else "FAIL", "disagreements": bad, "rows": rows,
            "worst_frames_beyond_effective_due": worst}


def status_checks(name: str, cmp: dict) -> list:
    if name != "overflow":
        return []
    sts = [r["status"] for r in cmp["rows"] if "status" in r]
    probs = []
    if len(sts) != 2:
        return [f"expected 2 STATUS replies, got {len(sts)}"]
    if sts[0]["drops"] != 3 or not sts[0]["flags"] & 0x1:
        probs.append(f"first STATUS {sts[0]}: want drops 3 and the overflow flag")
    if sts[1]["drops"] != 3 or sts[1]["flags"] & 0x1:
        probs.append(f"second STATUS {sts[1]}: want drops 3 (cumulative) and the overflow "
                     "flag cleared by the first STATUS")
    return probs


def report(label: str, name: str, cmp: dict, probs: list) -> str:
    lines = [f"{label}[{name}]: {cmp['verdict'] if not probs else 'FAIL'}"
             f" -- {cmp.get('disagreements', '?')} disagreements with the policy; worst "
             f"{cmp.get('worst_frames_beyond_effective_due')} frames beyond the effective due"]
    for r in cmp["rows"]:
        if "status" in r:
            continue
        if not r["ok"]:
            lines.append(f"  step {r['step']:>2} {r['tag']}: accepted {r['accept']} due16 "
                         f"{r['due16']}: policy {r['want']} @ {r['want_exec']}, got {r['got']} "
                         f"@ {r['got_exec']}")
    lines += [f"  {p}" for p in probs]
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--model-only", action="store_true")
    ap.add_argument("--control", choices=("model-revolution",))
    ap.add_argument("--scenario", nargs="*", default=list(SCENARIOS))
    ap.add_argument("--json", type=Path, default=ROOT / "build/late-events/verification.json")
    ap.add_argument("--workdir", type=Path, default=None)
    a = ap.parse_args(argv)
    record = {"tool": "fpga/verify_late_events.py", "policy": "fpga/late_event_policy.py",
              "control": a.control, "rtl": {}, "model": {}}
    verdicts = []
    work = a.workdir or Path(tempfile.mkdtemp(prefix="late-events-"))
    for name in a.scenario:
        if not a.model_only and not a.control:
            try:
                rr = run_rtl(name, work)
                c = compare(rr)
                p = status_checks(name, c)
                c["latency_cycles"] = rr["latency_cycles"]
            except Refused as exc:
                c, p = {"verdict": "NO VERDICT", "why": str(exc), "rows": []}, []
                print(f"late-events RTL[{name}]: NO VERDICT -- {exc}")
            else:
                print(report("late-events RTL", name, c, p))
            v = c["verdict"] if not p else "FAIL"
            record["rtl"][name] = dict(c, status_problems=p, verdict=v)
            verdicts.append(v)
        mm = run_model(name, control=a.control)
        c = compare(mm)
        p = status_checks(name, c)
        print(report("late-events model" + (f" (control {a.control})" if a.control else ""),
                     name, c, p))
        v = c["verdict"] if not p else "FAIL"
        record["model"][name] = dict(c, status_problems=p, verdict=v)
        verdicts.append(v)
    if "NO VERDICT" in verdicts:
        verdict = "NO VERDICT"
    else:
        verdict = "PASS" if all(v == "PASS" for v in verdicts) else "FAIL"
    if a.control:
        # a control is caught only when the clean model passes and this one fails
        verdict = "CAUGHT" if verdict == "FAIL" else "MISSED"
    record["verdict"] = verdict
    a.json.parent.mkdir(parents=True, exist_ok=True)
    a.json.write_text(json.dumps(record, indent=1, default=str) + "\n")
    print(f"late-events: {verdict}")
    return {"PASS": 0, "CAUGHT": 0, "FAIL": 1, "MISSED": 1}.get(verdict, 2)


if __name__ == "__main__":
    raise SystemExit(main())
