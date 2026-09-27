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
              -> Hh2: 2-pole HIGH-pass. Its corner is NOT resolved by the
              reference ("2nd-order non-unity-gain Sallen-Key (resonant)"); the
              kit's existing 10.5 kHz Q 2.5 is carried over and stated as a
              carried choice. Was: a 10.5 kHz BAND-pass.
  high short  7.1 kHz band-pass -> swing VCA x E_CYS -> Hh3: THIRD order, a 2-pole
              high-pass cascaded with a 1-pole high-pass at the SAME ~10.5 kHz
              corner [#102; reference 10 "resonant ~10.5 kHz"; Q carried at 2.5].
              Was: the CLOSED HAT's 11.7 kHz 2-pole high-pass, borrowed.
  level stage the +6 dB/octave differentiator of the LEVEL buffer [reference 10,
              W14b §11], applied to all three bands at their last stage as one
              more (1 - z^-1) in the numerator: Hh1 and Hh2 use HP3 = (1-z^-1)^3,
              Hh3's 1-pole stage uses HP = (1-z^-1)^2 (its own zero plus the tilt).

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
HH1_HZ, HH1_Q = 2500.0, 0.97
HH3_HZ, HH3_Q = dx.CY_HI_HZ, dx.CY_HI_Q                       # carried: 10.5 kHz, Q 2.5
P_CYS, P_CYD, P_CYL = 20, 21, 22                             # indices in kit_808's path list
P_CYH3 = 23


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
    for m, f0, q, num in ((M_CYH1, HH1_HZ, HH1_Q, HP3), (dx.M_CYHI, HH3_HZ, HH3_Q, HP3),
                          (M_CYH3, HH3_HZ, HH3_Q, HP)):
        for a, v in dx.mode_writes(m, f0, q, amps.get(m, 0.0 if m == M_CYH3 else 1.0), num):
            img[a] = v
    # Hh3's 1-pole stage: a1 = r, a2 = 0, with r the 1-pole high-pass pole at the corner
    r = math.exp(-2 * math.pi * HH3_HZ / dx.SR)
    base = dx.A_MODE + M_CYH3B * dx.MODE_STRIDE
    img[base] = int(round(r * (1 << 24))) & ((1 << 26) - 1)
    img[base + 1] = 0
    img[base + 2] = dx.amp_reg(amps.get(M_CYH3B, 1.0))
    img[base + 3] = HP
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
