#!/usr/bin/env python3
"""Verify musical-length playback: the real CLI, rolled, against the fixture.

    .venv/bin/python fpga/verify_rolling_playback.py                 # sim + controls
    .venv/bin/python fpga/verify_rolling_playback.py --rtl demo      # + UART RTL replay

WHAT IS UNDER TEST. `uart_host.main(["run", "--fixture", F])` -- the shipped
command, its Bridge and its rolling scheduler -- talking to the device
contract (fpga/uart_device_sim.py) on SIMULATED time. Nothing in the host is
replaced but the serial port and the clock (`main(bridge_factory=...)`).

WHAT IT IS CHECKED AGAINST. The fixture itself, not the planner: every
write `MusicHost.load()` emitted must execute, in order, before the music's
t=0; every other write must execute at EXACTLY t=0 plus its fixture frame
(after the link's own simultaneous-event spreading, which is part of the
fixture's rendering and is reported, not hidden). t=0 is the one number the
host chooses; it is read from the host and checked for plausibility, and
every other expectation is fixed relative to it.

WHAT IT RECORDS. The feasibility numbers the plan's whole-phrase average
cannot give: peak event-queue occupancy, minimum deadline slack (due minus
acceptance frame, per event), window count, counter wraps crossed, and the
spreading: how many writes the link moved and how far the worst anchor
(a stop bit or gate -- the audible instant) moved.

--rtl ALSO replays the host's ACTUAL bytes (the SimSerial transmit log,
STATUS polls included, at the frames the host wrote them) through the UART
RTL wrapper bench (fpga/verify_uart_bridge.py), whose I2S is compared with
the model driven by the INTENDED schedule above -- so a host that emitted
the wrong due, value or order is visible on the wire, not cancelled out.

CONTROLS (each must turn the run red for its recorded reason):

  ROLL_QUEUE_UNAWARE  window cut ignores queued events -> device drops (demo)
  ROLL_TAG_SPLIT      setup split by tag -> in-pattern accents play at t=0
  ROLL_NO_UNWRAP      anchors not counted across wraps -> refused or late
  corrupt-byte        one payload bit flipped on the wire -> checksum ERR, FAIL
  reset-mid           device reset mid-phrase -> host REFUSES, never continues

Exit 0 when every clean case passes and every control is caught for its
reason, 1 otherwise, 2 refused (apparatus precondition failed).
"""
from __future__ import annotations

import argparse
import contextlib
import io
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for _p in ("fpga", "rtl-sketch", "model"):
    sys.path.insert(0, str(ROOT / _p))

import uart_device_sim as dev                     # noqa: E402
import uart_host as uh                            # noqa: E402

SR = uh.SR
FIXTURES = ("bar808-full", "demo")
TAIL_S = 1.0          # simulated time after the host returns: decay tails
ANCHOR_TAGS = {"stops-on", "gate", "trig"}        # the audible instants


# ---- the intended schedule, from the fixture ---------------------------------
def intended(fixture: str, preset: str | None = None) -> dict:
    """What must happen, derived from the fixture and the preset image
    alone. `static` is in send order; `timed` is (rel_frame, flag, sec,
    addr, data) with rel_frame measured from the music's t=0."""
    static_w, timed_w, _n, _host = uh.fixture_split(fixture)
    static = [tuple(w) for w in uh.voice_image_writes(preset)]
    static += [(w.flag, w.sec, w.addr, w.data & 0xFFFFFFFF) for w in static_w]
    timed = [(w.frame, w.flag, w.sec, w.addr, w.data & 0xFFFFFFFF) for w in timed_w]
    moved = [w for w in timed_w if w.frame != w.nominal]
    anchors = [w for w in timed_w if w.tag in ANCHOR_TAGS]
    worst = max((w.frame - w.nominal for w in anchors), default=0)
    return {"static": static, "timed": timed,
            "spreading": {"timed_writes": len(timed_w),
                          "moved": len(moved),
                          "anchors": len(anchors),
                          "anchors_moved": sum(1 for w in anchors
                                               if w.frame != w.nominal),
                          "worst_anchor_shift_frames": worst,
                          "worst_anchor_shift_ms": round(worst * 1000 / SR, 3)}}


# ---- one run of the real CLI on the simulated device --------------------------
class Harness:
    def __init__(self, epoch: int = 0):
        self.clock = dev.SimClock()
        self.sim = dev.UartDeviceSim(epoch_frame=epoch, clock=self.clock)
        self.ser = dev.SimSerial(self.sim)
        self.bridge = None

    def factory(self, _port, baud):
        self.bridge = uh.Bridge.on_serial(self.ser, baud, clock=self.clock)
        return self.bridge


def run_cli(fixture: str, *, epoch: int = 0, inject: str | None = None,
            wire_fault=None, reset_after_events: int | None = None) -> dict:
    """uart_host.main on the scripted device; returns everything observed."""
    h = Harness(epoch)
    if wire_fault:
        h.ser.mutate = wire_fault
    if reset_after_events is not None:
        # press BTN0 once the device has accepted this many scheduled events
        orig_accept = h.sim._accept

        def accept(op, pkt, t):
            orig_accept(op, pkt, t)
            n = sum(1 for k, _ in h.sim.received if k == "event")
            if op == dev.OP_EVENT and n == reset_after_events and not h.sim.resets:
                h.ser.reset()
        h.sim._accept = accept
    want = intended(fixture)                    # the fixture's intent, uninjected
    saved = set(uh.INJECT_BUGS)
    uh.INJECT_BUGS.clear()
    if inject:
        uh.INJECT_BUGS.add(inject)
    out, err = io.StringIO(), io.StringIO()
    try:
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                rc = uh.main(["run", "--fixture", fixture, "--port", "sim"],
                             bridge_factory=h.factory)
            except SystemExit as exc:          # a Bridge REFUSED path
                rc = exc.code if isinstance(exc.code, int) else 2
    finally:
        uh.INJECT_BUGS.clear()
        uh.INJECT_BUGS.update(saved)
    h.ser.run_until(h.clock.t + TAIL_S)
    return {"rc": rc, "stdout": out.getvalue(), "stderr": err.getvalue(),
            "h": h, "want": want}


def _unwrap(frames16: list, start: int) -> list:
    """16-bit device frames as a monotone timeline beginning near `start`."""
    out, prev, acc = [], start & 0xFFFF, start
    for f in frames16:
        d = (f - prev) & 0xFFFF
        acc += d if d < 0x8000 else d - 0x10000
        prev = f
        out.append(acc)
    return out


def check(run: dict) -> dict:
    """Compare what the device executed with the fixture's intent."""
    h, want = run["h"], run["want"]
    sim, bridge = h.sim, h.bridge
    res = {"rc": run["rc"], "reasons": []}
    live = [w for w in sim.writes if w[5] == "live"]
    ev = [w for w in sim.writes if w[5] == "event"]
    res["static_executed"] = len(live)
    res["static_intended"] = len(want["static"])
    res["timed_executed"] = len(ev)
    res["timed_intended"] = len(want["timed"])
    res["device_errors"] = [list(e) for e in sim.errors]
    res["drops"] = sim.drops
    res["resets"] = sim.resets
    if run["rc"] != 0:
        tail = (run["stderr"].strip().splitlines() or ["?"])[-1]
        res["reasons"].append(f"cli rc {run['rc']}: {tail}")
    if sim.errors or sim.drops:
        codes = sorted({e[0] for e in sim.errors})
        res["reasons"].append(f"device errors {codes}, drops {sim.drops}")
    if [(w[1], w[2], w[3], w[4]) for w in live] != list(want["static"]):
        res["reasons"].append("static image not executed exactly, in order")
    p0 = bridge.performance_origin if bridge else None
    res["performance_origin"] = p0
    if p0 is None:
        res["reasons"].append("host recorded no performance origin")
        res["ok"] = False
        return res
    # P0 must follow the whole static image: music never plays on a
    # half-loaded patch
    if live and ((p0 - live[-1][0]) & 0xFFFF) >= 0x8000:
        res["reasons"].append(f"t=0 f{p0} precedes the last static write "
                              f"f{live[-1][0]}")
    got_frames = _unwrap([w[0] for w in ev], p0)
    got = [(f - p0, w[1], w[2], w[3], w[4]) for f, w in zip(got_frames, ev)]
    exp = list(want["timed"])
    timing_bad = sum(1 for g, e in zip(got, exp) if g[0] != e[0])
    value_bad = sum(1 for g, e in zip(got, exp) if g[1:] != e[1:])
    res["timing_bad"], res["value_bad"] = timing_bad, value_bad
    if len(got) != len(exp):
        res["reasons"].append(f"{len(got)} timed writes executed, {len(exp)} intended")
    if timing_bad:
        first = next((g, e) for g, e in zip(got, exp) if g[0] != e[0])
        res["reasons"].append(f"{timing_bad} timed writes off their frame "
                              f"(first: at {first[0][0]}, intended {first[1][0]})")
    if value_bad:
        res["reasons"].append(f"{value_bad} timed writes with wrong values/order")
    # feasibility numbers, from the device's own acceptance log
    acc = [(f, due) for k, v in sim.received if k == "event" for f, due, *_ in [v]]
    slack = [((due - f) & 0xFFFF) for f, due in acc]
    slack = [s if s < 0x8000 else s - 0x10000 for s in slack]
    span = (got_frames[-1] - got_frames[0]) if got_frames else 0
    res["metrics"] = {
        "peak_queue": sim.evq_peak, "queue_depth": uh.EVENT_QUEUE_DEPTH,
        "min_deadline_slack_frames": min(slack) if slack else None,
        "event_packets": len(acc),
        "status_polls": sim.status_requests,
        "phrase_span_frames": span,
        "phrase_span_s": round(span / SR, 4),
        "counter_wraps_crossed": ((p0 + span) >> 16) - (p0 >> 16) if span else 0,
        "host_sim_seconds": round(h.clock.t, 3),
        "spreading": want["spreading"],
    }
    bound = uh.EVENT_QUEUE_DEPTH - uh.ROLLING_QUEUE_MARGIN
    if sim.evq_peak > bound:
        res["reasons"].append(f"queue peaked at {sim.evq_peak}, above the "
                              f"planned bound {bound}: the cut's occupancy "
                              "model is not conservative")
    res["ok"] = not res["reasons"]
    return res


# ---- the RTL replay of the host's actual bytes -------------------------------
def _packets(data: bytes) -> list:
    sizes = {dev.OP_WRITE: 8, dev.OP_EVENT: 10, dev.OP_STATUS: 2, dev.OP_ABORT: 2}
    out, i = [], 0
    while i < len(data):
        n = sizes[data[i]]
        out.append(data[i:i + n])
        i += n
    return out


def write_rtl_capture(run: dict, prefix: Path) -> dict:
    """<prefix>.cmds/.plan.json in verify_uart_bridge's replay format, from
    the host's TRANSMIT LOG (the bytes main() actually wrote, at the frames
    it wrote them) with expectations from the INTENDED schedule. Requires a
    run from epoch 0 so device frames and bench frames coincide."""
    h, want = run["h"], run["want"]
    if h.sim.epoch != 0 or h.sim.resets:
        raise ValueError("RTL capture needs an epoch-0 run with no reset")
    p0 = h.bridge.performance_origin
    bc = uh.byte_cycles(uh.DEFAULT_BAUD)
    rows, static_i, timed_i = [], 0, 0
    wire_free = 0
    first_event_t = next(t for t, b in h.ser.tx_log if b and b[0] == dev.OP_EVENT)
    k = round((first_event_t * SR - p0) / 65536)
    p0u = p0 + 65536 * k                       # t=0 on the bench's timeline
    for t, data in h.ser.tx_log:
        send = int(t * SR) + 1                 # the bench starts on a frame edge
        for pkt in _packets(data):
            start = max(send * uh.CYC_PER_FRAME + 1, wire_free)
            end = start + len(pkt) * bc
            wire_free = end
            accept = end // uh.CYC_PER_FRAME
            row = {"index": len(rows), "packet": pkt.hex(), "send_frame": send,
                   "accept_frame": accept}
            if pkt[0] == dev.OP_WRITE:
                f, s_, a, d = want["static"][static_i]
                static_i += 1
                row.update(kind="write", due=-1, apply_frame=accept + 1,
                           expect={"flag": f, "sec": s_, "addr": a, "data": d})
            elif pkt[0] == dev.OP_EVENT:
                rel, f, s_, a, d = want["timed"][timed_i]
                timed_i += 1
                row.update(kind="event", due=p0u + rel, apply_frame=p0u + rel,
                           expect={"flag": f, "sec": s_, "addr": a, "data": d})
            elif pkt[0] == dev.OP_STATUS:
                row.update(kind="status", due=-1, apply_frame=-1, expect=None)
            else:
                raise ValueError(f"unexpected opcode 0x{pkt[0]:02x} in the host log")
            rows.append(row)
    prefix.parent.mkdir(parents=True, exist_ok=True)
    with open(f"{prefix}.cmds", "w") as fh:
        for r in rows:
            fh.write(f"S {r['send_frame']} {bytes.fromhex(r['packet']).hex(' ')}\n")
    rec = {"origin": 0, "baud": uh.DEFAULT_BAUD, "base_send_frame": 0,
           "performance_origin": p0u, "source": "SimSerial transmit log",
           "rows": rows}
    Path(f"{prefix}.plan.json").write_text(json.dumps(rec, indent=1) + "\n")
    return {"rows": len(rows), "writes": static_i, "events": timed_i,
            "last_due": max((r["due"] for r in rows), default=0)}


def rtl_replay(fixture: str, outdir: Path) -> dict:
    import verify_uart_bridge as vub
    run = run_cli(fixture)
    sim_res = check(run)
    if not sim_res["ok"]:
        return {"state": "REFUSED", "reason": "the sim run is not clean; "
                "replaying it would test nothing", "sim": sim_res}
    cap = write_rtl_capture(run, outdir / fixture)
    rr = vub.simulate_replay(str(outdir / fixture), outdir / "rtl",
                             tail_frames=int(TAIL_S * SR))
    if rr is None:
        return {"state": "REFUSED", "reason": "RTL replay did not run",
                "capture": cap}
    ok, comp, detail = vub.analyze(rr)
    return {"state": "PASS" if ok else "FAIL", "capture": cap,
            "comparison": comp, "detail": detail[:10]}


# ---- controls ---------------------------------------------------------------
def _flip_one_event(nth: int):
    """Wire fault: flip one payload bit of the nth scheduled-event packet."""
    seen = {"n": 0}

    def mutate(data: bytes) -> bytes:
        out = bytearray(data)
        for off, pkt in _offsets(data):
            if pkt[0] == dev.OP_EVENT:
                seen["n"] += 1
                if seen["n"] == nth:
                    out[off + 5] ^= 0x01
        return bytes(out)
    return mutate


def _offsets(data: bytes):
    i = 0
    for pkt in _packets(data):
        yield i, pkt
        i += len(pkt)


CONTROLS = {
    # name: (fixture, run kwargs, predicate on the result, the reason)
    "ROLL_QUEUE_UNAWARE": ("demo", {"inject": "ROLL_QUEUE_UNAWARE"},
                           lambda r: r["drops"] > 0 and any(
                               e[0] == dev.ERR_EVQ_FULL for e in r["device_errors"]),
                           "event-queue overflow reported by the device"),
    "ROLL_TAG_SPLIT": ("bar808-full", {"inject": "ROLL_TAG_SPLIT"},
                       lambda r: r["timed_executed"] < r["timed_intended"]
                       and r["static_executed"] > r["static_intended"],
                       "in-pattern writes executed as setup, missing from the music"),
    "ROLL_NO_UNWRAP": ("bar808-full", {"inject": "ROLL_NO_UNWRAP"},
                       lambda r: r["rc"] != 0 and r["timing_bad"] + abs(
                           r["timed_executed"] - r["timed_intended"]) > 0,
                       "phrase not delivered past the first half-revolution"),
    "corrupt-byte": ("bar808-full", {"wire_fault": "flip"},
                     lambda r: r["rc"] == 2 and any(
                         e[0] == dev.ERR_CHECKSUM for e in r["device_errors"])
                     and any("never queued" in x and "ERR [5]" in x
                             for x in r["reasons"]),
                     "checksum ERR; the host sees the missing event at its "
                     "next window and REFUSES rather than play on with a hole"),
    "reset-mid": ("demo", {"reset_after_events": 100},
                  lambda r: r["rc"] == 2 and r["resets"] == 1
                  and r["timed_executed"] < r["timed_intended"],
                  "host REFUSES after the reset instead of continuing"),
}


def run_control(name: str) -> dict:
    fixture, kw, pred, reason = CONTROLS[name]
    kw = dict(kw)
    if kw.get("wire_fault") == "flip":
        kw["wire_fault"] = _flip_one_event(120)
    r = check(run_cli(fixture, **kw))
    caught = (not r["ok"]) and bool(pred(r))
    return {"control": name, "fixture": fixture, "intended_reason": reason,
            "caught": caught, "reasons": r["reasons"],
            "rc": r["rc"], "timed": [r["timed_executed"], r["timed_intended"]],
            "static": [r["static_executed"], r["static_intended"]],
            "device_errors": sorted({e[0] for e in r["device_errors"]})}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--outdir", type=Path,
                    default=ROOT / "fpga/reports/arty/rolling-playback")
    ap.add_argument("--rtl", nargs="*", default=None, metavar="FIXTURE",
                    help="also replay the host bytes through the UART RTL")
    ap.add_argument("--epochs", default="0,32000,65300",
                    help="device frame counter at the host's first STATUS")
    a = ap.parse_args(argv)
    epochs = [int(e) for e in a.epochs.split(",")]
    a.outdir.mkdir(parents=True, exist_ok=True)
    ok = True
    clean = {}
    for fx in FIXTURES:
        for ep in epochs:
            r = check(run_cli(fx, epoch=ep))
            clean[f"{fx}@{ep}"] = r
            ok &= r["ok"]
            m = r.get("metrics", {})
            print(f"rolling[{fx} epoch {ep}]: {'PASS' if r['ok'] else 'FAIL'} -- "
                  f"timed {r['timed_executed']}/{r['timed_intended']}, static "
                  f"{r['static_executed']}/{r['static_intended']}, peak queue "
                  f"{m.get('peak_queue')}, min slack "
                  f"{m.get('min_deadline_slack_frames')} fr, wraps "
                  f"{m.get('counter_wraps_crossed')}"
                  + ("" if r["ok"] else f" -- {r['reasons']}"))
    controls = {}
    for name in CONTROLS:
        c = run_control(name)
        controls[name] = c
        ok &= c["caught"]
        print(f"rolling control {name}: {'CAUGHT' if c['caught'] else 'MISSED'} "
              f"-- {c['reasons'][:2]}")
    record = {"tool": "fpga/verify_rolling_playback.py", "baud": uh.DEFAULT_BAUD,
              "clean": clean, "controls": controls,
              "state": "PASS" if ok else "FAIL"}
    if a.rtl is not None:
        rtl = {}
        for fx in (a.rtl or ["demo"]):
            rr = rtl_replay(fx, a.outdir / "rtl-replay")
            rtl[fx] = rr
            ok &= rr["state"] == "PASS"
            comp = rr.get("comparison", {})
            print(f"rolling RTL[{fx}]: {rr['state']} -- writes "
                  f"{comp.get('writes_seen')}/{comp.get('writes_sent')}, timing bad "
                  f"{comp.get('frame_pred_bad')}, I2S mismatch "
                  f"{comp.get('wire_mismatch')} over {comp.get('periods')} periods"
                  + (f" -- {rr.get('detail') or rr.get('reason')}"
                     if rr["state"] != "PASS" else ""))
        record["rtl"] = rtl
        record["state"] = "PASS" if ok else "FAIL"
    (a.outdir / "verification.json").write_text(
        json.dumps(record, indent=2, default=str) + "\n")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
