#!/usr/bin/env python3
"""Qualify the DC-blocker DECAY preservation gate, in closed form.

    python3 tools/probes/dc_t20_gate_qualification.py
    python3 -m pytest tools/probes/test_dc_t20_gate_qualification.py -q

THE SECOND GATE WITH THE SAME DEFECT AS THE FIRST
-------------------------------------------------
`dc_centroid_gate_qualification.py` showed that a GLOBAL spectral centroid
cannot be a preservation gate for a DC blocker: removing sub-20 Hz energy
raises it mechanically, so the gate reads the candidate's intended effect as a
failure. That was found, qualified and repaired.

`t20_ms` is the same shape of defect and it was not. It is a Schroeder backward
ENERGY integral over the whole clip, with no band limit, so a sub-20 Hz
pedestal contributes to it at every instant -- and contributes MOST where the
gate reads, because the backward integral late in the clip is small and the
pedestal's share of it is therefore large. Remove the pedestal and the -20 dB
crossing moves EARLIER, for a reason that has nothing to do with the voice's
audible decay.

That is the whole of the cymbal's recorded preservation failure. At K = 10 the
candidate reports `t20_ms_pct` -6.96 % against a 3.00 % allowance while every
other gated property is inside its allowance; the same -6.96 % appears at
K = 9, 10, 11 and 12, which is itself the tell -- a real decay change would
track the corner, and this does not.

WHAT THIS FILE ESTABLISHES, WITHOUT THE DRUM MODEL
--------------------------------------------------
A signal whose decay is known BY CONSTRUCTION: a 2 kHz tone with an
exponential envelope, for which T20 = tau * ln(100) exactly (the Schroeder
integral of e^(-2t/tau) is itself an exponential, so 10*log10 of it is linear
in t at -20/ln(100) dB per tau). Add a sub-20 Hz pedestal of a realistic size
-- 4 % of clip energy, the cymbal's own DC share -- and ask each candidate
estimator for the decay it can see.

`case()` reports four numbers and `main()` refuses unless all four hold:

  1. the global T20 of the contaminated signal is INFLATED well past the truth
  2. the >= 20 Hz T20 of the contaminated signal recovers the truth
  3. on a pedestal-free control the two agree (the repair is a no-op there)
  4. on a genuinely 10 % faster decay the >= 20 Hz T20 still REGISTERS it
     -- the repair must not be a gate that cannot fail

(4) is the control that matters. A preservation gate loosened until the
candidate passes is not a repair, it is a goalpost; the way to tell the
difference is to feed the repaired gate a real regression and check that it
still goes red. The 20 Hz edge is not a new number chosen here either: it is
`dc_blocker.SUB20`'s edge, `CENTROID_FLOOR_HZ`, the `body_20_700_db` band edge
and `test_discrimination.HPF_HZ`.

THE BAND LIMIT IS A BRICK WALL IN THE FFT, NOT A FILTER. Zeroing the bins
below 20 Hz and inverting is zero-phase and has no state, so it cannot
contribute a settling tail of its own to a measurement of settling. A causal
high-pass used as the analysis instrument here would answer with its own
transient -- the exact confusion this file exists to remove.
"""
from __future__ import annotations

import argparse
import json
import math

import numpy as np

SR = 48_000
CLIP_S = 0.60
TONE_HZ = 2_000.0
BAND_FLOOR_HZ = 20.0              # the audible edge; dc_blocker.CENTROID_FLOOR_HZ
TRUE_T20_MS = 200.0               # the decay built into the carrier
PEDESTAL_HZ = 0.0                 # a standing offset, the cymbal's own case
PEDESTAL_ENERGY_FRAC = 0.04       # ~ the cymbal's measured sub-20 Hz share
T20_FRAME_MS = 2.0                # dc_blocker.T20_FRAME_MS, so the two agree
FASTER_DECAY_FRAC = 0.10          # the injected real regression for control (4)


def t20_ms(x, frame_ms: float = T20_FRAME_MS) -> float:
    """`dc_blocker.t20_ms`, duplicated deliberately.

    This file must be able to condemn the estimator it is qualifying, so it
    cannot import it: a shared helper that was itself wrong would make the
    qualification agree with the defect. The two are pinned equal by
    `test_this_file_and_dc_blocker_measure_t20_identically`."""
    x = np.asarray(x, float)
    h = max(1, int(SR * frame_ms / 1e3))
    e = np.add.reduceat(x ** 2, np.arange(0, len(x) - len(x) % h, h))
    edc = np.cumsum(e[::-1])[::-1]
    if edc[0] <= 0:
        return float("nan")
    db = 10.0 * np.log10(edc / edc[0] + 1e-30)
    i0 = int(np.argmax(db <= -0.0))
    below = np.where(db <= -20.0)[0]
    return float("nan") if not len(below) else (below[0] - i0) * frame_ms


def band_limited(x, lo: float = BAND_FLOOR_HZ):
    """`x` with every FFT bin below `lo` zeroed. Zero-phase, stateless."""
    x = np.asarray(x, float)
    spectrum = np.fft.rfft(x)
    spectrum[np.fft.rfftfreq(len(x), 1.0 / SR) < lo] = 0.0
    return np.fft.irfft(spectrum, n=len(x))


def carrier(t20_ms_target: float = TRUE_T20_MS, n: int | None = None):
    """A 2 kHz tone whose T20 is `t20_ms_target` BY CONSTRUCTION.

    For an envelope e^(-t/tau) the Schroeder integral is proportional to
    e^(-2t/tau), so 10*log10 of it is -20*t/(tau*ln 10) dB and falls 20 dB
    after tau*ln(10). Nothing here is fitted to anything.

    THAT CONSTANT WAS WRONG ONCE AND THE PROBE CAUGHT IT. Written as
    tau = T20/ln(100), the carrier's actual T20 was 100 ms while the file
    claimed 200, and `main()` REFUSED -- "the >= 20 Hz estimator did not
    recover the constructed truth", 102.0 ms against a declared 200.0. A
    qualification whose own ground truth is wrong is worse than none, and the
    only reason this one did not ship wrong is that it asserts its truth
    instead of printing it."""
    n = int(CLIP_S * SR) if n is None else n
    tau = (t20_ms_target / 1e3) / math.log(10.0)
    t = np.arange(n, dtype=float) / SR
    return np.sin(2 * np.pi * TONE_HZ * t) * np.exp(-t / tau)


def with_pedestal(x, energy_frac: float = PEDESTAL_ENERGY_FRAC,
                  hz: float = PEDESTAL_HZ):
    """`x` plus a sub-20 Hz component carrying `energy_frac` of the total.

    A STANDING offset at `hz` = 0, which is what the cymbal has: a component
    that does not decay with the voice, so the backward integral keeps finding
    it long after the voice is gone."""
    x = np.asarray(x, float)
    n = len(x)
    t = np.arange(n, dtype=float) / SR
    shape = np.ones(n) if hz == 0.0 else np.sin(2 * np.pi * hz * t)
    # solve for a: a^2 * sum(shape^2) = frac/(1-frac) * sum(x^2)
    a = math.sqrt((energy_frac / (1.0 - energy_frac)) *
                  float((x ** 2).sum()) / float((shape ** 2).sum()))
    return x + a * shape, a


def case(energy_frac: float = PEDESTAL_ENERGY_FRAC,
         pedestal_hz: float = PEDESTAL_HZ,
         faster: float = FASTER_DECAY_FRAC) -> dict[str, float]:
    clean = carrier()
    contaminated, pedestal_amp = with_pedestal(clean, energy_frac, pedestal_hz)

    # (4)'s injected regression: a genuinely shorter decay, same pedestal.
    faster_clean = carrier(TRUE_T20_MS * (1.0 - faster))
    faster_contaminated, _ = with_pedestal(faster_clean, energy_frac,
                                           pedestal_hz)

    out = {
        "true_t20_ms": TRUE_T20_MS,
        "pedestal_amplitude": pedestal_amp,
        "pedestal_energy_frac": energy_frac,
        # (1) and (2): the same contaminated signal, two estimators
        "global_t20_ms": t20_ms(contaminated),
        "banded_t20_ms": t20_ms(band_limited(contaminated)),
        # (3) the no-pedestal control: the repair must be a no-op here
        "clean_global_t20_ms": t20_ms(clean),
        "clean_banded_t20_ms": t20_ms(band_limited(clean)),
        # (4) a real 10 % faster decay, seen through the repaired gate
        "faster_true_t20_ms": TRUE_T20_MS * (1.0 - faster),
        "faster_banded_t20_ms": t20_ms(band_limited(faster_contaminated)),
        "faster_global_t20_ms": t20_ms(faster_contaminated),
    }
    out["global_error_pct"] = 100.0 * (out["global_t20_ms"] -
                                       TRUE_T20_MS) / TRUE_T20_MS
    out["banded_error_pct"] = 100.0 * (out["banded_t20_ms"] -
                                       TRUE_T20_MS) / TRUE_T20_MS
    out["clean_delta_ms"] = out["clean_banded_t20_ms"] - out["clean_global_t20_ms"]
    # what the gate reads when the pedestal is REMOVED and nothing else changes
    out["removal_global_pct"] = 100.0 * (out["clean_global_t20_ms"] -
                                         out["global_t20_ms"]) / out["global_t20_ms"]
    out["removal_banded_pct"] = 100.0 * (out["clean_banded_t20_ms"] -
                                         out["banded_t20_ms"]) / out["banded_t20_ms"]
    # (4) as the gate would read it, against the 3 % allowance
    out["faster_banded_pct"] = 100.0 * (out["faster_banded_t20_ms"] -
                                        out["banded_t20_ms"]) / out["banded_t20_ms"]
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--json")
    ap.add_argument("--pedestal-frac", type=float, default=PEDESTAL_ENERGY_FRAC,
                    help="sub-20 Hz share of clip energy; set it to ~0 to "
                         "remove this qualification's own premise and watch "
                         "it REFUSE rather than report")
    a = ap.parse_args(argv)
    r = case(energy_frac=a.pedestal_frac)
    print(f"truth (by construction)        T20 = {r['true_t20_ms']:.1f} ms")
    print(f"sub-20 Hz pedestal             {100 * r['pedestal_energy_frac']:.1f} %"
          f" of clip energy, amplitude {r['pedestal_amplitude']:.4f}")
    print()
    print(f"  global T20, contaminated     {r['global_t20_ms']:8.1f} ms"
          f"   {r['global_error_pct']:+8.2f} %  <- INFLATED by the pedestal")
    print(f"  >=20 Hz T20, contaminated    {r['banded_t20_ms']:8.1f} ms"
          f"   {r['banded_error_pct']:+8.2f} %  <- recovers the truth")
    print()
    print("  REMOVING the pedestal, nothing else changed -- what each gate reads:")
    print(f"    global   {r['removal_global_pct']:+8.2f} %"
          f"   (a 3 % allowance FAILS on a change that is not a decay change)")
    print(f"    >=20 Hz  {r['removal_banded_pct']:+8.2f} %   (inside 3 %)")
    print()
    print("  CONTROL: no pedestal at all -- the repair must be a no-op")
    print(f"    global {r['clean_global_t20_ms']:.1f} ms"
          f"   >=20 Hz {r['clean_banded_t20_ms']:.1f} ms"
          f"   delta {r['clean_delta_ms']:+.1f} ms")
    print()
    print(f"  CONTROL: a genuinely {100 * FASTER_DECAY_FRAC:.0f} % faster decay"
          f" -- the repaired gate must still go RED")
    print(f"    >=20 Hz reads {r['faster_banded_pct']:+.2f} %"
          f"   against the 3 % allowance")

    ok = True
    if not r["global_error_pct"] >= 10.0:
        print("REFUSED: the global estimator was not inflated; "
              "the premise of this qualification does not hold")
        ok = False
    if not abs(r["banded_error_pct"]) <= 1.5:
        print("REFUSED: the >= 20 Hz estimator did not recover the "
              "constructed truth")
        ok = False
    if not abs(r["clean_delta_ms"]) <= T20_FRAME_MS:
        print("REFUSED: the band limit moved a pedestal-free signal's T20")
        ok = False
    if not abs(r["faster_banded_pct"]) >= 3.0:
        print("REFUSED: the repaired gate cannot see a real decay regression")
        ok = False
    print()
    print("QUALIFIED: the decay gate must be read on the >= 20 Hz band"
          if ok else "NOT QUALIFIED")
    if a.json:
        with open(a.json, "w") as fh:
            json.dump(r, fh, indent=2)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
