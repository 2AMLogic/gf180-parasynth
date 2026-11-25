#!/usr/bin/env python3
"""Render the monosynth patches with the FIXED-POINT filter, beside the float
version, so the quantisation can be judged by ear rather than in decibels.

Scope: this renders the fixed LADDER behind the float oscillators and
envelopes, quantised to Q1.15 at the filter input -- the configuration the
filter was sized in. The fully integer voice is `voice_fx_render.py`; this
script is kept because it isolates the filter's own share of the difference.
"""
import sys, wave
from pathlib import Path
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "audition"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numpy as np
import dsp, engines, patches, fixed
from dsp import SR, to_wav16

OUT = Path(__file__).parent / "audio" / "fixed"


def write(path, x):
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(SR)
        w.writeframes(to_wav16(x).tobytes())


def fx_ladder_factory(**cfg):
    """Match dsp.ladder's fresh state for each audition note."""

    def f(x, cutoff_hz, res, drive=1.0, oversample=2, tanh_impl=None, **_):
        lad = fixed.LadderFx(**cfg)
        r = float(np.mean(res)) if np.ndim(res) else float(res)
        q = lad.process(fixed.f2q15(np.clip(x, -1.0, 1.0)), np.asarray(cutoff_hz, float), r, drive)
        return fixed.q15f(q)
    return f


def main():
    gap = np.zeros(int(0.35 * SR))
    cfg = dict(state_bits=24, state_q=20, tanh_entries=16, interp=True)
    print("fixed-point config:", cfg)
    sheet = []
    real = dsp.ladder
    for name, seq, total in patches.MONO:
        # engines.py binds `ladder` at import, so this is the name to replace.
        engines.ladder = real
        a = engines.render_mono(seq, total)
        engines.ladder = fx_ladder_factory(**cfg)
        b = engines.render_mono(seq, total)
        engines.ladder = real
        write(OUT / f"{name}-float.wav", a)
        write(OUT / f"{name}-fixed.wav", b)
        m = min(len(a), len(b))
        an = a[:m] / max(np.sqrt((a[:m] ** 2).mean()), 1e-12)
        bn = b[:m] / max(np.sqrt((b[:m] ** 2).mean()), 1e-12)
        import math
        d = 20 * math.log10(max(np.sqrt(((an - bn) ** 2).mean()), 1e-12))
        print(f"  {name:24} float/fixed difference {d:6.1f} dB")
        sheet += [a, gap, b, gap]
    write(OUT / "00-float-vs-fixed.wav", np.concatenate(sheet))
    print(f"\nwrote {OUT}/00-float-vs-fixed.wav  (float, fixed, float, fixed, ...)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
