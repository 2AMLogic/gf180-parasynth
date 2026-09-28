#!/usr/bin/env python3
"""The TR-808 cymbal's three swing VCAs (Q16/Q17/Q18), read off SN p.13.

WHY THIS EXISTS. Every route to the cymbal's inter-band balance has now
terminated on the same unmeasured quantity, three times over:

  * #396 REFUSED because "the three envelope generators' and swing VCAs' peak
    drive (Q16/Q17/Q18) ... no W14b figure plots them"
    (`tools/cymbal_band_balance.py::preconditions`, precondition `vca-drive`);
  * step 8 (`docs/scorecard/cymbal-369/vca-clip/`) swept §10's asymmetric
    clipping and could not reach the 808's joint (energy, decay) box at ANY
    drive, so the drive it needed was never pinned;
  * step 10 (`docs/scorecard/cymbal-369/tone-render/`) could not even STATE a
    "+X dB on the short band" requirement, because the swing VCAs clip and the
    bands therefore do not superpose.

Step 10 ended by naming exactly one next question, and this module is that
question:

    What sets the three swing VCAs' drive levels? The artifact that would
    resolve it is SN p.13's own resistor network around Q16/Q17/Q18, read the
    way `tools/tone_stage_schematic.py` read VR4's -- by nodal analysis off a
    hash-pinned scan, with `--verify-source` and a refusal, not by fitting a
    drive to a recording.

WHAT THE SCHEMATIC SAYS, AND IT IS NOT WHAT THE QUESTION ASSUMED. The question
is phrased as though three drive levels exist to be read. They do not. The
three stages are component-identical everywhere except ONE resistor:

  * the same 0.022 uF coupling capacitor into each base (C42, C44, C46);
  * the same 2 Mohm fixed base bias from B1 -- TWO 1 Mohm resistors in SERIES,
    with no ground leg, so the three stages carry the same base current and
    therefore the same gm (R96+R97, R100+R99, R102+R103);
  * the same 100 ohm emitter degeneration (R95, R98, R101);
  * the same series diode into the collector (D5, D11, D12) and the same pair
    of 0.0015 uF bypass capacitors around it;
  * and Q16 and Q17 take their signal from the SAME NODE -- IC3 pin 7, the
    7.1 kHz band-pass output -- with nothing between them. Their signal drives
    are not merely similar, they are identical by construction.

The one element that differs is the collector load, the resistor from each
band's own envelope reservoir down to its VCA output node:

    Q16 (high, short)   R94  39 kohm
    Q17 (high, DECAY)   R90  33 kohm
    Q18 (low)           R104 22 kohm

A common-emitter stage with the same degeneration and the same bias has gain
proportional to that resistor, so the VCA section's whole inter-band
contribution is 20*log10(39/22) = +4.97 dB on the short band and
20*log10(33/22) = +3.52 dB on the DECAY band, relative to the low band. The
balance needs +38.23 dB and +9.85 dB (`../balance/balance.json`). To supply
the short band's, R94 would have to be 1.79 Mohm. The schematic prints 39 k.

WHY THE READ CAN BE TRUSTED -- an external known answer, not a self-check.
The read includes two elements no document in this repository has ever
carried: each band-pass's own INPUT network (C10 0.0033 uF + R52 33 k on the
3.45 kHz filter, C11 0.001 uF + R55 22 k on the 7.1 kHz filter). Those set the
filters' absolute gain and nothing else here does. Solving the two bridged-T
band-passes with them gives peak gains of +22.96 dB and +24.11 dB, against
`model/cymbal_candidate.BP_PEAK_DB`'s +22.95 and +24.10 -- which were
digitised off W14b Figure 4 by `tools/werner_fig4.py`, by a different author,
from a different artifact, with no knowledge of R52/R55/C10/C11. Agreement to
0.01 dB is an external known-answer test on the schematic read itself: a
misread resistor or capacitor in the input network moves it immediately
(`DROP_INPUT_NETWORK` below moves it 8.6 dB), and f0 and Q are BLIND to that
network, so they cannot stand in for it.

PRECONDITIONS, ASSERTED AT THE POINT OF USE, REFUSED WHEN UNMET:

  * `--verify-source <pdf>` REFUSES (exit 3) unless the file's SHA-256 is
    `SN_PDF_SHA256` -- the same pin `tools/tone_stage_schematic.py` carries,
    asserted equal in the tests so the two modules cannot silently read
    different printings of the service notes. The scan is a ~6 MB third-party
    download and is deliberately NOT a test dependency; `--require-source`
    makes it binding for a caller who wants it to be, and exits 1 rather than
    reporting when it is absent.
  * `balance_targets()` re-reads `../balance/balance.json` and REFUSES if the
    two gap figures this module is judged against have drifted from the
    committed record. A conclusion quoted against a number that has since
    moved is the "correct instrument in a wrong state" failure this repository
    keeps meeting.

WHAT THIS MODULE DOES NOT SETTLE, stated rather than left to be assumed:

  * the three envelope generators' PEAK voltages. All three reservoirs are
    charged from Q19 through their own diode (D6, D7, D8), but each sits
    behind a different smoothing network (R87+C37/C38, R88+C39/C40,
    R105+C45), and the collector's DC operating point -- which is what sets
    where the swing VCA clips -- depends on it. This module reports the
    SIGNAL-path term only, which is the term the band balance multiplies.
  * the high-pass input loading on the two HIGH bands. Hh1's input network is
    fully read here (C48/C59/R124/R127, a unity-gain Sallen-Key whose f0 and Q
    reproduce §10's 2.5 kHz / 0.97), so the low band's loaded collector
    impedance is computed exactly; Hh2's and Hh3's are not. Because a passive
    load can only REDUCE |Z|, leaving the high bands unloaded makes the
    reported ratio an UPPER BOUND, which is the direction that matters: the
    bound is +8.60 dB (short) and +7.15 dB (decay), still 29.6 dB short of the
    balance's own requirement for the short band.

Usage:
    python3 tools/cymbal_vca_drive.py --report
    python3 tools/cymbal_vca_drive.py --check          # properties + defects
    python3 tools/cymbal_vca_drive.py --check --require-source <sn.pdf>
    python3 tools/cymbal_vca_drive.py --json docs/scorecard/cymbal-369/vca-drive/vca-drive.json
    python3 tools/cymbal_vca_drive.py --verify-source <sn.pdf>
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import pathlib
import shutil
import subprocess
import sys

import numpy as np

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT / "model") not in sys.path:
    sys.path.insert(0, str(ROOT / "model"))
if str(ROOT / "tools") not in sys.path:
    sys.path.insert(0, str(ROOT / "tools"))

import cymbal_candidate as cc  # noqa: E402

SCORECARD = ROOT / "docs" / "scorecard" / "cymbal-369"
BALANCE_ARTIFACT = SCORECARD / "balance" / "balance.json"
ARTIFACT = SCORECARD / "vca-drive" / "vca-drive.json"


# ---------------------------------------------------------------------------
# The source, pinned so the read is reproducible rather than merely cited.
# ---------------------------------------------------------------------------

# NOTE ON THE URL. `tools/tone_stage_schematic.py` records the
# `https://archive.org/download/...` route. On 2026-09-28 that route returned
# HTTP 500 for this item while the file itself was intact; the per-node route
# below served the identical bytes (SHA-256 matched on the first try). Both are
# recorded because the failing one is the one already written down elsewhere.
SN_PDF_URL = ("https://archive.org/download/synthmanual-roland-tr-808-service-"
              "notes/rolandtr-808servicenotes.pdf")
SN_PDF_URL_NODE = ("https://ia801906.us.archive.org/9/items/synthmanual-roland-"
                   "tr-808-service-notes/rolandtr-808servicenotes.pdf")
SN_PDF_SHA256 = "d3239b51e2eb5523ff7247528667dc753f9db84a8d29b62cf763cbc51d4aad74"
SN_PDF_PAGE = 13  # 1-based; the voicing-board schematic, VG 3116-140

# Crop boxes in pixels at the stated dpi on that page (A3, 1190.52 x 840.96
# pt), as consumed by `pdftoppm -r <dpi> -x -y -W -H`. Every component value
# below was read off one of these three regions.
SN_CROPS = {
    # Q16, its 2 Mohm base bias, R95, D5/C36/C47, R94 and the C38/R87/C37
    # envelope reservoir; Q17's base network and D7/C40/R88/C39/R90.
    "vca-hi": {"dpi": 600, "x": 2350, "y": 4180, "w": 1550, "h": 1150},
    # Q17's emitter, Q18 and its bias, D12, R105/C45/R104, and Hh1's own
    # C48/C59/R124/R127 on Q25 -- the low band's load.
    "vca-lo": {"dpi": 600, "x": 2350, "y": 5250, "w": 1500, "h": 1100},
    # The six-square summing bus (R35/R37/R39/R46/R48/R50 120 k into R53 1 k),
    # both bridged-T band-passes with their INPUT networks, and IC3 pins 1/7
    # feeding C46 and C42/C44.
    "bandpass": {"dpi": 400, "x": 700, "y": 3250, "w": 1100, "h": 1100},
}


class Refused(Exception):
    """A precondition failed. REFUSED is a verdict, distinct from a value."""


class SourceUnavailable(Refused):
    """The pinned scan is absent, unreadable, or does not match its hash."""


def verify_source(path) -> dict:
    """Assert `path` IS the pinned SN scan, then render `SN_CROPS` beside it.

    REFUSES rather than answering when the file is missing or its SHA-256 does
    not match: a schematic read checked against the wrong printing of the
    service notes would look exactly like a checked one.
    """
    path = pathlib.Path(path)
    if not path.is_file():
        raise SourceUnavailable(
            f"{path} does not exist. Fetch the pinned scan first:\n"
            f"  curl -sL -o {path} {SN_PDF_URL_NODE}")

    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    if digest != SN_PDF_SHA256:
        raise SourceUnavailable(
            f"{path} is not the pinned scan: sha256 {digest}, expected "
            f"{SN_PDF_SHA256}. Every component value in this module was read "
            "off the pinned printing.")

    pdftoppm = shutil.which("pdftoppm")
    if pdftoppm is None:
        raise SourceUnavailable(
            "pdftoppm (poppler-utils) is not on PATH, so the crops cannot be "
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
# The read. Reference designators exactly as printed on SN p.13.
# ---------------------------------------------------------------------------

BANDS = ("low", "decay", "short")

# The three swing-VCA stages. `feed` names the IC3 output pin each base hangs
# on; `bias_ohm` is the SERIES total from the B1 rail to the base (two 1 Mohm
# parts, no ground leg -- this is a fixed base bias, not a divider, which is
# what makes the three stages' collector currents equal by construction);
# `load_ohm` is the resistor from the band's envelope reservoir to the VCA's
# own output node, i.e. the collector load.
STAGES = {
    "short": {
        "q": "Q16", "feed": "IC3 pin 7", "couple_f": 0.022e-6, "couple": "C42",
        "bias_ohm": 2.0e6, "bias": ("R96 1M", "R97 1M"),
        "emitter_ohm": 100.0, "emitter": "R95",
        "series_diode": "D5", "bypass_f": (0.0015e-6, 0.0015e-6),
        "bypass": ("C36", "C47"),
        "load_ohm": 39e3, "load": "R94",
        "reservoir": "C38 1u/50 via D6, smoothed by R87 22k + C37 2.2u",
    },
    "decay": {
        "q": "Q17", "feed": "IC3 pin 7", "couple_f": 0.022e-6, "couple": "C44",
        "bias_ohm": 2.0e6, "bias": ("R100 1M", "R99 1M"),
        "emitter_ohm": 100.0, "emitter": "R98",
        "series_diode": "D11", "bypass_f": (0.0015e-6, 0.0015e-6),
        "bypass": ("C43", "C50"),
        "load_ohm": 33e3, "load": "R90",
        "reservoir": "C40 1u/50 via D7, smoothed by R88 33k + C39 0.47u",
    },
    "low": {
        "q": "Q18", "feed": "IC3 pin 1", "couple_f": 0.022e-6, "couple": "C46",
        "bias_ohm": 2.0e6, "bias": ("R102 1M", "R103 1M"),
        "emitter_ohm": 100.0, "emitter": "R101",
        "series_diode": "D12", "bypass_f": (None, None),
        "bypass": (None, None),
        "load_ohm": 22e3, "load": "R104",
        "reservoir": "via R105 33k from the Q20 envelope, smoothed by C45 2.2u",
    },
}

# The two bridged-T band-passes on IC3. `cap_f` is the pair of equal caps in
# the bridged-T; `shunt_ohm` is the resistor from the T's midpoint to ground;
# `fb_ohm` bridges the T. `in_*` is the filter's own INPUT network, in series
# from the shared square-wave bus to the op-amp's inverting input -- the part
# no prior document here carries, and the part the peak gain tests.
BANDPASS = {
    "low": {
        "out_pin": "IC3 pin 1", "f0_hz": 3450.0,
        "cap_f": 0.0068e-6, "caps": ("C13", "C14"),
        "shunt_ohm": 560.0, "shunt": "R56",
        "fb_ohm": 82e3, "fb": "R57",
        "in_cap_f": 0.0033e-6, "in_cap": "C10",
        "in_res_ohm": 33e3, "in_res": "R52",
    },
    "high": {
        "out_pin": "IC3 pin 7", "f0_hz": 7100.0,
        "cap_f": 0.0033e-6, "caps": ("C15", "C16"),
        "shunt_ohm": 560.0, "shunt": "R58",
        "fb_ohm": 82e3, "fb": "R59",
        "in_cap_f": 0.001e-6, "in_cap": "C11",
        "in_res_ohm": 22e3, "in_res": "R55",
    },
}
BP_OF_BAND = {"low": "low", "decay": "high", "short": "high"}

# The six Schmitt squares are summed passively into one node and BOTH filters
# tap it, so no per-band term hides here either.
SUM_RES_OHM = 120e3
SUM_RES = ("R35", "R37", "R39", "R46", "R48", "R50")
SUM_SHUNT_OHM = 1e3
SUM_SHUNT = "R53"

# Hh1, the low band's load: a unity-gain Sallen-Key high-pass on emitter
# follower Q25. Recorded in reference §10 already; repeated here because it is
# the only one of the three high-passes whose INPUT impedance this module
# needs, and its f0/Q are a second known answer on the read.
HH1 = {"c_in_f": 0.0015e-6, "c_in": "C48", "c_mid_f": 0.0015e-6, "c_mid": "C59",
       "r_fb_ohm": 22e3, "r_fb": "R124", "r_gnd_ohm": 82e3, "r_gnd": "R127",
       "f0_hz": 2500.0, "q": 0.97}

# Where each band's level is actually set, taken from the committed balance
# record rather than chosen here (`../balance/balance.json` `centre_hz`).
LEVEL_CENTRE_HZ = {"low": 3175.0, "decay": 10079.0, "short": 10079.0}

# Bounds, all stated before the numbers below were quoted.
PEAK_TOL_DB = 0.05    # against W14b Figure 4's digitised peaks
F0_TOL = 0.05         # against reference §10's 3450 / 7100 / 2500 Hz
Q_TOL = 0.10          # against reference §10's 6.0 / 0.97
GAP_MARGIN_DB = 20.0  # how far below the short band's gap the VCA term must sit
LOAD_MARGIN_DB = 20.0 # printed vs required collector load

DEFECTS = ("SWAP_BP_CAPS", "DROP_INPUT_NETWORK", "UNEQUAL_EMITTER",
           "SPLIT_HIGH_FEED", "EQUAL_COLLECTOR_LOADS", "HUGE_SHORT_LOAD",
           "HH1_WRONG_RATIO")


def config(defect=None) -> dict:
    """The read, with one named defect injected.

    Every defect is a change to a COMPONENT VALUE or a WIRE, never to a
    property's bound, so a defect that turns nothing red is evidence the
    property it was aimed at is not measuring what it claims.
    """
    if defect is not None and defect not in DEFECTS:
        raise Refused(f"unknown defect {defect!r}; known: {DEFECTS}")
    stages = {b: dict(v) for b, v in STAGES.items()}
    bp = {k: dict(v) for k, v in BANDPASS.items()}
    hh1 = dict(HH1)

    if defect == "SWAP_BP_CAPS":
        bp["low"]["cap_f"], bp["high"]["cap_f"] = bp["high"]["cap_f"], bp["low"]["cap_f"]
    elif defect == "DROP_INPUT_NETWORK":
        # The mistake a reader makes who has §10 but not the schematic: §10
        # records the bridged-T and says nothing about what drives it.
        for k in bp:
            bp[k]["in_cap_f"] = 1.0        # 1 F, i.e. a short at audio
            bp[k]["in_res_ohm"] = 10e3
    elif defect == "UNEQUAL_EMITTER":
        stages["decay"]["emitter_ohm"] = 220.0
    elif defect == "SPLIT_HIGH_FEED":
        stages["decay"]["feed"] = "IC3 pin 1"
    elif defect == "EQUAL_COLLECTOR_LOADS":
        for b in stages:
            stages[b]["load_ohm"] = 22e3
    elif defect == "HUGE_SHORT_LOAD":
        stages["short"]["load_ohm"] = 1.8e6
    elif defect == "HH1_WRONG_RATIO":
        hh1 = dict(hh1, r_fb_ohm=82e3, r_gnd_ohm=22e3)   # swapped -> Q moves

    return {"stages": stages, "bandpass": bp, "hh1": hh1, "defect": defect}


# ---------------------------------------------------------------------------
# The linear sections. Two formulations where one would do, for the same
# reason `tone_stage_schematic` carries two: a hand-eliminated expression that
# is easy to read and an MNA build that is hard to get subtly wrong.
# ---------------------------------------------------------------------------


def _s(hz):
    return 2j * np.pi * np.asarray(hz, dtype=float)


def _db(x):
    return 20.0 * np.log10(np.maximum(np.abs(x), 1e-30))


def bandpass_response(which, hz, cfg=None):
    """Vout/Vin of one bridged-T band-pass, INCLUDING its input network.

    The op-amp's inverting input is a virtual ground, so the input arm is
    Zin = R + 1/(sC) and the feedback network's transadmittance carries the
    rest. Derived by hand; `bandpass_response_mna` builds the same thing from
    a node-admittance matrix and the two are asserted equal in the tests.
    """
    cfg = cfg if cfg is not None else config()
    p = cfg["bandpass"][which]
    s = _s(hz)
    c, rs, rf = p["cap_f"], p["shunt_ohm"], p["fb_ohm"]
    z_in = p["in_res_ohm"] + 1.0 / (s * p["in_cap_f"])
    # Bridged-T between the virtual ground and the output: R_f in parallel
    # with the series C-C arm whose midpoint is shunted by R_s.
    y_t = 1.0 / rf + (s * s * c * c) / (2.0 * s * c + 1.0 / rs)
    return -(1.0 / z_in) / y_t


def bandpass_response_mna(which, hz, cfg=None):
    """The same transfer function from an explicit (G + sC) v = b solve.

    Four unknown nodes: the input arm's internal node (between R_in and C_in),
    the inverting input, the bridged-T midpoint, and the output. The op-amp is
    an ideal nullor: v(inv) = 0 is imposed, and the output node's own equation
    is replaced by that constraint, which is exactly what an ideal op-amp does.
    """
    cfg = cfg if cfg is not None else config()
    p = cfg["bandpass"][which]
    c, rs, rf = p["cap_f"], p["shunt_ohm"], p["fb_ohm"]
    hz = np.atleast_1d(np.asarray(hz, dtype=float))
    out = np.empty(hz.shape, dtype=complex)
    for i, f in enumerate(hz):
        s = 2j * math.pi * f
        # nodes: 0 = between R_in and C_in, 1 = inverting input, 2 = T midpoint,
        # 3 = output.  Source drives R_in from a 1 V ideal source.
        y_rin = 1.0 / p["in_res_ohm"]
        y_cin = s * p["in_cap_f"]
        y_rf = 1.0 / rf
        y_c = s * c
        y_rs = 1.0 / rs
        A = np.zeros((4, 4), dtype=complex)
        b = np.zeros(4, dtype=complex)
        # node 0
        A[0, 0] = y_rin + y_cin
        A[0, 1] = -y_cin
        b[0] = y_rin * 1.0
        # node 1 (inverting input): KCL is not imposed on the nullor's input
        # current, so this row states the virtual ground instead.
        A[1, 1] = 1.0
        b[1] = 0.0
        # node 2 (T midpoint)
        A[2, 1] = -y_c
        A[2, 2] = 2.0 * y_c + y_rs
        A[2, 3] = -y_c
        # node 3 (output): the nullor forces the current INTO node 1 to be the
        # current the input arm delivers, which is the KCL at node 1 written
        # with the op-amp's own input current removed.
        A[3, 0] = -y_cin
        A[3, 1] = y_cin + y_c + y_rf
        A[3, 2] = -y_c
        A[3, 3] = -y_rf
        out[i] = np.linalg.solve(A, b)[3]
    return out if out.size > 1 else out[0]


def bandpass_peak(which, cfg=None) -> tuple[float, float]:
    """(peak frequency in Hz, peak gain in dB) on a fine geometric grid."""
    hz = np.geomspace(200.0, 40000.0, 400001)
    db = _db(bandpass_response(which, hz, cfg))
    i = int(np.argmax(db))
    return float(hz[i]), float(db[i])


def bandpass_f0_q(which, cfg=None) -> tuple[float, float]:
    """f0 and Q of the bridged-T, in closed form from the components.

    The feedback network's numerator is s^2 C^2 + 2 s C / Rf + 1/(Rs Rf), so
    w0 = 1/(C sqrt(Rs Rf)) and Q = (1/2) sqrt(Rf / Rs). Both are independent
    of the input network -- which is why `DROP_INPUT_NETWORK` must leave them
    untouched and move the peak gain instead.
    """
    cfg = cfg if cfg is not None else config()
    p = cfg["bandpass"][which]
    c, rs, rf = p["cap_f"], p["shunt_ohm"], p["fb_ohm"]
    f0 = 1.0 / (2.0 * math.pi * c * math.sqrt(rs * rf))
    q = 0.5 * math.sqrt(rf / rs)
    return f0, q


def hh1_input_impedance(hz, cfg=None):
    """Z seen looking into Hh1 from the low band's VCA output node.

    Unity-gain Sallen-Key high-pass on an emitter follower, modelled as an
    ideal unity-gain buffer: C_in to node A, C_mid from A to the buffer input
    B, R_gnd from B to ground, R_fb from A back to the buffer output. The
    emitter follower's finite output impedance is NOT modelled, and that is
    stated rather than hidden -- it can only raise the loading, i.e. lower the
    low band's effective collector impedance, which moves the reported ratio
    in the direction that makes the headline WEAKER.
    """
    cfg = cfg if cfg is not None else config()
    h = cfg["hh1"]
    s = _s(hz)
    k = (s * h["c_mid_f"] * h["r_gnd_ohm"]) / (1.0 + s * h["c_mid_f"] * h["r_gnd_ohm"])
    y = (1.0 - k) * (1.0 / h["r_fb_ohm"] + s * h["c_mid_f"])
    return 1.0 / y + 1.0 / (s * h["c_in_f"])


def hh1_f0_q(cfg=None) -> tuple[float, float]:
    cfg = cfg if cfg is not None else config()
    h = cfg["hh1"]
    f0 = 1.0 / (2.0 * math.pi * math.sqrt(h["c_in_f"] * h["c_mid_f"]
                                          * h["r_fb_ohm"] * h["r_gnd_ohm"]))
    q = 0.5 * math.sqrt(h["r_gnd_ohm"] / h["r_fb_ohm"])
    return f0, q


# ---------------------------------------------------------------------------
# The answer
# ---------------------------------------------------------------------------


def stage_differences(cfg=None) -> dict:
    """Which per-stage components differ across the three VCAs, and which do not.

    The whole finding is in this function's output: the differing set has
    exactly one member.
    """
    cfg = cfg if cfg is not None else config()
    st = cfg["stages"]
    # `feed` is deliberately NOT in this list. Which band-pass a stage hangs on
    # is the band split itself, and the chain already carries both filters'
    # transfer functions; the question here is what the VCA section adds ON TOP
    # of them. The feed is checked separately, as "the two HIGH bands share one
    # node" -- which is the claim that removes a drive rather than measures one.
    fields = ("couple_f", "bias_ohm", "emitter_ohm", "load_ohm")
    same, differ = [], []
    for f in fields:
        vals = {b: st[b][f] for b in BANDS}
        (differ if len(set(vals.values())) > 1 else same).append(f)
    return {
        "identical": sorted(same),
        "differing": sorted(differ),
        "values": {f: {b: st[b][f] for b in BANDS} for f in fields + ("feed",)},
        "high_bands_share_feed": st["short"]["feed"] == st["decay"]["feed"],
    }


def vca_term_db(cfg=None) -> dict:
    """The VCA section's own inter-band term, relative to the low band.

    Two conventions, both reported, because they answer to different callers:

      * `chain_db` -- the collector-load ratio alone. This is the term to
        multiply into `cymbal_band_balance.band_filter_db`, which already
        carries each band's high-pass separately, so the loading must not be
        counted twice.
      * `bound_db` -- the low band's collector impedance loaded by Hh1's
        measured input impedance at its own level centre, with the two high
        bands left UNLOADED. A passive load can only reduce |Z|, so this is an
        upper bound on the true ratio, not an estimate of it.
    """
    cfg = cfg if cfg is not None else config()
    st = cfg["stages"]
    rc = {b: st[b]["load_ohm"] for b in BANDS}
    z_hh1 = hh1_input_impedance(LEVEL_CENTRE_HZ["low"], cfg)
    z_low_loaded = 1.0 / (1.0 / rc["low"] + 1.0 / z_hh1)
    return {
        "collector_load_ohm": rc,
        "hh1_z_ohm": float(abs(z_hh1)),
        "low_loaded_ohm": float(abs(z_low_loaded)),
        "chain_db": {b: round(20.0 * math.log10(rc[b] / rc["low"]), 3) for b in BANDS},
        "bound_db": {b: round(20.0 * math.log10(rc[b] / abs(z_low_loaded)), 3)
                     if b != "low" else 0.0 for b in BANDS},
    }


def balance_targets(path=None) -> dict:
    """The gap figures this module is judged against, re-read and pinned.

    REFUSES if `../balance/balance.json` is absent or if its two gap figures
    have moved from the committed record. The headline here is a comparison
    against those numbers; quoting it against a number that has since drifted
    is exactly the failure this repository keeps meeting.
    """
    path = pathlib.Path(path) if path is not None else BALANCE_ARTIFACT
    if not path.is_file():
        raise Refused(f"{path} is absent; the gap this module is judged "
                      "against is #396's, not one chosen here")
    rec = json.loads(path.read_text())
    want = {"decay": 9.85, "short": 38.23}
    got = {b: float(rec["gap_db"][b]["gap_db"]) for b in want}
    drift = {b: round(got[b] - want[b], 3) for b in want if abs(got[b] - want[b]) > 0.005}
    if drift:
        raise Refused(
            f"{path} has drifted from the figures this module quotes: {drift}. "
            "Re-read the conclusion before re-quoting it.")
    return {"path": str(path.relative_to(ROOT)), "gap_db": got,
            "centre_hz": {b: float(v) for b, v in rec["centre_hz"].items()}}


def required_collector_load(cfg=None, targets=None) -> dict:
    """The collector load each band would need for the VCAs to close the gap."""
    cfg = cfg if cfg is not None else config()
    targets = targets if targets is not None else balance_targets()
    rc_low = cfg["stages"]["low"]["load_ohm"]
    out = {}
    for b, gap in targets["gap_db"].items():
        need = rc_low * 10.0 ** (gap / 20.0)
        printed = cfg["stages"][b]["load_ohm"]
        out[b] = {"printed_ohm": printed, "required_ohm": need,
                  "shortfall_db": round(20.0 * math.log10(need / printed), 3)}
    return out


# ---------------------------------------------------------------------------
# Properties, defects, and the gate
# ---------------------------------------------------------------------------


def properties(cfg=None, targets=None) -> dict:
    cfg = cfg if cfg is not None else config()
    targets = targets if targets is not None else balance_targets()
    out = {}

    # 1. THE EXTERNAL KNOWN ANSWER. The peak gains this read produces must
    #    reproduce W14b Figure 4's digitised peaks. Nothing about the figure
    #    informed the input networks, and nothing about the input networks
    #    informed the figure.
    worst, per = 0.0, {}
    for which in ("low", "high"):
        _, db = bandpass_peak(which, cfg)
        ref = cc.BP_PEAK_DB[which]
        per[which] = {"computed_db": round(db, 3), "figure4_db": ref,
                      "delta_db": round(db - ref, 3)}
        worst = max(worst, abs(db - ref))
    out["bp-peak-matches-figure4"] = {
        "value_db": round(worst, 3), "bound_db": PEAK_TOL_DB,
        "ok": worst <= PEAK_TOL_DB, "per_band": per,
        "what": "band-pass peak gain from the schematic == W14b Fig. 4's digitised peak"}

    # 2/3. f0 and Q against reference §10, which took them from W14b.
    worst_f0, worst_q, per = 0.0, 0.0, {}
    for which in ("low", "high"):
        f0, q = bandpass_f0_q(which, cfg)
        tgt = BANDPASS[which]["f0_hz"]
        per[which] = {"f0_hz": round(f0, 1), "target_hz": tgt, "q": round(q, 3)}
        worst_f0 = max(worst_f0, abs(f0 - tgt) / tgt)
        worst_q = max(worst_q, abs(q - 6.0) / 6.0)
    out["bp-f0-matches-reference"] = {
        "value_rel": round(worst_f0, 4), "bound_rel": F0_TOL,
        "ok": worst_f0 <= F0_TOL, "per_band": per,
        "what": "bridged-T centres are §10's 3450 and 7100 Hz"}
    out["bp-q-matches-reference"] = {
        "value_rel": round(worst_q, 4), "bound_rel": Q_TOL,
        "ok": worst_q <= Q_TOL,
        "what": "both bridged-T Q are §10's 6"}

    # 4. Hh1's own f0/Q, a second known answer on the read -- this time on the
    #    network whose INPUT IMPEDANCE the bound in property 7 depends on.
    f0, q = hh1_f0_q(cfg)
    rel = max(abs(f0 - HH1["f0_hz"]) / HH1["f0_hz"], abs(q - HH1["q"]) / HH1["q"] * (F0_TOL / Q_TOL))
    out["hh1-matches-reference"] = {
        "f0_hz": round(f0, 1), "q": round(q, 3), "bound_rel": F0_TOL,
        "ok": (abs(f0 - HH1["f0_hz"]) / HH1["f0_hz"] <= F0_TOL
               and abs(q - HH1["q"]) / HH1["q"] <= Q_TOL),
        "what": "Hh1 from C48/C59/R124/R127 is §10's 2.5 kHz, Q 0.97"}
    del rel

    # 5. The structural finding: exactly one per-stage component differs.
    diff = stage_differences(cfg)
    out["only-collector-load-differs"] = {
        "differing": diff["differing"], "ok": diff["differing"] == ["load_ohm"],
        "what": "the three swing-VCA stages differ in the collector load and nothing else"}

    # 6. ...and the two high bands are on one node, so there is no third drive.
    out["high-bands-share-one-node"] = {
        "feed": diff["values"]["feed"], "ok": bool(diff["high_bands_share_feed"]),
        "what": "Q16 and Q17 hang on the same IC3 output pin, so their signal drives are equal"}

    # 7. The headline, under BOTH conventions, against #396's own gap.
    term = vca_term_db(cfg)
    gap = targets["gap_db"]["short"]
    worst_term = max(term["chain_db"]["short"], term["bound_db"]["short"])
    margin = gap - worst_term
    out["vca-term-far-below-the-gap"] = {
        "chain_db": term["chain_db"]["short"], "bound_db": term["bound_db"]["short"],
        "gap_db": gap, "margin_db": round(margin, 3), "bound_margin_db": GAP_MARGIN_DB,
        "ok": margin >= GAP_MARGIN_DB,
        "what": "the short band's whole VCA term is >= 20 dB below the balance's gap"}

    # 8. The same statement with no gain model in it at all: the resistor the
    #    schematic would have to print, against the one it does print.
    need = required_collector_load(cfg, targets)
    out["required-load-not-printed"] = {
        "printed_ohm": need["short"]["printed_ohm"],
        "required_ohm": round(need["short"]["required_ohm"], 1),
        "shortfall_db": need["short"]["shortfall_db"],
        "bound_db": LOAD_MARGIN_DB,
        "ok": need["short"]["shortfall_db"] >= LOAD_MARGIN_DB,
        "what": "closing the short band's gap in the VCA needs a collector load >= 20 dB above the printed one"}

    # 9. The pin is the same scan `tone_stage_schematic` read VR4 off.
    try:
        import tone_stage_schematic as ts
        same_pin = (ts.SN_PDF_SHA256 == SN_PDF_SHA256 and ts.SN_PDF_PAGE == SN_PDF_PAGE)
    except Exception:                                    # pragma: no cover
        same_pin = False
    out["same-pinned-scan-as-vr4"] = {
        "ok": bool(same_pin),
        "what": "this read and the VR4 read are pinned to the same printing and page"}

    return out


def check(cfg_defect=None, *, targets=None) -> tuple[bool, list[str]]:
    """Run every property, then every injected defect. Green needs both."""
    lines, ok = [], True
    targets = targets if targets is not None else balance_targets()

    base = properties(config(), targets)
    for name, p in base.items():
        lines.append(f"  {'PASS' if p['ok'] else 'FAIL'}  {name}: {p['what']}")
        ok = ok and bool(p["ok"])

    lines.append("  -- injected defects, each must turn at least one property red --")
    for d in DEFECTS:
        try:
            got = properties(config(d), targets)
            red = sorted(n for n, p in got.items() if not p["ok"])
        except Refused as exc:                            # pragma: no cover
            red = [f"REFUSED: {exc}"]
        lines.append(f"  {'PASS' if red else 'FAIL'}  {d}: turns red {red or 'NOTHING'}")
        ok = ok and bool(red)

    lines.append("  -- blindness, asserted and verified blind --")
    for d, blind in BLIND.items():
        got = properties(config(d), targets)
        moved = [n for n in blind if not got[n]["ok"]]
        lines.append(f"  {'PASS' if not moved else 'FAIL'}  {d} leaves {sorted(blind)} untouched"
                     + (f" -- but moved {moved}" if moved else ""))
        ok = ok and not moved

    return ok, lines


# Properties that MUST NOT move under a given defect. A property that moves
# under everything is not measuring anything in particular.
BLIND = {
    # f0 and Q are set by the bridged-T alone; the input network cannot reach
    # them. This is what makes the peak-gain check the only test of the input
    # read -- and therefore the only external check on the part of the read no
    # other document carries.
    "DROP_INPUT_NETWORK": ("bp-f0-matches-reference", "bp-q-matches-reference"),
    # A wiring change in the VCA section cannot move a filter.
    "SPLIT_HIGH_FEED": ("bp-peak-matches-figure4", "bp-f0-matches-reference",
                        "hh1-matches-reference"),
    # ...nor can an emitter resistor.
    "UNEQUAL_EMITTER": ("bp-peak-matches-figure4", "vca-term-far-below-the-gap",
                        "required-load-not-printed"),
    # Swapping the band-pass capacitors cannot move the VCA stages.
    "SWAP_BP_CAPS": ("only-collector-load-differs", "high-bands-share-one-node",
                     "vca-term-far-below-the-gap"),
}


def record(cfg=None) -> dict:
    cfg = cfg if cfg is not None else config()
    targets = balance_targets()
    ok, lines = check(targets=targets)
    peaks = {w: dict(zip(("f_hz", "gain_db"), bandpass_peak(w, cfg))) for w in ("low", "high")}
    commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT,
                            capture_output=True, text=True).stdout.strip() or None
    dirty = subprocess.run(["git", "diff", "--quiet", "HEAD", "--", "model", "tools"],
                           cwd=ROOT, capture_output=True).returncode != 0
    return {
        "commit": commit,
        "sources_dirty": bool(dirty),
        "tool": "tools/cymbal_vca_drive.py",
        "source": {"url": SN_PDF_URL, "url_node": SN_PDF_URL_NODE,
                   "sha256": SN_PDF_SHA256, "page": SN_PDF_PAGE,
                   "crops": SN_CROPS},
        "stages": {b: {k: v for k, v in cfg["stages"][b].items()} for b in BANDS},
        "bandpass": cfg["bandpass"],
        "summing_bus": {"per_square_ohm": SUM_RES_OHM, "designators": list(SUM_RES),
                        "shunt_ohm": SUM_SHUNT_OHM, "shunt": SUM_SHUNT,
                        "note": "both filters tap this one node"},
        "hh1": cfg["hh1"],
        "bandpass_peak": peaks,
        "stage_differences": stage_differences(cfg),
        "vca_term_db": vca_term_db(cfg),
        "balance": targets,
        "required_collector_load": required_collector_load(cfg, targets),
        "properties": properties(cfg, targets),
        "defects": list(DEFECTS),
        "gate": {"ok": ok, "lines": lines},
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--report", action="store_true")
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--json", type=pathlib.Path, default=None)
    ap.add_argument("--verify-source", type=pathlib.Path, default=None,
                    metavar="SN.PDF",
                    help="assert the file is the pinned scan and re-render the crops")
    ap.add_argument("--require-source", type=pathlib.Path, default=None,
                    metavar="SN.PDF",
                    help="make the scan check BINDING: refuse (exit 1) rather than "
                         "report if it is absent or does not match")
    args = ap.parse_args(argv)
    if not (args.report or args.check or args.json or args.verify_source
            or args.require_source):
        args.report = True

    if args.verify_source is not None:
        try:
            info = verify_source(args.verify_source)
        except SourceUnavailable as exc:
            print(f"REFUSED: {exc}")
            return 3
        print(f"verified {info['path']}")
        print(f"  sha256 {info['sha256']} (matches SN_PDF_SHA256)")
        for p in info["crops"]:
            print(f"  wrote {p}")
        if not (args.report or args.check or args.json):
            return 0

    if args.require_source is not None:
        try:
            verify_source(args.require_source)
        except SourceUnavailable as exc:
            print(f"REFUSED (--require-source): {exc}")
            return 1

    try:
        targets = balance_targets()
    except Refused as exc:
        print(f"REFUSED: {exc}")
        return 1

    if args.report:
        cfg = config()
        diff = stage_differences(cfg)
        print("The three swing VCAs, SN p.13:")
        for b in BANDS:
            st = cfg["stages"][b]
            print(f"  {b:6s} {st['q']}  feed {st['feed']:11s} couple {st['couple']} "
                  f"{st['couple_f']*1e6:.3f}u  bias {st['bias_ohm']/1e6:.1f}M  "
                  f"emitter {st['emitter']} {st['emitter_ohm']:.0f}  "
                  f"load {st['load']} {st['load_ohm']/1e3:.0f}k")
        print(f"  identical across all three: {', '.join(diff['identical'])}")
        print(f"  differing:                  {', '.join(diff['differing'])}")
        term = vca_term_db(cfg)
        print("\nThe VCA section's own inter-band term, re low:")
        print(f"  collector-load ratio  short {term['chain_db']['short']:+.2f} dB   "
              f"decay {term['chain_db']['decay']:+.2f} dB")
        print(f"  upper bound (Hh1 load) short {term['bound_db']['short']:+.2f} dB   "
              f"decay {term['bound_db']['decay']:+.2f} dB")
        need = required_collector_load(cfg, targets)
        print("\nAgainst #396's gap:")
        for b in ("decay", "short"):
            n = need[b]
            print(f"  {b:6s} gap {targets['gap_db'][b]:+6.2f} dB -> needs "
                  f"{n['required_ohm']/1e3:9.1f}k, printed {n['printed_ohm']/1e3:5.0f}k "
                  f"({n['shortfall_db']:+.2f} dB short)")
        print("\nBand-pass peaks vs W14b Fig. 4:")
        for w in ("low", "high"):
            f_hz, db = bandpass_peak(w, cfg)
            print(f"  {w:5s} {f_hz:7.0f} Hz  {db:+.2f} dB  "
                  f"(figure 4: {cc.BP_PEAK_DB[w]:+.2f})")

    rc = 0
    if args.check:
        ok, lines = check(targets=targets)
        print("\n".join(lines))
        print("GATE: " + ("green" if ok else "RED"))
        rc = 0 if ok else 1

    if args.json is not None:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(record(), indent=2, sort_keys=True) + "\n")
        print(f"wrote {args.json}")

    return rc


if __name__ == "__main__":
    raise SystemExit(main())
