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
balance needs +39.79 dB and +10.13 dB (`../balance/balance.json`). To supply
the short band's, R94 would have to be 2.15 Mohm. The schematic prints 39 k.

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

THE COLLECTOR SUPPLY (#432, step 12), AND THE HYPOTHESIS IT REFUTES. Step 11
left one per-band freedom open: the three envelope generators' peak collector
voltages. The plausible shape was "equal at the peak, different only in decay",
because all three reservoirs are charged from Q19's emitter through their own
diode. Read off the same scan and solved as a transient, that is HALF true and
the half that fails is the half that matters:

  * EQUAL AT THE RESERVOIR -- CONFIRMED. C38, C40 and C41 are all 1 uF, all
    charged from Q19's emitter follower through D6/D7/D8, and the 1 ms trigger
    is long enough that each reaches V_trig - V_BE - V_f. They agree to within
    the diodes' own forward-drop spread at their different load currents,
    which this module computes rather than assumes.
  * NOT EQUAL AT THE COLLECTOR -- REFUTED, and by a lot. Only the SHORT band's
    collector load hangs on its own reservoir (R94 straight off C38). The
    DECAY band's hangs behind R88 33 k into C39 0.47 uF and then a resistive
    divider (R90/R91/R89/R92); the LOW band's hangs behind Q20's emitter
    follower and R105 33 k into C45 2.2 uF. Both lags are 10-70 ms against a
    1 ms trigger, so neither node is anywhere near its reservoir when the
    reservoir peaks.

AND THE DECAY KNOB IS NOT WHERE THE REFERENCE PUTS IT. VR2's wiper is tied to
its pin 3 and R93 470 k is in SERIES with it, not in parallel -- so the timing
resistance is 470 k to 2.47 M, never zero, and C41's tail is 0.47-2.47 s rather
than the reference's "up to 0.38 s". That reservoir feeds the LOW band's
collector supply at full weight through Q20 and R105, and reaches the DECAY
band only through R92/R89/R91. Which is exactly what the Fischer recordings
already said and the reference's own S10 denies: "the low band's own decay
tracks DECAY ... the recordings contradict S10" (`../docs/scorecard/
cymbal-369/README.md`). The span is the external known answer below.

WHAT THIS MODULE DOES NOT SETTLE, stated rather than left to be assumed:

  * the swing VCA's LOWER edge. Werner's "a lower edge that is itself a
    function of the envelope" needs the transistor's large-signal model and
    the base drive amplitude; neither is read here. This module settles the
    UPPER edge -- the ceiling the collector node reaches when the stage cuts
    off -- which is the level a band can never exceed, not the level it
    reaches.
  * the conduction duty of the three swing VCAs, which loads the envelope
    nodes. It is bracketed (0.25 to 1.0) rather than chosen, and every
    envelope number is reported across that bracket.
  * the high-pass input loading on the two HIGH bands. Hh1's input network is
    fully read here (C48/C59/R124/R127, a unity-gain Sallen-Key whose f0 and Q
    reproduce §10's 2.5 kHz / 0.97), so the low band's loaded collector
    impedance is computed exactly; Hh2's and Hh3's are not. Because a passive
    load can only REDUCE |Z|, leaving the high bands unloaded makes the
    reported ratio an UPPER BOUND, which is the direction that matters: the
    bound is +8.71 dB (short) and +7.26 dB (decay), still 31.1 dB short of the
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
    # #432: the attack buffer Q19 (R84 10 k base, R85 100 collector, R86 100 /
    # C35 47 u supply decoupling) and the D6/D7/D8 common-anode bus off its
    # EMITTER -- the arrow points away from the base, so Q19 is an NPN
    # follower and the three reservoirs are charged, not discharged, through
    # it. Read at 1800 dpi before the direction was written down.
    "env-q19": {"dpi": 600, "x": 3550, "y": 4150, "w": 950, "h": 950},
    # #432: Q20 (the second follower), R91/R92/R89, R93 470 k IN SERIES with
    # VR2 2 M(B) whose wiper is tied to its own pin 3, C41 1 uF, and the low
    # band's R105 33 k / C45 2.2 uF / R104 22 k chain down to D12.
    "env-q20": {"dpi": 600, "x": 2800, "y": 5150, "w": 1150, "h": 1000},
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

# ---------------------------------------------------------------------------
# #432: the three envelope generators, SN p.13, crops `env-q19` / `env-q20`.
#
# NODE NAMES, so the netlist below can be checked against the scan by anyone:
#
#   E  Q19's emitter -- the common anode of D6/D7/D8
#   A  C38 1 uF, the SHORT band's reservoir.  R94 leaves it for the VCA.
#   B  C37 2.2 uF, behind R87 22 k: a shunt lag on A, not a supply
#   S  C47 + C49, the SHORT band's VCA output node (D5's anode, Hh3's input)
#   P  C40 1 uF, the DECAY band's reservoir
#   Q  C39 0.47 uF, behind R88 33 k.  R90 leaves THIS node, not P.
#   Z  C50 + C51, the DECAY band's VCA output node (D11's anode, Hh2's input)
#   X  R89 10 k to ground, fed by R91 from Z and R92 from Q20's emitter
#   F  C41 1 uF, the LOW band's reservoir AND Q20's base
#   V  the top of VR2, reached from F through R93 -- SERIES, see below
#   G  Q20's emitter
#   W  C45 2.2 uF, behind R105 33 k.  R104 leaves THIS node.
#   Y  C48, the LOW band's VCA output node (D12's anode, Hh1's input)
#
# THE ONE READ THAT CONTRADICTS A COMMITTED DOCUMENT. `docs/tr808-reference.md`
# S10 records "DECAY VR2 2 MOhm || R93 470 kOhm x C41 1 uF: RC up to ~0.38 s".
# The scan prints them in SERIES: R93 runs from F down to VR2's pin 3, VR2's
# wiper (pin 2) is strapped to that same pin 3, and pin 1 goes to ground. So
# the timing resistance is R93 + p*2 M, i.e. 470 k to 2.47 M, and it is never
# zero. A parallel pair would be 0 to 380 k -- a DECAY knob whose minimum kills
# the low band outright, which is not a design. `VR2_PARALLEL_NOT_SERIES`
# injects the reference's reading and the span properties go red.
ENV_RES = (
    ("A", "B", 22e3, "R87"),
    ("A", "S", 39e3, "R94"),
    ("P", "Q", 33e3, "R88"),
    ("Q", "Z", 33e3, "R90"),
    ("Z", "X", 33e3, "R91"),
    ("X", "0", 10e3, "R89"),
    ("X", "G", 33e3, "R92"),
    ("F", "V", 470e3, "R93"),
    ("G", "W", 33e3, "R105"),
    ("W", "Y", 22e3, "R104"),
)
ENV_POT = {"node": "V", "ohm": 2.0e6, "designator": "VR2", "taper": "B",
           "wiper_strapped_to": "pin 3"}
ENV_CAP = {
    "A": (1.0e-6, "C38"), "B": (2.2e-6, "C37"), "P": (1.0e-6, "C40"),
    "Q": (0.47e-6, "C39"), "F": (1.0e-6, "C41"), "W": (2.2e-6, "C45"),
    # The three VCA output nodes carry only their own coupling capacitors.
    # They are four orders of magnitude smaller than the reservoirs and change
    # nothing; they are included because a node with no capacitor and no
    # resistive path to ground makes the matrix singular, and inventing one
    # would be a fitted parameter.
    "S": (0.0015e-6 + 0.0033e-6, "C47+C49"),
    "Z": (0.0015e-6 + 0.001e-6, "C50+C51"),
    "Y": (0.0015e-6, "C48"),
}
ENV_DIODE = (("A", "D6"), ("P", "D7"), ("F", "D8"))
# band -> (VCA output node, collector-load designator, the node that SUPPLIES
# that load).  Only the short band's supply is its own reservoir.
ENV_BAND = {
    "short": {"out": "S", "load": "R94", "supply": "A", "reservoir": "A"},
    "decay": {"out": "Z", "load": "R90", "supply": "Q", "reservoir": "P"},
    "low": {"out": "Y", "load": "R104", "supply": "W", "reservoir": "F"},
}
ENV_NODES = ("A", "B", "S", "P", "Q", "Z", "X", "F", "V", "G", "W", "Y")

# Device and drive constants. Every one is either printed on the scan, quoted
# in `docs/tr808-reference.md`, or a textbook constant -- none is fitted, and
# the three that could plausibly move (duty, beta, V_f) are swept.
ENV_TRIG_MS = 1.0             # S1.1 / SN p.5 Fig. 7, p.14
ENV_TRIG_V = (4.0, 14.0)      # S1.1: accent sets the common trigger 4-14 V
ENV_V_BE = 0.6                # Q19 and Q20, silicon
ENV_V_F = 0.6                 # D6/D7/D8, 1S1588 at a few hundred uA
ENV_N_DIODE = 1.9             # 1S1588 emission coefficient, for the spread bound
ENV_V_THERMAL = 0.02585
ENV_R_DIODE_ON = 10.0
ENV_V_CLAMP = 0.7             # the swing VCA's lower edge: V_CEsat + its series diode
ENV_R_SAT = 100.0             # R95 / R98 / R101, the emitter degeneration
ENV_BETA = 200.0              # 2SC945 mid-band; swept 100-400
ENV_DUTY = (0.25, 0.5, 1.0)   # swing-VCA conduction duty: bracketed, not chosen
ENV_DUTY_NOMINAL = 1.0
ENV_VR2 = (0.0, 0.5, 1.0)     # DECAY at minimum, 12 o'clock and maximum

# THE EXTERNAL KNOWN ANSWERS FOR THE ENVELOPE, and their bound, both stated
# here before any derived number appears anywhere in this file.
#
# Three artifacts outside this module state how far the CY decay moves when
# DECAY is swept end to end:
#
#   Roland's own chart      SN p.14, 350 -> 1200 ms          span 3.43
#   Fischer, low band       Ln EDT10 400 -> 1280 ms          span 3.20
#   Fischer, high bands     late T20 250 -> 1090 ms          span 4.36
#
# (the two Fischer figures are `docs/scorecard/cymbal-369/README.md` S1, measured
# off the 808 recordings by `tools/cymbal_bands.py` in an earlier step, with no
# knowledge of this schematic read.)
#
# THE BOUND: a factor of 1.6 either way. That is not chosen to fit -- it is the
# spread of the three external sources against each OTHER (4.36 / 3.20 = 1.36)
# rounded up. A model that reproduces the machine no better than the machine's
# own three measurements agree is as much as this derivation can claim.
ENV_SPAN_EXTERNAL = {"chart": 1200.0 / 350.0, "low": 1280.0 / 400.0,
                     "decay": 1090.0 / 250.0}
ENV_SPAN_FACTOR = 1.6
# The short band's reservoir is not on VR2 at all, so its span must be 1.
ENV_BLIND_SPAN_TOL = 0.01
# How far apart the three RESERVOIR peaks may be and still count as "equal at
# the peak": the diodes' own forward-drop spread at their own load currents,
# n*Vt*ln(I_hi/I_lo), computed from the transient rather than assumed.
ENV_PEAK_EQUAL_SLACK_V = 0.02  # arithmetic slack on top of that spread
# How far apart the three COLLECTOR peaks must be for the hypothesis to count
# as refuted, at every duty in the bracket.
ENV_PEAK_UNEQUAL_DB = 3.0
# The DECAY band's ceiling sits BELOW the low band's, which is the opposite
# direction to its collector load (R90 33 k against R104 22 k, +3.5 dB in step
# 11). Required at every duty, so it is a statement about the network.
ENV_DECAY_BELOW_LOW_DB = -1.0
# The two smoothed bands do not even peak when their reservoirs do. Required
# ratio of the low band's ceiling-peak time to the short band's.
ENV_PEAK_TIME_RATIO = 20.0

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
           "HH1_WRONG_RATIO",
           # #432, the envelope side
           "VR2_PARALLEL_NOT_SERIES", "WRONG_C41", "FAST_SMOOTHING",
           "NO_SMOOTHING_CAPS", "MISMATCHED_D8", "R89_OPEN")


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
    env = {"res": [list(r) for r in ENV_RES],
           "pot": dict(ENV_POT),
           "pot_series": True,
           "cap": {k: list(v) for k, v in ENV_CAP.items()},
           "vf": {d: ENV_V_F for _, d in ENV_DIODE}}

    if defect == "VR2_PARALLEL_NOT_SERIES":
        # The reading `docs/tr808-reference.md` S10 currently carries.
        env["pot_series"] = False
    elif defect == "WRONG_C41":
        env["cap"]["F"] = [0.1e-6, "C41"]
    elif defect == "FAST_SMOOTHING":
        # Collapse the two lags that separate a reservoir from its collector
        # load, so all three bands look like the short band.
        for row in env["res"]:
            if row[3] in ("R88", "R105"):
                row[2] = 100.0
    elif defect == "NO_SMOOTHING_CAPS":
        env["cap"]["Q"] = [1e-12, "C39"]
        env["cap"]["W"] = [1e-12, "C45"]
        env["cap"]["B"] = [1e-12, "C37"]
    elif defect == "MISMATCHED_D8":
        env["vf"]["D8"] = ENV_V_F + 0.4
    elif defect == "R89_OPEN":
        for row in env["res"]:
            if row[3] == "R89":
                row[2] = 10e6

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

    return {"stages": stages, "bandpass": bp, "hh1": hh1, "env": env,
            "defect": defect}


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
# #432: the envelope section, as a piecewise-linear transient.
#
# Two formulations again, for the same reason the band-pass has two: the
# closed-form modes below are the ones a reader can check by hand, and the MNA
# transient is the one that is hard to get subtly wrong. The tests assert that
# the transient's late slope reproduces the closed-form eigenvalue of the same
# sub-network to 2 %, on a sub-network simple enough to solve on paper.
# ---------------------------------------------------------------------------


def _env_index(cfg):
    return {n: i for i, n in enumerate(ENV_NODES)}


def env_resistors(cfg, vr2_pos):
    """The resistor list with VR2 resolved at wiper position `vr2_pos`.

    The wiper is strapped to pin 3, so the two-terminal element between the
    strapped pair and pin 1 is `vr2_pos * 2 MOhm` -- the pin-3-to-wiper section
    is shorted out, not added.
    """
    env = cfg["env"]
    out = [tuple(r) for r in env["res"]]
    pot = vr2_pos * env["pot"]["ohm"]
    if env["pot_series"]:
        out.append(("V", "0", max(pot, 1.0), "VR2"))
    else:
        # The reference's reading: R93 and VR2 both straight to ground from F.
        r93 = next(r[2] for r in out if r[3] == "R93")
        out = [r for r in out if r[3] != "R93"]
        out.append(("F", "0", r93, "R93"))
        out.append(("F", "0", max(pot, 1.0), "VR2"))
        out.append(("V", "0", 1e9, "VR2-open"))   # keeps the matrix non-singular
    return out


def _env_stamp(G, b, idx, a, c, g, src=0.0):
    if a != "0":
        G[idx[a], idx[a]] += g
        b[idx[a]] += g * src
    if c != "0":
        G[idx[c], idx[c]] += g
    if a != "0" and c != "0":
        G[idx[a], idx[c]] -= g
        G[idx[c], idx[a]] -= g


def _env_base(cfg, vr2_pos, duty):
    """The time-invariant part: (G, b, C) with the swing VCAs averaged in.

    THE SWING VCA'S AVERAGE LOAD, and why it is a bracket and not a choice.
    The stage switches at the band-pass frequency (>= 3.45 kHz), which is two
    to three orders of magnitude faster than every envelope time constant
    here, so the reservoirs see only its average. A switch that clamps its
    collector to `ENV_V_CLAMP` for a fraction `duty` of each cycle and opens
    for the rest draws the same average current as a fixed resistance
    R_load*(1/duty - 1) + R_SAT from the collector node to that clamp -- that
    identity is what `duty` means here. duty is NOT known from the schematic,
    so every number below is reported across `ENV_DUTY`.
    """
    idx = _env_index(cfg)
    n = len(ENV_NODES)
    G, b = np.zeros((n, n)), np.zeros(n)
    for a, c, r, _d in env_resistors(cfg, vr2_pos):
        _env_stamp(G, b, idx, a, c, 1.0 / r)
    loads = {r[3]: r[2] for r in env_resistors(cfg, vr2_pos)}
    for band, spec in ENV_BAND.items():
        r_sw = loads[spec["load"]] * (1.0 / duty - 1.0) + ENV_R_SAT
        _env_stamp(G, b, idx, spec["out"], "0", 1.0 / r_sw, ENV_V_CLAMP)
    C = np.zeros((n, n))
    for k, (cv, _d) in cfg["env"]["cap"].items():
        C[idx[k], idx[k]] += cv
    return G, b, C, idx


def _env_steps(t_end):
    """Time grid: 5 us through the trigger, then 0.2 ms, then 1 ms."""
    out = []
    t = 0.0
    while t < t_end:
        h = 5e-6 if t < 3e-3 else (2e-4 if t < 0.2 else 1e-3)
        t += h
        out.append((t, h))
    return out


_ENV_CACHE: dict = {}


def _env_key(cfg, vr2_pos, duty, v_trig, beta, t_end):
    env = cfg["env"]
    return (json.dumps([env["res"], env["pot"], env["pot_series"],
                        env["cap"], env["vf"]], sort_keys=True),
            vr2_pos, duty, v_trig, beta, t_end)


def envelope_transient(cfg=None, vr2_pos=1.0, duty=ENV_DUTY_NOMINAL,
                       v_trig=None, beta=ENV_BETA, t_end=4.0) -> dict:
    """Backward-Euler transient of the whole CY envelope section.

    Nonlinear elements, all piecewise linear and all resolved by a fixed point
    on their own state rather than by a tolerance on the answer:

      * Q19, NPN emitter follower. Its emitter is the common anode of
        D6/D7/D8; the arrow on the scan points AWAY from the base, which is
        what makes the three reservoirs charge rather than discharge through
        it. Modelled as `max(v_trig(t) - V_BE, 0)`.
      * D6/D7/D8, each `V_f` + `R_DIODE_ON`, on only while forward current is
        positive. This is what blocks the reservoirs from discharging back
        into Q19 when the 1 ms pulse ends.
      * Q20, NPN emitter follower on C41. Its base current is taken off F as
        `I_E / beta`, which is a real discharge path on the LOW band's
        reservoir and is swept over beta 100-400 rather than assumed.
    """
    cfg = cfg if cfg is not None else config()
    v_trig = ENV_TRIG_V[1] if v_trig is None else v_trig
    key = _env_key(cfg, vr2_pos, duty, v_trig, beta, t_end)
    hit = _ENV_CACHE.get(key)
    if hit is not None:
        return hit

    G0, b0, C, idx = _env_base(cfg, vr2_pos, duty)
    vf = cfg["env"]["vf"]
    n = len(ENV_NODES)
    v = np.zeros(n)
    ts, rows = [], []
    # DC load Q20's emitter sees, used only for its base current.
    r_e20 = 23.4e3
    for t, h in _env_steps(t_end):
        e_v = max((v_trig if t <= ENV_TRIG_MS * 1e-3 else 0.0) - ENV_V_BE, 0.0)
        state = None
        nv = v
        for _ in range(12):
            G = G0 + C / h
            b = b0 + C.dot(v) / h
            on = {}
            for node, des in ENV_DIODE:
                want = True if state is None else state[node]
                if want:
                    g = 1.0 / ENV_R_DIODE_ON
                    G[idx[node], idx[node]] += g
                    b[idx[node]] += g * (e_v - vf[des])
                on[node] = want
            if v[idx["F"]] > ENV_V_BE + 0.05:
                gg = 1.0 / 20.0
                G[idx["G"], idx["G"]] += gg
                b[idx["G"]] += gg * (v[idx["F"]] - ENV_V_BE)
                G[idx["F"], idx["F"]] += 1.0 / (beta * r_e20)
            nv = np.linalg.solve(G, b)
            new = {}
            for node, des in ENV_DIODE:
                drive = e_v - vf[des]
                new[node] = ((drive - nv[idx[node]]) / ENV_R_DIODE_ON > 0.0
                             if on[node] else drive > nv[idx[node]])
            if new == on:
                break
            state = new
        v = nv
        ts.append(t)
        rows.append(v.copy())
    out = {"t": np.array(ts), "v": np.array(rows), "idx": idx,
           "vr2_pos": vr2_pos, "duty": duty, "v_trig": v_trig, "beta": beta}
    out["clip"] = _env_clip(cfg, out, vr2_pos)
    _ENV_CACHE[key] = out
    return out


def _env_clip(cfg, tr, vr2_pos):
    """Each band's UPPER clip: the collector node with the stage cut off.

    S1.3: the swing VCA "clips wildly between the envelope voltage and a lower
    edge". This is that envelope voltage, at the collector rather than at the
    reservoir -- the level the band cannot exceed.

    Short and low are trivial (no current through R94 / R104 when the stage is
    off, so the output node sits at its supply). The DECAY band is not: Z sees
    R90 back to its own supply AND R91 down into the R89/R92 network, so its
    ceiling is a divider between C39's node and Q20's emitter and has to be
    solved. That solve is done here rather than hard-coded so that `R89_OPEN`
    moves it.
    """
    idx, v = tr["idx"], tr["v"]
    res = {r[3]: r for r in env_resistors(cfg, vr2_pos)}
    g90 = 1.0 / res["R90"][2]
    g91 = 1.0 / res["R91"][2]
    g89 = 1.0 / res["R89"][2]
    g92 = 1.0 / res["R92"][2]
    m = np.array([[g90 + g91, -g91], [-g91, g91 + g89 + g92]])
    mi = np.linalg.inv(m)
    vq, vg = v[:, idx["Q"]], v[:, idx["G"]]
    vz = mi[0, 0] * g90 * vq + mi[0, 1] * g92 * vg
    return {"short": v[:, idx["A"]], "decay": vz, "low": v[:, idx["W"]]}


def env_tau_ms(t, y, lo_db=-10.0, hi_db=-30.0) -> float:
    """1/e time constant of the SWING above the clamp, from -10 to -30 dB.

    The swing, not the node: the swing VCA's lower edge is `ENV_V_CLAMP`, so a
    trace that settles there has decayed to nothing. Fitting the node voltage
    instead reports tens of seconds for a 140 ms network, which is the first
    thing this function got wrong (wrong-then-right 2).
    """
    y = np.maximum(np.asarray(y) - ENV_V_CLAMP, 1e-12)
    i0 = int(np.argmax(y))
    pk = y[i0]
    seg = (y[i0:] < pk * 10 ** (lo_db / 20.0)) & (y[i0:] > pk * 10 ** (hi_db / 20.0))
    if int(seg.sum()) < 5:
        return float("nan")
    slope = np.polyfit(t[i0:][seg], np.log(y[i0:][seg]), 1)[0]
    return float(-1.0 / slope * 1e3)


def env_short_band_modes(cfg=None, duty=ENV_DUTY_NOMINAL) -> list:
    """THE SECOND FORMULATION, on the one sub-network that stands alone.

    C38 touches exactly two resistors -- R87 up to C37 and R94 out to the
    VCA -- and nothing else in the cymbal reaches it. So the SHORT band's
    envelope is a three-node linear network whose modes can be written down:

        A: C38, R87 to B, R94 to S        B: C37       S: C47+C49, R_sw to gnd

    `eig(-C^-1 G)` on those three nodes gives its time constants in closed
    form, and `test_the_transient_reproduces_the_closed_form_short_band_modes`
    requires the MNA transient's deep tail to reproduce the slowest one. Two
    formulations where one would do, for the same reason the band-pass carries
    two: the hand-checkable one and the one that is hard to get subtly wrong.
    """
    cfg = cfg if cfg is not None else config()
    res = {r[3]: r[2] for r in env_resistors(cfg, 1.0)}
    caps = cfg["env"]["cap"]
    r_sw = res["R94"] * (1.0 / duty - 1.0) + ENV_R_SAT
    g87, g94, gsw = 1.0 / res["R87"], 1.0 / res["R94"], 1.0 / r_sw
    g = np.array([[g87 + g94, -g87, -g94],
                  [-g87, g87, 0.0],
                  [-g94, 0.0, g94 + gsw]])
    c = np.diag([caps["A"][0], caps["B"][0], caps["S"][0]])
    ev = np.linalg.eigvals(-np.linalg.solve(c, g))
    return sorted(float(-1.0 / e.real * 1e3) for e in ev if e.real < 0)


def envelope_peaks(cfg=None, duty=ENV_DUTY_NOMINAL, vr2_pos=1.0,
                   v_trig=None, beta=ENV_BETA) -> dict:
    """Peak reservoir and peak collector voltage per band, plus the times."""
    cfg = cfg if cfg is not None else config()
    tr = envelope_transient(cfg, vr2_pos, duty, v_trig, beta)
    idx, v = tr["idx"], tr["v"]
    out = {}
    for band, spec in ENV_BAND.items():
        res = v[:, idx[spec["reservoir"]]]
        clip = tr["clip"][band]
        i = int(np.argmax(clip))
        out[band] = {
            "reservoir_node": spec["reservoir"],
            "reservoir_peak_v": float(res.max()),
            "collector_peak_v": float(clip[i]),
            "collector_peak_ms": float(tr["t"][i] * 1e3),
            "tau_ms": env_tau_ms(tr["t"], clip),
        }
    lowpk = out["low"]["collector_peak_v"]
    for band in ENV_BAND:
        out[band]["collector_peak_db_re_low"] = round(
            20.0 * math.log10(out[band]["collector_peak_v"] / lowpk), 3)
    return out


def envelope_peak_spread(cfg=None, duty=ENV_DUTY_NOMINAL, vr2_pos=1.0,
                         v_trig=None, beta=ENV_BETA) -> dict:
    """Are the three RESERVOIRS equal at the peak, and to what bound?

    The bound is not a tolerance: it is the diodes' own forward-drop spread at
    their own load currents, n*Vt*ln(I_hi/I_lo), with the currents taken off
    the transient at the instant each reservoir peaks. D6 feeds R87 and R94;
    D7 feeds R88; D8 feeds R93+VR2 and Q20's base -- currents that differ by
    two orders of magnitude, which is the whole reason this is not zero.
    """
    cfg = cfg if cfg is not None else config()
    tr = envelope_transient(cfg, vr2_pos, duty, v_trig, beta)
    idx, v = tr["idx"], tr["v"]
    res = env_resistors(cfg, vr2_pos)
    g = {}
    for a, c, r, _d in res:
        g.setdefault(a, []).append((c, 1.0 / r))
        if c != "0":
            g.setdefault(c, []).append((a, 1.0 / r))
    i_peak, v_peak = {}, {}
    for band, spec in ENV_BAND.items():
        node = spec["reservoir"]
        k = int(np.argmax(v[:, idx[node]]))
        row = v[k]
        cur = sum(gg * (row[idx[node]] - (0.0 if other == "0" else row[idx[other]]))
                  for other, gg in g.get(node, []))
        if node == "F":                     # Q20's base current, on C41 only
            cur += max(row[idx["G"]], 0.0) / 23.4e3 / tr["beta"]
        i_peak[band] = float(max(cur, 1e-12))
        v_peak[band] = float(row[idx[node]])
    hi, lo = max(i_peak.values()), min(i_peak.values())
    bound = ENV_N_DIODE * ENV_V_THERMAL * math.log(hi / lo) + ENV_PEAK_EQUAL_SLACK_V
    spread = max(v_peak.values()) - min(v_peak.values())
    return {"reservoir_peak_v": v_peak, "load_current_a": i_peak,
            "spread_v": round(spread, 4), "bound_v": round(bound, 4),
            "ok": spread <= bound}


def envelope_spans(cfg=None, duty=ENV_DUTY_NOMINAL, beta=ENV_BETA) -> dict:
    """tau at DECAY maximum / tau at DECAY minimum, per band.

    This is the quantity the three external artifacts constrain, and it is a
    RATIO -- so C41's absolute value, the undefined meaning of the chart's
    "decay time", and the amplitude calibration of an EDT all cancel.
    """
    cfg = cfg if cfg is not None else config()
    lo = envelope_peaks(cfg, duty, ENV_VR2[0], None, beta)
    mid = envelope_peaks(cfg, duty, ENV_VR2[1], None, beta)
    hi = envelope_peaks(cfg, duty, ENV_VR2[-1], None, beta)
    out = {}
    for band in ENV_BAND:
        a, b = lo[band]["tau_ms"], hi[band]["tau_ms"]
        out[band] = {"tau_min_ms": round(a, 1), "tau_mid_ms": round(mid[band]["tau_ms"], 1),
                     "tau_max_ms": round(b, 1),
                     "span": round(b / a, 3) if a and a == a and a > 0 else float("nan")}
    # "composite" is the longer of the two bands VR2 actually reaches. The
    # SHORT band is deliberately excluded and that is not a convenience: its
    # reservoir is not on VR2, so it contributes a FIXED tail that shortens the
    # span a chart measurement can see at both ends. Leaving it out therefore
    # makes this an UPPER bound on the observable span -- the direction that
    # matters, since the observed 3.43 is below the prediction.
    knob = ("low", "decay")
    longest = {}
    for pos, rec in (("min", lo), ("mid", mid), ("max", hi)):
        longest[pos] = max(rec[b]["tau_ms"] for b in knob
                           if rec[b]["tau_ms"] == rec[b]["tau_ms"])
    out["composite"] = {"tau_min_ms": round(longest["min"], 1),
                        "tau_mid_ms": round(longest["mid"], 1),
                        "tau_max_ms": round(longest["max"], 1),
                        "span": round(longest["max"] / longest["min"], 3),
                        "bands": list(knob)}
    return out


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
    want = {"decay": 10.13, "short": 39.79}
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

    out.update(envelope_properties(cfg))
    return out


def envelope_properties(cfg=None) -> dict:
    """#432: the three envelope generators' peak collector voltages."""
    cfg = cfg if cfg is not None else config()
    out = {}

    # 10. EQUAL AT THE RESERVOIR. The half of the hypothesis that holds.
    sp = envelope_peak_spread(cfg)
    out["env-reservoirs-equal-at-the-peak"] = {
        "spread_v": sp["spread_v"], "bound_v": sp["bound_v"], "ok": bool(sp["ok"]),
        "reservoir_peak_v": {k: round(v, 4) for k, v in sp["reservoir_peak_v"].items()},
        "what": "C38/C40/C41 reach the same peak, to within the diodes' own "
                "forward-drop spread at their own load currents"}

    # 11. NOT EQUAL AT THE COLLECTOR. The half that fails, required to fail at
    #     EVERY duty in the bracket -- otherwise it is a statement about the
    #     duty rather than about the schematic.
    per_duty, worst, worst_decay = {}, None, None
    times = {}
    for duty in ENV_DUTY:
        pk = envelope_peaks(cfg, duty)
        d = pk["short"]["collector_peak_db_re_low"]
        per_duty[str(duty)] = {b: pk[b]["collector_peak_db_re_low"] for b in ENV_BAND}
        worst = d if worst is None else min(worst, d)
        dd = pk["decay"]["collector_peak_db_re_low"]
        worst_decay = dd if worst_decay is None else max(worst_decay, dd)
        if duty == ENV_DUTY_NOMINAL:
            times = {b: pk[b]["collector_peak_ms"] for b in ENV_BAND}
    out["env-collector-peaks-are-not-equal"] = {
        "min_short_re_low_db": round(worst, 3), "bound_db": ENV_PEAK_UNEQUAL_DB,
        "ok": worst >= ENV_PEAK_UNEQUAL_DB, "per_duty_db": per_duty,
        "what": "the short band's collector ceiling is >= 3 dB above the low "
                "band's at every duty in the bracket"}

    # 11b. ...and the DECAY band's ceiling is BELOW the low band's, which is the
    #      opposite direction to its collector load. Step 11's +3.52 dB and this
    #      sign do not compose into one number, and saying so is the point.
    out["env-decay-band-ceiling-below-the-low-band"] = {
        "max_decay_re_low_db": round(worst_decay, 3),
        "bound_db": ENV_DECAY_BELOW_LOW_DB,
        "ok": worst_decay <= ENV_DECAY_BELOW_LOW_DB,
        "what": "the DECAY band's ceiling is below the low band's at every duty, "
                "although its collector load is 1.5x larger"}

    # 11c. The two smoothed bands do not peak when their reservoirs do -- the
    #      lag networks are 10-70 ms against a 1 ms trigger.
    ratio = times["low"] / max(times["short"], 1e-9)
    out["env-ceilings-peak-at-different-times"] = {
        "peak_ms": {b: round(times[b], 2) for b in ENV_BAND},
        "low_over_short": round(ratio, 2), "bound": ENV_PEAK_TIME_RATIO,
        "ok": ratio >= ENV_PEAK_TIME_RATIO,
        "what": "the low band's ceiling peaks >= 20x later than the short "
                "band's, because C45 is behind R105 and C38 is behind nothing"}

    # 12. ...and the large-signal version of step 11's negative. The ceiling is
    #     an UPPER bound on what a band can deliver, so the largest admissible
    #     value is the one to argue against.
    ceiling = max(per_duty[str(d)]["short"] for d in ENV_DUTY)
    margin = targets_gap_short(cfg) - ceiling
    out["env-collector-ceiling-still-below-the-gap"] = {
        "ceiling_db": round(ceiling, 3), "margin_db": round(margin, 3),
        "bound_db": GAP_MARGIN_DB, "ok": margin >= GAP_MARGIN_DB,
        "what": "the short band's clip ceiling, at its most favourable duty, is "
                "still >= 20 dB below the balance's gap"}

    # 13/14/15. THE EXTERNAL KNOWN ANSWER: how far the DECAY knob moves each
    #     band, against Roland's chart and the Fischer recordings. Bounds in
    #     ENV_SPAN_EXTERNAL / ENV_SPAN_FACTOR, stated before any number here.
    spans = envelope_spans(cfg)
    for key, band, label in (("low", "low", "the low band"),
                             ("decay", "decay", "the DECAY band"),
                             ("chart", "composite", "the longest envelope")):
        want = ENV_SPAN_EXTERNAL[key]
        got = spans[band]["span"]
        ok = (got == got and want / ENV_SPAN_FACTOR <= got <= want * ENV_SPAN_FACTOR)
        out[f"env-decay-knob-span-{key}"] = {
            "span": got, "external_span": round(want, 3),
            "bound": [round(want / ENV_SPAN_FACTOR, 3), round(want * ENV_SPAN_FACTOR, 3)],
            "tau_ms": [spans[band]["tau_min_ms"], spans[band]["tau_mid_ms"],
                       spans[band]["tau_max_ms"]],
            "ok": bool(ok),
            "what": f"DECAY moves {label}'s decay by the factor the machine moves it"}

    # 16. ...and the band VR2 does NOT reach, asserted and verified blind.
    short_span = spans["short"]["span"]
    out["env-short-band-blind-to-the-decay-knob"] = {
        "span": short_span, "bound": ENV_BLIND_SPAN_TOL,
        "ok": bool(short_span == short_span
                   and abs(short_span - 1.0) <= ENV_BLIND_SPAN_TOL),
        "what": "the short band's reservoir is not on VR2 at all, so its decay "
                "must not move with DECAY"}

    return out


def targets_gap_short(cfg=None) -> float:
    """#396's short-band gap, re-read through the refusing accessor."""
    return float(balance_targets()["gap_db"]["short"])


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
    # Swapping the band-pass capacitors cannot move the VCA stages -- nor,
    # #432, anything in the envelope section, which shares no component with
    # either filter.
    "SWAP_BP_CAPS": ("only-collector-load-differs", "high-bands-share-one-node",
                     "vca-term-far-below-the-gap",
                     "env-reservoirs-equal-at-the-peak",
                     "env-collector-peaks-are-not-equal",
                     "env-decay-band-ceiling-below-the-low-band",
                     "env-ceilings-peak-at-different-times",
                     "env-decay-knob-span-low", "env-decay-knob-span-chart"),
    # #432. VR2 is on C41, which reaches the SHORT band through nothing at all:
    # its reservoir is C38, charged by its own diode. Reading the pot the
    # reference's way, or getting C41 wrong, must leave the short band exactly
    # where it was -- and must leave both filters alone as well.
    "VR2_PARALLEL_NOT_SERIES": ("env-short-band-blind-to-the-decay-knob",
                                "env-reservoirs-equal-at-the-peak",
                                "bp-peak-matches-figure4",
                                "only-collector-load-differs"),
    "WRONG_C41": ("env-short-band-blind-to-the-decay-knob",
                  "bp-peak-matches-figure4", "hh1-matches-reference"),
    # A change inside the envelope section cannot move a filter.
    "FAST_SMOOTHING": ("bp-peak-matches-figure4", "bp-f0-matches-reference",
                       "hh1-matches-reference", "only-collector-load-differs"),
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
        "envelope": envelope_record(cfg),
        "gate": {"ok": ok, "lines": lines},
    }


def envelope_record(cfg=None) -> dict:
    """#432's own machine-readable half: the collector-supply derivation."""
    cfg = cfg if cfg is not None else config()
    peaks = {str(d): {p: envelope_peaks(cfg, d, p) for p in ENV_VR2}
             for d in ENV_DUTY}
    accent = {str(v): envelope_peaks(cfg, ENV_DUTY_NOMINAL, 1.0, v)
              for v in ENV_TRIG_V}
    beta_sweep = {str(b): envelope_spans(cfg, ENV_DUTY_NOMINAL, b)
                  for b in (100.0, 200.0, 400.0)}
    return {
        "netlist": {
            "resistors": [list(r) for r in cfg["env"]["res"]],
            "pot": dict(cfg["env"]["pot"], series_with="R93",
                        in_series=cfg["env"]["pot_series"]),
            "capacitors": {k: list(v) for k, v in cfg["env"]["cap"].items()},
            "diodes": [list(d) for d in ENV_DIODE],
            "bands": ENV_BAND,
        },
        "drive": {"trigger_ms": ENV_TRIG_MS, "trigger_v": list(ENV_TRIG_V),
                  "v_be": ENV_V_BE, "v_f": cfg["env"]["vf"],
                  "v_clamp": ENV_V_CLAMP, "r_sat": ENV_R_SAT,
                  "beta": ENV_BETA, "duty_bracket": list(ENV_DUTY)},
        "peaks_by_duty_and_vr2": peaks,
        "peaks_by_accent": accent,
        "reservoir_spread": envelope_peak_spread(cfg),
        "spans": envelope_spans(cfg),
        "spans_by_beta": beta_sweep,
        "external_spans": ENV_SPAN_EXTERNAL,
        "span_bound_factor": ENV_SPAN_FACTOR,
        "properties": envelope_properties(cfg),
    }


def supply_record(cfg=None) -> dict:
    """The step-12 artifact: `../vca-supply/vca-supply.json`."""
    cfg = cfg if cfg is not None else config()
    commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT,
                            capture_output=True, text=True).stdout.strip() or None
    dirty = subprocess.run(["git", "diff", "--quiet", "HEAD", "--", "model", "tools"],
                           cwd=ROOT, capture_output=True).returncode != 0
    env = envelope_record(cfg)
    ok = all(p["ok"] for p in env["properties"].values())
    return {
        "commit": commit,
        "sources_dirty": bool(dirty),
        "tool": "tools/cymbal_vca_drive.py",
        "question": "the three envelope generators' peak collector voltages "
                    "(Q16/Q17/Q18), SN p.13",
        "source": {"url": SN_PDF_URL, "url_node": SN_PDF_URL_NODE,
                   "sha256": SN_PDF_SHA256, "page": SN_PDF_PAGE,
                   "crops": {k: SN_CROPS[k] for k in ("env-q19", "env-q20")}},
        "reference_correction": {
            "document": "docs/tr808-reference.md S10",
            "was": "DECAY VR2 2 MOhm || R93 470 kOhm x C41 1 uF: RC up to ~0.38 s",
            "is": "R93 470 kOhm in SERIES with VR2 2 M(B) (wiper strapped to "
                  "pin 3), x C41 1 uF: RC 0.47 s to 2.47 s",
            "how_it_is_gated": "VR2_PARALLEL_NOT_SERIES turns the three span "
                               "properties red",
        },
        "envelope": env,
        "defects": [d for d in DEFECTS if d in (
            "VR2_PARALLEL_NOT_SERIES", "WRONG_C41", "FAST_SMOOTHING",
            "NO_SMOOTHING_CAPS", "MISMATCHED_D8", "R89_OPEN")],
        "gate": {"ok": bool(ok)},
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--report", action="store_true")
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--json", type=pathlib.Path, default=None)
    ap.add_argument("--json-supply", type=pathlib.Path, default=None,
                    metavar="PATH",
                    help="#432's own record: the collector-supply derivation")
    ap.add_argument("--verify-source", type=pathlib.Path, default=None,
                    metavar="SN.PDF",
                    help="assert the file is the pinned scan and re-render the crops")
    ap.add_argument("--require-source", type=pathlib.Path, default=None,
                    metavar="SN.PDF",
                    help="make the scan check BINDING: refuse (exit 1) rather than "
                         "report if it is absent or does not match")
    args = ap.parse_args(argv)
    if not (args.report or args.check or args.json or args.json_supply
            or args.verify_source or args.require_source):
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
        if not (args.report or args.check or args.json or args.json_supply):
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

        print("\n#432 -- the three envelope generators, DECAY at maximum:")
        sp = envelope_peak_spread(cfg)
        print(f"  reservoirs (C38/C40/C41): "
              + "  ".join(f"{b} {sp['reservoir_peak_v'][b]:.3f} V" for b in BANDS)
              + f"   spread {sp['spread_v']:.3f} V, bound {sp['bound_v']:.3f} V "
                f"({'EQUAL' if sp['ok'] else 'UNEQUAL'})")
        for duty in ENV_DUTY:
            pk = envelope_peaks(cfg, duty)
            print(f"  duty {duty:4.2f}  collector ceiling  " + "  ".join(
                f"{b} {pk[b]['collector_peak_v']:6.3f} V "
                f"({pk[b]['collector_peak_db_re_low']:+.2f} dB)" for b in BANDS))
        spans = envelope_spans(cfg)
        print("\n  DECAY knob span (tau at max / tau at min), vs the machine:")
        for key, band in (("low", "low"), ("decay", "decay"), ("chart", "composite")):
            s = spans[band]
            print(f"    {band:9s} {s['tau_min_ms']:7.1f} -> {s['tau_mid_ms']:7.1f} "
                  f"-> {s['tau_max_ms']:7.1f} ms   span {s['span']:.2f}  "
                  f"(external {ENV_SPAN_EXTERNAL[key]:.2f})")
        print(f"    short     {spans['short']['tau_min_ms']:7.1f} -> "
              f"{spans['short']['tau_max_ms']:7.1f} ms   span "
              f"{spans['short']['span']:.3f}  (asserted blind to DECAY)")

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

    if args.json_supply is not None:
        args.json_supply.parent.mkdir(parents=True, exist_ok=True)
        args.json_supply.write_text(
            json.dumps(supply_record(), indent=2, sort_keys=True) + "\n")
        print(f"wrote {args.json_supply}")

    return rc


if __name__ == "__main__":
    raise SystemExit(main())
