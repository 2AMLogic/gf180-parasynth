#!/usr/bin/env python3
"""Answer design-versus-router for the residual route DRC sites from the routed DEF (#1006).

``classify_route_drc.py`` classifies by *nets*: the 24 ``Metal2``/``VSS``
Metal Spacing sites are ``unclassified`` because each mixes a signal with a
power net, and the routed DEF needed to go one level further -- is the ``VSS``
geometry at the site the power block's M1->M4 via-column land, or something
the router itself put there? -- was not collected, so the question stayed
open (``docs/tool-findings.md``, #808). This tool reads that DEF and records,
per site:

* every ``VSS`` ``Metal2`` shape inside a window around the violation bbox,
  tagged by origin: a via pad (from a via placement whose definition carries
  ``Metal2`` rectangles), a special-net wire segment or pad, or an unresolved
  via (its definition is not in the DEF, so its pads cannot be derived --
  counted, never silently dropped);
* every signal-net ``Metal2`` shape in the same window;
* whether any signal shape *overlaps* a ``VSS`` shape (the router placed metal
  inside power geometry) or only comes near it at spacing distance.

The output is evidence, not a verdict: the per-site records name what
geometry bounds each violation so ``docs/tool-findings.md`` can conclude
design-versus-router on recorded facts -- this tool classifies geometry, it
does not decide whose fault it is, mirroring ``classify_route_drc.py``'s
contract.

DEF parsing here covers what OpenROAD writes for this flow: ``VIAS``/``+VIAS``
definitions, ``SPECIALNETS`` and ``NETS`` routing geometry (wire segments from
point pairs, ``*`` repeated coordinates, the path-point extension form
``( x y ext )`` (its extension is recorded per segment end,
``ext_start``/``ext_end``, applied to the wire rect along the path axis and
counted in the record, never used as a width), via placements at points,
``RECT`` pads relative to the last point), plus the
``UNITS DISTANCE MICRONS`` scale. The numeric arguments of the skip-keywords
``STYLE``/``MASK``/``WEIGHT`` are consumed with their keyword, never read as
a width (#1021).
Tokens that do not fit the grammar are counted per section and reported in
the output, never dropped.

Exit: ``0`` analysed; ``3`` REFUSED, an input is missing or unusable (the DEF
has no parsable net sections, the DRC report has no parsable records while
the committed record says 24, the par response names no power nets), or
``--output`` names a directory or its parent cannot be made -- a half-answer
is not published. ``1`` for usage.

Usage
-----
::

    python3 pnr/klt/ladder_dp/analyze_def_sites.py \
        --def <ladder_dp.def> --drc-report <ladder_dp_route_drc.rpt> \
        --par-response <par_response.json> \
        --output pnr/klt/ladder_dp/def_site_analysis.json
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from classify_route_drc import parse_drc_report, sha256_file  # noqa: E402

EXIT_OK = 0
EXIT_USAGE = 1
EXIT_REFUSED = 3

SCHEMA_VERSION = 1

#: How far outside the violation bbox geometry is still collected as evidence.
DEFAULT_WINDOW_UM = 1.0

_UNITS = re.compile(r"(?m)^\s*UNITS\s+DISTANCE\s+MICRONS\s+(\d+)\s*;")

_SECTION = {
    "vias": re.compile(r"(?ms)^\s*(?:\+)?VIAS\s+\d+\s*;(.*?)^\s*END\s+VIAS", re.M),
    "specialnets": re.compile(r"(?ms)^\s*SPECIALNETS\s+\d+\s*;(.*?)^\s*END\s+SPECIALNETS", re.M),
    "nets": re.compile(r"(?ms)^\s*NETS\s+\d+\s*;(.*?)^\s*END\s+NETS", re.M),
}

_RECT_DEF = re.compile(
    r"RECT\s+(\S+)\s*\(\s*(-?\d+)\s+(-?\d+)\s*\)\s*\(\s*(-?\d+)\s+(-?\d+)\s*\)"
)

_POINT = re.compile(r"\(\s*(-?\d+)\s+(-?\d+|\*)\s*\)")

_PATH_KEYWORDS = {"ROUTED", "FIXED", "COVER", "NEW"}

_SHAPE_TYPES = {
    "FOLLOWPIN", "STRIPE", "COREWIRE", "RING", "BLOCKRING", "PADRING",
    "RINGWIRE", "DRCFILL", "FILLWIRE", "IOWIRE", "SPECIALNET", "RESCUE", "NONE",
}

_SKIP_TOKENS = {
    "+", "SHAPE", "TAPER", "TAPERRULE", "STYLE", "MASK", "NONDEFAULT", "VIRTUAL",
    "ORIGINAL",
    # DEF net properties -- no geometry, and naming them keeps the unparsed
    # count meaning "shapes the grammar missed", not "+ USE SIGNAL noise"
    "USE", "SIGNAL", "POWER", "GROUND", "CLOCK", "SOURCE", "NETLIST", "DIST",
    "WEIGHT", "PROPERTY", "SYNTHESIZED", "NET", "SPECIAL", "INOUT", "INPUT",
    "OUTPUT", "FEEDTHRU", "TAPERRULENAME",
}

#: The ``_SKIP_TOKENS`` members whose DEF grammar argument is a single
#: number (``STYLE styleNum``, ``MASK maskNum``, ``WEIGHT weightValue``).
#: The keyword alone was being skipped and the number left in the walk,
#: where a widthless path's ``width is None`` branch silently consumed it
#: as the width (#1021, the class #1013 closed for the extension form) --
#: so the argument is consumed with its keyword. Members taking a name
#: (``TAPERRULE``, ``USE``, ``SOURCE``, ...) are NOT safe by default: a name
#: cannot be mistaken for a width, but it is counted unparsed only *before*
#: the first point. After a point ``expect_via`` is set and the via branch
#: swallows any word starting with a letter, so the name became a phantom
#: via with ``unparsed`` still 0 (#1030). Those are handled below. The
#: argument is only consumed when it *is* a bare numeric word, so a
#: malformed spec keeps today's behaviour for whatever follows.
_SKIP_TOKENS_WITH_NUMERIC_ARG = {"STYLE", "MASK", "WEIGHT"}

#: Skip-keywords taking one NAME argument (``TAPERRULE ruleName``,
#: ``NONDEFAULT``, ``ORIGINAL netName``). The grammar read here
#: is checked against the LEF/DEF 5.8 Language Reference, NETS section
#: (http://coriolis.lip6.fr/doc/lefdef/lefdefref/DEFSyntax.html):
#: ``layerName [TAPER | TAPERRULE ruleName] [STYLE styleNum]`` in
#: regularWiring and each ``NEW``; ``+ ORIGINAL netName``; and the net-level
#: ``+ PROPERTY {propName propVal} ...``, the last clause before ``;``. The
#: spec does not list ``NONDEFAULT`` (the real keyword is ``NONDEFAULTRULE``,
#: before regularWiring; #1034). ``USE``/``SOURCE`` arguments
#: are themselves ``_SKIP_TOKENS`` members, so they were never at risk. The
#: name is consumed with its keyword; a real via name that follows is a
#: separate word and still reaches the via branch.
_SKIP_TOKENS_WITH_NAME_ARG = {"TAPERRULE", "NONDEFAULT", "ORIGINAL"}


def _norm_bbox_um(bbox: str) -> tuple[float, float, float, float] | None:
    """``(292.7400, 200.3020) - (293.0200, 200.5700)`` -> floats, or None."""
    nums = re.findall(r"-?\d+\.?\d*", bbox)
    if len(nums) != 4:
        return None
    x1, y1, x2, y2 = (float(n) for n in nums)
    return (min(x1, x2), min(y1, y2), max(x1, x2), max(y1, y2))


_CUTSIZE = re.compile(r"CUTSIZE\s+(-?\d+)\s+(-?\d+)")
_VIA_LAYERS = re.compile(r"LAYERS\s+(\S+)\s+(\S+)\s+(\S+)")
_ENCLOSURE = re.compile(r"ENCLOSURE\s+(-?\d+)\s+(-?\d+)\s+(-?\d+)\s+(-?\d+)")


def parse_via_definitions(vias_body: str) -> dict[str, dict[str, list[tuple[int, int, int, int]]]]:
    """One ``VIAS`` body -> {name: {layer: [(x1, y1, x2, y2), ...]}}.

    Two DEF spellings are covered. An explicit-RECT via yields the rectangles
    it names. A VIARULE-generated via (what OpenROAD emits for this flow:
    ``+ CUTSIZE x y + LAYERS bot cut top + ENCLOSURE xB yB xT yT``) yields the
    per-layer pad derived from cut and enclosure -- the bottom layer is
    enclosed ``(xB, yB)``, the top ``(xT, yT)``, each added around the cut
    centre, so a 1x1 Via2 with cut 520 and enclosure ``20 120 120 20`` lands a
    560x760 pad on Metal2 and a 760x560 pad on Metal3. Nothing here knows
    where a via is placed until a net references it; arrays are the set of
    their per-placement pads.
    """
    vias: dict[str, dict[str, list[tuple[int, int, int, int]]]] = {}
    for entry in re.finditer(r"(?m)^\s*-\s+(\S+)(.*?);", vias_body, re.S):
        name, spec = entry.group(1), entry.group(2)
        rects: dict[str, list[tuple[int, int, int, int]]] = {}
        for rect in _RECT_DEF.finditer(spec):
            layer = rect.group(1)
            coords = tuple(int(rect.group(i)) for i in (2, 3, 4, 5))
            rects.setdefault(layer, []).append(coords)
        if not rects:
            cut = _CUTSIZE.search(spec)
            layers = _VIA_LAYERS.search(spec)
            enc = _ENCLOSURE.search(spec)
            if cut and layers and enc:
                sx, sy = int(cut.group(1)), int(cut.group(2))
                xe_b, ye_b, xe_t, ye_t = (int(enc.group(i)) for i in (1, 2, 3, 4))
                bottom, top = layers.group(1), layers.group(3)
                rects.setdefault(bottom, []).append(
                    (-(sx // 2 + xe_b), -(sy // 2 + ye_b), sx // 2 + xe_b, sy // 2 + ye_b)
                )
                rects.setdefault(top, []).append(
                    (-(sx // 2 + xe_t), -(sy // 2 + ye_t), sx // 2 + xe_t, sy // 2 + ye_t)
                )
        if rects:
            vias[name] = rects
    return vias


def _parse_net_section(
    body: str,
) -> tuple[dict[str, list[dict[str, Any]]], int, list[dict[str, Any]], tuple[int, int]]:
    """One ``NETS``/``SPECIALNETS`` body -> ({net: [shapes]}, unparsed count,
    unparsed samples, (extension points seen, non-zero ones)).

    The extension-point count is taken at tokenise time, on every
    ``( x y ext )`` token, so it is the population in the file -- independent
    of whether the point later completes a wire pair (a via-terminated stub or
    a point before ``NEW`` completes none) and never counting an interior
    point once per adjoining segment (#1022).

    Shapes carry ``kind`` (``wire``/``via``/``pad``), ``layer``, and integer
    DBU coordinates. A wire is a point pair on one path; either point of the
    pair may carry the DEF path-point extension ``( x y ext )``: recorded per
    end as ``ext_start``/``ext_end`` and applied to the wire rect (that end
    extends past its point along the path axis), never used as the width. A
    via is a via name at a point; a pad is a ``RECT`` relative to the last
    point.
    Anything else a net says is counted as unparsed for the section, never
    dropped.
    """
    nets: dict[str, list[dict[str, Any]]] = {}
    unparsed = 0
    unparsed_samples: list[dict[str, Any]] = []
    ext_points = 0
    ext_points_nonzero = 0
    for entry in re.finditer(r"(?m)^\s*-\s+([^\s(]+)([^;]*);", body):
        net = entry.group(1)
        spec = entry.group(2)
        # everything before the first path keyword marker is the instance-pin
        # list, which this tool does not need; keeping it would only flood the
        # unparsed count with pin pairs that parse fine as far as DEF is
        # concerned
        plus = spec.find("+")
        if plus > 0:
            spec = spec[plus:]
        shapes: list[dict[str, Any]] = []
        layer: str | None = None
        width: int | None = None
        expect_layer = False
        shape_tag: str | None = None
        pending_pt: tuple[int, int] | None = None
        pending_ext: int | None = None
        last_pt: tuple[int, int] | None = None
        expect_via = False

        # materialise once, walk by index: a RECT consumes its own two
        # points, and re-tokenising mid-walk is how numbers get read twice.
        # Points stay raw strings here because ``*`` resolves against the
        # walk's running previous point, not against a tokeniser-time state
        toks: list[tuple[str, Any]] = []
        for m in re.finditer(
            # the quad form -- RECT ( dx1 dy1 dx2 dy2 ) -- must be matched
            # before the two-number point form, or its numbers read as points;
            # likewise the three-number point form -- ( x y ext ) -- before
            # the two-number one
            r"\(\s*(-?\d+)\s+(-?\d+)\s+(-?\d+)\s+(-?\d+)\s*\)"
            r"|\(\s*(-?\d+|\*)\s+(-?\d+|\*)\s+(-?\d+)\s*\)"
            r"|\(\s*(-?\d+|\*)\s+(-?\d+|\*)\s*\)"
            r"|RECT\b|\S+",
            spec,
        ):
            if m.group(1) is not None:
                toks.append(("rect_quad", tuple(int(m.group(i)) for i in (1, 2, 3, 4))))
            elif m.group(5) is not None:
                # the DEF path-point extension form ( x y ext ): the third
                # number is how far the wire end extends past the point,
                # never a width
                toks.append(("pt_ext", (m.group(5), m.group(6), int(m.group(7)))))
                ext_points += 1
                if int(m.group(7)) != 0:
                    ext_points_nonzero += 1
            elif m.group(8) is not None:
                toks.append(("pt", (m.group(8), m.group(9))))
            elif m.group(0) == "RECT":
                toks.append(("rect_kw", None))
            else:
                toks.append(("word", m.group(0)))

        i = 0
        while i < len(toks):
            kind, value = toks[i]
            if kind in ("pt", "pt_ext"):
                prev = last_pt if last_pt else (0, 0)
                x = prev[0] if value[0] == "*" else int(value[0])
                y = prev[1] if value[1] == "*" else int(value[1])
                pt = (x, y)
                ext = value[2] if kind == "pt_ext" else None
                if pending_pt is not None and layer is not None:
                    # the path's points are the wire centreline; the metal is
                    # the centreline widened by the path width on the
                    # perpendicular axis, which is what spacing is measured
                    # against -- a zero-length point pair is a square patch
                    x1, y1, x2, y2 = pending_pt[0], pending_pt[1], pt[0], pt[1]
                    half = (width or 0) / 2
                    # each end's extension is metal past its own point along
                    # the path axis (#1022); an out-of-spec negative extension
                    # is read as none (0), never as a shrink below the
                    # centreline. An interior point's extension therefore
                    # extends BOTH adjoining segments past the joint: a
                    # conservative over-cover (can only add overlap reports)
                    ext_start = max(pending_ext or 0, 0)
                    ext_end = max(ext or 0, 0)
                    if y1 == y2:
                        # horizontal: the start end extends past x1 and the
                        # end past x2, whichever side of the pair each sits on
                        lo, hi = (ext_start, ext_end) if x1 <= x2 else (ext_end, ext_start)
                        rect = (
                            min(x1, x2) - lo,
                            int(y1 - half),
                            max(x1, x2) + hi,
                            int(y1 + half),
                        )
                    elif x1 == x2:
                        lo, hi = (ext_start, ext_end) if y1 <= y2 else (ext_end, ext_start)
                        rect = (
                            int(x1 - half),
                            min(y1, y2) - lo,
                            int(x1 + half),
                            max(y1, y2) + hi,
                        )
                    else:
                        # not Manhattan: no single axis to extend along, so
                        # each end's extension is applied on both axes at the
                        # sides of the bbox that end owns -- the fallback
                        # stays a conservative cover
                        xlo = min(x1, x2) - (ext_start if x1 <= x2 else ext_end)
                        xhi = max(x1, x2) + (ext_start if x1 > x2 else ext_end)
                        ylo = min(y1, y2) - (ext_start if y1 <= y2 else ext_end)
                        yhi = max(y1, y2) + (ext_start if y1 > y2 else ext_end)
                        rect = (xlo, ylo, xhi, yhi)
                    shapes.append(
                        {
                            "kind": "wire",
                            "layer": layer,
                            "width": width,
                            "shape": shape_tag,
                            "x1": rect[0],
                            "y1": rect[1],
                            "x2": rect[2],
                            "y2": rect[3],
                            # the extension values ride alongside, per end of
                            # the completed pair -- already applied to the
                            # rect above and counted by the census below, so
                            # they are never silently dropped nor mistaken
                            # for the width
                            "ext_start": pending_ext,
                            "ext_end": ext,
                        }
                    )
                pending_pt = pt
                pending_ext = ext
                last_pt = pt
                expect_via = True
                i += 1
                continue
            if kind == "rect_kw":
                # RECT relative to the last point, in either DEF spelling:
                # ( dx1 dy1 ) ( dx2 dy2 ), or the one-paren quad OpenROAD
                # writes for via pads -- ( dx1 dy1 dx2 dy2 )
                quad = None
                if i + 1 < len(toks) and toks[i + 1][0] == "rect_quad":
                    quad = toks[i + 1][1]
                    i += 2
                elif (
                    i + 2 < len(toks)
                    and toks[i + 1][0] == "pt"
                    and toks[i + 2][0] == "pt"
                    and "*" not in toks[i + 1][1] + toks[i + 2][1]
                ):
                    # RECT deltas are absolute numbers by DEF spec; a '*'
                    # here is not legal and is refused below, never guessed
                    dxs = toks[i + 1][1] + toks[i + 2][1]
                    quad = tuple(int(v) for v in dxs)
                    i += 3
                else:
                    quad = None
                    i += 1
                if quad is not None and layer is not None and last_pt is not None:
                    shapes.append(
                        {
                            "kind": "pad",
                            "layer": layer,
                            "width": None,
                            "shape": shape_tag,
                            "x1": last_pt[0] + quad[0],
                            "y1": last_pt[1] + quad[1],
                            "x2": last_pt[0] + quad[2],
                            "y2": last_pt[1] + quad[3],
                        }
                    )
                elif quad is None:
                    unparsed += 1
                    if len(unparsed_samples) < 20:
                        unparsed_samples.append({"net": net, "token": "RECT"})
                expect_via = False
                continue
            word = value
            if word in _SHAPE_TYPES:
                # + SHAPE <tag>: applies to the path that carries it (DRCFILL
                # vs STRIPE vs FOLLOWPIN is exactly the fill-versus-PDN
                # distinction the analysis records)
                shape_tag = word
                i += 1
                continue
            if word in _SKIP_TOKENS:
                if word == "+":
                    # a property introducer ends the via-name position: a
                    # via name directly follows a point and never a '+', so
                    # the keyword after it is not a via (#1034)
                    expect_via = False
                    # ... and ends the layer-name position the same way:
                    # DEF 5.8 regularWiring is ``+ ROUTED layerName``, the
                    # layer directly after the path keyword, so the keyword
                    # after a '+' is never a layer (#1037)
                    expect_layer = False
                if word in _SKIP_TOKENS_WITH_NUMERIC_ARG and i + 1 < len(toks):
                    # the keyword's own numeric argument rides with it --
                    # never left in the walk to be read as a width (#1021)
                    nxt_kind, nxt_value = toks[i + 1]
                    if nxt_kind == "word" and re.fullmatch(r"-?\d+", nxt_value):
                        i += 2
                        continue
                if word in _SKIP_TOKENS_WITH_NAME_ARG and i + 1 < len(toks):
                    nxt_kind, nxt_value = toks[i + 1]
                    if (
                        nxt_kind == "word"
                        and re.match(r"^[A-Za-z_]", nxt_value)
                        and nxt_value not in _SKIP_TOKENS
                        and nxt_value not in _PATH_KEYWORDS
                        and nxt_value not in _SHAPE_TYPES
                    ):
                        i += 2
                        continue
                if word == "PROPERTY":
                    # ``PROPERTY {propName propVal}...``. Consumed pair by pair
                    # and only while the next token is a plain word, never to
                    # the end of the net: geometry after a (non-conformant)
                    # PROPERTY must stay in the walk, not be erased with
                    # ``unparsed`` still 0 (#1031 review). A lone trailing name
                    # (no value) is consumed alone so it cannot become a via.
                    def _plain(tok: tuple[str, str]) -> bool:
                        return (
                            tok[0] == "word"
                            and tok[1] not in _SKIP_TOKENS
                            and tok[1] not in _PATH_KEYWORDS
                            and tok[1] not in _SHAPE_TYPES
                        )

                    i += 1
                    while i < len(toks) and _plain(toks[i]):
                        i += 2 if i + 1 < len(toks) and _plain(toks[i + 1]) else 1
                    continue
                i += 1
                continue
            if word in _PATH_KEYWORDS:
                # a new path within the same net resets the point context:
                # after NEW, a bare point is a start, never a segment end
                layer, width, pending_pt, pending_ext, last_pt, expect_via = (
                    None,
                    None,
                    None,
                    None,
                    None,
                    False,
                )
                shape_tag = None
                expect_layer = True
                i += 1
                continue
            if expect_layer and re.match(r"^[A-Za-z_]", word):
                # the layer name that follows ROUTED/FIXED/COVER/NEW
                layer = word
                expect_layer = False
                i += 1
                continue
            if re.fullmatch(r"-?\d+", word) and layer is not None and width is None:
                width = int(word)
                i += 1
                continue
            if expect_via and re.match(r"^[A-Za-z_]", word):
                if layer is not None and last_pt is not None:
                    shapes.append(
                        {
                            "kind": "via",
                            "layer": layer,
                            "shape": shape_tag,
                            "via": word,
                            "x": last_pt[0],
                            "y": last_pt[1],
                        }
                    )
                expect_via = False
                i += 1
                continue
            unparsed += 1
            if len(unparsed_samples) < 20:
                unparsed_samples.append({"net": net, "token": word[:60]})
            i += 1
        nets[net] = shapes
    return nets, unparsed, unparsed_samples, (ext_points, ext_points_nonzero)


class DefGeometry:
    """Parsed DEF: via definitions plus per-net shapes, in DBU."""

    def __init__(self, text: str) -> None:
        self.units = 1000
        units = _UNITS.search(text)
        if units:
            self.units = int(units.group(1))
        vias_match = _SECTION["vias"].search(text)
        self.vias = parse_via_definitions(vias_match.group(1)) if vias_match else {}
        self.specialnets: dict[str, list[dict[str, Any]]] = {}
        self.nets: dict[str, list[dict[str, Any]]] = {}
        self.unparsed_tokens = 0
        self.unparsed_samples: list[dict[str, Any]] = []
        # #1022 census: path-point extensions in the file, counted at tokenise
        # time (see _parse_net_section) -- not derived from completed wires,
        # which would drop points that complete no pair and double-count an
        # interior point. "All zero" claimed from a capped unparsed sample is
        # the guess this replaces.
        self.ext_points_total = 0
        self.ext_points_nonzero = 0
        self.sections_found: list[str] = []
        for section, attr in (("specialnets", "specialnets"), ("nets", "nets")):
            match = _SECTION[section].search(text)
            if match is None:
                continue
            self.sections_found.append(section)
            parsed, unparsed, samples, ext_counts = _parse_net_section(match.group(1))
            self.ext_points_total += ext_counts[0]
            self.ext_points_nonzero += ext_counts[1]
            getattr(self, attr).update(parsed)
            self.unparsed_tokens += unparsed
            self.unparsed_samples.extend(samples)

    def shapes_near(
        self,
        net: str,
        layer: str,
        window: tuple[int, int, int, int],
        section: str,
    ) -> dict[str, Any]:
        """``layer`` shapes of ``net`` (wires, pads) plus via pads on ``layer``.

        Via placements are read two ways: pads derived from the via's DEF
        definition (exact), and -- when the definition is not in the DEF and
        the via sits *on* ``layer`` -- counted as unresolved, with the via
        name and point, because a via placed on a layer necessarily lands
        geometry on it. Vias on other layers with missing definitions are not
        evidence for ``layer`` and are not claimed.
        """
        source = self.specialnets if section == "specialnets" else self.nets
        wx1, wy1, wx2, wy2 = window

        def touches(rect: tuple[int, int, int, int]) -> bool:
            return not (rect[2] < wx1 or rect[0] > wx2 or rect[3] < wy1 or rect[1] > wy2)

        wires: list[dict[str, Any]] = []
        pads: list[dict[str, Any]] = []
        vias_resolved: list[dict[str, Any]] = []
        vias_unresolved: list[dict[str, Any]] = []
        for shape in source.get(net, []):
            if shape["kind"] == "via":
                if shape["layer"] != layer:
                    continue
                at = (shape["x"], shape["y"])
                if shape["via"] in self.vias:
                    for rect in self.via_pads(shape, layer):
                        if touches(rect):
                            vias_resolved.append(
                                {"via": shape["via"], "shape": shape.get("shape"), "rect": list(rect)}
                            )
                elif touches((at[0], at[1], at[0], at[1])):
                    # definition not in the DEF, so its pads cannot be
                    # derived; the placement point itself is the evidence,
                    # and only when it is inside the window
                    vias_unresolved.append({"via": shape["via"], "at": [at[0], at[1]]})
                continue
            if shape["layer"] != layer:
                continue
            rect = (shape["x1"], shape["y1"], shape["x2"], shape["y2"])
            if touches(rect):
                (wires if shape["kind"] == "wire" else pads).append(dict(shape))
        return {
            "wires": wires,
            "pads": pads,
            "vias_resolved": vias_resolved,
            "vias_unresolved": vias_unresolved,
        }

    def via_pads(self, shape: dict[str, Any], layer: str) -> list[tuple[int, int, int, int]]:
        """Absolute rectangles one via placement puts on ``layer``."""
        rects = self.vias.get(shape.get("via", ""), {}).get(layer)
        if not rects:
            return []
        return [
            (shape["x"] + r[0], shape["y"] + r[1], shape["x"] + r[2], shape["y"] + r[3])
            for r in rects
        ]


def _rect_distance(a: tuple[int, int, int, int], b: tuple[int, int, int, int]) -> int:
    """Gap between two rects in DBU on both axes: 0 when they overlap."""
    dx = max(a[0], b[0]) - min(a[2], b[2])
    dy = max(a[1], b[1]) - min(a[3], b[3])
    return max(0, dx, dy)


def analyse(
    def_geometry: DefGeometry,
    drc: dict[str, Any],
    power_nets: frozenset[str],
    window_um: float,
) -> list[dict[str, Any]]:
    """One evidence record per distinct DRC site, in the report's order."""
    records: list[dict[str, Any]] = []
    span = int(round(window_um * def_geometry.units))
    dbu = def_geometry.units
    for site in drc["sites"].values():
        bbox_um = _norm_bbox_um(site.bbox)
        if bbox_um is None:
            records.append(
                {
                    "type": site.type,
                    "srcs": site.srcs,
                    "bbox": site.bbox,
                    "layer": site.layer,
                    "evidence": "unparsable bbox",
                    "verdict": "undetermined",
                }
            )
            continue
        x1, y1, x2, y2 = bbox_um
        bbox_dbu = tuple(int(round(v * dbu)) for v in (x1, y1, x2, y2))
        window = (
            bbox_dbu[0] - span,
            bbox_dbu[1] - span,
            bbox_dbu[2] + span,
            bbox_dbu[3] + span,
        )
        layer = site.layer
        signal_nets = [n for n in site.nets if n not in power_nets]
        power_nets_here = [n for n in site.nets if n in power_nets]
        per_power = {
            name: def_geometry.shapes_near(name, layer, window, "specialnets")
            for name in power_nets_here
        }
        per_signal = {
            name: def_geometry.shapes_near(name, layer, window, "nets")
            for name in signal_nets
        }

        # per-site SEGMENT-END census (#1022): extension values carried by the
        # window's wires -- power and signal both -- and how many are non-zero.
        # This counts ends on completed wires, NOT extension points: an
        # interior point is seen by two segments, and a point that completes
        # no pair is absent. The file-wide point count is def_parse.extension_points_*.
        ext_total = ext_nonzero = 0
        for found in list(per_power.values()) + list(per_signal.values()):
            for wire in found["wires"]:
                for value in (wire["ext_start"], wire["ext_end"]):
                    if value is not None:
                        ext_total += 1
                        if value != 0:
                            ext_nonzero += 1

        # every power shape rect with its gap to the bbox and its origin
        power_rects: list[dict[str, Any]] = []
        for name, found in per_power.items():
            for wire in found["wires"]:
                rect = (wire["x1"], wire["y1"], wire["x2"], wire["y2"])
                power_rects.append(
                    {
                        "net": name,
                        "origin": "wire",
                        "shape": wire.get("shape"),
                        "gap_dbu": _rect_distance(bbox_dbu, rect),
                        "rect": list(rect),
                    }
                )
            for pad in found["pads"]:
                rect = (pad["x1"], pad["y1"], pad["x2"], pad["y2"])
                power_rects.append(
                    {
                        "net": name,
                        "origin": "pad",
                        "shape": pad.get("shape"),
                        "gap_dbu": _rect_distance(bbox_dbu, rect),
                        "rect": list(rect),
                    }
                )
            for via in found["vias_resolved"]:
                rect = tuple(via["rect"])
                power_rects.append(
                    {
                        "net": name,
                        "origin": "via_pad",
                        "shape": via.get("shape"),
                        "via": via["via"],
                        "gap_dbu": _rect_distance(bbox_dbu, rect),
                        "rect": list(rect),
                    }
                )
            for via in found["vias_unresolved"]:
                power_rects.append(
                    {
                        "net": name,
                        "origin": "via_unresolved",
                        "via": via["via"],
                        "at": via["at"],
                        "gap_dbu": None,
                    }
                )
        power_rects.sort(key=lambda r: (r["gap_dbu"] is None, r["gap_dbu"] or 0))

        # overlap: does any signal shape intersect any power shape on layer?
        overlap = False
        for found in per_signal.values():
            signal_rects = [
                (w["x1"], w["y1"], w["x2"], w["y2"]) for w in found["wires"]
            ] + [
                (p["x1"], p["y1"], p["x2"], p["y2"]) for p in found["pads"]
            ] + [
                tuple(v["rect"]) for v in found["vias_resolved"]
            ]
            for srect in signal_rects:
                for prect in power_rects:
                    if prect.get("rect") is None:
                        continue
                    if _rect_distance(srect, tuple(prect["rect"])) == 0:
                        overlap = True
        signal_present = any(
            found["wires"] or found["pads"] or found["vias_resolved"] or found["vias_unresolved"]
            for found in per_signal.values()
        )
        nearest = power_rects[0] if power_rects else None
        if nearest is None:
            evidence = f"no {'/'.join(power_nets_here) or 'power'} {layer} geometry within the window"
            verdict = "undetermined"
        else:
            origin = nearest["origin"]
            gap = nearest["gap_dbu"]
            if overlap:
                verdict = "router_overlap_signal_crosses_power"
            elif origin in ("via_pad", "pad", "via_unresolved"):
                verdict = "power_via_land_spaced"
            elif origin == "wire":
                verdict = (
                    "fill_wire_spaced"
                    if nearest.get("shape") == "DRCFILL"
                    else "power_wire_spaced"
                )
            else:
                verdict = "undetermined"
            evidence = (
                f"nearest power {layer} shape: origin={origin}"
                + (f" shape={nearest['shape']}" if nearest.get("shape") else "")
                + (f" via={nearest['via']}" if nearest.get("via") else "")
                + (f", at={nearest['at']}" if nearest.get("at") else "")
                + f", gap={gap} dbu"
                + (
                    f"; signal {', '.join(signal_nets)} {layer} shapes in window"
                    if signal_present
                    else f"; no signal {layer} shapes in window"
                )
                + ("; SIGNAL OVERLAPS POWER" if overlap else "")
            )
        records.append(
            {
                "type": site.type,
                "srcs": site.srcs,
                "bbox": site.bbox,
                "layer": layer,
                "nets": site.nets,
                "signal_nets": signal_nets,
                "power_nets": power_nets_here,
                "power_shapes_in_window_total": len(power_rects),
                "power_shapes_in_window": power_rects[:8],
                "signal_shapes_in_window": {
                    name: {
                        "wires": len(found["wires"]),
                        "pads": len(found["pads"]),
                        "vias_resolved": len(found["vias_resolved"]),
                        "vias_unresolved": len(found["vias_unresolved"]),
                    }
                    for name, found in per_signal.items()
                },
                "wire_ext_ends_in_window": {"total": ext_total, "nonzero": ext_nonzero},
                "signal_overlaps_power": overlap,
                "evidence": evidence,
                "verdict": verdict,
            }
        )
    return records


def read_par_power_nets(par_path: Path) -> tuple[frozenset[str] | None, str | None]:
    """Power-net names from the par response's power block, or a refusal.

    klt's power block names the nets as ``power_net``/``ground_net`` (an
    optional ``nets`` list is accepted too, for symmetry with the request
    shape); whichever it carries, deriving them from the DEF alone would be
    guessing.
    """
    try:
        par = json.loads(par_path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        return None, f"par response unreadable: {exc}"

    def find_power(obj: Any) -> Any:
        if isinstance(obj, dict):
            if "power" in obj and isinstance(obj["power"], dict):
                return obj["power"]
            for value in obj.values():
                found = find_power(value)
                if found is not None:
                    return found
        return None

    block = find_power(par)
    if block is None:
        return None, "par response has no power block"
    nets: set[str] = set()
    for key in ("power_net", "ground_net"):
        if block.get(key):
            nets.add(str(block[key]))
    nets.update(str(n) for n in (block.get("nets") or []))
    if not nets:
        return None, "par response power block names no power/ground nets"
    return frozenset(nets), None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--def", dest="def_path", required=True)
    parser.add_argument("--drc-report", required=True)
    parser.add_argument("--par-response", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--window-um", type=float, default=DEFAULT_WINDOW_UM)
    parser.add_argument(
        "--source-job",
        default=None,
        help="batch job id the DEF/DRC/par inputs came from, recorded verbatim",
    )
    args = parser.parse_args(argv)

    def_path, drc_path, par_path = Path(args.def_path), Path(args.drc_report), Path(args.par_response)
    refusals = [
        f"{label} not found: {path}"
        for label, path in (("DEF", def_path), ("DRC report", drc_path), ("par response", par_path))
        if not path.is_file()
    ]
    power_nets, reason = read_par_power_nets(par_path)
    if reason:
        refusals.append(reason)
    if Path(args.output).is_dir():
        refusals.append(f"--output names a directory: {args.output}")
    else:
        try:
            Path(args.output).parent.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            refusals.append(f"--output: its directory cannot be created: {exc}")
    for refusal in refusals:
        print(f"REFUSED: {refusal}", file=sys.stderr)
    if refusals:
        return EXIT_REFUSED

    text = def_path.read_text(errors="replace")
    geometry = DefGeometry(text)
    missing = [s for s in ("nets", "specialnets") if s not in geometry.sections_found]
    if missing:
        print(
            f"REFUSED: DEF has no parsable {' and '.join(missing)} section(s); "
            "the geometry the question is about cannot be read",
            file=sys.stderr,
        )
        return EXIT_REFUSED

    drc = parse_drc_report(drc_path.read_text(errors="replace"))
    if not drc["sites"]:
        print(
            "REFUSED: DRC report has no parsable records -- the report disagrees "
            "with the committed count (24) and a zero-site answer would look "
            "like data",
            file=sys.stderr,
        )
        return EXIT_REFUSED

    sites = analyse(geometry, drc, power_nets, args.window_um)
    verdicts = Counter(record["verdict"] for record in sites)
    result = {
        "schema_version": SCHEMA_VERSION,
        "what": "Design-versus-router evidence for the residual route DRC sites, read from the routed DEF (#1006). Evidence, not a verdict: per-site geometry records; docs/tool-findings.md concludes on these. signal_overlaps_power is a lower bound: regular NETS carry no width, so signal wires are zero-width centrelines and real metal edges can overlap power where a centreline does not.",
        "inputs": {
            "def": {"path": str(def_path), "sha256": sha256_file(def_path)},
            "drc_report": {"path": str(drc_path), "sha256": sha256_file(drc_path)},
            "par_response": {"path": str(par_path), "sha256": sha256_file(par_path)},
        },
        "source_job": args.source_job,
        "def_parse": {
            "units_distance_microns": geometry.units,
            "sections_found": geometry.sections_found,
            "nets_parsed": len(geometry.nets),
            "specialnets_parsed": len(geometry.specialnets),
            "via_definitions_parsed": len(geometry.vias),
            "unparsed_tokens": geometry.unparsed_tokens,
            "unparsed_samples": geometry.unparsed_samples,
            "extension_points_total": geometry.ext_points_total,
            "extension_points_nonzero": geometry.ext_points_nonzero,
        },
        "power_nets": sorted(power_nets),
        "window_um": args.window_um,
        "drc_summary": {
            "total_records": drc["total_records"],
            "distinct_sites": drc["distinct_sites_type_srcs_bbox_layer"],
        },
        "verdict_counts": dict(verdicts),
        "sites": sites,
    }
    try:
        Path(args.output).write_text(json.dumps(result, indent=2) + "\n")
    except OSError as exc:
        print(f"REFUSED: the analysis was not written to {args.output}: {exc}", file=sys.stderr)
        return EXIT_REFUSED
    print(f"def_site_analysis: {args.output}")
    print(f"  sites analysed: {len(sites)}; verdicts: {dict(verdicts)}")
    print(f"  DEF parse: {json.dumps(result['def_parse'])}")
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
