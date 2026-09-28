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
        band split 16.2 dB from the 808 CY5025 (H-L 24.38 against 8.16) where
        this rule is 3.9 dB from it, and takes the strike window from 0 of 14
        thirds outside +-6 dB to 8 of 14. So the balance is not applicable
        until SN p.13's VR4 network AND the VCA drives are both digitised.
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


def candidate_kit(amps: dict | None = None, kit=None):
    """The §10 structure on the remapped image. `amps` (mode -> level, and
    'E_CYS' -> envelope peak) are the band levels; None uses unity placeholders
    for calibration renders."""
    img = remap_kit(kit if kit is not None else dx.kit_808())
    amps = amps or {}
    for m, f0, q, num in ((M_CYH1, HH1_HZ, HH1_Q, TONE_REALISATION["low"]["num"]),
                          (dx.M_CYHI, HH2_HZ, HH2_Q, TONE_REALISATION["decay"]["num"]),
                          (M_CYH3, HH3_HZ, HH3_Q, HP)):
        for a, v in dx.mode_writes(m, f0, q, amps.get(m, 0.0 if m == M_CYH3 else 1.0), num):
            img[a] = v
    # Hh3's 1-pole stage, plus revision 3's one exactly-realisable tone pole:
    # two REAL poles in the one section, its numerator HP = (Hh3's own zero) x
    # (the LEVEL differentiator's zero).
    poles = [HH3_P1_HZ] + [f for f in (TONE_REALISATION["short"]["extra_pole_hz"],) if f]
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
