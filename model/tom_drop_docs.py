#!/usr/bin/env python3
"""Bind the tom pitch drop's prose to the law that ships, and re-examine the
congas against the corpus.

    python model/tom_drop_docs.py            # both checks, as a table
    python model/tom_drop_docs.py --json o.json

WHY THIS EXISTS (issue #95). `docs/tr808-reference.md` tagged the toms' pitch
drop *[verified: SN text; magnitude inferred]* -- the mechanism read out of the
service notes, the **magnitude** computed by us from the diode branch's
resistance limit. `docs/drum-verification.md` 8.4 then recorded the rev-6 kit
as taking `x1.7, accent-scaled, 60 ms` with the authority `verified in a
source, 4`, and the qualifier was gone. A number marked as an inference was
cited as a measurement for three contract revisions, and shipped a sweep the
machine misses by 11x at an unaccented hit.

#110 measured it and #154 shipped the correction; nothing noticed that the two
reference documents still carried x1.7, because **nothing was checking.** That
is `docs/failure-modes.md` mechanism 4 -- claims outliving their evidence --
and this module is the loop that can notice. The numbers printed in the prose
are re-derived here from `model/drums_fx.py` and from #110's own result table,
and the sentences carry `<!-- claim: ... -->` markers pointing at the tests
that call it (`docs/claim-markers.md`).

TWO CHECKS, AND BOTH REFUSE RATHER THAN GUESS.

`stated_law(text)` parses 4's amendment out of the reference document. If the
table is not there it raises `Refused`: a document that no longer states the
law is not a document that agrees with it, and reporting OK for an absent
claim is the failure this repository keeps re-finding.

`conga_cell_errors()` answers #95's last question -- whether the congas, which
share the circuit at about half the size, carry the same clamp and
missing-tuning defects the toms did. It compares what `drums_fx` ships against
#110's per-file rows, at each row's own pot position.

THE COMPARISON IS CENTRE TO CENTRE, AND GETTING THAT WRONG IS EASY.

`u` is where the TUNING pot sits, normalised to each position's own centre:
the machine's own median settled f0 on the measuring side, `TOM_PRESET`'s
chart f0 on the shipping side (`model/tom_drop_fit.py` states this and gives
the reason). The first pass of this comparison fed the machine's settled f0
straight into the shipped law and reported LC at *Accent* **2.1x too large**.
It is not: this unit's LC pot centre sits 11 % above the chart, so that asks
the law for a hit with the tuning wound up, and it correctly answers with a
bigger drop. Centre to centre the same cell is off by 0.0006. The wrong answer
looked exactly like a finding, which is why the convention is restated at the
point of use here and not only in the fit.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys

import numpy as np

HERE = pathlib.Path(__file__).resolve().parent
REPO = HERE.parent
sys.path.insert(0, str(HERE))

import drums_fx as dx                                          # noqa: E402
import tom_drop_fit as F                                       # noqa: E402

REFERENCE = REPO / "docs" / "tr808-reference.md"
VERIFICATION = REPO / "docs" / "drum-verification.md"
RESULTS = REPO / "docs" / "tom-pitch-drop-results.json"

CONGAS = ("LC", "MC", "HC")
# Both halves of one circuit: the mode is the circuit's, the POSITION is read
# off the tuning (`drums_fx.tom_position`), which is what selects A0 and G.
MODE_OF = {"LT": dx.M_LT, "MT": dx.M_MT, "HT": dx.M_HT,
           "LC": dx.M_LT, "MC": dx.M_MT, "HC": dx.M_HT}

# A cell needs this many usable files before its median means anything. Same
# threshold the fit uses to decide a cell can carry an intercept.
MIN_CELL = F.MIN_CELL

# Bounds on the conga residual, chosen to sit between the clean worst and the
# nearest injected control rather than picked for roundness -- measured
# 2026-09-26 on this corpus:
#
#   clean            median 0.0055   max|.| 0.0226
#   clamp restored   median 0.0565   max|.| 0.0914     (fires on the median)
#   conga given the  median 0.0055   max|.| 0.0640     (fires on the max: at
#   tom's G                                             the pot CENTRE the two
#                                                       slopes agree exactly,
#                                                       so only off-centre
#                                                       rows can see it)
#
# The two bounds catch different defects and neither alone catches both. An
# accent fault moves the whole cell, so it shows in the median; a tuning fault
# is zero at the centre by construction, so it shows only at the pot's ends.
CONGA_MEDIAN_MAX = 0.010
CONGA_WORST_MAX = 0.030


class Refused(Exception):
    """A precondition is not met; nothing was attempted."""


# ------------------------------------------------------------ the prose ----

_ACCENT_ROW = re.compile(
    r"^>?\s*\|\s*(no accent|more accent|accent)\s*\|\s*(\d+)\s*\|\s*\*\*[x×]([\d.]+)\*\*",
    re.MULTILINE)
_EXCESS = re.compile(r"excess\s*=\s*([\d.]+)\s*[·*]")
_A0_ROW = re.compile(r"\|\s*accent threshold\s*`A0`\s*\|\s*\*\*([\d.]+)\*\*\s*\|\s*\*\*([\d.]+)\*\*")
_G_ROW = re.compile(r"\|\s*tuning slope\s*`G`\s*\|\s*\*\*([\d.]+)\*\*\s*\|\s*\*\*([\d.]+)\*\*")
# drum-verification section 12's before/after table
_SHIPPED_ROW = re.compile(
    r"magnitude at accent 1\.0, pot centred\s*\|[^|]*\|\s*\*\*[x×]([\d.]+)\*\*")

LEVEL_OF = {"no accent": "A", "accent": "B", "more accent": "C"}


def stated_law(text: str) -> dict:
    """The pitch-drop numbers a document states, parsed back out of its prose.

    REFUSES rather than returning a partial answer: a reference document that
    has lost the amendment table cannot be checked against the corpus, and
    saying so is the only honest verdict available."""
    levels = {}
    for name, n, ratio in _ACCENT_ROW.findall(text):
        levels[LEVEL_OF[name]] = {"n": int(n), "ratio": float(ratio)}
    if set(levels) != set("ABC"):
        raise Refused(
            "the measured-magnitude table is not in this document "
            f"(found levels {sorted(levels) or 'none'}, need A, B and C): "
            "nothing here states what the drop is, so nothing can agree with it")
    m = _EXCESS.search(text)
    if not m:
        raise Refused("no `excess = <ratio> * ...` law in this document")
    a0 = _A0_ROW.search(text)
    g = _G_ROW.search(text)
    if not (a0 and g):
        raise Refused("the A0 / G constants table is not in this document")
    return {
        "levels": levels,
        "excess_at_reference": float(m.group(1)),
        "A0": {"tom": float(a0.group(1)), "conga": float(a0.group(2))},
        "G": {"tom": float(g.group(1)), "conga": float(g.group(2))},
    }


def stated_shipped_ratio(text: str) -> float:
    """The corrected magnitude `drum-verification.md` section 12 records."""
    m = _SHIPPED_ROW.search(text)
    if not m:
        raise Refused("section 12's before/after table is not in this document")
    return float(m.group(1))


def measured_levels(path: pathlib.Path = RESULTS) -> dict:
    """#110's pooled onset ratio per accent level -- the ground truth the
    prose has to agree with."""
    if not path.exists():
        raise Refused(f"{path} is missing: there is no measurement to check against")
    pooled = json.loads(path.read_text(encoding="utf-8"))["pooled"]
    return {k: {"n": v["n"], "ratio": v["median"]} for k, v in pooled.items()}


def prose_disagreements(reference: str, verification: str, places: int = 3) -> list[str]:
    """Every way the two documents' stated numbers differ from the tree.

    Empty means the prose and the code say the same thing. Rounding to
    `places` is allowed because the documents are written for a reader;
    drifting is not."""
    law = stated_law(reference)
    meas = measured_levels()
    bad = []
    for lvl in "ABC":
        want, got = meas[lvl], law["levels"][lvl]
        if round(want["ratio"], places) != round(got["ratio"], places):
            bad.append(f"accent {lvl}: document says x{got['ratio']}, "
                       f"#110 measured x{want['ratio']:.4f}")
        if want["n"] != got["n"]:
            bad.append(f"accent {lvl}: document says n={got['n']}, #110 used n={want['n']}")
    shipped = dx.TOM_DROP_RATIO - 1.0
    if abs(law["excess_at_reference"] - shipped) > 5e-4:
        bad.append(f"the stated law's magnitude {law['excess_at_reference']} is not "
                   f"drums_fx.TOM_DROP_RATIO - 1 = {shipped:.4f}")
    for key, name, shipped_value in (
            ("A0", "tom", dx.TOM_DROP_ACCENT_0),
            ("A0", "conga", dx.TOM_DROP_ACCENT_0_CONGA),
            ("G", "tom", dx.TOM_DROP_TUNING_G),
            ("G", "conga", dx.TOM_DROP_TUNING_G_CONGA)):
        stated = law[key][name]
        if abs(stated - shipped_value) > 5e-3:
            bad.append(f"{key} ({name}): document says {stated}, drums_fx ships {shipped_value}")
    v_shipped = stated_shipped_ratio(verification)
    if abs(v_shipped - dx.TOM_DROP_RATIO) > 5e-4:
        bad.append(f"drum-verification says the shipped magnitude is x{v_shipped}, "
                   f"drums_fx ships x{dx.TOM_DROP_RATIO}")
    return bad


# ------------------------------------------------------------ the congas ----

def _rows():
    rows, _ = F.load_rows()
    return F.with_u(rows)


def conga_cell_errors(excess=None, voices=CONGAS) -> dict:
    """Shipped minus measured excess, per (voice, accent) cell, per file.

    `excess(mode, f0_hz, accent)` defaults to what `drums_fx` ships; the
    controls pass a deliberately broken one. Each measured row is placed on
    the MODEL's pot -- `TOM_PRESET`'s chart f0 scaled by that row's own `u` --
    so the two sides are compared centre to centre. See this module's header
    for what happens when they are not."""
    fn = excess or dx.tom_drop_excess
    out = {}
    for r in _rows():
        if r["voice"] not in voices:
            continue
        key = f"{r['voice']} {r['accent']}"
        f0 = dx.TOM_PRESET[r["voice"]][0] * (1.0 + r["u"])
        out.setdefault(key, []).append(
            fn(MODE_OF[r["voice"]], f0, F.ACCENT_MAP[r["accent"]]) - r["excess"])
    cells = {k: {"n": len(v), "median": float(np.median(v)),
                 "worst": float(np.max(np.abs(v)))}
             for k, v in out.items() if len(v) >= MIN_CELL}
    if not cells:
        raise Refused(f"no cell of {voices} has {MIN_CELL} usable files")
    return cells


def conga_verdict(excess=None) -> dict:
    """PASS/FAIL on the two bounds, with the numbers that decided it."""
    cells = conga_cell_errors(excess)
    median = max(abs(c["median"]) for c in cells.values())
    worst = max(c["worst"] for c in cells.values())
    fails = [k for k, c in cells.items()
             if abs(c["median"]) > CONGA_MEDIAN_MAX or c["worst"] > CONGA_WORST_MAX]
    return {"cells": cells, "max_abs_median": median, "worst": worst,
            "failing_cells": sorted(fails),
            "verdict": "PASS" if not fails else "FAIL"}


# ---------------------------------------------------------------- report ----

def run() -> dict:
    ref = REFERENCE.read_text(encoding="utf-8")
    ver = VERIFICATION.read_text(encoding="utf-8")
    return {"prose_disagreements": prose_disagreements(ref, ver),
            "stated_law": stated_law(ref),
            "congas": conga_verdict()}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--json", type=pathlib.Path)
    a = ap.parse_args(argv)
    try:
        r = run()
    except Refused as e:
        print(f"REFUSED: {e}")
        return 2
    print("== the prose against the tree ==")
    if r["prose_disagreements"]:
        for d in r["prose_disagreements"]:
            print("  STALE:", d)
    else:
        print("  OK: tr808-reference.md 4 and drum-verification.md 12 state the")
        print("      magnitude, threshold and tuning slope drums_fx ships.")
    print("\n== the congas, shipped against #110's files (centre to centre) ==")
    print(f"  {'cell':8} {'n':>3} {'median':>9} {'worst':>9}")
    for k in sorted(r["congas"]["cells"]):
        c = r["congas"]["cells"][k]
        print(f"  {k:8} {c['n']:3d} {c['median']:+9.4f} {c['worst']:9.4f}")
    print(f"  bounds: |median| <= {CONGA_MEDIAN_MAX}, worst <= {CONGA_WORST_MAX}"
          f"  -> {r['congas']['verdict']}")
    if a.json:
        a.json.write_text(json.dumps(r, indent=1), encoding="utf-8")
        print(f"\nwrote {a.json}")
    return 0 if not r["prose_disagreements"] and r["congas"]["verdict"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
