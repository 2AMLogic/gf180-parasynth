#!/usr/bin/env python3
"""Can the control link CARRY the register writes the models perform?

Every register write in this design comes from a model: the voice's patch
image is `model/voice_fx.py`'s own conversion, the drum kit is
`model/drums_fx.py`'s `kit_808()` (contract Appendix G, SHA-pinned). Both
sides of the link have been shown bit-exact against those models at their own
ports, and neither test can see the link in between.

This script closes that. It takes the writes the two models produce, sends
them through `spi_ctl` as an MCU's SPI master would, and compares what reaches
the register write port against what the host INTENDED -- not against
anything the DUT produced.

    .venv/bin/python rtl-sketch/verify_ctl.py                 # revision 2, 48-bit frame
    .venv/bin/python rtl-sketch/verify_ctl.py --link dr7rev1  # revision 1, 32-bit: RED

Exit status, as verify_ladder.py: 0 identical, 1 differed, 2 did not run.

  --link {rev2,dr7rev1}  which receiver: the current one, or the standing
                         negative control (rtl-sketch/stubs/spi_ctl_dr7rev1.v,
                         DR 0007 revision 1's 32-bit frame).
  --inject NAME          compile with -DINJECT_BUG_<NAME>:
                           SPI_ADDR7      keep only A[6:0]      (the rev-1 address field)
                           SPI_DATA24     keep only D[23:0]     (the rev-1 data field)
                           SPI_NOSEC      ignore the section bit
                           SPI_ANYLEN     accept any bit count  (drops DR 0007 section 1)
                           SPI_DRAIN_LATE drain from cycle 8, i.e. at `go`
  --expect-fail          exit 0 only if the comparison gave 1

Every `--inject` or `--link dr7rev1` run also prints a blindness matrix: which
of the SIX properties the comparison decomposes a run into -- the four fields
of a write (flag, section, address, data), the write COUNT, and the DRAIN
window -- the defect MOVED and which stayed BLIND to it. That is the same
MOVED/BLIND distinction `model/sound_report.py --inject` reports for the sound
model, extended here to a second suite whose "properties" are link properties
rather than acoustic ones. SPI_ADDR7 (address truncated to 7 bits) should show
`address` MOVED and the other five BLIND; SPI_ANYLEN moves `count` alone and
SPI_DRAIN_LATE moves `drain` alone. A defect that moves all six, or none, is
worth reading even when the pass/fail verdict alone would look ordinary.

The row set is `PROPERTIES` below, and it is deliberately the single source
for both what `compare_writes` records and what `print_blindness` prints: the
matrix listed only the four bit-fields while `compare_writes` had been
recording the count and drain counters all along, so SPI_ANYLEN and
SPI_DRAIN_LATE -- both CAUGHT, each by one of the two unlisted properties --
printed four BLIND rows and "no property moved". A reporting hole reads
exactly like a coverage hole, which is why the two are now tied together.
"""
from __future__ import annotations
import argparse, os, subprocess, sys
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(ROOT, "model"))
sys.path.insert(0, os.path.join(ROOT, "audition"))
import voice_fx as vf
import drums_fx as dx
from verify_ladder import tool

SEC_VOICE, SEC_DRUM = 0, 1
GO_CYCLE = 8            # synth_top: every datapath block starts here (DR 0007 section 5)
AV = dict(INC=0x00, WAVE=0x04, W=0x08, GLIDE=0x0C, VOL=0x0D, DVOL=0x0E, ROUTE=0x0F,
          AMP=0x10, FILT=0x14, CUT_LO=0x18, CUT_HI=0x19, TRACK=0x1A, K=0x1C, GAIN=0x1D, OGAIN=0x1E,
          GATE_ON=0x20, GATE_OFF=0x21, TRIG=0x22, RESET=0x23,
          DCUT=0x28, DK=0x29, DGAIN=0x2A, DOGAIN=0x2B, BVOL=0x2C, NOP=0x3F)
WAVE_CODE = dict(saw=0, square=1, pulse25=2, tri=3, sine=4)


def voice_writes(note: int = 45) -> list:
    """The voice's whole control image, as the reference host writes it."""
    regs = vf.VoiceFx.patch_regs()
    w = [(0, SEC_VOICE, AV["NOP"], 0)]
    for k, s in enumerate(regs["waves"]):   w.append((0, SEC_VOICE, AV["WAVE"] + k, WAVE_CODE[s]))
    for k, g in enumerate(regs["weights"]): w.append((0, SEC_VOICE, AV["W"] + k, g))
    for base, key in ((AV["AMP"], "amp"), (AV["FILT"], "fenv")):
        for j, v in enumerate(regs[key]):   w.append((0, SEC_VOICE, base + j, v))
    for name, key in (("CUT_LO", "cut_lo"), ("CUT_HI", "cut_hi"), ("K", "k"),
                      ("GAIN", "gain"), ("OGAIN", "ogain"), ("GLIDE", "glide"), ("VOL", "vol")):
        w.append((0, SEC_VOICE, AV[name], regs[key]))
    w += [(0, SEC_VOICE, AV["DVOL"], 16384), (0, SEC_VOICE, AV["BVOL"], 16384),
          (0, SEC_VOICE, AV["ROUTE"], 1),
          (0, SEC_VOICE, AV["DCUT"], 1200), (0, SEC_VOICE, AV["DK"], regs["k"]),
          (0, SEC_VOICE, AV["DGAIN"], regs["gain"]), (0, SEC_VOICE, AV["DOGAIN"], regs["ogain"])]
    for k, v in enumerate(vf.VoiceFx.note_incs(note, regs["detune"])):
        w.append((1, SEC_VOICE, AV["INC"] + k, v))                      # flag = jump
    w.append((0, SEC_VOICE, AV["TRACK"], vf.VoiceFx.note_track(note, regs["track"])))
    w.append((0, SEC_VOICE, AV["GATE_ON"], 0))
    return w


def stimulus() -> list:
    """(flag, sec, addr, data) in send order: both models' images, then the
    corners of the frame -- every field at its width, both sections, the flag
    on and off, and the two RESET addresses."""
    w = voice_writes()
    w += [(0, SEC_DRUM, a, v) for a, v in dx.kit_808()]
    # the widest value of every drum register class, at the top and bottom address of each
    bits = dx.REG_BITS
    # The TOP address of every block, at the block's width, at the CURRENT size
    # -- not at revision 8's. The drum page grew (11 stops, 18 envelopes, 23
    # paths, 16 modes) and PATH and MODE moved to 0x90 and 0xB0 to keep the last
    # mode's NUM register off 0xFF, which is the drum soft reset. A corner list
    # pinned to the old counts would leave the new top of each block -- exactly
    # the addresses the move was made for -- untested, and would still pass.
    e_top = dx.A_ENV + (dx.N_ENV - 1) * dx.ENV_STRIDE
    m_top = dx.A_MODE + (dx.N_MODES - 1) * dx.MODE_STRIDE
    p_top = dx.A_PATH + dx.N_PATH - 1
    assert m_top + 3 != dx.A_RESET, "the top MODE register aliases the drum RESET: revision 8's defect"
    assert e_top + 3 < dx.A_PATH and p_top < dx.A_MODE, "two drum register blocks overlap"
    corners = [
        (dx.A_STOPS,               (1 << bits["stops"]) - 1),         # all ELEVEN stop bits
        (dx.A_ACCENT,              (1 << bits["accent"]) - 1),
        (dx.A_ACCENT + dx.N_STOPS - 1, (1 << bits["accent"]) - 1),    # the last accent, 0x1A
        (dx.A_ACCENT + dx.N_STOPS, 0),                                # one past it: must be ignored
        (dx.A_OSC,                 (1 << bits["osc_inc"]) - 1),
        (dx.A_OSC + 5,             1),
        (dx.A_ENV,                 (1 << bits["env_ctl"]) - 1),       # 27 bits
        (dx.A_ENV + 1,             (1 << bits["peak"]) - 1),
        (dx.A_ENV + 2,             (1 << bits["rate"]) - 1),
        (dx.A_ENV + 3,             (1 << bits["frate"]) - 1),         # revision 13: FRATE
        (e_top,                    (1 << bits["env_ctl"]) - 1),       # envelope 17, 0x84
        (e_top + 2,                (1 << bits["rate"]) - 1),
        (e_top + 3,                (1 << bits["frate"]) - 1),         # 0x87: the block's top now
        (dx.A_PATH,                (1 << bits["path"]) - 1),          # 25 bits now, at 0x90
        (p_top,                    (1 << bits["path"]) - 1),          # path 22, 0xA6
        (dx.A_MODE,                (1 << bits["a1"]) - 1),            # 26 bits, at 0xB0
        (dx.A_MODE + 1,            (1 << bits["a2"]) - 1),
        (dx.A_MODE + 2,            (1 << bits["amp"]) - 1),
        (dx.A_MODE + 3,            3),
        (m_top,                    (1 << bits["a1"]) - 1),            # mode 15, 0xEC
        (m_top + 3,                3),                                # 0xEF -- 0xC0 + 15*4 + 3 would
                                                                      # have been 0xFF, the reset
        (dx.A_RESET - 1,           (1 << 24) - 1),                    # 0xFE, next to the reset
        (dx.A_RESET,               0),                                # 0xFF, the drum page's reset
    ]
    w += [(0, SEC_DRUM, a, v) for a, v in corners]
    w += [(0, SEC_VOICE, AV["RESET"], 0), (1, SEC_VOICE, AV["INC"], (1 << 24) - 1),
          (0, SEC_VOICE, AV["NOP"], 0)]
    return w


def write_stream(path: str, writes: list, gap: int = 2) -> None:
    """[63:48] wait_frames  [47:44] flag  [43:40] sec  [39:32] addr  [31:0] data"""
    with open(path, "w") as fh:
        for i, (f, s, a, d) in enumerate(writes):
            fh.write(f"{gap if i else 0:04x}{f:01x}{s:01x}{a & 0xFF:02x}{d & 0xFFFFFFFF:08x}\n")


def expected_stream(writes: list) -> list:
    out = []
    for f, s, a, d in writes:
        out += [int(f), int(s), int(a) & 0xFF, int(d) & 0xFFFFFFFF]
    return out


def simulate(link: str, defines, outdir: str, bits: int, timeout_s: float = 900.0):
    iverilog, vvp = tool("iverilog"), tool("vvp")
    if not iverilog or not vvp:
        print("verify_ctl: iverilog/vvp not on PATH (or set OSS_CAD_SUITE)"); return None
    src = os.path.join(HERE, "spi_ctl.v") if link == "rev2" else os.path.join(HERE, "stubs", "spi_ctl_dr7rev1.v")
    vvp_file = os.path.join(outdir, f"tb_ctl_{link}.vvp")
    out_file = os.path.join(outdir, f"ctl_rtl_out_{link}.txt")
    if os.path.exists(out_file): os.remove(out_file)
    r = subprocess.run([iverilog, "-g2012", "-o", vvp_file] + [f"-D{d}" for d in defines]
                       + [os.path.join(HERE, "tb_ctl.v"), src], cwd=HERE, capture_output=True, text=True)
    if r.returncode != 0:
        print("verify_ctl: iverilog failed:\n" + r.stdout + r.stderr); return None
    try:
        r = subprocess.run([vvp, "-n", vvp_file, f"+writes={os.path.join(outdir, 'ctl_writes.hex')}",
                            f"+out={out_file}", f"+bits={bits}"],
                           cwd=HERE, capture_output=True, text=True, timeout=timeout_s)
    except subprocess.TimeoutExpired:
        print("verify_ctl: simulation timed out"); return None
    sys.stdout.write("".join("  sim: " + l + "\n" for l in r.stdout.splitlines() if l.startswith("tb_ctl")))
    if r.returncode != 0 or not os.path.exists(out_file):
        print("verify_ctl: vvp failed:\n" + r.stdout + r.stderr); return None
    return out_file


# What the last run actually found, so a caller can check that a negative
# control failed for the reason it was recorded to fail for. The "intended"
# side of this comparison comes from model/voice_fx.py and model/drums_fx.py --
# never from the RTL -- which is what keeps it from going self-referential.
LAST: dict = {}

# The properties a run is decomposed into: the name the matrix prints, the key
# in `LAST` holding that property's numerator, the key holding the POPULATION
# that numerator was counted over, and what a nonzero numerator means.
#
# This table is the single source of the matrix's row set. `compare_writes`
# records exactly these keys and `print_blindness` prints exactly one row per
# entry, so a counter cannot be added to the comparison without a row
# appearing in the report. That coupling is the fix for a real defect: the
# comparison decomposed every run into six properties and recorded all six,
# while the matrix listed only the first four -- so `SPI_ANYLEN` (write count)
# and `SPI_DRAIN_LATE` (drain window), both caught by the comparison, were
# reported as "no property moved", i.e. as a coverage hole in the bench rather
# than a hole in its own reporting.
#
# Each denominator is the population its own numerator was counted over, never
# a larger one. `compared` is min(sent, seen), because a field can only be
# compared on a write that actually arrived; printing a field count against
# the number of writes SENT is the "37 of 155 writes" shape from CLAUDE.md
# with the two populations swapped.
PROPERTIES = (
    # name       numerator    population    what a nonzero numerator means
    ("flag",     "bad_flag",  "compared",   "compared writes wrong"),
    ("section",  "bad_sec",   "compared",   "compared writes wrong"),
    ("address",  "bad_addr",  "compared",   "compared writes wrong"),
    ("data",     "bad_data",  "compared",   "compared writes wrong"),
    ("count",    "count_off", "count_sent", "writes unaccounted for"),
    ("drain",    "late",      "cyc_seen",   "writes at or after `go`"),
)
# `LAST` keys that are deliberately NOT matrix rows, so the test that pins the
# coupling above can tell a new counter from ordinary bookkeeping: `bad` is an
# aggregate of the four field counters, `total` is a legacy alias of
# `count_sent`, and `count_seen` is the raw arrival count the `count` row is
# derived from.
NON_PROPERTY_KEYS = frozenset({"bad", "total", "count_seen"})


def compare_writes(writes: list, rtl_out: str) -> int:
    """0 identical, 1 differed, 2 did not run. Reports per-property, because
    'the address was truncated', 'the datum was truncated', 'two writes the
    host never sent arrived' and 'every write landed after `go`' are different
    defects with different fixes.

    Records every property's numerator AND the population it was counted over
    in `LAST`, for `print_blindness` -- see `PROPERTIES`.

    Clears `LAST` first, unconditionally. Every `return` below happens before
    the `LAST.update(...)` line, so a call that returns 2 leaves nothing
    behind -- without the clear, a SECOND call in the same process that
    returns 2 (no RTL output this time) would leave the FIRST call's counters
    in place, and `print_blindness` would then print a full, confident matrix
    for a run that delivered nothing. `main()` only calls this once per
    process today, but the precondition `print_blindness` documents -- empty
    `LAST` means nothing was recorded -- has to be true unconditionally, not
    just true for a single call, or the missing-keys refusal below cannot see
    the difference: the keys are all present, they just describe a run that
    already ended."""
    LAST.clear()
    try:
        rows = [l.split() for l in open(rtl_out).read().splitlines() if l.strip()]
    except OSError:
        print(f"verify_ctl: no RTL output at {rtl_out}"); return 2
    got = [tuple(int(t) if t != "x" else None for t in r[:4]) for r in rows]
    cycs = [int(r[4]) for r in rows if len(r) > 4 and r[4] != "x"]
    n = len(writes)
    count_bad = len(got) != n
    if count_bad:
        print(f"verify_ctl: FAIL -- {len(got)} writes reached the port, the host sent {n}")
        if len(got) < n:
            print(f"  {n - len(got)} write(s) never arrived: the link could not carry them at all")
            if not got:
                return 2
    late = [c for c in cycs if c >= GO_CYCLE]
    if late:
        print(f"verify_ctl: FAIL -- {len(late)} write(s) applied at or after cycle {GO_CYCLE} (`go`), "
              f"worst cycle {max(late)}: the datapath had already read its registers")
    bad_f = bad_s = bad_a = bad_d = 0
    first = None
    for i, (want, have) in enumerate(zip(writes, got)):
        wf, ws, wa, wd = int(want[0]), int(want[1]), int(want[2]) & 0xFF, int(want[3]) & 0xFFFFFFFF
        if have is None or None in have:
            bad_f += 1
            if first is None: first = (i, (wf, ws, wa, wd), have)
            continue
        gf, gs, ga, gd = have
        hit = False
        if gf != wf: bad_f += 1; hit = True
        if gs != ws: bad_s += 1; hit = True
        if ga != wa: bad_a += 1; hit = True
        if gd != wd: bad_d += 1; hit = True
        if hit and first is None: first = (i, (wf, ws, wa, wd), have)
    bad = sum(1 for want, have in zip(writes, got)
              if have != (int(want[0]), int(want[1]), int(want[2]) & 0xFF, int(want[3]) & 0xFFFFFFFF))
    # `compared` is the population every per-field numerator above was counted
    # over -- `zip` stops at the shorter of the two -- and is NOT the number of
    # writes sent whenever the link dropped or invented writes.
    compared = min(n, len(got))
    LAST.update(bad=bad, bad_flag=bad_f, bad_sec=bad_s, bad_addr=bad_a, bad_data=bad_d,
                count_off=abs(len(got) - n), count_seen=len(got), count_sent=n,
                late=len(late), cyc_seen=len(cycs), compared=compared, total=n)
    if bad == 0 and not count_bad and not late:
        print(f"verify_ctl: PASS -- all {n} writes reached the register port exactly as sent, "
              f"every one in the drain window (cycles {min(cycs)}..{max(cycs)}, before `go` at {GO_CYCLE})")
        return 0
    if first is None:
        return 1
    i, want, have = first
    print(f"verify_ctl: FAIL -- {bad} of {compared} compared writes corrupted in the link"
          + ("" if compared == n else f" ({n - compared} of the {n} sent never arrived, "
                                      "so no field could be compared on them)"))
    print(f"  wrong flag {bad_f}, wrong section {bad_s}, wrong address {bad_a}, wrong data {bad_d}")
    print(f"  first at write {i}: host sent flag {want[0]} sec {want[1]} addr 0x{want[2]:02X} data 0x{want[3]:X}"
          f" ({want[3].bit_length()} bits)")
    print(f"                 port saw  flag {have[0]} sec {have[1]} addr 0x{have[2]:02X} data 0x{have[3]:X}")
    return 1


def print_blindness(tag: str, simulated: bool = True) -> None:
    """Which of `PROPERTIES` this control moved and which it did not -- the
    same MOVED/BLIND distinction `model/sound_report.py --inject` makes for
    the sound model, extended here to a second, differently-shaped suite
    (link properties rather than named acoustic ones).

    One row per entry in `PROPERTIES`, each against its own population, so no
    property `compare_writes` measures can be left out of the report. Leaving
    two of them out is what made SPI_ANYLEN and SPI_DRAIN_LATE -- each caught
    by one of the omitted rows -- print as "no property moved".

    `simulated` distinguishes an apparatus failure from a DUT failure: pass
    False when `simulate()` itself returned `None` (iverilog/vvp missing,
    compile failure, vvp failure, or a timeout), so `compare_writes` was
    never even called. Without that flag this function had exactly one way
    to render "no data", worded as a claim about the LINK ("the link
    delivered no writes at all") -- which is wrong when the link was never
    exercised in the first place: the correct-instrument-in-a-wrong-state
    shape from CLAUDE.md, reported as a property of the design instead of the
    apparatus.

    Four outcomes are distinct, and none may be dressed as another:

      MOVED/BLIND    measured: the property had a population, and did or did
                     not see the defect.
      REFUSED (row)  a property whose population is zero was never measured
                     at all. "0 of 0" would render as BLIND, i.e. as evidence
                     that the property cannot see this defect, which is a
                     claim no data was taken for.
      REFUSED (sim)  `simulated` is False: the simulator did not produce a
                     run at all, so nothing about the LINK was exercised.
                     This is not the same claim as the next line, even though
                     both look like "no matrix": one says the comparison ran
                     and saw nothing arrive, the other says the comparison
                     never started.
      no matrix      `simulated` is True but `LAST` is empty (`compare_writes`
                     returned 2 before recording anything -- no RTL output,
                     or an empty one), or `LAST` predates this row set. NOTE
                     that a mere write-count MISMATCH does not land here:
                     that run still populates `LAST` and is reported by the
                     `count` row.
    """
    if not simulated:
        print(f"\nverify_ctl: no per-field blindness matrix for '{tag}' -- "
              "REFUSED: the simulator did not run, so the comparison was never attempted "
              "and this says nothing about the link")
        return
    if not LAST:
        print(f"\nverify_ctl: no per-field blindness matrix for '{tag}' -- "
              "the comparison recorded nothing: the link delivered no writes at all")
        return
    missing = sorted({k for _, num, pop, _ in PROPERTIES for k in (num, pop)} - set(LAST))
    if missing:
        print(f"\nverify_ctl: no per-field blindness matrix for '{tag}' -- REFUSED: `LAST` has no "
              f"{', '.join(missing)}, so this run cannot be decomposed into the {len(PROPERTIES)} "
              "properties the matrix reports; a partial matrix is what it exists to prevent")
        return
    n = LAST.get("total", LAST["count_sent"])
    print(f"\nverify_ctl: blindness matrix for '{tag}' -- which of the {len(PROPERTIES)} properties "
          f"moved, of {n} writes sent")
    moved = refused = 0
    for name, num_key, pop_key, what in PROPERTIES:
        count, pop = LAST[num_key], LAST[pop_key]
        if pop == 0:
            refused += 1
            print(f"  REFUSED {name:8s} population 0 -- this property was never measured on this "
                  "run, which is not evidence that it cannot see the defect")
        elif count:
            moved += 1
            print(f"  MOVED   {name:8s} {count} of {pop} {what} -- this property sees the defect")
        else:
            print(f"  BLIND   {name:8s} 0 of {pop} {what} -- this property cannot see this defect")
    if not moved:
        print(f"  NO PROPERTY MOVED for '{tag}'. Either the defect does not change what reaches the "
              "register port, or the coverage has a hole.")
    if refused:
        print(f"  {refused} of {len(PROPERTIES)} properties had no population on this run, so this "
              "matrix is INCOMPLETE -- read those rows as no evidence, not as BLIND.")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--link", choices=("rev2", "dr7rev1"), default="rev2")
    ap.add_argument("--inject", default=None)
    ap.add_argument("--expect-fail", action="store_true")
    ap.add_argument("--outdir", default=os.path.join(HERE, "build"))
    a = ap.parse_args(argv)
    a.outdir = os.path.abspath(a.outdir)
    os.makedirs(a.outdir, exist_ok=True)
    writes = stimulus()
    nd = sum(1 for w in writes if w[1] == SEC_DRUM)
    wide_a = sum(1 for w in writes if w[1] == SEC_DRUM and (w[2] & 0xFF) > 0x7F)
    wide_d = sum(1 for w in writes if (w[3] & 0xFFFFFFFF) > 0xFFFFFF)
    print(f"verify_ctl: {len(writes)} writes from the two models ({len(writes) - nd} voice, {nd} drum); "
          f"{wide_a} need an address above 0x7F, {wide_d} a datum wider than 24 bits")
    write_stream(os.path.join(a.outdir, "ctl_writes.hex"), writes)
    bits = 48 if a.link == "rev2" else 32
    defines = [f"INJECT_BUG_{a.inject}"] if a.inject else []
    print(f"verify_ctl: driving the pins of {'spi_ctl.v' if a.link == 'rev2' else 'stubs/spi_ctl_dr7rev1.v'} "
          f"at {bits} bits per transaction{' with ' + defines[0] if defines else ''}")
    out = simulate(a.link, defines, a.outdir, bits)
    status = 2 if out is None else compare_writes(writes, out)
    if a.inject or a.link != "rev2":
        print_blindness(a.inject or a.link, simulated=out is not None)
    if a.expect_fail:
        tag = a.inject or a.link
        if status == 1:
            print(f"verify_ctl: negative control {tag} CAUGHT (comparison failed as required)"); return 0
        print(f"verify_ctl: NEGATIVE CONTROL NOT CAUGHT (status {status})"); return 1 if status == 0 else 2
    return status


if __name__ == "__main__":
    sys.exit(main())
