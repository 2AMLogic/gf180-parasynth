#!/usr/bin/env python3
"""Does the VCAs' asymmetric clipping supply the 808's 1-2.5 kHz? (#369 step 8)

`docs/scorecard/cymbal-369/low-tail/` ended by naming exactly one next question,
and this module asks it and nothing else:

    "Does the VCAs' asymmetric clipping supply 1-2.5 kHz with a slower decay
     than the low band's? It is the only element §10 names for this region and
     does not quantify, and it is testable the same way this step was: put the
     documented nonlinearity in the probe's mixture, measure rho and the M/Ln
     energy with the SAME instrument, and see whether it crosses the bound in
     §5 and the skirt baseline in §2."

THE PREDICTION, WRITTEN BEFORE ANY NUMBER WAS MEASURED
------------------------------------------------------
Recorded here because a prediction stated after the fact is not a prediction.
Reasoning from the algebra of a memoryless nonlinearity acting on an enveloped
signal, and from where §10 puts it in the chain:

  ENERGY. Yes. Six squares beating inside a Q 6 band-pass leave a dense comb
  whose partials are spaced by the oscillator fundamentals (205-800 Hz). A
  second-order term folds every pair of those partials down to their DIFFERENCE,
  and 891-2828 Hz is exactly where those differences land. Predicted to clear
  the linear chain's bound.

  DECAY. No, and this is the half the question turns on. A quadratic term in an
  envelope g(t) produces a product that decays as g(t)^2 -- TWICE AS FAST as the
  band it came from, not slower. §10 puts the nonlinearity in the VCAs, i.e.
  AFTER the envelope multiply, so the prediction is rho_M BELOW the skirt
  baseline, in the same direction our renders are already wrong, and further.

  The one position that could give rho > 1 is a nonlinearity at the SUMMING node
  rather than in the VCAs, because there the DECAY band's long envelope (tau up
  to 0.38 s) is still present and undivided by Hh2, and a cross term in
  g_low * g_decay decays more slowly than g_low^2. Predicted to be the only
  position with the right sign, and still short of the low band's own tau.

So the probe sweeps the nonlinearity's POSITION as a discrete structural choice
(§369's rule: structural decisions are discrete choices tested against the
circuit, not continuous Q hacks) as well as its drive.

THE ANSWER: NO, AND THE PREDICTION ABOVE WAS WRONG ABOUT WHY
-------------------------------------------------------------
**No single (position, drive, asymmetry) lands inside both of the 808's
measured ranges at once**, at any of the three positions and any of the four
asymmetries swept. `verdict()` REFUSES rather than naming one.

The energy half was right and the decay half was wrong IN SIGN. Clipping does
not make rho_M fall; it makes it rise, steeply, at every position:

    asym 0.5, staircase source    M re Ln    rho_M(-10)   rho_Mn(-10)
    linear (no clipper)             -7.90        0.9917        1.0966
    post_vca, the literal §10 spot  -6.92        1.1108        1.4791   (-20 dB)
                                    -5.57        2.0660        2.6894   ( +0 dB)
    sum                             -4.53        2.6492        3.3319   ( +0 dB)
    sum                             -3.34        2.8195        3.2741   (+12 dB)
    the 808                  -5.00 .. -3.45   1.048..1.139   0.949..1.097

The prediction's error was to reason only about a band's product with ITSELF
(g^2, twice as fast). The DECAY band's self-product decays with tau_decay/2 =
190 ms, which still OUTLASTS the low band's own 100 ms -- so the products that
dominate M late are longer-lived than Ln, not shorter, and rho goes up. The
algebra was right and the term that matters was not the one I wrote down.

**And it overshoots by too much to be tuned into place.** The two quantities
move together: by the drive at which M re Ln enters the 808's range, rho_M has
reached 2.0-3.1 against the machine's 1.05-1.14, and rho_Mn 2.7-3.3 against
0.95-1.10. The closest single row over all four asymmetries is post_vca at
-20 dB, and it is still 1.4-2.6 dB short in energy. There is no drive in
between: the ratio in which clipping moves the two is a property of the
mechanism, not a free parameter.

So §10's only named candidate for this region is ELIMINATED, the same way the
tone stage's shape was in step 7 -- by being measured and being the wrong size.

WHAT THIS CORRECTS IN STEP 7, WHICH IS THE LARGER HALF OF THE RESULT
---------------------------------------------------------------------
Step 7's "the documented chain is short by 9.4 dB in M" compares an ANALYTIC
bound against a FILTERED measurement. Two preconditions were assumed there and
are asserted here, and together they account for most of the 9.4 dB:

1. **The analysis filter.** `leakage_error()`: rendered through
   `cymbal_low_tail.measure` and compared with the same cascade's analytic band
   energy, the low band agrees to 0.25 dB -- Ln IS its peak -- but the two bands
   peaking at 7.1 kHz read +3.67 and +9.49 dB HIGH, because the analysis
   band-pass's skirt admits more of that peak's shoulder than the band truly
   holds. Step 7's own rejection table quotes -91.3 dB at 7100 Hz, which is the
   rejection AT the peak and not the integral over the shoulder between.
2. **The source.** `source_tilt()`: §1.5's staircase is not flat, and through
   the same chain it moves the low band's M re Ln by +4.55 dB.

`rendered_bound()` puts both on the same footing -- the bound computed the way
the measurement it is compared against was computed:

    M re Ln, best over 64 inter-band balances
      analytic, flat source (step 7's number)      -12.74 dB  (-13.10 for `ref`)
      rendered, flat source                         -8.72 dB
      rendered, §1.5's staircase source             -5.07 dB
      the 808, 20 settings                    -5.00 .. -3.45 dB

**So the energy gap is about 1.3 dB at the best balance and 4.2 dB at §10's own
balance, not 9.4 dB.** That does not make the chain right, and it does not touch
step 7's DECAY finding, which is a within-record ratio and immune to both
corrections. It does mean "a missing mechanism supplies 9-17 dB" overstates the
case by most of its size, and the two things that were missing were in the
apparatus, not in the machine.

The decay finding changes shape rather than surviving intact. Rendered from the
documented staircase the LINEAR chain at §10's own balance reads rho_M(-10) =
0.9917 and rho_Mn(-10) = 1.0966, against the 808's 1.048-1.139 and 0.949-1.097
-- Mn already inside the machine's range, M 0.06 below the bottom of it, where
our shipped kit reads 0.871 and 0.878. And step 7's "sweeping the balance ...
rho_M(-10) stays between 0.89 and 0.97, it never even clears the skirt
baseline" does not hold for the rendered chain: over the 64 balances
`rendered_bound()` sweeps it reaches **1.2413** (staircase) and **1.2643**
(white), past the 808's own top of 1.139.

WHAT REPLACES BOTH: A JOINT CONSTRAINT
---------------------------------------
Neither quantity is the finding on its own, because they are not independent.
The balances that raise rho are the balances that starve M:

    linear chain, staircase, 64 balances       M re Ln    rho_M(-10)
      best M re Ln                              -5.07      (lower)
      best rho_M                                -7.70        1.2413
      the 808 needs BOTH                  -5.00..-3.45   1.048..1.139

**`rendered_bound()` reports `n_balances_in_808_box` and it is 0** for both
sources, and it is 0 for the clipper at every position, drive and asymmetry too.
That is the durable statement: the documented chain traces a locus in the
(energy, decay) plane that does not pass through the machine's box, and neither
the inter-band balance nor §10's nonlinearity moves it onto one. It is a
sharper claim than either step 7's or this step's halves, and it is the one the
next increment has to break.

WHAT IT MEASURES, AND AGAINST WHAT
-----------------------------------
The same instrument as step 7 -- `cymbal_low_tail.measure`, imported unchanged
so the band edges, the Schroeder definition and the truncation refusals cannot
drift -- reading the two quantities step 7 froze:

    rho_M(-10)   the 808 reaches 1.021-1.139; the instrument's own skirt
                 baseline tops out at 1.017; ours read 0.866-0.894
    M re Ln      the 808's median is -3.73 dB; the documented LINEAR chain
                 cannot beat -13.10 dB at ANY inter-band balance

Both targets are quoted from the committed `low-tail.json` and `m-origin.json`
rather than re-derived, and `targets()` REFUSES if those files disagree with the
numbers written here.

THE SOURCE, WHICH STEP 7 ASSUMED AND THIS STEP ASSERTS
-------------------------------------------------------
`cymbal_m_origin.band_energies` integrates the chain's MAGNITUDE RESPONSE over
each analysis band. That is the energy a FLAT (white) source would leave there.
The 808's source is not flat: §1.5's six Schmitt-trigger squares summed through
six 120 kOhm resistors are a 7-level staircase whose harmonics fall at 6 dB per
octave. Between Ln's 3.45 kHz and M's centre that tilt is worth several dB in
the direction of the gap, so a bound computed against a white source is not the
bound that applies to the machine.

This module therefore renders BOTH sources through the same chain and reports
both, and `source_tilt()` states the difference. It is a precondition of step 7's
headline number that step 7 did not assert. See §"what this corrects" in the
scorecard directory.

CONTROLS
--------
`properties()` / `check()` follow the shape the other #369 instruments use: named
properties, each moved by at least one injected defect, one defect asserted blind
and verified blind, and `main()` refuses to report a measurement if the matrix
does not pass.

  chain-exact        the time-domain cascade's magnitude response equals
                     `cymbal_tone_realisation`'s analytic one to < 1e-6 dB rms.
                     Not a tolerance: both are built from the same bank
                     registers, so anything but equality is a transcription bug
  linear-limit       with the nonlinearity off, the probe's LOW-band M re Ln
                     reproduces `cymbal_m_origin.band_energies`' committed
                     figure -- two independently written instruments agreeing
                     on the linear case before the nonlinear one is quoted. The
                     other two bands are deliberately excluded and that
                     exclusion is the finding, not an omission: see
                     `leakage_error`
  im-known-answer    two sines through the clipper put a difference tone at
                     |f1-f2| of amplitude exactly a2*A1*A2, where a2 is the
                     analytic second-order coefficient of the clipper's own
                     expansion. Checked to 2 %
  asym-load-bearing  the SYMMETRIC clipper (bias 0) puts NO difference tone
                     there -- the paired negative, without which
                     `im-known-answer` would pass on any nonlinearity at all
  product-law        a quadratic acting on exp(-t/tau) yields a component whose
                     measured decay time is tau/2. This is the algebra the
                     DECAY half of the prediction rests on, so it is measured
                     rather than asserted
  tau-reads-back     §10's own low-band tau, read back off the render by the
                     shared instrument: T_Ln matches `lt.t_edt(TAU_S["low"])`.
                     This is what ties the strike to the reference's numbers
                     rather than to a plausible-looking envelope
  source-comb        the staircase's spectrum carries all six documented
                     fundamentals (§1.5) and its sum takes exactly 7 levels
  source-alias       a naively sampled square aliases, and the source-tilt
                     finding rests on the staircase's real harmonic content in
                     M -- so the generator's own fold-back is bounded against
                     the same source built at 16x and decimated. 0.018 dB
  drive-monotone     M-band energy rises monotonically with drive
  gain-blind         rho and the band ratios are invariant to a gain applied
                     after the level stage (blind by construction, verified)

WRONG-THEN-RIGHT RATE OF THIS MODULE: 4, every one caught by a control rather
than by inspection, published here because that rate is how a reader calibrates
any single figure above.
  1. `im-known-answer` read 0.35 against a predicted 1.0. The block was right
     and the TEST was wrong: it drove the clipper at the sweep's own 0 dB,
     where the tanh argument has unit RMS and the second-order coefficient is
     not the whole story. It is a known answer only where the expansion holds
     (`KA_DRIVE_DB`).
  2. `product-law` read 2.83 against a predicted 1.0: the dB slope of a POWER
     envelope is -8.686/T, and the first version used -20/T.
  3. `targets()` REFUSED on its first run -- the module quoted the 808's
     weakest rho_M(-10) as 1.048 and the file says 1.0213. Both are right: 1.048
     is the weakest in the 2.0 s window and 1.0213 the weakest over all three,
     and low-tail's own rule is that a comparison is never made across windows.
     The quote was under-specified and the refusal caught it.
  4. `verdict()` reported "reaches both targets at ['sum']" from two rows
     twelve dB of drive apart, and treated "above the top of the 808's range"
     as a match. Both fixed; see its docstring.

REFUSALS. `MIN_BAND_RE_REF_DB` is imported from `cymbal_m_origin` rather than
restated: a band more than 22 dB below Ln is outside the range the shared
instrument was qualified on, and rho there is division by almost nothing
(m_origin's wrong-then-right 2). The clipper REFUSES a drive at which the output
is more than `MAX_THD_DB` of harmonic distortion, because past that the "swing
VCA" is a square-wave generator and no longer anything §10 describes.
"""
from __future__ import annotations

import argparse
import json
import math
import pathlib
import subprocess
import sys

import numpy as np
from scipy.signal import freqz, lfilter

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "model"), str(ROOT / "tools")]
import run_case as rc                      # noqa: E402
import modal_fixed as mf                   # noqa: E402
import cymbal_candidate as cc              # noqa: E402
import cymbal_low_tail as lt               # noqa: E402
import cymbal_m_origin as mo               # noqa: E402
import cymbal_tone_realisation as tr       # noqa: E402

SR = lt.SR
BANDS = mo.BANDS
REF = mo.REF
SPAN_HZ = mo.SPAN_HZ
MIN_BAND_RE_REF_DB = mo.MIN_BAND_RE_REF_DB
TAU_S = mo.TAU_S
CHAIN_DB = mo.CHAIN_DB
ATTACK_S = mo.ATTACK_S

# §1.5, "the nominal frequencies Werner derives from them": four untrimmed plus
# the two factory-trimmed ones. Nominal +-(tens of) percent unit to unit, which
# is why `SEED_PHASE` is carried as a defect and verified not to move the
# headline -- a conclusion that depended on one machine's particular six
# frequencies would not be a conclusion about the TR-808.
OSC_HZ = (205.3, 369.6, 304.4, 522.7, 800.0, 540.0)
DUTY = 0.4798                       # §1.5, "duty cycle 47.98 %", at 5 V
N_OSC = len(OSC_HZ)

# §10's two bridged-T band-passes, and the Q the reference infers for both.
BP_HZ = {"low": 3450.0, "decay": 7100.0, "short": 7100.0}
BP_Q = 6.0

# Where the nonlinearity can sit. These are the three points §10's own sentence
# order admits, and they are DISCRETE structural choices, not a knob:
#   pre_vca   on the band-pass output, before the envelope multiply -- the
#             constant-amplitude swing the transistor actually sees
#   post_vca  on the enveloped signal, before the high-pass -- "the VCAs'
#             asymmetric clipping", read literally
#   sum       after the per-band tone paths meet, before the LEVEL buffer --
#             the one shared node in the chain
POSITIONS = ("none", "pre_vca", "post_vca", "sum")

# Drive, in dB relative to the signal's own RMS at the insertion point. The
# clipper is normalised so that its LINEAR term is the identity at every drive
# (see `clip`), which makes drive -> -inf exactly the linear chain rather than
# approximately it.
DRIVE_DB = (-40.0, -20.0, -12.0, -6.0, 0.0, 6.0, 12.0)

# The asymmetry. §10 says "asymmetric" and quantifies nothing, so this is
# UNRESOLVED in the reference and is swept rather than chosen. `ASYM` is the
# clipper's bias in units of its own knee.
ASYM = 0.5
ASYM_SWEEP = (0.15, 0.3, 0.5, 0.8)

# Past this much harmonic distortion the block is a square-wave generator, not a
# "swing VCA", and the probe refuses rather than reporting.
MAX_THD_DB = -6.0

# Quoted from the committed step-7 artifacts; `targets()` re-reads them and
# REFUSES if they have moved, so these cannot go stale silently.
# The 808's WEAKEST rho_M(-10) IN THE 2.0 s WINDOW, which is the window this
# probe's own 3.6 s renders are trimmed to. Window-specific on purpose: a
# Schroeder curve is integrated from the end of the record, so low-tail's rule
# is that a comparison is never made across windows -- and the first version of
# `targets()` here took the minimum over ALL windows, got 1.0213 (CY0010 at
# 3.5 s) and REFUSED against the 1.048 quoted from `m_origin.verdict`. The
# refusal was right and the quote was under-specified. Wrong-then-right 3.
T_RHO_WINDOW = "2.0s"
T_RHO_808_MIN = 1.048           # CY2550, 2.0 s window
T_RHO_808_MIN_ANY_WINDOW = 1.021        # CY0010, 3.5 s window -- reported, not the target
T_RHO_BASELINE_MAX = 1.017      # the instrument's own skirt baseline, top of range
T_M_RE_LN_808_MEDIAN = -3.73    # the 808's median M re Ln over the first second
T_CHAIN_BOUND_DB = -13.10       # the ANALYTIC bound; see `leakage_error` for why
                                # that is not the bound a filtered measurement faces

# The 808's measured RANGE on each quantity, in the 2.0 s window, from the same
# two artifacts. A match is landing inside both boxes at once; being above the
# top of one is not a match, it is a different instrument (`verdict`, note 2).
RHO_M_808_RANGE = (1.0476, 1.1392)          # low-tail.json, 2.0 s window, 10 settings
M_RE_LN_808_RANGE = (-4.998, -3.448)        # m-origin.json, gap.bands.M, 20 settings

Refused = mo.Refused


# ---------------------------------------------------------------------------
# the source: §1.5's six squares, summed as a 7-level staircase
# ---------------------------------------------------------------------------
def staircase(n, *, sr=SR, freqs=OSC_HZ, duty=DUTY, phase_seed=0, defect=None, oversample=1):
    """The passive sum of six 0/5 V squares through six equal resistors.

    Returned DC-free, because every path downstream begins with a band-pass that
    blocks it; keeping the offset would only change what the clipper's bias
    means. `defect="SINGLE_OSC"` collapses the bank to one oscillator, which
    removes the beating the whole mechanism depends on.

    `oversample` exists because a naively sampled square wave ALIASES: its
    harmonics run to infinity and everything above sr/2 folds back into the
    audio band, including M. That is a defect of the generator that would look
    exactly like a finding. `_source_alias` bounds it by building the same
    source at 16x with a proper decimation filter and comparing M re Ln.
    """
    use = freqs[:1] if defect == "SINGLE_OSC" else freqs
    k = max(1, int(oversample))
    t = np.arange(n * k, dtype=np.float64) / (sr * k)
    ph = np.random.default_rng(phase_seed).random(len(use)) if phase_seed else np.zeros(len(use))
    y = np.zeros(n * k)
    for f, p in zip(use, ph):
        y = y + (np.mod(t * f + p, 1.0) < duty).astype(np.float64)
    if k > 1:
        y = decimate_fir(y, k)
    return y - float(np.mean(y))


def decimate_fir(y, k, *, numtaps=513):
    """Decimate by `k` through a linear-phase FIR at 0.45 of the output rate.
    scipy's `decimate` defaults to an IIR; an FIR is used so the comparison the
    aliasing control makes is not itself shaped by a filter's phase."""
    from scipy.signal import firwin
    h = firwin(numtaps, 0.9 / k)
    return np.convolve(y, h, mode="same")[::k]


def source(n, *, kind="staircase", seed=3, sr=SR, defect=None):
    """The probe's two sources. `white` is what step 7's magnitude-integral bound
    implicitly assumed; `staircase` is what §1.5 says the machine has."""
    if kind == "white":
        return np.random.default_rng(seed).standard_normal(n)
    if kind == "staircase":
        return staircase(n, sr=sr, phase_seed=seed if defect == "SEED_PHASE" else 0,
                         defect=defect)
    raise Refused(f"unknown source {kind!r}")


def source_levels(n=1 << 14, **kw) -> int:
    """How many distinct levels the summed staircase takes. Six 0/5 V squares
    through equal resistors give exactly 7; anything else means the sum is not
    the one §1.5 describes."""
    return int(len(np.unique(np.round(staircase(n, **kw), 6))))


# ---------------------------------------------------------------------------
# the chain, in the time domain, from the SAME registers as the magnitude model
# ---------------------------------------------------------------------------
_NUM_B = {mf.RAW: [1.0], mf.BP: [1.0, 0.0, -1.0], mf.HP: [1.0, -2.0, 1.0],
          cc.HP3: [1.0, -3.0, 3.0, -1.0]}


def _sec(a1, a2, num):
    """One bank section as (b, a): y = ((a1 y1 + a2 y2) >> 24) + num(x)."""
    return list(_NUM_B[num]), [1.0, -a1 / (1 << 24), -a2 / (1 << 24)]


def band_sections(band, *, defect=None) -> list:
    """The band's own filters, section by section -- exactly the cascade
    `cymbal_tone_realisation.band_chain_db` sums in dB, so the two agree by
    construction and `chain-exact` checks the construction."""
    secs = [_sec(*mf.pole_regs(BP_HZ[band], BP_Q), mf.BP)]
    if defect != "NO_HIGHPASS":
        hh = {"low": (cc.HH1_HZ, cc.HH1_Q), "decay": (cc.HH2_HZ, cc.HH2_Q),
              "short": (cc.HH3_HZ, cc.HH3_Q)}[band]
        secs.append(_sec(*mf.pole_regs(*hh), mf.HP))
        if band == "short":
            a1, _ = cc.real_pole_regs([cc.HH3_P1_HZ])
            secs.append(([1.0, -1.0], [1.0, -a1 / (1 << 24)]))
    return secs


def tone_sections(band, poles, *, defect=None) -> list:
    """(tone stage x LEVEL stage) as revision 3 realises it -- the same
    `realisation()` that `cymbal_tone_realisation.realised_db` plots:
    (1 - z^-1)^dc_zeros / (1 - a1 z^-1 - a2 z^-2)."""
    if defect == "NO_LEVEL_STAGE":
        return []
    r = tr.realisation(band, poles)
    return [([1.0, -1.0] if r["dc_zeros"] else [1.0], [1.0, -r["a1_f"], -r["a2_f"]])]


def _apply(x, secs):
    for b, a in secs:
        x = lfilter(b, a, x)
    return x


def chain_db(band, hz, poles, *, defect=None) -> np.ndarray:
    """The time-domain cascade's own magnitude response, read off the filters
    the render uses -- not off a parallel analytic model of them."""
    out = np.zeros(len(np.atleast_1d(hz)))
    for b, a in band_sections(band, defect=defect) + tone_sections(band, poles, defect=defect):
        w, h = freqz(b, a, worN=2 * math.pi * np.asarray(hz, dtype=float) / SR)
        out = out + 20.0 * np.log10(np.maximum(np.abs(h), 1e-30))
    return out


# ---------------------------------------------------------------------------
# the nonlinearity
# ---------------------------------------------------------------------------
def clip_a2(drive, asym=ASYM, *, defect=None):
    """The clipper's analytic SECOND-ORDER coefficient.

    y(x) = [tanh(g x + b) - tanh(b)] / [g (1 - tanh(b)^2)] expands about b as
    x - tanh(b) g x^2 + O(x^3), so a2 = -tanh(b) * g, exactly. Two things follow
    and both are used as controls: the linear term is the identity at EVERY
    drive (so drive -> 0 is the linear chain exactly), and at b = 0 the
    second-order term vanishes identically (the paired negative).
    """
    g = 0.0 if drive is None else 10.0 ** (drive / 20.0)
    b = 0.0 if defect == "SYMMETRIC_NL" else asym
    if defect == "NO_NONLINEARITY":
        return 0.0
    return -math.tanh(b) * g


def clip(x, drive, asym=ASYM, *, defect=None, rms=None):
    """§10's "asymmetric clipping", as a biased tanh normalised so that its
    linear term is the identity. `drive` is in dB relative to the signal's own
    RMS at this point, so the same number means the same thing at every
    insertion position."""
    if drive is None or defect == "NO_NONLINEARITY":
        return np.asarray(x, dtype=np.float64)
    r = float(rms if rms is not None else np.sqrt(np.mean(np.asarray(x) ** 2)))
    if r <= 0:
        raise Refused("the signal at the clipper is silent")
    b = 0.0 if defect == "SYMMETRIC_NL" else asym
    g = 10.0 ** (drive / 20.0) / r
    return (np.tanh(g * np.asarray(x, dtype=np.float64) + b) - math.tanh(b)) / (g * (1 - math.tanh(b) ** 2))


def thd_db(drive, asym=ASYM, *, f0=1000.0, n=1 << 15, sr=SR):
    """Harmonic distortion of the clipper on a unit sine, as dB relative to the
    fundamental. `MAX_THD_DB` is the refusal: past it the block is a square-wave
    generator and outside anything §10 describes."""
    t = np.arange(n) / sr
    x = np.sin(2 * math.pi * f0 * t)
    y = clip(x, drive, asym, rms=float(np.sqrt(0.5)))
    sp = np.abs(np.fft.rfft(y * np.hanning(n)))
    k = int(round(f0 * n / sr))
    fund = float(np.max(sp[k - 3:k + 4]))
    rest = sp.copy()
    rest[max(0, k - 3):k + 4] = 0.0
    rest[:8] = 0.0
    return 20.0 * math.log10(max(float(np.sqrt(np.sum(rest ** 2))), 1e-30) / max(fund, 1e-30))


# ---------------------------------------------------------------------------
# the render
# ---------------------------------------------------------------------------
def render(*, position="post_vca", drive=0.0, asym=ASYM, kind="staircase", taus=None,
           dur=3.6, seed=3, sr=SR, levels=None, defect=None, post_gain=1.0):
    """One strike through §10's three-band chain with the nonlinearity at
    `position`. Returns a record ready for `cymbal_low_tail.measure`."""
    taus = dict(taus or TAU_S)
    if defect == "SWAP_TAUS":
        taus["low"], taus["decay"] = taus["decay"], taus["low"]
    lvl = dict(levels or CHAIN_DB)
    n = int(dur * sr)
    poles = tr.tone_poles()
    src = source(n, kind=kind, seed=seed, sr=sr, defect=defect)
    d = None if position == "none" else drive
    summed = np.zeros(n)
    for band in ("low", "decay", "short"):
        x = _apply(src, band_sections(band, defect=defect))
        if position == "pre_vca":
            x = clip(x, d, asym, defect=defect)
        x = x * mo.envelope(taus[band], n, sr, ATTACK_S)
        if position == "post_vca":
            x = clip(x, d, asym, defect=defect)
        x = _apply(x, tone_sections(band, poles, defect=defect))
        summed = summed + 10.0 ** (lvl[band] / 20.0) * x
    if position == "sum":
        summed = clip(summed, d, asym, defect=defect)
    peak = float(np.max(np.abs(summed)))
    if peak <= 0:
        raise Refused("the render is silent")
    y = post_gain * (2.0 if defect == "POST_GAIN_2X" else 1.0) * summed / peak
    return rc.prepare(np.concatenate([np.zeros(sr // 20), y]), sr,
                      side=f"cy-vca {position} {drive:+.0f} dB {kind}"), sr


def read(y, sr, *, trim_s=lt.TRIM_S) -> dict:
    """The two quantities, through the SHARED instrument, with m_origin's
    qualified-range refusal applied to each band at the point of use."""
    m = lt.measure(y, sr, trim_s=trim_s)
    out = {"refused": {}}
    for band in ("M", "Mn"):
        re_ref = 10 * math.log10(m["bands"][band]["energy_j"] / m["bands"][REF]["energy_j"])
        out[f"{band}_re_{REF}"] = round(re_ref, 2)
        if re_ref < MIN_BAND_RE_REF_DB:
            out[f"rho_{band}_-10"] = None
            out["refused"][band] = (f"{band} is {re_ref:.1f} dB below {REF}, under the "
                                    f"{MIN_BAND_RE_REF_DB:.0f} dB the instrument is qualified to")
        else:
            out[f"rho_{band}_-10"] = m["rho"][band]["-10"]
            out[f"rho_{band}_-5"] = m["rho"][band]["-5"]
    return out


# ---------------------------------------------------------------------------
# the source-spectrum correction to step 7's bound
# ---------------------------------------------------------------------------
def source_tilt(*, dur=3.6, seed=3, sr=SR) -> dict:
    """What step 7's bound assumed, and what §1.5 actually supplies.

    `cymbal_m_origin.band_energies` integrates the chain's magnitude response,
    i.e. the energy a FLAT source leaves in each band. Rendering the same chain
    from the staircase instead moves M re Ln by this much, per band. It is a
    precondition of step 7's headline gap that step 7 did not assert.
    """
    n = int(dur * sr)
    poles = tr.tone_poles()
    out = {}
    for band in ("low", "decay", "short"):
        row = {}
        for kind in ("white", "staircase"):
            x = _apply(source(n, kind=kind, seed=seed, sr=sr), band_sections(band))
            x = _apply(x, tone_sections(band, poles))
            e = {}
            for name, (lo, hi) in BANDS.items():
                xb = lt.cb._bp(x, sr, lo, hi)
                e[name] = float(np.sum(xb ** 2))
            row[kind] = {f"{k}_re_{REF}": round(10 * math.log10(e[k] / e[REF]), 2)
                         for k in ("M", "Mn")}
        row["tilt_M_db"] = round(row["staircase"][f"M_re_{REF}"] - row["white"][f"M_re_{REF}"], 2)
        row["tilt_Mn_db"] = round(row["staircase"][f"Mn_re_{REF}"] - row["white"][f"Mn_re_{REF}"], 2)
        out[band] = row
    out["bound_white_db"] = max(v["white"][f"M_re_{REF}"] for v in out.values()
                                if isinstance(v, dict) and "white" in v)
    out["bound_staircase_db"] = max(v["staircase"][f"M_re_{REF}"] for v in out.values()
                                    if isinstance(v, dict) and "white" in v)
    out["bound_moves_db"] = round(out["bound_staircase_db"] - out["bound_white_db"], 2)
    return out


# ---------------------------------------------------------------------------
# the properties and the defects that must move them
# ---------------------------------------------------------------------------
PROPERTIES = ("chain-exact", "linear-limit", "im-known-answer", "asym-load-bearing",
              "product-law", "tau-reads-back", "source-comb", "source-alias",
              "drive-monotone", "gain-blind")
DEFECTS = ("NO_NONLINEARITY", "SYMMETRIC_NL", "SINGLE_OSC", "NO_HIGHPASS",
           "SWAP_TAUS", "NO_LEVEL_STAGE")
BLIND_BY_CONSTRUCTION = ("POST_GAIN_2X", "SEED_PHASE")


def _chain_exact(poles, *, defect=None):
    """The time-domain cascade against `cymbal_tone_realisation`'s analytic
    chain, in dB rms over the span. These are built from the same registers, so
    the answer is 0 to floating point and any tolerance at all would be slack."""
    hz = np.geomspace(*SPAN_HZ, 2000)
    err = []
    for band in ("low", "decay", "short"):
        mine = chain_db(band, hz, poles, defect=defect)
        theirs = tr.band_chain_db(band, hz) + tr.realised_db(band, hz, poles)
        err.append(float(np.sqrt(np.mean((mine - theirs) ** 2))))
    return max(err)


def _linear_limit(*, defect=None):
    """The LOW band's rendered M re Ln under a white source with the
    nonlinearity off, against `cymbal_m_origin.band_energies`' committed `cand3`
    figure -- two instruments, written independently, agreeing on the linear
    case before the nonlinear one is quoted.

    **Only the low band.** The decay and short bands are deliberately NOT
    asserted here, and that is this module's largest finding rather than an
    omission: their M content sits 27-36 dB below their own peaks at 7.1 kHz,
    and the analysis band-pass's skirt admits more of that peak's shoulder than
    the band truly holds, so a rendered measurement of them reads 3.7 and 9.5 dB
    HIGH. `leakage_error()` reports all three, and `rendered_bound()` is the
    consequence. The bound here is 0.6 dB rather than zero because even the low
    band's reading carries the analysis filter's -24.6 dB rejection at 3.45 kHz.
    """
    e = mo.band_energies()
    return abs(_band_white_m("low", defect=defect) - e["low"][f"cand3_M_re_{REF}"])


def _band_white_m(band, *, defect=None, dur=3.6, seed=3, sr=SR, kind="white", bands=("M",)):
    n = int(dur * sr)
    poles = tr.tone_poles()
    x = _apply(source(n, kind=kind, seed=seed, sr=sr, defect=defect),
               band_sections(band, defect=defect))
    x = _apply(x, tone_sections(band, poles, defect=defect))
    e = {k: float(np.sum(lt.cb._bp(x, sr, *BANDS[k]) ** 2)) for k in set(bands) | {REF}}
    out = {k: 10 * math.log10(e[k] / e[REF]) for k in bands}
    return out["M"] if bands == ("M",) else out


def analytic_band_db(band, *, poles=None, n=4000) -> dict:
    """The same quantity computed from the cascade's magnitude response, i.e.
    the energy a flat source leaves in each analysis band with NO analysis
    filter involved. Reproduces `cymbal_m_origin.band_energies`' `cand3` column
    exactly, which `test_cymbal_vca_clip` asserts."""
    poles = poles or tr.tone_poles()
    hz = np.geomspace(*SPAN_HZ, n)
    tot = chain_db(band, hz, poles)
    e = {}
    for name, (lo, hi) in BANDS.items():
        m = (hz >= lo) & (hz <= hi)
        e[name] = float(np.trapezoid(10 ** (tot[m] / 10.0), hz[m]))
    return {k: round(10 * math.log10(e[k] / e[REF]), 2) for k in ("M", "Mn")}


def leakage_error(*, kind="white") -> dict:
    """Per band: what the ANALYSIS filter reads off a rendered record, minus
    what the band truly holds.

    This is the like-for-like check step 7's §5 did not make. Its bound came
    from integrating the cascade's magnitude response (no analysis filter); the
    808 figures it was compared against came from `cymbal_low_tail.measure`
    (analysis filter). For the low band the two agree, because Ln IS its peak.
    For the two bands whose peak is at 7.1 kHz they do not, and the error is in
    the direction that makes a measured M look larger than it is.
    """
    out = {}
    for band in ("low", "decay", "short"):
        got = _band_white_m(band, kind=kind, bands=("M", "Mn"))
        want = analytic_band_db(band)
        out[band] = {"rendered_M_re_Ln": round(got["M"], 2), "analytic_M_re_Ln": want["M"],
                     "leak_M_db": round(got["M"] - want["M"], 2),
                     "rendered_Mn_re_Ln": round(got["Mn"], 2), "analytic_Mn_re_Ln": want["Mn"],
                     "leak_Mn_db": round(got["Mn"] - want["Mn"], 2)}
    return out


def band_records(*, kind="white", taus=None, dur=3.6, seed=3, sr=SR, defect=None) -> dict:
    """Each band's own contribution to the strike, rendered once, at its
    `CHAIN_DB` level. The linear chain is linear, so a balance sweep is a
    weighted sum of these three rather than a re-render each time -- which is
    what makes `rendered_bound`'s balance sweep cheap. The first version
    re-rendered every balance and did not finish inside two minutes."""
    taus = dict(taus or TAU_S)
    n = int(dur * sr)
    poles = tr.tone_poles()
    src = source(n, kind=kind, seed=seed, sr=sr, defect=defect)
    out = {}
    for band in ("low", "decay", "short"):
        x = _apply(src, band_sections(band, defect=defect))
        x = x * mo.envelope(taus[band], n, sr, ATTACK_S)
        out[band] = _apply(x, tone_sections(band, poles, defect=defect))
    return out


def _combine(recs, levels, sr=SR):
    y = sum(10.0 ** (levels[b] / 20.0) * recs[b] for b in recs)
    peak = float(np.max(np.abs(y)))
    if peak <= 0:
        raise Refused("the mix is silent")
    return rc.prepare(np.concatenate([np.zeros(sr // 20), y / peak]), sr, side="cy-vca mix"), sr


def rendered_bound(*, kind="white", lo=-12.0, hi=30.0, step=6.0, taus=None,
                   trim_s=lt.TRIM_S) -> dict:
    """The linear chain's bound on M re Ln, computed the way the 808 figures it
    is compared against were computed: by RENDERING each balance and reading it
    with `cymbal_low_tail.measure`.

    Step 7's -13.10 dB is the analytic bound. Against a measurement made with
    the analysis filter it is not a like-for-like comparison, and `leakage_error`
    says by how much. This is the same sweep, same lemma, same instrument on
    both sides.
    """
    recs = band_records(kind=kind, taus=taus)
    best = {"M": (-1e9, None), "Mn": (-1e9, None)}
    rows = []
    for d in np.arange(lo, hi + 1e-9, step):
        for s in np.arange(lo, hi + 1e-9, step):
            y, sr = _combine(recs, {"low": 0.0, "decay": float(d), "short": float(s)})
            r = read(y, sr, trim_s=trim_s)
            rows.append({"decay_db": float(d), "short_db": float(s),
                         "M_re_Ln": r[f"M_re_{REF}"], "Mn_re_Ln": r[f"Mn_re_{REF}"],
                         "rho_M_-10": r.get("rho_M_-10")})
            for name in ("M", "Mn"):
                if rows[-1][f"{name}_re_Ln"] > best[name][0]:
                    best[name] = (rows[-1][f"{name}_re_Ln"], (float(d), float(s)))
    rg = [r["rho_M_-10"] for r in rows if r.get("rho_M_-10") is not None]
    # The JOINT question, which neither this step's bound nor step 7's answers on
    # its own: does any LINEAR balance land inside the 808's measured range on
    # BOTH quantities at once? The two are not independent -- the balances that
    # raise rho are the ones that starve M -- so a bound on each separately does
    # not say whether the pair is reachable.
    box = [r for r in rows if r.get("rho_M_-10") is not None
           and RHO_M_808_RANGE[0] <= r["rho_M_-10"] <= RHO_M_808_RANGE[1]
           and M_RE_LN_808_RANGE[0] <= r["M_re_Ln"] <= M_RE_LN_808_RANGE[1]]
    return {"source": kind, "n_balances": len(rows),
            "max_rho_M_-10": max(rg) if rg else None,
            "n_rho_answered": len(rg),
            "n_balances_in_808_box": len(box),
            "balances_in_808_box": box[:8],
            "at_max_rho": max((r for r in rows if r.get("rho_M_-10") is not None),
                              key=lambda r: r["rho_M_-10"], default=None),
            **{name: {"rendered_bound_db": round(best[name][0], 2),
                      "at_decay_short_db": best[name][1],
                      "analytic_bound_db": max(analytic_band_db(b)[name]
                                               for b in ("low", "decay", "short"))}
               for name in ("M", "Mn")}}


# The known-answer drive. The clipper's second-order coefficient is the leading
# term of an EXPANSION, so it is the right answer only where the expansion is
# valid: at the sweep's own 0 dB the tanh argument has unit RMS and the x^3 and
# x^4 terms are not small. This is wrong-then-right 1 of this module -- the test
# first ran at 0 dB and read 0.35 against a predicted 1.0, which is the
# expansion failing, not the block being wrong. -20 dB puts the argument at
# 0.1 RMS, where the quartic correction is ~1e-2 of the quadratic.
KA_DRIVE_DB = -20.0
KA_F1, KA_F2 = 3400.0, 2600.0     # bin-aligned at KA_N below, so no scalloping loss
KA_N = 48_000                     # one second at SR: bin spacing exactly 1 Hz
KA_AMP = 0.02


def _im_amp(*, defect=None, drive=KA_DRIVE_DB, f1=KA_F1, f2=KA_F2, amp=KA_AMP,
            n=KA_N, sr=SR) -> tuple:
    """(measured, predicted) amplitude of the difference tone at |f1-f2|.

    Two sines of equal amplitude through the clipper. x^2 contains
    A1*A2*cos((w1-w2)t) exactly once, so the analytic second-order coefficient
    predicts the tone's amplitude with no free parameter. `f1`, `f2` and their
    difference all land on exact DFT bins at `n`, so the peak is the amplitude
    rather than the amplitude minus an unstated scalloping loss.
    """
    t = np.arange(n) / sr
    x = amp * (np.cos(2 * math.pi * f1 * t) + np.cos(2 * math.pi * f2 * t))
    rms = float(np.sqrt(np.mean(x ** 2)))
    y = clip(x, drive, ASYM, defect=defect, rms=rms)
    a2 = clip_a2(drive, ASYM, defect=defect) / rms
    sp = np.abs(np.fft.rfft(y * np.hanning(n))) * 4.0 / n
    k = int(round(abs(f1 - f2) * n / sr))
    return float(np.max(sp[k - 2:k + 3])), abs(a2) * amp * amp


def _im_known_answer(*, defect=None):
    """measured / predicted, which is 1.0 when the block is the documented one.
    REFUSES rather than dividing when the prediction is exactly zero -- that is
    the symmetric case, and it is `asym-load-bearing`'s job, not this one's."""
    got, want = _im_amp(defect=defect)
    if want <= 0:
        raise Refused("the clipper's predicted second-order coefficient is zero; "
                      "measured/predicted is not defined (see asym-load-bearing)")
    return got / want


def _asym_load_bearing(*, defect=None):
    """The paired negative, without which `im-known-answer` would pass on any
    nonlinearity at all: force the bias to zero and the difference tone must be
    gone. Returned as the symmetric case's difference tone in dB relative to the
    asymmetric one, so "gone" is a number."""
    hi, _ = _im_amp(defect=defect)
    lo, _ = _im_amp(defect="SYMMETRIC_NL")
    return 20 * math.log10(max(lo, 1e-18) / max(hi, 1e-18))


def _product_law(*, defect=None, tau=0.25, dur=2.0, sr=SR, drive=KA_DRIVE_DB):
    """A quadratic acting on carriers with envelope exp(-t/tau) leaves a
    difference component whose amplitude envelope is exp(-2t/tau) -- HALF the
    time constant of the band it came from. That is the algebra the DECAY half
    of this module's prediction rests on, so it is measured, not asserted.

    Returned as fitted/expected. A power envelope falling exp(-4t/tau) has a dB
    slope of -8.686/(tau/2), so the amplitude time constant is -8.686/slope;
    the first version of this used -20/slope and read 2.83 against an expected
    1.0 (wrong-then-right 2 of this module).
    """
    n = int(dur * sr)
    t = np.arange(n) / sr
    env = np.exp(-t / tau)
    x = env * (np.cos(2 * math.pi * KA_F1 * t) + np.cos(2 * math.pi * KA_F2 * t))
    y = clip(x, drive, ASYM, defect=defect, rms=float(np.sqrt(np.mean(x ** 2))))
    d = lt.cb._bp(y, sr, 700.0, 900.0)            # the 800 Hz difference tone
    w = int(0.02 * sr)
    e = np.convolve(d * d, np.ones(w) / w, mode="same")
    i0, i1 = int(0.05 * sr), int(0.40 * sr)
    slope = np.polyfit(t[i0:i1], 10 * np.log10(np.maximum(e[i0:i1], 1e-30)), 1)[0]
    if slope >= 0:
        raise Refused("the difference component does not decay")
    return (-8.686 / slope) / (tau / 2.0)


def _tau_reads_back(*, defect=None, depth="-10"):
    """§10's own envelope, read back off the render by the SHARED instrument.

    Ln (2.9-4.1 kHz) is the low band's own peak, so the low path's tau ought to
    be what the record's Ln Schroeder curve reports: `lt.t_edt(TAU_S["low"])`.
    Returned as measured/expected. This is what ties the rendered strike to the
    reference's numbers rather than to a plausible-looking envelope, and it is
    the property `SWAP_TAUS` moves.
    """
    y, sr = render(position="none", drive=0.0, defect=defect, dur=1.6)
    m = lt.measure(y, sr, trim_s=1.5)
    got = m["bands"][REF]["times_ms"].get(depth)
    if got is None:
        raise Refused(f"the reference band refuses at {depth} dB: "
                      f"{m['bands'][REF]['refused'].get(depth)}")
    return got / lt.t_edt(TAU_S["low"], float(depth))


def _source_alias(*, defect=None, dur=1.6, sr=SR):
    """How much of the staircase's M-band energy is ALIASING rather than signal.

    A naively sampled square wave has harmonics to infinity and everything above
    sr/2 folds back, including into 891-2828 Hz. The whole source-tilt finding
    below rests on the staircase's real 1/n harmonic content there, so the
    generator's own fold-back has to be bounded before that finding is quoted --
    otherwise the tilt could be the generator and would look exactly like the
    machine. Returned as |dB difference| in M re Ln between the source as
    rendered and the same source built at 16x and decimated.
    """
    n = int(dur * sr)
    poles = tr.tone_poles()
    out = []
    for k in (1, 16):
        x = staircase(n, sr=sr, defect=defect, oversample=k)
        x = _apply(_apply(x, band_sections("low", defect=defect)),
                   tone_sections("low", poles, defect=defect))
        e = {b: float(np.sum(lt.cb._bp(x, sr, *BANDS[b]) ** 2)) for b in ("M", REF)}
        out.append(10 * math.log10(e["M"] / e[REF]))
    return abs(out[0] - out[1])


def _source_comb(*, defect=None, n=1 << 17, sr=SR):
    """All six documented fundamentals present as peaks, and the sum taking
    exactly 7 levels. Returned as (n_found + n_levels/7) so a single number
    carries both and either failing moves it. The six frequencies checked for
    are FIXED -- an earlier revision looked for only the surviving one under
    `SINGLE_OSC`, which is a test that adapts to the defect it is meant to
    catch."""
    x = staircase(n, sr=sr, phase_seed=(3 if defect == "SEED_PHASE" else 0), defect=defect)
    sp = np.abs(np.fft.rfft(x * np.hanning(n)))
    found = 0
    for f in OSC_HZ:
        k = int(round(f * n / sr))
        w = sp[max(0, k - 40):k + 41]
        if len(w) and float(np.max(w)) >= 20 * float(np.median(sp[8:int(2000 * n / sr)])):
            found += 1
    lv = len(np.unique(np.round(x, 6)))
    return found + lv / (N_OSC + 1.0)


def _drive_monotone(*, defect=None, drives=(-40.0, -12.0, 0.0, 12.0)):
    """M-band energy against drive, as the smallest step: positive means
    monotone rising."""
    vals = []
    for d in drives:
        y, sr = render(position="post_vca", drive=d, defect=defect, dur=1.6)
        vals.append(read(y, sr, trim_s=1.5)[f"M_re_{REF}"])
    return min(b - a for a, b in zip(vals, vals[1:]))


def _gain_blind(*, defect=None):
    """rho and M re Ln with a 2x gain applied after the level stage. Returned as
    the largest absolute change; blind by construction and verified blind."""
    a = read(*render(position="post_vca", drive=0.0, defect=defect, dur=1.6), trim_s=1.5)
    b = read(*render(position="post_vca", drive=0.0, defect=defect, dur=1.6, post_gain=2.0),
             trim_s=1.5)
    return max(abs(a[f"M_re_{REF}"] - b[f"M_re_{REF}"]),
               abs((a["rho_M_-10"] or 0) - (b["rho_M_-10"] or 0)))


# property -> (function, predicate on the clean value)
_BOUNDS = {
    "chain-exact": (lambda **k: _chain_exact(tr.tone_poles(), **k), lambda v: v < 1e-6),
    "linear-limit": (_linear_limit, lambda v: v < 0.6),
    "im-known-answer": (_im_known_answer, lambda v: 0.98 <= v <= 1.02),
    "asym-load-bearing": (_asym_load_bearing, lambda v: v < -40.0),
    "product-law": (_product_law, lambda v: 0.92 <= v <= 1.08),
    "tau-reads-back": (_tau_reads_back, lambda v: 0.90 <= v <= 1.10),
    "source-comb": (_source_comb, lambda v: v >= N_OSC + 1.0 - 1e-9),
    "source-alias": (_source_alias, lambda v: v < 0.30),
    "drive-monotone": (_drive_monotone, lambda v: v > 0.0),
    "gain-blind": (_gain_blind, lambda v: v < 1e-6),
}


def properties(*, defect=None) -> dict:
    out = {}
    for name, (fn, ok) in _BOUNDS.items():
        try:
            v = fn(defect=defect)
            out[name] = {"value": (round(v, 6) if isinstance(v, float) else v), "pass": bool(ok(v))}
        except Refused as exc:
            out[name] = {"value": None, "pass": False, "refused": str(exc)}
    return out


def check(*, fast=True) -> tuple[bool, list[str]]:
    """The clean measurement, then every injected defect, then the blind ones.
    A defect that turns nothing red is a defect the suite cannot see."""
    lines, ok = [], True
    clean = properties()
    lines.append("  clean measurement")
    for k, v in clean.items():
        lines.append(f"    {k:20s} {'PASS' if v['pass'] else 'FAIL'}   {v['value']}")
        ok &= v["pass"]
    lines.append("")
    lines.append("  defect            " + " ".join(f"{p:>18s}" for p in PROPERTIES))
    for d in DEFECTS:
        got = properties(defect=d)
        moved = {k: (not got[k]["pass"]) for k in PROPERTIES}
        if not any(moved.values()):
            ok = False
            lines.append(f"  {d:18s} TURNED NOTHING RED -- this defect is invisible to the suite")
            continue
        lines.append(f"  {d:18s}" + " ".join(f"{('MOVED' if moved[p] else ''):>18s}"
                                             for p in PROPERTIES))
    for d in BLIND_BY_CONSTRUCTION:
        got = properties(defect=d)
        bad = [k for k in PROPERTIES if not got[k]["pass"]]
        lines.append(f"  {d:18s}" + ("  (blind by construction, verified blind)" if not bad
                                     else f"  NOT BLIND: {bad}"))
        ok &= not bad
    return ok, lines


# ---------------------------------------------------------------------------
# the sweep and the verdict
# ---------------------------------------------------------------------------
def targets(low_tail=None, m_origin=None) -> dict:
    """Re-read the committed step-7 artifacts and REFUSE if the numbers this
    module quotes have moved. A target copied into a docstring goes stale; one
    checked against its source cannot."""
    lt_p = low_tail or ROOT / "docs/scorecard/cymbal-369/low-tail/low-tail.json"
    mo_p = m_origin or ROOT / "docs/scorecard/cymbal-369/low-tail/m-origin.json"
    out = {"rho_808_min": T_RHO_808_MIN, "rho_baseline_max": T_RHO_BASELINE_MAX,
           "m_re_ln_808_median": T_M_RE_LN_808_MEDIAN, "chain_bound_db": T_CHAIN_BOUND_DB,
           "rho_M_808_range": list(RHO_M_808_RANGE), "m_re_ln_808_range": list(M_RE_LN_808_RANGE),
           "verified_against": {}}
    if mo_p.is_file():
        blob = json.loads(mo_p.read_text())
        g = blob.get("gap", {}).get("bands", {}).get("M", {})
        if blob.get("gap", {}).get("window") != T_RHO_WINDOW:
            raise Refused(f"{mo_p.name}'s gap is in the "
                          f"{blob.get('gap', {}).get('window')} window, this module compares "
                          f"in {T_RHO_WINDOW}")
        for key, want in (("chain_bound_db", T_CHAIN_BOUND_DB),
                          ("measured_median", T_M_RE_LN_808_MEDIAN),
                          ("measured_min", M_RE_LN_808_RANGE[0]),
                          ("measured_max", M_RE_LN_808_RANGE[1])):
            got = g.get(key)
            if got is None:
                raise Refused(f"{mo_p.name} carries no gap.bands.M.{key}")
            if abs(float(got) - want) > 0.011:
                raise Refused(f"{mo_p.name} says {key} = {got}, this module quotes {want}")
        out["verified_against"]["m-origin.json"] = str(mo_p)
    if lt_p.is_file():
        blob = json.loads(lt_p.read_text())
        per = {}
        for wname, w in blob.get("windows", {}).items():
            vals = [float(m["rho"]["M"]["-10"]) for m in w.get("fischer", {}).values()
                    if m.get("rho", {}).get("M", {}).get("-10") is not None]
            if vals:
                per[wname] = {"n": len(vals), "min": round(min(vals), 4),
                              "max": round(max(vals), 4)}
        if T_RHO_WINDOW not in per:
            raise Refused(f"{lt_p.name} carries no Fischer rho_M(-10) readings in the "
                          f"{T_RHO_WINDOW} window, which is the one this probe compares in")
        if abs(per[T_RHO_WINDOW]["min"] - T_RHO_808_MIN) > 0.0011:
            raise Refused(f"{lt_p.name}'s weakest 808 rho_M(-10) in the {T_RHO_WINDOW} window "
                          f"is {per[T_RHO_WINDOW]['min']}, this module quotes {T_RHO_808_MIN}")
        for i, key in enumerate(("min", "max")):
            if abs(per[T_RHO_WINDOW][key] - RHO_M_808_RANGE[i]) > 0.0011:
                raise Refused(f"{lt_p.name}'s 808 rho_M(-10) {key} in the {T_RHO_WINDOW} "
                              f"window is {per[T_RHO_WINDOW][key]}, this module quotes "
                              f"{RHO_M_808_RANGE[i]}")
        allmin = min(v["min"] for v in per.values())
        if abs(allmin - T_RHO_808_MIN_ANY_WINDOW) > 0.0011:
            raise Refused(f"{lt_p.name}'s weakest 808 rho_M(-10) over all windows is "
                          f"{allmin}, this module quotes {T_RHO_808_MIN_ANY_WINDOW}")
        out["verified_against"]["low-tail.json"] = str(lt_p)
        out["rho_window"] = T_RHO_WINDOW
        out["per_window_808_rho_M"] = per
    return out


def sweep(*, positions=POSITIONS, drives=DRIVE_DB, asym=ASYM, kind="staircase",
          trim_s=lt.TRIM_S, taus=None) -> list[dict]:
    """rho and M re Ln at every (position, drive), with the clipper's own THD
    reported beside each so a reading taken past `MAX_THD_DB` is visible."""
    rows = []
    for pos in positions:
        for d in (drives if pos != "none" else (None,)):
            row = {"position": pos, "drive_db": d, "asym": asym, "source": kind}
            row["thd_db"] = (None if d is None else round(thd_db(d, asym), 2))
            if row["thd_db"] is not None and row["thd_db"] > MAX_THD_DB:
                row["refused_drive"] = (f"THD {row['thd_db']:.1f} dB exceeds {MAX_THD_DB:.0f} dB: "
                                        f"the block is a square-wave generator, not a swing VCA")
            y, sr = render(position=pos, drive=(0.0 if d is None else d), asym=asym,
                           kind=kind, taus=taus)
            row.update(read(y, sr, trim_s=trim_s))
            rows.append(row)
    return rows


def verdict(rows, *, targets_=None) -> dict:
    """Does any ONE (position, drive) land inside the 808's measured range on
    BOTH quantities at the same time?

    Three things this deliberately does NOT do, each of which an earlier
    revision did and each of which would have reported a match that is not one:

    1. It does not let different rows satisfy the two halves. The first version
       asked "does any row reach the rho target" and "does any row reach the
       energy target" separately and answered "reaches both targets at
       ['sum']" -- from two rows 12 dB of drive apart. Wrong-then-right 4.
    2. It does not treat "above the target" as a match. The 808's rho_M(-10) is
       a RANGE, 1.048-1.139 in the comparison window; a reading of 3.02 is not
       "at least as good as" 1.14, it is a different instrument. Both bounds of
       the box are enforced.
    3. It does not average the two. A candidate that is right on one and wrong
       on the other is not half right (#369's rules: no averaged score).
    """
    t = targets_ or {}
    rho_lo, rho_hi = t.get("rho_M_808_range", RHO_M_808_RANGE)
    e_lo, e_hi = t.get("m_re_ln_808_range", M_RE_LN_808_RANGE)
    base = t.get("rho_baseline_max", T_RHO_BASELINE_MAX)
    usable = [r for r in rows if "refused_drive" not in r]
    got = [r for r in usable if r.get("rho_M_-10") is not None]
    out = {"n_rows": len(rows), "n_usable": len(usable), "n_rho_answered": len(got),
           "rho_M_808_range": [rho_lo, rho_hi], "m_re_ln_808_range": [e_lo, e_hi],
           "max_rho_M_-10": max((r["rho_M_-10"] for r in got), default=None),
           "max_M_re_Ln": max((r[f"M_re_{REF}"] for r in usable), default=None),
           "n_above_skirt_baseline": sum(1 for r in got if r["rho_M_-10"] > base),
           "per_position": {}}
    matched = []
    for pos in sorted({r["position"] for r in rows}):
        p = [r for r in usable if r["position"] == pos]
        pg = [r for r in p if r.get("rho_M_-10") is not None]
        inbox = [r for r in pg if rho_lo <= r["rho_M_-10"] <= rho_hi
                 and e_lo <= r[f"M_re_{REF}"] <= e_hi]
        out["per_position"][pos] = {
            "max_rho_M_-10": max((r["rho_M_-10"] for r in pg), default=None),
            "max_M_re_Ln": max((r[f"M_re_{REF}"] for r in p), default=None),
            "rho_in_box_at_db": [r["drive_db"] for r in pg if rho_lo <= r["rho_M_-10"] <= rho_hi],
            "energy_in_box_at_db": [r["drive_db"] for r in p
                                    if e_lo <= r[f"M_re_{REF}"] <= e_hi],
            "both_in_box_at_db": [r["drive_db"] for r in inbox],
        }
        if inbox:
            matched.append(pos)
    out["positions_matching"] = matched
    if not matched:
        # The closest single row, so "no" carries a size rather than only a sign.
        def miss(r):
            dr = max(0.0, rho_lo - r["rho_M_-10"]) + max(0.0, r["rho_M_-10"] - rho_hi)
            de = max(0.0, e_lo - r[f"M_re_{REF}"]) + max(0.0, r[f"M_re_{REF}"] - e_hi)
            return dr / max(rho_hi - rho_lo, 1e-9) + de / max(e_hi - e_lo, 1e-9)
        near = min(got, key=miss) if got else None
        out["closest_row"] = near
        out["refused"] = (
            f"no single (position, drive) in the swept range lands inside BOTH the 808's "
            f"measured rho_M(-10) range [{rho_lo}, {rho_hi}] and its measured M re Ln range "
            f"[{e_lo}, {e_hi}] dB at the same time"
            + (f"; the closest is {near['position']} at {near['drive_db']:+.0f} dB, "
               f"rho_M {near['rho_M_-10']}, M re Ln {near[f'M_re_{REF}']} dB" if near else ""))
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--out", type=pathlib.Path, default=None)
    ap.add_argument("--asym", type=float, default=ASYM)
    ap.add_argument("--asym-sweep", action="store_true",
                    help="repeat the sweep at every ASYM_SWEEP value (the reference does "
                         "not resolve the asymmetry, so its effect is shown, not chosen)")
    ap.add_argument("--source", default="staircase", choices=("staircase", "white"))
    ap.add_argument("--skip-controls", action="store_true",
                    help=argparse.SUPPRESS)      # for the unit tests only
    a = ap.parse_args(argv)

    if not a.skip_controls:
        ok, lines = check()
        print("\n".join(lines))
        if not ok:
            print("\nREFUSED: this instrument's own controls do not pass")
            return 1
        print()
    else:
        ok = None

    res = {"controls_pass": ok, "asym": a.asym, "source": a.source,
           "positions": list(POSITIONS), "drives_db": list(DRIVE_DB)}
    res["targets"] = targets()

    res["leakage_error"] = leakage_error()
    print("the ANALYSIS filter's own error, white source, per band "
          "(rendered minus analytic M re Ln, dB):")
    for band, v in res["leakage_error"].items():
        print(f"  {band:7s} rendered {v['rendered_M_re_Ln']:+7.2f}   analytic "
              f"{v['analytic_M_re_Ln']:+7.2f}   reads {v['leak_M_db']:+.2f} dB "
              f"(Mn {v['leak_Mn_db']:+.2f})")
    print("  -- step 7's bound is analytic and the 808 figures it is compared against are "
          "filtered;\n     for the two bands peaking at 7.1 kHz that is not like for like.\n")

    res["rendered_bound"] = {k: rendered_bound(kind=k) for k in ("white", a.source)} \
        if a.source != "white" else {"white": rendered_bound(kind="white")}
    for kind, rb in res["rendered_bound"].items():
        print(f"the same bound computed BY RENDERING, {kind} source, {rb['n_balances']} balances:")
        for name in ("M", "Mn"):
            print(f"  {name} re {REF}: rendered bound {rb[name]['rendered_bound_db']:+.2f} dB "
                  f"(at decay/short {rb[name]['at_decay_short_db']}), analytic bound "
                  f"{rb[name]['analytic_bound_db']:+.2f} dB")
    print()

    res["source_tilt"] = source_tilt()
    st = res["source_tilt"]
    print("the source step 7 assumed, against the source §1.5 documents "
          "(M re Ln, dB, same chain):")
    for band in ("low", "decay", "short"):
        v = st[band]
        print(f"  {band:7s} white {v['white'][f'M_re_{REF}']:+7.2f}   staircase "
              f"{v['staircase'][f'M_re_{REF}']:+7.2f}   moves {v['tilt_M_db']:+.2f} dB")
    print(f"  the linear chain's BOUND moves from {st['bound_white_db']:+.2f} to "
          f"{st['bound_staircase_db']:+.2f} dB ({st['bound_moves_db']:+.2f} dB) "
          f"when the source is the documented staircase rather than a flat one\n")

    asyms = ASYM_SWEEP if a.asym_sweep else (a.asym,)
    res["sweeps"] = {}
    for asym in asyms:
        rows = sweep(asym=asym, kind=a.source)
        res["sweeps"][f"{asym:g}"] = {"rows": rows, "verdict": verdict(rows, targets_=res["targets"])}
        print(f"asymmetry {asym:g}:")
        print(f"  {'position':9s} {'drive':>7s} {'THD':>7s} {'M re Ln':>8s} {'Mn re Ln':>9s} "
              f"{'rho_M(-10)':>11s} {'rho_Mn(-10)':>12s}")
        for r in rows:
            d = "linear" if r["drive_db"] is None else f"{r['drive_db']:+.0f} dB"
            flag = "  REFUSED-DRIVE" if "refused_drive" in r else ""
            print(f"  {r['position']:9s} {d:>7s} {str(r['thd_db']):>7s} "
                  f"{r[f'M_re_{REF}']:8.2f} {r[f'Mn_re_{REF}']:9.2f} "
                  f"{str(r['rho_M_-10']):>11s} {str(r['rho_Mn_-10']):>12s}{flag}")
        v = res["sweeps"][f"{asym:g}"]["verdict"]
        print(f"\n  REFUSED: {v['refused']}\n" if "refused" in v
              else f"\n  lands inside both 808 boxes at: {v['positions_matching']}\n")

    res["commit"] = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT,
                                   capture_output=True, text=True).stdout.strip()
    res["sources_dirty"] = subprocess.run(["git", "diff", "--quiet", "HEAD", "--", "model", "tools"],
                                          cwd=ROOT).returncode != 0
    if a.out:
        a.out.parent.mkdir(parents=True, exist_ok=True)
        a.out.write_text(json.dumps(res, indent=1, default=float) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
