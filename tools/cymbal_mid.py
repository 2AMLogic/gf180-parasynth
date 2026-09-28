#!/usr/bin/env python3
"""A qualified 1-2.5 kHz decay for the 808 cymbal (#369 step 6, the measurement #400 asks for first).

WHY THIS EXISTS. Step 5 (docs/scorecard/cymbal-369/candidate3/README.md §6) found that the
cymbal's remaining 1-2.5 kHz error is TIME-dependent: candidate 3's residual there is near zero
during the strike and 8-12 dB negative afterwards, and all three renders (shipped, candidate 2,
candidate 3) fall 5-11 dB further than the 808 does between the strike and 50-300 ms while
differing from each other by 30 dB of static response in the same third. That section states in
its own "evidence strength" note that it is NOT a qualified decay measurement: it is an energy
ratio between two fixed windows of the frozen `thirds()` instrument, and none of the three
qualified bands (L, Ln, H) covers 1-2.5 kHz. This module is that missing measurement.

It is DELIBERATELY A SEPARATE MODULE, not a new entry in `cymbal_bands.BANDS`: plan098 forbids
changing the frozen instrument mid-selection, so every existing L/Ln/H number and every
committed scorecard JSON is bit-for-bit unaffected by this file. What it reuses from
`cymbal_bands` is the already-qualified machinery (zero-phase band-pass from the guaranteed
lead, the floor-subtracted Schroeder curve, EDT10/late-T20 and their refusals); what it adds is
a band, two preconditions that must hold for that band to mean anything, and a second estimator
built on different arithmetic.

THE BANDS.
  M   891-1782 Hz  the 1.0 / 1.26 / 1.59 kHz thirds. The qualified band. The low band's
                   3.45 kHz Q 6 band-pass is >= 18.7 dB down at 1782 Hz and 26.7 dB down at
                   891 Hz (`skirt_db`, asserted in the tests), so M is to the low band what Ln
                   is to the high bands: the window where the neighbour's skirt is far enough
                   down that what decays here can be read as its own.
  M25 891-2818 Hz  all five thirds §6 quotes (adds 2.0 and 2.5 kHz). REPORTED, NOT QUALIFIED
                   for separation: the same skirt is only 12.1 dB down at 2818 Hz, so the
                   leakage precondition below is the thing that decides, per record, whether
                   M25 may be read at all.

TWO PRECONDITIONS, ASSERTED PER RECORD, THAT REFUSE RATHER THAN ANSWER.
  1. FLOOR. The 50 ms envelope where the Schroeder curve crosses -10 dB -- the point EDT10 is
     read at -- must sit at least FLOOR_MARGIN_DB = 10 dB above the record's own floor (mean
     power of its last 100 ms). `cymbal_bands.band_decay` guards the -30 dB point this way
     already (`end_margin_db`, 15 dB) but computes NOTHING for the -10 dB point, so it will
     report an EDT10 for a band that is entirely noise floor -- and 1-2 kHz is exactly where a
     1994 16-bit transfer's floor, hum and room rumble live. At 10 dB the floor-subtraction
     residual there is under ~0.4 dB, a few per cent of T20: far inside the repo's stated
     +-50 % time tolerance. This is the precondition that makes the band honest.
  2. LEAKAGE, AND PER WINDOW. The band's measured energy must exceed the energy the 3.45 kHz Q 6
     low band would leak into it by at least LEAK_MARGIN_DB = 6 dB -- checked separately over
     EACH window a quantity is read from: [0, -10 dB) for EDT10 and [-10 dB, -30 dB) for the late
     T20, so a quantity can be refused while the other is answered. A leak 20 dB down during the
     strike can DOMINATE the tail when the low band decays slower than the mid band, and the
     first version of this precondition -- one check over the strike's first second -- passed a
     planted signal whose late T20 it then read 40 % high, reporting the low band's decay under
     M's name. So: leakage may contribute at most ~25 %
     of the band's power in the window being read. The leak is predicted from the L band's OWN
     measured energy in that same window times the analytic response of the low path -- the
     3.45 kHz Q 6 band-pass, THEN Hh1 (2.5 kHz Q 0.97) if the record under test has Hh1. Which
     applies is a required argument, `low_has_hh1`, not a default: the 808 and any candidate that
     restores Hh1 have it, the shipped kit does not, and it moves the prediction by ~10 dB over
     0.9-1.8 kHz. Both numbers are reported for every record.
     6 dB rather than the 3 dB "energy ratio" convention: 3 dB is the threshold for calling two
     energies different, and here the neighbour must be not merely smaller but subordinate.

     THIS IS THE PRECONDITION THAT DECIDED THE ANSWER. Run with the band-pass alone, all 25 808
     recordings REFUSE: their 1-1.8 kHz sits only 4.1-5.5 dB above what the band-pass skirt alone
     predicts. The machine's actual path has Hh1's extra rejection in it, and then they clear the
     margin. The shipped kit, which omits Hh1, is 10.5 dB clear either way -- it has independent
     content at 1-1.8 kHz that the 808 does not.

A SECOND ESTIMATOR, ON DIFFERENT ARITHMETIC (`two_window_t20`). Two adjacent equal windows of
the floor-subtracted power, anchored where the smoothed envelope has fallen 10 dB from its peak
and each as long as the envelope's next 6 dB of fall: for a single exponential,
E1/E2 = exp(2W/tau) exactly, so tau = 2W / ln(E1/E2) with no
integration, no line fit and no residual bound anywhere in it. It shares only the band-pass with
the Schroeder estimator. Its purpose is grounding that does not come from us, and it is
load-bearing rather than decorative:

  **The late T20 is QUALIFIED -- `t20_qualified_ms`, the only late figure a report may quote --
  only if the two independent estimators agree inside XCHECK_TOL = 25 %.** EDT10
  (`edt10_qualified_ms`) is qualified by its preconditions plus its own analytic known answer
  (EDT10 = 1.1513 * tau for an exponential), because the second EDT estimator that was built for
  the same job turned out to scatter -36 %..+10 % on planted signals and is therefore reported
  as a diagnostic only -- see `envelope_edt10_ms`.

That rule is there because the preconditions alone are not enough. A planted 345 ms tail passed
the 6 dB leak margin with 7.9 dB to spare and the Schroeder estimator read it as 497 ms, +44 %;
the two-window estimator read 344 ms, and their disagreement is the only thing that caught it
(`test_control_a_tail_that_passes_the_leak_margin_can_still_be_biased`). The raw numbers stay in
the record for diagnosis; a disagreement is reported, never settled by preferring one of them.
What this cannot catch is two estimators biased the same way by the same cause, and that limit is
stated rather than papered over.

WHAT IS AND IS NOT VALIDATION HERE.
  * Known answers (tools/test_cymbal_mid.py): planted single exponentials, planted two-slope
    mixes, planted level changes, scale and prepended-silence invariance, a truncated record,
    a raised floor -- all synthetic, none of them produced by any drum model of ours, and BOTH
    estimators are held to the planted value.
  * Controls that must fail: a record whose M content is only the low band's skirt must REFUSE
    (paired with the same record plus real M content, which must answer), and a raised noise
    floor must REFUSE (paired with the same record at a low floor, which must answer). Each is
    stated with its paired opposite because #376 is the precedent: a control that cannot fail
    passed here for weeks.
  * External check: the recordings themselves, all 25 CY settings, via the knobs' known physics
    (DECAY is monotone in decay time) and via the cross-estimator agreement above.

THE PREDICTION, STATED BEFORE THE FIRST RUN (this docstring is committed before any number is
measured, on the precedent of step 5 §1). If §6's indication is real, then at CY5025 the 808's M
band should decay MEASURABLY LONGER than ours -- §6 reads our 1-1.6 kHz thirds falling 6-10 dB
further than the 808's over the same interval, and if that is one exponential over 250 ms it is
a factor of 2-3 in decay time. So: the 808's M EDT10 (or late T20, where both are measured)
should exceed the shipped kit's by more than 50 % -- larger than the repo's own +-50 % time
tolerance, which is the only way the difference counts as a difference -- and on the QUALIFIED
field, not the raw one. If instead the two agree
inside that tolerance, or the measurement REFUSES on the 808's own records, then §6's indication
does NOT survive qualification and #400's structural search has no target yet. Both outcomes are
reportable; the second is the more useful one, because §6 currently points at "the envelopes or
the source" for future work.
"""
from __future__ import annotations

import argparse
import json
import math
import pathlib
import subprocess
import sys

import numpy as np

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "model"), str(ROOT / "tools")]
import run_case as rc  # noqa: E402
import cymbal_bands as cb  # noqa: E402

# 1/3-octave edges: 891 = 1000/2^(1/6), 1782 = 1587*2^(1/6), 2818 = 2512*2^(1/6).
MID = {"M": (891.0, 1782.0), "M25": (891.0, 2818.0)}
L_BAND = cb.BANDS["L"]                  # the neighbour whose skirt is the threat
LOW_BP = (3450.0, 6.0)                  # docs/tr808-reference.md §10: the low band's band-pass
HH1 = (2500.0, 0.97)                    # ...and the 2-pole high-pass after its VCA, same table
FLOOR_MARGIN_DB = 10.0
LEAK_MARGIN_DB = 6.0
XCHECK_DROP_DB = 10.0                   # where the second estimator's first window starts
XCHECK_SPAN_DB = 6.0                    # how far the envelope must fall across one window
XCHECK_MIN_WIN_S = 0.03
XCHECK_MAX_WIN_S = 0.50
XCHECK_MIN_LN = 0.8                     # below this the two-window ratio's own scatter > ~20 %
XCHECK_TOL = 0.25                       # fraction; half the repo's ±50 % time tolerance


def bp_mag(f, f0=LOW_BP[0], q=LOW_BP[1]):
    """Magnitude of an ANALOG 2-pole band-pass (unity at f0). The circuit is analog, so the
    skirt that matters is the analog one, not a discretisation of it."""
    r = np.asarray(f, dtype=np.float64) / f0
    return (r / q) / np.sqrt((1.0 - r * r) ** 2 + (r / q) ** 2)


def hp2_mag(f, f0=HH1[0], q=HH1[1]):
    """Magnitude of an analog 2-pole high-pass: Hh1, the 2.5 kHz Q 0.97 Sallen-Key that sits
    after the low band's VCA (docs/tr808-reference.md §10, table row "low")."""
    r = np.asarray(f, dtype=np.float64) / f0
    return (r * r) / np.sqrt((1.0 - r * r) ** 2 + (r / q) ** 2)


def low_path_mag(f, with_hh1):
    """The whole low band's response at `f`: the 3.45 kHz band-pass, and Hh1 after it if the
    record under test HAS Hh1. The shipped kit omits it (`../README.md` §3); the 808 and the
    candidate have it. Which one applies is declared by the caller, never guessed."""
    return bp_mag(f) * (hp2_mag(f) if with_hh1 else 1.0)


def skirt_db(f, with_hh1=False):
    """The low band's response at `f`, dB relative to its 3.45 kHz peak."""
    return 20.0 * np.log10(low_path_mag(f, with_hh1) / low_path_mag(LOW_BP[0], with_hh1))


def skirt_leak_db(band, with_hh1, n=4001):
    """How much of the low band's energy lands in `band`, dB, relative to how much lands in
    L (2-5 kHz) -- i.e. what to expect in `band` from a measured L energy. Power-integrated
    over the analytic response on a log-frequency grid.

    `with_hh1` is REQUIRED, not defaulted, because getting it wrong is the difference between a
    measurement and a refusal: with the band-pass alone the 808's own M band sits only 4.1-5.5 dB
    above this prediction and every one of the 25 recordings REFUSES, while the machine's actual
    low path -- band-pass THEN Hh1 -- puts Hh1's ~10 dB of extra rejection over 0.9-1.8 kHz into
    the prediction and the same records clear the margin. A default here would silently pick one.

    Assumes the source driving the path is flat in frequency over 0.9-5 kHz, which six beating
    squares at ~500 Hz and up are to within the skirt's own 15 dB of tilt."""
    def e(lo, hi):
        u = np.linspace(math.log(lo), math.log(hi), n)
        f = np.exp(u)
        g = low_path_mag(f, with_hh1) ** 2 * f      # dE/du = |H|^2 * f  (flat source, du = df/f)
        return float(np.sum(0.5 * (g[1:] + g[:-1]) * np.diff(u)))
    return 10.0 * math.log10(e(*band) / e(*L_BAND))


def _boxcar_same(p, k):
    """`np.convolve(p, np.ones(k)/k, mode="same")` in O(n) instead of O(nk) -- bit-for-bit the
    same alignment, which `test_boxcar_same_matches_numpy_convolve` asserts. `cymbal_bands`
    computes the identical envelope with np.convolve and that costs 3 s per band on a 6 s record;
    this module needs the same envelope again for its own guards and will not pay for it twice."""
    p = np.asarray(p, dtype=np.float64)
    c = np.concatenate([[0.0], np.cumsum(p)])
    n, off = len(p), (k - 1) // 2
    top = np.arange(n) + off + 1                    # the window's exclusive end BEFORE clipping:
    hi = np.minimum(top, n)                         # np.convolve zero-pads, so a window running
    lo = np.maximum(top - k, 0)                     # off either end keeps the divisor k
    return (c[hi] - c[lo]) / k


def floor_margins(x, sr) -> dict:
    """How far the band's 50 ms ENVELOPE sits above the record's own floor at the two points the
    Schroeder estimator reads -- its -10 dB crossing (where EDT10 is taken) and its -30 dB
    crossing (the far end of the late-T20 fit) -- plus the two crossing TIMES, which are the
    windows every other quantity here is evaluated over.

    `cymbal_bands.band_decay` computes the second margin itself as `end_margin_db` and refuses
    below 15 dB, but it computes NOTHING for the first -- so it will report an EDT10 for a band
    that is entirely noise floor, which 1-2 kHz on a 1994 16-bit transfer can be. The definitions
    here are deliberately the same as `band_decay`'s (50 ms boxcar power envelope, floor = mean
    power of the last 100 ms, floor-subtracted Schroeder curve) so the two agree where they
    overlap; that agreement is asserted by
    tools/test_cymbal_mid.py::test_floor_margin_at_minus30_matches_band_decays_own_end_margin."""
    x = np.asarray(x, dtype=np.float64)
    p = x * x
    nf = max(4, int(0.1 * sr))
    floor = float(np.mean(p[-nf:]))
    sm = int(0.05 * sr)
    env = _boxcar_same(p, sm)
    pk = float(np.max(env))
    lvl = lambda v: 10 * math.log10(max(float(v), 1e-30) / pk)
    lvl_floor = lvl(np.mean(env[-sm:]))
    q = np.maximum(p - floor, 0.0)
    sch = np.cumsum(q[::-1])[::-1]
    out = {"floor_db_re_peak": round(lvl_floor, 2), "margin10_db": None, "margin30_db": None,
           "i10": None, "i30": None}
    if sch[0] <= 0:
        return out
    c = 10 * np.log10(np.maximum(sch / sch[0], 1e-30))
    for tag, itag, thr in (("margin10_db", "i10", -10.0), ("margin30_db", "i30", -30.0)):
        i = np.nonzero(c <= thr)[0]
        if len(i):
            out[itag] = int(i[0])
            out[tag] = round(lvl(env[i[0]]) - lvl_floor, 2)
    return out


def leak_dominance_db(xm, xl, band, a, b, with_hh1) -> float:
    """How far the mid band's energy in samples [a, b) exceeds what the 3.45 kHz low band would
    leak into it OVER THE SAME SAMPLES, in dB.

    Evaluated per window, not once over the strike's first second, and the difference matters:
    leakage that is 20 dB down at the strike can DOMINATE the tail if the low band decays slower
    than the mid band does. Measuring it over the first second only was the first version of this
    precondition, and it passed a planted signal whose late T20 it then read 40 % high -- the low
    band's decay wearing M's name, which is precisely the failure the precondition exists to
    catch (`docs/scorecard/cymbal-369/mid-band/README.md`, wrong-then-right 1)."""
    em = float(np.sum(np.asarray(xm[a:b], dtype=np.float64) ** 2))
    el = float(np.sum(np.asarray(xl[a:b], dtype=np.float64) ** 2))
    leak = el * 10.0 ** (skirt_leak_db(band, with_hh1) / 10.0)
    return 10.0 * math.log10(max(em, 1e-30) / max(leak, 1e-30))


def _smooth_env_db(x, sr):
    env_db, n = cb._env_db(x, sr)
    k = 9                                       # 45 ms median: the beating, not the decay
    if len(env_db) > k:
        pad = np.pad(env_db, (k // 2, k // 2), mode="edge")
        env_db = np.median(np.lib.stride_tricks.sliding_window_view(pad, k), axis=-1)
    return env_db, n


def envelope_edt10_ms(x, sr, drop_db=XCHECK_DROP_DB) -> float | None:
    """EDT10 read straight off the smoothed 5 ms power envelope: the time from its peak to
    `drop_db` below it.

    REPORTED AS A DIAGNOSTIC, NOT USED AS A GATE, and the reason is measured rather than assumed.
    This was built to be EDT10's second estimator, by analogy with `two_window_t20` for the late
    T20 -- for a single exponential it must agree, since the Schroeder curve of an exponential
    falls at the same 8.686/tau dB per second the power envelope does. Against 12 planted
    exponentials (4 time constants x 3 seeds) it scattered -36 % to +10 % of the planted value
    (a median-anchored variant: -25 % to +48 %), while the Schroeder EDT10 was within 9 % on
    every one of the 12 -- because peak-picking on a band of beating noise picks an outlier.
    A 25 % agreement bound built on it would have refused correct answers, so EDT10 is qualified
    by its preconditions and by its own analytic known answer (EDT10 = 1.1513 * tau, asserted in
    tools/test_cymbal_mid.py) instead, and this number is kept only because a reader comparing
    them should see the same thing we did."""
    env_db, n = _smooth_env_db(x, sr)
    i_pk = int(np.argmax(env_db))
    below = np.nonzero(env_db[i_pk:] <= env_db[i_pk] - drop_db)[0]
    return None if not len(below) else round(1e3 * int(below[0]) * n / sr, 2)


def two_window_t20(x, sr, drop_db=XCHECK_DROP_DB, span_db=XCHECK_SPAN_DB) -> dict:
    """T20 from the ratio of two adjacent equal windows of floor-subtracted power.

    For a single exponential amplitude envelope, E1/E2 = exp(2W/tau) exactly, so
    tau = 2W / ln(E1/E2) and T20 = ln(10) * tau. No backward integration, no line fit, no
    residual bound: this shares only the band-pass with `cymbal_bands.band_decay`, which is the
    point of having it.

    The window length W is NOT fixed. It is read off the smoothed envelope itself -- the time to
    fall a further `span_db` past the -`drop_db` point -- so the drop across a window pair is
    about span_db by construction whatever the decay. A fixed W=150 ms was the first version and
    it reported 5,399 ms for a planted 1,382 ms (a 2.9x error) on one seed of a slow decay,
    because ln(E1/E2) there is ~0.5 and the relative error in tau is (relative error in the
    ratio) / ln(E1/E2): a 15 % ratio estimate on a 600 Hz-wide noise band in 150 ms became 30 %
    of tau, and worse on an unlucky seed. At span_db = 6 dB, ln(E1/E2) ~ 1.4 and the same ratio
    error costs ~11 %.

    REFUSES when the record is too short, when the power is not falling, when either window is
    within FLOOR_MARGIN_DB of the floor, or when ln(E1/E2) is below XCHECK_MIN_LN -- the last
    being the estimator stating its own resolution rather than answering beyond it."""
    x = np.asarray(x, dtype=np.float64)
    p = x * x
    nf = max(4, int(0.1 * sr))
    floor = float(np.mean(p[-nf:]))
    env_db, n = _smooth_env_db(x, sr)
    i_pk = int(np.argmax(env_db))
    below = np.nonzero(env_db[i_pk:] <= env_db[i_pk] - drop_db)[0]
    if not len(below):
        return {"t20_ms": None, "refused": f"the envelope never falls {drop_db:.0f} dB from its peak"}
    i_a = i_pk + int(below[0])
    a = i_a * n
    further = np.nonzero(env_db[i_a:] <= env_db[i_a] - span_db)[0]
    if not len(further):
        return {"t20_ms": None,
                "refused": f"the envelope never falls a further {span_db:.0f} dB, so no window length is defined"}
    win_s = min(max(int(further[0]) * n / sr, XCHECK_MIN_WIN_S), XCHECK_MAX_WIN_S)
    w = int(win_s * sr)
    if a + 2 * w > len(p):
        return {"t20_ms": None, "refused": "record too short for two windows after the -10 dB point"}
    e1 = float(np.mean(p[a:a + w])) - floor
    e2 = float(np.mean(p[a + w:a + 2 * w])) - floor
    out = {"start_ms": round(1e3 * a / sr, 1), "win_ms": round(1e3 * win_s, 1),
           "w1_over_floor_db": round(10 * math.log10(max(e1, 1e-30) / max(floor, 1e-30)), 2) if floor > 0 else None,
           "w2_over_floor_db": round(10 * math.log10(max(e2, 1e-30) / max(floor, 1e-30)), 2) if floor > 0 else None}
    if e1 <= 0 or e2 <= 0:
        return {**out, "t20_ms": None, "refused": "a window has no energy above the floor"}
    if e2 >= e1:
        return {**out, "t20_ms": None, "refused": "the power is not falling across the two windows"}
    if floor > 0 and min(out["w1_over_floor_db"], out["w2_over_floor_db"]) < FLOOR_MARGIN_DB:
        return {**out, "t20_ms": None,
                "refused": (f"a window is only {min(out['w1_over_floor_db'], out['w2_over_floor_db']):.1f} dB "
                            f"above the floor (need {FLOOR_MARGIN_DB:.0f})")}
    ln_r = math.log(e1 / e2)
    out["ln_ratio"] = round(ln_r, 3)
    if ln_r < XCHECK_MIN_LN:
        return {**out, "t20_ms": None,
                "refused": (f"the power falls only {10 * ln_r / math.log(10):.1f} dB across a window pair "
                            f"(ln ratio {ln_r:.2f} < {XCHECK_MIN_LN}), where this estimator's own scatter "
                            f"exceeds ~20 % of tau")}
    return {**out, "t20_ms": round(1e3 * math.log(10.0) * 2.0 * win_s / ln_r, 2)}


def measure_mid(y, sr, low_has_hh1, bands=None) -> dict:
    """`y` must come through run_case.prepare (the guaranteed lead, #101).

    `low_has_hh1` DECLARES whether the record's low band has Hh1 (the 2.5 kHz Q 0.97 high-pass
    after its VCA) behind its 3.45 kHz band-pass -- True for the 808 and for any candidate that
    restores Hh1, False for the shipped kit, which omits it. It has no default on purpose: it
    changes the leakage prediction by ~10 dB over 0.9-1.8 kHz, which is the difference between
    measuring the 808's mid band and refusing it. Both values are reported per band
    (`over_leak_*_db` under the declaration, `*_bp_only_db` with the band-pass alone) so a reader
    can see the effect of the declaration rather than take it on trust.

    `bands` restricts the work to some of MID (the known-answer tests ask only for M).

    Per mid band: energy share (dB re 200 Hz-20 kHz over the strike's first second, the same
    convention as `cymbal_bands.measure`), the Schroeder EDT10/late T20, the independent
    two-window T20, and the two preconditions. A band that fails a precondition carries
    `refused` and its decay numbers are set to None -- REFUSED is an outcome, not a gap."""
    y = np.asarray(y, dtype=np.float64)
    o = rc.required_lead_samples(sr)
    n_e = int(cb.ENERGY_S * sr)
    tot = cb._bp(y, sr, *cb.TOTAL)
    e_tot = float(np.sum(tot[:n_e] ** 2))
    if e_tot <= 0:
        raise cb.Refused("silent")
    xl = cb._bp(y, sr, *L_BAND)
    e_l = float(np.sum(xl[:n_e] ** 2))
    res = {"sr": sr, "record_s": round((len(y) - o) / sr, 3), "low_has_hh1": bool(low_has_hh1),
           "L_energy_share_db": round(10 * math.log10(max(e_l, 1e-30) / e_tot), 3)}
    for name, band in ((k, v) for k, v in MID.items() if bands is None or k in bands):
        xb = cb._bp(y, sr, *band)
        eb = float(np.sum(xb[:n_e] ** 2))
        leak = skirt_leak_db(band, low_has_hh1)
        r = {"band_hz": list(band),
             "energy_share_db": round(10 * math.log10(max(eb, 1e-30) / e_tot), 3),
             "skirt_db_at_edges": [round(float(skirt_db(band[0], low_has_hh1)), 2),
                                   round(float(skirt_db(band[1], low_has_hh1)), 2)],
             "predicted_leak_db_re_L": round(leak, 2),
             "predicted_leak_db_re_L_bp_only": round(skirt_leak_db(band, False), 2),
             "over_leak_1s_db": round(leak_dominance_db(xb, xl, band, 0, n_e, low_has_hh1), 2),
             "over_leak_1s_bp_only_db": round(leak_dominance_db(xb, xl, band, 0, n_e, False), 2)}
        r.update(cb.band_decay(xb, sr))
        r.update(floor_margins(xb, sr))
        r["xcheck"] = two_window_t20(xb, sr)
        r["edt10_env_ms"] = envelope_edt10_ms(xb, sr)
        r["edt10_qualified_ms"] = None
        r["t20_qualified_ms"] = None
        i10, i30 = r.pop("i10"), r.pop("i30")
        # Each quantity is gated over ITS OWN window: EDT10 over [0, -10 dB), the late T20 over
        # [-10 dB, -30 dB). A leak that is subordinate during the strike can dominate the tail.
        r["over_leak_edt_db"] = (None if i10 is None else
                                 round(leak_dominance_db(xb, xl, band, 0, i10, low_has_hh1), 2))
        r["over_leak_late_db"] = (None if i10 is None or i30 is None else
                                  round(leak_dominance_db(xb, xl, band, i10, i30, low_has_hh1), 2))
        r["over_leak_edt_bp_only_db"] = (None if i10 is None else
                                         round(leak_dominance_db(xb, xl, band, 0, i10, False), 2))
        r["over_leak_late_bp_only_db"] = (None if i10 is None or i30 is None else
                                          round(leak_dominance_db(xb, xl, band, i10, i30, False), 2))
        refusals = []
        m10 = r["margin10_db"]
        if m10 is None or m10 < FLOOR_MARGIN_DB:
            refusals.append(f"the envelope at the -10 dB point is only {m10} dB above the record's floor "
                            f"(need {FLOOR_MARGIN_DB:.0f}): the band is reading its own noise floor")
        if r["over_leak_edt_db"] is None or r["over_leak_edt_db"] < LEAK_MARGIN_DB:
            refusals.append(f"over the EDT window the band is only {r['over_leak_edt_db']} dB above the "
                            f"3.45 kHz band's predicted skirt leakage (need {LEAK_MARGIN_DB:.0f}): what "
                            f"decays here cannot be separated from the low band")
        if refusals:
            r["refused"] = "; ".join(refusals)
            for k in ("edt10_ms", "t20_late_ms"):
                r[k] = None
            r["xcheck"] = {"t20_ms": None, "refused": "band refused"}
            res[name] = r
            continue
        if r["t20_late_ms"] is not None and (r["over_leak_late_db"] is None
                                             or r["over_leak_late_db"] < LEAK_MARGIN_DB):
            # the EDT window is clean but the tail is the low band's: refuse the LATE quantity only
            r["t20_late_ms"] = None
            r["t20_refused"] = (f"over the late window the band is only {r['over_leak_late_db']} dB above the "
                                f"3.45 kHz band's predicted skirt leakage (need {LEAK_MARGIN_DB:.0f})")
            r["xcheck"] = {"t20_ms": None, "refused": "late window is leak-dominated"}
        # --- what may be QUOTED. A number that passed the preconditions is still only qualified
        # if the second, independent estimator agrees with it inside XCHECK_TOL. 6 dB of leak
        # margin alone does NOT bound the bias: a planted 345 ms tail read 497 ms (+44 %) at
        # 7.9 dB of margin, and the disagreement with the two-window estimator (344 ms) is the
        # only thing that caught it. So `*_qualified_ms` is None unless both agree, and the
        # raw numbers stay in the record for diagnosis.
        if r["t20_late_ms"] is not None and r["xcheck"].get("t20_ms") is not None:
            a, b = r["t20_late_ms"], r["xcheck"]["t20_ms"]
            r["xcheck_rel_diff"] = round((b - a) / a, 3)
            r["xcheck_agrees"] = bool(abs(b - a) <= XCHECK_TOL * a)
            if r["xcheck_agrees"]:
                r["t20_qualified_ms"] = r["t20_late_ms"]
            else:
                r["t20_refused"] = (f"the two estimators disagree by {100 * r['xcheck_rel_diff']:+.0f} % "
                                    f"(Schroeder {a:.0f} ms, two-window {b:.0f} ms; bound "
                                    f"{100 * XCHECK_TOL:.0f} %), so neither is qualified here")
        # EDT10 is qualified by its preconditions and its analytic known answer, NOT by agreement
        # with `edt10_env_ms` -- that estimator's measured scatter on planted exponentials is
        # -36 %..+10 %, wider than any bound worth gating on (see its docstring). The difference
        # is recorded so a reader can see it rather than take the claim on trust.
        r["edt10_qualified_ms"] = r["edt10_ms"]
        if r["edt10_ms"] is not None and r["edt10_env_ms"] is not None:
            r["edt_env_rel_diff"] = round((r["edt10_env_ms"] - r["edt10_ms"]) / r["edt10_ms"], 3)
        res[name] = r
    return res


# --------------------------------------------------------------------------- corpus and renders

def fischer(refs: pathlib.Path) -> dict:
    """Every Fischer CY recording. `low_has_hh1=True`: the machine has Hh1 (reference §10)."""
    out = {}
    for tone in cb.CODES:
        for decay in cb.CODES:
            p = refs / "cy8" / f"CY{tone}{decay}.WAV"
            x, sr = cb._load(p)
            out[f"CY{tone}{decay}"] = measure_mid(rc.prepare(x, sr, side=p.name), sr, True)
    return out


def renders(refs: pathlib.Path, candidate: bool = True) -> dict:
    """The shipped kit's CY, the 808's CY5025, and (optionally) candidate 3 -- the three records
    §6's table is about, measured by this module instead of by an energy ratio.

    Each declares its own low path: the 808 HAS Hh1; the SHIPPED kit omits it (`../README.md` §3,
    the omission that leaves 9-15 dB of excess below 2.5 kHz); candidate 3 restores it
    (`model/cymbal_candidate.py` revision 3, mode M_CYH1)."""
    out = {}
    rx, rsr = cb._load(refs / "cy8" / "CY5025.WAV")
    out["fischer_CY5025"] = measure_mid(rc.prepare(rx, rsr, side="CY5025"), rsr, True)
    ys, sr = rc.render_drum_solo("CY")
    out["shipped"] = measure_mid(rc.prepare(ys, sr, side="shipped CY"), sr, False)
    if candidate:
        import cymbal_candidate as cc
        import cymbal_candidate_eval as ce
        ce.VARIANT = "full"
        cal = ce.calibrate()
        yc, _ = cc.render(ce.kit_with_levels(cal["amps"]), "CY")
        out["candidate3"] = measure_mid(rc.prepare(yc, sr, side="candidate3 CY"), sr, True)
    return out


def _row(name, r, band="M"):
    f = lambda v: "  REF" if v is None else f"{v:5.0f}"
    g = lambda v: " REF" if v is None else f"{v:4.1f}"
    m = r[band]
    return (f"{name:16s} {band:3s} share {m['energy_share_db']:7.2f}  over-leak 1s {g(m['over_leak_1s_db'])} "
            f"edt {g(m['over_leak_edt_db'])} late {g(m['over_leak_late_db'])}  "
            f"floor {m['floor_db_re_peak']:6.1f}  EDT {f(m['edt10_ms'])}/{f(m['edt10_env_ms'])} "
            f"qual {f(m['edt10_qualified_ms'])}  T20 {f(m['t20_late_ms'])}/{f(m['xcheck'].get('t20_ms'))} "
            f"qual {f(m['t20_qualified_ms'])}"
            + (f"  REFUSED: {m['refused']}" if "refused" in m else "")
            + (f"  T20 REFUSED: {m['t20_refused']}" if m.get("t20_refused") and "refused" not in m else ""))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--refs", default=str(rc.configured_refs()))
    ap.add_argument("--out", type=pathlib.Path, required=True)
    ap.add_argument("--fischer", action="store_true", help="all 25 CY recordings")
    ap.add_argument("--renders", action="store_true", help="808 CY5025 vs the shipped kit vs candidate 3")
    ap.add_argument("--no-candidate", action="store_true", help="skip candidate 3 (skips the ~4 min calibration)")
    a = ap.parse_args(argv)
    if not (a.fischer or a.renders):
        ap.error("choose --fischer and/or --renders")
    refs = pathlib.Path(a.refs)
    res = {"bands": {k: list(v) for k, v in MID.items()},
           "preconditions": {"floor_margin_db": FLOOR_MARGIN_DB, "leak_margin_db": LEAK_MARGIN_DB,
                             "xcheck_tol": XCHECK_TOL},
           "skirt": {k: {"with_hh1": round(skirt_leak_db(v, True), 2),
                         "bp_only": round(skirt_leak_db(v, False), 2)} for k, v in MID.items()}}
    if a.renders:
        res["renders"] = renders(refs, candidate=not a.no_candidate)
        for k, v in res["renders"].items():
            for b in MID:
                print(_row(k, v, b))
    if a.fischer:
        res["fischer"] = fischer(refs)
        print(f"{'file':8s} {'tone':>4s} {'decay':>5s} | {'M share':>7s} {'lk1s':>5s} {'lkE':>5s} {'lkL':>5s} "
              f"{'floor':>6s} {'EDTsch':>6s} {'EDTenv':>6s} {'EDTq':>6s} {'T20sch':>6s} {'T20 2w':>6s} "
              f"{'T20q':>6s} | {'M25 EDTq':>8s}")
        for k in sorted(res["fischer"], key=lambda k: (cb.KNOB[k[2:4]], cb.KNOB[k[4:6]])):
            m, m25 = res["fischer"][k]["M"], res["fischer"][k]["M25"]
            f = lambda v: "   REF" if v is None else f"{v:6.0f}"
            g = lambda v: "  REF" if v is None else f"{v:5.1f}"
            print(f"{k:8s} {cb.KNOB[k[2:4]]:4.1f} {cb.KNOB[k[4:6]]:5.1f} | {m['energy_share_db']:7.2f} "
                  f"{g(m['over_leak_1s_db'])} {g(m['over_leak_edt_db'])} {g(m['over_leak_late_db'])} "
                  f"{m['floor_db_re_peak']:6.1f} {f(m['edt10_ms'])} {f(m['edt10_env_ms'])} "
                  f"{f(m['edt10_qualified_ms'])} {f(m['t20_late_ms'])} {f(m['xcheck'].get('t20_ms'))} "
                  f"{f(m['t20_qualified_ms'])} | {f(m25['edt10_qualified_ms']):>8s}")
    res["commit"] = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True,
                                   text=True).stdout.strip()
    res["sources_dirty"] = subprocess.run(["git", "diff", "--quiet", "HEAD", "--", "model", "tools"],
                                          cwd=ROOT).returncode != 0
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text(json.dumps(res, indent=1, default=float) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
