#!/usr/bin/env python3
"""Render the #369 cymbal candidate across the TONE knob and measure it against the 808 (step 10).

ONE QUESTION, and step 9 (`docs/scorecard/cymbal-369/tone-knob/`) is the step
that asked it:

    Does a candidate driven by VR4's own wiper law track the 808 as TONE moves?

Step 9 answered the *circuit's* half analytically -- with one inter-band balance
free, the nodal network reproduces the machine's H - L versus TONE to 0.33 dB --
and said explicitly that applying it to a candidate needs a render, the frozen
development/confirmation split and the hats' preservation set. This is that
render. Nothing here is fitted to a recording: the knob law is `alpha = TONE/100`
off the pot's "20K(B)" marking, the per-band levels it implies come from the
nodal network at each band's own calibration centre, and the absolute levels come
from the shipped kit.

ORDER, FIXED BEFORE ANY NUMBER WAS READ.
 1. PRECONDITION. `tools/cymbal_tone_nodal.py --check` must pass, and the
    realisation it chooses must be the one `model/cymbal_candidate.TONE_R4`
    writes. REFUSES otherwise: a render of a realisation nobody validated is
    not evidence.
 2. LEVELS, at the ANCHOR TONE only, by the candidate's own rule -- each band
    matched to the shipped kit's same band in the 1/3 octave at its centre
    (3175 / 10079 / 10079 Hz), first second of a strike. The rule references
    the shipped kit and never a recording.
 3. TONE. Each band's level is then scaled by the nodal network's own gain at
    that band's centre, relative to the anchor. REFUSES when the register
    cannot carry it to within QUANT_TOL_DB.
 4. PRESERVATION. Every one of the 16 sounds other than CY must render
    bit-identically to the shipped kit on the 20-mode layout, at every TONE
    position. Hats included -- D15A, D16A and the OH settings share the
    7.1 kHz band-pass, which is acceptance item 4.
 5. THE MEASUREMENT, with the frozen instrument (`tools/cymbal_bands.measure`):
    H - L, H's EDT10 and Ln's EDT10 at all five TONE positions, ANCHORED at
    TONE 50 so the unresolved inter-band balance (#396) cancels as far as it
    can, against the 808's own anchored curves in all five DECAY columns
    (`docs/scorecard/cymbal-369/fischer.json`, measured by the same instrument).

WHAT IS NOT CLAIMED. The DECAY knob's law -- VR2 sits on the other rail and step
9 says nothing about it -- so the render sits at the shipped kit's one DECAY and
is compared against every DECAY column. The spread across those columns is the
measured bound on that confound, not an assumption: the 808's anchored H - L
agrees across all five columns to 0.5 dB.
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
import drums_fx as dx                    # noqa: E402
import run_case as rc                    # noqa: E402
import cymbal_candidate as cc            # noqa: E402
import cymbal_bands as cb                # noqa: E402
import cymbal_tone_knob as ctk           # noqa: E402
import cymbal_tone_nodal as tn           # noqa: E402

CENTRE = {"low": "3175", "decay": "10079", "short": "10079"}
# Revision 4's low band leaves the chain at M_CYH1B, not M_CYH1.
LEVEL_MODE = {"low": cc.M_CYH1B, "decay": dx.M_CYHI, "short": cc.M_CYH3B}
LEVEL_ENV = {"low": dx.E_CYL, "decay": dx.E_CYD, "short": dx.E_CYS}
AMP_MAX = 65535 / 65536
CODES = tuple(tn.CODES)
ANCHOR = tn.ANCHOR

# ---- bounds, stated before the render -------------------------------------
# H - L is the quantity step 9 bounded, so its bound is step 9's, unchanged.
BOUND_DB = ctk.BOUND_DB                       # 3.0
# docs/audio-distance-metrics.md's tolerance for a time constant, via step 9.
TIME_TOL = ctk.TIME_TOL                       # 0.50
# A register must carry the level TONE asks for to this accuracy, or the tool
# refuses rather than reporting a level it did not realise.
QUANT_TOL_DB = 0.2
# The machine's own Ln EDT10 moves +-3 % across TONE. Ours must not move more
# than the time tolerance, and the figure is reported either way.
LN_INVARIANCE = TIME_TOL


class Refused(RuntimeError):
    """A precondition this render needs is not met. Not a failed check."""


# ---------------------------------------------------------------------------
# 1. the precondition
# ---------------------------------------------------------------------------
def assert_realisation_is_the_validated_one() -> dict:
    ok, lines = tn.check()
    if not ok:
        raise Refused("tools/cymbal_tone_nodal.py --check does not pass:\n" + "\n".join(lines))
    chosen = tn.chosen()
    for band, v in chosen.items():
        want = cc.TONE_R4[band]["pole_hz"]
        got = v["poles"][0] if v["poles"] else None
        if (want is None) != (got is None) or (want is not None and abs(want - got) > 1e-6):
            raise Refused(f"{band}: the model writes pole {want} and the validated "
                          f"realisation is {got}")
    gain = tn.tone_gain_db()
    for code in CODES:
        for band in tn.BANDS:
            a, b = cc.TONE_GAIN_DB[code][band], gain[code][band]
            if abs(a - b) > 1e-3:
                raise Refused(f"TONE {code} {band}: model records {a} dB, tool computes {b} dB")
    bud = tn.budget(chosen)
    if (bud["modes"], bud["paths"]) != (cc.N_MODES_R4, cc.N_PATH_R4):
        raise Refused(f"budget mismatch: tool {bud['modes']}/{bud['paths']}, "
                      f"model {cc.N_MODES_R4}/{cc.N_PATH_R4}")
    if not bud["addressable"]:
        raise Refused(f"the realisation needs mode {bud['last_mode']}, which the "
                      f"8-bit register map cannot address")
    return {"chosen": chosen, "budget": bud, "check_ok": ok}


# ---------------------------------------------------------------------------
# 2/3. levels at the anchor, then TONE
# ---------------------------------------------------------------------------
def _abs_third(y, sr, fc):
    from scipy.signal import butter, sosfiltfilt
    f = float(fc)
    lo, hi = f / 2 ** (1 / 6), min(f * 2 ** (1 / 6), 0.45 * sr)
    o = int(0.01 * sr)                       # the strike frame (480): the first second after it
    x = sosfiltfilt(butter(4, [lo, hi], btype="bandpass", fs=sr, output="sos"), y)[o:o + sr]
    return float(np.sum(x * x))


def render_r4(kit, sound="CY", seconds=4.0):
    return cc.render(kit, sound, seconds=seconds,
                     modes=cc.N_MODES_R4, paths=cc.N_PATH_R4)


def calibrate() -> dict:
    """The candidate's own level rule, at the ANCHOR TONE. Identical in form to
    `tools/cymbal_candidate_eval.calibrate`, on the revision-4 structure."""
    shipped = dx.kit_808()
    unit = cc.candidate_kit({cc.M_CYH1B: 0.25, dx.M_CYHI: 0.25, cc.M_CYH3B: 0.25},
                            tone=ANCHOR)
    out, amps = {}, {}
    for band in ("low", "decay", "short"):
        ys, sr = cc.render_shipped(cc.band_only(shipped, band), "CY")
        yc, _ = render_r4(cc.band_only(unit, band))
        es, ec = _abs_third(ys, sr, CENTRE[band]), _abs_third(yc, sr, CENTRE[band])
        if ec <= 0:
            raise Refused(f"the {band} band renders no energy at its own centre")
        amps[band] = 0.25 * math.sqrt(es / ec)
        out[band] = {"shipped_abs": es, "unit_abs": ec, "anchor_level": amps[band]}
    return {"per_band": out, "anchor_level": amps}


def levels_at(anchor_level: dict, code: str) -> dict:
    """The three band levels at TONE `code`, split between the Q0.16 amp
    register and the envelope's Q24 peak, and REFUSED if the pair cannot carry
    the level the knob asks for."""
    g = cc.tone_gains(code)
    base_peak = {b: dict(dx.kit_808())[dx.A_ENV + LEVEL_ENV[b] * dx.ENV_STRIDE + 1] / dx.FULL24
                 for b in anchor_level}
    amps, detail = {}, {}
    for band, lvl0 in anchor_level.items():
        want = lvl0 * g[band]
        amp, env = want, 1.0
        if amp > AMP_MAX:
            amp, env = AMP_MAX, amp / AMP_MAX
        peak = base_peak[band] * env
        if peak > 1.0:
            raise Refused(f"TONE {code} {band}: needs envelope peak {peak:.3f} > 1.0")
        # what the registers actually realise, against what was asked
        got = (dx.amp_reg(amp) / 65536.0) * (dx.peak_reg(peak) / dx.FULL24) / base_peak[band]
        if got <= 0:
            raise Refused(f"TONE {code} {band}: the level underflows both registers")
        err = 20.0 * math.log10(got / want)
        if abs(err) > QUANT_TOL_DB:
            raise Refused(f"TONE {code} {band}: registers realise {err:+.2f} dB of the "
                          f"requested level (bound {QUANT_TOL_DB} dB)")
        amps[LEVEL_MODE[band]] = amp
        if env != 1.0:
            amps[f"E_{band}"] = peak
        detail[band] = {"requested_db": round(cc.TONE_GAIN_DB[code][band], 3),
                        "level": round(want, 8), "amp": round(amp, 8),
                        "env_peak": round(peak, 6), "register_error_db": round(err, 4)}
    return {"amps": amps, "detail": detail}


def kit_at(anchor_level: dict, code: str, sound="CY"):
    lv = levels_at(anchor_level, code)
    k = dict(cc.candidate_kit({m: v for m, v in lv["amps"].items() if isinstance(m, int)},
                              kit=dx.kit_with_sounds(sound), tone=code))
    for band, e in LEVEL_ENV.items():
        if f"E_{band}" in lv["amps"]:
            k[dx.A_ENV + e * dx.ENV_STRIDE + 1] = dx.peak_reg(lv["amps"][f"E_{band}"])
    return sorted(k.items()), lv["detail"]


# ---------------------------------------------------------------------------
# 4. preservation
# ---------------------------------------------------------------------------
def cy_owned_addresses() -> set:
    """Every register the cymbal owns on the revision-4 layout: its own six
    modes, its five paths and its three envelopes' peaks. Nothing outside this
    set may differ from the shipped image, and that is checked exactly rather
    than inferred from a render."""
    out = set()
    for m in (dx.M_CYBP, dx.M_CYHI, cc.M_CYH1, cc.M_CYH3, cc.M_CYH3B, cc.M_CYH1B):
        out |= {dx.A_MODE + m * dx.MODE_STRIDE + f for f in range(4)}
    for p in (cc.P_CYS, cc.P_CYD, cc.P_CYL, cc.P_CYH3, cc.P_CYH1B):
        out.add(dx.A_PATH + p)
    for e in LEVEL_ENV.values():
        out |= {dx.A_ENV + e * dx.ENV_STRIDE + f for f in range(4)}
    return out


def preservation_addresses(anchor_level: dict) -> dict:
    """Exact, and free: which addresses the candidate changes relative to the
    shipped image on the same layout, at every TONE position."""
    base = dict(cc.remap_kit(dx.kit_808()))
    owned, out = cy_owned_addresses(), {}
    for code in CODES:
        kit, _ = kit_at(anchor_level, code)
        got = dict(kit)
        # kit_at renders through kit_with_sounds("CY"), which mutes the other
        # stops; compare only the registers the candidate itself writes.
        diff = sorted(a for a in set(base) | set(got)
                      if a in owned or base.get(a) != got.get(a))
        stray = sorted(a for a in diff if a not in owned and a < dx.A_PATH)
        out[code] = {"n_changed_outside_cy": len(stray),
                     "stray": [f"0x{a:02X}" for a in stray]}
    return out


def preservation_renders(anchor_level: dict, codes, sounds=None, _cache={}) -> dict:
    """Bit-exact renders. The shipped side is TONE-independent and cached."""
    res = {}
    for code in codes:
        row = {}
        for s in (sounds or [x for x in dx.SOUND_NAMES if x != "CY"]):
            if s not in _cache:
                _cache[s] = rc.render_drum_solo(s)[0]
            a = _cache[s]
            kit, _ = kit_at(anchor_level, code, s)
            b, _ = render_r4(kit, s, seconds=rc.SOLO_SECONDS.get(s, 2.2))
            n = min(len(a), len(b))
            row[s] = bool(np.array_equal(a[:n], b[:n]) and len(a) == len(b))
        res[code] = row
    return res


def preservation(anchor_level: dict) -> dict:
    """Two complementary checks, because neither alone is enough.

    The ADDRESS check is exact and covers all five TONE positions: no register
    outside the cymbal's own is touched. The RENDER check proves bit-exactness
    where a shared register could still bite -- every one of the 16 other sounds
    at the anchor, and the two HATS at every TONE position, because OH and CH
    share the 7.1 kHz band-pass with the cymbal and the shipped kit routes the
    cymbal's short band through the CLOSED HAT's own high-pass M_CHHP (which the
    candidate re-routes). That is acceptance item 4.
    """
    addr = preservation_addresses(anchor_level)
    all_at_anchor = preservation_renders(anchor_level, (ANCHOR,))
    hats = preservation_renders(anchor_level, CODES, sounds=("OH", "CH"))
    return {"addresses": addr,
            "bit_exact_all_sounds_at_anchor": all_at_anchor[ANCHOR],
            "bit_exact_hats_every_tone": hats,
            "ok": (all(v["n_changed_outside_cy"] == 0 for v in addr.values())
                   and all(all_at_anchor[ANCHOR].values())
                   and all(all(r.values()) for r in hats.values()))}


# ---------------------------------------------------------------------------
# 5. the measurement
# ---------------------------------------------------------------------------
def fischer_curves(path: pathlib.Path | None = None) -> dict:
    """The 808's anchored curves, per DECAY column, off the committed record."""
    p = path or (ROOT / "docs" / "scorecard" / "cymbal-369" / "fischer.json")
    if not p.exists():
        raise Refused(f"{p} is absent; run tools/cymbal_bands.py --out {p}")
    blob = json.loads(p.read_text())
    out = {}
    for dec in CODES:
        rows = {}
        for t in CODES:
            k = f"CY{t}{dec}"
            if k not in blob:
                raise Refused(f"{p} carries no {k}")
            rows[t] = blob[k]
        hml = {t: rows[t]["H_minus_L_db"] for t in CODES}
        hed = {t: rows[t]["H"]["edt10_ms"] for t in CODES}
        lned = {t: rows[t]["Ln"]["edt10_ms"] for t in CODES}
        out[dec] = {
            "settings": {t: f"CY{t}{dec}" for t in CODES},
            "h_minus_l_db": hml,
            "h_minus_l_anchored_db": {t: round(hml[t] - hml[ANCHOR], 3) for t in CODES},
            "h_edt10_ms": hed,
            "h_edt10_ratio": {t: round(hed[t] / hed[ANCHOR], 4) for t in CODES},
            "ln_edt10_ms": lned,
            "ln_edt10_ratio": {t: round(lned[t] / lned[ANCHOR], 4) for t in CODES},
        }
    return out


def measure_render(anchor_level: dict) -> dict:
    out = {}
    for code in CODES:
        kit, detail = kit_at(anchor_level, code)
        y, sr = render_r4(kit)
        m = cb.measure(rc.prepare(y, sr, side=f"candidate TONE {code}"), sr)
        out[code] = {"levels": detail, "bands": m}
    hml = {c: out[c]["bands"]["H_minus_L_db"] for c in CODES}
    hed = {c: out[c]["bands"]["H"]["edt10_ms"] for c in CODES}
    lned = {c: out[c]["bands"]["Ln"]["edt10_ms"] for c in CODES}
    for name, d in (("h_minus_l_db", hml), ("h_edt10_ms", hed), ("ln_edt10_ms", lned)):
        if any(v is None for v in d.values()):
            raise Refused(f"the frozen instrument refused {name} at "
                          f"{[c for c in CODES if d[c] is None]}")
    return {"per_tone": out,
            "h_minus_l_db": hml,
            "h_minus_l_anchored_db": {c: round(hml[c] - hml[ANCHOR], 3) for c in CODES},
            "h_edt10_ms": hed,
            "h_edt10_ratio": {c: round(hed[c] / hed[ANCHOR], 4) for c in CODES},
            "ln_edt10_ms": lned,
            "ln_edt10_ratio": {c: round(lned[c] / lned[ANCHOR], 4) for c in CODES}}


# ---------------------------------------------------------------------------
# 6. where the knob's authority goes: each band's share of L and H, rendered
# ---------------------------------------------------------------------------
SUPERPOSITION_TOL_DB = 0.5


def band_shares(anchor_level: dict) -> dict:
    """Each band's own energy inside L and H at the anchor, one band at a time.

    Rendered, not inferred: `cc.band_only` switches the other two VCA paths off,
    so this is the same instrument measuring the same kit with two thirds of it
    muted. It is what turns "the knob does not move H - L" into a number.
    """
    out = {}
    for band in ("low", "decay", "short"):
        kit, _ = kit_at(anchor_level, ANCHOR)
        y, sr = render_r4(cc.band_only(kit, band))
        p = rc.prepare(y, sr, side=f"{band} band alone")
        row = {}
        for name in ("L", "H"):
            lo, hi = cb.BANDS[name]
            x = cb._bp(p, sr, lo, hi)
            row[name] = float(np.sum(x[:int(cb.ENERGY_S * sr)] ** 2))
        out[band] = row
    return out


def predict_anchored(shares: dict, offsets=None) -> dict:
    """The anchored H - L a LINEAR mix of the three rendered bands would give,
    with `offsets` (dB, per band) added to their levels.

    Linear superposition is an assumption here, not a fact -- the VCAs clip
    (NL_SWING) -- so `superposition_check` below asserts it against the render
    itself and the caller REFUSES when it does not hold.
    """
    off = {b: 0.0 for b in shares} | (offsets or {})
    g = cc.TONE_GAIN_DB
    out = {}
    for c in CODES:
        e = {n: sum(shares[b][n] * 10.0 ** ((g[c][b] + off[b]) / 10.0) for b in shares)
             for n in ("L", "H")}
        out[c] = 10.0 * math.log10(e["H"] / e["L"])
    return {c: round(out[c] - out[ANCHOR], 3) for c in CODES}


def superposition_check(shares: dict, measured: dict) -> dict:
    """Does the linear mix of the three bands reproduce the render's own anchored
    curve? If not, the swing VCAs' clipping is carrying the knob and the
    requirement below cannot be derived this way."""
    pred = predict_anchored(shares)
    err = {c: round(pred[c] - measured[c], 3) for c in CODES}
    worst = max(abs(v) for v in err.values())
    return {"predicted": pred, "measured": measured, "error_db": err,
            "worst_db": round(worst, 3), "bound_db": SUPERPOSITION_TOL_DB,
            "ok": worst <= SUPERPOSITION_TOL_DB}


def required_short_band_offset(shares: dict, ref_anchored: dict) -> dict:
    """The ONE number the balance has to move to make the knob track: how much
    the short band's level must rise, relative to the other two, for the
    rendered bands to reproduce the 808's anchored H - L.

    This is an inference with one free parameter, reported as a REQUIREMENT for
    #396 and deliberately NOT applied -- applying a number fitted to the
    recordings is exactly the closed loop `../README.md` §4 records. Its
    residual is reported so a reader can see how well one number can do.
    """
    grid = np.arange(0.0, 60.01, 0.25)
    best = None
    for off in grid:
        pred = predict_anchored(shares, {"short": float(off)})
        resid = max(abs(pred[c] - ref_anchored[c]) for c in CODES)
        if best is None or resid < best[0]:
            best = (resid, float(off), pred)
    resid, off, pred = best
    return {"offset_db": off, "worst_residual_db": round(resid, 3),
            "bound_db": BOUND_DB, "reaches_bound": resid <= BOUND_DB,
            "predicted_anchored_db": pred,
            "note": "a requirement handed to #396, NOT applied to any candidate"}


def bracket_db() -> dict:
    """What the circuit's per-band levels allow the anchored H - L to be, before
    the render and without knowing the inter-band balance: L follows the low
    band and H lies between the decay band's shift and the short band's."""
    g = cc.TONE_GAIN_DB
    out = {}
    for c in CODES:
        lo = min(g[c]["decay"], g[c]["short"]) - g[c]["low"]
        hi = max(g[c]["decay"], g[c]["short"]) - g[c]["low"]
        out[c] = [round(lo, 3), round(hi, 3)]
    return out


def verdict(ours: dict, ref: dict) -> dict:
    """Every named property, per DECAY column, with its bound. No averaged
    score: each column and each TONE position is reported."""
    br = bracket_db()
    a = ours["h_minus_l_anchored_db"]
    seq = [a[c] for c in CODES]
    out = {
        "h-minus-l-monotone": {
            "ok": all(y > x for x, y in zip(seq, seq[1:])),
            "value": [round(v, 2) for v in seq],
            "what": "the rendered anchored H - L rises with TONE (the 808's does, in all five columns)"},
        "h-minus-l-in-bracket": {
            "ok": all(br[c][0] - 1e-9 <= a[c] <= br[c][1] + 1e-9 for c in CODES),
            "value": {c: [a[c], br[c]] for c in CODES},
            "what": "inside the pre-render bracket the circuit's per-band levels allow"},
        "h-edt-falls-with-tone": {
            # bool(): `x and y` returns y, and y here is a numpy comparison, so
            # this `ok` was an np.bool_ and json.dumps(..., default=float)
            # serialised it as 1.0 rather than true -- truthy for every consumer
            # but not equal to True, while every sibling property serialised as
            # a real boolean. A record's flag must not depend on which branch of
            # an `and` produced it.
            "ok": bool(ours["h_edt10_ratio"]["10"] < 1.0 and ours["h_edt10_ratio"]["00"] > 1.0),
            "value": ours["h_edt10_ratio"],
            "what": "H's own EDT10 falls as TONE opens -- a decay, so no balance can fake it"},
        "ln-edt-tone-invariant": {
            "ok": all(abs(v - 1.0) <= LN_INVARIANCE for v in ours["ln_edt10_ratio"].values()),
            "value": ours["ln_edt10_ratio"], "bound": LN_INVARIANCE,
            "what": "the low band's decay does not move with TONE (the machine's moves +-3 %)"},
    }
    cols = {}
    for dec, r in ref.items():
        d_hml = {c: round(a[c] - r["h_minus_l_anchored_db"][c], 3) for c in CODES}
        d_hed = {c: round(ours["h_edt10_ratio"][c] / r["h_edt10_ratio"][c] - 1.0, 4) for c in CODES}
        cols[dec] = {
            "settings": r["settings"],
            "split": {c: ("development" if r["settings"][c] in cb.DEVELOPMENT else "confirmation")
                      for c in CODES},
            "h_minus_l_anchored_delta_db": d_hml,
            "h_minus_l_worst_db": round(max(abs(v) for v in d_hml.values()), 3),
            "h_minus_l_ok": bool(max(abs(v) for v in d_hml.values()) <= BOUND_DB),
            "h_edt10_ratio_rel_err": d_hed,
            "h_edt10_worst_rel": round(max(abs(v) for v in d_hed.values()), 4),
            "h_edt10_ok": bool(max(abs(v) for v in d_hed.values()) <= TIME_TOL),
        }
    out["h-minus-l-tracks-808"] = {
        "ok": all(v["h_minus_l_ok"] for v in cols.values()),
        "bound_db": BOUND_DB,
        "worst_db": round(max(v["h_minus_l_worst_db"] for v in cols.values()), 3),
        "what": f"every TONE position within {BOUND_DB} dB of the 808, in every DECAY column"}
    out["h-edt-tracks-808"] = {
        "ok": all(v["h_edt10_ok"] for v in cols.values()),
        "bound_rel": TIME_TOL,
        "worst_rel": round(max(v["h_edt10_worst_rel"] for v in cols.values()), 4),
        "what": f"H's EDT10 ratio within {TIME_TOL:.0%} of the 808's, in every DECAY column"}
    return {"properties": out, "columns": cols}


def print_report(res: dict):
    ours, ref, v = res["render"], res["fischer"], res["verdict"]
    br = bracket_db()
    print(f"\nanchor TONE {ANCHOR}; bounds: H-L {BOUND_DB} dB, time {TIME_TOL:.0%}")
    print(f"budget: {res['precondition']['budget']['modes']} modes, "
          f"{res['precondition']['budget']['paths']} paths, N_NUMS {cc.N_NUMS}\n")
    print("anchored H - L (dB re TONE 50)")
    print(f"  {'TONE':>5s} " + " ".join(f"{int(tn.FRAC[c] * 100):>8d}" for c in CODES))
    print(f"  {'ours':>5s} " + " ".join(f"{ours['h_minus_l_anchored_db'][c]:8.2f}" for c in CODES))
    print(f"  {'brkt':>5s} " + " ".join(f"{br[c][0]:4.1f}/{br[c][1]:3.1f}" for c in CODES))
    for dec in CODES:
        r = ref[dec]
        print(f"  D{dec:<4s} " + " ".join(f"{r['h_minus_l_anchored_db'][c]:8.2f}" for c in CODES)
              + "   delta " + " ".join(f"{v['h_minus_l_anchored_delta_db'][c]:+6.2f}"
                                       for c in CODES for v in [res["verdict"]["columns"][dec]]))
    print("\nH EDT10, ratio to TONE 50")
    print(f"  {'ours':>5s} " + " ".join(f"{ours['h_edt10_ratio'][c]:8.3f}" for c in CODES))
    for dec in CODES:
        print(f"  D{dec:<4s} " + " ".join(f"{ref[dec]['h_edt10_ratio'][c]:8.3f}" for c in CODES))
    print("\nLn EDT10, ratio to TONE 50")
    print(f"  {'ours':>5s} " + " ".join(f"{ours['ln_edt10_ratio'][c]:8.3f}" for c in CODES))
    for dec in CODES:
        print(f"  D{dec:<4s} " + " ".join(f"{ref[dec]['ln_edt10_ratio'][c]:8.3f}" for c in CODES))
    sh, sup, req = res["band_shares"], res["superposition"], res["required_short_band_offset"]
    tot = {n: sum(sh[b][n] for b in sh) for n in ("L", "H")}
    print("\nwhere the knob's authority goes: each band's rendered share, in dB re the band total")
    print(f"  {'band':6s} {'in L':>8s} {'in H':>8s}")
    for b in ("low", "decay", "short"):
        print(f"  {b:6s} " + " ".join(
            f"{10 * math.log10(max(sh[b][n], 1e-300) / tot[n]):8.2f}" for n in ("L", "H")))
    print(f"  linear superposition reproduces the render's own curve to "
          f"{sup['worst_db']:.2f} dB (bound {sup['bound_db']}): {sup['ok']}")
    if req:
        print(f"  REQUIREMENT for #396: the short band needs {req['offset_db']:+.2f} dB "
              f"relative to the other two; residual {req['worst_residual_db']:.2f} dB "
              f"(bound {req['bound_db']}), reaches it: {req['reaches_bound']}")
        print("     -> " + " ".join(f"{c}:{req['predicted_anchored_db'][c]:+.2f}" for c in CODES))
    print("\nproperties")
    for name, p in v["properties"].items():
        print(f"  {'PASS' if p['ok'] else 'FAIL'}  {name:24s} {p['what']}")
    print("\nper setting, every one reported (acceptance 3)")
    print(f"  {'setting':9s} {'split':13s} {'808 anch':>9s} {'ours':>8s} {'delta':>7s} "
          f"{'808 Hedt':>9s} {'ours':>8s}")
    for dec in CODES:
        col, r = v["columns"][dec], ref[dec]
        for c in CODES:
            print(f"  {r['settings'][c]:9s} {col['split'][c]:13s} "
                  f"{r['h_minus_l_anchored_db'][c]:9.2f} {ours['h_minus_l_anchored_db'][c]:8.2f} "
                  f"{col['h_minus_l_anchored_delta_db'][c]:+7.2f} "
                  f"{r['h_edt10_ratio'][c]:9.3f} {ours['h_edt10_ratio'][c]:8.3f}")
    p = res["preservation"]
    if p is None:
        print("\npreservation: SKIPPED (--skip-preservation); this record is not a verdict")
        return
    print("\npreservation")
    print("  registers outside the cymbal's own, changed at any TONE position: "
          + str(sum(v["n_changed_outside_cy"] for v in p["addresses"].values())))
    bad = [s for s, ok in p["bit_exact_all_sounds_at_anchor"].items() if not ok]
    print(f"  bit-exact, all 16 other sounds at TONE {ANCHOR}: "
          + ("all true" if not bad else f"FAILED {bad}"))
    badh = {c: [s for s, ok in r.items() if not ok]
            for c, r in p["bit_exact_hats_every_tone"].items() if not all(r.values())}
    print("  bit-exact, OH and CH at every TONE position: "
          + ("all true" if not badh else f"FAILED {badh}"))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--out", type=pathlib.Path, required=True)
    ap.add_argument("--fischer", type=pathlib.Path, default=None)
    ap.add_argument("--wavs", type=pathlib.Path, default=None)
    ap.add_argument("--skip-preservation", action="store_true",
                    help="diagnostic only; the record marks itself incomplete")
    a = ap.parse_args(argv)
    try:
        pre = assert_realisation_is_the_validated_one()
        cal = calibrate()
        ref = fischer_curves(a.fischer)
        pres = None if a.skip_preservation else preservation(cal["anchor_level"])
        ours = measure_render(cal["anchor_level"])
        shares = band_shares(cal["anchor_level"])
        sup = superposition_check(shares, ours["h_minus_l_anchored_db"])
        req = (required_short_band_offset(shares, ref["50"]["h_minus_l_anchored_db"])
               if sup["ok"] else None)
    except Refused as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 3
    res = {"precondition": pre, "levels": cal, "preservation": pres,
           "preservation_complete": not a.skip_preservation,
           "render": ours, "fischer": ref, "bracket_db": bracket_db(),
           "band_shares": shares, "superposition": sup,
           "required_short_band_offset": req}
    res["verdict"] = verdict(ours, ref)
    print_report(res)
    if a.wavs:
        from scipy.io import wavfile
        a.wavs.mkdir(parents=True, exist_ok=True)
        for code in CODES:
            kit, _ = kit_at(cal["anchor_level"], code)
            y, sr = render_r4(kit)
            wavfile.write(a.wavs / f"CY-TONE{code}-candidate4.wav", sr,
                          np.clip(y * 32767, -32768, 32767).astype(np.int16))
    res["commit"] = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT,
                                   capture_output=True, text=True).stdout.strip()
    res["sources_dirty"] = subprocess.run(["git", "diff", "--quiet", "HEAD", "--", "model", "tools"],
                                          cwd=ROOT).returncode != 0
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text(json.dumps(res, indent=1, default=tn.json_leaf) + "\n")
    return 0 if (pres is None or pres["ok"]) else 1


if __name__ == "__main__":
    raise SystemExit(main())
