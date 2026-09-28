#!/usr/bin/env python3
"""The TR-808 cymbal TONE stage (CY TONE, VR4), derived from SN p.13.

WHY THIS EXISTS. #369 step 4 (`tools/werner_fig9.py`, PR #397) digitised W14b
Figure 9 and got the tone stage's per-band SHAPE (each of Ht1/Ht2/Ht3 is a
2-pole band-pass over its own window), but Ht1's and Ht2's windows are only
1.7 dB and 1.0 dB tall and end at 564 Hz and 1.64 kHz -- nowhere near the
cymbal's own 3.45 kHz / 7.1 kHz corners -- so reading the cymbal-band values
off the figure alone means EXTRAPOLATING a narrow local fit, and the resulting
bounds are 18 dB (Ht1) and 9.1 dB (Ht2) wide at 7.1 kHz: wide enough that they
cannot exclude an extra pole or zero the window never shows. That is
`docs/scorecard/cymbal-369/tone-stage/README.md` section 4, and #390 is the
issue this module answers.

W14b §10 states the tone network is "a highly-interconnected passive network
of resistors and capacitors" giving three FIFTH-ORDER transfer functions and
declines to print their coefficients. It does NOT decline to draw the
network: SN p.13 (Roland TR-808 Service Notes, voicing board VG 3116-140) has
the resistor and capacitor values around VR4 ("CY TONE", 20 k(B) linear)
printed on the schematic. This module reads them off directly and solves the
network by nodal analysis -- the same route that produced Hh1 from
R124/R127/C48/C59 (`docs/tr808-reference.md` §10) -- rather than fitting or
bounding a curve.

WHERE THE VALUES CAME FROM, SO A READER CAN RE-READ THEM. The citation "SN
p.13" is not checkable on its own, and this repository's own rule is that a
number without the instrument that produced it is a claim rather than
evidence. So the source is pinned: `SN_PDF_URL` / `SN_PDF_SHA256` /
`SN_PDF_PAGE` / `SN_CROP_*` below name the exact scan, page and crop box that
every value in the next section was read off, and `--verify-source
<local.pdf>` re-renders that crop after checking the file's SHA-256. It
REFUSES (exit 3) when the PDF is absent or its hash does not match, rather
than answering from an unverified file -- the scan is a ~6 MB third-party
download, so it is deliberately NOT a test dependency and nothing in
`make verify` needs the network.

The read was re-verified against that crop on 2026-09-28: every value below,
the wiper-to-ground wiring of VR4, and Q25's emitter as Ht1's source all
match the scan. Two things the crop settles that the fit could only infer:
the top rail's op-amp has a THREE-capacitor input network (C49 .0033, C53
.001, C54 .001) and the bottom rail's has TWO (C51 .001, C52 .001), which is
exactly the 3rd-order/2nd-order split `docs/tr808-reference.md` §10 already
records for Hh3/Hh2; and the bottom op-amp is the one wired to VR2 "CY
DECAY", which is Hh2's band by definition. The rail assignment is therefore
confirmed by the schematic as well as selected by the fit -- two independent
routes to the same answer, not one.

THE NETWORK (SN p.13, voicing board, around VR4/"CY TONE"/"CY LEVEL"). Two
op-amp outputs and one transistor-buffer output feed it:

  * Va  -- an op-amp output (one of the two 7.1 kHz-band Sallen-Key filters,
    Hh2 or Hh3; the assignment is resolved empirically below, not asserted).
    Drives node N1 through C55 (0.01 uF) + R112 (22 k) in series.
  * Vb  -- the OTHER 7.1 kHz-band op-amp's output. Drives node N4 through
    C56 (0.01 uF) + R120 (10 k) in series.
  * Vh1 -- Q25's emitter (Hh1's own Sallen-Key output, "2nd-order Sallen-Key
    on emitter follower Q25", already in `docs/tr808-reference.md` §10).
    Drives an internal node Nx through C58 (0.01 uF) + R123 (100 k) in
    series; Nx is shunted to ground by C57 (0.0033 uF); Nx then drives N4
    through R121 (10 k). This extra low-pass stage is why Ht1's corner sits
    an octave below Ht2's even though both share node N4.

  N1 --- R119 (22 k) --- N2                    N4 --- R129 (15 k) --- N2
  N1 -+- VR4 top-half (alpha * 20 k) -- GND     N4 -+- R125 (2.2 k) + VR4
                                                       bottom-half
                                                       ((1-alpha) * 20 k) -- GND
  (VR4's wiper, the middle terminal, is tied straight to ground -- this is a
  balanced bridging attenuator, not a simple divider: turning it moves
  attenuation from one rail to the other, matching W14b's "TONE ... shifts
  the others" and Fig. 9's own asymmetric spreads, see below.)

  N2 (== Vtone) is loaded by C90 (0.01 uF) in series with IC6's inverting
  input -- an ideal op-amp's virtual ground, so C90 is a plain shunt
  capacitor from N2 to AC ground for this network's purposes. IC6 itself
  (R128/470k, C77/220p, VR6, R166) is the LEVEL stage already measured in
  Fig. 10 (`tools/werner_fig4.py --level`) and is not part of this module.

Five capacitors (C55, C56, C57, C58, C90) feed a connected resistive network
with no cap-only loop, so the transfer function from any one of the three
sources to N2 is a ratio of polynomials in `s` with a 5th-order denominator
-- W14b's own word for it, arrived at independently of W14b's coefficients.

That claim is checked TWO ways, and the reason there are two is that the first
one did not run where it matters -- twice over, and the second reason was found
only when the first was checked.

  1. `sympy` is not in any of this repository's CI requirement sets (the
     workflows install `numpy scipy pytest`, plus `pyyaml`), so a
     `sympy`-gated test skips wherever it is collected, and a skip is reported
     beside passes and read as one.
  2. It was not being collected either. `tools/test_tone_stage_schematic.py`
     was in no workflow at all: rungs.yml's `python` job runs `model/`, `spec/`
     and a NAMED list of `tools/test_*.py` files, full `pytest tools/` runs
     only under `make verify` (which no workflow invokes), and `docs/dag.json`
     has no node under `tools/`. So the first diagnosis -- "it skipped in CI"
     -- was itself wrong: nothing here appeared in a CI report, skipped or
     otherwise.

Both are fixed. This file's tests are now named in rungs.yml's `python` job
(#417), so they run on every pull request, and `poles_hz()` /
`solve_vtone_mna()` below re-derive the same facts with numpy/scipy only so
what runs there is the structural claim itself, not a skip line:

  * `solve_vtone_mna()` builds the network a DIFFERENT way -- seven nodes with
    each series R-C split at its own internal node, so every element is a
    plain resistor or a plain capacitor and the system is exactly
    `(G + s*C) v = b`. `solve_vtone()` instead eliminates those internal nodes
    by hand into `Z = R + 1/(sC)` series impedances. The two agree to ~1e-14
    dB, which makes the hand elimination a checked step rather than an assumed
    one (`test_the_two_independent_node_formulations_agree`).
  * `poles_hz()` takes the finite generalised eigenvalues of `(-G, C)`. `C` has
    exactly five nonzero entries and full rank on its support, so "exactly
    five finite poles" is a countable fact rather than a degree assertion, and
    it holds with no symbolic algebra: 128.3 / 509.1 / 681.4 / 1635.7 /
    4191.5 Hz at ALPHA_K1.
  * The shared denominator is structural, not measured: `A` in either
    formulation depends on the network and the pot, never on `drive` -- only
    `b` changes. "One network, one denominator" is therefore true by
    construction for all three paths, which is what W14b asserts in prose.

The agreement in the first bullet is not vacuous: swapping R119 and R129 inside
`mna_matrices` alone, leaving `solve_vtone`'s hand elimination on the true
values, parts the two formulations by 2.75 dB against that 1e-14 dB baseline
(`test_control_the_formulations_would_notice_a_swapped_component`).

The `sympy` test is kept as a third, symbolic witness and still skips where
`sympy` is absent; it is no longer the only thing standing behind the claim,
and it is no longer the only thing in a file CI does not collect.

WHICH RAIL IS WHICH BAND, AND WHAT "k = 1.0" MEANS ON THIS POT. Nothing on
the schematic says so directly, so it is resolved the same way Figure 9's own
asterisk was: by fitting, not by asserting a spatial guess.

  * `fit_alpha_k1()` fits the wiper fraction `alpha` in [0, 1] (VR4's top-half
    resistance is `alpha * 20 k`) against Figure 9's own digitised k = 1.0
    curves, one assignment (Va, Vb, Vh1) -> (Ht3, Ht2, Ht1) at a time, with
    NO free per-path gain -- only the network's own component values decide
    the level. If this network and this assignment are right, the fit should
    already be at the noise floor without needing a fudge factor.
  * It is: a SINGLE shared `alpha ~= 0.3977` fits all three families -- 707
    digitised points across three independently-plotted windows -- to
    0.001-0.013 dB RMS, matched against Figure 9's own FULLY measured Ht3
    curve (not extrapolated) to 0.05 dB across three decades. The two
    candidate assignments that put Hh2/Hh3 on the wrong rail (checked in
    `test_tone_stage_schematic.py`) fit 20-100x worse. This is why the
    result below is reported as resolved, not merely bounded.
  * `alpha` is a wiper FRACTION, not W14b's own knob parameter `k` -- the two
    need not be linearly related, and nothing here claims they are (the
    knob-law mapping stays explicitly out of scope, per #390 and #369 step
    4's own "out of scope" section).

WHAT THIS RESOLVES. At k = 1.0 (alpha = ALPHA_K1), reading the three transfer
functions directly off the solved network at the cymbal's own 3.45 kHz /
7.1 kHz corners (no extrapolation needed -- the network covers the whole
audio band):

    Ht3 (top, N1 alone)      -28.1 dB @ 3.45 kHz   -33.7 dB @ 7.1 kHz
    Ht2 (bottom, N4 direct)  -20.5 dB @ 3.45 kHz   -26.7 dB @ 7.1 kHz
    Ht1 (bottom, via Q25)    -42.0 dB @ 3.45 kHz   -51.8 dB @ 7.1 kHz

Both fall inside Figure 9's own extrapolation bounds (Ht1: [-54.5, -36.5] dB
at 7.1 kHz; Ht2: [-31.1, -22.0] dB) -- an independent cross-check the
schematic route did not need to pass to be usable, and did. See `--report`.

Usage:
    python3 tools/tone_stage_schematic.py --report   # the resolved numbers
    python3 tools/tone_stage_schematic.py --check     # the gate
    python3 tools/tone_stage_schematic.py --poles     # the shared pole set
    python3 tools/tone_stage_schematic.py --verify-source <sn.pdf>
"""

from __future__ import annotations

import argparse
import hashlib
import math
import pathlib
import shutil
import subprocess
import sys

import numpy as np

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import werner_fig9 as w9  # noqa: E402

# ---------------------------------------------------------------------------
# The source, pinned so the read below is reproducible rather than merely
# cited. `--verify-source <path>` checks the hash and re-renders the crop.
# ---------------------------------------------------------------------------

SN_PDF_URL = ("https://archive.org/download/synthmanual-roland-tr-808-service-"
              "notes/rolandtr-808servicenotes.pdf")
SN_PDF_SHA256 = "d3239b51e2eb5523ff7247528667dc753f9db84a8d29b62cf763cbc51d4aad74"
SN_PDF_PAGE = 13  # 1-based; the voicing-board schematic, VG 3116-140

# Crop boxes in pixels at 400 dpi on that page (A3, 1190.52 x 840.96 pt), as
# consumed by `pdftoppm -r <dpi> -x -y -W -H`. These are the two regions every
# component value in the next section was read off.
SN_CROPS = {
    # VR4 "CY TONE", both rail op-amps, R125, C90, IC6 "CY LEVEL".
    "tone": {"dpi": 400, "x": 2480, "y": 3040, "w": 1600, "h": 1300},
    # Q25's emitter follower and the C58/R123/C57/R121 pre-filter (Ht1's path),
    # plus Hh1's own C48/C59/R124/R127 for cross-reference against §10.
    "q25": {"dpi": 500, "x": 2500, "y": 4700, "w": 1500, "h": 900},
}


# ---------------------------------------------------------------------------
# Component values, read off SN p.13 (Roland TR-808 Service Notes, 1st ed.,
# 15 June 1981), voicing board VG 3116-140, around VR4 "CY TONE" / VR6 "CY
# LEVEL". Reference designators as printed on the schematic.
# ---------------------------------------------------------------------------

C55 = 0.01e-6   # F, coupling cap, top op-amp output (Va) -> N1
R112 = 22e3     # ohm, in series with C55
R119 = 22e3     # ohm, N1 -> N2

VR4_TOTAL = 20e3   # ohm, "20K(B)" linear taper, TONE pot
R125 = 2.2e3       # ohm, in series between VR4 pin 3 and N4

R129 = 15e3     # ohm, N4 -> N2

R120 = 10e3     # ohm, in series with C56
C56 = 0.01e-6   # F, coupling cap, bottom op-amp output (Vb) -> N4

C58 = 0.01e-6      # F, coupling cap, Q25 emitter (Vh1) -> Nx
R123 = 100e3       # ohm, in series with C58
C57 = 0.0033e-6    # F, Nx -> ground (shunt)
R121 = 10e3        # ohm, Nx -> N4

C90 = 0.01e-6   # F, N2 -> IC6 virtual ground (== shunt to AC ground here)

# The wiper fraction at W14b Fig. 9's k = 1.0, fitted (not assumed) against
# the digitised evidence by `fit_alpha_k1()` -- see module docstring and
# `test_tone_stage_schematic.py::test_alpha_k1_matches_fitted_constant`.
ALPHA_K1 = 0.3977174262519643

CY_BANDS_HZ = (3450.0, 7100.0)


class Refused(Exception):
    """Raised when a precondition this module needs is not met."""


class SourceUnavailable(Refused):
    """The pinned scan is absent, unreadable, or does not match its hash.

    A distinct type because it is the one REFUSAL a caller may reasonably
    treat as "not checkable here" rather than "something is wrong": the scan
    is a third-party download and deliberately not a test dependency.
    """


def verify_source(path) -> dict:
    """Assert `path` IS the pinned SN scan, then render `SN_CROPS` beside it.

    REFUSES rather than answering when the file is missing or its SHA-256 does
    not match `SN_PDF_SHA256` -- a schematic read checked against the wrong
    printing of the service notes would look exactly like a checked one.
    Returns a dict describing what was verified and written.
    """
    path = pathlib.Path(path)
    if not path.is_file():
        raise SourceUnavailable(
            f"{path} does not exist. Fetch the pinned scan first:\n"
            f"  curl -sL -o {path} {SN_PDF_URL}")

    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    if digest != SN_PDF_SHA256:
        raise SourceUnavailable(
            f"{path} is not the pinned scan: sha256 {digest}, expected "
            f"{SN_PDF_SHA256}. Component values were read off the pinned "
            "printing; a different scan may paginate or revise differently.")

    pdftoppm = shutil.which("pdftoppm")
    if pdftoppm is None:
        raise SourceUnavailable(
            "pdftoppm (poppler-utils) is not on PATH, so the crop cannot be "
            "re-rendered. The hash above still matched.")

    written = []
    for name, c in SN_CROPS.items():
        stem = path.parent / f"sn-p{SN_PDF_PAGE}-{name}"
        subprocess.run(
            [pdftoppm, "-png", "-r", str(c["dpi"]),
             "-f", str(SN_PDF_PAGE), "-l", str(SN_PDF_PAGE),
             "-x", str(c["x"]), "-y", str(c["y"]),
             "-W", str(c["w"]), "-H", str(c["h"]),
             str(path), str(stem)],
            check=True, capture_output=True)
        written.extend(str(p) for p in sorted(path.parent.glob(f"{stem.name}*.png")))

    return {"path": str(path), "sha256": digest, "page": SN_PDF_PAGE,
            "crops": written}


# ---------------------------------------------------------------------------
# The network
# ---------------------------------------------------------------------------


def solve_vtone(f_hz, alpha, drive):
    """V(N2)/V(drive) of the CY TONE network at wiper fraction `alpha`.

    `drive` selects which of the three sources is the unit source (the other
    two are set to zero, i.e. treated as ideal-voltage-source outputs, which
    is what an op-amp output or an emitter-follower output approximates):

      "top"    -- Va into N1 (through C55 + R112).       -> Ht3 candidate
      "bottom" -- Vb into N4 (through C56 + R120).        -> Ht2 candidate
      "hh1"    -- Vh1 into N4 via Nx (through C58, R123,  -> Ht1 candidate
                  the C57 shunt, then R121).

    Nodal analysis, 4 nodes (N1, Nx, N4, N2), vectorised over frequency.
    """
    if drive not in ("top", "bottom", "hh1"):
        raise ValueError(drive)
    if not (0.0 <= alpha <= 1.0):
        raise ValueError(f"alpha out of [0, 1]: {alpha}")

    f_hz = np.asarray(f_hz, dtype=float)
    w = 2.0 * np.pi * f_hz
    s = 1j * w

    Za = R112 + 1.0 / (s * C55)
    Zb = R120 + 1.0 / (s * C56)
    Z1 = R123 + 1.0 / (s * C58)
    # A literal alpha = 0 shorts N1 straight to ground (a real, if extreme,
    # circuit state) -- represent it as a very small but nonzero resistance
    # so 1/Ra stays finite rather than raising.
    Ra = max(alpha, 1e-9) * VR4_TOTAL
    Rb = R125 + (1.0 - alpha) * VR4_TOTAL

    Ya, Yb, Y1 = 1.0 / Za, 1.0 / Zb, 1.0 / Z1
    YRa, YRb = 1.0 / Ra, 1.0 / Rb
    YR119, YR129, YR121 = 1.0 / R119, 1.0 / R129, 1.0 / R121
    YC57, YC90 = s * C57, s * C90

    n = f_hz.shape[0]
    A = np.zeros((n, 4, 4), dtype=complex)
    b = np.zeros((n, 4), dtype=complex)

    A[:, 0, 0] = Ya + YR119 + YRa
    A[:, 0, 3] = -YR119
    A[:, 1, 1] = Y1 + YC57 + YR121
    A[:, 1, 2] = -YR121
    A[:, 2, 1] = -YR121
    A[:, 2, 2] = Yb + YR129 + YRb + YR121
    A[:, 2, 3] = -YR129
    A[:, 3, 0] = -YR119
    A[:, 3, 2] = -YR129
    A[:, 3, 3] = YR119 + YR129 + YC90

    if drive == "top":
        b[:, 0] = Ya
    elif drive == "bottom":
        b[:, 2] = Yb
    else:
        b[:, 1] = Y1

    V = np.linalg.solve(A, b[:, :, None])[:, :, 0]
    return V[:, 3]


DRIVE_OF = {"Ht1": "hh1", "Ht2": "bottom", "Ht3": "top"}


# ---------------------------------------------------------------------------
# The same network, built a second and structurally different way, with
# numpy/scipy only -- see the module docstring's "checked TWO ways".
#
# Here each series R-C branch is split at its own internal node (Pa, Pb, P1),
# so every element is a plain resistor or a plain capacitor and the system is
# exactly (G + s*C) v = b with G, C real and constant. `solve_vtone` instead
# folds those branches into Z = R + 1/(sC) by hand. Agreement between the two
# is what makes that hand elimination a checked step.
# ---------------------------------------------------------------------------

MNA_NODES = ("N1", "N2", "N4", "Nx", "Pa", "Pb", "P1")
_MNA_IDX = {name: i for i, name in enumerate(MNA_NODES)}

# Which internal node each source injects into, and through which coupling cap.
MNA_SOURCE = {"top": ("Pa", "C55"), "bottom": ("Pb", "C56"), "hh1": ("P1", "C58")}


def mna_matrices(alpha):
    """(G, C): the conductance and capacitance matrices over `MNA_NODES`.

    Neither depends on which source is driven -- that is the whole content of
    "one network, one denominator": `drive` only ever changes the right-hand
    side, so all three transfer functions share this matrix pencil and hence
    their poles, exactly and by construction.
    """
    if not (0.0 <= alpha <= 1.0):
        raise ValueError(f"alpha out of [0, 1]: {alpha}")

    n = len(MNA_NODES)
    G = np.zeros((n, n))
    C = np.zeros((n, n))

    def stamp(M, a, b, value):
        """Stamp `value` between nodes `a` and `b` (None == ground)."""
        if a is not None:
            M[_MNA_IDX[a], _MNA_IDX[a]] += value
        if b is not None:
            M[_MNA_IDX[b], _MNA_IDX[b]] += value
        if a is not None and b is not None:
            M[_MNA_IDX[a], _MNA_IDX[b]] -= value
            M[_MNA_IDX[b], _MNA_IDX[a]] -= value

    Ra = max(alpha, 1e-9) * VR4_TOTAL
    Rb = R125 + (1.0 - alpha) * VR4_TOTAL

    stamp(G, "Pa", "N1", 1.0 / R112)
    stamp(G, "N1", "N2", 1.0 / R119)
    stamp(G, "N1", None, 1.0 / Ra)
    stamp(G, "Pb", "N4", 1.0 / R120)
    stamp(G, "N4", "N2", 1.0 / R129)
    stamp(G, "N4", None, 1.0 / Rb)
    stamp(G, "P1", "Nx", 1.0 / R123)
    stamp(G, "Nx", "N4", 1.0 / R121)

    stamp(C, "Pa", None, C55)
    stamp(C, "Pb", None, C56)
    stamp(C, "P1", None, C58)
    stamp(C, "Nx", None, C57)
    stamp(C, "N2", None, C90)

    return G, C


def solve_vtone_mna(f_hz, alpha, drive):
    """`solve_vtone`'s answer, from the seven-node formulation instead."""
    if drive not in MNA_SOURCE:
        raise ValueError(drive)
    G, C = mna_matrices(alpha)
    node, cap = MNA_SOURCE[drive]
    c_val = {"C55": C55, "C56": C56, "C58": C58}[cap]

    f_hz = np.asarray(f_hz, dtype=float)
    s = 2j * np.pi * f_hz
    out = np.empty(f_hz.shape, dtype=complex)
    for i, sv in enumerate(s):
        b = np.zeros(len(MNA_NODES), dtype=complex)
        # A unit source behind the coupling cap injects s*C*Vsrc into the node.
        b[_MNA_IDX[node]] = sv * c_val
        out[i] = np.linalg.solve(G + sv * C, b)[_MNA_IDX["N2"]]
    return out


def poles_hz(alpha=None):
    """The network's pole frequencies in Hz, shared by all three paths.

    Finite generalised eigenvalues of the pencil (-G, C). `C` has exactly five
    nonzero (diagonal) entries, so a 5th-order denominator is a COUNT here,
    not an assertion about a polynomial degree -- and it needs no symbolic
    algebra, so unlike the `sympy` witness it runs wherever numpy/scipy do.
    """
    from scipy.linalg import eig

    G, C = mna_matrices(ALPHA_K1 if alpha is None else alpha)
    ev = eig(-G, C, right=False)
    finite = ev[np.isfinite(ev)]
    return np.sort(np.abs(finite) / (2.0 * np.pi))


def db_at(f_hz, alpha, name):
    """20*log10|Ht_name(f_hz)| at the given wiper fraction."""
    H = solve_vtone(f_hz, alpha, DRIVE_OF[name])
    return 20.0 * np.log10(np.abs(H))


# ---------------------------------------------------------------------------
# Fitting alpha (and checking the (Va, Vb) <-> (Ht3, Ht2) assignment)
# against Figure 9's own digitised evidence
# ---------------------------------------------------------------------------


def _measured_k1(data, name):
    v = data[name]
    hz, db = v["curves"][v["k1_index"]]
    return np.asarray(hz, dtype=float), np.asarray(db, dtype=float)


def fit_one(data, name, drive, free_gain=True):
    """Least-squares wiper fraction (and, if `free_gain`, a flat dB offset)
    fitting `drive`'s network response to Figure 9's digitised `name` curve.

    Returns (alpha, gain_offset_db, rms_db). With `free_gain=False` the only
    free parameter is alpha -- the honest version of "does the schematic's
    OWN level, with no fudge, explain the figure".
    """
    from scipy.optimize import least_squares

    hz, db = _measured_k1(data, name)

    def resid(p):
        alpha = 1.0 / (1.0 + math.exp(-p[0]))  # unconstrained -> (0, 1)
        gain = p[1] if free_gain else 0.0
        return gain + db_at(hz, alpha, {"top": "Ht3", "bottom": "Ht2",
                                        "hh1": "Ht1"}[drive]) - db

    x0 = [0.0, 0.0] if free_gain else [0.0]
    r = least_squares(resid, x0)
    alpha = 1.0 / (1.0 + math.exp(-r.x[0]))
    gain = r.x[1] if free_gain else 0.0
    rms = float(np.sqrt(np.mean(r.fun ** 2)))
    return alpha, gain, rms


def fit_alpha_k1(data):
    """The single wiper fraction that fits ALL THREE of Figure 9's k = 1.0
    curves at once, with NO per-path gain offset -- see module docstring."""
    from scipy.optimize import least_squares

    curves = {"Ht3": "top", "Ht2": "bottom", "Ht1": "hh1"}
    measured = {name: _measured_k1(data, name) for name in curves}

    def resid(p):
        alpha = 1.0 / (1.0 + math.exp(-p[0]))
        parts = []
        for name, drive in curves.items():
            hz, db = measured[name]
            parts.append(db_at(hz, alpha, name) - db)
        return np.concatenate(parts)

    r = least_squares(resid, [math.log(0.4 / 0.6)])
    alpha = 1.0 / (1.0 + math.exp(-r.x[0]))
    rms = float(np.sqrt(np.mean(r.fun ** 2)))
    return alpha, rms


# ---------------------------------------------------------------------------
# Reporting / gate
# ---------------------------------------------------------------------------


def check(data) -> tuple[bool, list[str]]:
    lines = []
    ok = True

    alpha, joint_rms = fit_alpha_k1(data)
    lines.append(f"joint fit (one alpha, zero per-path gain, all 3 families): "
                 f"alpha={alpha:.4f}  rms={joint_rms:.4f} dB")
    if abs(alpha - ALPHA_K1) > 1e-3:
        ok = False
        lines.append(f"  FAIL: fitted alpha has drifted from the committed "
                     f"ALPHA_K1={ALPHA_K1:.6f}")
    if joint_rms > 0.05:
        ok = False
        lines.append("  FAIL: joint RMS above 0.05 dB -- schematic no longer "
                     "matches Figure 9 without a fudge factor")

    for name, drive in DRIVE_OF.items():
        hz, db = _measured_k1(data, name)
        pred = db_at(hz, ALPHA_K1, name)
        rms = float(np.sqrt(np.mean((pred - db) ** 2)))
        lines.append(f"{name} ({drive:6s}) vs Figure 9's own curve: "
                     f"rms={rms:.4f} dB over {hz.min():.0f}-{hz.max():.0f} Hz "
                     f"({len(hz)} points)")
        if rms > 0.1:
            ok = False
            lines.append(f"  FAIL: {name} residual above 0.1 dB")

    # Passivity: a passive RC network cannot have |H| > 1 anywhere.
    f_grid = np.logspace(math.log10(20.0), math.log10(20000.0), 2000)
    for name in DRIVE_OF:
        peak_db = float(db_at(f_grid, ALPHA_K1, name).max())
        lines.append(f"{name} peak over 20 Hz-20 kHz: {peak_db:+.2f} dB "
                     "(passive iff <= 0)")
        if peak_db > 0.05:
            ok = False
            lines.append(f"  FAIL: {name} exceeds 0 dB -- not passive, "
                         "something in the model is wrong")

    # Cross-check against Figure 9's OWN extrapolation bounds at the
    # cymbal's band, computed independently by werner_fig9 (not by this
    # module) -- an inconsistency here means the two routes disagree.
    for name in ("Ht1", "Ht2"):
        hz, db = _measured_k1(data, name)
        for f_target in CY_BANDS_HZ:
            bound = w9.extrapolation_bound(hz, db, f_target)
            schem = float(db_at(np.array([f_target]), ALPHA_K1, name)[0])
            lines.append(
                f"{name} @ {f_target / 1000:.2f} kHz: schematic {schem:+.2f} dB, "
                f"Figure 9 extrapolation bound [{bound['lo_db']:+.2f} .. "
                f"{bound['hi_db']:+.2f}] dB")
            if not (bound["lo_db"] - 0.5 <= schem <= bound["hi_db"] + 0.5):
                ok = False
                lines.append(
                    f"  FAIL: schematic value falls outside Figure 9's own "
                    "extrapolation bound -- the two routes disagree")

    return ok, lines


def report(data) -> list[str]:
    lines = []
    alpha = ALPHA_K1
    lines.append(f"CY TONE network, SN p.13, wiper fraction alpha = {alpha:.4f} "
                 "at Figure 9's k = 1.0 (fit_alpha_k1, not assumed)")
    lines.append(f"  R_top (N1 -> gnd)  = {alpha * VR4_TOTAL:7.0f} ohm")
    lines.append(f"  R_bot (N4 -> gnd)  = {R125 + (1 - alpha) * VR4_TOTAL:7.0f} "
                 "ohm (R125 + VR4 bottom-half)")
    lines.append("")
    for name in ("Ht1", "Ht2", "Ht3"):
        vals = db_at(np.array([*CY_BANDS_HZ, 20000.0]), alpha, name)
        lines.append(f"{name}: @3.45 kHz {vals[0]:+7.2f} dB   "
                     f"@7.1 kHz {vals[1]:+7.2f} dB   @20 kHz {vals[2]:+7.2f} dB")
    lines.append("")
    lines.append("relative to Ht3 (the resolved inter-band balance):")
    ht3 = db_at(np.array(CY_BANDS_HZ), alpha, "Ht3")
    for name in ("Ht1", "Ht2"):
        v = db_at(np.array(CY_BANDS_HZ), alpha, name)
        lines.append(f"  {name} - Ht3: {v[0] - ht3[0]:+6.2f} dB @ 3.45 kHz   "
                     f"{v[1] - ht3[1]:+6.2f} dB @ 7.1 kHz")
    return lines


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--artifact", default=str(w9.ARTIFACT))
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--report", action="store_true")
    ap.add_argument("--poles", action="store_true",
                    help="the shared pole set, from the numpy-only formulation")
    ap.add_argument("--verify-source", metavar="SN_PDF",
                    help="check a local copy of the pinned SN scan against "
                         "SN_PDF_SHA256 and re-render the crops the component "
                         "values were read off")
    a = ap.parse_args(argv)

    if a.verify_source:
        try:
            info = verify_source(a.verify_source)
        except SourceUnavailable as exc:
            print(f"REFUSED: {exc}", file=sys.stderr)
            return 3
        print(f"source OK: {info['path']}")
        print(f"  sha256 {info['sha256']} (matches SN_PDF_SHA256)")
        print(f"  page   {info['page']} (voicing board VG 3116-140)")
        for p in info["crops"]:
            print(f"  crop   {p}")
        return 0

    if a.poles:
        p = poles_hz()
        print(f"shared denominator, {len(p)} finite poles at "
              f"alpha = {ALPHA_K1:.4f} (Hz):")
        for f in p:
            print(f"  {f:10.2f}")
        if not (a.check or a.report):
            return 0
        print()

    try:
        data, _meta, _blob = w9.from_artifact(pathlib.Path(a.artifact))
    except w9.Refused as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 3

    rc = 0
    if a.check or not a.report:
        ok, lines = check(data)
        for line in lines:
            print(line)
        print("GATE:", "PASS" if ok else "FAIL")
        rc = 0 if ok else 1

    if a.report:
        if a.check:
            print()
        for line in report(data):
            print(line)

    return rc


if __name__ == "__main__":
    raise SystemExit(main())
