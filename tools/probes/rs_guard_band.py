#!/usr/bin/env python3
"""Why D10A's "Partial balance" REFUSES on our own rimshot: is our 900-1100 Hz
mid-band content elevated, or is the guard band the wrong place to read a floor?

#109 gave D10A a floor-gated instant-amplitude-ratio estimator
(`run_case.balance_trajectory_db`). On our own RS render it refuses: no instant
in [0, 60] ms clears the record's own floor by 6 dB on BOTH partials at once
(best joint headroom 2.5 dB at t=12.5 ms). #380 named two candidate causes and
deliberately did not pick one:

  (a) our rimshot exciter / bridged-T networks put real unwanted energy at
      900-1100 Hz that a real TR-808 does not have at comparable level -- a
      sound defect, in scope for milestone #282;
  (b) `RS_BALANCE_OP`'s guards (900.0, 1100.0) do not generalise to our own
      model's spectral shape -- an estimator retune, not a model defect.

WHAT THIS PROBE ANSWERS, AND HOW IT AVOIDS ANSWERING WITH OUR OWN MODEL.
The comparison that settles (a) cannot be "our mid-band looks loud"; every
level here is peak-normalised by `run_case.prepare`, and the two records have
their peak in DIFFERENT modes, so a raw level comparison is a comparison of
normalisations. Every number this probe prints for a real record is therefore
quoted **in dB re that record's OWN low-partial peak** -- a ratio internal to
each side, so the two sides can be compared without either one's normalisation
entering. And because a 6 ms Hann window's own sidelobes put a floor at
900-1100 Hz even in a record with literally zero energy there, each real record
is printed beside a SYNTHETIC two-partial control at the same frequencies and
the same balance, which has exactly zero guard-band energy by construction. The
difference between the two is the record's real mid-band content; the synthetic
alone is the apparatus's own leakage. Comparing the real records without that
control is how you report window leakage as a defect (CLAUDE.md's own example).

    python tools/probes/rs_guard_band.py validate   # red first, then known balances
    python tools/probes/rs_guard_band.py compare    # ours vs the Fischer reference
    python tools/probes/rs_guard_band.py guards     # candidate guard sets, both records

Needs the Fischer corpus (tidalcycles/sounds-tr808-fischer) at $GF180_TR808_REFS
/ $TR808_REFS or /tmp/tr808-ref. REFUSES rather than reports when it is absent:
`compare` and `guards` have no meaning without the independent signal, and a
probe that answers anyway is worse than one that is absent.

THE ANSWER THIS PROBE MEASURED (2026-09-27, see the PR for #380 for the tables):

  (a) REFUTED. Relative to its own low partial, our 900-1100 Hz band is
      QUIETER than the machine's, not louder: at 900 Hz, ours -12.6 dB re our
      low-partial peak against the reference's -7.8 dB. Leakage-corrected
      against the synthetic controls, the real content above the window's own
      sidelobes is ~+5 dB for us and ~+7 dB for the machine. We do not have
      excess mid-band content; we have slightly less of it than the hardware.

  The whole difference is the HIGH partial. The machine's 1711 Hz mode sits
  +6.5 dB ABOVE its low mode; our 1795 Hz mode sits -12.1 dB BELOW ours. That
  18.7 dB deficit is what buries our high partial 0.5 dB above the floor while
  the machine's clears it by 14.2 dB. The refusal is not a false refusal and
  not a mid-band defect: it is the estimator declining to measure a partial
  that our model has very nearly failed to produce.

  (b) HOLDS, as the mechanism -- and the fix reports the defect rather than
      hiding it. The 900 Hz guard was still reading THE STRIKE: on a struck
      synthetic carrying only the low partial (zero steady guard-band energy),
      leakage reads -17.5 dB re that partial at 900 Hz against -19.9 at 1000
      and -21.5 at 1100, falling monotonically with distance from the partial.
      It is the onset step's broadband splash, not the window's stationary
      sidelobes -- the same partial with no onset step reads -35.2 dB at 900 Hz.
      `floor_at` takes the MAX over guards, so that one member set the floor
      2.4 dB high, which for a high mode already 0.5 dB above it was the whole
      difference between a verdict and a refusal.
      #380 therefore moved RS's lower guard 900 -> 1000 Hz. `guards` shows the
      retune costs NOTHING in contamination sensitivity (identical refusal
      thresholds at every injection frequency across the gap) and that the
      scored number is a property of the voice, not the guard: every in-gap
      candidate that clears at all reports the reference at +3.85 dB and ours
      at -11.7 dB -- an error of -15.5 dB against a 3.0 dB tolerance, a large
      FAIL, which is the sound defect #282 needs surfaced.

WRONG BEFORE IT WAS RIGHT (four, every one caught by a control or a test, none
by inspection -- the rate a reader should calibrate the tables above against):
  1. Predicted from the leakage survey that (900, 1100) was UNSATISFIABLE for
     any record whose high mode is >~12 dB below its low mode. `validate`'s
     satisfiability sweep refutes that outright: on a clean signal the shipping
     guards report down to -24 dB of true balance. The refusal on our render
     needs the record's REAL mid-band content, not leakage alone.
  2. The first contamination control injected nothing at 0 dB, because the
     injection level was tested with `if mid:` and 0 dB is amplitude 1.0 --
     a falsy sentinel on a real value. It printed "REPORTED" for the loudest
     contamination in the sweep, i.e. exactly backwards, and looked like data.
  3. The first known-balance accuracy table measured the truth at the window
     centre's ABSOLUTE time rather than its time since the onset, inventing a
     3-7 dB estimator error that does not exist (it is <=2.4 dB).
  4. The 900 Hz leakage was first attributed to the Hann window's first
     sidelobe, which peaks ~2.5 bins (417 Hz at win_ms=6.0) off centre and so
     lands almost exactly on 900 Hz for a 452 Hz partial -- an explanation that
     fitted the number and was wrong. `test_rs_guards_sit_clear_of_the_strikes
     _own_broadband_splash` refused to pass with it: a partial with no onset
     step reads 18 dB lower at the same frequency, so the leakage is the
     STRIKE's splash. The retune is the same either way; the stated reason
     would have been false, and a false mechanism is how the next guard gets
     placed wrong.
"""
import math
import os
import pathlib
import sys

import numpy as np

ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "model"))
sys.path.insert(0, str(ROOT / "tools"))
import partial_trajectory as PT                  # noqa: E402
import run_case as RC                            # noqa: E402

OP = RC.RS_BALANCE_OP
FLOOR_MARGIN_DB = 6.0
WIN_KW = dict(win_ms=OP["win_ms"], hop_ms=OP["hop_ms"], t_end=OP["t_end"])

# The synthetic controls' parameters. tau_lo/tau_hi are the rimshot's own
# order of magnitude (measure_partial_balance.py's known cases use 4.7/2.4 ms);
# 6/4 ms keeps a decaying high mode inside the 60 ms search either way.
SYN_SR = 48000
SYN_ONSET_S = 0.010
SYN_TAU_LO = 0.006
SYN_TAU_HI = 0.004
SYN_A_LO = 0.5

# Candidate guard sets. Every one sits in the 900-1200 Hz gap between the two
# modes ON PURPOSE: `guards` shows that a guard moved out of the gap (2.4 kHz
# and up) stops detecting injected mid-band contamination altogether, so
# "move the guard somewhere quiet" is not an available answer.
CANDIDATE_GUARDS = {
    "(900,1100) shipping": (900.0, 1100.0),
    "(1000,1100)": (1000.0, 1100.0),
    "(1050,1150)": (1050.0, 1150.0),
    "(1100,1150)": (1100.0, 1150.0),
    "(1100,)": (1100.0,),
    "(2400,2600) out of gap": (2400.0, 2600.0),
}


class Refused(Exception):
    """A precondition of the apparatus failed. REFUSED is a first-class
    outcome here, distinct from pass and from fail."""


# --------------------------------------------------------------------------
# signals
# --------------------------------------------------------------------------
def refdir() -> pathlib.Path:
    for var in ("GF180_TR808_REFS", "TR808_REFS"):
        if os.environ.get(var):
            return pathlib.Path(os.environ[var])
    return pathlib.Path("/tmp/tr808-ref")


def both_records():
    """The two sides, loaded and prepared by `run_case`'s OWN chain -- not a
    second copy of it. What D10A scores is `render_drum_solo` + `prepare`, so
    that is what this probe measures; a local reimplementation would be a
    different signal wearing the same name."""
    d = refdir()
    if not d.exists():
        raise Refused(f"reference corpus not at {d} -- clone "
                      f"tidalcycles/sounds-tr808-fischer or set GF180_TR808_REFS")
    if not (d / RC.REF_MAIN["RS"][0]).exists():
        raise Refused(f"reference recording missing: {RC.REF_MAIN['RS'][0]} under {d}")
    ref_x, ref_sr, rel, setting = RC.load_reference("RS", d)
    ours_x, ours_sr = RC.render_drum_solo("RS")
    return (RC.prepare(ref_x, ref_sr, side=f"the reference recording {rel}"), ref_sr,
            RC.prepare(ours_x, ours_sr, side="our RS render"), ours_sr, rel, setting)


def _damped(f, tau, amp, n, sr, phase=0.0):
    t = np.arange(n) / sr
    return amp * np.exp(-t / tau) * np.sin(2 * math.pi * f * t + phase)


def synth(balance_db, *, f_lo, f_hi, mid_db=None, mid_f=1000.0, seconds=0.25):
    """Two damped partials whose balance AT THE ONSET is chosen, with exactly
    zero energy at any guard frequency unless `mid_db` asks for some.

    `mid_db=None` means no injection. `mid_db=0.0` means an injected component
    AS LOUD AS the low partial -- which is why the sentinel is None and not a
    falsy 0: testing `if mid_db:` silently injected nothing at 0 dB and printed
    the loudest contamination in the sweep as cleanly REPORTED (see the module
    docstring's wrong-then-right list)."""
    n = int(seconds * SYN_SR)
    on = int(SYN_ONSET_S * SYN_SR)
    x = np.zeros(n)
    a_hi = SYN_A_LO * 10.0 ** (balance_db / 20.0)
    x[on:] = (_damped(f_lo, SYN_TAU_LO, SYN_A_LO, n - on, SYN_SR, 0.3)
              + _damped(f_hi, SYN_TAU_HI, a_hi, n - on, SYN_SR, 1.9))
    if mid_db is not None:
        x[on:] += _damped(mid_f, SYN_TAU_LO, SYN_A_LO * 10.0 ** (mid_db / 20.0),
                          n - on, SYN_SR, 0.7)
    return x


def synth_truth_at(balance_db, t_abs):
    """The synthetic's TRUE balance at absolute time `t_abs`, which is the
    balance at the onset plus the two taus' divergence over the time SINCE THE
    ONSET -- not since the start of the record. Measuring it from the start of
    the record invents a 3-7 dB estimator error out of the 10 ms lead-in."""
    d = max(0.0, t_abs - SYN_ONSET_S)
    return balance_db - 8.685889638 * d * (1.0 / SYN_TAU_HI - 1.0 / SYN_TAU_LO)


# --------------------------------------------------------------------------
# the gate, as `balance_trajectory_db` applies it
# --------------------------------------------------------------------------
def gate(x, sr, f_lo, f_hi, guards, *, margin_db=FLOOR_MARGIN_DB):
    """The floor gate at KNOWN partial frequencies -- the same arithmetic
    `balance_trajectory_db` uses, minus its `find_partial` search, so a sweep
    over hundreds of synthetic signals whose lines we CHOSE stays affordable.
    `guards`/`compare` call the shipping function itself on the real records;
    this fast path is only ever pointed at signals we synthesised."""
    ts, al = PT.trajectory(x, sr, f_lo, **WIN_KW)
    _, ah = PT.trajectory(x, sr, f_hi, **WIN_KW)
    floor = PT.floor_at(x, sr, f_lo, guards, **WIN_KW)
    with np.errstate(divide="ignore", invalid="ignore"):
        h_lo = 20.0 * np.log10(np.clip(al, 1e-300, None) / np.clip(floor, 1e-300, None))
        h_hi = 20.0 * np.log10(np.clip(ah, 1e-300, None) / np.clip(floor, 1e-300, None))
    joint = np.minimum(h_lo, h_hi)
    ok = (joint >= margin_db) & (al > 0) & (ah > 0)
    idx = np.nonzero(ok)[0]
    if len(idx) == 0:
        k = int(np.argmax(joint))
        return dict(ok=False, best_joint_db=float(joint[k]), t_best_ms=float(ts[k] * 1e3))
    i = int(idx[0])
    return dict(ok=True, t1_ms=float(ts[i] * 1e3),
                balance_db=float(20.0 * math.log10(ah[i] / al[i])),
                joint_db=float(joint[i]))


def shipping(x, sr, guards):
    """`run_case.balance_trajectory_db` itself, so `compare`/`guards` report
    the number D10A would score and not a lookalike."""
    return RC.balance_trajectory_db(
        x, sr, OP["f_lo_range"], OP["f_hi_range"], win_ms=OP["win_ms"],
        hop_ms=OP["hop_ms"], t_end=OP["t_end"], guards=guards,
        min_gap_ms=OP["min_gap_ms"])


def lines_of(x, sr):
    return (PT.find_partial(x, sr, *OP["f_lo_range"], seconds=OP["t_end"]),
            PT.find_partial(x, sr, *OP["f_hi_range"], seconds=OP["t_end"]))


# --------------------------------------------------------------------------
# validate -- red first, then signals whose answer is chosen
# --------------------------------------------------------------------------
def cmd_validate():
    print("RED FIRST -- the gate against inputs that carry no measurable pair.")
    print("These MUST refuse; this command exits 1 if any of them reports.")
    for name, x in (("digital silence", np.zeros(int(0.25 * SYN_SR))),
                    ("a partial that never decays",
                     np.sin(2 * math.pi * 452.0 * np.arange(int(0.25 * SYN_SR)) / SYN_SR)),
                    ("only the low partial",
                     synth(-400.0, f_lo=452.0, f_hi=1795.0))):
        r = gate(x, SYN_SR, 452.0, 1795.0, (900.0, 1100.0))
        if r["ok"]:
            print(f"    {name:28s} REPORTED {r['balance_db']:+.2f} dB "
                  f"-- the bench cannot fail")
            return 1
        print(f"    {name:28s} REFUSED  (best joint headroom "
              f"{r['best_joint_db']:+.1f} dB)")

    print("\nNOT ASSERTED, BECAUSE IT IS FALSE -- and that is a finding, not a")
    print("relaxation. WHITE NOISE IS REPORTED, not refused, by the SHIPPING")
    print("`run_case.balance_trajectory_db` at D10A's own operating point:")
    for seed in range(4):
        x = 0.01 * np.random.default_rng(seed).standard_normal(int(0.25 * SYN_SR))
        e = shipping(x, SYN_SR, OP["guards"])
        verdict = (f"REPORTED bal1 {e.value:+7.2f} dB at t1 {e.detail['t1_ms']:5.2f} ms"
                   if e.ok else "refused")
        print(f"    white noise, seed {seed}          {verdict}")
    print("    The joint-headroom gate compares partials found by `find_partial`")
    print("    (the STRONGEST line in each search range -- an upward-biased pick on")
    print("    noise) against guards read at FIXED frequencies (an unbiased one), so")
    print("    the headroom it measures on a record with no partials at all is a")
    print("    selection artifact. `measure_partial_balance.py validate` does refuse")
    print("    noise, but through `fit_decay`'s decay fit, not through this gate.")
    print("    Out of scope for #380 (which is about WHERE the floor is read, not")
    print("    about what the gate does with a record that has no partials) and")
    print("    filed separately; recorded here so a reader of the tables below knows")
    print("    the 6 dB gate is weaker than it looks.")

    print("\nKNOWN BALANCES -- two damped partials, balance at the onset CHOSEN,")
    print("zero energy at any guard by construction. 'err' is the gate's balance")
    print("minus the truth at the same instant, so an accurate gate reads ~0.")
    sets = {k: v for k, v in CANDIDATE_GUARDS.items()
            if k in ("(900,1100) shipping", "(1000,1100)", "(1100,1150)")}
    print(f"    {'true':>6s} " + "  ".join(f"{k:>30s}" for k in sets))
    worst = {k: 0.0 for k in sets}
    for bal in (0, -3, -6, -9, -12, -15, -18, -21, -24, -30):
        cells = []
        x = synth(float(bal), f_lo=452.0, f_hi=1795.0)
        for k, g in sets.items():
            r = gate(x, SYN_SR, 452.0, 1795.0, g)
            if not r["ok"]:
                cells.append(f"REFUSED (best {r['best_joint_db']:+.1f} dB)")
                continue
            err = r["balance_db"] - synth_truth_at(float(bal), r["t1_ms"] / 1e3)
            worst[k] = max(worst[k], abs(err))
            cells.append(f"t1 {r['t1_ms']:5.1f} ms  bal {r['balance_db']:+7.2f}  "
                         f"err {err:+5.2f}")
        print(f"    {bal:+6d} " + "  ".join(f"{c:>30s}" for c in cells))
    print()
    for k in sets:
        print(f"    worst |err| over the balances it did not refuse, {k}: "
              f"{worst[k]:.2f} dB")
    print("    (measure_partial_balance.py declares +-2.4 dB for this operating point;")
    print("     a retune that stayed inside it has not traded accuracy for a verdict.)")
    print("\n    SATISFIABILITY, read off the same table: the shipping guards report")
    print("    down to -24 dB of true balance on a CLEAN signal and refuse at -30, so")
    print("    they are not unsatisfiable at our own -12 dB balance. The refusal on our")
    print("    render needs the record's REAL mid-band content, not leakage alone --")
    print("    which is the prediction this control refuted (see the module docstring).")
    return 0


# --------------------------------------------------------------------------
# compare -- the issue's asked-for diagnostic, with a leakage control per side
# --------------------------------------------------------------------------
def cmd_compare():
    ref_y, ref_sr, our_y, our_sr, rel, setting = both_records()
    print("PROVENANCE")
    print(f"    reference   {refdir() / rel}  ({setting}); Fischer/Technopolis 1994,")
    print(f"                CC0-1.0, real TR-808 s/n 103852")
    print(f"    ours        run_case.render_drum_solo('RS') + run_case.prepare, "
          f"drums_fx@{RC._sha(ROOT / 'model' / 'drums_fx.py')}")
    print(f"    window      win_ms={OP['win_ms']}  hop_ms={OP['hop_ms']}  "
          f"t_end={OP['t_end']*1e3:.0f} ms  guards={OP['guards']}  "
          f"floor margin {FLOOR_MARGIN_DB:.0f} dB")

    sides = {}
    for name, y, sr in (("reference", ref_y, ref_sr), ("ours", our_y, our_sr)):
        f_lo, f_hi = lines_of(y, sr)
        ts, al = PT.trajectory(y, sr, f_lo, **WIN_KW)
        _, ah = PT.trajectory(y, sr, f_hi, **WIN_KW)
        # the leakage-only control: the SAME lines and the SAME balance, with
        # zero guard-band energy. Whatever the real record reads above this is
        # its real mid-band content; what it reads at this level is our window.
        bal = 20.0 * math.log10(ah.max() / al.max())
        ctl = synth(bal, f_lo=f_lo, f_hi=f_hi)
        _, cl = PT.trajectory(ctl, SYN_SR, f_lo, **WIN_KW)
        sides[name] = dict(y=y, sr=sr, f_lo=f_lo, f_hi=f_hi, ts=ts, al=al, ah=ah,
                           ref_lvl=float(al.max()), bal=bal,
                           ctl=ctl, ctl_lvl=float(cl.max()))
        print(f"\n  {name:10s} lines actually present: low {f_lo:7.2f} Hz  "
              f"high {f_hi:7.2f} Hz")
        print(f"             peak of each mode's own trajectory: low {al.max():.4f}  "
              f"high {ah.max():.4f}")
        print(f"             HIGH MODE re its OWN LOW MODE: {bal:+6.2f} dB")

    print("\n" + "=" * 96)
    print("1. THE MID-BAND, IN dB RE EACH RECORD'S OWN LOW-PARTIAL PEAK")
    print("   (a ratio internal to each side, so neither side's peak "
          "normalisation enters)")
    print("   'leak' is the synthetic control at the same lines and the same "
          "balance with ZERO")
    print("   guard-band energy -- i.e. this window's own sidelobes. "
          "'real' = actual - leak.")
    print("=" * 96)
    freqs = [700, 800, 850, 900, 950, 1000, 1050, 1100, 1150, 1200, 1250, 1300]
    for name, d in sides.items():
        act, leak = [], []
        for f in freqs:
            _, a = PT.trajectory(d["y"], d["sr"], float(f), **WIN_KW)
            act.append(20.0 * math.log10(max(a.max(), 1e-12) / d["ref_lvl"]))
            _, c = PT.trajectory(d["ctl"], SYN_SR, float(f), **WIN_KW)
            leak.append(20.0 * math.log10(max(c.max(), 1e-12) / d["ctl_lvl"]))
        print(f"\n  {name}")
        print("    Hz      " + "".join(f"{f:>8d}" for f in freqs))
        print("    actual  " + "".join(f"{v:>8.1f}" for v in act))
        print("    leak    " + "".join(f"{v:>8.1f}" for v in leak))
        print("    real    " + "".join(f"{a-l:>8.1f}" for a, l in zip(act, leak)))
    r, o = sides["reference"], sides["ours"]
    print("\n  READ IT AS: 'actual' is where the floor gate reads its floor. If OUR")
    print("  'actual' row were ABOVE the reference's, hypothesis (a) would hold.")

    print("\n" + "=" * 96)
    print("2. THE GATE, INSTANT BY INSTANT -- both partials and the floor, in dB")
    print("   re the same record's low-partial peak, with the joint headroom the")
    print("   gate actually tests.")
    print("=" * 96)
    for name, d in sides.items():
        floor = PT.floor_at(d["y"], d["sr"], d["f_lo"], OP["guards"], **WIN_KW)
        print(f"\n  {name}   guards={OP['guards']}")
        print(f"    {'t (ms)':>7s} {'low':>8s} {'high':>8s} {'floor':>8s}   "
              f"{'low-floor':>9s} {'high-floor':>10s} {'JOINT':>7s}")
        for t in (0.008, 0.010, 0.0125, 0.014, 0.016, 0.020, 0.025, 0.030, 0.040):
            i = int(np.argmin(np.abs(d["ts"] - t)))
            if min(d["al"][i], d["ah"][i], floor[i]) <= 0:
                continue
            lo = 20.0 * math.log10(d["al"][i] / d["ref_lvl"])
            hi = 20.0 * math.log10(d["ah"][i] / d["ref_lvl"])
            fl = 20.0 * math.log10(floor[i] / d["ref_lvl"])
            print(f"    {d['ts'][i]*1e3:7.2f} {lo:8.1f} {hi:8.1f} {fl:8.1f}   "
                  f"{lo-fl:9.1f} {hi-fl:10.1f} {min(lo-fl, hi-fl):7.1f}")

    print("\n" + "=" * 96)
    print("3. THE VERDICT ON #380's TWO HYPOTHESES")
    print("=" * 96)
    mid_ours = _at(sides["ours"], 900.0)
    mid_ref = _at(sides["reference"], 900.0)
    print(f"  (a) excess mid-band in our model: at 900 Hz, ours {mid_ours:+.1f} dB re our")
    print(f"      own low-partial peak against the machine's {mid_ref:+.1f} dB "
          f"-- ours is")
    print(f"      {mid_ref - mid_ours:.1f} dB {'QUIETER' if mid_ours < mid_ref else 'LOUDER'} "
          f"there, so (a) is "
          f"{'REFUTED' if mid_ours < mid_ref else 'SUPPORTED'}.")
    print(f"  the high mode is the whole difference: machine {r['bal']:+.2f} dB re its")
    print(f"      own low mode, ours {o['bal']:+.2f} dB -- a {r['bal']-o['bal']:.1f} dB "
          f"deficit, which is")
    print(f"      the sound defect D10A's 'Partial balance' exists to score.")
    print(f"  (b) run `guards` for the retune, the number it reports, and its cost.")
    return 0


def _at(d, f):
    _, a = PT.trajectory(d["y"], d["sr"], float(f), **WIN_KW)
    return 20.0 * math.log10(max(a.max(), 1e-12) / d["ref_lvl"])


# --------------------------------------------------------------------------
# guards -- candidate guard sets against both records and against contamination
# --------------------------------------------------------------------------
def cmd_guards():
    ref_y, ref_sr, our_y, our_sr, rel, _ = both_records()
    print("A. THE SHIPPING ESTIMATOR (run_case.balance_trajectory_db) ON BOTH REAL")
    print("   RECORDS, per candidate guard set. `bal1` is the SCORED number; `bal2`")
    print("   and the slope are #109's unscored evidence. A retune is only honest if")
    print("   `bal1` is a property of the voice and not of the guard.")
    print(f"   {'candidate':24s} {'reference bal1':>16s} {'t1':>7s}   "
          f"{'ours bal1':>16s} {'t1':>7s}")
    vals = {}
    for k, g in CANDIDATE_GUARDS.items():
        cells = []
        for y, sr in ((ref_y, ref_sr), (our_y, our_sr)):
            e = shipping(y, sr, g)
            if e.ok:
                cells.append(f"{e.value:+16.2f} {e.detail['t1_ms']:6.2f}m")
            else:
                cells.append(f"{'REFUSED':>16s} {'-':>7s}")
        vals[k] = cells
        print(f"   {k:24s} {cells[0]}   {cells[1]}")
    print("\n   D10A scores ours against the reference at a 3.0 dB tolerance, so a")
    print("   candidate that reports on both sides gives the error in the last column:")
    for k, g in CANDIDATE_GUARDS.items():
        a, b = shipping(ref_y, ref_sr, g), shipping(our_y, our_sr, g)
        if a.ok and b.ok:
            err = b.value - a.value
            print(f"   {k:24s} error {err:+7.2f} dB  -> "
                  f"{'FAIL' if abs(err) > 3.0 else 'pass'} "
                  f"(worst = {abs(err)/3.0:.2f})")

    print("\nB. WHAT EACH CANDIDATE COSTS IN CONTAMINATION SENSITIVITY.")
    print("   A real mid-band component is injected into a clean two-partial signal")
    print("   of true balance -12 dB (ours). The cell is the LOWEST injected level,")
    print("   in dB re the low partial, at which that candidate refuses. More")
    print("   negative = catches quieter contamination = a better guard. 'none' means")
    print("   it never refused, up to a component as loud as the low partial itself.")
    inject_f = (900, 1000, 1100, 1200)
    levels = (-36, -30, -24, -21, -18, -15, -12, -9, -6, -3, 0)
    print(f"   {'candidate':24s} " + "".join(f"{f:>9d} Hz" for f in inject_f))
    for k, g in CANDIDATE_GUARDS.items():
        row = []
        for mf in inject_f:
            thr = None
            for lvl in levels:
                x = synth(-12.0, f_lo=452.0, f_hi=1795.0, mid_db=float(lvl),
                          mid_f=float(mf))
                if not gate(x, SYN_SR, 452.0, 1795.0, g)["ok"]:
                    thr = lvl
                    break
            row.append(f"{thr:>+9d} dB" if thr is not None else f"{'none':>9s}   ")
        print(f"   {k:24s} " + "".join(row))
    print("\n   READ IT AS: (1000,1100) refuses at exactly the same injected level as")
    print("   the shipping (900,1100) at every frequency across the gap, so dropping the")
    print("   900 Hz member buys the retune its verdict at ZERO cost in contamination")
    print("   sensitivity -- that is the measurement that distinguishes this from a gate")
    print("   loosened to manufacture an answer. Moving further up DOES cost: (1050,1150)")
    print("   loses 3 dB at a 900 Hz injection and (1100,1150)/(1100,) lose 6 dB, which")
    print("   is why the smallest move that clears the strike's splash was taken. A guard")
    print("   moved OUT of the 900-1200 Hz gap ('(2400,2600) out of gap') stops guarding")
    print("   altogether, so 'put the guard somewhere quiet' is not an available answer.")
    return 0


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "validate"
    try:
        sys.exit({"validate": cmd_validate, "compare": cmd_compare,
                  "guards": cmd_guards}[cmd]())
    except Refused as e:
        print(f"REFUSED  {e}")
        sys.exit(2)
    except KeyError:
        print(__doc__)
        sys.exit(64)
