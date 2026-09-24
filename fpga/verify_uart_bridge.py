#!/usr/bin/env python3
"""Verify the USB-UART control bridge on the Arty wrapper, at its pins.

    .venv/bin/python fpga/verify_uart_bridge.py                  # clean + controls
    .venv/bin/python fpga/verify_uart_bridge.py --start-red      # against the stub

The bench (rtl-sketch/tb_uart_bx.v) is the host: it serialises packets onto the
reserved UART RX pin at the real baud, holds the SPI pins idle all run, decodes
the I2S wire the way a DAC does, and captures what the bridge says on UART TX.
The expectation comes from fpga/uart_host.py's DEVICE CONTRACT -- the chip is
required to agree with the contract, never with anything it reported itself.
The model (model/synth_top_model.py) is driven from the contract's predicted
frames, so a link that fired an event one frame late would move neither the
model nor the prediction and the comparison would see it.

Scenarios, each a full run of the wrapper:

  held        one command: preset image, note on, hold 1920 frames, note off.
              The release must run to the envelope floor -- asserted on the
              model AND delivered bit-exact on the wire.
  phrase      the existing short scripted M5A phrase, re-rendered through the
              UART contract: every write is an event scheduled at its frame and
              fired by the DEVICE. No host-side sleeps exist anywhere in this
              path.
  overflow    70 events against a 64-deep queue: the 6 that do not fit must be
              dropped AND reported (ERR code 2, drop counter), never silent.
  noff-full   a note-off sent while the event queue is full: the live path must
              still deliver it. A note-off that waits behind a phrase is a
              note that never ends.
  reset-mid   BTN0 reset mid-phrase: queues die with the core (nothing stale
              fires afterwards), the host sees BOOT on the wire, and a fresh
              load works.
  drop-byte   a byte removed from one packet: the checksum must reject it, the
              write must never execute, one error is reported, and the parser
              must recover on the very next packet. (A dropped 0x00 can in
              principle re-align a modulo-256 sum; the scenario drops a
              non-zero byte and the recovery probe is 0xFF, both documented.)
  corrupt-byte one payload bit flipped: rejected by checksum, reported, never
              applied, stream stays aligned.

Negative controls (each must turn this red, with the recorded mismatch):

  UART_SKIP_BYTE       the parser eats one payload byte per write: every
                       checksum fails, no write is delivered (writes_seen 0).
  UART_CORRUPT_ADDR    the address LSB flips at push time, after the checksum:
                       every write lands corrupted (writes_bad == all).
  UART_EVQ_OVF_SILENT  the full queue silently overwrites its oldest entry:
                       the ERR reports never come and six dues never fire.
  UART_NOFF_BLOCKED    the live path is served only when the event queue is
                       empty: the note-off during queue-full never executes.
  UART_RESET_LEAK      the queues survive reset: stale phrase events fire
                       after the reset that should have killed them.

Exit: 0 clean and every control caught for its recorded reason, 1 otherwise,
2 refused. Records land in <outdir>/<run>/verification.json, one per run, and
controls.json summarises the controls. The clean phrase record doubles as the
wrapper's digital evidence for fpga/build_arty.py.
"""
from __future__ import annotations

import argparse
import contextlib
import hashlib
import io
import json
import os
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for _p in ("fpga", "rtl-sketch", "model"):
    sys.path.insert(0, str(ROOT / _p))

import uart_host as uh                              # noqa: E402
import verify_synth_top as top                      # noqa: E402
from build_arty import roms                         # noqa: E402

BENCH = ROOT / "rtl-sketch/tb_uart_bx.v"
WRAPPER = ROOT / "fpga/rtl/arty_a7_top.v"
STUB = ROOT / "fpga/stubs/arty_a7_uart_stub.v"
BRIDGE = ROOT / "rtl-sketch/uart_bridge.v"
CONFIG = {"OSC2X": 1, "FILTER2X": 1, "PULSE2X": 0}

RE_SEG = re.compile(r"SEG (\d+) origin_tcc (\d+) periods (\d+) strobes (\d+)")
RE_RAN = re.compile(r"ran (\d+) frames in (\d+) segments; (\d+) I2S periods decoded")
RE_WRT = re.compile(r"writes drained (\d+), spi\+uart slot collisions (\d+)")
RE_STRB = re.compile(r"sample strobed in (\d+) frames, MISSING in (\d+), worst strobe cycle (\d+) of 256")
RE_BUSY = re.compile(r"busy at a tick: (\d+); core overrun (\d+); link overflow (\d+)")
RE_TXB = re.compile(r"TX bytes captured (\d+)")

INJECTS = ("UART_NO_CHECKSUM", "UART_CORRUPT_ADDR", "UART_EVQ_OVF_SILENT",
           "UART_NOFF_BLOCKED", "UART_RESET_LEAK")
INJECT_SCENARIO = {
    "UART_NO_CHECKSUM": "drop-byte",
    "UART_CORRUPT_ADDR": "corrupt-byte",
    "UART_EVQ_OVF_SILENT": "overflow",
    "UART_NOFF_BLOCKED": "noff-full",
    "UART_RESET_LEAK": "reset-mid",
}


# ---- scenario builders: items in uart_host.plan() format --------------------
def boot_items():
    return [("write", f, s, a, d) for f, s, a, d in uh.voice_image_writes(None)]


def key_events_items(events):
    """Key events through the model's own KeyHost, at the frames it names."""
    import synth_top_model as stm
    import voice_fx as vf
    out = vf.KeyHost().writes(events, vf.VoiceFx.patch_regs())
    items, last_f = [], 0
    for f, op, *args in out:
        if op == "INC":
            k, v, jump = args
            put = ("write", 1 if jump else 0, 0, stm.A_INC + k, v)
        elif op == "TRACK":
            put = ("write", 0, 0, stm.A_TRACK, args[0])
        elif op == "GATE":
            put = ("write", 0, 0, stm.A_GATE_ON if args[0] else stm.A_GATE_OFF, 0)
        else:
            raise ValueError(f"unexpected KeyHost op {op!r}")
        items.append(("wait", max(0, f - last_f)))
        items.append(put)
        last_f = f
    return items


def phrase_items(*, smoke=True):
    """The existing short scripted phrase, re-rendered through the UART
    contract: boot image live, then every phrase write as an EVENT at the frame
    the link schedule names. Dues keep their spacing; if the contract refuses
    (a due that upload speed cannot beat), the whole phrase shifts later in
    whole frames -- the schedule is the device's, never the host's sleep."""
    cmds, tail, _info = top.m5a_script(
        str(ROOT / "docs/scorecard/mono-m5a-miniv3/manifest.json"), smoke=smoke,
        saw_cutoff_hz=20000, saw_volume_correction_db=-0.45428)
    items = boot_items()
    f = 0
    for wait, flag, sec, addr, data in cmds:
        f += wait
        items.append(("event", f, flag, sec, addr, data))
        f += 1
    items.append(("wait", int(tail)))
    return items


def _base_dues_items(n_events, *, spread, hold_before_reset=0, pre=None):
    items = list(pre or boot_items())
    items += key_events_items([(0, "on", 45)])
    f = 400
    for i in range(n_events):
        items.append(("event", f, 0, 0, 0x40 + (i % 3), 0x111111 * (i + 1) & 0xFFFFFFFF))
        f += spread
    if hold_before_reset:
        items.append(("wait", hold_before_reset))
    return items


def sc_held():
    items = boot_items() + key_events_items([(0, "on", 45), (1920, "off", 45)])
    return items, {"tail": 400, "release_tail": 400}


def sc_phrase():
    return phrase_items(), {"tail": 100}


def _shift_until_plannable(items, start_frame=14):
    """plan() refuses a due that upload speed cannot beat; shift the whole
    phrase later, spacing intact, until the contract accepts it."""
    shift = 0
    for _ in range(200):
        shifted = [it if it[0] != "event" else ("event", it[1] + shift, *it[2:])
                   for it in items]
        try:
            rows = uh.plan(shifted, start_frame=start_frame)
        except ValueError as exc:
            if "acceptance" not in str(exc) and "wrap" not in str(exc):
                raise
            shift += 256
            continue
        return shifted, rows, shift
    raise ValueError("could not plan the phrase within 200 shifts")


def _lay_out(items, *, start_frame=14, reset_pad=3):
    """Split items into reset-delimited segments and plan each. Returns
    (segments, reset_frames) where each segment is a list of planned rows."""
    segs, cur = [], []
    for it in items:
        if it[0] == "reset":
            cur.append(("reset",))
            segs.append(cur)
            cur = []
        else:
            cur.append(it)
    if cur or not segs:
        segs.append(cur)
    planned, bodies, reset_frames, t_frame = [], [], [], start_frame
    for seg in segs:
        has_reset = bool(seg) and seg[-1][0] == "reset"
        body = seg[:-1] if has_reset else seg
        body, rows, _shift = _shift_until_plannable(body, start_frame=t_frame)
        bodies.append(body)
        planned.append(rows)
        if has_reset:
            end_cyc = max((r.end_cycle for r in rows), default=t_frame * uh.CYC_PER_FRAME)
            # trailing waits are part of the schedule: the reset must land
            # AFTER them, or it cuts the final ACKs off the wire mid-byte
            for it in reversed(body):
                if it[0] == "wait":
                    end_cyc += int(it[1]) * uh.CYC_PER_FRAME
                else:
                    break
            r_frame = -(-int(end_cyc) // uh.CYC_PER_FRAME) + 1
            reset_frames.append(r_frame)
            t_frame = r_frame + reset_pad
        else:
            t_frame = max((r.send_frame for r in rows), default=t_frame) + 1
    return bodies, planned, reset_frames


def _corrupt_packet_bytes(packet: bytes, drop_index: int | None = None,
                          flip_index: int | None = None) -> bytes:
    b = bytearray(packet)
    if drop_index is not None:
        del b[drop_index]
    if flip_index is not None:
        b[flip_index] ^= 1
    return bytes(b)


def sc_overflow():
    """70 events, 64-deep queue: the last 6 must be dropped AND reported."""
    items = boot_items()
    f = 400
    for i in range(70):
        items.append(("event", f, 0, 0, 0x40 + (i % 3), 0x010101 * (i + 1)))
        f += 1
    items.append(("status",))
    items.append(("wait", 400))
    return items, {"tail": 200, "drop_from": 64}


def sc_noff_full():
    """The note-off is uploaded right behind the 64th event: the event queue is
    full when it is accepted, and the live path must carry it anyway."""
    items = boot_items()
    f = 3000
    for i in range(64):
        items.append(("event", f, 0, 0, 0x40 + (i % 3), 0x020202 * (i + 1)))
        f += 1
    items += key_events_items([(0, "on", 45), (1, "off", 45)])
    items.append(("wait", 400))
    return items, {"tail": 200}


def sc_reset_mid():
    """BTN0 reset mid-phrase: roughly half the events are still queued and due
    shortly after the reset point. A clean device executes none of them; the
    fresh load that follows must work."""
    items = boot_items()
    items += key_events_items([(0, "on", 45)])
    f = 400
    for i in range(30):
        items.append(("event", f, 0, 0, 0x40 + (i % 3), 0x030303 * (i + 1)))
        f += 40
    items.append(("wait", 30))                # reset lands mid-phrase,
                                              # clear of the final ACKs
    items.append(("reset",))
    items += boot_items()
    items += key_events_items([(0, "on", 45), (200, "off", 45)])
    items.append(("wait", 900))               # room for any stale event to show
    return items, {"tail": 100}


def sc_drop_byte():
    """A byte removed from one packet: the checksum must reject it, the write
    must never execute, exactly one error is reported, and the parser must be
    aligned again on the next packet's opcode. The 0xFF probe becomes the
    truncated packet's checksum byte, so recovery is deterministic; a probe of
    0x00 could re-align a modulo-256 sum and is deliberately not used."""
    items = boot_items()
    victim_index = 6                          # a boot write, sent corrupted
    packet = uh.pkt_write(*items[victim_index][1:])
    drop_index = next(i for i in range(1, 7) if packet[i] not in (0x00, 0xFF))
    corrupt = _corrupt_packet_bytes(packet, drop_index=drop_index)
    items = items[:victim_index] + [("raw", corrupt), ("raw", b"\xFF")] + items[victim_index:]
    items += key_events_items([(0, "on", 45), (400, "off", 45)])
    items.append(("wait", 300))
    return items, {"tail": 100}


def sc_corrupt_byte():
    items = boot_items()
    f = 2400
    for i in range(12):
        items.append(("event", f, 0, 0, 0x40 + (i % 3), 0x040404 * (i + 1)))
        f += 60
    items += key_events_items([(0, "on", 45), (1200, "off", 45)])
    items.append(("wait", 300))
    return items, {"tail": 100, "corrupt_event": 4}


SCENARIOS = {"held": sc_held, "phrase": sc_phrase, "overflow": sc_overflow,
             "noff-full": sc_noff_full, "reset-mid": sc_reset_mid,
             "drop-byte": sc_drop_byte, "corrupt-byte": sc_corrupt_byte}


# ---- expected execution, from the contract ---------------------------------
def expected_execution(items, rows):
    """(apply_frame_abs, flag, sec, addr, data, kind, plan_index), in the order
    the contract executes them: frame order, due-scheduled before live within a
    frame, each FIFO. Raw packets never execute."""
    out = []
    ri = 0                                  # the plan-row index (waits make no row)
    for it in items:
        if it[0] == "write":
            out.append((rows[ri].apply_frame, it[1], it[2], it[3], it[4] & 0xFFFFFFFF,
                        "write", ri))
        elif it[0] == "event":
            out.append((it[1], it[2], it[3], it[4], it[5] & 0xFFFFFFFF, "event", ri))
        if it[0] not in ("wait", "reset"):
            ri += 1
    out.sort(key=lambda e: (e[0], 0 if e[5] == "event" else 1, e[6]))
    return out


# ---- the run ---------------------------------------------------------------
def build_cmd_file(planned_segments, reset_frames, path):
    """Segments and their resets interleaved IN EXECUTION ORDER: the bench
    processes the file top to bottom, so an R line must sit between the
    segments it separates."""
    with open(path, "w") as fh:
        for i, rows in enumerate(planned_segments):
            for row in rows:
                fh.write(f"S {row.send_frame} {row.packet.hex(' ')}\n")
            if i < len(reset_frames):
                fh.write(f"R {reset_frames[i]}\n")


def rows_from_capture(prefix):
    """Rebuild (items, planned_rows, origin, baud) from a CLI capture written
    by uart_host.write_capture: <prefix>.cmds (bench S-lines) and
    <prefix>.plan.json (the planner's own rows, rebased). The bytes replayed
    are the bytes the CLI emitted -- the expectation is re-derived from the
    same packets, so the bench checks the CLI's schedule, not a lookalike."""
    import struct
    plan = json.loads(Path(prefix + ".plan.json").read_text())
    items, rows = [], []
    for r in plan["rows"]:
        pkt = bytes.fromhex(r["packet"])
        accept = r["accept_frame"]
        # the EXPECTATION comes from the captured intent, the STIMULUS from
        # the emitted bytes -- so a wrong-value mutation of the bytes is
        # visible as a mismatch instead of cancelling itself out
        exp = r.get("expect") or {}
        if r["kind"] == "write":
            items.append(("write", exp["flag"], exp["sec"], exp["addr"],
                          exp["data"]))
            rows.append(uh.Placed(r["index"], "write", pkt, r["send_frame"],
                                  (r["send_frame"] + len(pkt)) * uh.CYC_PER_FRAME,
                                  accept, -1, r["apply_frame"]))
        elif r["kind"] == "event":
            due = r["due"] if r["due"] >= 0 else (pkt[1] | (pkt[2] << 8))
            items.append(("event", due, exp["flag"], exp["sec"], exp["addr"],
                          exp["data"]))
            rows.append(uh.Placed(r["index"], "event", pkt, r["send_frame"],
                                  (r["send_frame"] + len(pkt)) * uh.CYC_PER_FRAME,
                                  accept, due, due))
        elif r["kind"] == "status":
            # a STATUS poll the host really sent: it occupies the RX wire and
            # draws a reply, and executes nothing
            items.append(("status",))
            rows.append(uh.Placed(r["index"], "status", pkt, r["send_frame"],
                                  (r["send_frame"] + len(pkt)) * uh.CYC_PER_FRAME,
                                  accept, -1, -1))
        else:
            raise ValueError(f"capture row kind {r['kind']!r} not replayable")
    return items, [rows], plan.get("origin", 0), plan.get("baud", uh.DEFAULT_BAUD)


def _sha(path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _rel(p) -> str:
    p = Path(p).resolve()
    return str(p.relative_to(ROOT)) if p.is_relative_to(ROOT) else str(p)


def replay_identity(srcs, defines, cmd_path, tail_frames) -> dict:
    """Everything a replay's outputs depend on: the compiled sources, the
    ROM images the RTL $readmemh's (they shape the sound as much as the
    Verilog does), the defines (so an injection is part of the identity),
    the stimulus and the frames run. A reused run must match ALL of it."""
    return {"sources": {_rel(p): _sha(p) for p in srcs},
            "roms": {_rel(p): _sha(p) for p in roms()},
            "defines": list(defines), "stimulus": _sha(cmd_path),
            "tail_frames": int(tail_frames)}


def simulate_replay(prefix, outdir, inject=None, tail_frames=None,
                    timeout_s=3600, reuse=False):
    """Run the wrapper bench on a CLI capture (see rows_from_capture).
    `tail_frames` extends the run (and the model comparison) past the last
    due, so decay and release tails are on the wire, not cut off.

    `reuse=True` re-ANALYSES an earlier run instead of re-simulating (a
    musical-length replay is ~50 minutes) -- only when the stimulus file
    the bench consumed is byte-identical to this capture's and the run
    reached the frames this analysis needs; otherwise it REFUSES (None).
    Expectations may change between the two; the stimulus may not."""
    outdir = Path(outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    items, planned_segments, _origin, _baud = rows_from_capture(prefix)
    cmd_path = outdir / "uart_cmds.txt"
    previous = cmd_path.read_bytes() if (reuse and cmd_path.exists()) else None
    build_cmd_file(planned_segments, [], cmd_path)
    if reuse and previous != cmd_path.read_bytes():
        print("verify_uart_bridge: REFUSED -- reuse asked, but the stimulus "
              "differs from the run on disk (or there is none)")
        return None
    last_due = max([r.due for rows in planned_segments for r in rows if r.due >= 0]
                   + [0])
    last_send = max([r.send_frame for rows in planned_segments for r in rows]
                    + [0])
    model_tail = max(600, int(tail_frames or 0))
    tail_frames = max(800, last_due - last_send + 200 + model_tail)

    resolved = [(n, p) for n, p in top.resolve_sources(None) if n != "tb_top_bx.v"]
    srcs = [str(BENCH)] + [p for _, p in resolved if p != str(BRIDGE)] \
        + [str(BRIDGE), str(WRAPPER)]
    defines = ["VOICE_OSC_2X", "VOICE_FILTER_2X", "UART_HIER"]
    if inject:
        defines.append(f"INJECT_BUG_{inject}")
    identity = replay_identity(srcs, defines, cmd_path, tail_frames)
    id_path = outdir / "run_identity.json"
    files = {k: str(outdir / f"uart_{k}.txt") for k in ("i2s", "wrs", "txd", "samp")}
    if reuse:
        # the run on disk is evidence only if it is the run THIS call would
        # make: same sources, defines (injection included), stimulus and
        # length -- and its outputs are the ones that run wrote
        rec = json.loads(id_path.read_text()) if id_path.exists() else None
        why = None
        if rec is None:
            why = "no run identity on disk (not a fresh run of this tool)"
        elif rec.get("identity") != identity:
            diff = [k for k in identity if rec["identity"].get(k) != identity[k]]
            why = f"the run on disk differs in {diff}"
        else:
            bad = [k for k, f in files.items()
                   if not Path(f).exists() or _sha(f) != rec["outputs"].get(k)]
            if bad or not (outdir / "transcript.txt").exists() or \
                    _sha(outdir / "transcript.txt") != rec["outputs"].get("transcript"):
                why = f"outputs changed since that run wrote them: {bad or ['transcript']}"
        if why:
            print(f"verify_uart_bridge: REFUSED -- reuse asked, but {why}")
            return None
        report = (outdir / "transcript.txt").read_text().splitlines()
        return {"outdir": outdir, "items": items, "bodies": [items],
                "planned": planned_segments, "reset_frames": [],
                "report": report, "files": files,
                "scenario": "replay", "inject": inject,
                "defines": defines, "model_tail": model_tail, "reused": True}
    iverilog, vvp = top.tool("iverilog"), top.tool("vvp")
    if not iverilog or not vvp:
        print("verify_uart_bridge: REFUSED -- iverilog/vvp not on PATH")
        return None
    exe = outdir / "tb_uart_bx.vvp"
    compile_cmd = [iverilog, "-g2012", "-o", str(exe)] + [f"-D{d}" for d in defines] + srcs
    r = subprocess.run(compile_cmd, capture_output=True, text=True)
    if r.returncode != 0:
        print("verify_uart_bridge: compile failed:\n" + r.stdout + r.stderr)
        return None
    id_path.unlink(missing_ok=True)       # a failed run leaves no identity
    run_cmd = [vvp, "-n", str(exe), f"+uart={cmd_path}",
               f"+i2s={files['i2s']}", f"+wrs={files['wrs']}",
               f"+txd={files['txd']}", f"+samp={files['samp']}",
               f"+frames={tail_frames}"]
    try:
        r = subprocess.run(run_cmd, capture_output=True, text=True, timeout=timeout_s,
                           cwd=str(ROOT / "rtl-sketch"))
    except subprocess.TimeoutExpired:
        print(f"verify_uart_bridge: simulation timed out after {timeout_s}s")
        return None
    report = [l for l in r.stdout.splitlines() if l.startswith("tb_uart_bx")]
    (outdir / "transcript.txt").write_text("\n".join(report) + "\n")
    if r.returncode != 0:
        print("verify_uart_bridge: vvp failed:\n" + r.stdout + r.stderr)
        return None
    outputs = {k: _sha(f) for k, f in files.items() if Path(f).exists()}
    outputs["transcript"] = _sha(outdir / "transcript.txt")
    id_path.write_text(json.dumps({"identity": identity, "outputs": outputs},
                                  indent=1) + "\n")
    return {"outdir": outdir, "items": items, "bodies": [items],
            "planned": planned_segments, "reset_frames": [],
            "report": report, "files": files,
            "scenario": "replay", "inject": inject,
            "defines": defines, "model_tail": model_tail}


def simulate(scenario, inject, outdir, *, rtl_wrapper=WRAPPER, uart_hier=True):
    outdir = Path(outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    items, _opts = SCENARIOS[scenario]()
    if scenario == "corrupt-byte":
        # flip one payload bit of one scheduled event; checksum must catch it
        events = [i for i, it in enumerate(items) if it[0] == "event"]
        victim = events[_opts["corrupt_event"]]
        due, flag, sec, addr, data = items[victim][1:]
        good = uh.pkt_event(due, flag, sec, addr, data)
        bad = _corrupt_packet_bytes(good, flip_index=5)
        items = items[:victim] + [("raw", bad)] + items[victim + 1:]
    bodies, planned_segments, reset_frames = _lay_out(items)

    cmd_path = outdir / "uart_cmds.txt"
    build_cmd_file(planned_segments, reset_frames, cmd_path)
    # the tail must outlast the last DUE, not the last SEND: a phrase keeps
    # firing events long after the wire has gone quiet
    last_due = max([r.due for rows in planned_segments for r in rows if r.due >= 0]
                   + [0])
    last_send = max([r.send_frame for rows in planned_segments for r in rows]
                    + [0])
    tail_frames = max(800, last_due - last_send + 800)

    resolved = [(n, p) for n, p in top.resolve_sources(None) if n != "tb_top_bx.v"]
    # uart_bridge.v arrives via resolve_sources now; the explicit entry below
    # would duplicate the compile unit
    srcs = [str(BENCH)] + [p for _, p in resolved if p != str(BRIDGE)] \
        + [str(BRIDGE), str(rtl_wrapper)]
    defines = ["VOICE_OSC_2X", "VOICE_FILTER_2X"]
    if inject:
        defines.append(f"INJECT_BUG_{inject}")
    if uart_hier:
        defines.append("UART_HIER")
    iverilog, vvp = top.tool("iverilog"), top.tool("vvp")
    if not iverilog or not vvp:
        print("verify_uart_bridge: REFUSED -- iverilog/vvp not on PATH")
        return None
    exe = outdir / "tb_uart_bx.vvp"
    compile_cmd = [iverilog, "-g2012", "-o", str(exe)] + [f"-D{d}" for d in defines] + srcs
    r = subprocess.run(compile_cmd, capture_output=True, text=True)
    if r.returncode != 0:
        print("verify_uart_bridge: compile failed:\n" + r.stdout + r.stderr)
        return None
    files = {k: str(outdir / f"uart_{k}.txt") for k in ("i2s", "wrs", "txd", "samp")}
    run_cmd = [vvp, "-n", str(exe), f"+uart={cmd_path}",
               f"+i2s={files['i2s']}", f"+wrs={files['wrs']}",
               f"+txd={files['txd']}", f"+samp={files['samp']}",
               f"+frames={tail_frames}"]
    try:
        # cwd = rtl-sketch: the core's $readmemh ROM paths resolve from there,
        # exactly as verify_synth_top.simulate runs it. From anywhere else the
        # ROMs silently load as X and every downstream sample is X.
        r = subprocess.run(run_cmd, capture_output=True, text=True, timeout=3600,
                           cwd=str(ROOT / "rtl-sketch"))
    except subprocess.TimeoutExpired:
        print("verify_uart_bridge: simulation timed out")
        return None
    report = [l for l in r.stdout.splitlines() if l.startswith("tb_uart_bx")]
    (outdir / "transcript.txt").write_text("\n".join(report) + "\n")
    if r.returncode != 0:
        print("verify_uart_bridge: vvp failed:\n" + r.stdout + r.stderr)
        return None
    return {"outdir": outdir, "items": items, "bodies": bodies,
            "planned": planned_segments, "reset_frames": reset_frames,
            "report": report, "files": files, "scenario": scenario,
            "inject": inject, "defines": defines}


# ---- analysis --------------------------------------------------------------
def _rows(path):
    return [ln.split() for ln in Path(path).read_text().splitlines() if ln.strip()]


def analyze(run):
    """Check a run against the contract. Returns (ok, comparison, detail)."""
    planned, reset_frames = run["planned"], run["reset_frames"]
    items = [it for body in run["bodies"] for it in body]       # shifted dues included
    rows_flat = [r for rows in planned for r in rows]
    report = "\n".join(run["report"])
    comp = {"scenario": run["scenario"], "inject": run["inject"]}
    detail = []

    segs = [(int(m.group(1)), int(m.group(2)), int(m.group(3)), int(m.group(4)))
            for m in RE_SEG.finditer(report)]
    if len(segs) != len(planned):
        comp["segment_mismatch"] = (len(segs), len(planned))
        detail.append(f"bench reported {len(segs)} segments, planned {len(planned)}")
        return False, comp, detail
    origins = [s[1] >> 8 for s in segs]                  # absolute frames per segment

    wr = _rows(run["files"]["wrs"])
    tx = _rows(run["files"]["txd"])
    i2s = _rows(run["files"]["i2s"])
    samp = _rows(run["files"]["samp"])

    # -- write integrity and timing, against the contract's prediction --------
    expected = expected_execution(items, rows_flat)
    if reset_frames:
        # events still queued at the reset die with the core (the contract);
        # they were ACKed at acceptance but never execute. Ownership is by
        # plan index: an event belongs to the segment that sent it.
        seg_bounds = []
        cum = 0
        for rows in planned:
            cum += len(rows)
            seg_bounds.append(cum)
        killed = []
        for e in expected:
            if e[5] != "event":
                continue
            for s, b in enumerate(seg_bounds):
                if e[6] < b:
                    if s < len(reset_frames) and e[0] >= reset_frames[s]:
                        killed.append(e)
                    break
        expected = [e for e in expected if e not in killed]
    else:
        killed = []
    if run["scenario"] == "overflow":
        # the arriving packet is the one dropped: the last six events sent
        ev = sorted((e for e in expected if e[5] == "event"), key=lambda e: e[6])
        drop_idx = {e[6] for e in ev[-6:]}
        expected = [e for e in expected if e[6] not in drop_idx]
    seen = []
    x_bad = 0
    for row in wr:
        try:
            seen.append((int(row[0]), int(row[1]), int(row[2]), int(row[3]),
                         int(row[4]) & 0xFFFFFFFF, int(row[5])))
        except ValueError:
            x_bad += 1
    comp["writes_sent"] = len(expected)
    comp["writes_seen"] = len(seen)
    comp["writes_bad"] = 0
    comp["frame_pred_bad"] = 0
    comp["frame_no_pred"] = 0
    # timing: scheduled events must land in EXACTLY their due frame (the
    # deadline that justifies the device-side queue). Live writes are allowed
    # the documented +-1: their apply frame depends on where the acceptance
    # instant falls relative to the device's window phase, which a reset moves.
    for i, (want, got) in enumerate(zip(expected, seen)):
        if (want[1], want[2], want[3], want[4]) != (got[1], got[2], got[3], got[4]):
            comp["writes_bad"] += 1
            if comp["writes_bad"] <= 3:
                detail.append(f"write {i}: intended f/sec/a/d "
                              f"{want[1]}/{want[2]}/{want[3]:#x}/{want[4]:#x}, "
                              f"got {got[1]}/{got[2]}/{got[3]:#x}/{got[4]:#x}")
        tol = 0 if want[5] == "event" else 1
        if abs(want[0] - got[0]) > tol:
            comp["frame_pred_bad"] += 1
            if comp["frame_pred_bad"] <= 5:
                detail.append(f"write {i} (a={want[3]:#x}): contract frame "
                              f"{want[0]}, landed {got[0]}")
        elif want[0] != got[0]:
            comp.setdefault("live_phase_jitter", 0)
            comp["live_phase_jitter"] += 1
    if len(seen) != len(expected):
        comp["frame_no_pred"] = abs(len(seen) - len(expected))
        detail.append(f"{len(seen)} writes drained, {len(expected)} expected")
    comp["x_bad"] = x_bad

    # -- the device's TX side -------------------------------------------------
    def seg_bytes(seg):
        lo = origins[seg]
        hi = origins[seg + 1] if seg + 1 < len(origins) else 1 << 30
        return bytes(int(b[1]) for b in tx if lo <= int(b[0]) < hi)

    acks = errs = boots = 0
    err_codes = Counter()
    status_bad = 0
    status_rows = [r for rows in planned for r in rows if r.kind == "status"]
    status_k = 0
    for s in range(len(origins)):
        for pkt in uh.parse_device_stream(seg_bytes(s)):
            if pkt.kind == "ack":
                acks += 1
            elif pkt.kind == "err":
                errs += 1
                err_codes[pkt.code] += 1
            elif pkt.kind == "boot":
                boots += 1
            elif pkt.kind == "status":
                # the device frame must match the contract's clock, ±0
                # replies pair with the polls IN ORDER (a replayed host
                # capture polls many times); a lone poll pairs with itself
                acc = status_rows[min(status_k, len(status_rows) - 1)] \
                    if status_rows else None
                status_k += 1
                if acc is not None:
                    # the STATUS reply carries the device's frame REGISTER,
                    # which reads audio_frame+1 during the frame; 16 bits
                    want = ((acc.accept_frame - origins[s]) + 1)
                    d = (pkt.frame - want) & 0xFFFF
                    if min(d, 0x10000 - d) > 1:
                        status_bad += 1
                        detail.append(f"STATUS frame {pkt.frame}, contract {want}")
    comp["acks_seen"] = acks
    comp["errs_seen"] = errs
    comp["err_codes"] = dict(err_codes)
    comp["boots_seen"] = boots
    comp["ack_missing"] = max(0, len(expected) + len(killed) - acks)
    comp["status_bad"] = status_bad

    # scenario-specific expectations ----------------------------------------
    ok_specific = True
    sc = run["scenario"]
    if sc == "overflow":
        want_drops = 6
        if err_codes[uh.ERR_EVQ_FULL] != want_drops:
            ok_specific = False
            detail.append(f"expected {want_drops} ERR(event-queue overflow), "
                          f"got {err_codes[uh.ERR_EVQ_FULL]}")
    elif sc in ("drop-byte", "corrupt-byte"):
        if err_codes[uh.ERR_CHECKSUM] != 1 or errs != 1:
            ok_specific = False
            detail.append(f"expected exactly one ERR(bad checksum), got {dict(err_codes)}")
    elif sc == "reset-mid":
        if boots < len(reset_frames):
            ok_specific = False
            detail.append(f"expected {len(reset_frames)} BOOT on the wire, got {boots}")
        # nothing may execute between the reset and the fresh segment's first write
        window_lo = reset_frames[0]
        window_hi = min((e[0] for e in expected if e[0] >= origins[-1]),
                        default=None)
        if window_hi is not None:
            # one frame of slack: the fresh segment's first live write may
            # land a frame early against the plan's bench-phase arithmetic
            stray = sum(1 for g in seen if window_lo <= g[0] < window_hi - 1)
            if stray:
                ok_specific = False
                comp["stray_after_reset"] = stray
                detail.append(f"{stray} write(s) executed between the reset "
                              f"(frame {window_lo}) and the fresh load ({window_hi})")
    elif sc == "noff-full":
        gate_offs = [e for e in expected if e[4] is not None and e[3] == 0x21]
        if not gate_offs:
            ok_specific = False
            detail.append("scenario bug: no gate-off in the expected list")
    if errs and sc not in ("overflow", "drop-byte", "corrupt-byte"):
        ok_specific = False
        detail.append(f"unexpected device errors: {dict(err_codes)}")

    # -- model comparison, segment by segment --------------------------------
    import synth_top_model as stm
    import numpy as np
    comp["wire_mismatch"] = comp["swap"] = comp["width"] = 0
    comp["core_bad"] = 0
    total_periods = 0
    for s in range(len(origins)):
        seg_rows = planned[s]
        exp = [e for e in expected if e[0] >= origins[s]
               and (s + 1 >= len(origins) or e[0] < origins[s + 1])]
        if not exp:
            continue
        origin = origins[s]
        model_writes = [(e[0] - origin, e[1], e[2], e[3], e[4]) for e in exp]
        n = max(f for f, *_ in model_writes) + run.get("model_tail", 600)
        if s + 1 < len(origins):
            # a segment ends where the next one's audio begins (the reset)
            n = min(n, origins[s + 1] - origin)
        m = stm.SynthTopModel(oversample_2x=True, filter_2x=True, pulse_2x=False
                              ).run(model_writes, n)
        exp_i2s, exp_s = m["i2s"], m["sample"]
        p0 = segs[s][2]                                   # periods at segment origin
        periods = [r for r in i2s if p0 <= int(r[0]) < p0 + n]
        for r in periods:
            try:
                p, left, right, nbl, nbr = (int(r[0]), int(r[1]), int(r[2]),
                                            int(r[3]), int(r[4]))
            except ValueError:
                # an X on the wire is a defect (or an X core upstream of it),
                # never a pass: count it as both a mismatch and a width fault
                comp["wire_mismatch"] += 1
                comp["width"] += 1
                if comp["wire_mismatch"] <= 3:
                    detail.append(f"seg {s}: X on the I2S wire at row {r}")
                continue
            mi = p - p0
            if mi >= len(exp_i2s):
                break
            e = int(exp_i2s[mi])
            if nbl != 32 or nbr != 32:
                comp["width"] += 1
            if left != e:
                comp["wire_mismatch"] += 1
                if comp["wire_mismatch"] <= 3:
                    detail.append(f"seg {s} period {mi}: model {e}, wire {left}, "
                                  f"error {left - e:+d} LSB")
            if right != left:
                comp["swap"] += 1
        total_periods += len(periods)
        # the core's own stream, as a diagnostic; X counts as bad, never as pass
        for r in samp:
            try:
                fr, sval = int(r[0]), int(r[1])
            except ValueError:
                comp["core_bad"] += 1
                continue
            if origin <= fr < origin + n:
                if sval != int(exp_s[fr - origin]):
                    comp["core_bad"] += 1
    comp["periods"] = total_periods

    # -- the frame budget ----------------------------------------------------
    mw, ms = RE_WRT.search(report), RE_STRB.search(report)
    mb, mt = RE_BUSY.search(report), RE_TXB.search(report)
    if not (mw and ms and mb and mt):
        comp["report_missing"] = True
        detail.append("the bench did not report its frame budget")
        return False, comp, detail
    comp["collisions"] = int(mw.group(2))
    comp["busy_at_tick"] = int(mb.group(1))
    comp["overrun"] = int(mb.group(2))
    comp["overflow"] = int(mb.group(3))
    comp["frames_no_sample"] = int(ms.group(2))
    comp["worst_strobe_cycle"] = int(ms.group(3))

    # envelope floor: after the release the model must actually reach it
    if sc == "held":
        import synth_top_model as stm2
        exp_all = expected
        origin = origins[0]
        model_writes = [(e[0] - origin, e[1], e[2], e[3], e[4]) for e in exp_all]
        n = max(f for f, *_ in model_writes) + 500
        m = stm2.SynthTopModel(oversample_2x=True, filter_2x=True,
                               pulse_2x=False).run(model_writes, n)
        tail_peak = int(np.abs(m["sample"][-400:]).max())
        comp["release_tail_peak"] = tail_peak
        if tail_peak > 1024:
            ok_specific = False
            detail.append(f"release tail peak {tail_peak} LSB: the envelope "
                          f"did not decay to its floor; the floor claim is unmet")

    clean = (comp["writes_bad"] == 0 and comp["frame_pred_bad"] == 0
             and comp["frame_no_pred"] == 0 and comp["x_bad"] == 0
             and comp["wire_mismatch"] == 0 and comp["swap"] == 0
             and comp["width"] == 0 and comp["core_bad"] == 0
             and comp["collisions"] == 0 and comp["busy_at_tick"] == 0
             and comp["overrun"] == 0 and comp["overflow"] == 0
             and comp["frames_no_sample"] == 0
             and 0 < comp["worst_strobe_cycle"] < 256
             and comp["ack_missing"] == 0 and comp["status_bad"] == 0
             and ok_specific)
    if not clean:
        for d in detail:
            print("    " + d)
    return clean, comp, detail


def record_for(run, comp, ok):
    """verification.json content. The clean phrase run carries the same keys
    fpga/build_arty.py's gate reads."""
    resolved = [(n, p) for n, p in top.resolve_sources(None) if n != "tb_top_bx.v"]
    hashed = {}
    for name, path in resolved + [("tb_uart_bx.v", str(BENCH)),
                                  ("uart_bridge.v", str(BRIDGE)),
                                  ("arty_a7_top.v", str(WRAPPER))]:
        p = Path(path)
        hashed[str(p.relative_to(ROOT)) if p.is_relative_to(ROOT) else str(p)] = \
            hashlib.sha256(p.read_bytes()).hexdigest()
    for path in roms():
        hashed[str(path.relative_to(ROOT))] = hashlib.sha256(path.read_bytes()).hexdigest()
    transcript = (Path(run["outdir"]) / "transcript.txt").read_bytes()
    comparison = {k: v for k, v in comp.items()
                  if k in ("wire_mismatch", "swap", "width", "core_bad",
                           "writes_bad", "frame_pred_bad", "frame_no_pred",
                           "busy_at_tick", "overrun", "overflow",
                           "frames_no_sample", "periods", "writes_seen",
                           "writes_sent", "worst_strobe_cycle")}
    return {"state": "PASS" if ok else "FAIL", "exit_code": 0 if ok else 1,
            "inject": run["inject"], "scenario": run["scenario"],
            "configuration": CONFIG,
            "scope": ("Arty wrapper UART-bridge digital check: device-scheduled "
                      "events and live writes through the reserved UART pins vs "
                      "the device contract and the integer model; MMCM bypassed; "
                      "no physical timing claim"),
            "comparison": comp, "source_sha256": hashed,
            "transcript_sha256": hashlib.sha256(transcript).hexdigest()}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--outdir", type=Path, default=ROOT / "build/uart-controls")
    ap.add_argument("--scenario", choices=tuple(SCENARIOS) + ("all",), default="all")
    ap.add_argument("--inject", choices=INJECTS, default=None)
    ap.add_argument("--start-red", action="store_true",
                    help="run the held scenario against the ported stub; must FAIL")
    ap.add_argument("--replay", type=Path, default=None, metavar="PREFIX",
                    help="replay a CLI capture (<PREFIX>.cmds + <PREFIX>.plan.json "
                         "from uart_host --capture) through the wrapper sim")
    ap.add_argument("--replay-name", default=None,
                    help="scenario label for the replay record (default: replay)")
    ap.add_argument("--rtl", type=Path, default=None,
                    help="wrapper file to compile (default: fpga/rtl/arty_a7_top.v)")
    ap.add_argument("--jobs", type=int, default=3)
    a = ap.parse_args(argv)
    outdir = a.outdir.resolve()
    outdir.mkdir(parents=True, exist_ok=True)

    if a.start_red:
        wrapper = a.rtl or STUB
        print(f"verify_uart_bridge: START RED against {wrapper}")
        run = simulate("held", None, outdir / "start-red", rtl_wrapper=wrapper,
                       uart_hier=False)
        if run is None:
            return 2
        ok, comp, detail = analyze(run)
        rec = record_for(run, comp, ok)
        (outdir / "start-red" / "verification.json").write_text(
            json.dumps(rec, indent=2) + "\n")
        print(f"verify_uart_bridge: start-red run state {rec['state']} "
              f"(writes_seen {comp.get('writes_seen')} of {comp.get('writes_sent')}, "
              f"wire_mismatch {comp.get('wire_mismatch')}, periods {comp.get('periods')})")
        if ok:
            print("verify_uart_bridge: FAIL -- the stub PASSED; the bench cannot "
                  "be trusted and must not be used")
            return 1
        print("verify_uart_bridge: start red confirmed -- the bench fails against "
              "a behaviour-free stub, as it must")
        return 0

    if a.replay:
        name = a.replay_name or "replay"
        run = simulate_replay(str(a.replay), outdir / name)
        if run is None:
            return 2
        ok, comp, detail = analyze(run)
        rec = record_for(run, comp, ok)
        rec["capture"] = str(a.replay)
        (outdir / name / "verification.json").write_text(
            json.dumps(rec, indent=2) + "\n")
        print(f"verify_uart_bridge[{name}]: {rec['state']} -- "
              f"writes {comp.get('writes_seen')}/{comp.get('writes_sent')}, "
              f"timing bad {comp.get('frame_pred_bad')}, corrupt {comp.get('writes_bad')}, "
              f"I2S mismatch {comp.get('wire_mismatch')} over {comp.get('periods')} periods, "
              f"errs {comp.get('errs_seen')} {comp.get('err_codes')}")
        for d in detail[:6]:
            print(f"verify_uart_bridge[{name}]:   {d}")
        return 0 if rec["state"] == "PASS" else 1

    work = []
    if a.scenario == "all":
        scenarios = list(SCENARIOS)
    else:
        scenarios = [a.scenario]
    if a.inject:
        scenarios = [INJECT_SCENARIO[a.inject]]
    for sc in scenarios:
        name = f"{sc}-inject-{a.inject}" if a.inject else sc
        work.append((sc, a.inject, name))

    def do(job):
        sc, inject, name = job
        run = simulate(sc, inject, outdir / name)
        if run is None:
            return name, None, False, {}
        ok, comp, detail = analyze(run)
        rec = record_for(run, comp, ok)
        (outdir / name / "verification.json").write_text(json.dumps(rec, indent=2) + "\n")
        tag = "PASS" if ok else "FAIL"
        print(f"verify_uart_bridge[{name}]: {tag} -- "
              f"writes {comp.get('writes_seen')}/{comp.get('writes_sent')}, "
              f"timing bad {comp.get('frame_pred_bad')}, corrupt {comp.get('writes_bad')}, "
              f"I2S mismatch {comp.get('wire_mismatch')} over {comp.get('periods')} periods, "
              f"errs {comp.get('errs_seen')} {comp.get('err_codes')}", flush=True)
        for d in detail[:4]:
            print(f"verify_uart_bridge[{name}]:   {d}")
        return name, rec, ok, comp

    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=a.jobs) as pool:
        results = list(pool.map(do, work))

    if a.inject:
        rec = results[0][1]
        ok = results[0][2]
        comp = results[0][3]
        if rec is None:
            return 2
        if ok or rec["state"] == "PASS":
            print(f"verify_uart_bridge: NEGATIVE CONTROL NOT CAUGHT ({a.inject})")
            return 1
        # a control must fail for ITS recorded reason, not any failure
        reasons = {"UART_NO_CHECKSUM": lambda c: c.get("writes_bad", 0) > 0,
                   "UART_CORRUPT_ADDR": lambda c: c.get("writes_bad", 0) > 0,
                   "UART_EVQ_OVF_SILENT":
                       lambda c: c.get("err_codes", {}).get(uh.ERR_EVQ_FULL, 0) == 0
                       and (c.get("frame_pred_bad", 0) > 0
                            or c.get("frame_no_pred", 0) > 0),
                   "UART_NOFF_BLOCKED":
                       lambda c: c.get("frame_no_pred", 0) > 0
                       or c.get("frame_pred_bad", 0) > 0,
                   "UART_RESET_LEAK":
                       lambda c: c.get("stray_after_reset", 0) > 0
                       or c.get("frame_no_pred", 0) > 0}
        if not reasons[a.inject](comp):
            print(f"verify_uart_bridge: control {a.inject} failed for the WRONG "
                  f"reason: {comp.get('err_codes')}, seen {comp.get('writes_seen')}")
            return 1
        print(f"verify_uart_bridge: control {a.inject} CAUGHT for its recorded "
              f"reason (writes {comp.get('writes_seen')}/{comp.get('writes_sent')}, "
              f"corrupt {comp.get('writes_bad')}, timing bad {comp.get('frame_pred_bad')})")
        return 0

    good = all(r is not None and r["state"] == "PASS" for _, r, _, _ in results)
    summary = {name: (rec or {}).get("state", "REFUSED") for name, rec, _, _ in results}
    (outdir / "controls.json").write_text(json.dumps(
        {"state": "PASS" if good else "FAIL", "runs": summary}, indent=2) + "\n")
    print(json.dumps({"state": "PASS" if good else "FAIL", "runs": summary}))
    return 0 if good else 1


if __name__ == "__main__":
    raise SystemExit(main())
