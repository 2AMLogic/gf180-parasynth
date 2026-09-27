#!/usr/bin/env python3
"""fpga/measure_mac_midi_latency.py -- Mac host scheduling latency, live MIDI (#322).

    .venv/bin/python fpga/measure_mac_midi_latency.py            # on the Mac
    .venv/bin/python fpga/measure_mac_midi_latency.py --source direct   # no CoreMIDI

WHAT IS MEASURED. The HOST half of LATENCY_TARGET (fpga/live_midi_contract.py,
criterion live-midi/1, unchanged): the real `MidiSession` and `run_live`
loop on this machine's scheduler and wall clock, fed by the real CoreMIDI
adapter (fpga/coremidi_input.py) from an in-process VIRTUAL SOURCE that plays
the declared load (`sustained`, seed 281, 20 s) in real time. UART and device
timing are NOT measured here: the session writes into the device contract
(uart_device_sim, SimSerial) evaluated on the same wall clock, so the wire and
the device queue are the modelled ones and only the host varies. USB, the
FTDI bridge and the board are the hardware capture's job (plan087 section 8).

Reported, separately:

  endpoint    receipt -> applied, the contract's endpoints: the device frame
              containing the receipt instant (on the device's own timeline,
              not the session's map) to the frame the event's anchor write
              executed in; a superseded knob value starts at its own receipt.
              Against p95 <= 20 / p99 <= 30 ms (property `target`)
  host_hold   receipt -> the anchor packet's first byte written (real host)
  lateness    each event packet's actual write time minus its planned release
              (due - lookahead): what the Mac's scheduler costs. The fixed
              lookahead absorbs it until a packet misses its frame, so the
              endpoint only moves once lateness is large -- this is the number
              that says how much margin the host leaves
  delivery    virtual-source send -> adapter receipt: CoreMIDI's own hand-off,
              BEFORE the endpoint's start, so not part of the target

  on_time     property: every anchor executed in the frame the session
              planned, no device error, no deadline miss

This does NOT re-run T-LIVE-MIDI's oracle (verify_live_midi.check assumes one
time anchor for a run, and a real clock re-anchors); T-LIVE-MIDI stays the
source of truth for WHAT is scheduled. The anchor write is the session's own
`anchor` role, which T-LIVE-MIDI checks against the oracle.

THE CONTROL (rule 2). `--control-stall-ms` (default 25) repeats a shorter run
with the send loop stalled ONCE by that long, 3 s in. It must turn `on_time`
red: proof this apparatus sees host lateness on the device's timeline at all.
A stall it cannot see means the clean number is not evidence.

Exit 0 PASS, 1 FAIL, 2 REFUSED (a precondition: not macOS for --source
coremidi, the messages received are not the messages sent, the device reset,
or the control could not be run).
"""
from __future__ import annotations

import argparse
import difflib
import json
import math
import os
import platform
import subprocess
import sys
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
for _p in (HERE, HERE / "release", ROOT / "model", ROOT / "audition"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import coremidi_input as cmi                               # noqa: E402
import live_midi_contract as C                             # noqa: E402
import midi_session as ms                                  # noqa: E402
import uart_device_sim as dev                              # noqa: E402
import verify_live_midi as vlm                             # noqa: E402

SR = C.SR
OUT = ROOT / "fpga/reports/live-midi/mac-host-latency.json"


class WallClock:
    """SimClock's interface on the real monotonic clock: `t` reads now, and
    setting it (SimSerial waiting for a reply) sleeps until then. The device
    contract is then evaluated lazily on wall time -- no pty, no thread."""

    @property
    def t(self) -> float:
        return time.monotonic()

    @t.setter
    def t(self, value: float) -> None:
        d = value - time.monotonic()
        if d > 0:
            time.sleep(d)

    def monotonic(self) -> float:
        return time.monotonic()

    def sleep(self, seconds: float) -> None:
        if seconds > 0:
            time.sleep(seconds)


class DirectBackend:
    """PortMidiInput's backend with no framework: the player thread calls
    on_bytes. Separates CoreMIDI's hand-off from the session's own timing."""

    def __init__(self, name: str):
        self.port = cmi.PortInfo(name, 1)
        self.on_bytes = None

    def sources(self):
        return [self.port]

    def connect(self, _port, on_bytes, _on_lost):
        self.on_bytes = on_bytes
        return self

    def disconnect(self, _h):
        pass

    wait = staticmethod(cmi.CoreMidiBackend.wait)
    now = staticmethod(time.monotonic)

    def send(self, data: bytes) -> float:
        t = time.monotonic()
        self.on_bytes(time.monotonic(), data)
        return t


def dist(xs) -> dict:
    return {"n": len(xs), "min": min(xs, default=None), "p50": vlm.pctl(xs, 50) if xs else None,
            "p95": vlm.pctl(xs, 95) if xs else None, "p99": vlm.pctl(xs, 99) if xs else None,
            "max": max(xs, default=None)}


def hist(xs) -> dict:
    """1 ms bins keyed by their floor. (verify_live_midi._hist cannot key a
    negative bin -- it crashes on "-1-0" -- and a stalled run can pair one.)"""
    out: dict = {}
    for x in xs:
        out[math.floor(x)] = out.get(math.floor(x), 0) + 1
    return {f"[{k},{k + 1})": out[k] for k in sorted(out)}


class Probe:
    """Observes one live session from outside: each message's receipt, the
    session map's error there, which knob values superseded an unsent one,
    and every event packet as it leaves (group, role, due, value, planned
    release, actual write time). With `stall_ms`, stalls the send loop ONCE
    (the control). It changes nothing the session decides."""

    def __init__(self, s, ser, sim, *, stall_ms: float = 0.0, stall_at: float | None = None):
        self.s, self.ser, self.sim = s, ser, sim
        self.receipts, self.map_err, self.value_t, self.sent, self.stalled = [], [], {}, [], []
        self.parser = ms.MidiParser()
        feed, cc, send_due = s.feed, s._cc, s._send_due

        def w_feed(t, data):
            for m in self.parser.feed(data):
                self.receipts.append((t, m))
            self.map_err.append(s.frame_of(t) - self.truth(t))
            feed(t, data)

        def w_cc(t, m, num, v):
            n0 = s.stats["superseded"]
            cc(t, m, num, v)
            if s.stats["superseded"] > n0:       # written by the EARLIER group
                self.value_t[s.knob_pending[num][0].gid] = t

        def w_send_due(now):
            if stall_ms and not self.stalled and stall_at is not None \
                    and s.clock.monotonic() >= stall_at and s.unsent:
                self.stalled.append(s.clock.monotonic())
                s.clock.sleep(stall_ms / 1000.0)  # THE CONTROL: one host stall
                now = s.clock.monotonic()
            before = [(p, max(s.release_of(p.due), p.not_before)) for p in s.unsent]
            n_tx = len(ser.tx_log)
            send_due(now)
            if len(ser.tx_log) > n_tx:
                t_w = ser.tx_log[-1][0]
                self.sent.extend({"gid": p.gid, "role": p.role, "due": p.due,
                                  "key": (p.write[2], p.write[3] & 0xFFFFFFFF),
                                  "t_written": t_w, "planned": rel}
                                 for p, rel in before if p.sent)
        s.feed, s._cc, s._send_due = w_feed, w_cc, w_send_due

    def truth(self, t: float) -> int:
        """The device frame containing host instant t, on the device's own
        timeline (unwrapped; epoch 0)."""
        return math.floor((t - self.sim._t0) * SR)

    def analyse(self) -> dict:
        s, sim = self.s, self.sim
        executed = [w for w in sim.writes if w[5] == "event"]
        ekeys = [(w[3], w[4] & 0xFFFFFFFF) for w in executed]
        skeys = [p["key"] for p in self.sent]
        pair = {}
        for blk in difflib.SequenceMatcher(None, skeys, ekeys, autojunk=False) \
                .get_matching_blocks():
            for k in range(blk.size):
                pair[blk.a + k] = blk.b + k
        groups = {g.gid: g for g in s.groups}
        lat, hold, off_due, unpaired, by_kind = [], [], 0, 0, {}
        for i, pk in enumerate(self.sent):
            g = groups[pk["gid"]]
            if pk["role"] != "anchor" or g.kind == "panic":
                continue
            if i not in pair:
                unpaired += 1
                continue
            near = self.truth(pk["t_written"])            # unwrap the 16-bit frame
            f16 = executed[pair[i]][0]
            f = f16 + 65536 * round((near - f16) / 65536)
            t_v = self.value_t.get(g.gid, g.t)
            x = (f - self.truth(t_v)) * 1000.0 / SR
            lat.append(x)
            by_kind[g.kind] = max(by_kind.get(g.kind, 0.0), x)
            hold.append((pk["t_written"] - t_v) * 1000.0)
            off_due += f != pk["due"]
        tgt = C.LATENCY_TARGET
        rec = {"endpoint_ms": dist(lat), "endpoint_histogram_ms": hist(lat),
               "endpoint_by_kind_max_ms": by_kind, "host_hold_ms": dist(hold),
               "lateness_ms": dist([(p["t_written"] - p["planned"]) * 1000.0
                                    for p in self.sent]),
               "map_error_frames": {"min": min(self.map_err, default=None),
                                    "max": max(self.map_err, default=None)},
               "anchors_off_due": off_due, "anchors_unpaired": unpaired,
               "superseded": s.stats["superseded"], "refused": dict(s.stats["refused"]),
               "deadline_misses": s.stats["deadline_misses"],
               "device_errors": sorted({e[0] for e in sim.errors}),
               "reanchors": s.stats["reanchors"], "_lat": lat}
        rec["props"] = {
            "target": bool(lat) and rec["endpoint_ms"]["p95"] <= tgt["p95_ms"]
            and rec["endpoint_ms"]["p99"] <= tgt["p99_ms"],
            "on_time": bool(lat) and not off_due and not unpaired and not sim.errors
            and not s.stats["deadline_misses"],
            "load_admitted": not s.stats["refused"].get("queue-pressure")
            and not s.stats["pushed_events"],
        }
        return rec


def sim_reference(seconds: float = 5.0, *, stall_ms: float = 0.0) -> tuple:
    """The Probe on SIMULATED time, driven exactly as verify_live_midi.
    run_session drives the session: its endpoint distribution must equal
    check()'s, which pairs by the independent oracle (the apparatus's
    known-answer test, fpga/test_measure_mac_midi_latency.py)."""
    events = vlm.sc_sustained(seconds)
    clock = dev.SimClock()
    sim = dev.UartDeviceSim(clock=clock)
    ser = dev.SimSerial(sim)
    s = ms.MidiSession(ser, clock=clock, image=vlm.HARNESS_IMAGE)
    s.start()
    probe = Probe(s, ser, sim, stall_ms=stall_ms, stall_at=clock.t + 2.0)
    t0 = clock.t + 0.020
    times = [t0 + e.t for e in events]
    for t, e in zip(times, events):
        s.service(t)
        s.feed(t, vlm.enc(e))
    s.close()
    ser.run_until(clock.t + vlm.RTL_TAIL_S)
    run = {"scenario": "sustained", "inject": None, "events": events, "times": times,
           "session": s, "sim": sim, "ser": ser, "clock": clock, "epoch": 0}
    return probe.analyse(), (None if stall_ms else vlm.check(run, target=True))


def measure(source: str, seconds: float, *, stall_ms: float = 0.0, stall_at_s: float = 3.0) -> dict:
    events = vlm.sc_sustained(seconds)
    wire = [vlm.enc(e) for e in events]
    backend = vsrc = None
    name = f"parasynth-latency-{os.getpid()}"
    if source == "coremidi":
        backend = cmi.CoreMidiBackend()
        vsrc = backend.create_source(name)
        deadline = time.monotonic() + 2.0
        while name not in [p.name for p in backend.sources()] and time.monotonic() < deadline:
            time.sleep(0.01)
        inp = cmi.PortMidiInput(backend, name)
        send = vsrc.send
    else:
        backend = DirectBackend(name)
        inp = cmi.PortMidiInput(backend, name)
        send = backend.send

    clock = WallClock()
    sim = dev.UartDeviceSim(clock=clock)
    ser = dev.SimSerial(sim)
    s = ms.MidiSession(ser, clock=clock, image=vlm.HARNESS_IMAGE)
    load0 = os.getloadavg()
    s.start()
    probe = Probe(s, ser, sim, stall_ms=stall_ms, stall_at=time.monotonic() + 0.2 + stall_at_s)
    emits = []

    def play():
        t0 = time.monotonic() + 0.2
        for e, b in zip(events, wire):
            d = t0 + e.t - time.monotonic()
            if d > 0:
                time.sleep(d)
            emits.append(send(b))
    th = threading.Thread(target=play, daemon=True)
    th.start()
    why = ms.run_live(s, inp, duration_s=0.2 + events[-1].t + 0.3)
    th.join(5.0)
    inp.close()
    if vsrc is not None:
        vsrc.dispose()
    if hasattr(backend, "close"):
        backend.close()
    ser.run_until(clock.t + vlm.RTL_TAIL_S)

    rec = {"source": source, "seconds": seconds, "events": len(events), "why_closed": why,
           "stall_ms": stall_ms, "stalled": bool(probe.stalled),
           "loadavg_before_after": [round(load0[0], 2), round(os.getloadavg()[0], 2)],
           "preconditions": []}
    got = [m for _t, m in probe.receipts]
    if got != wire:
        rec["preconditions"].append(f"received {len(got)} messages, sent {len(wire)}: "
                                    "receipts cannot be attributed to the load")
    if sim.resets:
        rec["preconditions"].append("the device reset during the run")
    if rec["preconditions"]:
        return rec
    rec.update(probe.analyse())
    rec["delivery_ms"] = dist([(r - e) * 1000.0 for (r, _m), e in zip(probe.receipts, emits)])
    rec.pop("_lat")
    return rec


def host_identity() -> dict:
    try:
        sha = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "HEAD"], capture_output=True,
                             text=True, check=True).stdout.strip()
        dirty = bool(subprocess.run(["git", "-C", str(ROOT), "status", "--porcelain",
                                     "--untracked-files=no"], capture_output=True, text=True,
                                    check=True).stdout.strip())
    except (OSError, subprocess.CalledProcessError):
        sha, dirty = None, None
    return {"platform": platform.platform(), "machine": platform.machine(),
            "mac_ver": platform.mac_ver()[0], "python": platform.python_version(),
            "cpus": os.cpu_count(), "loadavg": [round(x, 2) for x in os.getloadavg()],
            "git_sha": sha, "git_dirty": dirty}


def fmt(d: dict) -> str:
    if not d or d.get("p50") is None:
        return "n/a"
    return (f"p50 {d['p50']:.3f}, p95 {d['p95']:.3f}, p99 {d['p99']:.3f}, "
            f"max {d['max']:.3f} ms (n {d['n']})")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--source", choices=("coremidi", "direct"), default="coremidi")
    ap.add_argument("--seconds", type=float, default=C.SUSTAINED_S)
    ap.add_argument("--control-stall-ms", type=float, default=25.0)
    ap.add_argument("--control-seconds", type=float, default=6.0)
    ap.add_argument("--allow-loaded", action="store_true",
                    help="measure even when the 1-minute load average exceeds the CPU "
                         "count (the result is then labelled a LOADED host)")
    ap.add_argument("--json", type=Path, default=OUT)
    a = ap.parse_args(argv)
    if a.source == "coremidi" and sys.platform != "darwin":
        print(f"mac-latency: REFUSED -- --source coremidi needs macOS (this is {sys.platform})")
        return 2
    host = host_identity()
    loaded = host["loadavg"][0] > (host["cpus"] or 1)
    if loaded and not a.allow_loaded:
        print(f"mac-latency: REFUSED -- load average {host['loadavg'][0]} on {host['cpus']} "
              "CPUs: this would measure the other load, not the host path. Close it, or "
              "pass --allow-loaded to record a LOADED-host result")
        return 2
    rec = {"tool": "fpga/measure_mac_midi_latency.py", "issue": 322,
           "criterion_version": C.CRITERION_VERSION, "target": {
               "p95_ms": C.LATENCY_TARGET["p95_ms"], "p99_ms": C.LATENCY_TARGET["p99_ms"]},
           "host": host, "host_loaded": loaded,
           "scope": "host only: UART and device are the modelled contract (SimSerial on "
                    "wall time); USB/FTDI/board are the hardware capture's"}
    clean = measure(a.source, a.seconds)
    ctl = measure(a.source, a.control_seconds, stall_ms=a.control_stall_ms)
    rec["clean"], rec["control"] = clean, ctl
    print(f"mac-latency: {host['platform']}, {host['cpus']} CPUs, load {host['loadavg']}"
          + (" -- LOADED HOST" if loaded else ""))
    for label, r in (("clean", clean), (f"control stall {a.control_stall_ms:g} ms", ctl)):
        if r["preconditions"]:
            print(f"mac-latency[{label}]: REFUSED -- {'; '.join(r['preconditions'])}")
            continue
        print(f"mac-latency[{label}]: {r['props']}")
        print(f"  endpoint (receipt -> applied): {fmt(r['endpoint_ms'])}")
        print(f"  host hold (receipt -> anchor written): {fmt(r['host_hold_ms'])}")
        print(f"  host lateness vs planned release, every packet: {fmt(r['lateness_ms'])}")
        print(f"  {r['source']} delivery (before the endpoint): {fmt(r['delivery_ms'])}")
        print(f"  anchors off their due {r['anchors_off_due']}, unpaired "
              f"{r['anchors_unpaired']}; deadline misses {r['deadline_misses']}, device errors "
              f"{r['device_errors']}, re-anchors {r['reanchors']}, map error "
              f"{r['map_error_frames']} frames; refused {r['refused']}")
    if clean["preconditions"] or ctl["preconditions"] or not ctl.get("stalled"):
        verdict = "REFUSED"
    else:
        matrix = {k: "MOVED" if clean["props"][k] != ctl["props"][k] else "BLIND"
                  for k in clean["props"]}
        caught = clean["props"]["on_time"] and not ctl["props"]["on_time"]
        rec["control_matrix"], rec["control_caught"] = matrix, caught
        verdict = ("PASS" if all(clean["props"].values()) and caught else
                   "NO VERDICT" if not clean["props"]["on_time"] else "FAIL")
        print(f"mac-latency: control {'CAUGHT' if caught else 'NOT DEMONSTRATED'} (on_time "
              f"must move from a clean on_time run) -- {matrix}")
    rec["verdict"] = verdict
    a.json.parent.mkdir(parents=True, exist_ok=True)
    a.json.write_text(json.dumps(vlm._clean(rec), indent=1, default=str) + "\n")
    print(f"mac-latency: {verdict} -> {a.json}")
    return {"PASS": 0, "FAIL": 1}.get(verdict, 2)


if __name__ == "__main__":
    raise SystemExit(main())
