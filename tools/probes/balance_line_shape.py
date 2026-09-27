#!/usr/bin/env python3
"""Is the line `find_partial` returned a PARTIAL, or the maximum of a
fluctuation? The measurements that chose `partial_trajectory.line_is_resolved`
and its one tolerance -- and the four candidate gates they REFUTED (#389).

WHY A NEW PRECONDITION EXISTED TO BE CHOSEN. `run_case.balance_trajectory_db`
-- the floor-gated instant-ratio estimator D10A ("Partial balance", rimshot)
and D13A (cowbell) are scored on -- REPORTED a balance on pure white noise
instead of refusing, at both shipping operating points, for most seeds. The
gate compared a partial CHOSEN as the strongest line over ~4800 and ~14000 grid
points against a floor read at one or two FIXED guard frequencies, so its
"headroom" on a record with no partials was the pick's own selection bias.

The shipping gate cannot be tightened to close that. Our own rimshot clears the
6 dB joint-headroom gate by 0.4 dB while white noise clears it by 6 dB and more
(`noise` prints both columns), so ANY raised floor or raised margin -- including
#389's own suggestion of measuring the floor the same way the partials are
measured, a max over a grid of guards -- silences D10A, a real record with a
real defect in it, before it silences noise.

WHAT THIS PROBE MEASURES. Five candidate preconditions, on the same signals, so
the choice is a table and not an opinion. All five ask the same question of the
LINE'S SHAPE rather than its height, because the height is what the pick biased:

  drop1        the drop in coherent projection ONE search bin (1/T) off centre.
  ratio2       ((P0/P1)^2 - 1) at 2 bins over the same at 1 bin. A damped
               sinusoid's DTFT magnitude about its line is a Lorentzian, so
               (P0/Pd)^2 - 1 is proportional to d^2 and this ratio is exactly 4
               at ANY tau.
  slope        the log-log slope of (P0/Pd)^2 - 1 against d over all the offsets.
               The same d^2 law, so the closed form is exactly 2 -- read over a
               range of d instead of from two adjacent points.
  fit resid    a least-squares fit of the Lorentzian itself to the drops at
               +-n_bins, scored by its RMS residual in dB. SHIPPED.
  floorless /  the same fit with and without an additive pedestal term for
  with floor   whatever else the record holds at those offsets.

THE NUMBERS THE SHIPPING GATE WAS SET FROM (`margin 24`, 2026-09-27, 48 kHz),
over 8 real partials and 96 white-noise partials at n_bins=6:

    fit resid, no pedestal   records 0.22 .. 1.70 dB   noise 2.91 .. 12.60 dB
    fit resid, pedestal      records 0.22 .. 1.63 dB   noise 1.91 .. 10.82 dB
    tau / T                  records 0.037.. 0.156     noise 0.044.. 0.470

`max_resid_db=2.2` is the geometric mean of the no-pedestal edges: 1.29x of
margin on the worst real record, 1.32x on the best noise record. `sweep` chose
n_bins=6 the same way -- it is the width with the widest separation, and both
n_bins=3 and n_bins=24 are worse.

WHY THE OTHER FOUR LOST, and each of these is a measurement, not a preference:

  1. THE BARE DROP IS A FUNCTION OF tau/T, SO IT CANNOT BE A GATE. #389 suggested
     requiring the found line to "exceed the projection a resolution bin-width
     either side of it by some margin, which noise fails and a real mode
     passes". It is the other way round: a real percussive mode is BROAD (a
     Lorentzian of half-width 1/(pi*tau), which for the rimshot's 6 ms low mode
     is 53 Hz against a 16.7 Hz search resolution) so it barely falls at all one
     bin off, while NOISE is a knife-edge and falls much further, because at a
     spacing of exactly 1/T a rectangular window's own correlation is zero
     (sinc(1) = 0). Gated that way round, a drop threshold refuses every real
     record and passes the noise. Gated the other way round it STILL fails:
     drop1 = 10*log10(1 + (2*pi*tau/T)^2) is a function of tau/T alone, so a
     legitimate tau = T/3 mode reads 7.3 dB and lands inside the noise band.
     Measured: records 0.17..3.69 dB against noise 3.40..22.76 dB -- overlapping.
     `theory` prints both halves of that.

  2. ratio2 IS ILL-CONDITIONED EXACTLY WHERE THE REAL VOICES LIVE. It is
     tau-free, which is why it was tried, but at small tau/T the quantity it
     divides is a sub-dB difference: at tau = T/120 the drop it reads is 0.01 dB,
     so (P0/P1)^2 - 1 is 0.002 and a hair of leakage from elsewhere in the record
     swamps it. `theory` shows it far from its own closed form on legitimate
     damped sinusoids at tau <= 2 ms against T = 60 ms -- and the rimshot's HIGH
     mode is a ~1.5 ms mode on a 60 ms window.

  3. slope FIXES THE CONDITIONING AND STILL HAS NO MARGIN: records 1.13..2.66
     against noise -1.57..2.38, overlapping on the side that matters.

  4. THE PEDESTAL HELPS THE NULL MORE THAN THE SIGNAL. Adding a floor term is the
     physically honest model and it does lower the worst real record (1.70 ->
     1.63 dB) -- but it lowers the best noise record far more (2.91 -> 1.91 dB),
     because an extra free parameter is worth more to a record with no structure
     in it. Margin 1.71x -> 1.17x. This is the one result here that was decided
     purely by measurement against an argument from first principles, and the
     first principles lost.

  5. tau/T IS NOT A NOISE GATE, though it looks like one. Under the pedestal
     model 68 of 96 noise partials fit tau > T and it looks strong; without the
     pedestal 96 of 96 fit tau well inside T and it discriminates nothing. It is
     retained for the STEADY-TONE record only, and `line_is_resolved` says so.

COMMANDS:

    python tools/probes/balance_line_shape.py theory      # closed form; no corpus needed
    python tools/probes/balance_line_shape.py synthetics  # signals whose answer is chosen
    python tools/probes/balance_line_shape.py noise [N]   # N white-noise seeds, both OPs
    python tools/probes/balance_line_shape.py records      # the four real records
    python tools/probes/balance_line_shape.py survey [N]   # records vs noise, all candidates
    python tools/probes/balance_line_shape.py margin [N]   # the two shipping conditions' margins
    python tools/probes/balance_line_shape.py sweep [N]    # n_bins, the one free width

`records`, `survey`, `margin` and `sweep` need the Fischer corpus (tidalcycles/sounds-tr808-fischer)
at $GF180_TR808_REFS / $TR808_REFS / /tmp/tr808-ref and REFUSE without it.

WRONG BEFORE IT WAS RIGHT (5, every one caught by a measurement rather than by
inspection -- the rate a reader should calibrate the tables above against):

  1. The direction of the peak test, item 1. Written the way #389 suggested it,
     the gate refuses all four real records and passes noise.
  2. A first draft gated on `drop1` with the sign corrected (real records <=
     3.7 dB, noise >= 9.9 dB over 26 records, an apparently clean 6 dB gap). It
     is unusable for the tau/T reason in item 1, and `synthetics` carries the
     tau = T/3 case that refutes it.
  3. A second draft SHIPPED `ratio2` with ratio_tol_factor=2.0 and a docstring
     claiming "the ratio is 4 at every tau across two decades". Its own `theory`
     table printed REFUSE for three of eight legitimate taus in the same run,
     and it turned
     `test_balance_trajectory_db_excludes_a_point_below_the_floor` red. The claim
     came from measuring four real records at two taus and generalising; the
     table that refuted it took one command.
  4. The pedestal term, item 4 above -- added from correct physics, kept for two
     hours on an 8-seed sample where it looked neutral, and withdrawn when 24
     seeds showed it halving the margin. An 8-seed noise sample put the best
     noise residual at 4.18 dB; 24 seeds put it at 2.91 dB. A null distribution
     estimated from 8 draws is not a null distribution.
  5. A third draft set max_resid_db from a records-only measurement (3.5 dB,
     "records under 2.1, noise over 4.5") written into the docstring BEFORE
     `margin` was run. The real numbers were 1.70 and 2.91, so 3.5 would have
     passed white noise. The gate was never wrong in the tree -- the claim about
     it was, for about twenty minutes, in a docstring.

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

SR = 48000
OPS = {"RS (D10A)": RC.RS_BALANCE_OP, "CB (D13A)": RC.CB_BALANCE_OP}
NOISE_SECONDS = {"RS (D10A)": 0.25, "CB (D13A)": 0.75}


class Refused(Exception):
    """A precondition of the apparatus failed. REFUSED is a first-class
    outcome here, distinct from pass and from fail."""


def refdir() -> pathlib.Path:
    for var in ("GF180_TR808_REFS", "TR808_REFS"):
        if os.environ.get(var):
            return pathlib.Path(os.environ[var])
    return pathlib.Path("/tmp/tr808-ref")


def damped(f, tau, amp, n, sr, phase=0.0, onset=0):
    x = np.zeros(n)
    t = np.arange(n - onset) / sr
    x[onset:] = amp * np.exp(-t / tau) * np.sin(2 * math.pi * f * t + phase)
    return x


# --------------------------------------------------------------------------
# The four candidates, on the same 16 offsets, so only the statistic differs.
# --------------------------------------------------------------------------
def offsets_db(x, sr, f, seconds, n_bins=6):
    """(P0, d array in Hz, (P0/Pd)^2 - 1 array, drop array in dB) over +-n_bins
    search bins, from `partial_trajectory.line_offsets` -- the SAME measurement
    the shipping gate reads, so this probe cannot drift away from what runs."""
    p0, ds, drops = PT.line_offsets(x, sr, f, seconds=seconds, n_bins=n_bins)
    with np.errstate(over="ignore"):
        vs = 10.0 ** (drops / 10.0) - 1.0
    return p0, ds, vs, drops


def cand_drop1(p0, ds, vs, drops, seconds):
    """Mean drop one bin off centre, in dB. Refuted: a function of tau/T."""
    return float(np.mean(drops[:2]))


def cand_ratio2(p0, ds, vs, drops, seconds):
    """v(2d)/v(d), mean of the two sides. Closed form 4. Refuted:
    ill-conditioned at small tau/T, which is where the real voices are."""
    qs = []
    for side in (0, 1):
        den, num = vs[side], vs[2 + side]
        qs.append(num / den if den > 0 and math.isfinite(den) and math.isfinite(num)
                  else math.inf)
    finite = [q for q in qs if math.isfinite(q)]
    return float(np.mean(finite)) if finite else math.inf


def cand_slope(p0, ds, vs, drops, seconds):
    """log-log slope of v against d over all 16 offsets. Closed form 2.
    Refuted: conditioning is fixed but records and noise overlap."""
    m = np.isfinite(vs) & (vs > 0)
    if m.sum() < 4:
        return math.inf
    c = np.polyfit(np.log(ds[m]), np.log(vs[m]), 1)
    return float(c[0])


def cand_fit0(p0, ds, vs, drops, seconds):
    """RMS dB residual of a FLOORLESS Lorentzian fit. Refuted: a mode whose skirt
    has fallen into the record's own floor by the last bin cannot fit it."""
    return float(PT._lorentzian_fit(ds, drops, pedestal=False)["resid_db"])


def cand_fit(p0, ds, vs, drops, seconds):
    """RMS dB residual of the mode-plus-floor Lorentzian fit. SHIPPED."""
    return float(PT._lorentzian_fit(ds, drops)["resid_db"])


CANDS = {"drop1 dB": cand_drop1, "ratio2": cand_ratio2, "slope": cand_slope,
         "floorless dB": cand_fit0, "fit resid dB": cand_fit}


def row(x, sr, f, seconds):
    p0, ds, vs, drops = offsets_db(x, sr, f, seconds)
    vals = {k: fn(p0, ds, vs, drops, seconds) for k, fn in CANDS.items()}
    tau = PT._lorentzian_fit(ds, drops)["tau_ms"]
    shipped = PT.line_is_resolved(x, sr, f, seconds=seconds)
    return vals, tau, shipped


def fmt(vals, tau, shipped):
    cells = []
    for k, v in vals.items():
        cells.append(f"{k} {'inf' if not math.isfinite(v) else f'{v:7.2f}'}")
    t = "inf" if not math.isfinite(tau) else f"{tau:6.2f}"
    return "  ".join(cells) + f"   tau {t} ms   " + ("pass" if shipped["ok"] else "REFUSE")


# --------------------------------------------------------------------------
def cmd_theory(argv):
    """The closed forms, against damped sinusoids whose tau we CHOSE.

    This is the external grounding: every 'predicted' column comes from the DTFT
    of a damped sinusoid, not from anything measured in this repository."""
    T = 0.060
    print(f"Damped sinusoids at 452 Hz over T = {T*1e3:.0f} ms, so the search")
    print(f"resolution d = 1/T = {1/T:.2f} Hz. Closed forms: drop1 = "
          f"10*log10(1 + (2*pi*tau/T)^2), ratio2 = 4, slope = 2, fit resid = 0.")
    print(f"    {'tau (ms)':>9s} {'tau/T':>7s} {'drop1 pred':>11s}   verdicts per candidate")
    for tau_ms in (0.5, 1.0, 2.0, 4.0, 8.0, 16.0, 32.0, 64.0):
        tau = tau_ms * 1e-3
        x = damped(452.0, tau, 1.0, int(0.25 * SR), SR, 0.3)
        pred = 10.0 * math.log10(1.0 + (2 * math.pi * tau / T) ** 2)
        vals, t_fit, shipped = row(x, SR, 452.0, T)
        print(f"    {tau_ms:9.1f} {tau/T:7.3f} {pred:11.2f}   {fmt(vals, t_fit, shipped)}")
    print()
    print("    READ THE ratio2 COLUMN AT THE TOP THREE ROWS. Those are legitimate")
    print("    damped sinusoids and ratio2 is nowhere near its closed form of 4,")
    print("    because at tau/T <= 0.03 the drop it divides is under 0.2 dB. The")
    print("    rimshot's HIGH mode is a ~1.5 ms mode on a 60 ms window: tau/T =")
    print("    0.025, the second row. A gate on ratio2 is a gate that refuses the")
    print("    voice it was written for, on a good day, by luck of the phase.")
    print("    `slope` and `fit resid` hold across all eight rows.")
    print()
    print("    AND THE SIMPLEST GATE OF ALL, REFUTED IN ONE COLUMN: drop1 runs from")
    print("    0.01 dB to 16.6 dB over these eight legitimate modes, so no drop")
    print("    threshold can separate them from white noise, which reads in the")
    print("    same range. It is a measure of tau/T, not of whether a line exists.")
    return 0


def cmd_synthetics(argv):
    """Signals whose answer is chosen, including the exact signals
    `test_run_case.py`'s existing ground truth uses -- so the tolerance is not
    set by the real records alone, and so a gate that turns an existing
    ground-truth test red says so here first."""
    T = 0.060
    n25 = int(0.25 * SR)
    cases = {
        "rimshot-shaped lo tau 6ms": (damped(460.0, 0.006, 1.0, n25, SR, 0.3), 460.0, "pass"),
        "rimshot-shaped hi tau 1.5ms": (damped(1800.0, 0.0015, 3.0, n25, SR, 1.9), 1800.0, "pass"),
        "flat-balance tau 20ms (T/3)": (damped(460.0, 0.020, 1.0, n25, SR, 0.3), 460.0, "pass"),
        "struck (onset step) tau 6ms": (damped(452.0, 0.006, 1.0, n25, SR, 0.3,
                                               onset=int(0.010 * SR)), 452.0, "pass"),
        "never decays (pure sine)": (np.sin(2 * math.pi * 452.0 * np.arange(n25) / SR),
                                     452.0, "REFUSE"),
    }
    # test_balance_trajectory_db_excludes_a_point_below_the_floor's signal: two
    # tau=20 ms partials plus a guard-frequency tone gated off at 20 ms. The
    # suite requires a VERDICT on it, so a shape gate that refuses it is a
    # regression however well it refuses noise -- which is why both amplitudes
    # are here.
    #
    # AT 5x, WHICH IS WHAT THE FIXTURE WAS WRITTEN WITH, IT IS CORRECTLY REFUSED.
    # A 1000 Hz tone that loud throws a 1/df skirt across the whole record: at
    # the +-100 Hz offsets read around the LOW partial the burst alone measures
    # 13 to 21 dB BELOW that partial -- comparable to and at the outer offsets
    # louder than the partial's own skirt, which it scrambles by up to 7 dB. The
    # fixture's own docstring claimed the burst "raises the floor there and
    # nowhere else"; at 5x that was false. So the fixture was reduced to 0.6 --
    # swept, see that test's docstring -- and made to ASSERT the premise instead
    # of asserting it in prose (#389). At 0.6 the fixture's conclusion is
    # unchanged: t1 still moves from 3.0 ms to 21.0 ms, past the burst, which is
    # the whole of what it tests.
    n = int(0.060 * SR)
    t = np.arange(n) / SR
    two = damped(460.0, 0.020, 1.0, n, SR, 0.3) + damped(1800.0, 0.020, 1.0, n, SR, 1.9)
    for amp, want in ((0.6, "pass"), (5.0, "REFUSE")):
        burst = two + amp * np.sin(2 * math.pi * 1000.0 * t) * (t < 0.020)
        cases[f"two partials + {amp:.1f}x guard burst lo"] = (burst, 460.0, want)
        cases[f"two partials + {amp:.1f}x guard burst hi"] = (burst, 1800.0, "pass")
    print(f"T = {T*1e3:.0f} ms, d = {1/T:.2f} Hz. 'want' is chosen from what the")
    print("signal IS, before any statistic is read.")
    bad = 0
    for name, (x, f, want) in cases.items():
        vals, t_fit, shipped = row(x, SR, f, T)
        got = "pass" if shipped["ok"] else "REFUSE"
        mark = "  " if got == want else "  <== WRONG, wanted " + want
        bad += got != want
        print(f"    {name:34s} {fmt(vals, t_fit, shipped)}{mark}")
    print(f"\n    {len(cases) - bad} of {len(cases)} as chosen.")
    return 1 if bad else 0


def cmd_noise(argv):
    seeds = int(next((a for a in argv if a.isdigit()), 8))
    print(f"White noise, {seeds} seeds, at BOTH shipping operating points.")
    print("`headroom` is what the OLD 6 dB joint-headroom gate measured -- the")
    print("column showing why the floor could not be the fix: our own rimshot")
    print("clears it by 0.4 dB and noise clears it by 6 and more.")
    refused = {}
    for label, op in OPS.items():
        kw = {k: v for k, v in op.items() if k not in ("f_lo_range", "f_hi_range")}
        T = op["t_end"]
        print(f"\n  {label}   t_end {T*1e3:.0f} ms  d {1/T:.2f} Hz")
        n_ref = 0
        for seed in range(seeds):
            x = 0.01 * np.random.default_rng(seed).standard_normal(
                int(NOISE_SECONDS[label] * SR))
            e = RC.balance_trajectory_db(x, SR, op["f_lo_range"], op["f_hi_range"], **kw)
            n_ref += not e.ok
            if e.ok:
                verdict = f"REPORTED {e.value:+7.2f} dB"
            elif "resolved line" in e.reason:
                verdict = "refused: shape"
            else:
                verdict = "refused: floor"
            print(f"    seed {seed:3d}  {verdict:24s}")
            for tag, rng in (("low ", op["f_lo_range"]), ("high", op["f_hi_range"])):
                f = PT.find_partial(x, SR, *rng, seconds=T)
                print(f"      {tag} {f:8.2f} Hz  {fmt(*row(x, SR, f, T))}")
        refused[label] = n_ref
    print()
    for label, n in refused.items():
        print(f"    {label:12s} {n} of {seeds} REFUSED")
    return 0 if all(n == seeds for n in refused.values()) else 1


def _real_records(d):
    """The four records D10A and D13A are actually scored on, through
    run_case's OWN load/render/prepare chain."""
    if not d.exists():
        raise Refused(f"reference corpus not at {d} -- clone "
                      f"tidalcycles/sounds-tr808-fischer or set GF180_TR808_REFS")
    for label, op in OPS.items():
        voice = label.split()[0]
        if not (d / RC.REF_MAIN[voice][0]).exists():
            raise Refused(f"reference recording missing: {RC.REF_MAIN[voice][0]} under {d}")
        ref_x, ref_sr, rel, _ = RC.load_reference(voice, d)
        ours_x, ours_sr = RC.render_drum_solo(voice)
        yield (label, op, "reference " + rel, RC.prepare(ref_x, ref_sr, side="ref"), ref_sr)
        yield (label, op, "ours", RC.prepare(ours_x, ours_sr, side="ours"), ours_sr)


def cmd_records(argv):
    print("THE FOUR REAL RECORDS. These are what D10A and D13A score; a gate that")
    print("refuses one of them is a regression, not a fix.")
    bad = 0
    for label, op, side, y, sr in _real_records(refdir()):
        T = op["t_end"]
        kw = {k: v for k, v in op.items() if k not in ("f_lo_range", "f_hi_range")}
        e = RC.balance_trajectory_db(y, sr, op["f_lo_range"], op["f_hi_range"], **kw)
        verdict = ((f"REPORTED {e.value:+7.2f} dB, headroom "
                    f"{e.detail['headroom1_db']:4.1f} dB") if e.ok
                   else "REFUSED: " + e.reason[:60])
        bad += not e.ok
        print(f"\n  {label}  {side}\n    {verdict}")
        for tag, rng in (("low ", op["f_lo_range"]), ("high", op["f_hi_range"])):
            f = PT.find_partial(y, sr, *rng, seconds=T)
            print(f"    {tag} {f:8.2f} Hz  {fmt(*row(y, sr, f, T))}")
    print(f"\n    {bad} of 4 real records refused (must be 0).")
    return 1 if bad else 0


def cmd_survey(argv):
    """Records and noise in ONE table per candidate, which is the only form in
    which a tolerance can honestly be chosen: the gap between the worst real
    record and the best noise record IS the margin."""
    seeds = int(next((a for a in argv if a.isdigit()), 16))
    rec, noi = {k: [] for k in CANDS}, {k: [] for k in CANDS}
    rec_tau, noi_tau = [], []
    for label, op, side, y, sr in _real_records(refdir()):
        T = op["t_end"]
        for rng in (op["f_lo_range"], op["f_hi_range"]):
            f = PT.find_partial(y, sr, *rng, seconds=T)
            vals, tau, _ = row(y, sr, f, T)
            for k, v in vals.items():
                rec[k].append(v)
            rec_tau.append(tau)
    for label, op in OPS.items():
        T = op["t_end"]
        for seed in range(seeds):
            x = 0.01 * np.random.default_rng(seed).standard_normal(
                int(NOISE_SECONDS[label] * SR))
            for rng in (op["f_lo_range"], op["f_hi_range"]):
                f = PT.find_partial(x, SR, *rng, seconds=T)
                vals, tau, _ = row(x, SR, f, T)
                for k, v in vals.items():
                    noi[k].append(v)
                noi_tau.append(tau)
    print(f"8 real partials (4 records x 2) against {2*2*seeds} noise partials "
          f"({seeds} seeds x 2 OPs x 2 ranges).")
    print(f"    {'candidate':14s} {'records min':>12s} {'records max':>12s} "
          f"{'noise min':>11s} {'noise max':>11s}   separable?")
    for k in CANDS:
        r = np.asarray([v for v in rec[k] if math.isfinite(v)])
        nz = np.asarray([v for v in noi[k] if math.isfinite(v)])
        n_inf = sum(1 for v in noi[k] if not math.isfinite(v))
        sep = "yes" if (len(r) == len(rec[k]) and r.max() < nz.min()) else "NO -- overlaps"
        print(f"    {k:14s} {r.min():12.2f} {r.max():12.2f} {nz.min():11.2f} "
              f"{nz.max():11.2f}   {sep}"
              + (f"  (+{n_inf} inf on noise)" if n_inf else ""))
    print()
    print("    'separable?' is one-sided on purpose: a gate is an upper bound on")
    print("    the statistic, so it separates only if EVERY real record reads")
    print("    below EVERY noise record. `drop1` and `slope` overlap, `ratio2`")
    print("    goes non-finite on legitimate records, `fit resid` separates.")
    print(f"\n    fitted tau: records {min(rec_tau):.2f}..{max(rec_tau):.2f} ms "
          f"against windows of 60 and 600 ms -- every real mode decays inside its")
    print("    own analysis window, which is the second half of the shipping gate.")
    return 0


def _flat_resid_db(drops):
    """RMS residual of the best CONSTANT fit to the same drops -- the null the
    Lorentzian is being compared against. A record with no line in it fits a
    constant about as well as it fits a Lorentzian; a real mode does not."""
    m = np.isfinite(drops)
    if m.sum() < 2:
        return math.inf
    return float(np.std(drops[m]))


def _offsets_by_group(seeds, n_bins=6):
    """(name, T, ds, drops) for every real partial and every noise partial, so a
    statistic can be re-scored without re-measuring the projections."""
    groups = {"records": [], "noise": []}
    for label, op, side, y, sr in _real_records(refdir()):
        for rng in (op["f_lo_range"], op["f_hi_range"]):
            f = PT.find_partial(y, sr, *rng, seconds=op["t_end"])
            groups["records"].append(
                (f"{label} {side[:12]} {f:7.1f}", op["t_end"])
                + PT.line_offsets(y, sr, f, seconds=op["t_end"], n_bins=n_bins)[1:])
    for label, op in OPS.items():
        for seed in range(seeds):
            x = 0.01 * np.random.default_rng(seed).standard_normal(
                int(NOISE_SECONDS[label] * SR))
            for rng in (op["f_lo_range"], op["f_hi_range"]):
                f = PT.find_partial(x, SR, *rng, seconds=op["t_end"])
                groups["noise"].append(
                    (f"{label} seed {seed} {f:7.1f}", op["t_end"])
                    + PT.line_offsets(x, SR, f, seconds=op["t_end"], n_bins=n_bins)[1:])
    return groups


def cmd_margin(argv):
    """THE TABLE THE SHIPPING GATE'S TWO CONDITIONS WERE SET FROM.

    Both conditions are upper bounds, so each separates only if EVERY real
    partial reads below EVERY noise partial, and the ratio between those two is
    the margin. Printed for the pedestal model and without it, because which way
    that goes was a surprise: giving the fit a floor term to absorb makes it fit
    NOISE better too, and at 24 seeds it closes the residual margin from 1.7x to
    1.2x. The pedestal was added for a reason that measurement then withdrew --
    see `synthetics`."""
    seeds = int(next((a for a in argv if a.isdigit()), 24))
    groups = _offsets_by_group(seeds)
    print(f"{len(groups['records'])} real partials (4 records x 2 ranges) against "
          f"{len(groups['noise'])} noise partials ({seeds} seeds x 2 OPs x 2 "
          f"ranges), n_bins=6, 48 kHz.\n")
    print("`tau/T` is the fitted decay over the window it was measured on. It needs")
    print("NO tolerance at all: the bound is 1.0, meaning 'a struck mode decays")
    print("inside the window the estimator reads it over', which is the estimator's")
    print("own window and not a chosen constant.\n")
    for ped in (False, True):
        out = {}
        for g, rows in groups.items():
            res, taus = [], []
            for name, T, ds, drops in rows:
                r = PT._lorentzian_fit(ds, drops, pedestal=ped)
                res.append(r["resid_db"])
                taus.append(r["tau_ms"] * 1e-3 / T)
            out[g] = (np.asarray(res), np.asarray(taus))
        rr, rt = out["records"]
        nr, nt = out["noise"]
        print(f"  {'WITH pedestal' if ped else 'FLOORLESS (ships)'}")
        print(f"    resid dB   records {rr.min():6.2f}..{rr.max():6.2f}   "
              f"noise {nr.min():6.2f}..{nr.max():7.2f}   margin {nr.min()/rr.max():5.2f}x")
        print(f"    tau/T      records {rt.min():6.3f}..{rt.max():6.3f}   "
              f"noise {nt.min():6.3f}..{nt.max():7.3f}   margin {nt.min()/rt.max():5.2f}x"
              f"   ({int((nt < 1.0).sum())} of {len(nt)} noise partials under 1.0)")
        both = int(((nr <= rr.max()) & (nt <= rt.max())).sum())
        print(f"    noise partials inside BOTH records' envelopes: {both} of {len(nt)}")
        if not ped:
            w = np.argsort(rr)[-3:][::-1]
            b = np.argsort(nr)[:3]
            print("    worst 3 real:  " + " | ".join(
                f"{groups['records'][i][0]} {rr[i]:.2f} dB, tau/T {rt[i]:.3f}" for i in w))
            print("    best 3 noise:  " + " | ".join(
                f"{groups['noise'][i][0]} {nr[i]:.2f} dB, tau/T {nt[i]:.3f}" for i in b))
        print()
    return 0


def cmd_sweep(argv):
    """n_bins is the gate's ONE free parameter. Sweep it before arguing about
    it: for each width, the worst real record against the best noise record, for
    both the absolute residual and the residual relative to a no-line fit.

    Offsets are measured ONCE at the widest width and sliced, so every row reads
    the same projections and the comparison is not confounded by re-measurement.
    `line_offsets` interleaves the two sides (-1,+1,-2,+2,...), so the first 2k
    entries are exactly +-1..+-k bins."""
    seeds = int(next((a for a in argv if a.isdigit()), 8))
    widths = (3, 4, 6, 8, 12, 16, 24)
    wide = max(widths)
    groups = {"records": [], "noise": []}
    for label, op, side, y, sr in _real_records(refdir()):
        for rng in (op["f_lo_range"], op["f_hi_range"]):
            f = PT.find_partial(y, sr, *rng, seconds=op["t_end"])
            groups["records"].append(
                (f"{label} {side[:14]} {f:7.1f}",)
                + PT.line_offsets(y, sr, f, seconds=op["t_end"], n_bins=wide)[1:])
    for label, op in OPS.items():
        for seed in range(seeds):
            x = 0.01 * np.random.default_rng(seed).standard_normal(
                int(NOISE_SECONDS[label] * SR))
            for rng in (op["f_lo_range"], op["f_hi_range"]):
                f = PT.find_partial(x, SR, *rng, seconds=op["t_end"])
                groups["noise"].append(
                    (f"{label} seed {seed} {f:7.1f}",)
                    + PT.line_offsets(x, SR, f, seconds=op["t_end"], n_bins=wide)[1:])
    print(f"{len(groups['records'])} real partials against {len(groups['noise'])} "
          f"noise partials ({seeds} seeds x 2 OPs x 2 ranges), 48 kHz.")
    print("`resid` is the mode-plus-floor fit's RMS dB residual (what ships);")
    print("`floorless` is the same fit with the pedestal term forced to zero.")
    print(f"    {'n_bins':>6s} {'rec resid max':>13s} {'noi resid min':>13s} {'gap':>6s}"
          f" {'ratio':>6s}   {'rec f-less':>10s} {'noi f-less':>10s} {'ratio':>6s}")
    best = None
    for k in widths:
        stats = {}
        for g, rows in groups.items():
            resids, rels = [], []
            for name, ds, drops in rows:
                d, y = ds[:2 * k], drops[:2 * k]
                r = PT._lorentzian_fit(d, y, min_points=max(4, int(1.5 * k)))
                resids.append(r["resid_db"] if r["ok"] else math.inf)
                r0 = PT._lorentzian_fit(d, y, min_points=max(4, int(1.5 * k)),
                                        pedestal=False)
                rels.append(r0["resid_db"] if r0["ok"] else math.inf)
            stats[g] = (np.asarray(resids), np.asarray(rels))
        rr, rl = stats["records"]
        nr, nl = stats["noise"]
        gap, ratio = nr.min() - rr.max(), nr.min() / rr.max() if rr.max() > 0 else math.inf
        rel_ratio = nl.min() / rl.max() if rl.max() > 0 else math.inf
        print(f"    {k:6d} {rr.max():13.2f} {nr.min():13.2f} {gap:6.2f} {ratio:6.2f}"
              f"   {rl.max():10.2f} {nl.min():10.2f} {rel_ratio:6.2f}")
        if best is None or ratio > best[1]:
            best = (k, ratio)
    print(f"\n    widest absolute-residual separation at n_bins={best[0]} "
          f"(noise min / records max = {best[1]:.2f}x).")
    print("    A ratio near 1 is not a gate: it means one more record or one more")
    print("    seed can land on the wrong side of any threshold between them.")
    return 0


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "theory"
    rest = sys.argv[2:]
    try:
        sys.exit({"theory": cmd_theory, "synthetics": cmd_synthetics,
                  "noise": cmd_noise, "records": cmd_records,
                  "survey": cmd_survey, "sweep": cmd_sweep,
                  "margin": cmd_margin}[cmd](rest))
    except Refused as e:
        print(f"REFUSED  {e}")
        sys.exit(2)
    except KeyError:
        print(__doc__)
        sys.exit(64)
