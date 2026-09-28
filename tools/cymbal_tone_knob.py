#!/usr/bin/env python3
"""The TR-808 cymbal's TONE KNOB LAW, predicted from VR4's wiper (#369 step 9).

THE QUESTION, and it is one question. #369 acceptance 3 asks for a cymbal that
is right "across the knobs, not just at one setting", and acceptance 1 asks for
every value to come from the circuit "not from fitting". The chain's own
`docs/scorecard/cymbal-369/README.md` section 4 records why the existing map
cannot answer it: `model/test_discrimination.kit_at`'s CY TONE law solves, in
closed form, for the 808's OWN measured 5-13 kHz / 2-5 kHz ratio at each TONE
position. A render whose band ratio is calibrated to the reference cannot then
be compared against the reference on band ratio -- that is `docs/failure-modes.md`'s
root cause exactly, an estimator calibrated on the thing it is measuring.

So ask the circuit instead:

    Does VR4's wiper position, solved from SN p.13, predict the 808's own
    H - L versus TONE -- with the wiper fraction taken from the pot's linear
    taper rather than fitted to the recordings?

WHY THIS IS ANSWERABLE WITH NOTHING FITTED TO THE RECORDINGS. #390/PR #417
(`tools/tone_stage_schematic.py`) solved the tone network by nodal analysis from
SN p.13's R/C values around VR4, parameterised in the wiper fraction `alpha`,
and fitted ONE alpha -- ALPHA_K1 -- against W14b Figure 9's k = 1.0 curves. It
states the knob mapping is out of scope: "`alpha` is a wiper FRACTION, not
W14b's own knob parameter `k` ... nothing here claims they are [linearly
related]". But the schematic does say what the pot is: **"20K(B)", a LINEAR
taper**, so the wiper fraction IS the fraction of rotation, and

    alpha = TONE / 100

is a PREDICTION read off a component marking, with no parameter fitted to the
recordings at all. The recordings' TONE dependence was used nowhere in
deriving it, which is what makes comparing the two an external check rather
than a consistency check.

WHAT IT PREDICTS. The tone network is a balanced bridging attenuator: VR4's
wiper is tied to ground, so turning it moves attenuation from one rail to the
other. The low band (Ht1, via Q25) and the DECAY band (Ht2) share the bottom
rail; the short band (Ht3) is on the top rail (#417 resolved which is which by
fit, 20-100x better than either alternative). So raising alpha lifts the short
band and drops both others, and H - L must RISE with TONE -- monotonically, and
by an amount the network fixes once the three bands' relative drive is fixed.

    band          rail        tone gain @3.45 kHz / @7.1 kHz, alpha = ALPHA_K1
    low   (Ht1)   bottom      -42.0 dB / -51.8 dB
    decay (Ht2)   bottom      -20.5 dB / -26.7 dB
    short (Ht3)   top         -28.1 dB / -33.7 dB

WHAT IS MEASURED AGAINST IT. H - L (`tools/cymbal_bands.BANDS`: L = 2-5 kHz,
H = 6-14 kHz) at all 25 Fischer settings, in three frozen windows. H - L is a
ratio inside one file, so it survives `run_case.prepare`'s peak normalisation
and any per-file capture gain; an absolute band energy would not.

THE TWO FREE NUMBERS, and how much they can absorb -- MEASURED, not asserted.
The three bands' relative drive (§10's three envelope generators into three
swing VCAs, Q16/Q17/Q18) is the term `docs/scorecard/cymbal-369/balance/README.md`
REFUSED for want of any artifact that carries it. It is therefore fitted here, as
ONE balance shared by all 25 settings -- two numbers, decay and short relative to
low -- and the residual is reported per setting.

**The small residual is NOT by itself the evidence, and an early draft of this
docstring said it was.** `shape_audit()` fits the same two numbers to eight
stated five-point curves and finds the family reaches five of them inside the
bound: the machine's own curve (0.20 dB), a FLAT line (0.38), a straight ramp of
the same span (0.34), and two curved ramps. So "0.33 dB over a 7.3 dB span" is
consistent with the circuit but is not on its own decisive -- the family is
flexible across monotone rises.

What IS decisive is what the same freedom cannot reach: the machine's curve
REVERSED (5.47 dB), a step (4.51) and a V (7.75), and, on the real data, the
three structural alternatives -- the inverted wiper law (5.47), no tone network
at all (5.46) and the low band on the top rail (6.95). Every one of those has the
same two free numbers. That is the argument, and it is a weaker and more honest
one than the residual alone.

WHAT THIS RESOLVES, AND WHAT IT LEAVES OPEN. Read `verdict()`'s own output, not
this paragraph, but in summary: the orientation (alpha rises with TONE), the
fact that the tone network is the mechanism, and the low band's rail are all
resolved decisively. Three things are NOT, and each is stated as a number
rather than as a caveat:

  * the exact shape of alpha(TONE). Linear is the best of the candidates swept
    in `mapping_sweep()` and is the one the component marking predicts, but
    sqrt(TONE) and a half-travel variant are within 0.6 dB of it -- so the
    linear taper is CONSISTENT with the recordings, not proven by them.
  * the Ht2/Ht3 rail assignment, which is BLIND to this measurement in all
    three windows by construction: both bands share the 7.1 kHz band-pass and
    both land in H, so the fitted balance absorbs a swap exactly. #417's fit
    against Figure 9 is what resolves it; this instrument cannot.
  * the inter-band balance itself. Inside the 3 dB bound the allowed region is
    wide in the absolute weights and narrow only in their DIFFERENCE, so this
    does NOT lift the balance refusal of #396/#414 -- see `balance_region()`.

Usage:
    python3 tools/cymbal_tone_knob.py --check            # the gate, no corpus needed
    python3 tools/cymbal_tone_knob.py --out <json>       # the record
    python3 tools/cymbal_tone_knob.py --refs <dir>       # add the windowed measurement
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
import run_case as rc                       # noqa: E402
import cymbal_bands as cb                   # noqa: E402
import cymbal_tone_realisation as tr        # noqa: E402
import tone_stage_schematic as ts           # noqa: E402

FISCHER_JSON = ROOT / "docs" / "scorecard" / "cymbal-369" / "fischer.json"

# The three bands' sources into the tone network, as #390/PR #417 resolved them
# (fit against Figure 9's three families; the two alternatives fit 20-100x
# worse). `solve_vtone`'s own names for the three drives.
RAILS = {"low": "hh1", "decay": "bottom", "short": "top"}

# TONE / DECAY code -> fraction of the pot's travel. The Fischer corpus names
# each file CY{TONE}{DECAY} with the two-digit code being percent/10 ("10" is
# 100 %, not 10 %) -- the same map `cymbal_bands.KNOB` uses on a 0-10 dial.
FRAC = {"00": 0.0, "25": 0.25, "50": 0.5, "75": 0.75, "10": 1.0}
# IN KNOB ORDER, which `cymbal_bands.CODES` is NOT: that tuple is in FILENAME
# order ("00", "10", "25", "50", "75") because "10" means 100 %. Every residual
# here is order-independent, but the EDT10 trend is not, and reading the trend
# down the filename order put TONE = 100 % second and reported a 109 % "local
# rise" in a quantity that falls. Wrong-then-right 4 -- and the reason this is a
# derived tuple with an assertion rather than a second literal.
CODES = tuple(sorted(cb.CODES, key=lambda c: FRAC[c]))
assert set(CODES) == set(cb.CODES) == set(FRAC), (CODES, cb.CODES)
assert all(FRAC[c] == cb.KNOB[c] / 10.0 for c in CODES), (FRAC, cb.KNOB)
# The TONE position every difference is taken against: the D14A anchor's TONE,
# and inside `cymbal_bands.DEVELOPMENT`. Differences, not absolute H - L,
# because the absolute value carries the balance and the balance is the one
# thing here that is fitted.
ANCHOR_TONE = "50"

# A literal alpha of 0 or 1 shorts one rail to ground; `solve_vtone` handles 0
# with a small resistance but the physical pot has end resistance too, so both
# ends are held off the rail by one part in a thousand of the pot.
ALPHA_EPS = 1e-3

# Frozen before any residual was computed, and each for a stated reason:
#   strike  0-20 ms   -- §10's short band is "fixed, short"; TAU_S puts it at
#                        20 ms, so this is the window where it is least
#                        outnumbered by the DECAY band.
#   tail    100-300 ms-- five time constants after the short band is gone, and
#                        inside the record at DECAY 00 (1.501 s, floor -71 dB).
#   1s      0-1000 ms -- the window `cymbal_bands.measure` already uses, so this
#                        one is checked against the committed artifact rather
#                        than only against itself.
WINDOWS = {"strike": (0.0, 0.020), "tail": (0.100, 0.300), "1s": (0.0, cb.ENERGY_S)}
ARTIFACT_WINDOW = "1s"

# The bound, stated in advance and not invented here: 3.0 dB is
# `docs/audio-distance-metrics.md`'s board tolerance for band tilt, the same
# constant `cymbal_tone_realisation.SHAPE_BOUND_DB` applies to a per-band shape
# error. Asserted equal to it below so the two cannot drift apart.
BOUND_DB = 3.0

# A record's own noise floor, measured as the same-length window at the end of
# the record, must be this far below the analysed window's energy. 15 dB is
# `cymbal_bands.TRUNC_MARGIN_DB`, which bounds the missing-tail bias there.
FLOOR_MARGIN_DB = 15.0

# 50 % of the reference value is `run_case.TOLERANCE_POLICY`'s time tolerance
# (§1.7's +-50 % on Q, and tau is proportional to Q in these resonators).
TIME_TOL = 0.50

# The balance grid, in dB relative to the low band. Wide on purpose: a bound
# that does not bracket the answer is not a bound (`cymbal_m_origin`'s SWEEP_DB
# spans -12..+30 for the same reason; this spans -30..+36).
BALANCE_DB = np.arange(-30.0, 36.001, 0.5)

# Where the gains are integrated, and how finely. Same span and grid count as
# `cymbal_m_origin.band_energies`, so the two are comparable arithmetic.
SPAN_HZ = (200.0, 20000.0)
N_HZ = 4000

# The §1.5 source: six squares whose harmonic POWER falls 6.02 dB/octave
# (amplitudes 1/n, power 1/n^2, constant line density). Step 8 measured the
# source tilt to matter for an absolute band energy; `SOURCE_FLAT` below shows
# it does not matter here, which is why it is a blind-by-construction control
# rather than an assumption.
SOURCE_DB_PER_OCT = -6.0206

DEFECTS = ("NO_TONE_NETWORK", "ALPHA_INVERTED", "RAIL_LOW_ON_TOP",
           "RAIL_SWAP_HT2_HT3", "EDT_BAND_SWAP")
# Two controls need the ~30 MB corpus, so they cannot run in the CI job that
# runs the rest. They are reported as NO VERDICT there -- which is red, and is
# not a pass (docs/verification-rules.md rule 5) -- and `--require-corpus`
# turns that NO VERDICT into a failure for the gate that does have the corpus
# (`make reference-integration`, the build box).
CORPUS_DEFECTS = ("WRONG_ANALYSIS_BANDS", "SHORT_RECORD")
BLIND_BY_CONSTRUCTION = ("SOURCE_FLAT",)
PROPERTIES = ("tone-exact", "artifact-binds", "floor-margin",
              "low-decay-tone-invariant", "h-edt-falls-with-tone",
              "alpha-law-1s", "alpha-law-strike", "alpha-law-tail",
              "orientation-decisive", "tone-load-bearing", "low-band-rail",
              "family-selective")

# Eight five-point curves the two free numbers are fitted to, to measure how much
# they can absorb. All but the first are synthetic and are built from the
# MACHINE's own span so that "can the family reach this" is asked at the size
# that matters. Frozen here rather than chosen after the answers were seen.
SHAPES = ("machine", "flat", "linear-ramp", "machine-reversed",
          "convex", "concave", "step", "vee")

# Rule 4's matrix is per (property, defect), not per defect: a defect that moves
# one property and is BLIND to another is the interesting case, and asserting
# WHICH is how a property that can never see anything gets found. Each entry is
# a defect and the properties it must NOT move, with the reason.
#
#   RAIL_SWAP_HT2_HT3 -- the two 7.1 kHz bands both land in H, so exchanging
#   their rails is absorbed exactly by the fitted balance. It is therefore blind
#   to every alpha-law property, and visible ONLY to `tone-exact`, which checks
#   each band's own tone gain against #417's reported numbers. That asymmetry is
#   the reason this instrument cannot resolve the assignment and #417's fit can.
#
#   WRONG_ANALYSIS_BANDS -- shifting L and H by half an octave changes the
#   MEASURED H - L (so `artifact-binds` sees it) but barely changes the
#   PREDICTED change across TONE, because the tone network's alpha dependence is
#   nearly flat in frequency inside each rail. Asserted rather than assumed: it
#   is the same robustness statement as the source tilt being blind.
BLIND_PAIRS = {
    "RAIL_SWAP_HT2_HT3": ("alpha-law-1s", "alpha-law-strike", "alpha-law-tail",
                          "tone-load-bearing"),
    "WRONG_ANALYSIS_BANDS": ("alpha-law-1s", "alpha-law-strike", "alpha-law-tail"),
}

# The structural alternatives every "decisive" property is measured against.
# Each is an INVOLUTION of the instrument's configuration, not an absolute
# setting, so a defect and an alternative compose: under `ALPHA_INVERTED` the
# "inverted" alternative is the true law and must then fit, which is exactly how
# that defect is caught. Each alternative is fitted with the SAME two free
# balance numbers as the prediction, so what separates them is the structure and
# not the freedom.
ALTERNATIVES = ("inverted", "no-tone-network", "low-on-top-rail", "ht2-ht3-swapped")


class Refused(RuntimeError):
    """A precondition this instrument needs is not met. Not a failed check."""


# ---------------------------------------------------------------------------
# the circuit's side: what the tone network does to each band, per analysis band
# ---------------------------------------------------------------------------
def alpha_of(frac: float, *, mapping="linear") -> float:
    """VR4's wiper fraction at TONE = `frac` of full rotation.

    `linear` is the prediction: VR4 is marked "20K(B)" on SN p.13 and a B taper
    is linear, so the wiper fraction IS the rotation fraction. The others exist
    to be swept (`mapping_sweep`) and to be controls, not to be chosen.
    """
    f = min(max(float(frac), 0.0), 1.0)
    if mapping == "linear":
        a = f
    elif mapping == "inverted":
        a = 1.0 - f
    elif mapping == "fixed":
        a = ts.ALPHA_K1
    elif mapping == "sqrt":
        a = math.sqrt(f)
    elif mapping == "square":
        a = f * f
    elif mapping == "upper-half":
        a = 0.5 + 0.5 * f
    elif mapping == "lower-half":
        a = 0.5 * f
    else:
        raise ValueError(mapping)
    return min(max(a, ALPHA_EPS), 1.0 - ALPHA_EPS)


def hz_grid(n: int = N_HZ) -> np.ndarray:
    return np.geomspace(SPAN_HZ[0], SPAN_HZ[1], n)


def level_db(hz) -> np.ndarray:
    """The LEVEL buffer's differentiator, W14b Figure 10, exactly as
    `cymbal_tone_realisation.analog_target_db` applies it. Common to all three
    bands, so it cancels in a ratio between bands at one frequency -- but not
    inside a band integral, which is why it is here."""
    s = 2j * np.pi * np.asarray(hz, dtype=float)
    return 20.0 * np.log10(np.maximum(np.abs(s / (s + 2 * math.pi * tr.level_corner_hz())), 1e-30))


def source_db(hz, *, flat=False) -> np.ndarray:
    """§1.5's source, as a POWER spectrum in dB (see SOURCE_DB_PER_OCT)."""
    if flat:
        return np.zeros(len(np.atleast_1d(hz)))
    return SOURCE_DB_PER_OCT * np.log2(np.asarray(hz, dtype=float) / 1000.0)


def _swap(rails, a, b):
    r = dict(rails)
    r[a], r[b] = r[b], r[a]
    return r


def config(*, defect=None, alternative=None, mapping=None) -> dict:
    """The instrument's configuration: the defect mutates it, then the
    alternative TRANSFORMS whatever the defect left.

    Composing in that order is what makes each control able to fail. An earlier
    version set the alternative's rails and mapping absolutely, so injecting
    `NO_TONE_NETWORK` silently dropped the "no-tone-network" alternative's own
    flat network and the property went red for the wrong reason -- a control
    that fires on the wrong assertion is condition 3 of rule 5, and it read as a
    catch. Wrong-then-right 2.
    """
    cfg = {"mapping": mapping or "linear", "rails": dict(RAILS),
           "tone_flat": False, "source_flat": False, "bands_shifted": False,
           "edt_swapped": False, "short_record": False}
    if defect == "NO_TONE_NETWORK":
        cfg["tone_flat"] = True
    elif defect == "ALPHA_INVERTED":
        cfg["mapping"] = "inverted"
    elif defect == "RAIL_LOW_ON_TOP":
        cfg["rails"] = _swap(cfg["rails"], "low", "short")
    elif defect == "RAIL_SWAP_HT2_HT3":
        cfg["rails"] = _swap(cfg["rails"], "decay", "short")
    elif defect == "SOURCE_FLAT":
        cfg["source_flat"] = True
    elif defect == "WRONG_ANALYSIS_BANDS":
        cfg["bands_shifted"] = True
    elif defect == "EDT_BAND_SWAP":
        cfg["edt_swapped"] = True
    elif defect == "SHORT_RECORD":
        cfg["short_record"] = True
    elif defect is not None:
        raise ValueError(defect)
    if alternative == "inverted":
        cfg["mapping"] = {"linear": "inverted", "inverted": "linear"}.get(
            cfg["mapping"], "inverted")
    elif alternative == "no-tone-network":
        cfg["tone_flat"] = not cfg["tone_flat"]
    elif alternative == "low-on-top-rail":
        cfg["rails"] = _swap(cfg["rails"], "low", "short")
    elif alternative == "ht2-ht3-swapped":
        cfg["rails"] = _swap(cfg["rails"], "decay", "short")
    elif alternative is not None:
        raise ValueError(alternative)
    return cfg


def tone_db(band, hz, alpha, cfg) -> np.ndarray:
    """The tone network's response from `band`'s source to N2, in dB.

    Read off `tone_stage_schematic.solve_vtone` -- the same nodal solution the
    committed module reports, not a re-derivation.
    """
    if cfg["tone_flat"]:
        return np.zeros(len(np.atleast_1d(hz)))
    return 20.0 * np.log10(np.maximum(
        np.abs(ts.solve_vtone(hz, alpha, cfg["rails"][band])), 1e-30))


def analysis_bands(cfg) -> dict:
    """L and H, from the qualified instrument. `bands_shifted` moves both by half
    an octave, which must break the binding to the artifact."""
    b = {k: cb.BANDS[k] for k in ("L", "H")}
    if cfg["bands_shifted"]:
        return {k: (lo * 2 ** 0.5, hi * 2 ** 0.5) for k, (lo, hi) in b.items()}
    return b


def gain_table(cfg=None, *, n=N_HZ, **kw) -> dict:
    """{(tone_code, analysis_band, band): dB} -- band `band`'s energy inside
    analysis band `analysis_band` at TONE `tone_code`, on ONE common scale.

    The per-band chain is the documented cascade: its own band-pass at Q 6 and
    its Sallen-Key high-pass (`cymbal_tone_realisation.band_chain_db`, which is
    what the candidate bank writes), then the tone network at this wiper
    position, then the LEVEL differentiator, weighted by §1.5's source. The
    CROSS terms are kept: the low band's energy inside H and both high bands'
    inside L are what step 8 section 3a measured the analysis filter to admit,
    and dropping them would be the leakage error that step made its own
    correction for.
    """
    cfg = cfg if cfg is not None else config(**kw)
    hz = hz_grid(n)
    lv = level_db(hz)
    src = source_db(hz, flat=cfg["source_flat"])
    bands = analysis_bands(cfg)
    chain = {b: tr.band_chain_db(b, hz) for b in RAILS}
    out = {}
    for code in CODES:
        a = alpha_of(FRAC[code], mapping=cfg["mapping"])
        for b in RAILS:
            tot = chain[b] + tone_db(b, hz, a, cfg) + lv + src
            p = 10.0 ** (tot / 10.0)
            for name, (lo, hi) in bands.items():
                m = (hz >= lo) & (hz <= hi)
                out[(code, name, b)] = 10.0 * math.log10(
                    float(np.trapezoid(p[m], hz[m])))
    return out


def predicted_hml(g: dict, weights_db) -> np.ndarray:
    """H - L across the five TONE codes for balances `weights_db`.

    `weights_db` is (..., 3) in dB relative to the low band, ordered
    (low, decay, short) -- low is always 0 by construction, so only two numbers
    are free. Vectorised over the leading axes because the balance grid is
    17,689 points and this is called once per alternative.
    """
    w = 10.0 ** (np.asarray(weights_db, dtype=float) / 10.0)
    order = ("low", "decay", "short")
    gh = np.array([[g[(c, "H", b)] for b in order] for c in CODES])
    gl = np.array([[g[(c, "L", b)] for b in order] for c in CODES])
    num = w @ (10.0 ** (gh / 10.0)).T
    den = w @ (10.0 ** (gl / 10.0)).T
    return 10.0 * np.log10(num / den)


def _anchored(y: np.ndarray) -> np.ndarray:
    return y - y[..., CODES.index(ANCHOR_TONE), None]


def fit_balance(g: dict, measured: dict, *, grid=None) -> dict:
    """The ONE balance, shared by every DECAY column, that minimises the worst
    |residual| in H - L referred to the anchor TONE.

    Worst-case, not least-squares, and not averaged: #369's rules forbid an
    averaged score, and a balance that is 0.2 dB out on 24 settings and 6 dB out
    on one has not reproduced the machine.
    """
    grid = BALANCE_DB if grid is None else np.asarray(grid, dtype=float)
    dd, ss = np.meshgrid(grid, grid, indexing="ij")
    w = np.stack([np.zeros_like(dd), dd, ss], axis=-1).reshape(-1, 3)
    pred = _anchored(predicted_hml(g, w))                      # (n_w, 5)
    cols = sorted(measured)
    missing = [(c, t) for c in cols for t in CODES if measured[c].get(t) is None]
    if missing:
        raise Refused("the measurement refused "
                      + ", ".join(f"CY{t}{c}" for c, t in missing[:5])
                      + (f" and {len(missing) - 5} more" if len(missing) > 5 else ""))
    obs = np.array([_anchored(np.array([measured[c][t] for t in CODES])) for c in cols])
    resid = pred[:, None, :] - obs[None, :, :]                 # (n_w, n_col, 5)
    worst = np.max(np.abs(resid), axis=(1, 2))
    i = int(np.argmin(worst))
    return {
        "worst_db": round(float(worst[i]), 3),
        "balance_db": {"low": 0.0, "decay": round(float(w[i, 1]), 2),
                       "short": round(float(w[i, 2]), 2)},
        "n_settings": int(obs.size),
        "per_column": {c: {"worst_db": round(float(np.max(np.abs(resid[i, j]))), 3),
                           "residual_db": [round(float(v), 3) for v in resid[i, j]],
                           "measured_db": [round(float(v), 3) for v in obs[j]],
                           "predicted_db": [round(float(v), 3) for v in pred[i]],
                           "measured_span_db": round(float(obs[j].max() - obs[j].min()), 3)}
                       for j, c in enumerate(cols)},
        "n_inside_bound": int(np.count_nonzero(worst <= BOUND_DB)),
        "n_balances": int(worst.size),
    }


def balance_region(g: dict, measured: dict, *, bound=BOUND_DB, grid=None) -> dict:
    """Which balances the measurement admits, as a region rather than a point.

    Reported because the point estimate above is not the honest answer to "what
    does this measure about the VCA drives": the TONE law constrains the
    DIFFERENCE between the two 7.1 kHz bands' weights much more tightly than it
    constrains either of them, and saying so is the difference between a
    measurement and a fit that looks like one.
    """
    grid = BALANCE_DB if grid is None else np.asarray(grid, dtype=float)
    dd, ss = np.meshgrid(grid, grid, indexing="ij")
    w = np.stack([np.zeros_like(dd), dd, ss], axis=-1).reshape(-1, 3)
    pred = _anchored(predicted_hml(g, w))
    cols = sorted(measured)
    obs = np.array([_anchored(np.array([measured[c][t] for t in CODES])) for c in cols])
    worst = np.max(np.abs(pred[:, None, :] - obs[None, :, :]), axis=(1, 2))
    ok = w[worst <= bound]
    if not len(ok):
        raise Refused(f"no balance in the swept grid reaches {bound} dB "
                      f"(best {float(worst.min()):.2f} dB)")
    diff = ok[:, 2] - ok[:, 1]
    return {"bound_db": bound, "n_inside": int(len(ok)), "n_balances": int(len(w)),
            "fraction_inside": round(len(ok) / len(w), 4),
            "decay_db": [round(float(ok[:, 1].min()), 2), round(float(ok[:, 1].max()), 2)],
            "short_db": [round(float(ok[:, 2].min()), 2), round(float(ok[:, 2].max()), 2)],
            "short_minus_decay_db": [round(float(diff.min()), 2), round(float(diff.max()), 2)],
            "grid_db": [float(grid[0]), float(grid[-1])]}


def shape_audit(measured: dict, *, column=None, cfg=None) -> dict:
    """How much the two free numbers can absorb, as a table rather than a claim.

    Each entry is a five-point H - L curve the family is fitted to with its own
    best balance. `machine` is the real column; the rest are synthetic, built
    from that column's own span. A family that reaches ALL of them would make the
    headline residual meaningless, and this is the measurement that says it does
    not -- three of the eight are out of reach, including the machine's own curve
    reversed.
    """
    col = column or ANCHOR_TONE
    real = [measured[col][t] for t in CODES]
    span = max(real) - min(real)
    curves = {
        "machine": real,
        "flat": [0.0] * len(CODES),
        "linear-ramp": [span * FRAC[t] for t in CODES],
        "machine-reversed": real[::-1],
        "convex": [0.0] * (len(CODES) - 1) + [span],
        "concave": [0.0] + [span] * (len(CODES) - 1),
        "step": [0.0, 0.0] + [span] * (len(CODES) - 2),
        "vee": [span, span / 2, 0.0, span / 2, span],
    }
    assert tuple(curves) == SHAPES, sorted(set(curves) ^ set(SHAPES))
    g = gain_table(cfg if cfg is not None else config())
    out = {}
    for name, y in curves.items():
        m = {d: {t: float(v) for t, v in zip(CODES, y)} for d in CODES}
        f = fit_balance(g, m)
        out[name] = {"worst_db": f["worst_db"], "balance_db": f["balance_db"],
                     "reachable": f["worst_db"] <= BOUND_DB,
                     "span_db": round(float(span), 3)}
    return out


def mapping_sweep(measured: dict, *, mappings=("linear", "sqrt", "square",
                                               "upper-half", "lower-half",
                                               "inverted", "fixed")) -> dict:
    """The residual of every candidate alpha(TONE), each with its own best
    balance. Swept rather than argued about: CLAUDE.md's rule, and the reason
    the section on what this does NOT resolve can carry numbers."""
    return {m: fit_balance(gain_table(mapping=m), measured) for m in mappings}


def alternatives(measured: dict, *, defect=None) -> dict:
    """Each structural alternative, fitted with the same two free numbers."""
    return {name: fit_balance(gain_table(config(defect=defect, alternative=name)), measured)
            for name in ALTERNATIVES}


# ---------------------------------------------------------------------------
# the machine's side
# ---------------------------------------------------------------------------
def from_artifact(path: pathlib.Path | None = None) -> dict:
    """{DECAY code: {TONE code: H - L dB}} out of the committed
    `fischer.json`, which `tools/cymbal_bands.py` produced and qualified.

    Read rather than re-measured so the headline property runs in CI without
    the ~30 MB corpus, and REFUSES rather than answering when the artifact is
    absent or does not carry all 25 settings.
    """
    p = path or FISCHER_JSON
    if not p.is_file():
        raise Refused(f"{p} is absent; run tools/cymbal_bands.py --out {p}")
    blob = json.loads(p.read_text())
    out = {}
    for d in CODES:
        row = {}
        for t in CODES:
            key = f"CY{t}{d}"
            if key not in blob or blob[key].get("H_minus_L_db") is None:
                raise Refused(f"{p} carries no H_minus_L_db for {key}")
            row[t] = float(blob[key]["H_minus_L_db"])
        out[d] = row
    return out


def decay_readings(path: pathlib.Path | None = None, *, cfg=None, defect=None) -> dict:
    """The decay quantities the committed artifact already holds, per column.

    Two independent checks of the same structure live here, and they pull in
    opposite directions, which is the point:
      * H's EDT10 must FALL with TONE. Nothing in the machine changes any band's
        own RC, so this can only happen if TONE shifts H's COMPOSITION toward the
        short band -- which is what a rising alpha does. It is a decay moving
        because an energy balance moved, so it is evidence independent of the
        energy fit rather than a restatement of it.
      * the low band's own EDT10 must NOT move with TONE beyond the time
        tolerance, because TONE cannot change the low band's RC at all.
    `EDT_BAND_SWAP` reads each from the other band and must turn both red.

    END TO END, not step by step. The fall is not strictly monotone: at DECAY 00
    the first two TONE positions read 139 and 143 ms, a 3 % rise, and four of the
    five columns have one such wiggle. The claim is therefore stated as the two
    things the structure actually requires -- a fall from TONE 0 to TONE full,
    and every local rise inside the time tolerance -- rather than as strict
    monotonicity, which the machine does not exhibit and which would have been an
    unsatisfiable gate. Wrong-then-right 1.
    """
    p = path or FISCHER_JSON
    if not p.is_file():
        raise Refused(f"{p} is absent; run tools/cymbal_bands.py --out {p}")
    cfg = cfg if cfg is not None else config(defect=defect)
    blob = json.loads(p.read_text())
    hi, lo = ("Ln", "H") if cfg["edt_swapped"] else ("H", "Ln")
    out = {}
    for d in CODES:
        h = [blob[f"CY{t}{d}"][hi]["edt10_ms"] for t in CODES]
        n = [blob[f"CY{t}{d}"][lo]["edt10_ms"] for t in CODES]
        if any(v is None for v in h + n):
            raise Refused(f"{p} refuses an EDT10 in column {d}")
        rises = [b / a - 1.0 for a, b in zip(h, h[1:]) if b > a]
        out[d] = {
            "h_edt10_ms": [round(float(v), 1) for v in h],
            "h_falls_end_to_end": h[-1] < h[0],
            "h_ratio_first_over_last": round(float(h[0] / h[-1]), 3),
            "h_worst_local_rise": round(float(max(rises, default=0.0)), 3),
            "low_edt10_ms": [round(float(v), 1) for v in n],
            "low_rel_spread": round(float((max(n) - min(n)) / np.mean(n)), 3),
        }
    return out


FLOOR_S = 0.100


def _floorful_energy(x, sr, t0, t1):
    """The window's energy, and the energy the record's own noise floor would
    put in a window of the same length.

    The floor is the mean power of the last FLOOR_S of the record, scaled to the
    analysed window's length -- `cymbal_bands.band_decay`'s own convention.
    NOT the energy of a same-length window at the end, which is what the first
    version used and which is wrong exactly where it matters: the 1 s window on a
    1.501 s record (every DECAY 00 setting) puts that "floor" window at
    0.5-1.5 s, still full of the cymbal's own tail, so the guard was comparing
    the signal against itself and read 14.7 dB. Wrong-then-right 3.
    """
    a, b = int(round(t0 * sr)), int(round(t1 * sr))
    n = b - a
    nf = max(4, int(round(FLOOR_S * sr)))
    if b > len(x) or n <= 0:
        raise Refused(f"window [{t0}, {t1}) s does not fit a {len(x) / sr:.3f} s band")
    if len(x) < nf + n:
        raise Refused(f"a {len(x) / sr:.3f} s band cannot carry both a "
                      f"{n / sr:.3f} s window and a {FLOOR_S:.3f} s floor estimate")
    return float(np.sum(x[a:b] ** 2)), float(np.mean(x[-nf:] ** 2)) * n


def measure_windows(refs: pathlib.Path, *, cfg=None, defect=None) -> dict:
    """H - L per setting per window, measured from the WAVs.

    The band-passes, the zero-phase filtering and the guaranteed lead are
    `cymbal_bands`' -- the qualified instrument -- so the `1s` window here is
    the same quantity the committed artifact carries and is checked against it.
    """
    refs = pathlib.Path(refs)
    if not (refs / "cy8").is_dir():
        raise Refused(f"the Fischer corpus is not present at {refs}")
    cfg = cfg if cfg is not None else config(defect=defect)
    bands = analysis_bands(cfg)
    out = {w: {d: {} for d in CODES} for w in WINDOWS}
    margins = []
    for t in CODES:
        for d in CODES:
            p = refs / "cy8" / f"CY{t}{d}.WAV"
            x, sr = cb._load(p)
            y = rc.prepare(x, sr, side=p.name)
            if cfg["short_record"]:
                y = y[:rc.required_lead_samples(sr) + int(0.25 * sr)]
            filt = {k: cb._bp(y, sr, lo, hi) for k, (lo, hi) in bands.items()}
            for w, (t0, t1) in WINDOWS.items():
                try:
                    e = {k: _floorful_energy(v, sr, t0, t1) for k, v in filt.items()}
                except Refused as exc:
                    out[w][d][t] = None
                    margins.append({"setting": f"CY{t}{d}", "window": w,
                                    "margin_db": None, "refused": str(exc)})
                    continue
                for k, (sig, flo) in e.items():
                    margins.append({"setting": f"CY{t}{d}", "window": w, "band": k,
                                    "margin_db": round(10 * math.log10(
                                        max(sig, 1e-30) / max(flo, 1e-30)), 2)})
                out[w][d][t] = round(10 * math.log10(e["H"][0] / e["L"][0]), 4)
    return {"hml": out, "floor_margins": margins,
            "min_floor_margin_db": min((m["margin_db"] for m in margins
                                        if m["margin_db"] is not None), default=None),
            "n_refused": sum(1 for m in margins if m["margin_db"] is None)}


def measured_or_refused(refs=None, *, cfg=None, defect=None):
    try:
        return measure_windows(refs or rc.configured_refs(), cfg=cfg, defect=defect), None
    except Refused as exc:
        return None, str(exc)


# ---------------------------------------------------------------------------
# properties
# ---------------------------------------------------------------------------
TONE_EXACT_DB = {"low": (-42.01, -51.76), "decay": (-20.46, -26.68),
                 "short": (-28.08, -33.66)}
TONE_EXACT_TOL_DB = 0.05


def _tone_exact(cfg) -> dict:
    """The tone term integrated here is the network the committed module
    reports, at its own fitted wiper position. The same filter written twice:
    anything but agreement is a transcription bug, so the bound is 0.05 dB and
    not a tolerance chosen to pass."""
    got, worst = {}, 0.0
    for band, want in TONE_EXACT_DB.items():
        v = tone_db(band, np.array(ts.CY_BANDS_HZ), ts.ALPHA_K1, cfg)
        got[band] = [round(float(x), 2) for x in v]
        worst = max(worst, max(abs(float(a) - b) for a, b in zip(v, want)))
    return {"value": round(worst, 4), "bound": TONE_EXACT_TOL_DB, "ok": worst <= TONE_EXACT_TOL_DB,
            "at_alpha_k1_db": got, "reported_db": {k: list(v) for k, v in TONE_EXACT_DB.items()}}


def _artifact_binds(meas, art) -> dict:
    """The re-measured 1 s H - L against the committed artifact, all 25
    settings. This is what makes the strike and tail windows statements about
    the same recordings the qualified instrument already measured."""
    if meas is None:
        return {"value": None, "bound": 0.01, "ok": None,
                "refused": "the corpus is absent, so nothing was re-measured"}
    worst, where = 0.0, None
    for d in CODES:
        for t in CODES:
            got = meas["hml"][ARTIFACT_WINDOW][d][t]
            if got is None:
                return {"value": None, "bound": 0.01, "ok": False,
                        "refused": f"the {ARTIFACT_WINDOW} window refused CY{t}{d}"}
            e = abs(got - art[d][t])
            if e > worst:
                worst, where = e, f"CY{t}{d}"
    return {"value": round(worst, 4), "bound": 0.01, "ok": worst <= 0.01, "worst_at": where}


def _floor_margin(meas) -> dict:
    if meas is None:
        return {"value": None, "bound": FLOOR_MARGIN_DB, "ok": None,
                "refused": "the corpus is absent"}
    v = meas["min_floor_margin_db"]
    return {"value": v, "bound": FLOOR_MARGIN_DB, "n_refused": meas["n_refused"],
            "ok": meas["n_refused"] == 0 and v is not None and v >= FLOOR_MARGIN_DB}


def _low_decay_tone_invariant(dec) -> dict:
    worst = max(dec[d]["low_rel_spread"] for d in CODES)
    return {"value": round(float(worst), 3), "bound": TIME_TOL, "ok": worst <= TIME_TOL,
            "per_column": {d: dec[d]["low_rel_spread"] for d in CODES}}


def _h_edt_falls(dec) -> dict:
    """H's EDT10 must fall with TONE by MORE than the low band's own spread.

    A bare "does it fall" is not enough and the control proved it: reading H's
    trend off the low band instead (`EDT_BAND_SWAP`) still passed, because the
    low band's EDT10 drifts ~10 % down the TONE column too. The quantity with
    discrimination in it is the CONTRAST -- H moves 15-69 % where the low band
    moves 2.5-9.7 %, and the two exchanged is what the swap produces. Stated as
    a ratio so the weakest column carries the verdict. Wrong-then-right 5.
    """
    per = {d: round(float((dec[d]["h_ratio_first_over_last"] - 1.0)
                          / max(dec[d]["low_rel_spread"], 1e-9)), 2) for d in CODES}
    n = sum(1 for d in CODES if dec[d]["h_falls_end_to_end"])
    worst = min(per.values())
    return {"value": round(worst, 2), "bound": 1.0,
            "ok": n == len(CODES) and worst > 1.0,
            "n_columns_falling": n, "n_columns": len(CODES),
            "per_column_h_over_low": per,
            "per_column_h_ratio": {d: dec[d]["h_ratio_first_over_last"] for d in CODES},
            "per_column_low_spread": {d: dec[d]["low_rel_spread"] for d in CODES}}


def _alpha_law(g, measured) -> dict:
    if measured is None:
        return {"value": None, "bound": BOUND_DB, "ok": None,
                "refused": "the corpus is absent, so this window was not measured"}
    try:
        f = fit_balance(g, measured)
    except Refused as exc:
        return {"value": None, "bound": BOUND_DB, "ok": None, "refused": str(exc)}
    return {"value": f["worst_db"], "bound": BOUND_DB, "ok": f["worst_db"] <= BOUND_DB,
            "balance_db": f["balance_db"],
            "n_settings": f["n_settings"],
            "min_measured_span_db": round(min(v["measured_span_db"]
                                              for v in f["per_column"].values()), 3)}


def _family_selective(measured, cfg=None) -> dict:
    """The family must reach the machine's curve AND miss at least one of the
    stated shapes. Both halves are needed: reaching everything would make the
    residual meaningless, and reaching nothing would mean the instrument is
    broken. A defect that breaks the law fails the first half, which is what
    makes this a property with a control rather than a printed table."""
    a = shape_audit(measured, cfg=cfg)
    n = sum(1 for v in a.values() if v["reachable"])
    return {"value": n, "bound": f"machine reachable, < {len(SHAPES)} of {len(SHAPES)}",
            "ok": a["machine"]["reachable"] and n < len(SHAPES),
            "machine_worst_db": a["machine"]["worst_db"],
            "unreachable": sorted(k for k, v in a.items() if not v["reachable"]),
            "per_shape_db": {k: v["worst_db"] for k, v in a.items()}}


def _alternative_fails(name, measured, cfg) -> dict:
    """A structural alternative must MISS the bound. Its value is its own best
    worst-residual, so "decisive" carries a size and not only a sign.

    The alternative transforms `cfg` -- the instrument as the defect left it --
    so under the matching defect the alternative becomes the TRUE structure, fits,
    and this property goes red. That composition is the whole mechanism by which
    these three properties are controls rather than assertions.
    """
    if measured is None:
        return {"value": None, "bound": BOUND_DB, "ok": None, "refused": "not measured"}
    alt = config(alternative=name, mapping=cfg["mapping"])
    alt["rails"] = _swap(cfg["rails"], *{"low-on-top-rail": ("low", "short"),
                                         "ht2-ht3-swapped": ("decay", "short")}
                         .get(name, ("low", "low")))
    alt["tone_flat"] = (not cfg["tone_flat"]) if name == "no-tone-network" else cfg["tone_flat"]
    alt["source_flat"], alt["bands_shifted"] = cfg["source_flat"], cfg["bands_shifted"]
    f = fit_balance(gain_table(alt), measured)
    return {"value": f["worst_db"], "bound": BOUND_DB, "ok": f["worst_db"] > BOUND_DB,
            "margin_db": round(f["worst_db"] - BOUND_DB, 3)}


def properties(*, refs=None, meas=None, defect=None) -> dict:
    """Every named property, with the defect (if any) threaded through the
    instrument's CONFIGURATION rather than through the data."""
    cfg = config(defect=defect)
    if meas is None and (cfg["bands_shifted"] or cfg["short_record"]):
        meas, _ = measured_or_refused(refs, cfg=cfg)
    art = from_artifact()
    dec = decay_readings(cfg=cfg)
    g = gain_table(cfg)
    art_1s = {d: art[d] for d in CODES}
    win = (meas or {}).get("hml", {})
    out = {
        "tone-exact": _tone_exact(cfg),
        "artifact-binds": _artifact_binds(meas, art),
        "floor-margin": _floor_margin(meas),
        "low-decay-tone-invariant": _low_decay_tone_invariant(dec),
        "h-edt-falls-with-tone": _h_edt_falls(dec),
        # the headline runs off the committed artifact, so it does not need the
        # corpus -- and the two windowed ones do, and say so when it is absent.
        "alpha-law-1s": _alpha_law(g, art_1s),
        "alpha-law-strike": _alpha_law(g, win.get("strike")),
        "alpha-law-tail": _alpha_law(g, win.get("tail")),
        "orientation-decisive": _alternative_fails("inverted", art_1s, cfg),
        "tone-load-bearing": _alternative_fails("no-tone-network", art_1s, cfg),
        "low-band-rail": _alternative_fails("low-on-top-rail", art_1s, cfg),
        "family-selective": _family_selective(art_1s, cfg),
    }
    assert set(out) == set(PROPERTIES), sorted(set(out) ^ set(PROPERTIES))
    return out


def check(*, refs=None, meas=None, require_corpus=False) -> tuple[bool, list[str]]:
    """The gate: every property, then every injected defect's row of the
    properties x defects matrix, then the two blind controls verified blind.

    A defect counts as caught when a property that PASSES on the clean run goes
    red under it -- and a property the defect makes REFUSE counts, because
    declining to answer is a detection, not a miss. A defect that moves nothing
    fails the gate: a control that cannot fail is worse than no control.
    """
    lines, ok = [], True
    clean = properties(refs=refs, meas=meas)
    lines.append(f"{'property':26s} {'value':>10s} {'bound':>8s}  verdict")
    for name in PROPERTIES:
        p = clean[name]
        verdict = "REFUSED" if p["ok"] is None else ("pass" if p["ok"] else "FAIL")
        if p["ok"] is False:
            ok = False
        lines.append(f"{name:26s} {str(p['value']):>10s} {str(p['bound']):>8s}  {verdict}"
                     + (f"   ({p['refused']})" if p.get("refused") else ""))
    lines.append("")
    lines.append("injected defect            properties it turns red")
    for d in DEFECTS + CORPUS_DEFECTS:
        if d in CORPUS_DEFECTS and meas is None:
            lines.append(f"{d:26s} NO VERDICT -- needs the Fischer corpus; "
                         f"run with --refs (make reference-integration)")
            if require_corpus:
                ok = False
            continue
        try:
            got = properties(refs=refs, meas=(None if d in CORPUS_DEFECTS else meas), defect=d)
        except Refused as exc:
            lines.append(f"{d:26s} NO VERDICT ({exc})")
            ok = False
            continue
        red = [n for n in PROPERTIES
               if clean[n]["ok"] is True and got[n]["ok"] is not True]
        undecidable = [n for n in PROPERTIES if clean[n]["ok"] is None]
        lines.append(f"{d:26s} {', '.join(red) if red else 'NOTHING -- this control cannot fail'}"
                     + (f"   [not decidable here: {len(undecidable)}]" if undecidable else ""))
        if not red:
            ok = False
        # rule 4's other half: the properties this defect is ASSERTED blind to
        # must actually be blind to it, or the assertion is decoration.
        for n in BLIND_PAIRS.get(d, ()):
            if clean[n]["ok"] is None:
                lines.append(f"{'':26s}   blind-to {n}: NO VERDICT (not decidable here)")
                continue
            same = got[n]["ok"] == clean[n]["ok"]
            lines.append(f"{'':26s}   blind-to {n}: "
                         + ("verified blind" if same else "MOVED -- the blindness claim is wrong"))
            if not same:
                ok = False
    lines.append("")
    for b in BLIND_BY_CONSTRUCTION:
        got = properties(refs=refs, meas=meas, defect=b)
        moved = [n for n in PROPERTIES
                 if clean[n]["ok"] is not None and got[n]["ok"] != clean[n]["ok"]]
        lines.append(f"{b:26s} blind by construction -- "
                     + ("verified blind" if not moved else f"MOVED {moved}"))
        if moved:
            ok = False
    return ok, lines


# ---------------------------------------------------------------------------
# the verdict
# ---------------------------------------------------------------------------
def verdict(*, refs=None, meas=None) -> dict:
    """What is resolved, what is not, and the number behind each."""
    art = from_artifact()
    g = gain_table()
    head = fit_balance(g, art)
    alts = alternatives(art)
    res = {
        "mapping": "alpha = TONE / 100 (VR4 is 20K(B), a linear taper -- SN p.13)",
        "rails": dict(RAILS),
        "bound_db": BOUND_DB,
        "headline": head,
        "region": balance_region(g, art),
        "alternatives": alts,
        "mapping_sweep": mapping_sweep(art),
        "decay": decay_readings(),
        "resolved": [], "not_resolved": [],
    }
    if head["worst_db"] <= BOUND_DB:
        res["resolved"].append(
            f"the tone network's wiper law reproduces the 808's H - L versus TONE at all "
            f"{head['n_settings']} settings to {head['worst_db']} dB worst case, against a "
            f"{BOUND_DB} dB bound and a measured span of at least "
            f"{min(v['measured_span_db'] for v in head['per_column'].values()):.2f} dB per column")
    else:
        res["refused"] = (f"alpha = TONE/100 misses the {BOUND_DB} dB bound by "
                          f"{head['worst_db'] - BOUND_DB:.2f} dB")
    for name, want in (("inverted", "the orientation (alpha rises with TONE)"),
                       ("no-tone-network", "the tone network as the mechanism"),
                       ("low-on-top-rail", "the low band's rail (bottom, with Ht2)")):
        a = alts[name]
        (res["resolved"] if a["worst_db"] > BOUND_DB else res["not_resolved"]).append(
            f"{want}: the '{name}' alternative reads {a['worst_db']} dB "
            f"({'outside' if a['worst_db'] > BOUND_DB else 'INSIDE'} the bound)")
    sw = res["mapping_sweep"]
    near = {m: v["worst_db"] for m, v in sw.items() if v["worst_db"] <= BOUND_DB}
    res["not_resolved"].append(
        "the exact shape of alpha(TONE): " + ", ".join(f"{m} {v}" for m, v in sorted(near.items(), key=lambda kv: kv[1]))
        + f" -- {len(near)} of {len(sw)} candidate mappings are inside the bound, so the linear "
          "taper is the best of them and is what the component marking predicts, but it is not "
          "the only one the recordings admit")
    res["not_resolved"].append(
        "the Ht2/Ht3 rail assignment: 'ht2-ht3-swapped' reads "
        f"{alts['ht2-ht3-swapped']['worst_db']} dB against the prediction's "
        f"{head['worst_db']} dB -- blind by construction (both bands share the 7.1 kHz "
        "band-pass and both land in H, so the fitted balance absorbs the swap)")
    r = res["region"]
    res["not_resolved"].append(
        f"the inter-band balance: inside {BOUND_DB} dB the admitted region spans "
        f"{r['decay_db']} dB on the decay band and {r['short_db']} dB on the short band "
        f"(the whole swept grid in both), and only their difference is constrained, to "
        f"{r['short_minus_decay_db']} dB. So this does NOT lift the balance refusal of #396")
    if meas is not None:
        res["windows"] = {w: fit_balance(g, meas["hml"][w]) for w in WINDOWS}
    return res


def record(*, refs=None) -> dict:
    meas, why = measured_or_refused(refs)
    ok, lines = check(refs=refs, meas=meas)
    out = {"controls_pass": ok, "check_report": lines,
           "properties": properties(refs=refs, meas=meas),
           "verdict": verdict(refs=refs, meas=meas),
           "windows_refused": why,
           "measured_windows": (None if meas is None else
                                {"hml": meas["hml"],
                                 "min_floor_margin_db": meas["min_floor_margin_db"],
                                 "n_refused": meas["n_refused"]})}
    out["commit"] = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT,
                                   capture_output=True, text=True).stdout.strip()
    out["sources_dirty"] = subprocess.run(
        ["git", "diff", "--quiet", "HEAD", "--", "model", "tools"], cwd=ROOT).returncode != 0
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--refs", default=None,
                    help="the Fischer corpus; without it the two windowed properties REFUSE")
    ap.add_argument("--out", type=pathlib.Path, default=None)
    ap.add_argument("--check", action="store_true", help="the gate only")
    a = ap.parse_args(argv)

    assert BOUND_DB == tr.SHAPE_BOUND_DB, (BOUND_DB, tr.SHAPE_BOUND_DB)
    assert FLOOR_MARGIN_DB == cb.TRUNC_MARGIN_DB, (FLOOR_MARGIN_DB, cb.TRUNC_MARGIN_DB)

    refs = a.refs
    meas, why = measured_or_refused(refs)
    ok, lines = check(refs=refs, meas=meas)
    print("\n".join(lines))
    if why:
        print(f"\n(the windowed measurement REFUSED: {why})")
    if a.check:
        return 0 if ok else 1
    if not ok:
        print("\nREFUSED: this instrument's own controls do not pass")
        return 1

    v = verdict(refs=refs, meas=meas)
    print(f"\nmapping: {v['mapping']}\n")
    h = v["headline"]
    print(f"one balance for all {h['n_settings']} settings: decay {h['balance_db']['decay']:+.1f} dB, "
          f"short {h['balance_db']['short']:+.1f} dB (re the low band); worst residual "
          f"{h['worst_db']:.2f} dB against a {BOUND_DB} dB bound")
    print(f"\n{'DECAY':6s} {'span':>6s} {'worst':>6s}   residual per TONE "
          f"({'/'.join(CODES)})")
    for d, c in sorted(h["per_column"].items()):
        print(f"{d:6s} {c['measured_span_db']:6.2f} {c['worst_db']:6.2f}   "
              + " ".join(f"{x:+5.2f}" for x in c["residual_db"]))
    print("\nstructural alternatives, each with the same two free numbers:")
    for name, alt in sorted(v["alternatives"].items(), key=lambda kv: -kv[1]["worst_db"]):
        print(f"  {name:18s} worst {alt['worst_db']:6.2f} dB  "
              + ("FAILS the bound (so it is excluded)" if alt["worst_db"] > BOUND_DB
                 else "inside the bound (NOT excluded by this measurement)"))
    print("\nalpha(TONE) candidates swept:")
    for m, f in sorted(v["mapping_sweep"].items(), key=lambda kv: kv[1]["worst_db"]):
        print(f"  {m:12s} worst {f['worst_db']:6.2f} dB")
    if "windows" in v:
        print("\nthe same law in the two windows the corpus is needed for:")
        for w, f in v["windows"].items():
            print(f"  {w:7s} worst {f['worst_db']:6.2f} dB  balance decay "
                  f"{f['balance_db']['decay']:+.1f} short {f['balance_db']['short']:+.1f}")
    print("\nRESOLVED:")
    for s in v["resolved"]:
        print(f"  * {s}")
    print("NOT RESOLVED:")
    for s in v["not_resolved"]:
        print(f"  * {s}")
    if a.out:
        res = record(refs=refs)
        a.out.parent.mkdir(parents=True, exist_ok=True)
        a.out.write_text(json.dumps(res, indent=1, default=float) + "\n")
        print(f"\nwrote {a.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
