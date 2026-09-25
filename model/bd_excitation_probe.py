#!/usr/bin/env python3
"""Does SHAPING the bass drum's excitation move its first-4 ms band split?

The hypothesis under test (issue #21, contract 17.20). `docs/drum-verification.md`
section 8.3 measures, over the first 4 ms after onset, filtered in the time
domain before integrating:

    % of energy in the first 4 ms   20-80   80-150   150-300   300-600 Hz
    reference unit (BD5050)           0.6     41.2      52.3       5.8
    ours, rev 5                      97.5      2.7       0.2       0.2
    ours, with section 2's window    73.4     22.3       4.2       0.1

and attributes the remaining 41.2-vs-22.3 gap to the "excitation shape" -- the
reference's pulse shaper -- which the kit does not implement: `SRC_PULSE` is a
flat full-scale constant (`drums_fx.py`, `s = 32767`) gated only by an
amplitude envelope.

THIS PROBE TESTS THAT ATTRIBUTION BEFORE ANY OF IT IS SHIPPED. It is arranged
so that the thing it measures is the thing that ships: every arm renders
through `drums_fx.DrumsFx` at its real register interface, and the measurement
is `drum_fit.band_energy_interval` -- the same function the acceptance test
`test_bd_has_the_attack_window_and_without_it_does_not` and the published table
both use.

Three families of arm, in increasing order of what they would cost:

  (a) THE EXCITER ENVELOPE. Keep the flat pulse; change E_BDX's tau, peak and
      hold, and add a second envelope segment on the same path. Free: register
      writes only. Note the analytic ceiling -- `v = 32767 * (ENV(e1) +
      ENV(e2))` with both envelopes non-negative can only ever be a NON-NEGATIVE
      pulse, whose spectrum is maximal at DC, so no setting of (a) can tilt the
      excitation upward in frequency relative to the near-impulse that ships.
      The sweep is here to measure that, not to assume it.

  (a2) THE MODE'S NUMERATOR. `M_BD` is mode 8 and `N_NUMS` is 11, so the bass
      drum's resonator ALREADY has a numerator register it does not use
      (`RAW`). `BP` injects `e[n] - e[n-2]` and `HP` injects `e[n] - 2e[n-1] +
      e[n-2]`: a genuinely bipolar, high-pass-shaped excitation, for one
      register write and no new hardware. This is the cheapest thing that can
      do what (a) provably cannot.

  (b) AN ARBITRARY SHAPE, as a BOUND. Drive the modal bank directly with any
      integer sequence at all -- more than any plausible source could emit --
      and report the range of first-4 ms band splits reachable. If the target
      is outside that range then no excitation-shaping hardware reaches it and
      the attribution is refuted, which is a result, not a failure.

PRECONDITIONS, asserted rather than assumed (docs/failure-modes.md):

  * the reference file must exist and reproduce the published 41.2 %;
  * the shipped arm must reproduce the published 22.3 %;
  * the (b) replay harness must reproduce the shipped render BIT FOR BIT from
    the recorded excitation and coefficients before any shape is substituted.

Any of those failing raises `Refused`. An apparatus that answers when its own
controls have moved is worse than one that is absent.

Usage:

    git clone --depth 1 https://github.com/tidalcycles/sounds-tr808-fischer /tmp/tr808-ref
    python3 model/bd_excitation_probe.py --refs /tmp/tr808-ref
    python3 model/bd_excitation_probe.py --self-test     # no audio needed
"""
from __future__ import annotations
import argparse
import math
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import drum_fit as df
import drums_fx as dx
from drums_fx import SR
from modal_fixed import ModalFx, RAW, BP, HP

# The bands of docs/drum-verification.md section 8.3, and its interval.
EDGES = [20, 80, 150, 300, 600, 2000]
T0, T1 = 0.0, 0.004
LOW, TARGET = 0, 1                        # 20-80 Hz and 80-150 Hz columns

# The published figures this probe's preconditions are checked against
# (docs/drum-verification.md section 8.3, measured 2026-09-18 against
# sounds-tr808-fischer @ 85fbecf; re-measured here, see the PR for issue #21).
PUBLISHED_REF = 41.2
PUBLISHED_OURS = 22.3
PUBLISHED_TOL = 2.0                        # percentage points

REF_BD = os.path.join("bd8", "BD5050.WAV")
HIT_FRAME = 10
DUR_S = 0.8


class Refused(RuntimeError):
    """A precondition of the apparatus failed. Not a result, and not a fail."""


# ------------------------------------------------------------ measurement ----
def first4(x: np.ndarray, sr: int) -> list:
    """The section-8.3 row for one signal: onset-trimmed, filtered per band,
    integrated over [T0, T1). Scale-invariant, so an arm that only changes the
    excitation's LEVEL cannot move it -- which is the point."""
    return df.band_energy_interval(df.trim_onset(np.asarray(x, np.float64), sr),
                                   sr, EDGES, T0, T1)


def reference_row(refs: str) -> tuple:
    path = os.path.join(refs, REF_BD)
    if not os.path.exists(path):
        raise Refused(f"no reference bass drum at {path}; clone sounds-tr808-fischer")
    sr, x = df.read_wav(path)
    return first4(x, sr), sr


# ------------------------------------------------------------------ arms -----
def render(kit: list, *, coef_seq: bool = True, accent: float = 1.0,
           dur_s: float = DUR_S, trace: bool = False, envs: int = dx.N_ENV):
    """One BD hit, through the block's real write port. Returns the body bus as
    float (the bus the published row is measured on), and the DrumsFx if asked
    -- `.trace` then carries the per-frame excitation."""
    d = dx.DrumsFx(envs=envs)
    w = dx.hit_writes([(HIT_FRAME, dx.BD, accent)], kit, coef_seq=coef_seq)
    _, body = d.play(w, int(dur_s * SR))
    x = body.astype(np.float64) / 32768.0
    return (x, d) if trace else x


def _set_env(kit: list, e: int, stop: int, tau_s: float, peak: float, **kw) -> list:
    """Replace one envelope's registers in a kit image, keeping write order."""
    new = dict(dx.env_writes(e, stop, tau_s, peak, **kw))
    return [(a, new.get(a, v)) for a, v in kit]


def _set_path(kit: list, p: int, word: int) -> list:
    a_p = dx.A_PATH + p
    return [(a, word if a == a_p else v) for a, v in kit]


def _set_num(kit: list, m: int, num: int) -> list:
    a_n = dx.A_MODE + m * dx.MODE_STRIDE + 3
    return [(a, num if a == a_n else v) for a, v in kit]


def spare_env(kit: list) -> int:
    """An envelope index no path in `kit` reads, or the first index past the
    shipped bank if every one of the 18 is taken.

    MEASURED, and it matters for costing approach (a): all 18 envelopes are
    already read by a path, so "add a second envelope segment to E_BDX" is NOT
    a free register change -- it is a 19th envelope (three registers, a level
    accumulator and a rate multiply). The arm below renders with
    `DrumsFx(envs=N_ENV + 1)` and says so rather than borrowing E_BDCLICK,
    which would silently change the click path as well.
    """
    used = set()
    for a, v in kit:
        if dx.A_PATH <= a < dx.A_PATH + dx.N_PATH:
            used.add((v >> 5) & 31)
            used.add((v >> 10) & 31)
    for e in range(dx.N_ENV):
        if e not in used:
            return e
    return dx.N_ENV


def arm_shipped() -> np.ndarray:
    """What ships: flat 32767 through E_BDX (tau 0.1 ms, peak 0.25), plus the
    130 Hz / 4 ms mode-retuning attack window of #154."""
    return render(dx.kit_808())


def arm_no_window() -> np.ndarray:
    """The negative control the table's middle row is: no attack window."""
    return render(dx.kit_808(), coef_seq=False)


def arm_env(tau_s: float, peak: float = 0.25, hold: int = 0) -> np.ndarray:
    """(a): the flat pulse kept, E_BDX reshaped."""
    return render(_set_env(dx.kit_808(), dx.E_BDX, dx.BD, tau_s, peak, hold=hold))


def arm_two_segments(tau_a: float, peak_a: float, tau_b: float, peak_b: float) -> np.ndarray:
    """(a) proper: two envelope segments summed on the one exciter path, which
    the datapath already offers (`ENV(e1) + ENV(e2)`). Costs a 19th envelope --
    see `spare_env`."""
    kit = _set_env(dx.kit_808(), dx.E_BDX, dx.BD, tau_a, peak_a)
    spare = spare_env(kit)
    kit = _set_env(kit, spare, dx.BD, tau_b, peak_b)
    kit = _set_path(kit, dx.P_BDX,
                    dx.path_word(dx.SRC_PULSE, dx.E_BDX, spare, dest=dx.M_BD))
    return render(kit, envs=max(dx.N_ENV, spare + 1))


def arm_numerator(num: int, tau_s: float = 0.1e-3, peak: float = 0.25) -> np.ndarray:
    """(a2): the BD mode's unused numerator register, which turns the injected
    excitation into `e[n] - e[n-2]` (BP) or `e[n] - 2e[n-1] + e[n-2]` (HP)."""
    kit = _set_env(dx.kit_808(), dx.E_BDX, dx.BD, tau_s, peak)
    return render(_set_num(kit, dx.M_BD, num))


# ------------------------------------------ (b): the arbitrary-shape bound ---
class BankReplay:
    """Replay the shipped BD render through a fresh modal bank from RECORDED
    per-frame excitation and coefficients, so an arbitrary excitation sequence
    can be substituted for the BD mode's.

    This is a bound, not a proposal: nothing in the block can emit an arbitrary
    sequence. Its whole job is to say what the ceiling is if something could.

    Its precondition is bit-exactness against the render it recorded from --
    checked in `__init__`, which raises `Refused` on any mismatch.
    """

    def __init__(self, kit: list = None, dur_s: float = DUR_S):
        kit = dx.kit_808() if kit is None else kit
        self.n = int(dur_s * SR)
        d = dx.DrumsFx()
        seen = []
        step = d.bank.step

        def recording_step(exc, coefs, num):
            seen.append(([int(v) for v in exc], [tuple(c) for c in coefs],
                         [int(v) for v in num]))
            return step(exc, coefs, num)

        d.bank.step = recording_step
        _, body = d.play(dx.hit_writes([(HIT_FRAME, dx.BD, 1.0)], kit), self.n)
        self.body = body
        self.exc = np.array([s[0] for s in seen], dtype=np.int64)
        self.coefs = [s[1] for s in seen]
        self.num = [s[2] for s in seen]
        if not np.array_equal(self.replay(self.exc[:, dx.M_BD]), body):
            raise Refused("the replay harness is not bit-exact against DrumsFx; "
                          "every (b) number it would print is meaningless")

    def replay(self, bd_exc) -> np.ndarray:
        """`bd_exc` replaces the BD mode's excitation; every other mode keeps
        exactly what the recorded render had."""
        bd_exc = np.asarray(bd_exc, dtype=np.int64)
        bank = ModalFx(modes=dx.N_MODES, nums=dx.N_NUMS, headroom=dx.BODY_HR,
                       out_bits=dx.BODY_BITS)
        out = np.empty(self.n, dtype=np.int32)
        for t in range(self.n):
            e = list(self.exc[t])
            e[dx.M_BD] = int(bd_exc[t]) if t < len(bd_exc) else 0
            out[t] = bank.step(e, self.coefs[t], self.num[t])
        return out

    def shipped_exc(self) -> np.ndarray:
        return self.exc[:, dx.M_BD].copy()

    def row(self, bd_exc) -> list:
        y = self.replay(bd_exc).astype(np.float64) / 32768.0
        return first4(y, SR)


# ---- the shape family swept as the bound ------------------------------------
def shape_impulse(amp: int = 1 << 20) -> np.ndarray:
    return np.array([amp], dtype=np.int64)


def shape_rect(n: int, amp: int = 1 << 15) -> np.ndarray:
    return np.full(n, amp, dtype=np.int64)


def shape_biphasic(n: int, amp: int = 1 << 15) -> np.ndarray:
    """+amp for n samples then -amp for n: zero area, spectrum zero at DC and
    peaking near 1/(4n). The canonical 'shaped pulse'."""
    return np.concatenate([np.full(n, amp), np.full(n, -amp)]).astype(np.int64)


def shape_halfsine(n: int, amp: int = 1 << 15) -> np.ndarray:
    t = (np.arange(n) + 0.5) / n
    return np.round(amp * np.sin(math.pi * t)).astype(np.int64)


def shape_sine_cycle(n: int, amp: int = 1 << 15) -> np.ndarray:
    """One full cycle of a sine over n samples: zero area, smooth."""
    t = (np.arange(n) + 0.5) / n
    return np.round(amp * np.sin(2 * math.pi * t)).astype(np.int64)


def shape_exp(tau_s: float, amp: int = 1 << 15, n: int = None) -> np.ndarray:
    n = n or int(8 * tau_s * SR) + 1
    return np.round(amp * np.exp(-np.arange(n) / (tau_s * SR))).astype(np.int64)


def shape_tone(hz: float, tau_s: float, amp: int = 1 << 15) -> np.ndarray:
    """A damped tone at `hz`: the most favourable excitation there is for
    putting energy in one band, and far beyond anything the block could emit.
    The upper bound of the bound."""
    n = int(min(8 * tau_s, 0.2) * SR) + 1
    t = np.arange(n) / SR
    return np.round(amp * np.exp(-t / tau_s) * np.sin(2 * math.pi * hz * t)).astype(np.int64)


def bound_family() -> list:
    """(name, sequence) over a family far wider than the block can emit."""
    out = [("impulse, 1 sample", shape_impulse()),
           ("shipped exp tau 0.1 ms", shape_exp(0.1e-3))]
    for n in (2, 8, 32, 128, 512):
        out.append((f"rect {n} smp ({n / SR * 1e3:.2f} ms)", shape_rect(n)))
    for n in (1, 2, 8, 32, 128, 512):
        out.append((f"biphasic +-{n} smp ({2 * n / SR * 1e3:.2f} ms)", shape_biphasic(n)))
    for n in (16, 64, 256):
        out.append((f"half-sine {n} smp", shape_halfsine(n)))
    for n in (16, 64, 256, 1024):
        out.append((f"sine cycle {n} smp ({SR / n:.0f} Hz)", shape_sine_cycle(n)))
    for hz, tau in ((130.0, 4e-3), (130.0, 16e-3), (250.0, 4e-3), (500.0, 2e-3)):
        out.append((f"damped tone {hz:.0f} Hz tau {tau * 1e3:g} ms", shape_tone(hz, tau)))
    return out


# --------------------------------------------------------------- reporting ---
def header() -> str:
    return ("  " + " " * 30 + "  ".join(f"{a}-{b}".rjust(7)
                                        for a, b in zip(EDGES, EDGES[1:])))


def row_str(name: str, row: list) -> str:
    return f"  {name:<30} " + "  ".join(f"{v:7.2f}" for v in row)


def check_preconditions(refs: str, verbose: bool = True) -> dict:
    ref, ref_sr = reference_row(refs)
    ours = first4(arm_shipped(), SR)
    if verbose:
        print(header())
        print(row_str(f"reference BD5050 @ {ref_sr} Hz", ref))
        print(row_str("ours, shipped", ours))
    if abs(ref[TARGET] - PUBLISHED_REF) > PUBLISHED_TOL:
        raise Refused(f"reference 80-150 Hz share is {ref[TARGET]:.1f} %, published "
                      f"{PUBLISHED_REF} -- the corpus or the apparatus moved")
    if abs(ours[TARGET] - PUBLISHED_OURS) > PUBLISHED_TOL:
        raise Refused(f"our 80-150 Hz share is {ours[TARGET]:.1f} %, published "
                      f"{PUBLISHED_OURS} -- the kit or the apparatus moved")
    return dict(ref=ref, ours=ours, ref_sr=ref_sr)


def self_test(verbose: bool = True) -> int:
    """No audio needed. Checks the two things that would silently invalidate
    every number this file prints: that the replay harness is bit-exact, and
    that the measurement is invariant to the excitation's LEVEL (so an arm that
    only got louder could never appear to have changed the shape)."""
    rp = BankReplay()                       # raises Refused if not bit-exact
    e = rp.shipped_exc()
    a, b = rp.row(e), rp.row(e // 4)
    worst = max(abs(x - y) for x, y in zip(a, b))
    if verbose:
        print("self-test -- replay is bit-exact against DrumsFx: OK")
        print(f"self-test -- band split invariant to a 4x level change: "
              f"worst column moves {worst:.3f} pp")
    return 0 if worst < 0.5 else 1


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--refs", help="the unpacked sounds-tr808-fischer tree")
    ap.add_argument("--self-test", action="store_true")
    a = ap.parse_args(argv)

    if a.self_test or not a.refs:
        rc = self_test()
        if not a.refs:
            return rc
        print()

    print("PRECONDITIONS -- the two published anchors must reproduce")
    try:
        base = check_preconditions(a.refs)
    except Refused as e:
        print(f"REFUSED: {e}")
        return 2
    target = base["ref"][TARGET]
    print("  both anchors reproduce\n")

    print("negative control -- no attack window at all (published 2.7 %)")
    print(row_str("ours, window off", first4(arm_no_window(), SR)))
    print()

    print("(a) the exciter envelope E_BDX, flat pulse kept")
    for tau in (0.1e-3, 0.3e-3, 1e-3, 2e-3, 4e-3, 8e-3, 16e-3):
        print(row_str(f"tau {tau * 1e3:g} ms", first4(arm_env(tau), SR)))
    for hold in (8, 48, 192):
        print(row_str(f"tau 0.1 ms, hold {hold} frames",
                      first4(arm_env(0.1e-3, hold=hold), SR)))
    print()

    print("(a) two envelope segments summed on the exciter path")
    for ta, pa, tb, pb in ((0.1e-3, 0.25, 2e-3, 0.06),
                           (0.1e-3, 0.25, 4e-3, 0.06),
                           (0.1e-3, 0.125, 4e-3, 0.125),
                           (0.1e-3, 0.25, 16e-3, 0.03)):
        print(row_str(f"{ta * 1e3:g}ms@{pa:g} + {tb * 1e3:g}ms@{pb:g}",
                      first4(arm_two_segments(ta, pa, tb, pb), SR)))
    print()

    print("(a2) the BD mode's unused numerator register -- a bipolar excitation")
    for name, num in (("RAW (shipped)", RAW), ("BP: e[n]-e[n-2]", BP),
                      ("HP: e[n]-2e[n-1]+e[n-2]", HP)):
        print(row_str(name, first4(arm_numerator(num), SR)))
    print()

    print("(b) BOUND -- an arbitrary excitation sequence into the same bank")
    rp = BankReplay()
    rows = []
    for name, seq in bound_family():
        r = rp.row(seq)
        rows.append((name, r))
        print(row_str(name, r))
    best = max(rows, key=lambda t: t[1][TARGET])
    print()
    print(f"  best 80-150 Hz share over the whole family: {best[1][TARGET]:.2f} % "
          f"({best[0]})")
    print(f"  the reference unit measures {target:.2f} %")
    print("  VERDICT: " + ("reachable" if best[1][TARGET] >= target - PUBLISHED_TOL
                           else "NOT reachable by any excitation shape"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
