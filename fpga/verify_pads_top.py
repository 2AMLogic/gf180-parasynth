#!/usr/bin/env python3
"""Verify the pads demo wrapper (#449) at its pins.

    python3 fpga/verify_pads_top.py                     # every scenario, clean
    python3 fpga/verify_pads_top.py --start-red         # against the stub: must FAIL
    python3 fpga/verify_pads_top.py --inject PADS_TRIG_STUCK   # a control: must be caught

The bench (rtl-sketch/tb_pads_top.v) is the player: it presses the buttons at
chosen cycles (with contact bounce when a scenario asks), throws the source
switch SW0 and the reset switch SW3, plays a host on the FTDI RX pin in host
mode, decodes the I2S wire the way a DAC does, logs every write at the core's
register port and captures the bridge's TX bytes.

THE EXPECTATION is never the design's own report:
  * which cycles are presses: fpga/pads_rom.press_cycles, from the PIN
    transitions this script wrote (the eager-debounce contract);
  * which writes a press makes and the frame each lands in: pads_rom.schedule
    + apply_frames -- model/drums_fx.hit_writes (the ROM is checked against it
    by pads_rom.check_rom before anything runs), due = t0 + offset + LAT, the
    bridge's two write slots per frame. Hit writes must land in EXACTLY their
    frame, with EXACTLY their values;
  * the boot image: the ROM's writes in order, each within +-1 frame of the
    wire-timing prediction (live writes carry the bridge's documented +-1);
  * the audio: model/synth_top_model.SynthTopModel driven by those writes
    (boot and host writes at the frames they landed, hit writes at the frames
    the CONTRACT names), compared with the I2S wire bit for bit;
  * the device's own TX: one BOOT per reset, and no ERR at all -- an ERR 3
    would say an event was accepted after its due, i.e. LAT was not enough.

Scenarios:
  phases   pads mode from power-up; a press during the boot load (ignored);
           every button at four latch phases (cycle 255, 0, 1, 128 of a
           frame: both sides of the frame boundary); a press held 1500 frames
           with contact bounce on press and release (one hit, no retrigger);
           all four buttons in one cycle (the worst case LAT is sized for);
           two buttons in one frame, the higher one first (dues tie; the
           lower button goes first by contract).
  switch   host mode first: three host WRITE packets must land, a press must
           do nothing; then SW0 up: boot, and two presses play.
  reset    a press plays; another is pressed and SW3 flipped before its dues:
           nothing of it may land after the reset; the next segment boots
           again (the switch is still up) and plays.

Negative controls (each must turn this red for its recorded reason):
  PADS_DEBOUNCE_DOUBLE  no lockout: the release bounce of the held press
                        fires an extra hit (extra writes at the port). The
                        chord is placed so that extra hit cannot be masked
                        by a later busy drop (see sc_phases).
  PADS_TRIG_STUCK       the stop's bit is never dropped: the second press of a
                        button writes no 0->1 edge and is SILENT (wire mismatch).
  PADS_SRC_STUCK        the source select ignores SW0: host mode's writes never
                        reach the core (host writes missing).
  PADS_KIT_HASH         the boot ROM's kit differs from r1-kit.json by one
                        word: pads_rom.check_rom REFUSES before any simulation.

Exit: 0 clean (or a control caught for its reason), 1 otherwise, 2 refused.
Records land in <outdir>/<run>/verification.json; controls.json summarises.
"""
from __future__ import annotations

import argparse
import contextlib
import hashlib
import io
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for _p in ("fpga", "rtl-sketch", "model"):
    if str(ROOT / _p) not in sys.path:
        sys.path.insert(0, str(ROOT / _p))

import pads_rom as pr                               # noqa: E402
import uart_host as uh                              # noqa: E402
import synth_top_model as stm                       # noqa: E402

BENCH = ROOT / "rtl-sketch/tb_pads_top.v"
WRAPPER = ROOT / "fpga/rtl/arty_a7_pads_top.v"
SEQ = ROOT / "fpga/rtl/pads_seq.v"
ROM = pr.ROM_V
STUB = ROOT / "fpga/stubs/arty_a7_pads_stub.v"
CONFIG = {"OSC2X": 1, "FILTER2X": 1, "PULSE2X": 0}
CPF = pr.CYC_PER_FRAME
GUARD_CYCLES = pr.GUARD_BITS * pr.DIV

INJECTS = ("PADS_DEBOUNCE_DOUBLE", "PADS_TRIG_STUCK", "PADS_SRC_STUCK", "PADS_KIT_HASH")
INJECT_SCENARIO = {"PADS_DEBOUNCE_DOUBLE": "phases", "PADS_TRIG_STUCK": "phases",
                   "PADS_SRC_STUCK": "switch", "PADS_KIT_HASH": "phases"}

RE_SEG = re.compile(r"SEG (\d+) periods (\d+)")
RE_READY = re.compile(r"READY seg (\d+) level (\d+) g (\d+)")
RE_WRT = re.compile(r"writes drained (\d+), spi\+uart slot collisions (\d+)")
RE_STRB = re.compile(r"sample strobed in (\d+) frames, MISSING in (\d+), worst strobe cycle (\d+) of 256")
RE_BUSY = re.compile(r"busy at a tick: (\d+); core overrun (\d+)")


# ---- scripts: what the player does, and so what must happen -------------------
class Segment:
    def __init__(self, pads_at: int | None):
        self.cmds = []              # (g, order, line)
        self.btn = {v: [] for v in range(4)}   # pin transitions per button
        self.mask = 0
        self.mode = []              # (g, level) of SW0
        self.host = []              # (g, packet bytes, (flag, sec, addr, data))
        self.pads_at = pads_at      # the cycle SW0 is (effectively) set to pads
        self.end = None

    def _add(self, g, line):
        self.cmds.append((int(g), len(self.cmds), line))


class Script:
    """Commands per reset segment, with the pin transitions they imply."""

    def __init__(self):
        self.segs = [Segment(None)]
        self._pending = {}          # g -> {button: level} for the current segment

    @property
    def cur(self):
        return self.segs[-1]

    def pin(self, v, g, level):
        self._pending.setdefault(int(g), {})[v] = level
        self.cur.btn[v].append((int(g), level))

    def press(self, v, g, hold, bounce=False):
        """Pin up at g (optionally bouncing), down `hold` cycles later."""
        seq = (0, 4000, 9000, 16000, 25000) if bounce else (0,)
        for k, dt in enumerate(seq):
            self.pin(v, g + dt, 1 if k % 2 == 0 else 0)
        for k, dt in enumerate(seq):
            self.pin(v, g + hold + dt, 0 if k % 2 == 0 else 1)

    def switch(self, g, level):
        self._flush()
        self.cur._add(g, f"M {g} {level}")
        self.cur.mode.append((int(g), level))
        if level and self.cur.pads_at is None:
            self.cur.pads_at = int(g)

    def host_write(self, g, flag, sec, addr, data):
        self._flush()
        pkt = uh.pkt_write(flag, sec, addr, data)
        self.cur._add(g, f"S {g} " + " ".join(f"{b:02x}" for b in pkt))
        self.cur.host.append((int(g), pkt, (flag, sec, addr, data)))

    def reset(self, g):
        self._flush()
        self.cur._add(g, f"R {g}")
        self.cur.end = int(g)
        pads = self.cur.mode[-1][1] if self.cur.mode else 0
        # after a reset the switch is still where it was: pads mode re-enters as
        # if SW0 had moved 3 cycles before the anchor (pads_rom.cycle_frame docs)
        self.segs.append(Segment(-3 if pads else None))
        if pads:
            self.cur.mode.append((-3, 1))

    def end(self, g):
        self._flush()
        self.cur._add(g, f"E {g}")
        self.cur.end = int(g)

    def _flush(self):
        for g in sorted(self._pending):
            for v, lvl in self._pending[g].items():
                self.cur.mask = (self.cur.mask & ~(1 << v)) | (lvl << v)
            self.cur._add(g, f"B {g} {self.cur.mask:x}")
        self._pending = {}

    def lines(self):
        self._flush()
        out = []
        for seg in self.segs:
            cmds = sorted(seg.cmds)
            last = -1
            for g, _o, line in cmds:
                if g < last:
                    raise ValueError(f"script out of order at {line}")
                last = g
                out.append(line)
        return out


def boot_ready_cycle(pads_at: int) -> int:
    """The cycle the boot load finishes, from the sender's own wire timing:
    SW0 seen 3 cycles after the pin, the guard, then 152 WRITE packets of 8
    bytes, one idle cycle between packets."""
    n = len(pr.boot_image())
    return pads_at + 5 + GUARD_CYCLES + n * (pr.WRITE_BYTES * pr.BYTE_CYCLES + 1)


def boot_apply_frames(pads_at: int) -> list:
    """Predicted apply frame of each boot write (the live path: accept + 1,
    +-1 by the bridge's contract)."""
    out = []
    for k in range(len(pr.boot_image())):
        s = pads_at + 5 + GUARD_CYCLES + k * (pr.WRITE_BYTES * pr.BYTE_CYCLES + 1)
        push = s + (pr.WRITE_BYTES - 1) * pr.BYTE_CYCLES + 4 + 9 * pr.DIV + pr.DIV // 2
        out.append(push // CPF + 1)
    return out


def latch(frame: int, phase: int) -> int:
    """The pin cycle whose press latches in cycle `phase` of audio frame `frame`."""
    return frame * CPF + phase - pr.PRESS_LAT_CYC


def sc_phases():
    s = Script()
    s.switch(10, 1)
    ready = boot_ready_cycle(10) // CPF
    s.press(2, 2000 * CPF, 50 * CPF)                         # during the boot: ignored
    f0 = ready + 200
    for r, phase in enumerate((255, 0, 1, 128)):
        for v in range(4):
            f = f0 + (4 * r + v) * 300
            s.press(v, latch(f, phase), 150 * CPF)
    f1 = f0 + 16 * 300
    s.press(0, latch(f1, 40), 1500 * CPF, bounce=True)       # held, bouncing: one hit
    # The chord must start after any BD busy window a SPURIOUS release press
    # could open (release + bounce + last_off + LAT = ~1500 + 100 + 192 + 704).
    # At f1 + 1900 it did not: a double-firing debouncer's extra BD hit made the
    # chord's BD press a busy drop, the write count came out equal, and the
    # PADS_DEBOUNCE_DOUBLE control went red only through misplaced frames --
    # the wrong reason (measured, 2026-09-28; extra_writes 0, hit_frame_bad 16).
    f2 = f1 + 1500 + 1100
    for v in range(4):                                       # the chord: all four at once
        s.pin(v, latch(f2, 60), 1)
        s.pin(v, latch(f2, 60) + 100 * CPF, 0)
    f3 = f2 + 1000
    s.press(2, latch(f3, 10), 100 * CPF)                     # CH first in the frame,
    s.press(1, latch(f3, 200), 100 * CPF)                    # SD later: SD's writes go first
    s.end((f3 + pr.LAT + 1200) * CPF)
    return s


def sc_switch():
    s = Script()
    s.host_write(100 * CPF, 0, stm.SEC_VOICE, stm.A_DVOL, 0x1234)
    s.host_write(200 * CPF, 0, stm.SEC_VOICE, stm.A_BVOL, 0x2345)
    s.host_write(300 * CPF, 0, stm.SEC_DRUM, 0x10, 0x4000)   # BD accent: harmless
    s.press(1, latch(400, 7), 60 * CPF)                      # host mode: must do nothing
    s.switch(600 * CPF, 1)
    ready = boot_ready_cycle(600 * CPF) // CPF
    s.press(1, latch(ready + 200, 99), 60 * CPF)
    s.press(0, latch(ready + 500, 3), 60 * CPF)
    s.end((ready + 500 + pr.LAT + 192 + 800) * CPF)
    return s


def sc_reset():
    s = Script()
    s.switch(10, 1)
    ready = boot_ready_cycle(10) // CPF
    s.press(1, latch(ready + 200, 20), 60 * CPF)
    s.press(0, latch(ready + 1100, 20), 60 * CPF)
    s.reset((ready + 1200) * CPF)                            # before BD's first due
    ready1 = boot_ready_cycle(-3) // CPF
    s.press(2, latch(ready1 + 200, 50), 60 * CPF)
    s.end((ready1 + 200 + pr.LAT + 800) * CPF)
    return s


SCENARIOS = {"phases": sc_phases, "switch": sc_switch, "reset": sc_reset}


def expected_segment(seg: Segment) -> dict:
    """The contract's view of one segment: host writes, boot writes and their
    predicted frames, the presses that register in RUN, and the hit writes
    with their EXACT frames."""
    host = [w for _g, _p, w in seg.host
            if seg.pads_at is None or _g < seg.pads_at]
    boot, boot_frames, presses, ready = [], [], [], None
    if seg.pads_at is not None:
        boot = pr.boot_image()
        boot_frames = boot_apply_frames(seg.pads_at)
        ready = boot_ready_cycle(seg.pads_at)
        for v in range(4):
            for c in pr.press_cycles(seg.btn[v]):
                if c > ready + 2 and (seg.end is None or c < seg.end):
                    presses.append((c, v))
    presses.sort()
    sch = pr.schedule([(pr.cycle_frame(c), v) for c, v in presses])
    frames = pr.apply_frames([w[0] for w in sch["writes"]])
    hits = [(f, *w[1:5]) for f, w in zip(frames, sch["writes"])]
    if seg.end is not None:
        # a reset kills everything not yet executed (both queues and the sender)
        end_frame = pr.cycle_frame(seg.end)
        hits = [h for h in hits if h[0] < end_frame]
    return {"host": host, "boot": boot, "boot_frames": boot_frames, "ready": ready,
            "presses": presses, "accepted": sch["accepted"], "dropped": sch["dropped"],
            "hits": hits}


# ---- the run ----------------------------------------------------------------------
def _sha(path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def sources(rom=ROM, wrapper=WRAPPER):
    import verify_synth_top as top
    resolved = [p for n, p in top.resolve_sources(None) if n != "tb_top_bx.v"]
    if Path(wrapper).resolve() == STUB.resolve():
        return [str(BENCH)] + resolved + [str(wrapper)]
    return [str(BENCH)] + resolved + [str(rom), str(SEQ), str(wrapper)]


def tampered_rom(outdir: Path) -> Path:
    """The PADS_KIT_HASH mutant: one kit word of the boot ROM changed (the BD
    body mode's a1, the first kit write whose value it moves by one LSB)."""
    text = ROM.read_text()
    m = pr._BOOT_LINE.search(text, text.index("8'd2: word"))
    old = m.group(0)
    val = int(m.group(5), 16) ^ 1
    new = old[:old.rindex("32'h")] + f"32'h{val:08X}}};"
    out = outdir / "pads_rom_tampered.v"
    out.write_text(text.replace(old, new, 1))
    return out


def simulate(scenario, inject, outdir, *, wrapper=WRAPPER, rom=ROM, timeout_s=5400):
    import verify_synth_top as top
    outdir = Path(outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    script = SCENARIOS[scenario]()
    cmd_path = outdir / "pads_cmds.txt"
    cmd_path.write_text("\n".join(script.lines()) + "\n")
    srcs = sources(rom, wrapper)
    defines = ["VOICE_OSC_2X", "VOICE_FILTER_2X"]
    if Path(wrapper).resolve() != STUB.resolve():
        defines.append("PADS_HIER")
    if inject and inject != "PADS_KIT_HASH":
        defines.append(f"INJECT_BUG_{inject}")
    iverilog, vvp = top.tool("iverilog"), top.tool("vvp")
    if not iverilog or not vvp:
        print("verify_pads_top: REFUSED -- iverilog/vvp not on PATH")
        return None
    exe = outdir / "tb_pads_top.vvp"
    r = subprocess.run([iverilog, "-g2012", "-o", str(exe)] + [f"-D{d}" for d in defines] + srcs,
                       capture_output=True, text=True)
    if r.returncode != 0:
        print("verify_pads_top: compile failed:\n" + r.stdout + r.stderr)
        return None
    files = {k: str(outdir / f"pads_{k}.txt") for k in ("wrs", "i2s", "txd")}
    run_cmd = [vvp, "-n", str(exe), f"+cmd={cmd_path}", f"+wrs={files['wrs']}",
               f"+i2s={files['i2s']}", f"+txd={files['txd']}"]
    try:
        # cwd = rtl-sketch: the core's $readmemh ROMs resolve from there
        r = subprocess.run(run_cmd, capture_output=True, text=True, timeout=timeout_s,
                           cwd=str(ROOT / "rtl-sketch"))
    except subprocess.TimeoutExpired:
        print(f"verify_pads_top: simulation timed out after {timeout_s}s")
        return None
    report = [ln for ln in r.stdout.splitlines() if ln.startswith("tb_pads_top")]
    (outdir / "transcript.txt").write_text("\n".join(report) + "\n")
    if r.returncode != 0 or any("SCRIPT LATE" in ln or "bad command" in ln for ln in report):
        print("verify_pads_top: vvp failed or the script could not be kept:\n"
              + "\n".join(report[-5:]) + r.stderr[-2000:])
        return None
    return {"outdir": outdir, "script": script, "report": report, "files": files,
            "scenario": scenario, "inject": inject, "defines": defines, "srcs": srcs,
            "hier": "PADS_HIER" in defines}


# ---- analysis ------------------------------------------------------------------
def _rows(path):
    p = Path(path)
    return [ln.split() for ln in p.read_text().splitlines() if ln.strip()] if p.exists() else []


def _ints(row):
    try:
        return [int(x) for x in row]
    except ValueError:
        return None


def analyze(run):
    report = "\n".join(run["report"])
    script = run["script"]
    comp = {"scenario": run["scenario"], "inject": run["inject"]}
    detail = []
    segs = [(int(m.group(1)), int(m.group(2))) for m in RE_SEG.finditer(report)]
    if len(segs) != len(script.segs):
        comp["segment_mismatch"] = (len(segs), len(script.segs))
        detail.append(f"bench reported {len(segs)} segments, the script has {len(script.segs)}")
        return False, comp, detail
    readies = {}
    for m in RE_READY.finditer(report):
        if int(m.group(2)) == 1:
            readies.setdefault(int(m.group(1)), int(m.group(3)))

    wr_all, x_bad = [], 0
    for row in _rows(run["files"]["wrs"]):
        v = _ints(row)
        if v is None:
            x_bad += 1
            continue
        wr_all.append((v[0], v[1], v[2], v[3], v[4], v[5] & 0xFFFFFFFF, v[6]))
    tx = [_ints(r) for r in _rows(run["files"]["txd"])]
    i2s = _rows(run["files"]["i2s"])

    for k in ("writes_expected", "writes_seen", "writes_bad", "extra_writes",
              "missing_writes", "hit_frame_bad", "boot_frame_bad", "host_writes_missing",
              "wire_mismatch", "swap", "width", "acks", "errs", "boots"):
        comp[k] = 0
    comp["x_bad"] = x_bad
    comp["presses"], comp["presses_dropped"] = 0, 0
    comp["precondition"] = []
    required_periods, total_periods, gaps = 0, 0, []

    import numpy as np  # noqa: F401  (SynthTopModel needs it)
    for si, seg in enumerate(script.segs):
        exp = expected_segment(seg)
        comp["presses"] += len(exp["accepted"])
        comp["presses_dropped"] += len(exp["dropped"])
        seen = [w[1:] for w in wr_all if w[0] == si]
        # -- preconditions: the script's presses must land after the kit loaded
        if run["hier"] and exp["ready"] is not None:
            got_ready = readies.get(si)
            if got_ready is None:
                detail.append(f"seg {si}: the sequencer never reported READY")
            elif exp["presses"] and got_ready > exp["presses"][0][0]:
                comp["precondition"].append(f"seg {si}: READY at g {got_ready} after the first "
                                            f"scripted press at {exp['presses'][0][0]}")
            elif abs(got_ready - exp["ready"]) > 4:
                detail.append(f"seg {si}: READY at g {got_ready}, predicted {exp['ready']}")
        # -- the writes, in the order the contract says they execute ----------
        want = ([("host", None, w) for w in exp["host"]]
                + [("boot", f, w) for f, w in zip(exp["boot_frames"], exp["boot"])]
                + [("hit", h[0], h[1:]) for h in exp["hits"]])
        comp["writes_expected"] += len(want)
        comp["writes_seen"] += len(seen)
        for i, (kind, f, w) in enumerate(want):
            if i >= len(seen):
                comp["missing_writes"] += 1
                if kind == "host":
                    comp["host_writes_missing"] += 1
                continue
            g = seen[i]
            got = (g[1], g[2], g[3], g[4])
            if tuple(w) != got:
                comp["writes_bad"] += 1
                if kind == "host":
                    comp["host_writes_missing"] += 1
                if comp["writes_bad"] <= 4:
                    detail.append(f"seg {si} {kind} write {i}: want f/s/a/d "
                                  f"{w[0]}/{w[1]}/{w[2]:#x}/{w[3]:#x}, got "
                                  f"{got[0]}/{got[1]}/{got[2]:#x}/{got[3]:#x} (frame {g[0]})")
            if kind == "hit" and g[0] != f:
                comp["hit_frame_bad"] += 1
                if comp["hit_frame_bad"] <= 4:
                    detail.append(f"seg {si} hit write {i} (a={w[2]:#x}): contract frame {f}, "
                                  f"landed {g[0]}")
            if kind == "boot" and abs(g[0] - f) > 1:
                comp["boot_frame_bad"] += 1
                if comp["boot_frame_bad"] <= 2:
                    detail.append(f"seg {si} boot write {i}: predicted frame {f}, landed {g[0]}")
        if len(seen) > len(want):
            comp["extra_writes"] += len(seen) - len(want)
            for g in seen[len(want):len(want) + 3]:
                detail.append(f"seg {si}: unexpected write f/s/a/d {g[1]}/{g[2]}/{g[3]:#x}/"
                              f"{g[4]:#x} in frame {g[0]}")
        # -- the device's TX ------------------------------------------------------
        seg_tx = bytes(t[2] for t in tx if t is not None and t[0] == si)
        for pkt in uh.parse_device_stream(seg_tx):
            if pkt.kind == "ack":
                comp["acks"] += 1
            elif pkt.kind == "err":
                comp["errs"] += 1
                detail.append(f"seg {si}: device ERR {pkt.code} ({uh.ERR_NAMES.get(pkt.code)})")
            elif pkt.kind == "boot":
                comp["boots"] += 1
        # -- the model: boot and host writes where they landed (their values are
        # checked above), hit writes where the CONTRACT puts them -------------
        mw = []
        n_pre = len(exp["host"]) + len(exp["boot"])
        for i in range(n_pre):
            kind, f, w = want[i]
            f_at = seen[i][0] if i < len(seen) and tuple(seen[i][1:5]) == tuple(w) else \
                (f if f is not None else 0)
            mw.append((f_at, *w))
        mw += [(h[0], *h[1:]) for h in exp["hits"]]
        mw.sort(key=lambda x: x[0])
        n = pr.cycle_frame(seg.end) - 2
        m = stm.SynthTopModel(oversample_2x=True, filter_2x=True, pulse_2x=False).run(mw, n)
        exp_i2s = m["i2s"]
        p0 = segs[si][1]
        periods = [r for r in i2s if _ints(r[:1]) and p0 <= int(r[0]) < p0 + n]
        required_periods += n
        idx = [int(r[0]) for r in periods]
        if idx != list(range(p0, p0 + n)):
            gaps.append(f"seg {si}: {len(idx)} of {n} required I2S periods decoded")
        first_bad = None
        for r in periods:
            v = _ints(r)
            if v is None:
                comp["wire_mismatch"] += 1
                comp["width"] += 1
                continue
            p, left, right, nbl, nbr = v
            e = int(exp_i2s[p - p0])
            if nbl != 32 or nbr != 32:
                comp["width"] += 1
            if left != e:
                comp["wire_mismatch"] += 1
                if first_bad is None:
                    first_bad = (p - p0, e, left)
            if right != left:
                comp["swap"] += 1
        if first_bad:
            detail.append(f"seg {si}: first I2S mismatch at frame {first_bad[0]}: model "
                          f"{first_bad[1]}, wire {first_bad[2]}")
        total_periods += len(periods)
        comp.setdefault("model_peak", 0)
        comp["model_peak"] = max(comp["model_peak"], int(np.abs(exp_i2s).max()) if n else 0)
    comp["periods"] = total_periods
    comp["periods_required"] = required_periods
    comp["i2s_incomplete"] = bool(gaps)
    detail[:0] = gaps
    mw_, ms_, mb_ = RE_WRT.search(report), RE_STRB.search(report), RE_BUSY.search(report)
    if not (mw_ and ms_ and mb_):
        detail.append("the bench did not report its frame budget")
        return False, comp, detail
    comp["collisions"] = int(mw_.group(2))
    comp["frames_no_sample"] = int(ms_.group(2))
    comp["worst_strobe_cycle"] = int(ms_.group(3))
    comp["busy_at_tick"] = int(mb_.group(1))
    comp["overrun"] = int(mb_.group(2))
    if comp["boots"] != len(script.segs):
        detail.append(f"{comp['boots']} BOOT bytes on TX for {len(script.segs)} segments")
    if comp["acks"] < comp["writes_expected"]:
        detail.append(f"{comp['acks']} ACKs for {comp['writes_expected']} expected writes")
    if comp["model_peak"] < 1000:
        # a scenario whose model is (near) silent cannot show a hit was right
        comp["precondition"].append(f"the model's loudest sample is {comp['model_peak']} LSB")
    ok = (all(comp[k] == 0 for k in ("writes_bad", "extra_writes", "missing_writes",
                                     "hit_frame_bad", "boot_frame_bad", "x_bad",
                                     "wire_mismatch", "swap", "width", "errs",
                                     "collisions", "frames_no_sample", "busy_at_tick",
                                     "overrun"))
          and comp["boots"] == len(script.segs) and comp["acks"] >= comp["writes_expected"]
          and 0 < comp["worst_strobe_cycle"] < 256 and not comp["i2s_incomplete"]
          and comp["writes_seen"] > 0
          and not any(d.startswith(f"seg ") and "READY" in d for d in detail))
    return ok, comp, detail


def record_for(run, comp, ok, state=None):
    hashed = {}
    for p in [Path(s) for s in run["srcs"]] + [ROOT / "fpga/pads_rom.py", pr.R1_KIT]:
        key = str(p.resolve().relative_to(ROOT)) if p.resolve().is_relative_to(ROOT) else str(p)
        hashed[key] = _sha(p)
    import build_arty
    for path in build_arty.roms():
        hashed[str(path.relative_to(ROOT))] = _sha(path)
    transcript = Path(run["outdir"]) / "transcript.txt"
    return {"state": state or ("PASS" if ok else "FAIL"), "exit_code": 0 if ok else 1,
            "inject": run["inject"], "scenario": run["scenario"], "configuration": CONFIG,
            "scope": ("Arty pads wrapper (#449) digital check: buttons and switches at the "
                      "pins, the internal UART sender into the unmodified bridge, the kit "
                      "boot ROM, hit writes at their contract frames, and the I2S wire vs "
                      "the integer model; MMCM bypassed; no physical timing claim"),
            "comparison": comp, "source_sha256": hashed,
            "transcript_sha256": _sha(transcript) if transcript.exists() else None}


def kit_hash_gate(rom: Path) -> tuple:
    """(ok, reason): the gate every consumer runs before trusting a ROM."""
    try:
        pr.check_rom(rom)
    except Exception as exc:      # Refused, KitRefused, a parse failure: all refusals
        return False, f"{type(exc).__name__}: {exc}"
    return True, "bound"


def run_one(scenario, inject, outdir, *, wrapper=WRAPPER):
    outdir = Path(outdir)
    rom = ROM
    if inject == "PADS_KIT_HASH":
        outdir.mkdir(parents=True, exist_ok=True)
        rom = tampered_rom(outdir)
    ok_gate, why = kit_hash_gate(rom)
    if not ok_gate:
        outdir.mkdir(parents=True, exist_ok=True)
        rec = {"state": "REFUSED", "exit_code": 2, "inject": inject, "scenario": scenario,
               "reason": "kit-hash", "detail": why}
        if inject == "PADS_KIT_HASH":
            # condition 2 of docs/verification-rules.md 5: the mutant must BUILD --
            # a ROM that does not compile would make the refusal meaningless
            import verify_synth_top as top
            iverilog = top.tool("iverilog")
            r = subprocess.run([iverilog, "-g2012", "-o", str(outdir / "mutant.vvp"),
                                "-DVOICE_OSC_2X", "-DVOICE_FILTER_2X"]
                               + sources(rom, wrapper), capture_output=True, text=True) \
                if iverilog else None
            rec["mutant_compiles"] = bool(r is not None and r.returncode == 0)
        (outdir / "verification.json").write_text(json.dumps(rec, indent=2) + "\n")
        print(f"verify_pads_top[{outdir.name}]: REFUSED -- kit-hash: {why}")
        return rec, False, {"refused": "kit-hash", "mutant_compiles": rec.get("mutant_compiles")}
    run = simulate(scenario, inject, outdir, wrapper=wrapper, rom=rom)
    if run is None:
        return None, False, {}
    ok, comp, detail = analyze(run)
    state = None
    if comp.get("precondition"):
        state = "REFUSED"
        ok = False
    rec = record_for(run, comp, ok, state)
    rec["detail"] = detail[:12]
    (outdir / "verification.json").write_text(json.dumps(rec, indent=2) + "\n")
    print(f"verify_pads_top[{outdir.name}]: {rec['state']} -- presses {comp.get('presses')} "
          f"(dropped {comp.get('presses_dropped')}), writes {comp.get('writes_seen')}/"
          f"{comp.get('writes_expected')}, bad {comp.get('writes_bad')}, extra "
          f"{comp.get('extra_writes')}, hit frame bad {comp.get('hit_frame_bad')}, "
          f"I2S mismatch {comp.get('wire_mismatch')} over {comp.get('periods')} periods, "
          f"errs {comp.get('errs')}, peak {comp.get('model_peak')}", flush=True)
    for d in (comp.get("precondition") or []) + detail[:6]:
        print(f"verify_pads_top[{outdir.name}]:   {d}")
    return rec, ok, comp


REASONS = {
    "PADS_DEBOUNCE_DOUBLE": lambda c: c.get("extra_writes", 0) > 0,
    "PADS_TRIG_STUCK": lambda c: c.get("wire_mismatch", 0) > 0 and c.get("writes_bad", 0) > 0,
    "PADS_SRC_STUCK": lambda c: c.get("host_writes_missing", 0) > 0,
    "PADS_KIT_HASH": lambda c: c.get("refused") == "kit-hash" and c.get("mutant_compiles"),
}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--outdir", type=Path, default=ROOT / "build/pads")
    ap.add_argument("--scenario", choices=tuple(SCENARIOS) + ("all",), default="all")
    ap.add_argument("--inject", choices=INJECTS, default=None)
    ap.add_argument("--start-red", action="store_true",
                    help="run the phases scenario against the stub; must FAIL")
    ap.add_argument("--jobs", type=int, default=3)
    a = ap.parse_args(argv)
    outdir = a.outdir.resolve()
    outdir.mkdir(parents=True, exist_ok=True)

    if a.start_red:
        print(f"verify_pads_top: START RED against {STUB.relative_to(ROOT)}")
        rec, ok, comp = run_one("phases", None, outdir / "start-red", wrapper=STUB)
        if rec is None:
            return 2
        if ok:
            print("verify_pads_top: FAIL -- the stub PASSED; the bench cannot be trusted")
            return 1
        if rec["state"] == "REFUSED":
            print("verify_pads_top: start red REFUSED rather than failed -- no verdict")
            return 2
        print("verify_pads_top: start red confirmed -- the bench fails against a "
              "behaviour-free stub, as it must")
        return 0

    if a.inject:
        sc = INJECT_SCENARIO[a.inject]
        rec, ok, comp = run_one(sc, a.inject, outdir / f"{sc}-inject-{a.inject}")
        if rec is None:
            return 2
        if ok:
            print(f"verify_pads_top: NEGATIVE CONTROL NOT CAUGHT ({a.inject})")
            return 1
        if not REASONS[a.inject](comp):
            print(f"verify_pads_top: control {a.inject} failed for the WRONG reason: "
                  f"{ {k: comp.get(k) for k in ('extra_writes', 'writes_bad', 'wire_mismatch', 'host_writes_missing', 'refused')} }")
            return 1
        print(f"verify_pads_top: control {a.inject} CAUGHT for its recorded reason")
        return 0

    scenarios = list(SCENARIOS) if a.scenario == "all" else [a.scenario]
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=a.jobs) as pool:
        results = list(pool.map(lambda sc: (sc, *run_one(sc, None, outdir / sc)), scenarios))
    summary = {sc: (rec or {}).get("state", "NO VERDICT") for sc, rec, _ok, _c in results}
    good = all(v == "PASS" for v in summary.values())
    (outdir / "controls.json").write_text(json.dumps(
        {"state": "PASS" if good else "FAIL", "runs": summary}, indent=2) + "\n")
    print(json.dumps({"state": "PASS" if good else "FAIL", "runs": summary}))
    return 0 if good else 1


if __name__ == "__main__":
    raise SystemExit(main())
