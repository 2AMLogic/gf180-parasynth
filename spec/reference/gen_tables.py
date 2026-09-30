#!/usr/bin/env python3
"""Regenerate, FROM THE COMMITTED MODEL, every lookup table that
spec/NUMERIC-CONTRACT.md pins by SHA-256, and check that the contract and the
committed table images are still the model's.

The model is the specification; this script is what stops the prose from
drifting away from it. It does three things:

  1. Evaluates the four tables exactly as model/voice_fx.py and model/fixed.py
     build them (same functions, no re-derivation here):
        NOTE_INC   128 x 24-bit    dsp.phase_inc(dsp.note_hz(n))
        SINE_Q256  256 x Q1.15     dsp._QUARTER (midpoint-sampled quarter wave)
        TANH16      16 x Q1.15     fixed.LadderFx(tanh_entries=16, interp=True).tbl
        G_ROM128   129 x Q0.16     voice_fx.make_g_rom()  (128 entries + guard)
        EXP_ROM65   65 x Q0.15     voice_fx.make_exp_rom() (64 entries + guard, DR 0012)
        K_ROM32     33 x Q1.15     voice_fx.make_k_rom()  (32 entries + guard, DR 0006)
        NOISE64     64 x Q1.15     drums_fx.lfsr_frame from LFSR_SEED (the first 64 noise words, DR 0008)
        KIT808      (addr, value)  drums_fx.kit_808()  (the reference kit, informative but pinned)
     plus two derived images the contract also states hashes for:
        SINE_FULL1024   the 1024-entry expansion via voice_fx.sine_fx
        TANH16_ROM      the 17-word ROM image ladder_dp.v reads (TANH16 + the guard word)
  2. Writes each table as one hex word per line to spec/reference/tables/, and
     rewrites the appendix block of the contract between the two marker lines
     <!-- BEGIN GENERATED APPENDICES --> / <!-- END GENERATED APPENDICES -->.
  3. With --check, writes nothing: exits 1 if any committed hex image, the
     appendix block, rtl-sketch/tanh16.hex, or any hash the contract states
     differs from what the model produces right now.

    .venv/bin/python spec/reference/gen_tables.py           # regenerate
    .venv/bin/python spec/reference/gen_tables.py --check   # CI freshness

Hash encoding, the same as gf180-polysynth's contract: the decimal values
joined by commas with no spaces, SHA-256, lower-case hex.
"""
from __future__ import annotations
import hashlib, os, re, sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.join(ROOT, "model"))
sys.path.insert(0, os.path.join(ROOT, "audition"))
import numpy as np                      # noqa: E402
import dsp, fixed, voice_fx as vf       # noqa: E402
import drums_fx as dx                   # noqa: E402

CONTRACT = os.path.join(ROOT, "spec", "NUMERIC-CONTRACT.md")
TABLE_DIR = os.path.join(HERE, "tables")
RTL_TANH_HEX = os.path.join(ROOT, "rtl-sketch", "tanh16.hex")
BEGIN = "<!-- BEGIN GENERATED APPENDICES -->"
END = "<!-- END GENERATED APPENDICES -->"


def sha(vals) -> str:
    return hashlib.sha256(",".join(str(int(v)) for v in vals).encode()).hexdigest()


# ---- the tables, evaluated by the model's own code ---------------------------
def note_inc() -> list[int]:
    return [dsp.phase_inc(dsp.note_hz(n)) for n in range(128)]


def sine_q256() -> list[int]:
    return [int(v) for v in dsp._QUARTER]


def sine_full1024() -> list[int]:
    ph = np.arange(1024, dtype=np.int64) << (dsp.PHASE_BITS - 10)
    return [int(v) for v in vf.sine_fx(ph)]


def tanh16() -> list[int]:
    return list(fixed.LadderFx(**vf.LADDER_CFG).tbl)


def tanh16_rom() -> list[int]:
    return tanh16() + [fixed.TANH_GUARD]   # the guard word: tanh(4), not full scale


def g_rom128() -> list[int]:
    return [int(v) for v in vf.make_g_rom(vf.GROM_BITS, vf.LADDER_CFG.get("oversample", 2))]


def k_rom32() -> list[int]:
    return [int(v) for v in vf.make_k_rom(vf.KROM_BITS, vf.GROM_BITS, vf.LADDER_CFG.get("oversample", 2))]


def exp_rom65() -> list[int]:
    """65 x Q0.15, the modulation path's 2^x table (contract 6.9, DR 0012):
    (2^(i/64) - 1) * 32768, edge-sampled over one octave with the guard entry.
    Stored biased by -32768 so the top entry (2.0) still fits in 16 bits."""
    return [int(v) for v in vf.make_exp_rom()]


def noise64() -> list[int]:
    """The first 64 noise words from reset: the LFSR of contract 15.4 run by
    the model (drums_fx.lfsr_frame from LFSR_SEED), signed Q1.15."""
    s, out = dx.LFSR_SEED, []
    for _ in range(64):
        s, w = dx.lfsr_frame(s)
        out.append(w)
    return out


def kit808() -> list[tuple[int, int]]:
    """The reference kit, Appendix G: (address, value) writes from drums_fx.kit_808()."""
    return [(int(a), int(v)) for a, v in dx.kit_808()]


def kit808_words() -> list[int]:
    """One 40-bit word per write, address in the top byte: the hex image."""
    return [(a << 32) | v for a, v in kit808()]


# name, values, hex digits per word, signed?, hex file (None = derived only)
def tables():
    return [
        ("NOTE_INC", note_inc(), 6, False, "note_inc.hex"),
        ("SINE_Q256", sine_q256(), 4, True, "sine_q256.hex"),
        ("SINE_FULL1024", sine_full1024(), 4, True, None),
        ("TANH16", tanh16(), 4, True, "tanh16.hex"),
        ("TANH16_ROM", tanh16_rom(), 4, True, None),
        ("G_ROM128", g_rom128(), 4, False, "g_rom128.hex"),
        ("K_ROM32", k_rom32(), 4, False, "k_rom32.hex"),
        ("EXP_ROM65", exp_rom65(), 4, False, "exp_rom65.hex"),
        ("NOISE64", noise64(), 4, True, "noise64.hex"),
        ("KIT808", kit808_words(), 10, False, "kit808.hex"),
    ]


def hex_image(vals, digits, signed) -> str:
    mask = (1 << (4 * digits)) - 1
    return "".join(f"{(v & mask) if signed else v:0{digits}x}\n" for v in vals)


# ---- the appendix text ------------------------------------------------------
def _rows(vals, per_row, fmt=lambda v: str(v)):
    out = []
    for i in range(0, len(vals), per_row):
        cells = [fmt(v) for v in vals[i:i + per_row]] + [""] * (per_row - len(vals[i:i + per_row]))
        out.append(f"| {i} | " + " | ".join(cells) + " |")
    return "\n".join(out)


def appendix() -> str:
    ni, sq, sf, th, thr, gr, kr = (note_inc(), sine_q256(), sine_full1024(),
                                   tanh16(), tanh16_rom(), g_rom128(), k_rom32())
    s = []
    s.append("### Appendix A -- NOTE_INC: MIDI note number -> 24-bit phase increment\n")
    s.append("Normative. `NOTE_INC[n] = round(440 * 2^((n-69)/12) * 2^24 / 48000)`, evaluated by "
             "`dsp.phase_inc(dsp.note_hz(n))`. Nominal frequency for reference only. "
             "Two notes per row.\n")
    s.append("| note | inc (dec) | inc (hex) | nominal Hz | | note | inc (dec) | inc (hex) | nominal Hz |")
    s.append("|---:|---:|---:|---:|---|---:|---:|---:|---:|")
    for n in range(64):
        a, b = n, n + 64
        s.append(f"| {a} | {ni[a]} | 0x{ni[a]:06X} | {dsp.note_hz(a):.3f} | "
                 f"| {b} | {ni[b]} | 0x{ni[b]:06X} | {dsp.note_hz(b):.3f} |")
    s.append(f"\nSHA-256 of the 128 decimal values joined by commas (no spaces): `{sha(ni)}`\n")
    s.append("### Appendix B -- SINE_Q256: quarter-wave sine table, i = 0..255\n")
    s.append("Normative. `SINE_Q256[i] = round(32767 * sin(pi/2 * (i + 0.5) / 256))` -- MIDPOINT "
             "sampled, 256 entries, no interpolation (`dsp._QUARTER`). The full 1024-entry table is "
             "derived by the symmetry rules in section 6.5. Eight entries per row; the first column is "
             "the index of the first entry in the row.\n")
    s.append("| i | +0 | +1 | +2 | +3 | +4 | +5 | +6 | +7 |")
    s.append("|---:|---:|---:|---:|---:|---:|---:|---:|---:|")
    s.append(_rows(sq, 8))
    s.append(f"\nSHA-256 of the 256 decimal values joined by commas: `{sha(sq)}`  ")
    s.append(f"SHA-256 of the derived 1024-entry full table (`voice_fx.sine_fx` at phases `i << 14`), "
             f"same encoding: `{sha(sf)}`\n")
    s.append("### Appendix C -- TANH16: the ladder's tanh table, i = 0..15\n")
    s.append("Normative. `TANH16[i] = round(tanh(i / 16 * 4.0) * 32767)` -- EDGE sampled over [0, 4), "
             "Q1.15, read with linear interpolation (section 11.3). The interpolation's top word, "
             "used above entry 15 AND returned by the clamp for |v| >= 4.0, is `fixed.TANH_GUARD` = "
             "32767 and is NOT tanh(4.0) (which would round to 32745). That leaves the top bin "
             "[3.75, 4) up to 6.5e-4 high. It is a known wrong constant and DR 0013 records BOTH the "
             "measurement of what correcting it buys -- the top bin twelve times more accurate, and "
             "no movement at all in the harmonic fingerprint at self-oscillation, because the "
             "16-entry table's own worst error is nine times larger -- and why it is not corrected "
             "here: `rtl-sketch/drum_dp.v` reads this same image with its own hardcoded clamp, so "
             "the word cannot move without a matching change in the drum section.\n")
    s.append("| i | +0 | +1 | +2 | +3 | +4 | +5 | +6 | +7 |")
    s.append("|---:|---:|---:|---:|---:|---:|---:|---:|---:|")
    s.append(_rows(th, 8))
    s.append(f"\nSHA-256 of the 16 decimal values joined by commas: `{sha(th)}`  ")
    s.append(f"SHA-256 of the 17-word ROM image (`TANH16` followed by the guard word), which is exactly "
             f"`rtl-sketch/tanh16.hex`: `{sha(thr)}`\n")
    s.append("### Appendix D -- G_ROM128: cutoff (Hz) -> ladder coefficient g, i = 0..128\n")
    s.append("Normative. `G_ROM128[i] = clip(round((1 - exp(-2*pi * (256*i) / 96000)) * 65536), 0, 65535)` "
             "-- EDGE sampled every 256 Hz at the ladder's 2x-oversampled rate, Q0.16, 128 entries plus "
             "entry 128 as the interpolation guard (`voice_fx.make_g_rom`). Entries 0..85 are reachable "
             "through the cutoff clamp of section 10; entries 86..128 are part of the table but never "
             "read. Eight entries per row.\n")
    s.append("| i | +0 | +1 | +2 | +3 | +4 | +5 | +6 | +7 |")
    s.append("|---:|---:|---:|---:|---:|---:|---:|---:|---:|")
    s.append(_rows(gr, 8))
    s.append(f"\nSHA-256 of the 129 decimal values joined by commas: `{sha(gr)}`\n")
    s.append("### Appendix E -- K_ROM32: cutoff (Hz) -> resonance compensation, i = 0..32\n")
    s.append("Normative (DR 0006). `K_ROM32[i] = round(k_onset(clamp(1024*i, 30, 21600)) / 4 * 32768)` -- "
             "unsigned Q1.15, EDGE sampled every 1024 Hz, 32 entries plus entry 32 as the interpolation "
             "guard (`voice_fx.make_k_rom`). `k_onset` is the small-signal onset of self-oscillation of the "
             "linearised loop, section 10.2 (`voice_fx.k_onset`); 32768 means k = 4 res, the uncompensated "
             "filter. Entries 0..21 are reachable through the cutoff clamp of section 10; entries 22..32 are "
             "evaluated at the clamp and never read. Eight entries per row.\n")
    s.append("| i | +0 | +1 | +2 | +3 | +4 | +5 | +6 | +7 |")
    s.append("|---:|---:|---:|---:|---:|---:|---:|---:|---:|")
    s.append(_rows(kr, 8))
    s.append(f"\nSHA-256 of the 33 decimal values joined by commas: `{sha(kr)}`\n")
    er = exp_rom65()
    s.append("### Appendix H -- EXP_ROM65: 2^x for the modulation path, i = 0..64\n")
    s.append("Normative (DR 0012). `EXP_ROM65[i] = round(2^(i/64) * 32768) - 32768` -- the modulation "
             "path's exponential, EDGE sampled over ONE octave, 64 entries plus entry 64 as the "
             "interpolation guard (`voice_fx.make_exp_rom`). Stored biased by -32768 so that the top "
             "entry (2.0 in Q1.15, 65536) still fits in 16 bits; `voice_fx.exp2_q` adds it back, reads "
             "the table on the top 6 bits of the Q3.12 octave word's fraction and interpolates on the "
             "low 6, and turns the integer part into a right shift of 12..19 places. Worst relative "
             "error over the octave 3.57e-5, which is 0.062 cents. Eight entries per row.\n")
    s.append("| i | +0 | +1 | +2 | +3 | +4 | +5 | +6 | +7 |")
    s.append("|---:|---:|---:|---:|---:|---:|---:|---:|---:|")
    s.append(_rows(er, 8))
    s.append(f"\nSHA-256 of the 65 decimal values joined by commas: `{sha(er)}`\n")
    nz, kit = noise64(), kit808()
    s.append("### Appendix F -- NOISE64: the first 64 noise words from reset\n")
    s.append("Normative (DR 0008), derived. The drum section's noise source (section 15.4) is a 31-bit LFSR, "
             "`s <- (s << 1) | (s[30] xor s[15] xor s[17] xor s[19])` -- the recurrence "
             "`b[n] = b[n-31] + b[n-16] + b[n-18] + b[n-20]` over GF(2), characteristic polynomial "
             "x^31 + x^15 + x^13 + x^11 + 1, primitive, period 2^31 - 1 bits -- seeded with 1 at reset and "
             "stepped 16 times per frame; the noise word of a frame is the 16 bits shifted in, oldest first, "
             "read as signed Q1.15 (`drums_fx.lfsr_frame`). Frame 0's word is 1: the seed's bit reaches the "
             "tap at bit 15 on the frame's last step. Eight words per row; the first column is the frame.\n")
    s.append("| frame | +0 | +1 | +2 | +3 | +4 | +5 | +6 | +7 |")
    s.append("|---:|---:|---:|---:|---:|---:|---:|---:|---:|")
    s.append(_rows(nz, 8))
    s.append(f"\nSHA-256 of the 64 decimal values joined by commas: `{sha(nz)}`\n")
    s.append("### Appendix G -- KIT808: the reference kit as register writes\n")
    s.append("Informative, pinned so that the renders and the RTL bench are reproducible: the write list "
             "`drums_fx.kit_808()` produces (section 15.7), address and value per row, in write order; the "
             "register map is section 15.1. Every number is docs/tr808-reference.md's where it gives one; "
             "the levels are the balance of `drums_fx_render.py --balance`; what was chosen rather than "
             "sourced is marked in `kit_808`'s comments and in 15.7.\n")
    s.append("| addr | value | register | | addr | value | register |")
    s.append("|---:|---:|---|---|---:|---:|---|")
    half = (len(kit) + 1) // 2
    for i in range(half):
        cells = []
        for j in (i, i + half):
            if j < len(kit):
                a_, v_ = kit[j]
                cells.append(f"| 0x{a_:02X} | 0x{v_:X} | {_reg_name(a_)} ")
            else:
                cells.append("| | | ")
        # the spacer column between the two halves, as Appendix A's rows have:
        # without it every row is one cell short of the header and the
        # right-hand triple renders shifted a column left
        s.append(cells[0] + "| " + cells[1] + "|")
    s.append(f"\nSHA-256 of the {len(kit)} decimal words `address << 32 | value`, joined by commas, which is "
             f"`spec/reference/tables/kit808.hex` read as decimal: `{sha(kit808_words())}`")
    return "\n".join(s) + "\n"


def _reg_name(a: int) -> str:
    if a == dx.A_STOPS: return "STOPS"
    if dx.A_ACCENT <= a < dx.A_ACCENT + 8: return f"ACCENT[{a - dx.A_ACCENT}]"
    if dx.A_OSC <= a < dx.A_OSC + 6: return f"OSC_INC[{a - dx.A_OSC}]"
    if dx.A_ENV <= a < dx.A_ENV + 48:
        e, f = divmod(a - dx.A_ENV, 4); return f"ENV_{('CTL', 'PEAK', 'RATE', 'FRATE')[f]}[{e}]"
    if dx.A_PATH <= a < dx.A_PATH + 16: return f"PATH[{a - dx.A_PATH}]"
    if dx.A_MODE <= a < dx.A_MODE + 48:
        m, f = divmod(a - dx.A_MODE, 4); return f"MODE_{('A1', 'A2', 'AMP', 'NUM')[f]}[{m}]"
    return "?"


# ---- write / check ----------------------------------------------------------
def _contract_parts(text: str):
    a, b = text.find(BEGIN), text.find(END)
    if a < 0 or b < 0 or b < a:
        raise SystemExit(f"gen_tables: {CONTRACT} lacks the appendix markers")
    return text[:a + len(BEGIN)] + "\n", text[b:]


def write() -> int:
    os.makedirs(TABLE_DIR, exist_ok=True)
    for name, vals, digits, signed, fn in tables():
        print(f"{name:14} {len(vals):4} entries  sha256 {sha(vals)}")
        if fn:
            with open(os.path.join(TABLE_DIR, fn), "w") as fh:
                fh.write(hex_image(vals, digits, signed))
    # rtl-sketch/tanh16.hex is the 17-word ROM IMAGE (the table plus its guard
    # word), not the 16-word table -- `check()` has always verified it and
    # nothing wrote it, which is how its guard word and the model's could
    # differ. It is written here now.
    open(RTL_TANH_HEX, "w").write(hex_image(tanh16_rom(), 4, True))
    text = open(CONTRACT).read()
    head, tail = _contract_parts(text)
    new = head + "\n" + appendix() + "\n" + tail
    if new != text:
        open(CONTRACT, "w").write(new)
        print(f"rewrote the appendix block of {os.path.relpath(CONTRACT, ROOT)}")
    else:
        print("contract appendices already current")
    return 0


def check() -> int:
    bad = 0
    def fail(msg):
        nonlocal bad
        bad += 1
        print("FAIL:", msg)
    text = open(CONTRACT).read() if os.path.exists(CONTRACT) else ""
    if not text:
        fail(f"{CONTRACT} missing")
    for name, vals, digits, signed, fn in tables():
        h = sha(vals)
        if h not in text:
            fail(f"contract does not state the model's {name} hash {h}")
        if fn:
            p = os.path.join(TABLE_DIR, fn)
            have = open(p).read() if os.path.exists(p) else None
            if have != hex_image(vals, digits, signed):
                fail(f"{os.path.relpath(p, ROOT)} differs from the model's {name} (stale or missing)")
    if text:
        head, tail = _contract_parts(text)
        want = head + "\n" + appendix() + "\n" + tail
        if want != text:
            fail("the contract's appendix block differs from what the model generates; "
                 "run gen_tables.py and bump the revision")
    rtl = open(RTL_TANH_HEX).read() if os.path.exists(RTL_TANH_HEX) else None
    if rtl != hex_image(tanh16_rom(), 4, True):
        fail(f"{os.path.relpath(RTL_TANH_HEX, ROOT)} is not the model's 17-word tanh ROM image")
    if bad:
        print(f"gen_tables: {bad} problem(s)")
        return 1
    print("gen_tables: every table image and hash matches the model")
    return 0


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if "--check" in argv:
        return check()
    if "--print-appendix" in argv:
        sys.stdout.write(appendix())
        return 0
    return write()


if __name__ == "__main__":
    sys.exit(main())
