"""#369 candidate: the TR-808 cymbal's three-band structure from reference §10.

MODEL ONLY. Nothing here changes `kit_808()`; it builds a register image for a
bank with 19 modes and 24 paths (the operator accepted the 17th-mode cliff,
2026-09-27) and renders through the same `DrumsFx` datapath.

The structural choices, fixed before any render (discrete, checked against the
circuit -- no continuous fitting):

  low band    3.45 kHz Q 6 band-pass (unchanged) -> swing VCA x E_CYL (unchanged)
              -> Hh1: 2-pole HIGH-pass 2.5 kHz Q 0.97 [reference 10, inferred from
              W14b eq. 16 + values]. Was: straight to the mix bus (Hh1 omitted).
  high DECAY  7.1 kHz band-pass (shared with the hats) -> swing VCA x E_CYD
              -> Hh2: 2-pole HIGH-pass, 8839 Hz Q 1.00 [W14b Fig. 4, read by
              tools/werner_fig4.py at 0.007 dB rms]. REVISION 2: the first
              candidate carried the kit's 10.5 kHz Q 2.5 here because the
              reference left the corner unresolved. Fig. 4 resolves it, and it
              is wrong on both axes.
  high short  7.1 kHz band-pass -> swing VCA x E_CYS -> Hh3: THIRD order, a
              2-pole high-pass 10323 Hz Q 5.64 cascaded with a 1-pole
              high-pass at 5195 Hz [same source, 0.008 dB rms; a 2-pole model
              of that curve leaves 0.717 dB]. REVISION 2: #102 and the first
              candidate put the third pole at the SAME corner as the 2-pole.
              It sits at half of it, and the 2-pole's Q is 5.64, not 2.5.
              Was, before either candidate: the CLOSED HAT's 11.7 kHz 2-pole.
  level stage the rising slope of the LEVEL buffer [reference 10, W14b §11],
              applied to all three bands at their last stage as one more
              (1 - z^-1) in the numerator: Hh1 and Hh2 use HP3 = (1-z^-1)^3,
              Hh3's 1-pole stage uses HP = (1-z^-1)^2 (its own zero plus the
              tilt). Unchanged in revision 2, and now with a number behind it:
              W14b Fig. 10's family is a single-pole differentiator with its
              corner at 18972 Hz (0.02 dB rms), which tilts +16.6 dB across
              2-20 kHz. A discrete (1 - z^-1) at 48 kHz tilts +17.4 dB over
              the same span, so the digital stand-in is 0.8 dB steep, not the
              "pure differentiator up to Nyquist" the first candidate's
              post-mortem suspected.

Mode layout (numerator modes must sit below N_NUMS = 11, tapped modes below 16):
  0 HATBP 1 OHHP 2 CHHP 3 SDN 4 CPBP 5 CBBP 6 CYBP 7 CYHI(Hh2)
  8 CYH1(Hh1)  9 CYH3(Hh3 2-pole, tapped)  10 CYH3B(Hh3 1-pole)
  11 LT 12 MT 13 HT 14 RS1 15 RS2 (unchanged)   16 BD 17 SDLO 18 SDHI (moved)
N_NUMS stays 11. Paths: 24 (one new: tap(CYH3) -> CYH3B, full-scale linear).

LEVELS are the kit's, not the circuit's (as `kit_808`'s own are): each band's
output is matched to the SHIPPED kit's same band, in the 1/3 octave at the
band's centre (3.15 kHz for the low band, 10 kHz for the two high bands), over
the first second of a CY strike. The rule is fixed here; it references the
shipped kit, never a recording.

UNCHANGED IN REVISION 2, deliberately. Figure 4 also gives the circuit's own
inter-band gains -- band-pass peaks +22.95 and +24.10 dB, high-pass pass bands
0, +6.03 and +8.86 dB, so the filter chain alone puts the decay band +7.2 dB
and the short band +10.0 dB above the low band. Adopting those is a SECOND
question, and changing the levels in the same step as the filter shapes would
make neither answerable. They are recorded here (HH2_PASS_DB, HH3_PASS_DB,
BP_PEAK_DB) and not applied.

REVISION 3 APPLIES THE TONE STAGE'S MEASURED TILT (#396). Revision 2 had no
tone stage at all, and `docs/scorecard/cymbal-369/tone-stage/` showed that is
why it overcorrects: W14b Figure 9 puts every band's path to the output at
about -20 dB of tilt from 1 kHz to 20 kHz, and revision 2 applied the LEVEL
stage's +16.6 dB with nothing against it. Revision 2's residual at CY5025
climbs +24.0 dB across the same span.

What revision 3 changes, fixed here before any render, and why each is a
DISCRETE structural choice rather than a fitted one (see
`tools/cymbal_tone_realisation.py`, which computes every number below from
the two digitised artifacts and REFUSES outside its bound):

  * Each band's tone path is, from Figure 9, one RC high-pass (pole at 127.7 /
    609.9 / 406.0 Hz) cascaded with one RC low-pass (pole at 589.5 / 1549.1 /
    1511.2 Hz), and the LEVEL stage is a single-pole differentiator cornered at
    18972 Hz (Figure 10). BOTH are now in the model, and the arithmetic of
    putting them in is what removes structure rather than adding it:

  * LOW and DECAY bands: the tone low-pass pole and the LEVEL differentiator
    CANCEL over the cymbal's band -- both are in their asymptotic regions there
    (-6 and +6 dB/octave), and the tone high-pass pole sits two decades below
    it. Over each band's own active range the exact analog cascade is flat to
    within 0.45 dB (low) and 1.41 dB (decay). So Hh1's and Hh2's numerators go
    back from HP3 = (1 - z^-1)^3 to HP = (1 - z^-1)^2: the differentiator's
    zero is REMOVED and no tone section is added. This is not "no tilt" -- it
    is the measured net of two stages, with its deviation stated per 1/3
    octave in docs/scorecard/cymbal-369/candidate3/.
  * SHORT band: exactly, and for free. Hh3's 1-pole stage (M_CYH3B) has an
    unused second pole slot, so its a2 now carries the tone low-pass pole at
    1511.2 Hz while its numerator keeps HP = (its own zero)(the LEVEL zero).
    Shape error over its active range 1.35 dB, against revision 2's 4.87 dB.
  * The BANK IS UNCHANGED from revision 2 in size: 19 modes, 24 paths,
    N_NUMS 11. No mode of revision 3 carries numerator code 3, so the shared
    `modal_fixed` decode does NOT need HP3 and neither does modal_dp.v -- the
    RTL change revisions 1 and 2 would have required is no longer required.
  * REFUSED, not merely deferred: the inter-band balance. TONE_K1's `peak_db`
    and the filter chain's own HH2_PASS_DB / HH3_PASS_DB / BP_PEAK_DB stay
    recorded and unused; the levels stay on the shipped-kit 1/3-octave rule.
    Revision 3 left this as "one question at a time"; #396 then asked the
    question and `tools/cymbal_band_balance.py` answered it with a measurement
    (`docs/scorecard/cymbal-369/balance/`):

      - The blocking uncertainty is NOT the 9-18 dB of Figure 9 window that
        revision 3's comment below blames. Evaluated where each band's level is
        actually SET rather than at a shared 7.1 kHz, that window reads 7.4 dB
        on the low band and 0.04 dB on the short band (measured, not
        extrapolated) -- and it is no longer the quantity in play at all:
        #390/#417 SOLVED the tone network from SN p.13 by nodal analysis
        (tools/tone_stage_schematic.py), so #420 re-derived the tone term as a
        circuit value bounded by that solution's own residual,
        TONE_BOUND_AT_CENTRE_DB = 0.008 / 0.008 / 0.067 dB.
      - What blocks it is a factor neither figure carries: the three swing
        VCAs' drive levels (Q16/Q17/Q18). Against this rule, the
        filters-plus-tone balance alone demands +10.1 dB on the decay band and
        +39.8 dB on the short band -- and resolving VR4 made those gaps BIGGER
        by 0.3 and 1.6 dB, which is the opposite of what a tone-term
        explanation of them would have predicted.
      - Applying every resolved factor with the VCA drives held equal is
        rendered as `cymbal_candidate_eval.py --variant balance` and puts the
        band split 16.9 dB from the 808 CY5025 (H-L 25.07 against 8.16) where
        this rule is 3.9 dB from it, and takes the strike window from 0 of 14
        thirds outside +-6 dB to 8 of 14. VR4's network IS now digitised and
        solved, so the balance is not applicable until the VCA drives are too.
  * NOT APPLIED: the TONE knob law. Figure 9 identifies only k = 1.0, and the
    knob-law render is separately not the instrument
    (`docs/scorecard/cymbal-369/README.md` §4, and §6 of `balance/README.md`
    for why that blocker needed filing as an issue at all).

Prediction, stated before the render (docs/scorecard/cymbal-369/candidate3/):
removing the differentiator's zero tilts the low band's response, relative to
revision 2 and to its own level at its 3.175 kHz calibration centre, by
+10.0 dB at 1 kHz and -13.4 dB at 20 kHz (decay band, at its 10 kHz centre:
+19.4 and -4.0 dB), so revision 2's +24.0 dB residual climb should mostly close. The cymbal's VCAs
clip and the bands are re-levelled, so this is arithmetic on transfer functions
and has to be confirmed by the render, not assumed.

===========================================================================
REVISION 4 (#369 step 10) ADDS THE TONE KNOB, AND REPAIRS THE LOW BAND'S
TONE REALISATION, WHICH REVISION 3 GOT WRONG.

Revision 4 is ADDITIVE: `candidate_kit(tone=None)` still builds revision 3
exactly, register for register, so every artifact of steps 5-9 stays
reproducible. Revision 4 is what `candidate_kit(tone="<code>")` builds.

WHY THE LOW BAND CHANGES. Revision 3 realised (tone stage x LEVEL stage) per
band against W14b Figure 9's per-band 2-pole fit, inside a 3.0 dB bound stated
in advance, and read 0.45 / 1.41 / 1.35 dB. Figure 9 plots Ht1 only over
121-564 Hz, so its low-band low-pass pole -- 589.5 Hz -- is an extrapolation
out of a window a decade below where the low band lives. #390/#417 replaced
that extrapolation with a NODAL solution of the actual network off SN p.13
(`tools/tone_stage_schematic.py`), and the network's dominant in-band pole is
at 4219 Hz, not 589.5 Hz. Measured against the nodal target
(`tools/cymbal_tone_nodal.py`):

  band    rev 3 vs Figure 9   rev 3 vs NODAL         revision 4
  low          0.45 dB          4.06-4.72 dB  <-- OUT   0.56-0.88 dB
  decay        1.41 dB          1.55-1.77 dB           unchanged (empty)
  short        1.35 dB          1.31-1.34 dB            1.75-1.79 dB

The nodal route is not the suspect: the SHORT band is the one path Figure 9
draws across the whole audio band (20 Hz-20 kHz), and the two routes agree
there to 0.026 dB over its active range. The low band's disagreement is 4.61 dB
over the same construction (5.59 dB peak-to-peak). That asymmetry is exactly
what a narrow window predicts, and it is asserted as two named properties
rather than argued -- `nodal-grounded` and `fig9-window-blind`, both re-derived
from the tool by `tools/test_cymbal_tone_writeup_figures.py` so that this
paragraph cannot drift away from them again (#429).

WHAT REVISION 4 CHANGES, each a discrete choice checked against the circuit:

  * LOW BAND gains ONE new bank section, M_CYH1B (mode 19), holding ONE REAL
    POLE at 4219 Hz -- the network's own top pole -- with numerator RAW. Its
    shape error falls 4.72 -> 0.88 dB. The pole is taken from
    `tone_stage_schematic.poles_hz`, not fitted: nothing else the bank can hold
    clears the bound.
  * SHORT BAND's tone pole moves 1511.2 -> 4219.0 Hz. 1511.2 Hz is NOT a pole
    of the network (its nearest is 1625.4 Hz); it is an artifact of the same
    2-pole window fit. Following the rule costs 0.45 dB of shape error here
    (1.34 -> 1.79) and is taken anyway, because a value that reads better and
    is not in the circuit is the trap #102 records for Q.
  * DECAY BAND is UNCHANGED. Its active range is 5-16 kHz, entirely above
    4219 Hz, so there the tone pole and the LEVEL differentiator really are in
    their asymptotic regions and really do cancel -- revision 3's argument,
    which holds for this band and not for the low band, whose 2-8 kHz range
    straddles the pole. 1.77 dB, inside the bound, at no cost.
  * THE TONE KNOB itself: alpha = TONE/100 off VR4's "20K(B)" linear-taper
    marking (step 9, `tools/cymbal_tone_knob.alpha_of`), and the knob acts as
    a PER-BAND LEVEL taken from the nodal network at each band's own
    calibration centre, relative to the anchor TONE 50:

        TONE    low    decay    short
           0  +1.02    +0.99   -48.41
          25  +0.69    +0.63    -3.67
          50   0.00     0.00     0.00
          75  -1.54    -1.36    +1.68
         100  -7.26    -6.46    +2.65

    The knob's SHAPE change with alpha is second order and is not realised:
    one fixed register set covers all five positions, which the
    `one-register-set` property measures at 0.171 dB of forgone accuracy
    against letting each pole track alpha -- 68 % of its own 0.25 dB bound,
    carried entirely by the low band (0.878 dB fixed against 0.707 dB
    tracking), with the other two bands gaining nothing. The network's top
    pole moves 4132.2 -> 4712.0 Hz across the five TONE codes (the ideal full
    rotation alpha 0 -> 1, which no code selects, gives 4132.1 -> 4715.1 Hz).

BUDGET, counted exactly. 19 -> 20 modes, 24 -> 25 paths, N_NUMS 11. No mode
carries numerator code 3, so `modal_dp.v` still needs no HP3 decode. AND THE
MARGIN IS NOW ZERO: mode 19 is the LAST mode the 8-bit register map (contract
15.1) can address, because `A_RESET` = 0xFF is decoded before the mode range
and mode 19's `num` register IS 0xFF. That is harmless only because mode 19
sits at or above N_NUMS = 11, so `ModalFx.step` never reads its numerator --
and it is why revision 4's low-band section must have numerator RAW rather
than a zero. A 21st mode has no address at all. The operator's +31 % drum-area
allowance (padding the bank to 32) covers the area; it does not cover the map.

PREDICTION, STATED BEFORE THE RENDER (docs/scorecard/cymbal-369/tone-render/).
The knob's per-band levels above bound what the render can do to H - L
(6-14 kHz minus 2-5 kHz), anchored at TONE 50, without knowing the inter-band
balance: L follows the low band, and H lies between the decay band's shift and
the short band's, so

    TONE     0        25        50       75       100
    Delta  [-49.4,   [-4.4,    0.00    [+0.2,   [+0.8,
           -0.03]    -0.06]            +3.2]    +9.9]

and the 808's own anchored H - L is -2.3 / -1.4 / 0 / +1.5 / +5.1 dB, the same
to within 0.601 dB in all five of its DECAY columns (worst case DECAY 50
against DECAY 75 at TONE 100). So:

  1. the rendered anchored H - L must RISE monotonically with TONE;
  2. it must land inside the bracket above -- and where inside is set by the
     inter-band balance, which is #396's open half, so a miss at TONE 0 or
     TONE 100 is a balance result and a miss in the MIDDLE (TONE 25, 75, where
     the bracket is 4.3 and 3.0 dB wide) is a TONE-law result;
  3. H's own EDT10 must FALL with TONE -- the short band, whose envelope is
     the fast one, takes over the high band as TONE opens. The 808's ratio to
     TONE 50 is 1.09-1.38 at TONE 0 and 0.58-0.76 at TONE 100. This is a
     DECAY, so no energy balance can produce it;
  4. Ln's EDT10 must stay TONE-invariant: the low band's envelope does not
     move with TONE in this model, and the machine's does not either (+-3 %
     across all five columns).

Point 3 is the one worth the render. It is the only one of the four that no
choice of inter-band balance can manufacture.

===========================================================================
REVISION 5 (#411) CHANGES ONE NUMBER: Hh1's OWN gain register. Everything
else in revision 3 -- Hh1's filter shape (2.5 kHz Q 0.97, HP numerator), the
decay and short bands, the bandpass, envelope and path registers -- is
byte-for-byte unchanged. (Revision 4 above, #369 step 10, is orthogonal: it
is additive and OFF by default -- `candidate_kit(tone=None)` still builds
revision 3 exactly -- so it does not touch what this revision changes.)

`docs/scorecard/cymbal-369/mid-band/README.md` (#369 step 7, `tools/cymbal_mid.py`)
qualified the 1-2.5 kHz decay band and found the defect is a LEVEL, not a rate:
revision 3's mid band is 4.4 dB under the 808's independent content there
(+0.04 dB over the band-pass-only skirt prediction against the 808's +4.41 dB),
while the shipped kit (which omits Hh1 entirely) is 6.1 dB over it. Revision 3's
decay rate is inside tolerance in both cases, so a level-only change is the
thing to test, not another filter -- and the level rule that sets it is the
prime suspect: each band's amp is matched to the SHIPPED kit's same band, in
the 1/3-octave at the band's own centre (3.175 kHz for the low band), over the
first second of a CY strike (`tools/cymbal_candidate_eval.calibrate`). For Hh1
this is a strange thing to do: Hh1 is a stage the shipped kit never had (its
low band goes straight to the mix bus, `dx.kit_808`'s P_CYL path), so "matching
the shipped kit" for Hh1's own gain means deriving a NEW register from a
single-frequency energy ratio, when the circuit states Hh1's gain directly --
docs/tr808-reference.md Sec.10's table and `HH1_PASS_DB = 0.0` above both read
Hh1 as unity-gain -- and the path feeding it, `P_CYL` (att = CY_ATT = 0), is
otherwise IDENTICAL to the shipped kit's own low-band path: same source
(M_CYBP), same envelope (E_CYL), same attenuation register, only `dest` moves
from DEST_MIX to M_CYH1 to route through Hh1 first. So the low band's absolute
scale is already inherited, unmodified, from the shipped kit through that
unchanged path; the ONE thing revision 3 then does is compute a SECOND, new
number for Hh1's own amp by matching 3.175 kHz post-Hh1 to the shipped kit
AGAIN, on top of a chain that already matches it. Revision 4 removes that
second match for Hh1 only and gives Hh1's own amp register its stated circuit
value instead: AMP_MAX (0.99998, the closest representable value to the
Q0.16 register's unity), the same ceiling HH2's and HH3's own amp registers
already sit at in revision 3.

THE PREDICTION, before the render. Revision 3's calibration measured Hh1's amp
at 0.693828 (`docs/scorecard/cymbal-369/candidate3/candidate3.json`, `levels
-> per_band -> low -> amp`); AMP_MAX / 0.693828 = 1.4413 = +3.17 dB. Because
`amp` is a flat linear multiplier applied identically to Hh1's ENTIRE output
spectrum (the same per-mode register revision 3 already computes, only its
VALUE changes -- no filter, no numerator, no coefficient moves), revision 4's
whole low-band output should be +3.17 dB louder than revision 3's at every
frequency and every time window, with no change in shape or decay rate. Two
consequences follow, and both have to be confirmed by the render, not assumed:

  * The mid band's over-skirt residual (revision 3: +0.04 dB) should move
    toward the 808's +4.41 dB by close to +3.17 dB, and possibly by more: the
    leak prediction it is measured against comes from the L band's OWN energy
    in the same render, and L mixes the low band with the (unchanged) decay
    and short bands, so a uniform +3.17 dB on the low band alone should raise
    L's measured energy by LESS than +3.17 dB, and the residual (M minus a
    leak prediction scaled from L) should therefore open by MORE than +3.17 dB
    of headroom against that prediction.
  * Revision 3's own strike-window thirds already sit within 0.5 dB of the
    3.0 dB board bound this chain reports against (candidate3/README.md Sec.4:
    +5.5 dB at 2.5 kHz, worst 5.7 dB at 16 kHz, against a 6 dB no-third-outside
    bound) -- a flat +3.17 dB there would put 2.5 kHz at +8.7 dB, over the
    bound. So revision 5 is very likely to trade back some or all of the one
    property no earlier revision in this chain has had (no strike-window third
    outside +-6 dB of the 808), which is exactly what
    docs/scorecard/cymbal-369/README.md's acceptance for this step requires be
    measured and reported, not assumed away because the mid-band tail improved.

RESULT, MEASURED (docs/scorecard/cymbal-369/candidate4/README.md): the second
consequence held -- the strike window gains one violation (2.5 kHz, +8.3 dB,
against the predicted +8.7). The first did NOT: the over-skirt residual moved
to -0.55 dB, AWAY from the 808's +4.41 dB rather than toward it. Both M's and
L's measured energy in the render rose together (L is (almost) entirely the
same post-Hh1 signal M is a sub-band of, and `amp` is a flat scalar on that
whole signal applied AFTER its filtering), so the ratio the over-skirt metric
reads is close to invariant to Hh1's own amp by construction -- not a render
defect: `tools/probes/cymbal_candidate4_gain_invariance.py` finds zero
saturation events in either render (0 of 25,152,000 `modal_fixed.sat` calls),
ruling out clipping as the cause. REFUSED: not promoted. `--variant
candidate4` in `tools/cymbal_candidate_eval.py` stays a diagnostic ablation.
"""
from __future__ import annotations

import contextlib
import math

import numpy as np

import drums_fx as dx
import modal_fixed as mf
from modal_fixed import BP, HP

# The candidate's one new numerator: (1 - z^-1)^3 = a 2-pole high-pass's
# numerator times the level stage's differentiator. It lives HERE, not in the
# shared `modal_fixed` decode: code 3 reads as RAW in modal_dp.v, and the
# shared model must not change without the matching RTL and equivalence
# evidence (#371 review). The RTL spec is handed over only once the candidate
# is confirmed.
HP3 = 3


class ModalFxHP3(mf.ModalFx):
    """ModalFx with numerator code 3 = (1 - z^-1)^3 (candidate only)."""

    def reset(self):
        super().reset()
        self.h3 = [0] * self.M

    def step(self, exc, coefs, num=None) -> int:
        CF, SB, SQ = self.CF, self.SB, self.SQ
        osh = SQ - 15 + self.HR
        y1, y2, h1, h2, h3 = self.y1, self.y2, self.h1, self.h2, self.h3
        mix = 0
        for m in range(self.M):
            a1, a2, amp = coefs[m]
            e = mf.shl(int(exc[m]), SQ - 15)
            if m < self.NUMS and num is not None:
                k = int(num[m])
                if k == mf.BP:
                    x = e - h2[m]
                elif k == mf.HP:
                    x = e - 2 * h1[m] + h2[m]
                elif k == HP3:
                    x = e - 3 * h1[m] + 3 * h2[m] - h3[m]
                else:
                    x = e
                h3[m], h2[m], h1[m] = h2[m], h1[m], e
            else:
                x = e
            acc = a1 * y1[m] + a2 * y2[m] + self.RND
            y = mf.sat((acc >> CF) + x, SB)
            y2[m], y1[m] = y1[m], y
            mix += (y * amp) >> 16
        return mf.sat(mix >> osh, self.OB)


def drums(modes, paths, nums):
    """A DrumsFx whose bank decodes HP3."""
    d = dx.DrumsFx(modes=modes, paths=paths, nums=nums)
    b = d.bank
    d.bank = ModalFxHP3(coef_frac=b.CF, state_bits=b.SB, state_q=b.SQ, headroom=b.HR, modes=b.M,
                        rounding=bool(b.RND), out_bits=b.OB, nums=b.NUMS, exc_bits=b.EW)
    d.reset()
    return d

M_CYH1, M_CYH3, M_CYH3B = 8, 9, 10
NEW_M = {"BD": 16, "SDLO": 17, "SDHI": 18}
OLD_TO_NEW = {dx.M_BD: 16, dx.M_SDLO: 17, dx.M_SDHI: 18}     # every other mode keeps its index
N_MODES, N_PATH, N_NUMS = 19, 24, 11

# ---- revision 4 (step 10): the low band's own tone section -----------------
# Mode 19 is the LAST mode contract 15.1 can address: A_RESET (0xFF) is decoded
# before the mode range and mode 19's `num` register IS 0xFF, so its numerator
# can never be written. That is harmless here and ONLY here, because 19 >=
# N_NUMS and `ModalFx.step` reads `num` only below N_NUMS -- which is also why
# this section's numerator must be RAW. Asserted by
# tools/test_cymbal_tone_nodal.py, not assumed.
M_CYH1B = 19
P_CYH1B = 24
N_MODES_R4, N_PATH_R4 = 20, 25
# Every value below is from W14b Figures 4 and 10, read by tools/werner_fig4.py
# and committed as docs/scorecard/cymbal-369/werner-fig4.json. Hh1's own
# 2500 Hz / Q 0.97 comes from SN p.13 component values and is what validates
# the reading: the digitiser recovers it as 2497.5 Hz / Q 0.97.
HH1_HZ, HH1_Q = 2500.0, 0.97
HH2_HZ, HH2_Q = 8839.0, 1.00
HH3_HZ, HH3_Q = 10323.0, 5.64
HH3_P1_HZ = 5195.0                       # Hh3's third pole, NOT at its corner
HH1_PASS_DB, HH2_PASS_DB, HH3_PASS_DB = 0.0, 6.03, 8.86     # recorded, not applied
BP_PEAK_DB = {"low": 22.95, "high": 24.10}                  # recorded, not applied

# The TONE stage at k = 1.0, from W14b Figure 9 via tools/werner_fig9.py
# (docs/scorecard/cymbal-369/tone-stage/). Each band's path to the output is a
# 2-pole band-pass whose poles are both REAL, so each is one RC high-pass
# cascaded with one RC low-pass.
#
# Read the two columns separately, because the evidence for them is not the
# same strength:
#   * (f0, q) is a SHAPE and is what costs each band about -20 dB from 1 kHz to
#     20 kHz. That tilt is the measured headline: it nearly cancels the LEVEL
#     stage's +16.6 dB, and revision 2 applied that +16.6 alone. REVISION 3
#     APPLIES THIS COLUMN, through `poles_hz` -- see the docstring, and
#     tools/cymbal_tone_realisation.py for the realisation and its error.
#   * `peak_db` is FIGURE 9's reading of the inter-band balance, and applying
#     these three peak levels as if they were circuit values is the mistake
#     this comment exists to prevent. Figure 9 plots Ht1 on a 4 dB axis and Ht2
#     on a 3 dB axis, so neither is plotted in the cymbal's band; their 7.1 kHz
#     values carry an 18 dB and a 9 dB bound, and at each band's OWN
#     calibration centre FIGURE_TONE_BOUND_AT_CENTRE_DB below, not those.
#     The BALANCE itself is now resolved on this half: #390/#417 solved the
#     network from SN p.13 and #420 re-derived the tone term from it, so the
#     applicable bound is TONE_BOUND_AT_CENTRE_DB (0.008-0.067 dB) and these
#     `peak_db` values are kept as the excluded route's own numbers. STILL NOT
#     APPLIED, because the unmeasured VCA drives -- not the tone term -- are
#     what block the balance: see the docstring's REFUSED bullet and
#     tools/cymbal_band_balance.py.
TONE_K1 = {
    "low":   {"f0": 274.4, "q": 0.383, "peak_db": -26.44,
              "poles_hz": (127.7, 589.5), "plotted_hz": (121.0, 563.8)},
    "decay": {"f0": 972.0, "q": 0.450, "peak_db": -15.12,
              "poles_hz": (609.9, 1549.0), "plotted_hz": (561.6, 1640.1)},
    "short": {"f0": 783.3, "q": 0.409, "peak_db": -22.09,
              "poles_hz": (406.0, 1511.2), "plotted_hz": (20.0, 19905.4)},
}
# Ht3 is the one path plotted across the whole axis; this is its own measured
# tilt over 2-20 kHz, against the LEVEL stage's +16.6 dB from Figure 10.
TONE_TILT_2K_20K_DB = -17.7
# How wide the tone term's uncertainty is AT THE FREQUENCY EACH BAND'S LEVEL IS
# SET (tools/cymbal_candidate_eval.CENTRE: 3175 / 10079 / 10079 Hz), from
# tools/cymbal_band_balance.py. #420: these are no longer Figure 9's
# extrapolation spread. #390/#417 SOLVED this network from SN p.13
# (tools/tone_stage_schematic.py -> docs/scorecard/cymbal-369/sn-p13-vr4.json),
# so the width here is that solution's own residual against Figure 9's
# digitised curves plus 3 sigma on its one fitted parameter -- 0.008 / 0.008 /
# 0.067 dB, where the figure route read 7.36 / 13.90 / 0.04. Bound to the tool
# by tools/test_cymbal_band_balance.py so it cannot outlive its evidence.
TONE_BOUND_AT_CENTRE_DB = {"low": 0.0077, "decay": 0.0085, "short": 0.0673}
# What the figure route read at the same three frequencies, kept because #396
# excludes that route and an exclusion has to stay checkable: these are the
# numbers the "9-18 dB" argument was actually made of.
FIGURE_TONE_BOUND_AT_CENTRE_DB = {"low": 7.36, "decay": 13.90, "short": 0.04}
# The inter-band balance's verdict (#396), in one place, with its number: the
# gap between the circuit's filters-plus-tone balance and the shipped-kit level
# rule this module uses, per band, in dB relative to the low band. It is what
# the ONE still-absent artifact (the VCA drives) has to account for. #420
# re-derived it from the resolved tone term and it grew -- 9.85 -> 10.13 and
# 38.23 -> 39.79 -- so resolving VR4 moved this away from the tone bound, not
# towards it. It is three orders of magnitude larger than
# TONE_BOUND_AT_CENTRE_DB and still ~1.9x the widest FIGURE bound (21.3 dB),
# which is why the balance is REFUSED rather than applied.
BALANCE_GAP_DB = {"low": 0.0, "decay": 10.13, "short": 39.79}
# The LEVEL buffer's differentiator corner, W14b Figure 10 via
# tools/werner_fig4.py (docs/scorecard/cymbal-369/werner-fig4.json,
# level_stage.one_pole_corner_hz). Revision 3 needs it as a NUMBER, not as a
# slope, because the cancellation below is between two real stages.
LEVEL_CORNER_HZ = 18972.0
# Revision 3's realisation of (tone stage x LEVEL stage), band by band. The
# first two are the empty cascade -- the two stages cancel over the band and
# BOTH are dropped, which is why Hh1's and Hh2's numerators lose a zero. The
# third is exact and costs nothing. `shape_err_db` is the deviation from the
# measured analog cascade over the band's own active range (within 20 dB of the
# band's peak), from tools/cymbal_tone_realisation.py; revision 2's figures are
# 7.32 / 6.11 / 4.87 dB against the same bound of 3.0 dB.
TONE_REALISATION = {
    "low":   {"extra_pole_hz": None,   "num": HP, "shape_err_db": 0.45},
    "decay": {"extra_pole_hz": None,   "num": HP, "shape_err_db": 1.41},
    "short": {"extra_pole_hz": 1511.2, "num": HP, "shape_err_db": 1.35},
}
# REVISION 4's realisation, from the NODAL network instead of Figure 9's window
# fit (tools/cymbal_tone_nodal.py -- which derives these and REFUSES outside the
# same 3.0 dB bound; the numbers here are the record, the tool is the authority,
# and test_cymbal_candidate_r4 binds them together so they cannot drift).
# `section` says where the pole goes: "new" = a new mode and path (the low band,
# whose Hh1 pole pair fills M_CYH1), "cyh3b" = M_CYH3B's free second slot
# (free), None = no section at all (the two stages really do cancel there).
TONE_R4 = {
    "low":   {"pole_hz": 4219.0, "section": "new",   "num": mf.RAW, "shape_err_db": 0.88},
    "decay": {"pole_hz": None,   "section": None,    "num": HP,     "shape_err_db": 1.77},
    "short": {"pole_hz": 4219.0, "section": "cyh3b", "num": HP,     "shape_err_db": 1.79},
}
# The knob's five positions and the per-band level the nodal network puts on each,
# in dB relative to the anchor TONE 50, at each band's own calibration centre
# (3175 / 10079 / 10079 Hz). Derived by tools/cymbal_tone_nodal.tone_gain_db();
# recorded here so the model states what it applies, bound to the tool by test.
TONE_ANCHOR = "50"
TONE_GAIN_DB = {
    "00": {"low": 1.017, "decay": 0.988, "short": -48.411},
    "25": {"low": 0.692, "decay": 0.632, "short": -3.670},
    "50": {"low": 0.000, "decay": 0.000, "short": 0.000},
    "75": {"low": -1.544, "decay": -1.358, "short": 1.676},
    "10": {"low": -7.261, "decay": -6.460, "short": 2.652},
}
P_CYS, P_CYD, P_CYL = 20, 21, 22                             # indices in kit_808's path list
P_CYH3 = 23


COEF_MASK = (1 << 26) - 1
COEF_LO, COEF_HI = -(2 << 24), (2 << 24) - 1


def real_pole_regs(hz):
    """(a1, a2) in Q2.24 for a cascade of one or two REAL poles, in Hz.

    y = ((a1 y1 + a2 y2) >> 24) + x realises 1 / (1 - a1 z^-1 - a2 z^-2), so a
    pole pair (p1, p2) is a1 = p1 + p2, a2 = -p1 p2 with p = exp(-2 pi f / SR).
    `pole_regs` cannot be used: it maps (f0, Q) to a COMPLEX pair, and every
    curve in W14b Figure 9 reads Q < 0.5. Raises rather than clipping -- a
    silently clamped coefficient is a different filter.
    """
    p = [math.exp(-2 * math.pi * float(f) / dx.SR) for f in hz]
    if len(p) == 1:
        a1, a2 = p[0], 0.0
    elif len(p) == 2:
        a1, a2 = p[0] + p[1], -p[0] * p[1]
    else:
        raise ValueError(f"a mode has two poles, got {len(p)}")
    r1, r2 = int(round(a1 * (1 << 24))), int(round(a2 * (1 << 24)))
    for r in (r1, r2):
        if not COEF_LO <= r <= COEF_HI:
            raise ValueError(f"coefficient {r / (1 << 24):.6f} outside the Q2.24 register")
    return r1, r2


def _remap_mode(m):
    return OLD_TO_NEW.get(m, m)


def _remap_path(word):
    src, e1 = word & 31, (word >> 5) & 31
    e2, nl = (word >> 10) & 31, (word >> 15) & 3
    att, dest = (word >> 17) & 7, (word >> 20) & 31
    if src >= dx.SRC_TAP:
        src = dx.SRC_TAP + _remap_mode(src - dx.SRC_TAP)
    if dest != dx.DEST_MIX:
        dest = _remap_mode(dest)
    return dx.path_word(src, e1, e2, nl=nl, att=att, dest=dest)


def remap_kit(kit):
    """The shipped image moved onto the 19-mode layout, bit for bit otherwise."""
    out = {}
    for a, v in kit:
        if dx.A_MODE <= a < dx.A_MODE + dx.N_MODES * dx.MODE_STRIDE:
            m, f = divmod(a - dx.A_MODE, dx.MODE_STRIDE)
            out[dx.A_MODE + _remap_mode(m) * dx.MODE_STRIDE + f] = v
        elif dx.A_PATH <= a < dx.A_PATH + dx.N_PATH:
            out[a] = _remap_path(v)
        else:
            out[a] = v
    return out


@contextlib.contextmanager
def layout():
    """dx's module constants on the new layout, so hit_writes' coefficient
    sequences (BD attack, tom pitch drop) address the moved modes."""
    saved = {k: getattr(dx, k) for k in ("M_BD", "M_SDLO", "M_SDHI")}
    try:
        dx.M_BD, dx.M_SDLO, dx.M_SDHI = NEW_M["BD"], NEW_M["SDLO"], NEW_M["SDHI"]
        yield
    finally:
        for k, v in saved.items():
            setattr(dx, k, v)


def tone_gains(tone: str) -> dict:
    """{band: LINEAR gain} the TONE knob puts on each band's level at `tone`,
    relative to the anchor. The caller applies it, because only the caller knows
    how much headroom the amp register has left and whether the remainder fits
    on the envelope peak -- and it must REFUSE rather than clip.

    Revision 4 realises the knob entirely as these three levels; its shape
    change with the wiper is bounded by `tools/cymbal_tone_nodal.py`'s
    `chosen-in-bound` property and deliberately not realised.
    """
    if tone not in TONE_GAIN_DB:
        raise ValueError(f"TONE code {tone!r} is not one of {sorted(TONE_GAIN_DB)}")
    return {b: 10.0 ** (v / 20.0) for b, v in TONE_GAIN_DB[tone].items()}


def candidate_kit(amps: dict | None = None, kit=None, tone: str | None = None):
    """The §10 structure on the remapped image. `amps` (mode -> level, and
    'E_CYS' -> envelope peak) are the band levels; None uses unity placeholders
    for calibration renders.

    `tone=None` builds REVISION 3, register for register -- every artifact of
    steps 5-9 stays reproducible. `tone="00".."75"` builds REVISION 4: the low
    band's own tone section on mode 19, and the short band's tone pole moved to
    the network's own 4219 Hz. The TONE code itself selects no coefficients
    (one register set covers the whole knob); it is here so that asking for a
    revision-4 image and asking for a TONE position cannot come apart.
    """
    img = remap_kit(kit if kit is not None else dx.kit_808())
    amps = amps or {}
    r4 = tone is not None
    if r4:
        tone_gains(tone)                     # validate the code before anything is written
    # In revision 4 the low band's output leaves the chain at M_CYH1B, so
    # M_CYH1 becomes an intermediate stage and contributes nothing to the mix --
    # the same arrangement Hh3 already uses for M_CYH3 -> M_CYH3B.
    low_amp = 0.0 if r4 else amps.get(M_CYH1, 1.0)
    for m, f0, q, num, amp in (
            (M_CYH1, HH1_HZ, HH1_Q, TONE_REALISATION["low"]["num"], low_amp),
            (dx.M_CYHI, HH2_HZ, HH2_Q, TONE_REALISATION["decay"]["num"], amps.get(dx.M_CYHI, 1.0)),
            (M_CYH3, HH3_HZ, HH3_Q, HP, amps.get(M_CYH3, 0.0))):
        for a, v in dx.mode_writes(m, f0, q, amp, num):
            img[a] = v
    # Hh3's 1-pole stage, plus the one exactly-realisable tone pole: two REAL
    # poles in the one section, its numerator HP = (Hh3's own zero) x (the LEVEL
    # differentiator's zero). Revision 3 put Figure 9's 1511.2 Hz here; revision
    # 4 puts the network's own 4219.0 Hz.
    short_pole = TONE_R4["short"]["pole_hz"] if r4 else TONE_REALISATION["short"]["extra_pole_hz"]
    poles = [HH3_P1_HZ] + [f for f in (short_pole,) if f]
    a1, a2 = real_pole_regs(poles)
    base = dx.A_MODE + M_CYH3B * dx.MODE_STRIDE
    img[base] = a1 & COEF_MASK
    img[base + 1] = a2 & COEF_MASK
    img[base + 2] = dx.amp_reg(amps.get(M_CYH3B, 1.0))
    img[base + 3] = TONE_REALISATION["short"]["num"]
    img[dx.A_PATH + P_CYS] = dx.path_word(dx.SRC_TAP + dx.M_HATBP, dx.E_CYS, nl=dx.NL_SWING,
                                          att=dx.CY_ATT, dest=M_CYH3)
    img[dx.A_PATH + P_CYD] = dx.path_word(dx.SRC_TAP + dx.M_HATBP, dx.E_CYD, nl=dx.NL_SWING,
                                          att=dx.CY_ATT, dest=dx.M_CYHI)
    img[dx.A_PATH + P_CYL] = dx.path_word(dx.SRC_TAP + dx.M_CYBP, dx.E_CYL, nl=dx.NL_SWING,
                                          att=dx.CY_ATT, dest=M_CYH1)
    img[dx.A_PATH + P_CYH3] = dx.path_word(dx.SRC_TAP + M_CYH3, dx.ENV_FULL, nl=dx.NL_LIN,
                                           dest=M_CYH3B)
    if r4:
        # The low band's own tone pole: ONE real pole, numerator RAW (mode 19 is
        # at or above N_NUMS, so the bank never reads a numerator for it -- and
        # its `num` register is A_RESET and cannot be written; see M_CYH1B).
        b1, b2 = real_pole_regs([TONE_R4["low"]["pole_hz"]])
        nb = dx.A_MODE + M_CYH1B * dx.MODE_STRIDE
        img[nb] = b1 & COEF_MASK
        img[nb + 1] = b2 & COEF_MASK
        img[nb + 2] = dx.amp_reg(amps.get(M_CYH1B, 1.0))
        img[dx.A_PATH + P_CYH1B] = dx.path_word(dx.SRC_TAP + M_CYH1, dx.ENV_FULL,
                                                nl=dx.NL_LIN, dest=M_CYH1B)
    if "E_CYS" in amps:
        img[dx.A_ENV + dx.E_CYS * dx.ENV_STRIDE + 1] = dx.peak_reg(amps["E_CYS"])
    return sorted(img.items())


def render(kit, sound: str, seconds: float = 4.0, hit_frame: int = 480, modes=N_MODES, paths=N_PATH):
    """render_drum_solo's exact path (one strike at accent 1.0, both drum
    buses at 0.45) on a bank of this size, with the layout's constants."""
    n = int(seconds * dx.SR)
    with layout():
        d = drums(modes, paths, N_NUMS)
        dm, bd = d.play(dx.hit_writes([(hit_frame, dx.SOUND_STOP[sound], 1.0)], kit), n)
    g = dx.accent_reg(0.45)
    return np.asarray(dx.output_fx(np.zeros(n), 0, dm, g, bd, g), dtype=np.float64) / 32768.0, dx.SR


def render_shipped(kit, sound: str, seconds: float = 4.0, hit_frame: int = 480):
    """The same path on the shipped 16-mode bank (for band-only calibration renders)."""
    n = int(seconds * dx.SR)
    d = dx.DrumsFx()
    dm, bd = d.play(dx.hit_writes([(hit_frame, dx.SOUND_STOP[sound], 1.0)], kit), n)
    g = dx.accent_reg(0.45)
    return np.asarray(dx.output_fx(np.zeros(n), 0, dm, g, bd, g), dtype=np.float64) / 32768.0, dx.SR


def _cy_addresses():
    out = set()
    for m in (M_CYH1, M_CYH3, M_CYH3B, dx.M_CYHI):
        out |= {dx.A_MODE + m * dx.MODE_STRIDE + f for f in range(4)}
    return out


def band_only(kit, band: str):
    """The kit with only one cymbal band's VCA path live (the other two
    switched off), for the level calibration."""
    k = dict(kit)
    off = dx.path_word(dx.SRC_OFF, dx.ENV_NONE, dest=dx.DEST_MIX)
    live = {"low": P_CYL, "decay": P_CYD, "short": P_CYS}[band]
    for p in (P_CYL, P_CYD, P_CYS):
        if p != live:
            k[dx.A_PATH + p] = off
    return sorted(k.items())
