#!/usr/bin/env python3
"""fpga/pads_rom.py -- the pads demo's ROMs and its timing contract (#449).

The pads wrapper (fpga/rtl/arty_a7_pads_top.v) plays four 808 voices from the
Arty's four push buttons with no host attached. It holds two ROMs, both
GENERATED here into fpga/rtl/pads_rom.v and never edited by hand:

  BOOT ROM   replayed as live UART write packets each time pads mode is
             entered (including after reset with the switch in pads mode):
               1. R1's known-state preamble (voice RESET 0x23, drum RESET 0xFF),
               2. R1's drum kit, FROZEN BY VALUE (fpga/release/r1-kit.json),
               3. the drum-bus gains DVOL/BVOL at the level uart_host's voice
                  image uses (drums_fx.accent_reg(0.45)).
             The kit is taken through uart_host.r1_kit(), which REFUSES unless
             it hashes to R1's digest, and the generated file is re-parsed and
             re-hashed by every consumer (`check_rom`), so the ROM cannot drift
             from the release kit without a build, a bench or this CLI saying so.
  HIT ROM    one program per button: model/drums_fx.hit_writes for that stop at
             ACCENT, coefficient sequences included -- verbatim, minus the kit
             prefix hit_writes puts in front. BD is 7 writes over 192 frames
             (the 4 ms attack retune), SD/CH/CP are 3 writes over 2 frames.

THE CONTRACT (what the RTL does, stated as code so the bench can check it):

  * a press is the rising edge of an EAGER debouncer: the first synchronised
    change flips the state and starts a DB_CYCLES lockout, during which the
    pin is ignored. The press is registered PRESS_LAT_CYC core cycles after
    the cycle in which the pin changed, and `t0` is the audio frame of that
    cycle (`press_cycles`, `cycle_frame`);
  * a voice runs one program at a time. It is busy from its press until
    frame t0 + last_offset + LAT has passed; a press while busy is DROPPED;
  * every program write is sent as a UART EVENT packet (the bridge's device-
    side scheduler) with due = t0 + offset + LAT. Writes whose scheduled frame
    t0 + offset has passed are sent in (sched, button, step) order -- so the
    dues are non-decreasing, which the bridge's FIFO event queue requires;
  * the stops register is one mask for all eleven stops, so its writes carry a
    SHADOW of it: a rise ORs the stop's bit in, a drop clears it. Isolated
    presses therefore send exactly hit_writes' values; overlapping ones keep
    every stop's 0->1 edge;
  * the bridge executes at most TWO writes per frame (synth_top's UART
    window). A frame with more than two dues spills the rest, in FIFO order,
    to the following frames (`apply_frames`). BD's first frame carries four
    writes, so its trigger lands one frame after its attack retune -- the only
    place an isolated hit differs from hit_writes' frames, and it is stated in
    fpga/ARTY.md rather than hidden;
  * LAT (frames) covers the worst case: all four voices pressed in one frame
    with every program outstanding, 16 packets on a 115200-baud wire
    (`lat_required`). It is 14.7 ms. That is the demo's press-to-sound latency.

    python3 fpga/pads_rom.py            # BOUND (0) / STALE (1) / REFUSED (2)
    python3 fpga/pads_rom.py --write    # regenerate fpga/rtl/pads_rom.v
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for _p in ("fpga", "model", "rtl-sketch"):
    if str(ROOT / _p) not in sys.path:
        sys.path.insert(0, str(ROOT / _p))

import drums_fx as dx                               # noqa: E402
import synth_top_model as stm                       # noqa: E402
import uart_host as uh                              # noqa: E402

ROM_V = ROOT / "fpga/rtl/pads_rom.v"
R1_KIT = uh.R1_KIT

# ---- the demo's choices (the operator's ear decides the voices; #449) ------
VOICES = ("BD", "SD", "CH", "CP")          # BTN0..BTN3
ACCENT = 1.0                               # PATTERN_808's plain 'x'
DRUM_BUS_LEVEL = 0.45                      # uart_host.voice_image_writes' DVOL/BVOL

# ---- the wire and the clock (mirrors of uart_host / uart_bridge.v) ---------
CLK_HZ = uh.CLK_HZ
CYC_PER_FRAME = uh.CYC_PER_FRAME
BAUD = uh.DEFAULT_BAUD
DIV = (CLK_HZ + BAUD // 2) // BAUD         # core cycles per bit, as the bridge counts
BYTE_CYCLES = 10 * DIV
EVENT_BYTES, WRITE_BYTES = 10, 8   # uart_host.pkt_event / pkt_write lengths
GUARD_BITS = 64                            # idle bit times before the first boot packet:
                                           # > the bridge's 40-bit mid-packet gap timer,
                                           # so a host packet cut by the switch is flushed

# ---- the debouncer ----------------------------------------------------------
DB_CYCLES = 61_440                         # 5.0 ms lockout at 12.288 MHz
PRESS_LAT_CYC = 3                          # pin change in cycle T -> press latched in T+3

KIND_PLAIN, KIND_RISE, KIND_DROP = 0, 1, 2


class Refused(Exception):
    """The ROM cannot be trusted: it does not re-derive R1's kit."""


# ---- the two images -----------------------------------------------------------
def r1_kit() -> list:
    """R1's frozen kit; drums_fx.KitRefused unless it hashes to R1's digest,
    and Refused unless r1-kit.json's own `sha256` field agrees."""
    kit = uh.r1_kit()
    rec = json.loads(R1_KIT.read_text())
    if rec.get("sha256") != dx._kit_sha256(kit):
        raise Refused(f"{R1_KIT.name}: its sha256 field does not match its writes")
    return kit


def boot_image(kit: list | None = None) -> list:
    """(flag, sec, addr, data) in send order."""
    kit = r1_kit() if kit is None else kit
    out = [tuple(w) for w in uh.known_state_preamble()]
    out += [(0, stm.SEC_DRUM, int(a), int(v)) for a, v in kit]
    lvl = dx.accent_reg(DRUM_BUS_LEVEL)
    out += [(0, stm.SEC_VOICE, stm.A_DVOL, lvl), (0, stm.SEC_VOICE, stm.A_BVOL, lvl)]
    return out


def hit_program(voice: str, kit: list | None = None) -> list:
    """[(offset, kind, flag, sec, addr, data)]: hit_writes for one press of
    `voice` at frame 0, verbatim, with the kit prefix removed."""
    kit = r1_kit() if kit is None else kit
    w = dx.hit_writes([(0, dx.SOUND_STOP[voice], ACCENT)], kit=kit, coef_seq=True)
    head = [(0, a, v) for a, v in kit]
    if w[:len(head)] != head:
        raise Refused("hit_writes no longer emits the kit first; the program split is wrong")
    prog = []
    for f, a, v in w[len(head):]:
        if a == dx.A_STOPS:
            kind = KIND_RISE if v else KIND_DROP
            v = v or (1 << dx.SOUND_STOP[voice])
        else:
            kind = KIND_PLAIN
        prog.append((int(f), kind, 0, stm.SEC_DRUM, int(a), int(v)))
    return prog


def programs(kit: list | None = None) -> list:
    kit = r1_kit() if kit is None else kit
    return [hit_program(v, kit) for v in VOICES]


# ---- the latency budget ----------------------------------------------------------
def lat_required(progs: list | None = None) -> int:
    """Frames from a write's scheduled frame to the latest acceptance of its
    event packet, worst case: every voice's whole program outstanding, one
    frame of pick delay (a write is picked only once its frame has PASSED),
    then every packet back to back, then the bridge's acceptance frame and the
    contract's due >= accept + 1."""
    progs = programs() if progs is None else progs
    packets = sum(len(p) for p in progs)
    wire = packets * EVENT_BYTES * BYTE_CYCLES
    return 1 + math.ceil(wire / CYC_PER_FRAME) + 2


LAT = 704                                  # 14.7 ms; asserted >= lat_required()


# ---- the generated Verilog ---------------------------------------------------------
def _boot_word(w) -> str:
    flag, sec, addr, data = w
    return f"{{1'b{flag}, 1'b{sec}, 8'h{addr:02X}, 32'h{data & 0xFFFFFFFF:08X}}}"


def render(kit: list | None = None) -> str:
    kit = r1_kit() if kit is None else kit
    boot = boot_image(kit)
    progs = programs(kit)
    need = lat_required(progs)
    if LAT < need:
        raise Refused(f"LAT {LAT} frames is below the worst-case requirement {need}")
    digest = dx._kit_sha256(kit)
    L = []
    L.append("// fpga/rtl/pads_rom.v -- GENERATED by fpga/pads_rom.py --write. DO NOT EDIT.")
    L.append("//")
    L.append("// The pads demo's ROMs (#449). `python3 fpga/pads_rom.py` re-derives this file")
    L.append("// and REFUSES a boot ROM whose kit does not hash to R1's frozen digest.")
    L.append(f"//   kit sha256 (drums_fx._kit_sha256 of boot entries 2..{1 + len(kit)}): {digest}")
    L.append(f"//   source: {R1_KIT.relative_to(ROOT)} (uart_host.R1_KIT_SHA256)")
    L.append(f"//   voices: {', '.join(f'BTN{i}={v}' for i, v in enumerate(VOICES))}; accent {ACCENT}")
    L.append(f"//   LAT {LAT} frames (worst case needs {need}); boot {len(boot)} writes")
    L.append("`default_nettype none")
    L.append("module pads_boot_rom (")
    L.append("    input  wire [7:0]  idx,")
    L.append("    output reg  [41:0] word,          // {flag, sec, addr[7:0], data[31:0]}")
    L.append("    output wire [7:0]  len,")
    L.append("    output wire [15:0] lat")
    L.append(");")
    L.append(f"    assign len = 8'd{len(boot)};")
    L.append(f"    assign lat = 16'd{LAT};")
    L.append("    always @(*) begin")
    L.append("        case (idx)")
    for i, w in enumerate(boot):
        L.append(f"        8'd{i}: word = {_boot_word(w)};")
    L.append("        default: word = 42'd0;")
    L.append("        endcase")
    L.append("    end")
    L.append("endmodule")
    L.append("")
    L.append("// One lookup per button; kind 0 = plain write, 1 = stops rise, 2 = stops drop")
    L.append("// (for 1 and 2 the data field is the stop's bit; the sender merges it into its")
    L.append("// shadow of the stops mask).")
    L.append("module pads_hit_rom (")
    L.append("    input  wire [1:0]  voice,")
    L.append("    input  wire [2:0]  step,")
    L.append("    output reg  [51:0] word,          // {kind[1:0], off[7:0], flag, sec, addr[7:0], data[31:0]}")
    L.append("    output reg  [2:0]  len,")
    L.append("    output reg  [7:0]  last_off")
    L.append(");")
    L.append("    always @(*) begin")
    L.append("        case (voice)")
    for v, p in enumerate(progs):
        L.append(f"        2'd{v}: begin len = 3'd{len(p)}; last_off = 8'd{p[-1][0]}; end   // {VOICES[v]}")
    L.append("        default: begin len = 3'd0; last_off = 8'd0; end")
    L.append("        endcase")
    L.append("        case ({voice, step})")
    for v, p in enumerate(progs):
        for s, (off, kind, flag, sec, addr, data) in enumerate(p):
            if off > 255:
                raise Refused(f"{VOICES[v]} step {s}: offset {off} does not fit 8 bits")
            L.append(f"        {{2'd{v}, 3'd{s}}}: word = {{2'd{kind}, 8'd{off}, "
                     f"{_boot_word((flag, sec, addr, data))[1:-1]}}};")
    L.append("        default: word = 52'd0;")
    L.append("        endcase")
    L.append("    end")
    L.append("endmodule")
    L.append("`default_nettype wire")
    return "\n".join(L) + "\n"


_BOOT_LINE = re.compile(r"8'd(\d+): word = \{1'b(\d), 1'b(\d), 8'h([0-9A-F]{2}), 32'h([0-9A-F]{8})\};")
_HIT_LINE = re.compile(r"\{2'd(\d), 3'd(\d)\}: word = \{2'd(\d), 8'd(\d+), 1'b(\d), 1'b(\d), "
                       r"8'h([0-9A-F]{2}), 32'h([0-9A-F]{8})\};")
_LAT_LINE = re.compile(r"assign lat = 16'd(\d+);")


def parse_rom(text: str) -> dict:
    """What a pads_rom.v file actually holds -- read from the Verilog, so a
    hand edit or a stale file is what gets checked, not what we meant."""
    boot = {}
    for m in _BOOT_LINE.finditer(text):
        boot[int(m.group(1))] = (int(m.group(2)), int(m.group(3)), int(m.group(4), 16),
                                 int(m.group(5), 16))
    progs = {}
    for m in _HIT_LINE.finditer(text):
        v, s = int(m.group(1)), int(m.group(2))
        progs.setdefault(v, {})[s] = (int(m.group(4)), int(m.group(3)), int(m.group(5)),
                                      int(m.group(6)), int(m.group(7), 16), int(m.group(8), 16))
    lat = _LAT_LINE.search(text)
    return {"boot": [boot[i] for i in sorted(boot)],
            "programs": [[progs[v][s] for s in sorted(progs[v])] for v in sorted(progs)],
            "lat": int(lat.group(1)) if lat else None}


def check_rom(path: Path = ROM_V) -> dict:
    """REFUSES (raises Refused) unless the ROM file's kit hashes to R1's digest
    (both uart_host.R1_KIT_SHA256 and r1-kit.json's own field), its preamble
    and tail are the boot image's, and its hit programs are hit_writes'.
    Returns the parsed ROM."""
    rom = parse_rom(Path(path).read_text())
    boot = rom["boot"]
    pre = [tuple(w) for w in uh.known_state_preamble()]
    if boot[:len(pre)] != pre:
        raise Refused(f"{Path(path).name}: boot ROM does not start with R1's known-state preamble")
    kit_rows = [w for w in boot[len(pre):] if w[1] == stm.SEC_DRUM]
    kit = [(a, d) for _f, _s, a, d in kit_rows]
    got = dx._kit_sha256(kit)
    want = json.loads(R1_KIT.read_text()).get("sha256")
    if got != uh.R1_KIT_SHA256 or got != want:
        raise Refused(f"{Path(path).name}: boot ROM kit hashes to {got[:12]}, not R1's frozen "
                      f"kit {uh.R1_KIT_SHA256[:12]} ({R1_KIT.name} says {str(want)[:12]})")
    if boot != boot_image(kit):
        raise Refused(f"{Path(path).name}: boot ROM is not R1's boot image (order or tail differs)")
    want_progs = programs(kit)
    if rom["programs"] != want_progs:
        raise Refused(f"{Path(path).name}: hit ROM is not drums_fx.hit_writes for {VOICES}")
    if rom["lat"] is None or rom["lat"] < lat_required(want_progs):
        raise Refused(f"{Path(path).name}: LAT {rom['lat']} is below the worst-case requirement")
    return rom


# ---- the contract -------------------------------------------------------------
def cycle_frame(g: int) -> int:
    """The audio frame of core cycle `g`, counted from g = 0 at audio frame 0's
    cycle 1 (the bench's anchor): synth_top's frame register increments at
    cyc 0, so cycle 255 of a frame (cyc 0 of the next) still reads it."""
    return g // CYC_PER_FRAME


def press_cycles(transitions: list, *, db: int = DB_CYCLES, lockout: bool = True) -> list:
    """The cycles at which the debouncer registers presses, for one button.

    transitions: [(T, level)] sorted -- the pin takes `level` in cycle T (the
    bench changes it at that cycle's falling edge). The pin starts at 0. The
    synchronised value q1(c) is the pin level of cycle c-2; the state flips at
    the edge ending cycle c when the lockout has run out and q1(c) differs, and
    the press is latched one cycle later (PRESS_LAT_CYC = 3 after T).
    `lockout=False` is the double-firing debouncer (INJECT_BUG_PADS_DEBOUNCE_DOUBLE)."""
    def pin(c):
        lvl = 0
        for t, l in transitions:
            if t <= c:
                lvl = l
            else:
                break
        return lvl
    cand = sorted({t + 2 for t, _ in transitions})
    state, free_at, out = 0, 0, []
    i = 0
    while i < len(cand):
        c = cand[i]
        if c < free_at:
            # changes during the lockout are ignored; the state catches up at expiry
            if free_at not in cand:
                cand.append(free_at)
                cand.sort()
            i += 1
            continue
        q1 = pin(c - 2)
        if q1 != state:
            state = q1
            if lockout:
                free_at = c + db
            if q1:
                out.append(c + 1)
        i += 1
    return out


def schedule(presses: list, progs: list | None = None, *, lat: int = LAT,
             trig_stuck: bool = False) -> dict:
    """presses: [(t0, voice)] registered in pads RUN mode, in time order.
    Returns {"writes": [(due, flag, sec, addr, data, voice, step)] in SEND
    order, "accepted": [...], "dropped": [...]}."""
    progs = programs() if progs is None else progs
    busy_until = {}
    accepted, dropped, entries = [], [], []
    for t0, v in presses:
        bu = busy_until.get(v)
        if bu is not None and not ((t0 - bu) > 0):
            dropped.append((t0, v))
            continue
        accepted.append((t0, v))
        busy_until[v] = t0 + progs[v][-1][0] + lat
        for s, e in enumerate(progs[v]):
            entries.append((t0 + e[0], v, s, e))
    entries.sort(key=lambda x: (x[0], x[1], x[2]))
    shadow = 0
    out = []
    for sched, v, s, (off, kind, flag, sec, addr, data) in entries:
        if kind == KIND_RISE:
            shadow |= data
            data = shadow
        elif kind == KIND_DROP:
            if not trig_stuck:
                shadow &= ~data & 0xFFFF
            data = shadow
        out.append((sched + lat, flag, sec, addr, data, v, s))
    return {"writes": out, "accepted": accepted, "dropped": dropped}


def apply_frames(dues: list, slots: int = uh.WRITE_SLOTS) -> list:
    """The frame each FIFO event executes in: due, or later when earlier
    events already filled that frame's `slots` write slots."""
    out, cur, used = [], None, 0
    for d in dues:
        f = d if cur is None or d > cur else cur
        if f == cur:
            if used < slots:
                used += 1
            else:
                f, used = f + 1, 1
        else:
            used = 1
        cur = f
        out.append(f)
    return out


def expected_hits(presses: list, **kw) -> list:
    """(apply_frame, flag, sec, addr, data) for presses [(t0, voice)]."""
    sch = schedule(presses, **kw)
    frames = apply_frames([w[0] for w in sch["writes"]])
    return [(f, w[1], w[2], w[3], w[4]) for f, w in zip(frames, sch["writes"])]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--write", action="store_true", help="regenerate fpga/rtl/pads_rom.v")
    ap.add_argument("--rom", type=Path, default=ROM_V)
    a = ap.parse_args(argv)
    try:
        text = render()
    except (Refused, dx.KitRefused) as exc:
        print(f"pads_rom: REFUSED -- {exc}")
        return 2
    if a.write:
        a.rom.write_text(text)
        print(f"pads_rom: wrote {a.rom}")
    try:
        check_rom(a.rom)
    except (Refused, dx.KitRefused, OSError, KeyError) as exc:
        print(f"pads_rom: REFUSED -- {exc}")
        return 2
    if a.rom.read_text() != text:
        print(f"pads_rom: STALE -- {a.rom} differs from a fresh derivation (run --write)")
        return 1
    print(f"pads_rom: BOUND -- {a.rom.relative_to(ROOT) if a.rom.is_relative_to(ROOT) else a.rom}"
          f" re-derives R1's kit ({uh.R1_KIT_SHA256[:12]}) and hit_writes for {VOICES}; "
          f"LAT {LAT} frames ({LAT * 1000 / uh.SR:.1f} ms, worst case needs {lat_required()})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
