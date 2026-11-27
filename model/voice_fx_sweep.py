#!/usr/bin/env python3
"""What the integer oscillators and envelopes need, measured.

  1. ALIASING: inharmonic energy of a sawtooth, naive vs float PolyBLEP vs
     integer PolyBLEP, at several notes. The shared Blackman-Harris estimator
     checks harmonic-guard coverage and measurement headroom on each record.
  2. RECIPROCAL WIDTH: how many bits the note-on reciprocal 1/dt needs before
     the integer PolyBLEP stops losing suppression, and how many bits of phase
     the fraction ph/dt has to keep.
  3. ENVELOPE WIDTH: the dead zone. An exponential release that subtracts a
     fraction of the level each frame truncates that fraction to zero below
     some level; with too few bits that floor is audible. Also the linear
     attack: with too few bits a long attack's per-frame increment is wrong.
  4. CUTOFF ROM: interpolation error of the g table against the float exp.
"""
import math, os, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "audition"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numpy as np
import dsp
import voice_fx as vf
from audio_measure import inharmonic_fraction_db
from dsp import SR


def saw_alias(note, kind, mant_bits=16, recip_bits=16, shape="saw", dur=0.5):
    inc = dsp.phase_inc(dsp.note_hz(note)); f0 = inc * SR / (1 << 24)
    n = int(dur * SR)
    if kind == "naive":
        x = vf.OscFx(shape, blep=False).render(n, inc) / 32768.0
    elif kind == "float":
        x = dsp.osc_bl(shape, dsp.ramp(n, inc), inc)
    else:
        x = vf.OscFx(shape, True, mant_bits, recip_bits).render(n, inc) / 32768.0
    return inharmonic_fraction_db(x, f0, SR)


def _alias_cell(note, kind, **oscillator):
    estimate = saw_alias(note, kind, **oscillator)
    return f"{estimate.value:.1f}" if estimate.ok else f"REFUSED ({estimate.reason})"


def print_aliasing(notes):
    print("ALIASING  (inharmonic energy, dB re total; lower is better)")
    print(f"  {'note':>4} {'f0':>7}  {'naive':>7} {'float BLEP':>11} {'fixed BLEP':>11}  {'square naive':>12} {'square fixed':>12}")
    for note in notes:
        f0 = dsp.note_hz(note)
        print(f"  {note:4} {f0:7.1f}  {_alias_cell(note, 'naive'):>7} {_alias_cell(note, 'float'):>11} "
              f"{_alias_cell(note, 'fixed'):>11}  {_alias_cell(note, 'naive', shape='square'):>12} "
              f"{_alias_cell(note, 'fixed', shape='square'):>12}")


def main():
    print_aliasing((28, 40, 64, 88, 100))

    print("\nRECIPROCAL WIDTH  (fixed BLEP saw, inharmonic dB; mantissa 16 bits)")
    print(f"  {'recip bits':>10}" + "".join(f" {'note '+str(n):>9}" for n in (40, 64, 88, 100)))
    for rb in (4, 6, 8, 10, 12, 14, 16):
        print(f"  {rb:10}" + "".join(f" {_alias_cell(n, 'fixed', recip_bits=rb):>9}" for n in (40, 64, 88, 100)))
    print(f"  {'float':>10}" + "".join(f" {_alias_cell(n, 'float'):>9}" for n in (40, 64, 88, 100)))

    print("\nPHASE FRACTION WIDTH  (bits of ph kept before the multiply; reciprocal 16 bits)")
    print(f"  {'mant bits':>10}" + "".join(f" {'note '+str(n):>9}" for n in (40, 64, 88, 100)))
    for mb in (6, 8, 10, 12, 16):
        print(f"  {mb:10}" + "".join(f" {_alias_cell(n, 'fixed', mant_bits=mb):>9}" for n in (40, 64, 88, 100)))

    print("\nBLEP ARITHMETIC ERROR vs float PolyBLEP  (max |error| in Q1.15 LSB, saw)")
    n = int(0.5 * SR)
    for rb in (8, 12, 16):
        row = []
        for note in (40, 64, 88, 100):
            inc = dsp.phase_inc(dsp.note_hz(note)); ph = dsp.ramp(n, inc)
            ref = dsp.osc_bl("saw", ph, inc) * 32768
            got = vf.OscFx("saw", True, 16, rb).render(n, inc)
            row.append(f"{np.abs(got - ref).max():7.1f}")
        print(f"  recip {rb:2} bits: " + " ".join(row))

    print("\nENVELOPE WIDTH  (release floor where the exponential step truncates to 1 LSB/frame;\n"
          "  attack-time error of a 0.9 s attack; max error vs float above -60 dB, in Q0.15 LSB)")
    print(f"  {'level bits':>10} {'rate bits':>9} {'floor r=0.1s':>13} {'floor r=0.6s':>13} {'attack err':>11} "
          f"{'err r=0.1s':>11} {'err r=0.6s':>11}")
    for eb in (15, 16, 18, 20, 22, 24):
        for rq in (16,) if eb != 24 else (12, 16, 20):
            e1 = vf.AdsrFx(0.005, 0.25, 0.75, 0.1, env_bits=eb, rate_q=rq)
            e2 = vf.AdsrFx(0.9, 1.6, 0.10, 0.6, env_bits=eb, rate_q=rq)
            fl1 = 20 * math.log10(e1.floor_level / (1 << eb))
            fl2 = 20 * math.log10(e2.floor_level / (1 << eb))
            nn = int(3.0 * SR); g = int(2.0 * SR)
            ref1 = dsp.adsr(nn, 0.005, 0.25, 0.75, 0.1, 2.0); got1 = e1.render(nn, g) / 32768.0
            ref2 = dsp.adsr(nn, 0.9, 1.6, 0.10, 0.6, 2.0); got2 = e2.render(nn, g) / 32768.0
            att = np.argmax(got2 >= 32767 / 32768.0) / SR
            m1 = np.abs(got1 - ref1)[ref1 > 1e-3].max() * 32768
            m2 = np.abs(got2 - ref2)[ref2 > 1e-3].max() * 32768
            print(f"  {eb:10} {rq:9} {fl1:10.0f} dB {fl2:10.0f} dB {100*(att-0.9)/0.9:+9.1f} % "
                  f"{m1:11.1f} {m2:11.1f}")

    print("\nRELEASE REACHES ZERO  (24-bit level; frames after gate-off until the level is exactly 0)")
    for r in (0.08, 0.12, 0.3, 0.6, 1.0, 3.0):
        e = vf.AdsrFx(0.005, 0.25, 0.75, r)
        nn = int((0.1 + 6 * r) * SR) + 1
        g = int(0.1 * SR)
        tail = e.render(nn, g)[g:]
        z = int(np.argmax(tail == 0)) if (tail == 0).any() else None
        print(f"  release {r:4.2f} s: rate={e.rate:3} (exact {65536*(1-math.exp(-4/(r*SR))):6.2f}), "
              f"floor {20*math.log10(e.floor_level/2**24):4.0f} dBFS, exactly zero after "
              f"{z/SR if z is not None else float('nan'):.2f} s  (float reaches -60 dB at {r*60/34.7:.2f} s, -90 dB at {r*90/34.7:.2f} s)")

    print("\nCUTOFF ROM  (g interpolation error vs float exp, cutoff 30..21600 Hz)")
    cut = np.arange(30, 21601)
    ex = np.clip(np.round((1 - np.exp(-2 * math.pi * cut / (2 * SR))) * 65536), 1, 65535)
    print(f"  {'entries':>7} {'ROM bits':>8} {'max abs':>8} {'rel @30Hz':>10} {'rel @120Hz':>10} {'rel @1kHz':>10}")
    for bits in (5, 6, 7, 8):
        rom = vf.make_g_rom(bits)
        g = vf.g_from_cut(cut, rom, bits)
        rel = (g - ex) / ex * 100
        at = lambda hz: rel[hz - 30]
        print(f"  {1<<bits:7} {(1<<bits)*16:8} {np.abs(g-ex).max():6.0f}   {at(30):+8.2f} % {at(120):+8.2f} % {at(1000):+8.2f} %")
    return 0


if __name__ == "__main__":
    sys.exit(main())
