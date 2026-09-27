#!/usr/bin/env python3
"""Read the TR-808 cymbal's five filter responses off Werner 2014 Figure 4.

WHY THIS EXISTS. `docs/tr808-reference.md` §10 resolves three of the cymbal's
five filters from component values on SN p.13 -- the two bridged-T band-passes
(3.45 kHz and 7.1 kHz, both Q 6) and Hh1 (2.5 kHz, Q 0.97). It does **not**
resolve Hh2's corner or Hh3's shape; §18 lists both as open, and
`docs/scorecard/cymbal-369/candidate/README.md` traced the §10 candidate's
overcorrection at CY5025 to exactly those two unknowns plus the level stage.
Guessing them is fitting, which #369 forbids for structural values.

W14b Figure 4 plots all five magnitude responses, and the figure is **vector**,
not a bitmap: the curve coordinates are in the PDF content stream to full
double precision. This tool reads them out. That makes Hh2 and Hh3 circuit
facts published by the same author who gave us Hh1, not values fitted to a
recording.

WHY IT CAN BE TRUSTED. Three of the five curves have answers that are known
**independently of this tool** -- they come from resistors and capacitors on
the schematic, through the textbook bridged-T and Sallen-Key formulae. The
digitiser is required to reproduce those three before any number is read off
the other two (`--check`). That is an external known-answer test, not a
self-consistency check: nothing about the axis calibration, the colour
separation or the curve assembly is informed by what the answers should be.

PRECONDITIONS, ASSERTED AT THE POINT OF USE. Every one of these is a REFUSAL,
not a warning, because a figure digitiser that answers from the wrong page or
the wrong axis calibration produces output that looks exactly like data:

  * the PDF's SHA-256 must be the recorded one (a different printing is a
    different figure);
  * the figure XObject must carry all five legend labels Hbp1..Hh3;
  * the two independent x-axis calibrations (decade spacing from the two tick
    labels, versus the axes rectangle's own width) must agree to 0.2 %;
  * likewise the two y-axis calibrations (tick-label spacing versus the axes
    rectangle's height);
  * exactly five long polylines must survive assembly, two red and three blue.

CONTROLS THAT MUST FAIL are in `tools/test_werner_fig4.py`: a corrupted axis
calibration, a swapped colour assignment and a reversed x-axis each have to
turn `--check` red. A known-answer test nobody has seen fail is not a control.

Usage:
    python3 tools/werner_fig4.py --check              # known answers + controls
    python3 tools/werner_fig4.py --json out.json      # the five curves
    python3 tools/werner_fig4.py --fit                # derived filter values
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import pathlib
import re
import sys
import zlib

import numpy as np

W14B_URL = "https://zenodo.org/api/records/850891/files/smc_2014_220.pdf/content"
W14B_SHA256 = "0e75e26df59d8117939fbb253935d648cf969d43988c6a97fdc19061df453a02"

# The three answers that do not come from this tool. Bridged-T centres and Q
# from SN p.13 R/C values via reference §1.2; Hh1 from the unity-gain
# Sallen-Key high-pass on R124/R127/C48/C59 via reference §10.
KNOWN = {
    "Hbp1": {
        "reader": "geometric_bp", "f0": 3450.0, "q": 6.0,
        "source": "SN p.13 R56 560 / R57 82k / C13=C14 3.3 nF, bridged-T (ref §1.2, §10)",
    },
    "Hbp2": {
        "reader": "geometric_bp", "f0": 7100.0, "q": 6.0,
        "source": "SN p.13 R58 560 / R59 82k / C15=C16 6.8 nF, bridged-T (ref §1.2, §10)",
    },
    "Hh1": {
        "reader": "fit_hp2", "f0": 2500.0, "q": 0.97, "rms_tol_db": 0.05,
        "source": "SN p.13 R124 22k / R127 82k / C48=C59 1.5 nF, unity-gain Sallen-Key (ref §10, W14b eq. 16)",
    },
}
# Reference §1.7 allows +-10 % on f0 and +-50 % on a high-Q figure between
# units; this bar is much tighter because both sides describe the SAME nominal
# design -- it is about the digitiser, not about unit spread.
F0_TOL = 0.05
Q_TOL = 0.20


class Refused(Exception):
    """A precondition failed. REFUSED is a verdict, distinct from a value."""


# ---------------------------------------------------------------------------
# PDF content-stream reading
# ---------------------------------------------------------------------------


def _streams(pdf: bytes):
    for num, body in re.findall(rb"(\d+)\s+0\s+obj(.*?)endobj", pdf, re.S):
        m = re.search(rb"stream\r?\n", body)
        if not m:
            continue
        raw = body[m.end():]
        end = raw.rfind(b"endstream")
        if end < 0:
            continue
        try:
            yield int(num), zlib.decompress(raw[:end])
        except zlib.error:
            continue


def _matmul(a, b):
    return [
        a[0] * b[0] + a[1] * b[2],
        a[0] * b[1] + a[1] * b[3],
        a[2] * b[0] + a[3] * b[2],
        a[2] * b[1] + a[3] * b[3],
        a[4] * b[0] + a[5] * b[2] + b[4],
        a[4] * b[1] + a[5] * b[3] + b[5],
    ]


def _apply(m, x, y):
    return (m[0] * x + m[2] * y + m[4], m[1] * x + m[3] * y + m[5])


def parse_content(stream: str):
    """Return (polylines, rects, texts) in a single device space.

    polylines: list of (rgb, [(x, y), ...])
    rects:     list of ((x0, y0), (x1, y1))
    texts:     list of (string, (x, y)) at the text object's Td origin
    """
    ctm = [1.0, 0.0, 0.0, 1.0, 0.0, 0.0]
    stack: list[list[float]] = []
    color = (0.0, 0.0, 0.0)
    cur: list[tuple[float, float]] = []
    polylines: list[tuple[tuple[float, float, float], list]] = []
    rects: list = []
    texts: list = []
    nums: list[float] = []
    in_text = False
    text_ctm = list(ctm)
    tx = ty = 0.0
    pending = ""

    tokens = re.findall(r"\((?:\\.|[^()\\])*\)|\[[^\]]*\]|\S+", stream)
    for tok in tokens:
        if tok.startswith("("):
            pending = re.sub(r"\\(.)", r"\1", tok[1:-1])
            continue
        if tok.startswith("["):
            continue
        try:
            nums.append(float(tok))
            continue
        except ValueError:
            pass
        if tok == "q":
            stack.append(list(ctm))
        elif tok == "Q":
            if stack:
                ctm = stack.pop()
        elif tok == "cm" and len(nums) >= 6:
            ctm = _matmul(nums[-6:], ctm)
        elif tok == "m" and len(nums) >= 2:
            if len(cur) > 1:
                polylines.append((color, cur))
            cur = [_apply(ctm, nums[-2], nums[-1])]
        elif tok == "l" and len(nums) >= 2:
            cur.append(_apply(ctm, nums[-2], nums[-1]))
        elif tok == "re" and len(nums) >= 4:
            x, y, w, h = nums[-4:]
            rects.append((_apply(ctm, x, y), _apply(ctm, x + w, y + h)))
        elif tok in ("S", "s", "B", "b"):
            if len(cur) > 1:
                polylines.append((color, cur))
            cur = []
        elif tok in ("f", "f*", "F", "n"):
            # A fill or a clip path (`W n`) is not a plotted line. Recording
            # them here put the axes clip rectangle in the curve list as a
            # full-width red polyline.
            cur = []
        elif tok == "RG" and len(nums) >= 3:
            color = tuple(nums[-3:])
        elif tok == "G" and len(nums) >= 1:
            color = (nums[-1],) * 3
        elif tok == "BT":
            in_text = True
            text_ctm = list(ctm)
            tx = ty = 0.0
        elif tok == "ET":
            in_text = False
        elif tok == "Td" and in_text and len(nums) >= 2:
            tx += nums[-2]
            ty += nums[-1]
        elif tok == "Tm" and in_text and len(nums) >= 6:
            tx, ty = nums[-2], nums[-1]
        elif tok == "Tj" and in_text:
            texts.append((pending, _apply(text_ctm, tx, ty)))
            pending = ""
        nums = []
    if len(cur) > 1:
        polylines.append((color, cur))
    return polylines, rects, texts


# ---------------------------------------------------------------------------
# Figure 4 specifically
# ---------------------------------------------------------------------------

LEGEND = ("bp1", "bp2", "h1", "h2", "h3")


def find_figure(pdf: bytes):
    for num, raw in _streams(pdf):
        try:
            text = raw.decode("latin-1")
        except UnicodeDecodeError:
            continue
        if all(f"({lab})Tj" in text for lab in LEGEND) and "(frequency" in text:
            return num, text
    raise Refused(
        "no XObject in this PDF carries all five Figure 4 legend labels "
        "(Hbp1, Hbp2, Hh1, Hh2, Hh3) together with the frequency axis title"
    )


def _dedupe(polylines):
    seen = set()
    out = []
    for color, pts in polylines:
        key = (color, round(pts[0][0], 6), round(pts[0][1], 6),
               round(pts[-1][0], 6), round(pts[-1][1], 6), len(pts))
        if key in seen:
            continue
        seen.add(key)
        out.append((color, pts))
    return out


def _join(polylines, eps=1e-6):
    """MATLAB's exporter splits one long line into 300-point chunks, and emits
    them in descending-x order, so a chunk may join to either end of the run
    accumulated so far."""
    out: list[tuple[tuple, list]] = []
    for color, pts in polylines:
        pts = list(pts)
        if out and out[-1][0] == color:
            run = out[-1][1]
            if _same(run[-1], pts[0], eps):
                run.extend(pts[1:])
                continue
            if _same(run[0], pts[-1], eps):
                out[-1] = (color, pts[:-1] + run)
                continue
            if _same(run[-1], pts[-1], eps):
                run.extend(reversed(pts[:-1]))
                continue
            if _same(run[0], pts[0], eps):
                out[-1] = (color, list(reversed(pts[1:])) + run)
                continue
        out.append((color, pts))
    return out


def _same(a, b, eps):
    return abs(a[0] - b[0]) < eps and abs(a[1] - b[1]) < eps


def gridlines(polylines, box):
    """The dotted grid MATLAB draws at each tick, as geometry.

    Tick *labels* give the value but not the position -- a `Td` is a glyph
    baseline, offset from the tick by most of a cap height, which read the
    axis 1.4 dB low the first time this was written. The grid lines are the
    tick positions exactly."""
    (bx0, by0), (bx1, by1) = box
    w, h = bx1 - bx0, by1 - by0
    hor, ver = set(), set()
    for _, pts in polylines:
        if len(pts) != 2:
            continue
        (x0, y0), (x1, y1) = pts
        if abs(y0 - y1) < 1e-6 and abs(x1 - x0) > 0.9 * w:
            hor.add(round(y0, 4))
        if abs(x0 - x1) < 1e-6 and abs(y1 - y0) > 0.9 * h:
            ver.add(round(x0, 4))
    return sorted(hor), sorted(ver)


def calibrate(polylines, rects, texts):
    """Axis calibration from the figure's own grid geometry.

    The value of each tick comes from the text labels; the *position* of each
    tick comes from the grid lines. Both axes are then re-checked by requiring
    every other grid line to land on a round value -- ten frequency decades
    steps and four 10 dB steps that were not used to fit anything.
    """
    uniq = {(round(r[0][0], 4), round(r[0][1], 4),
             round(r[1][0], 4), round(r[1][1], 4)) for r in rects}
    boxes = sorted((((x0, y0), (x1, y1)) for x0, y0, x1, y1 in uniq),
                   key=lambda r: (r[1][0] - r[0][0]) * (r[1][1] - r[0][1]))
    if len(boxes) < 2:
        raise Refused("figure has fewer than two rectangles; no axes box to find")
    box = boxes[-2]
    (bx0, by0), (bx1, by1) = box
    hor, ver = gridlines(polylines, box)

    # --- y: tick values from the labels, tick positions from the grid -------
    ylab = sorted({(float(t), p[1]) for t, p in texts
                   if re.fullmatch(r"-?\d+", t) and p[0] < bx0})
    if len(ylab) < 3:
        raise Refused(f"expected at least three y tick labels, found {len(ylab)}")
    if len(hor) < len(ylab):
        raise Refused(
            f"{len(ylab)} y tick labels but only {len(hor)} horizontal grid "
            "lines; cannot place the ticks"
        )
    vals = np.array([v for v, _ in ylab])
    base = np.array([y for _, y in ylab])
    guess_slope = np.polyfit(vals, base, 1)[0]
    if guess_slope <= 0:
        raise Refused("y tick labels do not increase upward")
    # Each label belongs to the nearest grid line above its baseline.
    placed = []
    for v, yb in zip(vals, base):
        cand = [y for y in hor if y >= yb - 0.5 * guess_slope]
        if not cand:
            raise Refused(f"y tick label {v:g} has no grid line above it")
        placed.append(min(cand))
    if len(set(placed)) != len(placed):
        raise Refused("two y tick labels claim the same grid line")
    slope, intercept = np.polyfit(vals, np.array(placed), 1)
    resid = np.array(placed) - (slope * vals + intercept)
    if np.abs(resid).max() > 0.05 * slope:
        raise Refused(
            "the labelled y ticks are not evenly spaced on the grid "
            f"(worst residual {np.abs(resid).max() / slope:.3f} dB)"
        )

    def y_to_db(y):
        return (np.asarray(y, dtype=float) - intercept) / slope

    # The axes box edges are the axis limits and need not be ticks; every
    # grid line strictly inside it is one.
    for y in [v for v in hor if by0 + 1e-6 < v < by1 - 1e-6]:
        db = float(y_to_db(y))
        if abs(db - round(db / 10.0) * 10.0) > 0.05:
            raise Refused(
                f"horizontal grid line at y={y:.3f} reads {db:.3f} dB, not a "
                "multiple of 10 dB; the y calibration is wrong"
            )

    # --- x: the decade pair, from the grid, confirmed by the two labels -----
    dec_lab = sorted([p[0] for t, p in texts if t == "10" and p[1] < by0])
    if len(dec_lab) != 2:
        raise Refused(
            f"expected exactly two decade mantissa labels below the axes, "
            f"found {len(dec_lab)}"
        )
    pt_per_decade = dec_lab[1] - dec_lab[0]
    if pt_per_decade <= 0:
        raise Refused("decade labels are not in increasing x order")
    # Several pairs of grid lines are a decade apart (700/7000, 800/8000 ...).
    # The decade pair is the one the two mantissa labels sit under. MATLAB
    # centres a tick label on its tick, so the tick is to the RIGHT of the
    # label's left-aligned origin by half a label width -- a positive offset,
    # under a quarter of a decade, and the same for both labels.
    pairs = [(a, b) for a in ver for b in ver
             if abs((b - a) - pt_per_decade) < 0.05]
    cands = [(a, b) for a, b in pairs
             if 0.0 < a - dec_lab[0] < 0.25 * pt_per_decade
             and abs((a - dec_lab[0]) - (b - dec_lab[1])) < 0.2]
    if len(cands) != 1:
        raise Refused(
            f"{len(pairs)} vertical grid-line pairs are one decade "
            f"({pt_per_decade:.3f} pt) apart and {len(cands)} of them sit "
            "under the two mantissa labels; expected exactly one"
        )
    x1k, x10k = cands[0]

    def x_to_hz(x):
        return 1000.0 * 10 ** ((np.asarray(x, dtype=float) - x1k) / pt_per_decade)

    for x in [v for v in ver if bx0 + 1e-6 < v < bx1 - 1e-6]:
        hz = float(x_to_hz(x))
        mant = hz / 10 ** math.floor(math.log10(hz))
        if abs(mant - round(mant)) > 0.004:
            raise Refused(
                f"vertical grid line at x={x:.3f} reads {hz:.2f} Hz, which is "
                "not a round mantissa; the x calibration is wrong"
            )

    return {
        "x_to_hz": x_to_hz,
        "y_to_db": y_to_db,
        "box": box,
        "xlim": (float(x_to_hz(bx0)), float(x_to_hz(bx1))),
        "ylim": (float(y_to_db(by0)), float(y_to_db(by1))),
        "pt_per_decade": float(pt_per_decade),
        "pt_per_db": float(slope),
        "n_gridlines": (len(hor), len(ver)),
    }


def curves(pdf: bytes):
    """The five (name -> (hz, db)) responses of Figure 4."""
    _, text = find_figure(pdf)
    polys, rects, texts = parse_content(text)
    cal = calibrate(polys, rects, texts)
    (bx0, by0), (bx1, by1) = cal["box"]

    joined = _join(_dedupe([(c, p) for c, p in polys if c in
                            ((1.0, 0.0, 0.0), (0.0, 0.0, 1.0))]))
    # A data curve covers a third of the axes and is finely sampled; a legend
    # sample line is two points and a marker glyph is a handful. Neither
    # "spans most of the axes" nor "reaches the right-hand edge" works on its
    # own: Hh2 and Hh3 are clipped at the bottom and start only around
    # 4-5 kHz, and Hbp1 leaves the bottom of the plot before 20 kHz.
    span = (bx1 - bx0) / 3.0
    long = [(c, p) for c, p in joined
            if max(x for x, _ in p) - min(x for x, _ in p) > span
            and len(p) > 50]
    red = [p for c, p in long if c == (1.0, 0.0, 0.0)]
    blue = [p for c, p in long if c == (0.0, 0.0, 1.0)]
    if len(red) != 2 or len(blue) != 3:
        raise Refused(
            f"expected 2 red and 3 blue full-width curves, found "
            f"{len(red)} and {len(blue)}"
        )

    def to_data(pts):
        pts = sorted(pts)
        x = np.array([p[0] for p in pts])
        y = np.array([p[1] for p in pts])
        keep = (x >= bx0 - 1e-6) & (x <= bx1 + 1e-6)
        return cal["x_to_hz"](x[keep]), cal["y_to_db"](y[keep])

    red_d = [to_data(p) for p in red]
    blue_d = [to_data(p) for p in blue]
    # Naming, by rules stated before any number is read:
    #   * the two band-passes are the red pair; Hbp1 is the one that peaks
    #     lower, since §10 puts the low band's 3.45 kHz first;
    #   * Hh1 is the blue curve that reaches its own pass-band level at the
    #     lowest frequency -- it is the only one of the three whose corner is
    #     below the band-passes;
    #   * of the two remaining, W14b §9 says Hh3's op-amp buffer "manifests as
    #     resonance at the corner frequency (around 10500 Hz)". Hh3 is
    #     therefore the resonant one and Hh2 the other. If both or neither
    #     resonate, this tool has mis-assembled the figure and refuses.
    red_d.sort(key=lambda d: peak(*d)[0])
    blue_d.sort(key=lambda d: _corner_proxy(*d))
    h1, rest = blue_d[0], blue_d[1:]
    reso = [resonance_db(*d) for d in rest]
    hot = [i for i, r in enumerate(reso) if r > 3.0]
    if len(hot) != 1:
        raise Refused(
            "exactly one of Hh2/Hh3 must show a resonant peak more than 3 dB "
            f"above its own pass band; measured {reso[0]:.2f} and "
            f"{reso[1]:.2f} dB"
        )
    h3 = rest[hot[0]]
    h2 = rest[1 - hot[0]]
    out = {"Hbp1": red_d[0], "Hbp2": red_d[1], "Hh1": h1, "Hh2": h2, "Hh3": h3}
    return out, cal


def _corner_proxy(hz, db):
    """Frequency at which the curve first passes 6 dB below its own maximum,
    coming up from the low end. Used only for ordering the three high-passes."""
    target = db.max() - 6.0
    idx = np.argmax(db >= target)
    return float(hz[idx])


# ---------------------------------------------------------------------------
# Reading filter parameters off a digitised curve
# ---------------------------------------------------------------------------


def peak(hz, db):
    """Interpolated peak (Hz, dB) by a parabola through the top sample."""
    i = int(np.argmax(db))
    if i == 0 or i == len(db) - 1:
        return float(hz[i]), float(db[i])
    lx = np.log10(hz[i - 1:i + 2])
    ly = db[i - 1:i + 2]
    a, b, c = np.polyfit(lx, ly, 2)
    if a >= 0:
        return float(hz[i]), float(db[i])
    xv = -b / (2 * a)
    return float(10 ** xv), float(a * xv * xv + b * xv + c)


def plateau(hz, db):
    """The curve's level over the top third of an octave of the plotted band."""
    return float(np.mean(db[hz > hz.max() / 1.25]))


def geometric_bp(hz, db):
    """f0 and Q of a resonator, by the definition -- the peak and the -3 dB
    bandwidth around it. This is the right reader for the two band-passes
    because they are BRIDGED-T sections, not 2-pole band-passes: their
    numerator differs, and a `bp2` least-squares fit to Hbp1 reads Q 3.07 for
    a filter the schematic puts at 6 (wrong-then-right #2 of this tool)."""
    f0, gain = peak(hz, db)
    half = gain - 3.0
    lo = hi = None
    for i in range(1, len(hz)):
        if db[i - 1] < half <= db[i] and hz[i] <= f0:
            lo = _log_interp(hz[i - 1], db[i - 1], hz[i], db[i], half)
        if db[i - 1] >= half > db[i] and hz[i] > f0:
            hi = _log_interp(hz[i - 1], db[i - 1], hz[i], db[i], half)
            break
    out = {"f0": f0, "gain_db": gain, "f_lo": lo, "f_hi": hi,
           "reader": "geometric_bp"}
    out["q"] = None if lo is None or hi is None else f0 / (hi - lo)
    return out


def _log_interp(x0, y0, x1, y1, y):
    t = (y - y0) / (y1 - y0)
    return float(10 ** (math.log10(x0) + t * (math.log10(x1) - math.log10(x0))))


def resonance_db(hz, db):
    """How far the curve's peak stands above its own high-frequency plateau.
    A high-pass section that is flat in its pass band reads about zero."""
    return peak(hz, db)[1] - plateau(hz, db)


# Candidate continuous-time structures. Each returns |H| in dB given the
# parameter vector; all are textbook sections, none has a free zero placed to
# make a fit work.
def _db(x):
    return 20.0 * np.log10(np.maximum(np.abs(x), 1e-18))


def _biquad(w, f0, q, kind):
    s = 1j * w
    w0 = 2 * np.pi * f0
    den = s * s + (w0 / q) * s + w0 * w0
    if kind == "bandpass":
        return (w0 / q) * s / den
    if kind == "highpass":
        return s * s / den
    raise ValueError(kind)


STRUCTURES = {
    # name: (parameter names, |H| model)
    "bp2": (("gain_db", "f0", "q"),
            lambda w, g, f0, q: g + _db(_biquad(w, f0, q, "bandpass"))),
    "hp2": (("gain_db", "f0", "q"),
            lambda w, g, f0, q: g + _db(_biquad(w, f0, q, "highpass"))),
    # a 3rd-order high-pass: the resonant 2-pole plus one real zero-at-DC pole
    "hp3": (("gain_db", "f0", "q", "fp"),
            lambda w, g, f0, q, fp: g + _db(_biquad(w, f0, q, "highpass")
                                            * (1j * w) / (1j * w + 2 * np.pi * fp))),
    # the same resonant 2-pole followed by a single real low-pass pole
    "hp2lp1": (("gain_db", "f0", "q", "fp"),
               lambda w, g, f0, q, fp: g + _db(_biquad(w, f0, q, "highpass")
                                               * (2 * np.pi * fp) / (1j * w + 2 * np.pi * fp))),
    # a 2-pole band-pass with a free centre and Q, for comparison
    "bp2free": (("gain_db", "f0", "q"),
                lambda w, g, f0, q: g + _db(_biquad(w, f0, q, "bandpass"))),
}


def fit_structure(hz, db, name):
    """Least squares in (log f, dB). Returns the parameters and the RMS
    residual, which is the number that says whether the structure is right."""
    from scipy.optimize import least_squares

    names, model = STRUCTURES[name]
    w = 2 * np.pi * np.asarray(hz, dtype=float)
    y = np.asarray(db, dtype=float)
    f_peak, g_peak = peak(hz, db)
    x0 = {"gain_db": plateau(hz, db) if name.startswith("hp") else g_peak,
          "f0": f_peak, "q": 2.0, "fp": f_peak}
    lo = {"gain_db": -60.0, "f0": hz.min() * 0.2, "q": 0.3, "fp": hz.min() * 0.05}
    hi = {"gain_db": 60.0, "f0": hz.max() * 5.0, "q": 60.0, "fp": hz.max() * 20.0}
    res = least_squares(
        lambda p: model(w, *p) - y,
        [x0[n] for n in names],
        bounds=([lo[n] for n in names], [hi[n] for n in names]),
        xtol=1e-12, ftol=1e-12,
    )
    out = {n: float(v) for n, v in zip(names, res.x)}
    out["rms_db"] = float(np.sqrt(np.mean(res.fun ** 2)))
    out["max_db"] = float(np.max(np.abs(res.fun)))
    out["structure"] = name
    return out


def describe(hz, db):
    """Everything this tool can say about one digitised curve."""
    f_peak, g_peak = peak(hz, db)
    return {
        "n_points": int(len(hz)),
        "f_range_hz": [float(hz.min()), float(hz.max())],
        "peak_hz": f_peak,
        "peak_db": g_peak,
        "plateau_db": plateau(hz, db),
        "resonance_db": resonance_db(hz, db),
        "top_octave_slope_db_per_oct": _slope_db_per_octave(
            hz, db, hz.max() / 1.9, hz.max()),
        "fits": {k: fit_structure(hz, db, k)
                 for k in ("bp2", "hp2", "hp3", "hp2lp1")},
    }


def _slope_db_per_octave(hz, db, f_lo, f_hi):
    m = (hz >= f_lo) & (hz <= f_hi)
    if m.sum() < 3:
        return None
    a, _ = np.polyfit(np.log2(hz[m]), db[m], 1)
    return float(a)


# ---------------------------------------------------------------------------
# The known-answer gate
# ---------------------------------------------------------------------------


def check(data) -> tuple[bool, list[str]]:
    """The gate. Four of the five curves have answers this tool did not
    produce: three from SN p.13 component values through the textbook
    formulae, and Hh3's resonance from W14b §9's own prose."""
    lines = []
    ok = True
    for name, want in KNOWN.items():
        hz, db = data[name]
        if want["reader"] == "geometric_bp":
            got = geometric_bp(hz, db)
            extra = f"peak {got['gain_db']:+6.2f} dB"
            rms_ok = True
        else:
            got = fit_structure(hz, db, "hp2")
            extra = f"rms {got['rms_db']:.3f} dB (<= {want['rms_tol_db']:.2f})"
            rms_ok = got["rms_db"] <= want["rms_tol_db"]
        if got["q"] is None:
            lines.append(f"FAIL {name:5s} the -3 dB points are off the plotted range")
            ok = False
            continue
        f_err = abs(got["f0"] - want["f0"]) / want["f0"]
        q_err = abs(got["q"] - want["q"]) / want["q"]
        good = f_err <= F0_TOL and q_err <= Q_TOL and rms_ok
        ok = ok and good
        lines.append(
            f"{'PASS' if good else 'FAIL'} {name:5s} {want['reader']:12s} "
            f"f0 {got['f0']:8.1f} Hz (known {want['f0']:7.1f}, {100 * f_err:5.2f}%)  "
            f"Q {got['q']:5.2f} (known {want['q']:.2f}, {100 * q_err:5.1f}%)  {extra}"
        )
        lines.append(f"       source: {want['source']}")
    # Hh3's resonance, from the paper's text rather than from components.
    f_res, _ = peak(*data["Hh3"])
    err = abs(f_res - 10500.0) / 10500.0
    good = err <= 0.10
    ok = ok and good
    lines.append(
        f"{'PASS' if good else 'FAIL'} Hh3   resonance at {f_res:.0f} Hz "
        f"(W14b §9: \"around 10500 Hz\", {100 * err:.1f}%)"
    )
    return ok, lines


def load_pdf(path: pathlib.Path, allow_download: bool) -> bytes:
    if not path.exists():
        if not allow_download:
            raise Refused(
                f"{path} is absent. Re-run with --download, or fetch "
                f"{W14B_URL} to that path. This tool does not guess."
            )
        import urllib.request
        path.parent.mkdir(parents=True, exist_ok=True)
        with urllib.request.urlopen(W14B_URL, timeout=120) as r:
            path.write_bytes(r.read())
    blob = path.read_bytes()
    got = hashlib.sha256(blob).hexdigest()
    if got != W14B_SHA256:
        raise Refused(
            f"{path} has SHA-256 {got}, not the recorded {W14B_SHA256}. A "
            "different file is a different figure; refusing to read it."
        )
    return blob


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--pdf", default=os.environ.get("W14B_PDF", "/tmp/w14b.pdf"))
    ap.add_argument("--download", action="store_true",
                    help="fetch the paper from Zenodo if --pdf is absent")
    ap.add_argument("--check", action="store_true",
                    help="known-answer gate against the three filters the "
                         "schematic already resolves")
    ap.add_argument("--fit", action="store_true", help="derived filter values")
    ap.add_argument("--json", help="write the five digitised curves here")
    a = ap.parse_args(argv)

    try:
        pdf = load_pdf(pathlib.Path(a.pdf), a.download)
        data, cal = curves(pdf)
    except Refused as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 3

    print(f"axes: {cal['xlim'][0]:.0f}-{cal['xlim'][1]:.0f} Hz, "
          f"{cal['ylim'][0]:.1f}..{cal['ylim'][1]:.1f} dB, "
          f"{cal['pt_per_decade']:.3f} pt/decade, {cal['pt_per_db']:.4f} pt/dB")

    rc = 0
    if a.check or not (a.fit or a.json):
        ok, lines = check(data)
        for line in lines:
            print(line)
        print("KNOWN-ANSWER GATE:", "PASS" if ok else "FAIL")
        rc = 0 if ok else 1

    if a.fit:
        print()
        for name in ("Hbp1", "Hbp2"):
            print(name, json.dumps(bandpass_params(*data[name]), indent=None,
                                   default=lambda v: round(v, 4)))
        for name in ("Hh1", "Hh2", "Hh3"):
            print(name, json.dumps(highpass_params(*data[name]), indent=None,
                                   default=lambda v: round(v, 4)))

    if a.json:
        out = {
            "source": {"paper": "Werner, Abel, Smith, ICMC|SMC 2014, Figure 4",
                       "url": W14B_URL, "sha256": W14B_SHA256},
            "axes": {"xlim_hz": list(cal["xlim"]), "ylim_db": list(cal["ylim"])},
            "curves": {k: {"hz": [round(float(v), 4) for v in h],
                           "db": [round(float(v), 5) for v in d]}
                       for k, (h, d) in data.items()},
        }
        pathlib.Path(a.json).write_text(json.dumps(out, indent=1))
        print(f"wrote {a.json}")
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
