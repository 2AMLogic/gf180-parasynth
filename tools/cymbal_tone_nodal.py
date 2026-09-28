#!/usr/bin/env python3
"""The #369 cymbal tone stage realised from the NODAL solution, across the whole TONE knob (step 10).

ONE QUESTION. Step 5 (`docs/scorecard/cymbal-369/candidate3/`) put the tone
stage into the bank and validated the realisation against W14b Figure 9's
per-band 2-pole window fit, inside a 3.0 dB bound stated in advance. Steps
#390/#417 then REPLACED Figure 9's extrapolation with a nodal solution of the
actual network off SN p.13 (`tools/tone_stage_schematic.py`), and step 9
(`tools/cymbal_tone_knob.py`) established that the knob position is VR4's wiper
fraction, alpha = TONE/100, off the pot's own "20K(B)" linear-taper marking.

So the question this module answers, and it is the precondition for any TONE
render:

    Is revision 3's tone realisation still inside its own 3.0 dB bound when the
    target is the nodal solution rather than Figure 9's window fit -- at every
    TONE position the knob reaches -- and if not, what DISCRETE section does the
    bank need?

Discrete, not fitted. The candidate realisations enumerated here take their pole
frequencies from the NETWORK'S OWN POLE SET (`tone_stage_schematic.poles_hz`),
never from a curve fit, and the choice between them is a search over subsets of
that set plus the two DC-zero options -- an enumeration with 42 members per
band, not an optimisation over a continuum. `poles-from-network` asserts that
property so a fitted pole cannot enter by accident.

WHAT IT REFUSES. A band with no realisation inside SHAPE_BOUND_DB at every TONE
position, a missing artifact, a coefficient that will not fit its register, and
a realisation that needs more bank sections than the 8-bit register map can
address. `Refused` is not a failed check: it is the tool declining to answer.

  --report   the per-band tables, the chosen realisation and the TONE gain table
  --check    the gate: named properties, and a properties x defects matrix
  --json P   write the evidence record

No recording is read. This is transfer-function arithmetic against the nodal
network and two committed digitised artifacts, which is exactly why the render
in `tools/cymbal_tone_render.py` still has to be run.
"""
from __future__ import annotations

import argparse
import itertools
import json
import math
import pathlib
import sys

import numpy as np

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "model"), str(ROOT / "tools")]
import drums_fx as dx                      # noqa: E402
import modal_fixed as mf                   # noqa: E402
import cymbal_candidate as cc              # noqa: E402
import cymbal_bands as cb                  # noqa: E402
import cymbal_tone_knob as ctk             # noqa: E402
import cymbal_tone_realisation as tr       # noqa: E402
import tone_stage_schematic as ts          # noqa: E402

SR = float(dx.SR)
BANDS = ("low", "decay", "short")
# Which network rail each band's high-pass drives. Imported from step 9 rather
# than restated: the assignment is resolved twice over (SN p.13's capacitor
# count and Figure 9's own fit) and this module must not be able to disagree
# with the module that resolved it.
RAILS = dict(ctk.RAILS)

# The knob's five positions, in KNOB order (`cymbal_bands.CODES` is in filename
# order and "10" means 100 %, which is wrong-then-right 4 of step 9).
CODES = tuple(ctk.CODES)
FRAC = dict(ctk.FRAC)
# The anchor: the level rule calibrates every band against the SHIPPED kit,
# whose one cymbal is compared with the D14A anchor recording CY5025 -- TONE
# code "50". So the anchor is TONE 50 and the TONE gain table is zero there by
# construction.
ANCHOR = "50"

# ---------------------------------------------------------------------------
# bounds, every one stated before the numbers below were computed
# ---------------------------------------------------------------------------
# The realisation bound, inherited unchanged from step 5 so this step cannot
# move the goalposts: docs/audio-distance-metrics.md's board tolerance for band
# tilt. Revision 3 declared it and was measured at 0.45 / 1.41 / 1.35 dB
# against Figure 9's target.
SHAPE_BOUND_DB = tr.SHAPE_BOUND_DB
# The two routes to the tone stage -- Figure 9's digitised curve and SN p.13's
# nodal solution -- must AGREE on the one band Figure 9 plots across the whole
# audio band (Ht3, 20 Hz-20 kHz), and that agreement is the external grounding
# for the nodal route. 0.5 dB is 10x werner_fig9's own 0.05 dB rms fit residual
# and a sixth of SHAPE_BOUND_DB.
CROSS_BOUND_DB = 0.5
# A realisation's pole must BE one of the network's own poles, not near one.
POLE_TOL_HZ = 1.0
# ...and it must lie within this many octaves of the band's ACTIVE range. A pole
# two decades below a band is in its asymptotic -6 dB/octave region there and is
# not distinguishable from any other such pole: over the short band's 6.3-16 kHz
# the network's 130.0, 488.6, 713.5 and 1625.4 Hz poles read 1.269, 1.277, 1.286
# and 1.357 dB, a 0.09 dB spread. Choosing among them by that spread would be
# FITTING dressed as enumeration -- the exact trap #102 records for Q. The
# restriction is stated here, before the choice, so the rule cannot be the
# result. It always admits the empty realisation (step 5's "the two stages
# cancel"), so it can never make a band unrealisable on its own.
POLE_MARGIN_OCT = 1.0
# A fixed register set must cost no more than this much shape error against
# letting the pole track alpha, or TONE would need a coefficient rewrite per
# position (which the RTL deadlines would then have to carry).
TRACK_TOL_DB = 0.25
# TONE has to be load-bearing at all: the knob must move some band's level by
# more than this over its rotation, or the law is not testable.
SPAN_MIN_DB = 6.0

# The register map, contract 15.1. `A_RESET` is decoded BEFORE the mode range,
# so a mode whose `num` register lands on 0xFF cannot have a numerator written
# -- which is harmless for a mode at or above N_NUMS, because `ModalFx.step`
# never reads `num` there. This is what makes mode 19 the LAST addressable
# mode and mode 20 unreachable, and it is asserted rather than assumed.
MAP_LAST_MODE = (dx.A_RESET - dx.A_MODE) // dx.MODE_STRIDE      # 19


class Refused(RuntimeError):
    """A precondition this instrument needs is not met. Not a failed check."""


# ---------------------------------------------------------------------------
# the configuration, and the defects that transform it
# ---------------------------------------------------------------------------
DEFECTS = ("NO_TONE_NETWORK", "ALPHA_INVERTED", "RAIL_LOW_ON_TOP",
           "RAIL_SWAP_HT2_HT3", "POLE_FROM_FIG9", "NO_LEVEL_STAGE", "SIGN_A1")
BLIND_BY_CONSTRUCTION = ("ALPHA_INVERTED",)
PROPERTIES = ("nodal-grounded", "fig9-window-blind", "rev3-low-out-of-bound",
              "chosen-in-bound", "poles-from-network", "poles-in-band",
              "one-register-set", "budget-addressable", "tone-load-bearing",
              "short-band-monotone")
# (defect, property) pairs asserted BLIND and verified so. Inverting the wiper
# is blind to every quantity that is anchored at TONE 50, because 1 - 0.5 = 0.5:
# the anchor maps to itself. That is a real blindness of the anchored
# measurement and it is why `short-band-monotone` exists as a separate property.
BLIND_PAIRS = {("ALPHA_INVERTED", "nodal-grounded"),
               ("ALPHA_INVERTED", "fig9-window-blind"),
               ("ALPHA_INVERTED", "rev3-low-out-of-bound"),
               ("ALPHA_INVERTED", "budget-addressable")}


def config(*, defect=None) -> dict:
    cfg = {"rails": dict(RAILS), "mapping": "linear", "tone_flat": False,
           "level": True, "pole_source": "network", "sign_a1": 1}
    if defect == "NO_TONE_NETWORK":
        cfg["tone_flat"] = True
    elif defect == "ALPHA_INVERTED":
        cfg["mapping"] = "inverted"
    elif defect == "RAIL_LOW_ON_TOP":
        cfg["rails"] = _swap(cfg["rails"], "low", "short")
    elif defect == "RAIL_SWAP_HT2_HT3":
        cfg["rails"] = _swap(cfg["rails"], "decay", "short")
    elif defect == "POLE_FROM_FIG9":
        cfg["pole_source"] = "fig9"
    elif defect == "NO_LEVEL_STAGE":
        cfg["level"] = False
    elif defect == "SIGN_A1":
        cfg["sign_a1"] = -1
    elif defect is not None:
        raise ValueError(defect)
    return cfg


def _swap(d, a, b):
    out = dict(d)
    out[a], out[b] = out[b], out[a]
    return out


def alpha_of(code, cfg) -> float:
    """VR4's wiper fraction at TONE `code`, through step 9's law."""
    return ctk.alpha_of(FRAC[code], mapping=cfg["mapping"])


# ---------------------------------------------------------------------------
# the target: (tone network x LEVEL stage), per band, at one wiper position
# ---------------------------------------------------------------------------
def nodal_db(band, hz, alpha, cfg) -> np.ndarray:
    """The network's response from `band`'s source to the mix node, in dB."""
    if cfg["tone_flat"]:
        return np.zeros(len(np.atleast_1d(hz)))
    return 20.0 * np.log10(np.maximum(
        np.abs(ts.solve_vtone(hz, alpha, cfg["rails"][band])), 1e-30))


def level_db(hz, cfg) -> np.ndarray:
    """The LEVEL buffer's single-pole differentiator, W14b Figure 10."""
    if not cfg["level"]:
        return np.zeros(len(np.atleast_1d(hz)))
    s = 2j * np.pi * np.asarray(hz, dtype=float)
    w = 2 * math.pi * tr.level_corner_hz()
    return 20.0 * np.log10(np.maximum(np.abs(s / (s + w)), 1e-30))


def target_db(band, hz, alpha, cfg) -> np.ndarray:
    return nodal_db(band, hz, alpha, cfg) + level_db(hz, cfg)


def fig9_target_db(band, hz, cfg, poles=None) -> np.ndarray:
    """Step 5's target: Figure 9's per-band 2-pole window fit x the LEVEL stage.
    Kept so the two routes can be compared rather than one silently replacing
    the other."""
    p = poles if poles is not None else tr.tone_poles()
    return tr.analog_target_db(band, hz, p, tr.level_corner_hz(),
                               level=cfg["level"])


# ---------------------------------------------------------------------------
# what the bank can hold, enumerated
# ---------------------------------------------------------------------------
def network_poles(alpha) -> tuple:
    """The network's own five pole frequencies, in Hz. Shared by all three
    paths -- one network, one denominator."""
    return tuple(float(v) for v in ts.poles_hz(alpha))


def realised_db(hz, poles, dc_zeros, *, sign=1) -> np.ndarray:
    """One bank sub-chain's transfer function from its registers as written:
    `(1 - z^-1)^dc_zeros / prod(1 - p_i z^-1)`, at most two real poles."""
    z = np.exp(-2j * np.pi * np.asarray(hz, dtype=float) / SR)
    if len(poles) > 2:
        raise Refused(f"a bank section holds at most two poles, got {len(poles)}")
    a1, a2 = cc.real_pole_regs(poles) if poles else (0, 0)
    a1 *= sign
    den = 1 - (a1 / (1 << 24)) * z - (a2 / (1 << 24)) * z * z
    return 20.0 * np.log10(np.maximum(
        np.abs((1 - z) ** dc_zeros / den), 1e-30))


def active_hz(band) -> tuple:
    """The band's active range's low and high edges, in Hz."""
    hz = tr.THIRDS[active_mask(band)]
    return float(hz[0]), float(hz[-1])


def admissible(band, f_hz) -> bool:
    """Is a pole at `f_hz` distinguishable over this band? See POLE_MARGIN_OCT."""
    lo, hi = active_hz(band)
    return (lo / 2.0 ** POLE_MARGIN_OCT) <= f_hz <= (hi * 2.0 ** POLE_MARGIN_OCT)


def active_mask(band) -> np.ndarray:
    """Where the band has energy: its OWN filters within ACTIVE_DB of their
    peak. `cymbal_tone_realisation.band_chain_db`'s convention, imported rather
    than restated -- moving it would be a rubric change mid-selection."""
    chain = tr.band_chain_db(band, tr.THIRDS)
    return chain >= chain.max() - tr.ACTIVE_DB


def shape_error_db(band, alpha, poles, dc_zeros, cfg, *, target=None) -> float:
    """The realisation's worst shape deviation from the target over the band's
    active range, in dB, with any uniform offset removed.

    Offset-free on purpose: the candidate's level rule matches each band's
    absolute energy in one 1/3 octave, so a uniform gain error is absorbed by
    the render and a SHAPE error is not. Same construction as
    `cymbal_tone_realisation.shape_error`, which is the frozen convention.
    """
    hz = tr.THIRDS
    t = target if target is not None else target_db(band, hz, alpha, cfg)
    g = realised_db(hz, poles, dc_zeros, sign=cfg["sign_a1"])
    chain = tr.band_chain_db(band, hz)
    act = active_mask(band)
    ref = float(np.interp(tr.CENTRE_HZ[band], hz, t))
    dev = (g - float(np.interp(tr.CENTRE_HZ[band], hz, g))) - (t - ref)
    w = (10.0 ** (chain / 10.0)) * act
    dev = dev - float(np.sum(w * dev) / np.sum(w))
    return float(np.max(np.abs(dev[act])))


# WHAT THE SUB-CHAIN UNDER TEST IS, band by band. `tr.band_chain_db` accounts
# for each band's OWN filters -- its Q 6 band-pass, its Sallen-Key high-pass,
# and (short band only) Hh3's third pole together with ONE of its register's two
# zeros. Everything else the bank writes for that band is the realisation of
# (tone x LEVEL), and that is what the bound applies to. Getting this split
# wrong double-counts Hh3's pole: an earlier draft of this module did exactly
# that and read revision 3's short band at 1.55 dB where step 5 recorded 1.35.
#
#   low    Hh1 is a 2-pole COMPLEX pair on M_CYH1: both slots used, so a tone
#          pole needs a NEW mode and a NEW path.
#   decay  Hh2 likewise, on M_CYHI.
#   short  Hh3's 1-pole stage M_CYH3B holds its own 5195 Hz pole and has ONE
#          free slot, so a tone pole there costs no section at all.
BASE_ZEROS = {"low": 0, "decay": 0, "short": 1}
FREE_SLOTS = {"low": 2, "decay": 2, "short": 1}
NEW_SECTION = {"low": True, "decay": True, "short": False}


def enumerate_realisations(band, alpha, cfg):
    """Every realisation the bank can hold for this band, as (poles, dc_zeros).

    `poles` are drawn from the network's own pole set at `alpha` (or, under the
    POLE_FROM_FIG9 defect, from Figure 9's per-band 2-pole fit), never fitted.
    `dc_zeros` is the discrete choice of whether the sub-chain carries one MORE
    DC zero than its current numerator code provides -- step 5's "the two stages
    cancel" decision, made enumerable instead of argued. It is not free: the
    extra zero is the `HP3 = (1 - z^-1)^3` numerator that revision 3 removed
    from the bank, so taking it back costs the shared `modal_dp.v` decode
    (`cost` below counts it).
    """
    if cfg["pole_source"] == "fig9":
        src = tuple(float(v) for v in tr.tone_poles()[band])
    else:
        src = network_poles(alpha)
    src = tuple(f for f in src if admissible(band, f))
    out = []
    for k in range(FREE_SLOTS[band] + 1):
        for sub in itertools.combinations(range(len(src)), k):
            poles = tuple(round(src[i], 1) for i in sub)
            for extra_zero in (0, 1):
                out.append((poles, BASE_ZEROS[band] + extra_zero))
    return out


def realisation_cost(band, poles, dc_zeros) -> dict:
    """What the bank pays for this realisation, in the two currencies that
    matter: new bank sections (a mode and a path each) and the HP3 decode."""
    sections = 1 if (poles and NEW_SECTION[band]) else 0
    return {"new_sections": sections,
            "hp3_decode": int(dc_zeros > BASE_ZEROS[band]),
            "extra_poles": len(poles)}


def choose(band, cfg, *, anchor=ANCHOR) -> dict:
    """The most faithful realisation inside SHAPE_BOUND_DB at EVERY TONE
    position, among those of least cost.

    Ordering: inside the bound first, then cost (new sections + the HP3 decode),
    then worst-case shape error, then fewest poles. Faithfulness breaks ties at
    equal cost rather than the other way round -- a pole the circuit HAS is not
    dropped to save a coefficient write that costs nothing, which is why the
    short band takes the network's pole into its free slot. Poles are FIXED at
    the anchor's wiper position; `one-register-set` is the property that says
    that costs nothing. REFUSES when nothing the bank can hold clears the bound
    at all five positions.
    """
    a_anchor = alpha_of(anchor, cfg)
    targets = {c: target_db(band, tr.THIRDS, alpha_of(c, cfg), cfg) for c in CODES}
    best, rejected = None, 0
    for poles, dcz in enumerate_realisations(band, a_anchor, cfg):
        try:
            errs = {c: shape_error_db(band, alpha_of(c, cfg), poles, dcz, cfg,
                                      target=targets[c]) for c in CODES}
        except (Refused, ValueError):
            rejected += 1
            continue
        worst = max(errs.values())
        cost = realisation_cost(band, poles, dcz)
        key = (worst > SHAPE_BOUND_DB,
               cost["new_sections"] + cost["hp3_decode"], worst, cost["extra_poles"])
        if best is None or key < best[0]:
            best = (key, {"band": band, "poles": [round(p, 1) for p in poles],
                          "dc_zeros": dcz, **cost,
                          "worst_db": round(worst, 3),
                          "per_tone_db": {c: round(errs[c], 3) for c in CODES}})
    if best is None:
        raise Refused(f"{band}: no enumerated realisation at all ({rejected} rejected)")
    got = best[1]
    if got["worst_db"] > SHAPE_BOUND_DB:
        raise Refused(f"{band}: the best realisation the bank can hold is "
                      f"{got['worst_db']:.2f} dB, outside the {SHAPE_BOUND_DB} dB bound")
    return got


def chosen(cfg=None) -> dict:
    cfg = cfg if cfg is not None else config()
    return {b: choose(b, cfg) for b in BANDS}


def rev3_realisation(band, cfg) -> dict:
    """What revision 3 hands the bank, measured against the NODAL target."""
    extra = cc.TONE_REALISATION[band]["extra_pole_hz"]
    poles = (round(float(extra), 1),) if extra else ()
    dcz = BASE_ZEROS[band]
    errs = {c: shape_error_db(band, alpha_of(c, cfg), poles, dcz, cfg) for c in CODES}
    fig = {c: shape_error_db(band, alpha_of(c, cfg), poles, dcz, cfg,
                             target=fig9_target_db(band, tr.THIRDS, cfg)) for c in CODES}
    return {"band": band, "poles": [round(p, 1) for p in poles], "dc_zeros": dcz,
            "worst_nodal_db": round(max(errs.values()), 3),
            "worst_fig9_db": round(max(fig.values()), 3),
            "per_tone_nodal_db": {c: round(errs[c], 3) for c in CODES}}


# ---------------------------------------------------------------------------
# the TONE gain table -- the knob's dominant action
# ---------------------------------------------------------------------------
def tone_gain_db(cfg=None, *, anchor=ANCHOR) -> dict:
    """{code: {band: dB relative to the anchor}} at each band's own calibration
    centre (`cymbal_candidate_eval.CENTRE`, where the level rule matches it).

    This is the number the render applies to a band's amp/envelope-peak
    register, and it is the whole of the TONE knob in the candidate: the SHAPE
    change with alpha is second order and is what `chosen-in-bound` bounds.
    """
    cfg = cfg if cfg is not None else config()
    a0 = alpha_of(anchor, cfg)
    ref = {b: float(nodal_db(b, np.array([tr.CENTRE_HZ[b]]), a0, cfg)[0]) for b in BANDS}
    out = {}
    for c in CODES:
        a = alpha_of(c, cfg)
        out[c] = {b: round(float(nodal_db(b, np.array([tr.CENTRE_HZ[b]]), a, cfg)[0])
                           - ref[b], 3) for b in BANDS}
    return out


# ---------------------------------------------------------------------------
# the budget, exactly
# ---------------------------------------------------------------------------
def budget(ch=None) -> dict:
    """Modes, paths and N_NUMS the chosen realisation costs, and whether the
    8-bit register map can address them."""
    ch = ch if ch is not None else chosen()
    new = sum(v["new_sections"] for v in ch.values())
    modes, paths = cc.N_MODES + new, cc.N_PATH + new
    last = modes - 1
    num_addr = dx.A_MODE + last * dx.MODE_STRIDE + 3
    return {
        "modes": modes, "paths": paths, "nums": cc.N_NUMS,
        "new_sections": new,
        "last_mode": last,
        "last_mode_num_addr": num_addr,
        "last_mode_num_writable": num_addr != dx.A_RESET,
        "last_mode_needs_num": last < cc.N_NUMS,
        "map_last_addressable_mode": MAP_LAST_MODE,
        "addressable": last <= MAP_LAST_MODE and not (
            num_addr == dx.A_RESET and last < cc.N_NUMS),
        "bank_pad": 32,
    }


# ---------------------------------------------------------------------------
# the properties
# ---------------------------------------------------------------------------
def properties(cfg=None) -> dict:
    cfg = cfg if cfg is not None else config()
    out = {}

    # 1. the nodal route is grounded on the one band Figure 9 fully plots.
    hz, act = tr.THIRDS, active_mask("short")
    a0 = alpha_of(ANCHOR, cfg)
    n = target_db("short", hz, a0, cfg)
    f = fig9_target_db("short", hz, cfg)
    n = n - float(np.interp(tr.CENTRE_HZ["short"], hz, n))
    f = f - float(np.interp(tr.CENTRE_HZ["short"], hz, f))
    d_short = float(np.max(np.abs((n - f)[act])))
    out["nodal-grounded"] = {"value_db": round(d_short, 3), "bound_db": CROSS_BOUND_DB,
                             "ok": d_short <= CROSS_BOUND_DB,
                             "what": "Ht3 (plotted 20 Hz-20 kHz) agrees between Figure 9 and the nodal network"}

    # 2. ...and the low band's window is blind, which is why step 5's target was wrong there.
    act_l = active_mask("low")
    nl = target_db("low", hz, a0, cfg)
    fl = fig9_target_db("low", hz, cfg)
    nl = nl - float(np.interp(tr.CENTRE_HZ["low"], hz, nl))
    fl = fl - float(np.interp(tr.CENTRE_HZ["low"], hz, fl))
    dl = (nl - fl)[act_l]
    d_low = float(np.max(np.abs(dl)))
    # BOTH constructions are recorded, because prose has already confused them:
    # step 10's write-up quoted 5.62 dB for this property, which is neither the
    # de-trended maximum the property measures (4.61 dB) nor exactly the
    # peak-to-peak span over the same range (5.59 dB). A number a document can
    # only get by re-deriving it by hand is a number that will drift, so the
    # span is computed here and cited by name rather than described in words.
    span_low = float(np.max(dl) - np.min(dl))
    out["fig9-window-blind"] = {"value_db": round(d_low, 3), "bound_db": CROSS_BOUND_DB,
                                "span_db": round(span_low, 3),
                                "ok": d_low > CROSS_BOUND_DB,
                                "what": "Ht1's 121-564 Hz window DISAGREES with the network over 2-8 kHz"}

    # 3. revision 3's low band is outside the bound at every TONE position.
    r3 = {b: rev3_realisation(b, cfg) for b in BANDS}
    worst_low = min(r3["low"]["per_tone_nodal_db"].values())
    out["rev3-low-out-of-bound"] = {
        "value_db": round(worst_low, 3), "bound_db": SHAPE_BOUND_DB,
        "ok": worst_low > SHAPE_BOUND_DB,
        "what": "revision 3's low band exceeds its own bound at EVERY TONE position (min over the five)"}

    # 4. the chosen realisation is inside the bound everywhere.
    try:
        ch = chosen(cfg)
        worst = max(v["worst_db"] for v in ch.values())
        ok = worst <= SHAPE_BOUND_DB
    except Refused as exc:
        ch, worst, ok = None, None, False
        out["chosen-in-bound"] = {"value_db": None, "bound_db": SHAPE_BOUND_DB,
                                  "ok": False, "refused": str(exc),
                                  "what": "every band inside the bound at every TONE position"}
    if ch is not None:
        out["chosen-in-bound"] = {"value_db": round(worst, 3), "bound_db": SHAPE_BOUND_DB,
                                  "ok": ok,
                                  "what": "every band inside the bound at every TONE position"}

    # 5. every pole is one of the network's own.
    net = network_poles(a0)
    worst_hz, n_poles = 0.0, 0
    if ch is not None:
        for v in ch.values():
            for p in v["poles"]:
                n_poles += 1
                worst_hz = max(worst_hz, min(abs(p - q) for q in net))
    out["poles-from-network"] = {"value_hz": round(worst_hz, 3), "bound_hz": POLE_TOL_HZ,
                                "n_tone_poles": n_poles,
                                "ok": ch is not None and n_poles > 0 and worst_hz <= POLE_TOL_HZ,
                                "what": "no tone pole is fitted: each is a pole of the network itself"}

    # 5b. ...and every pole is distinguishable over the band it acts on.
    worst_oct, n_in = 0.0, 0
    if ch is not None:
        for b, v in ch.items():
            lo, hi = active_hz(b)
            for p in v["poles"]:
                n_in += 1
                worst_oct = max(worst_oct, max(math.log2(lo / p) if p < lo else 0.0,
                                               math.log2(p / hi) if p > hi else 0.0))
    out["poles-in-band"] = {"value_oct": round(worst_oct, 3), "bound_oct": POLE_MARGIN_OCT,
                           "n_poles": n_in,
                           "ok": ch is not None and worst_oct <= POLE_MARGIN_OCT,
                           "what": "no pole sits so far outside its band that the choice is asymptotic"}

    # 6. one fixed register set covers the whole knob: letting each pole follow
    #    the network's own movement with alpha must not buy more than
    #    TRACK_TOL_DB, or TONE would need a coefficient rewrite per position and
    #    the RTL deadlines would have to carry it.
    #
    #    How far the top pole actually moves is recorded rather than written
    #    into prose, because the two available answers differ and a document
    #    that quotes one cannot say which: over the five TONE codes the wiper
    #    law clamps alpha to 0.001..0.999 (4132.2 -> 4712.0 Hz), while the
    #    ideal full rotation alpha 0 -> 1 -- which no TONE code selects -- gives
    #    4132.1 -> 4715.1 Hz. `codes_hz` is the one the knob can reach and the
    #    one this property is evaluated over.
    tops = [max(network_poles(alpha_of(c, cfg))) for c in CODES]
    top_pole = {"codes_hz": [round(tops[0], 1), round(tops[-1], 1)],
                "rotation_hz": [round(max(network_poles(0.0)), 1),
                                round(max(network_poles(1.0)), 1)]}
    gain, detail = 0.0, {}
    if ch is not None:
        for b, v in ch.items():
            fixed = max(v["per_tone_db"].values())
            track = max(shape_error_db(b, alpha_of(c, cfg), _tracked(b, v, c, cfg),
                                       v["dc_zeros"], cfg) for c in CODES)
            detail[b] = {"fixed_db": round(fixed, 3), "tracking_db": round(track, 3)}
            gain = max(gain, fixed - track)
    out["one-register-set"] = {"value_db": round(gain, 3), "bound_db": TRACK_TOL_DB,
                              "per_band": detail, "top_pole_hz": top_pole,
                              "ok": ch is not None and gain <= TRACK_TOL_DB,
                              "what": "fixing the pole at the anchor costs no more than letting it track alpha"}

    # 7. the budget is addressable by the register map.
    bud = budget(ch) if ch is not None else None
    out["budget-addressable"] = {"value": bud, "ok": bool(bud and bud["addressable"]),
                                "what": "every mode the realisation needs has a writable register set"}

    # 8/9. TONE is load-bearing, and the band it mainly attenuates moves monotonically.
    g = tone_gain_db(cfg)
    spans = {b: max(g[c][b] for c in CODES) - min(g[c][b] for c in CODES) for b in BANDS}
    out["tone-load-bearing"] = {"value_db": round(max(spans.values()), 3),
                                "bound_db": SPAN_MIN_DB, "spans_db": spans,
                                "ok": max(spans.values()) >= SPAN_MIN_DB,
                                "what": "the knob moves some band's level by more than the bound"}
    seq = [g[c]["short"] for c in CODES]
    rises = all(b - a > 0 for a, b in zip(seq, seq[1:]))
    out["short-band-monotone"] = {"value_db": [round(v, 2) for v in seq],
                                 "ok": rises,
                                 "what": "W14b: TONE 'mainly attenuates the third (highest) band' -- monotone in TONE"}
    # Honesty about the matrix's density: when `chosen` REFUSES there is no
    # realisation to interrogate, so the five properties that read off it are
    # UNDECIDED rather than independently red. Counting them as five catches
    # would overstate the controls -- condition 3 of verification rule 5.
    if ch is None:
        for k in ("poles-from-network", "poles-in-band", "one-register-set",
                  "budget-addressable"):
            out[k]["undecided"] = "chosen() refused; nothing to check"
    for k in out:
        out[k]["property"] = k
        out[k].setdefault("undecided", None)
    return out


def _tracked(band, ch, code, cfg):
    """`ch`'s poles with each moved to the network's pole at this TONE position
    (the same index in the sorted pole set)."""
    a0, a = alpha_of(ANCHOR, cfg), alpha_of(code, cfg)
    n0, n1 = network_poles(a0), network_poles(a)
    out = []
    for p in ch["poles"]:
        i = int(np.argmin([abs(p - q) for q in n0]))
        out.append(round(n1[i], 1))
    return tuple(out)


def check(cfg=None) -> tuple[bool, list[str]]:
    lines, ok = [], True
    base = properties(cfg)
    for name in PROPERTIES:
        p = base[name]
        ok = ok and bool(p["ok"])
        lines.append(f"{'PASS' if p['ok'] else 'FAIL'}  {name:24s} {p['what']}")
    lines.append("")
    lines.append("properties x defects -- every injected defect must turn at least one red")
    hdr = "  " + " ".join(f"{n[:9]:>10s}" for n in PROPERTIES)
    lines.append(f"{'defect':22s}{hdr}")
    for d in DEFECTS:
        try:
            p = properties(config(defect=d))
            row = ["." if p[n]["ok"] else ("n/a" if p[n]["undecided"] else "RED")
                   for n in PROPERTIES]
        except Refused:
            row = ["REF"] * len(PROPERTIES)
        moved = sum(1 for v in row if v == "RED")
        if moved == 0:
            ok = False
            lines.append(f"{d:22s}  turned NO property red -- a control that cannot fail")
            continue
        lines.append(f"{d:22s}" + "  " + " ".join(f"{v:>10s}" for v in row))
        for dd, pp in BLIND_PAIRS:
            if dd == d and row[PROPERTIES.index(pp)] == "RED":
                ok = False
                lines.append(f"  !! {d} was asserted BLIND to {pp} and moved it")
    lines.append("")
    lines.append("'n/a' = the property reads off a realisation that chosen() refused, so it is "
                 "UNDECIDED, not an independent catch.")
    return ok, lines


# ---------------------------------------------------------------------------
# reporting
# ---------------------------------------------------------------------------
def report(cfg=None) -> list[str]:
    cfg = cfg if cfg is not None else config()
    out = [f"anchor TONE {ANCHOR} (alpha {alpha_of(ANCHOR, cfg):.4f}); "
           f"bound {SHAPE_BOUND_DB} dB; codes {'/'.join(CODES)}", ""]
    out.append("revision 3's realisation, against BOTH targets (worst over the five TONE positions):")
    out.append(f"  {'band':6s} {'poles (Hz)':22s} {'zeros':>5s} {'vs Figure 9':>12s} {'vs nodal':>10s}")
    for b in BANDS:
        r = rev3_realisation(b, cfg)
        out.append(f"  {b:6s} {str(r['poles']):22s} {r['dc_zeros']:5d} "
                   f"{r['worst_fig9_db']:12.2f} {r['worst_nodal_db']:10.2f}"
                   + ("   <-- OUTSIDE BOUND" if r["worst_nodal_db"] > SHAPE_BOUND_DB else ""))
    out.append("")
    try:
        ch = chosen(cfg)
    except Refused as exc:
        out.append(f"REFUSED: {exc}")
        return out
    out.append("the chosen realisation (poles from the network's own pole set, fixed at the anchor):")
    out.append(f"  {'band':6s} {'poles (Hz)':22s} {'zeros':>5s} {'new':>4s} {'worst':>7s}  per-TONE")
    for b in BANDS:
        v = ch[b]
        out.append(f"  {b:6s} {str(v['poles']):22s} {v['dc_zeros']:5d} {v['new_sections']:4d} "
                   f"{v['worst_db']:7.2f}  " + " ".join(f"{c}:{v['per_tone_db'][c]:.2f}" for c in CODES))
    out.append("")
    bud = budget(ch)
    out.append(f"budget: {cc.N_MODES} -> {bud['modes']} modes, {cc.N_PATH} -> {bud['paths']} paths, "
               f"N_NUMS {bud['nums']}; bank pads to {bud['bank_pad']}")
    out.append(f"        mode {bud['last_mode']} is the last; its num register is "
               f"0x{bud['last_mode_num_addr']:02X}"
               + (" = A_RESET, UNWRITABLE" if not bud["last_mode_num_writable"] else "")
               + f"; it needs a numerator: {bud['last_mode_needs_num']}")
    out.append(f"        the 8-bit map addresses modes 0..{bud['map_last_addressable_mode']}; "
               f"addressable: {bud['addressable']}")
    out.append("")
    g = tone_gain_db(cfg)
    out.append("TONE gain per band, dB relative to the anchor, at each band's calibration centre:")
    out.append(f"  {'TONE':>5s} " + " ".join(f"{b:>9s}" for b in BANDS))
    for c in CODES:
        out.append(f"  {FRAC[c] * 100:5.0f} " + " ".join(f"{g[c][b]:9.2f}" for b in BANDS))
    return out


def record(cfg=None) -> dict:
    cfg = cfg if cfg is not None else config()
    ok, lines = check(cfg)
    try:
        ch = chosen(cfg)
        bud = budget(ch)
    except Refused as exc:
        ch, bud = {"refused": str(exc)}, None
    return {"anchor": ANCHOR, "codes": list(CODES),
            "alpha": {c: round(alpha_of(c, cfg), 6) for c in CODES},
            "bounds": {"shape_db": SHAPE_BOUND_DB, "cross_db": CROSS_BOUND_DB,
                       "pole_hz": POLE_TOL_HZ, "track_db": TRACK_TOL_DB,
                       "span_db": SPAN_MIN_DB},
            "rev3": {b: rev3_realisation(b, cfg) for b in BANDS},
            "chosen": ch, "budget": bud,
            "tone_gain_db": tone_gain_db(cfg),
            "properties": properties(cfg), "check_ok": ok, "check": lines}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--report", action="store_true")
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--json", type=pathlib.Path, default=None)
    a = ap.parse_args(argv)
    if not (a.report or a.check or a.json):
        a.report = True
    rc_ = 0
    try:
        if a.report:
            print("\n".join(report()))
        if a.check:
            ok, lines = check()
            print("\n".join(lines))
            rc_ = 0 if ok else 1
        if a.json:
            a.json.parent.mkdir(parents=True, exist_ok=True)
            a.json.write_text(json.dumps(record(), indent=1, default=float) + "\n")
    except Refused as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 3
    return rc_


if __name__ == "__main__":
    raise SystemExit(main())
