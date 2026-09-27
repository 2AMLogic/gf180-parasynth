#!/usr/bin/env python3
"""#388: our rimshot's high bridged-T mode is 18.7 dB too weak relative to its
low mode, and the band around it is a broad shelf where the machine has a
resonance. Is that the high network's **Q** or its **drive**?

The question is decidable without a recording, and the whole point of this
probe is that the decision is made there -- on the two impulse responses'
closed forms -- and only then confirmed against the machine. A parameter
picked to move D10A's own number and confirmed by re-reading D10A's own number
is the failure mode CLAUDE.md names; this is the other order.

  python tools/probes/rs_mode_drive.py derive   # closed form; NO recording
  python tools/probes/rs_mode_drive.py sweep    # Q and drive, ours only
  python tools/probes/rs_mode_drive.py confirm  # ours vs the machine, 2 accents

THE ANSWER (2026-09-27).

1. IT IS THE DRIVE, AND THE DRIVE IS WRONG FOR A REASON THAT HAS NOTHING TO DO
   WITH THE RIMSHOT. `drums_fx` excites both bridged-T bodies with the same
   pulse, which is what the circuit does -- but the bank's RAW numerator is
   ALL-POLE and the circuit's networks are BAND-PASS, and the two impulse
   responses are normalised differently:

       bank     y[n] = x[n] + a1 y[n-1] + a2 y[n-2]     peak ~ 1 / sin(w0)
       circuit  H(s) = H0 (w0/Q) s / (s^2+(w0/Q)s+w0^2) peak ~ H0 w0 / Q

   At the shipping constants that is -11.80 dB (bank) against +5.79 dB
   (circuit) for the high mode re the low -- a 17.60 dB deficit that is a pure
   function of f0 and Q and contains no measurement at all.

2. THE CIRCUIT'S CLOSED FORM PREDICTS THE MACHINE TO 0.7 dB. +5.79 dB
   predicted; the Fischer s/n 103852 recording measures its 1711 Hz mode at
   +6.5 dB re its 457 Hz mode. Two independent routes to the same number, and
   neither of them is our model. That is what makes this a correction rather
   than a tuning.

3. Q IS THE WRONG KNOB, AND `sweep` SECTION A SAYS SO WITH A NUMBER. Over
   RS_HI_Q 13.5 -> 54 -- four times the Q, tau 2.4 -> 9.6 ms -- the scored
   balance moves 1.17 dB (-11.66 -> -10.49) and the high mode's peak re the low
   2.66 dB, while `tail decay` goes 9.29 -> 10.86 ms. 1.17 dB of an 18 dB gap
   for 4x the ring, because the all-pole peak 1/sin(w0) does not contain Q at
   all; what little moves is the window catching a longer mode, not a louder
   one. There is no Q that closes this and keeps reference 5's 2.4 ms.

4. THE SHELF IS OUR OWN STRIKE SPLASH, not a low-Q mode. `sweep`'s band-shape
   column is the depth of the 1450 Hz level below the peak near 1700-1800 Hz.
   At the shipping drive it is 2.8 dB (a shelf); at att 3 it is 6.3 dB, purely
   by lifting the mode -- no Q change -- because the splash is at a fixed level
   re the low mode and the mode climbs out of it. The machine reads 8.5 dB.
   So the shelf was never evidence of a broad resonance; it was evidence of a
   resonance buried in a floor. It stops deepening around 6.5 dB (att 2-4),
   which is the remaining gap to the machine and is NOT a drive problem.

5. att 4 SCORES BETTER AND IS NOT TAKEN. Section B reads bal1 -1.79 dB at att 4
   against -4.69 at att 3, and the reference is +3.85 -- so the better-scoring
   step is the one the closed form does NOT pick (17.60 dB is 0.47 dB from att
   3 and 6.49 dB from att 4). Taking att 4 would be tuning our own estimator,
   which is the failure mode this probe's order exists to avoid. Recorded here
   because it is exactly the number a later reader will want to argue with.

6. IT COSTS 7.05 dB OF THE VOICE'S LEVEL, AND THAT IS A SEPARATE REPAIR. The
   455 Hz mode was setting the rimshot's peak, so attenuating its drive takes
   the whole voice down with it: `drums_fx_render.py --balance` reads RS at
   0.190 FS against its 0.4286 share of Roland's chart. PEAK_RSG (the gate, and
   the LAST stage in the path) carries the x2.2532 back. The balance estimator
   level-matches, so it would have scored a rimshot 7 dB too quiet as fixed --
   the kit-level test is what caught it, not the score.

WRONG BEFORE IT WAS RIGHT (2, both caught by a gate rather than by reading):
  1. The first version of the fix changed `kit_808()` and nothing else, which
     silently moved `kit_808_rev11()` -- the register image the PUBLISHED Arty
     release was verified with. `KitRefused` fired on the frozen hash, which is
     exactly what that gate is for. The rev-11 kit now undoes the write.
  2. The second version left PEAK_RSG alone, so the rimshot's balance was right
     and the rimshot was 7.05 dB too quiet -- invisible to D10A, which
     level-matches, and caught by `test_kit_voices_sit_at_the_chart_levels`
     (RS 0.190 FS against a 0.4286 target). Finding 6 is that bug's record.

Needs the Fischer corpus at $GF180_TR808_REFS / $TR808_REFS / /tmp/tr808-ref
for `confirm`, and REFUSES rather than reporting without it. `derive` and
`sweep` need no corpus and say so.
"""
import math
import os
import pathlib
import sys

import numpy as np

ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "model"))
sys.path.insert(0, str(ROOT / "tools"))
import drums_fx as dx                             # noqa: E402
import partial_trajectory as PT                   # noqa: E402
import run_case as RC                             # noqa: E402

OP = RC.RS_BALANCE_OP
WIN_KW = dict(win_ms=OP["win_ms"], hop_ms=OP["hop_ms"], t_end=OP["t_end"])
# The band the issue's own table walks, plus the two ends it reads the shelf
# between. 1450 Hz is the reference point both sides of #388's table use.
BAND_HZ = (1300, 1400, 1450, 1500, 1600, 1700, 1800, 1900, 2000, 2100)
SHELF_REF_HZ = 1450.0


class Refused(Exception):
    """A precondition of the apparatus failed. REFUSED is a first-class
    outcome here, distinct from pass and from fail."""


def refdir() -> pathlib.Path:
    for var in ("GF180_TR808_REFS", "TR808_REFS"):
        if os.environ.get(var):
            return pathlib.Path(os.environ[var])
    return pathlib.Path("/tmp/tr808-ref")


# --------------------------------------------------------------------------
# derive -- the two normalisations, no recording anywhere in it
# --------------------------------------------------------------------------
def bank_peak_db(f0: float, sr: int = dx.SR) -> float:
    """Peak of the all-pole mode's impulse response, in dB. h[n] =
    r^n sin((n+1)w)/sin(w) for y[n] = x[n] + 2r cos(w) y[n-1] - r^2 y[n-2], so
    at r ~ 1 the peak is 1/sin(w) and Q does not enter."""
    return -20.0 * math.log10(math.sin(2.0 * math.pi * f0 / sr))


def circuit_peak_db(f0: float, q: float) -> float:
    """Peak of the analog band-pass impulse response at unit midband gain, in
    dB re 1 rad/s. H(s) = H0 (w0/Q)s/(s^2+(w0/Q)s+w0^2) has h(0+) = H0 w0/Q,
    so at equal H0 the peak is proportional to the network's BANDWIDTH."""
    return 20.0 * math.log10(2.0 * math.pi * f0 / q)


def derived_correction_db() -> tuple[float, float, float]:
    """(bank high-re-low, circuit high-re-low, the correction) in dB, from the
    shipping constants only."""
    bank = bank_peak_db(dx.RS_HI_HZ) - bank_peak_db(dx.RS_LO_HZ)
    circ = (circuit_peak_db(dx.RS_HI_HZ, dx.RS_HI_Q)
            - circuit_peak_db(dx.RS_LO_HZ, dx.RS_LO_Q))
    return bank, circ, circ - bank


def cmd_derive() -> int:
    print("NO RECORDING IS READ BY THIS COMMAND. Everything below comes from")
    print("model/drums_fx.py's RS_LO_HZ/Q, RS_HI_HZ/Q and the two impulse")
    print("responses' closed forms.\n")
    print(f"    low  network   {dx.RS_LO_HZ:7.1f} Hz  Q {dx.RS_LO_Q:5.2f}")
    print(f"    high network   {dx.RS_HI_HZ:7.1f} Hz  Q {dx.RS_HI_Q:5.2f}")
    print(f"    sample rate    {dx.SR} Hz\n")
    print(f"    {'':28s} {'low':>10s} {'high':>10s} {'high re low':>13s}")
    print(f"    {'bank 1/sin(w0), dB':28s} {bank_peak_db(dx.RS_LO_HZ):10.2f} "
          f"{bank_peak_db(dx.RS_HI_HZ):10.2f} "
          f"{bank_peak_db(dx.RS_HI_HZ)-bank_peak_db(dx.RS_LO_HZ):+13.2f}")
    print(f"    {'circuit H0 w0/Q, dB':28s} {circuit_peak_db(dx.RS_LO_HZ, dx.RS_LO_Q):10.2f} "
          f"{circuit_peak_db(dx.RS_HI_HZ, dx.RS_HI_Q):10.2f} "
          f"{circuit_peak_db(dx.RS_HI_HZ, dx.RS_HI_Q)-circuit_peak_db(dx.RS_LO_HZ, dx.RS_LO_Q):+13.2f}")
    bank, circ, corr = derived_correction_db()
    print(f"\n    the discretisation's own error, high re low: {corr:+.2f} dB")
    print(f"    `att` is 3 bits of {20*math.log10(2):.2f} dB, so the available steps are")
    for a in range(5):
        mark = "  <- RS_LO_X_ATT" if a == dx.RS_LO_X_ATT else ""
        print(f"        att {a}   {a*20*math.log10(2):6.2f} dB   "
              f"residual {corr - a*20*math.log10(2):+6.2f} dB{mark}")
    print("\n    Q DOES NOT APPEAR IN THE BANK COLUMN. 1/sin(w0) is a function of f0")
    print("    alone, so no Q that keeps the reference's 2.4 ms tau can close this;")
    print("    `sweep` measures that rather than asserting it.")
    return 0


# --------------------------------------------------------------------------
# rendering at a chosen (att, Q, accent)
# --------------------------------------------------------------------------
def render(att: int | None = None, hi_q: float | None = None,
           accent: float = 1.0):
    """`run_case`'s OWN render + prepare chain with two constants overridden.
    Overriding the module globals is deliberate: `kit_808()` reads them at call
    time, so this measures the shipping code path with a different constant and
    not a reimplementation of it."""
    old = (dx.RS_LO_X_ATT, dx.RS_HI_Q)
    try:
        if att is not None:
            dx.RS_LO_X_ATT = int(att)
        if hi_q is not None:
            dx.RS_HI_Q = float(hi_q)
        x, sr = RC.render_drum_solo("RS", accent=accent)
    finally:
        dx.RS_LO_X_ATT, dx.RS_HI_Q = old
    return RC.prepare(x, sr, side="our RS render"), sr


def measure(y, sr) -> dict:
    """Everything D10A scores on the RS row, plus the band shape the issue
    tables, from one prepared record."""
    e = RC.balance_trajectory_db(y, sr, OP["f_lo_range"], OP["f_hi_range"],
                                 win_ms=OP["win_ms"], hop_ms=OP["hop_ms"],
                                 t_end=OP["t_end"], guards=OP["guards"],
                                 min_gap_ms=OP["min_gap_ms"])
    f_lo = PT.find_partial(y, sr, *OP["f_lo_range"], seconds=OP["t_end"])
    f_hi = PT.find_partial(y, sr, *OP["f_hi_range"], seconds=OP["t_end"])
    out = dict(balance=e.value if e.ok else None,
               why=e.reason, f_lo=f_lo, f_hi=f_hi,
               t1_ms=e.detail.get("t1_ms") if e.ok else None)
    if f_lo is not None:
        _, al = PT.trajectory(y, sr, f_lo, **WIN_KW)
        ref = float(al.max())
        band = {}
        for f in BAND_HZ:
            _, a = PT.trajectory(y, sr, float(f), **WIN_KW)
            band[f] = 20.0 * math.log10(max(float(a.max()), 1e-12) / ref)
        out["band"] = band
        peak_f = max((f for f in BAND_HZ if 1600 <= f <= 1900), key=lambda f: band[f])
        out["shelf_db"] = band[peak_f] - band[SHELF_REF_HZ]
        out["peak_hz"] = peak_f
        if f_hi is not None:
            _, ah = PT.trajectory(y, sr, f_hi, **WIN_KW)
            out["peak_ratio_db"] = 20.0 * math.log10(float(ah.max()) / ref)
    return out


def scored_rows(y, sr) -> dict:
    """D10A's other two rows, through run_case's own estimator table, so a
    regression in them shows up here and not only in the scorecard."""
    rows = {}
    for name, units, fn, _tol in RC.DRUM_PLAN["RS"]:
        if name == "Partial balance":
            continue
        est = fn(y, sr)
        rows[name] = (est.value if est.ok else None, units)
    return rows


# --------------------------------------------------------------------------
# sweep -- the measurement that decides Q against drive
# --------------------------------------------------------------------------
def _row(tag, m, rows):
    bal = f"{m['balance']:+7.2f}" if m["balance"] is not None else "REFUSED"
    shelf = f"{m.get('shelf_db', float('nan')):5.1f}"
    att_ms = rows.get("attack", (None,))[0]
    dec_ms = rows.get("tail decay", (None,))[0]
    return (f"   {tag:22s} {bal:>8s} {m.get('peak_ratio_db', float('nan')):+8.2f} "
            f"{shelf:>7s} {m.get('peak_hz', 0):7d} "
            f"{(att_ms if att_ms is not None else float('nan')):7.2f} "
            f"{(dec_ms if dec_ms is not None else float('nan')):8.2f}")


def _header(title):
    print(title)
    print(f"   {'setting':22s} {'bal1':>8s} {'peak re lo':>8s} {'shelf':>7s} "
          f"{'peak Hz':>7s} {'attack':>7s} {'decay':>8s}")


def cmd_sweep() -> int:
    print("NO RECORDING IS READ BY THIS COMMAND EITHER -- it is our own render at")
    print("a range of settings. `bal1` is D10A's scored instant; `peak re lo` is")
    print("the high mode's trajectory peak re the low mode's; `shelf` is how far")
    print(f"the {SHELF_REF_HZ:.0f} Hz level sits BELOW the peak near 1700-1800 Hz --")
    print("small = a flat shelf, large = a resonance. The machine reads 8.5 dB.")
    print("`attack` and `decay` are D10A's other two rows, in ms.\n")

    _header("A. Q, AT THE SHIPPING DRIVE (RS_LO_X_ATT = 0). tau = Q / (pi f0);")
    for q in (13.5, 20.0, 27.0, 40.0, 54.0):
        y, sr = render(att=0, hi_q=q)
        tau = q / (math.pi * dx.RS_HI_HZ) * 1e3
        print(_row(f"Q {q:5.1f} (tau {tau:4.1f} ms)", measure(y, sr), scored_rows(y, sr)))
    print("   reference 5 measures the high network at tau 2.4 ms, i.e. Q 13.5.\n")

    _header("B. DRIVE, AT THE SHIPPING Q (13.5). att is a right shift on the")
    for a in range(5):
        y, sr = render(att=a, hi_q=13.5)
        print(_row(f"att {a} ({a*6.02:5.2f} dB)", measure(y, sr), scored_rows(y, sr)))
    print("   LOW network's excitation path, so it lifts the high mode RELATIVE")
    print("   to the low without touching either network's tuning.\n")
    _, _, corr = derived_correction_db()
    print(f"   The closed form says the correction is {corr:+.2f} dB (`derive`), which")
    print(f"   is att {dx.RS_LO_X_ATT}. Nothing in section B was used to choose it.")
    return 0


# --------------------------------------------------------------------------
# confirm -- against the machine, on the scored accent and on another one
# --------------------------------------------------------------------------
def reference():
    d = refdir()
    if not d.exists():
        raise Refused(f"reference corpus not at {d} -- clone "
                      f"tidalcycles/sounds-tr808-fischer or set GF180_TR808_REFS")
    if not (d / RC.REF_MAIN["RS"][0]).exists():
        raise Refused(f"reference recording missing: {RC.REF_MAIN['RS'][0]} under {d}")
    x, sr, rel, setting = RC.load_reference("RS", d)
    return RC.prepare(x, sr, side=f"the reference recording {rel}"), sr, rel, setting


def cmd_confirm() -> int:
    ref_y, ref_sr, rel, setting = reference()
    rm = measure(ref_y, ref_sr)
    print(f"PROVENANCE  reference {refdir()/rel} ({setting}); Fischer/Technopolis")
    print(f"            1994, CC0-1.0, real TR-808 s/n 103852")
    print(f"            ours run_case.render_drum_solo('RS') + prepare, "
          f"drums_fx@{RC._sha(ROOT/'model'/'drums_fx.py')}")
    print(f"            RS_LO_X_ATT = {dx.RS_LO_X_ATT}   RS_HI_Q = {dx.RS_HI_Q}\n")

    print("1. ACCENT 1.0 -- the accent D10A scores, i.e. the one the fix was")
    print("   selected on. Not confirmation; the baseline for it.")
    _header("")
    print(_row("reference", rm, scored_rows(ref_y, ref_sr)))
    for a in (0, dx.RS_LO_X_ATT):
        y, sr = render(att=a)
        print(_row(f"ours, att {a}", measure(y, sr), scored_rows(y, sr)))

    print("\n2. A DIFFERENT ACCENT -- NOT used to choose anything. The rimshot's")
    print("   VCA is a tanh, so a level change is not a gain change: if the")
    print("   correction only worked at the accent it was derived at, it would")
    print("   be a fit and this is where that shows.")
    for accent in (0.5, 0.7, 1.5):
        _header(f"\n   accent {accent}")
        for a in (0, dx.RS_LO_X_ATT):
            y, sr = render(att=a, accent=accent)
            print(_row(f"ours, att {a}", measure(y, sr), scored_rows(y, sr)))

    print("\n3. THE BAND'S SHAPE, dB re each record's own low-partial peak -- the")
    print("   issue's own table, which is a SHAPE and not the scored instant.")
    rows = [("machine", rm)]
    for a in (0, dx.RS_LO_X_ATT):
        y, sr = render(att=a)
        rows.append((f"ours att {a}", measure(y, sr)))
    print("   " + f"{'Hz':14s}" + "".join(f"{f:>8d}" for f in BAND_HZ))
    for name, m in rows:
        print("   " + f"{name:14s}" + "".join(f"{m['band'][f]:>8.1f}" for f in BAND_HZ))
    print("\n   Read the SHAPE, not the level: every row is re its own low partial,")
    print("   so a row that rises to a point has a resonance and a row that runs")
    print("   flat has a shelf.")
    return 0


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "derive"
    try:
        sys.exit({"derive": cmd_derive, "sweep": cmd_sweep,
                  "confirm": cmd_confirm}[cmd]())
    except Refused as e:
        print(f"REFUSED  {e}")
        sys.exit(2)
    except KeyError:
        print(__doc__)
        sys.exit(64)
