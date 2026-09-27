#!/usr/bin/env python3
"""Read the TR-808 cymbal's TONE STAGE off Werner 2014 Figure 9.

WHY THIS EXISTS. The tone stage is the last block of `docs/tr808-reference.md`
§10 that the model does not have at all, and after #369 step 3 it was the only
remaining suspect for the candidate's 15 dB shortfall below 2.5 kHz
(`docs/scorecard/cymbal-369/candidate2/README.md` §4, filed as #390). W14b §10
describes it as "a highly-interconnected passive network of resistors and
capacitors" giving three FIFTH-ORDER transfer functions Ht1 = Vtone/Vh1,
Ht2 = Vtone/Vh2, Ht3 = Vtone/Vh3, declines to print their coefficients ("far
too lengthy"), and points at a companion site that returns 404.

But W14b **Figure 9 plots all three families**, for tone control k in
[0.01, 1.0], and that figure is a vector XObject in the same PDF as Figure 4.
So the same instrument reaches it. `tools/werner_fig4.calibrate()` does the
axis work here -- literally the same function, which is most of the gate
argument below.

WHAT THE GATE IS, AND WHAT IT IS NOT. Figure 4's digitiser is gated on three
filters whose answers come from resistors and capacitors on SN p.13. **Figure 9
has no such answer**: the paper prints no component values for the tone
network, no other source states its response, and the machine recording cannot
separate the three paths. There is therefore **no external known answer for
this figure**, and `--check` does not pretend otherwise. What it does have:

  1. SHARED MACHINERY. The axis calibration is `werner_fig4.calibrate()`, which
     Figure 4's external gate exercises on every run. An axis bug shows on
     Figure 4 first. (Weaker than a known answer: it covers the calibration,
     not the curve assembly or the naming.)
  2. PASSIVITY. The tone stage is a passive RC network, so |Ht_i| <= 1 at every
     frequency. Every digitised point must be <= 0 dB. This only catches a
     level error larger than 14.5 dB, which is stated here so nobody mistakes
     it for a tight bound.
  3. ONE X AXIS, THREE TIMES. Each sub-plot's x calibration is derived
     independently from its own labels and its own grid, and the three must
     agree to 0.1 %. This is what catches the failure the multi-axes extension
     introduces -- one sub-plot reading another's labels.
  4. THE MARKER IS ONE GLYPH. W14b's caption marks the k = 1.0 member of each
     family with an asterisk. The offset from a `(*)Tj` origin to the marked
     curve's peak is a property of the glyph, so it must be the SAME vector in
     all three sub-plots. Requiring that makes the k = 1.0 identification
     over-determined by two constraints instead of a guess -- and it is not a
     formality: in two of the three sub-plots the marked curve is NOT the
     topmost one.
  5. W14b's OWN PROSE. §10: "The primary effect of the tone control is to
     change the amount of attenuation in the third band. However, the center
     frequencies of all bands and the attenuation of the first and second
     bands are also somewhat affected." Exactly one family must therefore vary
     by a lot with k and the other two by a little. This is what names the
     sub-plots, because **the figure's own legend is wrong** -- see below.
  6. AN EXTRAPOLATION CONTROL THAT USES A KNOWN ANSWER FROM INSIDE THE FIGURE.
     Ht1's and Ht2's sub-plots are 4 dB and 3 dB tall, so those two curves
     leave the axes at 683 Hz and 1.86 kHz and the cymbal's own band is not
     plotted for them. Reaching 7.1 kHz means extrapolating a fitted section
     past the data. Ht3's sub-plot is 36 dB tall and IS plotted across
     20 Hz - 20 kHz, so the same procedure can be run on a curve whose answer
     is known: fit only its top 3 dB, extrapolate, compare. That is a genuine
     known-answer test of the extrapolation, and it is the only one available.

THE FIGURE'S LEGEND IS WRONG, AND THIS TOOL SAYS SO. The three legend labels
read "Ht3", "Ht2", "Ht3" top to bottom -- `t3` twice and no `t1`. The bottom
sub-plot carries two further label errors in the same figure ("magnitude
(hertz)" where the x title should read "frequency (hertz)", "amplitude (dB)"
where the y title should read "magnitude (dB)"), so it is the sub-plot whose
labels are unreliable. Naming is therefore done by rule 5, cross-checked
against whichever legend labels are unambiguous, and the discrepancy is
reported rather than silently patched.

Usage:
    python3 tools/werner_fig9.py --check          # the gate, from the evidence
    python3 tools/werner_fig9.py --fit            # the three transfer functions
    python3 tools/werner_fig9.py --from-pdf --json docs/.../werner-fig9.json
"""

from __future__ import annotations

import argparse
import json
import math
import os
import pathlib
import sys

import numpy as np

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import werner_fig4 as wf  # noqa: E402

Refused = wf.Refused

# W14b §10, quoted in full in the module docstring. The two thresholds turn
# "a lot" and "a little" into numbers; the measured spread is 29 dB for the
# third band and under 1 dB for the other two, so neither bound is marginal.
K_SPREAD_BIG_DB = 10.0
K_SPREAD_SMALL_DB = 3.0

# The asterisk is set in a 36 pt font, so its ink centre stands about 19 pt
# above the `Td` baseline and about 7 pt right of it. Those numbers are NOT
# assumed here -- they are solved for, and this is only how far apart two
# sub-plots' solutions may be before the identification is refused. The bound
# is not marginal in either direction: the winning assignment agrees to
# 1.32 pt and the next best of the 125 to 17.17 pt.
MARKER_AGREE_PT = 4.0

# How much worse than the plain 2-pole fit an alternative section may be and
# still count as "this window cannot tell them apart", in `extrapolation_bound`.
# Tightening it narrows the reported bound, so it is stated rather than tuned.
ALT_RMS_FACTOR = 3.0

# A curve must span at least this fraction of the axes width, and carry at
# least this many points, to be a plotted response rather than a legend rule
# or a marker glyph.
MIN_SPAN_FRAC = 0.05
MIN_POINTS = 50


# ---------------------------------------------------------------------------
# Finding the figure
# ---------------------------------------------------------------------------


def find_figure(pdf: bytes):
    """Figure 9: three asterisks (Figure 10's caption marks exactly one) and
    three axes boxes of equal area."""
    num, text = wf._figure(
        pdf, ["(magnitude", "(*)Tj", "(t2)Tj"],
        "a magnitude-response figure with an asterisk marker and an Ht2 "
        "legend label (Figure 9)",
    )
    n = text.count("(*)Tj")
    if n != 3:
        raise Refused(
            f"that figure carries {n} asterisks; Figure 9 marks three, one "
            "k = 1.0 response per band"
        )
    return num, text


def _plot_boxes(rects):
    """Figure 9's three stacked sub-plot boxes, top first.

    They are equal in area to a part in 10^3; the legend boxes are twenty
    times smaller and the page background twice as large, so requiring three
    equal-area boxes is a real assertion about having found the right figure.
    """
    boxes = wf.axes_boxes(rects)
    areas = [(b[1][0] - b[0][0]) * (b[1][1] - b[0][1]) for b in boxes]
    big = [b for b, a in zip(boxes, areas) if a > 0.5 * max(areas)]
    if len(big) != 3:
        raise Refused(
            f"expected three equal-area sub-plot boxes, found {len(big)} "
            f"(areas {[round(a) for a in areas]})"
        )
    a = [(b[1][0] - b[0][0]) * (b[1][1] - b[0][1]) for b in big]
    if (max(a) - min(a)) / max(a) > 1e-3:
        raise Refused(f"the three sub-plot boxes differ in area: {a}")
    return sorted(big, key=lambda b: -b[0][1])


def _legend_boxes(rects):
    boxes = wf.axes_boxes(rects)
    areas = [(b[1][0] - b[0][0]) * (b[1][1] - b[0][1]) for b in boxes]
    return [b for b, ar in zip(boxes, areas) if ar <= 0.5 * max(areas)]


# ---------------------------------------------------------------------------
# Curve assembly
# ---------------------------------------------------------------------------


def chain(pieces, eps=1e-3):
    """Join polyline fragments into runs by matching endpoints, in any order.

    `werner_fig4._join` only joins a fragment to the run immediately before
    it, which is enough for Figure 4's five curves. Figure 9 interleaves
    fifteen curves' 300-point chunks, and a chunk's partner can be anywhere
    in the stream, so the match has to be global.
    """
    pieces = [list(p) for p in pieces]
    used = [False] * len(pieces)
    out = []
    for i, first in enumerate(pieces):
        if used[i]:
            continue
        used[i] = True
        run = list(first)
        grew = True
        while grew:
            grew = False
            for j, q in enumerate(pieces):
                if used[j]:
                    continue
                for cand in (q, list(reversed(q))):
                    if _same(run[-1], cand[0], eps):
                        run.extend(cand[1:])
                    elif _same(run[0], cand[-1], eps):
                        run[:0] = cand[:-1]
                    else:
                        continue
                    used[j] = True
                    grew = True
                    break
                if grew:
                    break
        out.append(run)
    return out


def _same(a, b, eps):
    return abs(a[0] - b[0]) < eps and abs(a[1] - b[1]) < eps


def _inside(pts, box, tol=wf.BOX_TOL):
    (x0, y0), (x1, y1) = box
    return all(x0 - tol <= x <= x1 + tol and y0 - tol <= y <= y1 + tol
               for x, y in pts)


def subplot_curves(polys, rects, texts, box):
    """The family plotted in one sub-plot, as (hz, db) pairs, peak first."""
    cal = wf.calibrate(polys, rects, texts, box=box)
    (bx0, by0), (bx1, by1) = box
    legends = _legend_boxes(rects)
    pieces = [p for _c, p in polys
              if len(p) >= 3 and _inside(p, box)
              and not any(_inside(p, lb) for lb in legends)]
    runs = [r for r in chain(pieces) if len(r) >= MIN_POINTS
            and max(x for x, _ in r) - min(x for x, _ in r)
            > MIN_SPAN_FRAC * (bx1 - bx0)]
    out = []
    for r in runs:
        x = np.array([p[0] for p in r])
        y = np.array([p[1] for p in r])
        order = np.argsort(x)
        x, y = x[order], y[order]
        hz = np.asarray(cal["x_to_hz"](x), dtype=float)
        db = np.asarray(cal["y_to_db"](y), dtype=float)
        _, keep = np.unique(np.round(hz, 6), return_index=True)
        out.append((hz[keep], db[keep], float(x[int(np.argmax(y))]),
                    float(y.max())))
    out.sort(key=lambda t: -t[3])
    return out, cal


def asterisk_origins(texts, boxes):
    """The `(*)Tj` origin inside each sub-plot, top first."""
    marks = [p for t, p in texts if t == "*"]
    if len(marks) != 3:
        raise Refused(f"expected three asterisk glyphs, found {len(marks)}")
    out = []
    for (bx0, by0), (bx1, by1) in boxes:
        here = [m for m in marks if bx0 <= m[0] <= bx1
                and by0 - 0.35 * (by1 - by0) <= m[1] <= by1]
        if len(here) != 1:
            raise Refused(
                f"sub-plot {(bx0, by0)} holds {len(here)} asterisk glyphs; "
                "expected exactly one"
            )
        out.append(here[0])
    return out


def identify_marked(families, origins):
    """Which member of each family is k = 1.0.

    The rule, stated before any number is read: the asterisk is a glyph, so
    the vector from its `Td` origin to the peak of the curve it marks is the
    same in all three sub-plots. Choose the one assignment whose three vectors
    agree; refuse if none does, or if more than one does.

    This matters. Assuming "the marked curve is the top one" gets Ht3 right
    and Ht1 and Ht2 wrong -- in those two the asterisk sits on the LOWEST
    member, and the k = 1.0 response is the most attenuated, not the least.
    """
    import itertools

    best = None
    tied = 0
    for pick in itertools.product(*[range(len(f)) for f in families]):
        vecs = [(families[i][p][2] - origins[i][0],
                 families[i][p][3] - origins[i][1])
                for i, p in enumerate(pick)]
        spread = max(math.hypot(a[0] - b[0], a[1] - b[1])
                     for a in vecs for b in vecs)
        if spread > MARKER_AGREE_PT:
            continue
        tied += 1
        if best is None or spread < best[0]:
            best = (spread, pick, vecs)
    if best is None:
        raise Refused(
            "no choice of one curve per sub-plot puts the three asterisks at "
            f"the same offset from their peaks to within {MARKER_AGREE_PT} pt; "
            "the k = 1.0 members cannot be identified"
        )
    if tied > 1:
        raise Refused(
            f"{tied} different assignments agree to {MARKER_AGREE_PT} pt; the "
            "asterisks do not identify the k = 1.0 members uniquely"
        )
    return best[1], best[2], best[0]


# ---------------------------------------------------------------------------
# Naming the three bands
# ---------------------------------------------------------------------------


def legend_labels(texts, boxes):
    """The `Ht?` subscript printed in each sub-plot's legend, top first."""
    out = []
    for (bx0, by0), (bx1, by1) in boxes:
        here = sorted(t for t, p in texts
                      if t.startswith("t") and len(t) == 2 and t[1].isdigit()
                      and by0 <= p[1] <= by1)
        out.append(here[0] if len(here) == 1 else None)
    return out


def name_families(spreads, legends):
    """Assign Ht1/Ht2/Ht3 to the three sub-plots.

    By W14b §10 (rule 5 in the module docstring): exactly one family varies a
    lot with k and that one is the THIRD band. The remaining two are told
    apart by their legend labels where those are unambiguous, and by
    elimination where they are not.
    """
    big = [i for i, s in enumerate(spreads) if s >= K_SPREAD_BIG_DB]
    small = [i for i, s in enumerate(spreads) if s <= K_SPREAD_SMALL_DB]
    if len(big) != 1 or len(small) != 2:
        raise Refused(
            "W14b §10 requires exactly one band whose attenuation the tone "
            "control changes a lot and two it changes a little; the measured "
            f"spreads are {[round(s, 2) for s in spreads]} dB"
        )
    names = [None, None, None]
    names[big[0]] = "Ht3"
    notes = []
    if legends[big[0]] not in (None, "t3"):
        notes.append(
            f"the legend of the sub-plot that varies most reads 'H{legends[big[0]]}', "
            "but W14b §10 puts the tone control's primary effect on the third "
            "band; the prose is taken over the legend"
        )
    rest = [i for i in range(3) if i != big[0]]
    for want in ("Ht2", "Ht1"):
        tag = "t" + want[-1]
        hit = [i for i in rest if legends[i] == tag and names[i] is None]
        if len(hit) == 1:
            names[hit[0]] = want
    left = [i for i in rest if names[i] is None]
    missing = [n for n in ("Ht1", "Ht2") if n not in names]
    if len(left) == 1 and len(missing) == 1:
        names[left[0]] = missing[0]
        notes.append(
            f"sub-plot {left[0] + 1} of 3 is {missing[0]} by elimination: its "
            f"legend reads 'H{legends[left[0]]}', which is already taken. The "
            "same sub-plot also titles its x axis 'magnitude (hertz)' and its "
            "y axis 'amplitude (dB)', so it carries three label errors, not one"
        )
    elif left:
        raise Refused(
            f"cannot name sub-plots {left}: legend labels {legends} do not "
            "identify them and elimination is ambiguous"
        )
    return names, notes


# ---------------------------------------------------------------------------
# Reading a transfer function off one digitised curve
# ---------------------------------------------------------------------------


def bp2_db(hz, gain_db, f0, q):
    w = 2.0 * np.pi * np.asarray(hz, dtype=float)
    return gain_db + wf._db(wf._biquad(w, f0, q, "bandpass"))


def _extra_section_db(hz, gain_db, f0, q, f_extra, kind):
    """A 2-pole band-pass with one extra real pole or zero at `f_extra`."""
    w = 2.0 * np.pi * np.asarray(hz, dtype=float)
    base = wf._biquad(w, f0, q, "bandpass")
    we = 2.0 * np.pi * f_extra
    extra = we / (1j * w + we) if kind == "pole" else (1j * w + we) / we
    return gain_db + wf._db(base * extra)


def fit_bp2(hz, db):
    """The section every one of Figure 9's fifteen curves turns out to be."""
    return wf.fit_structure(np.asarray(hz), np.asarray(db), "bp2")


def bp2_poles(f0, q):
    """The two pole frequencies of a 2-pole band-pass, in Hz.

    Every one of Figure 9's curves reads Q < 0.5, so both poles are real and
    the section is one RC high-pass cascaded with one RC low-pass -- which is
    what a passive RC network can build.
    """
    if q >= 0.5:
        r = math.sqrt(1.0 / (4 * q * q) - 1 + 0j)
        return (f0 * abs(1 / (2 * q) - r), f0 * abs(1 / (2 * q) + r))
    r = math.sqrt(1.0 / (4 * q * q) - 1.0)
    return (f0 * (1 / (2 * q) - r), f0 * (1 / (2 * q) + r))


def shared_denominator_note(data):
    """Three transfer functions of ONE network to ONE output node share a
    denominator -- the network determinant -- so they have the SAME poles.
    The three windows' 2-pole fits do not agree on a pole pair, so at most one
    of them can be the network's in-band pair.

    That is not a contradiction: a 2-pole fit over a 3 dB window is a local
    shape, not a pole location. It IS the reason the Ht1 and Ht2 numbers must
    be quoted with `extrapolation_bound`'s spread and never as circuit values.
    """
    rows = []
    for name in ("Ht1", "Ht2", "Ht3"):
        v = data[name]
        hz, db = v["curves"][v["k1_index"]]
        fit = fit_bp2(hz, db)
        rows.append((name, fit["f0"], fit["q"], bp2_poles(fit["f0"], fit["q"])))
    return rows


def extrapolation_bound(hz, db, f_target, grid=None):
    """How much the value at `f_target` can move if the section is not a bp2.

    A 5th-order transfer function seen through a 3 dB window is not pinned
    down by a 2-pole fit, however small that fit's residual is. This asks the
    only answerable version of the question: for a real pole or a real zero
    placed at each frequency in `grid`, refit, and keep the ones the window
    cannot tell apart from the plain bp2 -- residual within 3x the plain fit's.
    The spread of what those alternatives predict at `f_target` is the bound.

    The bound is reported, not hidden inside a single number, because for Ht1
    and Ht2 it is several dB wide and that is the honest state of the evidence.
    """
    from scipy.optimize import least_squares

    hz = np.asarray(hz, dtype=float)
    db = np.asarray(db, dtype=float)
    base = fit_bp2(hz, db)
    nominal = float(bp2_db(f_target, base["gain_db"], base["f0"], base["q"]))
    if grid is None:
        grid = np.logspace(math.log10(max(hz.max() * 0.7, 500.0)),
                           math.log10(200e3), 25)
    lo = hi = nominal
    admitted = []
    for kind in ("pole", "zero"):
        for fe in grid:
            def resid(p, fe=fe, kind=kind):
                return _extra_section_db(hz, p[0], p[1], p[2], fe, kind) - db
            res = least_squares(
                resid, [db.max(), hz[int(np.argmax(db))], 0.4],
                bounds=([-90.0, hz.min() * 0.05, 0.2],
                        [10.0, hz.max() * 20.0, 5.0]),
                xtol=1e-13, ftol=1e-13)
            rms = float(np.sqrt(np.mean(res.fun ** 2)))
            if rms > ALT_RMS_FACTOR * max(base["rms_db"], 1e-4):
                continue
            val = float(_extra_section_db(np.array([f_target]), *res.x,
                                          fe, kind)[0])
            admitted.append((kind, float(fe), rms, val))
            lo, hi = min(lo, val), max(hi, val)
    return {"f_hz": float(f_target), "nominal_db": nominal,
            "lo_db": lo, "hi_db": hi,
            "bp2_rms_db": base["rms_db"],
            "n_alternatives": len(admitted)}


def truncation_control(hz, db, window_db=3.0, at_hz=(3450.0, 7100.0, 20000.0)):
    """THE known-answer test for the extrapolation, from inside the figure.

    Ht3's sub-plot is 36 dB tall, so its curve is plotted across the whole
    band. Throw all but the top `window_db` of it away -- the same view the
    figure gives of Ht1 and Ht2 -- fit, extrapolate, and compare against the
    part that was thrown away. The answer is known and was not used by the fit.
    """
    hz = np.asarray(hz, dtype=float)
    db = np.asarray(db, dtype=float)
    keep = db >= db.max() - window_db
    if keep.sum() < 50:
        raise Refused(
            f"only {int(keep.sum())} points lie within {window_db} dB of the "
            "peak; too few to run the truncation control")
    fit = fit_bp2(hz[keep], db[keep])
    rows = []
    for f in at_hz:
        if not (hz.min() <= f <= hz.max()):
            continue
        truth = float(np.interp(math.log10(f), np.log10(hz), db))
        got = float(bp2_db(f, fit["gain_db"], fit["f0"], fit["q"]))
        rows.append({"hz": float(f), "truth_db": truth, "extrapolated_db": got,
                     "error_db": got - truth})
    if not rows:
        raise Refused(
            f"none of {at_hz} lies inside the plotted range "
            f"{hz.min():.1f}-{hz.max():.1f} Hz, so there is nothing to check "
            "the extrapolation against")
    return {"window_db": window_db,
            "window_hz": [float(hz[keep].min()), float(hz[keep].max())],
            "n_points": int(keep.sum()), "fit": fit, "points": rows,
            "worst_error_db": max(abs(r["error_db"]) for r in rows)}


# The bound the truncation control has to meet. 0.5 dB is loose against the
# 0.37 dB it actually reads and tight against the 14 dB of inter-band
# difference the extrapolated values are used to argue about.
TRUNCATION_TOL_DB = 0.5


# ---------------------------------------------------------------------------
# The whole figure
# ---------------------------------------------------------------------------


def read_figure(pdf: bytes):
    _, text = find_figure(pdf)
    polys, rects, texts = wf.parse_content(text)
    boxes = _plot_boxes(rects)
    fams, cals = [], []
    for box in boxes:
        fam, cal = subplot_curves(polys, rects, texts, box)
        if len(fam) < 2:
            raise Refused(
                f"sub-plot at {box} assembled {len(fam)} curves; a family "
                "needs at least two")
        fams.append(fam)
        cals.append(cal)
    # Rule 3: the three x calibrations are derived independently and must agree.
    pd = [c["pt_per_decade"] for c in cals]
    lo = [c["xlim"][0] for c in cals]
    if (max(pd) - min(pd)) / max(pd) > 1e-3 or (max(lo) - min(lo)) / max(lo) > 1e-3:
        raise Refused(
            "the three sub-plots do not share an x axis: pt/decade "
            f"{[round(v, 3) for v in pd]}, left edge "
            f"{[round(v, 2) for v in lo]} Hz"
        )
    origins = asterisk_origins(texts, boxes)
    pick, vecs, spread = identify_marked(fams, origins)
    spreads = [float(max(c[1].max() for c in f) - min(c[1].max() for c in f))
               for f in fams]
    names, notes = name_families(spreads, legend_labels(texts, boxes))
    out = {}
    for i, name in enumerate(names):
        out[name] = {
            "curves": [(c[0], c[1]) for c in fams[i]],
            "k1_index": int(pick[i]),
            "peak_spread_db": float(spreads[i]),
            "axes_hz": list(cals[i]["xlim"]),
            "axes_db": list(cals[i]["ylim"]),
            "subplot": i,
        }
    meta = {
        "marker_offset_pt": [round(float(v), 3) for v in vecs[0]],
        "marker_agreement_pt": float(spread),
        "legend_notes": notes,
        "legend_labels": legend_labels(texts, boxes),
        "pt_per_decade": float(pd[0]),
    }
    return out, meta


# ---------------------------------------------------------------------------
# The gate
# ---------------------------------------------------------------------------


def check(data, meta) -> tuple[bool, list[str]]:
    lines = []
    ok = True

    # 1. shared machinery -- Figure 4's external known-answer gate
    try:
        fig4, _ = wf.from_artifact()
        ok4, _ = wf.check(fig4)
    except Refused as exc:
        ok4 = False
        lines.append(f"       ({exc})")
    ok = ok and ok4
    lines.append(
        f"{'PASS' if ok4 else 'FAIL'} shared    werner_fig4.calibrate() still "
        "reproduces Figure 4's three schematic-derived filters "
        "(EXTERNAL, but it gates the axis code, not this figure's curves)")

    # 2. passivity
    worst = max(float(np.max(d)) for v in data.values() for _, d in v["curves"])
    good = worst <= 0.0
    ok = ok and good
    lines.append(
        f"{'PASS' if good else 'FAIL'} passive   loudest plotted point "
        f"{worst:+.2f} dB <= 0 dB, as a passive RC network must be "
        "(WEAK: only catches a level error above 14.5 dB)")

    # 3. one x axis, three times -- asserted in read_figure(), restated here
    lo = [v["axes_hz"][0] for v in data.values()]
    hi = [v["axes_hz"][1] for v in data.values()]
    good = (max(lo) - min(lo)) / max(lo) < 1e-3 and (max(hi) - min(hi)) / max(hi) < 1e-3
    ok = ok and good
    lines.append(
        f"{'PASS' if good else 'FAIL'} one-x     the three sub-plots' "
        f"independently derived x axes agree: {min(lo):.1f}-{max(hi):.1f} Hz")

    # 4. the marker is one glyph
    good = meta["marker_agreement_pt"] <= MARKER_AGREE_PT
    ok = ok and good
    lines.append(
        f"{'PASS' if good else 'FAIL'} marker    one glyph offset "
        f"({meta['marker_offset_pt'][0]:+.2f}, {meta['marker_offset_pt'][1]:+.2f}) pt "
        f"fits all three asterisks to {meta['marker_agreement_pt']:.2f} pt "
        f"(<= {MARKER_AGREE_PT})")
    for name in ("Ht1", "Ht2", "Ht3"):
        v = data[name]
        n = len(v["curves"])
        lines.append(
            f"       {name}: k = 1.0 is member {v['k1_index'] + 1} of {n} "
            f"counting down from the top"
            + ("  <- NOT the topmost" if v["k1_index"] else ""))

    # 5. W14b's own prose
    sp = {k: data[k]["peak_spread_db"] for k in ("Ht1", "Ht2", "Ht3")}
    good = (sp["Ht3"] >= K_SPREAD_BIG_DB
            and max(sp["Ht1"], sp["Ht2"]) <= K_SPREAD_SMALL_DB)
    ok = ok and good
    lines.append(
        f"{'PASS' if good else 'FAIL'} prose     the tone control moves the "
        f"third band {sp['Ht3']:.1f} dB and the first two "
        f"{sp['Ht1']:.1f} / {sp['Ht2']:.1f} dB "
        "(W14b §10: \"primary effect … in the third band\", the other two "
        "\"also somewhat affected\")")

    # 6. the extrapolation control
    hz, db = data["Ht3"]["curves"][data["Ht3"]["k1_index"]]
    try:
        tc = truncation_control(hz, db)
        good = tc["worst_error_db"] <= TRUNCATION_TOL_DB
        detail = (f"worst error {tc['worst_error_db']:.2f} dB at "
                  f"{max(tc['points'], key=lambda r: abs(r['error_db']))['hz']:.0f} Hz")
    except Refused as exc:
        good, detail = False, str(exc)
    ok = ok and good
    lines.append(
        f"{'PASS' if good else 'FAIL'} extrap    fitting only Ht3's top 3 dB "
        f"(the view Figure 9 gives of Ht1/Ht2) and extrapolating to 20 kHz: "
        f"{detail} (<= {TRUNCATION_TOL_DB} dB)")
    for note in meta["legend_notes"]:
        lines.append(f"NOTE   {note}")
    return ok, lines


# ---------------------------------------------------------------------------
# Evidence file
# ---------------------------------------------------------------------------


ARTIFACT = pathlib.Path(__file__).resolve().parents[1] / \
    "docs" / "scorecard" / "cymbal-369" / "werner-fig9.json"
DECIMATE = 6


def to_artifact(data, meta) -> dict:
    return {
        "source": {
            "paper": "K. J. Werner, J. S. Abel, J. O. Smith, \"The TR-808 "
                     "Cymbal\", ICMC|SMC 2014 (W14b), Figure 9",
            "url": wf.W14B_URL,
            "sha256": wf.W14B_SHA256,
            "produced_by": "tools/werner_fig9.py --from-pdf --json",
        },
        "meta": meta,
        "decimation": DECIMATE,
        "bands": {
            name: {
                "k1_index": v["k1_index"],
                "peak_spread_db": v["peak_spread_db"],
                "axes_hz": v["axes_hz"],
                "axes_db": v["axes_db"],
                "subplot": v["subplot"],
                "curves": [{"hz": [round(float(a), 4) for a in h[::DECIMATE]],
                            "db": [round(float(b), 4) for b in d[::DECIMATE]]}
                           for h, d in v["curves"]],
            } for name, v in data.items()
        },
    }


def from_artifact(path: pathlib.Path = ARTIFACT):
    if not path.exists():
        raise Refused(f"{path} is absent; run --from-pdf --json with the paper")
    blob = json.loads(path.read_text())
    data = {}
    for name, v in blob["bands"].items():
        data[name] = dict(v)
        data[name]["curves"] = [(np.array(c["hz"]), np.array(c["db"]))
                                for c in v["curves"]]
    return data, blob["meta"], blob


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------


CY_BANDS_HZ = (3450.0, 7100.0)

# `tools/cymbal_bands.py`'s 1/3-octave centres, which is where the CY5025
# residual in docs/scorecard/cymbal-369/candidate2/README.md is reported.
THIRD_OCTAVE_HZ = (1000.0, 1260.0, 1590.0, 2000.0, 2500.0, 3200.0, 4000.0,
                   5000.0, 6300.0, 8000.0, 10000.0, 12700.0, 16000.0, 20000.0)


def tilt_table(data, ref_hz=1000.0):
    """Each band's tone-stage response relative to its own value at 1 kHz.

    This is the part of the tone stage that does NOT depend on the inter-band
    levels Figure 9 cannot pin down, so it is the part that can be carried
    into a candidate: a shape, per band, normalised out of its own gain.
    """
    rows = {}
    for name in ("Ht1", "Ht2", "Ht3"):
        v = data[name]
        hz, db = v["curves"][v["k1_index"]]
        fit = fit_bp2(hz, db)
        ref = float(bp2_db(ref_hz, fit["gain_db"], fit["f0"], fit["q"]))
        rows[name] = [
            (f, float(bp2_db(f, fit["gain_db"], fit["f0"], fit["q"])) - ref,
             bool(hz.min() <= f <= hz.max()))
            for f in THIRD_OCTAVE_HZ]
    return rows


def report(data) -> list[str]:
    lines = []
    for name in ("Ht1", "Ht2", "Ht3"):
        v = data[name]
        hz, db = v["curves"][v["k1_index"]]
        fit = fit_bp2(hz, db)
        lines.append(
            f"{name}  k = 1.0: plotted {hz.min():7.1f}-{hz.max():8.1f} Hz over "
            f"{db.max() - db.min():5.2f} dB;  2-pole band-pass "
            f"f0 {fit['f0']:7.1f} Hz  Q {fit['q']:.3f}  peak "
            f"{fit['gain_db']:+7.2f} dB  (rms {fit['rms_db']:.4f} dB)")
        for f in CY_BANDS_HZ + (20000.0,):
            b = extrapolation_bound(hz, db, f)
            inband = hz.min() <= f <= hz.max()
            src = "measured  " if inband else "EXTRAPOL. "
            lines.append(
                f"       {src}{f / 1000:5.2f} kHz: {b['nominal_db']:+7.2f} dB"
                + ("" if inband else
                   f"   [{b['lo_db']:+.2f} .. {b['hi_db']:+.2f}] over "
                   f"{b['n_alternatives']} sections this window cannot exclude"))
    # the one number this whole step was run for
    hz, db = data["Ht3"]["curves"][data["Ht3"]["k1_index"]]
    at = lambda f: float(np.interp(math.log10(f), np.log10(hz), db))  # noqa: E731
    lines.append("")
    lines.append(
        f"Ht3 k = 1.0 tilt across the cymbal's band: {at(2000.0):+.2f} dB at "
        f"2 kHz, {at(20000.0):+.2f} dB at 20 kHz  =  "
        f"{at(20000.0) - at(2000.0):+.2f} dB")
    lines.append(
        "  The LEVEL buffer tilts +16.6 dB across the same span (W14b Fig. 10, "
        "tools/werner_fig4.py --level).")
    lines.append("")
    lines.append("One network, one denominator -- so one pole pair, not three:")
    for name, f0, q, poles in shared_denominator_note(data):
        lines.append(
            f"  {name}  window fit f0 {f0:7.1f} Hz Q {q:.3f}  ->  real poles "
            f"{poles[0]:7.1f} and {poles[1]:7.1f} Hz")
    lines.append(
        "  These three pairs differ, so at most one is the network's in-band "
        "pair. A 2-pole fit over a 3 dB window is a local shape, not a pole "
        "location -- which is why Ht1 and Ht2 are quoted with a bound above.")
    lines.append("")
    lines.append(
        "Tone-stage tilt per band, dB relative to 1 kHz, on "
        "tools/cymbal_bands.py's 1/3-octave centres:")
    rows = tilt_table(data)
    head = "  band  " + " ".join(f"{f / 1000:>6.2f}k" for f in THIRD_OCTAVE_HZ)
    lines.append(head)
    for name, cells in rows.items():
        lines.append("  " + f"{name:5s} " +
                     " ".join(f"{d:+7.1f}" for _f, d, _ in cells))
    lines.append(
        "  measured over: " +
        ", ".join(f"{name} to {max(f for f, _d, ok in cells if ok) / 1000:.2f} kHz"
                  if any(ok for _f, _d, ok in cells) else f"{name} nowhere here"
                  for name, cells in rows.items()) +
        "; the rest of each row is extrapolated.")
    return lines


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--pdf", default=os.environ.get("W14B_PDF", "/tmp/w14b.pdf"))
    ap.add_argument("--artifact", default=str(ARTIFACT))
    ap.add_argument("--from-pdf", dest="pdf_required", action="store_true")
    ap.add_argument("--download", action="store_true")
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--fit", action="store_true")
    ap.add_argument("--json")
    a = ap.parse_args(argv)

    try:
        if a.pdf_required:
            pdf = wf.load_pdf(pathlib.Path(a.pdf), a.download)
            data, meta = read_figure(pdf)
            print(f"axes: {data['Ht3']['axes_hz'][0]:.0f}-"
                  f"{data['Ht3']['axes_hz'][1]:.0f} Hz, "
                  f"{meta['pt_per_decade']:.3f} pt/decade")
        else:
            data, meta, _ = from_artifact(pathlib.Path(a.artifact))
            print(f"from {a.artifact}")
    except Refused as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 3

    rc = 0
    if a.check or not (a.fit or a.json):
        ok, lines = check(data, meta)
        for line in lines:
            print(line)
        print("GATE:", "PASS" if ok else "FAIL",
              "(no external known answer exists for this figure -- see the "
              "module docstring)")
        rc = 0 if ok else 1

    if a.fit:
        print()
        for line in report(data):
            print(line)

    if a.json:
        if not a.pdf_required:
            print("REFUSED: --json re-derives the evidence and needs --from-pdf",
                  file=sys.stderr)
            return 3
        art = to_artifact(data, meta)
        thin = {name: {**v, "curves": [(np.array(c["hz"]), np.array(c["db"]))
                                       for c in v["curves"]]}
                for name, v in art["bands"].items()}
        ok_full, _ = check(data, meta)
        ok_thin, lines = check(thin, meta)
        if not (ok_full and ok_thin):
            print("REFUSED: decimation moved the gate", file=sys.stderr)
            for line in lines:
                print("  " + line, file=sys.stderr)
            return 3
        pathlib.Path(a.json).write_text(json.dumps(art, indent=1))
        print(f"wrote {a.json}")
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
