#!/usr/bin/env python3
"""What each knob of the eight sounds the discrimination study did not cover
actually moves, read off the machine rather than assumed.

This is the evidence behind `test_discrimination.EXTRA_REF`, its knob counts
and the two new control laws. It exists as a file because a number without the
code that produced it is a claim -- and because one of the two things it found
contradicts a comment in `model/drums_fx.py` that a reader would otherwise
take as settled.

    .venv/bin/python tools/probe_new_voice_knobs.py --refs /tmp/tr808-ref

WHAT IT FOUND (Fischer s/n 103852, 2026-09-18)

1.  TUNING moves f0 on all six tom/conga sounds and leaves tau flat, so the
    LT/HT law generalises to MT, LC, MC and HC unchanged:

        LT  80.0 -> 100.0 Hz    tau 91 -> 86 ms
        MT 123.3 -> 153.3 Hz    tau 59 -> 55 ms
        HT 170.0 -> 213.3 Hz    tau 44 -> 42 ms
        LC 183.3 -> 223.3 Hz    tau 79 -> 74 ms
        MC 260.0 -> 320.0 Hz    tau 40 -> 38 ms
        HC 376.7 -> 466.7 Hz    tau 36 -> 33 ms

2.  The cymbal's SECOND filename code is DECAY and its FIRST is TONE. Three
    independent things agree: the measured tau runs 158 -> 510 ms down the
    second code, the recordist's own file lengths grow 1.50 -> 4.00 s with it
    (he gave the longer settings more room), and the first code leaves the
    file length alone.

3.  CY TONE IS A BALANCE, NOT A DECAY -- and a single fitted tau reads it as
    one. Down the TONE column tau runs 464 -> 196 ms while the file length
    never moves; what is actually changing is the split between the long
    3.45 kHz band and the short 10.5 kHz one (2-5 kHz share 0.762 -> 0.686,
    5-13 kHz 0.180 -> 0.261). This is the fifth instance of the error
    withdrawn from the snare's TONE law on the same day, in a different
    voice, and fitting a tau here would have written a decay into a circuit
    whose decay the knob does not touch.

4.  A CORRECTION TO A COMMENT, NOT TO A MEASUREMENT. `drums_fx.py` labels
    `cy8/CY5025.WAV` as "TONE 5.0, DECAY 5.0"; by the filename convention the
    whole corpus uses (KNOB_CODES "25" -> 2.5) it is DECAY 2.5, and the file
    whose knob is 5.0 is CY5050. The CY_DECAY_T20 table beside that comment
    is self-consistent, so nothing measured moves -- but the same block also
    records that the knob-2.5 file "is the one file of the five whose length
    is shorter than its own decay" and excludes it. That is exactly the
    truncation defect PR #132 fixes (#118): a backward integral over a record
    that ends before the decay does reports the cut. The cymbal DECAY law
    fitted here does not use Schroeder T20 at all -- `measure_tau` regresses
    the log envelope over -3..-27 dB, which needs only 27 dB of record -- and
    reads the 2.5 file at 258.5 ms, in order with both its neighbours.
"""
from __future__ import annotations

import argparse
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "model"))

import test_discrimination as td       # noqa: E402

CODES = ("00", "25", "50", "75", "10")
TOMS = (("LT", "lt8"), ("MT", "mt8"), ("HT", "ht8"),
        ("LC", "lc8"), ("MC", "mc8"), ("HC", "hc8"))


def _load(refs, d, prefix, code):
    import wave
    path = os.path.join(refs, d, f"{prefix}{code}.WAV")
    x, sr = td.read_wav(path)
    with wave.open(path, "rb") as w:
        dur = w.getnframes() / float(w.getframerate())
    return x[td.onset(x):], sr, dur


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    import run_case
    ap.add_argument("--refs", default=str(run_case.configured_refs()))
    a = ap.parse_args(argv)

    print("== TUNING: does it move f0, and does it leave tau alone? ==")
    print(f"  {'':4s} {'knob':>6s}" + "".join(f"{k:>9s}" for k in CODES))
    for v, d in TOMS:
        f0 = [td.measure_f0(*_load(a.refs, d, v, c)[:2], td.TUNING_FMAX[v]) for c in CODES]
        tau = [1e3 * td.measure_tau(*_load(a.refs, d, v, c)[:2]) for c in CODES]
        print(f"  {v:4s} {'f0 Hz':>6s}" + "".join(f"{x:9.1f}" for x in f0))
        print(f"  {'':4s} {'tau ms':>6s}" + "".join(f"{x:9.1f}" for x in tau))
        mono = all(f0[i] < f0[i + 1] for i in range(4))
        span = max(tau) / min(tau)
        print(f"       -> f0 {'monotone' if mono else 'NOT MONOTONE'}, "
              f"tau spread {span:.2f}x over the whole knob")

    print("\n== CY: which code is DECAY? ==")
    for label, mk in (("second code swept (TONE held at 5.0)", lambda c: "50" + c),
                      ("FIRST code swept (second held at 5.0)", lambda c: c + "50")):
        print(f"  {label}")
        for c in CODES:
            x, sr, dur = _load(a.refs, "cy8", "CY", mk(c))
            lo = td.band_share(x, sr, 2000.0, 5000.0)
            hi = td.band_share(x, sr, 5000.0, 13000.0)
            print(f"    CY{mk(c)}  knob {td.KNOB_CODES[c]:4.1f}  "
                  f"tau {1e3 * td.measure_tau(x, sr):7.1f} ms   file {dur:4.2f} s   "
                  f"2-5k {lo:.3f}  5-13k {hi:.3f}  hi/lo {hi / max(lo, 1e-9):.3f}")
    print("\n  A knob that moves the file length the recordist chose is the decay knob;")
    print("  a knob that moves the band split and not the length is a tone knob, even")
    print("  when a single fitted tau moves with it.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
