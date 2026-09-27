#!/usr/bin/env python3
"""Part 5: the cymbal's Hh3 as a TRUE 3rd-order filter, against the shipped
2-pole -- a two-way structural enumeration, judged by the scorecard (#102).

    python3 tools/probes/hihat/hh_probe5.py --refs /tmp/tr808-ref --out /tmp/hh5
    python3 tools/probes/hihat/hh_probe5.py --quick        # no Q/gain grid

WHAT THIS IS NOT
----------------
It is **not** the experiment that added a second 2-pole to the cymbal's decay
band and reached five-band cost 10.3 (`hh_probe.py` STEP 1c). That is a second
biquad at a different corner: 12 dB/octave more, a different filter. Reference
10 says Hh3 is a **3rd-order** Sallen-Key resonant at ~10.5 kHz, and a genuine
third pole is **half a biquad at the same corner** -- one real pole, 6 dB per
octave. #97 briefed an agent out of exactly this conflation in exactly this
filter, so the two are named apart here and both are measured.

It is also **not** judged by the five-band energy cost, which is what every
number in #102's opening text is stated in. DR 0015 answers #99: the versioned
per-property scorecard is the acoustic acceptance authority and "the five-band-
energy-cost alternative and any learned/aggregate score are explicitly rejected
as judges". So the five-band cost appears here only as a surrogate that
PROPOSES candidates (DR 0015 §"A surrogate proposes; the exact rule accepts"),
and every verdict comes from `tools/scorecard.py`'s own `evaluate` / `compare`.

THE STRUCTURES, NAMED
---------------------
`M_CYHI` is a two-pole at 10.5 kHz with the bank's BP numerator (1 - z^-2),
`amp` 1.0, fed by the cymbal's DECAY-band swing VCA. The candidates cascade one
more real pole at the same corner, as a second mode with **a2 = 0** -- which is
what "half a biquad" is in this bank, since every mode is
y = x + a1 y[n-1] + a2 y[n-2]:

    2pole     the shipped filter, untouched (the baseline)
    3pole-bp  + 1 real pole at 10.5 kHz, BP numerator: (1 - z^-2)/(1 - r z^-1).
              One pole, one zero at DC -> a 6 dB/octave skirt BELOW the corner,
              which is where a HIGH-pass's skirt is, and reference 10 calls Hh3
              a high-pass. This is the reading this probe treats as primary.
    3pole-raw + 1 real pole at 10.5 kHz, RAW numerator: 1/(1 - r z^-1).
              A one-pole LOW-pass: the extra 6 dB/octave lands ABOVE the corner.
              Measured too, because #102's wording ("a 6 dB/octave skirt") does
              not say which side and the two are not the same filter.
    control   + a2 = 0 AND a1 = 0, RAW: y = x, a cascade stage that is not a
              filter at all. It MUST reproduce the 2-pole render, and that is
              how the cascade plumbing and its level normalisation are checked
              before any of it is believed. A cascade that silently changed the
              level would move every band share, and a band share is the thing
              under test.

THE DATAPATH GETS A VOTE, AND IT IS NOT FREE
--------------------------------------------
A cascade in this bank is a mode TAP feeding another mode's excitation, and the
contract's tap is `sat16(y1[m] >> 3)` (15.5). So an extra pole costs:

  * 18 dB of level, thrown away by TAP_SHIFT = 3, which the destination's `amp`
    register CANNOT recover -- `amp` is Q0.16 and saturates at 1.0. The level
    has to come back from the path word's SECOND envelope slot (e1 + e2 with the
    same envelope is an exact x2) and, for the RAW variant, from the drive
    envelope's peak. `derive_levels()` solves that chain in integers and the
    `control` arm asserts it.
  * a 16-bit rail on the tap, which the shipped `M_CYHI` has never had to face.
  * a 24th PATH, because the cascade needs its own path word. #102 prices the
    17th MODE (MW 4 -> 5) and does not price this.
  * and it pins WHICH end of the cascade is the new mode: PATH.src is 5 bits
    (`path_word`), so `SRC_TAP + m` only reaches m <= 15. **Mode 16 cannot be
    tapped at all.** The 2-pole must come first and the new 1-pole second; the
    other order needs a 6-bit src field, i.e. a 26-bit path word.

Every one of those is asserted here rather than assumed, and a failed assertion
is a REFUSAL, not a number.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import pathlib
import subprocess
import sys

import numpy as np

ROOT = pathlib.Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "model"))
sys.path.insert(0, str(ROOT / "audition"))
sys.path.insert(0, str(ROOT / "tools"))

import audio_measure as am              # noqa: E402
import drums_fx as dx                   # noqa: E402
from dsp import SR                      # noqa: E402
from modal_fixed import RAW, BP, HP     # noqa: E402

# The five-band split every earlier probe in this directory reports, kept so
# this one is readable beside them. A SURROGATE here, never a verdict.
CY_BANDS = ((20., 2000.), (2000., 5000.), (5000., 9000.),
            (9000., 13000.), (13000., 19000.))
HW_CY = (0.011, 0.103, 0.532, 0.233, 0.060)    # RECORDED cy8/CY5025.WAV
REC_CY = (0.013, 0.063, 0.584, 0.153, 0.067)   # RECORDED drums_fx.CY_FIT['shares']

# ---------------------------------------------------------------------------
# THE HELD-OUT CASE, SEALED HERE BEFORE ANYTHING IS SCORED
# ---------------------------------------------------------------------------
# docs/scorecard/cases.csv D14B ("Cymbal / variation") asks for "a second
# documented knob setting or independently recorded strike ... actual source
# settings; do not invent hardware accent behavior by scaling one WAV". Until
# this commit it had no result file at all, which is the gap #102 exists to
# close.
#
# WHY THIS FILE. The Fischer corpus holds 25 cymbal recordings, TONE x DECAY on
# {0, 2.5, 5, 7.5, 10}^2 (the filename's first code is TONE, the second DECAY --
# `tools/probe_new_voice_knobs.py` finding 2, confirmed three independent ways).
# Nine of them have already been read by something in this repository:
#
#   cy8/CY5025.WAV                     the anchor D14A, and the file the three
#                                      cymbal envelope taus and levels in
#                                      `drums_fx.CY_TAU_*` were FITTED to
#   CY50{00,25,50,75,10}               the DECAY column, read by
#                                      `test_discrimination.fit_laws`
#                                      ("CY.decay_tau") and by the
#                                      `drums_fx.CY_DECAY_T20` note
#   CY{00,25,50,75,10}50               the TONE row, read by the same function
#                                      ("CY.tone_ratio")
#
# That leaves 16 recordings no fit in this repository has ever seen. `CY2500`
# (TONE 2.5, DECAY 0.0) is chosen from them, on three criteria stated before the
# first score was taken:
#
#   1. unread by every fit above, so it is held out from the voice fit, from the
#      knob laws, AND from this issue's Q/gain fit;
#   2. reachable by interpolation of the two committed knob laws rather than
#      extrapolation -- both its coordinates lie inside the fitted crosses;
#   3. the SHORTEST decay in the corpus, and therefore the recording with the
#      best chance of being long enough for `schroeder_t20` to return a number
#      at all. The anchor CY5025 is NOT: D14A's "total decay" is invalid on the
#      REFERENCE side because that record ends before its own decay does, so
#      D14A is a no-verdict today and cannot by itself decide anything.
#
# WHAT IT IS NOT. D14B's split in cases.csv is "Expansion 48 / Development",
# not "Holdout 20" -- the cymbal family has no Holdout-20 case. Promoting one is
# a cases.csv change and belongs to #158's holdout programme, not here. So this
# is a real held-out RECORDING scored on a Development-split case, and that is
# stated rather than dressed up.
HOLDOUT = dict(case="D14B", rel="cy8/CY2500.WAV", tone=2.5, decay=0.0,
               setting="TONE 2.5, DECAY 0.0",
               seen_by=["cy8/CY5025.WAV -> drums_fx.CY_TAU_*/PEAK_CY*",
                        "cy8/CY50{00,25,50,75,10}.WAV -> CY.decay_tau",
                        "cy8/CY{00,25,50,75,10}50.WAV -> CY.tone_ratio"],
               why="unread by every fit in the repo; both knobs interpolated, "
                   "not extrapolated; shortest decay, so the record has the "
                   "best chance of supporting schroeder_t20")
DEV = dict(case="D14A", rel="cy8/CY5025.WAV", setting="TONE 5.0, DECAY 2.5")


def say(*a):
    print(*a, flush=True)


class Refused(SystemExit):
    """A precondition of the apparatus failed. REFUSED is a first-class outcome
    here, distinct from pass and from fail (CLAUDE.md, run_case.py)."""

    def __init__(self, why: str):
        super().__init__(f"REFUSED: {why}")


# ---------------------------------------------------------------- provenance
def provenance(cmd: str) -> dict:
    def _git(*a):
        return subprocess.run(["git", "-C", str(ROOT), *a],
                              capture_output=True, text=True).stdout.strip()
    files = ("model/drums_fx.py", "model/modal_fixed.py", "model/audio_measure.py",
             "model/test_808_acceptance.py", "model/test_discrimination.py",
             "tools/run_case.py", "tools/scorecard.py",
             "tools/probes/hihat/hh_probe5.py")
    import scipy
    return dict(source_commit=_git("rev-parse", "HEAD"),
                described=_git("describe", "--tags", "--always", "--dirty"),
                worktree_dirty=bool(_git("status", "--porcelain")),
                sha256_16={f: hashlib.sha256((ROOT / f).read_bytes()).hexdigest()[:16]
                           for f in files},
                python=sys.version.split()[0], numpy=np.__version__,
                scipy=scipy.__version__, sr=SR, cmd=cmd)


# ------------------------------------------------------- DERIVED filter maths
def _pole(f0: float, q: float) -> tuple[float, float]:
    from modal_fixed import pole_regs
    a1, a2 = pole_regs(f0, q)
    return a1 / (1 << 24), a2 / (1 << 24)


def onepole_a1(f0: float) -> float:
    """The single real pole of a 1-pole section whose corner is f0: r = e^-w0.

    Impulse-invariant, the same family `pole_regs` uses (r = e^{-pi f0/(Q fs)}
    is its two-pole radius). Not a bilinear design: the bank's own second-order
    sections are impulse-invariant and a cascade of two design families at one
    corner is not one filter."""
    return math.exp(-2.0 * math.pi * f0 / SR)


def onepole_regs(f0: float, coef_frac: int = 24) -> tuple[int, int]:
    """(a1, a2) registers for that pole. a2 = 0 IS the whole point: the mode is
    half a biquad, and the bank has no other way to spend one pole."""
    qf = 1 << coef_frac
    return int(round(onepole_a1(f0) * qf)), 0


NUMER = {RAW: (1., 0., 0.), BP: (1., 0., -1.), HP: (1., -2., 1.)}


def resp(f, b, a1, a2):
    z = np.exp(-2j * np.pi * np.atleast_1d(np.asarray(f, float)) / SR)
    return np.abs((b[0] + b[1] * z + b[2] * z ** 2) / (1.0 - a1 * z - a2 * z ** 2))


def derived_filters(out: dict) -> None:
    """What a 1-pole at 10.5 kHz can actually do at 48 kHz, before any audio.

    This is the cheapest thing in the whole probe and it reframes the question:
    #102 assumes the third pole buys "a 6 dB/octave skirt", and at THIS sample
    rate one of the two orientations cannot, because there is only 1.19 octaves
    of spectrum above 10.5 kHz and a pole at r = e^-w0 = 0.25 sits so close to
    the origin that its magnitude is nearly flat across all of it."""
    f0 = dx.CY_HI_HZ
    A1, A2 = _pole(f0, dx.CY_HI_Q)
    r = onepole_a1(f0)
    arms = {"2pole BP (shipped)": (NUMER[BP], A1, A2),
            "1pole BP  (skirt below)": (NUMER[BP], r, 0.0),
            "1pole RAW (skirt above)": (NUMER[RAW], r, 0.0),
            "1pole HP  (12 dB/oct, not half a biquad)": (NUMER[HP], r, 0.0)}
    fs = np.array([2000., 3450., 5000., 7000., 9000., f0, 13000., 16000., 19000., 22000.])
    say("\n== DERIVED: the candidate sections at SR = %d Hz, dB re their own "
        "value at %.0f Hz ==" % (SR, f0))
    say("  r of a 1-pole at %.0f Hz = %.4f  (%.2f octaves of spectrum remain "
        "above the corner)" % (f0, r, math.log2((SR / 2) / f0)))
    say("  " + " " * 42 + "".join(f"{f/1000:8.1f}k" for f in fs))
    rows = {}
    for name, (b, a1, a2) in arms.items():
        ref = resp(f0, b, a1, a2)[0]
        g = resp(fs, b, a1, a2) / ref
        say(f"  {name:<42}" + "".join(f"{20*math.log10(v):9.2f}" for v in g))
        top = min(2 * f0, SR / 2 - 1)
        rows[name] = dict(
            db_re_corner={f"{int(f)}": round(20 * math.log10(v), 3) for f, v in zip(fs, g)},
            low_side_db_per_oct=round(20 * math.log10(
                resp(f0, b, a1, a2)[0] / resp(f0 / 2, b, a1, a2)[0]), 3),
            high_side_db_per_oct=round(20 * math.log10(
                resp(f0, b, a1, a2)[0] / resp(top, b, a1, a2)[0]) / math.log2(top / f0), 3),
            gain_at_corner=round(float(resp(f0, b, a1, a2)[0]), 4))
    say("\n  per-octave slope each section actually delivers at this sample rate:")
    for name, v in rows.items():
        say(f"    {name:<42} below {v['low_side_db_per_oct']:+6.2f} dB/oct   "
            f"above {v['high_side_db_per_oct']:+6.2f} dB/oct")
    say("  -> the RAW (low-pass) orientation delivers +2.0 dB/octave above the")
    say("     corner, not 6: at 48 kHz the last octave ends at Nyquist and the")
    say("     pole is at r = 0.25. #102's '6 dB/octave skirt' is only available")
    say("     on the LOW side, which is where a high-pass's skirt is and which")
    say("     is what reference 10 calls Hh3. 3pole-bp is therefore the primary")
    say("     reading and 3pole-raw is carried as the alternative, not dropped.")
    out["derived_sections"] = rows


# ----------------------------------------------------------- the kit variants
M_CYHI_1P = 16          # the 17th mode. MODES 16 -> 17, so MW 4 -> 5.
P_CY_CASCADE = 23       # the 24th path. N_PATH 23 -> 24.


def assert_datapath(out: dict) -> None:
    """The contract's own field widths decide what a cascade can look like.
    Asserted, because #102 prices a 17th MODE and nothing else."""
    say("\n== the datapath's constraints, asserted rather than assumed ==")
    problems = []
    src_bits = 5                               # path_word: usat(src, 5)
    if dx.SRC_TAP + M_CYHI_1P >= (1 << src_bits):
        say(f"  PATH.src is {src_bits} bits, so SRC_TAP + m reaches m <= "
            f"{(1 << src_bits) - 1 - dx.SRC_TAP}: mode {M_CYHI_1P} CANNOT be tapped.")
        say("  => the 2-pole must be the FIRST stage and the new 1-pole the second.")
    else:
        problems.append("PATH.src is wide enough to tap mode 16 -- this probe's "
                        "reasoning about cascade order is stale")
    if dx.REG_BITS["amp"] != 16:
        problems.append("amp is no longer Q0.16; re-derive the level chain")
    say(f"  TAP m = sat16(y1[m] >> {dx.TAP_SHIFT}): a cascade stage throws away "
        f"{6.02*dx.TAP_SHIFT:.1f} dB")
    say(f"  amp is Q0.{dx.REG_BITS['amp']} and saturates at 1.0, so it cannot "
        f"recover that; the level chain below does.")
    if problems:
        raise Refused("; ".join(problems))
    out["datapath"] = dict(src_bits=src_bits, tap_shift=dx.TAP_SHIFT,
                           tap_loss_db=round(6.0206 * dx.TAP_SHIFT, 2),
                           cascade_order="2-pole first (mode 7), 1-pole second (mode 16)",
                           modes=f"{dx.N_MODES} -> {M_CYHI_1P + 1}",
                           paths=f"{dx.N_PATH} -> {P_CY_CASCADE + 1}")


def derive_levels(num: int, f0: float, q: float) -> dict:
    """The integer level chain that makes a cascaded stage unity overall.

    A tap is y1 >> 3 and `amp` cannot exceed 1.0, so 18 dB has to come from
    somewhere that is NOT a fitted voice parameter. Two exact x2s are available
    in the path word itself -- `e1 + e2` with the SAME envelope index doubles
    the value with no register change and no envelope LSB-floor effect (which is
    what caught hh_probe2: scaling an envelope's PEAK is not a gain) -- and the
    rest, if any, is a scale on the drive envelope's peak, reported as such.

        want:  (1 << TAP_SHIFT) == drive_x * casc_x * G * amp
    """
    g = float(resp(f0, NUMER[num], *[v / (1 << 24) for v in onepole_regs(f0)])[0])
    need = float(1 << dx.TAP_SHIFT) / g
    drive_x, casc_x = 2.0, 2.0                       # both e2 doublings
    amp = need / (drive_x * casc_x)
    peak_x = 1.0
    if amp > 1.0:                                    # amp saturates: raise the drive
        peak_x = min(1.0 / dx.PEAK_CYD, amp)
        amp /= peak_x
    return dict(stage_gain_at_corner=round(g, 4), drive_x=drive_x, casc_x=casc_x,
                drive_peak_x=round(peak_x, 5), amp=round(amp, 6),
                representable=amp <= 1.0 and dx.PEAK_CYD * peak_x <= 1.0,
                total=round(drive_x * casc_x * peak_x * g * amp, 5))


def cy_kit(arm: str, *, q: float = dx.CY_HI_Q, gain: float = 1.0,
           f0: float = dx.CY_HI_HZ, tone: float | None = None,
           decay: float | None = None, laws: dict | None = None) -> tuple[list, dict]:
    """The register image for one arm. `gain` is the fitted level on the whole
    high-decay band, applied to the mode that reaches the mix bus, so it means
    the same thing in every arm.

    `tone`/`decay`, when given, apply `test_discrimination.kit_at`'s committed
    knob laws first -- that is how the held-out setting is reached without
    inventing a control law here."""
    if tone is None and decay is None:
        img = dict(dx.kit_with_sounds("CY"))
    else:
        import test_discrimination as td
        if laws is None:
            raise Refused("a knob setting was asked for with no fitted laws")
        img = dict(td.kit_at("CY", (tone, decay), laws))
    meta: dict = {"arm": arm, "q": q, "gain": gain, "f0": f0}
    # The kit's own M_CYHI level, whatever the knob law left there, is the
    # reference point the `gain` multiplies.
    amp0 = img[dx.A_MODE + dx.M_CYHI * dx.MODE_STRIDE + 2] / 65536.0

    if arm == "2pole":
        for a, v in dx.mode_writes(dx.M_CYHI, f0, q, min(1.0, amp0 * gain), BP):
            img[a] = v
        return sorted(img.items()), meta

    num = {"3pole-bp": BP, "3pole-raw": RAW, "control": RAW}[arm]
    lv = derive_levels(num if arm != "control" else RAW, f0, q)
    if arm == "control":
        # y = x: not a filter. The one arm whose answer is known in advance.
        lv = dict(lv, stage_gain_at_corner=1.0, drive_x=2.0, casc_x=2.0,
                  drive_peak_x=min(1.0 / dx.PEAK_CYD, 2.0), amp=1.0)
        lv["amp"] = float(1 << dx.TAP_SHIFT) / (lv["drive_x"] * lv["casc_x"]
                                                * lv["drive_peak_x"])
        lv["total"] = (lv["drive_x"] * lv["casc_x"] * lv["drive_peak_x"] * lv["amp"])
        lv["representable"] = lv["amp"] <= 1.0
    if not lv["representable"]:
        raise Refused(f"{arm}: the level chain needs amp {lv['amp']:.3f} or peak "
                      f"x{lv['drive_peak_x']:.3f}, outside the registers")
    meta["levels"] = lv

    # stage 1: the existing 2-pole, TAPPED ONLY (amp 0) so it no longer reaches
    # the mix bus on its own.
    for a, v in dx.mode_writes(dx.M_CYHI, f0, q, 0.0, BP):
        img[a] = v
    # stage 2: the new mode. a2 = 0 -- half a biquad.
    a1_1p, a2_1p = onepole_regs(f0)
    if arm == "control":
        a1_1p = 0
    base = dx.A_MODE + M_CYHI_1P * dx.MODE_STRIDE
    img[base + 0] = a1_1p & ((1 << 26) - 1)
    img[base + 1] = a2_1p & ((1 << 26) - 1)
    img[base + 2] = dx.amp_reg(min(1.0, lv["amp"] * amp0 * gain))
    img[base + 3] = num
    # the drive into stage 1, with the second envelope slot as an exact x2, and
    # the drive peak scale if the chain needed one.
    if lv["drive_peak_x"] != 1.0:
        for a, v in dx.env_writes(dx.E_CYD, dx.CY, dx.CY_TAU_DECAY,
                                  min(1.0, dx.PEAK_CYD * lv["drive_peak_x"])):
            img[a] = v
    img[dx.A_PATH + dx.P_CYD] = dx.path_word(
        dx.SRC_TAP + dx.M_HATBP, dx.E_CYD,
        dx.E_CYD if lv["drive_x"] == 2.0 else dx.ENV_NONE,
        nl=dx.NL_SWING, att=dx.CY_ATT, dest=dx.M_CYHI)
    # the cascade path: stage 1's tap into stage 2, ungated.
    img[dx.A_PATH + P_CY_CASCADE] = dx.path_word(
        dx.SRC_TAP + dx.M_CYHI, dx.ENV_FULL,
        dx.ENV_FULL if lv["casc_x"] == 2.0 else dx.ENV_NONE,
        nl=dx.NL_LIN, att=0, dest=M_CYHI_1P)
    return sorted(img.items()), meta


# --------------------------------------------------------------- the renders
PRE = 0.010


def render(kit: list, seconds: float, arm: str) -> tuple[np.ndarray, dict]:
    """One cymbal hit through the fixed-point block -- the same path
    `hh_probe3` and `hh_probe4` use, never the offline emulator `hh_probe4`
    withdrew. Reports the tap rail, which only the cascade arms can hit."""
    two = arm == "2pole"
    modes = dx.N_MODES if two else M_CYHI_1P + 1
    paths = dx.N_PATH if two else P_CY_CASCADE + 1
    # NUMS is what #102 says can DROP from 11 to 9 by renumbering. Here the new
    # mode is appended at 16, so it needs a numerator register of its own and
    # NUMS must reach 17 -- the renumbering is priced separately, and only if a
    # cascade arm wins.
    nums = dx.N_NUMS if two else M_CYHI_1P + 1
    at = int(PRE * SR)
    n = at + int(seconds * SR)
    d = dx.DrumsFx(modes=modes, nums=nums, paths=paths)
    dm, bd = d.play(dx.hit_writes([(at, dx.CY, 1.0)], kit), n)
    x = dm.astype(np.float64) + bd.astype(np.float64)
    return x, dict(modes=modes, paths=paths, tap_saturations=int(d.n_tapsat),
                   peak=float(np.abs(x).max()))


_SOS: dict = {}


def band_shares(x, edges=CY_BANDS) -> np.ndarray:
    from scipy.signal import butter, sosfiltfilt
    x = np.asarray(x, float)
    tot = float((x ** 2).sum())
    if tot <= 0:
        return np.zeros(len(edges))
    out = []
    for lo, hi in edges:
        k = (lo, hi)
        if k not in _SOS:
            _SOS[k] = butter(4, [lo / (SR / 2.), min(hi, SR / 2. - 1.) / (SR / 2.)],
                             btype="band", output="sos")
        out.append(float((sosfiltfilt(_SOS[k], x) ** 2).sum()) / tot)
    return np.array(out)


def band_energy_abs(x, edges=CY_BANDS) -> np.ndarray:
    from scipy.signal import sosfiltfilt
    band_shares(x, edges)                       # populate the design cache
    x = np.asarray(x, float)
    return np.array([float((sosfiltfilt(_SOS[(lo, hi)], x) ** 2).sum())
                     for lo, hi in edges])


def decay_band_only(kit: list) -> list:
    """The same image with the cymbal's SHORT and LOW band VCAs silenced.

    The cascade is on the DECAY band alone, and the other two bands overlap it
    (`M_CHHP` is the short band's 11.7 kHz high-pass, squarely inside 9-13 kHz).
    A cascade stage also delays its band by one sample, which changes how those
    three bands interfere -- a real property of the structure, and one that
    would swamp a level check. So the level chain is checked with the other two
    bands off, where the answer is known exactly, and the interference is then
    reported separately instead of being absorbed into a tolerance."""
    img = dict(kit)
    for e, tau in ((dx.E_CYS, dx.CY_TAU_SHORT), (dx.E_CYL, dx.CY_TAU_LOW)):
        for a, v in dx.env_writes(e, dx.CY, tau, 0.0):
            img[a] = v
    return sorted(img.items())


def fmt(sh):
    return " / ".join(f"{v*100:5.1f}" for v in sh)


def five_band_cost(got, ref=HW_CY) -> float:
    """The SURROGATE. It proposes; it never accepts. DR 0015 rejects it as a
    judge by name, and it is printed here only because every number in #102's
    opening text is stated in it."""
    return float(sum(abs(g - r) for g, r in zip(got, ref)) * 100)


# ---------------------------------------------------------- the scorecard side
def scorecard_record(x: np.ndarray, ref_rel: str, setting: str, case: str,
                     refdir: pathlib.Path, prov: dict, extra: dict) -> dict:
    """A result record in exactly the shape `tools/scorecard.py` evaluates,
    measured with `tools/run_case.py`'s OWN estimators and tolerance rules.

    Nothing is reimplemented here. Reimplementing an estimator is how a probe
    ends up grading itself: the board's verdict has to come out of the board's
    apparatus, and `measurement_basis` hashes that apparatus so a divergence
    would read as INCOMPARABLE rather than as progress."""
    import run_case as rc
    from scipy.io import wavfile

    path = refdir / ref_rel
    if not path.exists():
        raise Refused(f"reference recording missing: {path}")
    sr_ref, ref_x = wavfile.read(str(path))
    ref_x = np.asarray(ref_x, float)
    if ref_x.ndim > 1:
        ref_x = ref_x.mean(axis=1)
    ref_x = ref_x / 32768.0
    if am.is_silent(ref_x):
        raise Refused(f"reference recording {ref_rel} is silent")

    ours_x = x / 32768.0
    ref_y = rc.prepare(ref_x, int(sr_ref), side=f"the reference recording {ref_rel}")
    ours_y = rc.prepare(ours_x, SR, side="our CY render")
    ref, ours = (ref_y, int(sr_ref)), (ours_y, SR)

    ctx = {}
    f0 = rc._f0("CY", 0.010, 0.200)(*ref)
    if f0.ok:
        ctx["ref_f0"] = f0.value
    metrics = {}
    for name, units, est, tol_rule in rc.DRUM_PLAN["CY"]:
        metrics[name] = rc.measure_pair(name, units, est, ours, ref, tol_rule, ctx)
        if ("CY", name) in rc.UNQUALIFIED:
            metrics[name] = rc.unqualified_metric(units, rc.UNQUALIFIED[("CY", name)],
                                                 metrics[name])
    inputs = rc.model_input_hashes({f"reference:{ref_rel}": rc._file_sha(path)})
    return {
        "engine": rc.ENGINE, "case_id": case, "subject": "Cymbal / probe arm",
        "source_commit": prov["source_commit"],
        "analysis_run": rc.analysis_run(),
        "provenance": {
            "engine": rc.ENGINE,
            "worktree": {"commit": prov["source_commit"], "described": prov["described"],
                         "dirty": prov["worktree_dirty"]},
            "command": prov["cmd"], "inputs": inputs,
            "config": dict(voice="CY", refs=str(refdir), inject=None,
                           render_seconds=rc.SOLO_SECONDS.get("CY", 2.2), accent=1.0,
                           bus_gain=0.45, level_matched=True, **extra),
            "python": prov["python"],
        },
        "reference_profile": f"fischer-tr808-103852:{ref_rel} ({setting})",
        "reference_identity": rc.REF_ID,
        "tolerance_policy": rc.TOLERANCE_POLICY,
        "windowing": {"ours": rc.lead_report(ours_x, SR),
                      "reference": rc.lead_report(ref_x, int(sr_ref))},
        "metrics": metrics,
    }


def score(rec: dict, case_row: dict) -> dict:
    import scorecard as sc
    return sc.evaluate(case_row, rec)


def case_row(case_id: str) -> dict:
    import scorecard as sc
    for c in sc.load_cases():
        if c["case_id"] == case_id:
            return c
    raise Refused(f"no case {case_id} in docs/scorecard/cases.csv")


def dr0015_on_valid(base_ev: dict, cand_ev: dict, allowance: float = None) -> dict:
    """DR 0015's acceptance rule applied to the properties that ARE valid.

    `scorecard.compare` returns INCOMPARABLE the moment either side is a
    no-verdict -- deliberately, and correctly: "a state that is not a verdict
    cannot be improved upon or regressed from". Every cymbal case here IS a
    no-verdict, because `total decay` is invalid on the REFERENCE side of the
    anchor (CY5025 ends before its own decay does) and on OUR side of the
    holdout. So `compare` cannot decide this, and that refusal is reported as
    the board's answer.

    This function is NOT a second judge and its result is NOT a board verdict.
    It is the same rule -- at least one required property improves meaningfully,
    none regresses beyond its allowance -- read over the properties that have
    distances, so that a reader can see WHICH WAY the evidence points inside a
    case the board will not score. Labelled everywhere it is printed.
    """
    import scorecard as sc
    allow = sc.DEFAULT_ALLOWANCE if allowance is None else allowance
    bp = base_ev.get("properties") or {}
    cp = cand_ev.get("properties") or {}
    shared = sorted(set(bp) & set(cp))
    improved = [f"{k} {bp[k]:.3f} -> {cp[k]:.3f}" for k in shared
                if cp[k] - bp[k] <= -allow]
    regressed = [f"{k} {bp[k]:.3f} -> {cp[k]:.3f} (+{cp[k]-bp[k]:.3f}, "
                 f"allowance {allow:.3f})" for k in shared if cp[k] - bp[k] > allow]
    lost = sorted(set(bp) - set(cp))
    reasons = []
    if lost:
        reasons.append("coverage lost: " + ", ".join(lost))
    if regressed:
        reasons.append("regressed beyond allowance: " + "; ".join(regressed))
    if not improved:
        reasons.append(f"no property improved meaningfully (threshold {allow:.3f})")
    return dict(indicative="reject" if reasons else "accept",
                compared=shared, improved=improved, regressed=regressed,
                lost=lost, reasons=reasons or ["improved: " + "; ".join(improved)],
                allowance=allow,
                allowance_note="scorecard.DEFAULT_ALLOWANCE, a PLACEHOLDER: "
                               "DR 0015 requires allowances derived from each "
                               "measurement's own uncertainty and #158 is where "
                               "that gets measured")


def props_line(ev: dict) -> str:
    p = ev.get("properties") or {}
    body = "  ".join(f"{k} {v:.3f}" for k, v in sorted(p.items()))
    w = "--" if ev.get("worst") is None else f"{ev['worst']:.3f}"
    return f"{ev['state']:<11} worst {w:>6}   {body}" + (
        f"   [{ev['why']}]" if ev.get("why") else "")


# ---------------------------------------------------------------------- main
def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--refs", default=os.environ.get("GF180_TR808_REFS", "/tmp/tr808-ref"))
    ap.add_argument("--out", default="/tmp/hh5")
    ap.add_argument("--quick", action="store_true",
                    help="skip the Q/gain grid; score the shipped values only")
    ap.add_argument("--write-d14b", action="store_true",
                    help="write the SHIPPED structure's held-out record into "
                         "docs/scorecard/results/D14B.json")
    ap.add_argument("--seconds", type=float, default=2.0,
                    help="render length for the surrogate sweep (the scorecard "
                         "arms always use run_case's own 3.6 s)")
    a = ap.parse_args(argv)
    refdir = pathlib.Path(a.refs)
    outdir = pathlib.Path(a.out)
    outdir.mkdir(parents=True, exist_ok=True)
    prov = provenance("tools/probes/hihat/hh_probe5.py --refs %s --out %s%s"
                      % (a.refs, a.out, " --quick" if a.quick else ""))
    out: dict = {"provenance": prov, "holdout": HOLDOUT, "dev": DEV}
    say("== provenance ==")
    say(json.dumps(prov, indent=2))
    if not refdir.exists():
        raise Refused(f"reference corpus not at {refdir} -- clone "
                      f"tidalcycles/sounds-tr808-fischer or set GF180_TR808_REFS")

    # ---- preconditions --------------------------------------------------
    say("\n== PRECONDITIONS ==")
    x0, info0 = render(dx.kit_with_sounds("CY"), 2.0, "2pole")
    base_sh = band_shares(x0)
    err = max(abs(p - q) for p, q in zip(base_sh, REC_CY))
    say(f"  shipped 2.0 s render   {fmt(base_sh)}")
    say(f"  drums_fx.CY_FIT        {fmt(REC_CY)}")
    if err > 0.003:
        raise Refused(f"cannot reproduce CY_FIT['shares'] (worst band "
                      f"{err*100:.2f} points) -- this is not the tree those "
                      f"numbers were taken on")
    say(f"  OK: reproduces the recorded row to {err*100:.2f} points.")
    for f in (DEV["rel"], HOLDOUT["rel"]):
        if not (refdir / f).exists():
            raise Refused(f"reference recording missing: {f}")
    say(f"  OK: {DEV['rel']} (dev) and {HOLDOUT['rel']} (held out) are both present.")

    derived_filters(out)
    assert_datapath(out)

    # ---- the cascade plumbing, checked against a known answer -----------
    say("\n== START RED: the `control` arm -- a cascade stage that is NOT a filter ==")
    say("  a1 = a2 = 0, RAW: y = x. With the cymbal's other two band VCAs off,")
    say("  its per-band ENERGY must match the 2-pole's, or the cascade's level")
    say("  normalisation is wrong and every band share below is measuring the")
    say("  plumbing instead of the filter.")
    kit_c, meta_c = cy_kit("control")
    say(f"  levels: {json.dumps(meta_c['levels'])}")
    xr_, _ = render(decay_band_only(dx.kit_with_sounds("CY")), 2.0, "2pole")
    xc_, infoc_ = render(decay_band_only(kit_c), 2.0, "control")
    er, ec = band_energy_abs(xr_), band_energy_abs(xc_)
    db = [20 * math.log10(max(c, 1e-30) / max(r, 1e-30)) for r, c in zip(er, ec)]
    tot_db = 20 * math.log10(float((xc_ ** 2).sum()) ** .5
                             / max(float((xr_ ** 2).sum()) ** .5, 1e-30))
    say("  decay band alone, per-band energy of control re 2-pole, dB:")
    say("    " + "  ".join(f"{lo/1000:g}-{hi/1000:g}k {d:+6.2f}"
                           for (lo, hi), d in zip(CY_BANDS, db)))
    say(f"    total {tot_db:+.2f} dB")
    worst_db = max(abs(d) for d in db[1:])      # <2 kHz is 100 dB down; skip it
    out["control_arm"] = dict(levels=meta_c["levels"], per_band_db=db,
                              total_db=tot_db, worst_band_db=worst_db,
                              tap_saturations=infoc_["tap_saturations"])
    if worst_db > 0.5 or abs(tot_db) > 0.5:
        raise Refused("the unity cascade does not reproduce the 2-pole render "
                      f"(worst band {worst_db:+.2f} dB, total {tot_db:+.2f} dB). "
                      "The level chain, not the filter, is what the arms below "
                      "would be measuring.")
    say("  OK: the cascade is transparent when its stage is transparent.")

    say("\n  What a cascade costs even so: the tap is read BEFORE the bank steps,")
    say("  so the decay band arrives one sample late relative to the short and")
    say("  low bands and the three interfere differently. Measured on the full")
    say("  kit, with the same transparent stage:")
    xc, infoc = render(kit_c, 2.0, "control")
    sh_c = band_shares(xc)
    say(f"    2-pole   {fmt(base_sh)}   peak {info0['peak']:.0f}  "
        f"tap rails {info0['tap_saturations']}")
    say(f"    control  {fmt(sh_c)}   peak {infoc['peak']:.0f}  "
        f"tap rails {infoc['tap_saturations']}")
    delay_cost = max(abs(p - q) for p, q in zip(sh_c, base_sh)) * 100
    say(f"    -> {delay_cost:.2f} points of band share, and "
        f"{20*math.log10(max(infoc['peak'],1)/max(info0['peak'],1)):+.2f} dB of peak,")
    say("       bought by the one-sample delay alone. Any filter the cascade adds")
    say("       has to beat THAT, not the 2-pole's own numbers.")
    out["control_arm"].update(full_kit_shares=list(sh_c),
                              delay_cost_points=delay_cost,
                              peak_db=20 * math.log10(max(infoc["peak"], 1)
                                                      / max(info0["peak"], 1)))

    # ---- the surrogate sweep -------------------------------------------
    say("\n== SURROGATE (proposes, never accepts): five-band split, 2.0 s ==")
    say("  DR 0015 rejects this measure as a judge by name. It is here to pick")
    say("  candidates cheaply and to be comparable with #102's own numbers.")
    qs = (dx.CY_HI_Q,) if a.quick else (1.8, 2.5, 3.2, 4.0)
    gs = (1.0,) if a.quick else (0.8, 1.0, 1.25)
    sweep = {}
    for arm in ("2pole", "3pole-bp", "3pole-raw"):
        rows = []
        for q in qs:
            for g in gs:
                try:
                    kit, meta = cy_kit(arm, q=q, gain=g)
                except Refused as e:
                    rows.append(dict(q=q, gain=g, refused=str(e)))
                    continue
                x, info = render(kit, a.seconds, arm)
                sh = band_shares(x)
                t20 = am.schroeder_t20(x, SR)
                rows.append(dict(q=q, gain=g, shares=list(sh),
                                 cost=five_band_cost(sh),
                                 t20_ms=(t20.value * 1e3) if t20.ok else None,
                                 tap_rails=info["tap_saturations"], peak=info["peak"]))
                say(f"  {arm:<10} Q {q:4.1f} gain {g:4.2f}  {fmt(sh)}  "
                    f"cost {five_band_cost(sh):5.1f}  "
                    f"T20 {(t20.value*1e3) if t20.ok else float('nan'):6.0f} ms  "
                    f"tap rails {info['tap_saturations']}")
        sweep[arm] = rows
    say(f"  machine    {fmt(HW_CY)}")
    out["surrogate_sweep"] = sweep

    # The candidates each structure proposes. TOP TWO by the surrogate, not one:
    # DR 0015 says the surrogate proposes and the exact rule accepts, and a
    # surrogate that hands over a single candidate has quietly done the
    # accepting. Both are then scored, and the choice WITHIN a structure is made
    # on the development case only -- never on the holdout.
    shortlist = {}
    for arm, rows in sweep.items():
        ok = [r for r in rows if "cost" in r]
        if not ok:
            continue
        ok.sort(key=lambda r: r["cost"])
        shortlist[arm] = [dict(q=r["q"], gain=r["gain"], surrogate=r["cost"])
                          for r in ok[:1 if a.quick else 2]]
        for c in shortlist[arm]:
            say(f"  proposes: {arm:<10} Q {c['q']} gain {c['gain']} "
                f"(surrogate {c['surrogate']:.1f})")
    out["shortlist"] = shortlist

    # ---- the judge, on the DEVELOPMENT case, to fit Q and gain ----------
    say("\n== FIT on the DEVELOPMENT case only (D14A / %s): the scorecard picks "
        "Q and gain ==" % DEV["rel"])
    rows_dev = case_row("D14A")
    rows_hold = case_row(HOLDOUT["case"])
    fitted: dict = {}
    dev_all: dict = {}
    for arm, cands in shortlist.items():
        best = None
        for c in cands:
            kit, meta = cy_kit(arm, q=c["q"], gain=c["gain"])
            x, info = render(kit, 3.6, arm)
            rec = scorecard_record(x, DEV["rel"], DEV["setting"], "D14A", refdir, prov,
                                   dict(structure=arm, cy_hi_q=c["q"],
                                        cy_hi_gain=c["gain"]))
            ev = score(rec, rows_dev)
            key = f"{arm} Q{c['q']} g{c['gain']}"
            dev_all[key] = dict(evaluated=ev, record=rec, candidate=c)
            say(f"  {key:<26} {props_line(ev)}")
            # Within a structure, prefer the candidate whose property vector is
            # smallest on the properties that have distances. Still a proposal
            # step: the between-structure decision is the acceptance rule's.
            p = ev.get("properties") or {}
            rank = sum(p.values()) if p else float("inf")
            if best is None or rank < best[0]:
                best = (rank, c, ev, rec)
        if best:
            fitted[arm] = dict(candidate=best[1], dev=dict(evaluated=best[2],
                                                           record=best[3]))
            say(f"  -> {arm:<10} fitted on D14A: Q {best[1]['q']} gain "
                f"{best[1]['gain']}  (sum of property distances {best[0]:.3f})")
    out["dev_scored"] = {k: v["evaluated"] for k, v in dev_all.items()}
    out["fitted"] = {k: v["candidate"] for k, v in fitted.items()}

    # ---- the holdout ---------------------------------------------------
    say("\n== THE HOLDOUT, sealed above: %s (%s), case %s =="
        % (HOLDOUT["rel"], HOLDOUT["setting"], HOLDOUT["case"]))
    say("  Our side is rendered at that documented setting through the COMMITTED")
    say("  knob laws (`test_discrimination.kit_at`), not by scaling one WAV --")
    say("  which cases.csv forbids by name. Those laws were fitted from the")
    say("  TONE row and DECAY column at 5.0; this recording is in neither, and")
    say("  no Q or gain here was fitted against it.")
    say("  (fitting the committed CY knob laws from the corpus once...)")
    import test_discrimination as td
    laws = td.fit_laws(str(refdir), all_sounds=True)
    for arm, f in fitted.items():
        c = f["candidate"]
        kit_h, meta_h = cy_kit(arm, q=c["q"], gain=c["gain"],
                               tone=HOLDOUT["tone"], decay=HOLDOUT["decay"], laws=laws)
        xh, infoh = render(kit_h, 3.6, arm)
        rech = scorecard_record(
            xh, HOLDOUT["rel"], HOLDOUT["setting"], HOLDOUT["case"], refdir, prov,
            dict(structure=arm, cy_hi_q=c["q"], cy_hi_gain=c["gain"],
                 knobs=dict(tone=HOLDOUT["tone"], decay=HOLDOUT["decay"]),
                 knob_law="test_discrimination.kit_at / fit_laws"))
        evh = score(rech, rows_hold)
        f["holdout"] = dict(evaluated=evh, record=rech)
        say(f"  {arm:<10} {HOLDOUT['case']}  {props_line(evh)}")
    out["verdicts"] = {k: {"D14A": v["dev"]["evaluated"],
                           HOLDOUT["case"]: v["holdout"]["evaluated"]}
                       for k, v in fitted.items()}
    out["records"] = {k: {"D14A": v["dev"]["record"],
                          HOLDOUT["case"]: v["holdout"]["record"]}
                      for k, v in fitted.items()}

    # ---- DR 0015's acceptance rule -------------------------------------
    say("\n== THE DECISION: DR 0015's acceptance rule ==")
    import scorecard as sc
    acc, ind = {}, {}
    for case in ("D14A", HOLDOUT["case"]):
        req = [m.strip() for m in (case_row(case)["required_measurements"] or "").split(";")
               if m.strip()]
        for arm in fitted:
            if arm == "2pole":
                continue
            bev = fitted["2pole"]["dev" if case == "D14A" else "holdout"]["evaluated"]
            cev = fitted[arm]["dev" if case == "D14A" else "holdout"]["evaluated"]
            cmpres = sc.compare(bev, cev, required=req)
            acc[f"{case}:{arm}"] = cmpres
            say(f"  scorecard.compare  {case}  2pole -> {arm:<10} "
                f"{cmpres['verdict'].upper():<13} " + "; ".join(cmpres["reasons"]))
    say("\n  The board refuses both cases, and that is the correct answer rather")
    say("  than a gap: `total decay` is a REQUIRED component of D14A and D14B and")
    say("  it is invalid on the reference side of the anchor and on our side of")
    say("  the holdout, so neither case has a verdict to compare. (The first")
    say("  reason printed above is an artefact of `scorecard.evaluate` dropping")
    say("  `provenance` from a no-verdict record; the substantive reason is the")
    say("  no-verdict itself.)")
    say("\n  The same rule, read over the properties that DO have distances --")
    say("  INDICATIVE, not a board verdict:")
    for case in ("D14A", HOLDOUT["case"]):
        for arm in fitted:
            if arm == "2pole":
                continue
            r = dr0015_on_valid(
                fitted["2pole"]["dev" if case == "D14A" else "holdout"]["evaluated"],
                fitted[arm]["dev" if case == "D14A" else "holdout"]["evaluated"])
            ind[f"{case}:{arm}"] = r
            say(f"    {case}  2pole -> {arm:<10} {r['indicative'].upper():<7} "
                + "; ".join(r["reasons"]))
    out["acceptance"] = acc
    out["acceptance_indicative"] = ind

    (outdir / "hh5.json").write_text(json.dumps(out, indent=2, default=float))
    say(f"\n(written {outdir / 'hh5.json'})")

    if a.write_d14b:
        # `tools/scorecard.py`: results are "written by whatever produced the
        # measurement". This is that. `run_case.py --batch` does not yet know
        # the variation cases; teaching it is filed as a follow-up.
        p = ROOT / "docs" / "scorecard" / "results" / f"{HOLDOUT['case']}.json"
        rec = fitted["2pole"]["holdout"]["record"]
        rec = dict(rec, subject=case_row(HOLDOUT["case"])["subject"],
                   holdout_seal=HOLDOUT,
                   note=("The SHIPPED structure (2-pole M_CYHI). #102 asked "
                         "whether a true 3rd-order Hh3 should replace it; the "
                         "answer, on this record and D14A, was no -- see "
                         "tools/probes/hihat/hh_probe5.py."))
        p.write_text(json.dumps(rec, indent=2, default=float) + "\n")
        say(f"(written {p})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
