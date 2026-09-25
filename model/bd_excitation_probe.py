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
flat full-scale constant (`drums_fx.py`: `s = 32767`) gated only by a
non-negative amplitude envelope.

THIS PROBE TESTS THAT ATTRIBUTION BEFORE ANY OF IT IS SHIPPED. It is arranged
so that the thing it measures is the thing that ships: every arm renders
through `drums_fx.DrumsFx` at its real register interface, and the measurement
is `drum_fit.band_energy_interval` -- the same function the acceptance test
`test_bd_has_the_attack_window_and_without_it_does_not` and the published table
both use.

WHAT IT FOUND (2026-09-25; every number below is printed by `--refs`):

  1. THE GAP IS STRUCTURAL, NOT A KNOB POSITION. Over ALL 25 bass-drum
     settings the corpus holds, the machine puts 0.29-2.30 % of its first
     4 ms below 80 Hz and 43.2-56.6 % in 150-300 Hz. Ours puts 73.4 % below
     80 Hz and 4.2 % in 150-300 -- 32x above the machine's largest and 10x
     below its smallest, at every setting it has. So the single published
     41.2 % is a median (41.2 over the 25), not a coincidence of one file.

  2. THE EXCITER ENVELOPE CANNOT FIX IT, and not for want of trying.
     `v = 32767 * (ENV(e1) + ENV(e2)) >> (15 + att)` is NON-NEGATIVE by
     construction, so its spectrum is maximal at DC and no setting of it can
     tilt the excitation upward in frequency relative to the near-impulse that
     already ships. Measured: every tau, hold and two-segment combination
     moves 22.3 % DOWN.

  3. A BIPOLAR EXCITATION DOES FIX IT, and the block can already express one.
     `M_BD` is mode 8 and `N_NUMS` is 11, so the BD's resonator has a
     numerator register it does not use. `BP` injects `e[n] - e[n-2]`, an
     AC-coupled biphasic pulse, for ONE register write and no new hardware.
     It lands INSIDE the machine's own 25-setting range on all five bands
     (0.45 / 42.86 / 48.37 / 6.84 / 1.47) while keeping f0 = 49.40 Hz,
     tau = 142.4 ms, the peak level and a monotone decay.

  4. AND IT STILL CANNOT SHIP, for a reason that has nothing to do with the
     excitation. `BP` costs 23.9 dB of resonator gain, which has to be put
     back with the mode's output `amp` -- so the resonator's STATE runs 15.6x
     smaller. The bank's biquad truncates (`acc >> CF`, floor), and a
     truncating biquad with a pole at r = 0.99993 has a DC deadband it settles
     into: a fixed STATE offset, so its size relative to the signal grows
     exactly as the state shrinks. Measured over accents 0.5-2.0, the shipped
     kit's pedestal is -40.2 dB at worst; the BP arm's is -19.4 dB.

So the excitation shape is the right mechanism and the numerator is the right
lever, and what blocks it is `modal_fixed`'s arithmetic. That is a different
defect, filed separately; this module ships the measurement and NOT a change
to the kit.

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
import glob
import math
import os
import statistics
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
LOW, TARGET = 0, 1                        # the 20-80 Hz and 80-150 Hz columns

# The published figures this probe's preconditions are checked against
# (docs/drum-verification.md section 8.3, measured 2026-09-18 against
# sounds-tr808-fischer @ 85fbecf; both reproduce here to 0.05 pp).
PUBLISHED_REF = 41.2
PUBLISHED_OURS = 22.3
PUBLISHED_TOL = 2.0                        # percentage points

REF_BD = os.path.join("bd8", "BD5050.WAV")
REF_GLOB = os.path.join("bd8", "BD*.WAV")
HIT_FRAME = 10
DUR_S = 0.8
TAIL_DUR_S = 1.5                           # long enough for the decay to be over
ACCENTS = (0.5, 0.7, 1.0, 1.4, 2.0)

# The kit's own BD numbers, so a regression can see them move.
KIT_AMP = 0.003309
KIT_PEAK = 0.25
KIT_TAU = 0.1e-3
BD_F0, BD_TAU_MS = 49.4, 143.7


class Refused(RuntimeError):
    """A precondition of the apparatus failed. Not a result, and not a fail."""


# ------------------------------------------------------------ measurement ----
def first4(x: np.ndarray, sr: int) -> list:
    """The section-8.3 row for one signal: onset-trimmed, filtered per band,
    integrated over [T0, T1). Scale-invariant, so an arm that only changed the
    excitation's LEVEL could not move it -- which `self_test` checks."""
    return df.band_energy_interval(df.trim_onset(np.asarray(x, np.float64), sr),
                                   sr, EDGES, T0, T1)


def reference_row(refs: str) -> tuple:
    path = os.path.join(refs, REF_BD)
    if not os.path.exists(path):
        raise Refused(f"no reference bass drum at {path}; clone sounds-tr808-fischer")
    sr, x = df.read_wav(path)
    return first4(x, sr), sr


def reference_range(refs: str) -> dict:
    """The machine's OWN spread over every bass-drum setting the corpus holds.

    This is what says whether a single file's 41.2 % is a target or an
    accident: if the machine's own knobs moved the split as far as our gap,
    the gap would not be evidence of anything.
    """
    rows = []
    for p in sorted(glob.glob(os.path.join(refs, REF_GLOB))):
        sr, x = df.read_wav(p)
        rows.append((os.path.basename(p), first4(x, sr)))
    if not rows:
        raise Refused(f"no bass-drum files under {os.path.join(refs, REF_GLOB)}")
    cols = list(zip(*[r for _, r in rows]))
    return dict(rows=rows,
                lo=[min(c) for c in cols], hi=[max(c) for c in cols],
                median=[statistics.median(c) for c in cols])


def inside(row, rng) -> bool:
    """Is every band of `row` inside the machine's own measured range?"""
    return all(lo <= v <= hi for v, lo, hi in zip(row, rng["lo"], rng["hi"]))


# ------------------------------------------------------------ the renders ----
def render(kit: list, *, coef_seq: bool = True, accent: float = 1.0,
           dur_s: float = DUR_S, envs: int = dx.N_ENV) -> np.ndarray:
    """One BD hit through the block's real write port; the body bus as float
    (the bus the published row is measured on)."""
    d = dx.DrumsFx(envs=envs)
    _, body = d.play(dx.hit_writes([(HIT_FRAME, dx.BD, accent)], kit, coef_seq=coef_seq),
                     int(dur_s * SR))
    return body.astype(np.float64) / 32768.0


def _set_env(kit: list, e: int, stop: int, tau_s: float, peak: float, **kw) -> list:
    new = dict(dx.env_writes(e, stop, tau_s, peak, **kw))
    return [(a, new.get(a, v)) for a, v in kit]


def _set_path(kit: list, p: int, word: int) -> list:
    a_p = dx.A_PATH + p
    return [(a, word if a == a_p else v) for a, v in kit]


def _set_num(kit: list, m: int, num: int) -> list:
    a_n = dx.A_MODE + m * dx.MODE_STRIDE + 3
    return [(a, num if a == a_n else v) for a, v in kit]


def _set_amp(kit: list, m: int, amp: float) -> list:
    a_a = dx.A_MODE + m * dx.MODE_STRIDE + 2
    v = dx.usat(int(round(amp * 65536)), 16)
    return [(a, v if a == a_a else w) for a, w in kit]


def spare_env(kit: list) -> int:
    """An envelope index no path in `kit` reads, or the first index past the
    shipped bank if all 18 are taken.

    MEASURED, and it is what approach (a) actually costs: all 18 envelopes are
    already read by a path, so "add a second envelope segment to E_BDX" is not
    a register change, it is a 19th envelope. The arm below renders with
    `DrumsFx(envs=N_ENV + 1)` and says so, rather than borrowing E_BDCLICK and
    silently changing the click path too.
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


def bd_kit(num: int = RAW, amp: float = KIT_AMP, peak: float = KIT_PEAK,
           hold: int = 0, tau_s: float = KIT_TAU) -> list:
    """`kit_808()` with only the bass drum's excitation path touched."""
    kit = _set_amp(dx.kit_808(), dx.M_BD, amp)
    kit = _set_env(kit, dx.E_BDX, dx.BD, tau_s, peak, hold=hold)
    return _set_num(kit, dx.M_BD, num)


def calibrate_amp(num: int, peak: float, hold: int, tau_s: float = KIT_TAU,
                  target_peak: float = None, probe_amp: float = 0.02) -> float:
    """The `amp` that restores the shipped peak level. Linear in amp, so one
    render answers it -- but the render is at the same duration as the arm it
    calibrates, because a shorter one can miss the peak."""
    if target_peak is None:
        target_peak = abs(render(bd_kit())).max()
    x = render(bd_kit(num, probe_amp, peak, hold, tau_s))
    if abs(x).max() < 1e-9:
        raise Refused("calibration render is silent")
    return probe_amp * target_peak / abs(x).max()


# --------------------------------------------------------- the diagnostics ---
def body_stats(x: np.ndarray) -> dict:
    """The four things that must NOT move when the excitation's shape does:
    the body mode's f0 and tau, a monotone decay, and the DC pedestal the
    bank's truncating biquad settles into."""
    y = df.trim_onset(np.asarray(x, np.float64), SR)
    w = int(0.020 * SR)
    r = [20 * math.log10(max(1e-12, math.sqrt(float((y[i * w:(i + 1) * w] ** 2).mean()))))
         for i in range(len(y) // w)]
    r = [v - r[0] for v in r] if r else [0.0]
    k = min(len(r), 35)
    m = df.noise_share(y[int(0.020 * SR):], SR, df.VOICE_MODES["BD"],
                       win_s=0.30)["modes"][0]
    peak = abs(np.asarray(x, np.float64)).max()
    tail = np.asarray(x, np.float64)[int(1.2 * SR):]
    ped = (20 * math.log10(max(1e-12, abs(float(tail.mean()))) / max(1e-12, peak))
           if len(tail) else float("nan"))
    return dict(f0=m["hz"], tau_ms=m["tau_ms"], peak=peak,
                drise=max((r[i + 1] - r[i]) for i in range(k - 1)) if k > 1 else 0.0,
                pedestal_db=ped)


def pedestal_over_accents(kit: list) -> list:
    """The DC pedestal at each accent. It is trajectory-dependent -- which
    accent lands in which deadband is not smooth -- so one accent is not a
    measurement of it. The WORST is what a listener meets."""
    out = []
    for a in ACCENTS:
        x = render(kit, accent=a, dur_s=TAIL_DUR_S)
        out.append(body_stats(x)["pedestal_db"])
    return out


# ------------------------------------------ (b): the arbitrary-shape bound ---
class BankReplay:
    """Replay the shipped BD render through a fresh modal bank from RECORDED
    per-frame excitation and coefficients, so an arbitrary excitation sequence
    can be substituted for the BD mode's.

    This is a BOUND, not a proposal: nothing in the block can emit an arbitrary
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
        return first4(self.replay(bd_exc).astype(np.float64) / 32768.0, SR)


def shape_impulse(amp: int = 1 << 20) -> np.ndarray:
    return np.array([amp], dtype=np.int64)


def shape_rect(n: int, amp: int = 1 << 15) -> np.ndarray:
    return np.full(n, amp, dtype=np.int64)


def shape_biphasic(n: int, amp: int = 1 << 15) -> np.ndarray:
    """+amp for n samples then -amp for n: zero area, spectrum zero at DC."""
    return np.concatenate([np.full(n, amp), np.full(n, -amp)]).astype(np.int64)


def shape_sine_cycle(n: int, amp: int = 1 << 15) -> np.ndarray:
    t = (np.arange(n) + 0.5) / n
    return np.round(amp * np.sin(2 * math.pi * t)).astype(np.int64)


def shape_exp(tau_s: float, amp: int = 1 << 15, n: int = None) -> np.ndarray:
    n = n or int(8 * tau_s * SR) + 1
    return np.round(amp * np.exp(-np.arange(n) / (tau_s * SR))).astype(np.int64)


def bound_family() -> list:
    out = [("impulse, 1 sample", shape_impulse()),
           ("shipped exp tau 0.1 ms", shape_exp(KIT_TAU))]
    for n in (8, 128):
        out.append((f"rect {n} smp", shape_rect(n)))
    for n in (2, 8, 32, 128):
        out.append((f"biphasic +-{n} smp ({2 * n / SR * 1e3:.2f} ms)", shape_biphasic(n)))
    for n in (64, 256):
        out.append((f"sine cycle {n} smp ({SR / n:.0f} Hz)", shape_sine_cycle(n)))
    return out


# --------------------------------------------------------------- reporting ---
def header() -> str:
    return ("  " + " " * 30 + "  ".join(f"{a}-{b}".rjust(7)
                                        for a, b in zip(EDGES, EDGES[1:])))


def row_str(name: str, row: list) -> str:
    return f"  {name:<30} " + "  ".join(f"{v:7.2f}" for v in row)


def check_preconditions(refs: str, verbose: bool = True) -> dict:
    ref, ref_sr = reference_row(refs)
    ours = first4(render(bd_kit()), SR)
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
    """No audio needed. Checks the three things that would silently invalidate
    every number this file prints."""
    rc = 0
    rp = BankReplay()                       # raises Refused if not bit-exact
    if verbose:
        print("self-test -- (b) replay is bit-exact against DrumsFx: OK")

    e = rp.shipped_exc()
    worst = max(abs(x - y) for x, y in zip(rp.row(e), rp.row(e // 4)))
    if verbose:
        print(f"self-test -- band split invariant to a 4x excitation level change: "
              f"worst column moves {worst:.3f} pp")
    rc |= 0 if worst < 0.5 else 1

    kit = dx.kit_808()
    if spare_env(kit) < dx.N_ENV:
        print("self-test -- WARNING: an envelope is now spare; (a)'s cost has changed")
    if dx.M_BD >= dx.N_NUMS:
        print("self-test -- WARNING: M_BD no longer has a numerator; (a2) is void")
        rc |= 1
    elif verbose:
        print(f"self-test -- M_BD ({dx.M_BD}) < N_NUMS ({dx.N_NUMS}): the numerator "
              "lever (a2) exists")
    return rc


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

    print("=== PRECONDITIONS -- the two published anchors must reproduce ===")
    try:
        base = check_preconditions(a.refs)
    except Refused as e:
        print(f"REFUSED: {e}")
        return 2
    print("  both anchors reproduce\n")

    print("=== 1. THE MACHINE'S OWN RANGE over every BD setting in the corpus ===")
    rng = reference_range(a.refs)
    print(header())
    print(row_str(f"machine, min over {len(rng['rows'])}", rng["lo"]))
    print(row_str("machine, median", rng["median"]))
    print(row_str("machine, max", rng["hi"]))
    print(row_str("ours, shipped", base["ours"]))
    print("  ours is outside the machine's own range on "
          f"{sum(1 for v, lo, hi in zip(base['ours'], rng['lo'], rng['hi']) if not lo <= v <= hi)}"
          f" of {len(EDGES) - 1} bands\n")

    print("=== 2. (a) THE EXCITER ENVELOPE -- non-negative, so it cannot tilt up ===")
    print(header())
    for tau in (KIT_TAU, 0.3e-3, 1e-3, 2e-3, 4e-3, 8e-3):
        print(row_str(f"E_BDX tau {tau * 1e3:g} ms", first4(render(bd_kit(tau_s=tau)), SR)))
    for hold in (8, 48, 192):
        print(row_str(f"E_BDX hold {hold} frames", first4(render(bd_kit(hold=hold)), SR)))
    kit = _set_env(dx.kit_808(), dx.E_BDX, dx.BD, KIT_TAU, 0.25)
    sp = spare_env(kit)
    kit2 = _set_env(kit, sp, dx.BD, 4e-3, 0.06)
    kit2 = _set_path(kit2, dx.P_BDX,
                     dx.path_word(dx.SRC_PULSE, dx.E_BDX, sp, dest=dx.M_BD))
    print(row_str(f"two segments (env {sp}, +1 env)",
                  first4(render(kit2, envs=max(dx.N_ENV, sp + 1)), SR)))
    print()

    print("=== 3. (a2) THE BD MODE'S UNUSED NUMERATOR -- a bipolar excitation ===")
    print(header())
    print(row_str("RAW (shipped)", base["ours"]))
    cands = []
    for label, num, peak, hold in (("BP, peak 1.0", BP, 1.0, 0),
                                   ("BP, peak 1.0, hold 8", BP, 1.0, 8),
                                   ("BP, peak 1.0, hold 64", BP, 1.0, 64),
                                   ("HP, peak 1.0", HP, 1.0, 0)):
        amp = calibrate_amp(num, peak, hold)
        kit = bd_kit(num, amp, peak, hold)
        x = render(kit, dur_s=TAIL_DUR_S)
        st = body_stats(x)
        row = first4(x, SR)
        cands.append((label, kit, row, st, amp))
        print(row_str(f"{label}, amp {amp:.4f}", row)
              + f"   f0 {st['f0']:.2f} tau {st['tau_ms']:.1f} ms"
                f" drise {st['drise']:+.1f} dB {'INSIDE' if inside(row, rng) else 'outside'}")
    print(f"  the shipped kit needs amp {KIT_AMP:.4f}; BP needs "
          f"{cands[0][4] / KIT_AMP:.1f}x that, i.e. the resonator's STATE runs "
          f"{20 * math.log10(cands[0][4] / KIT_AMP):.1f} dB smaller\n")

    print("=== 4. (b) BOUND -- an arbitrary excitation sequence into the same bank ===")
    rp = BankReplay()
    print(header())
    for name, seq in bound_family():
        print(row_str(name, rp.row(seq)))
    print()

    print("=== 5. THE BLOCKER -- the truncating biquad's DC pedestal, per accent ===")
    print("  " + " " * 30 + "".join(f"{v:9.2f}" for v in ACCENTS))
    ship = pedestal_over_accents(bd_kit())
    print("  " + f"{'RAW (shipped)':<30}" + "".join(f"{v:9.1f}" for v in ship)
          + f"   worst {max(ship):.1f} dB")
    for label, kit, row, st, amp in cands[:3]:
        p = pedestal_over_accents(kit)
        print("  " + f"{label:<30}" + "".join(f"{v:9.1f}" for v in p)
              + f"   worst {max(p):.1f} dB")
    print()

    print("=== VERDICT ===")
    best = min(cands[:3], key=lambda c: sum(abs(v - m) for v, m in zip(c[2], rng["median"])))
    print(f"  the excitation's SHAPE reaches the machine's range: {inside(best[2], rng)}"
          f"  ({best[0]})")
    print("  and it cannot ship while the bank truncates: the pedestal above is the")
    print("  same defect at every arm that needs the resonator to run quieter.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
