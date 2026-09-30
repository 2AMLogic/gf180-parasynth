#!/usr/bin/env python3
"""The TR-808 cymbal's INTER-BAND BALANCE: what resolves it, what does not, and by how much (#369/#396).

Step 5 (`docs/scorecard/cymbal-369/candidate3/`, PR #402) applied the tone
stage's measured *tilt* and deliberately left the *balance* alone -- the three
bands' levels relative to one another. #396 then asked for the balance itself,
preferring the schematic route (SN p.13's R/C values around VR4, the way Hh1
came from R124/R127/C48/C59) over any further fitting of W14b Figure 9.

THIS TOOL DOES NOT RESOLVE THE BALANCE. It decomposes it, states each factor's
provenance and uncertainty, asserts the preconditions an applicable answer
needs, and REFUSES when they are absent -- which one of them still is. The
value is in the numbers the refusal is made of:

  1. The 9-18 dB the issue quotes is read at the WRONG FREQUENCY for the
     balance question, and it is now the wrong QUANTITY as well.

     Wrong frequency: that 9/18 dB is `werner_fig9.extrapolation_bound`
     evaluated at 7.1 kHz for every band. Each band's level is set in one
     1/3 octave (3175 Hz for the low band, 10079 Hz for both high bands), and
     there the same figure bound is 7.4 dB (low, against the 18.0 dB read at
     7.1 kHz), 13.9 dB (decay, which is WIDER than its own 9.1 dB at 7.1 kHz)
     and 0.04 dB (short, MEASURED rather than extrapolated, because 10079 Hz
     is inside Ht3's plotted range).

     Wrong quantity: #390/#417 stopped bounding that network and SOLVED it.
     `tools/tone_stage_schematic.py` reads SN p.13's R/C values around VR4 off
     a SHA-256-pinned scan and solves the network by nodal analysis, fitting
     one shared wiper fraction -- no per-path gain -- to all three of Figure
     9's digitised k = 1.0 families to 0.001-0.013 dB rms. So the `tone`
     column below is a CIRCUIT VALUE, and `tone +-` is that solution's own
     residual: 0.008 / 0.008 / 0.067 dB wide, not 7.4 / 13.9 / 0.04 (#420).
     The figure route is still computed, because #396 EXCLUDES it and an
     exclusion has to be a measurement: `figure_route_gains()` refuses on a
     propagated bound of up to 21.3 dB against a 3.0 dB tolerance.

  2. What IS binding is a factor neither figure carries at all: **the three
     swing VCAs' drive levels.** §10's three bands leave the same source
     through three separate envelope generators and three separate VCAs
     (Q16/Q17/Q18), and Figures 4 and 9 measure only the filters and the tone
     network between them. Applying the filters-plus-tone balance while the
     VCA drives stay on the shipped-kit rule is applying one factor of a
     product and calling it the product.

     Its size is measured here rather than argued: against the shipped-kit
     level rule that candidate 3 ships, the filters-plus-tone balance alone
     demands +10.1 dB on the decay band and +39.8 dB on the short band. A
     39.8 dB unexplained residual is not a 9-18 dB figure-reading problem, and
     resolving the tone term MOVED IT THE WRONG WAY for anyone hoping VR4
     would explain it: the gap grew by 0.3 dB (decay) and 1.6 dB (short).

Everything is read from the committed artifacts -- `werner-fig4.json` (the
band-pass peaks and the high-pass pass bands, whose digitiser is gated on
schematic R/C values), `werner-fig9.json` (the tone stage as a figure),
`sn-p13-vr4.json` (the tone stage as a solved circuit, emitted by
`tone_stage_schematic.py --emit`) and `candidate3/candidate3.json` (the
shipped-kit rule's realised levels) -- and from `model/cymbal_candidate.py`,
so the tool and the model cannot drift.

  --report   the decomposition, per band, with provenance and bounds
  --check    the gate: five named properties, and a properties x defects
             matrix over five injected controls plus one asserted blind
  --json P   the evidence record

REFUSES rather than answering when an artifact is absent, and `balance_gains()`
still REFUSES today because ONE of its four preconditions is unmet -- the VCA
drives. That refusal is verified to be non-vacuous in both directions: it names
`vca-drive` and no longer names `schematic-vr4`, and handed a complete
synthetic precondition set it answers.
"""
from __future__ import annotations

import argparse
import json
import math
import pathlib
import sys

import numpy as np

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "model"), str(ROOT / "tools")]
import drums_fx as dx                       # noqa: E402
import cymbal_candidate as cc               # noqa: E402
import cymbal_tone_realisation as ct        # noqa: E402
import tone_stage_schematic as ts           # noqa: E402
import werner_fig4 as wf                    # noqa: E402
import werner_fig9 as w9                    # noqa: E402

Refused = ct.Refused
BANDS = ct.BANDS
BAND_OF = ct.BAND_OF

# Where each band's level is anchored by the candidate's level rule
# (`tools/cymbal_candidate_eval.CENTRE`). A balance is a statement about
# levels, so it has to be evaluated where the levels are set -- not at the
# band-pass peak, and not at one shared frequency for all three bands.
CENTRE_HZ = ct.CENTRE_HZ

# The band-pass each band leaves the source through (reference §10: the low
# band from IC3 pin 1, both high bands from IC3 pin 7 -- ONE filter, shared).
BP_HZ = {"low": 3450.0, "decay": 7100.0, "short": 7100.0}
BP_Q = 6.0
BP_KEY = {"low": "low", "decay": "high", "short": "high"}

# The bound a balance term must be known to before it may be applied. 3.0 dB is
# `docs/audio-distance-metrics.md`'s "board's tolerance" for a band split, the
# same bound `cymbal_tone_realisation.SHAPE_BOUND_DB` uses for band tilt, and
# 19x the machine's own unit-to-unit band-split spread (0.159 dB) measured in
# the same document. Stated here BEFORE any bound below was computed.
BALANCE_BOUND_DB = 3.0

# The preconditions an APPLICABLE balance needs, with the repo path each would
# live at. Two are absent, and the tool says which and why rather than
# substituting something weaker. `docs/scorecard/cymbal-369/` is where every
# other digitised artifact in this chain lives, so these are the paths a future
# digitisation would write, not invented names.
SCORECARD = ROOT / "docs" / "scorecard" / "cymbal-369"
SCHEMATIC_ARTIFACT = SCORECARD / "sn-p13-vr4.json"
VCA_ARTIFACT = SCORECARD / "vca-drive.json"
CANDIDATE3_ARTIFACT = SCORECARD / "candidate3" / "candidate3.json"

# The two routes to the tone term, kept side by side on purpose. The schematic
# one is what the balance uses; the figure one is what #396 EXCLUDES, and it is
# computed anyway so the exclusion stays a measurement (`figure_route_gains`).
ROUTE_SCHEMATIC = "schematic"
ROUTE_FIGURE = "figure"
ROUTES = (ROUTE_SCHEMATIC, ROUTE_FIGURE)


# ---------------------------------------------------------------------------
# Analytic sections. Each is normalised at the point its recorded constant is
# defined, so "peak" and "pass band" mean what Figure 4 measured them to mean.
# ---------------------------------------------------------------------------


def _s(hz):
    return 2j * np.pi * np.asarray(hz, dtype=float)


def _db(x):
    return 20.0 * np.log10(np.maximum(np.abs(x), 1e-30))


def bandpass_db(hz, f0, q):
    """A 2-pole band-pass normalised to 0 dB AT ITS OWN PEAK f0."""
    s, w0 = _s(hz), 2 * math.pi * f0
    return _db((s * w0 / q) / (s * s + s * w0 / q + w0 * w0))


def highpass2_db(hz, f0, q):
    """A 2-pole high-pass normalised to 0 dB in its PASS BAND (f >> f0)."""
    s, w0 = _s(hz), 2 * math.pi * f0
    return _db((s * s) / (s * s + s * w0 / q + w0 * w0))


def highpass1_db(hz, f0):
    s, w0 = _s(hz), 2 * math.pi * f0
    return _db(s / (s + w0))


def band_filter_db(band, hz, *, defect=None):
    """The band's own filter chain (band-pass x high-pass), in dB, with the
    absolute peak and pass-band constants Figure 4 measured applied."""
    peaks = dict(cc.BP_PEAK_DB)
    if defect == "SWAP_PEAKS":
        peaks = {"low": cc.BP_PEAK_DB["high"], "high": cc.BP_PEAK_DB["low"]}
    out = peaks[BP_KEY[band]] + bandpass_db(hz, BP_HZ[band], BP_Q)
    if band == "low":
        out = out + cc.HH1_PASS_DB + highpass2_db(hz, cc.HH1_HZ, cc.HH1_Q)
    elif band == "decay":
        out = out + cc.HH2_PASS_DB + highpass2_db(hz, cc.HH2_HZ, cc.HH2_Q)
    else:
        out = (out + cc.HH3_PASS_DB + highpass2_db(hz, cc.HH3_HZ, cc.HH3_Q)
               + highpass1_db(hz, cc.HH3_P1_HZ))
    return out


# ---------------------------------------------------------------------------
# The `filter-figure` precondition: read and validated, not merely checked to
# exist (#431). `band_filter_db` above applies `cc.BP_PEAK_DB` / `HH*_PASS_DB`
# -- literals pinned from W14b Figure 4 and marked "recorded, not applied" at
# their definition (model/cymbal_candidate.py). Before #431 the `filter-figure`
# precondition was `werner_fig4.ARTIFACT.exists()` and nothing ever opened the
# file: an empty file at that path satisfied it. `filter_record` opens it,
# re-derives each peak and pass band from the digitised curves the same way
# `tools/werner_fig4.py --fit` does, and REFUSES if the file is absent,
# unreadable, missing a curve, or if what it says disagrees with the pinned
# constants `band_filter_db` actually applies -- a schema-mismatched or
# drifted artifact is a REFUSAL, not a pass.
# ---------------------------------------------------------------------------

_FILTER_CACHE: dict = {}

# How far a live read of `werner_fig4.ARTIFACT` may disagree with the pinned
# `cc.BP_PEAK_DB` / `HH*_PASS_DB` literals before `filter-figure` refuses.
# `docs/tr808-reference.md` records this fit's own residual at 0.007-0.008 dB
# rms; this is an order of magnitude looser, tight enough to catch a swapped
# or stale literal and loose enough not to flag the fit's own noise floor.
FILTER_TOL_DB = 0.05


def filter_record(path: pathlib.Path | None = None) -> dict:
    """`werner-fig4.json`'s band-pass peaks and high-pass pass bands, read and
    cross-checked against the pinned constants `band_filter_db` applies -- or a
    REFUSAL naming the command that writes the artifact, or the value that
    disagrees with it.
    """
    p = path or wf.ARTIFACT
    key = str(p)
    if key not in _FILTER_CACHE:
        if not p.exists():
            raise Refused(f"{p} is absent; run tools/werner_fig4.py --json {p}")
        try:
            curves, _blob = wf.from_artifact(p)
        except wf.Refused as exc:
            raise Refused(f"{p} is not a readable werner_fig4 record: {exc}") from None
        except (json.JSONDecodeError, UnicodeDecodeError, KeyError, TypeError,
               AttributeError) as exc:
            raise Refused(f"{p} is not a readable werner_fig4 record "
                          f"({type(exc).__name__}: {exc})") from None
        need = {"Hbp1", "Hbp2", "Hh1", "Hh2", "Hh3"}
        missing = need - set(curves)
        if missing:
            raise Refused(f"{p} carries no curve(s) for {sorted(missing)}")
        for k in need:
            hz, db = curves[k]
            if len(hz) < 5 or len(db) < 5 or len(hz) != len(db):
                raise Refused(
                    f"{p} curve {k!r} has {len(hz)} hz / {len(db)} db points; "
                    "too few (or mismatched) to read a peak or pass band from")
        try:
            peak_db = {"low": wf.peak(*curves["Hbp1"])[1],
                      "high": wf.peak(*curves["Hbp2"])[1]}
            pass_db = {"low": wf.fit_structure(*curves["Hh1"], "hp2")["gain_db"],
                      "decay": wf.fit_structure(*curves["Hh2"], "hp2")["gain_db"],
                      "short": wf.fit_structure(*curves["Hh3"], "hp3")["gain_db"]}
        except Exception as exc:                       # noqa: BLE001
            raise Refused(f"{p}'s curves could not be read as band-pass peaks "
                          f"/ high-pass pass bands: {exc}") from None
        want_peak = dict(cc.BP_PEAK_DB)
        want_pass = {"low": cc.HH1_PASS_DB, "decay": cc.HH2_PASS_DB,
                    "short": cc.HH3_PASS_DB}
        mismatch = [
            f"BP_PEAK_DB[{k}] pinned {want_peak[k]:+.3f} vs artifact {v:+.3f} dB"
            for k, v in peak_db.items() if abs(v - want_peak[k]) > FILTER_TOL_DB
        ] + [
            f"{k} pass band pinned {want_pass[k]:+.3f} vs artifact {v:+.3f} dB"
            for k, v in pass_db.items() if abs(v - want_pass[k]) > FILTER_TOL_DB
        ]
        if mismatch:
            raise Refused(
                f"{p} disagrees with the pinned band_filter_db constants by "
                f"more than {FILTER_TOL_DB} dB: " + "; ".join(mismatch))
        _FILTER_CACHE[key] = {"peak_db": peak_db, "pass_db": pass_db}
    return _FILTER_CACHE[key]


def tone_fit(band, fig9):
    v = fig9[BAND_OF[band]]
    hz, db = v["curves"][v["k1_index"]]
    return hz, db, w9.fit_bp2(hz, db)


def tone_db(band, hz, fig9):
    """The tone stage's ABSOLUTE transmission for this band, at k = 1.0.

    Unlike `cymbal_tone_realisation.analog_target_db`, which normalises the
    gain away because it is asking about shape, this keeps `peak_db`: the
    balance IS the gain.
    """
    _, _, fit = tone_fit(band, fig9)
    return fit["gain_db"] + bandpass_db(hz, fit["f0"], fit["q"])


# `werner_fig9.extrapolation_bound` refits 50 alternative sections per call, and
# the gate below asks for the same (band, frequency) pair dozens of times over
# one run. The cache is keyed on the CURVE's bytes, not on the band name, so a
# different digitisation cannot silently reuse another's bound.
_BOUND_CACHE: dict = {}


def _bound_cached(band, hz, db, f):
    import hashlib
    key = (band, round(float(f), 6),
           hashlib.sha1(np.ascontiguousarray(hz).tobytes()
                        + np.ascontiguousarray(db).tobytes()).hexdigest())
    if key not in _BOUND_CACHE:
        _BOUND_CACHE[key] = w9.extrapolation_bound(hz, db, f)
    return _BOUND_CACHE[key]


def figure_tone_term(band, fig9, *, at_hz=None, defect=None):
    """The tone stage's contribution to this band's level FROM FIGURE 9, with
    its bound -- the route #396 excludes, kept because the exclusion has to be
    a measurement rather than a decree.

    The bound is `werner_fig9.extrapolation_bound` -- the spread of every
    alternative section the plotted window cannot tell apart from the 2-pole
    fit -- evaluated AT THE BAND'S OWN CENTRE. The 9 dB / 18 dB figures quoted
    in #396 and reference §18 are the same bound at 7.1 kHz for all three
    bands, which is not where two of the three bands are levelled.
    """
    f = float(at_hz if at_hz is not None else CENTRE_HZ[band])
    if defect == "TONE_AT_7100_FOR_ALL":
        f = 7100.0
    hz, db, _ = tone_fit(band, fig9)
    b = _bound_cached(band, hz, db, f)
    nominal = float(tone_db(band, np.array([f]), fig9)[0])
    if defect == "NO_TONE_TERM":
        return {"hz": f, "db": 0.0, "lo_db": 0.0, "hi_db": 0.0, "width_db": 0.0,
                "measured": True, "n_alternatives": 0, "route": ROUTE_FIGURE}
    return {
        "hz": f,
        "db": nominal,
        "lo_db": float(b["lo_db"]),
        "hi_db": float(b["hi_db"]),
        "width_db": float(b["hi_db"] - b["lo_db"]),
        "measured": bool(hz.min() <= f <= hz.max()),
        "n_alternatives": int(b["n_alternatives"]),
        "route": ROUTE_FIGURE,
    }


# ---------------------------------------------------------------------------
# The RESOLVED tone term: SN p.13's VR4 network, solved (#390/#417, applied
# here by #420). Read from the committed `sn-p13-vr4.json` record, not
# recomputed on the fly, so the precondition below is load-bearing at the point
# of use rather than a token the tool checks and then ignores.
# ---------------------------------------------------------------------------

_SCHEMATIC_CACHE: dict = {}


def schematic_record(path: pathlib.Path | None = None) -> dict:
    """`sn-p13-vr4.json`, or a REFUSAL naming the command that writes it."""
    p = path or SCHEMATIC_ARTIFACT
    key = str(p)
    if key not in _SCHEMATIC_CACHE:
        if not p.exists():
            raise Refused(
                f"{p} is absent; run tools/tone_stage_schematic.py --emit "
                f"{p}")
        blob = json.loads(p.read_text())
        if blob.get("artifact") != "sn-p13-vr4" or "bands" not in blob:
            raise Refused(f"{p} is not an sn-p13-vr4 record "
                          "(no artifact/bands key)")
        _SCHEMATIC_CACHE[key] = blob
    return _SCHEMATIC_CACHE[key]


def schematic_tone_term(band, *, at_hz=None, defect=None, path=None):
    """The tone stage's contribution to this band's level AS A CIRCUIT VALUE.

    `db` is `tone_stage_schematic.solve_vtone` at the fitted wiper fraction;
    `width_db` is twice that solution's own bound (its largest disagreement
    with Figure 9's digitised curve, plus 3 sigma on the one fitted parameter)
    -- NOT an extrapolation spread, because nothing is being extrapolated: the
    network is solved at whatever frequency it is asked about.

    REFUSES for a frequency the record does not carry rather than interpolating
    one, and REFUSES if the record's frequency for this band is no longer the
    frequency the band's level is set at -- a record that quietly answered for
    the wrong third would look exactly like a record that answered.
    """
    f = float(at_hz if at_hz is not None else CENTRE_HZ[band])
    if defect == "TONE_AT_7100_FOR_ALL":
        f = 7100.0
    if defect == "NO_TONE_TERM":
        return {"hz": f, "db": 0.0, "lo_db": 0.0, "hi_db": 0.0, "width_db": 0.0,
                "measured": True, "route": ROUTE_SCHEMATIC}
    rec = schematic_record(path)
    try:
        entry = rec["bands"][band]["at_hz"][f"{f:.1f}"]
    except KeyError:
        have = sorted(rec.get("bands", {}).get(band, {}).get("at_hz", {}))
        raise Refused(
            f"{SCHEMATIC_ARTIFACT.name} solves the {band} band at "
            f"{', '.join(have) or 'no frequency'} Hz, not at {f:.1f} Hz; "
            "re-emit it with tools/tone_stage_schematic.py --emit") from None
    if at_hz is None and defect != "TONE_AT_7100_FOR_ALL" \
            and abs(entry["hz"] - CENTRE_HZ[band]) > 1e-6:
        raise Refused(f"{SCHEMATIC_ARTIFACT.name} records the {band} band at "
                      f"{entry['hz']} Hz but its level is set at "
                      f"{CENTRE_HZ[band]} Hz")
    half = float(entry["bound_db"])
    return {
        "hz": f,
        "db": float(entry["db"]),
        "lo_db": float(entry["lo_db"]),
        "hi_db": float(entry["hi_db"]),
        "width_db": 2.0 * half,
        "measured": bool(entry["residual_measured_here"]),
        "residual_db": float(entry["residual_db"]),
        "alpha_db": float(entry["alpha_db"]),
        "family": rec["bands"][band]["family"],
        "route": ROUTE_SCHEMATIC,
    }


def tone_term(band, fig9, *, at_hz=None, defect=None, route=None):
    """The tone term by `route`. The default is the RESOLVED circuit value."""
    r = ROUTE_SCHEMATIC if route is None else route
    if r == ROUTE_SCHEMATIC:
        return schematic_tone_term(band, at_hz=at_hz, defect=defect)
    if r == ROUTE_FIGURE:
        return figure_tone_term(band, fig9, at_hz=at_hz, defect=defect)
    raise ValueError(f"unknown tone route {r!r}; expected one of {ROUTES}")


# ---------------------------------------------------------------------------
# The decomposition
# ---------------------------------------------------------------------------


FACTORS = ("band-pass peak", "high-pass pass band", "tone stage",
           "LEVEL differentiator", "VCA drive")
PROVENANCE = {
    "band-pass peak": "W14b Fig. 4 via tools/werner_fig4.py; the digitiser is "
                      "gated on SN p.13 R/C values (R56-R59, C13-C16)",
    "high-pass pass band": "W14b Fig. 4 via tools/werner_fig4.py, same gate",
    "tone stage": "SN p.13's VR4 network via tools/tone_stage_schematic.py, "
                  "solved by nodal analysis and emitted as sn-p13-vr4.json -- "
                  "RESOLVED (#390/#417, applied by #420). `width_db` is that "
                  "solution's own residual against W14b Fig. 9's digitised "
                  "curves, not an extrapolation spread. Fig. 9's own bounded "
                  "reading is kept beside it as the route #396 excludes",
    "LEVEL differentiator": "W14b Fig. 10 via tools/werner_fig4.py; common to "
                            "all three bands, so it cancels in a ratio -- but "
                            "not its frequency dependence, and the bands are "
                            "levelled at different frequencies",
    "VCA drive": "ABSENT. Three envelope generators and three swing VCAs "
                 "(Q16/Q17/Q18, reference §10) sit between the band-passes "
                 "and the high-passes. No figure in W14b plots them and no "
                 "artifact in this repository carries them.",
}


def band_terms(band, fig9, corner_hz, *, defect=None, route=None) -> dict:
    """Every factor of one band's level, at the frequency its level is set."""
    f = np.array([CENTRE_HZ[band]])
    peaks = dict(cc.BP_PEAK_DB)
    if defect == "SWAP_PEAKS":
        peaks = {"low": cc.BP_PEAK_DB["high"], "high": cc.BP_PEAK_DB["low"]}
    hp_pass = {"low": cc.HH1_PASS_DB, "decay": cc.HH2_PASS_DB,
               "short": cc.HH3_PASS_DB}[band]
    tone = tone_term(band, fig9, defect=defect, route=route)
    level = float(highpass1_db(f, corner_hz)[0])
    total = float(band_filter_db(band, f, defect=defect)[0]) + tone["db"] + level
    if defect == "UNIFORM_6DB_ALL_BANDS":
        # a gain common to all three bands, downstream of everything: the
        # output buffer's, not a balance. Every property here must be blind.
        total += 6.0
    return {
        "band": band,
        "centre_hz": CENTRE_HZ[band],
        "bp_peak_db": peaks[BP_KEY[band]],
        "bp_shape_db": round(float(bandpass_db(f, BP_HZ[band], BP_Q)[0]), 2),
        "hp_pass_db": hp_pass,
        "chain_db": round(float(band_filter_db(band, f, defect=defect)[0]), 2),
        "tone": tone,
        "level_db": round(level, 2),
        "total_db": round(total, 2),
    }


def relative_db(fig9, corner_hz, *, ref="low", defect=None, route=None) -> dict:
    """Each band's circuit level relative to `ref`, with the tone bound
    propagated. The VCA-drive factor is NOT in here -- that is the point."""
    t = {b: band_terms(b, fig9, corner_hz, defect=defect, route=route)
         for b in BANDS}
    out = {}
    for b in BANDS:
        nom = t[b]["total_db"] - t[ref]["total_db"]
        # worst case either way: this band at its bound's edge, the reference
        # at the other edge.
        lo = ((t[b]["total_db"] - t[b]["tone"]["db"] + t[b]["tone"]["lo_db"])
              - (t[ref]["total_db"] - t[ref]["tone"]["db"] + t[ref]["tone"]["hi_db"]))
        hi = ((t[b]["total_db"] - t[b]["tone"]["db"] + t[b]["tone"]["hi_db"])
              - (t[ref]["total_db"] - t[ref]["tone"]["db"] + t[ref]["tone"]["lo_db"]))
        # 4 decimals, not 2: #420's resolved tone term makes the propagated
        # width 0.015-0.075 dB, and at 2 decimals that reads as "0.02" -- a
        # rounding artefact standing where a measurement belongs.
        out[b] = {"db": round(nom, 4), "lo_db": round(lo, 4), "hi_db": round(hi, 4),
                  "width_db": round(hi - lo, 4)}
    return out


# ---------------------------------------------------------------------------
# What the shipped-kit level rule actually does, from the committed render
# ---------------------------------------------------------------------------


def shipped_rule_levels(path: pathlib.Path | None = None, *, defect=None) -> dict:
    """Candidate 3's per-band level record, from the committed `candidate3.json`.

    Read, not recomputed: these are the absolute 1/3-octave energies the level
    rule matched each band to, plus the amp and envelope registers it wrote to
    get there, so they are the balance the model currently ships.
    """
    p = path or CANDIDATE3_ARTIFACT
    if not p.exists():
        raise Refused(f"{p} is absent; run tools/cymbal_candidate_eval.py --out {p}")
    blob = json.loads(p.read_text())
    try:
        per = blob["levels"]["per_band"]
        out = {b: {"shipped_abs": float(per[b]["shipped_abs"]),
                   "unit_abs": float(per[b]["unit_abs"]),
                   "amp": float(per[b]["amp"]),
                   "env_gain": float(per[b].get("env_gain", 1.0))}
               for b in BANDS}
    except (KeyError, TypeError) as exc:
        raise Refused(f"{p} carries no levels.per_band[*] with shipped_abs/unit_abs/amp") from exc
    if defect == "SHIPPED_RULE_IS_FLAT":
        for b in BANDS:
            out[b]["shipped_abs"] = out["low"]["shipped_abs"]
    return out


# The unit render `cymbal_candidate_eval.calibrate()` measures each band
# against, and the register-round-off it leaves behind. Both are properties of
# that function, restated here so a change to it breaks this tool's bind test
# rather than silently rebasing it.
UNIT_AMP = 0.25
RULE_BIND_TOL = 2e-3


def shipped_rule_relative_db(path: pathlib.Path | None = None, *,
                             ref="low", defect=None) -> dict:
    """The shipped-kit level rule's band balance, in dB relative to `ref`."""
    lv = shipped_rule_levels(path=path, defect=defect)
    return {b: round(10.0 * math.log10(lv[b]["shipped_abs"] / lv[ref]["shipped_abs"]), 2)
            for b in BANDS}


def gap_db(fig9, corner_hz, *, defect=None, path=None, route=None) -> dict:
    """Circuit balance minus shipped-kit balance, per band.

    This difference is what the two ABSENT preconditions have to account for:
    the VCA drive ratios, and the shipped kit's own band-gain errors (it has no
    Hh1 and routes the short band through the closed hat's high-pass). It is
    reported as one number because nothing available here separates them --
    saying which is which is exactly what the schematic would do.
    """
    circuit = relative_db(fig9, corner_hz, defect=defect, route=route)
    rule = shipped_rule_relative_db(path=path, defect=defect)
    return {b: {"circuit_db": circuit[b]["db"], "rule_db": rule[b],
                "gap_db": round(circuit[b]["db"] - rule[b], 2),
                "bound_db": circuit[b]["width_db"]}
            for b in BANDS}


# ---------------------------------------------------------------------------
# The `vca-drive` precondition: read and validated, not merely checked to
# exist (#431). `docs/scorecard/cymbal-369/vca-drive/vca-drive.json` (#432, PR
# #433) MEASURED the three swing VCAs' per-band collector-load term -- +4.97 dB
# (short) and +3.52 dB (DECAY) relative to the low band -- but wrote it to a
# sibling path, deliberately NOT to `VCA_ARTIFACT` (the path below), because
# that measurement does not settle the question `balance_gains` needs: the
# DECAY band's own upper bound (+7.26 dB) sits within 2.6 dB of its gap
# (+9.85 dB). The supply-side term (the three envelope generators' peak
# collector voltages) is now ALSO measured -- `docs/scorecard/cymbal-369/
# vca-supply/vca-supply.json`, #432 -- and it does not close the gap either
# (the short band's ceiling is 31.5 dB short of it at its most favourable
# duty); it is written to the same kind of sibling path for the same reason.
# `vca_drive_record` reads and schema-validates whatever DOES land at
# `VCA_ARTIFACT` -- a present-but-empty or malformed file no longer satisfies
# the precondition -- but its `chain_db` is deliberately NOT folded into
# `balance_gains` below: applying a per-band term that is not yet resolved
# would be exactly the "one factor of a product, called the product" mistake
# this module's own top docstring warns against for the VCA drive as a whole.
# ---------------------------------------------------------------------------

_VCA_CACHE: dict = {}


def vca_drive_record(path: pathlib.Path | None = None) -> dict:
    """`vca-drive.json`'s per-band VCA term (`vca_term_db.chain_db` /
    `.bound_db`, the shape `tools/cymbal_vca_drive.py` emits), read and
    schema-validated -- or a REFUSAL naming why. Read, not applied: see the
    section comment above for why `balance_gains` does not fold this in yet.
    """
    p = path or VCA_ARTIFACT
    key = str(p)
    if key not in _VCA_CACHE:
        if not p.exists():
            raise Refused(
                f"{p} is absent; the three swing VCAs' drive levels have not "
                "been measured at this path (see #432, PR #433)")
        try:
            blob = json.loads(p.read_text())
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise Refused(f"{p} is not valid JSON ({exc})") from None
        try:
            term = blob["vca_term_db"]
            chain_db = {b: float(term["chain_db"][b]) for b in BANDS}
            bound_db = {b: float(term["bound_db"][b]) for b in BANDS}
        except (KeyError, TypeError, ValueError) as exc:
            raise Refused(
                f"{p} carries no readable vca_term_db.chain_db/bound_db per "
                f"band ({type(exc).__name__}: {exc})") from None
        _VCA_CACHE[key] = {"chain_db": chain_db, "bound_db": bound_db}
    return _VCA_CACHE[key]


# ---------------------------------------------------------------------------
# Preconditions -- asserted at the point of use, and REFUSED when unmet
# ---------------------------------------------------------------------------


def _content_checked(fn) -> bool:
    """True if `fn()` (a `*_record` reader) resolves without REFUSING."""
    try:
        fn()
        return True
    except Refused:
        return False


def preconditions(*, present=None) -> list[dict]:
    """What an applicable balance needs. `present` overrides the on-disk answer
    and exists only so the refusal can be shown to be non-vacuous.

    `filter-figure` and `vca-drive` are answered by actually reading and
    validating their artifact (`filter_record` / `vca_drive_record`), not by
    `path.exists()` alone (#431: any file at either path used to lift the
    refusal without a single number from it reaching the balance). `tone-figure`
    and `schematic-vr4` stay on `path.exists()` here because their content is
    already read and validated downstream, at the point `balance_gains` actually
    uses them -- `main()`'s own load of `fig9`, and `schematic_tone_term`'s call
    to `schematic_record` -- so a bad file there already REFUSES independently
    of this table.
    """
    want = [
        ("tone-figure", w9.ARTIFACT,
         "the tone stage's transmission per band (W14b Fig. 9)"),
        ("filter-figure", wf.ARTIFACT,
         "the band-pass peaks and high-pass pass bands (W14b Figs. 4, 10)"),
        ("schematic-vr4", SCHEMATIC_ARTIFACT,
         "SN p.13's R/C values around VR4, as circuit values rather than a "
         "figure fit -- #396's preferred route, and reference §18's first. "
         "RESOLVED by #390/#417: emitted from the nodal solution with "
         "tools/tone_stage_schematic.py --emit"),
        ("vca-drive", VCA_ARTIFACT,
         "the three envelope generators' and swing VCAs' peak drive "
         "(Q16/Q17/Q18), which no W14b figure plots"),
    ]
    content_checked = {"filter-figure": filter_record, "vca-drive": vca_drive_record}
    out = []
    for name, path, why in want:
        if present is not None:
            ok = bool(present.get(name, False))
        elif name in content_checked:
            ok = _content_checked(content_checked[name])
        else:
            ok = path.exists()
        out.append({"name": name, "path": str(path.relative_to(ROOT)),
                    "needed_for": why, "present": bool(ok)})
    return out


def balance_gains(fig9, corner_hz, *, present=None) -> dict:
    """The per-band gains an applicable balance would impose. REFUSES today.

    The refusal is the deliverable of #396's item 2. It used to name TWO
    missing inputs; #390/#417 supplied the first, so today it names exactly one
    -- `vca-drive` -- and the tool says so instead of quietly substituting the
    figure-fitting route the issue excludes. Hand it a complete precondition
    set and it answers: that is `test_the_refusal_is_not_vacuous`, and it is
    why the remaining refusal is an assertion rather than an opinion compiled
    into a function.

    `filter-figure` and `vca-drive` (#431) are gated by `preconditions()`
    actually reading and validating their artifact via `filter_record` /
    `vca_drive_record` -- a present-but-empty or schema-mismatched file no
    longer satisfies either.
    """
    missing = [p for p in preconditions(present=present) if not p["present"]]
    if missing:
        raise Refused(
            "the inter-band balance is not applicable: "
            + "; ".join(f"{p['name']} absent ({p['path']}) -- needed for "
                        f"{p['needed_for']}" for p in missing)
        )
    # Past this point every precondition holds, and the tone term is already a
    # circuit value carrying no figure-reading bound -- which is precisely why
    # #396 prefers this route.
    return {b: v["db"] for b, v in relative_db(fig9, corner_hz).items()}


def figure_route_gains(fig9, corner_hz, *, defect=None) -> dict:
    """The route #396 explicitly EXCLUDES: take the balance from Figure 9's
    fitted `peak_db` values and apply them.

    It lives here so the exclusion is a measurement rather than a decree. The
    bound is `relative_db`'s propagated extrapolation spread, and it is wider
    than `BALANCE_BOUND_DB` in more than one band, so this refuses.
    """
    rel = relative_db(fig9, corner_hz, defect=defect, route=ROUTE_FIGURE)
    wide = {b: v["width_db"] for b, v in rel.items() if v["width_db"] > BALANCE_BOUND_DB}
    if wide:
        raise Refused(
            "the figure route gives the balance only to "
            + ", ".join(f"{b} +-{v / 2:.1f} dB" for b, v in sorted(wide.items()))
            + f", outside the {BALANCE_BOUND_DB} dB bound")
    return {b: v["db"] for b, v in rel.items()}


# ---------------------------------------------------------------------------
# The ablation: what applying the resolved factors ALONE does
# ---------------------------------------------------------------------------


# `cymbal_candidate_eval`'s Q0.16 amp ceiling and the envelope peak's, restated
# so the arithmetic below cannot drift from the registers it has to fit.
AMP_MAX = 65535 / 65536
ENV_PEAK_MAX = 1.0
ENV_OF = {"low": "E_CYL", "decay": "E_CYD", "short": "E_CYS"}


def _env_base_peak(band: str) -> float:
    import drums_fx as _dx
    e = {"low": _dx.E_CYL, "decay": _dx.E_CYD, "short": _dx.E_CYS}[band]
    return dict(_dx.kit_808())[_dx.A_ENV + e * _dx.ENV_STRIDE + 1] / _dx.FULL24


def rebalance(cal: dict, fig9, corner_hz) -> dict:
    """`cymbal_candidate_eval.calibrate()`'s levels, moved onto the circuit's
    relative balance -- a DIAGNOSTIC ABLATION, not a candidate.

    It applies exactly the factors that ARE resolved (the band-pass peaks and
    the high-pass pass bands from Figure 4, and the tone stage's nominal
    transmission from Figure 9) and holds the three VCA drives equal, because
    nothing here measures them. That last assumption is the thing under test:
    if it were right, the render's band balance would land near the 808's.

    The anchor is the largest common scale that keeps every band inside its
    registers -- the ratios are the claim, the absolute level is not.
    """
    gaps = gap_db(fig9, corner_hz)
    per = cal["per_band"]
    want, ceiling = {}, {}
    for b in BANDS:
        base_gain = math.sqrt(per[b]["shipped_abs"] / per[b]["unit_abs"])
        want[b] = base_gain * 10.0 ** (gaps[b]["gap_db"] / 20.0)
        ceiling[b] = (AMP_MAX / UNIT_AMP) * (ENV_PEAK_MAX / _env_base_peak(b))
    k = min(ceiling[b] / want[b] for b in BANDS)
    out = {"per_band": {}, "amps": {}, "ablation": {
        "kind": "circuit-balance-equal-vca-drive",
        "gap_db": {b: gaps[b]["gap_db"] for b in BANDS},
        "common_scale_db": round(20.0 * math.log10(k), 2)}}
    for b in BANDS:
        gain = want[b] * k
        amp, env_gain = UNIT_AMP * gain, 1.0
        if amp > AMP_MAX:
            env_gain, amp = amp / AMP_MAX, AMP_MAX
        peak = _env_base_peak(b) * env_gain
        if peak > ENV_PEAK_MAX + 1e-9:
            raise Refused(f"{b} band needs envelope peak {peak:.3f} > {ENV_PEAK_MAX}")
        out["per_band"][b] = {**per[b], "amp": amp, "env_gain": env_gain,
                              "rebalanced_db": round(20.0 * math.log10(gain / (
                                  math.sqrt(per[b]["shipped_abs"] / per[b]["unit_abs"]))), 2)}
        out["amps"][LEVEL_MODE_OF[b]] = amp
        if env_gain != 1.0:
            out["amps"][f"E_{b}"] = peak
    return out


# Imported lazily by name to avoid a circular import: cymbal_candidate_eval
# imports this module.
LEVEL_MODE_OF = {"low": cc.M_CYH1, "decay": dx.M_CYHI, "short": cc.M_CYH3B}


# ---------------------------------------------------------------------------
# The gate
# ---------------------------------------------------------------------------


PROPERTIES = ("chain-normalisation", "tone-bound", "centre-bound-tighter",
              "rule-bind", "refusal-live")
DEFECTS = ("SWAP_PEAKS", "NO_TONE_TERM", "TONE_AT_7100_FOR_ALL",
           "SHIPPED_RULE_IS_FLAT", "PRECOND_ALWAYS_OK")
# Asserted the other way round: a gain common to all three bands is not a
# balance, so every property here MUST stay blind to one. A suite that reported
# it would be reading a level where it claims to read a ratio.
BLIND_BY_CONSTRUCTION = ("UNIFORM_6DB_ALL_BANDS",)

# The gap that says the VCA-drive factor dominates. Stated as a floor, not as
# an expected value: the claim is "much larger than the figure bound", so the
# assertion is that at least one band's gap exceeds the widest tone bound.
CHAIN_TOL_DB = 0.01


def properties(fig9, corner_hz, *, defect=None) -> dict:
    out = {}

    # 1. chain-normalisation: a known answer. Evaluated at its own peak the
    #    band-pass contributes exactly its recorded peak, and far above its
    #    corner the high-pass contributes exactly its recorded pass band. If
    #    either normalisation is wrong every level below is wrong by a
    #    constant, and a constant is exactly what a balance is.
    worst, at = 0.0, ""
    for band in BANDS:
        f = np.array([BP_HZ[band]])
        got = float(band_filter_db(band, f, defect=defect)[0])
        hp_pass = {"low": cc.HH1_PASS_DB, "decay": cc.HH2_PASS_DB,
                   "short": cc.HH3_PASS_DB}[band]
        hp_shape = {"low": float(highpass2_db(f, cc.HH1_HZ, cc.HH1_Q)[0]),
                    "decay": float(highpass2_db(f, cc.HH2_HZ, cc.HH2_Q)[0]),
                    "short": float(highpass2_db(f, cc.HH3_HZ, cc.HH3_Q)[0]
                                   + highpass1_db(f, cc.HH3_P1_HZ)[0])}[band]
        want = cc.BP_PEAK_DB[BP_KEY[band]] + hp_pass + hp_shape
        d = abs(got - want)
        if d > worst:
            worst, at = d, band
    out["chain-normalisation"] = (
        worst <= CHAIN_TOL_DB,
        f"max {worst:.4f} dB at each band's own band-pass peak ({at or 'n/a'}), "
        f"bound {CHAIN_TOL_DB}")

    # 2. tone-bound: the two routes must say the two different things they are
    #    each here to say. The FIGURE route must be outside the applicability
    #    bound for at least one band -- otherwise #396's exclusion of it is
    #    unearned and the balance could simply be read off Figure 9. The
    #    SCHEMATIC route must be INSIDE it for every band -- otherwise #390's
    #    solution has not actually resolved the term it claims to resolve, and
    #    the `tone +-` column below would be decoration.
    #
    #    Before #420 this property was the first half alone, and the first half
    #    alone would now be satisfied by a tone term that had resolved nothing.
    figrel = relative_db(fig9, corner_hz, defect=defect, route=ROUTE_FIGURE)
    rel = relative_db(fig9, corner_hz, defect=defect, route=ROUTE_SCHEMATIC)
    wide = [b for b in BANDS if figrel[b]["width_db"] > BALANCE_BOUND_DB]
    loose = [b for b in BANDS if rel[b]["width_db"] > BALANCE_BOUND_DB]
    out["tone-bound"] = (
        bool(wide) and not loose,
        "figure route "
        + "  ".join(f"{b} +-{figrel[b]['width_db'] / 2:.1f}" for b in BANDS)
        + f" dB re low ({len(wide)} outside {BALANCE_BOUND_DB} dB); schematic route "
        + "  ".join(f"{b} {rel[b]['db']:+.1f} +-{rel[b]['width_db'] / 2:.3f}" for b in BANDS)
        + f" dB re low ({len(loose)} outside)")

    # 3. centre-bound-tighter: #396's own arithmetic error, kept as a live
    #    assertion because it is what sent the chain after the wrong factor. At
    #    its own 3175 Hz calibration third the low band's FIGURE bound is
    #    tighter than the 18 dB #396 quotes, which is the same bound read at
    #    7.1 kHz. This one stays on the figure route by construction: the
    #    schematic route has no frequency-dependent window to be wrong about.
    here = figure_tone_term("low", fig9, defect=defect)["width_db"]
    there = figure_tone_term("low", fig9, at_hz=7100.0, defect=defect)["width_db"]
    out["centre-bound-tighter"] = (
        here < there,
        f"low band figure tone term {here:.1f} dB wide at its own "
        f"{CENTRE_HZ['low']:.0f} Hz centre vs {there:.1f} dB at 7.1 kHz")

    # 4. rule-bind: the balance being differenced against is the one the kit
    #    actually writes. `candidate3.json`'s per-band energies and its amp /
    #    envelope registers are two independent records of the same level, so
    #    each band's gain must be recoverable from the energies and land on the
    #    registers. Without this the gap below is arithmetic about a level rule
    #    nothing implements. The magnitude claim rides on the same property:
    #    the gap must exceed the widest tone bound, or Figure 9's window really
    #    would be what is blocking the balance.
    #
    #    The magnitude comparison is deliberately made against the FIGURE
    #    route's bound, not the schematic route's. #420 collapsed the tone
    #    term's own bound to ~0.07 dB, and "39.8 dB exceeds 0.07 dB" is a
    #    sentence that asserts nothing. The claim that has to keep holding is
    #    the one the refusal rests on: the gap is larger than the WIDEST
    #    uncertainty anyone has ever offered for the tone term -- 21.3 dB, from
    #    the figure window that #396 blamed.
    try:
        lv = shipped_rule_levels(defect=defect)
        g = gap_db(fig9, corner_hz, defect=defect)
        figg = gap_db(fig9, corner_hz, defect=defect, route=ROUTE_FIGURE)
        worst_bind, at = 0.0, ""
        for b in BANDS:
            want = math.sqrt(lv[b]["shipped_abs"] / lv[b]["unit_abs"]) * UNIT_AMP
            got = lv[b]["amp"] * lv[b]["env_gain"]
            d = abs(20.0 * math.log10(got / want))
            if d > worst_bind:
                worst_bind, at = d, b
        biggest = max(abs(v["gap_db"]) for v in g.values())
        widest = max(v["bound_db"] for v in figg.values())
        out["rule-bind"] = (
            worst_bind <= RULE_BIND_TOL and biggest > widest,
            f"registers vs energies {worst_bind:.5f} dB ({at or 'n/a'}, bound {RULE_BIND_TOL}); gaps "
            + "  ".join(f"{b} {v['gap_db']:+.1f}" for b, v in g.items())
            + f" dB re low; worst |gap| {biggest:.1f} vs widest FIGURE tone bound {widest:.1f}")
    except Refused as exc:
        out["rule-bind"] = (False, f"REFUSED: {exc}")

    # 5. refusal-live: the refusal must fire on the real tree AND must lift on
    #    a complete precondition set, AND the excluded figure route must refuse
    #    for its own, different reason. A refusal that can never lift is a
    #    hard-coded opinion, not an assertion (CLAUDE.md: run a gate against
    #    the current state before committing it).
    #
    #    #420 adds one clause: the live refusal must name `vca-drive`. Before
    #    #420 it named two preconditions and #390/#417 supplied one of them, so
    #    "it still refuses" is no longer enough on its own -- a refusal that
    #    had gone on naming the resolved input would be reporting a state of
    #    the repository that stopped being true.
    fired = lifted = excluded = False
    fired_why = lifted_why = ""
    try:
        balance_gains(fig9, corner_hz,
                      present=({p["name"]: True for p in preconditions()}
                               if defect == "PRECOND_ALWAYS_OK" else None))
    except Refused as exc:
        fired, fired_why = "vca-drive" in str(exc), str(exc)
    try:
        balance_gains(fig9, corner_hz,
                      present={p["name"]: True for p in preconditions()})
        lifted = True
    except Refused as exc:
        lifted_why = str(exc)
    try:
        figure_route_gains(fig9, corner_hz, defect=defect)
    except Refused:
        excluded = True
    out["refusal-live"] = (
        fired and lifted and excluded,
        "refuses on this tree naming vca-drive (schematic-vr4 is resolved), "
        "answers on a complete precondition set, and the excluded figure route "
        "refuses on its bound"
        if fired and lifted and excluded else
        (f"does not refuse on this tree for vca-drive: {fired_why or 'no refusal'}"
         if not fired else
         f"never lifts: {lifted_why}" if not lifted else
         "the excluded figure route does not refuse"))
    return out


def check(fig9, corner_hz) -> tuple[bool, list[str]]:
    lines = []
    clean = properties(fig9, corner_hz)
    ok = all(v[0] for v in clean.values())
    for name in PROPERTIES:
        good, detail = clean[name]
        lines.append(f"  {'PASS' if good else 'FAIL'}  {name:20s} {detail}")
    if not ok:
        lines.append("the clean run does not pass; the controls below mean nothing")
        return False, lines
    lines.append("")
    lines.append(f"  {'defect':22s} " + " ".join(f"{p:>20s}" for p in PROPERTIES))
    caught, blind_ok = [], []
    for d in DEFECTS + BLIND_BY_CONSTRUCTION:
        try:
            got = properties(fig9, corner_hz, defect=d)
            cells = ["MOVED" if got[p][0] is False else "BLIND" for p in PROPERTIES]
        except (Refused, ValueError) as exc:
            lines.append(f"  {d:22s} REFUSED: {exc}")
            caught.append(True)          # a control that makes the tool refuse is caught
            continue
        if d in BLIND_BY_CONSTRUCTION:
            blind_ok.append("MOVED" not in cells)
            lines.append(f"  {d:22s} " + " ".join(f"{c:>20s}" for c in cells)
                         + "   (must be BLIND everywhere)")
        else:
            caught.append("MOVED" in cells)
            lines.append(f"  {d:22s} " + " ".join(f"{c:>20s}" for c in cells))
    lines.append("")
    lines.append(f"  {sum(caught)}/{len(DEFECTS)} controls turned at least one property red; "
                 f"{sum(blind_ok)}/{len(BLIND_BY_CONSTRUCTION)} blind-by-construction "
                 "controls stayed blind")
    return all(caught) and all(blind_ok), lines


# ---------------------------------------------------------------------------


def report(fig9, corner_hz) -> list[str]:
    lines = [f"LEVEL differentiator corner {corner_hz:.1f} Hz (W14b Fig. 10)",
             f"applicability bound {BALANCE_BOUND_DB} dB "
             "(docs/audio-distance-metrics.md, the board's tolerance for a band split)",
             ""]
    lines.append("Each band's circuit level AT THE FREQUENCY ITS LEVEL IS SET "
                 "(tone = SN p.13's VR4 network solved, sn-p13-vr4.json):")
    lines.append(f"  {'band':6s} {'centre':>9s} {'BP peak':>8s} {'BP shape':>9s} "
                 f"{'HP pass':>8s} {'tone':>9s} {'tone +-':>8s} {'LEVEL':>7s} {'total':>8s}")
    for band in BANDS:
        t = band_terms(band, fig9, corner_hz)
        lines.append(
            f"  {band:6s} {t['centre_hz']:8.0f}H {t['bp_peak_db']:+8.2f} "
            f"{t['bp_shape_db']:+9.2f} {t['hp_pass_db']:+8.2f} {t['tone']['db']:+9.2f} "
            f"{t['tone']['width_db'] / 2:8.4f} {t['level_db']:+7.2f} {t['total_db']:+8.2f}"
            + ("" if t["tone"]["measured"]
               else "   residual carried outside Fig. 9's window"))
    lines.append("")
    lines.append("The same tone term the EXCLUDED figure route gives, for comparison "
                 "(#396's route, W14b Fig. 9):")
    lines.append(f"  {'band':6s} {'tone':>9s} {'tone +-':>8s}   {'schematic - figure':>18s}")
    for band in BANDS:
        ft = figure_tone_term(band, fig9)
        st = schematic_tone_term(band)
        lines.append(f"  {band:6s} {ft['db']:+9.2f} {ft['width_db'] / 2:8.2f}   "
                     f"{st['db'] - ft['db']:+18.2f}"
                     + ("" if ft["measured"] else "   EXTRAPOLATED"))
    lines.append("")
    rel = relative_db(fig9, corner_hz)
    g = gap_db(fig9, corner_hz)
    lines.append("Balance relative to the low band:")
    lines.append(f"  {'band':6s} {'circuit':>9s} {'bound':>16s} "
                 f"{'shipped-kit rule':>18s} {'gap':>8s}")
    for band in BANDS:
        lines.append(
            f"  {band:6s} {rel[band]['db']:+9.2f} "
            f"[{rel[band]['lo_db']:+7.2f},{rel[band]['hi_db']:+7.2f}] "
            f"{g[band]['rule_db']:+18.2f} {g[band]['gap_db']:+8.2f}")
    lines.append("")
    lines.append("Factors, and where each one comes from:")
    for f in FACTORS:
        lines.append(f"  {f:22s} {PROVENANCE[f]}")
    lines.append("")
    lines.append("Preconditions for an APPLICABLE balance:")
    for p in preconditions():
        lines.append(f"  {'PRESENT' if p['present'] else 'ABSENT '}  {p['name']:15s} "
                     f"{p['path']}")
        if not p["present"]:
            lines.append(f"            needed for {p['needed_for']}")
    lines.append("")
    try:
        got = balance_gains(fig9, corner_hz)
        lines.append(f"balance: {got}")
    except Refused as exc:
        lines.append(f"REFUSED: {exc}")
    return lines


def record(fig9, corner_hz) -> dict:
    ok, lines = check(fig9, corner_hz)
    try:
        applied = balance_gains(fig9, corner_hz)
        refusal = None
    except Refused as exc:
        applied, refusal = None, str(exc)
    return {
        "tool": "tools/cymbal_band_balance.py",
        "sources": {"tone": str(SCHEMATIC_ARTIFACT.relative_to(ROOT)),
                    "tone_figure": str(w9.ARTIFACT.relative_to(ROOT)),
                    "filters": str(wf.ARTIFACT.relative_to(ROOT)),
                    "shipped_rule": str(CANDIDATE3_ARTIFACT.relative_to(ROOT))},
        "tone_route": ROUTE_SCHEMATIC,
        "level_corner_hz": corner_hz,
        "balance_bound_db": BALANCE_BOUND_DB,
        "centre_hz": CENTRE_HZ,
        "bands": {b: band_terms(b, fig9, corner_hz) for b in BANDS},
        "figure_route_bands": {b: band_terms(b, fig9, corner_hz, route=ROUTE_FIGURE)
                               for b in BANDS},
        "relative_db": relative_db(fig9, corner_hz),
        "figure_route_relative_db": relative_db(fig9, corner_hz, route=ROUTE_FIGURE),
        "shipped_rule_db": shipped_rule_relative_db(),
        "gap_db": gap_db(fig9, corner_hz),
        "figure_route_gap_db": gap_db(fig9, corner_hz, route=ROUTE_FIGURE),
        "preconditions": preconditions(),
        "applied": applied,
        "refused": refusal,
        "gate": {"pass": bool(ok), "lines": lines},
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--report", action="store_true")
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--json", type=pathlib.Path)
    a = ap.parse_args(argv)
    if not (a.report or a.check or a.json):
        a.report = True
    try:
        fig9 = w9.from_artifact()[0]
        corner = ct.level_corner_hz()
    except (w9.Refused, Refused) as exc:
        print(f"REFUSED: {exc}")
        return 2
    rc = 0
    if a.report:
        print("\n".join(report(fig9, corner)))
    if a.check:
        ok, lines = check(fig9, corner)
        print("\n".join(lines))
        print("gate:", "PASS" if ok else "FAIL")
        rc = 0 if ok else 1
    if a.json:
        a.json.parent.mkdir(parents=True, exist_ok=True)
        rec = record(fig9, corner)
        a.json.write_text(json.dumps(rec, indent=1, default=float) + "\n")
        print(f"wrote {a.json}")
        rc = rc or (0 if rec["gate"]["pass"] else 1)
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
