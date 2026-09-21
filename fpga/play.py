#!/usr/bin/env python3
"""fpga/play.py -- PLAY IT. The instrument, driven the way the board will be.

    .venv/bin/python fpga/play.py --demo                  # no keyboard attached
    .venv/bin/python fpga/play.py --fixture bar808
    .venv/bin/python fpga/play.py --keys 45,48,52,55 --cutoff 1200 --resonance 0.9 \
                                  --decay 8 --pattern basic

Every note, hit and knob turn here becomes a 48-bit DR 0007 transaction and is
applied at the frame the LINK puts it in -- `spi_host.MusicHost.schedule()`,
the same schedule `fpga/verify_fixture.py` proved bit-exact at the pins of
synth_top.v over 8 378 I2S periods. The audio comes from
model/synth_top_model.py rather than from iverilog only because iverilog takes
45 seconds for 175 ms of sound; the WRITES and their FRAMES are identical, and
that equivalence is the thing the bench checks.

This is the difference #81 is about. `audition/play.py` drives the
floating-point engine from early in the project by poking its state. This
drives the verified integer chip through its control port, and it cannot
produce a sound the chip would not.

Every run also prints what it cost the link, because on this instrument that
is a real constraint: a transaction is 24.3 us at the contract's fastest and a
frame is 20.8 us, so one write per frame is the ceiling.
"""
from __future__ import annotations

import argparse
import os
import sys
import wave

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

import numpy as np                                   # noqa: E402
import spi_host as sh                                # noqa: E402
import drums_fx as dx                                # noqa: E402
import voice_fx as vf                                # noqa: E402
import synth_top_model as stm                        # noqa: E402
import fixtures                                      # noqa: E402
import selected_preset                               # noqa: E402

LINKS = {"bench": sh.BENCH, "max": sh.CONTRACT}
PATTERNS = {
    "basic": {"BD": "X...x...X...x...", "SD": "....X.......X...",
              "CH": "x.x.x.x.x.x.x.x.", "OH": "..x.......x....."},
    "808":   dx.PATTERN_808,
    "toms":  {"BD": "X.......X.......", "LT": "....x.......x...",
              "MT": "......x.......x.", "HT": "..x.......x....."},
    "none":  {},
}


def write_wav(path: str, x: np.ndarray, sr: int = sh.SR):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with wave.open(path, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(sr)
        w.writeframes(np.asarray(x, dtype="<i2").tobytes())


def build(a) -> tuple:
    if a.demo:
        return fixtures.demo(bars=a.bars, bpm=a.bpm)
    if a.fixture:
        return fixtures.FIXTURES[a.fixture]()
    patch = (selected_preset.definition(a.preset)["registers"] if a.preset else
             vf.VoiceFx.patch_regs(cutoff=(a.cutoff, max(a.cutoff * 4, a.cutoff + 200)),
                                   q=a.resonance))
    host = sh.MusicHost(patch=patch)
    host.load(0)
    step = int(round(60.0 / a.bpm / 4.0 * sh.SR))
    start = 400
    if PATTERNS[a.pattern]:
        host.hits(dx.pattern_hits(PATTERNS[a.pattern], bpm=a.bpm, bars=a.bars,
                                  start_s=start / sh.SR))
    notes = [int(n) for n in a.keys.split(",") if n.strip()] if a.keys else []
    ev = []
    for i, nt in enumerate(notes * a.bars):
        f = start + i * 2 * step
        ev += [(f, "on", nt), (f + int(step * 1.7), "off", nt)]
    if ev:
        host.keys(ev)
    if a.decay is not None:
        host.knob(start // 2, "decay", a.decay)
    n = max(w.frame for w in host.w) + fixtures.TOM_DROP_FRAMES + 2000
    if a.preset:
        n = max(n, max(w.frame for w in host.w) + 3 * sh.SR)
    return host, n, fixtures._coverage(host)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--demo", action="store_true",
                    help="play with no keyboard attached: pattern, bass line and knob sweeps")
    ap.add_argument("--fixture", default=None, choices=sorted(fixtures.FIXTURES))
    ap.add_argument("--preset", choices=selected_preset.NAMES,
                    help="measured candidate settings; requires OSC2X=1 FILTER2X=1")
    ap.add_argument("--pattern", default="basic", choices=sorted(PATTERNS))
    ap.add_argument("--keys", default="", help="comma-separated MIDI notes, played in order")
    ap.add_argument("--cutoff", type=float, default=600.0, help="Hz")
    ap.add_argument("--resonance", type=float, default=0.7, help="0..1.9")
    ap.add_argument("--decay", type=float, default=None, help="BD DECAY knob, 0..10")
    ap.add_argument("--bpm", type=float, default=118.0)
    ap.add_argument("--bars", type=int, default=2)
    ap.add_argument("--link", default="max", choices=sorted(LINKS))
    ap.add_argument("--out", default=os.path.join(ROOT, "build", "play.wav"))
    a = ap.parse_args(argv)
    if a.preset and (a.demo or a.fixture):
        ap.error("--preset requires --keys; demo and fixture carry their own patch")
    link = LINKS[a.link]

    host, n, cover = build(a)
    sched = host.schedule(link)
    st = sh.check(sched)
    if st["conflicts"]:
        p = st["conflicts"][0]
        print(f"play: REFUSED -- the host cannot deliver its own schedule: "
              f"{len(st['conflicts'])} musical instant(s) late, first {p.w.tag} "
              f"frame {p.w.frame} -> {p.land}")
        return 2

    print(f"play: {len(sched)} transactions, {len(sched) * sh.TX_BYTES} bytes over "
          f"{n / sh.SR:.2f} s -- {cover['strikes']} strikes, {cover['gates']} gate/trigger "
          f"events, {cover['knobs']} knob turns")
    print(f"play: link {link.name}: one write every {link.tx_period_ps / sh.FRAME_PS:.3f} "
          f"frames ({link.tx_period_ps / 1e6:.2f} us), {100 * len(sched) * link.tx_period_ps / (n * sh.FRAME_PS):.2f} % busy")
    print(f"play: the timed sequences of 15.7.1: {cover['bd_hot'] // 2} BD attack window(s), "
          f"{cover['tom_bend'] // (2 * (dx.TOM_DROP_STEPS + 1))} tom pitch drop(s) -- "
          f"{cover['bd_attack'] + cover['tom_bend']} of the transactions above")
    if st["anchor_jitter"]:
        print(f"play: {st['anchor_jitter']} musical instant(s) shared a frame and were "
              f"quantised, by at most {st['anchor_jitter_us']:.1f} us "
              f"(one transaction is the floor)")

    x = stm.SynthTopModel(oversample_2x=bool(a.preset), filter_2x=bool(a.preset)).run(
        sh.model_writes(sched), n)["sample"].astype(np.int16)
    write_wav(a.out, x)
    peak = int(np.abs(x.astype(np.int64)).max())
    clip = int(np.sum(np.abs(x.astype(np.int64)) >= 32767))
    print(f"play: {a.out} -- {len(x) / sh.SR:.2f} s, peak {peak} of 32768 "
          f"({peak / 32768:.3f} x FS), {clip} samples at the rail")
    print("play: every sample above came out of the register port. Nothing was poked.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
